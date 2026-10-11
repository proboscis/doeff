//! DOEFF002: Mutable Attribute Naming
//!
//! Class attributes that are assigned outside of __init__, __post_init__ or __set_name__
//! must be prefixed with mut_ (public) or _mut (private), whatever the type of the value.
//! __set_name__ is a descriptor's initialization: Python calls it once, when the owner class
//! is created (PEP 487). Every method counts (`def` and `async def`), with every body nested in
//! it (`utils::nested_bodies`) and tuple / list / starred targets.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use crate::utils::{has_dataclass_decorator, nested_bodies};
use rustpython_ast::{Expr, ExprAttribute, ExprName, Ranged, Stmt, StmtClassDef};
use std::collections::HashSet;

pub struct MutableAttributeNamingRule;

impl MutableAttributeNamingRule {
    pub fn new() -> Self {
        Self
    }

    fn is_init_method(func_name: &str) -> bool {
        func_name == "__init__" || func_name == "__post_init__" || func_name == "__set_name__"
    }

    fn extract_self_attribute(expr: &Expr) -> Option<&str> {
        if let Expr::Attribute(ExprAttribute { value, attr, .. }) = expr {
            if let Expr::Name(ExprName { id, .. }) = &**value {
                if id.as_str() == "self" {
                    return Some(attr.as_str());
                }
            }
        }
        None
    }

    fn check_mutable_naming(attr_name: &str) -> bool {
        if attr_name.starts_with("__") {
            true // Dunder attributes are special
        } else if attr_name.starts_with('_') {
            attr_name.starts_with("_mut")
        } else {
            attr_name.starts_with("mut_")
        }
    }

    fn collect_class_attributes(class_def: &StmtClassDef) -> Vec<(String, usize, bool)> {
        let mut attributes = Vec::new();
        let is_dataclass = has_dataclass_decorator(class_def);

        let mut dataclass_fields_with_defaults = Vec::new();
        if is_dataclass {
            for stmt in &class_def.body {
                if let Stmt::AnnAssign(ann_assign) = stmt {
                    if ann_assign.value.is_some() {
                        if let Expr::Name(name) = &*ann_assign.target {
                            dataclass_fields_with_defaults.push(name.id.as_str().to_string());
                        }
                    }
                }
            }
        }

        for stmt in &class_def.body {
            if let Some((name, body)) = Self::method_of(stmt) {
                Self::collect_body_attributes(body, Self::is_init_method(name), &mut attributes);
            }
        }

        if is_dataclass {
            let mut assigned_fields = HashSet::new();
            for (attr_name, _, is_init) in &attributes {
                if !is_init && dataclass_fields_with_defaults.contains(attr_name) {
                    assigned_fields.insert(attr_name.clone());
                }
            }

            for field_name in assigned_fields {
                for stmt in &class_def.body {
                    if let Stmt::AnnAssign(ann_assign) = stmt {
                        if let Expr::Name(name) = &*ann_assign.target {
                            if name.id.as_str() == field_name {
                                attributes.push((
                                    field_name.clone(),
                                    ann_assign.range.start().to_usize(),
                                    false,
                                ));
                                break;
                            }
                        }
                    }
                }
            }
        }

        attributes
    }

    /// A method of the class body (`def` or `async def`): its name and body.
    fn method_of(stmt: &Stmt) -> Option<(&str, &[Stmt])> {
        match stmt {
            Stmt::FunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
            Stmt::AsyncFunctionDef(func) => Some((func.name.as_str(), func.body.as_slice())),
            _ => None,
        }
    }

    /// The `self` attributes assigned in a method body and every body nested in it (the bodies are
    /// the one list in `utils::nested_bodies`). A class nested in the method is skipped: the `self`
    /// of its methods is its own instance.
    fn collect_body_attributes(body: &[Stmt], is_init: bool, attributes: &mut Vec<(String, usize, bool)>) {
        for stmt in body {
            Self::collect_stmt_attributes(stmt, is_init, attributes);
            if matches!(stmt, Stmt::ClassDef(_)) {
                continue;
            }
            for nested in nested_bodies(stmt) {
                Self::collect_body_attributes(nested, is_init, attributes);
            }
        }
    }

    /// The `self` attributes one statement assigns itself (not those of its nested bodies).
    fn collect_stmt_attributes(stmt: &Stmt, is_init: bool, attributes: &mut Vec<(String, usize, bool)>) {
        let offset = stmt.range().start().to_usize();
        match stmt {
            Stmt::Assign(assign) => {
                for target in &assign.targets {
                    Self::collect_target(target, offset, is_init, attributes);
                }
            }
            Stmt::AugAssign(aug_assign) => Self::collect_target(&aug_assign.target, offset, is_init, attributes),
            Stmt::AnnAssign(ann_assign) => Self::collect_target(&ann_assign.target, offset, is_init, attributes),
            _ => {}
        }
    }

    /// `self.X` in an assignment target, also inside a tuple / list target and a starred element.
    fn collect_target(target: &Expr, offset: usize, is_init: bool, attributes: &mut Vec<(String, usize, bool)>) {
        match target {
            Expr::Tuple(tuple) => {
                for element in &tuple.elts {
                    Self::collect_target(element, offset, is_init, attributes);
                }
            }
            Expr::List(list) => {
                for element in &list.elts {
                    Self::collect_target(element, offset, is_init, attributes);
                }
            }
            Expr::Starred(starred) => Self::collect_target(&starred.value, offset, is_init, attributes),
            _ => {
                if let Some(attr_name) = Self::extract_self_attribute(target) {
                    attributes.push((attr_name.to_string(), offset, is_init));
                }
            }
        }
    }
}

