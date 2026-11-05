//! DOEFF145: a type declaration (.pyi) must not misrepresent the runtime shape of the same-named .hy in the same dir (the destination of the judgment moved from
//! scripts/check_record_stubs.hy in agora-controllers. The meaning of the judgment is the same as the original).
//!
//! Shape of the mismatch: a class in the .hy is a kw-only record (a top-level `(defrecord NAME …)`, or a top-level `(defclass [DECORATOR …] NAME …)` whose decorators include `(dataclass … :kw-only True …)`)
//! but the same-named .pyi in the same dir declares that class with a `@dataclass` without kw_only=True.
//! The type checker reads the .pyi, so it lets calls with positional arguments through, but the runtime `__init__` accepts fields only by name, so it raises TypeError.
//!
//! The .pyi files that are read are those matching `:record-stubs {:files [..] :except [..]}` in the repo's architecture.hy and having a same-named .hy in the same dir.
//! On the .pyi side, only the top-level class decorators `@dataclass` and `@dataclasses.dataclass` (including the call form) are read, and only a `kw_only=True` literal is
//! read as kw-only (absent, False, or a non-literal value is not kw-only). A class without `@dataclass` is not read (it declares no `__init__`, so
//! the type checker does not let calls with positional arguments through — not the dangerous direction). Hy names are mangled to Python names before being matched.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use doeff_indexer::hy_index::reader::{Form, Node, Reader};
use rustpython_ast::{Constant, Expr, Mod, Ranged, Stmt};
use rustpython_parser::{parse, Mode};

use super::architecture::FileSelection;
use super::retired::{judge_files, selected};

const DATACLASS_NAMES: &[&str] = &["dataclass", "dataclasses.dataclass"];
/// How it is written on the .hy side (a word in the notice text).
pub const DEFRECORD_FORM: &str = "defrecord";
pub const KW_ONLY_DEFCLASS_FORM: &str = "defclass with a (dataclass :kw-only True) decorator";

/// One mismatch.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Mismatch {
    /// The byte range of the decorator in the .pyi.
    pub start: usize,
    pub end: usize,
    /// The name of the class.
    pub name: String,
    /// How it is written on the .hy side.
    pub form: &'static str,
}

/// The mismatches of one .pyi that was read.
pub struct FileMismatches {
    pub rel: String,
    pub path: PathBuf,
    pub source: String,
    pub mismatches: Vec<Mismatch>,
}

/// Turns a Hy name into a Python name (same as hy.mangle — a leading `_` is kept, `-` from the 2nd character on becomes `_`, and if the result is not an identifier, the `hyx_` form).
pub fn mangle(name: &str) -> String {
    if name.contains('.') && !name.trim_matches('.').is_empty() {
        return name.split('.').map(|part| if part.is_empty() { String::new() } else { mangle(part) }).collect::<Vec<_>>().join(".");
    }
    let rest = name.trim_start_matches('_');
    let leading = &name[..name.len() - rest.len()];
    let mut chars = rest.chars();
    let body: String = match chars.next() {
        Some(first) => std::iter::once(first).chain(chars.map(|c| if c == '-' { '_' } else { c })).collect(),
        None => String::new(),
    };
    let identifier = |text: &str| {
        let mut cs = text.chars();
        cs.next().is_some_and(|c| c == '_' || c.is_alphabetic()) && cs.all(|c| c == '_' || c.is_alphanumeric())
    };
    if identifier(&format!("{}{}", leading, body)) {
        return format!("{}{}", leading, body);
    }
    let encoded: String = body
        .chars()
        .map(|c| {
            if c != 'X' && (c == '_' || c.is_alphanumeric()) {
                c.to_string()
            } else {
                format!("X{}X", char_name(c))
            }
        })
        .collect();
    format!("{}hyx_{}", leading, encoded)
}

/// The names of the characters hy.mangle uses (unicodedata.name lowercased, `-` becomes H and space becomes _ — characters not in the table become U<hex>).
fn char_name(c: char) -> String {
    let name = match c {
        '!' => "exclamation mark",
        '?' => "question mark",
        '*' => "asterisk",
        '+' => "plus sign",
        '-' => "hyphen-minus",
        '/' => "solidus",
        '<' => "less-than sign",
        '>' => "greater-than sign",
        '=' => "equals sign",
        '%' => "percent sign",
        '&' => "ampersand",
        '$' => "dollar sign",
        '@' => "commercial at",
        '^' => "circumflex accent",
        '~' => "tilde",
        '|' => "vertical line",
        ':' => "colon",
        '\'' => "apostrophe",
        'X' => "latin capital letter x",
        _ => return format!("U{:x}", c as u32),
    };
    name.replace('-', "H").replace(' ', "_")
}

