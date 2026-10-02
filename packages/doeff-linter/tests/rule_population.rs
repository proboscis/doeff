//! 層の宣言で規則の母集団から外す形(`:modules` と `:exempt`)と DOEFF032 の検(agora-redesign #2811)。
//!
//! 一時の repo の根に `.git` を置き、package `pkg` の architecture.hy に起動の時点の層 startup(名指しの module `guard`・DOEFF004 を
//! 理由つきで外す・業務の module を禁じる)を宣言する。linter は根から(設定なしで)走らせる — doeff の根の実行(pre-commit・make
//! lint-doeff)と同じく、package の宣言は設定に名指されていない。それでも file から上へ最も近い宣言を引いて母集団を決めること:
//!   (a) 外した層の module(guard)の os.environ の読みは DOEFF004 に当たらない
//!   (b) 外していない module(biz)の os.environ の読みは、今までどおり DOEFF004 に当たる
//!   (c) 外した層の module が業務の module(doeff)を import すると DOEFF032 に当たる(外した層に業務の code が入ったら赤)
//!   (d) 宣言が無ければ guard も DOEFF004 に当たる(除外は宣言からだけ来る)
//!   (e) 理由の無い除外は宣言の誤り — 外さずに DOEFF004 が当たり、読めないことを DOEFF032 が名指す

use serde_json::Value;
use std::path::Path;
use std::process::Command;

const ENV_READ: &str =
    "import os\n\n\ndef store_dir() -> str:\n    return os.environ.get(\"STORE\", \"\")\n";

const DECLARATION: &str = r#"
(defarchitecture pkg
  :root "pkg"
  :layers [(layer startup
             :summary "Python の起動の時点(.pth)で入る見張り"
             :modules [guard]
             :exempt [(rule DOEFF004 "Program の外で起動の時点に入るので、設定を Ask で受ける入口が無い")]
             :forbid-modules [doeff doeff_hy])])
"#;

fn repo(files: &[(&str, &str)]) -> tempfile::TempDir {
    let dir = tempfile::TempDir::new().unwrap();
    std::fs::create_dir_all(dir.path().join(".git")).unwrap();
    for (rel, text) in files {
        let path = dir.path().join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }
    dir
}

/// 根から設定なしで走らせ、(規則, file の相対 path) の組を返す。
fn hits(root: &Path) -> Vec<(String, String)> {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args([
            "--no-config",
            "--no-log",
            "--enable",
            "DOEFF004,DOEFF032",
            "--output-format",
            "json",
            ".",
        ])
        .current_dir(root)
        .output()
        .unwrap();
    let report: Value = serde_json::from_slice(&output.stdout).unwrap_or_else(|e| {
        panic!(
            "JSON でない出力 ({}): {} / {}",
            e,
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        )
    });
    let mut out: Vec<(String, String)> = report
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|group| {
            let rule = group["rule"].as_str().unwrap_or("").to_string();
            group["violations"]
                .as_array()
                .cloned()
                .unwrap_or_default()
                .into_iter()
                .map(move |v| {
                    (
                        rule.clone(),
                        v["file"]
                            .as_str()
                            .unwrap_or("")
                            .trim_start_matches("./")
                            .to_string(),
                    )
                })
        })
        .collect();
    out.sort();
    out.dedup();
    out
}

fn package(guard: &str, declaration: Option<&str>) -> tempfile::TempDir {
    let mut files = vec![
        ("pkg/src/guard/__init__.py", ""),
        ("pkg/src/guard/hooks.py", guard),
        ("pkg/src/biz/__init__.py", ""),
        ("pkg/src/biz/settings.py", ENV_READ),
    ];
    if let Some(text) = declaration {
        files.push(("pkg/architecture.hy", text));
    }
    repo(&files)
}

#[test]
fn an_exempted_layer_module_is_out_of_the_rule_population_and_others_are_not() {
    let dir = package(ENV_READ, Some(DECLARATION));
    assert_eq!(
        hits(dir.path()),
        vec![(
            "DOEFF004".to_string(),
            "pkg/src/biz/settings.py".to_string()
        )]
    );
}

