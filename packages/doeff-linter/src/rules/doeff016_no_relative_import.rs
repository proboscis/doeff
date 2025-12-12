//! DOEFF016: No Relative Imports
//!
//! Forbid relative imports in favor of absolute imports.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use rustpython_ast::Stmt;

pub struct NoRelativeImportRule;

impl NoRelativeImportRule {
    pub fn new() -> Self {
        Self
    }
}

impl LintRule for NoRelativeImportRule {
    fn rule_id(&self) -> &str {
        "DOEFF016"
    }

    fn description(&self) -> &str {
        "Forbid relative imports in favor of absolute imports"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Stmt::ImportFrom(import) = context.stmt {
            // level > 0 indicates a relative import
            // level = 1 means "from . import ..." or "from .module import ..."
            // level = 2 means "from .. import ..." or "from ..module import ..."
            // etc.
            // Note: level is Option<Int>, where Int has to_u32() method
            let level: u32 = import
                .level
                .as_ref()
                .map(|l| l.to_u32())
                .unwrap_or(0);

            if level > 0 {
                let module_name = import
                    .module
                    .as_ref()
                    .map(|m| m.as_str())
                    .unwrap_or("");
                
                let dots = ".".repeat(level as usize);
                let import_repr = if module_name.is_empty() {
                    format!("from {} import ...", dots)
                } else {
                    format!("from {}{} import ...", dots, module_name)
                };

                let message = format!(
                    "Relative import detected: '{}'\n\n\
                     Problem: Relative imports make code harder to move and refactor.\n\n\
                     Fix: Use absolute import instead:\n  \
                     from <package>.<module> import ...",
                    import_repr
                );

                violations.push(Violation::new(
                    "DOEFF016".to_string(),
                    message,
                    import.range.start().to_usize(),
                    context.file_path.to_string(),
                    Severity::Error,
                ));
            }
        }

        violations
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_ast::Mod;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoRelativeImportRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            for stmt in &module.body {
                let context = RuleContext {
                    stmt,
                    file_path: "test.py",
                    source: code,
                    ast: &ast,
                };
                violations.extend(rule.check(&context));
            }
        }

        violations
    }

    #[test]
    fn test_single_dot_import_with_module() {
        // from .module import something
        let code = "from .module import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("from .module import"));
        assert!(violations[0].message.contains("Relative import detected"));
    }

    #[test]
    fn test_single_dot_import_without_module() {
        // from . import something
        let code = "from . import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("from . import"));
    }

    #[test]
    fn test_double_dot_import() {
        // from ..parent import something
        let code = "from ..parent import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("from ..parent import"));
    }

    #[test]
    fn test_triple_dot_import() {
        // from ...grandparent.module import something
        let code = "from ...grandparent.module import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("from ...grandparent.module import"));
    }

    #[test]
    fn test_absolute_import_allowed() {
        // from package.module import something
        let code = "from package.module import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_absolute_import_top_level_allowed() {
        // from os import path
        let code = "from os import path";
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_regular_import_allowed() {
        // import os
        let code = "import os";
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_multiple_relative_imports() {
        let code = r#"
from .module1 import a
from ..module2 import b
from package.module3 import c
"#;
        let violations = check_code(code);
        // Should detect 2 relative imports (module1 and module2), but not module3
        assert_eq!(violations.len(), 2);
    }

    #[test]
    fn test_relative_import_multiple_names() {
        // from .module import a, b, c
        let code = "from .module import a, b, c";
        let violations = check_code(code);
        // Only one violation for the whole import statement
        assert_eq!(violations.len(), 1);
    }

    #[test]
    fn test_error_message_contains_fix_suggestion() {
        let code = "from .module import something";
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("Use absolute import instead"));
        assert!(violations[0].message.contains("<package>.<module>"));
    }
}

