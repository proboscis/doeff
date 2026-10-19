//! 1 file の実行(`--output-format editor-json --stdin --path <file>` — 書き込み直後の hook とエディタ)が読む範囲の検(card ki-a9959efd9cf6 の残り)。
//!
//! 1 file の検査は、書いた file と、その file が名指す定義だけで答えが決まる。以前は repo の Hy の file を全部読んで(事実の種類ごとに
//! file 1 つずつ 約 1 万回の open・agora-controllers で 1 回 CPU 0.5〜1.5 秒)から書いた file 1 つを判じていた — 混んだ Pod(load 40〜100)では
//! 読み込みの待ちで実時間 2〜7 秒になり、hook の期限(4 秒)で助言が切れた(10-12 の実測 31 回のうち 7 回)。
//!
//! 2 つを検める:
//! - 答え: 1 file の実行の違反は、repo 全体の実行がその file に出す違反と同じ(全部読む実行と、名指しだけ読む実行で答えが変わらない)。
//!   失敗の型・引数に渡された Program・class の frozen は、名指さない file の宣言で決まる物(逆向きの依存)なので、それが要る file でも同じ答えになる。
//! - 読む範囲: 名指さない module の事実を cache に置かない。逆向きの依存を要らない file は、その事実の種類を 1 つも置かない。

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Stdio};

const MODULES: usize = 24;

const CONFIG: &str = r#"
[tool.doeff-linter]
architecture = "architecture.hy"
enable = ["DOEFF101", "DOEFF106", "DOEFF122", "DOEFF126", "DOEFF127", "DOEFF129", "DOEFF130", "DOEFF142"]

[tool.doeff-linter.definitions]
paths = ["app"]
test_paths = ["tests/**/test_*.hy"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const ARCHITECTURE: &str = r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "核" :roles [program] :imports [core]) (layer foundation :summary "土台" :roles [foundation] :imports [foundation])]
  :foundation foundation
  :handler-arguments {:files ["app/**/*.hy"] :exclude ["**/tests/**"] :keep-mark "keep:"})
"#;

const TAGS: &str = "{:tags {:context \"c\" :role \"program\"}}";

/// 書く file(W)。modules 0〜2 は呼び合い、3 以降は別の輪(W から呼びで届かない)。
fn module_source(i: usize) -> String {
    let mut text = String::from("(import time)\n");
    if i == 0 {
        // core が foundation を import する(DOEFF101)— 置き場で foundation の file と、置き場は core の dir でもタグの role で foundation に推される file。
        text.push_str("(import app.core.mod1)\n(import app.foundation.f)\n(import app.core.proxy)\n");
    }
    text.push_str(&format!("(defrecord Box{i} [value])\n(defeffect Ping{i} {{:fields [(: n int)] :answer None}})\n"));
    text.push_str(&format!("(defk step-{i}-0 [x] {{:tags {{:context \"c\" :role \"program\"}} :effects [Ping{i}]}}\n  (<- y (Ping{i} {i}))\n  [y x])\n"));
    // 0 → 1 → 2 は呼ぶ。3 以降は 3 → 4 → … → 3 の輪。
    let next = match i {
        0 => Some(1),
        1 => Some(2),
        2 => None,
        n if n + 1 < MODULES => Some(n + 1),
        _ => Some(3),
    };
    match next {
        Some(next) => text.push_str(&format!(
            "(defk run-{i} [x] {TAGS}\n  (<- a (step-{i}-0 x))\n  (<- c (app.core.mod{next}.step-{next}-0 a))\n  c)\n"
        )),
        None => text.push_str(&format!("(defk run-{i} [x] {TAGS}\n  (<- a (step-{i}-0 x))\n  a)\n")),
    }
    if i == 0 {
        // defk の素の呼び(DOEFF126)と、層の外の生の副作用(DOEFF106)。
        text.push_str(&format!("(defk bare-0 [x] {TAGS}\n  (+ 1 (app.core.mod1.step-1-0 x)))\n(defn now-0 [] (time.time))\n"));
    } else {
        text.push_str(&format!("(defn now-{i} [] (time.time))\n"));
    }
    text
}

