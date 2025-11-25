//! DOEFF008: No Dataclass Attribute Mutation
//!
//! Dataclass instances should be immutable. Use dataclasses.replace() instead.

use crate::models::{RuleContext, Severity, Violation};
use crate::rules::base::LintRule;
use crate::utils::{has_dataclass_decorator, looks_like_dataclass_name};
use rustpython_ast::{Expr, Mod, Stmt, StmtClassDef};
use std::collections::{HashMap, HashSet};

pub struct NoDataclassAttributeMutationRule;

impl NoDataclassAttributeMutationRule {
    pub fn new() -> Self {
        Self
    }

    fn collect_dataclass_names(stmts: &[Stmt]) -> HashSet<String> {
        let mut names = HashSet::new();
        for stmt in stmts {
            if let Stmt::ClassDef(class_def) = stmt {
                if has_dataclass_decorator(class_def) {
                    names.insert(class_def.name.to_string());
                }
            }
        }
        names
    }

    fn track_dataclass_instances(
        stmts: &[Stmt],
        dataclass_names: &HashSet<String>,
    ) -> HashMap<String, String> {
        let mut instances = HashMap::new();

        for stmt in stmts {
            match stmt {
                Stmt::Assign(assign) => {
                    if let Some(target) = assign.targets.first() {
                        if let Expr::Name(target_name) = target {
                            if let Expr::Call(call) = &*assign.value {
                                if let Expr::Name(class_name) = &*call.func {
                                    let name = class_name.id.as_str();
                                    if dataclass_names.contains(name)
                                        || looks_like_dataclass_name(name)
                                    {
                                        instances.insert(
                                            target_name.id.to_string(),
                                            class_name.id.to_string(),
                                        );
                                    }
                                }
                            }
                        }
                    }
                }
                Stmt::AnnAssign(ann_assign) => {
                    if let Expr::Name(target_name) = &*ann_assign.target {
                        if let Some(value) = &ann_assign.value {
                            if let Expr::Call(call) = &**value {
                                if let Expr::Name(class_name) = &*call.func {
                                    let name = class_name.id.as_str();
                                    if dataclass_names.contains(name)
                                        || looks_like_dataclass_name(name)
                                    {
                                        instances.insert(
                                            target_name.id.to_string(),
                                            class_name.id.to_string(),
                                        );
                                    }
                                }
                            }
                        }
                        if let Expr::Name(ann_name) = &*ann_assign.annotation {
                            let name = ann_name.id.as_str();
                            if dataclass_names.contains(name) || looks_like_dataclass_name(name) {
                                instances
                                    .insert(target_name.id.to_string(), ann_name.id.to_string());
                            }
                        }
                    }
                }
                Stmt::FunctionDef(func) => {
                    instances.extend(Self::track_dataclass_instances(&func.body, dataclass_names));
                }
                Stmt::AsyncFunctionDef(func) => {
                    instances.extend(Self::track_dataclass_instances(&func.body, dataclass_names));
                }
                Stmt::ClassDef(class_def) => {
                    instances
                        .extend(Self::track_dataclass_instances(&class_def.body, dataclass_names));
                }
                Stmt::If(if_stmt) => {
                    instances.extend(Self::track_dataclass_instances(&if_stmt.body, dataclass_names));
                    instances
                        .extend(Self::track_dataclass_instances(&if_stmt.orelse, dataclass_names));
                }
                Stmt::For(for_stmt) => {
                    instances
                        .extend(Self::track_dataclass_instances(&for_stmt.body, dataclass_names));
                }
                Stmt::While(while_stmt) => {
                    instances
                        .extend(Self::track_dataclass_instances(&while_stmt.body, dataclass_names));
                }
                Stmt::With(with_stmt) => {
                    instances
                        .extend(Self::track_dataclass_instances(&with_stmt.body, dataclass_names));
                }
                _ => {}
            }
        }

        instances
    }

