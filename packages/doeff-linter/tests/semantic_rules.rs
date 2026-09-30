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
            if body["questions"]["q"]["criteria"].get("mixed").is_some() {
                // DOEFF205: 文字列の鍵の .get を持つ定義は mixed、それ以外は judgment-only。較正の例もこの規則で幅に入る。
                let (choice, probabilities) = if source.contains(".get") {
                    ("mixed", serde_json::json!({"mixed": 0.9, "shape-only": 0.05, "judgment-only": 0.04, "neither": 0.01}))
                } else {
                    ("judgment-only", serde_json::json!({"mixed": 0.03, "shape-only": 0.02, "judgment-only": 0.94, "neither": 0.01}))
                };
                let answer = serde_json::json!({"answers": {"q": {"type": "choice", "choice": choice, "probabilities": probabilities}}, "usage": {"input_tokens": 40}, "model": "jev-test-1"}).to_string();
                let _ = write!(stream, "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}", answer.len(), answer);
                continue;
            }
            if body["questions"]["q"]["criteria"].get("external-world").is_some() {
                // DOEFF204: client を欄に持つ class は external-world、それ以外は value。較正の合成の例もこの規則で幅に入る。
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
                // DOEFF203: sorted の key は library-callback、設定を読む定義は config-read(受け入れない型)、
                // 検の補助は library-callback を選ぶが受け入れない答えの和が大きい、それ以外は none。
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

/// 判定 1 つの file を書く(1 行目が鍵・2 行目から後が理由)。
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
    // 閾値に届かず違反にならない定義の鍵(read-body)も、同じ綴りで判定できる。
    let read_body = borderline.replace("borderline", "read_body");
    write_judgment(dir.path(), "FALSE", "a", &borderline, "値を読むだけで業務の判断ではない");
    write_judgment(dir.path(), "FALSE", "b", &route, "宛先の名を渡すだけ");
    write_judgment(dir.path(), "TRUE", "a", &may_post, "誰に許すかを決めている");
    write_judgment(dir.path(), "TRUE", "b", &read_body, "本当は判断している(Jev は拾えない)");
    let (code, report, stderr) = run(dir.path(), &jev.url, &[]);
    assert_eq!(code, 0, "{}", stderr);
    assert!(report["errors"].as_array().unwrap().is_empty(), "{}", report["errors"]);
    // 誤判定の一覧に載った当たりは出さず、数だけを別に出す。正例の一覧は出し方を変えない。
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
    // text の出力は stderr の要約の行に誤判定の数を出す。
    let home = tempfile::TempDir::new().unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(["--no-log"])
        .current_dir(dir.path())
        .env("HOME", home.path())
        .env("JEV_BASE_URL", &jev.url)
        .env("JEV_MODEL", "jev-test")
        .output()
        .unwrap();
    assert!(String::from_utf8_lossy(&output.stderr).contains("意味の規則の誤判定 2 件"), "{}", String::from_utf8_lossy(&output.stderr));
    // 理由の無い判定は読まない・両方の一覧に在る鍵は食い違いとしてどちらとしても読まない(どちらも errors に出す)。
    std::fs::write(dir.path().join("FALSE/b.txt"), format!("{}\n\n", route)).unwrap();
    write_judgment(dir.path(), "TRUE", "c", &borderline, "やはり違反");
    let (_, report, _) = run(dir.path(), &jev.url, &[]);
    let errors = report["errors"].to_string();
    assert!(errors.contains("判定の理由"), "{}", errors);
    assert!(errors.contains("食い違い"), "{}", errors);
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
  :plain-callable-reasons [(reason library-callback "外の library が素の関数として呼ぶ")]
  :rejected-plain-callable-reasons [(reason config-read "設定・環境変数を読む" :fix "Ask などの effect で設定を受け取る defk にする")])
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
            "(deff by-key [row] (sorted rows :key row))  ; defk にできない: sorted の key\n",
            "(deff settings [] (os.getenv \"X\"))  ; defk にできない: handler の組み立てが設定を読む\n",
            "(deff handlers [f] [f])  ; defk にできない(library-callback): handler の組を組む\n",
            "(deff helper [] (fixture))  ; defk にできない: 検の値を組む口\n",
            "(deff ditto [] 1)  ; defk にできない: 同上\n",
        ),
    )
    .unwrap();
    let jev = fake_jev(false);
    let (_, report, stderr) = run(dir.path(), &jev.url, &["--semantic-all"]);
    // 理由の文がある deff 4 つを問う(「同上」は DOEFF111 が出すので問わない・Noul の較正の例も撃たない)。
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    let doubt = |name: &str| find(&report, "DOEFF203", name).cloned();
    assert!(doubt("by_key").is_none(), "受け入れる理由は出ない");
    let settings = doubt("settings").expect("config-read");
    assert_eq!(settings["severity"], "warning");
    assert_eq!(settings["source"], "jev");
    assert_eq!(settings["probability"], 0.8);
    assert!(settings["explanation"]["reason"].as_str().unwrap().contains("この理由は受け入れられない — 近い型は config-read(設定・環境変数を読む)"), "{}", settings["explanation"]["reason"]);
    assert_eq!(settings["hint"], "Ask などの effect で設定を受け取る defk にする");
    assert_eq!(settings["explanation"]["subject"], "定義 settings(deff)— 書かれた理由「handler の組み立てが設定を読む」");
    let handlers = doubt("handlers").expect("none");
    assert_eq!(handlers["severity"], "warning");
    assert!(handlers["explanation"]["reason"].as_str().unwrap().contains("宣言した受け入れる理由のどれにも当たらず"));
    let helper = doubt("helper").expect("疑わしい受け入れ");
    assert_eq!(helper["severity"], "info");
    assert!(helper["explanation"]["reason"].as_str().unwrap().contains("受け入れない答えの確率の和が 0.50"), "{}", helper["explanation"]["reason"]);
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
    // 問うのは DOEFF119 が何も出さず処理を持つ method のある Reader と Point だけ(Raw は DOEFF119 の error・Plain は欄だけ)。較正は 2 例。
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
        "[tool.doeff-linter]\nenable = [\"DOEFF205\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\", \"protocol\"]\npaths = { core = \"app/core\", protocol = \"app/protocol\" }\n[tool.doeff-linter.layers.describe.core]\nsummary = \"業務の判断\"\nknows = \"業務の判断\"\ndoes_not_know = \"相手の話し方\"\n[tool.doeff-linter.semantic]\nmixed_concerns = { layer = \"core\" }\n",
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
    // 問うのは役 judgment の decide と pure(module の頭のタグ)— 役 type の shaped は問わない。較正は 2 例。
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    assert_eq!(report["semantic"]["calibration"], "ok");
    let decide = find(&report, "DOEFF205", "decide").expect("decide");
    assert_eq!(decide["severity"], "warning");
    assert_eq!(decide["source"], "jev");
    assert!(decide["explanation"]["reason"].as_str().unwrap().contains("入力の形の検め"), "{}", decide["explanation"]["reason"]);
    assert!(decide["hint"].as_str().unwrap().contains("defwire"));
    assert!(find(&report, "DOEFF205", "pure").is_none());
    assert!(find(&report, "DOEFF205", "shaped").is_none());
}

