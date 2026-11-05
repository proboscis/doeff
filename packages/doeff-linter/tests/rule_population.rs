//! The form that removes modules from the rule population via a layer declaration (`:modules` and `:exempt`), and the DOEFF032 check.
//!
//! Put `.git` at the root of a temporary repo and declare, in package `pkg`'s architecture.hy, a startup layer (named module `guard`, DOEFF004 exempted
//! with a reason, business modules forbidden). The linter runs from the root (with no config) — as in a root run of doeff (pre-commit, make
//! lint-doeff), the package's declaration is not named in the config. Even so, it must find the nearest declaration above the file and decide the population:
//!   (a) an os.environ read in a module (guard) of the exempted layer does not hit DOEFF004
//!   (b) an os.environ read in a non-exempted module (biz) hits DOEFF004 as before
//!   (c) if a module of the exempted layer imports a business module (doeff), DOEFF032 hits (red if business code enters an exempted layer)
//!   (d) without the declaration, guard hits DOEFF004 too (exemptions come only from the declaration)
//!   (e) an exemption without a reason is a declaration error — DOEFF004 hits without being exempted, and DOEFF032 names that it could not be read

use serde_json::Value;
use std::path::Path;
use std::process::Command;

const ENV_READ: &str =
    "import os\n\n\ndef store_dir() -> str:\n    return os.environ.get(\"STORE\", \"\")\n";

