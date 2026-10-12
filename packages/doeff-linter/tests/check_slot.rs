//! 機体の検の枠の門の検(card ki-ce35bf38508d)— 重い mode の doeff-linter は、HOME の下の正本
//! (`dotfiles/agent/tests/machine_check_slots.py`)の命令の包み `run` へ自分を渡し、枠を取ってから走る。
//!
//! 本物の正本を写した検は、HOME を一時の dir に向け、正本の module の写しと、K = 1・置き場 = 一時の dir の宣言を隣に置いて撃つ
//! (走っている機体の枠 /tmp/agora-check-slots に触らない — dotfiles の agent/tests/test_machine_check_slots.py と同じ形)。
//! 正本は撃つ機体の HOME の dotfiles から写す — 無い機体では、何を確かめられないかを名乗って赤にする(未実行を合格にしない)。
//! 門の道の形(どの mode が正本を通るか・同じ引数・再入の印)は、引数と env を記して命令を exec する偽の正本で見る。

use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

const LINTER: &str = env!("CARGO_BIN_EXE_doeff-linter");
/// 子の process を待つ上限。
const DONE: Duration = Duration::from_secs(60);

/// 撃つ機体の正本(HOME の下)。無ければ何を確かめられないかを名乗って赤。
fn real_canon() -> PathBuf {
    let home = std::env::var_os("HOME").expect("HOME が無い");
    let canon = Path::new(&home).join("dotfiles/agent/tests/machine_check_slots.py");
    assert!(
        canon.is_file(),
        "正本 {} が無い — この検は機体の検の枠の正本(dotfiles)を HOME に置いた機体で走る(枠を待つ linter を確かめられない)",
        canon.display()
    );
    canon
}

/// 撃つ機体の python(linter が正本を走らせる時と同じ `uv python find`)。
fn python() -> PathBuf {
    let found = Command::new("uv").args(["python", "find"]).output().expect("uv を起こせない");
    assert!(found.status.success(), "uv python find: {}", String::from_utf8_lossy(&found.stderr));
    PathBuf::from(String::from_utf8_lossy(&found.stdout).trim())
}

/// lint する小さな repo(Python の file 1 つ・DOEFF016 = 相対 import の当たり 1 件 — lint が走れば終了 1)。
fn small_repo(root: &Path) -> PathBuf {
    let repo = root.join("repo");
    fs::create_dir_all(&repo).unwrap();
    fs::write(repo.join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF016\"]\n").unwrap();
    fs::write(repo.join("tool.py"), "from . import sibling\n").unwrap();
    repo
}

/// HOME を一時の dir に向けた linter の命令。uv が python を探す置き場は撃つ機体のまま(HOME を替えても同じ python を見つける)。
fn linter_under(home: &Path, repo: &Path) -> Command {
    let mut command = Command::new(LINTER);
    command.current_dir(repo).env("HOME", home).env_remove("DOEFF_LINTER_CHECK_SLOT");
    if std::env::var_os("UV_PYTHON_INSTALL_DIR").is_none() && std::env::var_os("XDG_DATA_HOME").is_none() {
        let real_home = std::env::var_os("HOME").expect("HOME が無い");
        command.env("XDG_DATA_HOME", Path::new(&real_home).join(".local/share"));
    }
    command
}

/// 条件が成り立つまで待つ。ポーリングの訳: 別の process が枠を取った・待ちを名乗った事を知らせる口は無い(検体の外の観測)。
fn wait_until(what: &str, mut ready: impl FnMut() -> bool) {
    let deadline = Instant::now() + DONE;
    while !ready() {
        assert!(Instant::now() < deadline, "{what}");
        std::thread::sleep(Duration::from_millis(50));
    }
}

fn read(path: &Path) -> String {
    fs::read_to_string(path).unwrap_or_default()
}

