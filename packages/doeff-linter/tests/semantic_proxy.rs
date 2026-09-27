//! 意味の規則と Jev の呼び出しを覚える代理(agora-redesign #843)の検。代理は手元の偽の HTTP(127.0.0.1)で、覚えは本文の綴りを鍵にする。
//! 反例: 全体の実行は代理に「覚えている時だけ」問い、本物の Jev を呼ばせない・代理に届かなくても止まらない・編集中の 1 file(--stdin)は
//! 代理に問わない・--semantic-changed は中身の変わった定義だけを問い、書きかけで読めない定義は問わない・較正は覚えを使わない・
//! 代理には代理の token だけを送る(TypeSafe のキーを送らない)・env の JEV_BASE_URL が repo の代理より勝つ。

use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// 偽の代理に届いた問い 1 つ(見出し Cache-Control・Authorization・定義の名)。
#[derive(Debug, Clone)]
struct Seen {
    cache_control: String,
    authorization: String,
    name: String,
}

/// 偽の代理: 本文の綴り → 答えを覚え、Cache-Control の only-if-cached / no-cache を代理と同じに扱う。
struct FakeProxy {
    url: String,
    seen: Arc<Mutex<Vec<Seen>>>,
}

/// 本物の Jev の代わりの答え(較正の例は幅に入る・source の語で確率を決める)。
fn answer_for(body: &Value) -> Value {
    let name = body["state"]["definition"]["name"].as_str().unwrap_or("");
    let source = body["state"]["definition"]["source"].as_str().unwrap_or("");
    let p = if ["may-manage?", "classifier-call"].contains(&name) || source.contains("permission") || source.contains("http://") {
        0.93
    } else {
        0.05
    };
    serde_json::json!({"answers": {"q": {"noul": p}}, "usage": {"input_tokens": 100}, "model": "jev-test-1"})
}

fn fake_proxy() -> FakeProxy {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    let memory: Arc<Mutex<HashMap<String, String>>> = Arc::new(Mutex::new(HashMap::new()));
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let log = log.clone();
            let memory = memory.clone();
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let (mut length, mut cache_control, mut authorization) = (0usize, String::new(), String::new());
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                        break;
                    }
                    let lower = line.to_ascii_lowercase();
                    if let Some(value) = lower.strip_prefix("content-length:") {
                        length = value.trim().parse().unwrap_or(0);
                    } else if let Some(value) = lower.strip_prefix("cache-control:") {
                        cache_control = value.trim().to_string();
                    } else if lower.starts_with("authorization:") {
                        authorization = line["authorization:".len()..].trim().to_string();
                    }
                }
                let mut raw = vec![0u8; length];
                reader.read_exact(&mut raw).unwrap();
                let text = String::from_utf8(raw).unwrap();
                let body: Value = serde_json::from_str(&text).unwrap();
                let name = body["state"]["definition"]["name"].as_str().unwrap_or("").to_string();
                log.lock().unwrap().push(Seen { cache_control: cache_control.clone(), authorization, name });
                let remembered = memory.lock().unwrap().get(&text).cloned();
                let (status, marker, answer) = match (cache_control.as_str(), remembered) {
                    ("only-if-cached", Some(answer)) => ("200 OK", "hit", answer),
                    ("only-if-cached", None) => ("504 Gateway Timeout", "absent", r#"{"error":"not-cached"}"#.to_string()),
                    ("no-cache", _) | (_, None) => {
                        let answer = answer_for(&body).to_string();
                        memory.lock().unwrap().insert(text, answer.clone());
                        ("200 OK", "miss", answer)
                    }
                    (_, Some(answer)) => ("200 OK", "hit", answer),
                };
                let _ = write!(
                    stream,
                    "HTTP/1.1 {}\r\ncontent-type: application/json\r\nx-jev-proxy: {}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                    status,
                    marker,
                    answer.len(),
                    answer
                );
            });
        }
    });
    FakeProxy { url, seen }
}

