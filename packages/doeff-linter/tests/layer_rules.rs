//! Tests for the layer rules (DOEFF101 to 108) and `--output-format editor-json` — build a temporary repo, run the binary, and inspect the output JSON.
//! Each rule has bad examples (a violation appears) and good examples (none appears), in both Hy and Python.

use serde_json::Value;
use std::io::Write;
use std::path::Path;
use std::process::{Command, Stdio};

/// Settings used by the tests (4 layers, laws, registry, environment words). `extra` can be appended at the end.
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
translation = "translation handler"

[tool.doeff-linter.layers.describe.core]
summary = "business judgment"
knows = "business judgment"
does_not_know = "means of communication"
question = "Does it stay the same when communication changes?"

[tool.doeff-linter.layers.describe.foundation]
knows = "real I/O"

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
statement = "core reads only intent"

[[tool.doeff-linter.laws]]
name = "no-environment-name"
adr = "ADR-TEST"
rules = ["DOEFF108"]
statement = "no environment words in business names"

[[tool.doeff-linter.laws]]
name = "not-wired-yet"
adr = "ADR-TEST"
statement = "a law with no hook yet"

[[tool.doeff-linter.laws]]
name = "absolute-imports"
rules = ["DOEFF016"]
statement = "do not use relative imports"

[tool.doeff-linter.registry]
dirs = ["registry/BREACHES"]
files = ["registry/keys.txt"]
{extra}
"#
    )
}

/// Build a temporary repo (pyproject.toml and a list of files).
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

/// Run the binary at the root and return (exit code, stdout, stderr). Can pass stdin.
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

/// Run editor-json over the whole repo and return (exit code, JSON).
fn editor(root: &Path) -> (i32, Value) {
    let (code, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    let value: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    (code, value)
}

/// From the list of violations, list the keys of rule `rule`.
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

/// Take the violation with key `key`.
fn violation<'a>(report: &'a Value, key: &str) -> &'a Value {
    report["violations"].as_array().unwrap().iter().find(|v| v["key"] == key).unwrap_or_else(|| panic!("key {} is missing: {}", key, report))
}

const INTENT_HY: &str = "(val MODULE-TAGS {:context \"billing\" :role \"intent\"})\n(defclass Charge [] \"billing intent\")\n";
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
    // The responsibility-boundary rule is critical by default even without a declaration.
    assert_eq!(hy["level"], "critical");
    // The position is the import symbol send (columns 27 to 31 of line 1).
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
    // Prefix match: urllib.request.urlopen matches urllib.request, and the pure urllib.parse does not.
    assert_eq!(
        keys(&report, "DOEFF102"),
        vec![
            "app/core/net.hy::core-imports-only-intent::httpx",
            "app/core/net.hy::core-imports-only-intent::urllib.request",
            "app/core/proc.py::core-imports-only-intent::subprocess",
        ]
    );
    // For a layer without laws (intent), the <rule> field of the key is the rule ID.
    assert_eq!(keys(&report, "DOEFF103"), vec!["app/intent/bad.hy::DOEFF103::definitions", "app/intent/bad_py.py::DOEFF103::definitions"]);
    let intent = violation(&report, "app/intent/bad.hy::DOEFF103::definitions");
    assert_eq!(intent["law"], Value::Null);
    assert_eq!(intent["range"]["start"]["line"], 2);
}

#[test]
fn tags_are_declared_and_roles_match_the_layer() {
    let dir = repo(
        &[
            // The definition has no tag, and the module head has no tag either.
            ("app/core/untagged.hy", "(defn decide [x] x)\n(defk plan [x] {:pre [] :post []} x)\n"),
            // A Python module that claims nothing.
            ("app/core/untagged_py.py", "def decide(x):\n    return x\n"),
            // Claims only by the definition's :tags (no module-head tag needed) — a good example.
            ("app/core/per_definition.hy", "(defk plan [x] \"plan\" {:pre [] :post [] :tags {:context \"billing\" :role \"judgment\"}} x)\n"),
            // The role does not fit the layer, or there is no context.
            ("app/core/wrong_role.hy", "(val MODULE-TAGS {:context \"billing\" :role \"translation\"})\n(defn f [] 1)\n"),
            ("app/core/no_context.py", "MODULE_TAGS = {\"role\": \"judgment\"}\n"),
            ("app/core/tests/test_x.hy", "(defn test-x [] 1)\n"),
            // Claims via the :tags in the defeffect dictionary — a good example (no module-head tag needed). A defeffect without tags is a bad example.
            ("app/intent/effects.hy", "(defeffect Charge \"billing\" {:fields [amount] :answer int :tags {:context \"billing\" :role \"intent\"}})\n"),
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
    assert!(untagged["message"].as_str().unwrap().contains("decide, plan"));
    // The position is the name of the first definition without a tag.
    assert_eq!(untagged["range"]["start"], serde_json::json!({"line": 0, "character": 6}));
    let modules = report["modules"].as_array().unwrap();
    let per_definition = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("per_definition.hy")).unwrap();
    assert_eq!(per_definition["layer"], "core");
    assert_eq!(per_definition["context"], "billing");
    assert_eq!(per_definition["role"], "judgment");
    assert_eq!(per_definition["violations"], 0);
    assert!(!modules.iter().any(|m| m["path"].as_str().unwrap().contains("/tests/")), "tests are outside the layer population");
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
            // Silent examples (DOEFF107): a call chain that does not reach a raw side effect, and a route through an allowed layer.
            ("app/core/sum.hy", "(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn add [a b] (+ a b))\n(defn total [xs] (add 1 2))\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    // DOEFF107 appears only on a route that reaches a raw side effect (core's later -> now), not on total that does not reach it or foundation's relay.
    let via_keys = keys(&report, "DOEFF107");
    assert_eq!(via_keys.len(), 1, "{:?}", via_keys);
    assert!(via_keys[0].starts_with("app/core/clock.hy::DOEFF107::later"), "{:?}", via_keys);
    let direct = keys(&report, "DOEFF106");
    assert!(direct.contains(&"app/core/clock.hy::DOEFF106::now::time.time".to_string()), "{:?}", direct);
    assert!(direct.iter().all(|k| k.starts_with("app/core/")), "foundation is an allowed layer: {:?}", direct);
    let strong = violation(&report, "app/core/clock.hy::DOEFF106::now::time.time");
    assert_eq!(strong["severity"], "error");
    let weak = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF106" && v["key"].as_str().unwrap().ends_with(".stat")).unwrap();
    assert_eq!(weak["severity"], "warning");
    // The route evidence (whole run only) is info with the path.
    let via = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF107").expect("route evidence");
    assert_eq!(via["severity"], "info");
    assert!(via["message"].as_str().unwrap().contains("later"));
    assert!(via["message"].as_str().unwrap().contains("now"));
}

#[test]
fn python_env_reads_outside_allowed_layers() {
    // Reading environment variables in a business layer's `.py` also hits DOEFF106 (not only Hy files).
    let dir = repo(
        &[
            (
                "app/core/settings.py",
                "import os\nfrom os import getenv as ge\nMODULE_TAGS = {\"context\": \"billing\", \"role\": \"judgment\"}\n\ndef mode():\n    return os.environ[\"MODE\"]\n\ndef level():\n    return ge(\"LEVEL\")\n",
            ),
            // Non-hitting examples: reads in an allowed layer (foundation), spellings inside strings and comments, and an environ attribute of something else.
            ("app/foundation/env.py", "import os\nMODULE_TAGS = {\"context\": \"io\", \"role\": \"foundation\"}\nX = os.getenv(\"X\")\n"),
            (
                "app/core/plain.py",
                "import os\nMODULE_TAGS = {\"context\": \"billing\", \"role\": \"judgment\"}\n# os.environ\nS = \"os.getenv\"\n\ndef f(request):\n    return os.path.join(request.environ, \"a\")\n",
            ),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
    let direct = keys(&report, "DOEFF106");
    assert_eq!(
        direct,
        vec!["app/core/settings.py::DOEFF106::level::os.getenv".to_string(), "app/core/settings.py::DOEFF106::mode::os.environ".to_string()],
        "{:?}",
        direct
    );
    let hit = violation(&report, "app/core/settings.py::DOEFF106::mode::os.environ");
    assert_eq!(hit["severity"], "error");
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
    std::fs::write(dir.path().join("registry/BREACHES/aaaaaaaaaaaa.txt"), "app/core/untagged.hy::DOEFF104\nreason — owner #1\n").unwrap();
    let (code, report) = editor(dir.path());
    let known = violation(&report, "app/core/untagged.hy::DOEFF104");
    assert_eq!(known["severity"], "warning");
    assert_eq!(known["registered"], true);
    let new = violation(&report, "app/core/other.hy::DOEFF104");
    assert_eq!(new["severity"], "error");
    assert_eq!(code, 1);

    // The same holds for a one-key-per-line registry. A reconciling rule is info (registered follows the registry), and the exit code is 0.
    let dir = repo(&files, "reconciling = [\"DOEFF104\"]\n");
    std::fs::write(dir.path().join("registry/keys.txt"), "# known\napp/core/other.hy::DOEFF104\n").unwrap();
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
    // Version 2 (#849, 8f7b7201) added the defk / deff heading `signatures` and the binding types `bindings`. Both are empty lists in a whole run.
    assert_eq!(report["version"], 2);
    assert!(report["root"].as_str().unwrap().starts_with('/'));
    assert_eq!(report["errors"], serde_json::json!([]));
    for field in ["violations", "modules", "rules", "errors", "signatures", "bindings"] {
        assert!(report[field].is_array(), "{}", field);
    }
    assert_eq!(report["signatures"], serde_json::json!([]));
    assert_eq!(report["bindings"], serde_json::json!([]));
    // In a pre-save single-file run (stdin), that Hy file's headings and binding types appear per field.
    let plan = "(defk plan [x]\n  {:pre [(: x int)] :post [(: % str)] :tags {:context \"billing\" :role \"judgment\"}}\n  (<- s str (render x))\n  (val n 1)\n  s)\n(defk render [x] {:pre [(: x int)] :post [(: % str)]} (str x))\n";
    std::fs::write(dir.path().join("app/core/plan.hy"), plan).unwrap();
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/plan.hy"], Some(plan));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(single["version"], 2);
    let signature = single["signatures"].as_array().unwrap().iter().find(|s| s["name"] == "plan").expect("heading of plan");
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
    let bound = bindings.iter().find(|b| b["name"] == "s").expect("binding of s");
    assert_eq!(bound["form"], "<-");
    assert_eq!(bound["origin"], "annotation");
    assert_eq!(bound["type"]["name"], "str");
    assert_eq!(bound["range"]["start"], serde_json::json!({"line": 2, "character": 6}));
    for field in ["form_range", "head_range", "annotation_range", "value_range"] {
        assert!(bound[field]["start"]["line"].is_number(), "{}", field);
    }
    assert_eq!(bound["absent"], false);
    assert_eq!(bound["raises"], serde_json::json!([]));
    let plain = bindings.iter().find(|b| b["name"] == "n").expect("binding of n");
    assert_eq!(plain["form"], "val");
    assert!(plain["annotation_range"].is_null());
    assert!(plain["modifier"].is_null());
    // Body text lines per definition (clause 20, #910) — empty in a whole run; in a stdin run, lines and text roles appear per definition.
    assert_eq!(report["bodies"], serde_json::json!([]));
    let body = single["bodies"].as_array().unwrap().iter().find(|b| b["name"] == "plan").expect("body of plan");
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
    // Existing Python rules (DOEFF016) also appear with line ranges and laws.
    let relative = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF016").expect("DOEFF016");
    assert_eq!(relative["law"], "absolute-imports");
    assert_eq!(relative["key"], Value::Null);
    assert_eq!(relative["range"], serde_json::json!({"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 16}}));
    for key in ["rule", "law", "adr", "severity", "path", "range", "message", "hint", "key", "registered"] {
        assert!(relative.get(key).is_some(), "field {} is missing", key);
    }
    assert_eq!(code, 1);
    let rules = report["rules"].as_array().unwrap();
    let unwired = rules.iter().find(|r| r["rule"] == "not-wired-yet").expect("law with no hook");
    assert_eq!(unwired["wired"], false);
    assert_eq!(unwired["adr"], "ADR-TEST");
    let core = rules.iter().find(|r| r["rule"] == "DOEFF101").unwrap();
    assert_eq!(core["wired"], true);
    assert!(core["statement"].as_str().unwrap().contains("core reads only intent"));
}

#[test]
fn stdin_path_judges_unsaved_text_with_utf16_columns() {
    let dir = repo(&[("app/intent/charge.hy", INTENT_HY), ("app/foundation/io.hy", FOUNDATION_HY), ("app/core/edit.hy", "")], "");
    let path = dir.path().join("app/core/edit.hy");
    let unsaved = ";; Japanese comment — positions are UTF-16\n(val MODULE-TAGS {:context \"請求\" :role \"judgment\"})\n(import 日本.語 app.foundation.io [send])\n";
    let (code, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", path.to_str().unwrap()], Some(unsaved));
    assert_eq!(code, 1);
    let report: Value = serde_json::from_str(&stdout).unwrap();
    let hit = violation(&report, "app/core/edit.hy::core-imports-only-intent::app.foundation.io.send");
    // The send in line 3 "(import 日本.語 app.foundation.io [" starts at column 32 in UTF-16 (38 in bytes).
    assert_eq!(hit["range"]["start"], serde_json::json!({"line": 2, "character": 32}));
    assert_eq!(hit["range"]["end"], serde_json::json!({"line": 2, "character": 36}));
    // path is the normalized root + the path from the root (the same form as a whole run). It judged the stdin contents, not the empty file on disk.
    assert_eq!(hit["path"], path.canonicalize().unwrap().to_str().unwrap());
    assert_eq!(report["modules"].as_array().unwrap().len(), 1, "a single-file run covers only that file");
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
    // Silent example: with a config of only known keys, DOEFF100 does not appear.
    assert!(keys(&report, "DOEFF100").is_empty(), "{:?}", keys(&report, "DOEFF100"));

    // A key unknown to this binary (config newer than the binary, or a typo) does not stop the whole lint; only that key is skipped and reported
    // as a DOEFF100 warning (previously exit code 2 left the editor's violation field empty).
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

    // A mismatch in config names is not silently dropped; exit code 2.
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
    // Remove pyproject, and pass a config file placed outside (only the section contents) with --config and --root.
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
    // A whole run (from a symlink dir) and a single-file run via a path through a symlink give the same file path for the same file.
    let (_, whole) = editor(&link);
    let whole_path = violation(&whole, "app/core/untagged.hy::DOEFF104")["path"].clone();
    let via_link = link.join("app/core/untagged.hy");
    let (_, stdout, _) = run(&link, &["--output-format", "editor-json", "--no-log", "--stdin", "--path", via_link.to_str().unwrap()], Some("(defn decide [x] x)\n"));
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(violation(&single, "app/core/untagged.hy::DOEFF104")["path"], whole_path);
    // Even when narrowed by a path argument (through a symlink or `..`), Python rule violations are not dropped.
    let dotted = link.join("app/core/../billing");
    let (_, stdout, _) = run(&link, &["--output-format", "editor-json", "--no-log", dotted.to_str().unwrap()], None);
    let narrowed: Value = serde_json::from_str(&stdout).unwrap();
    assert!(narrowed["violations"].as_array().unwrap().iter().any(|v| v["rule"] == "DOEFF016"), "{}", narrowed);
    assert!(narrowed["violations"].as_array().unwrap().iter().all(|v| v["path"].as_str().unwrap().contains("/app/billing/")));
}

/// Look up a violation's explanation (subject, reason, law_statement) by key.
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
    // DOEFF101: the subject is the import target and its layer; the reason is the layer description (config) and the layers that may be imported; the law text is verbatim.
    let direction = explanation(&report, "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send");
    assert_eq!(
        direction["subject"],
        "import target app.foundation.io.send is in layer foundation (path under app/foundation/) — This file is in layer core (business judgment) — path is under app/core/, tag role = judgment"
    );
    assert_eq!(
        direction["reason"],
        "Layer core (business judgment) is the layer farthest from the outside world; it knows business judgment and does not know means of communication. core may import only layer core, layer intent. The import target, layer foundation, has real I/O, so reading it from core makes core touch what layer foundation holds (even if handlers are swapped in the simulation, only that place touches the real thing)."
    );
    assert_eq!(direction["law_statement"], "core reads only intent");
    // DOEFF102, 104, 106, 107, 108, and also 103 and 105, have a subject and a reason sentence.
    let forbidden = explanation(&report, "app/core/bad.hy::core-imports-only-intent::httpx");
    assert!(forbidden["subject"].as_str().unwrap().starts_with("import target httpx (an I/O module forbidden in layer core) —"));
    assert!(forbidden["reason"].as_str().unwrap().contains("I/O is held by the handlers of layer foundation, layer entry"), "{}", forbidden["reason"]);
    let role = explanation(&report, "app/core/peer.hy::DOEFF105::translation");
    assert!(role["subject"].as_str().unwrap().contains("the path and the tag disagree (role translation is not a role of any layer (not in the current list of roles))"), "{}", role["subject"]);
    assert!(role["reason"].as_str().unwrap().starts_with("role translation (translation handler) is"), "{}", role["reason"]);
    assert_eq!(role["law_statement"], Value::Null, "a rule without a law is null");
    let untagged = explanation(&report, "app/core/untagged.hy::DOEFF104");
    assert!(untagged["subject"].as_str().unwrap().starts_with("definition decide (no tags)"));
    assert!(explanation(&report, "app/core/empty.py::DOEFF104")["subject"].as_str().unwrap().starts_with("this module declares no tags at all"));
    let types = explanation(&report, "app/intent/funcs.hy::DOEFF103::definitions");
    // intent has no description — the sentence is built only from the layer order and the rules.
    assert_eq!(
        types["reason"],
        "Layer intent is layer number 2 in order of distance from the outside world. This layer holds only type declarations, so functions and handlers that contain processing cannot be placed here (move them to another layer)."
    );
    let raw = explanation(&report, "app/core/bad.hy::DOEFF106::now::time.time");
    assert!(raw["subject"].as_str().unwrap().starts_with("definition now (defn) directly touches time.time (a raw side effect of time; strong evidence: a name via an import, or a built-in)"), "{}", raw["subject"]);
    let via = report["violations"].as_array().unwrap().iter().find(|v| v["rule"] == "DOEFF107").unwrap();
    assert!(via["explanation"]["subject"].as_str().unwrap().starts_with("definition later reaches, through now, time.time"));
    let env = explanation(&report, "app/billing/handlers_fake.hy::no-environment-name::fake_charge");
    assert_eq!(env["subject"], "name of definition fake_charge (defhandler) (contains environment words fake)");
    assert_eq!(env["law_statement"], "no environment words in business names");
    // The module's layer_reason, and the top-level layers (a layer without a description has only name).
    let modules = report["modules"].as_array().unwrap();
    let peer = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("peer.hy")).unwrap();
    assert!(peer["layer_reason"].as_str().unwrap().starts_with("the path and the tag disagree"), "{}", peer["layer_reason"]);
    let good = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("app/intent/charge.hy")).unwrap();
    assert_eq!(good["layer_reason"], "Decided by the path location — under app/intent/ is layer intent. The tag role = intent is also a role of this layer");
    assert_eq!(
        report["layers"],
        serde_json::json!([
            {"name": "core", "summary": "business judgment", "knows": "business judgment", "does_not_know": "means of communication", "question": "Does it stay the same when communication changes?"},
            {"name": "intent", "summary": null, "knows": null, "does_not_know": null, "question": null},
            {"name": "foundation", "summary": null, "knows": "real I/O", "does_not_know": null, "question": null},
            {"name": "entry", "summary": null, "knows": null, "does_not_know": null, "question": null}
        ])
    );
    // The explanation of Python per-statement rules is null (the form the contract allows).
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
    assert!(stdout.contains("This is: This file is in layer core (business judgment)"), "{}", stdout);
    assert!(stdout.contains("Why: role translation (translation handler) is"), "{}", stdout);
    assert!(stdout.contains("Fix: "), "{}", stdout);
}

#[test]
fn describing_an_unknown_layer_is_a_config_error() {
    let dir = repo(&[], "");
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap() + "\n[tool.doeff-linter.layers.describe.ghost]\nsummary = \"nonexistent layer\"\n";
    std::fs::write(dir.path().join("pyproject.toml"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("layers.describe") && stderr.contains("ghost"), "{}", stderr);
}

/// A config repo that lines up the service-first form (controllers/<service>/{core,intent,protocol}/, controllers/shared/{core,intent}/, controllers/foundation/) and
/// the layer-first form (controllers/core/).
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

/// The head lines of the tag (context and role).
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
    // Reading another service's core and protocol is a breach. intent and shared may be read. The layer-first form (no service) is not judged.
    assert_eq!(
        keys(&report, "DOEFF109"),
        vec![
            "controllers/billing/core/decide.hy::DOEFF109::controllers.custody.core.lease.pick",
            "controllers/billing/protocol/talk.hy::DOEFF109::controllers.custody.protocol.http.talk",
        ]
    );
    let crossing = explanation(&report, "controllers/billing/core/decide.hy::DOEFF109::controllers.custody.core.lease.pick");
    assert!(crossing["subject"].as_str().unwrap().starts_with("import target controllers.custody.core.lease.pick is in service custody's layer core (path under controllers/custody/core/) — This file is in layer core of service billing — path is under controllers/billing/core/"), "{}", crossing["subject"]);
    assert!(crossing["reason"].as_str().unwrap().contains("Reading judgments or translations across services means that when the contents of custody change, billing breaks. Requests to custody go through custody's intents"), "{}", crossing["reason"]);
    // The layer judgment also works in the service-first form (two forms of the core location).
    let modules = report["modules"].as_array().unwrap();
    let decide = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("billing/core/decide.hy")).unwrap();
    assert_eq!(decide["layer"], "core");
    assert_eq!(decide["service"], "billing");
    assert!(decide["layer_reason"].as_str().unwrap().contains("under controllers/billing/core/ is layer core of service billing"), "{}", decide["layer_reason"]);
    let old = modules.iter().find(|m| m["path"].as_str().unwrap().ends_with("controllers/core/old.hy")).unwrap();
    assert_eq!(old["layer"], "core");
    assert_eq!(old["service"], Value::Null);
    // A mismatch between :context and the dir's service is an info notice. shared is not checked.
    assert_eq!(keys(&report, "DOEFF113"), vec!["controllers/custody/core/misplaced.hy::DOEFF113::billing"]);
    assert_eq!(violation(&report, "controllers/custody/core/misplaced.hy::DOEFF113::billing")["severity"], "info");

    // A pair written in the exceptions may be read.
    let dir = service_repo(&files, "exceptions = [{ from = \"billing\", to = \"custody\" }]\n");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF109").is_empty());
}

#[test]
fn named_paths_judge_definitions_only_in_the_named_files() {
    // A run that names paths on the command line (the commit hook) judges the definition rules only on the files under the named paths.
    // That it does not read files outside the named paths is checked with an unreadable file (not UTF-8) — a whole run claims it is "unreadable".
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

/// A repo with only the definition-writing rules (DOEFF110 to 112).
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
(deff callback [x] {:tags {:context "billing" :role "judgment"}} x)  ; defk にできない: callback of an external library
; defk にできない: where a plain callable is passed
(deff above [x] {:tags {:context "billing" :role "judgment"}} x)
(deff bare [x] {:tags {:context "billing" :role "judgment"}} x)
(defeffect Charge "Billing" {:fields [amount] :answer int})
"#;
    let dir = definition_repo(
        &[("app/billing/logic.hy", source), ("vendor/macros/m.hy", "(defn m [] 1)\n"), ("app/billing/tests/t.hy", "(defn t [] 1)\n")],
        "",
    );
    let (code, report) = editor(dir.path());
    assert_eq!(code, 1);
    // defn is a violation (with a decorator too). Inside eval-and-compile, and in excluded locations and tests, it is outside.
    assert_eq!(keys(&report, "DOEFF110"), vec!["app/billing/logic.hy::DOEFF110::decorated", "app/billing/logic.hy::DOEFF110::helper"]);
    // The reason comment is on the same line or the line just before.
    assert_eq!(keys(&report, "DOEFF111"), vec!["app/billing/logic.hy::DOEFF111::bare"]);
    // Tags required: no :tags, a missing required key, and defeffect.
    assert_eq!(
        keys(&report, "DOEFF112"),
        vec!["app/billing/logic.hy::DOEFF112::Charge", "app/billing/logic.hy::DOEFF112::half", "app/billing/logic.hy::DOEFF112::untagged"]
    );
    let defn = explanation(&report, "app/billing/logic.hy::DOEFF110::helper");
    assert_eq!(defn["subject"], "definition helper (defn)");
    assert!(defn["reason"].as_str().unwrap().starts_with("defn cannot have a contract dict and cannot write :tags"));
    assert!(violation(&report, "app/billing/logic.hy::DOEFF110::helper")["hint"].as_str().unwrap().starts_with("Make it a defk (no reason is visible why it must be a bare function)"));
    let half = explanation(&report, "app/billing/logic.hy::DOEFF112::half");
    assert_eq!(half["subject"], "definition half (defk) lacks role in :tags");
    let untagged = explanation(&report, "app/billing/logic.hy::DOEFF112::untagged");
    assert_eq!(untagged["subject"], "definition untagged (defk) lacks context, role in :tags (the contract dict has no :tags at all)");

    // Can be supplemented by the module head tag (module_default = true, the default) / cannot (false). Registry keys are warnings.
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
    // The head dictionary {:tags … :check […]} of doeff-hy's defrecord. The head dictionary can be omitted, so
    // the default DOEFF112 does not count defrecord. A repo that adds it to require_on requires :tags in the head dictionary.
    let source = r#"(defrecord ChatId "chat id" {:tags {:context "chat" :role "type"} :check [(ok? value)]} #^ str value)
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
    // doeff-hy's defwire (a type at the JSON boundary) expands to defrecord. The head dictionary's :tags are the definition's tags,
    // and :names and :unknown are keys of the wire shape (not tags). A repo that adds defwire to require_on names a defwire without tags.
    let source = r#"(defwire LandingRow "one row of the ledger" {:tags {:context "chat" :role "type"} :names :camel :unknown :reject} #^ str lane-id)
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
    // Put the config file and the registry in the same dir outside the repo, and read them relative to the config file with registry.config_files.
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
    // A breach listed in the registry gets the config severity (info), and one not listed is an error as before.
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["severity"], "info");
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["registered"], true);
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::new_one")["severity"], "error");
    assert_eq!(code, 1);
    // An unknown severity is a config error.
    std::fs::write(&config_path, config.replace("\"info\"", "\"loud\"")).unwrap();
    let (code, _, stderr) = run(dir.path(), &args, None);
    assert_eq!(code, 2);
    assert!(stderr.contains("registered_severity"), "{}", stderr);
}

#[test]
fn level_is_declared_per_rule_and_registry_lowers_only_the_severity() {
    // The gravity (level) is the repo's declaration, and lowering the severity in the registry does not lower it — the axis by which the editor counts "untouched critical".
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
    // A rule with neither a declaration nor a default table uses the rule's own severity (error = major).
    let dir = definition_repo(&[("app/a.hy", source)], "");
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/a.hy::DOEFF110::old")["level"], "major");
    // For a rule in the default table, the repo's declaration also wins (write only where it differs from the default).
    let dir = repo(
        &[("app/foundation/io.hy", FOUNDATION_HY), ("app/core/bad.hy", "(import app.foundation.io [send])\n(val MODULE-TAGS {:context \"billing\" :role \"judgment\"})\n(defn decide [x] x)\n")],
        "[tool.doeff-linter.rules.DOEFF101]\nlevel = \"major\"\n",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/core/bad.hy::core-imports-only-intent::app.foundation.io.send")["level"], "major");
    // An unknown gravity is a config error.
    let dir = definition_repo(&[("app/a.hy", source)], &config.replace("\"critical\"", "\"loud\""));
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("rules.DOEFF110.level"), "{}", stderr);
}

/// A repo with architecture.hy at the repo root (the TOML has only rule on/off).
fn architecture_repo(files: &[(&str, String)], toml_extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let architecture = r#"
(defarchitecture sample
  :root "app"
  :layers [(layer core :summary "business judgment" :roles [judgment type] :imports [core intent])
           (layer intent :summary "request types" :roles [intent type] :imports [intent] :types-only True)
           (layer foundation :summary "generic I/O" :roles [foundation] :imports [foundation])]
  :shared "shared"
  :foundation foundation)
(defservice billing "Billing" {:depends-on [custody ledger] :layers [core intent]})
(defservice custody "Custody" {:layers [core intent]})
(defservice ledger "Ledger" {:layers [core intent]})
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

/// A root-level module whose name is declared in `:root-modules` does not become DOEFF114; removing the declaration (or a file with another name) produces DOEFF114.
#[test]
fn root_modules_declared_pass_and_undeclared_fail_doeff114() {
    let files = [("app/macros.hy", "(defn m [] 1)\n".to_string()), ("app/other.hy", "(defn o [] 1)\n".to_string())];
    let with_declaration = architecture_repo(&files, "");
    let source = std::fs::read_to_string(with_declaration.path().join("architecture.hy")).unwrap();
    std::fs::write(with_declaration.path().join("architecture.hy"), source.replace("  :foundation foundation)", "  :foundation foundation\n  :root-modules [\"macros\"])")).unwrap();
    let (_, report) = editor(with_declaration.path());
    assert_eq!(keys(&report, "DOEFF114"), vec!["app/other.hy::DOEFF114"], "{}", report);
    let without = architecture_repo(&files, "");
    let (_, report) = editor(without.path());
    assert_eq!(keys(&report, "DOEFF114"), vec!["app/macros.hy::DOEFF114", "app/other.hy::DOEFF114"], "{}", report);
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
    // DOEFF114: every module outside the declaration (directly under root, directly under a service dir, inside a dir not in the declaration — layer-first dirs and old dirs alike, no exceptions).
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
    // DOEFF115: a service dir not in the declaration, and a layer dir not in the declaration inside a service (one per dir).
    assert_eq!(keys(&report, "DOEFF115"), vec!["app/billing/scripts::DOEFF115", "app/core::DOEFF115", "app/mystery::DOEFF115", "app/old::DOEFF115"]);
    // Suggested destination: the service from the :context tag, the layer from the :role tag (if it cannot be inferred, the layer of the current path segment).
    assert_eq!(
        violation(&report, "app/core/legacy_core.hy::DOEFF114")["hint"],
        "Move it to app/kanban/core/legacy_core.hy (a proposal inferred from the :context tag for the service and the definition's :role tag for the layer; the current location's layer only when it cannot be inferred)"
    );
    assert!(violation(&report, "app/billing/scripts/tool.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("Move it to app/billing/core/tool.hy"));
    assert!(violation(&report, "app/old/anything.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("Move it to app/<service>/<layer>/anything.hy"));
    let undeclared = explanation(&report, "app/mystery::DOEFF115");
    assert!(undeclared["subject"].as_str().unwrap().contains("service mystery is not declared in architecture.hy"), "{}", undeclared["subject"]);
    // DOEFF116: reading something other than the dependency's intent, or reading a service not in :depends-on. shared and foundation are not services.
    assert_eq!(
        keys(&report, "DOEFF116"),
        vec!["app/billing/core/decide.hy::DOEFF116::app.custody.core.pick.pick", "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge"]
    );
    let undeclared_dependency = explanation(&report, "app/custody/core/uses_billing.hy::DOEFF116::app.billing.intent.charge.Charge");
    assert!(undeclared_dependency["reason"].as_str().unwrap().contains("The declaration of service custody's dependencies (:depends-on = none) does not include billing"), "{}", undeclared_dependency["reason"]);
    // DOEFF117 (info): billing declared a dependency on ledger but does not read it. The position is architecture.hy.
    let unused = violation(&report, "architecture.hy::DOEFF117::billing>ledger");
    // Silent example: it does not appear for custody, a dependency billing does read (only the one for ledger, declared but unused).
    assert_eq!(keys(&report, "DOEFF117"), vec!["architecture.hy::DOEFF117::billing>ledger"]);
    assert_eq!(unused["severity"], "info");
    assert!(unused["path"].as_str().unwrap().ends_with("architecture.hy"));
    // DOEFF113: a :context mismatch inside a declared service is a warning.
    assert_eq!(violation(&report, "app/billing/core/wrong_context.hy::DOEFF113::custody")["severity"], "warning");
    // A module outside the declaration infers its layer from the :role tag and receives the layer rules (role judgment -> core; core reading foundation gives DOEFF101).
    assert_eq!(keys(&report, "DOEFF101"), vec!["app/core/legacy_core.hy::DOEFF101::app.foundation.io.send"]);
    let module = report["modules"].as_array().unwrap().iter().find(|m| m["path"].as_str().unwrap().ends_with("app/core/legacy_core.hy")).unwrap();
    assert!(module["layer_reason"].as_str().unwrap().starts_with("Decided by tag — app/core/ is not a location declared in architecture.hy"), "{}", module["layer_reason"]);
    // Even when existing ones are listed in the registry, registered_severity = warning keeps them visible as yellow.
    let dir = architecture_repo(&files, "[tool.doeff-linter.registry]\nfiles = [\"known.txt\"]\n[tool.doeff-linter.rules.DOEFF114]\nregistered_severity = \"warning\"\n");
    std::fs::write(dir.path().join("known.txt"), "app/old/anything.hy::DOEFF114\n").unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(violation(&report, "app/old/anything.hy::DOEFF114")["severity"], "warning");
    assert_eq!(violation(&report, "app/top.hy::DOEFF114")["severity"], "error");
    // :legacy is removed — writing it is a config error.
    std::fs::write(dir.path().join("architecture.hy"), "(defarchitecture s :root \"app\" :layers [(layer core)] :legacy [\"app/old\"])\n").unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains(":legacy has been retired"), "{}", stderr);
    // The editor-json architecture (the list of services and the layer declarations).
    let architecture = &report["architecture"];
    assert_eq!(architecture["name"], "sample");
    assert_eq!(architecture["services"][0]["name"], "billing");
    assert_eq!(architecture["services"][0]["description"], "Billing");
    assert_eq!(architecture["services"][0]["depends_on"], serde_json::json!(["custody", "ledger"]));
    assert_eq!(architecture["layers"][0]["summary"], "business judgment");
    assert_eq!(report["layers"][0]["name"], "core");
}

/// A service whose name differs from its :dir dir (the form where doeff-cluster hides a partition in a _ dir — ADR-DOE-API-001 R15 of doeff). The location is the :dir dir
/// (not DOEFF114), and DOEFF113 compares the tag's :context against the service name, not the dir.
#[test]
fn a_service_with_its_own_dir_matches_the_context_by_its_name() {
    let files = [
        ("app/_custody/core/pick.hy", tags("custody", "judgment") + "(defn pick [] 1)\n"),
        ("app/_custody/intent/lease.hy", tags("custody", "intent") + "(defclass Lease [])\n"),
        ("app/_custody/core/wrong_context.hy", tags("billing", "judgment") + "(defn g [] 1)\n"),
    ];
    let dir = architecture_repo(&files, "");
    let architecture = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap();
    let hidden = architecture.replace("(defservice custody \"Custody\" {:layers", "(defservice custody \"Custody\" {:dir \"_custody\" :layers");
    assert_ne!(hidden, architecture);
    std::fs::write(dir.path().join("architecture.hy"), hidden).unwrap();
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF114").is_empty(), "{}", report);
    // A file whose :context is the service name (custody) is silent. Only a :context that differs from the name (billing) is a warning.
    assert_eq!(keys(&report, "DOEFF113"), vec!["app/_custody/core/wrong_context.hy::DOEFF113::billing"]);
    let wrong = violation(&report, "app/_custody/core/wrong_context.hy::DOEFF113::billing");
    assert_eq!(wrong["severity"], "warning");
    assert!(wrong["message"].as_str().unwrap().contains("disagrees with the location's service custody"), "{}", wrong["message"]);
    assert!(wrong["explanation"]["reason"].as_str().unwrap().starts_with("The dir points to service custody"), "{}", wrong["explanation"]["reason"]);
}

/// #1799: DOEFF101 and DOEFF116 do not count the same import twice (DOEFF101 owns the layer direction). The DOEFF116 guidance does not say
/// "add it to the declaration" when the dependency would form a cycle. The destination layer for DOEFF114 is inferred from the definition's :role (the current location's layer only when it cannot be inferred).
#[test]
fn dependency_and_place_rules_do_not_double_count_or_misguide() {
    let files = [
        // Reading another service's core from an intent layer — one hit by the layer direction (DOEFF101). It is not also counted as an undeclared dependency (DOEFF116).
        ("app/custody/intent/peek.hy", tags("custody", "intent") + "(import app.billing.core.rate [rate])\n(defclass Peek [])\n"),
        ("app/billing/core/rate.hy", tags("billing", "judgment") + "(defn rate [] 1)\n"),
        // billing depends on custody — adding custody's read of billing to :depends-on would form a cycle.
        ("app/billing/intent/charge.hy", tags("billing", "intent") + "(defclass Charge [])\n"),
        ("app/custody/core/uses_billing.hy", tags("custody", "judgment") + "(import app.billing.intent.charge [Charge])\n(defn u [] 1)\n"),
        // ledger does not depend on custody — the guidance stays "add it".
        ("app/ledger/intent/entry.hy", tags("ledger", "intent") + "(defclass Entry [])\n"),
        ("app/custody/core/uses_ledger.hy", tags("custody", "judgment") + "(import app.ledger.intent.entry [Entry])\n(defn v [] 1)\n"),
        // A judgment placed in intent/ of a layer-first dir — the destination layer is by role (judgment -> core). Only when the role fits two layers does the path's intent apply.
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
    assert!(cyclic.contains("a cycle") && cyclic.contains("into custody's intent or into shared") && !cyclic.contains("Add the dependency to :depends-on"), "{}", cyclic);
    let plain = violation(&report, "app/custody/core/uses_ledger.hy::DOEFF116::app.ledger.intent.entry.Entry")["hint"].as_str().unwrap().to_string();
    assert!(plain.starts_with("Add the dependency to :depends-on"), "{}", plain);
    assert!(violation(&report, "app/intent/judge.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("Move it to app/kanban/core/judge.hy"));
    assert!(violation(&report, "app/intent/shape.hy::DOEFF114")["hint"].as_str().unwrap().starts_with("Move it to app/kanban/intent/shape.hy"));
    // Turning DOEFF101 off makes DOEFF116 count the same import (avoiding the double count applies only when DOEFF101 judges).
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
    assert!(stderr.contains("ghost") && stderr.contains("service a is declared twice") && stderr.contains("architecture.hy:3:1"), "{}", stderr);
    // Declaring layers in both TOML and architecture.hy is an error (the declaration lives in one place).
    let dir = architecture_repo(&[], "[tool.doeff-linter.layers]\norder = [\"core\"]\npaths = { core = \"app/core\" }\n");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("duplicate declaration"), "{}", stderr);
    // A repo without architecture.hy stays as before (the editor-json architecture is null).
    let (_, report) = editor(repo(&[], "").path());
    assert_eq!(report["architecture"], Value::Null);
}

/// A repo with an architecture.hy that declares the kinds of reasons, plus the definition rules.
fn reason_repo(source: &str, registry: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture s :root "app" :layers [(layer core)]
  :plain-callable-reasons [(reason library-callback "called as a plain function by an external library (sorted key, dataclass hook)")
                           (reason process-entry "main, the entry of a process")])
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
    // What it emits deterministically is only: no comment, an empty reason, and "same as above" (同上) and its variants. A kind label is not required (kinds not in the list are accepted too).
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
    assert!(same["explanation"]["reason"].as_str().unwrap().contains("library-callback (called as a plain function by an external library"), "{}", same["explanation"]["reason"]);
    assert!(same["hint"].as_str().unwrap().contains("do not use \"同上\" (\"ditto\")"));
    // defn: if the same-line comment names a kind from the list, "make it a deff"; otherwise "make it a defk".
    assert!(violation(&report, "app/core/x.hy::DOEFF110::main")["hint"].as_str().unwrap().starts_with("Make it a deff (reason library-callback"));
    assert!(violation(&report, "app/core/x.hy::DOEFF110::helper")["hint"].as_str().unwrap().starts_with("Make it a defk"));
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
    assert_eq!(violation(&report, "app/tests/t.hy::DOEFF118::test_d")["explanation"]["subject"], "definition test_d (fn) — a function in a test location whose name starts with test");
    assert!(violation(&report, "app/tests/t.hy::DOEFF118::test_a")["hint"].as_str().unwrap().starts_with("Make it a deftest"));
    assert_eq!(code, 1);
}

/// The DOEFF119 repo (population of the definition rules = app, with a registry of known breaches).
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
    // Value classes (Point2D), exceptions, and external-library bases (EffectBase) do not appear. Fields only (Row — only __post_init__ validation) is info.
    // Touching raw side effects (Client's method, Poller's field initial value) is error; rewriting self's fields (Counter, and Child whose base is inside the repo) is warning.
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
    assert!(client["explanation"]["subject"].as_str().unwrap().contains("a class that touches the outside world (evidence "), "{}", client["explanation"]["subject"]);
    assert!(client["explanation"]["subject"].as_str().unwrap().contains("httpx.Client"));
    assert!(client["hint"].as_str().unwrap().contains("(session val …)"));
    let counter = violation(&report, "app/core/shapes.hy::DOEFF119::Counter");
    assert!(counter["explanation"]["subject"].as_str().unwrap().contains("bump: self.n"), "{}", counter["explanation"]["subject"]);
    assert!(counter["explanation"]["subject"].as_str().unwrap().contains("note: self.seen"));
    assert!(counter["hint"].as_str().unwrap().starts_with("Values are defrecord (immutable), behavior is pure functions that return new values"));
    assert!(violation(&report, "app/core/shapes.hy::DOEFF119::Row")["hint"].as_str().unwrap().starts_with("Make it a defrecord"));
    assert_eq!(violation(&report, "app/core/shapes.hy::DOEFF119::Known")["registered"], true);
}

/// The DOEFF120 repo — architecture.hy (with foundation and :wire-modules), a TOML with only DOEFF120, and a registry of known breaches.
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
(defservice billing "Billing" {:layers [core intent protocol]})
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
        // core Hy — import and annotations (#^). Words in a docstring are not counted.
        (
            "app/billing/core/decide.hy",
            "(import doeff_hy.wire [JsonValue])\n(defk decide [#^ JsonValue payload] {:pre [] :post []}\n  \"Receives a JsonValue and decides\"\n  (get payload \"k\"))\n",
        ),
        // Words inside a type alias string are also counted (1 name + 2 words in the string = 3 places, all on line 3).
        (
            "app/billing/protocol/shape.py",
            "from typing import TypeAlias\n\nJsonValue: TypeAlias = \"dict[str, JsonValue] | list[JsonValue] | str | int | float | bool | None\"\n",
        ),
        // JsonObject (dict[str, JsonValue]) is the same loophole — in a string annotation and inside Hy #( … ).
        ("app/billing/core/obj.py", "def read(payload: \"JsonObject\") -> int:\n    return 1\n"),
        ("app/billing/intent/body.hy", "(setv Body (get dict #(str JsonObject)))\n"),
        // Mentions only in strings, comments, and docstrings are not counted.
        ("app/billing/core/doc_only.hy", ";; JsonValue is not used\n(defk f [x] \"Takes a typed value, not JsonValue\" x)\n"),
        ("app/billing/core/doc_only.py", "\"\"\"Only talks about JsonValue.\"\"\"\n# JsonValue\ndef f(x: int) -> int:\n    \"\"\"Does not write JsonObject\"\"\"\n    return x\n"),
        // In :wire-modules and in the foundation layer -> allowed. Even in foundation, not allowed if it is not in :wire-modules.
        ("app/foundation/records_client.hy", "(import doeff_hy.wire [JsonValue])\n(defk send [#^ JsonValue body] body)\n"),
        ("app/foundation/other.py", "from doeff_hy.wire import JsonValue\n"),
        // Matches a :wire-modules pattern but outside foundation -> not allowed (the reason goes in the explanation).
        ("app/billing/protocol/wire_codec.hy", "(import doeff_hy.wire [JsonValue])\n"),
        // The built-in generic resolver (a module spelling ending in doeff_records._wire) is allowed wherever it is placed.
        ("vendor/doeff_records/_wire.hy", "(setv JsonValue object)\n"),
        // Below a hidden dir is outside the population.
        (".venv/lib/x.py", "JsonValue = dict\n"),
        // Known ones listed in the registry get registered_severity (info in this config).
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
    // One per module; the position is the first use; the count and other lines go in the explanation.
    let decide = violation(&report, "app/billing/core/decide.hy::DOEFF120");
    assert_eq!(decide["severity"], "error");
    assert_eq!(decide["range"]["start"], serde_json::json!({"line": 0, "character": 23}));
    assert_eq!(decide["explanation"]["subject"], "module app.billing.core.decide — JsonValue used in 2 places (first on line 1, others on lines 2)");
    assert!(decide["explanation"]["reason"].as_str().unwrap().ends_with("This module is not in :wire-modules."), "{}", decide["explanation"]["reason"]);
    assert!(decide["hint"].as_str().unwrap().starts_with("Turn the JSON shape into a type with defwire"), "{}", decide["hint"]);
    let shape = violation(&report, "app/billing/protocol/shape.py::DOEFF120");
    assert_eq!(shape["severity"], "error");
    assert_eq!(shape["explanation"]["subject"], "module app.billing.protocol.shape — JsonValue used in 3 places (all on line 3)");
    assert_eq!(violation(&report, "app/billing/core/obj.py::DOEFF120")["explanation"]["subject"], "module app.billing.core.obj — JsonObject used in 1 place (line 1)");
    let listed = violation(&report, "app/billing/protocol/wire_codec.hy::DOEFF120");
    assert_eq!(listed["severity"], "error");
    assert!(
        listed["explanation"]["reason"].as_str().unwrap().contains("matches app.billing.protocol.wire_* in :wire-modules but is not in the foundation layer (app/foundation/), so it is not allowed"),
        "{}",
        listed["explanation"]["reason"]
    );
    assert!(listed["hint"].as_str().unwrap().starts_with("Move the sending and receiving itself into a module under app/foundation/"), "{}", listed["hint"]);
    let known = violation(&report, "app/billing/core/known.hy::DOEFF120");
    assert_eq!(known["severity"], "info");
    assert_eq!(known["registered"], true);

    // A pre-save single file (stdin) gets the same judgment. An allowed module produces nothing.
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/billing/core/decide.hy"], Some(files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}", e, stderr));
    assert_eq!(keys(&single, "DOEFF120"), vec!["app/billing/core/decide.hy::DOEFF120"]);
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/foundation/records_client.hy"], Some(files[6].1));
    let allowed: Value = serde_json::from_str(&stdout).unwrap();
    assert!(keys(&allowed, "DOEFF120").is_empty(), "{}", allowed);
}

/// The repo for the smell rules (DOEFF121 to 125) — layers core and protocol, the population of the definition rules, the smell settings, and a registry.
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

/// A shrunk form of decide-tag (agora-controllers controllers/kanban/core/tag_judgment.hy) — it has all 5 smells.
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
    let rules = "(defrecord Refusal \"Refusal\" {:failure True} #^ str reason #^ str detail)\n(defrecord TagsAccepted #^ tuple words)\n";
    let files = [("app/core/rules.hy", rules), ("app/core/tag_judgment.hy", DECIDE_TAG), ("app/protocol/tags.hy", DECIDE_TAG)];
    let dir = smell_repo(&files, "", "");
    let (code, report) = editor(dir.path());
    // DOEFF121 is only for files in the judgment layer (core). DOEFF122 to 125 are for all business files.
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
    // The default severity is warning (after Absent / Raise entered the main line — exit code 0). The explanation and the fix.
    assert!(report["violations"].as_array().unwrap().iter().filter(|v| v["rule"].as_str().unwrap().starts_with("DOEFF12")).all(|v| v["severity"] == "warning"));
    assert_eq!(code, 0);
    let rethrow = violation(&report, "app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said");
    assert!(rethrow["explanation"]["subject"].as_str().unwrap().contains("receives the failure type Refusal"), "{}", rethrow["explanation"]["subject"]);
    assert!(rethrow["hint"].as_str().unwrap().contains("(<- (Raise failure-value))"));
    let shape = violation(&report, "app/core/tag_judgment.hy::DOEFF121::decide_tag::subject");
    assert!(shape["hint"].as_str().unwrap().contains("defwire"));
    assert_eq!(shape["range"]["start"]["line"], 5);

    // Without a failure-type declaration DOEFF122 does not appear (it does not hard-code by name).
    let plain = "(defrecord Refusal \"Refusal\" #^ str reason #^ str detail)\n(defrecord TagsAccepted #^ tuple words)\n";
    let dir = smell_repo(&[("app/core/rules.hy", plain), ("app/core/tag_judgment.hy", DECIDE_TAG)], "", "");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF122").is_empty());
    // A defeffect :failure declaration also makes a failure type.
    let effect = "(import app.core.rules [Refusal TagsAccepted])\n(defeffect Check \"Check\" {:fields [x] :answer (| TagsAccepted Refusal) :failure [Refusal]})\n";
    let dir = smell_repo(&[("app/core/rules.hy", plain), ("app/core/effects.hy", effect), ("app/core/tag_judgment.hy", DECIDE_TAG)], "", "");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said"]);
    // If a type with the same name is in another module, only the declared one is a failure type (webapp's Refusal has no declaration).
    let webapp = DECIDE_TAG.replace("(import app.core.rules [Refusal TagsAccepted])", "(import app.webapp.model [Refusal TagsAccepted])");
    let dir = smell_repo(
        &[("app/core/rules.hy", rules), ("app/webapp/model.hy", plain), ("app/core/tag_judgment.hy", DECIDE_TAG), ("app/core/webapp_tag.hy", &webapp)],
        "",
        "",
    );
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/tag_judgment.hy::DOEFF122::decide_tag::add_said"]);

    // Ones listed in the registry are info, new ones are warning. Config severity can lower them to info.
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
    // An ID that is not a smell rule, and a severity of error, are config errors.
    for bad in ["[tool.doeff-linter.rules.DOEFF101]\nseverity = \"warning\"\n", "[tool.doeff-linter.rules.DOEFF121]\nseverity = \"error\"\n"] {
        let dir = smell_repo(&files, bad, "");
        let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
    }
    // An unknown layer name is also a config error.
    let dir = smell_repo(&files, "", "");
    let text = std::fs::read_to_string(dir.path().join("pyproject.toml")).unwrap().replace("shape_check_layers = [\"core\"]", "shape_check_layers = [\"ghost\"]");
    std::fs::write(dir.path().join("pyproject.toml"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("ghost"), "{}", stderr);
}

#[test]
fn smells_have_clean_counterparts_and_run_on_a_single_file() {
    // Judgment of a typed value, a failure emitted with Raise, a comprehension accumulator, a reused name — none of these hit.
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
    // A single-file run (an editor save) also reads the failure types from the repo declaration.
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/clean.hy"], Some(DECIDE_TAG));
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&report, "DOEFF122"), vec!["app/core/clean.hy::DOEFF122::decide_tag::add_said"]);
    assert_eq!(keys(&report, "DOEFF121"), vec!["app/core/clean.hy::DOEFF121::decide_tag::subject"]);
}

#[test]
fn only_the_assembly_layer_may_read_a_dependency_protocol() {
    // Rule: only in an assembling entry, the protocol of a dependency declared in :depends-on (a translation handler) may also be read.
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])
           (layer entry :roles [entry] :dependency-layers [intent protocol])])
