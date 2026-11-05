//! Utility functions for AST analysis

use rustpython_ast::{ExceptHandler, Expr, Mod, Stmt, StmtClassDef};
use std::collections::BTreeSet;

/// Bodies nested in statements (the statement lists handed to each rule one by one). Covers every kind of statement (no `_ =>`) — if there is a body we do not descend into,
/// no rule applies to the code there (this used to drop for / while else, async for, async with, match case, and try*).
/// The one place read by the linter's own per-statement recursion and by rules that walk the whole module themselves.
pub fn nested_bodies(stmt: &Stmt) -> Vec<&[Stmt]> {
    match stmt {
        Stmt::FunctionDef(func) => vec![func.body.as_slice()],
        Stmt::AsyncFunctionDef(func) => vec![func.body.as_slice()],
        Stmt::ClassDef(class_def) => vec![class_def.body.as_slice()],
        Stmt::For(for_stmt) => vec![for_stmt.body.as_slice(), for_stmt.orelse.as_slice()],
        Stmt::AsyncFor(for_stmt) => vec![for_stmt.body.as_slice(), for_stmt.orelse.as_slice()],
        Stmt::While(while_stmt) => vec![while_stmt.body.as_slice(), while_stmt.orelse.as_slice()],
        Stmt::If(if_stmt) => vec![if_stmt.body.as_slice(), if_stmt.orelse.as_slice()],
        Stmt::With(with_stmt) => vec![with_stmt.body.as_slice()],
        Stmt::AsyncWith(with_stmt) => vec![with_stmt.body.as_slice()],
        Stmt::Match(match_stmt) => match_stmt.cases.iter().map(|case| case.body.as_slice()).collect(),
        Stmt::Try(try_stmt) => try_bodies(&try_stmt.body, &try_stmt.handlers, &try_stmt.orelse, &try_stmt.finalbody),
        Stmt::TryStar(try_stmt) => {
            try_bodies(&try_stmt.body, &try_stmt.handlers, &try_stmt.orelse, &try_stmt.finalbody)
        }
        Stmt::Return(_)
        | Stmt::Delete(_)
        | Stmt::Assign(_)
        | Stmt::TypeAlias(_)
        | Stmt::AugAssign(_)
        | Stmt::AnnAssign(_)
        | Stmt::Raise(_)
        | Stmt::Assert(_)
        | Stmt::Import(_)
        | Stmt::ImportFrom(_)
        | Stmt::Global(_)
        | Stmt::Nonlocal(_)
        | Stmt::Expr(_)
        | Stmt::Pass(_)
        | Stmt::Break(_)
        | Stmt::Continue(_) => Vec::new(),
    }
}

/// The bodies of try and try*, the body of each handler, else, and finally.
fn try_bodies<'a>(
    body: &'a [Stmt],
    handlers: &'a [ExceptHandler],
    orelse: &'a [Stmt],
    finalbody: &'a [Stmt],
) -> Vec<&'a [Stmt]> {
    let handler_bodies = handlers
        .iter()
        .map(|ExceptHandler::ExceptHandler(handler)| handler.body.as_slice());
    std::iter::once(body)
        .chain(handler_bodies)
        .chain([orelse, finalbody])
        .collect()
}

/// Passes every statement of a statement list and its nested bodies to `visit` exactly once, in written order (an outer statement, then the statements of its body).
pub fn each_statement<'a>(body: &'a [Stmt], visit: &mut impl FnMut(&'a Stmt)) {
    for stmt in body {
        visit(stmt);
        for nested in nested_bodies(stmt) {
            each_statement(nested, &mut *visit);
        }
    }
}

/// Table of names that point to environment-variable reads within a file (the one place read by the environment-variable kinds of DOEFF004 and DOEFF106).
/// Built once from the file's import statements (all of them: directly under the module, and nested inside functions, classes, if, and so on).
/// The binding target of a name is one for the whole file (Python name scopes are not considered — `x` of `import os as x` inside a function is also treated as pointing to os across the file).
/// `importlib.import_module("os")`, `__import__("os")`, and `from os import *` are not considered (out of scope).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OsEnvNames {
    /// Names pointing to the os module — always `os`, and `N` of `import os as N`.
    pub os: BTreeSet<String>,
    /// Bare names pointing to os.environ — `environ` of `from os import environ`, and `N` of `from os import environ as N`.
    pub environ: BTreeSet<String>,
    /// Bare names pointing to os.getenv — `getenv` of `from os import getenv`, and `N` of `from os import getenv as N`.
    pub getenv: BTreeSet<String>,
}

impl OsEnvNames {
    /// Table for when there is no syntax tree (only the bare `os`).
    pub fn plain() -> Self {
        Self {
            os: BTreeSet::from(["os".to_string()]),
            environ: BTreeSet::new(),
            getenv: BTreeSet::new(),
        }
    }

