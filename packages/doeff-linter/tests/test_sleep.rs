//! DOEFF211(Hy の検は realtime を名乗らない限り壁の時計で眠らない — doeff の ADR-DOE-TIME-001 R3・card ki-bb3ee40d0af2)の検。
//! binary を走らせ、`--output-format editor-json` の出力を見る。
//!
//!   * 検体 tests/fixtures/test_sleep/sleeps.hy の「; 鳴る」の行の集合と、当たりの行の集合が同じ(極性 — 規則を外す・名乗りの読みを
//!     狭める・広げる・言い換えの読みを外すと、どれも集合が合わなくなって赤)。
//!   * module の段で名乗る検体 realtime_module.hy は当たりが無く、名乗りの 1 行を消すと当たる(0 件が空の母集団のせいでない)。
//!   * 母集団は設定の `[tool.doeff-linter.test_sleep]` だけが決める(節が無ければ判じない・検の置き場の dir と検の名の file・除く dir)。
//!   * 1 file の実行(エディタの `--stdin --path`)も同じ当たりを出す。
//!   * この repo(doeff)の Hy の検に当たりが無い — 根の pyproject.toml の節が規則を効かせていて、0 件である事。名乗らない素の
//!     sleep を Hy の検に書くと、この検が file と行を名指して赤になる(日次の全体検証の処理ステージ rust)。名乗った検が名簿
//!     tests/realtime_tests.toml と 1 対 1 である事は tests/semgrep/test_realtime_tests_registry.py が見る。

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF211";
/// 検体の中の、鳴るべき行の印。
const FIRES: &str = "; 鳴る";
/// module の段の名乗りの 1 行(検体 realtime_module.hy の中)。
const MODULE_DECLARATION: &str = "(val pytestmark [pytest.mark.slow pytest.mark.realtime])\n";
/// 検の置き場を「名が tests の dir の下」と「検の名の file」に置き、規則の検体の dir と、名指しの script の file を除いた設定。
const CONFIG: &str = "[tool.doeff-linter]\n\n[tool.doeff-linter.test_sleep]\nparts = [\"tests\"]\nnames = [\"test_*.hy\", \"*_test.hy\"]\nexclude = [\"pkg/tests/samples\", \"pkg/tests/stub_cli/fake_cli.hy\"]\n";

