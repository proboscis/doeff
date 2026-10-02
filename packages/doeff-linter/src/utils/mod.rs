//! Utility functions for AST analysis

use rustpython_ast::{ExceptHandler, Expr, Mod, Stmt, StmtClassDef};
use std::collections::BTreeSet;

/// 文の入れ子の本体(規則に 1 つずつ渡す文の列)。文の種類を網羅する(`_ =>` を使わない)— 降りない本体が在ると、
/// そこの code にはどの規則も当たらない(for / while の else・async for・async with・match の case・try* を
/// 落としていた — agora-redesign #2834)。linter 本体の文ごとの再帰と、module 全体を自分で辿る規則が読む 1 か所。
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

/// try と try* の本体・各 handler の本体・else・finally。
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

/// 文の列とその入れ子の本体の文を全部、書いた順(外の文の後にその本体の文)に 1 度ずつ `visit` へ渡す。
pub fn each_statement<'a>(body: &'a [Stmt], visit: &mut impl FnMut(&'a Stmt)) {
    for stmt in body {
        visit(stmt);
        for nested in nested_bodies(stmt) {
            each_statement(nested, &mut *visit);
        }
    }
}

/// file の中で環境変数の読みを指す名の表(DOEFF004 と DOEFF106 の環境変数の種類が読む 1 か所 — agora-redesign #3012)。
/// file の import 文(module の直下と、関数・class・if ほかの入れ子の中の import 文の全部)から 1 度だけ作る。
/// 名の束ね先は file 全体で 1 つ(Python の名の有効範囲は見ない — 関数の中の `import os as x` の `x` も file の全体で os を指すと見る)。
/// `importlib.import_module("os")`・`__import__("os")`・`from os import *` は見ない(範囲の外)。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct OsEnvNames {
    /// os の module を指す名 — 常に `os`、と `import os as N` の `N`。
    pub os: BTreeSet<String>,
    /// os.environ を指す素の名 — `from os import environ` の `environ`、`from os import environ as N` の `N`。
    pub environ: BTreeSet<String>,
    /// os.getenv を指す素の名 — `from os import getenv` の `getenv`、`from os import getenv as N` の `N`。
    pub getenv: BTreeSet<String>,
}

impl OsEnvNames {
    /// 構文木の無い時の表(素の `os` だけ)。
    pub fn plain() -> Self {
        Self {
            os: BTreeSet::from(["os".to_string()]),
            environ: BTreeSet::new(),
            getenv: BTreeSet::new(),
        }
    }

    /// module の全部の import 文から作る。
    pub fn of_module(ast: &Mod) -> Self {
        let mut names = Self::plain();
        if let Mod::Module(module) = ast {
            each_statement(&module.body, &mut |stmt| names.learn(stmt));
        }
        names
    }

    /// import 文 1 つが束ねる名を覚える(import 文でない文は何も束ねない)。
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

    /// 式が os.environ を指すか — `<os の名>.environ` か、environ を指す素の名。
    pub fn is_environ(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Attribute(attr) => attr.attr.as_str() == "environ" && self.is_os(&attr.value),
            Expr::Name(name) => self.environ.contains(name.id.as_str()),
            _ => false,
        }
    }

    /// 式が os.getenv を指すか — `<os の名>.getenv` か、getenv を指す素の名。
    pub fn is_getenv(&self, expr: &Expr) -> bool {
        match expr {
            Expr::Attribute(attr) => attr.attr.as_str() == "getenv" && self.is_os(&attr.value),
            Expr::Name(name) => self.getenv.contains(name.id.as_str()),
            _ => false,
        }
    }

    /// 式が os の module を指す名か。
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