impl FakeProxy {
    /// 届いた問いを読み、列を空にする。
    fn take(&self) -> Vec<Seen> {
        std::mem::take(&mut *self.seen.lock().unwrap())
    }
}

const PROXY_TOKEN: &str = "proxy-token-for-test";
const TYPESAFE_KEY: &str = "apikey_typesafe_must_not_leave";

/// 層 2 つと意味の規則と代理の設定の repo(代理の token は repo の外の一時の file)。
fn repo(files: &[(&str, &str)], proxy_url: &str, token_file: &Path) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let config = format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF201", "DOEFF202"]
[tool.doeff-linter.layers]
order = ["core", "protocol"]
paths = {{ core = "app/core", protocol = "app/protocol" }}
[tool.doeff-linter.layers.describe.protocol]
summary = "翻訳の handler"
[tool.doeff-linter.semantic]
business_decision = {{ layers = ["protocol"] }}
transport_knowledge = {{ layers = ["core"] }}
proxy_url = "{proxy_url}"
proxy_token_file = "{token}"
"#,
        token = token_file.display()
    );
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// editor-json で走らせる(HOME は一時の dir・TypeSafe のキーを env に置く — 代理へ送られないことを見る)。
fn run(root: &Path, extra: &[&str], stdin: Option<&str>, env: &[(&str, &str)]) -> (Value, Duration) {
    let home = tempfile::TempDir::new().unwrap();
    let mut args = vec!["--output-format", "editor-json", "--no-log"];
    args.extend_from_slice(extra);
    let mut command = Command::new(env!("CARGO_BIN_EXE_doeff-linter"));
    command
        .args(&args)
        .current_dir(root)
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("HOME", home.path())
        .env("TYPESAFE_API_KEY", TYPESAFE_KEY)
        .stdin(if stdin.is_some() { Stdio::piped() } else { Stdio::null() })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    for (key, value) in env {
        command.env(key, value);
    }
    let started = Instant::now();
    let mut child = command.spawn().unwrap();
    if let Some(text) = stdin {
        child.stdin.take().unwrap().write_all(text.as_bytes()).unwrap();
    }
    let output = child.wait_with_output().unwrap();
    let elapsed = started.elapsed();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    (value, elapsed)
}

const FILES: &[(&str, &str)] = &[
    ("app/protocol/chat.hy", "(defk may-post? [who] (in who permission))\n(defk read-body [text] (json.loads text))\n"),
    ("app/core/plan.hy", "(defk route [cid] (+ \"http://records/\" cid))\n(defk decide [x] x)\n"),
];

fn token_file() -> tempfile::NamedTempFile {
    let mut file = tempfile::NamedTempFile::new().unwrap();
    write!(file, "{}", PROXY_TOKEN).unwrap();
    file
}

fn violation<'a>(report: &'a Value, rule: &str, name: &str) -> Option<&'a Value> {
    report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule && v["key"].as_str().unwrap().ends_with(name))
}

