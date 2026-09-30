//! 層の規則(DOEFF101〜108)と `--output-format editor-json` の検 — 一時の repo を作って binary を走らせ、出力の JSON を見る。
//! 各規則の悪い例(違反が出る)と良い例(出ない)を Hy と Python の両方で持つ。

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

/// 検で使う設定(層 4 つ・law・登録簿・環境の語)。`extra` を末尾に足せる。
fn config(extra: &str) -> String {
    format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF101", "DOEFF102", "DOEFF103", "DOEFF104", "DOEFF105", "DOEFF106", "DOEFF107", "DOEFF108", "DOEFF016"]

[tool.doeff-linter.layers]
order = ["core", "intent", "foundation", "entry"]
paths = {{ core = "app/core", intent = "app/intent", foundation = "app/foundation", entry = "app/entry" }}
exclude = ["tests"]
types_only = ["intent"]

[tool.doeff-linter.layers.allow_imports]
core = ["core", "intent"]
intent = ["intent"]
foundation = ["foundation"]
entry = ["core", "intent", "foundation", "entry"]

[tool.doeff-linter.layers.forbid_modules]
core = ["httpx", "subprocess", "urllib.request"]

[tool.doeff-linter.roles]
names = ["judgment", "type", "intent", "foundation", "entry"]
[tool.doeff-linter.roles.by_layer]
core = ["judgment", "type"]
intent = ["intent", "type"]
foundation = ["foundation"]
entry = ["entry"]
[tool.doeff-linter.roles.describe]
translation = "翻訳の handler"

[tool.doeff-linter.layers.describe.core]
summary = "業務の判断"
knows = "業務の判断"
does_not_know = "通信の手段"
question = "通信が変わっても変わらないか?"

[tool.doeff-linter.layers.describe.foundation]
knows = "本物の I/O"

[tool.doeff-linter.raw_side_effects]
allowed_layers = ["foundation", "entry"]

[tool.doeff-linter.environment_names]
words = ["fake", "local", "wire"]
paths = ["app/core", "app/billing"]
exclude_parts = ["tests"]
assembly_files = ["handler_sets.hy"]

[[tool.doeff-linter.laws]]
name = "core-imports-only-intent"
adr = "ADR-TEST"
rules = ["DOEFF101", "DOEFF102"]
layers = ["core"]
statement = "core は intent だけを読む"

[[tool.doeff-linter.laws]]
name = "no-environment-name"
adr = "ADR-TEST"
rules = ["DOEFF108"]
statement = "業務の名に環境の語が無い"

[[tool.doeff-linter.laws]]
name = "not-wired-yet"
adr = "ADR-TEST"
statement = "まだ針の無い law"

[[tool.doeff-linter.laws]]
name = "absolute-imports"
rules = ["DOEFF016"]
statement = "相対 import を使わない"

[tool.doeff-linter.registry]
dirs = ["registry/BREACHES"]
files = ["registry/keys.txt"]
{extra}
"#
    )
}

/// 一時の repo(pyproject.toml と file の列)を作る。
fn repo(files: &[(&str, &str)], extra_config: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), config(extra_config)).unwrap();
    std::fs::create_dir_all(dir.path().join("registry/BREACHES")).unwrap();
    std::fs::write(dir.path().join("registry/keys.txt"), "").unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// binary を root で走らせ、(終了コード・stdout・stderr)を返す。stdin を渡せる。
fn run(root: &Path, args: &[&str], stdin: Option<&str>) -> (i32, String, String) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args(args)
        .current_dir(root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    child.stdin.take().unwrap().write_all(stdin.unwrap_or("").as_bytes()).unwrap();
    let output = child.wait_with_output().unwrap();
    (output.status.code().unwrap_or(-1), String::from_utf8_lossy(&output.stdout).into_owned(), String::from_utf8_lossy(&output.stderr).into_owned())
}

/// editor-json を repo 全体で走らせ、(終了コード・JSON)を返す。
fn editor(root: &Path) -> (i32, Value) {
    let (code, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    let value: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({}): {}\n{}", e, stdout, stderr));
    (code, value)
}

/// 違反の列から、規則 rule の鍵を並べる。
fn keys(report: &Value, rule: &str) -> Vec<String> {
    let mut keys: Vec<String> = report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| v["rule"] == rule)
        .map(|v| v["key"].as_str().unwrap_or("").to_string())
        .collect();
    keys.sort();
    keys
}

/// 鍵 key の違反を 1 つ取る。
fn violation<'a>(report: &'a Value, key: &str) -> &'a Value {
    report["violations"].as_array().unwrap().iter().find(|v| v["key"] == key).unwrap_or_else(|| panic!("鍵 {} が無い: {}", key, report))
}

const INTENT_HY: &str = "(val MODULE-TAGS {:context \"billing\" :role \"intent\"})\n(defclass Charge [] \"請求の intent\")\n";
const FOUNDATION_HY: &str = "(val MODULE-TAGS {:context \"io\" :role \"foundation\"})\n(defn send [x] x)\n";

#[test]
fn import_direction_bad_and_good_in_hy_and_python() {
    let dir = repo(
        &[
            ("app/intent/charge.hy", INTENT_HY),
            ("app/foundation/io.hy", FOUNDATION_HY),
            (
                "app/core/bad.hy",
                "(import app.foundation.io [send])\n(import app.intent.charge [Charge])\n(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn decide [x] x)\n",
            ),
            ("app/core/bad_py.py", "from app.foundation.io import send\nfrom app.intent.charge import Charge\nMODULE_TAGS = {\"context\": \"billing\", \"role\": \"judgment\"}\n"),
            ("app/core/good.hy", "(import app.intent.charge [Charge])\n(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn decide [x] x)\n"),
            ("app/entry/main.hy", "(import app.foundation.io [send])\n(import app.core.good [decide])\n(val MODULE-TAGS {:context \"billing\" :role \"entry\"})\n(defn run [] (decide 1))\n"),
        ],
        "",
    );
    let (code, report) = editor(dir.path());
    assert_eq!(code, 1, "{}", report);
    assert_eq!(
        keys(&report, "DOEFF101"),
        vec![
            "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send",
            "app/core/bad_py.py::core-imports-only-intent::app.foundation.io.send",
        ]
    );
    let hy = violation(&report, "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send");
    assert_eq!(hy["law"], "core-imports-only-intent");
    assert_eq!(hy["adr"], "ADR-TEST");
    assert_eq!(hy["severity"], "error");
    assert_eq!(hy["registered"], false);
    // 責務の境界の規則は宣言が無くても既定で critical(agora-redesign #1041)。
    assert_eq!(hy["level"], "critical");
    // 位置は import の記号 send(1 行目の 27〜31 列)。
    assert_eq!(hy["range"]["start"], serde_json::json!({"line": 0, "character": 27}));
    assert_eq!(hy["range"]["end"], serde_json::json!({"line": 0, "character": 31}));
    let py = violation(&report, "app/core/bad_py.py::core-imports-only-intent::app.foundation.io.send");
    assert_eq!(py["range"]["start"], serde_json::json!({"line": 0, "character": 30}));
}

