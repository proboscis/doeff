//! Test of the semantic rules (DOEFF201, 202 — Jev). Jev's endpoint is a local fake HTTP server (127.0.0.1); the real destination is never hit.

use serde_json::Value;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

/// A fake Jev (the TypeSafe direct form). Decides probabilities by words contained in the body's source. Counts the requests it receives.
struct FakeJev {
    url: String,
    hits: Arc<AtomicUsize>,
}

/// Starts a fake Jev. calibration_p overrides the probability returned for the calibration examples (positives of may-manage? and classifier-call, negatives of post-json and wake).
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
            if body["questions"]["q"]["criteria"].get("mixed").is_some() {
                // DOEFF205: a definition with a .get on a string key is mixed, anything else is judgment-only. The calibration examples also fall in the band by this rule.
                // A definition with messageId gets the same probabilities as the production convert-project answer (judgment-only 0.97, mixed 0.02).
                let (choice, probabilities) = if source.contains("messageId") {
                    ("judgment-only", serde_json::json!({"mixed": 0.02, "shape-only": 0.0, "judgment-only": 0.97, "neither": 0.01}))
                } else if source.contains(".get") {
                    ("mixed", serde_json::json!({"mixed": 0.9, "shape-only": 0.05, "judgment-only": 0.04, "neither": 0.01}))
                } else {
                    ("judgment-only", serde_json::json!({"mixed": 0.03, "shape-only": 0.02, "judgment-only": 0.94, "neither": 0.01}))
                };
                let answer = serde_json::json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 40}, "model": "jev-test-1"}).to_string();
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
                continue;
            }
            if body["questions"]["q"]["criteria"].get("external-world").is_some() {
                // DOEFF204: a class with a client field is external-world, anything else is value. The synthetic calibration examples also fall in the band by this rule.
                let (choice, probabilities) = if source.contains("client") {
                    ("external-world", serde_json::json!({"value": 0.05, "external-world": 0.9, "stateful": 0.04, "other": 0.01}))
                } else {
                    ("value", serde_json::json!({"value": 0.95, "external-world": 0.02, "stateful": 0.02, "other": 0.01}))
                };
                let answer = serde_json::json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 40}, "model": "jev-test-1"}).to_string();
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
                continue;
            }
            if body["questions"]["q"]["type"] == "choice" {
                // DOEFF203: the key of sorted is library-callback, a definition that reads settings is config-read (a type that is not accepted),
                // a test helper picks library-callback but has a large sum of not-accepted answers, anything else is none.
                let (choice, probabilities) = if source.contains("sorted") {
                    ("library-callback", serde_json::json!({"library-callback": 0.9, "config-read": 0.02, "none": 0.08}))
                } else if source.contains("getenv") {
                    ("config-read", serde_json::json!({"library-callback": 0.1, "config-read": 0.8, "none": 0.1}))
                } else if source.contains("fixture") {
                    ("library-callback", serde_json::json!({"library-callback": 0.5, "config-read": 0.2, "none": 0.3}))
                } else {
                    ("none", serde_json::json!({"library-callback": 0.1, "config-read": 0.05, "none": 0.85}))
                };
                counter.fetch_add(1, Ordering::SeqCst);
                let answer = serde_json::json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 50}, "model": "jev-test-1"}).to_string();
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
                continue;
            }
            let name = body["state"]["definition"]["name"].as_str().unwrap_or("");
            assert_eq!(body["model"], "jev-test", "the direct form puts model in the body");
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

/// A repo with settings (2 layers, semantic rules) and files.
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
summary = "translation handler"
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

/// Runs with editor-json (Jev's destination is the fake, HOME is a temporary dir — the real ~/.config/jev is not read).
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

