//! 事実の cache を作業木の間で分け合う検(card ki-9743c30d49bc)— 一時の置き場で binary を走らせ、置き場に新しく書かれた byte を数える。
//!
//! zeus は作業木が 1 日に数百本作られ、checkout で全 file の更新時刻が新しくなるので、作業木ごとに事実の cache(約 208MB)を全部作り直し、
//! 1 時間に約 3.4GB を USB の SSD へ書いていた。事実は file の中身・根からの path・linter の印だけで決まるので、同じ中身の file の事実は
//! 作業木が違っても 1 つを分け合い、作業木ごとには file → 事実の鍵の小さな表だけを置く。

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::SystemTime;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF106", "DOEFF122", "DOEFF126", "DOEFF127", "DOEFF129", "DOEFF130"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample :root "app" :layers [(layer core :summary "核") (layer foundation :summary "土台")] :foundation foundation)
"#;

const MODULES: usize = 24;

/// 1 module の中身 — 定義を十数本持つ(事実が 1 file で数 KB になる大きさ)。modules 同士が呼び合うので、推論の世界も file をまたぐ。
fn module_source(i: usize) -> String {
    let mut text = String::from("(import time)\n");
    // 実際の module は import を十数本持つ(module の依存の事実が、表の 1 行より大きくなる)。
    for k in 1..=12 {
        text.push_str(&format!("(import app.core.mod{})\n", (i + k) % MODULES));
    }
    text.push_str(&format!("(defrecord Box{i} [value])\n(defeffect Ping{i} {{:fields [(: n int)] :answer None}})\n"));
    for j in 0..10 {
        text.push_str(&format!(
            "(defk step-{i}-{j} [x] {{:tags {{:context \"c\" :role \"program\"}} :effects [Ping{i}]}}\n  (<- y (Ping{i} {j}))\n  (<- z (Ping{i} (+ y {j})))\n  [y z x])\n"
        ));
    }
    let next = (i + 1) % MODULES;
    text.push_str(&format!(
        "(defk run-{i} [x] {{:tags {{:context \"c\" :role \"program\"}}}}\n  (<- a (step-{i}-0 x))\n  (<- b (step-{i}-9 a))\n  (<- c (app.core.mod{next}.step-{next}-0 b))\n  c)\n"
    ));
    text.push_str(&format!("(defn now-{i} [] (time.time))\n"));
    text
}

/// 作業木 1 本ぶんの repo を dir に作る。同じ関数で作れば、中身が同じで更新時刻だけ違う木になる(checkout し直した作業木)。
fn checkout(dir: &Path) {
    std::fs::write(dir.join("pyproject.toml"), CONFIG).unwrap();
    std::fs::write(dir.join("architecture.hy"), ARCHITECTURE).unwrap();
    std::fs::create_dir_all(dir.join("app/core")).unwrap();
    for i in 0..MODULES {
        std::fs::write(dir.join(format!("app/core/mod{i}.hy")), module_source(i)).unwrap();
    }
}

/// binary を root で走らせて出力(editor-json)を返す。cache = Some(dir) ならその置き場の cache を使い、None なら使わない。
/// `DOEFF_LINTER_CHECK_SLOT` は機体の検の枠の再入の印 — この検は機体の枠を待たずに走らせる。
fn lint(root: &Path, cache: Option<&Path>) -> String {
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env_remove("DOEFF_LINTER_NO_CACHE");
    match cache {
        Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir),
        None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
    };
    let output = command.output().unwrap();
    assert!(output.status.code().is_some_and(|c| c <= 1), "{}", String::from_utf8_lossy(&output.stderr));
    String::from_utf8(output.stdout).unwrap()
}

/// 置き場の file の (大きさ, 更新時刻)(path ごと)。
fn snapshot(cache: &Path) -> HashMap<PathBuf, (u64, SystemTime)> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .map(|e| {
            let meta = e.metadata().unwrap();
            (e.path().to_path_buf(), (meta.len(), meta.modified().unwrap()))
        })
        .collect()
}

fn total(snapshot: &HashMap<PathBuf, (u64, SystemTime)>) -> u64 {
    snapshot.values().map(|(len, _)| len).sum()
}

