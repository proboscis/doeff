//! DOEFF175(宣言を切り替えた effect の古い分岐 — ADR-DOE-CORE-EFFECTS-003 R14 (4)・law declarations-switch-one-effect-at-a-time)の検。
//! binary を走らせ、`--output-format editor-json` の出力を見る。
//!
//!   * 検体 tests/fixtures/stale_outcome_branch/branches.hy の「; 鳴る」の行の集合と、当たりの行の集合が同じ(極性 — 規則を外す・形を
//!     狭める・広げる・宣言の読みを外すと、どれも集合が合わなくなって赤)。鳴らす形 = match の腕・isinstance・型の tuple・cond・型を注釈した束ね・
//!     `!`(束ねる形と直に分ける形)・:absent の付いた束ね・module の別名・2 つ目の effect・検の呼び手。鳴らさない形 = :value の型・
//!     成功の型・宣言の無い effect・defhandler の節・別の module の同じ名の型・別の effect の宣言・束ね直した名・引数で隠れた名・
//!     maybe で包んだ答え・pattern の欄の中の型・defk を経由した答え・quote。
//!   * 宣言は検体 effects.hy の defeffect だけが決める(宣言を消すと当たりが 0 になり、宣言の無い effect に宣言を足すと当たる)。
//!   * 母集団は設定の `[tool.doeff-linter.definitions]` が決める(節が無ければ判じない・置き場の外は当てない)。
//!   * 1 file の実行(エディタの `--stdin --path`)も同じ当たりを出す。
//!   * この repo(doeff)の本番の code に当たりが無い — 根の pyproject.toml の設定が規則を効かせていて、0 件である事(日次の全体検証の
//!     処理ステージ rust)。ADR(docs/adr)と doeff-core-effects の probe の検は、doeff の定義の母集団の外(`paths` は 3 つの本体の dir だけ)。

use serde_json::Value;
use std::collections::BTreeSet;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

const RULE: &str = "DOEFF175";
/// 検体の中の、鳴るべき行の印。
const FIRES: &str = "; 鳴る";
/// 定義の規則の母集団を app/ に置いた設定(この規則だけを有効にする)。
const CONFIG: &str = "[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n";

/// 検体の file(鳴る行に印)。
fn fixture(name: &str) -> String {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/stale_outcome_branch").join(name);
    std::fs::read_to_string(&path).unwrap_or_else(|error| panic!("検体 {} を読めない: {}", path.display(), error))
}

/// 検体の repo の file の列(宣言・別の module の同じ名の型・使う側)。
fn files() -> Vec<(&'static str, String)> {
    vec![("app/effects.hy", fixture("effects.hy")), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))]
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