/// Looks up a violation of rule `rule` by name.
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
    assert_eq!(code, 0, "Jev violations are not errors: {}", stderr);
    // 5 definitions + 4 calibration examples.
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
    assert!(decision["explanation"]["reason"].as_str().unwrap().starts_with("Jev's judgment p=0.93 — makes business decisions beyond rephrasing the request"), "{}", decision["explanation"]["reason"]);
    // Between thresholds (info <= p < warning) is info; low ones do not appear.
    assert_eq!(find(&report, "DOEFF201", "borderline").unwrap()["severity"], "info");
    assert!(find(&report, "DOEFF201", "read_body").is_none());
    assert_eq!(find(&report, "DOEFF202", "route").unwrap()["severity"], "warning");
    assert!(find(&report, "DOEFF202", "decide").is_none());
    // A run of deterministic rules does not call Jev and reads the cache. A new definition counts as unjudged (it does not fall to pass).
    let before = jev.hits.load(Ordering::SeqCst);
    std::fs::write(dir.path().join("app/core/new.hy"), "(defk fresh [x] x)\n").unwrap();
    let (_, report, _) = run(dir.path(), &jev.url, &[]);
    assert_eq!(jev.hits.load(Ordering::SeqCst), before, "a run that only reads the cache does not call Jev");
    assert_eq!(report["semantic"]["judged"], 5);
    assert_eq!(report["semantic"]["unjudged"], 1);
    assert_eq!(report["semantic"]["asked"], 0);
    assert!(find(&report, "DOEFF202", "route").is_some());
    // Passing a file to --semantic fires only that file's definitions (including the 4 calibration examples).
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
    assert!(report["errors"].as_array().unwrap().iter().any(|e| e.as_str().unwrap().contains("calibration")), "{}", report["errors"]);
    // The discarded cache's part returns to unjudged (only the 2 definitions of plan.hy fired this time are judged).
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

/// Writes the file of one judgment (line 1 is the key, line 2 onward is the reason).
fn write_judgment(dir: &Path, list: &str, name: &str, key: &str, reason: &str) {
    let path = dir.join(list);
    std::fs::create_dir_all(&path).unwrap();
    std::fs::write(path.join(format!("{}.txt", name)), format!("{}\n{}\n", key, reason)).unwrap();
}