#[test]
fn forbidden_module_and_types_only_layer() {
    let dir = repo(
        &[
            ("app/core/net.hy", "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(import httpx)\n(import urllib.parse [quote])\n(import urllib.request [urlopen])\n(defn f [] 1)\n"),
            ("app/core/proc.py", "MODULE_TAGS = {\"context\": \"billing\", \"role\": \"judgment\"}\ndef f():\n    import subprocess\n    return subprocess\n"),
            ("app/intent/bad.hy", "(val MODULE-TAGS {:context \"billing\" :role \"intent\"})\n(defclass Charge [])\n(defk decide [x] {:pre [] :post []} x)\n"),
            ("app/intent/bad_py.py", "MODULE_TAGS = {\"context\": \"billing\", \"role\": \"intent\"}\nclass Charge: ...\ndef decide(x):\n    return x\n"),
            ("app/intent/good.hy", INTENT_HY),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    // 前方一致: urllib.request.urlopen は urllib.request に当たり、純粋な urllib.parse は当たらない。
    assert_eq!(
        keys(&report, "DOEFF102"),
        vec![
            "app/core/net.hy::core-imports-only-intent::httpx",
            "app/core/net.hy::core-imports-only-intent::urllib.request",
            "app/core/proc.py::core-imports-only-intent::subprocess",
        ]
    );
    // law の無い層(intent)の規則は、鍵の <規則> の欄が規則の ID になる。
    assert_eq!(keys(&report, "DOEFF103"), vec!["app/intent/bad.hy::DOEFF103::definitions", "app/intent/bad_py.py::DOEFF103::definitions"]);
    let intent = violation(&report, "app/intent/bad.hy::DOEFF103::definitions");
    assert_eq!(intent["law"], Value::Null);
    assert_eq!(intent["range"]["start"]["line"], 2);
}

#[test]
fn tags_are_declared_and_roles_match_the_layer() {
    let dir = repo(
        &[
            // 定義にタグが無く、module の頭のタグも無い。
            ("app/core/untagged.hy", "(defn decide [x] x)\n(defk plan [x] {:pre [] :post []} x)\n"),
            // 何も名乗らない Python の module。
            ("app/core/untagged_py.py", "def decide(x):\n    return x\n"),
            // 定義の :tags だけで名乗る(module の頭のタグは要らない)— 良い例。
            ("app/core/per_definition.hy", "(defk plan [x] \"計画\" {:pre [] :post [] :tags {:context \"billing\" :role \"judgment\"}} x)\n"),
            // role が層に合わない・context が無い。
            ("app/core/wrong_role.hy", "(val MODULE-TAGS {:context \"billing\" :role \"translation\"})\n(defn f [] 1)\n"),
            ("app/core/no_context.py", "MODULE_TAGS = {\"role\": \"judgment\"}\n"),
            ("app/core/tests/test_x.hy", "(defn test-x [] 1)\n"),
            // defeffect の辞書の :tags で名乗る — 良い例(module の頭のタグは要らない)。タグの無い defeffect は悪い例。
            ("app/intent/effects.hy", "(defeffect Charge \"請求\" {:fields [amount] :answer int :tags {:context \"billing\" :role \"intent\"}})\n"),
            ("app/intent/untagged_effect.hy", "(defeffect Refund {:fields [amount] :answer int})\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF104"),
        vec!["app/core/untagged.hy::DOEFF104", "app/core/untagged_py.py::DOEFF104", "app/intent/untagged_effect.hy::DOEFF104"]
    );
    assert_eq!(keys(&report, "DOEFF105"), vec!["app/core/no_context.py::DOEFF105::judgment", "app/core/wrong_role.hy::DOEFF105::translation"]);
    let untagged = violation(&report, "app/core/untagged.hy::DOEFF104");
    assert!(untagged["message"].as_str().unwrap().contains("decide・plan"));
    // 位置は最初のタグの無い定義の名。
    assert_eq!(untagged["range"]["start"], serde_json::json!({"line": 0, "character": 6}));
    let modules = report["modules"].as_array().unwrap();
    let per_definition = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("per_definition.hy")).unwrap();
    assert_eq!(per_definition["layer"], "core");
    assert_eq!(per_definition["context"], "billing");
    assert_eq!(per_definition["role"], "judgment");
    assert_eq!(per_definition["violations"], 0);
    assert!(!modules.iter().any(|m| m["path"].as_str().unwrap().contains("/tests/")), "検は層の母集団の外");
}

#[test]
fn raw_side_effects_outside_allowed_layers() {
    let dir = repo(
        &[
            (
                "app/core/clock.hy",
                "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(import time)\n(import pathlib [Path])\n(defn now [] (time.time))\n(defn size [p] (.stat (Path p)))\n(defn later [] (now))\n",
            ),
            ("app/foundation/clock.hy", "(val MODULE-TAGS {:context \"io\" :role \"foundation\"})\n(import time)\n(defn now [] (time.time))\n(defn relay [] (now))\n"),
            // 鳴らない例(DOEFF107): 生の副作用に届かない呼びの連なりと、許された層の中の経由。
            ("app/core/sum.hy", "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn add [a b] (+ a b))\n(defn total [xs] (add 1 2))\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    // DOEFF107 は生の副作用へ届く経由(core の later → now)だけに出て、届かない total と foundation の relay には出ない。
    let via_keys = keys(&report, "DOEFF107");
    assert_eq!(via_keys.len(), 1, "{:?}", via_keys);
    assert!(via_keys[0].starts_with("app/core/clock.hy::DOEFF107::later"), "{:?}", via_keys);
    let direct = keys(&report, "DOEFF106");
    assert!(direct.contains(&"app/core/clock.hy::DOEFF106::now::time.time".to_string()), "{:?}", direct);
    assert!(direct.iter().all(|k| k.starts_with("app/core/")), "foundation は許された層: {:?}", direct);
    let strong = violation(&report, "app/core/clock.hy::DOEFF106::now::time.time");
    assert_eq!(strong["severity"], "error");
    let weak = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF106" && v["key"].as_str().unwrap().ends_with(".stat")).unwrap();
    assert_eq!(weak["severity"], "warning");
    // 経由の証拠(全体の実行だけ)は info で経路つき。
    let via = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF107").expect("経由の証拠");
    assert_eq!(via["severity"], "info");
    assert!(via["message"].as_str().unwrap().contains("later"));
    assert!(via["message"].as_str().unwrap().contains("now"));
}

#[test]
fn environment_names_in_business_code() {
    let dir = repo(
        &[
            ("app/billing/handlers_fake.hy", "(defhandler fake-charge [] (Charge [e k] (k 1)))\n(defn helper-local [] 1)\n"),
            ("app/billing/handler_sets.hy", "(setv TRANSLATION-HANDLERS [])\n(defn wire-handlers [] [])\n(defn handlers-of [f] [])\n"),
            ("app/billing/wire_model.py", "X = 1\n"),
            ("app/billing/tests/test_fake.hy", "(defhandler fake-x [] 1)\n"),
            ("app/billing/charge.hy", "(defhandler charge-translation [] 1)\n"),
            ("app/other/local_thing.hy", "(defhandler local-x [] 1)\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF108"),
        vec![
            "app/billing/handler_sets.hy::no-environment-name::wire_handlers",
            "app/billing/handlers_fake.hy::no-environment-name",
            "app/billing/handlers_fake.hy::no-environment-name::fake_charge",
            "app/billing/wire_model.py::no-environment-name",
        ]
    );
}

#[test]
fn registry_marks_known_breaches_and_reconciling_lowers_to_info() {
    let files = [("app/core/untagged.hy", "(defn decide [x] x)\n"), ("app/core/other.hy", "(defn plan [x] x)\n")];
    let dir = repo(&files, "");
    std::fs::write(dir.path().join("registry/BREACHES/aaaaaaaaaaaa.txt"), "app/core/untagged.hy::DOEFF104\n理由 — 担い手 #1\n").unwrap();
    let (code, report) = editor(dir.path());
    let known = violation(&report, "app/core/untagged.hy::DOEFF104");
    assert_eq!(known["severity"], "warning");
    assert_eq!(known["registered"], true);
    let new = violation(&report, "app/core/other.hy::DOEFF104");
    assert_eq!(new["severity"], "error");
    assert_eq!(code, 1);

    // 1 行 1 鍵の登録簿でも同じ。照合中の規則は info(registered は登録簿どおり)で、終了コードは 0。
    let dir = repo(&files, "reconciling = [\"DOEFF104\"]\n");
    std::fs::write(dir.path().join("registry/keys.txt"), "# 既知\napp/core/other.hy::DOEFF104\n").unwrap();
    let (code, report) = editor(dir.path());
    assert_eq!(code, 0, "{}", report);
    assert_eq!(violation(&report, "app/core/other.hy::DOEFF104")["severity"], "info");
    assert_eq!(violation(&report, "app/core/other.hy::DOEFF104")["registered"], true);
    assert_eq!(violation(&report, "app/core/untagged.hy::DOEFF104")["registered"], false);
    assert_eq!(violation(&report, "app/core/untagged.hy::DOEFF104")["severity"], "info");
}

#[test]
fn editor_json_shape_rules_and_python_rule_ranges() {
    let dir = repo(
        &[
            ("app/core/good.hy", "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn f [] 1)\n"),
            ("app/billing/rel.py", "import os\nfrom .x import y\n"),
        ],
        "",
    );
    let (code, report) = editor(dir.path());
    // 版 2(#849・8f7b7201)で defk / deff の見出し signatures と束縛の型 bindings を足した。全体の実行では両方とも空の列。
    assert_eq!(report["version"], 2);
    assert!(report["root"].as_str().unwrap().starts_with('/'));
    assert_eq!(report["errors"], serde_json::json!([]));
    for field in ["violations", "modules", "rules", "errors", "signatures", "bindings"] {
        assert!(report[field].is_array(), "{}", field);
    }
    assert_eq!(report["signatures"], serde_json::json!([]));
    assert_eq!(report["bindings"], serde_json::json!([]));
    // 保存前の 1 file の実行(stdin)では、その Hy の file の見出しと束縛の型が欄ごとに出る。
    let plan = "(defk plan [x]\n  {:pre [(: x int)] :post [(: % str)] :tags {:context \"billing\" :role \"judgment\"}}\n  (<- s str (render x))\n  (val n 1)\n  s)\n(defk render [x] {:pre [(: x int)] :post [(: % str)]} (str x))\n";
    std::fs::write(dir.path().join("app/core/plan.hy"), plan).unwrap();
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/plan.hy"], Some(plan));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(single["version"], 2);
    let signature = single["signatures"].as_array().unwrap().iter().find(|s| s["name"] == "plan").expect("plan の見出し");
    assert_eq!(signature["kind"], "defk");
    assert!(signature["path"].as_str().unwrap().ends_with("app/core/plan.hy"));
    assert_eq!(signature["range"]["start"], serde_json::json!({"line": 0, "character": 6}));
    assert_eq!(signature["full_range"]["start"]["line"], 0);
    assert_eq!(signature["contract_range"]["start"]["line"], 1);
    assert_eq!(signature["params"], serde_json::json!([{"name": "x", "type": {"kind": "name", "name": "int", "definition": null}}]));
    assert_eq!(signature["answer"], serde_json::json!({"kind": "name", "name": "str", "definition": null}));
    assert_eq!(signature["absent"], false);
    assert_eq!(signature["raises"], serde_json::json!([]));
    assert!(signature["effects"]["declared"].is_null() && signature["effects"]["inferred"].is_array());
    assert_eq!(signature["effects"]["complete"], true);
    assert_eq!(signature["tags"], serde_json::json!({"context": "billing", "role": "judgment"}));
    let bindings = single["bindings"].as_array().unwrap();
    let bound = bindings.iter().find(|b| b["name"] == "s").expect("s の束縛");
    assert_eq!(bound["form"], "<-");
    assert_eq!(bound["origin"], "annotation");
    assert_eq!(bound["type"]["name"], "str");
    assert_eq!(bound["range"]["start"], serde_json::json!({"line": 2, "character": 6}));
    for field in ["form_range", "head_range", "annotation_range", "value_range"] {
        assert!(bound[field]["start"]["line"].is_number(), "{}", field);
    }
    assert_eq!(bound["absent"], false);
    assert_eq!(bound["raises"], serde_json::json!([]));
    let plain = bindings.iter().find(|b| b["name"] == "n").expect("n の束縛");
    assert_eq!(plain["form"], "val");
    assert!(plain["annotation_range"].is_null());
    assert!(plain["modifier"].is_null());
    // 定義ごとの本体の文字の行(20 節・#910)— 全体の実行では空、stdin の実行では定義ごとに行と字の役が出る。
    assert_eq!(report["bodies"], serde_json::json!([]));
    let body = single["bodies"].as_array().unwrap().iter().find(|b| b["name"] == "plan").expect("plan の本体");
    let shown: Vec<(u64, String)> = body["lines"]
        .as_array()
        .unwrap()
        .iter()
        .map(|l| {
            let text: String = l["segments"].as_array().unwrap().iter().map(|s| s["text"].as_str().unwrap()).collect();
            (l["line"].as_u64().unwrap(), text)
        })
        .collect();
    assert_eq!(shown, vec![(2, "val str s ⇐ render(x)".to_string()), (3, "val int n = 1".to_string()), (4, "s".to_string())]);
    assert_eq!(body["lines"][0]["segments"][0], serde_json::json!({"text": "val", "role": "keyword", "range": {"start": {"line": 2, "character": 3}, "end": {"line": 2, "character": 5}}, "effect": null, "definition": null}));
    // 既存の Python の規則(DOEFF016)も行の範囲と law つきで出る。
    let relative = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF016").expect("DOEFF016");
    assert_eq!(relative["law"], "absolute-imports");
    assert_eq!(relative["key"], Value::Null);
    assert_eq!(relative["range"], serde_json::json!({"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 16}}));
    for key in ["rule", "law", "adr", "severity", "path", "range", "message", "hint", "key", "registered"] {
        assert!(relative.get(key).is_some(), "欄 {} が無い", key);
    }
    assert_eq!(code, 1);
    let rules = report["rules"].as_array().unwrap();
    let unwired = rules.iter().find(|r| r["rule"] == "not-wired-yet").expect("針の無い law");
    assert_eq!(unwired["wired"], false);
    assert_eq!(unwired["adr"], "ADR-TEST");
    let core = rules.iter().find(|r| r["rule"] == "DOEFF101").unwrap();
    assert_eq!(core["wired"], true);
    assert!(core["statement"].as_str().unwrap().contains("core は intent だけを読む"));
}

#[test]
fn stdin_path_judges_unsaved_text_with_utf16_columns() {
    let dir = repo(&[("app/intent/charge.hy", INTENT_HY), ("app/foundation/io.hy", FOUNDATION_HY), ("app/core/edit.hy", "")], "");
    let path = dir.path().join("app/core/edit.hy");
    let unsaved = ";; 日本語の註 — 位置は UTF-16\n(val MODULE-TAGS {:context \"請求\" :role \"judgment\"})\n(import 日本.語 app.foundation.io [send])\n";
    let (code, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", path.to_str().unwrap()], Some(unsaved));
    assert_eq!(code, 1);
    let report: Value = serde_json::from_str(&stdout).unwrap();
    let hit = violation(&report, "app/core/edit.hy::core-imports-only-intent::app.foundation.io.send");
    // 3 行目 "(import 日本.語 app.foundation.io [" の send は UTF-16 で 32 列目から(byte では 38)。
    assert_eq!(hit["range"]["start"], serde_json::json!({"line": 2, "character": 32}));
    assert_eq!(hit["range"]["end"], serde_json::json!({"line": 2, "character": 36}));
    // path は正規化した根 + 根からの path(全体の実行と同じ形)。disk の空の file ではなく stdin の内容を判じた。
    assert_eq!(hit["path"], path.canonicalize().unwrap().to_str().unwrap());
    assert_eq!(report["modules"].as_array().unwrap().len(), 1, "1 file の実行は その file だけ");
    assert_eq!(report["modules"][0]["context"], "請求");
}

#[test]
fn exit_codes_for_arguments_and_broken_config() {
    let dir = repo(&[("app/core/good.hy", "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n")], "");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--stdin"], Some(""));
    assert_eq!(code, 2);
    assert!(stderr.contains("--path"));
    let (code, report) = editor(dir.path());
    assert_eq!(code, 0, "{}", report);
    // 鳴らない例(agora-redesign #1818): 知っている鍵だけの設定では DOEFF100 は出ない。
    assert!(keys(&report, "DOEFF100").is_empty(), "{:?}", keys(&report, "DOEFF100"));

    // この binary の知らない鍵(設定が binary より新しい・書き違い)は lint 全体を止めず、その鍵だけを読まずに DOEFF100 の warning で
    // 知らせる(agora-redesign #848 — 以前は終了コード 2 でエディタの違反の欄が空になった)。
    let newer = repo(&[], "[tool.doeff-linter.raw_side_effects.extra]\n");
    let (code, stdout, stderr) = run(newer.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 0, "{}", stderr);
    let report: serde_json::Value = serde_json::from_str(&stdout).unwrap();
    let notices: Vec<&serde_json::Value> = report["violations"].as_array().unwrap().iter().filter(|v| v["rule"] == "DOEFF100").collect();
    assert_eq!(notices.len(), 1, "{}", stdout);
    assert_eq!(notices[0]["severity"], "warning");
    assert!(notices[0]["message"].as_str().unwrap().contains("raw_side_effects.extra"), "{}", stdout);
    assert!(notices[0]["path"].as_str().unwrap().ends_with("pyproject.toml"));
    assert!(notices[0]["explanation"]["reason"].as_str().unwrap().contains(doeff_linter::BUILD_COMMIT));
    assert_eq!(report["linter"]["commit"], doeff_linter::BUILD_COMMIT);

    // 設定の名前の食い違いは黙って捨てず終了コード 2。
    let unknown_layer = tempfile::TempDir::new().unwrap();
    std::fs::write(
        unknown_layer.path().join("pyproject.toml"),
        "[tool.doeff-linter.layers]\norder = [\"core\"]\npaths = { core = \"c\" }\n[tool.doeff-linter.layers.allow_imports]\ncore = [\"nowhere\"]\n",
    )
    .unwrap();
    let (code, _, stderr) = run(unknown_layer.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("nowhere"), "{}", stderr);
}

#[test]
fn explicit_config_file_and_root() {
    let dir = repo(&[("app/core/untagged.hy", "(defn decide [x] x)\n")], "");
    // pyproject を消し、外に置いた設定 file(節の中身だけの形)を --config と --root で渡す。
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap();
    std::fs::remove_file(dir.path().join("pyproject.toml")).unwrap();
    let outside = tempfile::TempDir::new().unwrap();
    let bare = text.replace("[tool.doeff-linter]\n", "").replace("[tool.doeff-linter.", "[").replace("[[tool.doeff-linter.", "[[");
    let config_path = outside.path().join("lint.toml");
    std::fs::write(&config_path, bare).unwrap();
    let (code, stdout, stderr) = run(
        outside.path(),
        &["--output-format", "editor-json", "--no-log", "--config", config_path.to_str().unwrap(), "--root", dir.path().to_str().unwrap()],
        None,
    );
    assert_eq!(code, 1, "{}", stderr);
    let report: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(keys(&report, "DOEFF104"), vec!["app/core/untagged.hy::DOEFF104"]);
}

#[test]
fn misspelled_section_is_an_error_not_silence() {
    let dir = repo(&[], "");
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap().replace("[tool.doeff-linter.layers]", "[tool.doeff-linter.layer]");
    std::fs::write(dir.path().join("pyproject.toml"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("layer"), "{}", stderr);
}

#[test]
fn stdin_and_whole_runs_agree_on_paths_through_a_symlink() {
    let dir = repo(&[("app/core/untagged.hy", "(defn decide [x] x)\n"), ("app/billing/rel.py", "from .x import y\n")], "");
    let outside = tempfile::TempDir::new().unwrap();
    let link = outside.path().join("link");
    std::os::unix::fs::symlink(dir.path(), &link).unwrap();
    // 全体の実行(symlink の dir から)と、symlink を通した path の 1 file の実行で、同じ file の path が同じ。
    let (_, whole) = editor(&link);
    let whole_path = violation(&whole, "app/core/untagged.hy::DOEFF104")["path"].clone();
    let via_link = link.join("app/core/untagged.hy");
    let (_, stdout, _) = run(&link, &["--output-format", "editor-json", "--no-log", "--stdin", "--path", via_link.to_str().unwrap()], Some("(defn decide [x] x)\n"));
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(violation(&single, "app/core/untagged.hy::DOEFF104")["path"], whole_path);
    // path の引数で絞っても(symlink や `..` を通しても)Python の規則の違反は落ちない。
    let dotted = link.join("app/core/../billing");
    let (_, stdout, _) = run(&link, &["--output-format", "editor-json", "--no-log", dotted.to_str().unwrap()], None);
    let narrowed: Value = serde_json::from_str(&stdout).unwrap();
    assert!(narrowed["violations"].as_array().unwrap().iter().any(|v| v["rule"] == "DOEFF016"), "{}", narrowed);
    assert!(narrowed["violations"].as_array().unwrap().iter().all(|v| v["path"].as_str().unwrap().contains("/app/billing/")));
}

/// 違反の explanation(subject・reason・law_statement)を鍵で引く。
fn explanation<'a>(report: &'a Value, key: &str) -> &'a Value {
    &violation(report, key)["explanation"]
}

#[test]
fn every_layer_rule_explains_what_it_is_and_why() {
    let dir = repo(
        &[
            ("app/intent/charge.hy", INTENT_HY),
            ("app/foundation/io.hy", FOUNDATION_HY),
            (
                "app/core/bad.hy",
                "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(import app.foundation.io [send])\n(import httpx)\n(import time)\n(defn now [] (time.time))\n(defn later [] (now))\n",
            ),
            ("app/core/peer.hy", "(val MODULE-TAGS {:context \"peer\" :role \"translation\"})\n(defn f [] 1)\n"),
            ("app/core/untagged.hy", "(defn decide [x] x)\n"),
            ("app/core/empty.py", "X = 1\n"),
            ("app/intent/funcs.hy", "(val MODULE-TAGS {:context \"billing\" :role \"intent\"})\n(defk decide [x] {:pre []} x)\n"),
            ("app/billing/handlers_fake.hy", "(defhandler fake-charge [] (Charge [e k] (k 1)))\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    // DOEFF101: 主体は import 先とその層、理由は層の説明(設定)と import してよい層、law の文は逐語。
    let direction = explanation(&report, "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send");
    assert_eq!(
        direction["subject"],
        "import 先 app.foundation.io.send は層 foundation(path が app/foundation/ の下) — この file は層 core(業務の判断) — path が app/core/ の下、タグの role = judgment"
    );
    assert_eq!(
        direction["reason"],
        "層 core(業務の判断)は外の世界から最も遠い層で、業務の判断を知り、通信の手段は知らない。core が import してよいのは 層 core・層 intent だけ。import 先の層 foundation は本物の I/O を持つので、core から読むと、core が層 foundation の持つ物に触れる(模擬で handler を差し替えても、その所だけ本物に触る)。"
    );
    assert_eq!(direction["law_statement"], "core は intent だけを読む");
    // DOEFF102・104・106・107・108 と 103・105 も、主体と理由の文を持つ。
    let forbidden = explanation(&report, "app/core/bad.hy::core-imports-only-intent::httpx");
    assert!(forbidden["subject"].as_str().unwrap().starts_with("import 先 httpx(層 core で禁じた I/O の module) —"));
    assert!(forbidden["reason"].as_str().unwrap().contains("I/O は層 foundation・層 entry の handler が持ち"), "{}", forbidden["reason"]);
    let role = explanation(&report, "app/core/peer.hy::DOEFF105::translation");
    assert!(role["subject"].as_str().unwrap().contains("path とタグが食い違う(role translation はどの層の役でもない(今の role の一覧に無い))"), "{}", role["subject"]);
    assert!(role["reason"].as_str().unwrap().starts_with("role translation(翻訳の handler)は"), "{}", role["reason"]);
    assert_eq!(role["law_statement"], Value::Null, "law の無い規則は null");
    let untagged = explanation(&report, "app/core/untagged.hy::DOEFF104");
    assert!(untagged["subject"].as_str().unwrap().starts_with("定義 decide(タグ無し)"));
    assert!(explanation(&report, "app/core/empty.py::DOEFF104")["subject"].as_str().unwrap().starts_with("この module はタグを何も名乗っていない"));
    let types = explanation(&report, "app/intent/funcs.hy::DOEFF103::definitions");
    // intent には説明が無い — 層の順と規則の決まりだけで文を作る。
    assert_eq!(
        types["reason"],
        "層 intent は外の世界からの遠さの順で 2 番目の層。型の宣言だけを置く層なので、処理の中身を持つ関数と handler は置けない(別の層へ移す)。"
    );
    let raw = explanation(&report, "app/core/bad.hy::DOEFF106::now::time.time");
    assert!(raw["subject"].as_str().unwrap().starts_with("定義 now(defn)が time.time(time の生の副作用・import を通した名前か組み込みの強い証拠)に直に触る"), "{}", raw["subject"]);
    let via = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF107").unwrap();
    assert!(via["explanation"]["subject"].as_str().unwrap().starts_with("定義 later が now を通して time.time"));
    let env = explanation(&report, "app/billing/handlers_fake.hy::no-environment-name::fake_charge");
    assert_eq!(env["subject"], "定義 fake_charge(defhandler)の名(環境の語 fake を含む)");
    assert_eq!(env["law_statement"], "業務の名に環境の語が無い");
    // module の layer_reason と、最上位の layers(説明の無い層は name だけ)。
    let modules = report["modules"].as_array().unwrap();
    let peer = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("peer.hy")).unwrap();
    assert!(peer["layer_reason"].as_str().unwrap().starts_with("path とタグが食い違う"), "{}", peer["layer_reason"]);
    let good = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("app/intent/charge.hy")).unwrap();
    assert_eq!(good["layer_reason"], "path の置き場所で決めた — app/intent/ の下は層 intent。タグの role = intent もこの層の役");
    assert_eq!(
        report["layers"],
        serde_json::json!([
            {"name": "core", "summary": "業務の判断", "knows": "業務の判断", "does_not_know": "通信の手段", "question": "通信が変わっても変わらないか?"},
            {"name": "intent", "summary": null, "knows": null, "does_not_know": null, "question": null},
            {"name": "foundation", "summary": null, "knows": "本物の I/O", "does_not_know": null, "question": null},
            {"name": "entry", "summary": null, "knows": null, "does_not_know": null, "question": null}
        ])
    );
    // Python の文ごとの規則の explanation は null(契約で許す形)。
    let python = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF016");
    if let Some(python) = python {
        assert_eq!(python["explanation"], Value::Null);
    }
}

#[test]
fn text_output_carries_what_and_why_for_agents() {
    let dir = repo(&[("app/core/peer.hy", "(val MODULE-TAGS {:context \"peer\" :role \"translation\"})\n(defn f [] 1)\n")], "");
    let (code, stdout, _) = run(dir.path(), &["--no-log"], None);
    assert_eq!(code, 1);
    assert!(stdout.contains("これは: この file は層 core(業務の判断)"), "{}", stdout);
    assert!(stdout.contains("なぜ: role translation(翻訳の handler)は"), "{}", stdout);
    assert!(stdout.contains("直し方: "), "{}", stdout);
}

#[test]
fn describing_an_unknown_layer_is_a_config_error() {
    let dir = repo(&[], "");
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap() + "\n[tool.doeff-linter.layers.describe.ghost]\nsummary = \"無い層\"\n";
    std::fs::write(dir.path().join("pyproject.toml"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("layers.describe") && stderr.contains("ghost"), "{}", stderr);
}

/// service が先の形(controllers/<service>/{core,intent,protocol}/・controllers/shared/{core,intent}/・controllers/foundation/)と、
/// 層が先の形(controllers/core/)を並べた設定の repo。
fn service_repo(files: &[(&str, String)], extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let config = format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF101", "DOEFF104", "DOEFF105", "DOEFF109", "DOEFF113"]
[tool.doeff-linter.layers]
order = ["core", "intent", "protocol", "foundation", "entry"]
paths = {{ core = ["controllers/*/core", "controllers/core"], intent = ["controllers/*/intent", "controllers/intent"], protocol = ["controllers/*/protocol", "controllers/protocol"], foundation = "controllers/foundation", entry = ["controllers/*/entry", "controllers/entry"] }}
[tool.doeff-linter.layers.allow_imports]
core = ["core", "intent"]
protocol = ["protocol", "intent"]
[tool.doeff-linter.roles.by_layer]
core = ["judgment", "program", "type"]
intent = ["intent", "type"]
protocol = ["protocol"]
[tool.doeff-linter.services]
shared = ["shared"]
guarded_layers = ["core", "protocol"]
open_layers = ["intent"]
{extra}
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

/// タグの頭の行(context と role)。
fn tags(context: &str, role: &str) -> String {
    format!("(val MODULE-TAGS {{:context \"{}\" :role \"{}\"}})\n", context, role)
}

#[test]
fn service_first_layout_judges_layers_and_service_boundaries() {
    let judge = tags("billing", "judgment");
    let files = [
        ("controllers/billing/core/decide.hy", judge.clone() + "(import controllers.custody.core.lease [pick])\n(import controllers.custody.intent.borrow [Borrow])\n(import controllers.shared.core.clock [today])\n(defn f [] 1)\n"),
        ("controllers/billing/protocol/talk.hy", tags("billing", "protocol") + "(import controllers.custody.protocol.http [talk])\n(defn g [] 1)\n"),
        ("controllers/custody/core/lease.hy", tags("custody", "judgment") + "(defn pick [] 1)\n"),
        ("controllers/custody/intent/borrow.hy", tags("custody", "intent") + "(defclass Borrow [])\n"),
        ("controllers/custody/protocol/http.hy", tags("custody", "protocol") + "(defn talk [] 1)\n"),
        ("controllers/shared/core/clock.hy", tags("shared", "type") + "(defn today [] 1)\n"),
        ("controllers/core/old.hy", tags("kanban", "judgment") + "(import controllers.custody.core.lease [pick])\n(defn h [] 1)\n"),
        ("controllers/custody/core/misplaced.hy", tags("billing", "judgment") + "(defn m [] 1)\n"),
    ];
    let dir = service_repo(&files, "");
    let (_, report) = editor(dir.path());
    // 別の service の core と protocol を読むと破れ。intent と shared は読んでよい。層が先の形(service 無し)は判じない。
    assert_eq!(
        keys(&report, "DOEFF109"),
        vec![
            "controllers/billing/core/decide.hy::DOEFF109::controllers.custody.core.lease.pick",
            "controllers/billing/protocol/talk.hy::DOEFF109::controllers.custody.protocol.http.talk",
        ]
    );
    let crossing = explanation(&report, "controllers/billing/core/decide.hy::DOEFF109::controllers.custody.core.lease.pick");
    assert!(crossing["subject"].as_str().unwrap().starts_with("import 先 controllers.custody.core.lease.pick は service custody の層 core(path が controllers/custody/core/ の下) — この file は service billing の層 core — path が controllers/billing/core/ の下"), "{}", crossing["subject"]);
    assert!(crossing["reason"].as_str().unwrap().contains("service をまたいで判断や翻訳を読むと、custody の中身を変えた時に billing が壊れる。custody に頼むことは custody の intent を通す"), "{}", crossing["reason"]);
    // 層の判定は service が先の形でも効く(core の置き場の 2 つの形)。
    let modules = report["modules"].as_array().unwrap();
    let decide = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("billing/core/decide.hy")).unwrap();
    assert_eq!(decide["layer"], "core");
    assert_eq!(decide["service"], "billing");
    assert!(decide["layer_reason"].as_str().unwrap().contains("controllers/billing/core/ の下は service billing の層 core"), "{}", decide["layer_reason"]);
    let old = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("controllers/core/old.hy")).unwrap();
    assert_eq!(old["layer"], "core");
    assert_eq!(old["service"], Value::Null);
    // :context と dir の service の食い違いは info の知らせ。shared は見ない。
    assert_eq!(keys(&report, "DOEFF113"), vec!["controllers/custody/core/misplaced.hy::DOEFF113::billing"]);
    assert_eq!(violation(&report, "controllers/custody/core/misplaced.hy::DOEFF113::billing")["severity"], "info");

    // 例外に書いた組は読んでよい。
    let dir = service_repo(&files, "exceptions = [{ from = \"billing\", to = \"custody\" }]\n");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF109").is_empty());
}

#[test]
fn named_paths_judge_definitions_only_in_the_named_files() {
    // agora-redesign #1418: 命令の行で path を名指した実行(commit の hook)は、定義の規則を名指しの下の file だけで判じる。
    // 名指しの外の file を読まないことを、読めない file(UTF-8 でない)で確かめる — 全体の実行では「読めない」を名乗る。
    let dir = definition_repo(&[("app/a.hy", "(defn helper [x] x)\n"), ("app/b.hy", "(defn other [x] x)\n")], "");
    std::fs::write(dir.path().join("app/broken.hy"), [0xff_u8, 0xfe, 0x00]).unwrap();
    let (_, whole, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    let whole: Value = serde_json::from_str(&whole).unwrap();
    assert!(whole["errors"].to_string().contains("app/broken.hy"), "{}", whole["errors"]);
    assert_eq!(keys(&whole, "DOEFF110"), vec!["app/a.hy::DOEFF110::helper", "app/b.hy::DOEFF110::other"]);

    let (_, named, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/a.hy"], None);
    let named: Value = serde_json::from_str(&named).unwrap();
    assert!(!named["errors"].to_string().contains("app/broken.hy"), "{}", named["errors"]);
    assert_eq!(keys(&named, "DOEFF110"), vec!["app/a.hy::DOEFF110::helper"]);
}

/// 定義の書き方の規則(DOEFF110〜112)だけの repo。
fn definition_repo(files: &[(&str, &str)], extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let config = format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF110", "DOEFF111", "DOEFF112"]
[tool.doeff-linter.definitions]
exclude = ["vendor/macros"]
exclude_parts = ["tests"]
[tool.doeff-linter.registry]
files = ["known.txt"]
{extra}
"#
    );
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    std::fs::write(dir.path().join("known.txt"), "").unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

#[test]
fn defn_is_forbidden_deff_needs_a_reason_and_definitions_carry_tags() {
    let source = r#"(defn helper [x] x)
(defn [do] decorated [x] x)
(eval-and-compile
  (defn expand-time [form] form))
(defk good [x] {:pre [] :tags {:context "billing" :role "judgment"}} x)
(defk untagged [x] {:pre []} x)
(defk half [x] {:tags {:context "billing"}} x)
(deff callback [x] {:tags {:context "billing" :role "judgment"}} x)  ; defk にできない: 外の library の callback
; defk にできない: 素の callable を渡す先
(deff above [x] {:tags {:context "billing" :role "judgment"}} x)
(deff bare [x] {:tags {:context "billing" :role "judgment"}} x)
(defeffect Charge "請求" {:fields [amount] :answer int})
"#;
    let dir = definition_repo(
        &[("app/billing/logic.hy", source), ("vendor/macros/m.hy", "(defn m [] 1)\n"), ("app/billing/tests/t.hy", "(defn t [] 1)\n")],
        "",
    );
    let (code, report) = editor(dir.path());
    assert_eq!(code, 1);
    // defn は違反(decorator つきも)。eval-and-compile の中と、除いた置き場・検は外。
    assert_eq!(keys(&report, "DOEFF110"), vec!["app/billing/logic.hy::DOEFF110::decorated", "app/billing/logic.hy::DOEFF110::helper"]);
    // 理由の註は同じ行か直前の行。
    assert_eq!(keys(&report, "DOEFF111"), vec!["app/billing/logic.hy::DOEFF111::bare"]);
    // タグ必須: :tags が無い・必須の鍵が欠けた・defeffect。
    assert_eq!(
        keys(&report, "DOEFF112"),
        vec!["app/billing/logic.hy::DOEFF112::Charge", "app/billing/logic.hy::DOEFF112::half", "app/billing/logic.hy::DOEFF112::untagged"]
    );
    let defn = explanation(&report, "app/billing/logic.hy::DOEFF110::helper");
    assert_eq!(defn["subject"], "定義 helper(defn)");
    assert!(defn["reason"].as_str().unwrap().starts_with("defn は契約の辞書を持てず、:tags を書けない"));
    assert!(violation(&report, "app/billing/logic.hy::DOEFF110::helper")["hint"].as_str().unwrap().starts_with("defk にする(素の関数でなければならない理由が見当たらない)"));
    let half = explanation(&report, "app/billing/logic.hy::DOEFF112::half");
    assert_eq!(half["subject"], "定義 half(defk)の :tags に role が無い");
    let untagged = explanation(&report, "app/billing/logic.hy::DOEFF112::untagged");
    assert_eq!(untagged["subject"], "定義 untagged(defk)の :tags に context・role が無い(契約の辞書に :tags そのものが無い)");

    // module の頭のタグで補える(module_default = true・既定)/ 補えない(false)。登録簿の鍵は warning。
    let with_module = "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defk untagged [x] {:pre []} x)\n";
    let dir = definition_repo(&[("app/a.hy", with_module)], "");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF112").is_empty());
    let dir = definition_repo(&[("app/a.hy", with_module)], "[tool.doeff-linter.tags]\nmodule_default = false\n");
    std::fs::write(dir.path().join("known.txt"), "app/a.hy::DOEFF112::untagged\n").unwrap();
    let (code, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/a.hy::DOEFF112::untagged")["severity"], "warning");
    assert_eq!(violation(&report, "app/a.hy::DOEFF112::untagged")["registered"], true);
    assert_eq!(code, 0);
}

#[test]
fn defrecord_header_tags_are_read_and_required_only_when_configured() {
    // doeff-hy の defrecord の頭の辞書 {:tags … :check […]}(agora-redesign #798)。頭の辞書は省ける形なので、
    // 既定の DOEFF112 は defrecord を数えない。require_on に足した repo では頭の辞書の :tags を必須にする。
    let source = r#"(defrecord ChatId "chat の id" {:tags {:context "chat" :role "type"} :check [(ok? value)]} #^ str value)
(defrecord Span {:check [(<= start end)]} #^ int start #^ int end)
(defrecord Plain #^ str key)
"#;
    let dir = definition_repo(&[("app/chat/model.hy", source)], "");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF112").is_empty());
    let dir = definition_repo(
        &[("app/chat/model.hy", source)],
        "[tool.doeff-linter.tags]\nrequire_on = [\"defk\", \"deff\", \"defrecord\"]\n",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF112"), vec!["app/chat/model.hy::DOEFF112::Plain", "app/chat/model.hy::DOEFF112::Span"]);
}

#[test]
fn defwire_header_tags_are_read_like_defrecord() {
    // doeff-hy の defwire(JSON の境目の型 — agora-redesign #840)は defrecord へ展開する。頭の辞書の :tags は定義のタグで、
    // :names と :unknown は wire の形の鍵(タグではない)。require_on に defwire を足した repo ではタグの無い defwire を名指す。
    let source = r#"(defwire LandingRow "台帳の 1 行" {:tags {:context "chat" :role "type"} :names :camel :unknown :reject} #^ str lane-id)
(defwire Bare {:names :camel} #^ str key)
"#;
    let dir = definition_repo(&[("app/chat/model.hy", source)], "");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF112").is_empty());
    let dir = definition_repo(
        &[("app/chat/model.hy", source)],
        "[tool.doeff-linter.tags]\nrequire_on = [\"defk\", \"deff\", \"defwire\"]\n",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF112"), vec!["app/chat/model.hy::DOEFF112::Bare"]);
}

#[test]
fn registered_severity_per_rule_and_registry_relative_to_the_config_file() {
    let source = "(defn old [x] x)\n(defn new-one [x] x)\n";
    // 設定 file と登録簿を repo の外の同じ dir に置き、registry.config_files で設定 file からの相対で読む。
    let dir = definition_repo(&[("app/a.hy", source)], "");
    std::fs::remove_file(dir.path().join("pyproject.toml")).unwrap();
    let outside = tempfile::TempDir::new().unwrap();
    std::fs::write(outside.path().join("known.txt"), "app/a.hy::DOEFF110::old\n").unwrap();
    let config = r#"
[tool.doeff-linter]
enable = ["DOEFF110"]
[tool.doeff-linter.definitions]
[tool.doeff-linter.registry]
config_files = ["known.txt"]
[tool.doeff-linter.rules.DOEFF110]
registered_severity = "info"
"#;
    let config_path = outside.path().join("lint.toml");
    std::fs::write(&config_path, config).unwrap();
    let args = ["--output-format", "editor-json", "--no-log", "--config", config_path.to_str().unwrap(), "--root", dir.path().to_str().unwrap()];
    let (code, stdout, stderr) = run(dir.path(), &args, None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}", e, stderr));
    // 登録簿に載った破れは設定の重さ(info)、載っていない破れは今どおり error。
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["severity"], "info");
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["registered"], true);
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::new_one")["severity"], "error");
    assert_eq!(code, 1);
    // 知らない重さは設定の誤り。
    std::fs::write(&config_path, config.replace("\"info\"", "\"loud\"")).unwrap();
    let (code, _, stderr) = run(dir.path(), &args, None);
    assert_eq!(code, 2);
    assert!(stderr.contains("registered_severity"), "{}", stderr);
}

#[test]
fn level_is_declared_per_rule_and_registry_lowers_only_the_severity() {
    // 重大さ(level)は repo の宣言で、登録簿で重さ(severity)を下げても下げない — エディタが「手つかずの critical」を数える軸。
    let source = "(defn old [x] x)\n(defn new-one [x] x)\n";
    let config = r#"
[tool.doeff-linter.rules.DOEFF110]
registered_severity = "info"
level = "critical"
"#;
    let dir = definition_repo(&[("app/a.hy", source)], config);
    std::fs::write(dir.path().join("known.txt"), "app/a.hy::DOEFF110::old\n").unwrap();
    let (_, report) = editor(dir.path());
    let old = violation(&report, "app/a.hy::DOEFF110::old");
    assert_eq!(old["severity"], "info");
    assert_eq!(old["base_severity"], "error");
    assert_eq!(old["standing"], "registered");
    assert_eq!(old["level"], "critical");
    let fresh = violation(&report, "app/a.hy::DOEFF110::new_one");
    assert_eq!(fresh["severity"], "error");
    assert_eq!(fresh["standing"], "new");
    assert_eq!(fresh["level"], "critical");
    // 宣言も既定の表も無い規則は規則そのものの重さから(error = major)。
    let dir = definition_repo(&[("app/a.hy", source)], "");
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["level"], "major");
    // 既定の表の規則も repo の宣言が勝つ(既定と違う所だけを書く)。
    let dir = repo(
        &[("app/foundation/io.hy", FOUNDATION_HY), ("app/core/bad.hy", "(import app.foundation.io [send])\n(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn decide [x] x)\n")],
        "[tool.doeff-linter.rules.DOEFF101]\nlevel = \"major\"\n",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send")["level"], "major");
    // 知らない重大さは設定の誤り。
    let dir = definition_repo(&[("app/a.hy", source)], &config.replace("\"critical\"", "\"loud\""));
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("rules.DOEFF110.level"), "{}", stderr);
}

/// architecture.hy を repo の根に置いた repo(TOML には規則の入り切りだけ)。
fn architecture_repo(files: &[(&str, String)], toml_extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let architecture = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "業務の判断" :roles [judgment type] :imports [core intent])
           (layer intent :summary "要求の型" :roles [intent type] :imports [intent] :types-only True)
           (layer foundation :summary "汎用の I/O" :roles [foundation] :imports [foundation])]
  :shared "shared"
  :foundation foundation)
(defservice billing "請求" {:depends-on [custody ledger] :layers [core intent]})
(defservice custody "預かり所" {:layers [core intent]})
(defservice ledger "台帳" {:layers [core intent]})
"#;
    std::fs::write(dir.path().join("architecture.hy"), architecture).unwrap();
    let toml = format!(
        "[tool.doeff-linter]\nenable = [\"DOEFF101\", \"DOEFF104\", \"DOEFF105\", \"DOEFF113\", \"DOEFF114\", \"DOEFF115\", \"DOEFF116\", \"DOEFF117\"]\n{}",
        toml_extra
    );
    std::fs::write(dir.path().join("pyproject.toml"), toml).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

#[test]
fn architecture_declares_services_layers_and_dependencies() {
    let files = [
        ("app/billing/core/decide.hy", tags("billing", "judgment") + "(import app.custody.intent.lease [Lease])\n(import app.custody.core.pick [pick])\n(import app.shared.core.clock [today])\n(defn f [] 1)\n"),
        ("app/billing/intent/charge.hy", tags("billing", "intent") + "(defclass Charge [])\n"),
        ("app/billing/core/wrong_context.hy", tags("custody", "judgment") + "(defn g [] 1)\n"),
        ("app/billing/helpers.hy", tags("billing", "judgment") + "(defn h [] 1)\n"),
        ("app/billing/scripts/tool.hy", tags("billing", "judgment") + "(defn t [] 1)\n"),
        ("app/billing/scripts/tool2.hy", tags("billing", "judgment") + "(defn t2 [] 1)\n"),
        ("app/custody/core/pick.hy", tags("custody", "judgment") + "(defn pick [] 1)\n"),
        ("app/custody/intent/lease.hy", tags("custody", "intent") + "(defclass Lease [])\n"),
        ("app/custody/core/uses_billing.hy", tags("custody", "judgment") + "(import app.billing.intent.charge [Charge])\n(defn u [] 1)\n"),
        ("app/shared/core/clock.hy", tags("shared", "type") + "(defn today [] 1)\n"),
        ("app/foundation/io.hy", tags("io", "foundation") + "(defn send [] 1)\n"),
        ("app/old/anything.hy", "(defn legacy [] 1)\n".to_string()),
        ("app/core/legacy_core.hy", tags("kanban", "judgment") + "(import app.foundation.io [send])\n(defn l [] 1)\n"),
        ("app/mystery/core/x.hy", tags("mystery", "judgment") + "(defn x [] 1)\n"),
        ("app/top.hy", "(defn top [] 1)\n".to_string()),
    ];
    let dir = architecture_repo(&files, "");
    let (code, report) = editor(dir.path());
    assert_eq!(code, 1, "{}", report);
    // DOEFF114: 宣言の外の module は全部(root の直下・service の dir の直下・宣言に無い dir の中 — 層が先の dir も旧い dir も例外なし)。
    assert_eq!(
        keys(&report, "DOEFF114"),
        vec![
            "app/billing/helpers.hy::DOEFF114",
            "app/billing/scripts/tool.hy::DOEFF114",
            "app/billing/scripts/tool2.hy::DOEFF114",
            "app/core/legacy_core.hy::DOEFF114",
            "app/mystery/core/x.hy::DOEFF114",
            "app/old/anything.hy::DOEFF114",
            "app/top.hy::DOEFF114"
        ]
    );
    // DOEFF115: 宣言に無い service の dir と、service の中の宣言に無い層の dir(dir ごとに 1 件)。
    assert_eq!(keys(&report, "DOEFF115"), vec!["app/billing/scripts::DOEFF115", "app/core::DOEFF115", "app/mystery::DOEFF115", "app/old::DOEFF115"]);
    // 移し先の案: service は :context のタグ、層は :role のタグ(推せなければ今の path の段の層の名)。
    assert_eq!(
        violation(&report, "app/core/legacy_core.hy::DOEFF114")["hint"],
        "app/kanban/core/legacy_core.hy へ移す(service は :context のタグ、層は定義の :role のタグから推した案 — 推せない時だけ今の置き場所の層)"
    );
    assert!(violation(&report, "app/billing/scripts/tool.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("app/billing/core/tool.hy へ移す"));
    assert!(violation(&report, "app/old/anything.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("app/<service>/<層>/anything.hy へ移す"));
    let undeclared = explanation(&report, "app/mystery::DOEFF115");
    assert!(undeclared["subject"].as_str().unwrap().contains("service mystery は architecture.hy に宣言されていない"), "{}", undeclared["subject"]);
    // DOEFF116: 依存先の intent 以外を読む・:depends-on に無い service を読む。shared と foundation は service ではない。
    assert_eq!(
        keys(&report, "DOEFF116"),
        vec!["app/billing/core/decide.hy::DOEFF116::app.custody.core.pick.pick", "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge"]
    );
    let undeclared_dependency = explanation(&report, "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge");
    assert!(undeclared_dependency["reason"].as_str().unwrap().contains("service custody の依存の宣言(:depends-on = 無し)に billing が無い"), "{}", undeclared_dependency["reason"]);
    // DOEFF117(info): billing は ledger に依存すると宣言したが読んでいない。位置は architecture.hy。
    let unused = violation(&report, "architecture.hy::DOEFF117::billing>ledger");
    // 鳴らない例: billing が読んでいる依存 custody には出ない(宣言して使っていない ledger の 1 件だけ)。
    assert_eq!(keys(&report, "DOEFF117"), vec!["architecture.hy::DOEFF117::billing>ledger"]);
    assert_eq!(unused["severity"], "info");
    assert!(unused["path"].as_str().unwrap().ends_with("architecture.hy"));
    // DOEFF113: 宣言した service の中の :context の食い違いは warning。
    assert_eq!(violation(&report, "app/billing/core/wrong_context.hy::DOEFF113::custody")["severity"], "warning");
    // 宣言の外の module は :role のタグから層を推して層の規則を受ける(role judgment → core・core から foundation を読むと DOEFF101)。
    assert_eq!(keys(&report, "DOEFF101"), vec!["app/core/legacy_core.hy::DOEFF101::app.foundation.io.send"]);
    let module = report["modules"].as_array().unwrap().iter().find(|m| m["path"].as_str().unwrap().ends_with("app/core/legacy_core.hy")).unwrap();
    assert!(module["layer_reason"].as_str().unwrap().starts_with("タグで決めた — app/core/ は architecture.hy の宣言した置き場所ではない"), "{}", module["layer_reason"]);
    // 既存の分は登録簿に載せても、registered_severity = warning なら黄で見え続ける。
    let dir = architecture_repo(&files, "[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n[tool.doeff-linter.rules.DOEFF114]\nregistered_severity = \"warning\"\n");
    std::fs::write(dir.path().join("known.txt"), "app/old/anything.hy::DOEFF114\n").unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/old/anything.hy::DOEFF114")["severity"], "warning");
    assert_eq!(violation(&report, "app/top.hy::DOEFF114")["severity"], "error");
    // :legacy は廃止 — 書くと設定の誤り。
    std::fs::write(dir.path().join("architecture.hy"), "(defarchitecture s :root \"app\" :layers [(layer core)] :legacy [\"app/old\"])\n").unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains(":legacy は廃止した"), "{}", stderr);
    // editor-json の architecture(service の一覧と層の宣言)。
    let architecture = &report["architecture"];
    assert_eq!(architecture["name"], "sample");
    assert_eq!(architecture["services"][0]["name"], "billing");
    assert_eq!(architecture["services"][0]["description"], "請求");
    assert_eq!(architecture["services"][0]["depends_on"], serde_json::json!(["custody", "ledger"]));
    assert_eq!(architecture["layers"][0]["summary"], "業務の判断");
    assert_eq!(report["layers"][0]["name"], "core");
}

/// #1799: DOEFF101 と DOEFF116 は同じ import を二重に数えない(層の向きを DOEFF101 が持つ)。DOEFF116 の案内は依存が輪になる時に
/// 「宣言に足す」と言わない。DOEFF114 の移し先の層は定義の :role から推す(今の置き場所の層は推せない時だけ)。
#[test]
fn dependency_and_place_rules_do_not_double_count_or_misguide() {
    let files = [
        // intent の層から別の service の core を読む — 層の向き(DOEFF101)で 1 件。宣言に無い依存(DOEFF116)でも数えない。
        ("app/custody/intent/peek.hy", tags("custody", "intent") + "(import app.billing.core.rate [rate])\n(defclass Peek [])\n"),
        ("app/billing/core/rate.hy", tags("billing", "judgment") + "(defn rate [] 1)\n"),
        // billing は custody に依存する — custody が billing を読むのを :depends-on に足すと輪になる。
        ("app/billing/intent/charge.hy", tags("billing", "intent") + "(defclass Charge [])\n"),
        ("app/custody/core/uses_billing.hy", tags("custody", "judgment") + "(import app.billing.intent.charge [Charge])\n(defn u [] 1)\n"),
        // ledger は custody に依存しない — 足す案内のまま。
        ("app/ledger/intent/entry.hy", tags("ledger", "intent") + "(defclass Entry [])\n"),
        ("app/custody/core/uses_ledger.hy", tags("custody", "judgment") + "(import app.ledger.intent.entry [Entry])\n(defn v [] 1)\n"),
        // 層が先の dir の intent/ に置いた判断 — 移し先の層は role(judgment → core)。role が 2 つの層に合う時だけ path の intent。
        ("app/intent/judge.hy", tags("kanban", "judgment") + "(defn j [] 1)\n"),
        ("app/intent/shape.hy", tags("kanban", "type") + "(defclass Shape [])\n"),
    ];
    let dir = architecture_repo(&files, "");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF101"), vec!["app/custody/intent/peek.hy::DOEFF101::app.billing.core.rate.rate"]);
    assert_eq!(
        keys(&report, "DOEFF116"),
        vec![
            "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge",
            "app/custody/core/uses_ledger.hy::DOEFF116::app.ledger.intent.entry.Entry"
        ]
    );
    let cyclic = violation(&report, "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge")["hint"].as_str().unwrap().to_string();
    assert!(cyclic.contains("輪になる") && cyclic.contains("custody の intent か shared へ移し") && !cyclic.contains(":depends-on に足し、"), "{}", cyclic);
    let plain = violation(&report, "app/custody/core/uses_ledger.hy::DOEFF116::app.ledger.intent.entry.Entry")["hint"].as_str().unwrap().to_string();
    assert!(plain.starts_with("依存先を :depends-on に足し"), "{}", plain);
    assert!(violation(&report, "app/intent/judge.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("app/kanban/core/judge.hy へ移す"));
    assert!(violation(&report, "app/intent/shape.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("app/kanban/intent/shape.hy へ移す"));
    // DOEFF101 を切ると、同じ import は DOEFF116 が数える(二重を避けるのは DOEFF101 が判じる時だけ)。
    let dir = architecture_repo(&files, "");
    let toml = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap().replace("\"DOEFF101\", ", "");
    std::fs::write(dir.path().join("pyproject.toml"), toml).unwrap();
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF116").contains(&"app/custody/intent/peek.hy::DOEFF116::app.billing.core.rate.rate".to_string()));
}

#[test]
fn architecture_misreadings_and_double_declarations_are_config_errors() {
    let dir = architecture_repo(&[], "");
    std::fs::write(dir.path().join("architecture.hy"), "(defarchitecture s :root \"app\" :layers [(layer core)])\n(defservice a {:layers [ghost]})\n(defservice a {})\n").unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("ghost") && stderr.contains("service a が 2 度") && stderr.contains("architecture.hy:3:1"), "{}", stderr);
    // 層を TOML と architecture.hy の両方で宣言するのは誤り(宣言は 1 か所)。
    let dir = architecture_repo(&[], "[tool.doeff-linter.layers]\norder = [\"core\"]\npaths = { core = \"app/core\" }\n");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("二重の宣言"), "{}", stderr);
    // architecture.hy が無い repo は今どおり(editor-json の architecture は null)。
    let (_, report) = editor(repo(&[], "").path());
    assert_eq!(report["architecture"], Value::Null);
}

/// 理由の種類を宣言した architecture.hy と定義の規則の repo。
fn reason_repo(source: &str, registry: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture s :root "app" :layers [(layer core)]
  :plain-callable-reasons [(reason library-callback "外の library が素の関数として呼ぶ(sorted の key・dataclass の hook)")
                           (reason process-entry "process の入口の main")])
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF110\", \"DOEFF111\"]\n[tool.doeff-linter.definitions]\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("known.txt"), registry).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/x.hy"), source).unwrap();
    dir
}

#[test]
fn deff_reasons_are_free_text_and_only_missing_empty_or_ditto_is_an_error() {
    let source = r#"(deff by-key [row] (get row "k"))  ; defk にできない: sorted の key が素の関数で呼ぶ
(deff kinded [row] row)  ; defk にできない(library-callback): dataclass の __post_init__ が呼ぶ
(deff odd-kind [row] row)  ; defk にできない(handler-assembly): handler の組を組む
(deff same [row] row)  ; defk にできない: 同上
(deff same2 [row] row)  ; defk にできない: 上と同じ(sorted の key)
(deff empty [row] row)  ; defk にできない:
(deff registered-same [row] row)  ; defk にできない: 同上
(deff bare [row] row)
(defn main [] 1)  ; defk にできない(library-callback): sorted の key
(defn helper [] 1)
"#;
    let dir = reason_repo(source, "app/core/x.hy::DOEFF111::registered_same\n");
    let (_, report) = editor(dir.path());
    // 決定的に出すのは註が無い・理由が空・「同上」とその変形だけ。種類の札は要求しない(一覧に無い種類も受け付ける)。
    assert_eq!(
        keys(&report, "DOEFF111"),
        vec![
            "app/core/x.hy::DOEFF111::bare",
            "app/core/x.hy::DOEFF111::empty",
            "app/core/x.hy::DOEFF111::registered_same",
            "app/core/x.hy::DOEFF111::same",
            "app/core/x.hy::DOEFF111::same2"
        ]
    );
    for key in ["bare", "empty", "same", "same2"] {
        assert_eq!(violation(&report, &format!("app/core/x.hy::DOEFF111::{}", key))["severity"], "error", "{}", key);
    }
    assert_eq!(violation(&report, "app/core/x.hy::DOEFF111::registered_same")["severity"], "warning");
    let same = violation(&report, "app/core/x.hy::DOEFF111::same");
    assert!(same["explanation"]["reason"].as_str().unwrap().contains("library-callback(外の library が素の関数として呼ぶ"), "{}", same["explanation"]["reason"]);
    assert!(same["hint"].as_str().unwrap().contains("「同上」は使わない"));
    // defn: 同じ行の註が一覧の種類を名乗れば「deff にする」、そうでなければ「defk にする」。
    assert!(violation(&report, "app/core/x.hy::DOEFF110::main")["hint"].as_str().unwrap().starts_with("deff にする(理由 library-callback"));
    assert!(violation(&report, "app/core/x.hy::DOEFF110::helper")["hint"].as_str().unwrap().starts_with("defk にする"));
}

#[test]
fn tests_are_deftest_only_in_the_configured_test_places() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF118\"]\n[tool.doeff-linter.definitions]\ntest_paths = [\"**/tests/**\", \"test_*.hy\"]\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("known.txt"), "app/tests/t.hy::DOEFF118::test_old\n").unwrap();
    let write = |rel: &str, text: &str| {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    };
    write(
        "app/tests/t.hy",
        "(defn test-a [] 1)\n(deff test_b [] 1)\n(defk test-c [] 1)\n(setv test-d (fn [] 1))\n(defn test-old [] 1)\n(defn helper [] 1)\n(deftest test-good [] 1)\n(setv test-value 3)\n",
    );
    write("app/core/test_top.hy", "(defn test-e [] 1)\n");
    write("app/core/logic.hy", "(defn test-f [] 1)\n");
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF118"),
        vec![
            "app/core/test_top.hy::DOEFF118::test_e",
            "app/tests/t.hy::DOEFF118::test_a",
            "app/tests/t.hy::DOEFF118::test_b",
            "app/tests/t.hy::DOEFF118::test_c",
            "app/tests/t.hy::DOEFF118::test_d",
            "app/tests/t.hy::DOEFF118::test_old"
        ]
    );
    assert_eq!(violation(&report, "app/tests/t.hy::DOEFF118::test_a")["severity"], "error");
    assert_eq!(violation(&report, "app/tests/t.hy::DOEFF118::test_old")["severity"], "warning");
    assert_eq!(violation(&report, "app/tests/t.hy::DOEFF118::test_d")["explanation"]["subject"], "定義 test_d(fn)— 検の置き場の、名が test で始まる関数");
    assert!(violation(&report, "app/tests/t.hy::DOEFF118::test_a")["hint"].as_str().unwrap().starts_with("deftest にする"));
    assert_eq!(code, 1);
}

/// DOEFF119 の repo(定義の規則の母集団 = app・既知の破れの登録簿つき)。
fn class_repo(files: &[(&str, &str)], registry: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF119\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("known.txt"), registry).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

#[test]
fn classes_are_judged_by_what_their_methods_touch_not_by_name() {
    let source = r#"(import httpx)
(import threading)
(import doeff [EffectBase])
(import app.base [Local])
(defclass [(dataclass :frozen True)] Point2D []
  (#^ float x)
  (#^ float y)
  (defn norm [self] (+ (* self.x self.x) (* self.y self.y)))
  (defn add [self other] (Point2D (+ self.x other.x) (+ self.y other.y))))
(defclass [(dataclass :frozen True)] Row []
  (#^ str key)
  (defn __post_init__ [self] (assert self.key)))
(defclass Client []
  (defn __init__ [self] (setv self.http (httpx.Client)))
  (defn fetch [self url] (.get self.http url)))
(defclass Poller []
  (defn __init__ [self] (setv self.lock (threading.Lock))))
(defclass Counter []
  (defn __init__ [self] (setv self.n 0 self.seen []))
  (defn bump [self] (+= self.n 1))
  (defn note [self x] (.append self.seen x)))
(defclass Gone [Exception])
(defclass Put [EffectBase] (defn run [self] (setv self.x 1)))
(defclass Child [Local] (defn step [self] (setv self.y 2)))
(defclass Known [] (defn tick [self] (setv self.t 1)))
"#;
    let dir = class_repo(&[("app/core/shapes.hy", source), ("app/base.hy", "(defclass Local [])\n")], "app/core/shapes.hy::DOEFF119::Known\n");
    let (code, report) = editor(dir.path());
    let found: std::collections::BTreeMap<String, String> = report["violations"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|v| v["rule"] == "DOEFF119" && v["path"].as_str().unwrap().ends_with("shapes.hy"))
        .map(|v| (v["key"].as_str().unwrap().rsplit("::").next().unwrap().to_string(), v["severity"].as_str().unwrap().to_string()))
        .collect();
    // 値の class(Point2D)・例外・外の library の基底(EffectBase)は出ない。欄だけ(Row — __post_init__ の検めだけ)は info。
    // 生の副作用に触る(Client の method・Poller の欄の初期値)は error、self の欄を書き換える(Counter・repo の中の基底の Child)は warning。
    let expected: std::collections::BTreeMap<String, String> = [
        ("Row", "info"),
        ("Client", "error"),
        ("Poller", "error"),
        ("Counter", "warning"),
        ("Child", "warning"),
        ("Known", "info"),
    ]
    .iter()
    .map(|(k, v)| (k.to_string(), v.to_string()))
    .collect();
    assert_eq!(found, expected);
    assert_eq!(code, 1);
    let client = violation(&report, "app/core/shapes.hy::DOEFF119::Client");
    assert!(client["explanation"]["subject"].as_str().unwrap().contains("外の世界に触る class(証拠 "), "{}", client["explanation"]["subject"]);
    assert!(client["explanation"]["subject"].as_str().unwrap().contains("httpx.Client"));
    assert!(client["hint"].as_str().unwrap().contains("(session val …)"));
    let counter = violation(&report, "app/core/shapes.hy::DOEFF119::Counter");
    assert!(counter["explanation"]["subject"].as_str().unwrap().contains("bump: self.n"), "{}", counter["explanation"]["subject"]);
    assert!(counter["explanation"]["subject"].as_str().unwrap().contains("note: self.seen"));
    assert!(counter["hint"].as_str().unwrap().starts_with("値は defrecord(不変)、振る舞いは新しい値を返す純粋な関数"));
    assert!(violation(&report, "app/core/shapes.hy::DOEFF119::Row")["hint"].as_str().unwrap().starts_with("defrecord にする"));
    assert_eq!(violation(&report, "app/core/shapes.hy::DOEFF119::Known")["registered"], true);
}

/// DOEFF120 の repo — architecture.hy(foundation と :wire-modules つき)・DOEFF120 だけを入れた TOML・既知の破れの登録簿。
fn json_value_repo(files: &[(&str, &str)], registry: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment type])
           (layer intent :roles [intent type])
           (layer protocol :roles [translation])
           (layer foundation :roles [foundation])]
  :foundation foundation
  :wire-modules ["app.foundation.records_client" app.billing.protocol.wire_*])
(defservice billing "請求" {:layers [core intent protocol]})
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF120\"]\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n[tool.doeff-linter.rules.DOEFF120]\nregistered_severity = \"info\"\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("known.txt"), registry).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

#[test]
fn json_value_is_allowed_only_in_parsers_and_listed_foundation_modules() {
    let files = [
        // core の Hy — import と注釈(#^)。docstring の語は数えない。
        (
            "app/billing/core/decide.hy",
            "(import doeff_hy.wire [JsonValue])\n(defk decide [#^ JsonValue payload] {:pre [] :post []}\n  \"JsonValue を受けて決める\"\n  (get payload \"k\"))\n",
        ),
        // 型の別名の文字列の中の語も数える(名 1 つ + 文字列の中の語 2 つ = 3 か所・どれも 3 行目)。
        (
            "app/billing/protocol/shape.py",
            "from typing import TypeAlias\n\nJsonValue: TypeAlias = \"dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None\"\n",
        ),
        // JsonObject(dict[str, JsonValue])も同じ逃げ道 — 文字列の注釈と、Hy の #( … ) の中。
        ("app/billing/core/obj.py", "def read(payload: \"JsonObject\") -> int:\n    return 1\n"),
        ("app/billing/intent/body.hy", "(setv Body (get dict #(str JsonObject)))\n"),
        // 文字列・註・docstring だけの言及は数えない。
        ("app/billing/core/doc_only.hy", ";; JsonValue は使わない\n(defk f [x] \"JsonValue ではなく型のある値を受ける\" x)\n"),
        ("app/billing/core/doc_only.py", "\"\"\"JsonValue の話だけ。\"\"\"\n# JsonValue\ndef f(x: int) -> int:\n    \"\"\"JsonObject とは書かない\"\"\"\n    return x\n"),
        // :wire-modules に在り foundation の層に在る → 許す。foundation でも :wire-modules に無ければ許さない。
        ("app/foundation/records_client.hy", "(import doeff_hy.wire [JsonValue])\n(defk send [#^ JsonValue body] body)\n"),
        ("app/foundation/other.py", "from doeff_hy.wire import JsonValue\n"),
        // :wire-modules の pattern に当たるが foundation の外 → 許さない(訳を説明に出す)。
        ("app/billing/protocol/wire_codec.hy", "(import doeff_hy.wire [JsonValue])\n"),
        // 組み込みの汎用の解き手(module の綴りの末尾が doeff_records.wire)は、どこに置かれても許す。
        ("vendor/doeff_records/wire.hy", "(setv JsonValue object)\n"),
        // 隠し dir の下は母集団の外。
        (".venv/lib/x.py", "JsonValue = dict\n"),
        // 登録簿に載った既知の分は registered_severity(この設定では info)。
        ("app/billing/core/known.hy", "(setv x (: payload JsonValue))\n"),
    ];
    let dir = json_value_repo(&files, "app/billing/core/known.hy::DOEFF120\n");
    let (code, report) = editor(dir.path());
    assert_eq!(code, 1, "{}", report);
    assert_eq!(
        keys(&report, "DOEFF120"),
        vec![
            "app/billing/core/decide.hy::DOEFF120",
            "app/billing/core/known.hy::DOEFF120",
            "app/billing/core/obj.py::DOEFF120",
            "app/billing/intent/body.hy::DOEFF120",
            "app/billing/protocol/shape.py::DOEFF120",
            "app/billing/protocol/wire_codec.hy::DOEFF120",
            "app/foundation/other.py::DOEFF120",
        ]
    );
    // module ごとに 1 件・位置は最初の使用・数とほかの行は説明に。
    let decide = violation(&report, "app/billing/core/decide.hy::DOEFF120");
    assert_eq!(decide["severity"], "error");
    assert_eq!(decide["range"]["start"], serde_json::json!({"line": 0, "character": 23}));
    assert_eq!(decide["explanation"]["subject"], "module app.billing.core.decide — JsonValue を 2 か所で使う(最初 1 行目・ほかに 2 行目)");
    assert!(decide["explanation"]["reason"].as_str().unwrap().ends_with("この module は :wire-modules に無い。"), "{}", decide["explanation"]["reason"]);
    assert!(decide["hint"].as_str().unwrap().starts_with("JSON の形を defwire で型に起こし"), "{}", decide["hint"]);
    let shape = violation(&report, "app/billing/protocol/shape.py::DOEFF120");
    assert_eq!(shape["severity"], "error");
    assert_eq!(shape["explanation"]["subject"], "module app.billing.protocol.shape — JsonValue を 3 か所で使う(すべて 3 行目)");
    assert_eq!(violation(&report, "app/billing/core/obj.py::DOEFF120")["explanation"]["subject"], "module app.billing.core.obj — JsonObject を 1 か所で使う(1 行目)");
    let listed = violation(&report, "app/billing/protocol/wire_codec.hy::DOEFF120");
    assert_eq!(listed["severity"], "error");
    assert!(
        listed["explanation"]["reason"].as_str().unwrap().contains(":wire-modules の app.billing.protocol.wire_* に当たるが、foundation の層(app/foundation/)に無いので許さない"),
        "{}",
        listed["explanation"]["reason"]
    );
    assert!(listed["hint"].as_str().unwrap().starts_with("送受信そのものを app/foundation/ の module へ移して"), "{}", listed["hint"]);
    let known = violation(&report, "app/billing/core/known.hy::DOEFF120");
    assert_eq!(known["severity"], "info");
    assert_eq!(known["registered"], true);

    // 保存前の 1 file(stdin)も同じ判定。許された module は何も出さない。
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/billing/core/decide.hy"], Some(files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}", e, stderr));
    assert_eq!(keys(&single, "DOEFF120"), vec!["app/billing/core/decide.hy::DOEFF120"]);
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/foundation/records_client.hy"], Some(files[6].1));
    let allowed: Value = serde_json::from_str(&stdout).unwrap();
    assert!(keys(&allowed, "DOEFF120").is_empty(), "{}", allowed);
}

/// 臭いの規則(DOEFF121〜125)の repo — 層 core と protocol、定義の規則の母集団、臭いの設定、登録簿。
fn smell_repo(files: &[(&str, &str)], extra: &str, registry: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let config = format!(
        r#"
[tool.doeff-linter]
enable = ["DOEFF121", "DOEFF122", "DOEFF123", "DOEFF124", "DOEFF125"]
[tool.doeff-linter.layers]
order = ["core", "protocol"]
paths = {{ core = "app/core", protocol = "app/protocol" }}
[tool.doeff-linter.definitions]
paths = ["app"]
[tool.doeff-linter.smells]
shape_check_layers = ["core"]
[tool.doeff-linter.registry]
files = ["known.txt"]
{extra}
"#
    );
    std::fs::write(dir.path().join("pyproject.toml"), config).unwrap();
    std::fs::write(dir.path().join("known.txt"), registry).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// decide-tag(agora-controllers controllers/kanban/core/tag_judgment.hy)を縮めた形 — 5 つの臭いを全部持つ。
const DECIDE_TAG: &str = r#"(val MODULE-TAGS {:context "kanban" :role "judgment"})
(import app.core.rules [Refusal TagsAccepted])
(defk decide-tag [intent board]
  {:tags {:context "kanban" :role "judgment"}}
  (val payload intent.payload)
  (val subject (.get payload "subject"))
  (when (not (and (isinstance subject str) (!= subject "")))
    (<- bad-subject WritePlan (rejected "payload-invalid: subject"))
    (return bad-subject))
  (<- add-said (| TagsAccepted Refusal) (tags-verdict (.get payload "add")))
  (match add-said
    (Refusal) (do (<- add-refused WritePlan (rejected (+ add-said.reason ": " add-said.detail)))
                  (return add-refused))
    (TagsAccepted) None)
  (var writes #())
  (for [word add-said.words]
    (:= writes (+ writes #((AttachTag :tag word)))))
  (WritePlan :writes writes))
"#;

#[test]
fn smells_are_found_in_business_code_with_failure_types_from_declarations() {
    let rules = "(defrecord Refusal \"断り\" {:failure True} #^ str reason #^ str detail)\n(defrecord TagsAccepted #^ tuple words)\n";
    let files = [("app/core/rules.hy", rules), ("app/core/tag_judgment.hy", DECIDE_TAG), ("app/protocol/tags.hy", DECIDE_TAG)];
    let dir = smell_repo(&files, "", "");
    let (code, report) = editor(dir.path());
    // DOEFF121 は判断の層(core)の file だけ。DOEFF122〜125 は業務の file の全部。
    assert_eq!(keys(&report, "DOEFF121"), vec!["app/core/tag_judgment.hy::DOEFF121::decide_tag::subject"]);
    assert_eq!(
        keys(&report, "DOEFF122"),
        vec!["app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said", "app/protocol/tags.hy::DOEFF122::decide_tag::add_said"]
    );
    assert_eq!(
        keys(&report, "DOEFF123"),
        vec![
            "app/core/tag_judgment.hy::DOEFF123::decide_tag::add_refused",
            "app/core/tag_judgment.hy::DOEFF123::decide_tag::bad_subject",
            "app/protocol/tags.hy::DOEFF123::decide_tag::add_refused",
            "app/protocol/tags.hy::DOEFF123::decide_tag::bad_subject"
        ]
    );
    assert_eq!(keys(&report, "DOEFF124").len(), 2);
    assert_eq!(keys(&report, "DOEFF125"), vec!["app/core/tag_judgment.hy::DOEFF125::decide_tag::writes", "app/protocol/tags.hy::DOEFF125::decide_tag::writes"]);
    // 重さの既定は warning(Absent / Raise が本線に入った後 — 終了コード 0)。説明と直し方。
    assert!(report["violations"].as_array().unwrap().iter().filter(|v| v["rule"].as_str().unwrap().starts_with("DOEFF12")).all(|v| v["severity"] == "warning"));
    assert_eq!(code, 0);
    let rethrow = violation(&report, "app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said");
    assert!(rethrow["explanation"]["subject"].as_str().unwrap().contains("失敗の型 Refusal を受け"), "{}", rethrow["explanation"]["subject"]);
    assert!(rethrow["hint"].as_str().unwrap().contains("(<- (Raise 失敗の値))"));
    let shape = violation(&report, "app/core/tag_judgment.hy::DOEFF121::decide_tag::subject");
    assert!(shape["hint"].as_str().unwrap().contains("defwire"));
    assert_eq!(shape["range"]["start"]["line"], 5);

    // 失敗の型の宣言が無ければ DOEFF122 は出ない(名前で決め打ちしない)。
    let plain = "(defrecord Refusal \"断り\" #^ str reason #^ str detail)\n(defrecord TagsAccepted #^ tuple words)\n";
    let dir = smell_repo(&[("app/core/rules.hy", plain), ("app/core/tag_judgment.hy", DECIDE_TAG)], "", "");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF122").is_empty());
    // defeffect の :failure の宣言も失敗の型になる。
    let effect = "(import app.core.rules [Refusal TagsAccepted])\n(defeffect Check \"検め\" {:fields [x] :answer (| TagsAccepted Refusal) :failure [Refusal]})\n";
    let dir = smell_repo(&[("app/core/rules.hy", plain), ("app/core/effects.hy", effect), ("app/core/tag_judgment.hy", DECIDE_TAG)], "", "");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said"]);
    // 同じ名の型が別の module にあれば、宣言した方だけが失敗の型(webapp の Refusal は宣言が無い)。
    let webapp = DECIDE_TAG.replace("(import app.core.rules [Refusal TagsAccepted])", "(import app.webapp.model [Refusal TagsAccepted])");
    let dir = smell_repo(
        &[("app/core/rules.hy", rules), ("app/webapp/model.hy", plain), ("app/core/tag_judgment.hy", DECIDE_TAG), ("app/core/webapp_tag.hy", &webapp)],
        "",
        "",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said"]);

    // 登録簿に載った分は info、新しい分は warning。設定の severity で info に下げられる。
    let dir = smell_repo(&files, "", "app/core/tag_judgment.hy::DOEFF125::decide_tag::writes\n");
    let (code, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/core/tag_judgment.hy::DOEFF125::decide_tag::writes")["severity"], "info");
    assert_eq!(violation(&report, "app/protocol/tags.hy::DOEFF125::decide_tag::writes")["severity"], "warning");
    assert_eq!(code, 0);
    let lower = "[tool.doeff-linter.rules.DOEFF125]\nseverity = \"info\"\n";
    let dir = smell_repo(&files, lower, "");
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/protocol/tags.hy::DOEFF125::decide_tag::writes")["severity"], "info");
    assert_eq!(violation(&report, "app/protocol/tags.hy::DOEFF124::decide_tag::add_said")["severity"], "warning");
    // 臭いの規則でない ID と error の重さは設定の誤り。
    for bad in ["[tool.doeff-linter.rules.DOEFF101]\nseverity = \"warning\"\n", "[tool.doeff-linter.rules.DOEFF121]\nseverity = \"error\"\n"] {
        let dir = smell_repo(&files, bad, "");
        let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
    }
    // 知らない層の名も設定の誤り。
    let dir = smell_repo(&files, "", "");
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap().replace("shape_check_layers = [\"core\"]", "shape_check_layers = [\"ghost\"]");
    std::fs::write(dir.path().join("pyproject.toml"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("ghost"), "{}", stderr);
}

#[test]
fn smells_have_clean_counterparts_and_run_on_a_single_file() {
    // 型のある値の判断・Raise で出す失敗・内包表記の蓄え・使い回す名 — どれも当たらない。
    let clean = r#"(defk decide [intent board]
  {:tags {:context "kanban" :role "judgment"}}
  (<- said (| TagsAccepted Refusal) (tags-verdict intent.add))
  (match said
    (Refusal) (<- (Raise said))
    (TagsAccepted) None)
  (<- plan WritePlan (build said))
  (log plan)
  (val writes (tuple (lfor word said.words (AttachTag :tag word))))
  (return plan))
"#;
    let rules = "(defrecord Refusal {:failure True} #^ str reason)\n";
    let dir = smell_repo(&[("app/core/rules.hy", rules), ("app/core/clean.hy", clean)], "", "");
    let (_, report) = editor(dir.path());
    assert!(report["violations"].as_array().unwrap().iter().all(|v| !v["rule"].as_str().unwrap().starts_with("DOEFF12")), "{}", report["violations"]);
    // 1 file の実行(エディタの保存)でも、repo の宣言から失敗の型を読む。
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/clean.hy"], Some(DECIDE_TAG));
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/clean.hy::DOEFF122::decide_tag::add_said"]);
    assert_eq!(keys(&report, "DOEFF121"), vec!["app/core/clean.hy::DOEFF121::decide_tag::subject"]);
}

#[test]
fn only_the_assembly_layer_may_read_a_dependency_protocol() {
    // operator 2026-09-28 "A okay": 組み立ての entry に限り、:depends-on に宣言した依存先の protocol(翻訳の handler)も読んでよい。
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])
           (layer entry :roles [entry] :dependency-layers [intent protocol])])
(defservice automation "自動化" {:depends-on [messaging] :layers [core intent protocol entry]})
(defservice messaging "郵便" {:layers [core intent protocol]})
(defservice ledger "台帳" {:layers [core intent protocol]})
"#,
    )
    .unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF116\"]\n").unwrap();
    let files = [
        ("app/messaging/protocol/reception.hy", tags("messaging", "protocol") + "(defn translate [] 1)\n"),
        ("app/messaging/intent/submit.hy", tags("messaging", "intent") + "(defclass Submit [])\n"),
        ("app/ledger/protocol/book.hy", tags("ledger", "protocol") + "(defn book [] 1)\n"),
        // entry が依存先の protocol と intent を読む = 通る。
        ("app/automation/entry/handlers.hy", tags("automation", "entry") + "(import app.messaging.protocol.reception [translate])\n(import app.messaging.intent.submit [Submit])\n(defn handlers [] 1)\n"),
        // core が依存先の protocol を読む = 違反(entry 以外は intent だけ)。
        ("app/automation/core/plan.hy", tags("automation", "judgment") + "(import app.messaging.protocol.reception [translate])\n(defn plan [] 1)\n"),
        // entry が宣言に無い service の protocol を読む = 違反。
        ("app/automation/entry/main.hy", tags("automation", "entry") + "(import app.ledger.protocol.book [book])\n(defn main [] 1)\n"),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF116"),
        vec![
            "app/automation/core/plan.hy::DOEFF116::app.messaging.protocol.reception.translate",
            "app/automation/entry/main.hy::DOEFF116::app.ledger.protocol.book.book"
        ]
    );
    assert_eq!(code, 1);
    let core = violation(&report, "app/automation/core/plan.hy::DOEFF116::app.messaging.protocol.reception.translate");
    assert!(core["message"].as_str().unwrap().contains("層 core が依存先で読めるのは intent だけ"), "{}", core["message"]);
    assert!(core["explanation"]["reason"].as_str().unwrap().contains(":dependency-layers で広げた層"), "{}", core["explanation"]["reason"]);
    // 存在しない層の名は設定の誤り。
    let text = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().replace(":dependency-layers [intent protocol]", ":dependency-layers [intent ghost]");
    std::fs::write(dir.path().join("architecture.hy"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains(":dependency-layers の ghost"), "{}", stderr);
}

#[test]
fn placed_layers_depend_only_on_placed_modules() {
    // agora-redesign #1188: :placed-dependencies の層(service と shared)の module は、root の下の層の置き場の外の module を読まない。
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment type])
           (layer intent :roles [intent type])
           (layer foundation :roles [foundation])
           (layer entry :roles [entry])]
  :shared "shared"
  :foundation foundation
  :placed-dependencies [core intent])
(defservice billing "請求" {:layers [core intent entry]})
"#,
    )
    .unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF140\"]\n").unwrap();
    let files = [
        // 置き場の外: service の dir の直下の module・宣言に無い dir の中の module・package の中の置き場の外の module。
        ("app/billing/vocabulary.hy", "(setv WORD \"請求\")\n".to_string()),
        ("app/billing/model/row.hy", "(setv ROW 1)\n".to_string()),
        ("app/billing/views/__init__.py", "".to_string()),
        ("app/billing/intent/charge.hy", tags("billing", "intent") + "(defclass Charge [])\n"),
        ("app/foundation/store.hy", tags("io", "foundation") + "(defn store [] 1)\n"),
        ("elsewhere/lib.hy", "(setv LIB 1)\n".to_string()),
        // 違反: 同じ module を 2 度読んでも 1 件・名の import は持ち主の module に解く。
        (
            "app/billing/core/decide.hy",
            tags("billing", "judgment")
                + "(import app.billing.vocabulary [WORD])\n(import app.billing.vocabulary)\n(import app.billing.intent.charge [Charge])\n(import elsewhere.lib [LIB])\n(import json)\n(defn decide [] 1)\n",
        ),
        // 違反: shared の層も service と同じ(宣言に無い dir の中の module を読む)。
        ("app/shared/intent/names.hy", tags("shared", "intent") + "(import app.billing.model.row [ROW])\n(defclass Name [])\n"),
        // 通る: package の印(置き場の決まる前の dir の束ね)は数えない・:placed-dependencies に無い層(entry)は見ない。
        ("app/billing/core/views.hy", tags("billing", "judgment") + "(import app.billing.views)\n(defn views [] 1)\n"),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(import app.billing.vocabulary [WORD])\n(defn main [] 1)\n"),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF140"),
        vec!["app/billing/core/decide.hy::DOEFF140::app.billing.vocabulary", "app/shared/intent/names.hy::DOEFF140::app.billing.model.row"]
    );
    assert_eq!(code, 1);
    let decide = violation(&report, "app/billing/core/decide.hy::DOEFF140::app.billing.vocabulary");
    assert!(decide["message"].as_str().unwrap().contains("app/billing/vocabulary.hy"), "{}", decide["message"]);
    assert_eq!(decide["range"]["start"]["line"], 1, "最初の import の所に出す");
    // 渡した file 1 つの実行でも同じ 1 件(判定は渡した file の import と置き場だけで決まる)。
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/billing/core/decide.hy"], None);
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(keys(&single, "DOEFF140"), vec!["app/billing/core/decide.hy::DOEFF140::app.billing.vocabulary"]);
    // 宣言に無い層の名と foundation の層は設定の誤り。
    for (bad, said) in [("[core ghost]", ":placed-dependencies の ghost は :layers に無い"), ("[core foundation]", ":placed-dependencies の foundation は :foundation の層")] {
        let text = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().replace(":placed-dependencies [core intent]", &format!(":placed-dependencies {}", bad));
        let broken = tempfile::TempDir::new().unwrap();
        std::fs::write(broken.path().join("architecture.hy"), text).unwrap();
        std::fs::write(broken.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF140\"]\n").unwrap();
        let (code, _, stderr) = run(broken.path(), &["--output-format", "editor-json", "--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
        assert!(stderr.contains(said), "{}", stderr);
    }
}

#[test]
fn defk_called_bare_is_an_error_and_program_positions_are_not() {
    // 事実(#798): defk に改めた latest-by-ref を、deff と検が素のまま呼んでいた — Program が値として流れた。
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF126\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\ntest_paths = [\"**/tests/**\"]\n[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n",
    )
    .unwrap();
    std::fs::write(dir.path().join("known.txt"), "app/core/texts.hy::DOEFF126::expected_inputs::latest_by_ref\n").unwrap();
    let files = [
        ("app/core/reads.hy", "(defk latest-by-ref [ref] {:tags {:context \"c\" :role \"program\"}} ref)\n(deff plain [x] x)\n"),
        (
            "app/core/texts.hy",
            concat!(
                "(import app.core.reads [latest-by-ref plain])\n",
                "(deff text-at [ref] (.get (latest-by-ref ref) \"text\"))  ; defk にできない: 検の値\n",
                "(deff expected-inputs [ref] (tuple (latest-by-ref ref)))  ; defk にできない: 検の値\n",
                "(deff runner [ref] (run-on (latest-by-ref ref)))  ; defk にできない: Program を受けて走らせる\n",
                "(defk good [ref] (<- row (latest-by-ref ref)) (return (plain row)))\n",
            ),
        ),
        ("app/core/tests/test_reads.hy", "(import app.core.reads [latest-by-ref])\n(deftest test-reads (<- row (latest-by-ref 1)) (assert (= (latest-by-ref 1) row)))\n"),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF126"),
        vec![
            "app/core/tests/test_reads.hy::DOEFF126::test_reads::latest_by_ref",
            "app/core/texts.hy::DOEFF126::expected_inputs::latest_by_ref",
            "app/core/texts.hy::DOEFF126::text_at::latest_by_ref"
        ]
    );
    // 新しい素の呼びは error、登録簿に載った物は warning。defk でない plain の素の呼びは拾わない。
    assert_eq!(violation(&report, "app/core/texts.hy::DOEFF126::text_at::latest_by_ref")["severity"], "error");
    assert_eq!(violation(&report, "app/core/texts.hy::DOEFF126::expected_inputs::latest_by_ref")["severity"], "warning");
    assert_eq!(code, 1);
    let bare = violation(&report, "app/core/texts.hy::DOEFF126::text_at::latest_by_ref");
    assert_eq!(bare["explanation"]["subject"], "定義 text_at(deff)が defk latest-by-ref を素で呼んでいる");
    assert!(bare["hint"].as_str().unwrap().starts_with("(<- x (f …)) で束ねる"));
    assert_eq!(bare["range"]["start"]["line"], 1);
    // 1 file の実行(エディタの保存)でも repo の defk を知っている。
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/texts.hy"], Some("(import app.core.reads [latest-by-ref])\n(deff fresh [r] (len (latest-by-ref r)))\n"));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF126"), vec!["app/core/texts.hy::DOEFF126::fresh::latest_by_ref"]);
}

#[test]
fn a_function_argument_called_bare_across_files_is_found_at_the_callee() {
    // 事実(#798・agora L1550): rows-by-text が引数 field-of を素で呼び、呼び手はそこに fnk を渡していた — 索引が常に空になった。
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF126\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n").unwrap();
    let files = [
        ("app/core/index.hy", "(defk payload-of [row] row)\n(defk rows-by-text [rows field-of]\n  (for [row rows] (setv value (field-of row)) (when (isinstance value str) (print value)))\n  rows)\n(defk rows-safe [rows field-of] (<- v (field-of (get rows 0))) (return v))\n"),
        ("app/core/tags.hy", "(import app.core.index [rows-by-text rows-safe payload-of])\n(defk tag-rows-of [tags] (! (rows-by-text tags (fnk [row] (<- p (payload-of row)) (.get p \"subject\")))))\n(defk calm [tags] (! (rows-safe tags payload-of)))\n(defk plain [tags] (! (rows-by-text tags (fn [row] row))))\n"),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (code, report) = editor(dir.path());
    // 呼び先 rows-by-text の素の呼びだけが違反(rows-safe は (<- …) で受ける・fn を渡す呼び手は追わない)。
    assert_eq!(keys(&report, "DOEFF126"), vec!["app/core/index.hy::DOEFF126::rows_by_text::field_of"]);
    assert_eq!(code, 1);
    let found = violation(&report, "app/core/index.hy::DOEFF126::rows_by_text::field_of");
    assert!(found["explanation"]["subject"].as_str().unwrap().contains("呼び手 app/core/tags.hy の tag_rows_of がそこに fnk を渡す"), "{}", found["explanation"]["subject"]);
    assert_eq!(found["range"]["start"]["line"], 2);
}

#[test]
fn effects_disagreeing_with_inference_are_warnings_at_the_call_and_the_declaration() {
    // #849: 見出しの「宣言と推論の食い違い」の札をやめ、違反している場所(撃った呼び・:effects の中の名)に出す。
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF127\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n",
    )
    .unwrap();
    let files = [
        (
            "app/intent/rows.hy",
            "(defrecord Row \"行\" (#^ str id))\n(defeffect ReadRow \"読む\" {:fields [(: id str)] :answer Row :tags {:context \"c\" :role \"intent\"}})\n(defeffect PutRow \"書く\" {:fields [(: row Row)] :answer bool :tags {:context \"c\" :role \"intent\"}})\n",
        ),
        (
            "app/core/flow.hy",
            concat!(
                "(import app.intent.rows [ReadRow PutRow Row])\n",
                "(import doeff_core_effects [Delay])\n",
                "(defk fetch [id] {:pre [(: id str)] :post [(: % Row)] :effects [ReadRow]} (<- row (ReadRow id)) row)\n",
                "(defk store [id] {:pre [(: id str)] :post [(: % bool)] :effects [PutRow Delay]} (val row (! (fetch id))) (<- ok (PutRow row)) ok)\n",
                "(defk quiet [id] {:pre [(: id str)] :post [(: % Row)]} (<- row (ReadRow id)) row)\n",
                "(defk waits [s] {:pre [(: s int)] :post [(: % int)] :effects [Delay]} (<- (Delay s)) s)\n",
            ),
        ),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (_, report) = editor(dir.path());
    // 宣言の無い quiet・一致する fetch・外の effect Delay が一致する waits は出ない。
    assert_eq!(keys(&report, "DOEFF127"), vec!["app/core/flow.hy::DOEFF127::store::Delay", "app/core/flow.hy::DOEFF127::store::ReadRow"]);
    let undeclared = violation(&report, "app/core/flow.hy::DOEFF127::store::ReadRow");
    assert_eq!(undeclared["severity"], "warning");
    assert_eq!(undeclared["range"]["start"]["line"], 3);
    assert!(undeclared["message"].as_str().unwrap().contains("fetch を経由して、:effects に無い effect ReadRow"), "{}", undeclared);
    assert_eq!(undeclared["explanation"]["subject"], "defk store が fetch を経由して effect ReadRow を起こしている");
    let unused = violation(&report, "app/core/flow.hy::DOEFF127::store::Delay");
    assert!(unused["message"].as_str().unwrap().contains(":effects に Delay を書いているが、起こしていない"));
}

#[test]
fn an_unreadable_hy_file_is_reported_to_the_editor_and_the_hook() {
    // 読めない file の違反が黙って空にならない — 有効な規則の一覧(ここでは DOEFF104 だけ)に関わらず DOEFF128 の error で出る。
    let files = [
        ("app/core/broken.hy", "(val MODULE-TAGS {:context \"c\" :role \"judgment\"})\n(defk f [x]\n  (print \"no end)\n"),
        ("app/core/fine.hy", "(val MODULE-TAGS {:context \"c\" :role \"judgment\"})\n(defk g [xs] (print f\"{(.join \"; \" xs)}\"))\n"),
    ];
    let dir = repo(&files, "");
    let (code, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF128"), vec!["app/core/broken.hy::DOEFF128"]);
    let broken = violation(&report, "app/core/broken.hy::DOEFF128");
    assert_eq!(broken["severity"], "error");
    assert_eq!(broken["range"]["start"]["line"], 1);
    assert!(broken["message"].as_str().unwrap().contains("読めない"), "{}", broken["message"]);
    assert_eq!(code, 1);
    // 保存前の 1 file の実行でも同じ。
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/broken.hy"], Some(files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(keys(&single, "DOEFF128"), vec!["app/core/broken.hy::DOEFF128"]);
    // agent の hook の知らせにも出る。
    let hook_input = format!("{{\"workspace_roots\": [\"{}\"]}}", dir.path().display());
    let (_, stdout, stderr) = run(dir.path(), &["--hook", "--no-log"], Some(&hook_input));
    assert!(stdout.contains("DOEFF128"), "{}\n{}", stdout, stderr);
}

/// :verification-environment の dir(模擬の環境 — service ではない置き場)の下の module は DOEFF114・115 にしない。
/// 1 つだけ受け、ほかの置き場の判定は変えない。service と同じ名は設定の誤り。
#[test]
fn verification_environment_is_a_declared_non_service_place() {
    let files = [
        ("app/sim/world.hy", tags("sim", "entry") + "(defn world [] 1)\n"),
        ("app/sim/peers/jev.hy", tags("sim", "foundation") + "(defn peer [] 1)\n"),
        ("app/sim/tests/test_world.hy", "(defn test-world [] 1)\n".to_string()),
        ("app/old/anything.hy", "(defn legacy [] 1)\n".to_string()),
    ];
    let dir = architecture_repo(&files, "");
    let path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&path).unwrap().replace(":foundation foundation)", ":foundation foundation\n  :verification-environment \"sim\")");
    std::fs::write(&path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF114"), vec!["app/old/anything.hy::DOEFF114"]);
    assert_eq!(keys(&report, "DOEFF115"), vec!["app/old::DOEFF115"]);

    // service の dir と同じ名は設定の誤り(置き場の判定が 2 通りに読める)。
    let dir = architecture_repo(&files, "");
    let path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&path).unwrap().replace(":foundation foundation)", ":foundation foundation\n  :verification-environment \"billing\")");
    std::fs::write(&path, text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains(":verification-environment billing は宣言した service の dir と同じ名にできない"), "{}", stderr);
}

/// DOEFF130 の repo — 層 core・intent・protocol の service 2 つ(kanban は shared に依存)と、pyproject.toml(`toml_extra` を末尾に足す)。
fn translation_repo(files: &[(&str, &str)], toml_extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment program type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])])
(defservice kanban "盤" {:depends-on [shared] :layers [core intent protocol]})
(defservice shared "共有" {:layers [core]})
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        format!(
            "[tool.doeff-linter]\nenable = [\"DOEFF130\"]\n\n[[tool.doeff-linter.laws]]\nname = \"translation-targets-only-generic-foundation-effects\"\nadr = \"ADR-TEST\"\nrules = [\"DOEFF130\"]\nlayers = [\"protocol\"]\nstatement = \"protocol の handler は doeff の汎用の effect だけを出す\"\n{}",
            toml_extra
        ),
    )
    .unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// DOEFF130 の file の組 — 業務の intent 2 つ(defclass と defeffect)・shared の core の判断(intent を出す物・輪になる物)・kanban の protocol の handler。
const TRANSLATION_FILES: [(&str, &str); 4] = [
    (
        "app/kanban/intent/board.hy",
        "(defclass ReadBoard [])\n(defeffect CreateCard \"card を作る\" {:fields [(: title str)]})\n",
    ),
    (
        // #955 が申し送る反例の形: shared の core の関数が ReadBoard(intent)を出し、protocol の handler がそれを import して呼ぶ。
        "app/shared/core/board_view.hy",
        "(import app.kanban.intent.board [ReadBoard])\n(defk view [c] (<- b (ReadBoard)) b)\n(defk deep [c] (<- v (view c)) v)\n(defk ping [n] (<- x (pong n)) x)\n(defk pong [n] (<- x (ping n)) x)\n",
    ),
    (
        // 業務の流れ(層 core の program)が intent を出すのは正しい — protocol の外なので当たらない。
        "app/kanban/core/flow.hy",
        "(import app.kanban.intent.board [CreateCard])\n(defk open-card [t] (<- c (CreateCard t)) c)\n",
    ),
    (
        "app/kanban/protocol/reads.hy",
        r#"(import doeff_http [HttpRequest])
(import doeff_records.effects [PutRow])
(import app.kanban.intent.board [CreateCard ReadBoard])
(import app.shared.core.board_view [view deep ping])

;; 正例: 受けた intent を doeff の汎用の effect(HttpRequest・記録の書き)へ出し直すだけ。
(defk put-card [t] (<- r (PutRow "cards" t)) r)
(defhandler http-reads
  (ReadBoard []
    (<- r (HttpRequest "https://example.invalid/board"))
    (resume r))
  (CreateCard [t]
    (<- r (put-card t))
    (resume r)))

;; 反例: import した shared の core の関数を経由して ReadBoard を出す(本文の名だけでは見えない)。
(defhandler board-reads
  (Lookup [c]
    (<- b (view c))
    (resume b)))

;; 反例: 2 段の経由。
(defhandler deep-reads
  (Deep [c]
    (<- b (deep c))
    (resume b)))

;; 反例: 本体で直に intent を出す。
(defhandler card-writes
  (NewCard [t]
    (<- x (CreateCard t))
    (resume x)))

;; 反例: [effect k] を受ける関数の handler。
(defk raw-handler [effect k]
  (<- (CreateCard "x"))
  (k effect))

;; 輪になる呼び(ping ↔ pong)でも止まり、intent に届かなければ当たらない。
(defhandler loops
  (Spin [n]
    (<- x (ping n))
    (resume x)))
"#,
    ),
];

#[test]
fn a_translation_handler_that_emits_a_business_intent_is_an_error() {
    // agora-redesign #956(#942 の決定 2): 層 protocol の handler は doeff の汎用の effect だけを出し、層 intent の型(業務の intent)を出さない。
    // 推論は import した defk の先まで辿る — 本体の名だけの判定(DOEFF201・check_business_fakes.hy)がすり抜ける形を塞ぐ。
    let dir = translation_repo(&TRANSLATION_FILES, "");
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF130"),
        vec![
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::board_reads::app.kanban.intent.board.ReadBoard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::card_writes::app.kanban.intent.board.CreateCard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::deep_reads::app.kanban.intent.board.ReadBoard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::raw_handler::app.kanban.intent.board.CreateCard",
        ]
    );
    assert_eq!(code, 1);
    let via = violation(&report, "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::board_reads::app.kanban.intent.board.ReadBoard");
    assert_eq!(via["severity"], "error");
    // 場所 = handler の本体の、intent に至る最初の呼び((view c) の view — 0 始まりの 18 行目)。
    assert_eq!(via["range"]["start"]["line"], 18);
    assert!(via["message"].as_str().unwrap().contains("handler board-reads(層 protocol)が view を経由して、層 intent の業務の intent ReadBoard を出す"), "{}", via["message"]);
    assert_eq!(via["explanation"]["subject"], "handler board-reads(層 protocol)が view を経由して、層 intent の intent ReadBoard を出している");
    assert_eq!(via["explanation"]["law_statement"], "protocol の handler は doeff の汎用の effect だけを出す");
    let deep = violation(&report, "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::deep_reads::app.kanban.intent.board.ReadBoard");
    assert!(deep["message"].as_str().unwrap().contains("deep → view を経由して"), "{}", deep["message"]);
    let direct = violation(&report, "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::card_writes::app.kanban.intent.board.CreateCard");
    assert!(direct["message"].as_str().unwrap().contains("handler card-writes(層 protocol)が層 intent の業務の intent CreateCard を出す"), "{}", direct["message"]);

    // 保存前の 1 file の実行(エディタ)でも同じ 4 件。
    let source = TRANSLATION_FILES[3].1;
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/kanban/protocol/reads.hy"], Some(source));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF130"), keys(&report, "DOEFF130"));
}

