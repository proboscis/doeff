//! Jev の規則(DOEFF201・202・205)の問いに入れる線引き(architecture.hy の :semantic-lines — agora-redesign #1909)のテスト。
//! 宣言は tests/fixtures/semantic_lines/architecture.hy(線引き 5 つと、線引きごとの鳴る例・鳴らない例 1 つずつ = 計 10)。
//! 自動のテストは Jev の宛先を手元の偽の HTTP(127.0.0.1)にし、問いに線引きの文と例が入る事と、問いを組んで答えを受けるまでを確かめる。
//! 本物の Jev に 10 例を 1 回だけ問うテストは #[ignore](手で走らせる — 下の ask_jev_the_line_examples_once)。

use doeff_linter::project::architecture::{Architecture, SemanticLine};
use doeff_linter::project::semantic::{self, Gateway, SemanticQuestion};
use rayon::prelude::*;
use serde_json::{json, Value};
use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::{Arc, Mutex};

/// テストの宣言(線引き 5 つ・例 10・層 core と protocol)。
const ARCHITECTURE: &str = include_str!("fixtures/semantic_lines/architecture.hy");

/// テストの宣言を読んだ物。
fn declared() -> Architecture {
    Architecture::parse(ARCHITECTURE, Path::new("architecture.hy")).unwrap_or_else(|problems| panic!("テストの宣言が読めない: {:?}", problems))
}

/// 偽の Jev(TypeSafe の direct の形)— 受けた問いの本文を全部控え、線引きに従って答える: 問われた定義の source が問いの線引きの
/// 鳴る例なら違反の側(noul 0.93・Choice は mixed 0.97)、鳴らない例なら違反でない側、どちらでもなければ閾値の下(noul 0.3・judgment-only)。
/// 較正の見張りの例(同梱の data/semantic_calibration.json)は名で幅の内に答える。
struct FakeJev {
    url: String,
    bodies: Arc<Mutex<Vec<Value>>>,
}

/// 問われた定義の source が、問いの線引きの鳴る例か(Some(true))鳴らない例か(Some(false))どちらでもないか(None)。
fn line_verdict(body: &Value) -> Option<bool> {
    let source = body["state"]["definition"]["source"].as_str()?;
    let lines = body["questions"]["q"]["instructions"]["lines"].as_array()?;
    let holds = |line: &Value, key: &str| line[key].as_array().is_some_and(|codes| codes.iter().any(|code| code.as_str() == Some(source)));
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

/// 偽の Jev の答えの本文(問いの種類と線引きの判定から)。
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

/// 偽の Jev を立てる。
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

/// 線引きの鳴る例・鳴らない例の code(宣言の順)。
fn examples(lines: &[SemanticLine]) -> (Vec<String>, Vec<String>) {
    (lines.iter().flat_map(|l| l.fires.clone()).collect(), lines.iter().flat_map(|l| l.silent.clone()).collect())
}

/// テストの repo — 宣言の architecture.hy と、線引き 1 の例を protocol の file・残りの例を core の file に 1 定義ずつ置く(定義の本文 = 例の code)。
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
        ([first.fires.clone(), first.silent.clone()].concat(), [fires, silent].concat())
    };
    let module = |role: &str, codes: &[String]| format!("(val MODULE-TAGS {{:context \"lines\" :role \"{}\"}})\n\n{}\n", role, codes.join("\n\n"));
    for (rel, text) in [("app/lines/protocol/lease.hy", module("protocol", &protocol)), ("app/lines/core/rules.hy", module("judgment", &core))] {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// editor-json で走らせる(Jev の宛先は偽物・HOME は一時の dir — 本物の ~/.config/jev を読まない)。
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

/// 問いの本文がどの規則の問いか(Choice の mixed = DOEFF205・noul は問いの文で 201 と 202 を分ける)。
fn rule_of(body: &Value) -> &'static str {
    let q = &body["questions"]["q"];
    if q["criteria"].get("mixed").is_some() {
        "DOEFF205"
    } else if q["instructions"]["question"].as_str().is_some_and(|text| text.contains("make a business decision")) {
        "DOEFF201"
    } else {
        "DOEFF202"
    }
}

