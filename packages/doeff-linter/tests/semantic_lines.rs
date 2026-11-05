//! Tests for the lines drawn into the Jev rules' (DOEFF201, 202, 205) questions (architecture.hy :semantic-lines).
//! The declaration is tests/fixtures/semantic_lines/architecture.hy (5 lines, with one firing example and one silent example per line = 10 in total).
//! The automated tests point the Jev destination at a local fake HTTP server (127.0.0.1), and check that the question contains the line text and examples, and that a question is built and an answer received.
//! The test that asks the real Jev about the 10 examples once is #[ignore] (run by hand — ask_jev_the_line_examples_once below).

use doeff_linter::project::architecture::Architecture;
use doeff_linter::project::semantic::{LineExample, SemanticLine};
use doeff_linter::project::semantic::{self, Gateway, SemanticQuestion};
use rayon::prelude::*;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

/// The test declaration (5 lines, 10 examples, layers core and protocol).
const ARCHITECTURE: &str = include_str!("fixtures/semantic_lines/architecture.hy");

/// The parsed test declaration.
fn declared() -> Architecture {
    Architecture::parse(ARCHITECTURE, Path::new("architecture.hy")).unwrap_or_else(|problems| panic!("cannot read the test declaration: {:?}", problems))
}

/// A fake Jev (TypeSafe direct form) — records every question body it receives and answers according to the lines: if the asked definition's source is a firing
/// example of the question's line, the violating side (noul 0.93, Choice mixed 0.97); if a silent example, the non-violating side; otherwise below the threshold (noul 0.3, judgment-only).
/// The calibration watch examples (bundled data/semantic_calibration.json) are answered within the range by name.
struct FakeJev {
    url: String,
    bodies: Arc<Mutex<Vec<Value>>>,
}

/// From the question's instructions parts (a list of single-key objects), look up the content of key `key`.
fn part<'a>(question: &'a Value, key: &str) -> &'a Value {
    question["instructions"].as_array().and_then(|parts| parts.iter().find_map(|p| p.get(key))).unwrap_or(&Value::Null)
}

/// The keys of the question's instructions parts (in the order sent).
fn part_keys(question: &Value) -> Vec<String> {
    question["instructions"].as_array().map(|parts| parts.iter().filter_map(|p| p.as_object()?.keys().next().cloned()).collect()).unwrap_or_default()
}

/// Whether the asked definition's source is a firing example of the question's line (Some(true)), a silent example (Some(false)), or neither (None).
fn line_verdict(body: &Value) -> Option<bool> {
    let source = body["state"]["definition"]["source"].as_str()?;
    let lines = part(&body["questions"]["q"], "lines").as_array()?;
    let holds = |line: &Value, key: &str| line[key].as_array().is_some_and(|examples| examples.iter().any(|example| example["code"].as_str() == Some(source)));
    lines.iter().find_map(|line| {
        if holds(line, "violating_examples") {
            Some(true)
        } else if holds(line, "complying_examples") {
            Some(false)
        } else {
            None
        }
    })
}

/// The body of the fake Jev's answer (from the kind of question and the line's judgment).
fn fake_answer(body: &Value) -> Value {
    let name = body["state"]["definition"]["name"].as_str().unwrap_or("");
    let verdict = line_verdict(body);
    if body["questions"]["q"]["criteria"].get("mixed").is_some() {
        let mixed = verdict == Some(true) || name == "decide-tag";
        let (choice, probabilities) = if mixed {
            ("mixed", json!({"mixed": 0.97, "shape-only": 0.01, "judgment-only": 0.01, "neither": 0.01}))
        } else {
            ("judgment-only", json!({"mixed": 0.02, "shape-only": 0.02, "judgment-only": 0.95, "neither": 0.01}))
        };
        return json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 40}, "model": "jev-test-1"});
    }
    let p = match verdict {
        Some(true) => 0.93,
        Some(false) => 0.05,
        None if ["may-manage?", "classifier-call"].contains(&name) => 0.93,
        None if ["post-json", "wake"].contains(&name) => 0.05,
        None => 0.3,
    };
    json!({"answers": {"q": {"noul": p}}, "usage": {"input_tokens": 100}, "model": "jev-test-1"})
}

