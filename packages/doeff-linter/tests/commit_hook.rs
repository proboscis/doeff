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

/// hook を root で走らせ、(終了コード・stderr)を返す。置き場(事実の cache と HEAD の木の結果)は repo の .git の下に分ける — 検どうし・
/// 機体の置き場と混ぜない(agora-redesign #2723)。
fn hook(root: &Path, extra: &[&str]) -> (i32, String) {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .arg("--commit-hook")
        .args(extra)
        .current_dir(root)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git").join("doeff-linter-cache"))
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

/// 失敗ケース(agora-redesign #2377): 目録(data/world_handlers.json)に無い :wraps を宣言した architecture.hy は設定の誤り — linter は
/// 0 件に見せず終了コード 2 で名指し、hook も 2 で止めて、誤った :wraps を名指す(1 行目の「設定の誤り:」の見出しだけにしない)。
#[test]
fn a_wraps_outside_the_catalog_is_named_and_stops() {
    let dir = baseline_repo();
    let root = dir.path();
    let stale = "doeff_cluster.host_contract:host-reader";
    write(root, "architecture.hy", &ARCHITECTURE.replace("doeff_core_effects.os_file:os-file-handler", stale));
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(root)
        .output()
        .unwrap();
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(2), "{}", stderr);
    assert!(output.stdout.is_empty(), "設定の誤りで 0 件の報告を出さない: {}", String::from_utf8_lossy(&output.stdout));
    assert!(stderr.contains(&format!(":wraps の {} は doeff の実 I/O の handler の目録", stale)), "{}", stderr);
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains(stale), "hook が誤った :wraps を名指さない: {}", stderr);
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

/// agora-redesign #3834: `--list-rules` は Jev に問う規則かの名乗り(semantic)も出す — 門は ID の頭でなくこの欄で Jev の規則を分ける。
/// 頭が DOEFF2 の決定的な規則(DOEFF206〜209)は Jev の規則でない。
#[test]
fn list_rules_names_which_rules_ask_jev() {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter")).arg("--list-rules").output().unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let listed: Value = serde_json::from_slice(&output.stdout).unwrap();
    let semantic = |id: &str| {
        listed.as_array().unwrap().iter().find(|r| r["id"] == id).unwrap_or_else(|| panic!("{} が無い", id))["semantic"]
            .as_bool()
            .unwrap_or_else(|| panic!("{} に semantic の欄が無い", id))
    };
    for id in ["DOEFF201", "DOEFF202", "DOEFF203", "DOEFF204", "DOEFF205"] {
        assert!(semantic(id), "{} は Jev に問う", id);
    }
    for id in ["DOEFF016", "DOEFF110", "DOEFF206", "DOEFF207", "DOEFF208", "DOEFF209"] {
        assert!(!semantic(id), "{} は Jev に問わない", id);
    }
}

/// 失敗ケース(agora-redesign #3834): 門の口 `--split-rules` は DOEFF206〜209 を quick か whole に入れる(直す前はどちらにも 0 本 — ID の頭
/// DOEFF2 で Jev の規則として落ちていた)。Jev の規則(DOEFF201・205)はどちらにも入らない。
#[test]
fn split_rules_keeps_the_doeff2_rules_that_do_not_ask_jev() {
    let dir = baseline_repo();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--split-rules", "--enable", "DOEFF201,DOEFF205,DOEFF206,DOEFF207,DOEFF208,DOEFF209", "app/queue/main.hy"])
        .current_dir(dir.path())
        .output()
        .unwrap();
    assert!(output.status.success(), "{}", String::from_utf8_lossy(&output.stderr));
    let split: Value = serde_json::from_slice(&output.stdout).unwrap();
    let listed = |side: &str| -> Vec<String> { split[side].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect() };
    let (quick, whole) = (listed("quick"), listed("whole"));
    assert!(quick.contains(&"DOEFF208".to_string()), "{}", split);
    for id in ["DOEFF206", "DOEFF207", "DOEFF209"] {
        assert!(whole.contains(&id.to_string()), "{} {}", id, split);
    }
    for id in ["DOEFF201", "DOEFF205"] {
        assert!(!quick.contains(&id.to_string()) && !whole.contains(&id.to_string()), "{} {}", id, split);
    }
}

/// 規則 rule だけを有効にし、:business-fakes を宣言した repo(まだ commit しない)。
fn repo_enabling_only(rule: &str) -> tempfile::TempDir {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &format!("[tool.doeff-linter]\nenable = [\"{}\"]\n\n[tool.doeff-linter.commit_hook]\ntimeout_s = 120\n", rule));
    write(
        root,
        "architecture.hy",
        &ARCHITECTURE.replace(":foundation foundation", ":foundation foundation\n  :business-fakes {:simulation [\"app/sim/**\"] :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.queue\"]}"),
    );
    dir
}