#[test]
fn a_translation_handler_may_emit_another_services_public_intent() {
    // agora-redesign #1134 の決め(DOEFF156 の「他の service の公開の効果」と揃える): 止めるのは handler と同じ service の intent だけ。
    // 他の service の intent(公開の契約)を出すのは翻訳の仕事 — 例: durable の翻訳が郵便の受付の intent を出す(#1508)。
    let files = [
        ("app/kanban/intent/board.hy", "(defclass ReadBoard [])\n"),
        ("app/orders/intent/orders.hy", "(defclass PlaceOrder [])\n"),
        (
            "app/kanban/protocol/reads.hy",
            r#"(import app.kanban.intent.board [ReadBoard])
(import app.orders.intent.orders [PlaceOrder])

;; 正例: 他の service(orders)の公開の intent を出す。
(defhandler order-bridge
  (Bridge [x]
    (<- r (PlaceOrder))
    (resume r)))

;; 反例: 自分の service(kanban)の intent を出す — 今までどおり error。
(defhandler own-reads
  (Lookup [c]
    (<- b (ReadBoard))
    (resume b)))
"#,
        ),
    ];
    let dir = translation_repo(&files, "");
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment program type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])])
(defservice kanban "盤" {:depends-on [orders] :layers [core intent protocol]})
(defservice orders "注文" {:layers [core intent protocol]})
"#,
    )
    .unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF130"),
        vec!["app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::own_reads::app.kanban.intent.board.ReadBoard"]
    );
}

