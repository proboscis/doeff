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
            ("app/foundation/clock.hy", "(val MODULE-TAGS {:context \"io\" :role \"foundation\"})\n(import time)\n(defn now [] (time.time))\n"),
        ],
        "",
    );
    let (_, report) = editor(dir.path());
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
    assert_eq!(report["version"], 1);
    assert!(report["root"].as_str().unwrap().starts_with('/'));
    assert_eq!(report["errors"], serde_json::json!([]));
    for field in ["violations", "modules", "rules", "errors"] {
        assert!(report[field].is_array(), "{}", field);
    }
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

    // 設定の名前の食い違いは黙って捨てず終了コード 2。
    let broken = repo(&[], "[tool.doeff-linter.raw_side_effects.extra]\n");
    let (code, _, stderr) = run(broken.path(), &["--output-format", "editor-json", "--no-log"], None);
    assert_eq!(code, 2, "{}", stderr);
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
