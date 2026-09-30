//! 意味の規則と Jev の呼び出しを覚える代理(agora-redesign #843)の検。代理は手元の偽の HTTP(127.0.0.1)で、覚えは本文の代理の鍵
//! (proxy_key)で引く。反例: 全体の実行は代理に鍵の束で「覚えている時だけ」問い(定義 1 つずつ撃たない・本文を送らない)、本物の Jev を
//! 呼ばせない・代理に届かなくても止まらない・編集中の 1 file(--stdin)は
//! 代理に問わない・--semantic-changed は中身の変わった定義だけを問い、書きかけで読めない定義は問わない・較正は覚えを使わない・
//! 代理には代理の token だけを送る(TypeSafe のキーを送らない)・env の JEV_BASE_URL が repo の代理より勝つ。

use doeff_linter::project::semantic::{proxy_key, PEEK_BATCH};
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// 偽の代理に届いた問い 1 つ(path・見出し Cache-Control・Authorization・定義の名・束の鍵の数)。
#[derive(Debug, Clone)]
struct Seen {
    path: String,
    cache_control: String,
    authorization: String,
    name: String,
    keys: usize,
}

/// 偽の代理: 本文の代理の鍵(linter の proxy_key — 代理と同じ鍵になることは key_contract の検が確かめる)→ 答えを覚え、
/// /v1/systemone の no-cache と、/v1/systemone/peek(覚えている時だけの問いの束)を代理と同じに扱う。
struct FakeProxy {
    url: String,
    seen: Arc<Mutex<Vec<Seen>>>,
}

/// 本物の Jev の代わりの答え(較正の例は幅に入る・source の語で確率を決める)。cost が在れば上流が費用を載せた答え(Vercel の AI
/// Gateway の形 providerMetadata.gateway.cost)、無ければ TypeSafe 直と同じく usage だけの答え。
fn answer_for(body: &Value, cost: Option<&str>) -> Value {
    let name = body["state"]["definition"]["name"].as_str().unwrap_or("");
    let source = body["state"]["definition"]["source"].as_str().unwrap_or("");
    let p = if ["may-manage?", "classifier-call"].contains(&name) || source.contains("permission") || source.contains("http://") {
        0.93
    } else {
        0.05
    };
    let mut answer = serde_json::json!({"answers": {"q": {"noul": p}}, "usage": {"input_tokens": 100, "output_tokens": 2}, "model": "jev-test-1"});
    if let Some(cost) = cost {
        answer["providerMetadata"] = serde_json::json!({"gateway": {"cost": cost}});
    }
    answer
}

/// 上流が費用を載せない(TypeSafe 直と同じ)偽の proxy。
fn fake_proxy() -> FakeProxy {
    fake_proxy_with_cost(None)
}

