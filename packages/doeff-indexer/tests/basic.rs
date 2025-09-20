use std::fs;
use std::path::Path;

use doeff_indexer::{
    build_index, entry_matches, EntryCategory, ItemKind, ParameterKind, ProgramTypeKind,
    ProgramTypeUsage,
};

fn write_file<P: AsRef<Path>>(path: P, contents: &str) {
    if let Some(parent) = path.as_ref().parent() {
        fs::create_dir_all(parent).expect("create parent directories");
    }
    fs::write(path, contents).expect("write test file");
}

fn type_usage_contains(usages: &[ProgramTypeUsage], kind: ProgramTypeKind, needle: &str) -> bool {
    usages.iter().any(|usage| {
        usage.kind == kind
            && (usage.raw == needle
                || usage
                    .type_arguments
                    .iter()
                    .any(|arg| arg == needle || arg == needle.trim()))
    })
}

#[test]
fn indexes_do_and_program_definitions() {
    let temp = tempfile::tempdir().expect("tempdir");
    let root = temp.path();
    let file_path = root.join("doeff").join("sample.py");

    write_file(
        &file_path,
        r#"from doeff import do, Program, ProgramInterpreter

@do
def greet(name: str):
    yield Program.pure(f"hello {name}")


def run(program: Program[int]) -> Program[int]:
    return program


def interpret_program(program: Program[int]) -> int:
    return 42


def execute(engine: ProgramInterpreter):
    return engine


workflow: Program[int] = Program.pure(5)
"#,
    );

    let index = build_index(root).expect("build index");
    let entries = index.entries;

    let greet = entries
        .iter()
        .find(|entry| entry.qualified_name.ends_with("greet"))
        .expect("greet entry");
    assert!(greet.categories.contains(&EntryCategory::DoFunction));
    assert!(greet.categories.contains(&EntryCategory::KleisliProgram));
    assert_eq!(greet.item_kind, ItemKind::Function);
    assert!(type_usage_contains(
        &greet.type_usages,
        ProgramTypeKind::KleisliProgram,
        "KleisliProgram"
    ));

    let run = entries
        .iter()
        .find(|entry| entry.qualified_name.ends_with("run"))
        .expect("run entry");
    assert!(run.categories.contains(&EntryCategory::AcceptsProgramParam));
    assert!(run.categories.contains(&EntryCategory::ReturnsProgram));
    assert!(run.categories.contains(&EntryCategory::ProgramInterpreter));
    assert!(run.categories.contains(&EntryCategory::ProgramTransformer));
    assert_eq!(run.program_parameters.len(), 1);
    let param = &run.program_parameters[0];
    assert_eq!(param.name, "program");
    assert_eq!(param.annotation.as_deref(), Some("Program[int]"));
    assert!(param.is_required);
    assert_eq!(param.position, 0);
    assert!(matches!(param.kind, ParameterKind::Positional));
    assert_eq!(run.return_annotation.as_deref(), Some("Program[int]"));
    assert!(type_usage_contains(
        &run.type_usages,
        ProgramTypeKind::Program,
        "int"
    ));
    assert!(entry_matches(
        run,
        Some(ProgramTypeKind::Program),
        Some("int")
    ));

    let interpret = entries
        .iter()
        .find(|entry| entry.qualified_name.ends_with("interpret_program"))
        .expect("interpret entry");
    assert!(interpret
        .categories
        .contains(&EntryCategory::ProgramInterpreter));
    assert!(interpret
        .categories
        .contains(&EntryCategory::AcceptsProgramParam));
    assert!(!interpret
        .categories
        .contains(&EntryCategory::ProgramTransformer));
    assert_eq!(interpret.program_parameters.len(), 1);
    let interpret_param = &interpret.program_parameters[0];
    assert_eq!(interpret_param.annotation.as_deref(), Some("Program[int]"));
    assert!(interpret_param.is_required);
    assert!(matches!(interpret_param.kind, ParameterKind::Positional));

    let execute = entries
        .iter()
        .find(|entry| entry.qualified_name.ends_with("execute"))
        .expect("execute entry");
    assert!(execute
        .categories
        .contains(&EntryCategory::AcceptsProgramInterpreterParam));

    let workflow = entries
        .iter()
        .find(|entry| entry.qualified_name.ends_with("workflow"))
        .expect("workflow entry");
    assert!(workflow.categories.contains(&EntryCategory::Program));
    assert_eq!(workflow.item_kind, ItemKind::Assignment);
    assert!(workflow.qualified_name.ends_with("workflow"));
    assert!(type_usage_contains(
        &workflow.type_usages,
        ProgramTypeKind::Program,
        "int"
    ));
    assert!(entry_matches(
        workflow,
        Some(ProgramTypeKind::Program),
        Some("Any")
    ));
}