    fn check_for_mutations(
        stmts: &[Stmt],
        instances: &HashMap<String, String>,
        file_path: &str,
    ) -> Vec<Violation> {
        let mut violations = Vec::new();

        for stmt in stmts {
            match stmt {
                Stmt::Assign(assign) => {
                    for target in &assign.targets {
                        if let Expr::Attribute(attr) = target {
                            if let Expr::Name(name) = &*attr.value {
                                if let Some(class_name) = instances.get(name.id.as_str()) {
                                    violations.push(Violation::new(
                                        "DOEFF008".to_string(),
                                        format!(
                                            "Mutating attribute '{}' of dataclass '{}' (instance: '{}'). \
                                             Use dataclasses.replace({}, {}=new_value) instead.",
                                            attr.attr, class_name, name.id, name.id, attr.attr
                                        ),
                                        assign.range.start().to_usize(),
                                        file_path.to_string(),
                                        Severity::Error,
                                    ));
                                }
                            }
                        }
                    }
                }
                Stmt::AugAssign(aug_assign) => {
                    if let Expr::Attribute(attr) = &*aug_assign.target {
                        if let Expr::Name(name) = &*attr.value {
                            if let Some(class_name) = instances.get(name.id.as_str()) {
                                violations.push(Violation::new(
                                    "DOEFF008".to_string(),
                                    format!(
                                        "Mutating attribute '{}' of dataclass '{}' via augmented assignment. \
                                         Use dataclasses.replace() instead.",
                                        attr.attr, class_name
                                    ),
                                    aug_assign.range.start().to_usize(),
                                    file_path.to_string(),
                                    Severity::Error,
                                ));
                            }
                        }
                    }
                }
                Stmt::Delete(delete) => {
                    for target in &delete.targets {
                        if let Expr::Attribute(attr) = target {
                            if let Expr::Name(name) = &*attr.value {
                                if let Some(class_name) = instances.get(name.id.as_str()) {
                                    violations.push(Violation::new(
                                        "DOEFF008".to_string(),
                                        format!(
                                            "Deleting attribute '{}' of dataclass '{}'. \
                                             Dataclasses should be immutable.",
                                            attr.attr, class_name
                                        ),
                                        delete.range.start().to_usize(),
                                        file_path.to_string(),
                                        Severity::Error,
                                    ));
                                }
                            }
                        }
                    }
                }
                Stmt::If(if_stmt) => {
                    violations.extend(Self::check_for_mutations(&if_stmt.body, instances, file_path));
                    violations.extend(Self::check_for_mutations(&if_stmt.orelse, instances, file_path));
                }
                Stmt::For(for_stmt) => {
                    violations.extend(Self::check_for_mutations(&for_stmt.body, instances, file_path));
                }
                Stmt::While(while_stmt) => {
                    violations.extend(Self::check_for_mutations(&while_stmt.body, instances, file_path));
                }
                Stmt::With(with_stmt) => {
                    violations.extend(Self::check_for_mutations(&with_stmt.body, instances, file_path));
                }
                Stmt::FunctionDef(func) => {
                    violations.extend(Self::check_for_mutations(&func.body, instances, file_path));
                }
                Stmt::AsyncFunctionDef(func) => {
                    violations.extend(Self::check_for_mutations(&func.body, instances, file_path));
                }
                Stmt::Try(try_stmt) => {
                    violations.extend(Self::check_for_mutations(&try_stmt.body, instances, file_path));
                    for handler in &try_stmt.handlers {
                        if let rustpython_ast::ExceptHandler::ExceptHandler(h) = handler {
                            violations.extend(Self::check_for_mutations(&h.body, instances, file_path));
                        }
                    }
                }
                _ => {}
            }
        }

        violations
    }
}

impl LintRule for NoDataclassAttributeMutationRule {
    fn rule_id(&self) -> &str {
        "DOEFF008"
    }

    fn description(&self) -> &str {
        "Dataclass instances should be immutable. Use dataclasses.replace() instead."
    }

    fn check(&self, context: &RuleContext) -> Vec<Violation> {
        if let Mod::Module(module) = context.ast {
            let dataclass_names = Self::collect_dataclass_names(&module.body);
            let instances = Self::track_dataclass_instances(&module.body, &dataclass_names);
            Self::check_for_mutations(&module.body, &instances, context.file_path)
        } else {
            vec![]
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rustpython_parser::{parse, Mode};

    fn check_code(code: &str) -> Vec<Violation> {
        let ast = parse(code, Mode::Module, "test.py").unwrap();
        let rule = NoDataclassAttributeMutationRule::new();
        let mut violations = Vec::new();

        if let Mod::Module(module) = &ast {
            if let Some(stmt) = module.body.first() {
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
    fn test_dataclass_mutation() {
        let code = r#"
from dataclasses import dataclass

@dataclass
class Person:
    name: str
    age: int

person = Person("Alice", 30)
person.name = "Bob"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
        assert!(violations[0].message.contains("name"));
    }

    #[test]
    fn test_dataclass_replace_allowed() {
        let code = r#"
from dataclasses import dataclass, replace

@dataclass
class Person:
    name: str

person = Person("Alice")
new_person = replace(person, name="Bob")
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 0);
    }

    #[test]
    fn test_heuristic_detection() {
        let code = r#"
from external import UserData

user = UserData(name="Alice")
user.name = "Bob"
"#;
        let violations = check_code(code);
        assert_eq!(violations.len(), 1);
    }
}