/// 上流が答えに費用 cost を載せる(載せないなら None)偽の proxy。
fn fake_proxy_with_cost(cost: Option<&'static str>) -> FakeProxy {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    let seen = Arc::new(Mutex::new(Vec::new()));
    let log = seen.clone();
    let memory: Arc<Mutex<HashMap<String, Value>>> = Arc::new(Mutex::new(HashMap::new()));
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            let log = log.clone();
            let memory = memory.clone();
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut request_line = String::new();
                reader.read_line(&mut request_line).unwrap();
                let path = request_line.split_whitespace().nth(1).unwrap_or("").to_string();
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
                let body: Value = serde_json::from_slice(&raw).unwrap();
                let (status, marker, answer) = if path.ends_with("/peek") {
                    let keys: Vec<String> = body["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap().to_string()).collect();
                    log.lock().unwrap().push(Seen { path: path.clone(), cache_control, authorization, name: String::new(), keys: keys.len() });
                    let remembered = memory.lock().unwrap();
                    let found: serde_json::Map<String, Value> =
                        keys.iter().filter_map(|k| remembered.get(k).map(|a| (k.clone(), a.clone()))).collect();
                    ("200 OK", "peek", serde_json::json!({ "answers": found }))
                } else {
                    let name = body["state"]["definition"]["name"].as_str().unwrap_or("").to_string();
                    log.lock().unwrap().push(Seen { path: path.clone(), cache_control: cache_control.clone(), authorization, name, keys: 0 });
                    let key = proxy_key(&body);
                    let remembered = memory.lock().unwrap().get(&key).cloned();
                    match (cache_control.as_str(), remembered) {
                        ("no-cache", _) | (_, None) => {
                            let answer = answer_for(&body, cost);
                            memory.lock().unwrap().insert(key, answer.clone());
                            ("200 OK", "miss", answer)
                        }
                        (_, Some(answer)) => ("200 OK", "hit", answer),
                    }
                };
                let text = answer.to_string();
                let _ = write!(
                    stream,
                    "HTTP/1.1 {}\r\ncontent-type: application/json\r\nx-jev-proxy: {}\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                    status,
                    marker,
                    text.len(),
                    text
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
    let (value, elapsed, _) = run_with_code(root, extra, stdin, env);
    (value, elapsed)
}

/// run と同じで、終了コードも返す。
fn run_with_code(root: &Path, extra: &[&str], stdin: Option<&str>, env: &[(&str, &str)]) -> (Value, Duration, i32) {
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
    (value, elapsed, output.status.code().unwrap_or(-1))
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
    // 手元の cache の空の worktree(B)の全体の実行は、代理に鍵の束で「覚えている時だけ」問うて答えを得る。束は 1 つ(定義 4 つの鍵)で、
    // 本物の Jev への問い(/v1/systemone)は 0。
    let second = repo(FILES, &proxy.url, token.path());
    let (plain, _) = run(second.path(), &[], None, &[]);
    let seen = proxy.take();
    assert_eq!(seen.len(), 1, "{:?}", seen);
    assert_eq!((seen[0].path.as_str(), seen[0].keys), ("/v1/systemone/peek", 4));
    assert_eq!(seen[0].authorization, format!("Bearer {}", PROXY_TOKEN));
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
    // 届かない proxy の束は答えの有無を測れていない — 未判定ではなく「測れなかった」(agora-redesign #1885)。止まらずに終わる。
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", closed.local_addr().unwrap());
    drop(closed);
    let token = token_file();
    let dir = repo(FILES, &url, token.path());
    let (report, elapsed, code) = run_with_code(dir.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unmeasured"], 4, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["unjudged"], 0, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["peeked"], 0);
    assert_eq!(code, 3, "測れなかった定義が在る実行は緑と分ける(終了コード 3)");
    assert!(elapsed < Duration::from_secs(5), "届かない代理で止まらない: {:?}", elapsed);
}

/// 束を受けてから `delay` 待って答える proxy(答えは空 — 覚えていない)。待ちの内に返らない束の検のため。
fn slow_proxy(delay: Duration) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
            std::thread::spawn(move || {
                let mut reader = BufReader::new(stream.try_clone().unwrap());
                let mut length = 0usize;
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap_or(0) == 0 || line == "\r\n" {
                        break;
                    }
                    if let Some(value) = line.to_ascii_lowercase().strip_prefix("content-length:") {
                        length = value.trim().parse().unwrap_or(0);
                    }
                }
                let mut raw = vec![0u8; length];
                let _ = reader.read_exact(&mut raw);
                std::thread::sleep(delay);
                let text = r#"{"answers":{}}"#;
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", text.len(), text);
            });
        }
    });
    url
}

