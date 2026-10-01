//! `doeff-linter --commit-hook` の検(agora-redesign #1989)— 一時の git の repo を作り、きれいな基点を commit してから変更を stage し、
//! hook の終了コードと stderr の行を見る。repo 全体の規則は DOEFF163(service の不変条件の宣言)— 当たりは defservice の位置
//! (architecture.hy)に付くので、不変条件の関数の file だけを変えた commit でも、変更の外の file に新しい当たりが出る(merge-queue の形)。

use serde_json::Value;
use std::path::Path;
use std::process::{Command, Stdio};

const JUDGED: &str = "{:pre [(: writes tuple)] :post [(: % tuple)] :tags {:context \"lease\" :role \"judgment\"}}";

/// 検の repo の architecture.hy(`queue` は :entry-modules と :invariants を宣言した service)。
const ARCHITECTURE: &str = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])
           (layer foundation :roles [foundation] :imports [foundation])
           (layer entry :roles [entry] :imports [core foundation entry])]
  :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file]
                     :wraps ["doeff_core_effects.os_file:os-file-handler"])])
(defservice queue "列" {:layers [core] :entry-modules ["app.queue.main"] :invariants ["app.queue.lease_invariants:fenced-writes"]})
"#;

/// 設定 — DOEFF163 は repo 全体の規則、DOEFF016(Python の相対 import)は stage した path に当てる規則。
const PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF163", "DOEFF016"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

fn git(root: &Path, args: &[&str]) {
    let output = Command::new("git")
        .args(["-c", "user.name=commit-hook-test", "-c", "user.email=commit-hook-test@example.invalid", "-c", "commit.gpgsign=false"])
        .args(args)
        .current_dir(root)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .output()
        .unwrap();
    assert!(output.status.success(), "git {:?}: {}", args, String::from_utf8_lossy(&output.stderr));
}

fn write(root: &Path, rel: &str, text: &str) {
    let path = root.join(rel);
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, text).unwrap();
}

/// きれいな基点を 1 つ commit した repo。
fn baseline_repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let root = dir.path();
    write(root, "pyproject.toml", PYPROJECT);
    write(root, "architecture.hy", ARCHITECTURE);
    write(root, "app/foundation/host.hy", "(val MODULE-TAGS {:context \"shared\" :role \"foundation\"})\n(defk with-host [body] body)\n");
    write(root, "app/queue/main.hy", "(defk cycle [] 1)\n");
    write(root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes [writes]\n  {}\n  \"柵の条。\"\n  #())\n", JUDGED));
    write(root, "app/queue/tool.py", "import os\n");
    git(root, &["init", "-q", "-b", "main"]);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "基点"]);
    dir
}