(defservice automation "Automation" {:depends-on [messaging] :layers [core intent protocol entry]})
(defservice messaging "Mail" {:layers [core intent protocol]})
(defservice ledger "Ledger" {:layers [core intent protocol]})
"#,
    )
    .unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF116\"]\n").unwrap();
    let files = [
        ("app/messaging/protocol/reception.hy", tags("messaging", "protocol") + "(defn translate [] 1)\n"),
        ("app/messaging/intent/submit.hy", tags("messaging", "intent") + "(defclass Submit [])\n"),
        ("app/ledger/protocol/book.hy", tags("ledger", "protocol") + "(defn book [] 1)\n"),
        // entry reads the dependency's protocol and intent = passes.
        ("app/automation/entry/handlers.hy", tags("automation", "entry") + "(import app.messaging.protocol.reception [translate])\n(import app.messaging.intent.submit [Submit])\n(defn handlers [] 1)\n"),
        // core reads the dependency's protocol = violation (outside entry only intent).
        ("app/automation/core/plan.hy", tags("automation", "judgment") + "(import app.messaging.protocol.reception [translate])\n(defn plan [] 1)\n"),
        // entry reads the protocol of a service not in the declaration = violation.
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
    assert!(core["message"].as_str().unwrap().contains("the only layer-core reads allowed in a dependency are intent"), "{}", core["message"]);
    assert!(core["explanation"]["reason"].as_str().unwrap().contains("a layer that :dependency-layers in the layer of architecture.hy widens"), "{}", core["explanation"]["reason"]);
    // A nonexistent layer name is a config error.
    let text = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().replace(":dependency-layers [intent protocol]", ":dependency-layers [intent ghost]");
    std::fs::write(dir.path().join("architecture.hy"), text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2);
    assert!(stderr.contains("ghost in :dependency-layers of layer"), "{}", stderr);
}

#[test]
fn placed_layers_depend_only_on_placed_modules() {
    // Modules of the :placed-dependencies layers (service and shared) do not read modules outside the layer locations under root.
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
(defservice billing "Billing" {:layers [core intent entry]})
"#,
    )
    .unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF140\"]\n").unwrap();
    let files = [
        // Outside the locations: a module directly under a service dir, a module inside a dir not in the declaration, and a module outside the locations inside a package.
        ("app/billing/vocabulary.hy", "(setv WORD \"billing\")\n".to_string()),
        ("app/billing/model/row.hy", "(setv ROW 1)\n".to_string()),
        ("app/billing/views/__init__.py", "".to_string()),
        ("app/billing/intent/charge.hy", tags("billing", "intent") + "(defclass Charge [])\n"),
        ("app/foundation/store.hy", tags("io", "foundation") + "(defn store [] 1)\n"),
        ("elsewhere/lib.hy", "(setv LIB 1)\n".to_string()),
        // Violation: reading the same module twice is still one hit; a name import resolves to the owner module.
        (
            "app/billing/core/decide.hy",
            tags("billing", "judgment")
                + "(import app.billing.vocabulary [WORD])\n(import app.billing.vocabulary)\n(import app.billing.intent.charge [Charge])\n(import elsewhere.lib [LIB])\n(import json)\n(defn decide [] 1)\n",
        ),
        // Violation: the shared layer is the same as a service (reading a module inside a dir not in the declaration).
        ("app/shared/intent/names.hy", tags("shared", "intent") + "(import app.billing.model.row [ROW])\n(defclass Name [])\n"),
        // Passes: a package marker (a bundle of dirs before the location is decided) is not counted, and a layer not in :placed-dependencies (entry) is not checked.
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
    assert_eq!(decide["range"]["start"]["line"], 1, "shown at the first import");
    // A run with one given file also gives the same single hit (the judgment is decided only by the given file's imports and location).
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/billing/core/decide.hy"], None);
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(keys(&single, "DOEFF140"), vec!["app/billing/core/decide.hy::DOEFF140::app.billing.vocabulary"]);
    // A layer name not in the declaration, and the foundation layer, are config errors.
    for (bad, said) in [("[core ghost]", "ghost in :placed-dependencies is not in :layers"), ("[core foundation]", "foundation in :placed-dependencies is a :foundation layer")] {
        let text = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().replace(":placed-dependencies [core intent]", &format!(":placed-dependencies {}", bad));
        let broken = tempfile::TempDir::new().unwrap();
        std::fs::write(broken.path().join("architecture.hy"), text).unwrap();
        std::fs::write(broken.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF140\"]\n").unwrap();
        let (code, _, stderr) = run(broken.path(), &["--output-format", "editor-json", "--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
        assert!(stderr.contains(said), "{}", stderr);
    }
}

/// A bare defk call placed in a field whose declared type names Program in a defrecord of another file is not picked up (a field that carries a Program).
/// A bare call placed in a dict field of the same type is an error as before (fact: :prepare of start-records-server in doeff-records).
#[test]
fn defk_called_bare_into_a_program_field_of_a_record_is_not_an_answer() {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF126\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n",
    )
    .unwrap();
    let files = [
        (
            "app/core/serving.hy",
            "(defrecord Serving\n  \"Settings\"\n  (#^ (| Program EffectBase) prepare)\n  (#^ dict rows))\n(defk ready [h] {:tags {:context \"c\" :role \"program\"}} h)\n",
        ),
        (
            "app/core/shell.hy",
            concat!(
                "(import app.core.serving [Serving ready])\n",
                "(deff start [h] (Serving :prepare (ready h) :rows {}))  ; defk にできない: test shell\n",
                "(deff wrong [h] (Serving :prepare None :rows (ready h)))  ; defk にできない: test shell\n",
            ),
        ),
    ];
    for (rel, text) in &files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    let (code, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF126"), vec!["app/core/shell.hy::DOEFF126::wrong::ready"], "{}", report);
    assert_eq!(code, 1);
}

#[test]
fn defk_called_bare_is_an_error_and_program_positions_are_not() {
    // Fact (#798): latest-by-ref, converted to a defk, was being called bare by a deff and by tests — a Program flowed through as a value.
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
                "(deff text-at [ref] (.get (latest-by-ref ref) \"text\"))  ; defk にできない: test value\n",
                "(deff expected-inputs [ref] (tuple (latest-by-ref ref)))  ; defk にできない: test value\n",
                "(deff runner [ref] (run-on (latest-by-ref ref)))  ; defk にできない: takes a Program and runs it\n",
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
    // A new bare call is an error, and one listed in the registry is a warning. A bare call to a plain (non-defk) is not picked up.
    assert_eq!(violation(&report, "app/core/texts.hy::DOEFF126::text_at::latest_by_ref")["severity"], "error");
    assert_eq!(violation(&report, "app/core/texts.hy::DOEFF126::expected_inputs::latest_by_ref")["severity"], "warning");
    assert_eq!(code, 1);
    let bare = violation(&report, "app/core/texts.hy::DOEFF126::text_at::latest_by_ref");
    assert_eq!(bare["explanation"]["subject"], "definition text_at (deff) calls defk latest-by-ref bare");
    assert!(bare["hint"].as_str().unwrap().starts_with("Bind it with (<- x (f …))"));
    assert_eq!(bare["range"]["start"]["line"], 1);
    // A single-file run (an editor save) also knows the repo's defks.
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/texts.hy"], Some("(import app.core.reads [latest-by-ref])\n(deff fresh [r] (len (latest-by-ref r)))\n"));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF126"), vec!["app/core/texts.hy::DOEFF126::fresh::latest_by_ref"]);
}

#[test]
fn a_function_argument_called_bare_across_files_is_found_at_the_callee() {
    // Fact (#798, agora L1550): rows-by-text called its argument field-of bare, and the caller passed an fnk there — the index was always empty.
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
    // Only the bare call to the callee rows-by-text is a violation (rows-safe takes it with (<- …); callers passing an fn are not followed).
    assert_eq!(keys(&report, "DOEFF126"), vec!["app/core/index.hy::DOEFF126::rows_by_text::field_of"]);
    assert_eq!(code, 1);
    let found = violation(&report, "app/core/index.hy::DOEFF126::rows_by_text::field_of");
    assert!(found["explanation"]["subject"].as_str().unwrap().contains("caller app/core/tags.hy in tag_rows_of passes fnk there"), "{}", found["explanation"]["subject"]);
    assert_eq!(found["range"]["start"]["line"], 2);
}

#[test]
fn effects_disagreeing_with_inference_are_warnings_at_the_call_and_the_declaration() {
    // #849: drop the "mismatch between declaration and inference" label on the heading, and show it at the violating place (the call that fired, the name inside :effects).
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        "[tool.doeff-linter]\nenable = [\"DOEFF127\"]\n[tool.doeff-linter.definitions]\npaths = [\"app\"]\n",
    )
    .unwrap();
    let files = [
        (
            "app/intent/rows.hy",
            "(defrecord Row \"Row\" (#^ str id))\n(defeffect ReadRow \"Read\" {:fields [(: id str)] :answer Row :tags {:context \"c\" :role \"intent\"}})\n(defeffect PutRow \"Write\" {:fields [(: row Row)] :answer bool :tags {:context \"c\" :role \"intent\"}})\n",
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
    // quiet with no declaration, fetch that matches, and waits where the outside effect Delay matches do not appear.
    assert_eq!(keys(&report, "DOEFF127"), vec!["app/core/flow.hy::DOEFF127::store::Delay", "app/core/flow.hy::DOEFF127::store::ReadRow"]);
    let undeclared = violation(&report, "app/core/flow.hy::DOEFF127::store::ReadRow");
    assert_eq!(undeclared["severity"], "warning");
    assert_eq!(undeclared["range"]["start"]["line"], 3);
    assert!(undeclared["message"].as_str().unwrap().contains("goes through fetch and raises effect ReadRow, which is not in :effects"), "{}", undeclared);
    assert_eq!(undeclared["explanation"]["subject"], "defk store raises effect ReadRow via fetch");
    let unused = violation(&report, "app/core/flow.hy::DOEFF127::store::Delay");
    assert!(unused["message"].as_str().unwrap().contains("lists Delay in :effects but does not raise it"));
}

#[test]
fn an_unreadable_hy_file_is_reported_to_the_editor_and_the_hook() {
    // A violation of an unreadable file is not silently emptied — it appears as a DOEFF128 error regardless of the list of enabled rules (here only DOEFF104).
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
    assert!(broken["message"].as_str().unwrap().contains("cannot read"), "{}", broken["message"]);
    assert_eq!(code, 1);
    // The same in a pre-save single-file run.
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/core/broken.hy"], Some(files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap();
    assert_eq!(keys(&single, "DOEFF128"), vec!["app/core/broken.hy::DOEFF128"]);
    // It also appears in the agent hook notice.
    let hook_input = format!("{{\"workspace_roots\": [\"{}\"]}}", dir.path().display());
    let (_, stdout, stderr) = run(dir.path(), &["--hook", "--no-log"], Some(&hook_input));
    assert!(stdout.contains("DOEFF128"), "{}\n{}", stdout, stderr);
}

/// Modules under the :verification-environment dir (a simulation environment — a location that is not a service) do not become DOEFF114 or 115.
/// Only one is accepted, and the judgment of other locations does not change. The same name as a service is a config error.
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

    // The same name as a service dir is a config error (the location judgment would read two ways).
    let dir = architecture_repo(&files, "");
    let path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&path).unwrap().replace(":foundation foundation)", ":foundation foundation\n  :verification-environment \"billing\")");
    std::fs::write(&path, text).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains(":verification-environment billing cannot have the same name as a declared service's dir"), "{}", stderr);
}

/// The DOEFF130 repo — two services with layers core, intent, and protocol (kanban depends on shared), and pyproject.toml (`toml_extra` appended at the end).
fn translation_repo(files: &[(&str, &str)], toml_extra: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(
        dir.path().join("architecture.hy"),
        r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment program type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])])
(defservice kanban "Board" {:depends-on [shared] :layers [core intent protocol]})
(defservice shared "Shared" {:layers [core]})
"#,
    )
    .unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        format!(
            "[tool.doeff-linter]\nenable = [\"DOEFF130\"]\n\n[[tool.doeff-linter.laws]]\nname = \"translation-targets-only-generic-foundation-effects\"\nadr = \"ADR-TEST\"\nrules = [\"DOEFF130\"]\nlayers = [\"protocol\"]\nstatement = \"protocol handlers emit only doeff's generic effects\"\n{}",
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

/// The DOEFF130 file set — two business intents (a defclass and a defeffect), a shared core judgment (one that emits an intent, one that forms a cycle), and a kanban protocol handler.
const TRANSLATION_FILES: [(&str, &str); 4] = [
    (
        "app/kanban/intent/board.hy",
        "(defclass ReadBoard [])\n(defeffect CreateCard \"Creates a card\" {:fields [(: title str)]})\n",
    ),
    (
        // The counterexample shape handed over by #955: a shared core function emits ReadBoard (an intent), and a protocol handler imports and calls it.
        "app/shared/core/board_view.hy",
        "(import app.kanban.intent.board [ReadBoard])\n(defk view [c] (<- b (ReadBoard)) b)\n(defk deep [c] (<- v (view c)) v)\n(defk ping [n] (<- x (pong n)) x)\n(defk pong [n] (<- x (ping n)) x)\n",
    ),
    (
        // A business flow (a layer-core program) emitting an intent is correct — it is outside protocol, so it does not hit.
        "app/kanban/core/flow.hy",
        "(import app.kanban.intent.board [CreateCard])\n(defk open-card [t] (<- c (CreateCard t)) c)\n",
    ),
    (
        "app/kanban/protocol/reads.hy",
        r#"(import doeff_http [HttpRequest])
(import doeff_records.effects [PutRow])
(import app.kanban.intent.board [CreateCard ReadBoard])
(import app.shared.core.board_view [view deep ping])

;; Positive example: only re-emits the received intent into doeff's generic effects (HttpRequest, record writes).
(defk put-card [t] (<- r (PutRow "cards" t)) r)
(defhandler http-reads
  (ReadBoard []
    (<- r (HttpRequest "https://example.invalid/board"))
    (resume r))
  (CreateCard [t]
    (<- r (put-card t))
    (resume r)))

;; Counterexample: emits ReadBoard via an imported shared core function (invisible from the names in the body alone).
(defhandler board-reads
  (Lookup [c]
    (<- b (view c))
    (resume b)))

;; Counterexample: a two-step route.
(defhandler deep-reads
  (Deep [c]
    (<- b (deep c))
    (resume b)))

;; Counterexample: emits an intent directly in the body.
(defhandler card-writes
  (NewCard [t]
    (<- x (CreateCard t))
    (resume x)))

;; Counterexample: a handler of a function that takes [effect k].
(defk raw-handler [effect k]
  (<- (CreateCard "x"))
  (k effect))

;; It also stops at a cyclic call (ping <-> pong), and does not hit if the intent is not reached.
(defhandler loops
  (Spin [n]
    (<- x (ping n))
    (resume x)))
"#,
    ),
];

#[test]
fn a_translation_handler_that_emits_a_business_intent_is_an_error() {
    // A layer-protocol handler emits only doeff's generic effects, and does not emit layer-intent types (business intents).
    // Inference follows through imported defks — closing a shape that slips past a check on names in the body alone (DOEFF201, check_business_fakes.hy).
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
    // Location = in the handler body, the first call that leads to the intent (view in (view c) — line 18, 0-based).
    assert_eq!(via["range"]["start"]["line"], 18);
    assert!(via["message"].as_str().unwrap().contains("handler board-reads (layer protocol) goes through view and emits, from layer intent, the business intent ReadBoard"), "{}", via["message"]);
    assert_eq!(via["explanation"]["subject"], "handler board-reads (layer protocol) emits intent ReadBoard of layer intent via view");
    assert_eq!(via["explanation"]["law_statement"], "protocol handlers emit only doeff's generic effects");
    let deep = violation(&report, "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::deep_reads::app.kanban.intent.board.ReadBoard");
    assert!(deep["message"].as_str().unwrap().contains("goes through deep → view and emits"), "{}", deep["message"]);
    let direct = violation(&report, "app/kanban/protocol/reads.hy::translation-targets-only-generic-foundation-effects::card_writes::app.kanban.intent.board.CreateCard");
    assert!(direct["message"].as_str().unwrap().contains("handler card-writes (layer protocol) emits, from layer intent, the business intent CreateCard"), "{}", direct["message"]);

    // A pre-save single-file run (editor) also gives the same 4 hits.
    let source = TRANSLATION_FILES[3].1;
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/kanban/protocol/reads.hy"], Some(source));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF130"), keys(&report, "DOEFF130"));
}