fn finish(mut child: Child, what: &str) -> std::process::ExitStatus {
    let deadline = Instant::now() + DONE;
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            return status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            panic!("{what} が終わらない");
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// 正本の写しと、K = 1・置き場 = 一時の dir・待ちの上限 = wait_s の宣言を置いた HOME と、その正本と枠 0 の file。
fn canon_home(root: &Path, wait_s: u64) -> (PathBuf, PathBuf, PathBuf) {
    let home = root.join("home");
    let tests_dir = home.join("dotfiles/agent/tests");
    fs::create_dir_all(&tests_dir).unwrap();
    let canon = tests_dir.join("machine_check_slots.py");
    fs::copy(real_canon(), &canon).unwrap();
    let slots = root.join("slots");
    let declaration = serde_json::json!({"slots": 1, "dir": slots.to_string_lossy(), "waitLimitSeconds": wait_s});
    fs::write(tests_dir.join("machine_check_slots.json"), declaration.to_string()).unwrap();
    (home, canon, slots.join("slot-0.lock"))
}

/// 保持者 = 正本の包みで枠を 1 本取り、stdin を閉じるまで持つ命令(枠を取ったのを見てから返す)。
fn holding(home: &Path, canon: &Path, slot: &Path) -> Child {
    let python = python();
    let holder = Command::new(&python)
        .arg(canon)
        .args(["run", "--label", "long-check", "--"])
        .arg(&python)
        .args(["-c", "import sys; sys.stdin.read()"])
        .env("HOME", home)
        .env_remove("AGORA_CONVERSATION_ID")
        .stdin(Stdio::piped())
        .spawn()
        .unwrap();
    wait_until("保持者が枠を取らない", || read(slot).starts_with("pid "));
    holder
}

/// 正本が枠を持つ process の子へ継ぐ内部の印(env の名)— 正本の source の `HOLDER_ENV = "…"` から読む(この検に名を写さない)。
fn canon_holder_env() -> String {
    let text = read(&real_canon());
    let line = text.lines().find(|line| line.starts_with("HOLDER_ENV = ")).expect("正本に HOLDER_ENV の行が無い");
    line.split('"').nth(1).expect("HOLDER_ENV の値").to_string()
}

#[test]
fn a_linter_beyond_the_slots_waits_names_the_holder_and_runs_when_one_frees() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, canon, slot) = canon_home(tmp.path(), 60);
    let repo = small_repo(tmp.path());
    let mut holder = holding(&home, &canon, &slot);

    let err_path = tmp.path().join("linter.err");
    let mut linter = linter_under(&home, &repo)
        .args(["--no-log", "."])
        .env_remove("AGORA_CONVERSATION_ID")
        .stdout(Stdio::piped())
        .stderr(fs::File::create(&err_path).unwrap())
        .spawn()
        .unwrap();
    wait_until("linter が待ちを名乗らない", || read(&err_path).contains("埋まっているので待つ"));
    let waiting = read(&err_path);
    assert!(
        waiting.contains(&format!("保持 pid {} long-check", holder.id())) && waiting.contains("待つのは doeff-linter lint repo"),
        "{waiting}"
    );
    assert!(linter.try_wait().unwrap().is_none(), "枠が埋まっているのに linter が走り終えた");
    assert!(!read(&slot).contains("doeff-linter"), "枠が埋まっているのに linter が枠を取った");

    // 保持者が解いた拍に linter が枠を取り、lint して終わる(枠を取った名乗り・lint の当たり・終われば解く)。
    holder.stdin.take().unwrap().write_all(b"").unwrap();
    assert!(finish(holder, "保持者").success());
    let output = linter.wait_with_output().unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout);
    let err = read(&err_path);
    assert_eq!(output.status.code(), Some(1), "当たり(DOEFF016)で 1 のはず: {stdout}\n{err}");
    assert!(stdout.contains("DOEFF016"), "{stdout}");
    assert!(err.contains("を取った") && err.contains("— doeff-linter lint repo"), "{err}");
    assert_eq!(read(&slot), "", "linter が終わっても枠の名乗りが残った");
}

