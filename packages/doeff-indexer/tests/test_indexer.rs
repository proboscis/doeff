use doeff_indexer::{
    build_index, find_interceptors, find_interpreters, find_kleisli, find_kleisli_with_type,
    find_transforms, EntryCategory,
};
use std::fs;
use std::path::Path;
use tempfile::TempDir;

/// Helper to create a test Python file
fn create_test_file(dir: &Path, name: &str, content: &str) {
    let file_path = dir.join(name);
    fs::write(file_path, content).unwrap();
}

#[test]
fn test_interpreter_marker_detection() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_interpreters.py",
        r#"
from doeff import Program

# Marked interpreter - should be found
def run_program(p: Program[int]) -> int:  # doeff: interpreter
    return p.run()

# Unmarked interpreter - should NOT be found by find-interpreters
def execute_program(p: Program[int]) -> int:
    return p.run()

# Wrong return type but has marker - will be found (marker takes precedence)
def fake_interpreter(p: Program[int]) -> Program[int]:  # doeff: interpreter
    return p
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();
    let interpreters = find_interpreters(&index.entries);

    // Should find 2 interpreters with markers
    assert_eq!(interpreters.len(), 2);

    // Check names
    let names: Vec<&str> = interpreters.iter().map(|e| e.name.as_str()).collect();
    assert!(names.contains(&"run_program"));
    assert!(names.contains(&"fake_interpreter"));

    // execute_program should NOT be in the list (no marker)
    assert!(!names.contains(&"execute_program"));
}

#[test]
fn test_transform_detection() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_transforms.py",
        r#"
from doeff import Program, do

# Marked transform
def map_program(p: Program[int]) -> Program[str]:  # doeff: transform
    return p.map(str)

# @do with Program param -> Transform (even without marker)
@do
def do_transform(p: Program[int]) -> str:
    result = yield p
    return str(result)

# Unmarked transform - NOT found without marker
def chain_program(p: Program[int]) -> Program[int]:
    return p.map(lambda x: x * 2)
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();
    let transforms = find_transforms(&index.entries);

    // Should only find map_program (has marker)
    // do_transform doesn't have marker, so not found by find-transforms
    assert_eq!(transforms.len(), 1);
    assert_eq!(transforms[0].name, "map_program");
}

#[test]
fn test_kleisli_detection() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_kleisli.py",
        r#"
from doeff import Program, do
from typing import Any

# @do functions are automatically Kleisli
@do
def fetch_user(user_id: str) -> dict:
    return {"id": user_id}

# @do with Any parameter
@do
def process_any(data: Any) -> str:
    return str(data)

# Marked Kleisli
def create_program(value: int) -> Program[str]:  # doeff: kleisli
    return Program.of(str(value))

# Unmarked function returning Program - NOT Kleisli without marker or @do
def make_program(x: int) -> Program[int]:
    return Program.of(x)
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();
    let kleisli = find_kleisli(&index.entries);

    // Should find 3: two @do functions and one marked
    assert_eq!(kleisli.len(), 3);

    let names: Vec<&str> = kleisli.iter().map(|e| e.name.as_str()).collect();
    assert!(names.contains(&"fetch_user"));
    assert!(names.contains(&"process_any"));
    assert!(names.contains(&"create_program"));
    assert!(!names.contains(&"make_program"));
}

#[test]
fn test_kleisli_type_filtering() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_kleisli_types.py",
        r#"
from doeff import Program, do
from typing import Any

@do
def process_string(s: str) -> int:
    return len(s)

@do
def process_int(n: int) -> str:
    return str(n)

@do
def process_any(data: Any) -> str:
    return str(data)

