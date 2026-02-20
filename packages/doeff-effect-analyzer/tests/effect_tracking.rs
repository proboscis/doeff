use std::collections::HashSet;
use std::fs;
use std::path::{Path, PathBuf};

use doeff_effect_analyzer::analyze_with_root;
use tempfile::tempdir;

fn write_module(root: &Path, name: &str, contents: &str) {
    let module_path = root.join(format!("{name}.py"));
    fs::write(&module_path, contents).expect("failed to write test module");
}

fn effect_keys(report: &doeff_effect_analyzer::Report) -> Vec<String> {
    report
        .summary
        .effects
        .iter()
        .map(|effect| effect.key.clone())
        .collect()
}

fn copy_fixture_package(root: &Path) {
    let src = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../doeff-test-target/src/doeff_test_target");
    copy_dir_recursive(&src, &root.join("doeff_test_target")).expect("copy fixture package");
}

fn copy_dir_recursive(src: &Path, dst: &Path) -> std::io::Result<()> {
    fs::create_dir_all(dst)?;
    for entry in fs::read_dir(src)? {
        let entry = entry?;
        let file_type = entry.file_type()?;
        let src_path = entry.path();
        let dst_path = dst.join(entry.file_name());
        if file_type.is_dir() {
            copy_dir_recursive(&src_path, &dst_path)?;
        } else {
            fs::copy(&src_path, &dst_path)?;
        }
    }
    Ok(())
}

#[test]
fn propagates_effects_across_kleisli_calls() {
    let tmp = tempdir().expect("tmpdir");
    let module_source = r#"
from doeff import do
from doeff.effects import ask, emit, log

@do
def fetch_user():
    repo = yield ask("user_repo")
    yield emit("user")
    return repo

@do
def main():
    yield fetch_user()
    yield log("done")
"#;
    write_module(tmp.path(), "module", module_source);

    let report = analyze_with_root(tmp.path(), "module.main").expect("analysis succeeded");
    assert!(
        report.summary.warnings.is_empty(),
        "unexpected warnings: {:?}",
        report.summary.warnings
    );

    let keys: HashSet<_> = effect_keys(&report).into_iter().collect();
    let expected: HashSet<_> = ["ask:user_repo", "emit:user", "log:done"]
        .into_iter()
        .map(String::from)
        .collect();

    assert_eq!(keys, expected, "simple chain keys mismatch");
}

#[test]
fn propagates_effects_for_program_values() {
    let tmp = tempdir().expect("tmpdir");
    let module_source = r#"
from doeff import do
from doeff.effects import ask, emit, log

@do
def fetch_user():
    yield ask("user_repo")
    yield emit("user")

@do
def main():
    yield fetch_user()
    yield log("done")

program = main()
"#;
    write_module(tmp.path(), "module", module_source);

    let report = analyze_with_root(tmp.path(), "module.program").expect("analysis succeeded");
    assert!(
        report.summary.warnings.is_empty(),
        "unexpected warnings: {:?}",
        report.summary.warnings
    );

    let keys: HashSet<_> = effect_keys(&report).into_iter().collect();
    let expected: HashSet<_> = ["ask:user_repo", "emit:user", "log:done"]
        .into_iter()
        .map(String::from)
        .collect();

    assert_eq!(keys, expected, "program value keys mismatch");
}

#[test]
fn complex_program_structure() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let report = analyze_with_root(tmp.path(), "doeff_test_target.orchestrate.orchestrate")
        .expect("analysis succeeded");
    assert!(
        report.summary.warnings.is_empty(),
        "unexpected warnings: {:?}",
        report.summary.warnings
    );

    let keys: HashSet<_> = effect_keys(&report).into_iter().collect();
    let expected: HashSet<_> = [
        "log:orchestrate",
        "ask:alpha",
        "ask:beta",
        "emit:beta",
        "ask:gamma",
        "log:gamma",
        "ask:delta",
        "ask:epsilon",
        "ask:zeta",
        "ask:eta",
        "ask:theta",
        "emit:theta",
        "ask:iota",
        "log:iota",
        "ask:kappa",
    ]
    .into_iter()
    .map(String::from)
    .collect();

    assert_eq!(keys, expected, "complex orchestrate keys mismatch");
}