#[test]
fn a_stale_holder_marker_does_not_let_the_linter_skip_a_full_slot_and_the_expiry_exits_75() {
    // 入れ子かどうか(祖先が枠を持つか)は正本だけが判じる — 正本の内部の印(env)を継いだだけの linter は、印の指す pid が
    // 枠を持っていなければ内側ではない(保持者が退いた後に残った子・背景の子 — 盲検 B・設計検証 lt-2QBB34XF77DRT3V3THV7T67D3P)。
    // 枠が埋まったままなら待ちの上限で lint をせずに終わり、linter の公開の終了コード 75(未実行)を返す。
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, canon, slot) = canon_home(tmp.path(), 2);
    let repo = small_repo(tmp.path());
    let mut holder = holding(&home, &canon, &slot);
    let output = linter_under(&home, &repo)
        .args(["--no-log", "."])
        .env(canon_holder_env(), std::process::id().to_string())
        .env_remove("AGORA_CONVERSATION_ID")
        .output()
        .unwrap();
    holder.stdin.take().unwrap().write_all(b"").unwrap();
    assert!(finish(holder, "保持者").success());
    let stdout = String::from_utf8_lossy(&output.stdout);
    let err = String::from_utf8_lossy(&output.stderr);
    assert_eq!(output.status.code(), Some(75), "印を継いだだけの linter が枠の外で走った: {stdout}\n{err}");
    assert!(!stdout.contains("DOEFF016"), "未実行のはずが lint した: {stdout}");
    assert!(err.contains("埋まっているので待つ") && err.contains("doeff-linter lint repo は未実行"), "{err}");
}

/// 偽の正本 — 引数と再入の印を 1 行ずつ記し、`--` の後の命令を exec する(枠は取らない)。
const FAKE_CANON: &str = r#"import json, os, sys
with open(os.path.join(os.path.dirname(__file__), "calls.jsonl"), "a") as log:
    log.write(json.dumps({"argv": sys.argv[1:], "gated": os.environ.get("DOEFF_LINTER_CHECK_SLOT")}) + "\n")
rest = sys.argv[sys.argv.index("--") + 1:]
os.execv(rest[0], rest)
"#;

fn fake_home(root: &Path) -> (PathBuf, PathBuf) {
    let home = root.join("home");
    let tests_dir = home.join("dotfiles/agent/tests");
    fs::create_dir_all(&tests_dir).unwrap();
    fs::write(tests_dir.join("machine_check_slots.py"), FAKE_CANON).unwrap();
    (home, tests_dir.join("calls.jsonl"))
}

fn calls(log: &Path) -> Vec<serde_json::Value> {
    read(log).lines().map(|line| serde_json::from_str(line).unwrap()).collect()
}

#[test]
fn a_repo_scan_hands_itself_and_its_arguments_to_the_canon_once() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    let output = linter_under(&home, &repo).args(["--no-log", "--output-format", "json", "."]).output().unwrap();
    assert_eq!(output.status.code(), Some(1), "{}", String::from_utf8_lossy(&output.stderr));
    assert!(String::from_utf8_lossy(&output.stdout).contains("DOEFF016"));
    let seen = calls(&log);
    assert_eq!(seen.len(), 1, "正本を 1 度だけ通る(exec された linter は印で通らない): {seen:?}");
    let argv: Vec<String> = seen[0]["argv"].as_array().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
    let exe = fs::canonicalize(LINTER).unwrap();
    assert_eq!(
        argv,
        ["run", "--label", "doeff-linter lint repo", "--", exe.to_str().unwrap(), "--no-log", "--output-format", "json", "."]
    );
    assert_eq!(seen[0]["gated"], "1", "包みへ渡す時に再入の印を付ける(子孫の linter が python を通らない)");
}

#[test]
fn cheap_and_caller_bounded_modes_do_not_go_through_the_canon() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    for args in [
        vec!["--list-rules"],
        vec!["--split-rules", "tool.py"],
        vec!["--affected-tests", "tool.py", "--test-pattern", "test_*.py"],
    ] {
        // 一時の repo は git の木でない — `--split-rules`・`--affected-tests` は git を読めずに断りうる。見るのは正本を通らない事だけ。
        let output = linter_under(&home, &repo).args(&args).output().unwrap();
        assert!(output.status.code().is_some(), "{args:?} が信号で死んだ: {}", String::from_utf8_lossy(&output.stderr));
    }
    let mut stdin_run = linter_under(&home, &repo)
        .args(["--output-format", "editor-json", "--no-log", "--stdin", "--path", "tool.py"])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .spawn()
        .unwrap();
    stdin_run.stdin.take().unwrap().write_all(b"import os\n").unwrap();
    assert!(stdin_run.wait_with_output().unwrap().status.code().is_some());
    assert_eq!(calls(&log), Vec::<serde_json::Value>::new(), "軽い mode が正本を通った");
}