#[test]
fn mixed_concerns_does_not_ask_definitions_under_excluded_paths() {
    // agora-redesign #1952: DOEFF205 の母集団(definitions の file)から、層の宣言の exclude に当たる path(検の置き場)を外す。
    // 反例: exclude が無ければ、検の置き場の judgment の定義も Jev に問われ、業務の判断の当たりに混ざる。
    let setup = |exclude: &str| {
        let dir = tempfile::TempDir::new().unwrap();
        std::fs::write(
            dir.path().join("pyproject.toml"),
            format!(
                "[tool.doeff-linter]\nenable = [\"DOEFF205\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.layers]\norder = [\"core\", \"protocol\"]\npaths = {{ core = \"app/core\", protocol = \"app/protocol\" }}\nexclude = [{}]\n[tool.doeff-linter.layers.describe.core]\nsummary = \"業務の判断\"\nknows = \"業務の判断\"\ndoes_not_know = \"相手の話し方\"\n[tool.doeff-linter.semantic]\nmixed_concerns = {{ layer = \"core\" }}\n",
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
    // 問うのは app/core の decide だけ(較正は 2 例)— app/tests の check は問わない。
    assert_eq!(report["semantic"]["asked"], 3, "{} {}", report["semantic"], stderr);
    assert!(find(&report, "DOEFF205", "decide").is_some());
    assert!(find(&report, "DOEFF205", "check").is_none());
    let included = setup("");
    let (_, report, stderr) = run(included.path(), &jev.url, &["--semantic-all"]);
    assert_eq!(report["semantic"]["asked"], 4, "{} {}", report["semantic"], stderr);
    assert!(find(&report, "DOEFF205", "check").is_some());
}