#[test]
fn business_code_in_the_exempted_layer_is_red() {
    let guard = format!("from doeff import do\n\n{}", ENV_READ);
    let dir = package(&guard, Some(DECLARATION));
    assert_eq!(
        hits(dir.path()),
        vec![
            (
                "DOEFF004".to_string(),
                "pkg/src/biz/settings.py".to_string()
            ),
            ("DOEFF032".to_string(), "pkg/src/guard/hooks.py".to_string()),
        ]
    );
    // 関数の中の import も同じ(起動の時点の module は読み込みを遅らせて関数の中で import する)。
    let nested = format!(
        "{}\n\ndef late():\n    import doeff_hy.macros\n    return doeff_hy.macros\n",
        ENV_READ
    );
    let dir = package(&nested, Some(DECLARATION));
    assert!(
        hits(dir.path()).contains(&("DOEFF032".to_string(), "pkg/src/guard/hooks.py".to_string()))
    );
}

#[test]
fn without_a_declaration_the_layer_module_is_in_the_population() {
    let dir = package(ENV_READ, None);
    assert_eq!(
        hits(dir.path()),
        vec![
            (
                "DOEFF004".to_string(),
                "pkg/src/biz/settings.py".to_string()
            ),
            ("DOEFF004".to_string(), "pkg/src/guard/hooks.py".to_string()),
        ]
    );
}

#[test]
fn an_exemption_without_a_reason_is_not_applied_and_is_named() {
    let declaration = DECLARATION.replace(
        "\"Program の外で起動の時点に入るので、設定を Ask で受ける入口が無い\"",
        "\"\"",
    );
    let dir = package(ENV_READ, Some(&declaration));
    let found = hits(dir.path());
    assert!(
        found.contains(&("DOEFF004".to_string(), "pkg/src/guard/hooks.py".to_string())),
        "{:?}",
        found
    );
    assert!(
        found.contains(&("DOEFF032".to_string(), "pkg/src/guard/hooks.py".to_string())),
        "{:?}",
        found
    );
}

/// doeff の repo の `packages/architecture.hy`(agora-redesign #2859 — Rust の package の PEP 517 の build の入口を DOEFF004 から外す)
/// をそのまま一時の repo に置いて確かめる: 宣言を書き換えると、ここが赤になる。
const PACKAGES_DECLARATION: &str = include_str!("../../architecture.hy");

/// 自分の宣言を持たない package の根の build の入口と、同じ package の業務の module を置いた一時の repo。
fn packages_repo(backend: &str) -> tempfile::TempDir {
    repo(&[
        ("packages/architecture.hy", PACKAGES_DECLARATION),
        ("packages/doeff-vm/doeff_cargo_backend.py", backend),
        ("packages/doeff-vm/doeff_vm/__init__.py", ""),
        ("packages/doeff-vm/doeff_vm/settings.py", ENV_READ),
    ])
}

/// build の入口の os.environ の読みは DOEFF004 に当たらず、同じ package の業務の module の読みは今までどおり当たる
/// (外すのは名指しの module だけ・宣言は読めて DOEFF032 も出ない)。
#[test]
fn the_build_backend_is_out_of_the_environment_rule_and_business_code_is_not() {
    let dir = packages_repo(ENV_READ);
    assert_eq!(
        hits(dir.path()),
        vec![(
            "DOEFF004".to_string(),
            "packages/doeff-vm/doeff_vm/settings.py".to_string()
        )]
    );
}

/// build の入口が業務の module(doeff)を import すると DOEFF032 に当たる(外した層に業務の code が入ったら赤)。
#[test]
fn business_code_in_the_build_backend_is_red() {
    let backend = format!("import doeff\n{}", ENV_READ);
    let dir = packages_repo(&backend);
    let found = hits(dir.path());
    assert!(
        found.contains(&(
            "DOEFF032".to_string(),
            "packages/doeff-vm/doeff_cargo_backend.py".to_string()
        )),
        "{:?}",
        found
    );
}