/// 規則 rule だけを有効にし、:business-fakes を宣言し、当たりを持つ file `app/queue/wait.hy`(中身 text)を基点に commit した repo。
/// その file に註を 1 行足して stage した状態で返す。
fn repo_with_an_old_hit(rule: &str, text: &str) -> tempfile::TempDir {
    let dir = repo_enabling_only(rule);
    let root = dir.path();
    write(root, "app/queue/wait.hy", text);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "当たりを持つ基点"]);
    write(root, "app/queue/wait.hy", &format!(";; 註を足す\n{}", text));
    git(root, &["add", "app/queue/wait.hy"]);
    dir
}

/// 規則 rule だけを有効にし、:business-fakes を宣言した基点を commit した repo に、当たりを持つ file `app/queue/wait.hy`(中身 text)を
/// 新しく足して stage した状態で返す(HEAD に同じ鍵が無い当たり)。
fn repo_with_a_new_hit(rule: &str, text: &str) -> tempfile::TempDir {
    let dir = repo_enabling_only(rule);
    let root = dir.path();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "当たりの無い基点"]);
    write(root, "app/queue/wait.hy", text);
    git(root, &["add", "app/queue/wait.hy"]);
    dir
}

const RECORD_WAIT: &str = "(import doeff_records.effects [WatchChanges])\n(defk wait-for [cursor left]\n  (WatchChanges #(\"message\") cursor :timeout left))\n";
const POLLING_LOOP: &str = "(defk poll []\n  (while True\n    (<- (Delay 5.0))\n    (<- (ReadQueue))))\n";
/// HEAD にも同じ鍵が在るのに止める当たりの行の頭と、直し方の案内の行(agora-redesign #3834・cisco-c8 の頼み 2026-10-07)。
const ALREADY_ON_HEAD: &str = "変えた file の既存の当たり(main にも在る・この規則は既知の一覧で下げない): ";
const FIX_IT_IN_THE_SAME_CHANGE: &str = "この file を変えるなら、その当たりも同じ変更で直す(hook を飛ばさない)";

/// 失敗ケース(agora-redesign #3834・cisco-c8 の頼み 2026-10-07): HEAD にも同じ鍵が在る当たりで止めた時に「HEAD に無い」と書くと、
/// 作業役が hook の不具合と読んで hook を飛ばしかける。stage した path に当てる規則(DOEFF208)と repo 全体の比べ(DOEFF209)の両方で、
/// 既存の当たりは「変えた file の既存の当たり」と書き、直し方の案内を 1 行足し、「HEAD に無い」と書かない。
#[test]
fn an_old_hit_that_blocks_is_named_as_already_on_head() {
    for (rule, text, key) in [
        ("DOEFF208", RECORD_WAIT, "app/queue/wait.hy::DOEFF208::wait-for::WatchChanges"),
        ("DOEFF209", POLLING_LOOP, "app/queue/wait.hy::DOEFF209::poll::Delay::periodic"),
    ] {
        let dir = repo_with_an_old_hit(rule, text);
        let (code, stderr) = hook(dir.path(), &[]);
        assert_eq!(code, 1, "{}", stderr);
        assert!(stderr.contains(&format!("{}{}", ALREADY_ON_HEAD, key)), "{}", stderr);
        assert!(stderr.contains(FIX_IT_IN_THE_SAME_CHANGE), "{}", stderr);
        assert!(!stderr.contains("HEAD に無い"), "{}", stderr);
    }
}

/// 失敗ケース(同上の対): HEAD に同じ鍵が無い当たり(新しく足した file の当たり)は今までどおり「HEAD に無い」と書き、既存の当たりの
/// 行と案内は出さない。
#[test]
fn a_new_hit_that_blocks_is_still_named_as_not_on_head() {
    for (rule, text, line) in [
        ("DOEFF208", RECORD_WAIT, "HEAD に無い critical: app/queue/wait.hy::DOEFF208::wait-for::WatchChanges"),
        ("DOEFF209", POLLING_LOOP, "repo 全体の規則の HEAD に無い当たり: app/queue/wait.hy::DOEFF209::poll::Delay::periodic"),
    ] {
        let dir = repo_with_a_new_hit(rule, text);
        let (code, stderr) = hook(dir.path(), &[]);
        assert_eq!(code, 1, "{}", stderr);
        assert!(stderr.contains(line), "{}", stderr);
        assert!(!stderr.contains(ALREADY_ON_HEAD), "{}", stderr);
        assert!(!stderr.contains(FIX_IT_IN_THE_SAME_CHANGE), "{}", stderr);
    }
}

/// 当たりの在る file の stage を外し、当たりの無い file だけを stage する。
fn stage_only_an_untouched_file(root: &Path) {
    git(root, &["reset", "-q", "app/queue/wait.hy"]);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
}