#[test]
fn a_translation_handler_may_emit_another_services_public_intent() {
    // Decision (aligned with DOEFF156's "other services' public effects"): what is stopped is only the intent of the same service as the handler.
    // Emitting another service's intent (a public contract) is the translation's job — e.g. a durable translation emits the mail intake intent (#1508).
    let files = [
        ("app/kanban/intent/board.hy", "(defclass ReadBoard [])\n"),
        ("app/orders/intent/orders.hy", "(defclass PlaceOrder [])\n"),
        (
            "app/kanban/protocol/reads.hy",
            r#"(import app.kanban.intent.board [ReadBoard])
(import app.orders.intent.orders [PlaceOrder])

;; Positive example: emits another service's (orders) public intent.
(defhandler order-bridge
  (Bridge [x]
    (<- r (PlaceOrder))
    (resume r)))

;; Counterexample: emits its own service's (kanban) intent — an error as before.
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
(defservice kanban "Board" {:depends-on [orders] :layers [core intent protocol]})
(defservice orders "Orders" {:layers [core intent protocol]})
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
    // The limit on the steps followed is a setting — with 0, only the intent emitted directly in the handler body.
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
    // If a layer name written in the section does not exist, it is a config error (so it does not silently stop hitting).
    let dir = translation_repo(&TRANSLATION_FILES, "\n[tool.doeff-linter.translation_effects]\nhandler_layers = [\"ghost\"]\n");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("translation_effects.handler_layers: layer ghost is not among the declared layers"), "{}", stderr);
}

/// A repo that writes the allow-list (:world-handlers) (layers core, foundation, entry, and service billing).
fn world_repo(files: &[(&str, String)]) -> tempfile::TempDir {
    world_repo_with(files, "", "[\"DOEFF106\", \"DOEFF131\"]")
}

/// A world_repo with elements added to the list (`extra` is the element appended at the end of the :world-handlers list; `enable` is the list of rules).
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
                     :wraps ["doeff_core_effects._os_file:os-file-handler"])EXTRA])
(defservice billing "Billing" {:layers [core entry]})
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

/// R1: in a repo that writes an allow-list, raw I/O is allowed only in the modules of the list's definitions (not allowed by layer — entry and
/// foundation modules not in the list also hit), and only the list's definitions may name doeff's real-I/O handlers in :wraps (DOEFF131).
#[test]
fn world_handler_list_limits_raw_io_and_wrapped_handlers() {
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation")
                + "(import httpx)\n(import doeff_core_effects._os_file [os-file-handler])\n(import doeff_core_effects._file_effects [ReadText])\n\
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
                + "(import httpx)\n(import doeff_core_effects._os_file [os-file-handler])\n\
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
        "the list's module (host), and httpx and urllib.parse (unreachable) used only as exception types, do not hit: {}",
        report
    );
    assert!(violation(&report, "app/billing/entry/main.hy::DOEFF106::fetch::httpx.get")["message"].as_str().unwrap().contains(":world-handlers"));
    let named = keys(&report, "DOEFF131");
    assert_eq!(
        named,
        vec![
            "app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects._os_file:os-file-handler",
            "app/foundation/host.hy::DOEFF131::token_in_file::world::doeff_core_effects._os_file:os-file-handler",
        ],
        "inside the list's definition with-host does not hit; a definition not in the list in the same module, and entry, do hit: {}",
        report
    );
    let found = violation(&report, "app/foundation/host.hy::DOEFF131::token_in_file::world::doeff_core_effects._os_file:os-file-handler");
    assert_eq!(found["severity"], "error");
    assert_eq!(found["level"], "critical");
}

/// The route-following of DOEFF107 does not enter the allow-list (:world-handlers) definitions. The list's with-host and a make-client
/// not in the list reach the same raw side effect (httpx.AsyncClient) — with-host calls make-client inside. `entry` is the body of the entry module.
fn via_repo(entry: &str) -> tempfile::TempDir {
    let files = [
        ("app/foundation/client.hy", tags("shared", "foundation") + "(import httpx)\n(defk make-client [] (httpx.AsyncClient))\n"),
        (
            "app/foundation/host.hy",
            tags("shared", "foundation") + "(import app.foundation.client [make-client])\n(defk with-host [body] (make-client) body)\n",
        ),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry") + "(import app.foundation.host [with-host])\n(import app.foundation.client [make-client])\n" + entry,
        ),
    ];
    world_repo_with(&files, "", "[\"DOEFF107\"]")
}

/// The DOEFF107 key that hit definition `name` of the entry module.
fn via_keys_of(report: &Value, name: &str) -> Vec<String> {
    keys(report, "DOEFF107").into_iter().filter(|k| k.starts_with(&format!("app/billing/entry/main.hy::DOEFF107::{}::", name))).collect()
}

#[test]
fn via_through_a_declared_world_handler_is_not_reported() {
    let dir = via_repo("(defk run [body] (with-host body))\n");
    let (_, report) = editor(dir.path());
    assert!(via_keys_of(&report, "run").is_empty(), "beyond the list's with-host is with-host's responsibility: {}", report);
}

#[test]
fn via_through_an_undeclared_definition_is_reported() {
    let dir = via_repo("(defk run [] (make-client))\n");
    let (_, report) = editor(dir.path());
    let found = via_keys_of(&report, "run");
    assert_eq!(found.len(), 1, "a route through make-client, which is not in the list, hits: {}", report);
    assert!(found[0].contains("make-client") && found[0].ends_with("httpx.AsyncClient"), "{:?}", found);
}

#[test]
fn via_is_reported_when_an_undeclared_path_exists_beside_a_declared_one() {
    // It calls the list's with-host first, so following without stopping would let the with-host>make-client route take the same evidence first — even after stopping,
    // it hits via the make-client route that does not go through the list
    let dir = via_repo("(defk run [body] (with-host body) (make-client))\n");
    let (_, report) = editor(dir.path());
    let found = via_keys_of(&report, "run");
    assert_eq!(found.len(), 1, "a route that does not go through the list hits: {}", report);
    assert!(!found[0].contains("with-host"), "the route does not include the list's definition: {:?}", found);
}

/// R2: the allow-list definitions exist and are in a module of layer foundation (DOEFF132 — the position is the list element in architecture.hy).
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
        "an existing foundation with-host does not hit: {}",
        report
    );
    let misplaced = violation(&report, "architecture.hy::DOEFF132::app.billing.entry.main:run");
    assert!(misplaced["message"].as_str().unwrap().contains("is in layer entry"), "{}", misplaced);
    assert_eq!(misplaced["level"], "critical");
    assert!(violation(&report, "architecture.hy::DOEFF132::app.foundation.host:gone")["message"].as_str().unwrap().contains("has no definition gone"));
    let line = std::fs::read_to_string(dir.path().join("architecture.hy")).unwrap().lines().position(|l| l.contains("app.foundation.host:gone")).unwrap();
    assert_eq!(violation(&report, "architecture.hy::DOEFF132::app.foundation.host:gone")["range"]["start"]["line"], line);
}

/// R3: the kind of test is derived from where it reaches — a deftest that reaches a list definition, a :wraps handler, or raw I/O (following definitions)
/// needs an :edge-mark at the edge, and a deftest that does not reach has no mark locally. A mismatch is DOEFF133 (critical).
#[test]
fn test_kind_is_derived_from_what_the_test_reaches() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/calc.hy", tags("billing", "judgment") + "(defk add [a b] (+ a b))\n"),
        ("app/billing/core/helpers.hy", tags("billing", "judgment") + "(import app.foundation.host [with-host])\n(defk hosted [body] (with-host body))\n"),
        (
            "app/billing/tests/test_kinds.hy",
            "(import pytest)\n(import subprocess)\n(import doeff_core_effects._os_file [os-file-handler])\n\
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
        // :marks of a deftest nested inside a defadr list [..] also count as a mark (does not hit).
        (
            "app/billing/tests/test_nested_adr.hy",
            "(import app.billing.core.helpers [hosted])\n\
             (defadr adr-nested :title \"nested\" :tests [(deftest test-edge-in-a-list {:marks [\"real_world\"]} (<- n (hosted 1)) (assert n))])\n"
                .to_string(),
        ),
        // An edge definition that is only passed by value to a read-only receiver (:static-readers) is not executed, so it does not reach (local — does not hit).
        ("app/billing/core/closure.hy", tags("billing", "judgment") + "(defk read-closure [case] (str case))\n"),
        (
            "app/billing/tests/test_closure.hy",
            "(import app.billing.core.closure [read-closure])\n(import app.billing.core.helpers [hosted])\n\
             (deftest test-local-reads-a-foundation (<- text (read-closure hosted)) (assert text))\n"
                .to_string(),
        ),
        // A mark spelling that appears in a comment or in a test's string value is not the module's mark (a local test stays unmarked — does not hit).
        (
            "app/billing/tests/test_mark_in_text.hy",
            "(import app.billing.core.calc [add])\n;; explanation of the mark spelling: pytestmark = pytest.mark.real_world\n\
             (deftest test-local-reads-spellings\n  (<- n (add 1 2))\n  (assert (in \"pytest\" \"(val pytestmark pytest.mark.real-world)\")))\n\
             (deftest test-local-reads-a-marks-spelling\n  (assert (in \"marks\" \"(deftest test-x {:marks [\\\"real_world\\\"]} 1)\")))\n\
             (deftest test-local-with-fixture-and-doc-marked [tmp-path] \"description\" {:marks [\"real_world\"]} (<- n (add 1 2)) (assert n))\n"
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
            // :marks in the settings after the fixture list and the docstring also count as a mark (read the same as doeff-hy's deftest).
            "app/billing/tests/test_mark_in_text.hy::DOEFF133::test_local_with_fixture_and_doc_marked::local",
        ],
        "edges with a mark (:marks and a module pytestmark) and local tests without a mark do not hit; :marks inside a string is not a mark: {}",
        report
    );
    let two_steps = violation(&report, "app/billing/tests/test_kinds.hy::DOEFF133::test_edge_two_steps::edge");
    assert!(two_steps["message"].as_str().unwrap().contains("hosted → with-host → app.foundation.host:with-host"), "{}", two_steps["message"]);
    assert_eq!(two_steps["level"], "critical");
}

/// A reference that only checks an edge definition as a value — direct operands of comparison forms (is, is-not, =, !=, in, not-in) and assert (including the message shown on
/// failure) (including inside literal lists and tuples, and dotted attribute reads of the operand) — does not reach (local, does not hit). A reference that calls, passes to another definition,
/// compares the result of a call inside a comparison, or reads an attribute outside a comparison reaches as before (it is an edge with no mark, so it hits — so as not to break the shape of DOEFF137 where an interpreter constant names a handler
/// with `f.__module__`).
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
        "operands of comparisons and dunder attribute reads do not reach; references passed to another definition and calls inside a comparison do: {}",
        report
    );
}

/// A defrecord field name is a declared name, not a reference to an import (function) of the same name — a test that merely uses a record with a field named like a function that reaches the outside world
/// does not reach (no false red). A test that really calls that function reaches as before.
#[test]
fn a_defrecord_field_named_like_a_world_function_does_not_reach_it() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/board.hy", tags("billing", "judgment") + "(import app.foundation.host [with-host])\n(defk old-serving [] (with-host 1))\n"),
        (
            "app/billing/core/obs.hy",
            tags("billing", "judgment")
                + "(import app.billing.core.board [old-serving])\n(defrecord Obs (#^ int old-serving))\n(defk make-obs [] (Obs 1))\n(defk call-it [] (old-serving))\n",
        ),
        (
            "app/billing/tests/test_field.hy",
            "(import app.billing.core.obs [make-obs call-it])\n\
             (deftest test-local-builds-the-record (assert (make-obs)))\n\
             (deftest test-edge-calls-the-function (assert (call-it)))\n"
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
        vec!["app/billing/tests/test_field.hy::DOEFF133::test_edge_calls_the_function::edge"],
        "a field name does not reach; only a test that calls the function does: {}",
        report
    );
}

/// A Python function (`.py`) in the repo that a deftest names (through a Hy definition) is followed to child processes inside it
/// (`subprocess.run`, `os.system`) and to Python callees decided by name (functions in the same module, imported functions, class construction and
/// `self.m`) — if it reaches, it is an edge (a mark is needed). Names in type annotations and except types are not execution of values (they do not reach). Calls from a Python function
/// back to a Hy definition are also followed. A Python module with broken syntax is not silently made local; it is named in the report's errors.
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
        "an unmarked test reaching a Python function's child process hits as an edge; marked ones, and functions with only annotations and except types, do not hit: {}",
        report
    );
    let launch = violation(&report, "app/billing/tests/test_python.hy::DOEFF133::test_edge_launch_unmarked::edge");
    assert!(launch["message"].as_str().unwrap().contains("tools.bake.launch → tools.inner.spawn → subprocess.run"), "{}", launch["message"]);
    let helper = violation(&report, "app/billing/tests/test_python.hy::DOEFF133::test_edge_through_a_hy_helper::edge");
    assert!(helper["message"].as_str().unwrap().contains("bake-all → tools.bake.launch"), "{}", helper["message"]);
    let errors = report["errors"].as_array().unwrap();
    assert!(errors.iter().any(|e| e.as_str().unwrap().contains("tools/broken.py")), "the module with broken syntax is named: {}", report);
}

/// A contract test runs the same deftest under both assemblies via the name constants (string constants outside the file) of `:interpreters [real stand-in]`. It resolves the imported name constant
/// to a definition, and adds the assembly that the module's name -> assembly table assigns to that name to what the deftest reaches — even if the body calls only local definitions,
/// it is an edge (a mark is needed) if only the real assembly reaches the outside world. A deftest that runs only the stand-in stays local (25 contract tests in agora-controllers).
#[test]
fn test_kind_follows_the_interpreters_to_their_assemblies() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/calc.hy", tags("billing", "judgment") + "(defk add [a b] (+ a b))\n"),
        ("app/billing/core/stand.hy", tags("billing", "judgment") + "(defk with-stand [body] body)\n"),
        (
            "app/billing/tests/contract_handlers.hy",
            "(import app.foundation.host [with-host])\n(import app.billing.core.stand [with-stand])\n\
             (val REAL \"app.foundation.host:with-host@live\")\n(val STAND \"app.billing.core.stand:with-stand\")\n\
             (defk under-real [program] (with-host program))\n(defk under-stand [program] (with-stand program))\n\
             (val INTERPRETERS {REAL under-real STAND under-stand})\n"
                .to_string(),
        ),
        (
            "app/billing/tests/test_contract.hy",
            "(import app.billing.tests.contract_handlers [REAL STAND])\n(import app.billing.core.calc [add])\n\
             (deftest test-contract-marked {:interpreters [REAL STAND] :marks [\"real_world\"]} (<- n (add 1 2)) (assert n))\n\
             (deftest test-contract-unmarked {:interpreters [REAL STAND]} (<- n (add 1 2)) (assert n))\n\
             (deftest test-stand-only-marked {:interpreters [STAND] :marks [\"real_world\"]} (<- n (add 1 2)) (assert n))\n\
             (deftest test-stand-only-good {:interpreters [STAND]} (<- n (add 1 2)) (assert n))\n"
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
            "app/billing/tests/test_contract.hy::DOEFF133::test_contract_unmarked::edge",
            "app/billing/tests/test_contract.hy::DOEFF133::test_stand_only_marked::local",
        ],
        "a contract test reaching the real assembly is an edge (a marked one does not hit), and a test running only the stand-in is local: {}",
        report
    );
    let unmarked = violation(&report, "app/billing/tests/test_contract.hy::DOEFF133::test_contract_unmarked::edge");
    assert!(unmarked["message"].as_str().unwrap().contains("under-real → with-host → app.foundation.host:with-host"), "{}", unmarked["message"]);
}

/// Each allow-list handler needs an edge test (a deftest with a non-empty :interpreters that reaches the handler's definition in the same graph as DOEFF133)
/// — if missing, DOEFF137 (critical, detail = the list spelling) at the list element in architecture.hy. Elements of :interpreters stay
/// symbols of constants outside the file (not resolved). A handler with a :contract-test (none …) with a reason is not judged; a none without a reason fires (#1796).
#[test]
fn world_handler_needs_a_contract_test_that_runs_interpreters() {
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation")
                + "(defk with-host [body] body)\n(defk with-clock [body] body)\n(defk with-queue [body] body)\n(defk with-exempt [body] body)\n",
        ),
        // A constant of an interpreter assembly (a symbol outside the file) names the handler — the deftest just lists this constant in :interpreters.
        (
            "app/foundation/tests/contract_handlers.hy",
            "(import app.foundation.host [with-host])\n\
             (val HOST (+ with-host.__module__ \":\" with-host.__name__))\n(val SIM \"sim-host\")\n"
                .to_string(),
        ),
        (
            "app/foundation/tests/test_host_contract.hy",
            "(import app.foundation.tests.contract_handlers [HOST SIM])\n\
             (deftest test-host-answers\n  \"the real one and the simulation pass the same check\"\n  {:interpreters [HOST SIM] :marks [\"real_world\"]}\n  (assert True))\n"
                .to_string(),
        ),
        // A deftest without :interpreters that merely reaches, and an empty :interpreters, do not count as an edge test.
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
        "with-host reached by an edge test, and with-exempt with a reasoned :contract-test (none …), do not hit: {}",
        report
    );
    let clock = violation(&report, "architecture.hy::DOEFF137::app.foundation.host:with-clock");
    assert_eq!(clock["level"], "critical");
    assert_eq!(clock["severity"], "error");
    let line = text.lines().position(|l| l.contains("app.foundation.host:with-clock")).unwrap();
    assert_eq!(clock["range"]["start"]["line"], line);

    // A bare symbol none without a reason fires — it hits even when written on with-host, which an edge test reaches (#1796).
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
        "with-exempt and with-clock with a none without a reason hit: {}",
        report
    );
    let exempt = violation(&report, "architecture.hy::DOEFF137::app.foundation.host:with-exempt");
    assert!(exempt["message"].as_str().unwrap().contains("has no reason in :contract-test none"), "{}", exempt);

    // The value of :contract-test is only a reasoned (none …) or the symbol none — other values are declaration errors (exit code 2).
    let bad = bare.replace("with-exempt\" :touches [file] :contract-test none", "with-exempt\" :touches [file] :contract-test (none :doeff-test \"\")");
    assert_ne!(bad, bare);
    std::fs::write(&arch_path, bad).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("world-handler app.foundation.host:with-exempt :contract-test must be (none"), "{}", stderr);
}

/// Extension of DOEFF137 (the stand-in list :stand-ins): for each pair (stand-in, real), a deftest with a non-empty :interpreters that reaches both the stand-in and the real one
/// (a contract test — the verified fake / Pact shape) is needed. The real one may be outside the index (a package such as doeff), in which case it is followed from the definition naming the real one.
/// A constant that spells an interpreter as a name string (`"module:name"` / `"module:name@<variant>"`) also counts as a definition naming that handler. A test with :interpreters that reaches only one side, or one that reaches both but has no :interpreters, does not count. A reasoned :contract-test (none …)
/// pair is not judged; a none without a reason fires. The hit position is the list element, the detail is "stand-in->real", and the severity is critical.
#[test]
fn stand_in_needs_a_contract_test_that_runs_it_beside_the_real_handler() {
    let files = [
        (
            "app/foundation/host.hy",
            tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-mail [body] body)\n(defk with-queue [body] body)\n(defk with-clock [body] body)\n",
        ),
        (
            "app/foundation/peers.hy",
            tags("shared", "foundation")
                + "(defk with-fake-mail [body] body)\n(defk with-fake-llm [body] body)\n(defk with-fake-queue [body] body)\n\
                   (defk with-fake-clock [body] body)\n(defk with-fake-disk [body] body)\n(defk with-fake-bare [body] body)\n\
                   (defk with-fake-store [body] body)\n",
        ),
        // A constant of an interpreter assembly (a symbol outside the file) names the handler — the real LLM handler is in a package outside the index.
        (
            "app/foundation/tests/contract_handlers.hy",
            "(import app.foundation.host [with-host with-mail with-queue])\n\
             (import app.foundation.peers [with-fake-mail with-fake-llm with-fake-queue])\n\
             (import doeff_openai.live [with-live])\n\
             (val HOST (+ with-host.__module__ \":\" with-host.__name__))\n(val SIM \"sim-host\")\n\
             (val MAIL (+ with-mail.__module__ \":\" with-mail.__name__))\n(val FAKE-MAIL (+ with-fake-mail.__module__ \":\" with-fake-mail.__name__))\n\
             (val LIVE (+ with-live.__module__ \":\" with-live.__name__))\n(val FAKE-LIVE (+ with-fake-llm.__module__ \":\" with-fake-llm.__name__))\n\
             (val FAKE-QUEUE (+ with-fake-queue.__module__ \":\" with-fake-queue.__name__))\n\
             (val STORE \"doeff_records.store:with-store@postgres\")\n(val FAKE-STORE \"app.foundation.peers:with-fake-store\")\n"
                .to_string(),
        ),
        // Good example: a test listing the real one and the stand-in in :interpreters (a pair whose real one is in the index, and a pair whose real one is outside the index).
        (
            "app/foundation/tests/test_peer_contracts.hy",
            "(import app.foundation.tests.contract_handlers [HOST SIM MAIL FAKE-MAIL LIVE FAKE-LIVE FAKE-QUEUE STORE FAKE-STORE])\n\
             (deftest test-host-answers {:interpreters [HOST SIM] :marks [\"real_world\"]} (assert True))\n\
             (deftest test-mail-answers-alike {:interpreters [MAIL FAKE-MAIL] :marks [\"real_world\"]} (assert True))\n\
             (deftest test-live-answers-alike {:interpreters [LIVE FAKE-LIVE] :marks [\"real_world\"]} (assert True))\n\
             (deftest test-queue-fake-alone {:interpreters [FAKE-QUEUE SIM]} (assert True))\n\
             (deftest test-store-answers-alike {:interpreters [STORE FAKE-STORE] :marks [\"real_world\"]} (assert True))\n"
                .to_string(),
        ),
        // Bad example: reaches both the clock's stand-in and the real one, but has no :interpreters.
        (
            "app/foundation/tests/test_clock_plain.hy",
            "(import app.foundation.host [with-clock])\n(import app.foundation.peers [with-fake-clock])\n\
             (deftest test-clock-both {:marks [\"real_world\"]} (<- a (with-clock 1)) (<- b (with-fake-clock 1)) (assert (= a b)))\n"
                .to_string(),
        ),
    ];
    let extra = r#"]
  :edge-mark "real_world"
  :stand-ins [(stand-in "app.foundation.peers:with-fake-mail" :real "app.foundation.host:with-mail")
              (stand-in "app.foundation.peers:with-fake-llm" :real "doeff_openai.live:with-live")
              (stand-in "app.foundation.peers:with-fake-store" :real "doeff_records.store:with-store")
              (stand-in "app.foundation.peers:with-fake-queue" :real "app.foundation.host:with-queue")
              (stand-in "app.foundation.peers:with-fake-clock" :real "app.foundation.host:with-clock")
              (stand-in "app.foundation.peers:with-fake-disk" :real "doeff_core_effects._os_file:os-file-handler"
                :contract-test (none :doeff-test "packages/doeff-core-effects/tests/test_os_file.hy::test-os-file-contract"))
              (stand-in "app.foundation.peers:with-ghost" :real "doeff_openai.live:with-live")"#;
    let dir = world_repo_with(&files, extra, "[\"DOEFF137\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF137"),
        vec![
            "architecture.hy::DOEFF137::app.foundation.peers:with-fake-clock->app.foundation.host:with-clock",
            "architecture.hy::DOEFF137::app.foundation.peers:with-fake-queue->app.foundation.host:with-queue",
            "architecture.hy::DOEFF137::app.foundation.peers:with-ghost->doeff_openai.live:with-live",
        ],
        "mail and live (real one outside the index), store (interpreters spelled as name strings — the real one with a @variant), and the reasoned disk, do not hit; queue that does not reach the real one, clock without :interpreters, and ghost with no definition hit: {}",
        report
    );
    let clock = violation(&report, "architecture.hy::DOEFF137::app.foundation.peers:with-fake-clock->app.foundation.host:with-clock");
    assert_eq!(clock["level"], "critical");
    assert_eq!(clock["severity"], "error");
    assert!(clock["message"].as_str().unwrap().contains("of the same contract as real app.foundation.host:with-clock"), "{}", clock);
    let line = text.lines().position(|l| l.contains("app.foundation.peers:with-fake-clock")).unwrap();
    assert_eq!(clock["range"]["start"]["line"], line);
    let ghost = violation(&report, "architecture.hy::DOEFF137::app.foundation.peers:with-ghost->doeff_openai.live:with-live");
    assert!(ghost["message"].as_str().unwrap().contains("the stand-in's definition is not in the index"), "{}", ghost);

    // A bare symbol none without a reason fires — it hits even when written on mail, which a contract test reaches.
    let bare = text.replace(":real \"app.foundation.host:with-mail\")", ":real \"app.foundation.host:with-mail\" :contract-test none)");
    assert_ne!(bare, text);
    std::fs::write(&arch_path, &bare).unwrap();
    let (_, report) = editor(dir.path());
    let mail = violation(&report, "architecture.hy::DOEFF137::app.foundation.peers:with-fake-mail->app.foundation.host:with-mail");
    assert!(mail["message"].as_str().unwrap().contains("has no reason in :contract-test none"), "{}", mail);

    // A malformed list (a pair without :real) is a declaration error (exit code 2).
    let bad = text.replace("(stand-in \"app.foundation.peers:with-ghost\" :real \"doeff_openai.live:with-live\")", "(stand-in \"app.foundation.peers:with-ghost\")");
    assert_ne!(bad, text);
    std::fs::write(&arch_path, bad).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("stand-in app.foundation.peers:with-ghost has no :real"), "{}", stderr);
}

/// #1564: a property test using a seeded pseudo-random number is local; an operation using OS entropy stays an edge.
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

/// Real I/O handlers are known from doeff's inventory (data/world_handlers.json) — :wraps accepts only those in the inventory (outside is a config error),
/// a handler in the inventory but not in :wraps (subprocess-handler) named outside the list is also DOEFF131, and removing a file from :edge-touches makes a test that reaches
/// only the file local (decision B).
#[test]
fn world_catalog_widens_the_rules_and_edge_touches_narrow_the_edge() {
    // If :wraps is outside the inventory, it is a config error (exit code 2).
    let outside = r#"
                   (world-handler "app.foundation.host:with-other" :touches [http] :wraps ["somewhere.else:mystery-handler"])"#;
    let dir = world_repo_with(&[("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-other [body] body)\n")], outside, "[\"DOEFF131\"]");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("somewhere.else:mystery-handler is not in the catalog of real I/O handlers"), "{}", stderr);

    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry") + "(import doeff_core_effects._os_process [subprocess-handler])\n(defk run [body] (with-handlers [subprocess-handler] body))\n",
        ),
        (
            "app/billing/tests/test_io.hy",
            "(import doeff_core_effects._os_file [os-file-handler])\n(import doeff_core_effects._os_process [subprocess-handler])\n\
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
    assert!(named.contains(&"app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects._os_process:subprocess-handler".to_string()), "{:?}", named);
    let found = violation(&report, "app/billing/entry/main.hy::DOEFF131::run::world::doeff_core_effects._os_process:subprocess-handler");
    assert!(found["message"].as_str().unwrap().contains("no definition in the roster lists this handler in :wraps"), "{}", found["message"]);
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec!["app/billing/tests/test_io.hy::DOEFF133::test_files_marked::local", "app/billing/tests/test_io.hy::DOEFF133::test_process::edge"],
        "a test that reaches only a file is local (unmarked does not hit), and process is an edge: {}",
        report
    );
}

/// The catalog of real I/O handlers of a package outside doeff is kept by the package's repo and named by the using repo in `[tool.doeff-linter] world_catalogs`
/// (from the config file's dir): its rows may be written in :wraps and widen DOEFF131 like the bundled rows. Without it the same :wraps is a config error that names
/// world_catalogs; a file that does not exist is read as no rows, and the :wraps that needed it is the config error that names the file; `--world-catalog` replaces the config's list (the commit hook and the merge gate use it for trees placed elsewhere).
#[test]
fn a_repo_catalog_adds_handlers_of_packages_outside_doeff() {
    let catalog = r#"{"handlers": [{"handler": "pkg_agents:agent-handler", "touches": ["process"], "why": "starts the agent as a child process"}]}"#;
    let extra = r#"
                   (world-handler "app.foundation.host:with-agents" :touches [process] :wraps ["pkg_agents:agent-handler"])"#;
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-agents [body] body)\n"),
        ("app/billing/entry/main.hy", tags("billing", "entry") + "(import pkg_agents [agent-handler])\n(defk run [body] (with-handlers [agent-handler] body))\n"),
        ("catalogs/agents.json", catalog.to_string()),
    ];
    let dir = world_repo_with(&files, extra, "[\"DOEFF131\"]");
    let root = dir.path();
    let (code, _, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("pkg_agents:agent-handler is not in the catalog of real I/O handlers"), "{}", stderr);
    assert!(stderr.contains("world_catalogs"), "the error does not name the way to add the catalog: {}", stderr);

    let pyproject = root.join("pyproject.toml");
    let base = std::fs::read_to_string(&pyproject).unwrap();
    std::fs::write(&pyproject, format!("{}world_catalogs = [\"catalogs/agents.json\"]\n", base)).unwrap();
    let (_, report) = editor(root);
    let key = "app/billing/entry/main.hy::DOEFF131::run::world::pkg_agents:agent-handler";
    assert!(keys(&report, "DOEFF131").contains(&key.to_string()), "{}", report);

    std::fs::write(&pyproject, format!("{}world_catalogs = [\"catalogs/missing.json\"]\n", base)).unwrap();
    let (code, _, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("world_catalogs names files that do not exist") && stderr.contains("catalogs/missing.json"), "{}", stderr);

    let flag = root.join("catalogs/agents.json");
    let (code, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log", "--world-catalog", flag.to_str().unwrap()], None);
    assert_ne!(code, 2, "{}", stderr);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    assert!(keys(&report, "DOEFF131").contains(&key.to_string()), "{}", report);
}

/// environ answerers decide real I/O by name — os-environ-reader, which reads this process's os.environ, is env in the inventory,
/// and listing it outside the list gives DOEFF131. environ-table-reader, which answers only from a given table of values, is not in the inventory, and listing it in the same place does not hit
/// (so as not to falsely detect the shape where a simulated host answers from a table of values). Previously a single name environ-reader read os.environ only when its argument was the default, and could not be put in the inventory.
#[test]
fn os_environ_reader_is_a_world_handler_and_the_table_reader_is_not() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/main.hy",
            tags("billing", "entry")
                + "(import doeff_cluster._foundation.host_contract [environ-table-reader os-environ-reader])\n\
                   (defk run [body] (with-handlers [(os-environ-reader)] body))\n\
                   (defk run-on-table [body] (with-handlers [(environ-table-reader {\"NAME\" \"value\"})] body))\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF131\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert_eq!(
        keys(&report, "DOEFF131"),
        vec!["app/billing/entry/main.hy::DOEFF131::run::world::doeff_cluster._foundation.host_contract:os-environ-reader"],
        "{}",
        report
    );
}

/// R6: tests are only deftest — for files selected by the :test-forms spelling type, emit one DOEFF135 (critical) per file for Python def test_*, per-module skip,
/// check scripts outside pytest, and deftest runners. A file with only deftest does not hit.
#[test]
fn test_forms_other_than_deftest_are_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/tests/test_good.hy", "(deftest test-one (assert True))\n".to_string()),
        ("app/billing/tests/test_python.py", "import pytest\n\ndef helper():\n    pass\n\ndef test_a():\n    assert True\n\nclass TestB:\n    def test_b(self):\n        assert True\n".to_string()),
        ("app/billing/tests/test_skipped.hy", "(import pytest)\n(pytest.skip \"missing on the doeff side\" :allow-module-level True)\n(deftest test-x (assert True))\n".to_string()),
        // A multi-line pytestmark where the bundle name and the skip mark are on different lines (the shape of agora's test_local_agora_e2e.hy).
        (
            "app/billing/tests/test_marked_multiline.hy",
            "(import pytest)\n;; a per-module skip is written with the pytestmark mark (words in comments do not hit).\n(val pytestmark\n  (pytest.mark.skip :reason (+ \"first line of the reason (with parentheses too)\"\n                              \"second line of the reason\")))\n(deftest test-x (assert True))\n"
                .to_string(),
        ),
        ("app/billing/tests/test_marked_multiline.py", "import pytest\n\npytestmark = [\n    pytest.mark.skip(reason=\"x\"),\n]\n".to_string()),
        // A multi-line bundle of a non-skip mark, and a one-line mark, do not hit. A per-test skip after the bundle is not counted as the bundle either.
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
    // A multi-line bundle points at the line of the bundle name.
    let multiline = violation(&report, "app/billing/tests/test_marked_multiline.hy::DOEFF135::module-skip");
    assert_eq!(multiline["range"]["start"]["line"], 2);
    let python = violation(&report, "app/billing/tests/test_python.py::DOEFF135::python-test");
    assert!(python["message"].as_str().unwrap().contains("2 Python def test_* functions"), "{}", python["message"]);
    assert_eq!(python["range"]["start"]["line"], 5);
    assert_eq!(python["level"], "critical");
}

/// The allow-list rules (DOEFF106, 131) also hit files outside the layer locations (dirs outside the layers, and dirs outside :root listed in :raw-io-roots).
/// Test files and dirs outside :raw-io-roots (scripts/) do not hit.
#[test]
fn world_handler_rules_cover_files_outside_the_layers() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/runtime/handlers.hy", "(import socket)\n(defn open-one [] (socket.socket))\n".to_string()),
        (
            "services/record/main.hy",
            "(import threading)\n(import doeff_core_effects._os_file [os-file-handler])\n(defn serve [] (threading.Thread))\n(defn run [body] (with-handlers [os-file-handler] body))\n"
                .to_string(),
        ),
        ("services/record/tests/test_main.hy", "(import socket)\n(deftest test-x (socket.socket))\n".to_string()),
        ("scripts/tool.hy", "(import subprocess)\n(defn go [] (subprocess.run [\"true\"]))\n".to_string()),
        // A collection of executable ADRs (a file with defadr) and the real I/O of a deftest in a file are the tests' share — they do not hit.
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
    assert_eq!(keys(&report, "DOEFF131"), vec!["services/record/main.hy::DOEFF131::run::world::doeff_core_effects._os_file:os-file-handler"], "{}", report);
    let found = violation(&report, "services/record/main.hy::DOEFF131::run::world::doeff_core_effects._os_file:os-file-handler");
    assert!(found["message"].as_str().unwrap().contains("outside the layer locations"), "{}", found["message"]);
    assert_eq!(found["level"], "critical");

    // A single-file run (--stdin) also applies 106 and 131 to files outside the layer locations and gives the same hits as a whole run — because a single-file
    // run claims to judge these two (judged_rules), the editor replaces the whole result with this result.
    let main = dir.path().join("services/record/main.hy");
    let text = std::fs::read_to_string(&main).unwrap();
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", main.to_str().unwrap()], Some(&text));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF106"), vec!["services/record/main.hy::DOEFF106::serve::threading.Thread"], "{}", single);
    assert_eq!(keys(&single, "DOEFF131"), keys(&report, "DOEFF131"), "{}", single);
    let judged: Vec<&str> = single["judged_rules"].as_array().unwrap().iter().map(|r| r.as_str().unwrap()).collect();
    assert!(judged.contains(&"DOEFF106") && judged.contains(&"DOEFF131"), "{:?}", judged);
}

