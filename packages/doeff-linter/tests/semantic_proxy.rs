//! Tests for the proxy that remembers semantic rules and Jev calls. The proxy is a local fake HTTP server (127.0.0.1), and the memory is looked up by the body's proxy key
//! (proxy_key). Counterexamples: a whole run asks the proxy "only when it remembers" with a bundle of keys (does not fire per definition, does not send the body), does not
//! call the real Jev, does not stall when the proxy is unreachable, does not ask the proxy for the single file being edited (--stdin),
//! --semantic-changed asks only definitions whose content changed and does not ask definitions that are unreadable because they are half-written, calibration does not use the memory,
//! the Authorization header is not sent to the proxy (nor the TypeSafe key), and env's JEV_BASE_URL wins over the repo's proxy.

use doeff_linter::project::semantic::{proxy_key, PEEK_BATCH};
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// One query that reached the fake proxy (path, Cache-Control header, Authorization, x-jev-caller, the definition's name, the number of keys in the bundle).
#[derive(Debug, Clone)]
struct Seen {
    path: String,
    cache_control: String,
    authorization: String,
    caller: String,
    name: String,
    keys: usize,
}

/// The fake proxy: remembers answers keyed by the body's proxy key (the linter's proxy_key — that it equals the proxy's key is verified by the key_contract test),
/// and handles /v1/systemone no-cache and /v1/systemone/peek (a bundle of "only when remembered" queries) the same as the proxy.
struct FakeProxy {
    url: String,
    seen: Arc<Mutex<Vec<Seen>>>,
}

/// An answer in place of the real Jev (the calibration examples fall within the band; the probability is decided by words in the source). If cost is present, an answer where upstream attaches the cost (Vercel's AI
/// Gateway shape providerMetadata.gateway.cost); otherwise, as with TypeSafe directly, an answer with usage only.
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

/// A fake proxy whose upstream attaches no cost (same as TypeSafe directly).
fn fake_proxy() -> FakeProxy {
    fake_proxy_with_cost(None)
}

/// A fake proxy whose upstream attaches a cost `cost` to the answer (None if it attaches none).
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
                let (mut length, mut cache_control, mut authorization, mut caller) = (0usize, String::new(), String::new(), String::new());
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
                    } else if lower.starts_with("x-jev-caller:") {
                        caller = line["x-jev-caller:".len()..].trim().to_string();
                    }
                }
                let mut raw = vec![0u8; length];
                reader.read_exact(&mut raw).unwrap();
                let body: Value = serde_json::from_slice(&raw).unwrap();
                let (status, marker, answer) = if path.ends_with("/peek") {
                    let keys: Vec<String> = body["keys"].as_array().unwrap().iter().map(|k| k.as_str().unwrap().to_string()).collect();
                    log.lock().unwrap().push(Seen { path: path.clone(), cache_control, authorization, caller, name: String::new(), keys: keys.len() });
                    let remembered = memory.lock().unwrap();
                    let found: serde_json::Map<String, Value> =
                        keys.iter().filter_map(|k| remembered.get(k).map(|a| (k.clone(), a.clone()))).collect();
                    ("200 OK", "peek", serde_json::json!({ "answers": found }))
                } else {
                    let name = body["state"]["definition"]["name"].as_str().unwrap_or("").to_string();
                    log.lock().unwrap().push(Seen { path: path.clone(), cache_control: cache_control.clone(), authorization, caller, name, keys: 0 });
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
    /// Read the queries that arrived and empty the queue.
    fn take(&self) -> Vec<Seen> {
        std::mem::take(&mut *self.seen.lock().unwrap())
    }
}

const TYPESAFE_KEY: &str = "apikey_typesafe_must_not_leave";