/// Stand up the fake Jev.
fn fake_jev() -> FakeJev {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let url = format!("http://{}/v1/systemone", listener.local_addr().unwrap());
    let bodies = Arc::new(Mutex::new(Vec::new()));
    let kept = bodies.clone();
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
            let mut raw = vec![0u8; length];
            reader.read_exact(&mut raw).unwrap();
            let body: Value = serde_json::from_slice(&raw).unwrap();
            let answer = fake_answer(&body).to_string();
            kept.lock().unwrap().push(body);
            let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
        }
    });
    FakeJev { url, bodies }
}

/// The list of example code.
fn codes(examples: &[LineExample]) -> Vec<String> {
    examples.iter().map(|example| example.code.clone()).collect()
}

/// The code of each line's firing and silent examples (in declaration order).
fn examples(lines: &[SemanticLine]) -> (Vec<String>, Vec<String>) {
    (lines.iter().flat_map(|l| codes(&l.fires)).collect(), lines.iter().flat_map(|l| codes(&l.silent)).collect())
}

/// Turn the examples into the form carried in the question (an example with why is `{"code", "why"}`; one without is `{"code"}`).
fn wired(examples: &[LineExample]) -> Value {
    Value::Array(
        examples
            .iter()
            .map(|example| match &example.why {
                Some(why) => json!({"code": example.code, "why": why}),
                None => json!({"code": example.code}),
            })
            .collect(),
    )
}

/// The test repo — the declaration's architecture.hy, with line 1's examples placed in a protocol file and the remaining examples in a core file, one definition each (definition body = the example's code).
fn repo() -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("architecture.hy"), ARCHITECTURE).unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        concat!(
            "[tool.doeff-linter]\nenable = [\"DOEFF201\", \"DOEFF202\", \"DOEFF205\"]\n",
            "[tool.doeff-linter.definitions]\npaths = [\"app\"]\n",
            "[tool.doeff-linter.semantic]\nbusiness_decision = { layers = [\"protocol\"] }\ntransport_knowledge = { layers = [\"core\"] }\n",
            "mixed_concerns = { layer = \"core\" }\n",
        ),
    )
    .unwrap();
    let arch = declared();
    let first = &arch.semantic_lines[0];
    let (protocol, core): (Vec<String>, Vec<String>) = {
        let (fires, silent) = examples(&arch.semantic_lines[1..]);
        ([codes(&first.fires), codes(&first.silent)].concat(), [fires, silent].concat())
    };
    let module = |role: &str, codes: &[String]| format!("(val MODULE-TAGS {{:context \"lines\" :role \"{}\"}})\n\n{}\n", role, codes.join("\n\n"));
    for (rel, text) in [("app/lines/protocol/lease.hy", module("protocol", &protocol)), ("app/lines/core/rules.hy", module("judgment", &core))] {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Run with editor-json (the Jev destination is fake, and HOME is a temp dir — the real ~/.config/jev is not read).
fn run(root: &Path, jev: &str) -> (i32, Value, String) {
    let home = tempfile::TempDir::new().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--output-format", "editor-json", "--no-log", "--semantic-all"])
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

/// Which rule's question the question body is (Choice's mixed = DOEFF205; for noul, the question text separates 201 from 202).
fn rule_of(body: &Value) -> &'static str {
    let q = &body["questions"]["q"];
    if q["criteria"].get("mixed").is_some() {
        "DOEFF205"
    } else if part(q, "question").as_str().is_some_and(|text| text.contains("make a business decision")) {
        "DOEFF201"
    } else {
        "DOEFF202"
    }
}

/// Look up a violation of rule `rule` by definition name (mangled spelling).
fn finding<'a>(report: &'a Value, rule: &str, mangled: &str) -> Option<&'a Value> {
    report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule && v["key"].as_str().unwrap().ends_with(mangled))
}