/// R5: if not even one deftest under the simulation environment (:verification-environment) reaches a definition in a service's entry layer, it is
/// DOEFF136 (critical) at the defservice position. A service whose simulation-environment test calls the assembly, and a service without an entry layer, do not hit.
/// A call from a test inside the service (app/ledger/tests) is not counted since it is outside the simulation environment. billing is reached through a defsystem (a defsystem is also an owner of calls).
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
        + "(defservice ledger \"Ledger\" {:layers [core entry]})\n(defservice notes \"Notes\" {:layers [core]})\n";
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF136"), vec!["architecture.hy::DOEFF136::ledger"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF136::ledger");
    assert!(found["message"].as_str().unwrap().contains("service ledger's entry layer ("), "{}", found["message"]);
    assert_eq!(found["severity"], "error", "{}", found);
}

/// When a service that has code (at least one definition in the entry layer) does not declare :invariants (a list of `module:function`) in its defservice,
/// a named function does not exist, or the function's :role is not judgment, DOEFF163 (critical) at the defservice position.
/// A service without an entry layer, and a service with no definitions in the entry layer, do not hit. A gap listed in the registry drops to warning.
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
                "(defk billing-holds [seen]\n  {}\n  \"Billing invariants.\"\n  [])\n\
                 (defk orders-run [seen]\n  {{:pre [(: seen list)] :post [(: % list)] :tags {{:context \"sim\" :role \"program\"}}}}\n  \"Not a judgment.\"\n  [])\n\
                 (defk stock-holds [seen]\n  {}\n  \"Stock invariants.\"\n  [])\n",
                judged, judged
            ),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF163\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace("{:layers [core entry]})", "{:layers [core entry] :invariants [\"app.sim.invariants:billing-holds\"]})")
        + "(defservice ledger \"Ledger\" {:layers [core entry]})\n\
           (defservice orders \"Orders\" {:layers [core entry] :invariants [\"app.sim.invariants:gone\" \"app.sim.invariants:orders-run\"]})\n\
           (defservice stock \"Stock\" {:layers [core entry]})\n\
           (defservice notes \"Notes\" {:layers [core]})\n\
           (defservice empty \"Empty\" {:layers [core entry]})\n";
    std::fs::write(&arch_path, text).unwrap();
    // Known gap: stock's missing declaration is listed in the registry (drops to warning).
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
    assert!(missing["message"].as_str().unwrap().contains("service ledger does not declare :invariants"), "{}", missing["message"]);
    assert_eq!(missing["level"], "critical", "{}", missing);
    let gone = violation(&report, "architecture.hy::DOEFF163::orders::app.sim.invariants:gone");
    assert!(gone["message"].as_str().unwrap().contains("no definition (wrong module or function name)"), "{}", gone["message"]);
    let role = violation(&report, "architecture.hy::DOEFF163::orders::app.sim.invariants:orders-run");
    assert!(role["message"].as_str().unwrap().contains(":role is program"), "{}", role["message"]);
    let known = violation(&report, "architecture.hy::DOEFF163::stock");
    assert_eq!(known["severity"], "warning", "{}", known);
    assert_eq!(known["registered"], true, "{}", known);
}

/// Paired with DOEFF136: a service that has code declares :system (a defsystem placed in the cluster) in its defservice, and that system is
/// a defsystem in the entry layer whose argument is one typed foundation (the argument type of the job function that takes the foundation is not a plain Callable, mapping, or tuple).
/// Firing examples: no declaration, no definition, not a defsystem, two arguments, foundation type Callable, no type, outside entry, another service's entry system, an exception with an empty reason.
/// Firing examples (part-of): the system pointed at does not exist, the thing pointed at is not a defsystem.
/// Silent examples: a declaration + one typed foundation, a part-of pointing at another service's entry system, a reasoned exception (a service left on the old route, and
/// a part service with no process — the agent-catalog shape). A service without an entry layer is also counted (#2221): no :system fires, and
/// part-of and a reasoned exception do not fire.
#[test]
fn services_without_a_declared_system_are_red() {
    let job = |name: &str, ty: Option<&str>| match ty {
        Some(ty) => format!("(defk {} [foundation]\n  {{:pre [(: foundation {})] :post [(: % int)] :tags {{:context \"app\" :role \"entry\"}}}}\n  \"job。\"\n  1)\n", name, ty),
        None => format!("(defk {} [foundation]\n  {{:post [(: % int)] :tags {{:context \"app\" :role \"entry\"}}}}\n  \"job。\"\n  1)\n", name),
    };
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/system.hy",
            tags("billing", "entry")
                + &job("billing-job", Some("BillingFoundation"))
                + "(defsystem billing-system [foundation]\n  \"Billing system\"\n  (billing (billing-job foundation) :needs #{\"pg\"}))\n",
        ),
        ("app/ledger/entry/main.hy", tags("ledger", "entry") + "(defk start [] 2)\n"),
        (
            "app/orders/entry/system.hy",
            tags("orders", "entry")
                + &job("orders-job", Some("OrdersFoundation"))
                + &job("plain-job", Some("(of Callable [str] int)"))
                + &job("bare-job", None)
                + "(defsystem orders-system [foundation programs]\n  (orders (orders-job foundation)))\n\
                   (defsystem orders-plain [foundation]\n  (plain (plain-job foundation)))\n\
                   (defsystem orders-bare [foundation]\n  (bare (bare-job foundation)))\n\
                   (defsystem orders-typed [#^ OrdersFoundation foundation]\n  (typed (bare-job foundation)))\n\
                   (defsystem orders-typed-callable [#^ Callable foundation]\n  (callable (orders-job foundation)))\n\
                   (defsystem orders-typed-two [#^ OrdersFoundation foundation programs]\n  (two (orders-job foundation)))\n",
        ),
        ("app/orders/entry/main.hy", tags("orders", "entry") + "(defk take [] 3)\n"),
        ("app/stock/entry/main.hy", tags("stock", "entry") + "(defk count-all [] 4)\n"),
        (
            "app/sim/systems.hy",
            tags("sim", "entry") + &job("stock-job", Some("StockFoundation")) + "(defsystem stock-system [foundation]\n  (stock (stock-job foundation)))\n",
        ),
        ("app/archive/entry/main.hy", tags("archive", "entry") + "(defk keep [] 5)\n"),
        ("app/reports/entry/main.hy", tags("reports", "entry") + "(defk send [] 6)\n"),
        ("app/tasks/entry/main.hy", tags("tasks", "entry") + "(defk run-task [] 7)\n"),
        ("app/agents/entry/main.hy", tags("agents", "entry") + "(defk act [] 8)\n"),
        ("app/helpers/entry/main.hy", tags("helpers", "entry") + "(defk help [] 9)\n"),
        ("app/jobs/entry/main.hy", tags("jobs", "entry") + "(defk work [] 10)\n"),
        ("app/catalog/entry/main.hy", tags("catalog", "entry") + "(defk allowed [] 11)\n"),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF173\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace("{:layers [core entry]})", "{:layers [core entry] :system \"app.billing.entry.system:billing-system\"})")
        + "(defservice ledger \"Ledger\" {:layers [core entry]})\n\
           (defservice orders \"Orders\" {:layers [core entry] :system [\"app.orders.entry.system:orders-system\" \"app.orders.entry.system:orders-plain\"\n\
             \"app.orders.entry.system:orders-bare\" \"app.orders.entry.main:take\" \"app.orders.entry.system:gone\"\n\
             \"app.orders.entry.system:orders-typed\" \"app.orders.entry.system:orders-typed-callable\" \"app.orders.entry.system:orders-typed-two\"]})\n\
           (defservice stock \"Stock\" {:layers [core entry] :system \"app.sim.systems:stock-system\"})\n\
           (defservice archive \"Archive\" {:layers [core entry] :system {:exempt \"\"}})\n\
           (defservice reports \"Reports\" {:layers [core entry] :system {:exempt \"left on the old route — started per machine, not placed in the cluster system\"}})\n\
           (defservice catalog \"Allow-list\" {:layers [core entry] :system {:exempt \"a part with no process — no main or deploy, only read and used by other services\"}})\n\
           (defservice tasks \"Tasks\" {:layers [core entry] :system \"app.billing.entry.system:billing-system\"})\n\
           (defservice agents \"Agents\" {:layers [core entry] :system {:part-of \"app.billing.entry.system:billing-system\"}})\n\
           (defservice helpers \"Helpers\" {:layers [core entry] :system {:part-of \"app.billing.entry.system:nowhere\"}})\n\
           (defservice jobs \"Jobs\" {:layers [core entry] :system {:part-of \"app.orders.entry.main:take\"}})\n\
           (defservice notes \"Notes\" {:layers [core]})\n\
           (defservice vault \"Vault\" {:layers [core] :system {:part-of \"app.billing.entry.system:billing-system\"}})\n\
           (defservice parts \"Parts\" {:layers [core] :system {:exempt \"a part with no process — only read by other services\"}})\n";
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF173"),
        vec![
            "architecture.hy::DOEFF173::archive::exempt",
            "architecture.hy::DOEFF173::helpers::app.billing.entry.system:nowhere",
            "architecture.hy::DOEFF173::jobs::app.orders.entry.main:take",
            "architecture.hy::DOEFF173::ledger",
            // A service without an entry layer is also counted (the custody shape). vault (part-of) and parts (reasoned exception) do not fire.
            "architecture.hy::DOEFF173::notes",
            "architecture.hy::DOEFF173::orders::app.orders.entry.main:take",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:gone",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-bare::bare-job",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-plain::plain-job",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-system::params",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-typed-callable::orders-typed-callable",
            "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-typed-two::params",
            "architecture.hy::DOEFF173::stock::app.sim.systems:stock-system",
            "architecture.hy::DOEFF173::tasks::app.billing.entry.system:billing-system",
        ],
        "{}",
        report
    );
    // A declaration pointing at another service's entry system is also red (:system is the system in that service's own entry layer — the decision of cisco-c8 and #2188).
    let borrowed = violation(&report, "architecture.hy::DOEFF173::tasks::app.billing.entry.system:billing-system");
    assert!(borrowed["message"].as_str().unwrap().contains("not in the entry layer"), "{}", borrowed["message"]);
    // part-of (that service's code runs inside another service's system): it may point at another service's entry system (agents does not fire).
    // Red if the target does not exist or is not a defsystem.
    let nowhere = violation(&report, "architecture.hy::DOEFF173::helpers::app.billing.entry.system:nowhere");
    assert!(nowhere["message"].as_str().unwrap().contains("no definition found"), "{}", nowhere["message"]);
    let not_system = violation(&report, "architecture.hy::DOEFF173::jobs::app.orders.entry.main:take");
    assert!(not_system["message"].as_str().unwrap().contains("not a defsystem"), "{}", not_system["message"]);
    let missing = violation(&report, "architecture.hy::DOEFF173::ledger");
    assert!(missing["message"].as_str().unwrap().contains("service ledger did not declare :system"), "{}", missing["message"]);
    assert_eq!(missing["level"], "critical", "{}", missing);
    let two = violation(&report, "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-system::params");
    assert!(two["message"].as_str().unwrap().contains("arguments are not exactly one foundation ([foundation programs])"), "{}", two["message"]);
    let plain = violation(&report, "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-plain::plain-job");
    assert!(plain["message"].as_str().unwrap().contains("(of Callable [str] int)"), "{}", plain["message"]);
    let bare = violation(&report, "architecture.hy::DOEFF173::orders::app.orders.entry.system:orders-bare::bare-job");
    assert!(bare["message"].as_str().unwrap().contains("not written"), "{}", bare["message"]);
    let take = violation(&report, "architecture.hy::DOEFF173::orders::app.orders.entry.main:take");
    assert!(take["message"].as_str().unwrap().contains("not a defsystem"), "{}", take["message"]);
    let outside = violation(&report, "architecture.hy::DOEFF173::stock::app.sim.systems:stock-system");
    assert!(outside["message"].as_str().unwrap().contains("not in the entry layer"), "{}", outside["message"]);
}

/// The sample repo for DOEFF207: two production systems (billing, orders) and one simulation system that is not a production system. `outside` = the value of `:outside-writers`
/// written in architecture.hy (None = not written).
fn access_repo(outside: Option<&str>) -> tempfile::TempDir {
    let job = |name: &str| format!("(defk {} [foundation]\n  {{:post [(: % int)] :tags {{:context \"app\" :role \"entry\"}}}}\n  \"job。\"\n  1)\n", name);
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/system.hy",
            tags("billing", "entry")
                + &job("billing-job")
                + &job("audit-job")
                + &job("bare-job")
                + "(defsystem billing-system [foundation]\n  \"Billing system\"\n\
                   (billing (billing-job foundation) :needs #{\"pg\"} :reads #{\"store:orders\" \"store:audit\" \"ledger:items\"} :writes #{\"store:bills\"})\n\
                   (audit (audit-job foundation) :needs #{\"pg\"} :reads #{} :writes #{\"store:audit\"})\n\
                   (bare (bare-job foundation) :needs #{\"pg\"}))\n",
        ),
        (
            "app/orders/entry/system.hy",
            tags("orders", "entry")
                + &job("orders-job")
                + "(defsystem orders-system [foundation]\n  (orders (orders-job foundation) :needs #{\"pg\"} :reads #{\"store:bills\" \"record:turn\"} :writes #{\"store:orders\"}))\n",
        ),
        (
            "app/sim/systems.hy",
            // A simulation-system job's write is not counted as a writer (even if it writes record:turn, the production orders read stays red).
            tags("sim", "entry")
                + &job("stock-job")
                + "(defsystem stock-system [foundation]\n  (stock (stock-job foundation) :needs #{\"pg\"} :reads #{\"store:nothing\"} :writes #{\"record:turn\"}))\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF207\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declared = match outside {
        Some(table) => format!(":foundation foundation\n  :outside-writers {}", table),
        None => ":foundation foundation".to_string(),
    };
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", &declared)
        .replace("{:layers [core entry]})", "{:layers [core entry] :system \"app.billing.entry.system:billing-system\"})")
        + "(defservice orders \"Orders\" {:layers [core entry] :system \"app.orders.entry.system:orders-system\"})\n";
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// doeff-linter finds this kind of violation — a production-system job
/// is red if the pair it reads has neither a production job's :writes nor an :outside-writers line (the shape where a new entry screen read a conversation record that had no writer in production).
/// It is red if a production-system job has no field. A simulation system that is not a production system is not counted.
#[test]
fn production_reads_without_a_writer_are_red() {
    let dir = access_repo(Some("{\"ledger:items\" \"a tool outside the ledger\"}"));
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF207"),
        vec![
            "app/billing/entry/system.hy::DOEFF207::billing-system::bare::reads",
            "app/billing/entry/system.hy::DOEFF207::billing-system::bare::writes",
            // Tonight's shape: no production job writes the kind of conversation record that a production job reads (only a simulation-system job writes it).
            "app/orders/entry/system.hy::DOEFF207::orders-system::orders::record:turn",
        ]
    );
    let turn = violation(&report, "app/orders/entry/system.hy::DOEFF207::orders-system::orders::record:turn");
    assert!(turn["message"].as_str().unwrap().contains("has no writer"), "{}", turn["message"]);
    assert_eq!(turn["level"], "critical", "{}", turn);
    // If it is listed in the outside-writers table, the same read is green.
    let written = access_repo(Some("{\"ledger:items\" \"a tool outside the ledger\" \"record:turn\" \"the turn task\"}"));
    let (_, report) = editor(written.path());
    assert_eq!(
        keys(&report, "DOEFF207"),
        vec![
            "app/billing/entry/system.hy::DOEFF207::billing-system::bare::reads",
            "app/billing/entry/system.hy::DOEFF207::billing-system::bare::writes",
        ]
    );
}

/// The sample for a DOEFF207 writer that is not a job: a production-system orders job reads record:turn, and the task Program run-task
/// (app/orders/core/run.hy) claims to write via the contract's `:writes`. `reached` = whether the job calls run-task (whether it is reached from the production entry),
/// `declares` = whether run-task writes :writes. The production entry is decided by the :business-fakes declaration (same as DOEFF143).
fn task_writer_repo(reached: bool, declares: bool) -> tempfile::TempDir {
    let writes = if declares { " :writes #{\"record:turn\"}" } else { "" };
    let (import, call) = if reached { ("(import app.orders.core.run [run-task])\n", "(run-task 1)") } else { ("", "1") };
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/orders/core/run.hy", tags("orders", "judgment") + &format!("(defk run-task [x]\n  \"Program of the task.\"\n  {{:post [(: % int)]{}}}\n  x)\n", writes)),
        (
            "app/orders/entry/system.hy",
            tags("orders", "entry")
                + import
                + &format!("(defk orders-job [foundation]\n  {{:post [(: % int)] :tags {{:context \"app\" :role \"entry\"}}}}\n  \"job。\"\n  {})\n", call)
                + "(defsystem orders-system [foundation]\n  (orders (orders-job foundation) :needs #{\"pg\"} :reads #{\"record:turn\"} :writes #{}))\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF207\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declared = ":foundation foundation\n  :outside-writers {\"ledger:items\" \"a tool outside the ledger\"}\n  \
                    :business-fakes {:simulation [\"app/sim/**\"] :tests [\"tests/**\"] :production [\"app/**\"] :business-modules [\"app.orders\"]}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declared)
        + "(defservice orders \"Orders\" {:layers [core entry] :system \"app.orders.entry.system:orders-system\"})\n";
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// If a task Program reached from the production entry (a system job's Program) claims it in :writes, that pair's read is green. Removing the claim makes it red
/// (the shape where the write vanished from the task's code and the claim vanished too).
#[test]
fn a_reached_function_that_declares_its_writes_is_a_writer() {
    let (_, report) = editor(task_writer_repo(true, true).path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert!(keys(&report, "DOEFF207").is_empty(), "{:?}", keys(&report, "DOEFF207"));
    let (_, report) = editor(task_writer_repo(true, false).path());
    assert_eq!(keys(&report, "DOEFF207"), vec!["app/orders/entry/system.hy::DOEFF207::orders-system::orders::record:turn"]);
}

/// A :writes of a definition not reached from the production entry is not counted as a writer (even if a simulation-only task or an unused definition claims it, the read stays red).
#[test]
fn an_unreached_function_that_declares_writes_is_not_a_writer() {
    let (_, report) = editor(task_writer_repo(false, true).path());
    assert_eq!(keys(&report, "DOEFF207"), vec!["app/orders/entry/system.hy::DOEFF207::orders-system::orders::record:turn"]);
}

/// DOEFF207 is not applied to a repo that does not write :outside-writers (the field is optional in doeff's defsystem — only the production systems of repos that declare it are required).
#[test]
fn reads_are_not_judged_without_outside_writers() {
    let dir = access_repo(None);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF207").is_empty(), "{:?}", keys(&report, "DOEFF207"));
}

/// The sample repo for DOEFF208: in production code (:production of :business-fakes), lines the shape that uses waiting for a record change as the start mechanism, and shapes that do not hit.
/// `declared` = whether :business-fakes is written in architecture.hy (it is not applied to a repo that does not write it).
fn record_wait_repo(declared: bool) -> tempfile::TempDir {
    let files = [
        (
            // Hits: tying a record-change signal source into an entry handler list (read-signal-handler, and records-signal-handler with a module name).
            "app/billing/entry/turn_events.hy",
            tags("billing", "entry")
                + "(import doeff_records.event_source [read-signal-handler])\n(import doeff_records [event-source])\n\
                   (defk run-placer [body]\n  (with-handlers [(read-signal-handler BINDINGS on-moved)] body))\n\
                   (defk run-maker [body]\n  (with-handlers [(event-source.records-signal-handler BINDINGS on-moved)] body))\n",
        ),
        (
            // Hits: waiting for a record change (seconds as a variable, a literal 5.0, the third positional argument). Does not hit: seconds 0, 0.0, no seconds written (default 0),
            // and the spelling in a match pattern.
            "app/billing/core/memo_wait.hy",
            tags("billing", "judgment")
                + "(import doeff_records.effects [WatchChanges WatchEvents])\n\
                   (defk wait-for [cursor left]\n  (<- answer (WatchChanges #(\"message\") cursor :timeout left))\n  answer)\n\
                   (defk wait-literal [cursor]\n  (WatchChanges #(\"message\") cursor :timeout 5.0))\n\
                   (defk wait-stream [after]\n  (WatchEvents \"intake\" after 30))\n\
                   (defk catch-up [cursor]\n  (WatchChanges #(\"message\") cursor :timeout 0.0))\n\
                   (defk catch-up-int [cursor]\n  (WatchEvents \"intake\" 0 :timeout 0))\n\
                   (defk catch-up-default [cursor]\n  (WatchChanges #(\"message\") cursor))\n\
                   (defk waited [ask]\n  (match ask\n    (WatchChanges :timeout timeout) (float timeout)\n    (WatchEvents :timeout t) :if (> t 0) t\n    _ 0.0))\n",
        ),
        (
            // Does not hit: a wait re-emitted inside a handler that answers a record effect (a relay).
            "app/billing/foundation/relay.hy",
            tags("shared", "foundation")
                + "(defhandler records-relay\n  (WatchChanges [tables cursor timeout limit]\n    (<- answer (WatchChanges tables cursor :timeout timeout))\n    (resume answer))\n  \
                   (WatchEvents [stream after timeout]\n    (resume (WatchEvents stream after timeout))))\n",
        ),
        // Does not hit: inside the simulation environment, test files, and the doeff-records package (not users, but the owners of the effect and source).
        ("app/sim/world.hy", tags("sim", "entry") + "(defk emulated [body]\n  (with-handlers [(read-signal-handler BINDINGS on-moved)] body))\n"),
        ("app/billing/tests/test_wait.hy", tags("billing", "entry") + "(deftest waits\n  (WatchChanges #(\"message\") cursor :timeout 5.0))\n"),
        (
            "packages/doeff-records/src/doeff_records/event_source.hy",
            "(defk source-task [tables cursor]\n  (WatchChanges tables cursor :timeout WATCH-SECONDS))\n\
             (val SOURCE (records-signal-handler BINDINGS on-moved))\n"
                .to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF208\"]");
    if declared {
        let arch_path = dir.path().join("architecture.hy");
        let declared = ":foundation foundation\n  \
                        :business-fakes {:simulation [\"app/sim/**\"] :tests [\"**/tests/**\"] :production [\"app/**\" \"packages/**\"] :business-modules [\"app.billing\"]}";
        let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declared);
        std::fs::write(&arch_path, text).unwrap();
    }
    dir
}

/// A design that writes a record and waits for the record is forbidden by the linter — record and start are separated:
/// the place where production code makes record changes an event source (tying a signal source), and the place where a wait for a record change
/// with non-zero seconds is emitted outside a record-effect handler are red. Reading once without waiting (seconds 0), simulation, tests, inside doeff-records, inside a handler answering record effects,
/// and match patterns do not hit.
#[test]
fn production_waits_on_record_changes_are_red() {
    let dir = record_wait_repo(true);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert_eq!(
        keys(&report, "DOEFF208"),
        vec![
            "app/billing/core/memo_wait.hy::DOEFF208::wait-for::WatchChanges",
            "app/billing/core/memo_wait.hy::DOEFF208::wait-literal::WatchChanges",
            "app/billing/core/memo_wait.hy::DOEFF208::wait-stream::WatchEvents",
            "app/billing/entry/turn_events.hy::DOEFF208::run-maker::records-signal-handler",
            "app/billing/entry/turn_events.hy::DOEFF208::run-placer::read-signal-handler",
        ]
    );
    let source = violation(&report, "app/billing/entry/turn_events.hy::DOEFF208::run-placer::read-signal-handler");
    assert_eq!(source["level"], "critical", "{}", source);
    assert!(source["message"].as_str().unwrap().contains("makes record changes an event source"), "{}", source["message"]);
    let wait = violation(&report, "app/billing/core/memo_wait.hy::DOEFF208::wait-for::WatchChanges");
    assert!(wait["message"].as_str().unwrap().contains(":timeout left"), "{}", wait["message"]);
    assert!(!wait["explanation"]["reason"].as_str().unwrap().is_empty(), "{}", wait);
}

/// DOEFF208 is not applied to a repo that does not write :business-fakes (the range of production code, so the range of production code is unknown).
#[test]
fn record_waits_are_not_judged_without_business_fakes() {
    let dir = record_wait_repo(false);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF208").is_empty(), "{:?}", keys(&report, "DOEFF208"));
}

/// The sample repo for DOEFF209: in production code (:production of :business-fakes), lines the shape that repeatedly fetches the same thing with a time wait in between, and shapes that do not hit.
/// `declared` = whether :business-fakes is written in architecture.hy (it is not applied to a repo that does not write it).
fn timed_refetch_repo(declared: bool) -> tempfile::TempDir {
    let files = [
        (
            // Helper: set a deadline s seconds from now and wait just once (since it is once, the helper itself does not hit, and the caller inside a repetition does).
            "app/billing/core/clock_help.hy",
            tags("billing", "judgment")
                + "(defk arm-next [s]\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds s)))))\n",
        ),
        (
            // Hits: a loop that sets an ArmTimer of now + seconds and reads, a Delay loop, a shape wrapped in a helper defk, :poll-seconds,
            // a loop that also tries when there is no mark and has a "reconnect only while unreachable" written (the category does not fit), and a reason outside the vocabulary.
            "app/billing/core/polls.hy",
            tags("billing", "judgment")
                + "(import app.billing.core.clock_help [arm-next])\n\
                   (defk poll-arm []\n  (while True\n    (<- now (GetTime))\n    (<- (ArmTimer TAG (+ now (timedelta :seconds 5))))\n    (<- (WaitForEvent TimerFired))\n    (<- (ReadQueue))))\n\
                   (defk poll-sleep []\n  (while True\n    (<- (Delay 5.0))\n    (<- (ReadQueue))))\n\
                   (defk poll-helper []\n  (while True\n    (<- (arm-next 5.0))\n    (<- (WaitForEvent TimerFired))\n    (<- (ReadQueue))))\n\
                   (defk poll-lib [holder]\n  (SemaphoreSession holder :ttl-seconds 15.0 :poll-seconds 1.0))\n\
                   (defk reconnect-always []\n  (while True\n    ;; 時間で取り直す理由: 届かない間だけの繋ぎ直し\n    (<- (Delay INTERVAL))\n    (<- (Ping))))\n\
                   (defk poll-excuse []\n  (while True\n    (<- (Delay 5.0)) ; 時間で取り直す理由: 仕方ない\n    (<- (ReadQueue))))\n\
                   (defk poll-kube []\n  (while True\n    (<- (Delay 5.0)) ; 時間で取り直す理由: 相手に変更の知らせが無い: Kubernetes — 試した\n    (<- (ReadPods))))\n\
                   (defk poll-unproven []\n  (while True\n    (<- (Delay 5.0)) ; 時間で取り直す理由: 相手に変更の知らせが無い: 預かり所\n    (<- (ReadCustody))))\n",
        ),
        (
            // Does not hit: a deadline derived from a row, the upper-limit seconds for waiting for change (DOEFF208's side), a one-time deadline, a write-only loop (with a reason),
            // a counterpart with no change notification (a reason with the counterpart and the basis for the check), a reconnect that tries only while the mark is present (with a reason), a single wait handed to another task, the zero-second concession, a sleep for a deadline difference,
            // and a request timeout.
            "app/billing/core/fine.hy",
            tags("billing", "judgment")
                + "(import app.billing.core.clock_help [arm-next])\n\
                   (defk until-due [row]\n  (while True\n    (<- (ArmTimer TAG row.deadline))\n    (<- (WaitForEvent TimerFired))\n    (<- (ReadQueue))))\n\
                   (defk long-poll [c]\n  (while True\n    (<- (WatchChanges #(\"t\") c :timeout 30))\n    (<- (ReadQueue))))\n\
                   (defk once []\n  (<- now (GetTime))\n  (<- (ArmTimer TAG (+ now (timedelta :seconds 5))))\n  (<- (WaitForEvent TimerFired)))\n\
                   (defk beat []\n  (while True\n    ;; 時間で取り直す理由: 書くだけ: 生存の印\n    (<- (arm-next 5.0))\n    (<- (WaitForEvent TimerFired))\n    (<- (WriteBeat))))\n\
                   (defk custody []\n  (while True\n    (<- (Delay 60.0)) ; 時間で取り直す理由: 相手に変更の知らせが無い: 預かり所 — custody の API の文書に変更の知らせの節が無い\n    (<- (ReadCustody))))\n\
                   (defk reconnect [url]\n  (while True\n    (<- answer (Ping url))\n    (match answer\n      (Unreachable) (do\n        ;; 時間で取り直す理由: 届かない間だけの繋ぎ直し\n        (<- (Delay 2.0)))\n      _ (return answer))))\n\
                   (defk spawner []\n  (while True\n    (<- (Spawn (arm-next 5.0)))\n    (<- (WaitForEvent Moved))))\n\
                   (defk yields [due]\n  (while True\n    (<- now (GetTime))\n    (<- (Delay 0.0))\n    (<- (Delay (- due now)))\n    (<- (HttpRequest URL :timeout 5.0))))\n",
        ),
        // Hits: services/record leaked by the previous split, and doeff packages (including inside doeff-core-effects, doeff-traverse, and doeff-records).
        ("services/record/serving.hy", "(defk store-watch []\n  (while True\n    (<- (Delay 5.0))\n    (<- (ReadStore))))\n".to_string()),
        (
            "packages/doeff-core-effects/doeff_core_effects/os_process.hy",
            "(defk wait-exit [p]\n  (while (running? p)\n    (<- (Delay 0.02))))\n".to_string(),
        ),
        ("packages/doeff-traverse/doeff_traverse/walk.hy", "(defk walk-poll []\n  (while True\n    (<- (Delay 1.0))\n    (<- (Probe))))\n".to_string()),
        (
            "packages/doeff-records/src/doeff_records/maintenance.hy",
            "(defk maintain [s]\n  (while True\n    (<- (Delay s))\n    (<- (Compact))))\n".to_string(),
        ),
        // Does not hit: the simulation environment and test files (outside the population).
        ("app/sim/world.hy", tags("sim", "entry") + "(defk emulated []\n  (while True\n    (<- (Delay 5.0))\n    (<- (ReadQueue))))\n"),
        ("app/billing/tests/test_poll.hy", tags("billing", "entry") + "(deftest polls\n  (while True\n    (<- (Delay 5.0))))\n"),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF209\"]");
    if declared {
        let arch_path = dir.path().join("architecture.hy");
        let declared = ":foundation foundation\n  \
                        :business-fakes {:simulation [\"app/sim/**\"] :tests [\"**/tests/**\"] :production [\"app/**\" \"services/**\" \"packages/**\"] :business-modules [\"app.billing\"]}";
        let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", declared);
        std::fs::write(&arch_path, text).unwrap();
    }
    dir
}

/// Repeated fetches are all to be fixed, and limited to a last resort:
/// a place where a production-code Program fetches the same thing repeatedly with a time wait in between
/// is red — an ArmTimer of now + seconds, Delay, a shape wrapped in a helper definition, :poll-seconds. A deadline wait, the upper-limit seconds for waiting for change, a one-time deadline,
/// and a place with a reason in the closed vocabulary pass. The population also includes services/record and doeff packages (doeff-core-effects, doeff-traverse, doeff-records).
#[test]
fn production_polling_on_timers_is_red() {
    let dir = timed_refetch_repo(true);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert_eq!(
        keys(&report, "DOEFF209"),
        vec![
            "app/billing/core/polls.hy::DOEFF209::poll-arm::ArmTimer::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-excuse::Delay::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-helper::arm-next::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-kube::Delay::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-lib::SemaphoreSession::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-sleep::Delay::periodic",
            "app/billing/core/polls.hy::DOEFF209::poll-unproven::Delay::periodic",
            "app/billing/core/polls.hy::DOEFF209::reconnect-always::Delay::periodic",
            "packages/doeff-core-effects/doeff_core_effects/os_process.hy::DOEFF209::wait-exit::Delay::periodic",
            "packages/doeff-records/src/doeff_records/maintenance.hy::DOEFF209::maintain::Delay::periodic",
            "packages/doeff-traverse/doeff_traverse/walk.hy::DOEFF209::walk-poll::Delay::periodic",
            "services/record/serving.hy::DOEFF209::store-watch::Delay::periodic",
        ]
    );
    let arm = violation(&report, "app/billing/core/polls.hy::DOEFF209::poll-arm::ArmTimer::periodic");
    assert_eq!(arm["level"], "critical", "{}", arm);
    assert!(!arm["explanation"]["reason"].as_str().unwrap().is_empty(), "{}", arm);
    let helper = violation(&report, "app/billing/core/polls.hy::DOEFF209::poll-helper::arm-next::periodic");
    assert!(helper["message"].as_str().unwrap().contains("clock_help.hy:"), "{}", helper["message"]);
    let always = violation(&report, "app/billing/core/polls.hy::DOEFF209::reconnect-always::Delay::periodic");
    assert!(always["message"].as_str().unwrap().contains("refetch after unreachable"), "{}", always["message"]);
    let excuse = violation(&report, "app/billing/core/polls.hy::DOEFF209::poll-excuse::Delay::periodic");
    assert!(excuse["message"].as_str().unwrap().contains("outside the closed vocabulary"), "{}", excuse["message"]);
    let kube = violation(&report, "app/billing/core/polls.hy::DOEFF209::poll-kube::Delay::periodic");
    assert!(kube["message"].as_str().unwrap().contains("this party has change notifications (Kubernetes)"), "{}", kube["message"]);
    let unproven = violation(&report, "app/billing/core/polls.hy::DOEFF209::poll-unproven::Delay::periodic");
    assert!(unproven["message"].as_str().unwrap().contains("how it was verified"), "{}", unproven["message"]);
}

/// DOEFF209 is not applied to a repo that does not write :business-fakes (the declaration of the range of production code).
#[test]
fn polling_on_timers_is_not_judged_without_business_fakes() {
    let dir = timed_refetch_repo(false);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF209").is_empty(), "{:?}", keys(&report, "DOEFF209"));
}

/// Add a registry dir and write the key `key` into a file in it (try listing it as a known hit).
fn register_key(root: &Path, key: &str) {
    let pyproject = root.join("pyproject.toml");
    let settings = std::fs::read_to_string(&pyproject).unwrap() + "[tool.doeff-linter.registry]\ndirs = [\"registry/BREACHES\"]\n";
    std::fs::write(&pyproject, settings).unwrap();
    let registry = root.join("registry/BREACHES");
    std::fs::create_dir_all(&registry).unwrap();
    std::fs::write(registry.join("known.txt"), format!("{}\nTry listing it as a known hit (a sample showing it is not lowered)\n", key)).unwrap();
}

/// Even if the keys of DOEFF208 and 209 are listed in the registry, the hits stay error, critical, and outside the registry (not turned into passing by a known list —
/// the same shape as DOEFF206's intent_fakes_in_verification_are_not_lowered_by_the_registry).
#[test]
fn record_waits_and_polling_are_not_lowered_by_the_registry() {
    for (dir, rule, key) in [
        (record_wait_repo(true), "DOEFF208", "app/billing/core/memo_wait.hy::DOEFF208::wait-for::WatchChanges"),
        (timed_refetch_repo(true), "DOEFF209", "app/billing/core/polls.hy::DOEFF209::poll-sleep::Delay::periodic"),
    ] {
        register_key(dir.path(), key);
        let (_, report) = editor(dir.path());
        assert!(keys(&report, rule).contains(&key.to_string()), "{}", report);
        let found = violation(&report, key);
        assert_eq!(found["severity"], "error", "{}", found);
        assert_eq!(found["level"], "critical", "{}", found);
        assert_eq!(found["registered"], false, "{}", found);
    }
}

/// Run editor-json with the named paths and the baseline report, and return (exit code, the new critical identifiers in lexicographic order).
fn named_against_baseline(root: &Path, baseline: &Path, named: &[&str]) -> (i32, Vec<String>) {
    let mut args = vec!["--output-format", "editor-json", "--no-log", "--baseline-report", baseline.to_str().unwrap()];
    args.extend_from_slice(named);
    let (code, stdout, stderr) = run(root, &args, None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    let mut fresh: Vec<String> =
        report["new_critical"].as_array().unwrap_or_else(|| panic!("new_critical is missing: {}", report)).iter().map(|v| v.as_str().unwrap().to_string()).collect();
    fresh.sort();
    (code, fresh)
}

/// cisco-c8's approval — the shape of Rule A's entry: the critical of a rule with no known list (a rule not lowered by the registry — DOEFF206 to 209)
/// enters new_critical if it is in a named path (a changed file), even if the baseline report has the same key (it is lowered neither by the baseline diff nor by the registry). A hit in a file
/// that is not named does not enter. A whole run with no named paths is only the baseline diff (the whole count is reported daily).
#[test]
fn rules_without_a_registry_block_their_hits_in_named_files_even_when_the_baseline_has_them() {
    let dir = record_wait_repo(true);
    let root = dir.path();
    let baseline = baseline_file(root);
    let (code, fresh) = named_against_baseline(root, &baseline, &["app/billing/core/memo_wait.hy"]);
    assert_eq!(
        fresh,
        vec![
            "app/billing/core/memo_wait.hy::DOEFF208::wait-for::WatchChanges",
            "app/billing/core/memo_wait.hy::DOEFF208::wait-literal::WatchChanges",
            "app/billing/core/memo_wait.hy::DOEFF208::wait-stream::WatchEvents",
        ]
    );
    assert_eq!(code, 4);
    // A hit in a file that is not named (turn_events.hy) does not enter.
    let (_, fresh) = named_against_baseline(root, &baseline, &["app/billing/foundation/relay.hy"]);
    assert!(fresh.is_empty(), "{:?}", fresh);
    // A run with no named paths is only the baseline diff.
    let (_, fresh) = named_against_baseline(root, &baseline, &[]);
    assert!(fresh.is_empty(), "{:?}", fresh);
    // DOEFF209 goes through the same entry (judge everything, then narrow by the named paths).
    let dir = timed_refetch_repo(true);
    let root = dir.path();
    let baseline = baseline_file(root);
    let (code, fresh) = named_against_baseline(root, &baseline, &["services/record/serving.hy"]);
    assert_eq!(fresh, vec!["services/record/serving.hy::DOEFF209::store-watch::Delay::periodic"]);
    assert_eq!(code, 4);
}

/// A repo with no layer dirs (`<root>/<dir>/entry/`) (a repo split by feature dirs, like merge-queue) declares where its code lives with the defservice
/// `:entry-modules`. A declared service enters the population of DOEFF163 — in the form without a declaration there is no entry dir, so
/// the population was 0 and it did not fire even when the clauses were removed.
fn entry_module_repo(queue: &str) -> tempfile::TempDir {
    let judged = "{:pre [(: writes tuple)] :post [(: % tuple)] :tags {:context \"lease\" :role \"judgment\"}}";
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/queue/main.hy", "(defk cycle [] 1)\n".to_string()),
        ("app/queue/lease_invariants.hy", format!("(defk fenced-writes [writes]\n  {}\n  \"Fence clause.\"\n  #())\n", judged)),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF163\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap() + &format!("(defservice queue \"Queue\" {{:layers [core] {}}})\n", queue);
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// Failure case 1: a service that declares `:entry-modules` and has no :invariants is DOEFF163 (critical) even without an entry layer dir.
#[test]
fn a_service_with_entry_modules_and_no_invariants_is_red() {
    let dir = entry_module_repo(":entry-modules [\"app.queue.main\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF163"), vec!["architecture.hy::DOEFF163::queue"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF163::queue");
    assert!(found["message"].as_str().unwrap().contains("service queue does not declare :invariants"), "{}", found["message"]);
    assert_eq!(found["level"], "critical", "{}", found);
}

/// Failure case 2: a service that declares `:entry-modules` and a judgment :invariants is green (removing the clause turns it red in the shape of failure case 1).
/// A service with no declaration (neither `:entry-modules` nor an entry layer) is not counted, as before.
#[test]
fn a_service_with_entry_modules_and_a_judgment_invariant_is_green() {
    let dir = entry_module_repo(":entry-modules [\"app.queue.main\"] :invariants [\"app.queue.lease_invariants:fenced-writes\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF163").is_empty(), "{}", report);
    let silent = entry_module_repo("");
    let (_, report) = editor(silent.path());
    assert!(keys(&report, "DOEFF163").is_empty(), "a service with no declaration was counted: {}", report);
}

/// Failure case 3: if the Hy file of an `:entry-modules` module is missing, DOEFF163 (`entry::<module>`) so that a spelling mistake does not silently drop it from the population.
#[test]
fn an_entry_module_without_a_file_is_red() {
    let dir = entry_module_repo(":entry-modules [\"app.queue.mian\"] :invariants [\"app.queue.lease_invariants:fenced-writes\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF163"), vec!["architecture.hy::DOEFF163::queue::entry::app.queue.mian"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF163::queue::entry::app.queue.mian");
    assert!(found["message"].as_str().unwrap().contains("Hy file (app/queue/mian.hy) not found"), "{}", found["message"]);
}

/// In a monorepo package (`pkg/pyproject.toml` and `pkg/src/cluster/…`), the config's `root = "src"` aligns module names
/// with import names (`cluster.coord`). architecture.hy is placed at the package root (outside the root), and named by the config's `architecture`.
/// Three variants are made by whether `invariants` is written and whether `root` is written.
fn package_repo(invariants: bool, root: bool) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let settings = format!(
        "[tool.doeff-linter]\n{}architecture = \"architecture.hy\"\ndisable = [\"DOEFF114\", \"DOEFF115\"]\n",
        if root { "root = \"src\"\n" } else { "" }
    );
    let judged = "{:pre [(: before dict) (: after dict)] :post [(: % tuple)] :tags {:context \"cluster\" :role \"judgment\"}}";
    let declared = if invariants { " :invariants [\"cluster.coord_invariants:writes-survive\"]" } else { "" };
    let files = [
        ("pyproject.toml", settings),
        (
            "architecture.hy",
            format!(
                "(defarchitecture pkg :root \"cluster\" :layers [(layer foundation :summary \"foundation\")] :foundation foundation)\n\
                 (defservice coord \"Coordination\" {{:entry-modules [\"cluster.coord\"]{}}})\n",
                declared
            ),
        ),
        ("src/cluster/coord.hy", "(defk run [] 1)\n".to_string()),
        ("src/cluster/coord_invariants.hy", format!("(defk writes-survive [before after]\n  {}\n  \"Clause.\"\n  #())\n", judged)),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Failure case 1: with the config's root = "src", the references in :entry-modules and :invariants resolve, and DOEFF163 is green.
#[test]
fn a_package_with_a_source_root_setting_resolves_its_declarations() {
    let dir = package_repo(true, true);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report);
    assert!(keys(&report, "DOEFF163").is_empty(), "{}", report);
}

/// Failure case 2: removing the clause (:invariants) turns DOEFF163 red. The key names architecture.hy outside the root (src) with a relative `..` (the per-machine absolute path
/// is not put in the key).
#[test]
fn a_package_without_its_clause_is_red_with_a_relative_key() {
    let dir = package_repo(false, true);
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF163"), vec!["../architecture.hy::DOEFF163::coord"], "{}", report);
    assert_eq!(violation(&report, "../architecture.hy::DOEFF163::coord")["level"], "critical", "{}", report);
}

/// Failure case 3: if root is not written, the root is the config file's dir and the module name becomes `src.cluster.coord`, so the :entry-modules module is not found
/// (evidence that the config's root is in effect).
#[test]
fn a_package_without_the_source_root_setting_cannot_find_its_modules() {
    let dir = package_repo(true, false);
    let (_, report) = editor(dir.path());
    assert!(
        keys(&report, "DOEFF163").contains(&"architecture.hy::DOEFF163::coord::entry::cluster.coord".to_string()),
        "{}",
        report
    );
}

/// K2: for each service with an entry layer, if there is no counterexample of a broken handler (a deftest that reaches a clause of the counterexample table and also reaches that service's entry),
/// DOEFF164 (critical) at the defservice position. billing runs a broken handler of its own business effect in its own test (present).
/// stock runs a broken handler of a foundation effect (an effect under no service's dir) in a simulation test (present — also counted as a table hit, so not stale).
/// ledger's simulation test reaches the entry but has no counterexample (red). notes has no entry layer (not counted).
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
            "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\nA broken handler that returns 0 for billing\n".to_string(),
        ),
        ("tables/COUNTEREXAMPLES/stock.txt", "app/sim/broken_clock.hy::stopped-clock::app.foundation.clock_effects.Now\nA stopped clock\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF157\", \"DOEFF164\", \"DOEFF176\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes)
        + "(defservice ledger \"Ledger\" {:layers [core entry]})\n(defservice stock \"Stock\" {:layers [core entry]})\n(defservice notes \"Notes\" {:layers [core]})\n";
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF164"), vec!["architecture.hy::DOEFF164::ledger"], "{}", report);
    let found = violation(&report, "architecture.hy::DOEFF164::ledger");
    assert!(found["message"].as_str().unwrap().contains("service ledger has no counterexample of a broken handler"), "{}", found["message"]);
    assert_eq!(found["severity"], "error", "{}", found);
    // A row of a broken handler that answers a foundation effect is a table hit (it does not become the old-row DOEFF176).
    assert!(keys(&report, "DOEFF176").iter().all(|k| !k.contains("stopped-clock")), "{}", report);
}

/// A dir with no :business-fakes :unserved (a table that fixes violations, in the shrinking direction) is read as an empty table (one notice line, not put in errors
/// — git does not keep an empty dir when the last row is removed). A dir with no declaration list (:counterexamples) is unreadable as before (errors).
#[test]
fn an_absent_unserved_table_is_empty_but_an_absent_declaration_list_is_unreadable() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] \
                 :counterexamples \"tables/COUNTEREXAMPLES\" :unserved \"tables/EXTERNAL-UNSERVED\"}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes);
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    let errors: Vec<&str> = report["errors"].as_array().unwrap().iter().filter_map(Value::as_str).collect();
    assert!(errors.iter().all(|e| !e.contains("EXTERNAL-UNSERVED")), "a missing :unserved table is not a read error: {:?}", errors);
    assert!(errors.iter().any(|e| e.contains("tables/COUNTEREXAMPLES") && e.contains("cannot read")), "a missing declaration list is unreadable: {:?}", errors);
    // The notice goes to stderr in text output (the same channel as the notice for a dir with no registry — #1732)
    let (_, _, stderr) = run(dir.path(), &["--output-format", "text", "--no-log"], None);
    assert!(stderr.contains("notice") && stderr.contains("tables/EXTERNAL-UNSERVED"), "the empty reading is announced: {}", stderr);
}

/// Check DOEFF143 (simulation fakes) and DOEFF157 (test-only fakes) by naming them by rule ID.
/// The simulation environment's fake-charge and the test file's test-charge answer the business effect Charge rather than tap (firing examples). In the same location, a tap handler that passes the received effect
/// straight on (watch-charge, which resumes the re-performed answer; watch-refund, which reperforms —
/// `(resume (Charge))` is read as a clause that answers the effect value itself), and a deliberately broken handler in the counterexample table (broken-refund, broken-charge) do not fire (silent examples).
#[test]
fn simulation_fakes_and_test_only_fakes_are_red_but_taps_and_counterexamples_are_not() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/intent/effects.hy", "(import doeff [EffectBase])\n(defclass Charge [EffectBase])\n(defclass Refund [EffectBase])\n".to_string()),
        (
            "app/sim/fake_billing.hy",
            "(import app.billing.intent.effects [Charge Refund])\n\
             (defhandler fake-charge (Charge [] (resume 0)))\n\
             (defhandler watch-charge (Charge [] (<- charged effect) (resume charged)))\n\
             (defhandler broken-refund (Refund [] (resume 0)))\n"
                .to_string(),
        ),
        (
            "app/billing/tests/test_fakes.hy",
            "(import app.billing.intent.effects [Charge Refund])\n\
             (defhandler test-charge (Charge [] (resume 1)))\n\
             (defhandler watch-refund (Refund [] (reperform effect)))\n\
             (defhandler broken-charge (Charge [] (resume 0)))\n\
             (deftest test-with-handlers (with-handlers [test-charge watch-refund broken-charge] 1))\n"
                .to_string(),
        ),
        ("tables/COUNTEREXAMPLES/sim_refund.txt", "app/sim/fake_billing.hy::broken-refund::app.billing.intent.effects.Refund\nA broken handler that returns 0 for a refund\n".to_string()),
        (
            "tables/COUNTEREXAMPLES/test_charge.txt",
            "app/billing/tests/test_fakes.hy::broken-charge::app.billing.intent.effects.Charge\nA broken handler that returns 0 for billing\n".to_string(),
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
    assert!(violation(&report, fake)["message"].as_str().unwrap().contains("(reachable only from the simulation root)"), "{}", violation(&report, fake));
    let test_only = "app/billing/tests/test_fakes.hy::DOEFF157::test-charge::app.billing.intent.effects.Charge";
    assert_eq!(keys(&report, "DOEFF157"), vec![test_only], "{}", report);
    assert!(violation(&report, test_only)["message"].as_str().unwrap().contains("test-only fake"), "{}", violation(&report, test_only));
    assert_eq!(violation(&report, test_only)["severity"], "error");
}

/// Old rows of the counterexample table that no longer hit are emitted as DOEFF176 and not counted in DOEFF143 (fakes) (the count of fakes and the count of old rows are counted separately by rule number).
/// Of the table's 2 rows, broken-refund hits a broken handler that still exists (silent example), and gone-charge hits no clause (firing example).
/// Without enabling DOEFF176, old rows do not appear (they do not go back to DOEFF143).
#[test]
fn a_stale_counterexample_row_is_doeff176_not_a_doeff143_fake() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/intent/effects.hy", "(import doeff [EffectBase])\n(defclass Charge [EffectBase])\n(defclass Refund [EffectBase])\n".to_string()),
        ("app/sim/fake_billing.hy", "(import app.billing.intent.effects [Refund])\n(defhandler broken-refund (Refund [] (resume 0)))\n".to_string()),
        ("tables/COUNTEREXAMPLES/sim_refund.txt", "app/sim/fake_billing.hy::broken-refund::app.billing.intent.effects.Refund\nA broken handler that returns 0 for a refund\n".to_string()),
        ("tables/COUNTEREXAMPLES/gone_charge.txt", "app/sim/fake_billing.hy::gone-charge::app.billing.intent.effects.Charge\nA removed broken handler\n".to_string()),
    ];
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let stale = "tables/COUNTEREXAMPLES::DOEFF176::counterexample-unused::app/sim/fake_billing.hy::gone-charge::app.billing.intent.effects.Charge";
    for (enable, expected) in [("[\"DOEFF143\", \"DOEFF176\"]", vec![stale]), ("[\"DOEFF143\"]", vec![])] {
        let dir = world_repo_with(&files, "", enable);
        let arch_path = dir.path().join("architecture.hy");
        let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes);
        std::fs::write(&arch_path, text).unwrap();
        let (_, report) = editor(dir.path());
        assert_eq!(report["errors"], serde_json::json!([]), "{}: {}", enable, report["errors"]);
        assert_eq!(keys(&report, "DOEFF143"), Vec::<String>::new(), "{}: {}", enable, report);
        assert_eq!(keys(&report, "DOEFF176"), expected, "{}: {}", enable, report);
        if let Some(key) = expected.first() {
            let found = violation(&report, key);
            assert!(found["message"].as_str().unwrap().contains("no longer matches"), "{}", found);
            assert_eq!(found["severity"], "error", "{}", found);
        }
    }
}