/// A repo with two layers, a semantic rule, and a proxy setting.
fn repo(files: &[(&str, &str)], proxy_url: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let config = format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF201", "DOEFF202"]
[tool.doeff-linter.layers]
order = ["core", "protocol"]
paths = {{ core = "app/core", protocol = "app/protocol" }}
[tool.doeff-linter.layers.describe.protocol]
summary = "translation handler"
[tool.doeff-linter.semantic]
business_decision = {{ layers = ["protocol"] }}
transport_knowledge = {{ layers = ["core"] }}
proxy_url = "{proxy_url}"
"#
    );
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Run with editor-json (HOME is a temporary dir, and the TypeSafe key is put in env — to check that it is not sent to the proxy).
fn run(root: &Path, extra: &[&str], stdin: Option<&str>, env: &[(&str, &str)]) -> (Value, Duration) {
    let (value, elapsed, _) = run_with_code(root, extra, stdin, env);
    (value, elapsed)
}

/// Same as run, and also returns the exit code.
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

fn violation<'a>(report: &'a Value, rule: &str, name: &str) -> Option<&'a Value> {
    report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule && v["key"].as_str().unwrap().ends_with(name))
}

#[test]
fn whole_run_takes_remembered_answers_from_the_proxy_without_asking_jev() {
    let proxy = fake_proxy();
    // Another worktree (A) asked and the proxy remembered — calibration does not use the memory (no-cache).
    let first = repo(FILES, &proxy.url);
    let (asked, _) = run(first.path(), &["--semantic-all"], None, &[]);
    assert_eq!(asked["semantic"]["wire"], "direct(repo)", "{}", asked["semantic"]);
    let seen = proxy.take();
    let calibration: Vec<&Seen> = seen.iter().filter(|s| ["may-manage?", "classifier-call", "post-json", "wake"].contains(&s.name.as_str())).collect();
    assert!(!calibration.is_empty());
    assert!(calibration.iter().all(|s| s.cache_control == "no-cache"), "calibration does not use the memory: {:?}", calibration);
    assert!(seen.iter().filter(|s| !calibration.iter().any(|c| c.name == s.name)).all(|s| s.cache_control.is_empty()));
    assert!(seen.iter().all(|s| s.authorization.is_empty()), "the Authorization header is not sent to the proxy (nor the TypeSafe key): {:?}", seen);
    // A whole run in worktree (B), whose local cache is empty, asks the proxy "only when it remembers" with a bundle of keys and gets the answers. There is one bundle (the keys of four definitions),
    // and queries to the real Jev (/v1/systemone) are 0.
    let second = repo(FILES, &proxy.url);
    let (plain, _) = run(second.path(), &[], None, &[]);
    let seen = proxy.take();
    assert_eq!(seen.len(), 1, "{:?}", seen);
    assert_eq!((seen[0].path.as_str(), seen[0].keys), ("/v1/systemone/peek", 4));
    assert_eq!(seen[0].authorization, "", "no Authorization is attached even to the query that asks only when remembered");
    assert_eq!(plain["semantic"]["asked"], 0);
    assert_eq!(plain["semantic"]["peeked"], 4, "{}", plain["semantic"]);
    assert_eq!(plain["semantic"]["unjudged"], 0);
    let decision = violation(&plain, "DOEFF201", "may_post?").or_else(|| violation(&plain, "DOEFF201", "hyx_may_postXquestion_markX"));
    assert_eq!(decision.expect("a violation appears from the proxy's remembered answer")["probability"], 0.93);
    assert!(violation(&plain, "DOEFF202", "route").is_some());
    // The received answers were written to the local cache, so the next whole run does not ask the proxy.
    let (again, _) = run(second.path(), &[], None, &[]);
    assert!(proxy.take().is_empty());
    assert_eq!(again["semantic"]["peeked"], 0);
    assert_eq!(again["semantic"]["judged"], 4);
}

