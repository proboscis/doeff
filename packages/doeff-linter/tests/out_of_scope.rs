//! 名指しの範囲の外(agora-redesign #2821)の検 — 根を `src` にした一時の package で binary を走らせ、根の外の Hy の file(`tests/`)を
//! 名指した時に「対象の外」と名指して緑と分けることを確かめる(仕様 1 節「名指しの範囲の外」)。
//!
//! 失敗ケース: 根の外の検の file に違反(生の副作用)を書いて名指す — 以前は層の規則が file を判じず、終了コード 0 で何も出さなかった
//! (doeff-cluster の tests/ の .hy が hook でも手元の確かめでも測られていなかった形)。今は 1 行で名指し、終了コード 3。

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Output};

const CONFIG: &str = r#"
[tool.doeff-linter]
root = "src"
enable = ["DOEFF106"]

[tool.doeff-linter.layers]
order = ["core", "foundation"]
paths = { core = "app/core", foundation = "app/foundation" }

[tool.doeff-linter.layers.allow_imports]
core = ["core"]
foundation = ["foundation"]

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation"]
"#;

const PLAIN: &str = "(defn plan [x] x)\n";
const RAW: &str = "(import time)\n(defn now [] (time.time))\n";

/// 根 = src の一時の package: 根の下に層 core の file 1 つ、根の外の tests/ に違反を書いた Hy の file と Python の file を 1 つずつ。
fn package() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), CONFIG).unwrap();
    std::fs::create_dir_all(dir.path().join("src/app/core")).unwrap();
    std::fs::create_dir_all(dir.path().join("src/app/foundation")).unwrap();
    std::fs::create_dir_all(dir.path().join("tests")).unwrap();
    std::fs::write(dir.path().join("src/app/core/plan.hy"), PLAIN).unwrap();
    std::fs::write(dir.path().join("tests/test_clock.hy"), RAW).unwrap();
    std::fs::write(dir.path().join("tests/test_plain.py"), "def test_x():\n    assert True\n").unwrap();
    dir
}

fn lint(dir: &Path, args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log"])
        .args(args)
        .current_dir(dir)
        .env("DOEFF_LINTER_NO_CACHE", "1")
        .output()
        .unwrap()
}

fn editor_report(output: &Output) -> Value {
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({}): {}", e, stdout))
}

/// stderr に「対象の外」の行で rel(package の dir からの path)の file が名指されているか — 名指しの path は正規化した絶対の path で
/// 出る(違反の path と同じ形)ので、`/<rel>(` の綴りで照らす。
fn named_outside(stderr: &str, rel: &str) -> bool {
    stderr.lines().any(|line| line.contains("対象の外 — ") && line.contains(&format!("/{}(", rel)))
}