/// 検体の repo(設定を渡す)。
fn fixture_repo(config: &str) -> tempfile::TempDir {
    let owned = files();
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    repo(config, &borrowed)
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

/// この規則の当たりの鍵の集合。
fn keys(report: &Value) -> BTreeSet<String> {
    violations(report).iter().map(|v| v["key"].as_str().expect("鍵").to_string()).collect()
}

/// source の中の、行末に印の付いた行(1 始まり)の集合。
fn marked_lines(source: &str) -> BTreeSet<u64> {
    source.lines().zip(1u64..).filter(|(line, _)| line.trim_end().ends_with(FIRES)).map(|(_, number)| number).collect()
}

/// この規則をこの実行が判じたか(editor-json の judged_rules)。
fn judged(report: &Value) -> bool {
    report["judged_rules"].as_array().expect("judged_rules の列").iter().any(|id| id == RULE)
}

/// この規則の判定が repo の設定につながっているか(editor-json の rules の wired — 定義の母集団の節が在る時だけ真)。
fn wired(report: &Value) -> bool {
    report["rules"].as_array().expect("rules の列").iter().any(|rule| rule["rule"] == RULE && rule["wired"] == true)
}

#[test]
fn only_the_marked_lines_fire() {
    let source = fixture("branches.hy");
    let expected = marked_lines(&source);
    assert_eq!(expected.len(), 17, "検体の印の行の数が変わった — 鳴る形を足した・消したなら、この数も直す");
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report), "{} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), expected, "当たりの行が検体の印の行と違う: {:#?}", violations(&report));
}

#[test]
fn a_hit_is_critical_and_keyed_by_the_definition_the_name_and_the_type() {
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    let expected: BTreeSet<String> = [
        "match-an-absent-arm::found::Missing",
        "match-a-failure-arm::found::Unreachable",
        "match-an-alternation::found::Missing",
        "match-an-alternation::found::Unreachable",
        "isinstance-an-absent::found::Missing",
        "isinstance-a-tuple::found::Missing",
        "isinstance-a-tuple::found::Unreachable",
        "isinstance-a-union::found::Unreachable",
        "cond-branch::found::Unreachable",
        "typed-bind::found::Unreachable",
        "bang-bound::found::Missing",
        "bang-subject::ReadRow::Missing",
        "bang-subject::ReadRow::Unreachable",
        "absent-suffix-bind::found::Missing",
        "module-alias::found::effects.Missing",
        "a-second-effect::ack::Refused",
        "a-second-effect::ack::Gone",
        "nested-in-a-branch::found::Missing",
        "reads-a-row-in-a-test::found::Missing",
    ]
    .into_iter()
    .map(|detail| format!("app/branches.hy::{}::{}", RULE, detail.replace('-', "_")))
    .collect();
    assert_eq!(keys(&report), expected);
    for violation in violations(&report) {
        assert_eq!(violation["level"], "critical", "{}", violation);
        assert_eq!(violation["severity"], "error", "{}", violation);
    }
}

#[test]
fn the_message_names_the_effect_the_declaration_and_the_way_out() {
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &[], None);
    let hit = violations(&report).into_iter().find(|v| v["key"].as_str().unwrap_or("").ends_with("::match_an_absent_arm::found::Missing")).expect("当たり");
    let message = hit["message"].as_str().expect("message");
    assert!(
        message.contains("ReadRow は Missing を不在と宣言した") && message.contains("<- は found に Missing を束ねないので、この枝(match の腕)は通らない") && message.contains("maybe / on-raise / :absent"),
        "{}",
        message
    );
    let failure = violations(&report).into_iter().find(|v| v["key"].as_str().unwrap_or("").ends_with("::a_second_effect::ack::Refused")).expect("当たり");
    assert!(failure["message"].as_str().unwrap_or("").contains("PostRow は Refused を失敗と宣言した"), "{}", failure["message"]);
}

#[test]
fn the_single_file_run_reports_the_same_lines() {
    // エディタの保存前の中身(--stdin --path)にも当てる — 宣言は他の file(effects.hy)から、引く名を綴る file だけを読んで集める。
    let source = fixture("branches.hy");
    let dir = fixture_repo(CONFIG);
    let report = editor(dir.path(), &["--stdin", "--path", "app/branches.hy"], Some(&source));
    assert!(judged(&report), "1 file の実行が {} を判じていない: {}", RULE, report["judged_rules"]);
    assert_eq!(hit_lines(&report), marked_lines(&source));
    let whole = editor(dir.path(), &[], None);
    assert_eq!(keys(&report), keys(&whole), "1 file の実行の当たりの鍵が、全体の実行と違う");
}

#[test]
fn the_declaration_decides_not_the_name_of_the_type() {
    // 宣言(:absent / :failure)を消すと当たりは 0 になる — 型の名(Missing・Unreachable)から推し量っていない。
    let stripped = fixture("effects.hy").replace(":absent [Missing]", "").replace(":failure [Unreachable]", "").replace(":absent [Gone]", "").replace(":failure [Refused]", "");
    let owned = [("app/effects.hy", stripped), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))];
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let dir = repo(CONFIG, &borrowed);
    let report = editor(dir.path(), &[], None);
    assert!(judged(&report) && wired(&report));
    assert!(violations(&report).is_empty(), "宣言を消したのに当たった: {:#?}", violations(&report));
    // 逆に、宣言の無い effect(PlainRead)に宣言を足すと、その枝が当たる。
    let declared = fixture("effects.hy").replace(
        "   :answer (| Row Missing Unreachable)\n   :tags {:context \"sample\" :role \"intent\"}})\n",
        "   :answer (| Row Missing Unreachable)\n   :absent [Missing]\n   :tags {:context \"sample\" :role \"intent\"}})\n",
    );
    assert_ne!(declared, fixture("effects.hy"), "検体の書き換えが効いていない");
    let owned = [("app/effects.hy", declared), ("app/other.hy", fixture("other.hy")), ("app/branches.hy", fixture("branches.hy"))];
    let borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let dir = repo(CONFIG, &borrowed);
    let report = editor(dir.path(), &[], None);
    let plain: Vec<String> = keys(&report).into_iter().filter(|k| k.contains("::plain_effect::")).collect();
    assert_eq!(plain, vec![format!("app/branches.hy::{}::plain_effect::found::Missing", RULE)], "{:#?}", violations(&report));
}