#[test]
fn whole_run_without_a_reachable_proxy_stays_on_the_local_cache() {
    // The bundle to an unreachable proxy has not measured whether answers exist — it is "could not be measured", not unjudged. It finishes without stalling.
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", closed.local_addr().unwrap());
    drop(closed);
    let dir = repo(FILES, &url);
    let (report, elapsed, code) = run_with_code(dir.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unmeasured"], 4, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["unjudged"], 0, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["peeked"], 0);
    assert_eq!(code, 3, "a run with definitions that could not be measured is distinguished from green (exit code 3)");
    assert!(elapsed < Duration::from_secs(5), "does not stall on an unreachable proxy: {:?}", elapsed);
}

/// A proxy that waits `delay` after receiving a bundle and then answers (the answer is empty — nothing remembered). For testing bundles that do not return within the wait.
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
    // If the proxy does not answer a bundle within the wait (proxy_peek_timeout_ms), the linter discards the bundle. Formerly it silently counted those definitions
    // as unjudged, and a repo that had been judged showed "unjudged 7697". Since whether answers exist has not been measured, count them as "could not be measured" and not as unjudged.
    // Counterexample: if the same proxy answers within the wait (nothing remembered = no answer), that is unjudged.
    let slow = slow_proxy(Duration::from_millis(2000));
    let dir = repo(FILES, &slow);
    let config = dir.path().join("pyproject.toml");
    let text = std::fs::read_to_string(&config).unwrap();
    std::fs::write(&config, format!("{}proxy_peek_timeout_ms = 300\n", text)).unwrap();
    let (report, elapsed, code) = run_with_code(dir.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unmeasured"], 4, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["unjudged"], 0, "{}", report["semantic"]);
    assert_eq!(report["semantic"]["judged"], 0, "{}", report["semantic"]);
    assert_eq!(code, 3);
    assert!(elapsed < Duration::from_millis(1900), "the bundle is cut off at the wait: {:?}", elapsed);
    let answered = repo(FILES, &slow_proxy(Duration::from_millis(0)));
    let (report, _, code) = run_with_code(answered.path(), &[], None, &[]);
    assert_eq!(report["semantic"]["unjudged"], 4, "definitions in a bundle answered within the wait with nothing remembered are unjudged: {}", report["semantic"]);
    assert_eq!(report["semantic"]["unmeasured"], 0, "{}", report["semantic"]);
    assert_eq!(code, 0);
}

#[test]
fn stdin_plain_run_does_not_ask_the_proxy() {
    // The default for a single-file --stdin (hook and editor right after writing) is cache only (the hook's 3-second limit).
    let proxy = fake_proxy();
    let dir = repo(FILES, &proxy.url);
    let path = dir.path().join("app/protocol/chat.hy");
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap()], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert!(report["semantic"].is_object());
    assert!(proxy.take().is_empty(), "a deterministic run during editing does not ask the proxy either");
}

#[test]
fn stdin_changed_run_asks_only_uncached_definitions() {
    // A single-file run that names --semantic-changed asks only definitions that have no answer in the cache and writes the answers to the cache (it does not ask the second time).
    let proxy = fake_proxy();
    let dir = repo(FILES, &proxy.url);
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
    assert!(proxy.take().is_empty(), "the answers were written to the cache, so it does not ask the second time");
}