/// DOEFF164 is green if a service has at least one counterexample. For each clause in the defservice :clauses, DOEFF167 requires either a broken-handler counterexample claimed by the table row's
/// `breaks: <service>::<clause>` (a deftest reaching the clause also reaches the service's entry), or a :clause-exemptions reason.
/// billing: B1 has a counterexample, B2 has no claiming row (red), B3 is exempted with a reason. stock: the foundation effect's counterexample claims S1 (present — positive control).
/// ledger: does not write :clauses (red by the service name). notes has no entry layer (not counted). A row claiming B9 is a config error.
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
            "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\nA broken handler that returns 0 for billing\nbreaks: billing::B1\n"
                .to_string(),
        ),
        (
            "tables/COUNTEREXAMPLES/stock.txt",
            "app/sim/broken_clock.hy::stopped-clock::app.foundation.clock_effects.Now\nA stopped clock\nbreaks: stock::S1\n".to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF157\", \"DOEFF167\"]");
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :verification-environment \"sim\"\n  :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] \
                 :tests [\"**/tests/**\"] :production [\"app/**\"] :business-modules [\"app.billing\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let base = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes).replace(
        "(defservice billing \"Billing\" {:layers [core entry]})",
        "(defservice billing \"Billing\" {:layers [core entry] :clauses [\"B1\" \"B2\" \"B3\"] :clause-exemptions {\"B3\" \"cannot be broken by a broken handler (structural guarantee)\"}})",
    ) + "(defservice ledger \"Ledger\" {:layers [core entry]})\n(defservice stock \"Stock\" {:layers [core entry] :clauses [\"S1\"]})\n\
         (defservice notes \"Notes\" {:layers [core]})\n";
    assert!(base.contains(":clauses [\"B1\""), "billing's declaration cannot be replaced:\n{}", base);
    std::fs::write(&arch_path, &base).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF167"), vec!["architecture.hy::DOEFF167::billing::B2", "architecture.hy::DOEFF167::ledger"], "{}", report);
    let gap = violation(&report, "architecture.hy::DOEFF167::billing::B2");
    assert!(gap["message"].as_str().unwrap().contains("service billing clause B2 has no counterexample with a broken handler"), "{}", gap["message"]);
    assert_eq!(gap["severity"], "error", "{}", gap);
    let undeclared = violation(&report, "architecture.hy::DOEFF167::ledger");
    assert!(undeclared["message"].as_str().unwrap().contains("clause names (:clauses)"), "{}", undeclared["message"]);
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);

    // Counterexample: a row claiming a clause not in the declaration is a config error (not silently counted).
    std::fs::write(
        dir.path().join("tables/COUNTEREXAMPLES/billing.txt"),
        "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\nA broken handler that returns 0 for billing\nbreaks: billing::B1 billing::B9\n",
    )
    .unwrap();
    let (_, report) = editor(dir.path());
    assert!(report["errors"].to_string().contains("clause B9 is not in service billing's :clauses"), "{}", report["errors"]);

    // Positive control: if it claims B2 (the same counterexample breaks B1 and B2) and ledger exempts all its clauses with reasons, DOEFF167 is 0.
    std::fs::write(
        dir.path().join("tables/COUNTEREXAMPLES/billing.txt"),
        "app/billing/tests/test_broken.hy::broken-charge::app.billing.intent.effects.Charge\nA broken handler that returns 0 for billing\nbreaks: billing::B1 billing::B2\n",
    )
    .unwrap();
    let covered = base.replace(
        "(defservice ledger \"Ledger\" {:layers [core entry]})",
        "(defservice ledger \"Ledger\" {:layers [core entry] :clauses [\"L1\"] :clause-exemptions {\"L1\" \"reason\"}})",
    );
    std::fs::write(&arch_path, covered).unwrap();
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF167").is_empty(), "{:?}", keys(&report, "DOEFF167"));
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
}

