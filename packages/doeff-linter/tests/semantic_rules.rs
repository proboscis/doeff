//! 意味の規則(DOEFF201・202 — Jev)の検。Jev の口は手元の偽の HTTP(127.0.0.1)で、本物の宛先は叩かない。

use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// 偽の Jev(TypeSafe の direct の形)。本文の source に含む語で確率を決める。撃たれた数を数える。
struct FakeJev {
    url: String,
    hits: Arc<AtomicUsize>,
}

/// 偽の Jev を立てる。calibration_p は較正の例(may-manage? と classifier-call の正例・post-json と wake の反例)に返す確率の上書き。
fn fake_jev(drifted: bool) -> FakeJev {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    let hits = Arc::new(AtomicUsize::new(0));
    let counter = hits.clone();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut stream) = stream else { continue };
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
            let mut body = vec![0u8; length];
            reader.read_exact(&mut body).unwrap();
            counter.fetch_add(1, Ordering::SeqCst);
            let body: Value = serde_json::from_slice(&body).unwrap();
            let source = body["state"]["definition"]["source"].as_str().unwrap_or("");
            if body["questions"]["q"]["type"] == "choice" {
                // DOEFF203: sorted の key を渡す定義は library-callback、handler の並びを組む定義は none を選ぶ。
                let (choice, probabilities) = if source.contains("sorted") {
                    ("library-callback", serde_json::json!({"library-callback": 0.9, "process-entry": 0.02, "none": 0.08}))
                } else {
                    ("none", serde_json::json!({"library-callback": 0.1, "process-entry": 0.05, "none": 0.85}))
                };
                counter.fetch_add(1, Ordering::SeqCst);
                let answer = serde_json::json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 50}, "model": "jev-test-1"}).to_string();
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
                continue;
            }
            let name = body["state"]["definition"]["name"].as_str().unwrap_or("");
            assert_eq!(body["model"], "jev-test", "direct の形は model を本文に入れる");
            assert_eq!(body["questions"]["q"]["type"], "noul");
            let calibration_positive = ["may-manage?", "classifier-call"].contains(&name);
            let calibration_negative = ["post-json", "wake"].contains(&name);
            let p = if drifted && (calibration_positive || calibration_negative) {
                0.5
            } else if calibration_positive || source.contains("permission") || source.contains("http://") {
                0.93
            } else if source.contains("maybe") {
                0.65
            } else {
                0.05
            };
            let answer = serde_json::json!({"answers": {"q": {"noul": p}}, "usage": {"input_tokens": 100}, "model": "jev-test-1"}).to_string();
            let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
        }
    });
    FakeJev { url, hits }
}

/// 設定(層 2 つ・意味の規則)と file の repo。
fn repo(files: &[(&str, &str)], semantic: &str) -> tempfile::TempDir {
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
{semantic}
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

const SEMANTIC: &str = "business_decision = { layers = [\"protocol\"] }\ntransport_knowledge = { layers = [\"core\"] }\n";

/// editor-json で走らせる(Jev の宛先は偽物・HOME は一時の dir — 本物の ~/.config/jev を読まない)。
fn run(root: &Path, jev: &str, extra: &[&str]) -> (i32, Value, String) {
    let home = tempfile::TempDir::new().unwrap();
    let mut args = vec!["--output-format", "editor-json", "--no-log"];
    args.extend_from_slice(extra);
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(&args)
        .current_dir(root)
        .env_clear()
        .env("PATH", std::env::var("PATH").unwrap_or_default())
        .env("HOME", home.path())
        .env("JEV_BASE_URL", jev)
        .env("JEV_MODEL", "jev-test")
        .stdin(Stdio::null())
        .output()
        .unwrap();
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    let stderr = String::from_utf8_lossy(&output.stderr).into_owned();
    let value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    (output.status.code().unwrap_or(-1), value, stderr)
}

/// 規則 rule の違反を名で引く。
fn find<'a>(report: &'a Value, rule: &str, name: &str) -> Option<&'a Value> {
    report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule && v["key"].as_str().unwrap().ends_with(name))
}

const FILES: &[(&str, &str)] = &[
    ("app/protocol/chat.hy", "(defk may-post? [who] {:tags {:context \"chat\" :role \"protocol\"}} (in who permission))\n(defk read-body [text] (json.loads text))\n(defk borderline [x] (maybe x))\n"),
    ("app/core/plan.hy", "(defk route [cid] (+ \"http://records/\" cid))\n(defk decide [x] x)\n"),
];