#[test]
fn the_rule_judges_nothing_without_the_definitions_section() {
    // 母集団の宣言が無い repo には当てない(0 件を「守れている」と読ませない)。
    let dir = fixture_repo("[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n");
    let report = editor(dir.path(), &[], None);
    assert!(!wired(&report), "節が無いのに {} の判定がつながっていると名乗った", RULE);
    assert!(violations(&report).is_empty(), "{:#?}", violations(&report));
}

#[test]
fn places_outside_the_population_are_not_judged_and_test_places_are() {
    // 置き場の外(lib/)は当てない。検の置き場(test_paths)の Hy は当てる — 検の呼び手も切り替えで古くなる。
    let uses = fixture("branches.hy");
    let config = "[tool.doeff-linter]\nenable = [\"DOEFF175\"]\n\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\ntest_paths = [\"checks/test_*.hy\"]\n";
    let effects = fixture("effects.hy");
    let dir = repo(config, &[("app/effects.hy", &effects), ("lib/branches.hy", &uses), ("checks/test_branches.hy", &uses)]);
    let report = editor(dir.path(), &[], None);
    let places: BTreeSet<&str> = violations(&report).iter().map(|v| v["key"].as_str().expect("鍵").split("::").next().unwrap()).collect();
    assert_eq!(places, ["checks/test_branches.hy"].into_iter().collect(), "{:#?}", violations(&report));
}

#[test]
fn the_registry_does_not_lower_a_hit() {
    // 登録簿に載せても warning に下がらない — 当たりが 0 になるまでその版を着地させない(既知の一覧で門を空けない)。
    let key = format!("app/branches.hy::{}::match_an_absent_arm::found::Missing", RULE);
    let config = format!("{}\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n", CONFIG);
    let owned = files();
    let mut borrowed: Vec<(&str, &str)> = owned.iter().map(|(rel, text)| (*rel, text.as_str())).collect();
    let known = format!("{}\n", key);
    borrowed.push(("known.txt", known.as_str()));
    let dir = repo(&config, &borrowed);
    let report = editor(dir.path(), &[], None);
    let hit = violations(&report).into_iter().find(|v| v["key"] == key.as_str()).expect("登録簿に載せた当たりも出る");
    assert_eq!(hit["level"], "critical", "{}", hit);
    assert_eq!(hit["severity"], "error", "{}", hit);
}

#[test]
fn the_rule_can_be_disabled_by_the_repo() {
    // disable に書けば判じない(この検は、下の「この repo の本番の code」の検が disable を赤にする訳を示す対)。
    let config = CONFIG.replace("enable = [\"DOEFF175\"]\n", "disable = [\"DOEFF175\"]\n");
    let dir = fixture_repo(&config);
    let report = editor(dir.path(), &[], None);
    assert!(!judged(&report), "disable に書いた {} を判じたと名乗った: {}", RULE, report["judged_rules"]);
    assert!(violations(&report).is_empty());
}

#[test]
fn the_production_code_of_this_repo_has_no_stale_branches() {
    // doeff の根の設定(pyproject.toml の [tool.doeff-linter.definitions])で、本番の code に当たりが無い。
    // disable に足す・definitions の節を消すと「判じていない」で赤、宣言を切り替えた effect の古い分岐を書くと当たりで赤。
    // ADR(docs/adr/*.hy)と doeff-core-effects の probe の検は、この母集団(`paths` の 3 つの本体の dir)の外。
    let root = doeff_root();
    let report = editor(&root, &["packages"], None);
    assert!(judged(&report) && wired(&report), "doeff の根の設定が {} を判じていない — pyproject.toml の [tool.doeff-linter.definitions] の節と disable を確かめる", RULE);
    let found: Vec<String> = violations(&report)
        .iter()
        .map(|v| format!("{}:{} {}", v["path"].as_str().unwrap_or(""), v["range"]["start"]["line"].as_u64().unwrap_or(0) + 1, v["message"].as_str().unwrap_or("")))
        .collect();
    assert!(found.is_empty(), "宣言を切り替えた effect の古い分岐が残っている — 成功の道だけにして、不在・失敗は :absent / on-raise / maybe へ:\n{}", found.join("\n"));
}