/// Top-level forms of the .hy → an index of classes that accept fields only by name at runtime (Python name → how it is written).
fn hy_kw_only_classes(source: &str) -> Result<BTreeMap<String, &'static str>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("{} places the Hy reader cannot read (first = {:?})", reader.issues.len(), issue));
    }
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("").to_string();
    let kept = |items: &[Form]| -> Vec<usize> { items.iter().enumerate().filter(|(_, f)| !matches!(f.node, Node::Discarded)).map(|(i, _)| i).collect() };
    let mut out = BTreeMap::new();
    for form in &forms {
        let Some(all) = form.paren_items() else { continue };
        let items: Vec<&Form> = kept(all).into_iter().map(|i| &all[i]).collect();
        let symbol = |f: &Form| matches!(f.node, Node::Symbol);
        let head = if items.len() > 1 && symbol(items[0]) { text(items[0]) } else { String::new() };
        let decorators: Vec<&Form> = match items.get(1).and_then(|f| f.bracket_items()) {
            Some(list) if head == "defclass" && items.len() > 2 => list.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect(),
            _ => Vec::new(),
        };
        let kw_only_decorated = decorators.iter().any(|d| {
            let Some(parts) = d.paren_items() else { return false };
            let parts: Vec<&Form> = parts.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
            !parts.is_empty()
                && DATACLASS_NAMES.contains(&text(parts[0]).as_str())
                && (1..parts.len().saturating_sub(1)).any(|i| {
                    matches!(parts[i].node, Node::Keyword)
                        && mangle(text(parts[i]).trim_start_matches(':')) == "kw_only"
                        && symbol(parts[i + 1])
                        && text(parts[i + 1]) == "True"
                })
        });
        if head == "defrecord" && symbol(items[1]) {
            out.insert(mangle(&text(items[1])), DEFRECORD_FORM);
        } else if kw_only_decorated && symbol(items[2]) {
            out.insert(mangle(&text(items[2])), KW_ONLY_DEFCLASS_FORM);
        }
    }
    Ok(out)
}

fn dotted(node: &Expr) -> String {
    match node {
        Expr::Name(name) => name.id.to_string(),
        Expr::Attribute(attribute) => format!("{}.{}", dotted(&attribute.value), attribute.attr),
        _ => String::new(),
    }
}

/// Contents of the .hy and the same-named .pyi → mismatches (among the top-level @dataclass classes of the .pyi, those that are kw-only in the .hy but lack kw_only=True).
pub fn judge(hy_source: &str, stub_source: &str, stub_rel: &str) -> Result<Vec<Mismatch>, String> {
    let classes = hy_kw_only_classes(hy_source).map_err(|reason| format!("cannot read the same-named .hy: {}", reason))?;
    let module = parse(stub_source, Mode::Module, stub_rel).map_err(|error| format!("does not form a syntax tree: {}", error))?;
    let Mod::Module(module) = module else { return Ok(Vec::new()) };
    let mut out = Vec::new();
    for statement in &module.body {
        let Stmt::ClassDef(class) = statement else { continue };
        for decorator in &class.decorator_list {
            let (target, keywords) = match decorator {
                Expr::Call(call) => (call.func.as_ref(), Some(&call.keywords)),
                other => (other, None),
            };
            if !DATACLASS_NAMES.contains(&dotted(target).as_str()) {
                continue;
            }
            let kw_only = keywords.is_some_and(|keywords| {
                keywords.iter().any(|k| {
                    k.arg.as_ref().is_some_and(|a| a.as_str() == "kw_only") && matches!(&k.value, Expr::Constant(c) if matches!(c.value, Constant::Bool(true)))
                })
            });
            if let Some(form) = classes.get(class.name.as_str()).filter(|_| !kw_only) {
                let range = decorator.range();
                out.push(Mismatch { start: range.start().to_usize(), end: range.end().to_usize(), name: class.name.to_string(), form });
            }
        }
    }
    Ok(out)
}

/// The path of the same-named .hy in the same dir.
fn sibling_hy(path: &Path) -> PathBuf {
    path.with_extension("hy")
}

/// Whether this is a .pyi to judge (it matches the declaration's glob, and a same-named .hy exists in the same dir).
pub fn wants(rel: &str, path: &Path, selection: &FileSelection) -> bool {
    rel.ends_with(".pyi") && selected(rel, &selection.files, &selection.except) && sibling_hy(path).is_file()
}

/// Judges one .pyi (its contents as passed in) together with the same-named .hy read from disk.
pub fn judge_file(rel: &str, path: &Path, stub_source: &str) -> Result<Vec<Mismatch>, String> {
    let hy = sibling_hy(path);
    let hy_source = std::fs::read_to_string(&hy).map_err(|error| format!("cannot read the same-named .hy: {}", error))?;
    judge(&hy_source, stub_source, rel)
}