#[test]
fn scenario_traverse_items() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.traverse.traverse_items",
    )
    .expect("analysis succeeded");

    let keys: HashSet<_> = effect_keys(&report).into_iter().collect();
    let expected: HashSet<_> = [
        "ask:alpha",
        "ask:beta",
        "emit:beta",
        "ask:gamma",
        "log:gamma",
    ]
    .into_iter()
    .map(String::from)
    .collect();
    assert_eq!(keys, expected);
}

#[test]
fn scenario_first_success_some() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let success_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.first_choice.choose_first_success",
    )
    .expect("analysis succeeded");
    let success_keys: HashSet<_> = effect_keys(&success_report).into_iter().collect();
    let success_expected: HashSet<_> = ["ask:alpha", "ask:beta", "emit:beta"]
        .into_iter()
        .map(String::from)
        .collect();
    assert_eq!(success_keys, success_expected);

    let some_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.first_choice.choose_first_some",
    )
    .expect("analysis succeeded");
    let some_keys: HashSet<_> = effect_keys(&some_report).into_iter().collect();
    assert!(some_keys.contains("ask:alpha"));
    assert!(some_keys.contains("ask:beta"));
}

#[test]
fn scenario_intercept_and_lift() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let intercept_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.intercepting.intercepted_alpha",
    )
    .expect("analysis succeeded");
    let intercept_keys: HashSet<_> = effect_keys(&intercept_report).into_iter().collect();
    assert_eq!(intercept_keys, HashSet::from([String::from("ask:alpha")]));

    let lift_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.lifting.lifted_alpha",
    )
    .expect("analysis succeeded");
    let lift_keys: HashSet<_> = effect_keys(&lift_report).into_iter().collect();
    assert_eq!(lift_keys, HashSet::from([String::from("ask:alpha")]));

    let dict_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.lifting.dict_builder",
    )
    .expect("analysis succeeded");
    let dict_keys: HashSet<_> = effect_keys(&dict_report).into_iter().collect();
    assert!(dict_keys.contains("ask:alpha"));
    assert!(dict_keys.contains("ask:beta"));
}

#[test]
fn scenario_comprehension_decorated_methods() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let comps = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.comprehensions.comprehension_effects",
    )
    .expect("analysis succeeded");
    let comps_keys: HashSet<_> = effect_keys(&comps).into_iter().collect();
    let expected_comps: HashSet<_> = ["ask:alpha", "ask:beta", "emit:beta"]
        .into_iter()
        .map(String::from)
        .collect();
    assert_eq!(comps_keys, expected_comps);

    let decorated = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.decorated.decorated_alpha",
    )
    .expect("analysis succeeded");
    let decorated_keys: HashSet<_> = effect_keys(&decorated).into_iter().collect();
    assert!(decorated_keys.contains("ask:alpha"));

    let instance = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.methods.run_instance_method",
    )
    .expect("analysis succeeded");
    let instance_keys: HashSet<_> = effect_keys(&instance).into_iter().collect();
    assert!(instance_keys.contains("ask:alpha"));

    let class_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.methods.run_class_method",
    )
    .expect("analysis succeeded");
    let class_keys: HashSet<_> = effect_keys(&class_report).into_iter().collect();
    assert!(class_keys.contains("ask:beta"));
}

#[test]
fn scenario_pattern_try_dataclass() {
    let tmp = tempdir().expect("tmpdir");
    copy_fixture_package(tmp.path());

    let pattern = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.pattern.pattern_matcher",
    )
    .expect("analysis succeeded");
    let pattern_keys: HashSet<_> = effect_keys(&pattern).into_iter().collect();
    assert!(pattern_keys.contains("ask:alpha"));
    assert!(pattern_keys.contains("ask:beta"));

    let try_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.try_except.try_except_yield",
    )
    .expect("analysis succeeded");
    let try_keys: HashSet<_> = effect_keys(&try_report).into_iter().collect();
    assert!(try_keys.contains("ask:alpha"));

    let dataclass_report = analyze_with_root(
        tmp.path(),
        "doeff_test_target.scenarios.dataclasses.dataclass_program",
    )
    .expect("analysis succeeded");
    let dataclass_keys: HashSet<_> = effect_keys(&dataclass_report).into_iter().collect();
    assert!(dataclass_keys.contains("ask:alpha"));
}
