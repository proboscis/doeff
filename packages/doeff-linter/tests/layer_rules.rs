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
    // 移し先の案: service は :context のタグ、層は今の path の段の層の名(層が先の dir)か :role のタグ。
    assert_eq!(violation(&report, "app/core/legacy_core.hy::DOEFF114")["hint"], "app/kanban/core/legacy_core.hy へ移す(service は :context のタグ、層は今の置き場所か :role のタグから推した案)");
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