/// hook を root で走らせ、(終了コード・stderr)を返す。
fn hook(root: &Path, extra: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .arg("--commit-hook")
        .args(extra)
        .current_dir(root)
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// 登録簿の当たらない行の検の設定(#1992 の形)— DOEFF110(defn の残り)は stage した path に当てる規則で、repo 全体の比べの列
/// (規則が repo 全体が要ると名乗る物 — agora-redesign #2090)は DOEFF163・166 だけ。
const REGISTRY_PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF163", "DOEFF110", "DOEFF166"]
[tool.doeff-linter.definitions]
[tool.doeff-linter.registry]
dirs = ["reg"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

/// 失敗ケース(iii・#1992 の形): 登録簿に載った defn を直したのに行を消し忘れた commit は、repo 全体の比べの列が DOEFF163・166 だけでも
/// 止まる — 166 が行の名指す規則(DOEFF110)を同じ実行で当てる(agora-redesign #1999・#2033)。行も消した commit は通る。
#[test]
fn a_registry_row_left_behind_blocks_with_only_the_whole_repo_rules() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", REGISTRY_PYPROJECT);
    write(root, "app/core/x.hy", "(defn helper [] 1)\n");
    write(root, "reg/k1.txt", "app/core/x.hy::DOEFF110::helper\n既存の defn\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "登録簿つきの基点"]);
    write(root, "app/core/x.hy", "(defk helper [] 1)\n");
    git(root, &["add", "app/core/x.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: reg/k1.txt::DOEFF166::app/core/x.hy::DOEFF110::helper"), "{}", stderr);
    git(root, &["rm", "-q", "reg/k1.txt"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// 基点そのものが緑で、設定の `[tool.doeff-linter.commit_hook]` は知らない鍵(DOEFF100)にならない。
#[test]
fn commit_hook_section_is_a_known_key_and_the_baseline_is_clean() {
    let dir = baseline_repo();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|e| panic!("{}: {}", e, String::from_utf8_lossy(&output.stderr)));
    let rules: Vec<&str> = report["violations"].as_array().unwrap().iter().map(|v| v["rule"].as_str().unwrap()).collect();
    assert!(rules.is_empty(), "{}", report["violations"]);
}

/// 失敗ケース(i): 不変条件の関数の file だけを変え(名を変えて architecture.hy の :invariants が実在しない関数を指す)、stage した
/// file の外(architecture.hy)に DOEFF163 の新しい当たりが出る — hook は 1 で止め、鍵を名指す。
#[test]
fn a_new_hit_outside_the_staged_file_blocks() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes-renamed [writes]\n  {}\n  \"柵の条。\"\n  #())\n", JUDGED));
    git(root, &["add", "app/queue/lease_invariants.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(
        stderr.contains("doeff-linter commit-hook: repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF163::queue::app.queue.lease_invariants:fenced-writes"),
        "{}",
        stderr
    );
}

/// merge-queue の形(#1989): architecture.hy から :entry-modules を持つ service の :invariants を消した commit は止まる。
#[test]
fn removing_invariants_from_the_architecture_blocks() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "architecture.hy", &ARCHITECTURE.replace(" :invariants [\"app.queue.lease_invariants:fenced-writes\"]", ""));
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF163::queue"), "{}", stderr);
}

/// 失敗ケース(ii): きれいな変更は 0 で通す。stage した path に当てる規則の新しい error(DOEFF016)は 1 で止める。
#[test]
fn a_clean_change_passes_and_a_staged_error_blocks() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("HEAD に無い"), "{}", stderr);

    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("doeff-linter commit-hook: stage した file の破れ: app/queue/tool.py:1: DOEFF016"), "{}", stderr);
}