/// before の後に置き場へ新しく書かれた byte(新しい file か、更新された file の大きさ)。
fn written_since(before: &HashMap<PathBuf, (u64, SystemTime)>, cache: &Path) -> u64 {
    snapshot(cache).iter().filter(|(path, stamp)| before.get(*path) != Some(stamp)).map(|(_, (len, _))| len).sum()
}

/// 置き場の file(根の記録 `root.path` を除く)の中身のどこかに text が在る path。
fn files_containing(cache: &Path, text: &str) -> Vec<PathBuf> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        // 根の dir の `root.path` は、その根の path を記録する物(根が消えた dir の片づけが読む)— 分け合う事実ではない。
        .filter(|e| e.file_name() != "root.path")
        .filter(|e| std::fs::read(e.path()).is_ok_and(|bytes| bytes.windows(text.len()).any(|w| w == text.as_bytes())))
        .map(|e| e.path().to_path_buf())
        .collect()
}

/// 受入の条: 同じ commit を checkout した 2 つの作業木で、2 本目が事実の cache の下に新しく書く byte は 1 本目の 10% 未満。
/// 答えは cache を使わない実行と同じ(位置の path は 2 本目の作業木の file を指す)。
#[test]
fn a_second_checkout_of_the_same_tree_writes_less_than_a_tenth_of_the_first() {
    let cache = tempfile::TempDir::new().unwrap();
    let (first, second) = (tempfile::TempDir::new().unwrap(), tempfile::TempDir::new().unwrap());
    checkout(first.path());
    checkout(second.path());

    lint(first.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);
    assert!(written_first > 100_000, "1 本目が置いた事実が小さすぎて比べにならない: {written_first} byte");
    // 事実の種類(Hy の索引・signature の事実と推論・defk の名・失敗の型・record の欄・引数の呼び)が置かれている。
    for kind in ["hy-index.bin", "signature-facts.bin", "signature-summaries.bin", "defk-names.json", "program-params.json"] {
        assert!(
            after_first.keys().any(|path| path.components().any(|c| c.as_os_str() == kind)),
            "{kind} の事実が置かれていない: {:?}",
            after_first.keys().collect::<Vec<_>>()
        );
    }

    let cached = lint(second.path(), Some(cache.path()));
    let written_second = written_since(&after_first, cache.path());
    assert!(
        written_second * 10 < written_first,
        "2 本目の作業木が事実の cache の下に新しく書いた byte {written_second} が、1 本目 {written_first} の 10% 以上"
    );
    assert_eq!(cached, lint(second.path(), None), "分け合った事実から出した答えが、cache を使わない実行と違う");
}

/// 置き場に作業木の絶対 path が残らない(残ると、別の作業木が読んだ事実が他の作業木の file を指す)。
#[test]
fn no_working_tree_path_is_left_in_the_shared_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    let output = lint(tree.path(), Some(cache.path()));
    // この実行が位置の path を出している(= 事実が path を持つ種類を通っている)ことの確かめ。
    let canonical = std::fs::canonicalize(tree.path()).unwrap();
    assert!(output.contains(canonical.to_str().unwrap()), "出力に作業木の path が無い — 位置の path を通る検になっていない");
    for text in [canonical.to_str().unwrap(), tree.path().to_str().unwrap()] {
        assert_eq!(files_containing(cache.path(), text), Vec::<PathBuf>::new(), "置き場に作業木の path {text} が残った");
    }
}

/// 1 file を書き換えた作業木が新しく書くのは、その file の事実と表だけ — 他の file の事実は作り直さない。
#[test]
fn editing_one_file_writes_only_that_files_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    lint(tree.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);

    std::thread::sleep(std::time::Duration::from_millis(20));
    let edited = tree.path().join("app/core/mod3.hy");
    let mut text = std::fs::read_to_string(&edited).unwrap();
    // defk を足さない書き換え(defk の名の指紋が変わると、引数の呼びの事実は全部作り直される — 別の話)。
    text.push_str("(defn added [x] (+ x 1))\n");
    std::fs::write(&edited, text).unwrap();
    let cached = lint(tree.path(), Some(cache.path()));
    let written = written_since(&after_first, cache.path());
    assert!(written * 10 < written_first, "1 file の書き換えで新しく書いた byte {written} が、全体 {written_first} の 10% 以上");
    assert_eq!(cached, lint(tree.path(), None), "書き換えの後の答えが、cache を使わない実行と違う");
}