/// 失敗ケース(agora-redesign #3834・cisco-c8 の可): stage した file に HEAD の版から在る DOEFF208 の当たりも、commit を止める(既知の一覧を
/// 持たない規則は基点の差で下げない — stage した path に当てる列の new_critical)。DOEFF208 の当たりを持つ file に触らない commit は
/// 止まらない(file 単位)。
#[test]
fn a_staged_file_with_an_old_record_wait_blocks_and_an_untouched_one_does_not() {
    let dir = repo_with_an_old_hit("DOEFF208", RECORD_WAIT);
    let root = dir.path();
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains(&format!("{}app/queue/wait.hy::DOEFF208::wait-for::WatchChanges", ALREADY_ON_HEAD)), "{}", stderr);
    stage_only_an_untouched_file(root);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// 失敗ケース(agora-redesign #3834・案 1): repo 全体の比べに回る DOEFF209 も、stage した file に HEAD の版から在る当たりで commit を止める
/// (HEAD の木との識別子の差に、stage した path の基点の差で下げない当たりを足す)。当たりの在る file に触らない commit は止まらない。
/// 上限なし(--commit-hook-timeout-s 0)でも同じ当たりで止まる。
#[test]
fn a_staged_file_with_an_old_polling_loop_blocks_and_an_untouched_one_does_not() {
    let dir = repo_with_an_old_hit("DOEFF209", POLLING_LOOP);
    let root = dir.path();
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains(&format!("{}app/queue/wait.hy::DOEFF209::poll::Delay::periodic", ALREADY_ON_HEAD)), "{}", stderr);
    let (code, stderr) = hook(root, &["--commit-hook-timeout-s", "0"]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains(&format!("{}app/queue/wait.hy::DOEFF209::poll::Delay::periodic", ALREADY_ON_HEAD)), "{}", stderr);
    assert!(!stderr.contains("測れなかった"), "{}", stderr);
    stage_only_an_untouched_file(root);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
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

/// agora-redesign #3834: 上限 0 秒は上限なし — 打ち切らずに終わりまで測り、新しい当たりで止める(以前は 0 秒を「実行せずに越えた」
/// 扱いにして、測れなかったとして通していた)。上限で止まった commit を確かめ直す命令は、この形を使う。
#[test]
fn a_zero_timeout_measures_to_the_end() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "architecture.hy", &ARCHITECTURE.replace(" :invariants [\"app.queue.lease_invariants:fenced-writes\"]", ""));
    git(root, &["add", "architecture.hy"]);
    let (code, stderr) = hook(root, &["--commit-hook-timeout-s", "0"]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF163::queue"), "{}", stderr);
    assert!(!stderr.contains("測れなかった"), "{}", stderr);
}

/// 打ち切って止めた時の行の後ろ 2 行の頭(agora-redesign #3834・cisco-c8 の可 2026-10-07)。
const RERUN_COMMAND: &str = "上限なしで同じ比べをやり直す命令: ";
const RERUN_GUIDE: &str = "この命令の終了コードが 0(止める当たりが 0 件)である事を確かめてから commit し直す。--no-verify は使わない";

/// 打ち切って止めた判じの行から、上限なしの命令を取り出す。
fn rerun_command(lines: &[String]) -> String {
    lines.iter().find_map(|line| line.strip_prefix(RERUN_COMMAND)).map(str::to_string).unwrap_or_else(|| panic!("上限なしの命令の行が無い: {:?}", lines))
}

/// 上限なしの命令を shell で実行し、(終了コード・stderr)を返す(cache は hook() と同じく repo の .git の下)。
fn run_rerun(root: &Path, command: &str) -> (i32, String) {
    let output = Command::new("sh")
        .arg("-c")
        .arg(command)
        .env("DOEFF_LINTER_CACHE_DIR", root.join(".git").join("doeff-linter-cache"))
        .env_remove("GIT_DIR")
        .env_remove("GIT_INDEX_FILE")
        .env_remove("GIT_WORK_TREE")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// 遅い代役の linter — HEAD の木(hook が cache の `commit-hook-tree/<repo を表す名>/tree` に置く・agora-redesign #3858)で repo 全体(path が `.`)を実行された
/// 時だけ secs 秒眠ってから本物を実行する(冷えた事実の cache で HEAD の木を測る 1 回が上限を越える形の代役)。
fn slow_head_linter(dir: &Path, secs: u64) -> std::path::PathBuf {
    use std::os::unix::fs::PermissionsExt;
    let path = dir.join("slow-doeff-linter");
    let script = format!(
        "#!/bin/sh\nfor last in \"$@\"; do :; done\ncase \"$(pwd -P)\" in\n  */commit-hook-tree/*) [ \"$last\" = . ] && sleep {} ;;\nesac\nexec '{}' \"$@\"\n",
        secs,
        env!("CARGO_BIN_EXE_doeff-linter")
    );
    std::fs::write(&path, script).unwrap();
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    path
}

/// hook の前提を、子の linter と置き場の根と上限を差し替えて組む(本番の入口 main.rs と同じ組み方 — 根と設定の path は正規化)。
fn options_with(root: &Path, linter: &Path, cache: &Path, timeout_s: u64) -> doeff_linter::commit_hook::CommitHookOptions {
    let root = root.canonicalize().unwrap();
    let loaded = doeff_linter::config::load_config_checked(None, &root).unwrap().unwrap();
    let config_path = loaded.path.canonicalize().unwrap();
    let mut options =
        doeff_linter::commit_hook::CommitHookOptions::new(root, Some((&loaded.config, config_path)), &[], &[], Some(timeout_s), linter.to_path_buf());
    options.cache = Some(cache.to_path_buf());
    options
}

/// 宣言の file(architecture.hy)— service queue が条を宣言し、反例の表の節が壊した handler の反例を名乗る(DOEFF167 の形)。
fn clause_architecture(clauses: &[&str]) -> String {
    let fakes = ":foundation foundation\n  :business-fakes {:simulation [\"app/sim/**\"] :tests [\"tests/**\"] :production [\"app/**\"] \
                 :business-modules [\"app.queue\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let listed = clauses.iter().map(|c| format!("\"{}\"", c)).collect::<Vec<_>>().join(" ");
    let text = ARCHITECTURE.replace(":foundation foundation", fakes).replace(
        ":invariants [\"app.queue.lease_invariants:fenced-writes\"]}",
        &format!(":invariants [\"app.queue.lease_invariants:fenced-writes\"] :clauses [{}]}}", listed),
    );
    assert!(text.contains(":clauses ["), "queue の宣言を差し替えられない:\n{}", text);
    text
}