/// 名指さない file の宣言で答えが決まる file(逆向きの依存)と、その宣言の file。
fn reverse_files() -> Vec<(&'static str, String)> {
    vec![
        ("app/foundation/f.hy", "(defn f [] 1)\n".to_string()),
        ("app/core/proxy.hy", "(val MODULE-TAGS {:context \"c\" :role \"foundation\"})\n(defn p [] 1)\n".to_string()),
        // 失敗の型: errs の Unreachable は effs の defeffect の :failure で宣言される(errs 自身は宣言しない)。
        ("app/core/errs.hy", "(defrecord Unreachable [reason])\n(defrecord Accepted [word])\n".to_string()),
        (
            "app/core/effs.hy",
            "(import app.core.errs)\n(defeffect ReadRow {:fields [key] :answer None :failure [app.core.errs.Unreachable]})\n".to_string(),
        ),
        (
            "app/core/needs.hy",
            format!(
                "(import app.core.errs [Unreachable Accepted])\n(defk decide [x] {TAGS}\n  (<- said (| Accepted Unreachable) (verdict x))\n  (match said\n    (Unreachable) (do (<- refused Plan (rejected (+ said.reason \": \" said.detail)))\n                      (return refused))\n    (Accepted) None))\n"
            ),
        ),
        // 引数に渡された Program: hof の field-of を、user が fnk で渡す。
        (
            "app/core/hof.hy",
            format!("(defk rows-by-text [rows field-of] {TAGS}\n  (for [row rows]\n    (setv value (field-of row)))\n  rows)\n"),
        ),
        (
            "app/core/user.hy",
            format!(
                "(import app.core.hof [rows-by-text])\n(defk tag-rows-of [tags] {TAGS}\n  (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p \"subject\")))))\n"
            ),
        ),
        // class の frozen: handlers が引数の型 Cfg を使う。import する cfgs の Cfg は frozen、import しない legacy の同じ名の Cfg は frozen でない
        // (名で引く索引は「同じ名が frozen でない所に 1 つでもあれば frozen でない」)。
        ("app/core/cfgs.hy", "(defrecord Cfg [#^ str url])\n".to_string()),
        ("app/core/legacy.hy", "(defclass Cfg [] (setv x 1))\n".to_string()),
        (
            "app/core/handlers.hy",
            "(import app.core.cfgs [Cfg])\n(defhandler h [#^ Cfg cfg]\n  (Ping0 [e k] (k 1)))\n".to_string(),
        ),
    ]
}

fn checkout(dir: &Path) {
    std::fs::write(dir.join("pyproject.toml"), CONFIG).unwrap();
    std::fs::write(dir.join("architecture.hy"), ARCHITECTURE).unwrap();
    std::fs::create_dir_all(dir.join("app/core")).unwrap();
    for i in 0..MODULES {
        std::fs::write(dir.join(format!("app/core/mod{i}.hy")), module_source(i)).unwrap();
    }
    for (rel, text) in reverse_files() {
        std::fs::create_dir_all(dir.join(rel).parent().unwrap()).unwrap();
        std::fs::write(dir.join(rel), text).unwrap();
    }
}

fn editor(root: &Path, cache: &Path, args: &[&str], stdin: Option<&str>) -> (Value, String) {
    use std::io::Write;
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command
        .args(["--output-format", "editor-json", "--no-log"])
        .args(args)
        .current_dir(root)
        .env("DOEFF_LINTER_CHECK_SLOT", "1")
        .env("DOEFF_LINTER_TIMING", "1")
        .env("DOEFF_LINTER_CACHE_DIR", cache)
        .env_remove("DOEFF_LINTER_NO_CACHE")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn().unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({e}): {stdout}\n{stderr}"));
    (report, stderr)
}

/// file `rel` の違反の鍵(並べ替え済み)。
fn keys_of(report: &Value, rel: &str) -> Vec<String> {
    let prefix = format!("{rel}::");
    let mut keys: Vec<String> = report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|v| v["key"].as_str())
        .filter(|k| k.starts_with(&prefix))
        .map(str::to_string)
        .collect();
    keys.sort();
    keys
}

/// 置き場の file(根の記録 `root.path` を除く)の中身のどこかに text が在る path。
fn files_containing(cache: &Path, text: &str) -> Vec<std::path::PathBuf> {
    walkdir::WalkDir::new(cache)
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file() && e.file_name() != "root.path")
        .filter(|e| std::fs::read(e.path()).is_ok_and(|bytes| bytes.windows(text.len()).any(|w| w == text.as_bytes())))
        .map(|e| e.path().to_path_buf())
        .collect()
}