/// Reads and judges the declared .pyi files (only files under the focus if there is one). Unreadable files return a reason.
/// The same-named .pyi next to a named .hy, to be judged (path from the root, and path) — so that when a .hy is fixed, the type declaration that misrepresents its runtime shape is also judged.
pub fn stub_of_hy(hy_rel: &str, hy_path: &Path, selection: &FileSelection) -> Option<(String, PathBuf)> {
    let stem = hy_rel.strip_suffix(".hy")?;
    let rel = format!("{}.pyi", stem);
    let path = hy_path.with_extension("pyi");
    (path.is_file() && wants(&rel, &path, selection)).then_some((rel, path))
}

/// Adds, to the list of named paths, the same-named .pyi next to each named .hy (duplicates removed) — so that when a .hy is fixed, the type declaration that misrepresents its runtime shape is also
/// read (focus), and its hits are kept in the output (narrowing of the output). The one place that reads the names given on the command line (main's only_paths and --modified) uses it.
pub fn with_sibling_stubs(paths: Vec<PathBuf>) -> Vec<PathBuf> {
    let stubs: Vec<PathBuf> =
        paths.iter().filter(|p| p.extension().is_some_and(|e| e == "hy") && p.is_file()).map(|p| p.with_extension("pyi")).filter(|p| p.is_file()).collect();
    let mut out = paths;
    for stub in stubs {
        if !out.contains(&stub) {
            out.push(stub);
        }
    }
    out
}

/// Reads and judges the declared .pyi files (only files under the focus if there is one — the .pyi next to a named .hy is added to the focus by with_sibling_stubs).
/// Unreadable files return a reason.
pub fn find(root: &Path, selection: &FileSelection, focus: Option<&[PathBuf]>) -> (Vec<FileMismatches>, Vec<String>) {
    judge_files(
        root,
        selection.files.iter(),
        focus,
        |rel, path| wants(rel, path, selection),
        |rel, path, source| {
            let mismatches = judge_file(&rel, &path, &source).map_err(|reason| format!("{}: cannot read the DOEFF145 judgment ({})", rel, reason))?;
            Ok((!mismatches.is_empty()).then(|| FileMismatches { rel, path, source, mismatches }))
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn names(hy: &str, stub: &str) -> Vec<(String, &'static str)> {
        judge(hy, stub, "a.pyi").expect("readable").into_iter().map(|m| (m.name, m.form)).collect()
    }

    #[test]
    fn mangle_matches_hy() {
        assert_eq!(mangle("Foo-bar"), "Foo_bar");
        assert_eq!(mangle("_a-b"), "_a_b");
        assert_eq!(mangle("foo?"), "hyx_fooXquestion_markX");
        assert_eq!(mangle("\u{65e5}\u{672c}"), "\u{65e5}\u{672c}");
    }

    #[test]
    fn kw_only_records_need_kw_only_in_the_stub() {
        let hy = "(defrecord Row (#^ str a))\n(defrecord row-two (#^ str b))\n\
                  (defclass [(dataclass :frozen True :kw-only True)] Manual [] (#^ int n))\n\
                  (defclass [(dataclass :frozen True)] Positional [] (#^ int n))\n\
                  (defrecord Fine (#^ str c))\n(defrecord NoDeco (#^ str d))\n#_(defrecord Gone (#^ str e))\n";
        let stub = "import dataclasses\nfrom dataclasses import dataclass\n\
                    @dataclass(frozen=True)\nclass Row:\n    a: str\n\
                    @dataclasses.dataclass\nclass row_two:\n    b: str\n\
                    @dataclass(frozen=True, kw_only=False)\nclass Manual:\n    n: int\n\
                    @dataclass(frozen=True)\nclass Positional:\n    n: int\n\
                    @dataclass(frozen=True, kw_only=True)\nclass Fine:\n    c: str\n\
                    class NoDeco:\n    d: str\n\
                    @dataclass\nclass Gone:\n    e: str\n";
        assert_eq!(
            names(hy, stub),
            vec![("Row".to_string(), DEFRECORD_FORM), ("row_two".to_string(), DEFRECORD_FORM), ("Manual".to_string(), KW_ONLY_DEFCLASS_FORM)]
        );
    }

    #[test]
    fn kw_only_must_be_the_literal_true() {
        let hy = "(defrecord A (#^ str a))\n(defrecord B (#^ str b))\n";
        let stub = "KW = True\n@dataclass(kw_only=KW)\nclass A: ...\n@dataclass(kw_only=True)\nclass B: ...\n";
        assert_eq!(names(hy, stub), vec![("A".to_string(), DEFRECORD_FORM)]);
    }

    #[test]
    fn unreadable_sides_are_reasons() {
        assert!(judge("(defrecord A", "class A: ...\n", "a.pyi").is_err());
        assert!(judge("(defrecord A)", "class A(:\n", "a.pyi").is_err());
    }
}