def marked_str(s: str) -> Program[int]:  # doeff: kleisli
    return Program.of(len(s))
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();

    // Filter by str type
    let str_kleisli = find_kleisli_with_type(&index.entries, "str");
    let str_names: Vec<&str> = str_kleisli.iter().map(|e| e.name.as_str()).collect();

    // Should find process_string, process_any (Any matches all), and marked_str
    assert_eq!(str_kleisli.len(), 3);
    assert!(str_names.contains(&"process_string"));
    assert!(str_names.contains(&"process_any"));
    assert!(str_names.contains(&"marked_str"));
    assert!(!str_names.contains(&"process_int"));

    // Filter by int type
    let int_kleisli = find_kleisli_with_type(&index.entries, "int");
    let int_names: Vec<&str> = int_kleisli.iter().map(|e| e.name.as_str()).collect();

    // Should find process_int and process_any
    assert_eq!(int_kleisli.len(), 2);
    assert!(int_names.contains(&"process_int"));
    assert!(int_names.contains(&"process_any"));
}

#[test]
fn test_interceptor_detection() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_interceptors.py",
        r#"
from doeff import Effect, do

# Marked interceptor
def log_interceptor(effect: Effect) -> Effect:  # doeff: interceptor
    return LogEffect(f"[LOG] {effect}")

# @do with Effect param but no marker - NOT found by find-interceptors
@do
def do_interceptor(effect: Effect) -> str:
    yield effect
    return "done"

# Another marked interceptor
def wrap_effect(e: LogEffect) -> LogEffect:  # doeff: interceptor
    return LogEffect(f"wrapped: {e.message}")

class LogEffect(Effect):
    def __init__(self, message: str):
        self.message = message
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();
    let interceptors = find_interceptors(&index.entries);

    // Should only find marked interceptors
    assert_eq!(interceptors.len(), 2);

    let names: Vec<&str> = interceptors.iter().map(|e| e.name.as_str()).collect();
    assert!(names.contains(&"log_interceptor"));
    assert!(names.contains(&"wrap_effect"));
    assert!(!names.contains(&"do_interceptor"));
}

#[test]
fn test_do_decorator_categorization() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_do_categories.py",
        r#"
from doeff import Program, Effect, do

# @do with regular param -> Kleisli
@do
def kleisli_func(x: int) -> str:
    return str(x)

# @do with Program param -> Transform
@do
def transform_func(p: Program[int]) -> str:
    result = yield p
    return str(result)

# @do with Effect param -> Interceptor
@do
def interceptor_func(e: Effect) -> str:
    yield e
    return "done"
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();

    // Check kleisli_func
    let kleisli_entry = index
        .entries
        .iter()
        .find(|e| e.name == "kleisli_func")
        .unwrap();
    assert!(kleisli_entry
        .categories
        .contains(&EntryCategory::KleisliProgram));
    assert!(kleisli_entry
        .categories
        .contains(&EntryCategory::DoFunction));

    // Check transform_func
    let transform_entry = index
        .entries
        .iter()
        .find(|e| e.name == "transform_func")
        .unwrap();
    assert!(transform_entry
        .categories
        .contains(&EntryCategory::ProgramTransformer));
    assert!(transform_entry
        .categories
        .contains(&EntryCategory::DoFunction));

    // Check interceptor_func
    let interceptor_entry = index
        .entries
        .iter()
        .find(|e| e.name == "interceptor_func")
        .unwrap();
    assert!(interceptor_entry
        .categories
        .contains(&EntryCategory::Interceptor));
    assert!(interceptor_entry
        .categories
        .contains(&EntryCategory::DoFunction));
}

#[test]
fn test_class_method_detection() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_class_methods.py",
        r#"
from doeff import Program, do

class Controller:
    def run_program(self, p: Program[int]) -> int:  # doeff: interpreter
        return p.run()
    
    @do
    def fetch_data(self, key: str) -> dict:
        return {"key": key}
    
    def transform_data(self, p: Program[dict]) -> Program[str]:  # doeff: transform
        return p.map(str)
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();

    // Check interpreter detection
    let interpreters = find_interpreters(&index.entries);
    assert_eq!(interpreters.len(), 1);
    assert_eq!(interpreters[0].name, "Controller.run_program");

    // Check Kleisli detection (@do method)
    let kleisli = find_kleisli(&index.entries);
    assert_eq!(kleisli.len(), 1);
    assert_eq!(kleisli[0].name, "Controller.fetch_data");

    // Check transform detection
    let transforms = find_transforms(&index.entries);
    assert_eq!(transforms.len(), 1);
    assert_eq!(transforms[0].name, "Controller.transform_data");
}