#[test]
fn translation_effects_depth_and_layers_are_configured() {
    // 辿る段の上限は設定 — 0 なら handler の本体で直に出す intent だけ。
    let dir = translation_repo(&TRANSLATION_FILES, "\n[tool.doeff-linter.translation_effects]\nmax_depth = 1\n");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF130"),
        vec![
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::board_reads::app.kanban.intent.board.ReadBoard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::card_writes::app.kanban.intent.board.CreateCard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::raw_handler::app.kanban.intent.board.CreateCard",
        ]
    );
    let dir = translation_repo(&TRANSLATION_FILES, "\n[tool.doeff-linter.translation_effects]\nmax_depth = 0\n");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF130"),
        vec![
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::card_writes::app.kanban.intent.board.CreateCard",
            "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::raw_handler::app.kanban.intent.board.CreateCard",
        ]
    );
    // 節に書いた層の名が無ければ設定の誤り(黙って当たらなくならない)。
    let dir = translation_repo(&TRANSLATION_FILES, "\n[tool.doeff-linter.translation_effects]\nhandler_layers = [\"ghost\"]\n");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("translation_effects.handler_layers: 層 ghost は宣言した層に無い"), "{}", stderr);
}

/// 許可名簿(:world-handlers)を書いた repo(層 core・foundation・entry・service billing)。
fn world_repo(files: &[(&str, String)]) -> tempfile::TempDir {
    world_repo_with(files, "", "[\"DOEFF106\", \"DOEFF131\"]")
}

/// 名簿に要素を足した world_repo(`extra` は :world-handlers の列の末尾に足す要素・`enable` は規則の列)。
fn world_repo_with(files: &[(&str, String)], extra: &str, enable: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let architecture = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment] :imports [core])
           (layer foundation :roles [foundation] :imports [foundation])
           (layer entry :roles [entry] :imports [core foundation entry])]
  :foundation foundation
  :world-handlers [(world-handler "app.foundation.host:with-host" :touches [http file]
                     :wraps ["doeff_core_effects.os_file:os-file-handler"])EXTRA])
(defservice billing "請求" {:layers [core entry]})
"#
    .replace("EXTRA", extra);
    std::fs::write(dir.path().join("architecture.hy"), architecture).unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), format!("[tool.doeff-linter]\nenable = {}\n", enable)).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// agora-redesign #1140(R1): 許可名簿を書いた repo では、生の I/O は名簿の定義の module だけに許し(層では許さない — entry も
/// 名簿に無い foundation の module も当たる)、:wraps の doeff の実 I/O の handler を名指してよいのは名簿の定義だけ(DOEFF131)。
#[test]
fn world_handler_list_limits_raw_io_and_wrapped_handlers() {
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation")
                + "(import httpx)\n(import doeff_core_effects.os_file [os-file-handler])\n(import doeff_core_effects.file_effects [ReadText])\n\
                   (defk with-host [body] (with-handlers [os-file-handler (fn [] (httpx.AsyncClient))] body))\n\
                   (defk token-in-file [path] (with-handlers [os-file-handler] (ReadText path)))\n",
        ),
        (
            "app/foundation/unreachable.hy",
            tags("shared", "foundation") + "(import httpx)\n(import urllib.parse)\n(defn refuse [u] (raise (httpx.ConnectError (urllib.parse.quote u))))\n",
        ),
        ("app/foundation/sockets.hy", tags("shared", "foundation") + "(import socket)\n(defn open-one [] (socket.socket))\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry")
                + "(import httpx)\n(import doeff_core_effects.os_file [os-file-handler])\n\
                   (defn fetch [] (httpx.get \"http://x\"))\n(defk run [body] (with-handlers [os-file-handler] body))\n",
        ),
        ("app/billing/entry/good.hy", tags("billing", "entry") + "(import app.foundation.host [with-host])\n(defk ok [body] (with-host body))\n"),
    ];
    let dir = world_repo(&files);
    let (_, report) = editor(dir.path());
    let raw = keys(&report, "DOEFF106");
    assert_eq!(
        raw,
        vec!["app/billing/entry/main.hy::DOEFF106::fetch::httpx.get", "app/foundation/sockets.hy::DOEFF106::open_one::socket.socket"],
        "名簿の module(host)・例外の型だけの httpx と urllib.parse(unreachable)は当てない: {}",
        report
    );
    assert!(violation(&report, "app/billing/entry/main.hy::DOEFF106::fetch::httpx.get")["message"].as_str().unwrap().contains(":world-handlers"));
    let named = keys(&report, "DOEFF131");
    assert_eq!(
        named,
        vec![
            "app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects.os_file:os-file-handler",
            "app/foundation/host.hy::DOEFF131::token_in_file::world::doeff_core_effects.os_file:os-file-handler",
        ],
        "名簿の定義 with-host の中は当てず、同じ module の名簿に無い定義と entry は当てる: {}",
        report
    );
    let found = violation(&report, "app/foundation/host.hy::DOEFF131::token_in_file::world::doeff_core_effects.os_file:os-file-handler");
    assert_eq!(found["severity"], "error");
    assert_eq!(found["level"], "critical");
}

/// agora-redesign #1141(R2): 許可名簿の定義は実在し、層 foundation の module に在る(DOEFF132 — 位置は architecture.hy の名簿の要素)。
#[test]
fn world_handler_list_entries_exist_in_the_foundation_layer() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(defk run [body] body)\n"),
    ];
    let extra = r#"
                   (world-handler "app.foundation.host:gone" :touches [file])
                   (world-handler "app.billing.entry.main:run" :touches [file])
                   (world-handler "app.foundation.nowhere:x" :touches [file])"#;
    let dir = world_repo_with(&files, extra, "[\"DOEFF132\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF132"),
        vec![
            "architecture.hy::DOEFF132::app.billing.entry.main:run",
            "architecture.hy::DOEFF132::app.foundation.host:gone",
            "architecture.hy::DOEFF132::app.foundation.nowhere:x",
        ],
        "実在する foundation の with-host は当てない: {}",
        report
    );
    let misplaced = violation(&report, "architecture.hy::DOEFF132::app.billing.entry.main:run");
    assert!(misplaced["message"].as_str().unwrap().contains("層 entry に在る"), "{}", misplaced);
    assert_eq!(misplaced["level"], "critical");
    assert!(violation(&report, "architecture.hy::DOEFF132::app.foundation.host:gone")["message"].as_str().unwrap().contains("定義 gone が無い"));
    let line = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().lines().position(|l| l.contains("app.foundation.host:gone")).unwrap();
    assert_eq!(violation(&report, "architecture.hy::DOEFF132::app.foundation.host:gone")["range"]["start"]["line"], line);
}

/// agora-redesign #1142(R3): テストの種類は届く先から導く — 名簿の定義・:wraps の handler・生の I/O に(定義を辿って)届く deftest は
/// 縁で :edge-mark の印が要り、届かない deftest は手元で印を持たない。食い違いは DOEFF133(critical)。
#[test]
fn test_kind_is_derived_from_what_the_test_reaches() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/calc.hy", tags("billing", "judgment") + "(defk add [a b] (+ a b))\n"),
        ("app/billing/core/helpers.hy", tags("billing", "judgment") + "(import app.foundation.host [with-host])\n(defk hosted [body] (with-host body))\n"),
        (
            "app/billing/tests/test_kinds.hy",
            "(import pytest)\n(import subprocess)\n(import doeff_core_effects.os_file [os-file-handler])\n\
             (import app.billing.core.calc [add])\n(import app.billing.core.helpers [hosted])\n\
             (deftest test-local-good (<- n (add 1 2)) (assert (= n 3)))\n\
             (deftest test-local-marked {:marks [\"real_world\"]} (<- n (add 1 2)) (assert (= n 3)))\n\
             (deftest test-edge-two-steps (<- n (hosted (add 1 2))) (assert n))\n\
             (deftest test-edge-wrapped (<- n (with-handlers [os-file-handler] (add 1 2))) (assert n))\n\
             (deftest test-edge-raw-good {:marks [\"real_world\"]} (assert (subprocess.run [\"true\"])))\n"
                .to_string(),
        ),
        (
            "app/billing/tests/test_module_mark.hy",
            "(import pytest)\n(import app.billing.core.helpers [hosted])\n(setv pytestmark pytest.mark.real-world)\n\
             (deftest test-edge-module-marked (<- n (hosted 1)) (assert n))\n"
                .to_string(),
        ),
        // defadr の並び [..] の中に入れ子の deftest の :marks も印(当てない)。
        (
            "app/billing/tests/test_nested_adr.hy",
            "(import app.billing.core.helpers [hosted])\n\
             (defadr adr-nested :title \"入れ子\" :tests [(deftest test-edge-in-a-list {:marks [\"real_world\"]} (<- n (hosted 1)) (assert n))])\n"
                .to_string(),
        ),
        // 読むだけの受け手(:static-readers)に値で渡すだけの縁の定義は、実行されないので届かない(手元 — 当てない)。
        ("app/billing/core/closure.hy", tags("billing", "judgment") + "(defk read-closure [case] (str case))\n"),
        (
            "app/billing/tests/test_closure.hy",
            "(import app.billing.core.closure [read-closure])\n(import app.billing.core.helpers [hosted])\n\
             (deftest test-local-reads-a-foundation (<- text (read-closure hosted)) (assert text))\n"
                .to_string(),
        ),
        // 註と検の中の文字列の値に在る印の綴りは module の印ではない(手元のテストは印なしのまま — 当てない)。
        (
            "app/billing/tests/test_mark_in_text.hy",
            "(import app.billing.core.calc [add])\n;; 印の綴りの説明: pytestmark = pytest.mark.real_world\n\
             (deftest test-local-reads-spellings\n  (<- n (add 1 2))\n  (assert (in \"pytest\" \"(val pytestmark pytest.mark.real-world)\")))\n\
             (deftest test-local-reads-a-marks-spelling\n  (assert (in \"marks\" \"(deftest test-x {:marks [\\\"real_world\\\"]} 1)\")))\n\
             (deftest test-local-with-fixture-and-doc-marked [tmp-path] \"説明\" {:marks [\"real_world\"]} (<- n (add 1 2)) (assert n))\n"
                .to_string(),
        ),
    ];
    let architecture_extra = "";
    let dir = world_repo_with(&files, architecture_extra, "[\"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"\n  :static-readers [\"app.billing.core.closure:read-closure\"]");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec![
            "app/billing/tests/test_kinds.hy::DOEFF133::test_edge_two_steps::edge",
            "app/billing/tests/test_kinds.hy::DOEFF133::test_edge_wrapped::edge",
            "app/billing/tests/test_kinds.hy::DOEFF133::test_local_marked::local",
            // fixture の並びと docstring の後ろの設定の :marks も印(doeff-hy の deftest と同じ読み方)。
            "app/billing/tests/test_mark_in_text.hy::DOEFF133::test_local_with_fixture_and_doc_marked::local",
        ],
        "印の在る縁(:marks と module の pytestmark)と印の無い手元は当てない・文字列の中の :marks は印ではない: {}",
        report
    );
    let two_steps = violation(&report, "app/billing/tests/test_kinds.hy::DOEFF133::test_edge_two_steps::edge");
    assert!(two_steps["message"].as_str().unwrap().contains("hosted → with-host → app.foundation.host:with-host"), "{}", two_steps["message"]);
    assert_eq!(two_steps["level"], "critical");
}

/// agora-redesign #1581: 縁の定義を値として検めるだけの名指し — 比べの form(is・is-not・=・!=・in・not-in)と assert(失敗の時の
/// 表示の文を含む)の直接の被演算子(literal の列・組の中と、被演算子の dotted の属性の読みを含む)— は届かない(手元・当てない)。呼ぶ・他の定義へ渡す・比べの中で
/// 呼んだ結果を比べる・比べの外で属性を読む名指しは今までどおり届く(縁・印が無いので当てる — 解釈器の定数が handler を
/// `f.__module__` で名指す DOEFF137 の形を壊さない)。
#[test]
fn inspecting_a_value_does_not_reach_but_passing_or_calling_it_does() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/calc.hy", tags("billing", "judgment") + "(defk add [a b] (+ a b))\n"),
        ("app/billing/core/helpers.hy", tags("billing", "judgment") + "(import app.foundation.host [with-host])\n(defk hosted [body] (with-host body))\n"),
        (
            "app/billing/tests/test_inspect.hy",
            "(import app.billing.core.calc [add])\n(import app.billing.core.helpers [hosted])\n(import app.billing.core [helpers])\n\
             (deftest test-local-compares-identity (val args [add]) (assert (is-not (get args 0) hosted)))\n\
             (deftest test-local-compares-a-literal-list (assert (!= [add 1] [hosted 1])) (assert (not-in hosted #(add))))\n\
             (deftest test-local-compares-a-qualified-name (assert (is helpers.hosted hosted)))\n\
             (deftest test-local-compares-a-dunder (assert (= hosted.__doeff_needs__ (frozenset)) hosted.__doeff_needs__))\n\
             (deftest test-edge-passes-the-value (<- n (add hosted 1)) (assert n))\n\
             (deftest test-edge-compares-a-call-result (assert (= (hosted 1) 1)))\n\
             (deftest test-edge-reads-a-dunder-outside-a-comparison (val spelling (+ hosted.__module__ \":\")) (assert spelling))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec![
            "app/billing/tests/test_inspect.hy::DOEFF133::test_edge_compares_a_call_result::edge",
            "app/billing/tests/test_inspect.hy::DOEFF133::test_edge_passes_the_value::edge",
            "app/billing/tests/test_inspect.hy::DOEFF133::test_edge_reads_a_dunder_outside_a_comparison::edge",
        ],
        "比べの被演算子と dunder の属性の読みは届かず、他の定義へ渡す名指しと比べの中の呼び出しは届く: {}",
        report
    );
}