/// 条 Q1 に反例(`breaks: queue::Q1`)の在る基点を commit した repo(DOEFF167 は 0)。
fn clause_repo() -> tempfile::TempDir {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", &PYPROJECT.replace("enable = [\"DOEFF163\", \"DOEFF016\"]", "enable = [\"DOEFF163\", \"DOEFF016\", \"DOEFF167\"]"));
    write(root, "app/queue/effects.hy", "(import doeff [EffectBase])\n(defclass Put [EffectBase])\n");
    write(root, "app/queue/main.hy", "(import app.queue.effects [Put])\n(defk cycle [] (Put))\n");
    write(
        root,
        "tests/test_fence.hy",
        "(import app.queue.effects [Put])\n(import app.queue.main [cycle])\n\
         (defhandler unfenced (Put [] (resume 0)))\n(deftest test-unfenced-breaks-the-fence (with-handlers [unfenced] (cycle)))\n",
    );
    write(root, "tables/COUNTEREXAMPLES/queue.txt", "tests/test_fence.hy::unfenced::app.queue.effects.Put\n柵を迂回する書き手\nbreaks: queue::Q1\n");
    write(root, "architecture.hy", &clause_architecture(&["Q1"]));
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "条と反例つきの基点"]);
    dir
}

/// 失敗ケース(agora-redesign #2723・条 S10 の形): 宣言(architecture.hy)に条 Q2 を足した commit は、反例の無い条の DOEFF167 の新しい当たりを
/// 生む。(i) HEAD の木の 1 回が上限を越えると(遅い代役 — 冷えた cache)、測れなかった比べ・木・規則・秒を書いて止める(#3834)。
/// (ii) 上限の内で 1 度測れた HEAD の木の結果は置き場に残り(使った印つき)、同じ HEAD の次の hook は先端の木だけを上限の内で測って、
/// DOEFF167 の当たりで止める(上限の秒は上げない)。
#[test]
fn a_declaration_change_names_an_unmeasured_head_tree_and_blocks_doeff167_from_the_stored_head() {
    let dir = clause_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 5);
    let cache = side.path().join("cache");
    write(root, "architecture.hy", &clause_architecture(&["Q1", "Q2"]));
    git(root, &["add", "architecture.hy"]);
    let new_hit = "repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF167::queue::Q2";

    let cold = doeff_linter::commit_hook::assess(&options_with(root, &linter, &cache, 4));
    assert_eq!(cold.code, 1, "{:?}", cold);
    assert_eq!(cold.lines.len(), 3, "{:?}", cold);
    for part in ["HEAD の木の repo 全体の比べを測れなかった", "HEAD の木で当てた規則 3 個", "DOEFF167", "上限 4 秒で打ち切った", "commit を止める"] {
        assert!(cold.lines[0].contains(part), "{:?} が無い: {:?}", part, cold);
    }
    assert!(cold.lines[1].starts_with(RERUN_COMMAND) && cold.lines[2] == RERUN_GUIDE, "{:?}", cold);

    let measured = doeff_linter::commit_hook::assess(&options_with(root, &linter, &cache, 60));
    assert_eq!(measured.code, 1, "{:?}", measured);
    assert!(measured.lines.iter().any(|line| line == new_hit), "{:?}", measured);
    let stored: Vec<std::path::PathBuf> = std::fs::read_dir(&cache)
        .unwrap()
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.file_name().is_some_and(|name| name.to_string_lossy().starts_with(doeff_linter::head_report_cache::DIR_PREFIX)))
        .collect();
    assert_eq!(stored.len(), 1, "{:?}", stored);
    assert!(stored[0].join("used").is_file() && stored[0].join("report.json").is_file(), "{:?}", stored);

    let warm = doeff_linter::commit_hook::assess(&options_with(root, &linter, &cache, 4));
    assert_eq!(warm.code, 1, "{:?}", warm);
    assert_eq!(warm.lines, vec![new_hit.to_string()], "{:?}", warm);
}

