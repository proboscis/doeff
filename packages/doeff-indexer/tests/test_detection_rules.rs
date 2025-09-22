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
            entry
                .markers
                .iter()
                .any(|m| m.eq_ignore_ascii_case("interpreter")),
            "Found interpreter without marker: {}",
            entry.name
        );
    }

    // Verify categorization: interpreters should NOT return Program
    for entry in &index.entries {
        if entry
            .categories
            .contains(&EntryCategory::ProgramInterpreter)
        {
            // If categorized as interpreter, should not also be transformer
            // (unless it has wrong marker)
            if !entry
                .categories
                .contains(&EntryCategory::ProgramTransformer)
            {
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
            entry
                .markers
                .iter()
                .any(|m| m.eq_ignore_ascii_case("transform")),
            "Found transform without marker: {}",
            entry.name
        );
    }

    // Verify categorization: transforms should return Program
    for entry in &index.entries {
        if entry
            .categories
            .contains(&EntryCategory::ProgramTransformer)
        {
            // Should accept Program parameter
            assert!(
                entry
                    .categories
                    .contains(&EntryCategory::AcceptsProgramParam),
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

    // Should find functions with "kleisli" marker only
    for entry in &kleisli {
        assert!(
            entry
                .markers
                .iter()
                .any(|m| m.eq_ignore_ascii_case("kleisli")),
            "Found Kleisli without marker: {}",
            entry.name
        );
    }
}

#[test]
fn test_kleisli_type_filtering() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    // Test filtering by Program[str] type argument
    let str_kleisli = find_kleisli_with_type(&index.entries, "Program[str]");
    let str_names: Vec<&str> = str_kleisli.iter().map(|e| e.name.as_str()).collect();

    // Only @do functions with a single required parameter should be returned
    for entry in &str_kleisli {
        assert!(
            entry.categories.contains(&EntryCategory::DoFunction),
            "find-kleisli Program[str] returned non-@do function: {}",
            entry.name
        );

        let required_params: Vec<_> = entry
            .all_parameters
            .iter()
            .filter(|p| p.is_required)
            .collect();
        assert_eq!(
            required_params.len(),
            1,
            "{} should have exactly one required parameter",
            entry.name
        );

        let annotation = required_params[0].annotation.as_deref().unwrap_or_default();
        assert!(
            annotation.contains("str") || annotation == "Any" || annotation.contains("Any"),
            "Kleisli {} first param {} doesn't match Program[str] filter",
            entry.name,
            annotation
        );
    }

    assert!(str_names.contains(&"fetch_by_id"));
    assert!(str_names.contains(&"kleisli_str"));
    assert!(str_names.contains(&"kleisli_optional"));
    assert!(str_names.contains(&"kleisli_with_default"));
    assert!(str_names.contains(&"process_any"));
    assert!(!str_names.contains(&"manual_kleisli"));
    assert!(!str_names.contains(&"unmarked_kleisli"));
    assert!(!str_names.contains(&"kleisli_int"));
    assert!(!str_names.contains(&"kleisli_multi_required"));
    assert!(!str_names.contains(&"fetch_data"));
    assert!(!str_names.contains(&"hybrid_function"));

    // Test that Any-typed @do functions match all Program filters
    for type_arg in &["Program[str]", "Program[int]", "Program[User]"] {
        let filtered = find_kleisli_with_type(&index.entries, type_arg);
        assert!(
            filtered.iter().any(|entry| entry.name == "process_any"),
            "process_any should match filter {}",
            type_arg
        );
    }

    // Program[int] should include int-typed kleisli but still enforce single required arg rule
    let int_kleisli = find_kleisli_with_type(&index.entries, "Program[int]");
    let int_names: Vec<&str> = int_kleisli.iter().map(|e| e.name.as_str()).collect();

    assert!(int_names.contains(&"kleisli_int"));
    assert!(int_names.contains(&"process_any"));
    assert!(!int_names.contains(&"kleisli_str"));
    assert!(!int_names.contains(&"manual_kleisli"));

    // Multi-argument Kleisli should be excluded regardless of filter type
    let img_kleisli = find_kleisli_with_type(&index.entries, "Img");
    assert!(img_kleisli
        .iter()
        .all(|entry| entry.name != "kp_aggregate_segmentations"));
    let program_img_kleisli = find_kleisli_with_type(&index.entries, "Program[Img]");
    assert!(program_img_kleisli
        .iter()
        .all(|entry| entry.name != "kp_aggregate_segmentations"));
}

#[test]
fn test_cli_marker_requirements() {
    let test_dir = Path::new("tests/fixtures");
    let index = build_index(test_dir).expect("Failed to build index");

    let interpreter_names: Vec<_> = find_interpreters(&index.entries)
        .into_iter()
        .map(|entry| entry.name.as_str())
        .collect();
    assert!(interpreter_names.contains(&"exec_int"));
    assert!(interpreter_names.contains(&"exec_any"));
    assert!(!interpreter_names.contains(&"exec_unmarked"));

    let transform_names: Vec<_> = find_transforms(&index.entries)
        .into_iter()
        .map(|entry| entry.name.as_str())
        .collect();
    assert!(transform_names.contains(&"map_transform"));
    assert!(transform_names.contains(&"do_transform"));
    assert!(!transform_names.contains(&"unmarked_transform"));

    let kleisli_names: Vec<_> = find_kleisli(&index.entries)
        .into_iter()
        .map(|entry| entry.name.as_str())
        .collect();
    assert!(kleisli_names.contains(&"fetch_by_id"));
    assert!(kleisli_names.contains(&"kleisli_str"));
    assert!(kleisli_names.contains(&"hybrid_function"));
    assert!(!kleisli_names.contains(&"unmarked_kleisli"));
    assert!(!kleisli_names.contains(&"not_kleisli"));

    let interceptor_names: Vec<_> = find_interceptors(&index.entries)
        .into_iter()
        .map(|entry| entry.name.as_str())
        .collect();
    assert!(interceptor_names.contains(&"log_effect_interceptor"));
    assert!(interceptor_names.contains(&"do_effect_interceptor"));
    assert!(!interceptor_names.contains(&"unmarked_interceptor"));
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
                            entry
                                .categories
                                .contains(&EntryCategory::ProgramTransformer),
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
            entry
                .markers
                .iter()
                .any(|m| m.eq_ignore_ascii_case("interceptor")),
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
