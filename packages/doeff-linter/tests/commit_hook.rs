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
whole_repo_rules = ["DOEFF163"]
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
/// (whole_repo_rules)は DOEFF163・166 だけ。
const REGISTRY_PYPROJECT: &str = r#"[tool.doeff-linter]
enable = ["DOEFF163", "DOEFF110", "DOEFF166"]
[tool.doeff-linter.definitions]
[tool.doeff-linter.registry]
dirs = ["reg"]

[tool.doeff-linter.commit_hook]
whole_repo_rules = ["DOEFF163", "DOEFF166"]
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