#[test]
fn false_positives_are_not_reported_nor_counted_and_labels_meet_the_answers() {
    let semantic = format!("{}false_positives = [\"FALSE\"]\ntrue_positives = [\"TRUE\"]\n", SEMANTIC);
    let dir = repo(FILES, &semantic);
    std::fs::create_dir_all(dir.path().join("FALSE")).unwrap();
    std::fs::create_dir_all(dir.path().join("TRUE")).unwrap();
    let jev = fake_jev(false);
    let (_, report, _) = run(dir.path(), &jev.url, &["--semantic-all"]);
    let key_of = |rule: &str, name: &str| find(&report, rule, name).unwrap()["key"].as_str().unwrap().to_string();
    let borderline = key_of("DOEFF201", "borderline");
    let route = key_of("DOEFF202", "route");
    let may_post = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF201" && v["severity"] == "warning").unwrap()["key"].as_str().unwrap().to_string();
    // The key of a definition that does not reach the threshold and so is not a violation (read-body) can be judged with the same spelling.
    let read_body = borderline.replace("borderline", "read_body");
    write_judgment(dir.path(), "FALSE", "a", &borderline, "only reads a value, not a business decision");
    write_judgment(dir.path(), "FALSE", "b", &route, "only passes the name of the destination");
    write_judgment(dir.path(), "TRUE", "a", &may_post, "decides who to allow");
    write_judgment(dir.path(), "TRUE", "b", &read_body, "actually makes a decision (Jev cannot catch it)");
    let (code, report, stderr) = run(dir.path(), &jev.url, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(report["errors"].as_array().unwrap().is_empty(), "{}", report["errors"]);
    // A hit on the false-positive list is not reported; only the count is shown separately. The positive list does not change how it is shown.
    assert!(find(&report, "DOEFF201", "borderline").is_none());
    assert!(find(&report, "DOEFF202", "route").is_none());
    assert_eq!(report["violations"].as_array().unwrap().iter().filter(|v| v["source"] == "jev").count(), 1);
    let semantic = &report["semantic"];
    assert_eq!(semantic["false_positives"], 2);
    let labeled = &semantic["labeled"];
    assert_eq!(labeled["positives"], serde_json::json!({"listed": 2, "judged": 2, "flagged": 1}));
    assert_eq!(labeled["negatives"], serde_json::json!({"listed": 2, "judged": 2, "flagged": 2}));
    let missed = labeled["items"].as_array().unwrap().iter().find(|i| i["key"] == read_body.as_str()).unwrap();
    assert_eq!((missed["expect"].as_bool(), missed["flagged"].as_bool(), missed["probability"].as_f64()), (Some(true), Some(false), Some(0.05)));
    // The text output shows the number of false positives in the stderr summary line.
    let home = tempfile::TempDir::new().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log"])
        .current_dir(dir.path())
        .env("HOME", home.path())
        .env("JEV_BASE_URL", &jev.url)
        .env("JEV_MODEL", "jev-test")
        .output()
        .unwrap();
    assert!(String::from_utf8_lossy(&output.stderr).contains("2 semantic rule misjudgments"), "{}", String::from_utf8_lossy(&output.stderr));
    // A judgment with no reason is not read; a key present in both lists is read as neither, as a conflict (both go to errors).
    std::fs::write(dir.path().join("FALSE/b.txt"), format!("{}\n\n", route)).unwrap();
    write_judgment(dir.path(), "TRUE", "c", &borderline, "a violation after all");
    let (_, report, _) = run(dir.path(), &jev.url, &[]);
    let errors = report["errors"].to_string();
    assert!(errors.contains("no reason for the judgment"), "{}", errors);
    assert!(errors.contains("conflict in verdict lists"), "{}", errors);
    assert!(find(&report, "DOEFF202", "route").is_some());
    assert!(find(&report, "DOEFF201", "borderline").is_some());
    assert_eq!(report["semantic"]["false_positives"], 0);
}

#[test]
fn plain_callable_reason_is_accepted_or_rejected_by_jev() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture s :root "app" :layers [(layer core)]
  :plain-callable-reasons [(reason library-callback "called by an external library as a plain function")]
  :rejected-plain-callable-reasons [(reason config-read "reads settings or environment variables" :fix "Make it a defk that receives settings via an effect such as Ask")])
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF203\"]\n[tool.doeff-linter.definitions]\n[tool.doeff-linter.semantic]\nplain_callable = { warning_min = 0.4, info_min = 0.4 }\n",
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(
        dir.path().join("app/core/x.hy"),
        concat!(
            "(deff by-key [row] (sorted rows :key row))  ; defk にできない: key for sorted\n",
            "(deff settings [] (os.getenv \"X\"))  ; defk にできない: handler assembly reads settings\n",
            "(deff handlers [f] [f])  ; defk にできない(library-callback): builds a tuple of handlers\n",
            "(deff helper [] (fixture))  ; defk にできない: an entry point that builds a test value\n",
            "(deff ditto [] 1)  ; defk にできない: 同上\n",
        ),
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    // Ask about the 4 deffs that have a reason sentence ("ditto" is emitted by DOEFF111 so it is not asked; Noul's calibration examples are not fired either).
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    let doubt = |name: &str| find(&report, "DOEFF203", name).cloned();
    assert!(doubt("by_key").is_none(), "an accepted reason does not appear");
    let settings = doubt("settings").expect("config-read");
    assert_eq!(settings["severity"], "warning");
    assert_eq!(settings["source"], "jev");
    assert_eq!(settings["probability"], 0.8);
    assert!(settings["explanation"]["reason"].as_str().unwrap().contains("this reason is not accepted — the closest type is config-read (reads settings or environment variables)"), "{}", settings["explanation"]["reason"]);
    assert_eq!(settings["hint"], "Make it a defk that receives settings via an effect such as Ask");
    assert_eq!(settings["explanation"]["subject"], "definition settings (deff) — stated reason \"handler assembly reads settings\"");
    let handlers = doubt("handlers").expect("none");
    assert_eq!(handlers["severity"], "warning");
    assert!(handlers["explanation"]["reason"].as_str().unwrap().contains("it matches none of the declared accepted reasons"));
    let helper = doubt("helper").expect("a doubtful acceptance");
    assert_eq!(helper["severity"], "info");
    assert!(helper["explanation"]["reason"].as_str().unwrap().contains("the sum of the probabilities of the rejected answers is 0.50"), "{}", helper["explanation"]["reason"]);
}