    /// Built from all import statements of the module.
    pub fn of_module(ast: &Mod) -> Self {
        let mut names = Self::plain();
        if let Mod::Module(module) = ast {
            each_statement(&module.body, &mut |stmt| names.learn(stmt));
        }
        names
    }

    /// Remembers the names bound by one import statement (a statement that is not an import binds no names).
    fn learn(&mut self, stmt: &Stmt) {
        if let Stmt::Import(import) = stmt {
            for alias in &import.names {
                if alias.name.as_str() == "os" {
                    if let Some(bound) = &alias.asname {
                        self.os.insert(bound.to_string());
                    }
                }
            }
        }
        if let Stmt::ImportFrom(from) = stmt {
            let absolute = from.level.as_ref().map_or(0, |level| level.to_u32()) == 0;
            if absolute && from.module.as_ref().map(|module| module.as_str()) == Some("os") {
                for alias in &from.names {
                    let bound = alias.asname.as_ref().unwrap_or(&alias.name).to_string();
                    if alias.name.as_str() == "environ" {
                        self.environ.insert(bound);
                    } else if alias.name.as_str() == "getenv" {
                        self.getenv.insert(bound);
                    }
                }
            }
        }
    }

    /// Whether the expression points to os.environ — `<os name>.environ`, or a bare name pointing to environ.
    pub fn is_environ(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Attribute(attr) => attr.attr.as_str() == "environ" && self.is_os(&attr.value),
            Expr::Name(name) => self.environ.contains(name.id.as_str()),
            _ => false,
        }
    }

    /// Whether the expression points to os.getenv — `<os name>.getenv`, or a bare name pointing to getenv.
    pub fn is_getenv(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Attribute(attr) => attr.attr.as_str() == "getenv" && self.is_os(&attr.value),
            Expr::Name(name) => self.getenv.contains(name.id.as_str()),
            _ => false,
        }
    }

    /// Whether the expression is a name pointing to the os module.
    fn is_os(&self, expr: &Expr) -> bool {
        matches!(expr, Expr::Name(name) if self.os.contains(name.id.as_str()))
    }
}

/// Check if a class has the @dataclass decorator
pub fn has_dataclass_decorator(class_def: &StmtClassDef) -> bool {
    for decorator in &class_def.decorator_list {
        match decorator {
            Expr::Name(name) if name.id.as_str() == "dataclass" => return true,
            Expr::Call(call) => {
                if let Expr::Name(name) = &*call.func {
                    if name.id.as_str() == "dataclass" {
                        return true;
                    }
                }
            }
            Expr::Attribute(attr) => {
                if attr.attr.as_str() == "dataclass" {
                    if let Expr::Name(name) = &*attr.value {
                        if name.id.as_str() == "dataclasses" {
                            return true;
                        }
                    }
                }
            }
            _ => {}
        }
    }
    false
}

/// Check if a class name looks like it could be a dataclass (heuristic)
pub fn looks_like_dataclass_name(name: &str) -> bool {
    name.ends_with("State")
        || name.ends_with("Data")
        || name.ends_with("Config")
        || name.ends_with("Model")
        || name.ends_with("Params")
        || name.ends_with("Settings")
        || name.ends_with("Info")
        || name.ends_with("Record")
        || name.ends_with("Entry")
        || name.ends_with("Item")
        || name.ends_with("Details")
        || name.ends_with("Metadata")
        || name.contains("Dataclass")
        || name.contains("DataClass")
}

/// Python built-in names that should not be shadowed
pub const PYTHON_BUILTINS: &[&str] = &[
    // Types
    "dict", "list", "set", "tuple", "str", "int", "float", "bool", "bytes", "bytearray",
    "object", "type", "super", "property", "classmethod", "staticmethod", "frozenset",
    "complex", "slice", "range", "memoryview",
    // Functions
    "len", "enumerate", "zip", "map", "filter", "sorted", "reversed",
    "sum", "min", "max", "abs", "round", "pow", "divmod",
    "all", "any", "next", "iter", "callable", "isinstance", "issubclass",
    "getattr", "setattr", "delattr", "hasattr",
    "repr", "ascii", "bin", "hex", "oct", "chr", "ord",
    "format", "hash", "id", "vars", "dir", "help", "locals", "globals",
    // I/O
    "open", "print", "input",
    // Execution
    "compile", "exec", "eval", "__import__",
    // Exceptions
    "Exception", "BaseException", "ValueError", "TypeError", "KeyError", "IndexError",
    "AttributeError", "RuntimeError", "StopIteration", "GeneratorExit",
    "FileNotFoundError", "PermissionError", "OSError", "IOError",
    // Constants
    "True", "False", "None", "Ellipsis", "NotImplemented",
    // Other
    "breakpoint", "copyright", "credits", "license", "quit", "exit",
];