/// Put each line's firing and silent examples (10 in total) into the question JSON, receive the fake Jev's answer, and turn it into violations.
/// The question contains the rule's line name, text (as in architecture.hy :text), firing examples, and silent examples (code and why of :fires and :silent) —
/// lines 1 and 5 are DOEFF201, 2 and 3 are DOEFF202, and 4 and 5 are DOEFF205. The fake Jev answers by checking the question's examples, so if the line text or examples are
/// dropped from the question, the firing examples' violations disappear and this test fails.
/// #1995: the instructions of the body the fake Jev received (the question actually sent) are a list in declaration order — question text -> general examples and notes -> how to read the lines -> lines.
/// Reverting to a keyed object loses the part order and this test fails.
#[test]
fn line_examples_reach_the_questions_and_the_answers_come_back() {
    let arch = declared();
    assert_eq!(arch.semantic_lines.len(), 5, "there are 5 lines");
    assert!(arch.semantic_lines.iter().all(|l| l.fires.len() == 1 && l.silent.len() == 1), "one firing example and one silent example per line");
    let with_why: Vec<&str> = arch.semantic_lines.iter().filter(|l| l.fires.iter().chain(&l.silent).any(|e| e.why.is_some())).map(|l| l.name.as_str()).collect();
    assert_eq!(with_why, ["Line 4"], "the examples with why are the 2 of line 4");
    let dir = repo();
    let jev = fake_jev();
    let (code, report, stderr) = run(dir.path(), &jev.url);
    assert_eq!(code, 0, "Jev violations are not errors: {}", stderr);
    let semantic = &report["semantic"];
    assert_eq!(semantic["calibration"], "ok", "{} {}", semantic, report["errors"]);
    assert_eq!(semantic["unjudged"], 0, "{}", semantic);
    // Asked definitions = 2 in protocol (DOEFF201) + 8 in core (DOEFF202) + 8 of role judgment in core (DOEFF205).
    assert_eq!(semantic["judged"], 18, "{}", semantic);
    let bodies = jev.bodies.lock().unwrap().clone();
    let expected = |rule: &str| -> Vec<Value> {
        arch.semantic_lines
            .iter()
            .filter(|line| line.rules.iter().any(|q| q.id() == rule))
            .map(|line| json!({"name": line.name, "text": line.text, "violating_examples": wired(&line.fires), "complying_examples": wired(&line.silent)}))
            .collect()
    };
    for (rule, order) in [
        ("DOEFF201", vec!["question", "business_decision_examples", "not_business_decision_examples", "lines_note", "lines"]),
        ("DOEFF202", vec!["question", "note", "transport_knowledge_examples", "not_transport_knowledge_examples", "lines_note", "lines"]),
        ("DOEFF205", vec!["question", "note", "lines_note", "lines"]),
    ] {
        let asked: Vec<&Value> = bodies.iter().filter(|b| rule_of(b) == rule).collect();
        assert!(!asked.is_empty(), "no question for {}", rule);
        for body in asked {
            let question = &body["questions"]["q"];
            assert_eq!(part_keys(question), order, "part order of the {} question (the body sent)", rule);
            assert_eq!(part(question, "lines"), &Value::Array(expected(rule)), "lines of the {} question", rule);
            assert!(part(question, "lines_note").as_str().is_some_and(|note| note.contains("violating_examples")), "the {} question has no note on how to read the lines", rule);
        }
    }
    // An example with why is carried together with its code in the question sent (line 4's silent example join-chat).
    let join_chat_why = bodies
        .iter()
        .filter(|b| rule_of(b) == "DOEFF205")
        .find_map(|b| part(&b["questions"]["q"], "lines").as_array()?.iter().find_map(|l| l["complying_examples"][0]["why"].as_str().filter(|w| w.contains("join-problem"))))
        .map(str::to_string);
    assert_eq!(join_chat_why.as_deref(), Some("The shape check is left to join-problem, and this only returns a refusal value from its answer. There is no shape check, so nothing is mixed."));
    let names: Vec<(&str, &str)> = arch
        .semantic_lines
        .iter()
        .map(|line| (line.name.as_str(), line.rules.first().map(|q| q.id()).unwrap_or_default()))
        .collect();
    assert_eq!(names, [("Line 1", "DOEFF201"), ("Line 2", "DOEFF202"), ("Line 3", "DOEFF202"), ("Line 4", "DOEFF205"), ("Line 5", "DOEFF205")]);
    // Each line's firing example is a warning, and silent examples do not appear (10 examples).
    for (rule, fires, silent) in [
        ("DOEFF201", "access_of_held", "send_task"),
        ("DOEFF202", "tag_families_of", "beat_status"),
        ("DOEFF202", "relay_url_of", "admit_writer"),
        ("DOEFF205", "classify_message", "join_chat"),
        ("DOEFF205", "progress_items", "list_limit_of"),
    ] {
        let found = finding(&report, rule, fires).unwrap_or_else(|| panic!("{} firing example {} does not fire: {}", rule, fires, report["violations"]));
        assert_eq!(found["severity"], "warning");
        assert_eq!(found["source"], "jev");
        assert!(finding(&report, rule, silent).is_none(), "{} silent example {} fired", rule, silent);
    }
    let jev_findings = report["violations"].as_array().unwrap().iter().filter(|v| v["source"] == "jev").count();
    assert_eq!(jev_findings, 5, "only the 5 firing examples fire: {}", report["violations"]);
}