/// 規則 rule の違反を定義の名(mangle した綴り)で引く。
fn finding<'a>(report: &'a Value, rule: &str, mangled: &str) -> Option<&'a Value> {
    report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == rule && v["key"].as_str().unwrap().ends_with(mangled))
}

/// 線引きごとの鳴る例・鳴らない例(計 10)を問いの JSON に入れ、偽の Jev の答えを受けて違反にする(agora-redesign #1909)。
/// 問いには、その規則の線引きの名・文(architecture.hy の :text のまま)・鳴る例・鳴らない例(:fires・:silent の code)が入る —
/// 線引き 1 と 5 は DOEFF201、2 と 3 は DOEFF202、4 と 5 は DOEFF205。偽の Jev は問いの例に照らして答えるので、線引きの文か例を問いから
/// 外すと鳴る例の違反が消えてこのテストが落ちる。
#[test]
fn line_examples_reach_the_questions_and_the_answers_come_back() {
    let arch = declared();
    assert_eq!(arch.semantic_lines.len(), 5, "線引きは 5 つ");
    assert!(arch.semantic_lines.iter().all(|l| l.fires.len() == 1 && l.silent.len() == 1), "線引きごとに鳴る例・鳴らない例 1 つずつ");
    let dir = repo();
    let jev = fake_jev();
    let (code, report, stderr) = run(dir.path(), &jev.url);
    assert_eq!(code, 0, "Jev の違反は error にしない: {}", stderr);
    let semantic = &report["semantic"];
    assert_eq!(semantic["calibration"], "ok", "{} {}", semantic, report["errors"]);
    assert_eq!(semantic["unjudged"], 0, "{}", semantic);
    // 問う定義 = protocol の 2(DOEFF201)+ core の 8(DOEFF202)+ core の役 judgment の 8(DOEFF205)。
    assert_eq!(semantic["judged"], 18, "{}", semantic);
    let bodies = jev.bodies.lock().unwrap().clone();
    let expected = |rule: &str| -> Vec<Value> {
        arch.semantic_lines
            .iter()
            .filter(|line| line.rules.iter().any(|q| q.id() == rule))
            .map(|line| json!({"name": line.name, "text": line.text, "violating_examples": line.fires, "complying_examples": line.silent}))
            .collect()
    };
    for rule in ["DOEFF201", "DOEFF202", "DOEFF205"] {
        let asked: Vec<&Value> = bodies.iter().filter(|b| rule_of(b) == rule).collect();
        assert!(!asked.is_empty(), "{} の問いが無い", rule);
        for body in asked {
            let instructions = &body["questions"]["q"]["instructions"];
            assert_eq!(instructions["lines"], Value::Array(expected(rule)), "{} の問いの線引き", rule);
            assert!(instructions["lines_note"].as_str().is_some_and(|note| note.contains("violating_examples")), "{} の問いに線引きの読み方が無い", rule);
        }
    }
    let names: Vec<(&str, &str)> = arch
        .semantic_lines
        .iter()
        .map(|line| (line.name.as_str(), line.rules.first().map(|q| q.id()).unwrap_or_default()))
        .collect();
    assert_eq!(names, [("線引き 1", "DOEFF201"), ("線引き 2", "DOEFF202"), ("線引き 3", "DOEFF202"), ("線引き 4", "DOEFF205"), ("線引き 5", "DOEFF205")]);
    // 線引きごとの鳴る例は warning、鳴らない例は出ない(10 例)。
    for (rule, fires, silent) in [
        ("DOEFF201", "access_of_held", "send_task"),
        ("DOEFF202", "tag_families_of", "beat_status"),
        ("DOEFF202", "relay_url_of", "admit_writer"),
        ("DOEFF205", "classify_message", "join_chat"),
        ("DOEFF205", "progress_items", "list_limit_of"),
    ] {
        let found = finding(&report, rule, fires).unwrap_or_else(|| panic!("{} の鳴る例 {} が鳴らない: {}", rule, fires, report["violations"]));
        assert_eq!(found["severity"], "warning");
        assert_eq!(found["source"], "jev");
        assert!(finding(&report, rule, silent).is_none(), "{} の鳴らない例 {} が鳴った", rule, silent);
    }
    let jev_findings = report["violations"].as_array().unwrap().iter().filter(|v| v["source"] == "jev").count();
    assert_eq!(jev_findings, 5, "鳴るのは鳴る例 5 つだけ: {}", report["violations"]);
}