impl LintRule for MutableAttributeNamingRule {
    fn rule_id(&self) -> &str {
        "DOEFF002"
    }

    fn description(&self) -> &str {
        "Mutable attributes must be prefixed with mut_ (public) or _mut (private)"
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        let mut violations = Vec::new();

        if let Stmt::ClassDef(class_def) = context.stmt {
            let attributes = Self::collect_class_attributes(class_def);
            let mut seen = HashSet::new();

            for (attr_name, offset, is_init) in attributes {
                if !is_init && seen.insert(attr_name.clone()) && !Self::check_mutable_naming(&attr_name) {
                    let prefix = if attr_name.starts_with('_') && !attr_name.starts_with("__") {
                        "_mut"
                    } else {
                        "mut_"
                    };

                    violations.push(Violation::new(
                        self.rule_id().to_string(),
                        format!(
                            "Attribute '{}' in class '{}' is mutated outside __init__/__post_init__/__set_name__. \
                             Mutable attributes must be prefixed with '{}'.",
                            attr_name, class_def.name, prefix
                        ),
                        offset,
                        context.file_path.to_string(),
                        Severity::Error,
                    ));
                }
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
        let rule = MutableAttributeNamingRule::new();
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
    fn test_mutable_attribute_without_prefix() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.value = 0

    def update(self):
        self.value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("'value'"));
    }

    #[test]
    fn test_properly_named_mutable_attributes() {
        let code = r#"
class MyClass:
    def __init__(self):
        self.mut_counter = 0

    def update(self):
        self.mut_counter += 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_private_mutable_attribute() {
        let code = r#"
class MyClass:
    def __init__(self):
        self._value = 0

    def update(self):
        self._value = 1
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("_mut"));
    }

    #[test]
    fn test_descriptor_set_name_is_initialization() {
        // Python calls __set_name__ once, when the owner class is created (PEP 487): what it
        // assigns is the descriptor's initial state, as in __init__.
        let code = r#"
class Field:
    def __set_name__(self, owner, name):
        self._held = f"_{name}"

    def __get__(self, instance, owner=None):
        return instance.__dict__[self._held]
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_value_kept_on_first_use_is_mutated() {
        // Assigned again after construction whatever its type: str and bool count as list does.
        let code = r#"
class Reader:
    def __init__(self):
        self._absent = None
        self.missed = False

    def absent(self):
        if self._absent is None:
            self._absent = read()
        return self._absent

    def facts(self):
        self.missed = True
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 2);
        assert!(violations[0].message.contains("'_absent'"));
        assert!(violations[1].message.contains("'missed'"));
    }

    #[test]
    fn test_assignments_in_async_methods_and_every_nested_body_are_counted() {
        // 失敗ケース(設計検証 lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP の盲検 B): async def の method と、try の else・finally・match の
        // case・for の else・組の左辺・method の中の関数の代入を数えず、9 つのうち plain の 1 つだけを所見にしていた。
        let code = r#"
class Reader:
    def __init__(self):
        self._absent = None

    async def absent_receiver(self):
        if self._absent is None:
            self._absent = await read_absent()
        return self._absent

    def in_try_else(self):
        try:
            pass
        except ValueError:
            pass
        else:
            self._a = 1

    def in_finally(self):
        try:
            pass
        finally:
            self._b = 1

    def in_match(self, value):
        match value:
            case 1:
                self._c = 1

    def in_for_else(self):
        for _ in range(1):
            pass
        else:
            self._d = 1

    def in_tuple(self):
        self._e, [self._f, *self._g] = 1, [2, 3]

    def in_nested_function(self):
        def later():
            self._h = 1
        later()

    async def in_async_with(self):
        async with lock():
            self._i = 1

    def plain(self):
        self._j = 1
"#;
        let names: HashSet<String> = check_code(code)
            .iter()
            .filter_map(|violation| violation.message.split('\'').nth(1).map(str::to_string))
            .collect();
        let expected: HashSet<String> = ["_absent", "_a", "_b", "_c", "_d", "_e", "_f", "_g", "_h", "_i", "_j"]
            .iter()
            .map(|name| name.to_string())
            .collect();
        assert_eq!(names, expected);
    }

    #[test]
    fn test_a_nested_class_assigns_to_its_own_instance() {
        // method の中の class の method の self は、その入れ子の class の object — 外の class の属性に数えない。
        let code = r#"
class Outer:
    def make(self):
        class Inner:
            def set(self):
                self.value = 1
        return Inner
"#;
        assert_eq!(check_code(code).len(), 0);
    }

    #[test]
    fn test_assignments_in_init_bodies_stay_initialization() {
        // 初期化の method の入れ子の本体(try の finally・with)の代入は、今までどおり初期化。
        let code = r#"
class Pool:
    def __init__(self):
        try:
            self.handle = open_it()
        finally:
            self.opened = True
"#;
        assert_eq!(check_code(code).len(), 0);
    }
}