/// Ask the real Jev about the 10 examples once, and output per-example probability and token count as one JSON line per example (run by hand:
/// `SEMANTIC_LINES_DEFINITIONS=<json> SEMANTIC_LINES_PROXY_URL=<url> cargo test --test semantic_lines -- --ignored --nocapture`).
/// Asked definitions = JSON at the path in env SEMANTIC_LINES_DEFINITIONS (`[{"name", "rule", "path", "expect", "source"} …]` — sources of real definitions. As the linter does,
/// :tags are removed and the source is cut at `source_limit` characters). The lines and layer descriptions are those in this test's architecture.hy. Destination = a proxy that records Jev calls
/// (env SEMANTIC_LINES_PROXY_URL — the proxy does not check keys). The calibration watch is not asked (only the 10 examples are asked).
#[test]
#[ignore]
fn ask_jev_the_line_examples_once() {
    let definitions: Vec<Value> = std::env::var("SEMANTIC_LINES_DEFINITIONS")
        .ok()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .expect("pass the path of the JSON of asked definitions in SEMANTIC_LINES_DEFINITIONS");
    let url = std::env::var("SEMANTIC_LINES_PROXY_URL").expect("pass the proxy URL in SEMANTIC_LINES_PROXY_URL");
    let arch = declared();
    let layers = arch.layers_section();
    let bare = semantic::SemanticSettings::validate(&semantic::SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
    let settings = semantic::SemanticSettings { lines: arch.semantic_lines.clone(), ..bare };
    let proxy = semantic::ProxySettings {
        url,
        peek_timeout: std::time::Duration::from_millis(semantic::DEFAULT_PROXY_PEEK_TIMEOUT_MS),
    };
    let target = semantic::target_for_repo(Some(&proxy));
    let model = target.model.clone();
    let gateway = semantic::HttpGateway::new(target, std::time::Duration::from_secs(120)).expect("cannot create the client that asks the proxy");
    let at = doeff_indexer::hy_index::Position { line: 0, character: 0 };
    let range = doeff_indexer::hy_index::Range { start: at, end: at };
    let layer_of = |name: &str| {
        let index = layers.order.iter().position(|l| l == name).expect("layer is not in the declaration");
        (doeff_linter::project::layers::LayerId(index), layers.describe[name].clone())
    };
    let ask = |definition: &Value| -> Value {
        let text = |key: &str| definition[key].as_str().unwrap_or_else(|| panic!("definition {} is missing: {}", key, definition)).to_string();
        let (name, rule, path, source) = (text("name"), text("rule"), text("path"), text("source"));
        let item = match rule.as_str() {
            "DOEFF205" => {
                let (layer, description) = layer_of("core");
                semantic::mixed_item(&settings, &model, &path, Path::new(&path), &name, "defk", range, &source, layer, "core", &description)
            }
            "DOEFF201" | "DOEFF202" => {
                let (question, layer_name) =
                    if rule == "DOEFF201" { (SemanticQuestion::BusinessDecision, "protocol") } else { (SemanticQuestion::TransportKnowledge, "core") };
                let (layer, description) = layer_of(layer_name);
                semantic::item(&settings, &model, question, &path, Path::new(&path), &name, "defk", range, &source, layer, layer_name, &description)
            }
            other => panic!("not a rule whose question carries lines: {}", other),
        };
        match gateway.ask(&item.state, &item.question_json, semantic::Freshness::Remembered, &semantic::caller_of(item.question)) {
            Ok(asked) => json!({
                "name": name, "rule": rule, "expect": definition["expect"], "probability": asked.answer.probability,
                "choice": asked.answer.choice, "probabilities": asked.answer.probabilities,
                "input_tokens": asked.answer.input_tokens, "output_tokens": asked.answer.output_tokens,
                "served_model": asked.answer.served_model, "charge": format!("{:?}", asked.charge), "key": item.key,
                "question": item.question_json,
            }),
            Err(reason) => json!({"name": name, "rule": rule, "error": reason}),
        }
    };
    // Ask all 10 examples together (not waiting for each in turn). Output order is the order passed.
    let answers: Vec<Value> = definitions.par_iter().map(ask).collect();
    for answer in answers {
        println!("{}", answer);
    }
}