/// 失敗ケース(agora-redesign #2090・L3137 の形): 数を決めた綴り(DOEFF161)の当たりは、:files のうち最初に当たった file に付く。
/// stage した file(more.hy)に 2 つ目の綴りを足すと、当たりは stage していない main.hy に付く — stage した path だけに当てると出ない。
/// 規則が repo 全体が要ると名乗るので、設定に手の一覧が無くても repo 全体の比べが拾って 1 で止める。綴りを 1 つに戻せば通る。
#[test]
fn a_rule_that_needs_the_whole_repo_blocks_without_a_hand_list() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF161\"]"));
    write(
        root,
        "architecture.hy",
        &ARCHITECTURE.replace(
            ":foundation foundation",
            ":foundation foundation\n  :counted-spellings [(counted-spelling \"cycles\" :pattern r\"defk cycle\" \
             :files [\"app/queue/main.hy\" \"app/queue/more.hy\"] :count 1 :why \"周りの 1 点\")]",
        ),
    );
    write(root, "app/queue/more.hy", "(defk other [] 1)\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "数を決めた綴りつきの基点"]);
    write(root, "app/queue/more.hy", "(defk other [] 1)\n(defk cycle-again [] 2)\n");
    git(root, &["add", "app/queue/more.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: app/queue/main.hy::DOEFF161::cycles"), "{}", stderr);
    write(root, "app/queue/more.hy", "(defk other [] 1)\n");
    git(root, &["add", "app/queue/more.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// 退役した設定の鍵 whole_repo_rules が残っていれば、読まずに 1 行で名乗る(規則の分けは規則の名乗りのまま — 一覧に 163 を書かず
/// 110 を書いても、163 の当たりは repo 全体の比べが拾う)。
#[test]
fn a_retired_hand_list_is_named_and_not_read() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("timeout_s = 120", "whole_repo_rules = [\"DOEFF110\"]\ntimeout_s = 120"));
    git(root, &["add", "pyproject.toml"]);
    git(root, &["commit", "-q", "-m", "退役した鍵"]);
    write(root, "app/queue/lease_invariants.hy", &format!("(defk fenced-writes-renamed [writes]\n  {}\n  \"柵の条。\"\n  #())\n", JUDGED));
    git(root, &["add", "app/queue/lease_invariants.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("whole_repo_rules は退役した鍵で読まない"), "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF163::queue"), "{}", stderr);
}

/// `--list-rules` は全部の規則の ID と repo 全体が要るかの名乗りを出す(門と hook が選ぶ 1 か所)。Python の文ごとの規則は file 1 つ。
#[test]
fn list_rules_names_which_rules_need_the_whole_repo() {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--list-rules").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let listed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let whole = |id: &str| {
        listed.as_array().unwrap().iter().find(|r| r["id"] == id).unwrap_or_else(|| panic!("{} が無い", id))["whole_repo"].as_bool().unwrap()
    };
    for id in ["DOEFF149", "DOEFF161", "DOEFF163", "DOEFF166"] {
        assert!(whole(id), "{} は repo 全体が要る", id);
    }
    for id in ["DOEFF016", "DOEFF110", "DOEFF169"] {
        assert!(!whole(id), "{} は file 1 つで判じる", id);
    }
}

/// 失敗ケース(agora-redesign #2127): file 1 つで判じる規則(DOEFF102 — 層に禁じた module の import)でも、宣言(architecture.hy)だけを
/// 変えた commit で、stage していない file に新しい当たりが付く。宣言の file を変えた commit は全部の規則を repo 全体の比べに当てて
/// 止める。宣言を戻せば通る。
#[test]
fn a_declaration_change_blocks_a_new_hit_in_an_unstaged_file() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF102\"]"));
    write(root, "app/queue/core/rule.hy", &format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk parse [text]\n  {}\n  #())\n", JUDGED));
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "json を読む core の基点"]);
    let forbidding = ARCHITECTURE.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :forbid-modules [json])");
    write(root, "architecture.hy", &forbidding);
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("app/queue/core/rule.hy") && stderr.contains("DOEFF102"), "{}", stderr);
    write(root, "architecture.hy", ARCHITECTURE);
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// 規則を鳴らし始める commit(agora-redesign #2143): 層 core に json を禁じる宣言を足すと、触っていない core の file の既存の import が
/// 新しい当たりになり、本文に指定の無い commit は止まる(#2127 のまま)。本文に理由つきの `Lint-Baseline: declarations` の行が在れば、
/// HEAD の木を今の宣言で測るので既存の当たりは基点に在る物になって通る。同じ commit で新しい違反を足せば、指定が在っても止まる。
#[test]
fn a_reasoned_baseline_trailer_lets_rules_start_ringing_but_still_blocks_new_hits() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF102\"]"));
    write(root, "app/queue/core/rule.hy", &format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk parse [text]\n  {}\n  #())\n", JUDGED));
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "json を読む core の基点"]);
    let forbidding = ARCHITECTURE.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :forbid-modules [json])");
    write(root, "architecture.hy", &forbidding);
    git(root, &["add", "architecture.hy"]);
    let message = root.join("MSG");
    std::fs::write(&message, "core に json を禁じる\n\nLint-Baseline: declarations — 既存の import は別の変更で直す\n").unwrap();
    let message_arg = message.to_string_lossy().into_owned();
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.contains("Lint-Baseline: declarations — HEAD の木を今の宣言"), "{}", stderr);
    // 理由の無い行は効かない。
    std::fs::write(&message, "core に json を禁じる\n\nLint-Baseline: declarations\n").unwrap();
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 1, "{}", stderr);
    // 指定が在っても、同じ commit で足した新しい違反は止まる。
    std::fs::write(&message, "core に json を禁じる\n\nLint-Baseline: declarations — 既存の import は別の変更で直す\n").unwrap();
    write(root, "app/queue/core/more.hy", &format!("(val MODULE-TAGS {{:context \"queue\" :role \"judgment\"}})\n(import json)\n(defk more [text]\n  {}\n  #())\n", JUDGED));
    git(root, &["add", "app/queue/core/more.hy"]);
    let (code, stderr) = hook(root, &["--commit-message", &message_arg]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("app/queue/core/more.hy") && stderr.contains("DOEFF102"), "{}", stderr);
    assert!(!stderr.contains("app/queue/core/rule.hy::DOEFF102"), "{}", stderr);
}