#[test]
fn a_peek_batch_that_does_not_return_in_time_is_unmeasured_not_unjudged() {
    // agora-redesign #1885: proxy が束に待ち(proxy_peek_timeout_ms)の内に答えないと、linter は束を捨てる。以前はその定義を黙って
    // 未判定に数え、判定済みの repo が「未判定 7697」と出た。答えの有無を測れていないので「測れなかった」に数え、未判定に入れない。
    // 反例: 同じ proxy が待ちの内に答えれば(覚えていない = 答えが無い)、それは未判定。
    let slow = slow_proxy(Duration::from_millis(2000));
    let token = token_file();
    let dir = repo(FILES, &slow, token.path());
    let config = dir.path().join("pyproject.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    std::fs::write(&config, format!("{}proxy_peek_timeout_ms = 300\n", text)).unwrap();
    let (report, elapsed, code) = run_with_code(dir.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unmeasured"], 4, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["unjudged"], 0, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["judged"], 0, "{}", report["semantic"]);
    assert_eq!(code, 3);
    assert!(elapsed < Duration::from_millis(1900), "待ちで束を切る: {:?}", elapsed);
    let answered = repo(FILES, &slow_proxy(Duration::from_millis(0)), token.path());
    let (report, _, code) = run_with_code(answered.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unjudged"], 4, "待ちの内に答えた束の覚えていない定義は未判定: {}", report["semantic"]);
    assert_eq!(report["semantic"]["unmeasured"], 0, "{}", report["semantic"]);
    assert_eq!(code, 0);
}

#[test]
fn stdin_plain_run_does_not_ask_the_proxy() {
    // 1 file の --stdin(書いた直後の hook・エディタ)の既定は cache だけ(agora-redesign #1190 の決定 A — hook の 3 秒の上限)。
    let proxy = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let path = dir.path().join("app/protocol/chat.hy");
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap()], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert!(report["semantic"].is_object());
    assert!(proxy.take().is_empty(), "編集中の決定的な実行は代理にも問わない");
}

#[test]
fn stdin_changed_run_asks_only_uncached_definitions() {
    // --semantic-changed を名指した 1 file の実行は、cache に答えの無い定義だけを問い、答えを cache に書く(2 度目は問わない)。
    let proxy = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let path = dir.path().join("app/protocol/chat.hy");
    let source = "(defk may-post? [who] (in who permission))\n";
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic-changed"], Some(source), &[]);
    let asked: Vec<String> = proxy
        .take()
        .into_iter()
        .filter(|s| !["may-manage?", "classifier-call", "post-json", "wake"].contains(&s.name.as_str()))
        .map(|s| s.name)
        .collect();
    assert_eq!(asked, vec!["may-post?".to_string()], "{}", report["semantic"]);
    let (_, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic-changed"], Some(source), &[]);
    assert!(proxy.take().is_empty(), "答えを cache に書いたので 2 度目は問わない");
}

#[test]
fn unreachable_jev_is_unmeasured_and_not_green() {
    // agora-redesign #1160 の決定 2: 問うはずの定義が Jev に届かず答えを得られなければ「測れなかった」— 破れが無くても終了コード 3。
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", closed.local_addr().unwrap());
    drop(closed);
    let token = token_file();
    let dir = repo(FILES, &url, token.path());
    let path = dir.path().join("app/protocol/chat.hy");
    let (report, _, code) = run_with_code(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic-changed"], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert!(report["semantic"]["unmeasured"].as_u64().unwrap() >= 1, "{}", report["semantic"]);
    assert_eq!(code, 3, "測れなかった時は緑(0)と分ける");
    let (quiet, _, code) = run_with_code(dir.path(), &["--stdin", "--path", path.to_str().unwrap()], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert_eq!(quiet["semantic"]["unmeasured"], 0, "問わない実行は測れなかったに数えない");
    assert_eq!(code, 0);
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

#[test]
fn whole_run_peeks_thousands_of_definitions_in_a_few_batches() {
    // 定義が数千ある repo でも、覚えている時だけの問いは PEEK_BATCH 個ずつの束で撃つ(定義 1 つずつ撃たない)。
    let proxy = fake_proxy();
    let token = token_file();
    let many: String = (0..(PEEK_BATCH * 2 + 500)).map(|i| format!("(defk step-{} [x] (+ x {}))\n", i, i)).collect();
    let files: Vec<(&str, &str)> = vec![("app/protocol/many.hy", many.as_str())];
    let first = repo(&files, &proxy.url, token.path());
    let _ = run(first.path(), &["--semantic-all"], None, &[]);
    proxy.take();
    let second = repo(&files, &proxy.url, token.path());
    let (plain, _) = run(second.path(), &[], None, &[]);
    let seen = proxy.take();
    assert!(seen.iter().all(|s| s.path == "/v1/systemone/peek"), "本物の Jev への問いは 0");
    let mut sizes: Vec<usize> = seen.iter().map(|s| s.keys).collect();
    sizes.sort();
    assert_eq!(sizes, vec![500, PEEK_BATCH, PEEK_BATCH], "束は 3 つ");
    assert_eq!(plain["semantic"]["peeked"], PEEK_BATCH * 2 + 500, "{}", plain["semantic"]);
    assert_eq!(plain["semantic"]["unjudged"], 0);
}

#[test]
fn proxy_key_matches_the_proxy_key_contract_sample() {
    // 代理(doeff の packages/doeff-jev-proxy)の鍵の決まりの見本を、代理の検(test_key_contract_sample_is_the_proxy_key)と同じ file で読む。
    // 片方の決まりだけを変えると、どちらかが赤になる。
    let cases: Vec<Value> = serde_json::from_str(include_str!("../../doeff-jev-proxy/tests/key_contract.json")).unwrap();
    assert!(cases.len() >= 4);
    for case in cases {
        assert_eq!(proxy_key(&case["body"]), case["key"].as_str().unwrap(), "{}", case["body"]);
    }
}

/// 手元の cache の答え 1 つを読む(どれでもよい — 同じ偽の上流の答えなので費用の欄は同じ)。
fn one_cached_answer(root: &Path) -> Value {
    let cache = root.join(".doeff-linter").join("semantic-cache");
    let first = std::fs::read_dir(&cache).unwrap().next().expect("cache に答えが在る").unwrap().path();
    serde_json::from_str(&std::fs::read_to_string(first).unwrap()).unwrap()
}

#[test]
fn the_cache_records_the_upstream_cost_and_a_remembered_answer_is_not_charged_again() {
    // agora-redesign #1892: 上流が答えに費用を載せれば、linter は今回の費用に数え、手元の cache の答えにも費用が載る。
    let proxy = fake_proxy_with_cost(Some("0.00004"));
    let token = token_file();
    let first = repo(FILES, &proxy.url, token.path());
    let (asked, _) = run(first.path(), &["--semantic-all"], None, &[]);
    let summary = &asked["semantic"];
    let calls = summary["asked"].as_u64().unwrap();
    assert!(calls >= 4, "{}", summary);
    assert!((summary["cost_usd"].as_f64().unwrap() - 0.00004 * calls as f64).abs() < 1e-12, "{}", summary);
    assert_eq!((summary["cost_unreported"].as_u64(), summary["remembered"].as_u64()), (Some(0), Some(0)), "{}", summary);
    assert_eq!((summary["input_tokens"].as_u64(), summary["output_tokens"].as_u64()), (Some(100 * calls), Some(2 * calls)), "{}", summary);
    assert_eq!(one_cached_answer(first.path())["reported_cost_usd"], 0.00004);
    // 別の worktree が同じ定義を問うと、proxy の覚え(x-jev-proxy: hit)で答える — 上流を呼んでいないので、その回の費用と token は
    // 数えない(較正は no-cache で上流を呼ぶので数える)。
    proxy.take();
    let second = repo(FILES, &proxy.url, token.path());
    let (again, _) = run(second.path(), &["--semantic-all"], None, &[]);
    let hits = proxy.take().iter().filter(|s| s.path == "/v1/systemone" && s.cache_control.is_empty()).count() as u64;
    let summary = &again["semantic"];
    assert_eq!(summary["remembered"].as_u64(), Some(hits), "{}", summary);
    assert!(hits >= 4, "{}", summary);
    let charged = summary["asked"].as_u64().unwrap() - hits;
    assert!((summary["cost_usd"].as_f64().unwrap() - 0.00004 * charged as f64).abs() < 1e-12, "{}", summary);
    assert_eq!(summary["input_tokens"].as_u64(), Some(100 * charged), "{}", summary);
}

#[test]
fn an_upstream_without_a_cost_is_counted_as_unreported_not_as_zero() {
    // agora-redesign #1892: TypeSafe 直のように上流が費用を載せない答えは、費用 0 ではなく「不明」に数え、cache にも 0 を書かない。
    // token 数(usage)は数える。
    let proxy = fake_proxy();
    let token = token_file();
    let dir = repo(FILES, &proxy.url, token.path());
    let (asked, _) = run(dir.path(), &["--semantic-all"], None, &[]);
    let summary = &asked["semantic"];
    let calls = summary["asked"].as_u64().unwrap();
    assert_eq!(summary["cost_unreported"].as_u64(), Some(calls), "{}", summary);
    assert_eq!(summary["cost_usd"].as_f64(), Some(0.0), "{}", summary);
    assert_eq!(summary["input_tokens"].as_u64(), Some(100 * calls), "{}", summary);
    assert_eq!(one_cached_answer(dir.path())["reported_cost_usd"], Value::Null);
}