/// agora-redesign #1798: deftest が(Hy の定義を通して)名指す repo の中の Python の関数(`.py`)は、その中の子 process
/// (`subprocess.run`・`os.system`)と、名前で決まる Python の呼び先(同じ module の関数・import した関数・class の構築と
/// `self.m`)まで辿る — 届けば縁(印が要る)。型の注記と except の型の中の名は値の実行ではない(届かない)。Python の関数から
/// Hy の定義へ戻る呼びも辿る。構文の壊れた Python の module は黙って手元にせず、報告の誤りに名乗る。
#[test]
fn test_kind_follows_python_functions_into_their_subprocesses() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/calc.hy", tags("billing", "judgment") + "(defk add [a b] (+ a b))\n"),
        (
            "tools/bake.py",
            "import subprocess\nfrom tools.inner import spawn\nfrom app.foundation.host import with_host\n\n\
             def launch(argv: list[str]) -> int:\n    return spawn(argv)\n\n\
             def quiet(done: subprocess.CompletedProcess) -> int:\n    try:\n        return done.returncode\n    except subprocess.TimeoutExpired:\n        return 1\n\n\
             def hosted(body):\n    return with_host(body)\n\n\
             class Runner:\n    def __init__(self):\n        self.start()\n    def start(self):\n        import os\n        os.system('true')\n\n\
             def build():\n    return Runner()\n"
                .to_string(),
        ),
        ("tools/inner.py", "import subprocess as sp\n\ndef spawn(argv):\n    return sp.run(argv, check=False).returncode\n".to_string()),
        ("tools/broken.py", "def mystery(:\n".to_string()),
        ("app/billing/core/helpers.hy", tags("billing", "judgment") + "(import tools.bake [launch])\n(defk bake-all [argv] (launch argv))\n"),
        (
            "app/billing/tests/test_python.hy",
            "(import app.billing.core.calc [add])\n(import app.billing.core.helpers [bake-all])\n(import tools.bake [launch quiet hosted build])\n\
             (import tools.broken [mystery])\n\
             (deftest test-edge-launch-unmarked (assert (= (launch [\"true\"]) 0)))\n\
             (deftest test-edge-launch-marked {:marks [\"real_world\"]} (assert (= (launch [\"true\"]) 0)))\n\
             (deftest test-edge-through-a-hy-helper (assert (= (bake-all [\"true\"]) 0)))\n\
             (deftest test-edge-through-a-constructor (assert (build)))\n\
             (deftest test-edge-back-into-hy (assert (hosted 1)))\n\
             (deftest test-local-quiet (assert (= (quiet (add 1 2)) 3)))\n\
             (deftest test-local-quiet-marked {:marks [\"real_world\"]} (assert (= (quiet 1) 1)))\n\
             (deftest test-local-broken-module (assert (mystery)))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec![
            "app/billing/tests/test_python.hy::DOEFF133::test_edge_back_into_hy::edge",
            "app/billing/tests/test_python.hy::DOEFF133::test_edge_launch_unmarked::edge",
            "app/billing/tests/test_python.hy::DOEFF133::test_edge_through_a_constructor::edge",
            "app/billing/tests/test_python.hy::DOEFF133::test_edge_through_a_hy_helper::edge",
            "app/billing/tests/test_python.hy::DOEFF133::test_local_quiet_marked::local",
        ],
        "Python の関数の子 process に届く印なしは縁で当たり、印つき・注記と except の型だけの関数は当てない: {}",
        report
    );
    let launch = violation(&report, "app/billing/tests/test_python.hy::DOEFF133::test_edge_launch_unmarked::edge");
    assert!(launch["message"].as_str().unwrap().contains("tools.bake.launch → tools.inner.spawn → subprocess.run"), "{}", launch["message"]);
    let helper = violation(&report, "app/billing/tests/test_python.hy::DOEFF133::test_edge_through_a_hy_helper::edge");
    assert!(helper["message"].as_str().unwrap().contains("bake-all → tools.bake.launch"), "{}", helper["message"]);
    let errors = report["errors"].as_array().unwrap();
    assert!(errors.iter().any(|e| e.as_str().unwrap().contains("tools/broken.py")), "構文の壊れた module を名乗る: {}", report);
}

/// agora-redesign #1363: 許可名簿の handler ごとに縁の検(空でない :interpreters を持ち、DOEFF133 と同じ図でその handler の定義に
/// 届く deftest)が要る — 無ければ architecture.hy の名簿の要素で DOEFF137(critical・細目 = 名簿の綴り)。:interpreters の要素は
/// file の外の定数の記号のまま(読み解かない)。理由つきの :contract-test (none …) の handler は判じない・理由の無い none は鳴る(#1796)。
#[test]
fn world_handler_needs_a_contract_test_that_runs_interpreters() {
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation")
                + "(defk with-host [body] body)\n(defk with-clock [body] body)\n(defk with-queue [body] body)\n(defk with-exempt [body] body)\n",
        ),
        // 解釈器の組み立ての定数(file の外の記号)が handler を名指す — deftest は :interpreters でこの定数を並べるだけ。
        (
            "app/foundation/tests/contract_handlers.hy",
            "(import app.foundation.host [with-host])\n\
             (val HOST (+ with-host.__module__ \":\" with-host.__name__))\n(val SIM \"sim-host\")\n"
                .to_string(),
        ),
        (
            "app/foundation/tests/test_host_contract.hy",
            "(import app.foundation.tests.contract_handlers [HOST SIM])\n\
             (deftest test-host-answers\n  \"本物と模擬が同じ検を通る\"\n  {:interpreters [HOST SIM] :marks [\"real_world\"]}\n  (assert True))\n"
                .to_string(),
        ),
        // :interpreters を持たない deftest が届くだけ・空の :interpreters は縁の検に数えない。
        (
            "app/foundation/tests/test_clock_plain.hy",
            "(import app.foundation.host [with-clock with-queue])\n\
             (deftest test-clock-reached {:marks [\"real_world\"]} (<- n (with-clock 1)) (assert n))\n\
             (deftest test-queue-empty-interpreters {:interpreters [] :marks [\"real_world\"]} (<- n (with-queue 1)) (assert n))\n"
                .to_string(),
        ),
    ];
    let extra = r#"
                   (world-handler "app.foundation.host:with-clock" :touches [clock])
                   (world-handler "app.foundation.host:with-queue" :touches [thread])
                   (world-handler "app.foundation.host:with-exempt" :touches [file]
                     :contract-test (none :doeff-test "packages/doeff-core-effects/tests/test_os_file.hy::test-os-file-contract"))"#;
    let dir = world_repo_with(&files, extra, "[\"DOEFF137\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"");
    std::fs::write(&arch_path, &text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF137"),
        vec!["architecture.hy::DOEFF137::app.foundation.host:with-clock", "architecture.hy::DOEFF137::app.foundation.host:with-queue"],
        "縁の検の届く with-host と理由つきの :contract-test (none …) の with-exempt は当てない: {}",
        report
    );
    let clock = violation(&report, "architecture.hy::DOEFF137::app.foundation.host:with-clock");
    assert_eq!(clock["level"], "critical");
    assert_eq!(clock["severity"], "error");
    let line = text.lines().position(|l| l.contains("app.foundation.host:with-clock")).unwrap();
    assert_eq!(clock["range"]["start"]["line"], line);

    // 理由の無い記号 none は鳴る — 縁の検の届く with-host に書いても当てる(#1796)。
    let bare = text
        .replace(
            "\n                     :contract-test (none :doeff-test \"packages/doeff-core-effects/tests/test_os_file.hy::test-os-file-contract\")",
            " :contract-test none",
        )
        .replace("(world-handler \"app.foundation.host:with-clock\"", "(world-handler \"app.foundation.host:with-clock\" :contract-test none");
    assert_ne!(bare, text);
    std::fs::write(&arch_path, &bare).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF137"),
        vec![
            "architecture.hy::DOEFF137::app.foundation.host:with-clock",
            "architecture.hy::DOEFF137::app.foundation.host:with-exempt",
            "architecture.hy::DOEFF137::app.foundation.host:with-queue"
        ],
        "理由の無い none の with-exempt と with-clock を当てる: {}",
        report
    );
    let exempt = violation(&report, "architecture.hy::DOEFF137::app.foundation.host:with-exempt");
    assert!(exempt["message"].as_str().unwrap().contains(":contract-test none に理由が無い"), "{}", exempt);

    // :contract-test の値は理由つきの (none …) か記号 none だけ — ほかの値は宣言の誤り(終了コード 2)。
    let bad = bare.replace("with-exempt\" :touches [file] :contract-test none", "with-exempt\" :touches [file] :contract-test (none :doeff-test \"\")");
    assert_ne!(bad, bare);
    std::fs::write(&arch_path, bad).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("world-handler app.foundation.host:with-exempt の :contract-test は (none"), "{}", stderr);
}

/// #1564: 種つきの疑似乱数を使う性質の検は手元、OS の entropy を使う操作は縁のまま。
#[test]
fn test_kind_distinguishes_seeded_random_from_entropy() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/tests/test_random.hy", r#"
(import random uuid secrets)
(defk draw [rng] {:pre [(: rng random.Random)]} (.random rng))
(defk with-seed [seed] (val rng (random.Random seed)) (val alias rng) (draw alias))
(deftest test-seeded (with-seed 7))
(deftest test-local-import (import random) (val rng (random.Random 619)) (.randint rng 0 9))
(deftest test-unseeded (val rng (random.Random)) (.random rng))
(deftest test-none-seed (random.Random None))
(deftest test-global (random.random))
(deftest test-uuid (uuid.uuid4))
(deftest test-secrets (secrets.token-hex 8))
"#.to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF133\"]");
    let architecture = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&architecture).unwrap()
        .replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"");
    std::fs::write(architecture, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF133"), vec![
        "app/billing/tests/test_random.hy::DOEFF133::test_global::edge",
        "app/billing/tests/test_random.hy::DOEFF133::test_none_seed::edge",
        "app/billing/tests/test_random.hy::DOEFF133::test_secrets::edge",
        "app/billing/tests/test_random.hy::DOEFF133::test_unseeded::edge",
        "app/billing/tests/test_random.hy::DOEFF133::test_uuid::edge",
    ], "{report}");
}

/// agora-redesign #1209: 実 I/O の handler は doeff の目録(data/world_handlers.json)から知る — :wraps は目録に在る物だけ(外は設定の誤り)、
/// 目録に在り :wraps に無い handler(subprocess-handler)を名簿の外で名指しても DOEFF131、:edge-touches から file を外すと file だけに
/// 届くテストは手元(決定 B)。
#[test]
fn world_catalog_widens_the_rules_and_edge_touches_narrow_the_edge() {
    // :wraps が目録の外なら設定の誤り(終了コード 2)。
    let outside = r#"
                   (world-handler "app.foundation.host:with-other" :touches [http] :wraps ["somewhere.else:mystery-handler"])"#;
    let dir = world_repo_with(&[("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-other [body] body)\n")], outside, "[\"DOEFF131\"]");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("somewhere.else:mystery-handler は doeff の実 I/O の handler の目録"), "{}", stderr);

    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry") + "(import doeff_core_effects.os_process [subprocess-handler])\n(defk run [body] (with-handlers [subprocess-handler] body))\n",
        ),
        (
            "app/billing/tests/test_io.hy",
            "(import doeff_core_effects.os_file [os-file-handler])\n(import doeff_core_effects.os_process [subprocess-handler])\n\
             (deftest test-files-only (<- n (with-handlers [os-file-handler] 1)) (assert n))\n\
             (deftest test-files-marked {:marks [\"real_world\"]} (<- n (with-handlers [os-file-handler] 1)) (assert n))\n\
             (deftest test-process (<- n (with-handlers [subprocess-handler] 1)) (assert n))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF131\", \"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"\n  :edge-touches [http db process clock cluster network thread]");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    let named = keys(&report, "DOEFF131");
    assert!(named.contains(&"app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects.os_process:subprocess-handler".to_string()), "{:?}", named);
    let found = violation(&report, "app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects.os_process:subprocess-handler");
    assert!(found["message"].as_str().unwrap().contains("名簿のどの定義もこの handler を :wraps に挙げていない"), "{}", found["message"]);
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec!["app/billing/tests/test_io.hy::DOEFF133::test_files_marked::local", "app/billing/tests/test_io.hy::DOEFF133::test_process::edge"],
        "file だけのテストは手元(印なしは当てない)・process は縁: {}",
        report
    );
}

/// agora-redesign #1144(R6): テストは deftest だけ — :test-forms の綴りの型で選んだ file の、Python の def test_*・module ごとの skip・
/// pytest の外の check script・deftest の runner を file ごとに 1 件 DOEFF135(critical)で出す。deftest だけの file は当てない。
#[test]
fn test_forms_other_than_deftest_are_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/tests/test_good.hy", "(deftest test-one (assert True))\n".to_string()),
        ("app/billing/tests/test_python.py", "import pytest\n\ndef helper():\n    pass\n\ndef test_a():\n    assert True\n\nclass TestB:\n    def test_b(self):\n        assert True\n".to_string()),
        ("app/billing/tests/test_skipped.hy", "(import pytest)\n(pytest.skip \"doeff 側の不足\" :allow-module-level True)\n(deftest test-x (assert True))\n".to_string()),
        // agora-redesign #1426: 束ねの名と skip の印が別の行に在る複数行の pytestmark(agora の test_local_agora_e2e.hy の形)。
        (
            "app/billing/tests/test_marked_multiline.hy",
            "(import pytest)\n;; module ごとの skip は pytestmark の印で書く(註の中の語は当てない)。\n(val pytestmark\n  (pytest.mark.skip :reason (+ \"理由の 1 行目(括弧も在る)\"\n                              \"理由の 2 行目\")))\n(deftest test-x (assert True))\n"
                .to_string(),
        ),
        ("app/billing/tests/test_marked_multiline.py", "import pytest\n\npytestmark = [\n    pytest.mark.skip(reason=\"x\"),\n]\n".to_string()),
        // skip でない印の複数行の束ねと、1 行の印は当てない。束ねの後の検ごとの skip も束ねに数えない。
        (
            "app/billing/tests/test_marked_real_world.hy",
            "(import pytest)\n(val pytestmark\n  pytest.mark.real-world)\n(deftest test-x (assert True))\n(deftest test-y (pytest.mark.skip))\n".to_string(),
        ),
        ("app/billing/tests/test_marked_one_line.hy", "(import pytest)\n(val pytestmark pytest.mark.real-world)\n(deftest test-x (assert True))\n".to_string()),
        ("scripts/check_things.hy", "(defn main [] 0)\n".to_string()),
        ("app/billing/tests/billing_deftest_runner.hy", "(defn main [] 0)\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF135\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :test-forms {:tests [\"test_*.hy\" \"test_*.py\"] :check-scripts [\"scripts/check_*.hy\"] :runners [\"*_deftest_runner.hy\"]}",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF135"),
        vec![
            "app/billing/tests/billing_deftest_runner.hy::DOEFF135::runner",
            "app/billing/tests/test_marked_multiline.hy::DOEFF135::module-skip",
            "app/billing/tests/test_marked_multiline.py::DOEFF135::module-skip",
            "app/billing/tests/test_python.py::DOEFF135::python-test",
            "app/billing/tests/test_skipped.hy::DOEFF135::module-skip",
            "scripts/check_things.hy::DOEFF135::check-script",
        ],
        "{}",
        report
    );
    // 複数行の束ねは束ねの名の行を指す。
    let multiline = violation(&report, "app/billing/tests/test_marked_multiline.hy::DOEFF135::module-skip");
    assert_eq!(multiline["range"]["start"]["line"], 2);
    let python = violation(&report, "app/billing/tests/test_python.py::DOEFF135::python-test");
    assert!(python["message"].as_str().unwrap().contains("def test_* が 2 本"), "{}", python["message"]);
    assert_eq!(python["range"]["start"]["line"], 5);
    assert_eq!(python["level"], "critical");
}

/// agora-redesign #1147: 許可名簿の規則(DOEFF106・131)は層の置き場の外の file(層の外の dir・:root の外で :raw-io-roots に挙げた dir)にも当たる。
/// 検の file と :raw-io-roots の外(scripts/)は当てない。
#[test]
fn world_handler_rules_cover_files_outside_the_layers() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/runtime/handlers.hy", "(import socket)\n(defn open-one [] (socket.socket))\n".to_string()),
        (
            "services/record/main.hy",
            "(import threading)\n(import doeff_core_effects.os_file [os-file-handler])\n(defn serve [] (threading.Thread))\n(defn run [body] (with-handlers [os-file-handler] body))\n"
                .to_string(),
        ),
        ("services/record/tests/test_main.hy", "(import socket)\n(deftest test-x (socket.socket))\n".to_string()),
        ("scripts/tool.hy", "(import subprocess)\n(defn go [] (subprocess.run [\"true\"]))\n".to_string()),
        // 実行できる ADR の冊(defadr を持つ file)と、file の中の deftest の実 I/O はテストの持ち分 — 当てない。
        ("app/sim/adr/defadr_rule.hy", "(import pathlib [Path])\n(defadr rule \"x\")\n(defn read-src [] (open \"a.hy\"))\n".to_string()),
        ("app/sim/checks.hy", "(import socket)\n(deftest test-open (socket.socket))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF106\", \"DOEFF131\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :raw-io-roots [\"app\" \"services\"]");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF106"),
        vec!["app/billing/runtime/handlers.hy::DOEFF106::open_one::socket.socket", "services/record/main.hy::DOEFF106::serve::threading.Thread"],
        "{}",
        report
    );
    assert_eq!(keys(&report, "DOEFF131"), vec!["services/record/main.hy::DOEFF131::run::world::doeff_core_effects.os_file:os-file-handler"], "{}", report);
    let found = violation(&report, "services/record/main.hy::DOEFF131::run::world::doeff_core_effects.os_file:os-file-handler");
    assert!(found["message"].as_str().unwrap().contains("層の置き場の外"), "{}", found["message"]);
    assert_eq!(found["level"], "critical");
}

/// agora-redesign #1143(R5): service の entry の層の定義に、模擬の環境(:verification-environment)の下の deftest が 1 本も届かなければ
/// defservice の位置で DOEFF136(critical)。模擬の環境のテストが組み立てを呼ぶ service と、entry の層を持たない service は当てない。
/// service の中のテスト(app/ledger/tests)が呼んでいても、模擬の環境の外なので数えない。billing は defsystem を挟んで届く(defsystem も呼び出しの持ち主)。
#[test]
fn services_not_run_on_the_sim_are_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(defk run [] 1)\n"),
        ("app/ledger/entry/main.hy", tags("ledger", "entry") + "(defk start [] 2)\n"),
        ("app/ledger/tests/test_own.hy", "(import app.ledger.entry.main [start])\n(deftest test-own (start))\n".to_string()),
        ("app/sim/bench.hy", "(import app.billing.entry.main [run])\n(defsystem billing-bench [foundation] (run))\n".to_string()),
        ("app/sim/tests/test_billing.hy", "(import app.sim.bench [billing-bench])\n(deftest test-billing-on-sim (billing-bench None))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF136\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :verification-environment \"sim\"")
        + "(defservice ledger \"台帳\" {:layers [core entry]})\n(defservice notes \"覚え書き\" {:layers [core]})\n";
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF136"), vec!["architecture.hy::DOEFF136::ledger"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF136::ledger");
    assert!(found["message"].as_str().unwrap().contains("service ledger の entry の層("), "{}", found["message"]);
    assert_eq!(found["severity"], "error", "{}", found);
}

/// agora-redesign #1559(#1155 の K1): code を持つ service(entry の層に定義が 1 本以上)が defservice に :invariants(`module:関数` の列)を
/// 宣言していない・名指した関数が実在しない・その関数の :role が judgment でない時、defservice の位置で DOEFF163(critical)。
/// entry の層を持たない service と、entry の層に定義の無い service は当てない。登録簿に載った欠けは warning に下がる。
#[test]
fn services_without_declared_invariants_are_red() {
    let judged = "{:pre [(: seen list)] :post [(: % list)] :tags {:context \"sim\" :role \"judgment\"}}";
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(defk run [] 1)\n"),
        ("app/ledger/entry/main.hy", tags("ledger", "entry") + "(defk start [] 2)\n"),
        ("app/orders/entry/main.hy", tags("orders", "entry") + "(defk take [] 3)\n"),
        ("app/stock/entry/main.hy", tags("stock", "entry") + "(defk count-all [] 4)\n"),
        (
            "app/sim/invariants.hy",
            format!(
                "(defk billing-holds [seen]\n  {}\n  \"請求の不変条件。\"\n  [])\n\
                 (defk orders-run [seen]\n  {{:pre [(: seen list)] :post [(: % list)] :tags {{:context \"sim\" :role \"program\"}}}}\n  \"判断でない。\"\n  [])\n\
                 (defk stock-holds [seen]\n  {}\n  \"在庫の不変条件。\"\n  [])\n",
                judged, judged
            ),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF163\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace("{:layers [core entry]})", "{:layers [core entry] :invariants [\"app.sim.invariants:billing-holds\"]})")
        + "(defservice ledger \"台帳\" {:layers [core entry]})\n\
           (defservice orders \"注文\" {:layers [core entry] :invariants [\"app.sim.invariants:gone\" \"app.sim.invariants:orders-run\"]})\n\
           (defservice stock \"在庫\" {:layers [core entry]})\n\
           (defservice notes \"覚え書き\" {:layers [core]})\n\
           (defservice empty \"空\" {:layers [core entry]})\n";
    std::fs::write(&arch_path, text).unwrap();
    // 既知の欠け: stock の宣言の欠けは登録簿に載っている(warning に下がる)。
    let pyproject = dir.path().join("pyproject.toml");
    let settings = std::fs::read_to_string(&pyproject).unwrap() + "[tool.doeff-linter.registry]\nfiles = [\"registry/keys.txt\"]\n";
    std::fs::write(&pyproject, settings).unwrap();
    std::fs::create_dir_all(dir.path().join("registry")).unwrap();
    std::fs::write(dir.path().join("registry/keys.txt"), "architecture.hy::DOEFF163::stock\n").unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF163"),
        vec![
            "architecture.hy::DOEFF163::ledger",
            "architecture.hy::DOEFF163::orders::app.sim.invariants:gone",
            "architecture.hy::DOEFF163::orders::app.sim.invariants:orders-run",
            "architecture.hy::DOEFF163::stock",
        ],
        "{}",
        report
    );
    let missing = violation(&report, "architecture.hy::DOEFF163::ledger");
    assert!(missing["message"].as_str().unwrap().contains("service ledger は :invariants を宣言していない"), "{}", missing["message"]);
    assert_eq!(missing["level"], "critical", "{}", missing);
    let gone = violation(&report, "architecture.hy::DOEFF163::orders::app.sim.invariants:gone");
    assert!(gone["message"].as_str().unwrap().contains("定義が無い"), "{}", gone["message"]);
    let role = violation(&report, "architecture.hy::DOEFF163::orders::app.sim.invariants:orders-run");
    assert!(role["message"].as_str().unwrap().contains(":role が program"), "{}", role["message"]);
    let known = violation(&report, "architecture.hy::DOEFF163::stock");
    assert_eq!(known["severity"], "warning", "{}", known);
    assert_eq!(known["registered"], true, "{}", known);
}

/// agora-redesign #1560(K2): entry の層を持つ service ごとに、壊した handler の反例(反例の表の節に届き、その service の entry にも届く
/// deftest)が無ければ defservice の位置で DOEFF164(critical)。billing は自分の業務の効果の壊した handler を自分の検で回す(有り)。
/// stock は土台の効果(どの service の dir の下にも無い効果)の壊した handler を模擬の検で回す(有り — 表の当たりにも数え、腐りにしない)。
/// ledger は模擬の検が entry に届くが反例が無い(赤)。notes は entry の層を持たない(数えない)。
#[test]
fn services_without_a_counterexample_are_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/foundation/clock_effects.hy", "(import doeff [EffectBase])\n(defclass Now [EffectBase])\n".to_string()),
        ("app/billing/intent/effects.hy", "(import doeff [EffectBase])\n(defclass Charge [EffectBase])\n".to_string()),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(import app.billing.intent.effects [Charge])\n(defk run [] (Charge))\n"),
        (
            "app/billing/tests/test_broken.hy",
            "(import app.billing.intent.effects [Charge])\n(import app.billing.entry.main [run])\n\
             (defhandler broken-charge (Charge [] (resume 0)))\n(deftest test-broken-charge (with-handlers [broken-charge] (run)))\n"
                .to_string(),
        ),
        ("app/ledger/entry/main.hy", tags("ledger", "entry") + "(defk start [] 2)\n"),
        ("app/sim/tests/test_ledger.hy", "(import app.ledger.entry.main [start])\n(deftest test-ledger-on-sim (start))\n".to_string()),
        ("app/stock/entry/main.hy", tags("stock", "entry") + "(import app.foundation.clock_effects [Now])\n(defk tick [] (Now))\n"),
        ("app/sim/broken_clock.hy", "(import app.foundation.clock_effects [Now])\n(defhandler stopped-clock (Now [] (resume 0)))\n".to_string()),
        (
            "app/sim/tests/test_stock.hy",
            "(import app.sim.broken_clock [stopped-clock])\n(import app.stock.entry.main [tick])\n\
             (deftest test-stopped-clock (with-handlers [stopped-clock] (tick)))\n"
                .to_string(),
        ),
        (
            "tables/COUNTEREXAMPLES/billing.txt",
            "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\n請求を 0 で返す壊した handler\n".to_string(),
        ),
        ("tables/COUNTEREXAMPLES/stock.txt", "app/sim/broken_clock.hy::stopped-clock::app.foundation.clock_effects.Now\n止まった時計\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF157\", \"DOEFF164\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes)
        + "(defservice ledger \"台帳\" {:layers [core entry]})\n(defservice stock \"在庫\" {:layers [core entry]})\n(defservice notes \"覚え書き\" {:layers [core]})\n";
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF164"), vec!["architecture.hy::DOEFF164::ledger"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF164::ledger");
    assert!(found["message"].as_str().unwrap().contains("service ledger に壊した handler の反例が無い"), "{}", found["message"]);
    assert_eq!(found["severity"], "error", "{}", found);
    // 土台の効果に答える壊した handler の行は表の当たり(腐りの赤にしない)。
    assert!(keys(&report, "DOEFF143").iter().all(|k| !k.contains("counterexample-unused")), "{}", report);
}

/// agora-redesign #1818(#1810 の子): DOEFF143(模擬の偽物)と DOEFF157(検だけの偽物)を規則の ID で名指して確かめる。
/// 模擬の環境の fake-charge と検の file の test-charge は業務の効果 Charge に tap でなく答える(鳴る例)。同じ置き場の、効果を出し直す
/// tap の handler(watch-charge・watch-refund)と、反例の表に在るわざと壊した handler(broken-refund・broken-charge)は鳴らない(鳴らない例)。
#[test]
fn simulation_fakes_and_test_only_fakes_are_red_but_taps_and_counterexamples_are_not() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/intent/effects.hy", "(import doeff [EffectBase])\n(defclass Charge [EffectBase])\n(defclass Refund [EffectBase])\n".to_string()),
        (
            "app/sim/fake_billing.hy",
            "(import app.billing.intent.effects [Charge Refund])\n\
             (defhandler fake-charge (Charge [] (resume 0)))\n\
             (defhandler watch-charge (Charge [] (resume (Charge))))\n\
             (defhandler broken-refund (Refund [] (resume 0)))\n"
                .to_string(),
        ),
        (
            "app/billing/tests/test_fakes.hy",
            "(import app.billing.intent.effects [Charge Refund])\n\
             (defhandler test-charge (Charge [] (resume 1)))\n\
             (defhandler watch-refund (Refund [] (resume (Refund))))\n\
             (defhandler broken-charge (Charge [] (resume 0)))\n\
             (deftest test-with-handlers (with-handlers [test-charge watch-refund broken-charge] 1))\n"
                .to_string(),
        ),
        ("tables/COUNTEREXAMPLES/sim_refund.txt", "app/sim/fake_billing.hy::broken-refund::app.billing.intent.effects.Refund\n返金を 0 で返す壊した handler\n".to_string()),
        (
            "tables/COUNTEREXAMPLES/test_charge.txt",
            "app/billing/tests/test_fakes.hy::broken-charge::app.billing.intent.effects.Charge\n請求を 0 で返す壊した handler\n".to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF157\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes);
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    let fake = "app/sim/fake_billing.hy::DOEFF143::fake-charge::app.billing.intent.effects.Charge";
    assert_eq!(keys(&report, "DOEFF143"), vec![fake], "{}", report);
    assert!(violation(&report, fake)["message"].as_str().unwrap().contains("偽物(模擬の根からだけ届く)"), "{}", violation(&report, fake));
    let test_only = "app/billing/tests/test_fakes.hy::DOEFF157::test-charge::app.billing.intent.effects.Charge";
    assert_eq!(keys(&report, "DOEFF157"), vec![test_only], "{}", report);
    assert!(violation(&report, test_only)["message"].as_str().unwrap().contains("検だけの偽物"), "{}", violation(&report, test_only));
    assert_eq!(violation(&report, test_only)["severity"], "error");
}