/// A service of a repo with no layer dirs (declaring the entry with `:entry-modules` — the merge-queue shape) also enters the population of DOEFF164 and 167,
/// and is present if a deftest reaching a counterexample clause reaches that entry module. Previously only entry layer dirs were the population, so a `:entry-modules`
/// service was not counted (it did not fire even when the counterexample row was removed), and the link between clauses and counterexamples could not be checked.
/// `row` = whether to place the counterexample table row.
fn entry_module_counterexample_repo(row: bool) -> tempfile::TempDir {
    let mut files = vec![
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/queue/effects.hy", "(import doeff [EffectBase])\n(defclass Put [EffectBase])\n".to_string()),
        ("app/queue/main.hy", "(import app.queue.effects [Put])\n(defk cycle [] (Put))\n".to_string()),
        ("app/queue/judge.hy", "(defk fenced [writes] {:pre [] :post [] :tags {:context \"queue\" :role \"judgment\"}} \"Clause.\" #())\n".to_string()),
        (
            "tests/test_fence.hy",
            "(import app.queue.effects [Put])\n(import app.queue.main [cycle])\n\
             (defhandler unfenced (Put [] (resume 0)))\n(deftest test-unfenced-breaks-the-fence (with-handlers [unfenced] (cycle)))\n"
                .to_string(),
        ),
    ];
    if row {
        files.push(("tables/COUNTEREXAMPLES/queue.txt", "tests/test_fence.hy::unfenced::app.queue.effects.Put\nA writer that bypasses the fence\nbreaks: queue::Q1\n".to_string()));
    }
    let dir = world_repo_with(&files, "", "[\"DOEFF143\", \"DOEFF163\", \"DOEFF164\", \"DOEFF167\"]");
    std::fs::create_dir_all(dir.path().join("tables/COUNTEREXAMPLES")).unwrap();
    let arch_path = dir.path().join("architecture.hy");
    let fakes = ":foundation foundation\n  :business-fakes {:simulation [\"app/sim/**\"] :tests [\"tests/**\"] :production [\"app/**\"] \
                 :business-modules [\"app.queue\"] :counterexamples \"tables/COUNTEREXAMPLES\"}";
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", fakes)
        + "(defservice queue \"Queue\" {:layers [core] :entry-modules [\"app.queue.main\"] :invariants [\"app.queue.judge:fenced\"] :clauses [\"Q1\"]})\n";
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// Failure case 1: if a counterexample row claims clause Q1 and a deftest reaching that clause reaches the :entry-modules module (app/queue/main.hy), DOEFF164 and 167 are green.
#[test]
fn an_entry_module_service_with_a_counterexample_is_green() {
    let dir = entry_module_counterexample_repo(true);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert!(keys(&report, "DOEFF164").is_empty(), "{}", report);
    assert!(keys(&report, "DOEFF167").is_empty(), "{}", report);
}

/// Failure case 2: removing the counterexample row turns the :entry-modules service red for DOEFF164 (no counterexample) and DOEFF167 (clause Q1 has no counterexample).
#[test]
fn an_entry_module_service_without_its_counterexample_is_red() {
    let dir = entry_module_counterexample_repo(false);
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF164"), vec!["architecture.hy::DOEFF164::queue"], "{}", report);
    assert_eq!(keys(&report, "DOEFF167"), vec!["architecture.hy::DOEFF167::queue::Q1"], "{}", report);
}

/// K4: gaps in the coverage of intent effects (DOEFF165, the K3 table) fail as critical. Existing gaps are listed in the registry
/// (a dir with one key per file), and listed gaps drop to warning. Charge and Refund are both not emitted by tests and have no answerer (gaps) —
/// only Refund is listed in the registry. Settle has all 3 columns filled and does not appear (silent example, #1818).
#[test]
fn intent_effect_coverage_gaps_are_red_unless_registered() {
    let intent = |name: &str| {
        format!("(defeffect {} \"{}\" {{:fields [amount] :answer int :tags {{:context \"billing\" :role \"intent\"}}}})\n", name, name)
    };
    // Silent example: Settle has all 3 columns filled — a deftest emits it, and the translation layer's (here entry) settle-reads is reached
    // from both the simulation root (a file of the assembly layer) and the production entry (production-handlers of the assembly file).
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
        .replace("(defservice billing \"Billing\" {:layers [core entry]})", "(defservice billing \"Billing\" {:layers [core intent entry]})");
    std::fs::write(&arch_path, text).unwrap();
    let pyproject = dir.path().join("pyproject.toml");
    let settings = std::fs::read_to_string(&pyproject).unwrap() + "[tool.doeff-linter.registry]\ndirs = [\"registry/EFFECT-COVERAGE-BREACHES\"]\n";
    std::fs::write(&pyproject, settings).unwrap();
    let registry = dir.path().join("registry/EFFECT-COVERAGE-BREACHES");
    std::fs::create_dir_all(&registry).unwrap();
    std::fs::write(
        registry.join("refund.txt"),
        "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Refund\nA known gap (a test sample)\n",
    )
    .unwrap();
    let (_, report) = editor(dir.path());
    let charge = "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Charge";
    let refund = "app/billing/intent/effects.hy::DOEFF165::billing::app.billing.intent.effects.Refund";
    // Exact match: Settle, with 3 filled columns, does not appear.
    assert_eq!(keys(&report, "DOEFF165"), vec![charge, refund], "{}", report);
    let new_gap = violation(&report, charge);
    assert_eq!(new_gap["severity"], "error", "{}", new_gap);
    assert_eq!(new_gap["level"], "critical", "{}", new_gap);
    let known = violation(&report, refund);
    assert_eq!(known["severity"], "warning", "{}", known);
    assert_eq!(known["registered"], true, "{}", known);
}

/// A temporary repo with the DOEFF206 declarations (:verification-environment "sim", the 4 tables of :business-fakes, the intent layer of :assembly-shape).
/// The billing service has layer intent, the effects Charge, Refund, Settle, and Ship are layer intent, and Now is a foundation effect.
fn intent_fake_repo(files: &[(&str, String)], registry: Option<(&str, &str)>) -> tempfile::TempDir {
    intent_fake_repo_enabling(files, registry, "[\"DOEFF206\"]")
}

/// The same repo as `intent_fake_repo`, with the rules to apply selected by `enable`.
fn intent_fake_repo_enabling(files: &[(&str, String)], registry: Option<(&str, &str)>, enable: &str) -> tempfile::TempDir {
    let intent = |name: &str| format!("(defeffect {} \"{}\" {{:fields [amount] :answer int :tags {{:context \"billing\" :role \"intent\"}}}})\n", name, name);
    let mut all: Vec<(&str, String)> = vec![
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/foundation/clock_effects.hy", tags("shared", "foundation") + "(defeffect Now \"Clock\" {:fields [] :answer int :tags {:context \"shared\" :role \"foundation\"}})\n"),
        ("app/billing/intent/effects.hy", intent("Charge") + &intent("Refund") + &intent("Settle") + &intent("Ship")),
    ];
    all.extend(files.iter().cloned());
    let dir = world_repo_with(&all, "", enable);
    let arch_path = dir.path().join("architecture.hy");
    let declarations = ":foundation foundation\n  :verification-environment \"sim\"\n  \
                        :business-fakes {:simulation [\"app/sim/**\"] :assembly [\"app/*/entry/**\"] :tests [\"**/tests/**\"] \
                        :production [\"app/**\"] :business-modules [\"app.billing\"] \
                        :external-effects \"tables/EXTERNAL-EFFECTS\" :counterexamples \"tables/COUNTEREXAMPLES\" \
                        :lower-layer-passages \"tables/LOWER-LAYER-PASSAGES\" :test-only-fakes \"tables/TEST-ONLY-FAKES\"}\n  \
                        :assembly-shape {:translation-point \"with-*-translation\" :retired-function \"handlers-of\" \
                        :translations \"TRANSLATION-HANDLERS\" :translation-layer \"entry\" :intent-layer \"intent\"}";
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", declarations)
        .replace("(layer entry", "(layer intent :roles [intent] :imports [core intent])\n           (layer entry")
        .replace("(defservice billing \"Billing\" {:layers [core entry]})", "(defservice billing \"Billing\" {:layers [core intent entry]})");
    std::fs::write(&arch_path, text).unwrap();
    for table in ["EXTERNAL-EFFECTS", "COUNTEREXAMPLES", "LOWER-LAYER-PASSAGES", "TEST-ONLY-FAKES"] {
        std::fs::create_dir_all(dir.path().join("tables").join(table)).unwrap();
    }
    if let Some((name, key)) = registry {
        let pyproject = dir.path().join("pyproject.toml");
        let settings = std::fs::read_to_string(&pyproject).unwrap() + "[tool.doeff-linter.registry]\ndirs = [\"registry/BREACHES\"]\n";
        std::fs::write(&pyproject, settings).unwrap();
        let registry = dir.path().join("registry/BREACHES");
        std::fs::create_dir_all(&registry).unwrap();
        std::fs::write(registry.join(name), format!("{}\nTry listing it as a known hit (a sample showing it is not lowered)\n", key)).unwrap();
    }
    dir
}

/// DOEFF206 (a): if a row of a table that lets violations through (the external-world table, the table passing lower layers, the test-only fakes table) passes an intent-layer effect,
/// critical at that row's file position. A row passing a foundation effect does not fire.
#[test]
fn tables_passing_intent_effects_are_critical_at_the_row() {
    let files = [
        ("tables/EXTERNAL-EFFECTS/charge.txt", "app.billing.intent.effects.Charge\nAn outside party (an intent effect claimed as such)\n".to_string()),
        ("tables/EXTERNAL-EFFECTS/now.txt", "app.foundation.clock_effects.Now\nClock (a foundation effect — does not fire)\n".to_string()),
        (
            "tables/LOWER-LAYER-PASSAGES/refund.txt",
            "app/sim/route.hy::route-refund::app.billing.intent.effects.Refund\nRouting (an intent effect — fires)\n".to_string(),
        ),
        (
            "tables/TEST-ONLY-FAKES/settle.txt",
            "app/billing/tests/test_settle.hy::fake-settle::app.billing.intent.effects.Settle\nTest-only fake (an intent effect — fires)\n".to_string(),
        ),
        ("tables/TEST-ONLY-FAKES/now.txt", "app/billing/tests/test_clock.hy::fake-now::app.foundation.clock_effects.Now\nA foundation effect (does not fire)\n".to_string()),
    ];
    let dir = intent_fake_repo(&files, None);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    let external = "tables/EXTERNAL-EFFECTS/charge.txt::DOEFF206::pass:app.billing.intent.effects.Charge";
    let lower = "tables/LOWER-LAYER-PASSAGES/refund.txt::DOEFF206::pass:app/sim/route.hy::route-refund::app.billing.intent.effects.Refund";
    let test_only = "tables/TEST-ONLY-FAKES/settle.txt::DOEFF206::pass:app/billing/tests/test_settle.hy::fake-settle::app.billing.intent.effects.Settle";
    assert_eq!(keys(&report, "DOEFF206"), vec![external, lower, test_only], "{}", report);
    for key in [external, lower, test_only] {
        let found = violation(&report, key);
        assert_eq!(found["severity"], "error", "{}", found);
        assert_eq!(found["level"], "critical", "{}", found);
    }
    assert!(violation(&report, external)["message"].as_str().unwrap().contains("passes the intent-layer effect app.billing.intent.effects.Charge"), "{}", report);
}

/// DOEFF206 (b): critical if a handler in the verification-environment dir answers an intent-layer effect on its own.
/// Silent examples: a tap that resumes the re-performed answer as is (including re-performing under an alias of the received effect), a broken clause whose key is in the counterexample table, a clause answering a foundation effect, and the same clause outside the verification environment (the production side).
/// Firing examples: its own value, resuming with a changed re-performed answer, and a clause that passes its own value in one branch.
#[test]
fn verification_handlers_answering_intent_effects_are_critical_except_pass_through_taps() {
    let files = [
        (
            "app/sim/fake_billing.hy",
            "(import app.billing.intent.effects [Charge Refund Settle Ship])\n(import app.foundation.clock_effects [Now])\n\
             (defhandler fake-charge (Charge [amount] (resume 0)))\n\
             (defhandler watch-refund (Refund [amount] (<- answer int effect) (resume answer)))\n\
             (defhandler watch-settle (Settle [amount] (<- answer (Settle amount)) (resume answer)))\n\
             (defhandler alias-charge (Charge [amount] (val request effect) (<- answer request) (resume answer)))\n\
             (defhandler bend-settle (Settle [amount] (<- answer (Settle amount)) (resume (+ answer 1))))\n\
             (defhandler swap-refund (Refund [amount] (<- answer effect) (<- other (Refund 1)) (resume other)))\n\
             (defhandler maybe-refund (Refund [amount] (if (> amount 9) (resume 0) (do (<- answer effect) (resume answer)))))\n\
             (defhandler broken-ship (Ship [amount] (resume 0)))\n\
             (defhandler sim-clock (Now [] (resume 0)))\n"
                .to_string(),
        ),
        (
            "app/billing/entry/translate.hy",
            tags("billing", "entry") + "(import app.billing.intent.effects [Charge])\n(defhandler charge-reads (Charge [amount] (resume 0)))\n",
        ),
        ("tables/COUNTEREXAMPLES/ship.txt", "app/sim/fake_billing.hy::broken-ship::app.billing.intent.effects.Ship\nA broken handler that returns 0 for a shipment\n".to_string()),
    ];
    let dir = intent_fake_repo(&files, None);
    let (_, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    let key = |handler: &str, effect: &str| format!("app/sim/fake_billing.hy::DOEFF206::verification:{}::app.billing.intent.effects.{}", handler, effect);
    let mut expected = vec![key("bend-settle", "Settle"), key("fake-charge", "Charge"), key("maybe-refund", "Refund")];
    expected.push(key("swap-refund", "Refund"));
    expected.sort();
    assert_eq!(keys(&report, "DOEFF206"), expected, "{}", report);
    let found = violation(&report, &key("fake-charge", "Charge"));
    assert_eq!(found["level"], "critical", "{}", found);
    assert!(found["message"].as_str().unwrap().contains("by itself"), "{}", found);
}

/// Failure case: merely replacing the effect's import spelling with an entry that re-exports from the defining module (the root of `app.billing` —
/// the `X as X` under TYPE_CHECKING and the `__getattr__` name -> module table) leaves the counterexample table row (keyed by the defining spelling) still hitting, and
/// the layer judgment (whether it is an intent effect) does not change either. An implementation keyed by spelling would fail to match broken-ship's entry spelling to the table and give an old row (DOEFF176) and a
/// DOEFF206 hit, and the entry-spelled Charge would not be read as intent, so fake-charge's DOEFF206 would vanish.
#[test]
fn reimporting_an_effect_through_a_reexporting_entry_keeps_table_rows_and_layers() {
    let entry = "from importlib import import_module\nfrom typing import TYPE_CHECKING\n\nif TYPE_CHECKING:\n    \
                 from app.billing.intent.effects import Charge as Charge\n\n\
                 _LAZY_EXPORTS = {\"Charge\": \"app.billing.intent.effects\", \"Ship\": \"app.billing.intent.effects\"}\n\n\n\
                 def __getattr__(name):\n    return getattr(import_module(_LAZY_EXPORTS[name]), name)\n";
    for spelled in ["app.billing.intent.effects", "app.billing"] {
        let files = [
            ("app/billing/__init__.py", entry.to_string()),
            (
                "app/sim/fake_billing.hy",
                format!(
                    "(import {} [Charge Ship])\n(defhandler fake-charge (Charge [amount] (resume 0)))\n(defhandler broken-ship (Ship [amount] (resume 0)))\n",
                    spelled
                ),
            ),
            ("tables/COUNTEREXAMPLES/ship.txt", "app/sim/fake_billing.hy::broken-ship::app.billing.intent.effects.Ship\nA broken handler that returns 0 for a shipment\n".to_string()),
        ];
        let dir = intent_fake_repo_enabling(&files, None, "[\"DOEFF143\", \"DOEFF176\", \"DOEFF206\"]");
        let (_, report) = editor(dir.path());
        assert_eq!(report["errors"], serde_json::json!([]), "{}: {}", spelled, report["errors"]);
        // The notice detail stays as the clause's written spelling. Reading it as intent (fake-charge fires) and hitting the table row (broken-ship does not fire) do not depend on the spelling.
        assert_eq!(
            keys(&report, "DOEFF206"),
            vec![format!("app/sim/fake_billing.hy::DOEFF206::verification:fake-charge::{}.Charge", spelled)],
            "{}: {}",
            spelled,
            report
        );
        let fakes = keys(&report, "DOEFF143");
        assert!(fakes.iter().all(|k| !k.contains("broken-ship")), "{}: {:?}", spelled, fakes);
        assert_eq!(keys(&report, "DOEFF176"), Vec::<String>::new(), "{}: {}", spelled, report);
    }
}

/// An effect name that cannot be resolved (the defining module's file is missing) is matched by its spelling, and the failure to resolve is put in errors (it does not silently fall back to the spelling).
#[test]
fn an_effect_whose_origin_cannot_be_traced_is_reported() {
    let files = [(
        "app/sim/fake_billing.hy",
        "(import app.gone.effects [Charge])\n(defhandler fake-charge (Charge [amount] (resume 0)))\n".to_string(),
    )];
    let dir = intent_fake_repo(&files, None);
    let (_, report) = editor(dir.path());
    let errors: Vec<&str> = report["errors"].as_array().unwrap().iter().filter_map(Value::as_str).collect();
    assert!(
        errors.iter().any(|e| e.contains("app.gone.effects.Charge") && e.contains("cannot resolve the defining origin") && e.contains("fake-charge")),
        "{:?}",
        errors
    );
}

/// DOEFF206: even if a key is listed in the registry (the table of known hits), DOEFF206's hits are not lowered — error, critical, not registered.
#[test]
fn intent_fakes_in_verification_are_not_lowered_by_the_registry() {
    let files = [(
        "app/sim/fake_billing.hy",
        "(import app.billing.intent.effects [Charge])\n(defhandler fake-charge (Charge [amount] (resume 0)))\n".to_string(),
    )];
    let key = "app/sim/fake_billing.hy::DOEFF206::verification:fake-charge::app.billing.intent.effects.Charge";
    let dir = intent_fake_repo(&files, Some(("charge.txt", key)));
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF206"), vec![key], "{}", report);
    let found = violation(&report, key);
    assert_eq!(found["severity"], "error", "{}", found);
    assert_eq!(found["level"], "critical", "{}", found);
    assert_eq!(found["registered"], false, "{}", found);
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

/// C11: each word decided not to be used hits once per word (counterexample = one per word), and part of another word, a line that states the rule, a file outside the declaration, and a
/// file in :except do not hit. A semantic spelling is a regular expression, and the group of definition names looks only at definition names. New hits are critical.
#[test]
fn retired_words_hit_once_per_word_and_skip_rule_lines() {
    let words = ["席", "mailbox", "letter", "auth home", "mail", "掃引"];
    let mut files: Vec<(String, String)> = words.iter().enumerate().map(|(i, w)| (format!("app/billing/w{}.hy", i), format!(";; heading\n(setv note \"write {} in the body\")\n", w))).collect();
    files.extend([
        ("README.md".to_string(), "郵便は Message と書く。email address と mail-box は別の語。\n使わない語 = 席 / mail\n".to_string()),
        ("docs/README.md".to_string(), "mail is not the root README, so it is outside the declaration\n".to_string()),
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
    // The semantic spelling hits 2 lines (the Japanese form and the English form), and both become keys of the group name.
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
    assert!(mail["message"].as_str().unwrap().contains("retired spelling mail (group vocabulary, instead:"), "{}", mail["message"]);
    let names = violation(&report, "app/chat/rows.hy::DOEFF150::conversation-names");
    assert!(names["message"].as_str().unwrap().contains("definition name ConversationSlice"), "{}", names["message"]);
}

/// `:in lines` counts only spellings in code that is actually used — Hy symbols, field names, command
/// strings, other strings, and Python and shell code fire; comments (Hy `;`, Python and shell `#`), definition docstrings, and `.md` bodies do not.
#[test]
fn retired_words_count_code_not_comments_docstrings_or_markdown() {
    let dir = retired_repo(&[
        ("app/billing/symbol.hy", ";; comment on mail\n(setv mail 1)\n".to_string()),
        ("app/billing/field.hy", "(defrecord Row (#^ str letter))\n".to_string()),
        ("app/billing/command.hy", "(defk run [] (RunProcess \"cd x && mail -s hi\"))\n".to_string()),
        ("app/billing/code.py", "\"\"\"the mail module\"\"\"\nimport os  # mail is a comment\nsend = os.environ[\"mail\"]\n".to_string()),
        ("app/billing/run.sh", "# comment on mail\nexec mail -s hi\n".to_string()),
        (
            "app/billing/quiet.hy",
            ";;; head comment on mail\n(defk send [x]\n  \"to send mail\"\n  x) ; letter is a comment\n(defclass Box [] \"the mail box\" (setv n 1))\n\
             (setv note \"email address and mail-box are different words\")\n(setv rule \"mail\") ; 使わない語の例\n"
                .to_string(),
        ),
        ("app/billing/prose.md", "# mail\nmail and letter in the body\n```sh\nexec mail -s hi\n```\n".to_string()),
        ("app/billing/quiet.py", "def f():\n    '''function of mail'''\n    return 1  # letter\n".to_string()),
        ("app/billing/quiet.sh", "#!/bin/sh\n# comment on mail\necho $# ${#x}  # letter\n".to_string()),
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
    assert_eq!(symbol["range"]["start"]["line"], 1, "the line of code, not of the comment: {}", symbol);
}

/// A `:in paths` group looks only at the file name (before the last `.`) — a file with a retired name hits once per word; contents, dir names, and
/// part of another word do not hit. :rule-lines takes effect only for :in lines, so combining it with :in paths is a declaration problem.
#[test]
fn retired_paths_hit_the_file_name_only() {
    let dir = world_repo_with(
        &[
            ("app/operators/worker.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/design_request.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/worker_pool.hy", "(defk run [] 1)\n".to_string()),
            ("app/operators/probe.hy", "(setv worker 1) ; worker is content only\n".to_string()),
            ("app/worker/probe.hy", "(defk run [] 1)\n".to_string()),
        ],
        "",
        "[\"DOEFF150\"]",
    );
    let arch_path = dir.path().join("architecture.hy");
    let declarations = r#":foundation foundation
  :retired-words [(retired-words "retired-operators" :words ["worker" "design_request"] :files ["app/operators/*.hy"]
                    :in paths :instead "do not re-place retired operator files")]"#;
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
    assert!(worker["message"].as_str().unwrap().contains("retired spelling worker in a file name"), "{}", worker["message"]);
    assert_eq!(worker["level"], "critical", "{}", worker);
    std::fs::write(&arch_path, text.replace(":in paths", ":in paths :rule-lines [\"do not use\"]")).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--no-log"], None);
    assert!(stderr.contains(":rule-lines of retired-words"), "{}", stderr);
    assert_ne!(code, 0, "a declaration error stops without running");
}

/// C11: a retired call hits once per call (counterexample = one per call), and comments, strings, discarded forms, test files in :except,
/// and places that merely name it as a value do not hit.
#[test]
fn retired_calls_hit_once_per_call() {
    let calls = ["Now", "Elapsed", "EpochMillis", "ReadClock", "time.time"];
    let mut files: Vec<(String, String)> = calls.iter().enumerate().map(|(i, c)| (format!("app/billing/c{}.hy", i), format!("(defk stamp [] ({}))\n", c))).collect();
    files.extend([
        (
            "app/billing/quiet.hy".to_string(),
            ";; the effects (Now) and (Elapsed) are retired (elapsed time is (GetMonotonic))\n(setv note \"(time.time)\")\n#_(Now)\n(setv clock Now)\n(defk ok [] (GetMonotonic))\n"
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
    assert_eq!(now["range"]["start"]["character"], 16, "the position is the head symbol of the call: {}", now);
}

/// Condition: a rule judged per file reads only the files under the named path if one is given (it does not read the whole repo).
/// A whole run reads all the declared files — whether an unreadable file (not UTF-8) appears in the reasons distinguishes the population of files read.
#[test]
fn retired_rules_read_only_the_named_files() {
    let dir = retired_repo(&[("app/billing/named.hy", "(setv note \"send mail\")\n".to_string())]);
    std::fs::write(dir.path().join("app/billing/broken.md"), [0xffu8, 0xfe, b'\n']).unwrap();
    let (_, whole_out, whole_err) = run(dir.path(), &["--no-log"], None);
    assert!(whole_err.contains("app/billing/broken.md: cannot read"), "a whole run reads all declared files: {}\n{}", whole_err, whole_out);
    let (code, named_out, named_err) = run(dir.path(), &["--no-log", "app/billing/named.hy"], None);
    assert!(!named_err.contains("broken.md"), "a named run reads only the named file: {}", named_err);
    assert!(named_out.contains("DOEFF150") && named_out.contains("named.hy"), "a hit in the named file appears: {}\n{}", named_out, named_err);
    assert_ne!(code, 0, "a new hit is an error");
}

/// Misreads of a declaration (an unreadable regular expression, a backslash in an ordinary string, no :files, a word outside :in) become config errors with a position.
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
    assert!(all.contains("cannot read the regular expression ( of retired-words a"), "{}", all);
    assert!(all.contains("the regular expression of :patterns must be written as an r\"…\" string"), "{}", all);
    assert!(all.contains("retired-words c has no :files"), "{}", all);
    assert!(all.contains(":in must be lines, names, or paths"), "{}", all);
}

/// Contract file (the wire field carrier.mail and the stream-kind enum value mail — the shape of agora-controllers' agora-kinds.json and record-service.json).
/// letter appears nowhere (a spelling not in the contract).
const WIRE_CONTRACT: &str = r#"{"comment": "explanatory sentences about letter and mail are not collected",
  "kinds": {"conversation-input": {"schema": {"properties": {"status": {"properties": {"carrier": {"properties": {"mail": {"type": "string"}, "job": {"type": "string"}}}}}}}}},
  "$defs": {"storedEvent": {"properties": {"streamKind": {"enum": ["chat", "mail"]}}}},
  "examples": [{"note": "letter"}]}"#;

/// A temporary repo for the group that writes DOEFF150's :contract-files (and the DOEFF151 call group). contract_files is the contents of :contract-files, and contracts is
/// the contract files to place (path from the root and contents).
fn contract_repo(files: &[(&str, String)], contract_files: &str, contracts: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = world_repo_with(files, "", "[\"DOEFF150\", \"DOEFF151\"]");
    let arch_path = dir.path().join("architecture.hy");
    let declarations = format!(
        r#":foundation foundation
  :retired-words [(retired-words "message" :words ["mail" "letter"] :files ["app/**/*.hy" "app/**/*.py"]
                    :contract-files [{}] :instead "Message")]
  :retired-calls [(retired-calls "clock" :calls ["Now"] :files ["app/**/*.hy"] :instead "(GetTime)")]"#,
        contract_files
    );
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(":foundation foundation", &declarations);
    std::fs::write(&arch_path, text).unwrap();
    for (rel, text) in contracts {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Plan 2: words in the contract's spellings (key names, enum values) in :contract-files are not counted only when they are a Hy string whose contents are exactly that word,
/// the name of a defwire field definition, or a Python string whose contents are exactly that word. Internal names of the same spelling (variables, defrecord fields, loop
/// variables, Python names) and strings of a word not in the contract (letter) do hit. The hint does not recommend the registry, and makes the reader check the contract file.
#[test]
fn retired_words_skip_contract_strings_and_wire_fields_only() {
    let dir = contract_repo(
        &[
            ("app/billing/wire.hy", "(defwire InputCarrier \"taken mark\" {:names :camel} (#^ str job) (#^ str mail))\n".to_string()),
            ("app/billing/seen.hy", "(defwire InputSeen {:unknown :ignore} (setv #^ (| str None) mail None))\n".to_string()),
            ("app/billing/stream.hy", "(defk append [record] (.append record \"c\" \"id\" [] \"mail\"))\n".to_string()),
            ("app/billing/inner.hy", "(setv mail 1)\n".to_string()),
            ("app/billing/record.hy", "(defrecord Row (#^ str mail))\n".to_string()),
            ("app/billing/loop.hy", "(setv ids (lfor mail rows mail.id))\n".to_string()),
            ("app/billing/letter.hy", "(setv kind \"letter\")\n".to_string()),
            ("app/billing/vocab.py", "MESSAGE_SOURCE_MAIL = \"mail\"\nINPUT_CARRIER_MAIL = 'mail'\nmail = 1\n".to_string()),
            ("app/billing/clock.hy", "(defk stamp [] (Now))\n".to_string()),
        ],
        "\"docs/contracts/wire.json\"",
        &[("docs/contracts/wire.json", WIRE_CONTRACT)],
    );
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF150"),
        vec![
            "app/billing/inner.hy::DOEFF150::mail",
            "app/billing/letter.hy::DOEFF150::letter",
            "app/billing/loop.hy::DOEFF150::mail",
            "app/billing/record.hy::DOEFF150::mail",
            "app/billing/vocab.py::DOEFF150::mail",
        ],
        "{}",
        report
    );
    let python = violation(&report, "app/billing/vocab.py::DOEFF150::mail");
    assert_eq!(python["range"]["start"]["line"], 2, "the line of the internal name, not of the 2-line contract value string: {}", python);
    for key in ["app/billing/inner.hy::DOEFF150::mail", "app/billing/clock.hy::DOEFF151::Now"] {
        let hint = violation(&report, key)["hint"].as_str().unwrap().to_string();
        assert!(!hint.contains("registry"), "the hint recommends the registry: {}", hint);
        assert!(!hint.contains(":rule-lines"), "the hint invites adding marks: {}", hint);
    }
    assert!(violation(&report, "app/billing/inner.hy::DOEFF150::mail")["hint"].as_str().unwrap().contains(":contract-files"));
}

/// When a :contract-files file is missing, is not JSON, has no contract spelling at all, or is written for a group that is not :in lines, it does not silently become empty
/// but becomes a declaration error with a position and stops without running.
#[test]
fn contract_files_misreadings_are_config_errors() {
    let dir = contract_repo(
        &[("app/billing/inner.hy", "(setv mail 1)\n".to_string())],
        "\"docs/contracts/missing.json\" \"docs/contracts/broken.json\" \"docs/contracts/empty.json\" \"../outside.json\"",
        &[("docs/contracts/broken.json", "{\"mail\": "), ("docs/contracts/empty.json", "[1, 2]")],
    );
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_eq!(code, 2, "a declaration error stops without running: {}", all);
    for needle in [
        "docs/contracts/missing.json (",
        "cannot be read:",
        "docs/contracts/broken.json (",
        "cannot be read as JSON",
        "docs/contracts/empty.json in :contract-files of retired-words message has no contract spelling at all (key names, enum / const values)",
        "../outside.json in :contract-files of retired-words message must be a path from the repo root",
    ] {
        assert!(all.contains(needle), "{} is missing:\n{}", needle, all);
    }
    assert!(!all.contains("DOEFF150"), "it was not run: {}", all);
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap();
    let names = text
        .replace("\"docs/contracts/missing.json\" \"docs/contracts/broken.json\" \"docs/contracts/empty.json\" \"../outside.json\"", "\"docs/contracts/wire.json\"")
        .replace(":instead \"Message\")", ":in names :instead \"Message\")");
    std::fs::write(&arch_path, names).unwrap();
    std::fs::write(dir.path().join("docs/contracts/wire.json"), WIRE_CONTRACT).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    assert_eq!(code, 2, "{}{}", stdout, stderr);
    assert!(stderr.contains(":contract-files of retired-words message is effective only with :in lines"), "{}", stderr);
    std::fs::write(&arch_path, text.replace(":contract-files [\"docs/contracts/missing.json\" \"docs/contracts/broken.json\" \"docs/contracts/empty.json\" \"../outside.json\"]", ":contract-files []")).unwrap();
    let (code, _, stderr) = run(dir.path(), &["--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains(":contract-files of retired-words message is empty"), "{}", stderr);
}

/// A temporary repo with the DOEFF144 and 145 declarations added to architecture.hy (the globs of the files to read have the same shape as agora-controllers' declarations).
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

/// A plain mapping or plain tuple in a public type annotation hits once per place, by 4 kinds of keys: field, return value, :post, and answer tuple.
/// Internal names, nested functions, files in :except, and files outside the declaration do not hit. The fixed shape (a record with field names and types) emits nothing.
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
    assert_ne!(code, 0, "a new hit is an error");
    let decide = violation(&report, "app/core/bad.hy::DOEFF144::post:decide");
    assert_eq!(decide["level"], "critical", "{}", decide);
    assert!(decide["message"].as_str().unwrap().contains("answer (:post, no annotation on the name) decide has type bare tuple tuple"), "{}", decide["message"]);
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report);
}

/// A .pyi that declares a kw-only record of a .hy with @dataclass lacking kw_only=True hits once per class. A fixed .pyi,
/// a .pyi with no .hy of the same name, and a class without @dataclass do not hit.
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
    assert_eq!(row["range"]["start"]["line"], 1, "the position is the @dataclass decorator: {}", row);
    // The fixed shape emits nothing.
    std::fs::write(dir.path().join("app/core/rows.pyi"), stub.replace("@dataclass(frozen=True)\nclass Row", "@dataclass(frozen=True, kw_only=True)\nclass Row").replace("@dataclass\nclass Manual", "@dataclass(kw_only=True)\nclass Manual")).unwrap();
    let (_, fixed) = editor(dir.path());
    assert!(keys(&fixed, "DOEFF145").is_empty(), "{}", fixed);
}

/// Condition: DOEFF144 and 145 are judged per file, so if a named path is given they read only the files under it (they do not read the whole
/// repo). A whole run reads all the declared files — whether an unreadable file (not UTF-8) appears in the reasons distinguishes the population read.
#[test]
fn typed_value_rules_read_only_the_named_files() {
    let dir = typed_repo(&[("app/core/named.hy", "(defn #^ dict load [] 1)\n".to_string())]);
    std::fs::write(dir.path().join("app/core/broken.hy"), [0xffu8, 0xfe, b'\n']).unwrap();
    std::fs::write(dir.path().join("app/core/broken.pyi"), [0xffu8, 0xfe, b'\n']).unwrap();
    let (_, whole_out, whole_err) = run(dir.path(), &["--no-log"], None);
    assert!(whole_err.contains("app/core/broken.hy: "), "a whole run reads all declared files: {}\n{}", whole_err, whole_out);
    assert!(whole_err.contains("app/core/broken.pyi: "), "a whole run reads all declared .pyi files: {}\n{}", whole_err, whole_out);
    let (code, named_out, named_err) = run(dir.path(), &["--no-log", "app/core/named.hy"], None);
    assert!(!named_err.contains("broken"), "a named run reads only the named file: {}", named_err);
    assert!(named_out.contains("DOEFF144") && named_out.contains("named.hy"), "a hit in the named file appears: {}\n{}", named_out, named_err);
    assert_ne!(code, 0, "a new hit is an error");
}

/// Misreads of :typed-values and :record-stubs (not a dictionary, no :files, an unknown key) become config errors with a position.
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
    assert!(all.contains(":typed-values must be a dictionary {:files [..] :except [..]}"), "{}", all);
    assert!(all.contains(":record-stubs has no :files (globs of the files to read)"), "{}", all);
}

/// Review finding (#1191): even a run that names only the .hy judges the adjacent .pyi of the same name (one matching the declaration's glob) and emits with the .pyi's key.
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

/// A pre-save single-file run (--stdin): the .hy contents come from stdin, the adjacent .pyi is read from disk, and the hit is emitted with the .pyi's path. DOEFF144 also
/// judges by the stdin contents (not the disk contents).
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
    // The .hy on disk is a defclass (not kw-only), so DOEFF145 does not hit in a whole run.
    let (_, whole) = editor(dir.path());
    assert!(keys(&whole, "DOEFF145").is_empty(), "{}", whole);
}

/// A .pyi matching :except of :record-stubs is not read.
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

/// DOEFF146: a file that reads the group's vocabulary (regular expressions) outside :except of :single-point-vocabulary is emitted
/// once per group, as critical. The :except file itself does not hit.
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
         :except [\"app/billing/glue/slice.hy\"] :instead \"read the answer of app/billing/glue/slice.hy\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF146"), vec!["app/billing/glue/queue.hy::DOEFF146::job-phase"], "{}", report);
    let hit = violation(&report, "app/billing/glue/queue.hy::DOEFF146::job-phase");
    assert!(hit["message"].as_str().unwrap().contains("2 lines"), "{}", hit["message"]);
    assert!(hit["message"].as_str().unwrap().contains("read the answer of app/billing/glue/slice.hy"), "{}", hit["message"]);
    assert_eq!(hit["range"]["start"]["line"], 1);
    assert_eq!(hit["level"], "critical");
}

/// If a required key of :single-point-vocabulary is missing, it is a read error (with a position).
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
    assert!(all.contains("vocabulary-scope a has no :patterns"), "{}", all);
    assert!(all.contains("vocabulary-scope a has no :files"), "{}", all);
    assert!(all.contains("vocabulary-scope a has no :except (the single point of judgment)"), "{}", all);
    assert!(all.contains("vocabulary-scope a has no :instead (how to fix it)"), "{}", all);
}

/// DOEFF148: if a file outside :except writes a spelling of :confined-spellings, it is emitted once per file and group,
/// as critical. Inside strings is counted (the verbs of outside ports, route spellings), and comments are not. Python files are read too. A group with no file matching :files is
/// reported as missing at the architecture.hy position.
#[test]
fn spelling_outside_its_files_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/protocol/peer.hy", "(defk send [x] (HttpRequest \"POST\" \"/api/rows\" x))\n".to_string()),
        (
            "app/billing/core/glue.hy",
            "; \"/api/intake\" was retired\n(setv a 1)\n(defk go [x] (HttpRequest \"GET\" \"/api/intake\" x))\n".to_string(),
        ),
        ("app/billing/entry/values.py", "# comment on /api/intake\nROUTE = \"/api/intake\"\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF148\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :confined-spellings [(confined-spelling \"http\" :patterns [r\"\\(HttpRequest\\s\"] \
         :files [\"app/billing/**\"] :except [\"app/billing/protocol/*.hy\"] :why \"the outside port is only layer protocol\") \
         (confined-spelling \"acp-intake\" :patterns [r\"/api/intake\"] :files [\"app/billing/**\"] :why \"the mail-drop receiver is the queue\") \
         (confined-spelling \"gone\" :patterns [r\"x\"] :files [\"app/gone/**\"] :why \"gone\")]",
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
    assert!(hit["message"].as_str().unwrap().contains("1 occurrence(s)"), "{}", hit["message"]);
    assert_eq!(hit["range"]["start"]["line"], 2);
    assert_eq!(hit["level"], "critical");
    let python = violation(&report, "app/billing/entry/values.py::DOEFF148::acp-intake");
    assert_eq!(python["range"]["start"]["line"], 1);
}

/// If a required key of :confined-spellings is missing, it is a read error (with a position). :except need not be written.
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
    assert!(all.contains("confined-spelling a has no :patterns"), "{}", all);
    assert!(all.contains("confined-spelling a has no :files"), "{}", all);
    assert!(all.contains("confined-spelling a has no :why (why only these files)"), "{}", all);
    assert!(!all.contains("confined-spelling a has no :except"), "{}", all);
    assert!(all.contains("cannot read the regular expression ( of confined-spelling b"), "{}", all);
}

/// A name bound to a group spelling is also a group spelling (:names-bound-in — the group web-push-private-key in agora-controllers kept the names of secret-key references as a copy of regular expressions, and
/// reading an old key reference name from key rotation in another file did not turn red).
/// Failure case: if core reads a new reference name added to the declaration's :environ as a string, reads it by a translation's val name, or reads it by a name Python mangled, or
/// reads it by a name in a list where a translation lined up the reference names, it turns red without rewriting architecture.hy. A group that holds the names as a copy (bill-key-copied) lets the same 4 through.
/// A name bound to a value that does not match a group spelling, and a name bound inside a function, are not counted.
#[test]
fn a_name_bound_to_a_confined_spelling_is_confined_too() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/system.hy",
            "(defsystem billing-system [foundation]\n  \"Billing system\"\n  (billing (billing-job foundation) :needs #{\"zeus\"} :replicas 1\n    \
             :environ {\"BILL_KEY_REF\" \"secret:ns/bill-vapid/private_key\"\n              \
             \"BILL_PREVIOUS_KEY_REF\" \"secret:ns/bill-vapid/previous_private_key\"\n              \
             \"BILL_CONTACT\" \"https://example.org\"}))\n"
                .to_string(),
        ),
        (
            "app/billing/protocol/keys.hy",
            "(val BILL-KEY-REF-ENV \"BILL_KEY_REF\")\n(val BILL-PREVIOUS-KEY-REF-ENV \"BILL_PREVIOUS_KEY_REF\")\n\
             (val BILL-KEY-REFS #(BILL-KEY-REF-ENV BILL-PREVIOUS-KEY-REF-ENV))\n\
             (val CONTACT-ENV \"BILL_CONTACT\")\n(defk read-key [] (val ref \"BILL_KEY_REF\") (supplied-setting BILL-KEY-REF-ENV))\n"
                .to_string(),
        ),
        (
            "app/billing/core/by_list.hy",
            "(import app.billing.protocol.keys [BILL-KEY-REFS])\n(defk read-all [] (lfor r BILL-KEY-REFS (supplied-setting r)))\n".to_string(),
        ),
        ("app/billing/core/previous.hy", "(defk read-previous [] (supplied-setting \"BILL_PREVIOUS_KEY_REF\"))\n".to_string()),
        (
            "app/billing/core/by_name.hy",
            "(import app.billing.protocol.keys [BILL-PREVIOUS-KEY-REF-ENV])\n(defk read-previous [] (supplied-setting BILL-PREVIOUS-KEY-REF-ENV))\n"
                .to_string(),
        ),
        ("app/billing/core/by_python.py", "from app.billing.protocol.keys import BILL_KEY_REF_ENV\n".to_string()),
        (
            "app/billing/core/contact.hy",
            "(defk contact [] (supplied-setting \"BILL_CONTACT\"))\n(defk doc [] \"BILL_KEY_REFERENCE is another name\")\n(defk mine [ref] ref)\n".to_string(),
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF148\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :confined-spellings [(confined-spelling \"bill-key\" :patterns [r\"bill-vapid\"] \
         :names-bound-in [\"app/billing/entry/system.hy\" \"app/billing/protocol/keys.hy\"] :files [\"app/billing/**\"] \
         :except [\"app/billing/entry/system.hy\" \"app/billing/protocol/keys.hy\"] :why \"only keys.hy reads the secret key\") \
         (confined-spelling \"bill-key-copied\" :patterns [r\"bill-vapid\" r\"\\bBILL_KEY_REF\\b\" r\"\\bBILL-KEY-REF-ENV\\b\"] \
         :files [\"app/billing/**\"] :except [\"app/billing/entry/system.hy\" \"app/billing/protocol/keys.hy\"] :why \"copy\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF148"),
        vec![
            "app/billing/core/by_list.hy::DOEFF148::bill-key",
            "app/billing/core/by_name.hy::DOEFF148::bill-key",
            "app/billing/core/by_python.py::DOEFF148::bill-key",
            "app/billing/core/previous.hy::DOEFF148::bill-key",
        ],
        "{}",
        report
    );
    let hit = violation(&report, "app/billing/core/by_name.hy::DOEFF148::bill-key");
    let message = hit["message"].as_str().unwrap();
    assert!(message.contains("2 occurrence(s) outside the files allowed to write it (first match BILL-PREVIOUS-KEY-REF-ENV)"), "{}", message);
    let reason = hit["explanation"]["reason"].as_str().unwrap();
    assert!(reason.contains("derived names: BILL_KEY_REF, BILL_PREVIOUS_KEY_REF, BILL-KEY-REF-ENV, BILL-PREVIOUS-KEY-REF-ENV, BILL-KEY-REFS"), "{}", reason);
}

/// A :names-bound-in file is a readable Hy file where the group's spelling may be written (matches :except), and each file has at least one name bound to a value matching a group spelling
/// (so a file whose constant was moved into a function does not silently drop out of the group — a review after the blind test). If violated, a position-attached read error.
#[test]
fn names_bound_in_must_be_hy_files_the_group_lets_write() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        ("app/billing/core/other.hy", "(val OTHER \"bill-vapid\")\n".to_string()),
        ("app/billing/protocol/keyed.hy", "(val KEY-REF \"secret:ns/bill-vapid/k\")\n".to_string()),
        ("app/billing/protocol/plain.hy", "(val CONTACT-ENV \"BILL_CONTACT\")\n(defk read [] (val moved \"secret:ns/bill-vapid/k\") moved)\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF148\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :confined-spellings [(confined-spelling \"a\" :patterns [r\"bill-vapid\"] \
         :names-bound-in [\"app/billing/core/other.hy\" \"app/billing/protocol/gone.hy\" \"/abs.hy\"] :files [\"app/billing/**\"] \
         :except [\"app/billing/protocol/*.hy\"] :why \"y\") \
         (confined-spelling \"b\" :patterns [r\"bill-vapid\"] :names-bound-in [\"app/billing/protocol/keyed.hy\" \"app/billing/protocol/plain.hy\"] \
         :files [\"app/billing/**\"] :except [\"app/billing/protocol/*.hy\"] :why \"y\") \
         (confined-spelling \"c\" :patterns [r\"bill-vapid\"] :names-bound-in [] :files [\"app/billing/**\"] :except [\"app/billing/protocol/*.hy\"] :why \"y\")]",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("app/billing/core/other.hy in :names-bound-in of confined-spelling a does not match :except"), "{}", all);
    assert!(all.contains("cannot read app/billing/protocol/gone.hy"), "{}", all);
    assert!(all.contains("/abs.hy in :names-bound-in of confined-spelling a must be the path of a Hy file from the repo root"), "{}", all);
    assert!(all.contains("app/billing/protocol/plain.hy in :names-bound-in of confined-spelling b has no name bound to a value matching the group's spelling"), "{}", all);
    assert!(!all.contains("app/billing/protocol/keyed.hy in :names-bound-in of confined-spelling b"), "{}", all);
    assert!(all.contains(":names-bound-in of confined-spelling c is empty"), "{}", all);
}

/// DOEFF161: if the number of hits of :counted-spellings does not match :count / :at-least, it is emitted once, as critical.
/// :within counts per named definition, and a missing definition is missing. Hy comments are not counted, and yaml is read as is regardless of extension.
#[test]
fn spelling_count_differs_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/protocol/peer.hy",
            "(defk send [x] (Req \"POST\" x)) ; \"POST\" is a comment\n\n(defk read [x] (Req \"POST\" (Req \"POST\" x)))\n".to_string(),
        ),
        ("app/billing/rules.yaml", "rules:\n  - id: one\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF161\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :counted-spellings [(counted-spelling \"posts\" :pattern r\"\\x22POST\\x22\" \
         :files [\"app/billing/protocol/peer.hy\"] :count 2 :why \"two writes\") \
         (counted-spelling \"seats\" :pattern r\"\\x22POST\\x22\" :files [\"app/billing/protocol/peer.hy\"] \
         :within [\"send\" \"read\" \"gone\"] :count 1 :why \"one per seat\") \
         (counted-spelling \"rule-one\" :pattern r\"id: one\" :files [\"app/billing/rules.yaml\"] :at-least 1 :why \"the rule exists\") \
         (counted-spelling \"rule-two\" :pattern r\"id: two\" :files [\"app/billing/rules.yaml\"] :at-least 1 :why \"the rule exists\")]",
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
    assert!(posts["message"].as_str().unwrap().contains("3 occurrence(s) ("), "{}", posts["message"]);
    assert_eq!(posts["level"], "critical");
    let seat = violation(&report, "app/billing/protocol/peer.hy::DOEFF161::seats:read");
    assert_eq!(seat["range"]["start"]["line"], 2);
}

