#[cfg(test)]
mod tests {
    use crate::indexer::extract_markers_from_source;
    use rustpython_ast::text_size::TextRange;
    use rustpython_parser::ast;

    #[test]
    fn test_extract_marker_same_line() {
        let source = r#"
def interpreter(program: Program):  # doeff: interpreter
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_extract_marker_multiline_signature() {
        let source = r#"
def interpreter(  # doeff: interpreter
    program: Program,
    config: dict = None
):
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_extract_multiple_markers() {
        let source = r#"
@do
def hybrid(  # doeff: kleisli, transform
    program: Program
):
    pass
"#;
        let markers = extract_markers_from_source(source, 3, "hybrid", &default_args());
        assert!(markers.contains(&"kleisli".to_string()));
        assert!(markers.contains(&"transform".to_string()));
        assert_eq!(markers.len(), 2);
    }

    #[test]
    fn test_extract_marker_with_decorator() {
        let source = r#"
@do
def kleisli_func():  # doeff: kleisli
    yield Effect("test")
"#;
        let markers = extract_markers_from_source(source, 3, "kleisli_func", &default_args());
        assert_eq!(markers, vec!["kleisli".to_string()]);
    }

    #[test]
    fn test_extract_marker_inline_with_params() {
        let source = r#"
def interpreter(
    program: Program,  # doeff: interpreter
    verbose: bool = False
):
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_no_markers() {
        let source = r#"
def regular_function(x: int, y: int):
    # This is just a regular comment
    return x + y
"#;
        let markers = extract_markers_from_source(source, 2, "regular_function", &default_args());
        assert!(markers.is_empty());
    }

    #[test]
    fn test_marker_case_insensitive() {
        let source = r#"
def interpreter(program: Program):  # DOEFF: INTERPRETER
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["INTERPRETER".to_string()]);
    }

    #[test]
    fn test_marker_with_extra_spaces() {
        let source = r#"
def interpreter(program: Program):  # doeff:   interpreter  ,  transform  
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert!(markers.contains(&"interpreter".to_string()));
        assert!(markers.contains(&"transform".to_string()));
    }

    #[test]
    fn test_marker_with_space_after_hash() {
        // Handle "#   doeff:" with extra spaces between # and doeff
        let source = r#"
def interpreter(program: Program):  #   doeff:   interpreter  
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_marker_no_space_after_hash() {
        // Handle "#doeff:" with no space after #
        let source = r#"
def interpreter(program: Program):  #doeff: interpreter
    pass
"#;
        let markers = extract_markers_from_source(source, 2, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_async_function_marker() {
        let source = r#"
async def async_interpreter(  # doeff: interpreter
    program: Program
):
    return await program.async_run()
"#;
        let markers = extract_markers_from_source(source, 2, "async_interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_class_method_marker() {
        let source = r#"
class Executor:
    def execute(self, program: Program):  # doeff: interpreter
        return program.run()
"#;
        let markers = extract_markers_from_source(source, 3, "execute", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_property_marker() {
        let source = r#"
class Manager:
    @property
    def interpreter(self) -> Callable:  # doeff: interpreter
        return lambda p: p.run()
"#;
        let markers = extract_markers_from_source(source, 4, "interpreter", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_marker_on_comment_line_before_function() {
        // Markers on comment lines immediately before function should be detected
        let source = r#"
# doeff: interpreter
# This marker is on a comment line before the function
def regular_function(x: int):
    return x * 2
"#;
        let markers = extract_markers_from_source(source, 4, "regular_function", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    #[test]
    fn test_marker_on_decorator_line() {
        // Markers on decorator lines should be detected
        let source = r#"
@do  # doeff: kleisli
def kleisli_func(x: int):
    yield Effect("test")
"#;
        let markers = extract_markers_from_source(source, 3, "kleisli_func", &default_args());
        assert_eq!(markers, vec!["kleisli".to_string()]);
    }

    #[test]
    fn test_marker_in_function_body_comment() {
        // Markers in comment lines at the start of function body should be detected
        let source = r#"
def some_function(x: int):
    # doeff: kleisli
    return x * 2
"#;
        let markers = extract_markers_from_source(source, 2, "some_function", &default_args());
        assert_eq!(markers, vec!["kleisli".to_string()]);
    }

    #[test]
    fn test_marker_separated_by_code() {
        // Markers separated from function by actual code should NOT be detected
        let source = r#"
# doeff: interpreter
x = 42
def regular_function(x: int):
    return x * 2
"#;
        let markers = extract_markers_from_source(source, 4, "regular_function", &default_args());
        assert!(markers.is_empty());
    }

    #[test]
    fn test_marker_after_noqa_comment() {
        // Handle "# noqa: DOEFF017 # doeff: transform" pattern
        // The doeff marker should be detected even after noqa comment
        let source = r#"
def visualize_optimization_steps(  # noqa: DOEFF017 # doeff: transform
    program: Program
):
    pass
"#;
        let markers =
            extract_markers_from_source(source, 2, "visualize_optimization_steps", &default_args());
        assert_eq!(markers, vec!["transform".to_string()]);
    }

    #[test]
    fn test_marker_after_noqa_single_line() {
        // Handle noqa followed by doeff marker on def line
        let source = r#"
def process_data(x: int):  # noqa: E501 # doeff: kleisli
    return x * 2
"#;
        let markers = extract_markers_from_source(source, 2, "process_data", &default_args());
        assert_eq!(markers, vec!["kleisli".to_string()]);
    }

    #[test]
    fn test_marker_after_multiple_comments() {
        // Handle multiple comment sections before doeff marker
        let source = r#"
def complex_func(x: int):  # type: ignore # noqa # doeff: interpreter
    return x * 2
"#;
        let markers = extract_markers_from_source(source, 2, "complex_func", &default_args());
        assert_eq!(markers, vec!["interpreter".to_string()]);
    }

    fn default_args() -> ast::Arguments {
        ast::Arguments::empty(TextRange::default().into())
    }
}
