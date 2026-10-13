//! DOEFF210(Hy の program は壁の時計で眠らない — doeff の ADR-DOE-TIME-001 R1・R2・card ki-3c3d85253c2c)の検。
//! binary を走らせ、`--output-format editor-json` の出力を見る。
//!
//!   * 検体 tests/fixtures/program_sleep/waits.hy の「; 鳴る」の行の集合と、当たりの行の集合が同じ(極性 — 規則を外す・handler の
//!     実装の除外を外す・除外を足すと、どれも集合が合わなくなって赤)。
//!   * 母集団は設定の `[tool.doeff-linter.program_sleep]` だけが決める(節が無ければ判じない・除く区切りの下は当てない)。
//!   * 1 file の実行(エディタの `--stdin --path`)も同じ当たりを出す。
//!   * この repo(doeff)の本体の Hy の program に当たりが無い — 根の pyproject.toml の節が規則を効かせていて、0 件である事。
//!     新しい素の sleep を program に書くと、この検が file と行を名指して赤になる(日次の全体検証の処理ステージ rust)。

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF210";
/// 検体の中の、鳴るべき行の印。
const FIRES: &str = "; 鳴る";
/// program の置き場を app/ に置いた設定(検と例は外す)。
const CONFIG: &str = "[tool.doeff-linter]\n\n[tool.doeff-linter.program_sleep]\npaths = [\"app\"]\nexclude_parts = [\"tests\", \"examples\"]\n";

/// 検体の file(鳴る行に印)。
fn fixture() -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/program_sleep/waits.hy");
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("検体 {} を読めない: {}", path.display(), error))
}

/// この repo(doeff)の根。
fn doeff_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..").canonicalize().expect("doeff の根が在る")
}

/// 一時の repo(pyproject.toml と file の列)。
fn repo(config: &str, files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// binary を root で走らせ、editor-json を返す(stdin を渡せる)。
fn editor(root: &Path, args: &[&str], stdin: Option<&str>) -> Value {
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .args(args)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    serde_json::from_str(&stdout).unwrap_or_else(|error| panic!("JSON でない({}): {}\n{}", error, stdout, String::from_utf8_lossy(&output.stderr)))
}

/// 報告の中の、この規則の違反(JSON の値のまま)。
fn violations(report: &Value) -> Vec<&Value> {
    report["violations"].as_array().expect("violations の列").iter().filter(|v| v["rule"] == RULE).collect()
}

/// この規則の当たりの行(1 始まり)の集合。
fn hit_lines(report: &Value) -> BTreeSet<u64> {
    violations(report).iter().map(|v| v["range"]["start"]["line"].as_u64().expect("行") + 1).collect()
}

/// source の中の、行末に印の付いた行(1 始まり)の集合。
fn marked_lines(source: &str) -> BTreeSet<u64> {
    source.lines().zip(1u64..).filter(|(line, _)| line.trim_end().ends_with(FIRES)).map(|(_, number)| number).collect()
}

/// この規則をこの実行が判じたか(editor-json の judged_rules)。
fn judged(report: &Value) -> bool {
    report["judged_rules"].as_array().expect("judged_rules の列").iter().any(|id| id == RULE)
}

/// この規則の判定が repo の設定につながっているか(editor-json の rules の wired — 置き場の節が在る時だけ真)。
fn wired(report: &Value) -> bool {
    report["rules"].as_array().expect("rules の列").iter().any(|rule| rule["rule"] == RULE && rule["wired"] == true)
}

#[test]
fn only_the_marked_lines_fire() {
    let source = fixture();
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 7, "検体の印の行の数が変わった — 鳴る形を足した・消したなら、この数も直す");
    let dir = repo(CONFIG, &[("app/waits.hy", &source)]);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "当たりの行が検体の印の行と違う: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_program_and_the_callee() {
    let dir = repo(CONFIG, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    let keys: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("鍵")).collect();
    let expected: BTreeSet<&str> = [
        "app/waits.hy::DOEFF210::retry-after-a-pause::time.sleep",
        "app/waits.hy::DOEFF210::poll-until-ready::asyncio.sleep",
        "app/waits.hy::DOEFF210::waits-through-aliases::time.sleep",
        "app/waits.hy::DOEFF210::waits-through-aliases::asyncio.sleep",
        "app/waits.hy::DOEFF210::settle-then-go::time.sleep",
        "app/waits.hy::DOEFF210::sleeps-inside-a-nested-callable::time.sleep",
        "app/waits.hy::DOEFF210::program-without-tags::time.sleep",
    ]
    .into_iter()
    .collect();
    assert_eq!(keys, expected);
    for violation in violations(&report) {
        assert_eq!(violation["level"], "critical", "{}", violation);
        assert_eq!(violation["severity"], "error", "{}", violation);
    }
}

