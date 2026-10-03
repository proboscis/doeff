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

/// 根から設定なしで repo 全体に走らせ、(規則, file の相対 path) の組を返す。
fn hits(root: &Path) -> Vec<(String, String)> {
    hits_of(root, ".")
}

/// 根から設定なしで `target`(根からの相対 path)だけに走らせ、(規則, file の相対 path) の組を返す。
fn hits_of(root: &Path, target: &str) -> Vec<(String, String)> {
    hits_of_rules(root, target, "DOEFF004,DOEFF032")
}

/// hits_of の、走らせる規則 `rules`(--enable の並び)を名指す形。
fn hits_of_rules(root: &Path, target: &str, rules: &str) -> Vec<(String, String)> {
    let output = Command::new(env!("CARGO_BIN_EXE_doeff-linter"))
        .args([
            "--no-config",
            "--no-log",
            "--enable",
            rules,
            "--output-format",
            "json",
            target,
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

/// doeff の repo の `tools/architecture.hy`(agora-redesign #2859 — Rust の package の PEP 517 の build の入口を DOEFF004 から外す)
/// をそのまま一時の repo に置いて確かめる: 宣言を書き換えると、ここが赤になる。
const BUILD_DECLARATION: &str = include_str!("../../../tools/architecture.hy");

/// doeff の repo と同じ形の一時の repo: build の入口の本物は tools/doeff_cargo_backend.py の 1 つで、Rust の package の根の
/// doeff_cargo_backend.py はそこへの symlink。同じ package に業務の module も置く。
/// (#2859 の最初の検は package の根に普通の file を置いたので、symlink を解くと宣言の外に出る本物の形を見落とした。)
#[cfg(unix)]
fn packages_repo(backend: &str) -> tempfile::TempDir {
    let dir = repo(&[
        ("tools/architecture.hy", BUILD_DECLARATION),
        ("tools/doeff_cargo_backend.py", backend),
        ("packages/doeff-vm/doeff_vm/__init__.py", ""),
        ("packages/doeff-vm/doeff_vm/settings.py", ENV_READ),
    ]);
    std::os::unix::fs::symlink(
        "../../tools/doeff_cargo_backend.py",
        dir.path().join("packages/doeff-vm/doeff_cargo_backend.py"),
    )
    .unwrap();
    dir
}

/// build の入口の os.environ の読みは、本物の path でも package の根の symlink の path でも DOEFF004 に当たらず、同じ package の
/// 業務の module の読みは今までどおり当たる(外すのは名指しの module だけ・宣言は読めて DOEFF032 も出ない)。
#[cfg(unix)]
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
/// repo 全体を当てると、symlink とその本物は 1 つの file として本物の path で 1 度だけ名指し(agora-redesign #2905)、
/// symlink の path だけを名指しても(pre-commit が変えた path を渡す形)当たる。
#[cfg(unix)]
#[test]
fn business_code_in_the_build_backend_is_red() {
    let backend = format!("import doeff\n{}", ENV_READ);
    let dir = packages_repo(&backend);
    let named_032 = |found: Vec<(String, String)>| -> Vec<String> {
        found
            .into_iter()
            .filter(|(rule, _)| rule == "DOEFF032")
            .map(|(_, path)| path)
            .collect()
    };
    assert_eq!(
        named_032(hits(dir.path())),
        vec!["tools/doeff_cargo_backend.py".to_string()]
    );
    assert_eq!(
        named_032(hits_of(dir.path(), "packages/doeff-vm/doeff_cargo_backend.py")),
        vec!["packages/doeff-vm/doeff_cargo_backend.py".to_string()]
    );
}

/// 環境を読む module を 1 つに寄せて層の宣言で外した package(agora-redesign #2860)— doeff の repo の宣言をそのまま一時の repo に
/// 置いて確かめる: 宣言を書き換えると、ここが赤になる。(宣言の path・寄せた module の path・同じ package のほかの module の path)
/// doeff-hy と doeff-flow は環境変数を ReadEnvironment の効果で問う形にして層を外した(agora-redesign #3012)ので、表から外した。
const ENVIRONMENT_PACKAGES: [(&str, &str, &str, &str); 1] = [
    (
        "packages/doeff-effect-analyzer/architecture.hy",
        include_str!("../../doeff-effect-analyzer/architecture.hy"),
        "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/env_places.py",
        "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/program_effects.py",
    ),
];

/// 宣言・寄せた module(本文 `places`)・同じ package のほかの module(環境を読む)・package の `__init__.py` を置いた一時の repo。
fn environment_package(declaration_path: &str, declaration: &str, places_path: &str, places: &str, sibling_path: &str) -> tempfile::TempDir {
    let init = format!("{}/__init__.py", Path::new(places_path).parent().unwrap().display());
    repo(&[
        (declaration_path, declaration),
        (init.as_str(), ""),
        (places_path, places),
        (sibling_path, ENV_READ),
    ])
}

/// 寄せた module の環境の読みは DOEFF004 に当たらず、同じ package のほかの module の読みは今までどおり当たる(宣言は読めて
/// DOEFF032 も出ない)。
#[test]
fn only_the_named_environment_module_is_out_of_the_environment_rule() {
    for (declaration_path, declaration, places_path, sibling_path) in ENVIRONMENT_PACKAGES {
        let dir = environment_package(declaration_path, declaration, places_path, ENV_READ, sibling_path);
        assert_eq!(
            hits(dir.path()),
            vec![("DOEFF004".to_string(), sibling_path.to_string())],
            "{}",
            declaration_path
        );
    }
}

/// 寄せた module が業務の module(doeff)を import すると DOEFF032 に当たる(外した層に業務の code が入ったら赤)。
#[test]
fn business_code_in_the_environment_module_is_red() {
    let places = format!("import doeff\n{}", ENV_READ);
    for (declaration_path, declaration, places_path, sibling_path) in ENVIRONMENT_PACKAGES {
        let dir = environment_package(declaration_path, declaration, places_path, &places, sibling_path);
        let found = hits(dir.path());
        assert!(
            found.contains(&("DOEFF032".to_string(), places_path.to_string())),
            "{}: {:?}",
            declaration_path,
            found
        );
    }
}

/// 名指しの module が `__init__.py` の無い dir に在る package(module の名 = file の名・agora-redesign #2861)の宣言 —
/// package の根に置き、tests の dir の素の module を名指して DOEFF004 を外す。
/// 以前は doeff の repo の宣言(doeff-agents の runner-env・doeff-openrouter の local-dotenv)をそのまま読んでいたが、2 つとも
/// 環境変数を ReadEnvironment で読む形になって DOEFF004 の外しを消した(agora-redesign #3012)。素の module を名指して外す仕組みは
/// linter に残るので、同じ形の宣言をここに書いて確かめ続ける(agora-redesign #3023)。
const BARE_MODULE_DECLARATION: &str = r#"
(defarchitecture bare
  :root "."
  :layers [(layer local-env
             :summary "走らせる人の手元の設定を読んで値を返す module"
             :modules [local_env]
             :exempt [(rule DOEFF004 "検の process の外の手元の設定を読む入口で、設定を Ask で受ける入口が無い")]
             :forbid-modules [doeff doeff_hy])])
"#;

/// (宣言の path・宣言・名指しの module の path・同じ dir のほかの module の path)
const BARE_MODULE_PACKAGES: [(&str, &str, &str, &str); 1] = [(
    "packages/bare/architecture.hy",
    BARE_MODULE_DECLARATION,
    "packages/bare/tests/local_env.py",
    "packages/bare/tests/conftest.py",
)];

/// 宣言・名指しの module(本文 `named`)・同じ dir のほかの module(環境を読む)を置いた一時の repo(`__init__.py` は置かない)。
fn bare_module_package(declaration_path: &str, declaration: &str, named_path: &str, named: &str, sibling_path: &str) -> tempfile::TempDir {
    repo(&[(declaration_path, declaration), (named_path, named), (sibling_path, ENV_READ)])
}

/// 名指しの module の環境の読みは DOEFF004 に当たらず、同じ dir のほかの module の読みは今までどおり当たる。
#[test]
fn only_the_named_bare_module_is_out_of_the_environment_rule() {
    for (declaration_path, declaration, named_path, sibling_path) in BARE_MODULE_PACKAGES {
        let dir = bare_module_package(declaration_path, declaration, named_path, ENV_READ, sibling_path);
        assert_eq!(
            hits(dir.path()),
            vec![("DOEFF004".to_string(), sibling_path.to_string())],
            "{}",
            named_path
        );
    }
}

/// 名指しの module が業務の module(doeff)を import すると DOEFF032 に当たる(外した層に業務の code が入ったら赤)。
#[test]
fn business_code_in_a_named_bare_module_is_red() {
    let named = format!("import doeff\n{}", ENV_READ);
    for (declaration_path, declaration, named_path, sibling_path) in BARE_MODULE_PACKAGES {
        let dir = bare_module_package(declaration_path, declaration, named_path, &named, sibling_path);
        let found = hits(dir.path());
        assert!(
            found.contains(&("DOEFF032".to_string(), named_path.to_string())),
            "{}: {:?}",
            named_path,
            found
        );
    }
}

/// hash で封をした記録の file を :files で path ごとに名指して外す宣言(agora-redesign #2934)— doeff の repo の宣言をそのまま
/// 一時の repo に置いて確かめる: 宣言を書き換えると、ここが赤になる。
const SEALED_DECLARATIONS: [(&str, &str); 2] = [
    ("docs/architecture.hy", include_str!("../../../docs/architecture.hy")),
    (
        "docs/design/seat-home-common-instructions-AJ8C0B/model/architecture.hy",
        include_str!("../../../docs/design/seat-home-common-instructions-AJ8C0B/model/architecture.hy"),
    ),
];

/// 宣言が名指す封の file(doeff の repo の記録と同じ path)。
const SEALED_FILES: [&str; 4] = [
    "docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/link_artifact_doors.py",
    "docs/design-checks/lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH/evidence/race_b_probe.py",
    "docs/design/seat-home-common-instructions-AJ8C0B/model/chain.py",
    "docs/design/seat-home-common-instructions-AJ8C0B/model/test_violations_are_rejected.py",
];

/// 同じ dir に置いた、名指していない file(封の表が控えていない file)。
const UNSEALED_SIBLINGS: [&str; 3] = [
    "docs/design/symlink-verbs-fail-vocabulary-ZCN5BD/evidence/unsealed.py",
    "docs/design-checks/lt-N23MQ5ZMSM6KCDKCB0G2RTFCAH/evidence/unsealed.py",
    "docs/design/seat-home-common-instructions-AJ8C0B/model/unsealed.py",
];

/// 封の file と同じ dir の file に置く中身: 環境変数の直の読み(DOEFF004 — 宣言は外さない)と引数の書き換え(DOEFF007 — 宣言 2 つが
/// 外す)を 1 つずつ。
const ENV_READ_AND_ARGUMENT_MUTATION: &str = "import os\n\n\ndef store_dir() -> str:\n    return os.environ.get(\"STORE\", \"\")\n\n\ndef add(items: list) -> None:\n    items.append(1)\n";

/// 封の検で走らせる規則。
const SEALED_RULES: &str = "DOEFF004,DOEFF007,DOEFF032";

/// file `path` の (規則, path) の組を、規則 `rules` の分だけ並べる。
fn expected_hits(paths: &[&str], rules: &[&str]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = paths
        .iter()
        .flat_map(|path| rules.iter().map(move |rule| (rule.to_string(), path.to_string())))
        .collect();
    out.sort();
    out
}

/// 名指した封の file は外した規則(DOEFF007)から外れ、同じ dir の名指していない file は今どおり当たる(dir ごと外れない)。宣言が
/// 外さない規則(DOEFF004 — 封の file の環境変数の直の読みは agora-redesign #3012 で直して外すのをやめた)は、封の file にも当たる。
/// 宣言は読めて DOEFF032 も出ない。
#[test]
fn only_the_named_sealed_files_are_out_of_the_rule_population() {
    let mut files: Vec<(&str, &str)> = SEALED_DECLARATIONS.to_vec();
    files.extend(SEALED_FILES.iter().map(|path| (*path, ENV_READ_AND_ARGUMENT_MUTATION)));
    files.extend(UNSEALED_SIBLINGS.iter().map(|path| (*path, ENV_READ_AND_ARGUMENT_MUTATION)));
    let dir = repo(&files);
    let mut expected = expected_hits(&SEALED_FILES, &["DOEFF004"]);
    expected.extend(expected_hits(&UNSEALED_SIBLINGS, &["DOEFF004", "DOEFF007"]));
    expected.sort();
    assert_eq!(hits_of_rules(dir.path(), ".", SEALED_RULES), expected);
}

/// 宣言が無ければ、封の file も今どおり当たる(除外は宣言の :files の名指しからだけ来る)。
#[test]
fn without_the_declarations_sealed_files_are_in_the_rule_population() {
    let files: Vec<(&str, &str)> = SEALED_FILES.iter().map(|path| (*path, ENV_READ_AND_ARGUMENT_MUTATION)).collect();
    let dir = repo(&files);
    assert_eq!(
        hits_of_rules(dir.path(), ".", SEALED_RULES),
        expected_hits(&SEALED_FILES, &["DOEFF004", "DOEFF007"])
    );
}