/// 置き場の事実の種類(`objects/<種類>.<形>`)のうち、事実が 1 つでも置かれた種類。
fn stored_kinds(cache: &Path) -> Vec<String> {
    let objects = cache.join("objects");
    let Ok(entries) = std::fs::read_dir(&objects) else { return Vec::new() };
    let mut kinds: Vec<String> = entries
        .filter_map(Result::ok)
        .filter(|e| walkdir::WalkDir::new(e.path()).into_iter().filter_map(Result::ok).any(|f| f.file_type().is_file()))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .collect();
    kinds.sort();
    kinds
}

fn whole_report(root: &Path) -> Value {
    let cache = tempfile::TempDir::new().unwrap();
    editor(root, cache.path(), &[], None).0
}

fn single_report(root: &Path, cache: &Path, rel: &str) -> (Value, String) {
    let source = std::fs::read_to_string(root.join(rel)).unwrap();
    editor(root, cache, &["--stdin", "--path", &root.join(rel).to_string_lossy()], Some(&source))
}

/// 答え: 1 file の実行は、repo 全体の実行がその file に出す違反と同じ。どの file も、当たりが 1 つ以上ある(空の比べで通らない)。
#[test]
fn a_single_file_run_answers_like_the_whole_run_for_that_file() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let whole = whole_report(dir.path());
    for (rel, rules) in [
        ("app/core/mod0.hy", &["DOEFF101", "DOEFF126", "DOEFF106"][..]),
        ("app/core/needs.hy", &["DOEFF122"][..]),
        ("app/core/hof.hy", &["DOEFF126"][..]),
        ("app/core/handlers.hy", &["DOEFF142"][..]),
    ] {
        let expected = keys_of(&whole, rel);
        for rule in rules {
            assert!(expected.iter().any(|k| k.contains(rule)), "{rel} に {rule} が当たらない(検の材料の誤り): {expected:?}");
        }
        let cache = tempfile::TempDir::new().unwrap();
        let (single, stderr) = single_report(dir.path(), cache.path(), rel);
        assert_eq!(keys_of(&single, rel), expected, "{rel} の 1 file の実行(cache なし)\n{stderr}");
        // cache の温まった後も同じ答え。
        let (again, _) = single_report(dir.path(), cache.path(), rel);
        assert_eq!(keys_of(&again, rel), expected, "{rel} の 1 file の実行(cache あり)");
    }
}

/// 読む範囲: 書いた file から呼びで届く module(mod0 → mod1 → mod2)の事実だけを置く。届かない module(3〜)の事実は置かない。
#[test]
fn a_single_file_run_stores_facts_only_for_the_modules_the_file_names() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (_, stderr) = single_report(dir.path(), cache.path(), "app/core/mod0.hy");
    // 届く module(0〜2)の事実は置かれている — 検が空振りでないことの確かめ。
    for i in 1..=2 {
        assert!(!files_containing(cache.path(), &format!("Box{i}")).is_empty(), "mod{i} の事実が置かれていない(届く module)\n{stderr}");
    }
    for i in 3..MODULES {
        let found = files_containing(cache.path(), &format!("Box{i}"));
        assert!(found.is_empty(), "mod{i}(mod0 から届かない)の事実が置かれた: {found:?}\n{stderr}");
    }
    // 逆向きの依存が要らない file(mod0)は、その事実の種類を 1 つも置かない。
    let kinds = stored_kinds(cache.path());
    for kind in ["failure-types.json", "program-params.json", "handler-argument-classes.json"] {
        assert!(!kinds.iter().any(|k| k == kind), "mod0 に要らない {kind} が置かれた: {kinds:?}\n{stderr}");
    }
}