/// If a required key of :counted-spellings is missing, it is a read error. Exactly one of :count and :at-least.
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
    assert!(all.contains("counted-spelling a has no :pattern"), "{}", all);
    assert!(all.contains("counted-spelling a has no :files"), "{}", all);
    assert!(all.contains("counted-spelling a must have exactly one of :count or :at-least"), "{}", all);
    assert!(all.contains("counted-spelling a has no :why (why this count)"), "{}", all);
    assert!(all.contains("counted-spelling b must have exactly one of :count or :at-least"), "{}", all);
    assert!(all.contains(":count of counted-spelling c must be an integer of 0 or more"), "{}", all);
}

/// DOEFF149: if a class having a field of the :field-holders type differs from :holders, a holder outside the list is emitted at the class position as critical, and a listed class
/// that lacks the field and a class not in :classes are emitted at the architecture.hy position as critical. Docstrings, method bodies, and strings of default values are not counted.
#[test]
fn field_holders_differ_is_red() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/types.py",
            "class RecordCache:\n    \"\"\"RecordCache is the container for the body.\"\"\"\n    rows: tuple\n\n\
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
         :holders [\"Sent\" \"Page\"] :why \"one container\") \
         (field-holders \"state\" :type \"Record\" :files [\"app/billing/core/*.py\"] :classes [\"State\" \"Gone\"] :holders [] :why \"state does not hold the body\")]",
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
    assert!(sneaky["message"].as_str().unwrap().contains("class Sneaky has fields of RecordCache"), "{}", sneaky["message"]);
    assert_eq!(sneaky["level"], "critical");
    assert_eq!(sneaky["range"]["start"]["line"], 7);
}

/// If a required key of :field-holders is missing, it is a read error. :holders may be an empty list but must be written. If :classes is written, :holders are names within it.
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
    assert!(all.contains("field-holders a has no :type (the type spelling to count)"), "{}", all);
    assert!(all.contains("field-holders a has no :files"), "{}", all);
    assert!(all.contains("field-holders a has no :holders"), "{}", all);
    assert!(all.contains("field-holders a has no :why (why this roster)"), "{}", all);
    assert!(all.contains("B in :holders of field-holders b is not in :classes"), "{}", all);
}

/// DOEFF162: if the declarations of classes inheriting EffectBase in :files of :effect-census differ from the :effects list,
/// one hit per difference is emitted as critical (outside the list, declared twice, a listed effect with no declaration).
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
         :effects [\"Send\" \"Close\" \"Log\"] :why \"requests to the outside are closed by the list\")]",
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

/// If a required key of :effect-census is missing, it is a read error.
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
    assert!(all.contains("effect-census a has no :files"), "{}", all);
    assert!(all.contains("effect-census a has no :effects"), "{}", all);
    assert!(all.contains("effect-census a has no :why (why the list is closed)"), "{}", all);
}

/// The records client only emits HttpRequest effects — real HTTP is counted on the side of the real handler
/// that answers HttpRequest (the inventory's http-production-handler). Ordinary clients and clients narrowed by a table are not listed in the inventory.
/// Places that merely name RecordsEndpoint as a value (a type annotation) are not counted either.
#[test]
fn records_client_is_counted_by_its_outer_http_handler() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/ports.hy",
            tags("billing", "judgment")
                + "(import doeff_records.http_client [RecordsEndpoint http-records-handler http-table-records-handler])\n\
                   (import doeff_core_effects._http_handlers [http-production-handler])\n\
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

/// The inventory row kept only during migration has been removed. An old declaration that lists a records client
/// that only emits effects in the real I/O :wraps is stopped by the usual "not in the inventory" config error.
#[test]
fn records_clients_cannot_be_declared_as_world_handler_wraps() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n(defk with-records [body] body)\n")];
    for name in ["http-records-handler", "http-table-records-handler"] {
        let target = format!("doeff_records.http_client:{name}");
        let extra = format!("\n(world-handler \"app.foundation.host:with-records\" :touches [http] :wraps [\"{target}\"])");
        let dir = world_repo_with(&files, &extra, "[\"DOEFF131\"]");
        let (code, _, stderr) = run(dir.path(), &["--no-log"], None);
        assert_eq!(code, 2, "{}", stderr);
        assert!(stderr.contains(&format!("{target} is not in the catalog of real I/O handlers")), "{}", stderr);
    }
}

/// C7b: definitions judged only by decided material (:blind-definitions) — a definition reached by calls from a declared definition is red if it reads a word that must not be used,
/// even across helpers and other modules (one per reached definition and word). Words in comments and words in unreached definitions are not counted. With :no-imports, a module's
/// import is red, and a macro require in :allow-requires is allowed. If the named definition is missing, it is red at the architecture.hy position (an empty population is never green).
#[test]
fn blind_definitions_read_only_their_inputs() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/core/entrance.hy",
            "(require doeff-hy.macros [defk])\n(import app.billing.core.routes [peek])\n\
             (defk decide [said] (helper said))\n\
             (defk helper [x]\n  ;; view.policy is not read (comments are not counted)\n  (peek x))\n"
                .to_string(),
        ),
        ("app/billing/core/routes.hy", "(defk peek [x] (get x \"view.policy\"))\n(defk unrelated [] \"message-class\")\n".to_string()),
        ("app/billing/core/clean.hy", "(require doeff-hy.macros [defk])\n(defk decide [said] (if (is said None) True said))\n".to_string()),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF141\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :blind-definitions [(blind \"app.billing.core.entrance:decide\" :forbid-words [\"view.policy\" \"message-class\"]\n                         :no-imports True :allow-requires [\"doeff-hy.macros\"] :why \"the guarantee is decided only from the requester's words\")\n                       (blind \"app.billing.core.clean:decide\" :forbid-words [\"view.policy\"] :no-imports True\n                         :allow-requires [\"doeff-hy.macros\"] :why \"same\")\n                       (blind \"app.billing.core.gone:decide\" :no-imports True :why \"removed definition\")]",
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
    assert!(reached["message"].as_str().unwrap().contains("The definition peek reached from app.billing.core.entrance:decide"), "{}", reached["message"]);
    assert_eq!(reached["range"]["start"]["line"], 0);
}

/// Misreads of a declaration (a declaration that asks for nothing, :allow-requires without :no-imports, a declaration without :why, the spelling shape) are config errors.
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
    assert!(all.contains("blind a.b:c has neither :forbid-words nor :no-imports True"), "{}", all);
    assert!(all.contains(":allow-requires of blind a.b:d is effective only with :no-imports True"), "{}", all);
    assert!(all.contains("blind a.b:e has no :why"), "{}", all);
    assert!(all.contains("no-colon in blind must be a spelling \"module.path:NAME\""), "{}", all);
}

/// A definition with decided allowed heads (:allowed-heads) — if the head of a `( … )` inside the definition (including nested ones) is outside the list,
/// one hit per head (where it first appears). Spellings inside strings, comments, `#_`, and tuples do not count as heads. Heads of other definitions in the same module are not looked at.
/// If the named definition is missing, it is red at the architecture.hy position (an empty population is never green).
#[test]
fn allowed_heads_confine_what_a_definition_may_call() {
    let files = [
        ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
        (
            "app/billing/entry/server.hy",
            "(require doeff-hy.macros [defk <-])\n\
             (defk confined [conn text]\n  {:pre [(: text str)]}\n  ;; (decode text) is a comment so it is not counted\n  (<- r (Try (on-text conn text)))\n  \"(parse text)\"\n  #_(explode)\n  \
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
        ":foundation foundation\n  :allowed-heads [(allowed-heads \"app.billing.entry.server:confined\"\n                    :heads [\"defk\" \":\" \"<-\" \"Try\" \"on-text\" \"when\" \"isinstance\" \"return\" \"refuse\" \"setv\"]\n                    :why \"do not raise exceptions outside Try\")\n                  (allowed-heads \"app.billing.entry.server:refuse\" :heads [\"defk\" \"Send\"] :why \"the single point of refusal\")\n                  (allowed-heads \"app.billing.entry.server:gone\" :heads [\"defk\"] :why \"removed definition\")]",
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
    assert!(unlisted["message"].as_str().unwrap().contains("app.billing.entry.server:confined calls (decode …), which is outside the list of heads it may call"), "{}", unlisted["message"]);
    assert_eq!(unlisted["range"]["start"]["line"], 8, "where it first appears (inside the when branch): {}", unlisted);
}

/// Only heads in the list is green. Misreads of a declaration (a declaration without :heads, without :why, declared twice, the spelling shape) are config errors.
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
        ":foundation foundation\n  :allowed-heads [(allowed-heads \"app.billing.entry.server:refuse\" :heads [\"defk\" \"Send\"] :why \"the single point of refusal\")]",
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
    assert!(all.contains("allowed-heads a.b:c has no :heads"), "{}", all);
    assert!(all.contains("allowed-heads a.b:d has no :why"), "{}", all);
    assert!(all.contains("allowed-heads a.b:e is declared twice"), "{}", all);
    assert!(all.contains("no-colon in allowed-heads must be a spelling \"module.path:NAME\""), "{}", all);
}

/// Where and how many times a head may be called (:call-sites). The correct shape is green. A call outside the sites, a count mismatch, a mismatch of the immediately
/// enclosing form, a call outside a branch, a vanished site, and a declaration with no matching file are each red by their own key (files in :except and anything inside comments and strings are not counted).
#[test]
fn call_sites_confine_where_a_head_is_called() {
    let server = "(require doeff-hy.macros [defk <-])\n\
                  (defk confined [conn text] (<- r (Try (on-text conn text))) (when (isinstance r Err) (refuse conn r)))\n\
                  (defk serve [config]\n  (cond (isinstance arrival Text) (confined arrival.conn arrival.text)\n        (isinstance arrival AnswerFailed) (refuse arrival.conn arrival.failure)))\n\
                  (defk refuse [conn fault] (Send conn fault))\n";
    let service = "(defk process [config] (run (watching (try-handler (serve config)))))\n";
    let declare = "\n  :call-sites [(call-site \"Try\" :files [\"app/billing/**/*.hy\"] :except [\"app/billing/**/tests/**\"]\n                 :sites [(site \"app.billing.entry.server:confined\" :count 1)] :why \"confinement is at one point\")\n               (call-site \"refuse\" :files [\"app/billing/entry/server.hy\"]\n                 :sites [(site \"app.billing.entry.server:confined\" :count 1) (site \"app.billing.entry.server:serve\" :count 1 :branch \"AnswerFailed\")]\n                 :why \"refusal is at two places\")\n               (call-site \"serve\" :files [\"app/billing/entry/service.hy\"]\n                 :sites [(site \"app.billing.entry.service:process\" :count 1 :parent \"try-handler\")] :why \"try-handler is the innermost\")]";
    let build = |server: &str, service: &str, extra: &str| {
        let files = [
            ("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n"),
            ("app/billing/entry/server.hy", server.to_string()),
            ("app/billing/entry/service.hy", service.to_string()),
            ("app/billing/core/react.hy", "(defk react [x] ;; (Try x) is a comment\n  \"(Try x)\" x)\n".to_string()),
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

    // Rename the site definition (a vanished site) + call itself from within the single point of refusal (a call outside the sites).
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

/// A count mismatch, and a declaration with no matching file. Misreads of a declaration (a declaration without :files, :sites, or :why, declared twice, a non-integer :count) are config errors.
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
        ":foundation foundation\n  :call-sites [(call-site \"go\" :files [\"app/billing/entry/server.hy\"] :sites [(site \"app.billing.entry.server:serve\" :count 1)] :why \"once\")\n               (call-site \"stop\" :files [\"app/nowhere/**/*.hy\"] :sites [(site \"app.billing.entry.server:serve\")] :why \"empty\")]",
    );
    std::fs::write(&arch_path, counted).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF159"), vec!["app/billing/entry/server.hy::DOEFF159::go:count:serve", "architecture.hy::DOEFF159::stop:empty"], "{}", report);
    let count = violation(&report, "app/billing/entry/server.hy::DOEFF159::go:count:serve");
    assert!(count["message"].as_str().unwrap().contains("inside app.billing.entry.server:serve, (go …) appears 2 times (the declaration says exactly 1)"), "{}", count["message"]);

    let broken = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :call-sites [(call-site \"a\" :sites [(site \"x.y:z\")] :why \"x\")\n               (call-site \"b\" :files [\"x/*.hy\"] :why \"x\")\n               (call-site \"c\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\" :count many)])\n               (call-site \"d\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\")] :why \"x\")\n               (call-site \"d\" :files [\"x/*.hy\"] :sites [(site \"x.y:z\")] :why \"x\")]",
    );
    std::fs::write(&arch_path, broken).unwrap();
    let (code, stdout, stderr) = run(dir.path(), &["--no-log"], None);
    let all = format!("{}{}", stdout, stderr);
    assert_ne!(code, 0, "{}", all);
    assert!(all.contains("call-site a has no :files"), "{}", all);
    assert!(all.contains("call-site b has no :sites"), "{}", all);
    assert!(all.contains(":count must be an integer of 0 or more"), "{}", all);
    assert!(all.contains("call-site c has no :why"), "{}", all);
    assert!(all.contains("call-site d is declared twice"), "{}", all);
}

/// The foundation carried by a system value (the definition of a defsystem and the argument of :carriers in :systems) is reached only from tests that
/// reach the entry that runs the system (:runners in :systems) — a test that merely reads the system value, and a test that merely assembles with tools, are local. A test that calls the foundation directly is an edge as before.
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

/// DOEFF160: a broad exception catch in the Hy and Python files of :files in :broad-catches is allowed only if it binds the exception by name inside a :carriers definition
/// and passes it to the :event event — one per file for an outside catch, and one per boundary for a boundary that does not pass the name, as critical.
#[test]
fn broad_catches_are_confined_to_declared_carriers() {
    let deferred = "(defk carry [inbox program]\n  (try (<- a program)\n    (except [TaskCancelledError] (raise))\n    (except [error Exception] (<- (PutChannel inbox (Queued :arrival None :failure error))))))\n";
    let react = "(defk react [x] ;; (except [Exception] x) is a comment\n  \"(except [] x)\" (try (x) (except [ValueError] None)))\n";
    let worker = "def work():\n    try:\n        run()\n    except (KeyError, ValueError):\n        pass\n";
    let declare = "\n  :broad-catches [(broad-catch \"screen\" :files [\"app/billing/**/*.hy\" \"app/billing/**/*.py\"] :except [\"app/billing/**/tests/**\"]\n                   :carriers [(carrier \"app.billing.core.deferred:carry\" :event \"Queued\")] :why \"do not swallow contract violations\")]";
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

    // A broad catch in Hy in react, `except Exception:` and `except:` in Python, and a boundary that does not pass the exception to the event (swallowing).
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
    assert!(worker_hit["message"].as_str().unwrap().contains("2 broad exception catch(es)"), "{}", worker_hit);

    // A broad catch that does not bind hits even inside a boundary. If the boundary's definition vanishes, the catch inside becomes an outside catch, and there is no boundary.
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

/// An empty population for :broad-catches is red, and a misreading is a read error.
#[test]
fn broad_catches_empty_population_and_misreadings() {
    let files = [("app/foundation/host.hy", tags("shared", "foundation") + "(defk with-host [body] body)\n")];
    let dir = world_repo_with(&files, "", "[\"DOEFF160\"]");
    let arch_path = dir.path().join("architecture.hy");
    let original = std::fs::read_to_string(&arch_path).unwrap();
    let text = original.replace(
        ":foundation foundation",
        ":foundation foundation\n  :broad-catches [(broad-catch \"gone\" :files [\"app/gone/**/*.hy\"] :why \"empty population\")]",
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
    assert!(all.contains("carrier app.x:y has no :event (the event that carries the exception)"), "{}", all);
    assert!(all.contains("broad-catch a has no :files"), "{}", all);
    assert!(all.contains("broad-catch a has no :why (why broad catches are not placed)"), "{}", all);
    assert!(all.contains("broad-catch b is declared twice"), "{}", all);
}

/// The DOEFF166 repo: it judges DOEFF110 and 111 (111 builds its key with the law name bare-needs-reason), and the registry is a one-key-per-file dir plus a one-key-per-line file.
fn stale_registry_repo(dir_keys: &[&str], listed: &str, enable: &str) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::write(dir.path().join("architecture.hy"), "(defarchitecture s :root \"app\" :layers [(layer core)])\n").unwrap();
    std::fs::write(
        dir.path().join("pyproject.toml"),
        format!(
            "[tool.doeff-linter]\nenable = [{}]\n[tool.doeff-linter.definitions]\n[tool.doeff-linter.registry]\ndirs = [\"reg/BREACHES\"]\nfiles = [\"known.txt\"]\n\n[[tool.doeff-linter.laws]]\nname = \"bare-needs-reason\"\nrules = [\"DOEFF111\"]\nstatement = \"write a reason for a plain function\"\n",
            enable
        ),
    )
    .unwrap();
    std::fs::create_dir_all(dir.path().join("reg/BREACHES")).unwrap();
    for (i, key) in dir_keys.iter().enumerate() {
        std::fs::write(dir.path().join(format!("reg/BREACHES/k{}.txt", i)), format!("{}\nreason\n", key)).unwrap();
    }
    std::fs::write(dir.path().join("known.txt"), listed).unwrap();
    std::fs::create_dir_all(dir.path().join("app/core")).unwrap();
    std::fs::write(dir.path().join("app/core/x.hy"), "(deff bare [row] row)\n(defn helper [] 1)\n").unwrap();
    dir
}

#[test]
fn registry_keys_that_no_longer_hit_are_errors_only_for_rules_judged_on_the_whole_repo() {
    let enable = "\"DOEFF110\", \"DOEFF111\", \"DOEFF166\"";
    // Keys that still hit (law name, rule ID), keys that no longer hit (a dir file and a list line), a key of a rule not in enable, and a key of another check whose rule cannot be looked up.
    // A rule not in enable (DOEFF119) is also judged in the same run because the key names it (previously it was not judged and missed).
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
            "reg/BREACHES/k2.txt::DOEFF166::app/core/x.hy::DOEFF119::Old",
        ]
    );
    assert!(keys(&report, "DOEFF119").is_empty(), "hits of a rule not in enable are not reported: {:?}", keys(&report, "DOEFF119"));
    let gone = violation(&report, "reg/BREACHES/k1.txt::DOEFF166::app/core/x.hy::bare-needs-reason::gone");
    assert_eq!(gone["severity"], "error");
    assert!(gone["message"].as_str().unwrap().contains("DOEFF111"), "{}", gone["message"]);
    assert_eq!(code, 1, "a row that does not hit is an error — exit code 1");
    // A key that still hits drops to warning as before.
    assert_eq!(violation(&report, "app/core/x.hy::bare-needs-reason::bare")["severity"], "warning");

    // Counterexample: removing the old row makes DOEFF166 not appear.
    let clean = stale_registry_repo(&["app/core/x.hy::bare-needs-reason::bare"], "app/core/x.hy::DOEFF110::helper\n", enable);
    let (_, report) = editor(clean.path());
    assert!(keys(&report, "DOEFF166").is_empty(), "{:?}", keys(&report, "DOEFF166"));

    // A run with only named files, and a run that does not enable DOEFF166, do not judge.
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/core/x.hy"], None);
    let named: Value = serde_json::from_str(&stdout).unwrap();
    assert!(keys(&named, "DOEFF166").is_empty(), "{:?}", keys(&named, "DOEFF166"));
    let off = stale_registry_repo(&["app/core/x.hy::bare-needs-reason::gone"], "", "\"DOEFF110\", \"DOEFF111\"");
    let (_, report) = editor(off.path());
    assert!(keys(&report, "DOEFF166").is_empty());
}

/// Read the top-level field judged_rules of editor-json (the IDs of the rules judged in this run).
fn judged_rules(report: &Value) -> Vec<String> {
    report["judged_rules"].as_array().unwrap_or_else(|| panic!("judged_rules is missing: {}", report)).iter().map(|r| r.as_str().unwrap().to_string()).collect()
}

#[test]
fn a_single_file_run_names_the_rules_it_judged_and_leaves_out_rules_judged_only_on_the_whole_repo() {
    // A single-file run (--stdin --path) does not judge DOEFF166 — it is not listed in judged_rules, and the editor leaves that violation as the result of the whole
    // run. A whole run lists it. In both kinds of runs, the rules of the violations emitted are all in judged_rules.
    let dir = stale_registry_repo(&["app/core/x.hy::bare-needs-reason::gone"], "", "\"DOEFF110\", \"DOEFF111\", \"DOEFF166\"");
    let (_, whole) = editor(dir.path());
    assert!(!keys(&whole, "DOEFF166").is_empty(), "a whole run emits rows that do not hit: {}", whole);
    let whole_judged = judged_rules(&whole);
    for rule in ["DOEFF110", "DOEFF111", "DOEFF166"] {
        assert!(whole_judged.iter().any(|r| r == rule), "a whole run judges {}: {:?}", rule, whole_judged);
    }

    let path = dir.path().join("app/core/x.hy");
    let text = std::fs::read_to_string(&path).unwrap();
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", path.to_str().unwrap()], Some(&text));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    let single_judged = judged_rules(&single);
    assert!(!single_judged.iter().any(|r| r == "DOEFF166"), "a single-file run does not judge 166: {:?}", single_judged);
    assert!(single_judged.iter().any(|r| r == "DOEFF110") && single_judged.iter().any(|r| r == "DOEFF111"), "{:?}", single_judged);
    assert!(!keys(&single, "DOEFF110").is_empty(), "a single-file run also judges and emits 110: {}", single);
    // rules[] stays the list of enabled rules (166 is also listed as wired) — whether it was judged is claimed by judged_rules.
    assert!(single["rules"].as_array().unwrap().iter().any(|r| r["rule"] == "DOEFF166" && r["wired"] == true), "{}", single["rules"]);
    // A whole run with named paths also does not judge only 166.
    let (_, stdout, _) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "app/core/x.hy"], None);
    let named: Value = serde_json::from_str(&stdout).unwrap();
    assert!(!judged_rules(&named).iter().any(|r| r == "DOEFF166"), "{:?}", judged_rules(&named));
    for report in [&whole, &single, &named] {
        let judged = judged_rules(report);
        for v in report["violations"].as_array().unwrap() {
            assert!(judged.iter().any(|r| r == v["rule"].as_str().unwrap()), "the violation's rule {} is not in judged_rules: {:?}", v["rule"], judged);
        }
    }
}

/// Run the whole repo with editor-json, passing rules to `--enable`.
fn editor_enabling(root: &Path, rules: &str) -> (i32, Value) {
    let (code, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log", "--enable", rules], None);
    let value: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    (code, value)
}

#[test]
fn registry_rows_are_judged_by_the_rules_they_name_even_when_only_166_is_enabled() {
    // Even with only `--enable DOEFF166`, the rules named by the registry rows (DOEFF110 and DOEFF111 of the law bare-needs-reason) are judged in the same run, and
    // rows that do not hit become errors. Before the fix the named rules did not run, and 166 silently gave 0 hits (the miss of #1992).
    let dir = stale_registry_repo(
        &["app/core/x.hy::bare-needs-reason::bare", "app/core/x.hy::bare-needs-reason::gone"],
        "app/core/x.hy::DOEFF110::helper\napp/core/x.hy::DOEFF110::removed\nlayer/imports.hy::some-other-checker::x\n",
        "\"DOEFF110\", \"DOEFF111\", \"DOEFF166\"",
    );
    let (code, report) = editor_enabling(dir.path(), "DOEFF166");
    assert_eq!(
        keys(&report, "DOEFF166"),
        vec![
            "known.txt::DOEFF166::app/core/x.hy::DOEFF110::removed",
            "reg/BREACHES/k1.txt::DOEFF166::app/core/x.hy::bare-needs-reason::gone",
        ]
    );
    assert_eq!(code, 1, "a row that does not hit is an error — exit code 1");
    // The named rules are used only for 166's judgment — hits of rules not in enable (including rows that still hit) are not reported.
    assert!(keys(&report, "DOEFF110").is_empty(), "{:?}", keys(&report, "DOEFF110"));
    assert!(keys(&report, "DOEFF111").is_empty(), "{:?}", keys(&report, "DOEFF111"));

    // Hits of rules in enable do not change even when 166 is added (hits of 111 added by being named are not mixed in either).
    let (_, alone) = editor_enabling(dir.path(), "DOEFF110");
    let (_, with_166) = editor_enabling(dir.path(), "DOEFF110,DOEFF166");
    assert!(!keys(&alone, "DOEFF110").is_empty(), "{:?}", keys(&alone, "DOEFF110"));
    assert_eq!(keys(&with_166, "DOEFF110"), keys(&alone, "DOEFF110"));
    assert!(keys(&with_166, "DOEFF111").is_empty(), "{:?}", keys(&with_166, "DOEFF111"));
    assert_eq!(keys(&with_166, "DOEFF166"), keys(&report, "DOEFF166"));
}

/// A temporary repo with the DOEFF142 declaration (:handler-arguments) added to architecture.hy (the test for decision C's failure case).
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

/// DOEFF142: red if a defhandler argument takes a client or a mutable store (firing example). A handler taking a frozen record setting, and
/// a handler with a keep-reason comment (:keep-mark), do not fire (silent examples).
#[test]
fn handler_arguments_that_hold_a_client_or_a_store_are_red() {
    let firing = "(import httpx)\n\
                  (defhandler charge-reads [#^ httpx.AsyncClient client store]\n  (Charge [amount] (resume amount)))\n";
    let dir = handler_argument_repo(firing);
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF142");
    assert_eq!(hits.len(), 2, "two: a client and a name store: {:?}\n{}", hits, report);
    assert!(hits.iter().all(|k| k.starts_with("app/billing/protocol/answer.hy::DOEFF142::charge-reads::")), "{:?}", hits);
    assert_eq!(violation(&report, &hits[0])["severity"], "error");

    let quiet = "(require doeff-hy.record [defrecord])\n\
                 (defrecord ChargeSettings \"Billing settings\" (#^ str currency))\n\
                 (defhandler charge-reads [#^ ChargeSettings settings]\n  (Charge [amount] (resume amount)))\n\
                 (defhandler kept-reads [store]\n  ;; handler-state-kept: a record box passed only by the test rig (not in the production assembly)\n  (Charge [amount] (resume amount)))\n";
    let dir = handler_argument_repo(quiet);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF142").is_empty(), "{:?}\n{}", keys(&report, "DOEFF142"), report);
}

/// A temporary repo with the DOEFF155 and 158 declarations (:business-fakes and :assembly-shape). The billing service's layers are
/// core, intent, protocol (translation layer), and entry (assembly layer). Production entry = a definition in an assembly file (app/*/entry/handler_sets.hy) whose name starts
/// with production. The intent effects are Charge and Refund (app/billing/intent/effects.hy).
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
        .replace("(defservice billing \"Billing\" {:layers [core entry]})", "(defservice billing \"Billing\" {:layers [core intent protocol entry]})");
    std::fs::write(&arch_path, text).unwrap();
    dir
}

/// A file placing the handler charge-reads that answers Charge (under a layer dir, billing context).
fn charge_handler(layer: &str, name: &str) -> String {
    tags("billing", layer)
        + &format!(
            "(require doeff-hy.macros [defhandler])\n(import app.billing.intent.effects [Charge])\n(defhandler {} []\n  (Charge [amount] (resume amount)))\n",
            name
        )
}

/// The production entry (production-handlers of the assembly file) lines up the handlers.
fn production_set(imports: &str, handlers: &str) -> String {
    tags("billing", "entry") + &format!("{}\n(defk production-handlers [] [{}])\n", imports, handlers)
}

/// DOEFF158: red if an answerer of an intent effect reached from the production entry is outside the translation layer; both red if two are in the translation layer
/// (firing examples). If only one translation-layer handler answers, it does not fire (silent example).
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

/// The translation layer (protocol) places the Charge translation charge-reads and the file with its list constant TRANSLATION-HANDLERS.
fn translation_module() -> String {
    charge_handler("protocol", "charge-reads") + "(require doeff-hy.macros [val])\n(val TRANSLATION-HANDLERS [charge-reads])\n"
}

/// DOEFF155: both are red (firing examples) if the assembly layer still has a retired assembly function (:retired-function), or if the single point of the translation list lines up
/// a handler that is not a translation-list handler. If the single point of the translation list lines up only the translation layer's constant list, it does not fire (silent example).
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

/// DOEFF155 — the simulation assembly's translation handlers are the same as production's: both are red (firing examples) if the single point of the translation list takes the list as an argument, or if the
/// verification environment (:verification-environment) or a test file opens the translation list outside the single point and lines it up. If the simulation and tests call the production
/// single point, it does not fire (silent example).
#[test]
fn simulations_that_build_their_own_translation_list_are_red() {
    let point = |params: &str, extra: &str| {
        tags("billing", "entry")
            + &format!(
                "(import app.billing.protocol.translate [TRANSLATION-HANDLERS])\n\
                 (defk with-billing-translation [{}] (with-handlers [#* TRANSLATION-HANDLERS{}] body))\n",
                params, extra
            )
    };
    let given = [("app/billing/protocol/translate.hy", translation_module()), ("app/billing/entry/assembly.hy", point("translations body", " #* translations"))];
    let dir = assembly_repo(&given, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF155");
    assert_eq!(hits.len(), 1, "{:?}\n{}", hits, report);
    assert!(hits[0].starts_with("app/billing/entry/assembly.hy::") && hits[0].ends_with("parameter:translations"), "{:?}", hits);
    assert_eq!(violation(&report, &hits[0])["severity"], "error");

    let own_list = "(require doeff-hy.macros [defk])\n(import app.billing.protocol.translate [TRANSLATION-HANDLERS])\n\
                    (defk emulated-world [body] (with-handlers [#* TRANSLATION-HANDLERS] body))\n";
    let loose = [
        ("app/billing/protocol/translate.hy", translation_module()),
        ("app/billing/entry/assembly.hy", point("body", "")),
        ("app/sim/world.hy", tags("sim", "foundation") + own_list),
        ("app/billing/tests/test_world.hy", tags("billing", "test") + own_list),
    ];
    let dir = assembly_repo(&loose, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    let mut hits = keys(&report, "DOEFF155");
    hits.sort();
    assert_eq!(hits.len(), 2, "{:?}\n{}", hits, report);
    let list = "loose:app.billing.protocol.translate.TRANSLATION_HANDLERS";
    assert!(hits[0].starts_with("app/billing/tests/test_world.hy::") && hits[0].ends_with(list), "{:?}", hits);
    assert!(hits[1].starts_with("app/sim/world.hy::") && hits[1].ends_with(list), "{:?}", hits);

    // It is red even if the simulation opens and lines up a list in which the assembly layer bundled translation-layer handlers (part of the list the single point lines up).
    let bundled = [
        ("app/billing/protocol/translate.hy", translation_module()),
        (
            "app/billing/entry/assembly.hy",
            point("body", "")
                + "(import app.billing.protocol.translate [charge-reads])\n(require doeff-hy.macros [val])\n(val BILLING-TRANSLATIONS [charge-reads])\n",
        ),
        (
            "app/sim/world.hy",
            tags("sim", "foundation")
                + "(require doeff-hy.macros [defk])\n(import app.billing.entry.assembly [BILLING-TRANSLATIONS])\n\
                   (defk emulated-world [body] (with-handlers [#* BILLING-TRANSLATIONS] body))\n",
        ),
    ];
    let dir = assembly_repo(&bundled, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    let hits = keys(&report, "DOEFF155");
    assert_eq!(hits, vec!["app/sim/world.hy::DOEFF155::loose:app.billing.entry.assembly.BILLING_TRANSLATIONS".to_string()], "{}", report);

    let calls_the_point = "(require doeff-hy.macros [defk])\n(import app.billing.entry.assembly [with-billing-translation])\n\
                           (defk emulated-world [body] (with-billing-translation body))\n";
    let quiet = [
        ("app/billing/protocol/translate.hy", translation_module()),
        ("app/billing/entry/assembly.hy", point("body", "")),
        ("app/sim/world.hy", tags("sim", "foundation") + calls_the_point),
        ("app/billing/tests/test_world.hy", tags("billing", "test") + calls_the_point),
    ];
    let dir = assembly_repo(&quiet, "[\"DOEFF155\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF155").is_empty(), "{:?}\n{}", keys(&report, "DOEFF155"), report);
}

/// A file that lines up the translation list TRANSLATION-HANDLERS of the translation layer (protocol) in `order`. charge-reads answers Charge and re-performs the effect LegacyRow of the old location
/// (a business module that is not intent). legacy-reads answers LegacyRow. refund-reads answers Refund and
/// has `refund_body` as its body.
fn reissuing_translations(order: &str, refund_body: &str) -> String {
    tags("billing", "protocol")
        + "(require doeff-hy.macros [defhandler val])\n\
           (import app.billing.intent.effects [Charge Refund])\n\
           (import app.billing.legacy.effects [LegacyRow])\n\
           (defhandler legacy-reads []\n  (LegacyRow [amount] (resume amount)))\n\
           (defhandler charge-reads []\n  (Charge [amount] (resume (LegacyRow amount))))\n"
        + &format!("(defhandler refund-reads []\n  (Refund [amount] (resume {})))\n(val TRANSLATION-HANDLERS [{}])\n", refund_body, order)
}

/// The single point of the translation list (assembly layer) lines up only the translation layer's list.
fn translation_point() -> String {
    tags("billing", "entry")
        + "(import app.billing.protocol.translate [TRANSLATION-HANDLERS])\n\
           (defk with-billing-translation [body] (with-handlers [#* TRANSLATION-HANDLERS] body))\n"
}

const LEGACY_EFFECTS: &str = "(import doeff [EffectBase])\n(defclass LegacyRow [EffectBase])\n";

/// Check DOEFF156 (location of the answer) by naming it by rule ID. Firing examples = a translation handler re-performs an intent effect
/// (refund-reads -> Charge), a handler of the same list that answers the re-performed effect is inside (legacy-reads, which answers charge-reads' LegacyRow, is
/// behind), and a foundation handler that the assembly function of the assembly file lines up answers a business effect. Silent example = re-performing a non-intent
/// effect that a handler outside (in front of) the same list answers (the "may re-perform" side of the row is_shape() == false in the unit test).
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

/// A world_repo declaring boundary parts (:boundary-parts) (`parts` is the list of :boundary-parts elements, `enable` is the list of rules).
fn boundary_repo(files: &[(&str, String)], parts: &str, enable: &str) -> tempfile::TempDir {
    let dir = world_repo_with(files, "", enable);
    declare_boundary_parts(&dir, parts);
    dir
}

/// Add boundary parts (:boundary-parts — `parts` is the list of elements) to a temporary repo's architecture.hy.
fn declare_boundary_parts(dir: &tempfile::TempDir, parts: &str) {
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path)
        .unwrap()
        .replace(":foundation foundation", &format!(":foundation foundation\n  :edge-mark \"real_world\"\n  :boundary-parts [{}]", parts));
    std::fs::write(&arch_path, text).unwrap();
}

/// Inside a boundary-part (:boundary-parts) module, DOEFF106 does not hit raw side effects of the declared :touches kinds
/// — raw side effects outside the kinds, and modules with no declaration, hit as before. The same for files inside layers and files outside the layer locations.
#[test]
fn boundary_parts_allow_only_the_declared_touches() {
    let bench = "(import socket)\n(import time)\n(import subprocess)\n\
                 (defn open-one [] (socket.socket))\n(defn now [] (time.time))\n(defn spawn [] (subprocess.run [\"true\"]))\n";
    let files = [
        ("app/billing/entry/bench.hy", tags("billing", "entry") + bench),
        ("app/billing/entry/other.hy", tags("billing", "entry") + "(import socket)\n(defn open-one [] (socket.socket))\n"),
        ("app/tools/probe.hy", bench.to_string()),
    ];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network clock] :reason "an edge rig that fires at a real listener")
                   (boundary-part "app.tools.probe" :touches [network clock] :reason "an entry run by a person")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF106"),
        vec![
            "app/billing/entry/bench.hy::DOEFF106::spawn::subprocess.run",
            "app/billing/entry/other.hy::DOEFF106::open_one::socket.socket",
            "app/tools/probe.hy::DOEFF106::spawn::subprocess.run",
        ],
        "the declared network and clock do not hit; process outside the kinds and a module with no declaration do: {}",
        report
    );
}