#[test]
fn class_role_asks_jev_only_for_classes_the_deterministic_rule_left_alone() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF119\", \"DOEFF204\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\"]\npaths = { core = \"app/core\" }\n[tool.doeff-linter.semantic]\nclass_role = { warning_min = 0.7, info_min = 0.5 }\n",
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(
        dir.path().join("app/core/x.hy"),
        concat!(
            "(import httpx)\n",
            "(defclass Reader [] (#^ Store client) (defn rows [self t] (.list-rows self.client t)))\n",
            "(defclass Point [] (#^ float x) (defn twice [self] (* 2 self.x)))\n",
            "(defclass Raw [] (defn get [self u] (httpx.get u)))\n",
            "(defclass Plain [] (#^ int n))\n",
        ),
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    // What is asked is only Reader and Point, which DOEFF119 emits nothing for and which have a method that does processing (Raw is a DOEFF119 error, Plain has only fields). Calibration is 2 examples.
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    assert_eq!(report["semantic"]["calibration"], "ok");
    let reader = find(&report, "DOEFF204", "Reader").expect("Reader");
    assert_eq!(reader["severity"], "warning");
    assert_eq!(reader["source"], "jev");
    assert!(reader["explanation"]["subject"].as_str().unwrap().contains("client: Store"), "{}", reader["explanation"]["subject"]);
    assert!(reader["hint"].as_str().unwrap().contains("(session val …)"));
    assert!(find(&report, "DOEFF204", "Point").is_none());
    assert_eq!(find(&report, "DOEFF119", "Raw").expect("Raw")["severity"], "error");
}

#[test]
fn mixed_concerns_asks_jev_only_for_judgment_and_program_definitions() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF205\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\", \"protocol\"]\npaths = { core = \"app/core\", protocol = \"app/protocol\" }\n[tool.doeff-linter.layers.describe.core]\nsummary = \"business judgment\"\nknows = \"business judgment\"\ndoes_not_know = \"how the counterpart speaks\"\n[tool.doeff-linter.semantic]\nmixed_concerns = { layer = \"core\" }\n",
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(
        dir.path().join("app/core/x.hy"),
        concat!(
            "(val MODULE-TAGS {:context \"kanban\" :role \"judgment\"})\n",
            "(defk decide [payload] (val s (.get payload \"subject\")) (when (isinstance s str) (return 1)) 2)\n",
            "(defk pure [board] (any (gfor c board.cards (= c.key 1))))\n",
            "(defk shaped [p] {:tags {:context \"kanban\" :role \"type\"}} (.get p \"x\"))\n",
        ),
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    // What is asked is only decide and pure of role judgment (module-head tags) — shaped of role type is not asked. Calibration is 2 examples.
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    assert_eq!(report["semantic"]["calibration"], "ok");
    let decide = find(&report, "DOEFF205", "decide").expect("decide");
    assert_eq!(decide["severity"], "warning");
    assert_eq!(decide["source"], "jev");
    assert!(decide["explanation"]["reason"].as_str().unwrap().contains("checking of the input's shape"), "{}", decide["explanation"]["reason"]);
    assert!(decide["hint"].as_str().unwrap().contains("defwire"));
    assert!(find(&report, "DOEFF205", "pure").is_none());
    assert!(find(&report, "DOEFF205", "shaped").is_none());
}