#[test]
fn semantic_all_asks_jev_then_plain_runs_read_the_cache() {
    let dir = repo(FILES, SEMANTIC);
    let jev = fake_jev(false);
    let (code, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    assert_eq!(code, 0, "Jev の違反は error にしない: {}", stderr);
    // 5 定義 + 較正の 4 例。
    assert_eq!(jev.hits.load(Ordering::SeqCst), 9);
    let semantic = &report["semantic"];
    assert_eq!(semantic["model"], "jev-test");
    assert_eq!(semantic["judged"], 5);
    assert_eq!(semantic["unjudged"], 0);
    assert_eq!(semantic["asked"], 9);
    assert_eq!(semantic["input_tokens"], 900);
    assert_eq!(semantic["calibration"], "ok");
    let decision = find(&report, "DOEFF201", "may_post?").or_else(|| find(&report, "DOEFF201", "hyx_may_postXquestion_markX")).expect("DOEFF201");
    assert_eq!(decision["severity"], "warning");
    assert_eq!(decision["source"], "jev");
    assert_eq!(decision["probability"], 0.93);
    assert!(decision["explanation"]["reason"].as_str().unwrap().starts_with("Jev の判定 p=0.93 — 要求の言い換えを越えて、業務の判断"), "{}", decision["explanation"]["reason"]);
    // 閾値の間(info ≤ p < warning)は info、低い物は出ない。
    assert_eq!(find(&report, "DOEFF201", "borderline").unwrap()["severity"], "info");
    assert!(find(&report, "DOEFF201", "read_body").is_none());
    assert_eq!(find(&report, "DOEFF202", "route").unwrap()["severity"], "warning");
    assert!(find(&report, "DOEFF202", "decide").is_none());
    // 決定的な規則の実行は Jev を呼ばず cache を読む。新しい定義は未判定に数える(合格に倒さない)。
    let before = jev.hits.load(Ordering::SeqCst);
    std::fs::write(dir.path().join("app/core/new.hy"), "(defk fresh [x] x)\n").unwrap();
    let (_, report, _) = run(dir.path(), &jev.url, &[]);
    assert_eq!(jev.hits.load(Ordering::SeqCst), before, "cache を読むだけの実行は Jev を呼ばない");
    assert_eq!(report["semantic"]["judged"], 5);
    assert_eq!(report["semantic"]["unjudged"], 1);
    assert_eq!(report["semantic"]["asked"], 0);
    assert!(find(&report, "DOEFF202", "route").is_some());
    // --semantic に file を渡すと、その file の定義だけを撃つ(較正の 4 例を含む)。
    let (_, report, _) = run(dir.path(), &jev.url, &["--semantic", "app/core/new.hy"]);
    assert_eq!(report["semantic"]["asked"], 5);
    assert_eq!(report["semantic"]["unjudged"], 0);
}

#[test]
fn calibration_drift_discards_the_cache_and_warns() {
    let dir = repo(FILES, SEMANTIC);
    let good = fake_jev(false);
    run(dir.path(), &good.url, &["--semantic-all"]);
    assert!(dir.path().join(".doeff-linter/semantic-cache").is_dir());
    let drifted = fake_jev(true);
    let (_, report, _) = run(dir.path(), &drifted.url, &["--semantic", "app/core/plan.hy"]);
    assert_eq!(report["semantic"]["calibration"], "drifted");
    assert!(report["errors"].as_array().unwrap().iter().any(|e| e.as_str().unwrap().contains("較正")), "{}", report["errors"]);
    // 捨てた cache の分は未判定に戻る(今回撃った plan.hy の 2 定義だけが判定済み)。
    assert_eq!(report["semantic"]["judged"], 2);
    assert_eq!(report["semantic"]["unjudged"], 3);
}

#[test]
fn semantic_severity_cannot_be_error_and_thresholds_are_checked() {
    let dir = repo(FILES, "business_decision = { layers = [\"protocol\"], error = 0.9 }\n");
    let jev = fake_jev(false);
    let home = tempfile::TempDir::new().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(dir.path())
        .env("HOME", home.path())
        .env("JEV_BASE_URL", &jev.url)
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    let dir = repo(FILES, "business_decision = { layers = [\"protocol\"], warning = 0.3, info = 0.6 }\n");
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log"])
        .current_dir(dir.path())
        .env("HOME", home.path())
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&output.stderr).contains("info ≤ warning"));
}

#[test]
fn plain_callable_reason_is_checked_against_the_code() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture s :root "app" :layers [(layer core)]
  :plain-callable-reasons [(reason library-callback "外の library が素の関数として呼ぶ") (reason process-entry "process の入口")])
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF203\"]\n[tool.doeff-linter.definitions]\n[tool.doeff-linter.semantic]\nplain_callable = { info_below = 0.3 }\n",
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(
        dir.path().join("app/core/x.hy"),
        "(deff by-key [row] (sorted rows :key row))  ; defk にできない(library-callback): sorted の key\n(deff handlers [f] [f])  ; defk にできない(library-callback): handler の組を組む\n(deff legacy [f] f)  ; defk にできない: 旧い形は問わない\n",
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    // 種類を名乗った deff 2 つだけを問う(旧い形は問わない・Noul の較正の例も撃たない)。
    assert_eq!(report["semantic"]["asked"], 2, "{} {}", report["semantic"], stderr);
    let doubts: Vec<&Value> = report["violations"].as_array().unwrap().iter().filter(|v| v["rule"] == "DOEFF203").collect();
    assert_eq!(doubts.len(), 1);
    assert_eq!(doubts[0]["severity"], "info");
    assert_eq!(doubts[0]["source"], "jev");
    assert_eq!(doubts[0]["probability"], 0.1);
    assert!(doubts[0]["explanation"]["reason"].as_str().unwrap().contains("Jev が選んだのは none(p=0.85)"), "{}", doubts[0]["explanation"]["reason"]);
}