/// 同じ作業木の 2 回目は何も書かない(事実も表も)。
#[test]
fn a_second_run_in_the_same_tree_writes_no_facts() {
    let cache = tempfile::TempDir::new().unwrap();
    let tree = tempfile::TempDir::new().unwrap();
    checkout(tree.path());
    lint(tree.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    lint(tree.path(), Some(cache.path()));
    let rewritten: Vec<PathBuf> = snapshot(cache.path())
        .into_iter()
        .filter(|(path, stamp)| after_first.get(path) != Some(stamp))
        // 使った印(中身は空)と、置き場の走査の時刻の記録は毎回更新される。
        .filter(|(path, _)| !matches!(path.file_name().and_then(|n| n.to_str()), Some("used" | ".swept" | ".swept-cap")))
        .map(|(path, _)| path)
        .collect();
    assert_eq!(rewritten, Vec::<PathBuf>::new(), "変わっていない作業木の 2 回目が cache を書き直した");
}

/// `--affected-tests`(module の依存の事実 — `module-dependencies`)も、作業木をまたいで分け合う。git が知る file を母集団にするので、
/// 作業木は git の repo にする。
#[test]
fn module_dependencies_are_shared_between_checkouts_too() {
    let git = |dir: &Path, args: &[&str]| {
        let status = Command::new("git").arg("-C").arg(dir).args(args).status().unwrap();
        assert!(status.success(), "git {args:?}");
    };
    let affected = |root: &Path, cache: Option<&Path>| {
        let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
        command
            .args(["--affected-tests", "app/core/mod0.hy", "--test-pattern", "test_*.hy", "--no-log"])
            .current_dir(root)
            .env("DOEFF_LINTER_CHECK_SLOT", "1")
            .env_remove("DOEFF_LINTER_NO_CACHE");
        match cache {
            Some(dir) => command.env("DOEFF_LINTER_CACHE_DIR", dir),
            None => command.env("DOEFF_LINTER_NO_CACHE", "1"),
        };
        let output = command.output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        String::from_utf8(output.stdout).unwrap()
    };
    let cache = tempfile::TempDir::new().unwrap();
    let (first, second) = (tempfile::TempDir::new().unwrap(), tempfile::TempDir::new().unwrap());
    for tree in [first.path(), second.path()] {
        checkout(tree);
        std::fs::create_dir_all(tree.join("tests")).unwrap();
        std::fs::write(tree.join("tests/test_mod0.hy"), "(import app.core.mod0 [run-0])\n(deftest test-run (assert (run-0 1)))\n").unwrap();
        git(tree, &["init", "-q"]);
        git(tree, &["add", "-A"]);
    }
    affected(first.path(), Some(cache.path()));
    let after_first = snapshot(cache.path());
    let written_first = total(&after_first);
    assert!(after_first.keys().any(|path| path.components().any(|c| c.as_os_str() == "module-dependencies.json")), "module の依存の事実が置かれていない");

    let cached = affected(second.path(), Some(cache.path()));
    // 2 本目が書くのは根ごとの表だけ — 事実(`objects` の下)は 1 つも書かない。この検の module の依存の事実は小さく(1 file 数百 byte)、
    // 表の 1 行と同じ桁なので、全体の 10% ではなく事実の有無で確かめる(全体の割合は上の検)。
    let written_facts: Vec<PathBuf> = snapshot(cache.path())
        .into_iter()
        .filter(|(path, stamp)| {
            // 使った印 `used`(中身は空)は実行のたびに更新される。
            path.components().any(|c| c.as_os_str() == "objects") && path.file_name() != Some("used".as_ref()) && after_first.get(path) != Some(stamp)
        })
        .map(|(path, _)| path)
        .collect();
    assert_eq!(written_facts, Vec::<PathBuf>::new(), "同じ中身の file の module の依存の事実を、2 本目の作業木が置き直した");
    let written_second = written_since(&after_first, cache.path());
    assert!(written_second * 4 < written_first, "2 本目の作業木が新しく書いた表 {written_second} byte が、1 本目 {written_first} の 4 分の 1 以上");
    assert_eq!(cached, affected(second.path(), None));
    assert!(cached.contains("tests/test_mod0.hy"), "検の file に届いていない: {cached}");
}