/// agora-redesign #1713: DOEFF164 は service に反例が 1 本あれば緑。DOEFF167 は defservice の :clauses の条ごとに、反例の表の行の
/// `breaks: <service>::<条>` で名乗る壊した handler の反例(節に届く deftest が service の entry にも届く)か、:clause-exemptions の理由を求める。
/// billing: B1 は反例が有る・B2 は名乗る行が無い(赤)・B3 は理由つきで外した。stock: 土台の効果の反例が S1 を名乗る(有り — 陽性対照)。
/// ledger: :clauses を書かない(service の名で赤)。notes は entry の層を持たない(数えない)。B9 を名乗る行は設定の誤り。
#[test]
fn clauses_without_a_counterexample_are_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/foundation/clock_effects.hy", "(import doeff [EffectBase])\n(defclass Now [EffectBase])\n".to_string()),
        ("app/billing/intent/effects.hy", "(import doeff [EffectBase])\n(defclass Charge [EffectBase])\n".to_string()),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(import app.billing.intent.effects [Charge])\n(defk run [] (Charge))\n"),
        (
            "app/billing/tests/test_broken.hy",
            "(import app.billing.intent.effects [Charge])\n(import app.billing.entry.main [run])\n\
             (defhandler broken-charge (Charge [] (resume 0)))\n(deftest test-broken-charge (with-handlers [broken-charge] (run)))\n"
                .to_string(),
        ),
        ("app/ledger/entry/main.hy", tags("ledger", "entry") + "(defk start [] 2)\n"),
        ("app/sim/tests/test_ledger.hy", "(import app.ledger.entry.main [start])\n(deftest test-ledger-on-sim (start))\n".to_string()),
        ("app/stock/entry/main.hy", tags("stock", "entry") + "(import app.foundation.clock_effects [Now])\n(defk tick [] (Now))\n"),
        ("app/sim/broken_clock.hy", "(import app.foundation.clock_effects [Now])\n(defhandler stopped-clock (Now [] (resume 0)))\n".to_string()),
        (
            "app/sim/tests/test_stock.hy",
            "(import app.sim.broken_clock [stopped-clock])\n(import app.stock.entry.main [tick])\n\
             (deftest test-stopped-clock (with-handlers [stopped-clock] (tick)))\n"
                .to_string(),
        ),
        (
            "tables/COUNTEREXAMPLES/billing.txt",
            "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\n請求を 0 で返す壊した handler\nbreaks: billing::B1\n"
                .to_string(),
        ),
        (
            "tables/COUNTEREXAMPLES/stock.txt",
            "app/sim/broken_clock.hy::stopped-clock::app.foundation.clock_effects.Now\n止まった時計\nbreaks: stock::S1\n".to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF157\", \"DOEFF167\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let base = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes).replace(
        "(defservice billing \"請求\" {:layers [core entry]})",
        "(defservice billing \"請求\" {:layers [core entry] :clauses [\"B1\" \"B2\" \"B3\"] :clause-exemptions {\"B3\" \"壊した handler では破れない(構造の保証)\"}})",
    ) + "(defservice ledger \"台帳\" {:layers [core entry]})\n(defservice stock \"在庫\" {:layers [core entry] :clauses [\"S1\"]})\n\
         (defservice notes \"覚え書き\" {:layers [core]})\n";
    assert!(base.contains(":clauses [\"B1\""), "billing の宣言を差し替えられない:\n{}", base);
    std::fs::write(&arch_path, &base).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF167"), vec!["architecture.hy::DOEFF167::billing::B2", "architecture.hy::DOEFF167::ledger"], "{}", report);
    let gap = violation(&report, "architecture.hy::DOEFF167::billing::B2");
    assert!(gap["message"].as_str().unwrap().contains("service billing の条 B2 に壊した handler の反例が無い"), "{}", gap["message"]);
    assert_eq!(gap["severity"], "error", "{}", gap);
    let undeclared = violation(&report, "architecture.hy::DOEFF167::ledger");
    assert!(undeclared["message"].as_str().unwrap().contains("(:clauses)を宣言していない"), "{}", undeclared["message"]);
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);

    // 反例: 宣言に無い条を名乗る行は設定の誤り(黙って数えない)。
    std::fs::write(
        dir.path().join("tables/COUNTEREXAMPLES/billing.txt"),
        "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\n請求を 0 で返す壊した handler\nbreaks: billing::B1 billing::B9\n",
    )
    .unwrap();
    let (_, report) = editor(dir.path());
    assert!(report["errors"].to_string().contains("条 B9 は service billing の :clauses に無い"), "{}", report["errors"]);

    // 陽性対照: B2 を名乗り(同じ反例が B1 と B2 を破る)、ledger が条を理由つきで全部外せば、DOEFF167 は 0。
    std::fs::write(
        dir.path().join("tables/COUNTEREXAMPLES/billing.txt"),
        "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\n請求を 0 で返す壊した handler\nbreaks: billing::B1 billing::B2\n",
    )
    .unwrap();
    let covered = base.replace(
        "(defservice ledger \"台帳\" {:layers [core entry]})",
        "(defservice ledger \"台帳\" {:layers [core entry] :clauses [\"L1\"] :clause-exemptions {\"L1\" \"理由\"}})",
    );
    std::fs::write(&arch_path, covered).unwrap();
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF167").is_empty(), "{:?}", keys(&report, "DOEFF167"));
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
}

/// agora-redesign #1562(K4): intent の効果の網羅の欠け(DOEFF165・K3 の表)は critical で失敗にする。今ある欠けは登録簿
/// (1 鍵 1 file の dir)に載せ、載った欠けは warning に下がる。Charge と Refund はどちらも検から出さず答え手も無い(欠け)—
/// Refund だけが登録簿に載っている。Settle は 3 列がすべて埋まり出ない(鳴らない例・#1818)。
#[test]
fn intent_effect_coverage_gaps_are_red_unless_registered() {
    let intent = |name: &str| {
        format!("(defeffect {} \"{}\" {{:fields [amount] :answer int :tags {{:context \"billing\" :role \"intent\"}}}})\n", name, name)
    };
    // 鳴らない例(agora-redesign #1818): Settle は 3 列がすべて埋まる — deftest が出し、翻訳の層(ここでは entry)の settle-reads が
    // 模擬の根(組み立ての層の file)からも本番の入口(組の file の production-handlers)からも届く。
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/intent/effects.hy", intent("Charge") + &intent("Refund") + &intent("Settle")),
        (
            "app/billing/entry/translate.hy",
            tags("billing", "entry")
                + "(require doeff-hy.macros [defhandler])\n(import app.billing.intent.effects [Settle])\n\
                   (defhandler settle-reads []\n  (Settle [amount] (resume amount)))\n",
        ),
        (
            "app/billing/entry/handler_sets.hy",
            tags("billing", "entry") + "(import app.billing.entry.translate [settle-reads])\n(defk production-handlers [] [settle-reads])\n",
        ),
        ("app/billing/tests/test_settle.hy", "(import app.billing.intent.effects [Settle])\n(deftest test-settle (Settle 1))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF165\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declarations = ":foundation foundation\n  :verification-environment \"sim\"\n  \
                        :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] :tests [\"**/tests/**\"] \
                        :production [\"app/**\"] :business-modules [\"app.billing\"] :sets [\"app/*/entry/handler_sets.hy\"] \
                        :production-prefix \"production\"}\n  \
                        :assembly-shape {:translation-point \"with-*-translation\" :retired-function \"handlers-of\" \
                        :translations \"TRANSLATION-HANDLERS\" :translation-layer \"entry\" :intent-layer \"intent\"}";
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", declarations)
        .replace("(layer entry", "(layer intent :roles [intent] :imports [core intent])\n           (layer entry")
        .replace("(defservice billing \"請求\" {:layers [core entry]})", "(defservice billing \"請求\" {:layers [core intent entry]})");
    std::fs::write(&arch_path, text).unwrap();
    let pyproject = dir.path().join("pyproject.toml");
    let settings = std::fs::read_to_string(&pyproject).unwrap() + "[tool.doeff-linter.registry]\ndirs = [\"registry/EFFECT-COVERAGE-BREACHES\"]\n";
    std::fs::write(&pyproject, settings).unwrap();
    let registry = dir.path().join("registry/EFFECT-COVERAGE-BREACHES");
    std::fs::create_dir_all(&registry).unwrap();
    std::fs::write(
        registry.join("refund.txt"),
        "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Refund\n既知の欠け(検の見本)\n",
    )
    .unwrap();
    let (_, report) = editor(dir.path());
    let charge = "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Charge";
    let refund = "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Refund";
    // 完全一致: 3 列の埋まった Settle は出ない。
    assert_eq!(keys(&report, "DOEFF165"), vec![charge, refund], "{}", report);
    let new_gap = violation(&report, charge);
    assert_eq!(new_gap["severity"], "error", "{}", new_gap);
    assert_eq!(new_gap["level"], "critical", "{}", new_gap);
    let known = violation(&report, refund);
    assert_eq!(known["severity"], "warning", "{}", known);
    assert_eq!(known["registered"], true, "{}", known);
}

/// DOEFF150・151 の宣言を architecture.hy に足した一時の repo。語・呼びは agora-controllers の宣言(#1193 の移し元 check_vocabulary・
/// check_controller_clock が数えていた物)と同じ綴りを検の材料として書く — linter の本体は語の表を持たない。
fn retired_repo(files: &[(&str, String)]) -> tempfile::TempDir {
    let dir = world_repo_with(files, "", "[\"DOEFF150\", \"DOEFF151\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declarations = r#":foundation foundation
  :retired-words [(retired-words "vocabulary" :words ["席" "mailbox" "letter" "auth home" "mail" "掃引"]
                    :files ["README.md" "app/**/*.hy" "app/**/*.md" "app/**/*.json" "app/**/*.py" "app/**/*.sh"] :except ["app/terms.json"]
                    :rule-lines ["使わない" "置かない"] :instead "chat・agent・器 / Message / 退役 / 見回り")
                  (retired-words "conversation-means-agent"
                    :patterns [r"会話\s*[（(]\s*(?:意味は|=)\s*agent" r"(?i)\bconversation\s+(?:means|is|=)\s+(?:an?\s+|one\s+)?agent\b"]
                    :files ["docs/**/*.md" "docs/**/*.txt"] :instead "会話の id は chat の id、参加者は agent と書く")
                  (retired-words "conversation-names" :patterns [r"(?i)conversation"] :files ["app/chat/**/*.hy" "app/chat/**/*.py"]
                    :in names :instead "chat・agent・participation")]
  :retired-calls [(retired-calls "clock" :calls ["Now" "Elapsed" "EpochMillis" "ReadClock" "time.time"]
                    :files ["app/**/*.hy"] :except ["app/**/tests/**"] :instead "(GetMonotonic) か (GetTime)(doeff-time)")]"#;
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declarations);
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// agora-redesign #1193(C11): 使わないと決めた語は語ごとに 1 件ずつ当たり(反例 = 各語 1 本)、別の語の一部・規則を述べる行・宣言の外の
/// file・:except の file は当たらない。意味の綴りは正規表現で、定義の名の群は定義の名だけを見る。新しい当たりは critical。
#[test]
fn retired_words_hit_once_per_word_and_skip_rule_lines() {
    let words = ["席", "mailbox", "letter", "auth home", "mail", "掃引"];
    let mut files: Vec<(String, String)> = words.iter().enumerate().map(|(i, w)| (format!("app/billing/w{}.hy", i), format!(";; 見出し\n(setv note \"本文に {} を書く\")\n", w))).collect();
    files.extend([
        ("README.md".to_string(), "郵便は Message と書く。email address と mail-box は別の語。\n使わない語 = 席 / mail\n".to_string()),
        ("docs/README.md".to_string(), "mail は根の README ではないので宣言の外\n".to_string()),
        ("app/terms.json".to_string(), "{\"terms\": [{\"term\": \"mail\"}]}\n".to_string()),
        ("docs/design/turns.txt".to_string(), "会話(意味は agent)が chat を受ける\nconversation means agent\n会話は agent の手番の列を持つ\n".to_string()),
        (
            "app/chat/rows.hy".to_string(),
            "(defrecord ConversationSlice (#^ str chat))\n(setv CHAT-KIND \"conversation\") ; conversation の綴りは値と註だけ\n(defk chat-of [row] row.chat)\n"
                .to_string(),
        ),
    ]);
    let refs: Vec<(&str, String)> = files.iter().map(|(p, t)| (p.as_str(), t.clone())).collect();
    let dir = retired_repo(&refs);
    let (_, report) = editor(dir.path());
    let mut expected: Vec<String> = words.iter().enumerate().map(|(i, w)| format!("app/billing/w{}.hy::DOEFF150::{}", i, w)).collect();
    // 意味の綴りは 2 行(日本語の形と英語の形)に当たり、どちらも群の名の鍵になる。
    expected.extend([
        "app/chat/rows.hy::DOEFF150::conversation-names".to_string(),
        "docs/design/turns.txt::DOEFF150::conversation-means-agent".to_string(),
        "docs/design/turns.txt::DOEFF150::conversation-means-agent".to_string(),
    ]);
    expected.sort();
    assert_eq!(keys(&report, "DOEFF150"), expected, "{}", report);
    let turns: Vec<&Value> = report["violations"].as_array().unwrap().iter().filter(|v| v["key"] == "docs/design/turns.txt::DOEFF150::conversation-means-agent").collect();
    assert_eq!(turns.len(), 2, "意味の綴りは行ごとに 1 件: {:?}", turns);
    let mail = violation(&report, "app/billing/w4.hy::DOEFF150::mail");
    assert_eq!(mail["level"], "critical", "{}", mail);
    assert_eq!(mail["range"]["start"]["line"], 1);
    assert!(mail["message"].as_str().unwrap().contains("使わないと決めた綴り mail(群 vocabulary・代わり:"), "{}", mail["message"]);
    let names = violation(&report, "app/chat/rows.hy::DOEFF150::conversation-names");
    assert!(names["message"].as_str().unwrap().contains("定義の名 ConversationSlice"), "{}", names["message"]);
}

/// agora-redesign #1794(#1762 の決定 Q2-3): `:in lines` は実際に使う code の中の綴りだけを数える — Hy の記号・欄名・command の
/// 文字列・ほかの文字列と Python・shell の code は鳴り、註(Hy の `;`・Python と shell の `#`)・定義の docstring・`.md` の本文は鳴らない。
#[test]
fn retired_words_count_code_not_comments_docstrings_or_markdown() {
    let dir = retired_repo(&[
        ("app/billing/symbol.hy", ";; mail の註\n(setv mail 1)\n".to_string()),
        ("app/billing/field.hy", "(defrecord Row (#^ str letter))\n".to_string()),
        ("app/billing/command.hy", "(defk run [] (RunProcess \"cd x && mail -s hi\"))\n".to_string()),
        ("app/billing/code.py", "\"\"\"mail の module\"\"\"\nimport os  # mail は註\nsend = os.environ[\"mail\"]\n".to_string()),
        ("app/billing/run.sh", "# mail の註\nexec mail -s hi\n".to_string()),
        (
            "app/billing/quiet.hy",
            ";;; mail の頭の註\n(defk send [x]\n  \"mail を送るため\"\n  x) ; letter は註\n(defclass Box [] \"mail の箱\" (setv n 1))\n\
             (setv note \"email address と mail-box は別の語\")\n(setv rule \"mail\") ; 使わない語の例\n"
                .to_string(),
        ),
        ("app/billing/prose.md", "# mail\n本文の mail と letter\n```sh\nexec mail -s hi\n```\n".to_string()),
        ("app/billing/quiet.py", "def f():\n    '''mail の関数'''\n    return 1  # letter\n".to_string()),
        ("app/billing/quiet.sh", "#!/bin/sh\n# mail の註\necho $# ${#x}  # letter\n".to_string()),
    ]);
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF150"),
        vec![
            "app/billing/code.py::DOEFF150::mail",
            "app/billing/command.hy::DOEFF150::mail",
            "app/billing/field.hy::DOEFF150::letter",
            "app/billing/run.sh::DOEFF150::mail",
            "app/billing/symbol.hy::DOEFF150::mail",
        ],
        "{}",
        report
    );
    let symbol = violation(&report, "app/billing/symbol.hy::DOEFF150::mail");
    assert_eq!(symbol["range"]["start"]["line"], 1, "註の行でなく code の行: {}", symbol);
}

/// agora-redesign #1369: `:in paths` の群は file の名(最後の `.` より前)だけを見る — 退役した名の file は語ごとに 1 件、中身・dir の名・
/// 別の語の一部は当たらない。:rule-lines は :in lines の時だけ効くので、:in paths と組むと宣言の問題になる。
#[test]
fn retired_paths_hit_the_file_name_only() {
    let dir = world_repo_with(
        &[
            ("app/operators/worker.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/design_request.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/worker_pool.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/probe.hy", "(setv worker 1) ; worker は中身だけ\n".to_string()),
            ("app/worker/probe.hy", "(defk run [] 1)\n".to_string()),
        ],
        "",
        "[\"DOEFF150\"]",
    );
    let arch_path = dir.path().join("architecture.hy");
    let declarations = r#":foundation foundation
  :retired-words [(retired-words "retired-operators" :words ["worker" "design_request"] :files ["app/operators/*.hy"]
                    :in paths :instead "退役した operator の file を置き直さない")]"#;
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declarations);
    std::fs::write(&arch_path, &text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF150"),
        vec!["app/operators/design_request.hy::DOEFF150::design_request", "app/operators/worker.hy::DOEFF150::worker"],
        "{}",
        report
    );
    let worker = violation(&report, "app/operators/worker.hy::DOEFF150::worker");
    assert!(worker["message"].as_str().unwrap().contains("file の名に使わないと決めた綴り worker"), "{}", worker["message"]);
    assert_eq!(worker["level"], "critical", "{}", worker);
    std::fs::write(&arch_path, text.replace(":in paths", ":in paths :rule-lines [\"使わない\"]")).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--no-log"], None);
    assert!(stderr.contains(":rule-lines は :in lines の時だけ効く"), "{}", stderr);
    assert_ne!(code, 0, "宣言の誤りは走らせずに止まる");
}

/// agora-redesign #1193(C11): 退役した呼びは呼びごとに 1 件ずつ当たり(反例 = 各呼び 1 本)、註・文字列・読み捨てた form・:except の
/// テストの file・値として名指すだけの所は当たらない。
#[test]
fn retired_calls_hit_once_per_call() {
    let calls = ["Now", "Elapsed", "EpochMillis", "ReadClock", "time.time"];
    let mut files: Vec<(String, String)> = calls.iter().enumerate().map(|(i, c)| (format!("app/billing/c{}.hy", i), format!("(defk stamp [] ({}))\n", c))).collect();
    files.extend([
        (
            "app/billing/quiet.hy".to_string(),
            ";; 効果 (Now) と (Elapsed) は退役した(経過は (GetMonotonic))\n(setv note \"(time.time)\")\n#_(Now)\n(setv clock Now)\n(defk ok [] (GetMonotonic))\n"
                .to_string(),
        ),
        ("app/billing/tests/test_clock.hy".to_string(), "(deftest test-clock (Now))\n".to_string()),
    ]);
    let refs: Vec<(&str, String)> = files.iter().map(|(p, t)| (p.as_str(), t.clone())).collect();
    let dir = retired_repo(&refs);
    let (_, report) = editor(dir.path());
    let mut expected: Vec<String> = calls.iter().enumerate().map(|(i, c)| format!("app/billing/c{}.hy::DOEFF151::{}", i, c)).collect();
    expected.sort();
    assert_eq!(keys(&report, "DOEFF151"), expected, "{}", report);
    let now = violation(&report, "app/billing/c0.hy::DOEFF151::Now");
    assert_eq!(now["level"], "critical");
    assert_eq!(now["range"]["start"]["character"], 16, "位置は呼びの頭の記号: {}", now);
}

/// agora-redesign #1193 の条件: file 1 つで判じられる規則は、名指しの path が在ればその下の file だけを読む(repo 全体を読まない)。
/// 全体の実行は宣言の file を全部読む — 読めない file(UTF-8 でない)が理由に出るかどうかで、読んだ file の母集団を見分ける。
#[test]
fn retired_rules_read_only_the_named_files() {
    let dir = retired_repo(&[("app/billing/named.hy", "(setv note \"mail を送る\")\n".to_string())]);
    std::fs::write(dir.path().join("app/billing/broken.md"), [0xffu8, 0xfe, b'\n']).unwrap();
    let (_, whole_out, whole_err) = run(dir.path(), &["--no-log"], None);
    assert!(whole_err.contains("app/billing/broken.md: 読めない"), "全体の実行は宣言の file を全部読む: {}\n{}", whole_err, whole_out);
    let (code, named_out, named_err) = run(dir.path(), &["--no-log", "app/billing/named.hy"], None);
    assert!(!named_err.contains("broken.md"), "名指しの実行は名指しの file だけを読む: {}", named_err);
    assert!(named_out.contains("DOEFF150") && named_out.contains("named.hy"), "名指しの file の当たりは出る: {}\n{}", named_out, named_err);
    assert_ne!(code, 0, "新しい当たりは error");
}

/// 宣言の読み違い(正規表現が読めない・普通の文字列の \・:files が無い・:in の語の外)は、位置つきの設定の誤りにする。
#[test]
fn retired_declaration_misreadings_are_config_errors() {
    let dir = world_repo_with(&[], "", "[\"DOEFF150\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :retired-words [(retired-words \"a\" :patterns [r\"(\"] :files [\"x/**\"] :instead \"y\")\n                  (retired-words \"b\" :patterns [\"\\\\s+\"] :files [\"x/**\"] :instead \"y\")\n                  (retired-words \"c\" :words [\"z\"] :instead \"y\" :in everywhere)]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("retired-words a の正規表現 ( を読めない"), "{}", all);
    assert!(all.contains(":patterns の正規表現は r\"…\" の文字列で書く"), "{}", all);
    assert!(all.contains("retired-words c に :files が無い"), "{}", all);
    assert!(all.contains(":in は lines か names"), "{}", all);
}

/// DOEFF144・145 の宣言を architecture.hy に足した一時の repo(読む file の glob は agora-controllers の宣言と同じ形)。
fn typed_repo(files: &[(&str, String)]) -> tempfile::TempDir {
    let dir = world_repo_with(files, "", "[\"DOEFF144\", \"DOEFF145\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declarations = r#":foundation foundation
  :typed-values {:files ["app/**/*.hy" "app/**/*.py"] :except ["**/tests/**" "**/adr/**" "**/__pycache__/**"]}
  :record-stubs {:files ["app/**/*.pyi"] :except ["**/__pycache__/**"]}"#;
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declarations);
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// agora-redesign #1191: 公開面の型の注記の素の写像・素の組は、欄・戻り値・:post・答えの組の 4 種類の鍵で 1 か所 1 件ずつ当たる。
/// 内部の名・入れ子の関数・:except の file・宣言の外の file は当たらない。直した形(欄の名前と型を持つ record)は何も出さない。
#[test]
fn typed_values_red_on_counterexamples_and_empty_on_typed_forms() {
    let bad = "(defrecord Charge (#^ dict meta) (#^ (get dict #(str Row)) index))\n\
               (defclass Plain [] #^ (get tuple #(str int)) pair)\n\
               (defk decide [x] {:pre [(: x int)] :post [(: % tuple)]} x)\n\
               (deff #^ Row named [x] {:post [(: % tuple)]} x)\n\
               (deff #^ Row mapped [x] {:post [(: % Mapping)]} x)\n\
               (defn #^ JsonValue load [x] x)\n\
               (defn split [x] (defn inner [] #(1 2)) (when x (return #(x x))) x)\n\
               (defn #^ dict _private [] 1)\n";
    let py = "from typing import Any\nclass Row:\n    meta: dict[str, Any]\n    ok: dict[str, int]\n    def pair(self) -> tuple[int, str]: ...\ndef rows() -> 'list[dict]': ...\n";
    let good = "(defrecord Charge (#^ Meta meta) (#^ (get dict #(str Row)) index))\n(defk decide [x] {:post [(: % Decision)]} (Decision :x x))\n(defn #^ (get tuple #(Row ...)) rows [] #())\n";
    let dir = typed_repo(&[
        ("app/core/bad.hy", bad.to_string()),
        ("app/core/bad_py.py", py.to_string()),
        ("app/core/good.hy", good.to_string()),
        ("app/core/tests/test_x.hy", "(defn #^ dict helper [] 1)\n".to_string()),
        ("other/outside.hy", "(defn #^ dict helper [] 1)\n".to_string()),
    ]);
    let (code, report) = editor(dir.path());
    let mut expected: Vec<String> = [
        "app/core/bad.hy::DOEFF144::field:Charge.meta",
        "app/core/bad.hy::DOEFF144::field:Plain.pair",
        "app/core/bad.hy::DOEFF144::post:decide",
        "app/core/bad.hy::DOEFF144::post:mapped",
        "app/core/bad.hy::DOEFF144::return:load",
        "app/core/bad.hy::DOEFF144::pair:split",
        "app/core/bad_py.py::DOEFF144::field:Row.meta",
        "app/core/bad_py.py::DOEFF144::return:Row.pair",
        "app/core/bad_py.py::DOEFF144::return:rows",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    expected.sort();
    assert_eq!(keys(&report, "DOEFF144"), expected, "{}", report);
    assert_ne!(code, 0, "新しい当たりは error");
    let decide = violation(&report, "app/core/bad.hy::DOEFF144::post:decide");
    assert_eq!(decide["level"], "critical", "{}", decide);
    assert!(decide["message"].as_str().unwrap().contains("答え(:post・名の注釈なし) decide の型が 素の組 tuple"), "{}", decide["message"]);
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report);
}

/// agora-redesign #1191: .hy で kw-only の record を kw_only=True の無い @dataclass で宣言する .pyi は class ごとに 1 件。直した .pyi・
/// 同じ名の .hy の無い .pyi・@dataclass の無い class は当たらない。
#[test]
fn record_stubs_red_when_kw_only_record_stub_lacks_kw_only() {
    let hy = "(defrecord Row (#^ str a))\n(defclass [(dataclass :frozen True :kw-only True)] Manual [] (#^ int n))\n(defrecord Fine (#^ str b))\n(defrecord Bare (#^ str c))\n";
    let stub = "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row:\n    a: str\n@dataclass\nclass Manual:\n    n: int\n\
                @dataclass(frozen=True, kw_only=True)\nclass Fine:\n    b: str\nclass Bare:\n    c: str\n";
    let dir = typed_repo(&[
        ("app/core/rows.hy", hy.to_string()),
        ("app/core/rows.pyi", stub.to_string()),
        ("app/core/lonely.pyi", "from dataclasses import dataclass\n@dataclass\nclass Row:\n    a: str\n".to_string()),
    ]);
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF145"), vec!["app/core/rows.pyi::DOEFF145::Manual".to_string(), "app/core/rows.pyi::DOEFF145::Row".to_string()], "{}", report);
    let row = violation(&report, "app/core/rows.pyi::DOEFF145::Row");
    assert_eq!(row["level"], "critical", "{}", row);
    assert_eq!(row["range"]["start"]["line"], 1, "位置は @dataclass の飾り: {}", row);
    // 直した形は何も出さない。
    std::fs::write(dir.path().join("app/core/rows.pyi"), stub.replace("@dataclass(frozen=True)\nclass Row", "@dataclass(frozen=True, kw_only=True)\nclass Row").replace("@dataclass\nclass Manual", "@dataclass(kw_only=True)\nclass Manual")).unwrap();
    let (_, fixed) = editor(dir.path());
    assert!(keys(&fixed, "DOEFF145").is_empty(), "{}", fixed);
}

/// agora-redesign #1191 の条件: DOEFF144・145 は file 1 つで判じるので、名指しの path が在ればその下の file だけを読む(repo 全体を
/// 読まない)。全体の実行は宣言の file を全部読む — 読めない file(UTF-8 でない)が理由に出るかどうかで、読んだ母集団を見分ける。
#[test]
fn typed_value_rules_read_only_the_named_files() {
    let dir = typed_repo(&[("app/core/named.hy", "(defn #^ dict load [] 1)\n".to_string())]);
    std::fs::write(dir.path().join("app/core/broken.hy"), [0xffu8, 0xfe, b'\n']).unwrap();
    std::fs::write(dir.path().join("app/core/broken.pyi"), [0xffu8, 0xfe, b'\n']).unwrap();
    let (_, whole_out, whole_err) = run(dir.path(), &["--no-log"], None);
    assert!(whole_err.contains("app/core/broken.hy: 読めない"), "全体の実行は宣言の file を全部読む: {}\n{}", whole_err, whole_out);
    assert!(whole_err.contains("app/core/broken.pyi: 読めない"), "全体の実行は宣言の .pyi を全部読む: {}\n{}", whole_err, whole_out);
    let (code, named_out, named_err) = run(dir.path(), &["--no-log", "app/core/named.hy"], None);
    assert!(!named_err.contains("broken"), "名指しの実行は名指しの file だけを読む: {}", named_err);
    assert!(named_out.contains("DOEFF144") && named_out.contains("named.hy"), "名指しの file の当たりは出る: {}\n{}", named_out, named_err);
    assert_ne!(code, 0, "新しい当たりは error");
}

/// :typed-values・:record-stubs の読み違い(辞書でない・:files が無い・知らない鍵)は位置つきの設定の誤りにする。
#[test]
fn typed_value_declaration_misreadings_are_config_errors() {
    let dir = world_repo_with(&[], "", "[\"DOEFF144\", \"DOEFF145\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", ":foundation foundation\n  :typed-values [\"app/**\"]\n  :record-stubs {:except [\"x/**\"]}");
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains(":typed-values は {:files [..] :except [..]} の辞書"), "{}", all);
    assert!(all.contains(":record-stubs に :files(読む file の glob)が無い"), "{}", all);
}

/// レビューの指摘(#1191): .hy だけを名指した実行でも、隣の同じ名の .pyi(宣言の glob に当たる物)を判じ、.pyi の鍵で出す。
#[test]
fn record_stubs_judge_the_stub_next_to_a_named_hy() {
    let dir = typed_repo(&[
        ("app/core/rows.hy", "(defrecord Row (#^ str a))\n".to_string()),
        ("app/core/rows.pyi", "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row:\n    a: str\n".to_string()),
        ("app/core/other.hy", "(defrecord Other (#^ str a))\n".to_string()),
        ("app/core/other.pyi", "from dataclasses import dataclass\n@dataclass\nclass Other:\n    a: str\n".to_string()),
    ]);
    let (code, stdout, stderr) = run(dir.path(), &["--no-log", "--output-format", "editor-json", "app/core/rows.hy"], None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&report, "DOEFF145"), vec!["app/core/rows.pyi::DOEFF145::Row".to_string()], "{}", report);
    assert_ne!(code, 0);
}

/// 保存前の 1 file の実行(--stdin): .hy の中身は stdin から、隣の .pyi は disk から読み、当たりは .pyi の path で出す。DOEFF144 も
/// stdin の中身で判じる(disk の中身ではない)。
#[test]
fn stdin_run_judges_unsaved_hy_for_typed_values_and_record_stubs() {
    let dir = typed_repo(&[
        ("app/core/rows.hy", "(defclass Row [] (#^ str a))\n".to_string()),
        ("app/core/rows.pyi", "from dataclasses import dataclass\n@dataclass(frozen=True)\nclass Row:\n    a: str\n".to_string()),
    ]);
    let unsaved = "(defrecord Row (#^ str a))\n(defn #^ dict load [] 1)\n";
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/rows.hy"], Some(unsaved));
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&report, "DOEFF145"), vec!["app/core/rows.pyi::DOEFF145::Row".to_string()], "{}", report);
    assert_eq!(keys(&report, "DOEFF144"), vec!["app/core/rows.hy::DOEFF144::return:load".to_string()], "{}", report);
    // disk の .hy は defclass(kw-only でない)なので、全体の実行では DOEFF145 は当たらない。
    let (_, whole) = editor(dir.path());
    assert!(keys(&whole, "DOEFF145").is_empty(), "{}", whole);
}

/// :record-stubs の :except に当たる .pyi は読まない。
#[test]
fn record_stubs_except_is_honoured() {
    let hy = "(defrecord Row (#^ str a))\n".to_string();
    let stub = "from dataclasses import dataclass\n@dataclass\nclass Row:\n    a: str\n".to_string();
    let dir = typed_repo(&[
        ("app/core/rows.hy", hy),
        ("app/core/rows.pyi", stub),
    ]);
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF145"), vec!["app/core/rows.pyi::DOEFF145::Row".to_string()], "{}", report);
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":except [\"**/__pycache__/**\"]}", ":except [\"**/__pycache__/**\" \"app/core/**\"]}");
    std::fs::write(&arch_path, text).unwrap();
    let (_, excepted) = editor(dir.path());
    assert!(keys(&excepted, "DOEFF145").is_empty(), "{}", excepted);
}