/// 検体の file(鳴る行に印)。
fn fixture(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/test_sleep").join(name);
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

/// この規則の当たりの file(鍵の頭 — repo の根からの path)の集合。
fn hit_files(report: &Value) -> BTreeSet<&str> {
    violations(report).iter().map(|v| v["key"].as_str().expect("鍵").split("::").next().unwrap()).collect()
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
    let source = fixture("sleeps.hy");
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 14, "検体の印の行の数が変わった — 鳴る形を足した・消したなら、この数も直す");
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", &source)]);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "当たりの行が検体の印の行と違う: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_definition_and_the_callee() {
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    let keys: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("鍵")).collect();
    let expected: BTreeSet<&str> = [
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-for-the-server-by-sleeping::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::poll-until-ready::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::all-closed::asyncio.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-through-aliases::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-waits-through-aliases::asyncio.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-rewords-the-sleep-as-an-event-nobody-sets::threading.Event.wait",
        "pkg/tests/test_waits.hy::DOEFF211::test-marked-with-another-mark::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-with-a-map-that-is-not-the-meta::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::helper-with-marks-in-its-contract::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-decorated-with-another-mark::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::test-with-the-mark-in-its-parameters::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::declares-inside-a-function::time.sleep",
        "pkg/tests/test_waits.hy::DOEFF211::<module>::time.sleep",
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
fn a_module_that_declares_realtime_has_no_hits_and_loses_them_only_by_the_declaration() {
    let source = fixture("realtime_module.hy");
    assert!(source.contains(MODULE_DECLARATION), "検体の module の段の名乗りの行が変わった");
    let dir = repo(CONFIG, &[("pkg/tests/support_rig.hy", &source), ("pkg/tests/undeclared_rig.hy", &source.replace(MODULE_DECLARATION, ""))]);
    let report = editor(dir.path(), &[], None);
    // 名乗った file は当たらず、名乗りの 1 行を消した同じ中身は 2 つの眠りが当たる。
    assert_eq!(hit_files(&report), ["pkg/tests/undeclared_rig.hy"].into_iter().collect());
    assert_eq!(violations(&report).len(), 2, "{:#?}", violations(&report));
}

#[test]
fn the_rule_judges_nothing_without_its_section() {
    // 置き場の宣言が無い repo には当てない(0 件を「守れている」と読ませない — 他の節が在っても rules の wired は偽)。
    let config = "[tool.doeff-linter]\n\n[tool.doeff-linter.program_sleep]\npaths = [\"pkg\"]\n";
    let dir = repo(config, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "節が無いのに {} の判定がつながっていると名乗った", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn the_population_is_the_test_places_and_the_test_names() {
    // 検の置き場 = 区切りに tests を持つ file(深さを問わず・検の名でない道具も)と、どこに在っても検の名の file。
    // 本体(src)・名に tests を含むだけの dir・除く dir の下・名指しで除いた file は当てない(名指した file の隣の file は当てる)。
    let source = fixture("sleeps.hy");
    let dir = repo(
        CONFIG,
        &[
            ("pkg/tests/test_waits.hy", &source),
            ("pkg/tests/deep/support_rig.hy", &source),
            ("pkg/suite/test_waits.hy", &source),
            ("pkg/suite/waits_test.hy", &source),
            ("tests/rig.hy", &source),
            ("pkg/suite/harness.hy", &source),
            ("pkg/src/app/waits.hy", &source),
            ("pkg/src/contests/waits.hy", &source),
            ("pkg/tests/samples/test_waits.hy", &source),
            ("pkg/tests/stub_cli/fake_cli.hy", &source),
            ("pkg/tests/stub_cli/other_cli.hy", &source),
        ],
    );
    let report = editor(dir.path(), &[], None);
    let expected: BTreeSet<&str> =
        ["pkg/tests/test_waits.hy", "pkg/tests/deep/support_rig.hy", "pkg/suite/test_waits.hy", "pkg/suite/waits_test.hy", "tests/rig.hy", "pkg/tests/stub_cli/other_cli.hy"]
            .into_iter()
            .collect();
    assert_eq!(hit_files(&report), expected);
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // エディタの保存前の中身(--stdin --path)にも当てる — 書いた時点で見える。
    let source = fixture("sleeps.hy");
    let dir = repo(CONFIG, &[("pkg/tests/test_waits.hy", "(deftest test-clean (assert True))\n")]);
    let report = editor(dir.path(), &["--stdin", "--path", "pkg/tests/test_waits.hy"], Some(&source));
    assert!(judged(&report), "1 file の実行が {} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // disable に書けば判じない(この検は、下の「この repo の検」の検が disable を赤にする訳を示す対)。
    let config = CONFIG.replace("[tool.doeff-linter]\n", "[tool.doeff-linter]\ndisable = [\"DOEFF211\"]\n");
    let dir = repo(&config, &[("pkg/tests/test_waits.hy", &fixture("sleeps.hy"))]);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "disable に書いた {} を判じたと名乗った: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_hy_tests_of_this_repo_sleep_only_where_they_declare_realtime() {
    // doeff の根の設定(pyproject.toml の [tool.doeff-linter.test_sleep])で、repo の Hy の検に当たりが無い。
    // 節を消す・DOEFF211 を disable に足すと「判じていない」で赤、名乗らない検に素の sleep を書くと当たりで赤。
    let root = doeff_root();
    let report = editor(&root, &["."], None);
    assert!(
        judged(&report) && wired(&report),
        "doeff の根の設定が {} を判じていない — pyproject.toml の [tool.doeff-linter.test_sleep] の節と disable を確かめる",
        RULE
    );
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(
        found.is_empty(),
        "Hy の検が名乗らずに壁の時計で眠っている — 出来事か仮想の時計で待つ。実時間が対象なら realtime を名乗り(deftest の :marks か module の pytestmark)、tests/realtime_tests.toml に理由と一緒に載せる:\n{}",
        found.join("\n")
    );
}

#[test]
fn the_sample_of_a_sleeping_test_is_found_under_the_repo_s_own_settings() {
    // 上の 0 件が「母集団が空だから」でない事: doeff の根の設定のまま、検の置き場に検体を 1 つ置いた形(保存前の中身)は当たる。
    let root = doeff_root();
    let source = fixture("sleeps.hy");
    for place in [
        "packages/doeff-agents/tests/sample_waits.hy",
        "tests/sample_waits.hy",
        "packages/doeff-cluster/tests/fixtures/sample_job.hy",
        "packages/doeff-claude-code/tests/stub_cli/sample_cli.hy",
        "examples/sample/test_waits.hy",
    ] {
        let report = editor(&root, &["--stdin", "--path", place], Some(&source));
        assert_eq!(hit_lines(&report), marked_lines(&source), "{} に置いた検体の当たりが印の行と違う", place);
    }
    // 本体の置き場(program の眠りは DOEFF210 が持つ)と、規則の検体の dir は、この規則の母集団の外。
    for place in ["packages/doeff-agents/src/doeff_agents/sample_waits.hy", "packages/doeff-linter/tests/fixtures/test_sleep/sample_waits.hy", "tests/semgrep/fixtures/sample_waits.hy"] {
        let outside = editor(&root, &["--stdin", "--path", place], Some(&source));
        assert!(violations(&outside).is_empty(), "{}: {:#?}", place, violations(&outside));
    }
}