#[test]
fn test_multiple_markers() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_multi_markers.py",
        r#"
from doeff import Program

# Function with multiple markers
def hybrid(x: int) -> Program[int]:  # doeff: kleisli transform
    return Program.of(x * 2)
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();

    // Should be found by both find-kleisli and find-transforms
    let kleisli = find_kleisli(&index.entries);
    let transforms = find_transforms(&index.entries);

    assert_eq!(kleisli.len(), 1);
    assert_eq!(kleisli[0].name, "hybrid");

    assert_eq!(transforms.len(), 1);
    assert_eq!(transforms[0].name, "hybrid");

    let hybrid_entry = index.entries.iter().find(|e| e.name == "hybrid").unwrap();
    assert!(
        hybrid_entry.categories.contains(&EntryCategory::HasMarker),
        "hybrid entry should include HasMarker category"
    );
}

#[test]
fn test_marker_case_insensitivity() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_case.py",
        r#"
from doeff import Program

def upper(p: Program[int]) -> int:  # doeff: INTERPRETER
    return p.run()

def mixed(p: Program[int]) -> int:  # doeff: InTeRpReTeR
    return p.run()

def lower(p: Program[int]) -> int:  # doeff: interpreter
    return p.run()
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();
    let interpreters = find_interpreters(&index.entries);

    // All three should be found regardless of case
    assert_eq!(interpreters.len(), 3);

    let names: Vec<&str> = interpreters.iter().map(|e| e.name.as_str()).collect();
    assert!(names.contains(&"upper"));
    assert!(names.contains(&"mixed"));
    assert!(names.contains(&"lower"));
}

#[test]
fn test_signature_based_categorization() {
    let temp_dir = TempDir::new().unwrap();

    create_test_file(
        temp_dir.path(),
        "test_categories.py",
        r#"
from doeff import Program, Effect

# These have no markers but should be categorized internally
def interpreter(p: Program[int]) -> int:
    return p.run()

def transformer(p: Program[int]) -> Program[str]:
    return p.map(str)

def kleisli(x: int) -> Program[str]:
    return Program.of(str(x))

def interceptor(e: Effect) -> Effect:
    return e
"#,
    );

    let index = build_index(temp_dir.path()).unwrap();

    // Check internal categorization (not marker-based)
    let interpreter_entry = index
        .entries
        .iter()
        .find(|e| e.name == "interpreter")
        .unwrap();
    assert!(interpreter_entry
        .categories
        .contains(&EntryCategory::ProgramInterpreter));
    assert!(interpreter_entry
        .categories
        .contains(&EntryCategory::AcceptsProgramParam));

    let transformer_entry = index
        .entries
        .iter()
        .find(|e| e.name == "transformer")
        .unwrap();
    assert!(transformer_entry
        .categories
        .contains(&EntryCategory::ProgramTransformer));
    assert!(transformer_entry
        .categories
        .contains(&EntryCategory::AcceptsProgramParam));
    assert!(transformer_entry
        .categories
        .contains(&EntryCategory::ReturnsProgram));

    let kleisli_entry = index.entries.iter().find(|e| e.name == "kleisli").unwrap();
    assert!(kleisli_entry
        .categories
        .contains(&EntryCategory::KleisliProgram));
    assert!(kleisli_entry
        .categories
        .contains(&EntryCategory::ReturnsProgram));

    let interceptor_entry = index
        .entries
        .iter()
        .find(|e| e.name == "interceptor")
        .unwrap();
    assert!(interceptor_entry
        .categories
        .contains(&EntryCategory::Interceptor));
    assert!(interceptor_entry
        .categories
        .contains(&EntryCategory::AcceptsEffectParam));

    // But they should NOT be found by find-* commands (no markers)
    assert_eq!(find_interpreters(&index.entries).len(), 0);
    assert_eq!(find_transforms(&index.entries).len(), 0);
    assert_eq!(find_interceptors(&index.entries).len(), 0);
    // Kleisli also needs marker or @do decorator
    assert_eq!(find_kleisli(&index.entries).len(), 0);
}