#[test]
fn a_linter_inside_the_gate_or_without_a_canon_runs_directly() {
    let tmp = tempfile::TempDir::new().unwrap();
    let (home, log) = fake_home(tmp.path());
    let repo = small_repo(tmp.path());
    let inside = linter_under(&home, &repo).args(["--no-log", "."]).env("DOEFF_LINTER_CHECK_SLOT", "1").output().unwrap();
    assert_eq!(inside.status.code(), Some(1));
    assert_eq!(calls(&log), Vec::<serde_json::Value>::new(), "印の付いた linter が正本をもう 1 度通った");

    let bare_home = tmp.path().join("bare-home");
    fs::create_dir_all(&bare_home).unwrap();
    let bare = linter_under(&bare_home, &repo).args(["--no-log", "."]).output().unwrap();
    assert_eq!(bare.status.code(), Some(1));
    let said = String::from_utf8_lossy(&bare.stderr);
    assert!(!said.contains("機体の検の枠") && !said.contains("check-slot:"), "正本の無い機体で枠の名乗りを出した: {said}");
}

/// linter の source から `//` の註を除いた code。
fn code_of(path: &Path) -> String {
    read(path).lines().map(|line| line.split("//").next().unwrap_or("")).collect::<Vec<_>>().join("\n")
}

fn rust_sources(dir: &Path) -> Vec<PathBuf> {
    fs::read_dir(dir)
        .unwrap()
        .map(|entry| entry.unwrap().path())
        .flat_map(|path| if path.is_dir() { rust_sources(&path) } else { vec![path] })
        .filter(|path| path.extension().is_some_and(|ext| ext == "rs"))
        .collect()
}

#[test]
fn the_linter_keeps_no_copy_of_the_slot_values() {
    // 値の持ち主は正本の隣の宣言 1 つ(K・置き場・待ちの上限)。linter の code はその値も宣言の file の名も綴らない —
    // 綴るのは正本の在処(HOME からの相対)だけ。正本の内部の印(枠を持つ process の子へ継ぐ env の名)も綴らない —
    // 入れ子の判定を linter に写さない(盲検 B・設計検証 lt-2QBB34XF77DRT3V3THV7T67D3P)。
    let holder_env = canon_holder_env();
    let declaration_path = real_canon().with_file_name("machine_check_slots.json");
    let declaration: serde_json::Value = serde_json::from_str(&read(&declaration_path)).unwrap();
    let dir = declaration["dir"].as_str().expect("宣言の dir");
    let slots = declaration["slots"].as_u64().expect("宣言の slots").to_string();
    let wait = declaration["waitLimitSeconds"].as_f64().expect("宣言の waitLimitSeconds");
    let sources = rust_sources(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src"));
    let gate = Path::new(env!("CARGO_MANIFEST_DIR")).join("src/check_slot.rs");
    assert!(sources.contains(&gate));
    let copies: Vec<String> = sources
        .iter()
        .flat_map(|path| {
            let code = code_of(path);
            let named = [dir, "machine_check_slots.json", "waitLimitSeconds", "slot-", holder_env.as_str()]
                .into_iter()
                .filter(|word| code.contains(word))
                .map(|word| format!("{}: {word}", path.display()))
                .collect::<Vec<_>>();
            let numbers = if *path == gate {
                // 識別子の中の数字(from_utf8 の 8)を数えない — 語(英数字と _ と .)の単位で、値と同じ綴りの語を探す。
                let tokens: Vec<&str> = code.split(|c: char| !(c.is_ascii_alphanumeric() || c == '_' || c == '.')).collect();
                [slots.clone(), format!("{wait}"), format!("{}", wait as u64)]
                    .into_iter()
                    .filter(|value| tokens.contains(&value.as_str()))
                    .map(|value| format!("{}: 数 {value}", path.display()))
                    .collect()
            } else {
                Vec::new()
            };
            named.into_iter().chain(numbers)
        })
        .collect();
    assert_eq!(copies, Vec::<String>::new(), "枠の値の写しが linter の code に在る");
}
