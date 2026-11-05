//! Python branch of DOEFF106: finds environment-variable reads (`os.environ`, `os.getenv`) in `.py` files under the layer roots
//! (child (b) of #1898). Raw side effects in Hy files are collected by the Hy definition index (raw_catalog of hy_index), but Python files lie outside that index,
//! so nothing was flagged when a business-layer `.py` read an environment variable.
//!
//! Read positions are found with Python tokens (the rustpython lexer) — strings and comments are separate tokens and are not counted. Forms matched:
//!   * `os.environ`, `os.getenv` (also the alias `o.environ` from `import os as o`)
//!   * the bare names `environ` / `getenv` after `from os import environ` / `getenv` (also `as` aliases)
//!
//! Which names refer to os, os.environ, os.getenv is resolved with the same table as DOEFF004 (OsEnvNames in utils — built from the import statements of the syntax tree).
//! Previously aliases were collected from the token sequence, which missed `o` in `import json, os as o` (os in second or later position) and
//! treated `o` in `from pkg import os as o` as os. Tokens inside import statements are not reads, so they are not counted.
//!
//! Other raw side effects (files, clock, network) are not matched here — the scope is limited to environment-variable reads (decision of #1907 — widening would
//! increase hits, so add it in a separate change when needed). The name of the function enclosing a hit is taken from the function ranges of the syntax tree (module top level if none).

use rustpython_ast::{Mod, Ranged, Stmt};
use rustpython_parser::lexer::lex;
use rustpython_parser::{parse, Mode, Tok};

use crate::utils::{each_statement, OsEnvNames};

/// Names of `os` attributes that read environment variables.
const ENV_NAMES: &[&str] = &["environ", "getenv"];

/// One environment-variable read found.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EnvRead {
    /// Byte position in the source (start, end).
    pub start: usize,
    pub end: usize,
    /// Spelling of the read (`os.environ`, `os.getenv`).
    pub name: String,
    /// Name of the enclosing function (`outer.inner` — `<module>` if none).
    pub definition: String,
}

/// To collect function ranges and names (nested ones as `outer.inner`).
fn functions(body: &[Stmt], prefix: &str, out: &mut Vec<(usize, usize, String)>) {
    for statement in body {
        let (name, inner) = match statement {
            Stmt::FunctionDef(node) => (node.name.to_string(), &node.body),
            Stmt::AsyncFunctionDef(node) => (node.name.to_string(), &node.body),
            Stmt::ClassDef(node) => (node.name.to_string(), &node.body),
            _ => continue,
        };
        let full = if prefix.is_empty() { name } else { format!("{}.{}", prefix, name) };
        let range = statement.range();
        out.push((range.start().to_usize(), range.end().to_usize(), full.clone()));
        functions(inner, &full, out);
    }
}

/// To return the name of the innermost function enclosing a position.
fn enclosing(spans: &[(usize, usize, String)], at: usize) -> String {
    spans
        .iter()
        .filter(|(start, end, _)| *start <= at && at < *end)
        .max_by_key(|(start, _, _)| *start)
        .map(|(_, _, name)| name.clone())
        .unwrap_or_else(|| "<module>".to_string())
}

/// The os read name a bare name refers to (`environ`, `getenv`) — None for names not in the table.
fn bare_original(names: &OsEnvNames, name: &str) -> Option<&'static str> {
    if names.environ.contains(name) {
        Some("environ")
    } else if names.getenv.contains(name) {
        Some("getenv")
    } else {
        None
    }
}