/// agora-redesign #1192・#1371(DOEFF146): :single-point-vocabulary の :except の外でその群の語彙(正規表現)を読んでいる file を
/// 群ごとに 1 件、critical で出す。:except の file 自身は当てない。
#[test]
fn vocabulary_outside_its_single_point_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/glue/slice.hy",
            "(setv JOB-PHASE-RUNNING \"Running\")\n(defn slice-of [job] (= job.phase JOB-PHASE-RUNNING))\n".to_string(),
        ),
        (
            "app/billing/glue/queue.hy",
            "(setv a 1)\n(when (= phase JOB-PHASE-RUNNING) (print 1))\n(when (= phase JOB-PHASE-ENDED) (print 2))\n".to_string(),
        ),
        ("app/billing/glue/rows.hy", "(setv b (+ 1 2))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF146\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :single-point-vocabulary [(vocabulary-scope \"job-phase\" \
         :patterns [r\"\\bJOB-PHASE-[A-Z]+\\b\"] :files [\"app/billing/glue/**\"] \
         :except [\"app/billing/glue/slice.hy\"] :instead \"app/billing/glue/slice.hy の答えを読む\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF146"), vec!["app/billing/glue/queue.hy::DOEFF146::job-phase"], "{}", report);
    let hit = violation(&report, "app/billing/glue/queue.hy::DOEFF146::job-phase");
    assert!(hit["message"].as_str().unwrap().contains("2 行"), "{}", hit["message"]);
    assert!(hit["message"].as_str().unwrap().contains("slice.hy の答えを読む"), "{}", hit["message"]);
    assert_eq!(hit["range"]["start"]["line"], 1);
    assert_eq!(hit["level"], "critical");
}

/// :single-point-vocabulary の必須の鍵が無ければ読み取りの誤り(位置つき)。
#[test]
fn single_point_vocabulary_requires_patterns_files_except_and_instead() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF146\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :single-point-vocabulary [(vocabulary-scope \"a\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("vocabulary-scope a に :patterns が無い"), "{}", all);
    assert!(all.contains("vocabulary-scope a に :files が無い"), "{}", all);
    assert!(all.contains("vocabulary-scope a に :except(判定の 1 点)が無い"), "{}", all);
    assert!(all.contains("vocabulary-scope a に :instead(直し方)が無い"), "{}", all);
}

/// agora-redesign #1373・#1436(DOEFF148): :confined-spellings の綴りを :except の外の file が書いていれば、file と群ごとに 1 件、
/// critical で出す。文字列の中は数え(外の口の動詞・route の綴り)、註は数えない。Python の file も読む。:files に当たる file が
/// 無い群は architecture.hy の位置で missing。
#[test]
fn spelling_outside_its_files_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/protocol/peer.hy", "(defk send [x] (HttpRequest \"POST\" \"/api/rows\" x))\n".to_string()),
        (
            "app/billing/core/glue.hy",
            "; \"/api/intake\" は退いた\n(setv a 1)\n(defk go [x] (HttpRequest \"GET\" \"/api/intake\" x))\n".to_string(),
        ),
        ("app/billing/entry/values.py", "# /api/intake の註\nROUTE = \"/api/intake\"\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF148\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :confined-spellings [(confined-spelling \"http\" :patterns [r\"\\(HttpRequest\\s\"] \
         :files [\"app/billing/**\"] :except [\"app/billing/protocol/*.hy\"] :why \"外の口は層 protocol だけ\") \
         (confined-spelling \"acp-intake\" :patterns [r\"/api/intake\"] :files [\"app/billing/**\"] :why \"投函の受け手は列\") \
         (confined-spelling \"gone\" :patterns [r\"x\"] :files [\"app/gone/**\"] :why \"消えた\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF148"),
        vec![
            "app/billing/core/glue.hy::DOEFF148::acp-intake",
            "app/billing/core/glue.hy::DOEFF148::http",
            "app/billing/entry/values.py::DOEFF148::acp-intake",
            "architecture.hy::DOEFF148::gone:missing",
        ],
        "{}",
        report
    );
    let hit = violation(&report, "app/billing/core/glue.hy::DOEFF148::acp-intake");
    assert!(hit["message"].as_str().unwrap().contains("1 か所"), "{}", hit["message"]);
    assert_eq!(hit["range"]["start"]["line"], 2);
    assert_eq!(hit["level"], "critical");
    let python = violation(&report, "app/billing/entry/values.py::DOEFF148::acp-intake");
    assert_eq!(python["range"]["start"]["line"], 1);
}

/// :confined-spellings の必須の鍵が無ければ読み取りの誤り(位置つき)。:except は書かなくてよい。
#[test]
fn confined_spelling_requires_patterns_files_and_why() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF148\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :confined-spellings [(confined-spelling \"a\") (confined-spelling \"b\" :patterns [r\"(\"] :files [\"x/**\"] :why \"y\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("confined-spelling a に :patterns が無い"), "{}", all);
    assert!(all.contains("confined-spelling a に :files が無い"), "{}", all);
    assert!(all.contains("confined-spelling a に :why(なぜこの file だけか)が無い"), "{}", all);
    assert!(!all.contains("confined-spelling a に :except"), "{}", all);
    assert!(all.contains("confined-spelling b の正規表現 ( を読めない"), "{}", all);
}

/// agora-redesign #1373・#1437(DOEFF161): :counted-spellings の当たりの数が :count / :at-least に合わなければ 1 件、critical で出す。
/// :within は名指した定義ごとに数え、無い定義は missing。Hy の註は数えず、yaml は拡張子を問わずそのまま読む。
#[test]
fn spelling_count_differs_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/protocol/peer.hy",
            "(defk send [x] (Req \"POST\" x)) ; \"POST\" は註\n\n(defk read [x] (Req \"POST\" (Req \"POST\" x)))\n".to_string(),
        ),
        ("app/billing/rules.yaml", "rules:\n  - id: one\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF161\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :counted-spellings [(counted-spelling \"posts\" :pattern r\"\\x22POST\\x22\" \
         :files [\"app/billing/protocol/peer.hy\"] :count 2 :why \"書きは 2 つ\") \
         (counted-spelling \"seats\" :pattern r\"\\x22POST\\x22\" :files [\"app/billing/protocol/peer.hy\"] \
         :within [\"send\" \"read\" \"gone\"] :count 1 :why \"座ごとに 1 つ\") \
         (counted-spelling \"rule-one\" :pattern r\"id: one\" :files [\"app/billing/rules.yaml\"] :at-least 1 :why \"規則の実在\") \
         (counted-spelling \"rule-two\" :pattern r\"id: two\" :files [\"app/billing/rules.yaml\"] :at-least 1 :why \"規則の実在\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF161"),
        vec![
            "app/billing/protocol/peer.hy::DOEFF161::posts",
            "app/billing/protocol/peer.hy::DOEFF161::seats:read",
            "app/billing/rules.yaml::DOEFF161::rule-two",
            "architecture.hy::DOEFF161::seats:gone:missing",
        ],
        "{}",
        report
    );
    let posts = violation(&report, "app/billing/protocol/peer.hy::DOEFF161::posts");
    assert!(posts["message"].as_str().unwrap().contains("3 か所(ちょうど 2 のはず)"), "{}", posts["message"]);
    assert_eq!(posts["level"], "critical");
    let seat = violation(&report, "app/billing/protocol/peer.hy::DOEFF161::seats:read");
    assert_eq!(seat["range"]["start"]["line"], 2);
}

/// :counted-spellings の必須の鍵が無ければ読み取りの誤り。:count と :at-least はどちらか 1 つ。
#[test]
fn counted_spelling_requires_pattern_files_one_count_and_why() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF161\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :counted-spellings [(counted-spelling \"a\") \
         (counted-spelling \"b\" :pattern r\"x\" :files [\"x.hy\"] :count 1 :at-least 1 :why \"y\") \
         (counted-spelling \"c\" :pattern r\"x\" :files [\"x.hy\"] :count many :why \"y\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("counted-spelling a に :pattern が無い"), "{}", all);
    assert!(all.contains("counted-spelling a に :files が無い"), "{}", all);
    assert!(all.contains("counted-spelling a には :count か :at-least のどちらか 1 つを書く"), "{}", all);
    assert!(all.contains("counted-spelling a に :why(なぜこの数か)が無い"), "{}", all);
    assert!(all.contains("counted-spelling b には :count か :at-least のどちらか 1 つを書く"), "{}", all);
    assert!(all.contains("counted-spelling c の :count は 0 以上の整数"), "{}", all);
}

/// agora-redesign #1374(DOEFF149): :field-holders の型の欄を持つ class が :holders と違えば、一覧の外の持ち手は class の位置で、欄を
/// 持たない一覧の class と :classes の無い class は architecture.hy の位置で、critical で出す。docstring・method の中・既定値の文字列は数えない。
#[test]
fn field_holders_differ_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/types.py",
            "class RecordCache:\n    \"\"\"RecordCache は本文の容器。\"\"\"\n    rows: tuple\n\n\
             class Sent:\n    cache: RecordCache | None = None\n\n\
             class Sneaky:\n    spare: list[RecordCache]\n\n\
             class State:\n    note: str = \"Record\"\n    def f(self) -> None:\n        x: Record = None\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF149\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :field-holders [(field-holders \"cache\" :type \"RecordCache\" :files [\"app/billing/core/*.py\"] \
         :holders [\"Sent\" \"Page\"] :why \"容器は 1 つ\") \
         (field-holders \"state\" :type \"Record\" :files [\"app/billing/core/*.py\"] :classes [\"State\" \"Gone\"] :holders [] :why \"状態は本文を持たない\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF149"),
        vec![
            "app/billing/core/types.py::DOEFF149::cache:Sneaky",
            "architecture.hy::DOEFF149::cache:Page:absent",
            "architecture.hy::DOEFF149::state:Gone:missing",
        ],
        "{}",
        report
    );
    let sneaky = violation(&report, "app/billing/core/types.py::DOEFF149::cache:Sneaky");
    assert!(sneaky["message"].as_str().unwrap().contains("class Sneaky が RecordCache の欄を持つ"), "{}", sneaky["message"]);
    assert_eq!(sneaky["level"], "critical");
    assert_eq!(sneaky["range"]["start"]["line"], 7);
}

/// :field-holders の必須の鍵が無ければ読み取りの誤り。:holders は空の列でよいが書く。:classes を書けば :holders はその中の名。
#[test]
fn field_holders_requires_type_files_holders_and_why() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF149\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :field-holders [(field-holders \"a\") \
         (field-holders \"b\" :type \"T\" :files [\"x.py\"] :classes [\"A\"] :holders [\"B\"] :why \"y\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("field-holders a に :type(数える型の綴り)が無い"), "{}", all);
    assert!(all.contains("field-holders a に :files が無い"), "{}", all);
    assert!(all.contains("field-holders a に :holders が無い"), "{}", all);
    assert!(all.contains("field-holders a に :why(なぜこの顔ぶれか)が無い"), "{}", all);
    assert!(all.contains("field-holders b の :holders の B が :classes に無い"), "{}", all);
}

/// agora-redesign #1373・#1438(DOEFF162): :effect-census の :files で EffectBase を継ぐ class の宣言が :effects の一覧と食い違えば、
/// 食い違いごとに 1 件、critical で出す(一覧の外・2 度の宣言・宣言の無い一覧の effect)。
#[test]
fn effect_outside_census_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/effects.py", "# class Old(EffectBase):\nclass Send(EffectBase):\n    pass\n\nclass Sneaky(EffectBase):\n    pass\n".to_string()),
        ("app/billing/intent/socket.hy", "(defclass [(dataclass :frozen True)] Close [EffectBase])\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF162\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :effect-census [(effect-census \"billing\" :files [\"app/billing/effects.py\" \"app/billing/intent/socket.hy\"] \
         :effects [\"Send\" \"Close\" \"Log\"] :why \"外への要求は一覧で閉じる\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF162"),
        vec!["app/billing/effects.py::DOEFF162::billing:Sneaky", "architecture.hy::DOEFF162::billing:Log:missing"],
        "{}",
        report
    );
    let sneaky = violation(&report, "app/billing/effects.py::DOEFF162::billing:Sneaky");
    assert_eq!(sneaky["range"]["start"]["line"], 4);
    assert_eq!(sneaky["level"], "critical");
}

/// :effect-census の必須の鍵が無ければ読み取りの誤り。
#[test]
fn effect_census_requires_files_effects_and_why() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF162\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :effect-census [(effect-census \"a\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("effect-census a に :files が無い"), "{}", all);
    assert!(all.contains("effect-census a に :effects が無い"), "{}", all);
    assert!(all.contains("effect-census a に :why(なぜ一覧で閉じるか)が無い"), "{}", all);
}

/// agora-redesign #1312・#1410: 記録の client は HttpRequest の effect を出すだけ — 実 HTTP は HttpRequest に
/// 答える本物の handler(目録の http-production-handler)の側で数える。通常の client と表で絞る client は目録に載せない。
/// RecordsEndpoint を値として名指すだけ(型の注釈)の所も数えない。
#[test]
fn records_client_is_counted_by_its_outer_http_handler() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/ports.hy",
            tags("billing", "judgment")
                + "(import doeff_records.http_client [RecordsEndpoint http-records-handler http-table-records-handler])\n\
                   (import doeff_core_effects.http_handlers [http-production-handler])\n\
                   (defk effect-port [url] (http-records-handler (RecordsEndpoint url \"t\")))\n\
                   (defk table-port [url] (http-table-records-handler (RecordsEndpoint url \"t\") (frozenset [\"orders\"])))\n\
                   (defk answered-port [url body] (with-handlers [(http-production-handler) (effect-port url)] body))\n\
                   (defk answered-table-port [url body] (with-handlers [(http-production-handler) (table-port url)] body))\n\
                   (defk named-port [endpoint] {:pre [(: endpoint RecordsEndpoint)]} endpoint)\n",
        ),
        (
            "app/billing/tests/test_ports.hy",
            "(import app.billing.core.ports [effect-port table-port answered-port answered-table-port named-port])\n\
             (deftest test-effect-port-unmarked (<- h (effect-port \"http://x\")) (assert h))\n\
             (deftest test-table-port-unmarked (<- h (table-port \"http://x\")) (assert h))\n\
             (deftest test-answered-port-unmarked (<- h (answered-port \"http://x\" 1)) (assert h))\n\
             (deftest test-answered-table-port-unmarked (<- h (answered-table-port \"http://x\" 1)) (assert h))\n\
             (deftest test-named-port-unmarked (<- h (named-port 1)) (assert h))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", ":foundation foundation\n  :edge-mark \"real_world\"");
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    let expected = vec![
        "app/billing/tests/test_ports.hy::DOEFF133::test_answered_port_unmarked::edge",
        "app/billing/tests/test_ports.hy::DOEFF133::test_answered_table_port_unmarked::edge",
    ];
    assert_eq!(keys(&report, "DOEFF133"), expected, "{}", report);
    for key in expected {
        let answered = violation(&report, key);
        assert!(answered["message"].as_str().unwrap().contains("http-production-handler"), "{}", answered["message"]);
    }
}

/// agora-redesign #1410: 移行の間だけ残した目録の行は撤去済み。効果を出すだけの記録 client を
/// 実 I/O の :wraps に挙げる古い宣言は、通常の「目録に無い」設定エラーで止める。
#[test]
fn records_clients_cannot_be_declared_as_world_handler_wraps() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-records [body] body)\n")];
    for name in ["http-records-handler", "http-table-records-handler"] {
        let target = format!("doeff_records.http_client:{name}");
        let extra = format!("\n(world-handler \"app.foundation.host:with-records\" :touches [http] :wraps [\"{target}\"])");
        let dir = world_repo_with(&files, &extra, "[\"DOEFF131\"]");
        let (code, _, stderr) = run(dir.path(), &["--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
        assert!(stderr.contains(&format!("{target} は doeff の実 I/O の handler の目録")), "{}", stderr);
    }
}

/// agora-redesign #1368(C7b): 決めた材料だけで判じる定義(:blind-definitions)— 宣言した定義から呼び出しで届く定義が、helper と別の
/// module を越えても、使わない語を読めば赤(届いた定義と語ごとに 1 件)。註の中の語・届かない定義の語は数えない。:no-imports なら module の
/// import は赤で、:allow-requires の macro の require は許す。名指す定義が無ければ architecture.hy の位置で赤(母集団 0 を緑にしない)。
#[test]
fn blind_definitions_read_only_their_inputs() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/entrance.hy",
            "(require doeff-hy.macros [defk])\n(import app.billing.core.routes [peek])\n\
             (defk decide [said] (helper said))\n\
             (defk helper [x]\n  ;; view.policy は読まない(註は数えない)\n  (peek x))\n"
                .to_string(),
        ),
        ("app/billing/core/routes.hy", "(defk peek [x] (get x \"view.policy\"))\n(defk unrelated [] \"message-class\")\n".to_string()),
        ("app/billing/core/clean.hy", "(require doeff-hy.macros [defk])\n(defk decide [said] (if (is said None) True said))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF141\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :blind-definitions [(blind \"app.billing.core.entrance:decide\" :forbid-words [\"view.policy\" \"message-class\"]\n                         :no-imports True :allow-requires [\"doeff-hy.macros\"] :why \"保証は依頼者の言葉だけから決まる\")\n                       (blind \"app.billing.core.clean:decide\" :forbid-words [\"view.policy\"] :no-imports True\n                         :allow-requires [\"doeff-hy.macros\"] :why \"同じ\")\n                       (blind \"app.billing.core.gone:decide\" :no-imports True :why \"消えた定義\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF141"),
        vec![
            "app/billing/core/entrance.hy::DOEFF141::import:app.billing.core.routes",
            "app/billing/core/routes.hy::DOEFF141::peek:view.policy",
            "architecture.hy::DOEFF141::missing",
        ],
        "{}",
        report
    );
    let reached = violation(&report, "app/billing/core/routes.hy::DOEFF141::peek:view.policy");
    assert_eq!(reached["level"], "critical", "{}", reached);
    assert!(reached["message"].as_str().unwrap().contains("app.billing.core.entrance:decide から届く定義 peek"), "{}", reached["message"]);
    assert_eq!(reached["range"]["start"]["line"], 0);
}

/// 宣言の読み違い(何も求めない宣言・:no-imports の無い :allow-requires・:why の無い宣言・綴りの形)は設定の誤り。
#[test]
fn blind_declaration_misreadings_are_config_errors() {
    let dir = world_repo_with(&[], "", "[\"DOEFF141\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :blind-definitions [(blind \"a.b:c\" :why \"x\")\n                       (blind \"a.b:d\" :forbid-words [\"w\"] :allow-requires [\"m\"] :why \"x\")\n                       (blind \"a.b:e\" :forbid-words [\"w\"])\n                       (blind \"no-colon\" :forbid-words [\"w\"] :why \"x\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("blind a.b:c に :forbid-words も :no-imports True も無い"), "{}", all);
    assert!(all.contains("blind a.b:d の :allow-requires は :no-imports True の時だけ効く"), "{}", all);
    assert!(all.contains("blind a.b:e に :why"), "{}", all);
    assert!(all.contains("blind の no-colon は \"module.path:名\" の綴り"), "{}", all);
}

/// agora-redesign #1413(#1372 の孫 1): 呼んでよい頭を決めた定義(:allowed-heads)— 定義の中(入れ子を含む)の `( … )` の頭が一覧の
/// 外なら、頭ごとに 1 件(最初に現れた所)。文字列・註・`#_`・tuple の中の綴りは頭に数えない。同じ module のほかの定義の頭は見ない。
/// 名指す定義が無ければ architecture.hy の位置で赤(母集団 0 を緑にしない)。
#[test]
fn allowed_heads_confine_what_a_definition_may_call() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/server.hy",
            "(require doeff-hy.macros [defk <-])\n\
             (defk confined [conn text]\n  {:pre [(: text str)]}\n  ;; (decode text) は註なので数えない\n  (<- r (Try (on-text conn text)))\n  \"(parse text)\"\n  #_(explode)\n  \
             (when (isinstance r Err)\n    (return (refuse conn (decode r))))\n  (setv pair #(first second))\n  (decode r))\n\
             (defk refuse [conn fault] (Send conn fault))\n\
             (defk other [x] (anything-goes x))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF147\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :allowed-heads [(allowed-heads \"app.billing.entry.server:confined\"\n                    :heads [\"defk\" \":\" \"<-\" \"Try\" \"on-text\" \"when\" \"isinstance\" \"return\" \"refuse\" \"setv\"]\n                    :why \"Try の外で例外を上げない\")\n                  (allowed-heads \"app.billing.entry.server:refuse\" :heads [\"defk\" \"Send\"] :why \"断りの 1 点\")\n                  (allowed-heads \"app.billing.entry.server:gone\" :heads [\"defk\"] :why \"消えた定義\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF147"),
        vec!["app/billing/entry/server.hy::DOEFF147::decode", "architecture.hy::DOEFF147::missing"],
        "{}",
        report
    );
    let unlisted = violation(&report, "app/billing/entry/server.hy::DOEFF147::decode");
    assert_eq!(unlisted["level"], "critical", "{}", unlisted);
    assert!(unlisted["message"].as_str().unwrap().contains("app.billing.entry.server:confined の中で呼んでよい頭の一覧の外の (decode …)"), "{}", unlisted["message"]);
    assert_eq!(unlisted["range"]["start"]["line"], 8, "最初に現れた所(when の枝の中): {}", unlisted);
}

/// 一覧の中の頭だけなら緑。宣言の読み違い(:heads の無い宣言・:why の無い宣言・2 度の宣言・綴りの形)は設定の誤り。
#[test]
fn allowed_heads_pass_when_listed_and_misreadings_are_config_errors() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/entry/server.hy", "(defk refuse [conn fault] (Send conn fault))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF147\"]");
    let arch_path = dir.path().join("architecture.hy");
    let original = std::fs::read_to_string(&arch_path).unwrap();
    let clean = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :allowed-heads [(allowed-heads \"app.billing.entry.server:refuse\" :heads [\"defk\" \"Send\"] :why \"断りの 1 点\")]",
    );
    std::fs::write(&arch_path, clean).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF147"), Vec::<String>::new(), "{}", report);
    let broken = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :allowed-heads [(allowed-heads \"a.b:c\" :why \"x\")\n                  (allowed-heads \"a.b:d\" :heads [\"defk\"])\n                  (allowed-heads \"a.b:e\" :heads [\"defk\"] :why \"x\")\n                  (allowed-heads \"a.b:e\" :heads [\"defk\"] :why \"x\")\n                  (allowed-heads \"no-colon\" :heads [\"defk\"] :why \"x\")]",
    );
    std::fs::write(&arch_path, broken).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("allowed-heads a.b:c に :heads が無い"), "{}", all);
    assert!(all.contains("allowed-heads a.b:d に :why"), "{}", all);
    assert!(all.contains("allowed-heads a.b:e が 2 度宣言されている"), "{}", all);
    assert!(all.contains("allowed-heads の no-colon は \"module.path:名\" の綴り"), "{}", all);
}

/// agora-redesign #1414(#1372 の孫 2): 頭を呼んでよい場所と回数(:call-sites)。正しい形なら緑。場所の外の呼び・回数の食い違い・直ぐ外の
/// form の食い違い・分岐の外の呼び・消えた場所・当たる file の無い宣言は、それぞれの鍵で赤(:except の file と註・文字列の中は数えない)。
#[test]
fn call_sites_confine_where_a_head_is_called() {
    let server = "(require doeff-hy.macros [defk <-])\n\
                  (defk confined [conn text] (<- r (Try (on-text conn text))) (when (isinstance r Err) (refuse conn r)))\n\
                  (defk serve [config]\n  (cond (isinstance arrival Text) (confined arrival.conn arrival.text)\n        (isinstance arrival AnswerFailed) (refuse arrival.conn arrival.failure)))\n\
                  (defk refuse [conn fault] (Send conn fault))\n";
    let service = "(defk process [config] (run (watching (try-handler (serve config)))))\n";
    let declare = "\n  :call-sites [(call-site \"Try\" :files [\"app/billing/**/*.hy\"] :except [\"app/billing/**/tests/**\"]\n                 :sites [(site \"app.billing.entry.server:confined\" :count 1)] :why \"閉じ込めは 1 点\")\n               (call-site \"refuse\" :files [\"app/billing/entry/server.hy\"]\n                 :sites [(site \"app.billing.entry.server:confined\" :count 1) (site \"app.billing.entry.server:serve\" :count 1 :branch \"AnswerFailed\")]\n                 :why \"断りは 2 か所\")\n               (call-site \"serve\" :files [\"app/billing/entry/service.hy\"]\n                 :sites [(site \"app.billing.entry.service:process\" :count 1 :parent \"try-handler\")] :why \"try-handler は最も内側\")]";
    let build = |server: &str, service: &str, extra: &str| {
        let files = [
            ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
            ("app/billing/entry/server.hy", server.to_string()),
            ("app/billing/entry/service.hy", service.to_string()),
            ("app/billing/core/react.hy", "(defk react [x] ;; (Try x) は註\n  \"(Try x)\" x)\n".to_string()),
            ("app/billing/core/tests/test_react.hy", "(defk t [] (Try (x)))\n".to_string()),
        ];
        let dir = world_repo_with(&files, "", "[\"DOEFF159\"]");
        let arch_path = dir.path().join("architecture.hy");
        let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", &format!(":foundation foundation{}{}", declare, extra));
        std::fs::write(&arch_path, text).unwrap();
        dir
    };
    let (_, report) = editor(build(server, service, "").path());
    assert_eq!(keys(&report, "DOEFF159"), Vec::<String>::new(), "{}", report);

    let broken_server = server
        .replace("(defk serve [config]", "(defk widen [] (Try (x)))\n(defk serve [config]")
        .replace("(isinstance arrival AnswerFailed)", "(isinstance arrival AnswerLost)");
    let broken_service = service.replace("(watching (try-handler (serve config)))", "(try-handler (watching (serve config)))");
    let (_, report) = editor(build(&broken_server, &broken_service, "").path());
    assert_eq!(
        keys(&report, "DOEFF159"),
        vec![
            "app/billing/entry/server.hy::DOEFF159::Try:outside",
            "app/billing/entry/server.hy::DOEFF159::refuse:branch:serve",
            "app/billing/entry/service.hy::DOEFF159::serve:parent:process",
        ],
        "{}",
        report
    );
    let outside = violation(&report, "app/billing/entry/server.hy::DOEFF159::Try:outside");
    assert_eq!(outside["level"], "critical", "{}", outside);
    assert_eq!(outside["range"]["start"]["line"], 2, "{}", outside);

    // 場所の定義の名を変える(消えた場所)+ 断りの 1 点の中から自分を呼ぶ(場所の外の呼び)。
    let renamed = server.replace("(Send conn fault)", "(refuse conn fault)").replace("(defk confined", "(defk kept");
    let (_, report) = editor(build(&renamed, service, "").path());
    assert_eq!(
        keys(&report, "DOEFF159"),
        vec![
            "app/billing/entry/server.hy::DOEFF159::Try:outside",
            "app/billing/entry/server.hy::DOEFF159::refuse:outside",
            "architecture.hy::DOEFF159::Try:missing:confined",
            "architecture.hy::DOEFF159::refuse:missing:confined",
        ],
        "{}",
        report
    );
}

/// 回数の食い違いと、当たる file の無い宣言。宣言の読み違い(:files・:sites・:why の無い宣言・2 度の宣言・整数でない :count)は設定の誤り。
#[test]
fn call_site_counts_and_misreadings() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/entry/server.hy", "(defk serve [] (go 1) (go 2))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF159\"]");
    let arch_path = dir.path().join("architecture.hy");
    let original = std::fs::read_to_string(&arch_path).unwrap();
    let counted = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :call-sites [(call-site \"go\" :files [\"app/billing/entry/server.hy\"] :sites [(site \"app.billing.entry.server:serve\" :count 1)] :why \"1 度\")\n               (call-site \"stop\" :files [\"app/nowhere/**/*.hy\"] :sites [(site \"app.billing.entry.server:serve\")] :why \"空\")]",
    );
    std::fs::write(&arch_path, counted).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF159"), vec!["app/billing/entry/server.hy::DOEFF159::go:count:serve", "architecture.hy::DOEFF159::stop:empty"], "{}", report);
    let count = violation(&report, "app/billing/entry/server.hy::DOEFF159::go:count:serve");
    assert!(count["message"].as_str().unwrap().contains("app.billing.entry.server:serve の中の (go …) が 2 回(宣言は 1 回ちょうど)"), "{}", count["message"]);

    let broken = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :call-sites [(call-site \"a\" :sites [(site \"x.y:z\")] :why \"x\")\n               (call-site \"b\" :files [\"x/*.hy\"] :why \"x\")\n               (call-site \"c\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\" :count many)])\n               (call-site \"d\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\")] :why \"x\")\n               (call-site \"d\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\")] :why \"x\")]",
    );
    std::fs::write(&arch_path, broken).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("call-site a に :files が無い"), "{}", all);
    assert!(all.contains("call-site b に :sites が無い"), "{}", all);
    assert!(all.contains(":count は 0 以上の整数"), "{}", all);
    assert!(all.contains("call-site c に :why"), "{}", all);
    assert!(all.contains("call-site d が 2 度宣言されている"), "{}", all);
}

/// agora-redesign #1390: 系の値(defsystem の定義と :systems の :carriers の引数)が運ぶ土台は、系を回す入口(:systems の :runners)に
/// 届く検からだけ届く — 系の値を読むだけの検と、道具で組むだけの検は手元。土台を直に呼ぶ検は今までどおり縁。
#[test]
fn a_system_value_reaches_the_world_only_when_a_runner_runs_it() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/helpers.hy", tags("billing", "judgment") + "(import app.foundation.host [with-host])\n(defk hosted [body] (with-host body))\n"),
        (
            "app/billing/core/systems.hy",
            tags("billing", "judgment")
                + "(import app.billing.core.helpers [hosted])\n\
                   (defsystem billing-system [foundation] (hosted foundation))\n\
                   (defk part [name foundation] #(name foundation))\n\
                   (defk run-system [system] system)\n",
        ),
        (
            "app/billing/tests/test_systems.hy",
            "(import app.billing.core.systems [billing-system part run-system])\n(import app.billing.core.helpers [hosted])\n\
             (deftest test-reads-the-system (<- s (billing-system 1)) (assert s))\n\
             (deftest test-runs-the-system (<- s (run-system (billing-system 1))) (assert s))\n\
             (deftest test-builds-a-part (<- p (part \"a\" hosted)) (assert p))\n\
             (deftest test-calls-the-foundation (<- n (hosted 1)) (assert n))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF133\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :edge-mark \"real_world\"\n  :systems {:carriers [\"app.billing.core.systems:part\"] :runners [\"app.billing.core.systems:run-system\"]}",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report);
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec![
            "app/billing/tests/test_systems.hy::DOEFF133::test_calls_the_foundation::edge",
            "app/billing/tests/test_systems.hy::DOEFF133::test_runs_the_system::edge",
        ],
        "{}",
        report
    );
}