#[test]
fn unreachable_jev_is_unmeasured_and_not_green() {
    // Rule: if a definition that should have been asked does not reach Jev and gets no answer, it is "could not be measured" — exit code 3 even with no violation.
    let closed = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", closed.local_addr().unwrap());
    drop(closed);
    let dir = repo(FILES, &url);
    let path = dir.path().join("app/protocol/chat.hy");
    let (report, _, code) = run_with_code(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic-changed"], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert!(report["semantic"]["unmeasured"].as_u64().unwrap() >= 1, "{}", report["semantic"]);
    assert_eq!(code, 3, "when it could not be measured, it is distinguished from green (0)");
    let (quiet, _, code) = run_with_code(dir.path(), &["--stdin", "--path", path.to_str().unwrap()], Some("(defk may-post? [who] (in who permission))\n"), &[]);
    assert_eq!(quiet["semantic"]["unmeasured"], 0, "a run that asks nothing does not count as could not be measured");
    assert_eq!(code, 0);
}

#[test]
fn semantic_changed_asks_only_changed_and_readable_definitions() {
    let proxy = fake_proxy();
    let dir = repo(FILES, &proxy.url);
    let _ = run(dir.path(), &["--semantic-all"], None, &[]);
    proxy.take();
    let path = dir.path().join("app/protocol/chat.hy");
    // may-post? is unchanged, read-body changes its content, and half, which is half-written, is added.
    let edited = "(defk may-post? [who] (in who permission))\n(defk read-body [text] (json.loads (.strip text)))\n(defk half [x] (+ x\n";
    let (report, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic", "--semantic-changed"], Some(edited), &[]);
    let asked: Vec<String> = proxy
        .take()
        .into_iter()
        .filter(|s| !["may-manage?", "classifier-call", "post-json", "wake"].contains(&s.name.as_str()))
        .map(|s| s.name)
        .collect();
    assert_eq!(asked, vec!["read-body".to_string()], "only the changed definition is asked, and the half-written one (half) is not asked");
    assert_eq!(report["semantic"]["unjudged"], 1, "the half-written one is unjudged: {}", report["semantic"]);
    // If nothing changed, nothing is asked (calibration is not fired either).
    let (_, _) = run(dir.path(), &["--stdin", "--path", path.to_str().unwrap(), "--semantic", "--semantic-changed"], Some(edited), &[]);
    assert!(proxy.take().is_empty());
}

#[test]
fn env_base_url_wins_over_the_repo_proxy() {
    let proxy = fake_proxy();
    let other = fake_proxy();
    let dir = repo(FILES, &proxy.url);
    let (report, _) = run(dir.path(), &["--semantic-all"], None, &[("JEV_BASE_URL", &other.url), ("JEV_API_KEY", "env-key")]);
    assert_eq!(report["semantic"]["wire"], "direct(env)");
    assert!(proxy.take().is_empty(), "if env's destination exists, the repo's proxy is not used");
    let seen = other.take();
    assert!(!seen.is_empty() && seen.iter().all(|s| s.authorization == "Bearer env-key" && s.cache_control.is_empty()), "{:?}", seen);
}

#[test]
fn whole_run_peeks_thousands_of_definitions_in_a_few_batches() {
    // Even in a repo with thousands of definitions, "only when remembered" queries are fired in bundles of PEEK_BATCH (not per definition).
    let proxy = fake_proxy();
    let many: String = (0..(PEEK_BATCH * 2 + 500)).map(|i| format!("(defk step-{} [x] (+ x {}))\n", i, i)).collect();
    let files: Vec<(&str, &str)> = vec![("app/protocol/many.hy", many.as_str())];
    let first = repo(&files, &proxy.url);
    let _ = run(first.path(), &["--semantic-all"], None, &[]);
    proxy.take();
    let second = repo(&files, &proxy.url);
    let (plain, _) = run(second.path(), &[], None, &[]);
    let seen = proxy.take();
    assert!(seen.iter().all(|s| s.path == "/v1/systemone/peek"), "queries to the real Jev are 0");
    let mut sizes: Vec<usize> = seen.iter().map(|s| s.keys).collect();
    sizes.sort();
    assert_eq!(sizes, vec![500, PEEK_BATCH, PEEK_BATCH], "there are three bundles");
    assert_eq!(plain["semantic"]["peeked"], PEEK_BATCH * 2 + 500, "{}", plain["semantic"]);
    assert_eq!(plain["semantic"]["unjudged"], 0);
}

#[test]
fn proxy_key_matches_the_proxy_key_contract_sample() {
    // Read the copy of the key-rule sample of the proxy (repo proboscis/jev-proxy — moved from doeff on 2026-10-01).
    // The source of truth is tests/key_contract.json in the proxy's repo (read by the proxy's test test_key_contract_sample_is_the_proxy_key), and this copy
    // does not differ by even 1 byte from when it was moved (sha256 8b4acc8b…). When changing the key rule, fix the samples and implementations in both repos in one coordinated change
    // (changing the rule on only one side turns that side's test red).
    let cases: Vec<Value> = serde_json::from_str(include_str!("proxy_key_contract.json")).unwrap();
    assert!(cases.len() >= 4);
    for case in cases {
        assert_eq!(proxy_key(&case["body"]), case["key"].as_str().unwrap(), "{}", case["body"]);
    }
}

/// Read one answer in the local cache (any will do — they are answers of the same fake upstream, so the cost field is the same).
fn one_cached_answer(root: &Path) -> Value {
    let cache = root.join(".doeff-linter").join("semantic-cache");
    let first = std::fs::read_dir(&cache).unwrap().next().expect("an answer exists in the cache").unwrap().path();
    serde_json::from_str(&std::fs::read_to_string(first).unwrap()).unwrap()
}

#[test]
fn the_cache_records_the_upstream_cost_and_a_remembered_answer_is_not_charged_again() {
    // If upstream attaches a cost to the answer, the linter counts it in this run's cost, and the cost is also attached to the answer in the local cache.
    let proxy = fake_proxy_with_cost(Some("0.00004"));
    let first = repo(FILES, &proxy.url);
    let (asked, _) = run(first.path(), &["--semantic-all"], None, &[]);
    let summary = &asked["semantic"];
    let calls = summary["asked"].as_u64().unwrap();
    assert!(calls >= 4, "{}", summary);
    assert!((summary["cost_usd"].as_f64().unwrap() - 0.00004 * calls as f64).abs() < 1e-12, "{}", summary);
    assert_eq!((summary["cost_unreported"].as_u64(), summary["remembered"].as_u64()), (Some(0), Some(0)), "{}", summary);
    assert_eq!((summary["input_tokens"].as_u64(), summary["output_tokens"].as_u64()), (Some(100 * calls), Some(2 * calls)), "{}", summary);
    assert_eq!(one_cached_answer(first.path())["reported_cost_usd"], 0.00004);
    // When another worktree asks the same definition, the proxy answers from its memory (x-jev-proxy: hit) — upstream is not called, so that run's cost and tokens are
    // not counted (calibration calls upstream with no-cache, so it is counted).
    proxy.take();
    let second = repo(FILES, &proxy.url);
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
    // An answer where upstream attaches no cost, as with TypeSafe directly, is counted as "unknown" rather than cost 0, and 0 is not written to the cache either.
    // The token count (usage) is counted.
    let proxy = fake_proxy();
    let dir = repo(FILES, &proxy.url);
    let (asked, _) = run(dir.path(), &["--semantic-all"], None, &[]);
    let summary = &asked["semantic"];
    let calls = summary["asked"].as_u64().unwrap();
    assert_eq!(summary["cost_unreported"].as_u64(), Some(calls), "{}", summary);
    assert_eq!(summary["cost_usd"].as_f64(), Some(0.0), "{}", summary);
    assert_eq!(summary["input_tokens"].as_u64(), Some(100 * calls), "{}", summary);
    assert_eq!(one_cached_answer(dir.path())["reported_cost_usd"], Value::Null);
}

#[test]
fn each_question_names_its_caller_and_rule_in_the_caller_header() {
    // So that the proxy can count queries separately by caller and rule, each query declares `doeff-linter/<rule>` in the header x-jev-caller. Calibration
    // is distinguished from rule queries by appending `/calibration`, and the bundle of "only when remembered" queries is `doeff-linter/peek`.
    let proxy = fake_proxy();
    let first = repo(FILES, &proxy.url);
    let _ = run(first.path(), &["--semantic-all"], None, &[]);
    let seen = proxy.take();
    let calibration = ["may-manage?", "classifier-call", "post-json", "wake"];
    let caller = |name: &str| seen.iter().find(|s| s.name == name).map(|s| s.caller.as_str()).unwrap_or("");
    assert_eq!(caller("may-post?"), "doeff-linter/DOEFF201", "{:?}", seen);
    assert_eq!(caller("read-body"), "doeff-linter/DOEFF201", "{:?}", seen);
    assert_eq!(caller("route"), "doeff-linter/DOEFF202", "{:?}", seen);
    assert_eq!(caller("decide"), "doeff-linter/DOEFF202", "{:?}", seen);
    let calibrated: Vec<&Seen> = seen.iter().filter(|s| calibration.contains(&s.name.as_str())).collect();
    assert!(!calibrated.is_empty());
    assert!(
        calibrated.iter().all(|s| s.caller.starts_with("doeff-linter/DOEFF20") && s.caller.ends_with("/calibration")),
        "{:?}",
        calibrated
    );
    let second = repo(FILES, &proxy.url);
    let _ = run(second.path(), &[], None, &[]);
    let peeked = proxy.take();
    assert_eq!(peeked.iter().map(|s| (s.path.as_str(), s.caller.as_str())).collect::<Vec<_>>(), vec![("/v1/systemone/peek", "doeff-linter/peek")]);
}
