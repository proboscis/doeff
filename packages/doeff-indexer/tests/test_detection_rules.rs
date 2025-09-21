use doeff_indexer::{
    build_index, find_interceptors, find_interpreters, find_kleisli, find_kleisli_with_type,
    find_transforms, EntryCategory,
};
use std::path::Path;

#[test]
fn test_interpreter_detection() {
    // Build index from test fixture
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Find interpreters (marker-only)
    let interpreters = find_interpreters(&index.entries);
    
    // Should only find functions with "interpreter" marker
    for entry in &interpreters {
        assert!(
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("interpreter")),
            "Found interpreter without marker: {}",
            entry.name
        );
    }

    // Verify categorization: interpreters should NOT return Program
    for entry in &index.entries {
        if entry.categories.contains(&EntryCategory::ProgramInterpreter) {
            // If categorized as interpreter, should not also be transformer
            // (unless it has wrong marker)
            if !entry.categories.contains(&EntryCategory::ProgramTransformer) {
                // Should not have Program return type
                if let Some(ret) = &entry.return_annotation {
                    assert!(
                        !ret.contains("Program"),
                        "Interpreter {} returns Program",
                        entry.name
                    );
                }
            }
        }
    }
}

#[test]
fn test_transform_detection() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Find transforms (marker-only)
    let transforms = find_transforms(&index.entries);
    
    // Should only find functions with "transform" marker
    for entry in &transforms {
        assert!(
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("transform")),
            "Found transform without marker: {}",
            entry.name
        );
    }

    // Verify categorization: transforms should return Program
    for entry in &index.entries {
        if entry.categories.contains(&EntryCategory::ProgramTransformer) {
            // Should accept Program parameter
            assert!(
                entry.categories.contains(&EntryCategory::AcceptsProgramParam),
                "Transform {} doesn't accept Program",
                entry.name
            );
            // Should return Program (or be a @do function which wraps return in Program)
            let is_do = entry.categories.contains(&EntryCategory::DoFunction);
            if !is_do {
                assert!(
                    entry.categories.contains(&EntryCategory::ReturnsProgram),
                    "Transform {} doesn't return Program",
                    entry.name
                );
            }
        }
    }
}

#[test]
fn test_kleisli_detection() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Find Kleisli functions (marker OR @do)
    let kleisli = find_kleisli(&index.entries);
    
    // Should find functions with "kleisli" marker OR @do decorator
    for entry in &kleisli {
        let has_marker = entry.markers.iter().any(|m| m.eq_ignore_ascii_case("kleisli"));
        let has_do = entry.categories.contains(&EntryCategory::DoFunction);
        assert!(
            has_marker || has_do,
            "Found Kleisli without marker or @do: {}",
            entry.name
        );
    }
}

#[test]
fn test_kleisli_type_filtering() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Test filtering by type argument
    let str_kleisli = find_kleisli_with_type(&index.entries, "str");
    
    for entry in &str_kleisli {
        // Should have first parameter matching "str" or "Any"
        if let Some(first_param) = entry.all_parameters.first() {
            if let Some(annotation) = &first_param.annotation {
                assert!(
                    annotation.contains("str") || annotation == "Any" || annotation.contains("Any"),
                    "Kleisli {} first param {} doesn't match str filter",
                    entry.name,
                    annotation
                );
            }
        }
    }

    // Test that Any matches all types
    let any_entries: Vec<_> = index
        .entries
        .iter()
        .filter(|e| {
            e.all_parameters
                .first()
                .and_then(|p| p.annotation.as_ref())
                .map(|a| a == "Any" || a.contains("Any"))
                .unwrap_or(false)
        })
        .collect();

    for type_arg in &["str", "int", "User"] {
        let filtered = find_kleisli_with_type(&index.entries, type_arg);
        let kleisli_funcs = find_kleisli(&index.entries);
        
        for any_entry in &any_entries {
            // Check if this Any-typed entry is in Kleisli functions
            if kleisli_funcs.iter().any(|k| k.name == any_entry.name) {
                assert!(
                    filtered.iter().any(|e| e.name == any_entry.name),
                    "Any-typed Kleisli {} should match {} filter",
                    any_entry.name,
                    type_arg
                );
            }
        }
    }
}

#[test]
fn test_do_decorator_categorization() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    for entry in &index.entries {
        if entry.categories.contains(&EntryCategory::DoFunction) {
            // Check first parameter to determine categorization
            if let Some(first_param) = entry.all_parameters.first() {
                if let Some(annotation) = &first_param.annotation {
                    if annotation.contains("Program") {
                        // @do with Program param -> Transform
                        assert!(
                            entry.categories.contains(&EntryCategory::ProgramTransformer),
                            "@do function {} with Program param should be Transform",
                            entry.name
                        );
                    } else if annotation.contains("Effect") {
                        // @do with Effect param -> Interceptor
                        assert!(
                            entry.categories.contains(&EntryCategory::Interceptor),
                            "@do function {} with Effect param should be Interceptor",
                            entry.name
                        );
                    } else {
                        // @do with other param -> Kleisli
                        assert!(
                            entry.categories.contains(&EntryCategory::KleisliProgram),
                            "@do function {} with {} param should be Kleisli",
                            entry.name,
                            annotation
                        );
                    }
                }
            }
        }
    }
}

#[test]
fn test_interceptor_detection() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Find interceptors (marker-only)
    let interceptors = find_interceptors(&index.entries);
    
    // Should only find functions with "interceptor" marker
    for entry in &interceptors {
        assert!(
            entry.markers.iter().any(|m| m.eq_ignore_ascii_case("interceptor")),
            "Found interceptor without marker: {}",
            entry.name
        );
    }

    // Verify categorization
    for entry in &index.entries {
        if entry.categories.contains(&EntryCategory::Interceptor) {
            // Should have Effect as first parameter
            if let Some(first_param) = entry.all_parameters.first() {
                if let Some(annotation) = &first_param.annotation {
                    assert!(
                        annotation.contains("Effect"),
                        "Interceptor {} first param {} is not Effect",
                        entry.name,
                        annotation
                    );
                }
            }
        }
    }
}

#[test]
fn test_marker_only_detection() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Verify that find-* commands ONLY return marked functions
    let interpreters = find_interpreters(&index.entries);
    let transforms = find_transforms(&index.entries);
    let interceptors = find_interceptors(&index.entries);

    // Every interpreter must have marker
    for entry in interpreters {
        assert!(
            !entry.markers.is_empty(),
            "Interpreter {} found without any markers",
            entry.name
        );
    }

    // Every transform must have marker  
    for entry in transforms {
        assert!(
            !entry.markers.is_empty(),
            "Transform {} found without any markers",
            entry.name
        );
    }

    // Every interceptor must have marker
    for entry in interceptors {
        assert!(
            !entry.markers.is_empty(),
            "Interceptor {} found without any markers",
            entry.name
        );
    }
}