/// 失敗ケース(agora-redesign #2723): stage した file の当たり(DOEFF016)が在る commit で、repo 全体の比べが上限で打ち切られても、stage した
/// file の当たりは捨てずに止め、打ち切った比べを名指す(以前は repo 全体の比べの打ち切りで判じ全体を「測れなかった」として通した)。
#[test]
fn a_cut_whole_repo_comparison_keeps_the_staged_hits_blocking() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 5);
    write(root, "app/queue/tool.py", "from . import main\n");
    git(root, &["add", "app/queue/tool.py"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &side.path().join("cache"), 4));
    assert_eq!(got.code, 1, "{:?}", got);
    assert!(got.lines.iter().any(|line| line.starts_with("stage した file の破れ: app/queue/tool.py:1: DOEFF016")), "{:?}", got);
    assert!(got.lines.iter().any(|line| line.contains("HEAD の木の repo 全体の比べを測れなかった") && line.contains("(DOEFF163)")), "{:?}", got);
    assert!(got.lines.iter().any(|line| line == RERUN_GUIDE), "{:?}", got);
}

/// 失敗ケース(agora-redesign #3834・元の issue #2723・cisco-c8 の可 2026-10-07): repo 全体の比べが上限で打ち切られた commit は、stage した
/// file に当たりが無くても止める(終了コード 1 — 以前は 0 で通し、作業役は通った commit を確かめ直さなかった)。止める時の行は、測れなかった
/// 比べの 1 行・上限なしで同じ比べをやり直す命令・「0 件を確かめてから commit し直す・--no-verify は使わない」の 3 行。命令はそのまま
/// shell で実行でき、打ち切らずに終わりまで測って、新しい当たり(DOEFF163)で止める。
#[test]
fn a_cut_comparison_blocks_and_names_the_command_to_rerun_without_a_limit() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 5);
    write(root, "architecture.hy", &ARCHITECTURE.replace(" :invariants [\"app.queue.lease_invariants:fenced-writes\"]", ""));
    git(root, &["add", "architecture.hy"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &root.join(".git").join("doeff-linter-cache"), 4));
    assert_ne!(got.code, 0, "{:?}", got);
    assert_eq!(got.code, 1, "{:?}", got);
    assert!(got.lines.iter().any(|line| line.contains("HEAD の木の repo 全体の比べを測れなかった") && line.contains("commit を止める")), "{:?}", got);
    assert!(got.lines.iter().any(|line| line == RERUN_GUIDE), "{:?}", got);
    let command = rerun_command(&got.lines);
    assert!(command.ends_with("--commit-hook-timeout-s 0"), "{}", command);
    let (code, stderr) = run_rerun(root, &command);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("repo 全体の規則の HEAD に無い当たり: architecture.hy::DOEFF163::queue"), "{}", stderr);
    assert!(!stderr.contains("測れなかった"), "{}", stderr);
}

/// 上の対: 上限の内で終わった commit は今までどおり通す(終了コード 0・行なし)— 遅い代役でも上限に届かなければ止めない。
#[test]
fn a_comparison_within_the_limit_still_passes() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let linter = slow_head_linter(side.path(), 1);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let got = doeff_linter::commit_hook::assess(&options_with(root, &linter, &side.path().join("cache"), 60));
    assert_eq!(got.code, 0, "{:?}", got);
    assert!(got.lines.is_empty(), "{:?}", got);
}

/// agora-redesign #2683 の設定 — DOEFF172(写像の置き場の臭い・major の warning — 終了コードを変えない)だけ。
const WARNING_PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF172"]

[tool.doeff-linter.commit_hook]
timeout_s = 120
"#;

/// 層の置き場(core)の file — 定義 1 つが handler の外で写像を組む(DOEFF172 が 1 つ)。
const ONE_MAP: &str = "(defk table [n]\n  {\"a\" n})\n";

/// warning の 1 つ在る基点を commit した repo。
fn warning_repo() -> tempfile::TempDir {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "pyproject.toml", WARNING_PYPROJECT);
    write(root, "app/core/table.hy", ONE_MAP);
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "warning 1 つの基点"]);
    dir
}