/// 門の口 `--split-rules --commit-message <本文>` は、理由つきの Lint-Baseline の行が在る時だけ基点の木へ写す宣言の file を名乗る。
#[test]
fn split_rules_names_the_baseline_overlay_only_for_a_reasoned_trailer() {
    let dir = baseline_repo();
    let message = dir.path().join("MSG");
    let split = |text: &str| -> Value {
        std::fs::write(&message, text).unwrap();
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
            .args(["--split-rules", "--commit-message"])
            .arg(&message)
            .arg("architecture.hy")
            .current_dir(dir.path())
            .output()
            .unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };
    assert_eq!(split("x\n\nLint-Baseline: declarations — 鳴らし始める\n")["baseline_overlay"], serde_json::json!(["pyproject.toml", "architecture.hy"]));
    assert_eq!(split("x\n")["baseline_overlay"], serde_json::json!([]));
}

/// 門の口 `--split-rules <変えた path>` は hook と同じ判定の分けを出す — architecture.hy を名指せば file 1 つで判じる規則(DOEFF016)も
/// whole、source だけなら quick のまま(agora-redesign #2127)。
#[test]
fn split_rules_tells_the_gate_the_same_split_as_the_hook() {
    let dir = baseline_repo();
    let split = |changed: &[&str]| -> Value {
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--split-rules").args(changed).current_dir(dir.path()).output().unwrap();
        assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
        serde_json::from_slice(&output.stdout).unwrap()
    };
    let declared = split(&["architecture.hy"]);
    assert_eq!(declared["declaration_changed"], true, "{}", declared);
    assert_eq!(declared["quick"], serde_json::json!([]), "{}", declared);
    assert_eq!(declared["whole"], serde_json::json!(["DOEFF163", "DOEFF016"]), "{}", declared);
    let plain = split(&["app/queue/main.hy"]);
    assert_eq!(plain["declaration_changed"], false, "{}", plain);
    assert_eq!(plain["quick"], serde_json::json!(["DOEFF016"]), "{}", plain);
    assert_eq!(plain["whole"], serde_json::json!(["DOEFF163"]), "{}", plain);
}

/// 何も stage していなければ linter を撃たずに 0。
#[test]
fn nothing_staged_passes() {
    let dir = baseline_repo();
    let (code, stderr) = hook(dir.path(), &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.is_empty(), "{}", stderr);
}

/// 失敗ケース(iii): 上限 0 秒は測れなかったとして 1 行出し、止めない(新しい当たりの在る変更でも 0)。
#[test]
fn timeout_passes_with_an_unmeasured_line() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "architecture.hy", &ARCHITECTURE.replace(" :invariants [\"app.queue.lease_invariants:fenced-writes\"]", ""));
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &["--commit-hook-timeout-s", "0"]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(stderr.contains("測れなかった"), "{}", stderr);
    assert_eq!(stderr.lines().count(), 1, "{}", stderr);
}