/// 逆向きの依存のうち、引数に渡された Program(呼び手が別の file)が要る file の 1 file の実行は、呼び先の名を字面に含む file だけを解析する:
/// repo 全体の表を通らず、呼び手でない module(3〜)の defk の名も、引数に渡された Program の事実も cache に置かない(冷えた作業木の最初の 1 回が
/// repo の Hy の全部を作り直して 8〜13 秒かかっていた・card ki-58bfad2c13a1 の残り)。答えは `a_single_file_run_answers_like_the_whole_run_for_that_file` が守る。
#[test]
fn a_single_file_run_that_needs_the_callers_reads_only_the_files_that_spell_the_callee() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (report, stderr) = single_report(dir.path(), cache.path(), "app/core/hof.hy");
    // 検が空振りでないことの確かめ: 呼び手(user.hy)が fnk を渡すので、hof.hy の素の呼びは当たる。
    assert!(keys_of(&report, "app/core/hof.hy").iter().any(|k| k.contains("DOEFF126")), "hof.hy に DOEFF126 が当たらない(検の材料の誤り)\n{stderr}");
    assert!(!stderr.contains("doeff-linter-timing: facts-cache.resolve "), "repo 全体の表を通った(呼び手を探すのに全 file を作り直している)\n{stderr}");
    for i in 3..MODULES {
        let found = files_containing(cache.path(), &format!("step_{i}_0"));
        assert!(found.is_empty(), "mod{i}(hof.hy の呼び手でない)の defk の名が置かれた: {found:?}\n{stderr}");
    }
    let kinds = stored_kinds(cache.path());
    assert!(!kinds.iter().any(|k| k == "program-params.json"), "repo 全体の引数に渡された Program の事実が置かれた: {kinds:?}\n{stderr}");
}

/// 逆向きの依存のうち、失敗の型の宣言(別の file の defeffect の `:failure`)が要る file の 1 file の実行も、型の名を字面に含む宣言の file だけを読む:
/// repo 全体の表を通らず、置く失敗の型の事実も、型の名を綴る宣言の file の分だけ(冷えた作業木の最初の 1 回が 19〜27 秒かかっていた・card ki-58bfad2c13a1 の残り)。
/// 宣言の file の読み解きを置くのは、次の実行が同じ file を読み解き直さないため(混んだ機体の hook の CPU の大半だった・agora-redesign #1022)。置き場の鍵は全体の実行と同じ。
/// 答えは `a_single_file_run_answers_like_the_whole_run_for_that_file` が守る。
#[test]
fn a_single_file_run_that_needs_the_failure_types_reads_only_the_declarations_that_spell_the_type() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (report, stderr) = single_report(dir.path(), cache.path(), "app/core/needs.hy");
    // 検が空振りでないことの確かめ: effs.hy が Unreachable を失敗の型と宣言するので、needs.hy の腕は DOEFF122 に当たる。
    assert!(keys_of(&report, "app/core/needs.hy").iter().any(|k| k.contains("DOEFF122")), "needs.hy に DOEFF122 が当たらない(検の材料の誤り)\n{stderr}");
    assert!(!stderr.contains("doeff-linter-timing: facts-cache.resolve "), "repo 全体の表を通った(宣言を探すのに全 file を作り直している)\n{stderr}");
    // 置かれた失敗の型の事実は、型の名を綴る宣言の file(effs.hy の 1 つ)だけ。
    let stored: Vec<_> = walkdir::WalkDir::new(cache.path().join("objects").join("failure-types.json"))
        .into_iter()
        .filter_map(Result::ok)
        .filter(|e| e.file_type().is_file())
        .collect();
    assert_eq!(stored.len(), 1, "失敗の型の事実が宣言の file の数と合わない: {:?}\n{stderr}", stored.iter().map(|e| e.path().to_path_buf()).collect::<Vec<_>>());
    // 次の実行は、置いた読み解きを読む(同じ答え)。
    let (again, _) = single_report(dir.path(), cache.path(), "app/core/needs.hy");
    assert_eq!(keys_of(&again, "app/core/needs.hy"), keys_of(&report, "app/core/needs.hy"), "置いた読み解きから引いた答えが違う");
}

/// 1 file の実行は、repo 全体の file の事実を引く表(`facts-cache.resolve`)も、層の module の全部の走査(`layer-files`)も通らない。
#[test]
fn a_single_file_run_does_not_walk_the_whole_repo_tables() {
    let dir = tempfile::TempDir::new().unwrap();
    checkout(dir.path());
    let cache = tempfile::TempDir::new().unwrap();
    let (_, stderr) = single_report(dir.path(), cache.path(), "app/core/mod0.hy");
    for stage in ["facts-cache.resolve", "layer-files"] {
        assert!(!stderr.contains(&format!("doeff-linter-timing: {stage} ")), "{stage} を通った(repo 全体を読んでいる)\n{stderr}");
    }
}