/// 失敗ケース(agora-redesign #2683): major の warning は終了コードを変えないので、hook は規則ごとの数を HEAD の版と比べる。同じ file に
/// DOEFF172 を 1 つ足した commit と、warning の在る新しい file を足した commit は 1 で止まり、規則と数を名指す。
#[test]
fn a_new_major_warning_blocks_and_names_the_rule_and_counts() {
    let dir = warning_repo();
    let root = dir.path();
    write(root, "app/core/table.hy", &format!("{}\n(defk other [n]\n  {{\"b\" n}})\n", ONE_MAP));
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(
        stderr.contains("doeff-linter commit-hook: major の warning が HEAD の版より増えた(stage した file の組の数): DOEFF172 1 → 2"),
        "{}",
        stderr
    );

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    write(root, "app/core/fresh.hy", ONE_MAP);
    git(root, &["add", "app/core/fresh.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    // 組は stage した file だけ(table.hy は stage していない)— 新しい file の HEAD の版は無いので 0 から。
    assert!(stderr.contains("DOEFF172 0 → 1"), "新しい file の warning も組の数に入る: {}", stderr);
}

/// 数の増えない変更は通る — 同じ数の書き換え・別の file への移し(git mv — 移した元の HEAD の版も数える)・warning を直す変更。
/// 直した commit の後は、その HEAD が次の基点になる(基点の file を持たない)。
#[test]
fn a_same_count_change_a_move_and_a_fix_pass() {
    let dir = warning_repo();
    let root = dir.path();
    write(root, "app/core/table.hy", "(defk table [m]\n  {\"a\" m})\n");
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(!stderr.contains("増えた"), "{}", stderr);

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    git(root, &["mv", "app/core/table.hy", "app/core/moved.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "移しは数を増やさない: {}", stderr);

    git(root, &["reset", "-q", "--hard", "HEAD"]);
    write(root, "app/core/table.hy", "(defk table [n]\n  n)\n");
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    git(root, &["commit", "-q", "-m", "直した"]);
    // 直した後の HEAD が基点 — 同じ写像を戻す commit は 0 → 1 で止まる。
    write(root, "app/core/table.hy", ONE_MAP);
    git(root, &["add", "app/core/table.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 1, "{}", stderr);
    assert!(stderr.contains("DOEFF172 0 → 1"), "{}", stderr);
}

/// repo の HEAD の commit(40 桁)。
fn head_sha(root: &Path) -> String {
    let out = Command::new("git").args(["rev-parse", "HEAD"]).current_dir(root).env_remove("GIT_DIR").output().unwrap();
    String::from_utf8_lossy(&out.stdout).trim().to_string()
}

/// テストの場所に HEAD の木を用意する(lock を待つ上限 1 秒・一時の木は scratch の下)。
fn place(root: &Path, overlay: &[String], home: &Path, scratch: &Path) -> doeff_linter::commit_hook::HeadTree {
    doeff_linter::commit_hook::head_tree(root, overlay, home, scratch, std::time::Duration::from_secs(1)).unwrap().unwrap()
}

/// 失敗ケース(#3858): hook を 2 回実行すると、HEAD の木は cache の commit-hook-tree/<repo を表す名>/tree に残り、同じ HEAD の 2 回目は何も
/// 書かずに同じ木を使う(以前は一時 dir に書き出して終わりに消していた — cache に木が残らない)。
#[test]
fn the_hook_keeps_the_head_tree_under_the_cache_for_the_next_run() {
    let dir = baseline_repo();
    let root = dir.path();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    let trees = kept_trees(root);
    assert_eq!(trees.len(), 1, "{:?}", trees);
    assert_eq!(trees[0].file_name().unwrap(), "tree");
    let record = std::fs::read_to_string(trees[0].parent().unwrap().join("tree.sha")).unwrap();
    assert_eq!(record, format!("{}\n", head_sha(root)));
    std::fs::write(trees[0].join("reused.mark"), "").unwrap();
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(trees[0].join("reused.mark").is_file(), "2 回目が木を書き出し直した");
}

/// 失敗ケース: 同じ HEAD の 2 回目は何も書かない・HEAD を進めた回は変わった path だけを書く・記録の無い木は全部を書き出し直す。
#[test]
fn the_tree_is_written_whole_once_then_only_by_the_difference() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    use doeff_linter::commit_hook::TreeUpdate;
    assert_eq!(place(root, &[], &home, &scratch).update, TreeUpdate::Rebuilt);
    assert_eq!(place(root, &[], &home, &scratch).update, TreeUpdate::Unchanged);
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    std::fs::remove_file(root.join("app/queue/tool.py")).unwrap();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "次"]);
    let advanced = place(root, &[], &home, &scratch);
    assert_eq!(advanced.update, TreeUpdate::Advanced { rewritten: 1, removed: 1 });
    // 空になった dir は残さない(git は空の dir を持たない)。
    write(root, "app/gone/only.py", "x = 1\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "足す"]);
    drop(advanced);
    let tree = place(root, &[], &home, &scratch).path;
    assert!(tree.join("app/gone/only.py").is_file());
    std::fs::remove_file(root.join("app/gone/only.py")).unwrap();
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "消す"]);
    place(root, &[], &home, &scratch);
    assert!(!tree.join("app/gone").exists(), "空になった dir が残る");
}

/// 失敗ケース: 重ねた宣言の file(1 回限りの指定 Lint-Baseline)は、次に木を使う時に木の sha の中身へ戻す — 戻さないと、次の commit の
/// 基点が今の宣言で測られる。先端に在って HEAD に無い宣言の file は、戻す時に消す。
#[test]
fn an_overlaid_declaration_is_restored_on_the_next_run() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    write(root, "architecture.hy", "; 先端の宣言\n");
    write(root, "extra.hy", "; 先端だけの宣言\n");
    let overlay = vec!["architecture.hy".to_string(), "extra.hy".to_string()];
    let overlaid = place(root, &overlay, &home, &scratch);
    assert_eq!(std::fs::read_to_string(overlaid.path.join("architecture.hy")).unwrap(), "; 先端の宣言\n");
    assert!(overlaid.path.join("extra.hy").is_file());
    drop(overlaid);
    let plain = place(root, &[], &home, &scratch);
    assert_eq!(std::fs::read_to_string(plain.path.join("architecture.hy")).unwrap(), ARCHITECTURE);
    assert!(!plain.path.join("extra.hy").exists(), "HEAD に無い宣言の file が残る");
}

/// 失敗ケース: 同時の 2 本 — 別の実行が木の lock を持つ間は、置いた木に触らず、この実行の一時 dir に書き出した木を使う(木を測っている
/// 最中の別の実行の木を書き換えない)。
#[test]
fn a_tree_in_use_is_not_rewritten_and_a_temporary_tree_is_used() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    let held = place(root, &[], &home, &scratch);
    let kept_path = held.path.clone();
    write(root, "app/queue/main.hy", "(defk cycle [] 2)\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "次"]);
    let other = doeff_linter::commit_hook::head_tree(root, &[], &home, &scratch, std::time::Duration::from_millis(200)).unwrap().unwrap();
    assert_eq!(other.update, doeff_linter::commit_hook::TreeUpdate::Temporary);
    assert!(other.path.starts_with(&scratch), "{:?}", other.path);
    assert_eq!(std::fs::read_to_string(other.path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
    assert_eq!(std::fs::read_to_string(kept_path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 1)\n", "使用中の木を書き換えた");
    drop(held);
    let after = place(root, &[], &home, &scratch);
    assert_eq!(after.path, kept_path);
    assert_eq!(std::fs::read_to_string(after.path.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
}

/// 同じ repo の別の作業木は同じ 1 つの木を分け合う(repo を表す名は git の共有 dir から)。記録した共有 dir が無くなった repo の dir は
/// 消す・ほかの名の物には触らない。
#[test]
fn worktrees_of_one_repo_share_the_tree_and_vanished_repos_are_removed() {
    let dir = baseline_repo();
    let root = dir.path();
    let side = tempfile::TempDir::new().unwrap();
    let (home, scratch) = (side.path().join("commit-hook-tree"), side.path().join("scratch"));
    let linked = side.path().join("linked");
    git(root, &["worktree", "add", "-q", "--detach", &linked.to_string_lossy(), "HEAD"]);
    let main_tree = place(root, &[], &home, &scratch).path;
    let linked_tree = place(&linked, &[], &home, &scratch).path;
    assert_eq!(main_tree, linked_tree);
    let gone = home.join(format!("{:016x}", 5));
    std::fs::create_dir_all(&gone).unwrap();
    std::fs::write(gone.join("repo.path"), side.path().join("no-such-repo/.git").to_string_lossy().as_bytes()).unwrap();
    let unrecorded = home.join(format!("{:016x}", 6));
    std::fs::create_dir_all(&unrecorded).unwrap();
    place(root, &[], &home, &scratch);
    assert!(!gone.exists(), "共有 dir の無くなった repo の dir が残る");
    assert!(unrecorded.is_dir(), "記録の無い dir を消した");
}

// ------------------------------------------------------------------ HEAD の木は repo ごとに 1 つ(agora-redesign #3858 の直し)
// 事実の cache(facts_cache)は根の path ごとの dir に、file ごとの大きさと更新時刻を鍵にして置かれる。木を sha ごとの dir に置くと、
// commit のたびに HEAD が進むふだんの使い方では毎回が新しい根 = cache 無しの 1 回目になる。木は repo ごとに 1 つ置き、HEAD が進んだ時は
// 変わった file だけを書き換える。

/// hook が cache(repo の .git の下の事実の cache の根)に置いた HEAD の木の根の一覧(architecture.hy を持つ dir — 深さ 2 まで)。
fn kept_trees(root: &Path) -> Vec<std::path::PathBuf> {
    let home = root.join(".git").join("doeff-linter-cache").join("commit-hook-tree");
    let mut found = Vec::new();
    let mut walk = vec![(home, 0usize)];
    while let Some((dir, depth)) = walk.pop() {
        if dir.join("architecture.hy").is_file() {
            found.push(dir);
            continue;
        }
        if depth < 2 {
            for entry in std::fs::read_dir(&dir).into_iter().flatten().flatten() {
                if entry.file_type().map(|t| t.is_dir()).unwrap_or(false) {
                    walk.push((entry.path(), depth + 1));
                }
            }
        }
    }
    found.sort();
    found
}

/// file の (inode・更新時刻の ns・大きさ) — 書き換えなかった file はどれも変わらない。
fn file_stamp(path: &Path) -> (u64, i128, u64) {
    use std::os::unix::fs::MetadataExt;
    let meta = std::fs::metadata(path).unwrap_or_else(|e| panic!("{}: {}", path.display(), e));
    (meta.ino(), i128::from(meta.mtime()) * 1_000_000_000 + i128::from(meta.mtime_nsec()), meta.len())
}

/// ふだんの使い方の 1 回 — 1 行を変えて stage し、hook を実行する(通ることを確かめる)。
fn stage_and_hook(root: &Path, text: &str) {
    write(root, "app/queue/main.hy", text);
    git(root, &["add", "app/queue/main.hy"]);
    let (code, stderr) = hook(root, &[]);
    assert_eq!(code, 0, "{}", stderr);
}

/// 失敗ケース: HEAD を 1 つ進めた 2 回目の hook は、同じ 1 つの木を新しい sha へ進める — 変わらない file は書き換えず(inode と更新時刻が
/// 前と同じ — 事実の cache が当たる)、変わった file は新しい sha の中身・消えた file は消え・足した file は在る。以前は sha ごとに別の木を
/// 書き出し、変わらない file も新しい file になっていた(事実の cache は毎回 1 回目)。
#[test]
fn advancing_head_rewrites_only_the_changed_files_of_the_one_kept_tree() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let first = kept_trees(root);
    assert_eq!(first.len(), 1, "{:?}", first);
    let tree = first[0].clone();
    let untouched = file_stamp(&tree.join("app/foundation/host.hy"));
    std::fs::remove_file(root.join("app/queue/tool.py")).unwrap();
    write(root, "app/queue/extra.py", "import sys\n");
    git(root, &["add", "-A"]);
    git(root, &["commit", "-q", "-m", "進める"]);
    stage_and_hook(root, "(defk cycle [] 33)\n");
    assert_eq!(kept_trees(root), vec![tree.clone()], "HEAD を進めても木は 1 つ・同じ path");
    assert_eq!(file_stamp(&tree.join("app/foundation/host.hy")), untouched, "変わらない file を書き換えた");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
    assert!(!tree.join("app/queue/tool.py").exists(), "消えた file が残る");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/extra.py")).unwrap(), "import sys\n");
}

/// 失敗ケース: 同じ大きさで中身が変わった file は、事実の cache の鍵(大きさと更新時刻)の更新時刻が必ず変わる — 取りこぼすと前の中身の
/// 事実を読む。
#[test]
fn a_same_size_change_gets_a_new_cache_key() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let tree = kept_trees(root).remove(0);
    let before = file_stamp(&tree.join("app/queue/main.hy"));
    git(root, &["commit", "-q", "-m", "同じ大きさ"]);
    stage_and_hook(root, "(defk cycle [] 3)\n");
    assert_eq!(kept_trees(root), vec![tree.clone()]);
    let after = file_stamp(&tree.join("app/queue/main.hy"));
    assert_eq!(after.2, before.2, "大きさは同じ");
    assert_ne!(after.1, before.1, "同じ大きさで中身が変わった file の更新時刻が変わらない");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 2)\n");
}

/// 失敗ケース: 書き換えの途中で落ちた木(木の今の sha の記録が無い)は使わず、作り直す。
#[test]
fn a_tree_left_in_the_middle_of_an_update_is_rebuilt() {
    let dir = baseline_repo();
    let root = dir.path();
    stage_and_hook(root, "(defk cycle [] 2)\n");
    let tree = kept_trees(root).remove(0);
    std::fs::write(tree.join("app/queue/main.hy"), "(defk half-written [] 0)\n").unwrap();
    // 書き換えの間は記録を消しておく形 — 落ちた木は記録を持たない。
    match std::fs::remove_file(tree.parent().unwrap().join("tree.sha")) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
        Err(e) => panic!("{}", e),
    }
    stage_and_hook(root, "(defk cycle [] 3)\n");
    assert_eq!(std::fs::read_to_string(tree.join("app/queue/main.hy")).unwrap(), "(defk cycle [] 1)\n", "落ちた木を使った");
}

/// 失敗ケース: 前の形の sha ごとの木(<sha>・<sha>-<16 桁>・書き出しの残り .partial-…)は、新しい形が初めて走った時に消す。
/// ほかの名の物には触らない。
#[test]
fn old_sha_named_trees_are_removed_on_the_first_run() {
    let dir = baseline_repo();
    let root = dir.path();
    let home = root.join(".git").join("doeff-linter-cache").join("commit-hook-tree");
    let old = [format!("{:040x}", 7), format!("{:040x}-{:016x}", 7, 9), ".partial-crashed".to_string()];
    for name in old.iter().chain(["notes".to_string()].iter()) {
        std::fs::create_dir_all(home.join(name)).unwrap();
    }
    stage_and_hook(root, "(defk cycle [] 2)\n");
    for name in &old {
        assert!(!home.join(name).exists(), "{} が残る", name);
    }
    assert!(home.join("notes").is_dir(), "ほかの名の物を消した");
}