#[test]
fn a_named_hy_file_outside_the_root_is_named_and_not_green() {
    // 失敗ケース: 根の外の tests/ の Hy の file(生の副作用を書いた)を名指す — 層の規則は判じないので、黙らずに名指し、終了コード 3。
    let dir = package();
    let output = lint(dir.path(), &["tests/test_clock.hy"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(named_outside(&stderr, "tests/test_clock.hy"), "{}", stderr);
    assert!(stderr.contains("1 個が対象の外"), "{}", stderr);
    assert_eq!(output.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_named_hy_file_under_the_root_is_judged_as_before() {
    // 通る例: 根の下の file を名指すと今までどおり — 対象の外の行は出ず、破れが無ければ終了コード 0。
    let dir = package();
    let output = lint(dir.path(), &["src/app/core/plan.hy"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains("対象の外"), "{}", stderr);
    assert_eq!(output.status.code(), Some(0), "{}", stderr);
    // 同じ根の下の file に違反を書くと、層の規則が判じて終了コード 1(対象の外とは分かれる)。
    std::fs::write(dir.path().join("src/app/core/plan.hy"), RAW).unwrap();
    let raw = lint(dir.path(), &["src/app/core/plan.hy"]);
    assert_eq!(raw.status.code(), Some(1), "{}", String::from_utf8_lossy(&raw.stderr));
}

#[test]
fn the_declaration_file_outside_the_root_is_not_out_of_scope() {
    // 失敗ケース: package の根(src)の外に置いた service と層の宣言(architecture.hy)は宣言の規則が判じる — doeff-cluster の commit の
    // hook(scripts/lint-doeff-cluster.sh)は path を渡す時に必ずこの file を足すので、ここを対象の外に数えると毎回終了コード 3 になる。
    let dir = tempfile::TempDir::new().unwrap();
    let files = [
        ("pyproject.toml", "[tool.doeff-linter]\nroot = \"src\"\narchitecture = \"architecture.hy\"\ndisable = [\"DOEFF114\", \"DOEFF115\"]\n"),
        (
            "architecture.hy",
            "(defarchitecture pkg :root \"cluster\" :layers [(layer foundation :summary \"土台\")] :foundation foundation)\n",
        ),
        ("src/cluster/coord.hy", "(defk run [] 1)\n"),
        ("tests/test_coord.hy", RAW),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let hook = lint(dir.path(), &["src/cluster/coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&hook.stderr);
    assert!(!stderr.contains("対象の外"), "{}", stderr);
    assert_eq!(hook.status.code(), Some(0), "{}", stderr);
    // 同じ package の根の外の検の file は、宣言と並べて名指しても対象の外。
    let tests = lint(dir.path(), &["tests/test_coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&tests.stderr);
    assert!(named_outside(&stderr, "tests/test_coord.hy"), "{}", stderr);
    assert!(!named_outside(&stderr, "architecture.hy"), "{}", stderr);
    assert_eq!(tests.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_named_directory_opens_to_its_hy_files_and_skips_python() {
    // dir を名指すと、その下の Hy の file に開いて名指す。Python の file は Python の規則が名指しの path から集めて測るので数えない。
    let dir = package();
    let output = lint(dir.path(), &["tests"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(named_outside(&stderr, "tests/test_clock.hy"), "{}", stderr);
    assert!(!stderr.contains("test_plain.py"), "{}", stderr);
    assert_eq!(output.status.code(), Some(3), "{}", stderr);
}

#[test]
fn the_editor_report_carries_the_out_of_scope_list() {
    // editor-json(commit の hook の子)は一番上の欄 out_of_scope に載せ、終了コード 3。根の下だけなら空の列で 0・名指しが無ければ null。
    let dir = package();
    let outside = lint(dir.path(), &["--output-format", "editor-json", "tests/test_clock.hy"]);
    // 違反の path と同じく正規化した絶対の path(1 つ)。
    let listed = editor_report(&outside)["out_of_scope"].clone();
    assert_eq!(listed.as_array().map(Vec::len), Some(1), "{}", listed);
    assert!(listed[0].as_str().is_some_and(|p| p.starts_with('/') && p.ends_with("/tests/test_clock.hy")), "{}", listed);
    assert_eq!(outside.status.code(), Some(3), "{}", String::from_utf8_lossy(&outside.stderr));
    let inside = lint(dir.path(), &["--output-format", "editor-json", "src/app/core/plan.hy"]);
    assert_eq!(editor_report(&inside)["out_of_scope"], serde_json::json!([]));
    assert_eq!(inside.status.code(), Some(0), "{}", String::from_utf8_lossy(&inside.stderr));
    let whole = lint(dir.path(), &["--output-format", "editor-json"]);
    assert_eq!(editor_report(&whole)["out_of_scope"], Value::Null);
}

#[test]
fn the_commit_hook_reads_the_out_of_scope_list() {
    // commit の hook は子の報告の out_of_scope を読んで名指す(欄が無い・null なら空 — 以前の版の子でも落ちない)。
    let report = serde_json::json!({"violations": [], "out_of_scope": ["tests/test_clock.hy"]});
    assert_eq!(doeff_linter::commit_hook::out_of_scope_of(&report), vec!["tests/test_clock.hy".to_string()]);
    assert!(doeff_linter::commit_hook::out_of_scope_of(&serde_json::json!({"violations": []})).is_empty());
    assert!(doeff_linter::commit_hook::out_of_scope_of(&serde_json::json!({"out_of_scope": null})).is_empty());
}

// ---- 歩く範囲に足す dir(設定の include — agora-redesign #2821 の案 A-1) ----

/// 根 = src の package。`include` を書くと根の外の tests/ も歩き、定義の書き方の規則(ここでは critical の DOEFF126 だけ)が判じる。
fn walked_package(include: bool, test_source: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let settings = format!(
        "[tool.doeff-linter]\nroot = \"src\"\narchitecture = \"architecture.hy\"\n{}enable = [\"DOEFF126\"]\n\
         [tool.doeff-linter.definitions]\npaths = [\"../tests\"]\ntest_paths = [\"../tests/**/test_*.hy\"]\n",
        if include { "include = [\"tests\"]\n" } else { "" }
    );
    let files = [
        ("pyproject.toml", settings.as_str()),
        (
            "architecture.hy",
            "(defarchitecture pkg :root \"cluster\" :layers [(layer foundation :summary \"土台\")] :foundation foundation)\n",
        ),
        ("src/cluster/coord.hy", "(defk plan [x] {:tags {:context \"c\" :role \"program\"}} x)\n"),
        ("tests/test_coord.hy", test_source),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// 検の file の中で src の defk を素で呼び、答えとして比べに使う(Program が値として流れる — DOEFF126 の critical)。
const BARE_TEST: &str = "(import cluster.coord [plan])\n(deftest test-plan (assert (= (plan 1) 1)))\n";
/// 同じ検を `!` で答えにして呼ぶ(直した形)。
const BOUND_TEST: &str = "(import cluster.coord [plan])\n(deftest test-plan (assert (= (! (plan 1)) 1)))\n";

fn rule_keys(report: &Value, rule: &str) -> Vec<String> {
    report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| v["rule"] == rule)
        .map(|v| v["key"].as_str().unwrap_or("").to_string())
        .collect()
}

#[test]
fn an_included_directory_outside_the_root_is_judged_by_the_definition_rules() {
    // 失敗ケース: 根の外の tests/ の検が src の defk を素で呼ぶ。include が在れば定義の規則が判じて DOEFF126 で赤(名乗りは `..` を含む
    // 根からの相対)。include が無ければ判じられず、名指すと「対象の外」で終了コード 3(以前の形 — 測っていない)。
    let walked = walked_package(true, BARE_TEST);
    let whole = lint(walked.path(), &["--output-format", "editor-json"]);
    let report = editor_report(&whole);
    assert_eq!(rule_keys(&report, "DOEFF126"), vec!["../tests/test_coord.hy::DOEFF126::test_plan::plan".to_string()], "{}", report);
    assert_eq!(whole.status.code(), Some(1), "{}", String::from_utf8_lossy(&whole.stderr));
    let named = lint(walked.path(), &["tests/test_coord.hy", "architecture.hy"]);
    let stderr = String::from_utf8_lossy(&named.stderr);
    let stdout = String::from_utf8_lossy(&named.stdout);
    assert!(!stderr.contains("対象の外"), "{}", stderr);
    assert!(stdout.contains("DOEFF126") || stderr.contains("DOEFF126"), "{}\n{}", stdout, stderr);
    assert_eq!(named.status.code(), Some(1), "{}\n{}", stdout, stderr);

    let unwalked = walked_package(false, BARE_TEST);
    let report = editor_report(&lint(unwalked.path(), &["--output-format", "editor-json"]));
    assert!(rule_keys(&report, "DOEFF126").is_empty(), "{}", report);
    let named = lint(unwalked.path(), &["tests/test_coord.hy"]);
    let stderr = String::from_utf8_lossy(&named.stderr);
    assert!(named_outside(&stderr, "tests/test_coord.hy"), "{}", stderr);
    assert_eq!(named.status.code(), Some(3), "{}", stderr);
}

#[test]
fn a_fixed_test_in_the_included_directory_is_green() {
    // 通る例: 同じ検を `!` で答えにして呼ぶと緑(include の dir の file も根の下の file と同じ判定)。
    let dir = walked_package(true, BOUND_TEST);
    let output = lint(dir.path(), &["tests/test_coord.hy", "architecture.hy"]);
    assert_eq!(output.status.code(), Some(0), "{}\n{}", String::from_utf8_lossy(&output.stdout), String::from_utf8_lossy(&output.stderr));
}

#[test]
fn a_missing_included_directory_is_a_setting_error() {
    // 無い dir を include に書くと、黙って何も歩かない形にせず設定の誤りとして名指す。
    let dir = walked_package(true, BOUND_TEST);
    std::fs::remove_dir_all(dir.path().join("tests")).unwrap();
    let output = lint(dir.path(), &["--output-format", "editor-json"]);
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(stderr.contains("include の dir"), "{}", stderr);
}