/// Inside a boundary-part (:boundary-parts) module, raw calls to the touch targets declared in :touches (time.time -> clock) do not hit
/// DOEFF151 either (the same mapping as DOEFF106 — the place that passes the raw clock is one declaration of the part). Counterexamples = a module with no declaration, a part without
/// clock in :touches, and time.time in an undeclared file outside the layer locations, and even inside a part a call that is not a raw side effect (the effect Now) hits.
/// A single-file run (the editor shape passing pre-save contents on stdin) reads the same declaration.
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
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network clock] :reason "an edge rig that fires at a real listener")
                   (boundary-part "app.billing.entry.wire" :touches [network] :reason "an edge rig that does not read the clock")
                   (boundary-part "app.tools.probe" :touches [clock] :reason "an entry run by a person")"#;
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
        "only time.time in a part that declares clock does not hit; time.time in a module with no declaration or in a part without clock, and Now inside a part, do: {}",
        report
    );
    let (_, stdout, stderr) =
        run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/billing/entry/bench.hy"], Some(&files[0].1));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF151"), vec!["app/billing/entry/bench.hy::DOEFF151::Now"], "a single-file run also reads the part declaration: {}", single);
}

/// When a Hy definition in a :layers layer of the declaration (:environment-branches) compares with a value of an environment name or branches on a dry-run mark,
/// DOEFF168 hits as critical. Counterexamples = a comparison with a value that is not an environment name, and the same shape in a layer not in :layers (entry), do not hit.
#[test]
fn environment_branches_hit_only_in_the_declared_business_layers() {
    let files = [
        ("app/billing/core/rules.hy", tags("billing", "judgment") + "(defk total-of [mode total] (if (= mode \"emulated\") 0 total))\n"),
        ("app/billing/core/write.hy", tags("billing", "judgment") + "(defk write [dry-run] (when dry-run (return 0)) 1)\n"),
        ("app/billing/core/kinds.hy", tags("billing", "judgment") + "(defk kind-of [kind] (if (= kind \"message\") 0 1))\n"),
        ("app/billing/entry/wiring.hy", tags("billing", "entry") + "(defk choose [mode] (if (= mode \"production\") 1 0))\n"),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF168\"]");
    let arch_path = dir.path().join("architecture.hy");
    let text = std::fs::read_to_string(&arch_path).unwrap().replace(
        ":foundation foundation",
        ":foundation foundation\n  :environment-branches {:values [\"production\" \"emulated\"] :flags [\"dry-run\"] :layers [core]}",
    );
    std::fs::write(&arch_path, text).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF168"),
        vec!["app/billing/core/rules.hy::DOEFF168::value:emulated", "app/billing/core/write.hy::DOEFF168::flag:dry-run"],
        "a comparison with an environment name value and a branch on a dry-run mark in core hit; a value that is not an environment name and the entry layer do not: {}",
        report
    );
    let hit = violation(&report, "app/billing/core/rules.hy::DOEFF168::value:emulated");
    assert_eq!(hit["level"], "critical", "{}", hit);
}

/// If a `-` appears in a keyword field name of a match class pattern, it hits as critical in any Hy file in the layer locations
/// (a clause that never matches because Hy does not mangle keywords to attribute names). A field written with `_`, a positional-argument pattern, and a keyword in a clause body do not hit.
/// Inside a defk form it does not hit because doeff-hy rewrites field names to attribute names (doeff_hy/match_fields.py).
#[test]
fn hyphenated_fields_in_match_class_patterns_hit_in_every_layer() {
    let files = [
        ("app/billing/core/rules.hy", tags("billing", "judgment") + "(deff kind-of [r] (match r (Rec :ended-reason None) 0 _ 1))\n"),
        ("app/billing/entry/wiring.hy", tags("billing", "entry") + "(defhandler choose (Pick [r] (resume (match r (Rec :a-b 1) 0 _ 1))))\n"),
        ("app/billing/core/fixed.hy", tags("billing", "judgment") + "(deff kind-of [r] (match r (Rec :ended_reason None) 0 (Rec None 1) 2 _ (Rec :a-b 1)))\n"),
        ("app/billing/core/inside_defk.hy", tags("billing", "judgment") + "(defk kind-of [r] (match r (Rec :ended-reason None) 0 _ 1))\n"),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF169\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF169"),
        vec!["app/billing/core/rules.hy::DOEFF169::Rec:ended-reason", "app/billing/entry/wiring.hy::DOEFF169::Rec:a-b"],
        "a field name with `-` hits outside defk regardless of layer; a `_` field, positional arguments, a clause body, and inside defk do not: {}",
        report
    );
    let hit = violation(&report, "app/billing/core/rules.hy::DOEFF169::Rec:ended-reason");
    assert_eq!(hit["level"], "critical", "{}", hit);
}

/// In a layer that passes values only by type (the layer's :wire-free True — here core), a defwire declaration (DOEFF170), and a mapping in the type of :pre / :post and
/// defrecord fields (including a typed mapping) and a tuple with no content type (DOEFF171) hit as critical. The same shape in a layer with no declaration (entry), a typed
/// list of the same type as a typed defrecord, and a repo with the declaration removed do not hit (the judgment enters from the layer's declaration).
#[test]
fn wire_free_layer_forbids_defwire_and_maps() {
    let files = [
        (
            "app/billing/core/rules.hy",
            tags("billing", "judgment")
                + "(defwire InvoiceWire [] (#^ str id) (#^ JsonBody extra))\n(defrecord Invoice (#^ str id) (#^ dict extra))\n\
                   (defk total [invoices index]\n  {:pre [(: invoices (of list Invoice)) (: index (of dict str Invoice))] :post [(: % int)]}\n  0)\n",
        ),
        (
            "app/billing/core/clean.hy",
            tags("billing", "judgment") + "(defrecord Line (#^ str id) (#^ (of tuple str ...) tags))\n(defk ids [lines]\n  {:pre [(: lines (of list Line))] :post [(: % (of tuple str ...))]}\n  #())\n",
        ),
        (
            "app/billing/entry/wiring.hy",
            tags("billing", "entry") + "(defwire PageWire [] (#^ str id))\n(defk serve [body]\n  {:pre [(: body dict)] :post [(: % dict)]}\n  body)\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF170\", \"DOEFF171\"]");
    let path = dir.path().join("architecture.hy");
    let declared = std::fs::read_to_string(&path).unwrap();
    std::fs::write(&path, declared.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :wire-free True)")).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF170"), vec!["app/billing/core/rules.hy::DOEFF170::wire:InvoiceWire"], "{}", report);
    assert_eq!(
        keys(&report, "DOEFF171"),
        vec![
            "app/billing/core/rules.hy::DOEFF171::field:Invoice.extra",
            "app/billing/core/rules.hy::DOEFF171::field:InvoiceWire.extra",
            "app/billing/core/rules.hy::DOEFF171::pre:total:index"
        ],
        "a dict field in core (defrecord, defwire) and a :pre of a typed mapping hit; a typed list, a tuple of the same type, and the entry layer do not: {}",
        report
    );
    for key in ["app/billing/core/rules.hy::DOEFF170::wire:InvoiceWire", "app/billing/core/rules.hy::DOEFF171::pre:total:index"] {
        assert_eq!(violation(&report, key)["level"], "critical", "{}", key);
    }
    // Without the declaration, the same file does not hit (the layer's declaration is the entry of the judgment).
    std::fs::write(&path, declared).unwrap();
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF170").is_empty() && keys(&report, "DOEFF171").is_empty(), "{}", report);
}

/// DOEFF171 also hits Python-file fields (dataclass field annotations) of a layer that passes values only by type (previously it counted only Hy files, and
/// silently missed a Mapping[ field in a core .py). Three failure cases: a mapping field in a core .py fires / a .py that turns the same field into a class
/// with a name and field types does not fire / the same field in an entry-layer (no declaration) .py does not fire for DOEFF171, and DOEFF172 counts it.
#[test]
fn wire_free_layer_judges_python_dataclass_fields() {
    let dataclass = |body: &str| format!("from collections.abc import Mapping\nfrom dataclasses import dataclass\n\n\n@dataclass(frozen=True)\n{}", body);
    let files = [
        ("app/billing/core/views.py", dataclass("class Totals:\n    counts: Mapping[str, int]\n    lines: tuple\n    ids: tuple[str, ...]\n")),
        (
            "app/billing/core/typed.py",
            dataclass("class Count:\n    key: str\n    value: int\n\n\n@dataclass(frozen=True)\nclass Totals:\n    counts: tuple[Count, ...]\n"),
        ),
        ("app/billing/entry/page.py", dataclass("class Page:\n    counts: Mapping[str, int]\n")),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF171\", \"DOEFF172\"]");
    let path = dir.path().join("architecture.hy");
    let declared = std::fs::read_to_string(&path).unwrap();
    std::fs::write(&path, declared.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :wire-free True)")).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF171"),
        vec!["app/billing/core/views.py::DOEFF171::field:Totals.counts", "app/billing/core/views.py::DOEFF171::field:Totals.lines"],
        "a mapping field and a tuple with no content type in a core .py hit; a typed tuple, a field turned into a type, and an entry .py do not: {}",
        report
    );
    assert_eq!(violation(&report, "app/billing/core/views.py::DOEFF171::field:Totals.counts")["level"], "critical", "{}", report);
    assert_eq!(keys(&report, "DOEFF172"), vec!["app/billing/entry/page.py::DOEFF172::field:Page.counts"], "{}", report);
}

/// Smell of where mappings are placed (DOEFF172, major, warning — count only). It hits where a definition body outside a handler builds a mapping, and
/// mapping fields outside the layer that passes values only by type. Inside defhandler, a single point where :post is a mapping spelling, contract dictionaries, and fields of the layer that passes values only by type (DOEFF171's
/// territory) do not hit.
#[test]
fn dict_smells_count_built_maps_and_map_fields_outside_their_place() {
    let files = [
        (
            "app/billing/core/rules.hy",
            tags("billing", "judgment") + "(defrecord Invoice (#^ str id) (#^ dict extra))\n(defk index-of [rows]\n  {:pre [(: rows (of list Invoice))] :post [(: % int)]}\n  (len (dfor r rows r.id r)))\n",
        ),
        (
            "app/billing/entry/wiring.hy",
            tags("billing", "entry")
                + "(defrecord Page (#^ str id) (#^ (of dict str int) counts))\n\
                   (defk dump [page]\n  {:pre [(: page Page)] :post [(: % dict)]}\n  {\"id\" page.id})\n\
                   (defhandler with-cache [effect k]\n  (k {}))\n\
                   (defk serve [page]\n  {:pre [(: page Page)] :post [(: % int)]}\n  (len {:a 1}))\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF172\"]");
    let path = dir.path().join("architecture.hy");
    let declared = std::fs::read_to_string(&path).unwrap();
    std::fs::write(&path, declared.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :wire-free True)")).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF172"),
        vec![
            "app/billing/core/rules.hy::DOEFF172::built:index-of",
            "app/billing/entry/wiring.hy::DOEFF172::built:serve",
            "app/billing/entry/wiring.hy::DOEFF172::field:Page.counts"
        ],
        "building places and fields outside core hit; a :post that is a mapping dump, defhandler, and core fields do not: {}",
        report
    );
    let hit = violation(&report, "app/billing/entry/wiring.hy::DOEFF172::field:Page.counts");
    assert_eq!(hit["level"], "major", "{}", hit);
    assert_eq!(hit["severity"], "warning", "{}", hit);
}

/// Two failure cases of DOEFF172 — a definition that writes {…} in the position of a match pattern does not fire (a pattern does not build a value — previously it was
/// counted as "builds a mapping") / a definition that builds a mapping in the body of the same match fires.
#[test]
fn dict_smells_skip_match_patterns() {
    let files = [(
        "app/billing/entry/codec.hy",
        tags("billing", "entry")
            + "(defk detail-of [value]\n  {:pre [(: value dict)] :post [(: % str)] :tags {:context \"billing\" :role \"entry\"}}\n  (match value\n    {\"type\" \"call\"} \"call\"\n    _ \"other\"))\n\
               (defk counted [value]\n  {:pre [(: value dict)] :post [(: % int)] :tags {:context \"billing\" :role \"entry\"}}\n  (match value\n    {\"type\" \"call\"} (len {\"a\" 1})\n    _ 0))\n",
    )];
    let dir = world_repo_with(&files, "", "[\"DOEFF172\"]");
    let (_, report) = editor(dir.path());
    assert_eq!(keys(&report, "DOEFF172"), vec!["app/billing/entry/codec.hy::DOEFF172::built:counted"], "{{…}} in a pattern is not counted and a mapping in a body is: {}", report);
}

/// Two failure cases of DOEFF172 — a definition that has only a contract dictionary after the docstring does not fire (previously it counted the contract's metadata as
/// "a mapping built outside a handler") / a definition that builds a mapping in the body fires. :spells of :tags (the single-point claim that spells a wire shape) is not
/// counted outside core (here entry), and is counted even if claimed in the layer that passes values only by type (core).
#[test]
fn dict_smells_skip_the_contract_after_a_docstring_and_honour_spells_outside_core() {
    let files = [
        (
            "app/billing/entry/wire.hy",
            tags("billing", "entry")
                + "(defk note [n]\n  \"docstring comes first.\"\n  {:pre [(: n int)] :post [(: % str)] :tags {:context \"billing\" :role \"entry\"}}\n  (str n))\n\
                   (defk index-of [rows]\n  \"docstring comes first.\"\n  {:pre [(: rows list)] :post [(: % int)] :tags {:context \"billing\" :role \"entry\"}}\n  (len (dfor r rows r r)))\n\
                   (defk payload-text [n]\n  {:pre [(: n int)] :post [(: % str)] :tags {:context \"billing\" :role \"entry\" :spells \"json\"}}\n  (json.dumps {\"n\" n}))\n",
        ),
        (
            "app/billing/core/rules.hy",
            tags("billing", "judgment")
                + "(defk payload-text [n]\n  {:pre [(: n int)] :post [(: % str)] :tags {:context \"billing\" :role \"judgment\" :spells \"json\"}}\n  (json.dumps {\"n\" n}))\n",
        ),
    ];
    let dir = world_repo_with(&files, "", "[\"DOEFF172\"]");
    let path = dir.path().join("architecture.hy");
    let declared = std::fs::read_to_string(&path).unwrap();
    std::fs::write(&path, declared.replace("(layer core :roles [judgment] :imports [core])", "(layer core :roles [judgment] :imports [core] :wire-free True)")).unwrap();
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF172"),
        vec!["app/billing/core/rules.hy::DOEFF172::built:payload-text", "app/billing/entry/wire.hy::DOEFF172::built:index-of"],
        "a definition with only a contract and :spells in entry do not fire; a mapping in the body and :spells in core do: {}",
        report
    );
}

/// A boundary part names the allowed kinds (:touches — a closed vocabulary) and a reason (:reason). A missing one and a word outside the vocabulary are config errors that stop the run.
#[test]
fn boundary_parts_need_touches_and_a_reason() {
    let files = [("app/billing/entry/bench.hy", tags("billing", "entry") + "(defk run [] 1)\n")];
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network])
                   (boundary-part "app.billing.entry.other" :touches [socket] :reason "outside the vocabulary")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\"]");
    let (code, _, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
    assert!(stderr.contains("boundary-part app.billing.entry.bench has no :reason"), "{}", stderr);
    assert!(stderr.contains("socket in :touches of boundary-part app.billing.entry.other is not in the vocabulary"), "{}", stderr);
}

/// A boundary part's raw side effects are not hit by DOEFF106, but they remain as index evidence, so a deftest reaching the part needs the
/// :edge-mark mark at the edge (DOEFF133) — a test without the mark hits, and a test with the mark does not.
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
    let parts = r#"(boundary-part "app.billing.entry.bench" :touches [network] :reason "an edge rig")"#;
    let dir = boundary_repo(&files, parts, "[\"DOEFF106\", \"DOEFF133\"]");
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF106").is_empty(), "the declared network does not hit: {}", report);
    assert_eq!(
        keys(&report, "DOEFF133"),
        vec!["app/billing/tests/test_bench.hy::DOEFF133::test_unmarked::edge"],
        "only a test without a mark that reaches the part hits: {}",
        report
    );
}

// ------------------------------------------------------------------ Comparison with the baseline (--baseline-report)

/// The config of a temporary repo with DOEFF110 as critical (the registry is empty — comparison with the baseline does not depend on the registry).
const CRITICAL_110: &str = "[tool.doeff-linter.rules.DOEFF110]\nlevel = \"critical\"\n";

/// Write the output of editor-json run at root to the baseline file, and return its path.
fn baseline_file(root: &Path) -> std::path::PathBuf {
    let (_, stdout, stderr) = run(root, &["--output-format", "editor-json", "--no-log"], None);
    assert!(serde_json::from_str::<Value>(&stdout).is_ok(), "the baseline output is not JSON: {}\n{}", stdout, stderr);
    let path = root.join("baseline.json");
    std::fs::write(&path, stdout).unwrap();
    path
}

/// Run editor-json compared with the baseline file, and return (exit code, list of new critical identifiers).
fn against_baseline(root: &Path, baseline: &Path) -> (i32, Vec<String>) {
    let (code, stdout, stderr) =
        run(root, &["--output-format", "editor-json", "--no-log", "--baseline-report", baseline.to_str().unwrap()], None);
    let report: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    let fresh = report["new_critical"].as_array().unwrap_or_else(|| panic!("new_critical is missing: {}", report));
    (code, fresh.iter().map(|v| v.as_str().unwrap().to_string()).collect())
}

#[test]
fn baseline_a_new_critical_is_named_and_changes_the_exit_code() {
    // Increased: a critical identifier not in the baseline appears in new_critical, and the exit code becomes 4 (new critical present).
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n(defn new-one [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/a.hy::DOEFF110::new_one".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_fixing_three_and_adding_one_is_still_a_new_critical() {
    // Compare by the set of identifiers, not the count: a commit that fixes 3 and adds 1 is red even if the count decreased.
    let dir = definition_repo(&[("app/a.hy", "(defn a1 [x] x)\n(defn a2 [x] x)\n(defn a3 [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::write(dir.path().join("app/a.hy"), "(defn b1 [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/a.hy::DOEFF110::b1".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_fewer_or_same_criticals_are_not_new() {
    // Decreased or same: new_critical is empty, and the exit code is the same as when there is no baseline comparison (here 1 because there is an error violation).
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
    // File move: if rule and name are the same, only the path differs, and the same identifier in the baseline has vanished, it is regarded as the same breach (one to one).
    let dir = definition_repo(&[("app/a.hy", "(defn old [x] x)\n")], CRITICAL_110);
    let baseline = baseline_file(dir.path());
    std::fs::remove_file(dir.path().join("app/a.hy")).unwrap();
    std::fs::write(dir.path().join("app/b.hy"), "(defn old [x] x)\n").unwrap();
    let (_, fresh) = against_baseline(dir.path(), &baseline);
    assert!(fresh.is_empty(), "{:?}", fresh);
    // If the baseline identifier remains and the same name is added at a different path, it is a new breach, not a move.
    std::fs::write(dir.path().join("app/a.hy"), "(defn old [x] x)\n").unwrap();
    let (code, fresh) = against_baseline(dir.path(), &baseline);
    assert_eq!(fresh, vec!["app/b.hy::DOEFF110::old".to_string()]);
    assert_eq!(code, 4);
}

#[test]
fn baseline_only_criticals_are_compared_and_a_bad_file_is_an_argument_error() {
    // A new breach of a non-critical rule does not enter new_critical. An unreadable baseline file is an argument error (2).
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

/// A repo that removes the definition-writing rules with the layer declaration's :exempt. architecture.hy is placed in the package dir, and the src module is
/// named (the same form as the module at macro-expansion time in doeff-hy). If declaration is None, no declaration is placed.
fn exempt_repo(declaration: Option<&str>) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::create_dir_all(dir.path().join(".git")).unwrap();
    let mut files: Vec<(&str, &str)> = vec![
        ("pyproject.toml", "[tool.doeff-linter]\nenable = [\"DOEFF110\"]\n[tool.doeff-linter.definitions]\npaths = [\"pkg/src/app\"]\n"),
        ("pkg/src/app/__init__.py", ""),
        ("pkg/src/app/expand.hy", EXPAND_HY),
        ("pkg/src/app/runtime.hy", "(val MODULE-TAGS {:context \"app\" :role \"judgment\"})\n(defn decide [x] x)\n"),
    ];
    if let Some(text) = declaration {
        files.push(("pkg/architecture.hy", text));
    }
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// The exempted module: it has a defn (helper) that a macro body calls at expansion time.
const EXPAND_HY: &str = "(val MODULE-TAGS {:context \"app\" :role \"macro\"})\n(defn helper [form] form)\n(defmacro m [form] (helper form))\n";

/// A declaration that exempts DOEFF110 with a reason by naming the module at macro-expansion time. REASON is replaced to also make a declaration with no reason.
const EXEMPT_DECLARATION: &str = "(defarchitecture pkg\n  :root \"app\"\n  :layers [(layer macro\n             :summary \"called at macro-expansion time\"\n             :knows \"Hy forms\"\n             :does-not-know \"doeff Programs\"\n             :modules [app.expand]\n             :exempt [(rule DOEFF110 \"REASON\")])])\n";

#[test]
fn a_layer_exemption_drops_the_definition_rule_only_for_the_named_module() {
    let declared = EXEMPT_DECLARATION.replace("REASON", "called as a Python function at macro-expansion time — cannot return a Program");
    let dir = exempt_repo(Some(&declared));
    // Whole run: the defn of the named module (app.expand) is exempted, and the defn of a module that is not named still fires as before.
    let (_, whole) = editor(dir.path());
    assert_eq!(keys(&whole, "DOEFF110"), vec!["pkg/src/app/runtime.hy::DOEFF110::decide"], "{}", whole);
    // A single-file run (the child of the editor and commit hooks) exempts at the same single point.
    let source = std::fs::read_to_string(dir.path().join("pkg/src/app/expand.hy")).unwrap();
    let (_, stdout, stderr) =
        run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "pkg/src/app/expand.hy"], Some(&source));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    assert!(keys(&single, "DOEFF110").is_empty(), "{}", single);
}

#[test]
fn an_exempted_module_still_names_a_defn_that_no_macro_reaches() {
    // Failure case (a watch for decision 2 of #2877): if a runtime-time defn that no defmacro of the package reaches is added to the exempted module,
    // it fires even though it is exempted (with the reason sentence). The same in a single-file run.
    let declared = EXEMPT_DECLARATION.replace("REASON", "called as a Python function at macro-expansion time — cannot return a Program");
    let dir = exempt_repo(Some(&declared));
    let with_runtime = format!("{}(defn thaw [x] x)\n", EXPAND_HY);
    std::fs::write(dir.path().join("pkg/src/app/expand.hy"), &with_runtime).unwrap();
    let (_, whole) = editor(dir.path());
    assert_eq!(
        keys(&whole, "DOEFF110"),
        vec!["pkg/src/app/expand.hy::DOEFF110::thaw", "pkg/src/app/runtime.hy::DOEFF110::decide"],
        "{}",
        whole
    );
    let thaw = violation(&whole, "pkg/src/app/expand.hy::DOEFF110::thaw");
    assert!(thaw["message"].as_str().unwrap_or("").contains("no defmacro of the package reaches it"), "{}", thaw);
    let (_, stdout, stderr) =
        run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "pkg/src/app/expand.hy"], Some(&with_runtime));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("not JSON ({}): {}\n{}", e, stdout, stderr));
    assert_eq!(keys(&single, "DOEFF110"), vec!["pkg/src/app/expand.hy::DOEFF110::thaw"], "{}", single);
    // A part called by the expanded code (inside a quasiquote of a macro in another file) is also reached as "a function owned by the macro" — it is exempted.
    std::fs::write(
        dir.path().join("pkg/src/app/guards.hy"),
        "(val MODULE-TAGS {:context \"app\" :role \"macro\"})\n(defmacro g [] `(thaw 1))\n",
    )
    .unwrap();
    let (_, reached) = editor(dir.path());
    assert_eq!(keys(&reached, "DOEFF110"), vec!["pkg/src/app/runtime.hy::DOEFF110::decide"], "{}", reached);
}

#[test]
fn without_a_declaration_or_a_reason_the_definition_rule_still_hits() {
    // Failure case 1: without the declaration, the defn of both modules fires.
    let dir = exempt_repo(None);
    let (_, report) = editor(dir.path());
    assert_eq!(
        keys(&report, "DOEFF110"),
        vec!["pkg/src/app/expand.hy::DOEFF110::helper", "pkg/src/app/runtime.hy::DOEFF110::decide"],
        "{}",
        report
    );
    // Failure case 2: an exclusion with no reason is a declaration read error, and exempts nothing (it is not silently made green).
    let reasonless = exempt_repo(Some(&EXEMPT_DECLARATION.replace("REASON", "")));
    let (_, report) = editor(reasonless.path());
    assert_eq!(
        keys(&report, "DOEFF110"),
        vec!["pkg/src/app/expand.hy::DOEFF110::helper", "pkg/src/app/runtime.hy::DOEFF110::decide"],
        "{}",
        report
    );
}

/// The sample repo for DOEFF174 — service billing and shared with layers core, intent, protocol, foundation. `lower_layer` = whether to write :lower-layer-modules in architecture.hy's
/// :business-fakes (it is not judged in a repo that does not write it).
fn lower_layer_repo(lower_layer: bool) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    let declared = if lower_layer { " :lower-layer-modules [\"doeff_records.effects\"]" } else { "" };
    std::fs::write(
        dir.path().join("architecture.hy"),
        format!(
            r#"(defarchitecture sample
  :root "app"
  :layers [(layer core :roles [judgment program type])
           (layer intent :roles [intent type])
           (layer protocol :roles [protocol])
           (layer foundation :roles [foundation])]
  :foundation foundation
  :business-fakes {{:simulation ["app/sim/**"] :tests ["**/tests/**"] :production ["app/**"] :business-modules ["app.billing"]{}}})
(defservice billing "Billing" {{:depends-on [shared] :layers [core intent protocol]}})
(defservice shared "Shared" {{:layers [core]}})
"#,
            declared
        ),
    )
    .unwrap();
    std::fs::write(dir.path().join("pyproject.toml"), "[tool.doeff-linter]\nenable = [\"DOEFF174\"]\n").unwrap();
    let files = [
        (
            // Does not hit: the intent layer imports and uses value types (ChangedRow, Row, RowType).
            "app/billing/intent/orders.hy",
            "(import doeff_records.event_source [ChangedRow])\n(import doeff_records.values [Row])\n(import doeff_records.typed [RowType])\n\
             (defclass ReadOrder [])\n(defclass OrderMoved [] (setv #^ ChangedRow changed None) (setv #^ RowType kind None))\n",
        ),
        (
            // A shared core defk emits a raw effect (this itself is also a definition of business layer core, so it hits).
            "app/shared/core/rows.hy",
            "(import doeff_records.effects [ReadRow])\n(defk read-order-row [key] (<- row (ReadRow \"orders\" key)) row)\n",
        ),
        (
            "app/billing/core/flow.hy",
            r#"(import doeff [with_handlers])
(import doeff_events [timer-handler])
(import doeff_records.effects [ReadRow PutRow])
(import doeff_records.event_source [ChangedRow read-signal-handler])
(import doeff_records.values [Row])
(import doeff_records.typed [RowType typed-row read-typed])
(import app.billing.intent.orders [ReadOrder])
(import app.shared.core.rows [read-order-row])

;; Hits: emits a raw effect directly.
(defk direct-read [key] (<- row (ReadRow "orders" key)) row)

;; Hits: calls a shared defk, which emits a raw effect.
(defk via-shared [key] (<- row (read-order-row key)) row)

;; Hits: wraps a signal-source handler (doeff_records.event_source) with with_handlers.
(defk watch [tables body] (with_handlers [(timer-handler) (read-signal-handler tables "billing")] body))

;; Hits: runs a defk outside the repo in the same package (read-typed, which wraps an effect).
(defk typed-read [kind key] (<- row (read-typed kind key)) row)

;; Does not hit: use of value types (ChangedRow, Row, RowType; typed-row is not run).
(defk changed-key [changed] {:pre [(: changed ChangedRow)]} (. changed key))
(defk as-typed [#^ RowType kind #^ Row row] (typed-row kind row))

;; Does not hit: emits only domain effects.
(defk domain-read [key] (<- order (ReadOrder)) order)

;; Does not hit (the name is not statically decided): wraps a handler received as an answer, as a value.
(defk given-handlers [opened body] (with_handlers (list opened.handlers) body))
"#,
        ),
        (
            // Does not hit: a translation-layer handler emits a raw effect and wraps a signal source.
            "app/billing/protocol/reads.hy",
            r#"(import doeff [with_handlers])
(import doeff_records.effects [ReadRow])
(import doeff_records.event_source [read-signal-handler])
(import app.billing.intent.orders [ReadOrder])
(defhandler order-reads
  (ReadOrder []
    (<- row (ReadRow "orders" "k"))
    (resume row)))
(defk with-signals [tables body] (with_handlers [(read-signal-handler tables "billing")] body))
"#,
        ),
        (
            // Does not hit: the foundation layer wraps a records client handler and emits a raw effect.
            "app/foundation/records.hy",
            "(import doeff [with_handlers])\n(import doeff_records.http_client [http-records-handler])\n(import doeff_records.effects [PutRow])\n\
             (defk with-records [body] (with_handlers [(http-records-handler \"http://r\")] body))\n(defk put-one [row] (<- (PutRow \"t\" row)))\n",
        ),
    ];
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// Only handlers (protocol and foundation layers) use the records service's raw operations. A business layer
/// (core, intent) definition that emits an effect of :lower-layer-modules (directly, by following a defk, or by running a defk outside the repo in the same package), and
/// a place that wraps that package's handler, are critical. Use of value types, domain effects, and the translation and foundation layers do not hit.
#[test]
fn domain_layers_do_not_use_lower_layer_effects_or_handlers() {
    let dir = lower_layer_repo(true);
    let (code, report) = editor(dir.path());
    assert_eq!(report["errors"], serde_json::json!([]), "{}", report["errors"]);
    assert!(report["rules"].as_array().unwrap().iter().any(|r| r["rule"] == "DOEFF174" && r["wired"] == true), "{}", report["rules"]);
    assert_eq!(
        keys(&report, "DOEFF174"),
        vec![
            "app/billing/core/flow.hy::DOEFF174::direct_read::doeff_records.effects.ReadRow",
            "app/billing/core/flow.hy::DOEFF174::typed_read::doeff_records.typed.read_typed",
            "app/billing/core/flow.hy::DOEFF174::via_shared::doeff_records.effects.ReadRow",
            "app/billing/core/flow.hy::DOEFF174::watch::doeff_records.event_source.read_signal_handler",
            "app/shared/core/rows.hy::DOEFF174::read_order_row::doeff_records.effects.ReadRow",
        ]
    );
    assert_eq!(code, 1);
    let via = violation(&report, "app/billing/core/flow.hy::DOEFF174::via_shared::doeff_records.effects.ReadRow");
    assert_eq!(via["level"], "critical", "{}", via);
    assert!(via["message"].as_str().unwrap().contains("goes through read_order_row and emits the lower-layer effect ReadRow"), "{}", via["message"]);
    let handler = violation(&report, "app/billing/core/flow.hy::DOEFF174::watch::doeff_records.event_source.read_signal_handler");
    assert!(handler["message"].as_str().unwrap().contains("lower-layer handler"), "{}", handler["message"]);
    assert!(!handler["explanation"]["reason"].as_str().unwrap_or("").is_empty(), "{}", handler);

    // A pre-save single-file run (editor) gives the same hits.
    let source = std::fs::read_to_string(dir.path().join("app/billing/core/flow.hy")).unwrap();
    let (_, stdout, stderr) = run(dir.path(), &["--output-format", "editor-json", "--no-log", "--stdin", "--path", "app/billing/core/flow.hy"], Some(&source));
    let single: Value = serde_json::from_str(&stdout).unwrap_or_else(|e| panic!("{}: {}\n{}", e, stdout, stderr));
    let whole: Vec<String> = keys(&report, "DOEFF174").into_iter().filter(|k| k.starts_with("app/billing/core/flow.hy")).collect();
    assert_eq!(keys(&single, "DOEFF174"), whole);
}

/// Do not judge DOEFF174 when :lower-layer-modules is not written (the population is empty — shown as wired false in the rule list).
#[test]
fn lower_layer_uses_are_not_judged_without_the_declaration() {
    let dir = lower_layer_repo(false);
    let (_, report) = editor(dir.path());
    assert!(keys(&report, "DOEFF174").is_empty(), "{:?}", keys(&report, "DOEFF174"));
    assert!(report["rules"].as_array().unwrap().iter().any(|r| r["rule"] == "DOEFF174" && r["wired"] == false), "{}", report["rules"]);
}