/// agora-redesign #1372・#1415(DOEFF160): :broad-catches の :files の Hy・Python の file の広い例外の捕捉は、:carriers の定義の中で
/// 例外を名で束縛して :event の出来事へ渡す物だけ — 外の捕捉は file ごとに、名を渡さない境界は境界ごとに 1 件、critical で出す。
#[test]
fn broad_catches_are_confined_to_declared_carriers() {
    let deferred = "(defk carry [inbox program]\n  (try (<- a program)\n    (except [TaskCancelledError] (raise))\n    (except [error Exception] (<- (PutChannel inbox (Queued :arrival None :failure error))))))\n";
    let react = "(defk react [x] ;; (except [Exception] x) は註\n  \"(except [] x)\" (try (x) (except [ValueError] None)))\n";
    let worker = "def work():\n    try:\n        run()\n    except (KeyError, ValueError):\n        pass\n";
    let declare = "\n  :broad-catches [(broad-catch \"screen\" :files [\"app/billing/**/*.hy\" \"app/billing/**/*.py\"] :except [\"app/billing/**/tests/**\"]\n                   :carriers [(carrier \"app.billing.core.deferred:carry\" :event \"Queued\")] :why \"契約の破れを握りつぶさない\")]";
    let build = |deferred: &str, react: &str, worker: &str| {
        let files = [
            ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
            ("app/billing/core/deferred.hy", deferred.to_string()),
            ("app/billing/core/react.hy", react.to_string()),
            ("app/billing/core/worker.py", worker.to_string()),
            ("app/billing/core/tests/test_react.hy", "(defk t [] (try (x) (except [] None)))\n".to_string()),
        ];
        let dir = world_repo_with(&files, "", "[\"DOEFF160\"]");
        let arch_path = dir.path().join("architecture.hy");
        let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", &format!(":foundation foundation{}", declare));
        std::fs::write(&arch_path, text).unwrap();
        dir
    };
    let (_, report) = editor(build(deferred, react, worker).path());
    assert_eq!(keys(&report, "DOEFF160"), Vec::<String>::new(), "{}", report);

    // 反応に Hy の広い捕捉・Python に except Exception: と except:・境界が例外を出来事へ渡さない(握りつぶし)。
    let swallowing = deferred.replace(":failure error", ":failure None");
    let broad_react = react.replace("(except [ValueError] None)", "(except [e Exception] None)");
    let broad_worker = worker.replace("except (KeyError, ValueError):", "except (KeyError, Exception):") + "\ndef other():\n    try:\n        run()\n    except:\n        pass\n";
    let (_, report) = editor(build(&swallowing, &broad_react, &broad_worker).path());
    assert_eq!(
        keys(&report, "DOEFF160"),
        vec![
            "app/billing/core/deferred.hy::DOEFF160::screen:unbound:carry",
            "app/billing/core/react.hy::DOEFF160::screen:outside",
            "app/billing/core/worker.py::DOEFF160::screen:outside",
        ],
        "{}",
        report
    );
    let worker_hit = violation(&report, "app/billing/core/worker.py::DOEFF160::screen:outside");
    assert_eq!(worker_hit["level"], "critical", "{}", worker_hit);
    assert_eq!(worker_hit["range"]["start"]["line"], 3, "{}", worker_hit);
    assert!(worker_hit["message"].as_str().unwrap().contains("2 件"), "{}", worker_hit);

    // 束縛しない広い捕捉は境界の中でも当たる。境界の定義が消えれば、その中の捕捉は外の捕捉になり、境界は無い。
    let unbound = deferred.replace("(except [error Exception]", "(except [Exception]");
    let (_, report) = editor(build(&unbound, react, worker).path());
    assert_eq!(keys(&report, "DOEFF160"), vec!["app/billing/core/deferred.hy::DOEFF160::screen:unbound:carry"], "{}", report);
    let renamed = deferred.replace("(defk carry", "(defk kept");
    let (_, report) = editor(build(&renamed, react, worker).path());
    assert_eq!(
        keys(&report, "DOEFF160"),
        vec!["app/billing/core/deferred.hy::DOEFF160::screen:outside", "architecture.hy::DOEFF160::screen:missing:carry"],
        "{}",
        report
    );
}

/// :broad-catches の母集団 0 は赤、読み違いは読み取りの誤り。
#[test]
fn broad_catches_empty_population_and_misreadings() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF160\"]");
    let arch_path = dir.path().join("architecture.hy");
    let original = std::fs::read_to_string(&arch_path).unwrap();
    let text = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :broad-catches [(broad-catch \"gone\" :files [\"app/gone/**/*.hy\"] :why \"母集団 0\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF160"), vec!["architecture.hy::DOEFF160::gone:empty"], "{}", report);

    let text = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :broad-catches [(broad-catch \"a\" :carriers [(carrier \"app.x:y\")]) (broad-catch \"b\" :files [\"app/**/*.hy\"] :why \"x\") (broad-catch \"b\" :files [\"app/**/*.hy\"] :why \"x\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("carrier app.x:y に :event(例外を載せる出来事)が無い"), "{}", all);
    assert!(all.contains("broad-catch a に :files が無い"), "{}", all);
    assert!(all.contains("broad-catch a に :why(なぜ広い捕捉を置かないか)が無い"), "{}", all);
    assert!(all.contains("broad-catch b が 2 度宣言されている"), "{}", all);
}

/// DOEFF166 の repo: DOEFF110・111 を判じ(111 は law bare-needs-reason の名で鍵を組む)、登録簿は 1 鍵 1 file の dir と 1 行 1 鍵の file。
fn stale_registry_repo(dir_keys: &[&str], listed: &str, enable: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("architecture.hy"), "(defarchitecture s :root \"app\" :layers [(layer core)])\n").unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        format!(
            "[tool.doeff-linter]\nenable = [{}]\n[tool.doeff-linter.definitions]\n[tool.doeff-linter.registry]\ndirs = [\"reg/BREACHES\"]\nfiles = [\"known.txt\"]\n\n[[tool.doeff-linter.laws]]\nname = \"bare-needs-reason\"\nrules = [\"DOEFF111\"]\nstatement = \"素の関数は理由を書く\"\n",
            enable
        ),
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("reg/BREACHES")).unwrap();
    for (i, key) in dir_keys.iter().enumerate() {
        std::fs::write(dir.path().join(format!("reg/BREACHES/k{}.txt", i)), format!("{}\n理由\n", key)).unwrap();
    }
    std::fs::write(dir.path().join("known.txt"), listed).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/x.hy"), "(deff bare [row] row)\n(defn helper [] 1)\n").unwrap();
    dir
}

#[test]
fn registry_keys_that_no_longer_hit_are_errors_only_for_rules_judged_on_the_whole_repo() {
    let enable = "\"DOEFF110\", \"DOEFF111\", \"DOEFF166\"";
    // 今も当たる鍵(law の名・規則の ID)・当たらなくなった鍵(dir の file と list の行)・判じていない規則の鍵・規則を引けない他の検の鍵。
    let dir = stale_registry_repo(
        &["app/core/x.hy::bare-needs-reason::bare", "app/core/x.hy::bare-needs-reason::gone", "app/core/x.hy::DOEFF119::Old"],
        "app/core/x.hy::DOEFF110::helper\napp/core/x.hy::DOEFF110::removed\nlayer/imports.hy::some-other-checker::x\n",
        enable,
    );
    let (code, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF166"),
        vec![
            "known.txt::DOEFF166::app/core/x.hy::DOEFF110::removed",
            "reg/BREACHES/k1.txt::DOEFF166::app/core/x.hy::bare-needs-reason::gone",
        ]
    );
    let gone = violation(&report, "reg/BREACHES/k1.txt::DOEFF166::app/core/x.hy::bare-needs-reason::gone");
    assert_eq!(gone["severity"], "error");
    assert!(gone["message"].as_str().unwrap().contains("DOEFF111"), "{}", gone["message"]);
    assert_eq!(code, 1, "当たらない行は error — 終了コード 1");
    // 今も当たる鍵は今までどおり warning に下がる。
    assert_eq!(violation(&report, "app/core/x.hy::bare-needs-reason::bare")["severity"], "warning");

    // 反例: 古い行を消せば DOEFF166 は出ない。
    let clean = stale_registry_repo(&["app/core/x.hy::bare-needs-reason::bare"], "app/core/x.hy::DOEFF110::helper\n", enable);
    let (_, report) = editor(clean.path());
    assert!(keys(&report, "DOEFF166").is_empty(), "{:?}", keys(&report, "DOEFF166"));

    // 名指しの file だけの実行と、DOEFF166 を有効にしない実行では判じない。
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/core/x.hy"], None);
    let named: Value = serde_json::from_str(&stdout).unwrap();
    assert!(keys(&named, "DOEFF166").is_empty(), "{:?}", keys(&named, "DOEFF166"));
    let off = stale_registry_repo(&["app/core/x.hy::bare-needs-reason::gone"], "", "\"DOEFF110\", \"DOEFF111\"");
    let (_, report) = editor(off.path());
    assert!(keys(&report, "DOEFF166").is_empty());
}

/// DOEFF142 の宣言(:handler-arguments)を architecture.hy に足した一時の repo(agora-redesign #1809・決定 C の失敗ケースの検)。
fn handler_argument_repo(source: &str) -> tempfile::TempDir {
    let files = [("app/billing/protocol/answer.hy", tags("billing", "protocol") + source)];
    let dir = world_repo_with(&files, "", "[\"DOEFF142\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declarations = ":foundation foundation\n  :handler-arguments {:files [\"app/**/*.hy\"] :store-names [\"store\"] \
                        :store-suffixes [\"-store\"] :keep-mark \"handler-state-kept:\" :value-types []}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declarations);
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// agora-redesign #1809(DOEFF142): defhandler の引数が client・可変の店を取れば赤(鳴る例)。frozen の record の設定を取る handler と、
/// 残す理由の註(:keep-mark)を書いた handler は鳴らない(鳴らない例)。
#[test]
fn handler_arguments_that_hold_a_client_or_a_store_are_red() {
    let firing = "(import httpx)\n\
                  (defhandler charge-reads [#^ httpx.AsyncClient client store]\n  (Charge [amount] (resume amount)))\n";
    let dir = handler_argument_repo(firing);
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF142");
    assert_eq!(hits.len(), 2, "client と名の store の 2 つ: {:?}\n{}", hits, report);
    assert!(hits.iter().all(|k| k.starts_with("app/billing/protocol/answer.hy::DOEFF142::charge-reads::")), "{:?}", hits);
    assert_eq!(violation(&report, &hits[0])["severity"], "error");

    let quiet = "(require doeff-hy.record [defrecord])\n\
                 (defrecord ChargeSettings \"請求の設定\" (#^ str currency))\n\
                 (defhandler charge-reads [#^ ChargeSettings settings]\n  (Charge [amount] (resume amount)))\n\
                 (defhandler kept-reads [store]\n  ;; handler-state-kept: 検の台だけが渡す記録の箱(本番の組には載らない)\n  (Charge [amount] (resume amount)))\n";
    let dir = handler_argument_repo(quiet);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF142").is_empty(), "{:?}\n{}", keys(&report, "DOEFF142"), report);
}

/// DOEFF155・158 の宣言(:business-fakes と :assembly-shape)を書いた一時の repo(agora-redesign #1809)。請求の service の層は
/// core・intent・protocol(翻訳の層)・entry(組み立ての層)。本番の入口 = 組の file(app/*/entry/handler_sets.hy)の名が production で
/// 始まる定義。intent の効果は Charge と Refund(app/billing/intent/effects.hy)。
fn assembly_repo(files: &[(&str, String)], enable: &str) -> tempfile::TempDir {
    let intent = |name: &str| {
        format!("(defeffect {} \"{}\" {{:fields [amount] :answer int :tags {{:context \"billing\" :role \"intent\"}}}})\n", name, name)
    };
    let mut all: Vec<(&str, String)> = vec![
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/intent/effects.hy", intent("Charge") + &intent("Refund")),
    ];
    all.extend(files.iter().cloned());
    let dir = world_repo_with(&all, "", enable);
    let arch_path = dir.path().join("architecture.hy");
    let declarations = ":foundation foundation\n  :verification-environment \"sim\"\n  \
                        :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] :tests [\"**/tests/**\"] \
                        :production [\"app/**\"] :business-modules [\"app.billing\"] :sets [\"app/*/entry/handler_sets.hy\"] \
                        :production-prefix \"production\" :simulation-prefix \"emulated\"}\n  \
                        :assembly-shape {:translation-point \"with-*-translation\" :retired-function \"handlers-of\" \
                        :translations \"TRANSLATION-HANDLERS\" :translation-layer \"protocol\" :intent-layer \"intent\"}";
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", declarations)
        .replace(
            "(layer entry",
            "(layer intent :roles [intent] :imports [core intent])\n           (layer protocol :roles [protocol] :imports [intent protocol])\n           (layer entry",
        )
        .replace("(defservice billing \"請求\" {:layers [core entry]})", "(defservice billing \"請求\" {:layers [core intent protocol entry]})");
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// Charge に答える handler charge-reads を置いた file(層の dir の下・請求の文脈)。
fn charge_handler(layer: &str, name: &str) -> String {
    tags("billing", layer)
        + &format!(
            "(require doeff-hy.macros [defhandler])\n(import app.billing.intent.effects [Charge])\n(defhandler {} []\n  (Charge [amount] (resume amount)))\n",
            name
        )
}

/// 本番の入口(組の file の production-handlers)が handler を並べる。
fn production_set(imports: &str, handlers: &str) -> String {
    tags("billing", "entry") + &format!("{}\n(defk production-handlers [] [{}])\n", imports, handlers)
}

/// agora-redesign #1809(DOEFF158): 本番の入口から届く intent の効果の答え手が、翻訳の層の外に在れば赤・翻訳の層に 2 つ在れば両方赤
/// (鳴る例)。翻訳の層の handler 1 つだけが答えれば鳴らない(鳴らない例)。
#[test]
fn intent_answerers_outside_or_doubled_in_the_translation_layer_are_red() {
    let outside = [
        ("app/billing/core/answer.hy", charge_handler("judgment", "charge-reads")),
        ("app/billing/entry/handler_sets.hy", production_set("(import app.billing.core.answer [charge-reads])", "charge-reads")),
    ];
    let dir = assembly_repo(&outside, "[\"DOEFF158\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF158");
    assert_eq!(hits.len(), 1, "{:?}\n{}", hits, report);
    assert!(hits[0].contains("outside:charge-reads"), "{:?}", hits);
    assert_eq!(violation(&report, &hits[0])["severity"], "error");

    let doubled = [
        ("app/billing/protocol/first.hy", charge_handler("protocol", "charge-reads")),
        ("app/billing/protocol/second.hy", charge_handler("protocol", "charge-again")),
        (
            "app/billing/entry/handler_sets.hy",
            production_set("(import app.billing.protocol.first [charge-reads])\n(import app.billing.protocol.second [charge-again])", "charge-reads charge-again"),
        ),
    ];
    let dir = assembly_repo(&doubled, "[\"DOEFF158\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF158");
    assert_eq!(hits.len(), 2, "{:?}\n{}", hits, report);
    assert!(hits.iter().all(|k| k.contains("shared:")), "{:?}", hits);

    let single = [
        ("app/billing/protocol/first.hy", charge_handler("protocol", "charge-reads")),
        ("app/billing/entry/handler_sets.hy", production_set("(import app.billing.protocol.first [charge-reads])", "charge-reads")),
    ];
    let dir = assembly_repo(&single, "[\"DOEFF158\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF158").is_empty(), "{:?}\n{}", keys(&report, "DOEFF158"), report);
}

/// 翻訳の層(protocol)に Charge の翻訳 charge-reads と、その列の定数 TRANSLATION-HANDLERS を置いた file。
fn translation_module() -> String {
    charge_handler("protocol", "charge-reads") + "(require doeff-hy.macros [val])\n(val TRANSLATION-HANDLERS [charge-reads])\n"
}

/// agora-redesign #1809(DOEFF155): 組み立ての層に退役した組み立ての関数(:retired-function)が残る・翻訳の列の 1 点が翻訳の列でない
/// handler を並べる、のどちらも赤(鳴る例)。翻訳の列の 1 点が翻訳の層の定数の列だけを並べれば鳴らない(鳴らない例)。
#[test]
fn assembly_shape_breaks_are_red() {
    let retired = [
        ("app/billing/protocol/translate.hy", translation_module()),
        ("app/billing/entry/assembly.hy", tags("billing", "entry") + "(defk handlers-of [foundation] [foundation])\n"),
    ];
    let dir = assembly_repo(&retired, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF155");
    assert!(hits.iter().any(|k| k.ends_with("::retired")), "{:?}\n{}", hits, report);
    assert!(hits.iter().all(|k| violation(&report, k)["severity"] == "error"), "{:?}", hits);

    let stray = [
        ("app/billing/protocol/translate.hy", translation_module()),
        (
            "app/billing/core/answer.hy",
            charge_handler("judgment", "charge-direct") + "(require doeff-hy.macros [val])\n(val DIRECT-HANDLERS [charge-direct])\n",
        ),
        (
            "app/billing/entry/assembly.hy",
            tags("billing", "entry")
                + "(import app.billing.core.answer [DIRECT-HANDLERS])\n\
                   (defk with-billing-translation [body] (with-handlers [#* DIRECT-HANDLERS] body))\n",
        ),
    ];
    let dir = assembly_repo(&stray, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF155");
    assert!(hits.iter().any(|k| k.contains("stray:")), "{:?}\n{}", hits, report);

    let shaped = [
        ("app/billing/protocol/translate.hy", translation_module()),
        (
            "app/billing/entry/assembly.hy",
            tags("billing", "entry")
                + "(import app.billing.protocol.translate [TRANSLATION-HANDLERS])\n\
                   (defk with-billing-translation [body] (with-handlers [#* TRANSLATION-HANDLERS] body))\n",
        ),
    ];
    let dir = assembly_repo(&shaped, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF155").is_empty(), "{:?}\n{}", keys(&report, "DOEFF155"), report);
}

/// 翻訳の層(protocol)の翻訳の列 TRANSLATION-HANDLERS を `order` の順に並べた file。charge-reads は Charge に答え、旧い置き場
/// (intent でない業務の module)の効果 LegacyRow を出し直す。legacy-reads は LegacyRow に答える。refund-reads は Refund に答え、
/// `refund_body` を本体に持つ。
fn reissuing_translations(order: &str, refund_body: &str) -> String {
    tags("billing", "protocol")
        + "(require doeff-hy.macros [defhandler val])\n\
           (import app.billing.intent.effects [Charge Refund])\n\
           (import app.billing.legacy.effects [LegacyRow])\n\
           (defhandler legacy-reads []\n  (LegacyRow [amount] (resume amount)))\n\
           (defhandler charge-reads []\n  (Charge [amount] (resume (LegacyRow amount))))\n"
        + &format!("(defhandler refund-reads []\n  (Refund [amount] (resume {})))\n(val TRANSLATION-HANDLERS [{}])\n", refund_body, order)
}

/// 翻訳の列の 1 点(組み立ての層)が翻訳の層の列だけを並べる。
fn translation_point() -> String {
    tags("billing", "entry")
        + "(import app.billing.protocol.translate [TRANSLATION-HANDLERS])\n\
           (defk with-billing-translation [body] (with-handlers [#* TRANSLATION-HANDLERS] body))\n"
}

const LEGACY_EFFECTS: &str = "(import doeff [EffectBase])\n(defclass LegacyRow [EffectBase])\n";

/// agora-redesign #1818(#1810 の子): DOEFF156(答えの置き場)を規則の ID で名指して確かめる。鳴る例 = 翻訳の handler が intent の効果を
/// 出し直す(refund-reads → Charge)・出し直した効果に答える同じ列の handler が内側に在る(charge-reads の LegacyRow に答える legacy-reads が
/// 後ろ)・組の file の組の関数が並べる土台の handler が業務の効果に答える。鳴らない例 = 同じ列の外側(前)の handler が答える intent でない
/// 効果の出し直し(単体テストの is_shape() == false の行の「出し直してよい」側)。
#[test]
fn assembly_answers_in_the_wrong_place_are_red() {
    let misplaced = [
        ("app/billing/legacy/effects.hy", LEGACY_EFFECTS.to_string()),
        ("app/billing/protocol/translate.hy", reissuing_translations("charge-reads legacy-reads refund-reads", "(Charge amount)")),
        ("app/billing/entry/assembly.hy", translation_point()),
        ("app/billing/core/answer.hy", charge_handler("judgment", "charge-direct")),
        ("app/billing/entry/handler_sets.hy", production_set("(import app.billing.core.answer [charge-direct])", "charge-direct")),
    ];
    let dir = assembly_repo(&misplaced, "[\"DOEFF156\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    let translate = "app/billing/protocol/translate.hy::DOEFF156";
    assert_eq!(
        keys(&report, "DOEFF156"),
        vec![
            "app/billing/entry/handler_sets.hy::DOEFF156::foundation:production_handlers:charge-direct:app.billing.intent.effects.Charge".to_string(),
            format!("{}::inner:charge_reads:app.billing.legacy.effects.LegacyRow", translate),
            format!("{}::target:refund_reads:app.billing.intent.effects.Charge", translate),
        ],
        "{}",
        report
    );
    assert!(keys(&report, "DOEFF156").iter().all(|k| violation(&report, k)["severity"] == "error"), "{}", report);

    let placed = [
        ("app/billing/legacy/effects.hy", LEGACY_EFFECTS.to_string()),
        ("app/billing/protocol/translate.hy", reissuing_translations("legacy-reads charge-reads refund-reads", "amount")),
        ("app/billing/entry/assembly.hy", translation_point()),
    ];
    let dir = assembly_repo(&placed, "[\"DOEFF156\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert!(keys(&report, "DOEFF156").is_empty(), "{:?}\n{}", keys(&report, "DOEFF156"), report);
}

/// 境目の部品(:boundary-parts)を宣言した world_repo(`parts` は :boundary-parts の要素の列・`enable` は規則の列)。
fn boundary_repo(files: &[(&str, String)], parts: &str, enable: &str) -> tempfile::TempDir {
    let dir = world_repo_with(files, "", enable);
    declare_boundary_parts(&dir, parts);
    dir
}

/// 一時の repo の architecture.hy に境目の部品(:boundary-parts — `parts` は要素の列)を足す。
fn declare_boundary_parts(dir: &tempfile::TempDir, parts: &str) {
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", &format!(":foundation foundation\n  :edge-mark \"real_world\"\n  :boundary-parts [{}]", parts));
    std::fs::write(&arch_path, text).unwrap();
}

/// agora-redesign #1797: 境目の部品(:boundary-parts)の module の中では、宣言した :touches の種類の生の副作用を DOEFF106 で当てない
/// — 種類の外の生の副作用と、宣言の無い module は今どおり当たる。層の中の file と層の置き場の外の file の両方で同じ。
#[test]
fn boundary_parts_allow_only_the_declared_touches() {
    let bench = "(import socket)\n(import time)\n(import subprocess)\n\
                 (defn open-one [] (socket.socket))\n(defn now [] (time.time))\n(defn spawn [] (subprocess.run [\"true\"]))\n";
    let files = [
        ("app/billing/entry/bench.hy", tags("billing", "entry") + bench),
        ("app/billing/entry/other.hy", tags("billing", "entry") + "(import socket)\n(defn open-one [] (socket.socket))\n"),
        ("app/tools/probe.hy", bench.to_string()),
    ];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network clock] :reason "本物の待ち受けへ撃つ縁の台")
                   (boundary-part "app.tools.probe" :touches [network clock] :reason "人が回す入口")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF106"),
        vec![
            "app/billing/entry/bench.hy::DOEFF106::spawn::subprocess.run",
            "app/billing/entry/other.hy::DOEFF106::open_one::socket.socket",
            "app/tools/probe.hy::DOEFF106::spawn::subprocess.run",
        ],
        "宣言した network・clock は当てず、種類の外の process と宣言の無い module は当てる: {}",
        report
    );
}

/// agora-redesign #1894: 境目の部品(:boundary-parts)の module の中では、:touches に宣言した触れる先の生の呼び(time.time → clock)を
/// DOEFF151 でも当てない(DOEFF106 と同じ写し方 — 生の時計を通す所は部品の宣言 1 か所)。反例 = 宣言の無い module・:touches に
/// clock の無い部品・層の置き場の外の宣言の無い file の time.time と、部品の中でも生の副作用でない呼び(効果の Now)は当たる。
/// 1 file の実行(保存前の中身を stdin で渡す editor の形)も同じ宣言を読む。
#[test]
fn retired_calls_skip_only_the_raw_calls_a_boundary_part_declares() {
    let stamp = "(defk stamp [] (time.time))\n";
    let files = [
        ("app/billing/entry/bench.hy", format!("{}(defk later [] (Now))\n", stamp)),
        ("app/billing/entry/other.hy", stamp.to_string()),
        ("app/billing/entry/wire.hy", stamp.to_string()),
        ("app/tools/probe.hy", stamp.to_string()),
        ("app/tools/loose.hy", stamp.to_string()),
    ];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network clock] :reason "本物の待ち受けへ撃つ縁の台")
                   (boundary-part "app.billing.entry.wire" :touches [network] :reason "時計を読まない縁の台")
                   (boundary-part "app.tools.probe" :touches [clock] :reason "人が回す入口")"#;
    let dir = retired_repo(&files);
    declare_boundary_parts(&dir, parts);
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF151"),
        vec![
            "app/billing/entry/bench.hy::DOEFF151::Now",
            "app/billing/entry/other.hy::DOEFF151::time.time",
            "app/billing/entry/wire.hy::DOEFF151::time.time",
            "app/tools/loose.hy::DOEFF151::time.time",
        ],
        "clock を宣言した部品の time.time だけを当てず、宣言の無い module・clock の無い部品の time.time と部品の中の Now は当てる: {}",
        report
    );
    let (_, stdout, stderr) =
        run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/billing/entry/bench.hy"], Some(&files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({}): {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF151"), vec!["app/billing/entry/bench.hy::DOEFF151::Now"], "1 file の実行も部品の宣言を読む: {}", single);
}

/// agora-redesign #1797: 境目の部品は許す種類(:touches — 閉じた語彙)と理由(:reason)を名指す。欠けと語の外は設定の誤りで止まる。
#[test]
fn boundary_parts_need_touches_and_a_reason() {
    let files = [("app/billing/entry/bench.hy", tags("billing", "entry") + "(defk run [] 1)\n")];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network])
                   (boundary-part "app.billing.entry.other" :touches [socket] :reason "語の外")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\"]");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("boundary-part app.billing.entry.bench に :reason"), "{}", stderr);
    assert!(stderr.contains("boundary-part app.billing.entry.other の :touches の socket は語の外"), "{}", stderr);
}

/// agora-redesign #1797: 境目の部品の生の副作用は DOEFF106 で当てないが、索引の証拠としては残るので、部品に届く deftest は縁で
/// :edge-mark の印が要る(DOEFF133)— 印の無い検は当たり、印の在る検は当たらない。
#[test]
fn tests_reaching_a_boundary_part_need_the_edge_mark() {
    let files = [
        ("app/billing/entry/bench.hy", tags("billing", "entry") + "(import socket)\n(defk open-one [] (socket.socket))\n"),
        (
            "app/billing/tests/test_bench.hy",
            "(import app.billing.entry.bench [open-one])\n\
             (deftest test-unmarked (<- s (open-one)) (assert s))\n\
             (deftest test-marked {:marks [\"real_world\"]} (<- s (open-one)) (assert s))\n"
                .to_string(),
        ),
    ];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network] :reason "縁の台")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\", \"DOEFF133\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF106").is_empty(), "宣言した network は当てない: {}", report);
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec!["app/billing/tests/test_bench.hy::DOEFF133::test_unmarked::edge"],
        "部品に届く印の無い検だけを当てる: {}",
        report
    );
}

// ------------------------------------------------------------------ 基点との比べ(--baseline-report・agora-redesign #1803)

/// DOEFF110 を critical にした一時の repo の設定(登録簿は空 — 基点との比べは登録簿に依らない)。
const CRITICAL_110: &str = "[tool.doeff-linter.rules.DOEFF110]\nlevel = \"critical\"\n";

/// root で editor-json を走らせた出力を基点の file に書き、その path を返す。
fn baseline_file(root: &Path) -> std::path::PathBuf {
    let (_, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    assert!(serde_json::from_str::<Value>(&stdout).is_ok(), "基点の出力が JSON でない: {}\n{}", stdout, stderr);
    let path = root.join("baseline.json");
    std::fs::write(&path, stdout).unwrap();
    path
}

/// 基点の file と比べて editor-json を走らせ、(終了コード・新しい critical の識別子の列)を返す。
fn against_baseline(root: &Path, baseline: &Path) -> (i32, Vec<String>) {
    let (code, stdout, stderr) =
        run(root, &["--output-format", "editor-json", "--no-log", "--baseline-report", baseline.to_str().unwrap()], None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("JSON でない({}): {}\n{}", e, stdout, stderr));
    let fresh = report["new_critical"].as_array().unwrap_or_else(|| panic!("new_critical が無い: {}", report));
    (code, fresh.iter().map(|v| v.as_str().unwrap().to_string()).collect())
}

#[test]
fn baseline_a_new_critical_is_named_and_changes_the_exit_code() {
    // 増えた: 基点に無い critical の識別子が new_critical に出て、終了コードが 4(新しい critical あり)になる。
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n(defn new-one [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/a.hy::DOEFF110::new_one".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_fixing_three_and_adding_one_is_still_a_new_critical() {
    // 件数ではなく識別子の集合で比べる: 3 件直して 1 件足した commit は、件数が減っても赤。
    let dir = definition_repo(&[("app/a.hy", "(defn a1 [x] x)\n(defn a2 [x] x)\n(defn a3 [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::write(dir.path().join("app/a.hy"), "(defn b1 [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/a.hy::DOEFF110::b1".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_fewer_or_same_criticals_are_not_new() {
    // 減った・同じ: new_critical は空で、終了コードは基点の比べの無い時と同じ(ここでは error の違反が在るので 1)。
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n(defn gone [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert!(fresh.is_empty(), "{:?}", fresh);
    assert_eq!(code, 1);
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert!(fresh.is_empty(), "{:?}", fresh);
    assert_eq!(code, 1);
}

#[test]
fn baseline_a_moved_file_keeps_its_criticals() {
    // file の移動: 規則と名が同じで path だけ違い、基点の同じ識別子が消えていれば同じ破れとみなす(1 対 1)。
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::remove_file(dir.path().join("app/a.hy")).unwrap();
    std::fs::write(dir.path().join("app/b.hy"), "(defn old [x] x)\n").unwrap();
    let (_, fresh) = against_baseline(dir.path(), &baseline);
    assert!(fresh.is_empty(), "{:?}", fresh);
    // 基点の識別子が残ったまま別の path に同じ名が増えたら、移動ではなく新しい破れ。
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/b.hy::DOEFF110::old".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_only_criticals_are_compared_and_a_bad_file_is_an_argument_error() {
    // critical でない規則の新しい破れは new_critical に入らない。読めない基点の file は引数の誤り(2)。
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n")], "");
    let baseline = baseline_file(dir.path());
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n(defn new-one [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert!(fresh.is_empty(), "{:?}", fresh);
    assert_eq!(code, 1);
    std::fs::write(&baseline, "not json").unwrap();
    let (code, _, stderr) =
        run(dir.path(), &["--output-format", "editor-json", "--no-log", "--baseline-report", baseline.to_str().unwrap()], None);
    assert_eq!(code, 2, "{}", stderr);
}