#[test]
fn the_rule_judges_nothing_without_its_section() {
    // 置き場の宣言が無い repo には当てない(0 件を「守れている」と読ませない — 他の節が在っても rules の wired は偽)。
    let config = "[tool.doeff-linter]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n";
    let dir = repo(config, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "節が無いのに {} の判定がつながっていると名乗った", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn places_outside_the_section_are_not_judged() {
    // 置き場の外(lib/)と、除く区切り(tests・examples)の下の file は当てない。
    let source = fixture();
    let dir = repo(
        CONFIG,
        &[("lib/waits.hy", &source), ("app/tests/waits.hy", &source), ("app/pkg/examples/waits.hy", &source), ("app/pkg/waits.hy", &source)],
    );
    let report = editor(dir.path(), &[], None);
    let files: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("鍵").split("::").next().unwrap()).collect();
    assert_eq!(files, ["app/pkg/waits.hy"].into_iter().collect());
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // エディタの保存前の中身(--stdin --path)にも当てる — 書いた時点で見える。
    let source = fixture();
    let dir = repo(CONFIG, &[("app/waits.hy", "(defk clean [] {:pre [] :post [(: % None)]} None)\n")]);
    let report = editor(dir.path(), &["--stdin", "--path", "app/waits.hy"], Some(&source));
    assert!(judged(&report), "1 file の実行が {} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // disable に書けば判じない(この検は、下の「この repo の本体」の検が disable を赤にする訳を示す対)。
    let config = CONFIG.replace("[tool.doeff-linter]\n", "[tool.doeff-linter]\ndisable = [\"DOEFF210\"]\n");
    let dir = repo(&config, &[("app/waits.hy", &fixture())]);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "disable に書いた {} を判じたと名乗った: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_hy_programs_of_this_repo_do_not_sleep() {
    // doeff の根の設定(pyproject.toml の [tool.doeff-linter.program_sleep])で、本体の Hy の program に当たりが無い。
    // 節を消す・DOEFF210 を disable に足すと「判じていない」で赤、program に素の sleep を書くと当たりで赤。
    let root = doeff_root();
    let report = editor(&root, &["doeff", "packages"], None);
    assert!(
        judged(&report) && wired(&report),
        "doeff の根の設定が {} を判じていない — pyproject.toml の [tool.doeff-linter.program_sleep] の節と disable を確かめる",
        RULE
    );
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(
        found.is_empty(),
        "Hy の program が壁の時計で眠っている — doeff-time の効果(Delay など)で待つ(handler からだけ呼ばれる helper なら :role foundation を名乗る):\n{}",
        found.join("\n")
    );
}

#[test]
fn the_sample_of_a_sleeping_program_is_found_under_the_repo_s_own_settings() {
    // 上の 0 件が「母集団が空だから」でない事: doeff の根の設定のまま、本体の置き場に検体を 1 つ置いた形(保存前の中身)は当たる。
    let root = doeff_root();
    let source = fixture();
    let report = editor(&root, &["--stdin", "--path", "packages/doeff-agents/src/doeff_agents/sample_waits.hy"], Some(&source));
    assert_eq!(hit_lines(&report), marked_lines(&source));
    // 検の置き場(tests/)は、この規則の母集団の外(Hy の検の扱いは別の card ki-bb3ee40d0af2 — ki-6aa7cb5ffb83 の続き)。
    let in_tests = editor(&root, &["--stdin", "--path", "packages/doeff-agents/tests/sample_waits.hy"], Some(&source));
    assert!(violations(&in_tests).is_empty(), "{:#?}", violations(&in_tests));
}