/// 本物の Jev に 10 例を 1 回だけ問い、例ごとの確率と token 数を 1 行 1 例の JSON で出す(agora-redesign #1909 — 手で走らせる:
/// `SEMANTIC_LINES_DEFINITIONS=<json> SEMANTIC_LINES_PROXY_URL=<url> cargo test --test semantic_lines -- --ignored --nocapture`)。
/// 問う定義 = env SEMANTIC_LINES_DEFINITIONS の JSON(`[{"name", "rule", "path", "expect", "source"} …]` — 本物の定義の source。linter と同じく
/// :tags を消して `source_limit` 字で切る)。線引きと層の説明はこのテストの architecture.hy の物。宛先 = Jev の呼び出しを覚える proxy
/// (env SEMANTIC_LINES_PROXY_URL・token は ~/.config/jev/proxy-token)。較正の見張りは問わない(問うのは 10 例だけ)。
#[test]
#[ignore]
fn ask_jev_the_line_examples_once() {
    let definitions: Vec<Value> = std::env::var("SEMANTIC_LINES_DEFINITIONS")
        .ok()
        .and_then(|path| std::fs::read_to_string(path).ok())
        .and_then(|text| serde_json::from_str(&text).ok())
        .expect("SEMANTIC_LINES_DEFINITIONS に問う定義の JSON の path を渡す");
    let url = std::env::var("SEMANTIC_LINES_PROXY_URL").expect("SEMANTIC_LINES_PROXY_URL に proxy の URL を渡す");
    let arch = declared();
    let layers = arch.layers_section();
    let bare = semantic::SemanticSettings::validate(&semantic::SemanticSection::default(), &mut |_, _| None, &mut Vec::new());
    let settings = semantic::SemanticSettings { lines: arch.semantic_lines.clone(), ..bare };
    let proxy = semantic::ProxySettings {
        url,
        token_file: semantic::DEFAULT_PROXY_TOKEN_FILE.to_string(),
        peek_timeout: std::time::Duration::from_millis(semantic::DEFAULT_PROXY_PEEK_TIMEOUT_MS),
    };
    let target = semantic::target_for_repo(Some(&proxy));
    let model = target.model.clone();
    let gateway = semantic::HttpGateway::new(target, std::time::Duration::from_secs(120)).expect("proxy へ問う client を作れない");
    let at = doeff_indexer::hy_index::Position { line: 0, character: 0 };
    let range = doeff_indexer::hy_index::Range { start: at, end: at };
    let layer_of = |name: &str| {
        let index = layers.order.iter().position(|l| l == name).expect("層が宣言に無い");
        (doeff_linter::project::settings::LayerId(index), layers.describe[name].clone())
    };
    let ask = |definition: &Value| -> Value {
        let text = |key: &str| definition[key].as_str().unwrap_or_else(|| panic!("定義の {} が無い: {}", key, definition)).to_string();
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
            other => panic!("線引きを入れる問いの規則でない: {}", other),
        };
        match gateway.ask(&item.state, &item.question_json, semantic::Freshness::Remembered) {
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
    // 10 例を並べて問う(1 件ずつ順に待たない)。出す順は渡した順。
    let answers: Vec<Value> = definitions.par_iter().map(ask).collect();
    for answer in answers {
        println!("{}", answer);
    }
}