#[test]
fn whole_run_takes_remembered_answers_from_the_proxy_without_asking_jev() {
    let proxy = fake_proxy();
    let token = token_file();
    // 別の worktree(A)が問うて代理が覚えた — 較正は覚えを使わない(no-cache)。
    let first = repo(FILES, &proxy.url, token.path());
    let (asked, _) = run(first.path(), &["--semantic-all"], None, &[]);
    assert_eq!(asked["semantic"]["wire"], "direct(repo)", "{}", asked["semantic"]);
    let seen = proxy.take();
    let calibration: Vec<&Seen> = seen.iter().filter(|s| ["may-manage?", "classifier-call", "post-json", "wake"].contains(&s.name.as_str())).collect();
    assert!(!calibration.is_empty());
    assert!(calibration.iter().all(|s| s.cache_control == "no-cache"), "較正は覚えを使わない: {:?}", calibration);
    assert!(seen.iter().filter(|s| !calibration.iter().any(|c| c.name == s.name)).all(|s| s.cache_control.is_empty()));
    assert!(seen.iter().all(|s| s.authorization == format!("Bearer {}", PROXY_TOKEN)), "代理には代理の token だけ: {:?}", seen);
    // 手元の cache の空の worktree(B)の全体の実行は、代理に「覚えている時だけ」問うて答えを得る。本物の Jev への問い(印なし)は 0。
    let second = repo(FILES, &proxy.url, token.path());
    let (plain, _) = run(second.path(), &[], None, &[]);
    let seen = proxy.take();
    assert!(!seen.is_empty() && seen.iter().all(|s| s.cache_control == "only-if-cached"), "{:?}", seen);
    assert_eq!(plain["semantic"]["asked"], 0);
    assert_eq!(plain["semantic"]["peeked"], 4, "{}", plain["semantic"]);
    assert_eq!(plain["semantic"]["unjudged"], 0);
    let decision = violation(&plain, "DOEFF201", "may_post?").or_else(|| violation(&plain, "DOEFF201", "hyx_may_postXquestion_markX"));
    assert_eq!(decision.expect("代理の覚えの答えで違反が出る")["probability"], 0.93);
    assert!(violation(&plain, "DOEFF202", "route").is_some());
    // 受け取った答えは手元の cache に書いたので、次の全体の実行は代理に問わない。
    let (again, _) = run(second.path(), &[], None, &[]);
    assert!(proxy.take().is_empty());
    assert_eq!(again["semantic"]["peeked"], 0);
    assert_eq!(again["semantic"]["judged"], 4);
}

#[test]
fn whole_run_without_a_reachable_proxy_stays_on_the_local_cache() {
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", closed.local_addr().unwrap());
    drop(closed);
    let token = token_file();
    let dir = repo(FILES, &url, token.path());
    let (report, elapsed) = run(dir.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unjudged"], 4, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["peeked"], 0);
    assert!(elapsed < Duration::from_secs(5), "届かない代理で止まらない: {:?}", elapsed);
}

#[test]
fn stdin_plain_run_does_not_ask_the_proxy() {
    let proxy = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let path = dir.path().join("app/protocol/chat.hy");
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap()], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert!(report["semantic"].is_object());
    assert!(proxy.take().is_empty(), "編集中の決定的な実行は代理にも問わない");
}

#[test]
fn semantic_changed_asks_only_changed_and_readable_definitions() {
    let proxy = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let _ = run(dir.path(), &["--semantic-all"], None, &[]);
    proxy.take();
    let path = dir.path().join("app/protocol/chat.hy");
    // may-post? は変えず・read-body は中身を変え・書きかけの half を足す。
    let edited = "(defk may-post? [who] (in who permission))\n(defk read-body [text] (json.loads (.strip text)))\n(defk half [x] (+ x\n";
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic", "--semantic-changed"], Some(edited), &[]);
    let asked: Vec<String> = proxy
        .take()
        .into_iter()
        .filter(|s| !["may-manage?", "classifier-call", "post-json", "wake"].contains(&s.name.as_str()))
        .map(|s| s.name)
        .collect();
    assert_eq!(asked, vec!["read-body".to_string()], "変わった定義だけを問い、書きかけ(half)は問わない");
    assert_eq!(report["semantic"]["unjudged"], 1, "書きかけは未判定: {}", report["semantic"]);
    // 何も変わっていなければ 1 つも問わない(較正も撃たない)。
    let (_, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic", "--semantic-changed"], Some(edited), &[]);
    assert!(proxy.take().is_empty());
}

#[test]
fn env_base_url_wins_over_the_repo_proxy() {
    let proxy = fake_proxy();
    let other = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let (report, _) = run(dir.path(), &["--semantic-all"], None, &[("JEV_BASE_URL", &other.url), ("JEV_API_KEY", "env-key")]);
    assert_eq!(report["semantic"]["wire"], "direct(env)");
    assert!(proxy.take().is_empty(), "env の宛先が在れば repo の代理を使わない");
    let seen = other.take();
    assert!(!seen.is_empty() && seen.iter().all(|s| s.authorization == "Bearer env-key" && s.cache_control.is_empty()), "{:?}", seen);
}