#[test]
fn labeled_probability_of_a_choice_answer_is_the_target_word_not_the_chosen_one() {
    // Counterexample (#1994): for the answer judgment-only 0.97, mixed 0.02 of the choice question (DOEFF205), the cross-check with human judgments
    // reported the probability 0.97 of the chosen word (in the threshold table it looked like mixed 0.97). What is reported is the probability 0.02 of the target word mixed. The probability of yes / no questions does
    // not change (read-body = 0.05 in false_positives_are_not_reported_nor_counted_and_labels_meet_the_answers).
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF205\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\", \"protocol\"]\npaths = { core = \"app/core\", protocol = \"app/protocol\" }\n[tool.doeff-linter.layers.describe.core]\nsummary = \"business judgment\"\nknows = \"business judgment\"\ndoes_not_know = \"how the counterpart speaks\"\n[tool.doeff-linter.semantic]\nmixed_concerns = { layer = \"core\" }\nfalse_positives = [\"FALSE\"]\ntrue_positives = [\"TRUE\"]\n",
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::create_dir_all(dir.path().join("FALSE")).unwrap();
    std::fs::create_dir_all(dir.path().join("TRUE")).unwrap();
    std::fs::write(
        dir.path().join("app/core/x.hy"),
        concat!(
            "(val MODULE-TAGS {:context \"kanban\" :role \"judgment\"})\n",
            "(defk decide [payload] (val s (.get payload \"subject\")) (when (isinstance s str) (return 1)) 2)\n",
            "(defk convert-project [value] (convert-outcome-of value \"messageId\"))\n",
        ),
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    let decide = find(&report, "DOEFF205", "decide").unwrap_or_else(|| panic!("decide did not appear: {} {}", report, stderr))["key"].as_str().unwrap().to_string();
    let convert = decide.replace("decide", "convert_project");
    write_judgment(dir.path(), "TRUE", "a", &decide, "shape checking and judgment are in the same definition");
    write_judgment(dir.path(), "FALSE", "a", &convert, "judgment is left to the callee");
    let (code, report, stderr) = run(dir.path(), &jev.url, &[]);
    assert_eq!(code, 0, "{}", stderr);
    let items = report["semantic"]["labeled"]["items"].as_array().unwrap();
    let item_of = |key: &str| items.iter().find(|i| i["key"] == key).unwrap_or_else(|| panic!("{} is not in labeled: {}", key, report["semantic"]["labeled"]));
    let chosen_other = item_of(&convert);
    assert_eq!(
        (chosen_other["expect"].as_bool(), chosen_other["flagged"].as_bool(), chosen_other["probability"].as_f64()),
        (Some(false), Some(false), Some(0.02)),
        "an answer that chose judgment-only is reported with the probability of mixed (not 0.97 of the chosen word)"
    );
    let chosen_mixed = item_of(&decide);
    assert_eq!((chosen_mixed["expect"].as_bool(), chosen_mixed["flagged"].as_bool(), chosen_mixed["probability"].as_f64()), (Some(true), Some(true), Some(0.9)));
    assert_eq!(report["semantic"]["labeled"]["negatives"], serde_json::json!({"listed": 1, "judged": 1, "flagged": 0}));
}

#[test]
fn mixed_concerns_does_not_ask_definitions_under_excluded_paths() {
    // From the population of DOEFF205 (files of definitions), exclude paths that match the layer declaration's exclude (test locations).
    // Counterexample: without exclude, judgment definitions in the test location would also be asked of Jev and mix into the hits of business judgment.
    let setup = |exclude: &str| {
        let dir = tempfile::TempDir::new().unwrap();
        std::fs::write(
            dir.path().join("pyproject.toml"),
            format!(
                "[tool.doeff-linter]\nenable = [\"DOEFF205\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\", \"protocol\"]\npaths = {{ core = \"app/core\", protocol = \"app/protocol\" }}\nexclude = [{}]\n[tool.doeff-linter.layers.describe.core]\nsummary = \"business judgment\"\nknows = \"business judgment\"\ndoes_not_know = \"how the counterpart speaks\"\n[tool.doeff-linter.semantic]\nmixed_concerns = {{ layer = \"core\" }}\n",
                exclude
            ),
        )
        .unwrap();
        std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
        std::fs::create_dir_all(dir.path().join("app/tests")).unwrap();
        let body = "(val MODULE-TAGS {:context \"kanban\" :role \"judgment\"})\n(defk decide [payload] (val s (.get payload \"subject\")) (when (isinstance s str) (return 1)) 2)\n";
        std::fs::write(dir.path().join("app/core/x.hy"), body).unwrap();
        std::fs::write(dir.path().join("app/tests/rules.hy"), body.replace("decide", "check")).unwrap();
        dir
    };
    let jev = fake_jev(false);
    let excluded = setup("\"tests\"");
    let (_, report, stderr) = run(excluded.path(), &jev.url, &["--semantic-all"]);
    // What is asked is only decide in app/core (calibration is 2 examples) — check in app/tests is not asked.
    assert_eq!(report["semantic"]["asked"], 3, "{} {}", report["semantic"], stderr);
    assert!(find(&report, "DOEFF205", "decide").is_some());
    assert!(find(&report, "DOEFF205", "check").is_none());
    let included = setup("");
    let (_, report, stderr) = run(included.path(), &jev.url, &["--semantic-all"]);
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    assert!(find(&report, "DOEFF205", "check").is_some());
}

#[test]
fn semantic_with_several_files_asks_every_named_definition() {
    // Non-firing example: naming 2 files in one --semantic asks about both definitions (plan.hy 2 + chat.hy 3 + 4 calibration examples).
    let dir = repo(FILES, SEMANTIC);
    let jev = fake_jev(false);
    let (code, report, stderr) = run(dir.path(), &jev.url, &["--semantic", "app/core/plan.hy", "app/protocol/chat.hy"]);
    assert_eq!(code, 0, "{}", stderr);
    assert_eq!(report["semantic"]["asked"], 9, "{} {}", report["semantic"], stderr);
    assert_eq!(report["semantic"]["calibration"], "ok");
    assert_eq!(jev.hits.load(Ordering::SeqCst), 9);
}

#[test]
fn semantic_refuses_a_named_path_that_does_not_exist() {
    // Firing example: one argument in which 2 paths are joined by a space (zsh's unquoted $FILES) is a nonexistent path — instead of asking nothing and claiming 0 and green,
    // it states the reason and stops with exit code 2 (before, it was "fired this time 0, calibration not-run" and exit code 0).
    let dir = repo(FILES, SEMANTIC);
    let jev = fake_jev(false);
    let home = tempfile::TempDir::new().unwrap();
    for format in ["editor-json", "json"] {
        let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
            .args(["--output-format", format, "--no-log", "--semantic", "app/core/plan.hy app/protocol/chat.hy"])
            .current_dir(dir.path())
            .env_clear()
            .env("PATH", std::env::var("PATH").unwrap_or_default())
            .env("HOME", home.path())
            .env("JEV_BASE_URL", &jev.url)
            .env("JEV_MODEL", "jev-test")
            .stdin(Stdio::null())
            .output()
            .unwrap();
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert_eq!(output.status.code(), Some(2), "{}: {}", format, stderr);
        assert!(stderr.contains("path named by --semantic does not exist") && stderr.contains("app/core/plan.hy app/protocol/chat.hy"), "{}: {}", format, stderr);
    }
    assert_eq!(jev.hits.load(Ordering::SeqCst), 0, "a stopped run does not call Jev");
}

#[test]
fn semantic_names_a_file_that_has_nothing_to_ask() {
    // Firing example: if a named file is outside the layers asked about, it states the reason for 0 asked (does not silently make it 0). If together with a file inside a layer, that part is asked.
    let mut files = FILES.to_vec();
    files.push(("app/entry/main.hy", "(defk main [] 0)\n"));
    let dir = repo(&files, SEMANTIC);
    let jev = fake_jev(false);
    let (code, report, stderr) = run(dir.path(), &jev.url, &["--semantic", "app/entry/main.hy", "app/core/plan.hy"]);
    assert_eq!(code, 0, "{}", stderr);
    assert_eq!(report["semantic"]["asked"], 6, "{} {}", report["semantic"], stderr);
    let errors = report["errors"].as_array().unwrap();
    assert!(
        errors.iter().any(|e| e.as_str().unwrap().contains("have no definition to ask about") && e.as_str().unwrap().contains("app/entry/main.hy")),
        "{}",
        report["errors"]
    );
    assert!(!errors.iter().any(|e| e.as_str().unwrap().contains("app/core/plan.hy")), "{}", report["errors"]);
}