const DECLARATION: &str = r#"
(defarchitecture pkg
  :root "pkg"
  :layers [(layer startup
             :summary "A watcher installed at Python startup (.pth)"
             :modules [guard]
             :exempt [(rule DOEFF004 "Installed at startup outside any Program, so there is no entry point to receive config via Ask")]
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

/// Run over the whole repo from the root with no config, and return (rule, file's relative path) pairs.
fn hits(root: &Path) -> Vec<(String, String)> {
    hits_of(root, ".")
}

/// Run from the root with no config on only `target` (a path relative to the root), and return (rule, file's relative path) pairs.
fn hits_of(root: &Path, target: &str) -> Vec<(String, String)> {
    hits_of_rules(root, target, "DOEFF004,DOEFF032")
}

/// The form of hits_of that names the rules `rules` to run (a sequence of --enable).
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
            "non-JSON output ({}): {} / {}",
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
    // The same holds for an import inside a function (startup-time modules delay loading and import inside a function).
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
        "\"Installed at startup outside any Program, so there is no entry point to receive config via Ask\"",
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

/// Check by placing, in a temporary repo as is, doeff's repo `tools/architecture.hy` (which exempts the Rust package's PEP 517 build entry from DOEFF004):
/// rewriting the declaration turns this red.
const BUILD_DECLARATION: &str = include_str!("../../../tools/architecture.hy");

/// A temporary repo of the same form as doeff's repo: the real build entry is the single tools/doeff_cargo_backend.py, and the Rust package root's
/// doeff_cargo_backend.py is a symlink to it. A business module is placed in the same package too.
/// (The first check of #2859 placed an ordinary file at the package root, so it missed the real form, where resolving the symlink leaves the declaration's scope.)
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

/// An os.environ read in the build entry does not hit DOEFF004 via either the real path or the symlink path at the package root, while a read in a business module of the same package
/// hits as before (only the named module is exempted; the declaration is readable and DOEFF032 is not emitted either).
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

/// If the build entry imports a business module (doeff), DOEFF032 hits (red if business code enters an exempted layer).
/// When the whole repo is run, a symlink and its real file are named once, as a single file at the real path,
/// and it hits even when only the symlink path is named (the form where pre-commit passes the changed path).
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

/// A package that funnels environment reads into one module and exempts it via the layer declaration — places doeff's repo declaration as is in a temporary repo
/// to check: rewriting the declaration turns this red. (declaration path, funneled module path, path of another module in the same package)
/// doeff-hy and doeff-flow now ask for environment variables via the ReadEnvironment effect and left the layer exemption, so they were removed from the table.
const ENVIRONMENT_PACKAGES: [(&str, &str, &str, &str); 1] = [
    (
        "packages/doeff-effect-analyzer/architecture.hy",
        include_str!("../../doeff-effect-analyzer/architecture.hy"),
        "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_env_places.py",
        "packages/doeff-effect-analyzer/python/doeff_effect_analyzer/_program_effects.py",
    ),
];

/// A temporary repo containing the declaration, the funneled module (body `places`), another module of the same package (that reads the environment), and the package's `__init__.py`.
fn environment_package(declaration_path: &str, declaration: &str, places_path: &str, places: &str, sibling_path: &str) -> tempfile::TempDir {
    let init = format!("{}/__init__.py", Path::new(places_path).parent().unwrap().display());
    repo(&[
        (declaration_path, declaration),
        (init.as_str(), ""),
        (places_path, places),
        (sibling_path, ENV_READ),
    ])
}

/// An environment read in the funneled module does not hit DOEFF004, while a read in another module of the same package hits as before (the declaration is readable and
/// DOEFF032 is not emitted either).
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

/// If the funneled module imports a business module (doeff), DOEFF032 hits (red if business code enters an exempted layer).
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

/// Declaration for a package where the named module is in a dir without `__init__.py` (module name = file name) —
/// placed at the package root, naming a bare module in the tests dir to exempt DOEFF004.
/// It used to read doeff's repo declarations as is (doeff-agents' runner-env and doeff-openrouter's local-dotenv), but both
/// came to read environment variables via ReadEnvironment and removed their DOEFF004 exemption. The linter still has the mechanism for naming a bare module to exempt it,
/// so a declaration of the same form is written here and kept under check.
const BARE_MODULE_DECLARATION: &str = r#"
(defarchitecture bare
  :root "."
  :layers [(layer local-env
             :summary "A module that reads the local configuration of the person running it and returns values"
             :modules [local_env]
             :exempt [(rule DOEFF004 "Local-configuration reader outside the check process; there is no entry point to receive config via Ask")]
             :forbid-modules [doeff doeff_hy])])
"#;

/// (declaration path, declaration, path of the named module, path of another module in the same dir)
const BARE_MODULE_PACKAGES: [(&str, &str, &str, &str); 1] = [(
    "packages/bare/architecture.hy",
    BARE_MODULE_DECLARATION,
    "packages/bare/tests/local_env.py",
    "packages/bare/tests/conftest.py",
)];

/// A temporary repo with the declaration, the named module (body `named`), and another module in the same dir (that reads the environment) (no `__init__.py` is placed).
fn bare_module_package(declaration_path: &str, declaration: &str, named_path: &str, named: &str, sibling_path: &str) -> tempfile::TempDir {
    repo(&[(declaration_path, declaration), (named_path, named), (sibling_path, ENV_READ)])
}

/// An environment read in the named module does not hit DOEFF004, while a read in another module in the same dir hits as before.
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

/// If the named module imports a business module (doeff), DOEFF032 hits (red if business code enters an exempted layer).
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

/// A model of a declaration that exempts, by naming each hash-sealed record file by path with :files — one declaration under docs, and
/// one nearer declaration inside the record dir. Placed in a temporary repo and checked.
const SEALED_DECLARATIONS: [(&str, &str); 2] = [
    ("docs/architecture.hy", DOCS_SEALED_DECLARATION),
    ("docs/records/model/model/architecture.hy", MODEL_SEALED_DECLARATION),
];

/// The declaration under docs: names two record files and exempts only the rules that currently hit (DOEFF002 and DOEFF007).
const DOCS_SEALED_DECLARATION: &str = r#"(defarchitecture doeff-docs
  :root "."
  :layers [(layer sealed
             :summary "A hash-sealed record file — its content is held by the record seal table, so it is not rewritten"
             :knows "seal table"
             :does-not-know "doeff Programs, effects, and handlers"
             :files ["records/probe/evidence/link_artifact_doors.py"
                     "checks/probe/evidence/race_b_probe.py"]
             :exempt [(rule DOEFF002 "A file whose content the record seal table holds; rewriting it breaks the seal")
                      (rule DOEFF007 "A file whose content the record seal table holds; rewriting it breaks the seal")])])
"#;

/// The declaration inside the record dir: names one file of that dir and exempts only DOEFF007.
const MODEL_SEALED_DECLARATION: &str = r#"(defarchitecture sealed-model
  :root "."
  :layers [(layer sealed
             :summary "A hash-sealed record file — its content is held by the seal table, so it is not rewritten"
             :knows "seal table"
             :does-not-know "doeff Programs, effects, and handlers"
             :files ["chain.py"]
             :exempt [(rule DOEFF007 "A record file whose content the seal table holds; rewriting it breaks the seal")])])
"#;

/// The sealed file that the declaration names.
const SEALED_FILES: [&str; 3] = [
    "docs/records/probe/evidence/link_artifact_doors.py",
    "docs/checks/probe/evidence/race_b_probe.py",
    "docs/records/model/model/chain.py",
];

/// A file placed in the same dir that is not named (a file the seal table does not hold).
const UNSEALED_SIBLINGS: [&str; 3] = [
    "docs/records/probe/evidence/unsealed.py",
    "docs/checks/probe/evidence/unsealed.py",
    "docs/records/model/model/unsealed.py",
];

/// Content to place in files in the same dir as the sealed file: one direct environment-variable read (DOEFF004 — the declaration does not exempt it) and one argument mutation (DOEFF007 — two declarations
/// exempt it).
const ENV_READ_AND_ARGUMENT_MUTATION: &str = "import os\n\n\ndef store_dir() -> str:\n    return os.environ.get(\"STORE\", \"\")\n\n\ndef add(items: list) -> None:\n    items.append(1)\n";

/// Rules run in the seal check.
const SEALED_RULES: &str = "DOEFF004,DOEFF007,DOEFF032";

/// Line up (rule, path) pairs for file `path`, for each of the rules `rules`.
fn expected_hits(paths: &[&str], rules: &[&str]) -> Vec<(String, String)> {
    let mut out: Vec<(String, String)> = paths
        .iter()
        .flat_map(|path| rules.iter().map(move |rule| (rule.to_string(), path.to_string())))
        .collect();
    out.sort();
    out
}

/// The named sealed file is exempt from the exempted rule (DOEFF007), while the unnamed file in the same dir still hits as before (the exemption does not cover the whole dir). A rule the declaration does not
/// exempt (DOEFF004 — direct environment-variable reads in sealed files were fixed and the exemption dropped) still hits the sealed file.
/// The declaration is readable and DOEFF032 is not emitted.
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

/// Without the declaration, the sealed file also hits as before (exemptions come only from the :files naming in the declaration).
#[test]
fn without_the_declarations_sealed_files_are_in_the_rule_population() {
    let files: Vec<(&str, &str)> = SEALED_FILES.iter().map(|path| (*path, ENV_READ_AND_ARGUMENT_MUTATION)).collect();
    let dir = repo(&files);
    assert_eq!(
        hits_of_rules(dir.path(), ".", SEALED_RULES),
        expected_hits(&SEALED_FILES, &["DOEFF004", "DOEFF007"])
    );
}