/// To find environment-variable reads in `.py` source (a file that cannot be tokenized yields empty — syntax errors are reported by another rule).
/// A file that cannot be parsed into a syntax tree cannot give a name table, so only the plain `os.environ` and `os.getenv` are matched.
pub fn env_reads(source: &str) -> Vec<EnvRead> {
    let Ok(tokens) = lex(source, Mode::Module).collect::<Result<Vec<_>, _>>() else {
        return Vec::new();
    };
    let mut spans = Vec::new();
    // Ranges of import statements (tokens inside them — e.g. `environ` in `from os import environ` — are not reads).
    let mut imports: Vec<(usize, usize)> = Vec::new();
    let mut names = OsEnvNames::plain();
    if let Ok(ast) = parse(source, Mode::Module, "<module>") {
        if let Mod::Module(module) = &ast {
            functions(&module.body, "", &mut spans);
            each_statement(&module.body, &mut |stmt| {
                if matches!(stmt, Stmt::Import(_) | Stmt::ImportFrom(_)) {
                    imports.push((stmt.range().start().to_usize(), stmt.range().end().to_usize()));
                }
            });
        }
        names = OsEnvNames::of_module(&ast);
    }
    let name_of = |i: usize| match tokens.get(i) {
        Some((Tok::Name { name }, _)) => Some(name.as_str()),
        _ => None,
    };
    let mut out = Vec::new();
    for i in 0..tokens.len() {
        let at = tokens[i].1.start().to_usize();
        if imports.iter().any(|(start, end)| *start <= at && at < *end) {
            continue;
        }
        let Some(name) = name_of(i) else { continue };
        let after_dot = i > 0 && matches!(tokens[i - 1].0, Tok::Dot);
        let range = tokens[i].1;
        if names.os.contains(name) && !after_dot && matches!(tokens.get(i + 1), Some((Tok::Dot, _))) {
            if let Some(attribute) = name_of(i + 2).filter(|a| ENV_NAMES.contains(a)) {
                let end = tokens[i + 2].1.end().to_usize();
                out.push(EnvRead {
                    start: range.start().to_usize(),
                    end,
                    name: format!("os.{}", attribute),
                    definition: enclosing(&spans, range.start().to_usize()),
                });
            }
        } else if let Some(original) = bare_original(&names, name).filter(|_| !after_dot) {
            out.push(EnvRead {
                start: range.start().to_usize(),
                end: range.end().to_usize(),
                name: format!("os.{}", original),
                definition: enclosing(&spans, range.start().to_usize()),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(source: &str) -> Vec<(String, String)> {
        env_reads(source).into_iter().map(|r| (r.name, r.definition)).collect()
    }

    #[test]
    fn os_environ_and_getenv_are_found_with_their_function() {
        let source = "import os\n\ndef mode():\n    return os.environ[\"MODE\"]\n\nclass C:\n    def f(self):\n        return os.getenv(\"X\")\n";
        assert_eq!(names(source), vec![("os.environ".into(), "mode".into()), ("os.getenv".into(), "C.f".into())]);
    }

    #[test]
    fn aliases_and_bare_imports_are_found() {
        let source = "import os as o\nfrom os import environ, getenv as ge\nA = o.environ.get(\"A\")\nB = environ[\"B\"]\nC = ge(\"C\")\n";
        assert_eq!(
            names(source),
            vec![("os.environ".into(), "<module>".into()), ("os.environ".into(), "<module>".into()), ("os.getenv".into(), "<module>".into())]
        );
    }

    /// Counterexample: `o` in `import json, os as o` refers to os — before the fix, an alias was
    /// remembered only when the name right after import was os, giving 0 hits. The same holds for aliases from imports inside functions.
    #[test]
    fn an_os_alias_after_another_module_in_one_import_is_found() {
        let source = "import json, os as o\nA = o.environ[\"A\"]\n\ndef f():\n    import sys, os as p\n    return p.getenv(\"B\")\n";
        assert_eq!(names(source), vec![("os.environ".into(), "<module>".into()), ("os.getenv".into(), "f".into())]);
    }

    /// Counterexample: `o` in `from pkg import os as o` is the os inside pkg, not the os module — before the fix, the token
    /// sequence `import os as o` matched and it was treated as an alias of os.
    #[test]
    fn os_imported_from_another_package_is_not_an_os_alias() {
        let source = "from pkg import os as o\nA = o.environ[\"A\"]\n";
        assert!(names(source).is_empty(), "{:?}", names(source));
    }

    #[test]
    fn strings_comments_and_other_attributes_are_not_found() {
        // Counterexample: spellings inside strings and comments, other attributes of os, and the environ attribute of another object are not environment-variable reads.
        let source = "import os\n# do not read os.environ\nS = \"os.getenv\"\nP = os.path.join(\"a\", \"b\")\nQ = request.environ\n";
        assert!(names(source).is_empty(), "{:?}", names(source));
    }
}
