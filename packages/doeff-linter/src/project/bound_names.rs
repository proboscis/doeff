//! Names bound to a group's spelling (DOEFF148's `:names-bound-in [<path> …]`) — a part that, within the named Hy files, also treats names bound to values matching a group's
//! spelling as part of the group's spelling. Bindings = names in the declaration's `:environ {"name" "value" …}` pairs (a doeff-cluster job declaration — the value is a reference spelling) and the names of top-level
//! `(val name value)` / `(setv name value)` (plus the Python-mangled names). Names bound to values matching the added names are added too, until nothing more is added (a declaration's reference name
//! → the constant name holding it → the sequence listing that constant).
//! If a group keeps the names of references pointing at a secret storage as a copy in a regular expression, then even when a new reference name (such as the old key's reference in a key rotation) is added to a declaration the group
//! does not guard it, and reading it in another file does not turn red (the group web-push-private-key of agora-controllers).
//! The derivation happens when architecture.hy is read (names_bound_in in architecture.rs); this file holds only the pure parts: reading bindings and deriving.
//! (It does not depend on architecture — DOEFF148's judge confined_spellings.rs reads architecture's groups, so putting it here would make a dependency cycle.)

use doeff_indexer::hy_index::reader::{Delim, Form, Node, Reader, StrKind};

use super::names::hy_mangle;
use super::spelling_scope::code_text;

/// Form of a binding (a closed set of two).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BindingForm {
    /// A pair in the declaration's `:environ {"name" "value" …}` (the name is a string — an environment variable name; the value is a string literal).
    Environ,
    /// A top-level `(val name value)` / `(setv name value)` (the name is a Hy symbol — its spelling mangled into a Python name is counted too; the value is the form's spelling).
    Constant,
}

/// One binding of a name to a value (within a :names-bound-in file). value is the spelling to match — for :environ pairs it is the string value; for a constant it is the value form's spelling with comments removed
/// (a sequence of reference names `#("…" "…")` and a construction `(+ name "…")` are matched too — the same reading as DOEFF148, which judges by statement spelling).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Binding {
    pub name: String,
    pub value: String,
    pub form: BindingForm,
    /// The file holding the binding (path from the repo root).
    pub origin: String,
}

/// One name bound to a group's spelling, the regular expression of that name's spelling (including the mangled name for Hy symbols), and the file holding the binding.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BoundName {
    pub name: String,
    pub patterns: Vec<String>,
    pub origin: String,
}

/// Read the bindings in source of Hy file rel (`:environ` pairs of declarations at any depth; `(val|setv name value)` at top level only — names bound inside a function have no meaning
/// outside that function). Comments are dropped before reading. None if parentheses or strings are not closed.
pub fn bindings_of(rel: &str, source: &str) -> Option<Vec<Binding>> {
    let code = code_text(rel, source);
    let mut reader = Reader::new(&code, 0, code.len());
    let forms = reader.read_all();
    if !reader.issues.is_empty() {
        return None;
    }
    let mut out = Vec::new();
    for form in &forms {
        if let Some(binding) = constant_of(rel, &code, form) {
            out.push(binding);
        }
        environ_pairs(rel, &code, form, &mut out);
    }
    Some(out)
}

/// Derive names bound to values matching the group spellings patterns (regular expressions), in binding order (including names bound to values matching the spellings of added names, until nothing more is added).
pub fn names_bound_to(patterns: &[String], bindings: &[Binding]) -> Vec<BoundName> {
    let mut regexes: Vec<regex::Regex> = patterns.iter().filter_map(|p| regex::Regex::new(p).ok()).collect();
    let mut out: Vec<BoundName> = Vec::new();
    loop {
        let mut grew = false;
        for binding in bindings {
            if out.iter().any(|n| n.name == binding.name) || !regexes.iter().any(|re| re.is_match(&binding.value)) {
                continue;
            }
            let mut spellings = vec![binding.name.clone()];
            match binding.form {
                BindingForm::Constant => {
                    let mangled = hy_mangle(&binding.name);
                    if mangled != binding.name {
                        spellings.push(mangled);
                    }
                }
                BindingForm::Environ => {}
            }
            let found: Vec<String> = spellings.iter().map(|s| literal_pattern(s)).collect();
            regexes.extend(found.iter().filter_map(|p| regex::Regex::new(p).ok()));
            out.push(BoundName { name: binding.name.clone(), patterns: found, origin: binding.origin.clone() });
            grew = true;
        }
        if !grew {
            return out;
        }
    }
}

/// Regular expression matching a name's spelling (a `\b` is added if the edge is a word character — `KEY_REF` does not match `KEY_REFS`. Hy's `-` is not a word character,
/// so `KEY-REF-ENV` also matches `MY-KEY-REF-ENV` — the same breadth as a hand-written copy `\bKEY-REF-ENV\b`. A direction that matches broadly shows up red and is not silent).
fn literal_pattern(name: &str) -> String {
    let word = |c: char| c.is_alphanumeric() || c == '_';
    let head = if name.chars().next().is_some_and(word) { r"\b" } else { "" };
    let tail = if name.chars().last().is_some_and(word) { r"\b" } else { "" };
    format!("{}{}{}", head, regex::escape(name), tail)
}

/// Spelling of a form.
fn text<'s>(source: &'s str, form: &Form) -> &'s str {
    source.get(form.span.start..form.span.end).unwrap_or("")
}

/// Value of a string literal (plain strings, r"…", bracket strings — f-strings and bytes have no fixed value, so they are not read).
fn string_of(source: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Str { kind: StrKind::Plain | StrKind::Raw | StrKind::Bracket, body } => source.get(body.start..body.end).map(|t| t.replace("\\\"", "\"")),
        Node::Str { kind: StrKind::Format | StrKind::Bytes | StrKind::FormatBracket, .. }
        | Node::Seq { .. }
        | Node::Symbol
        | Node::Keyword
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => None,
    }
}

/// The contents of a sequence excluding discarded reads (None if not a sequence).
fn live_items(form: &Form) -> Option<Vec<&Form>> {
    match &form.node {
        Node::Seq { items, .. } => Some(items.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect()),
        Node::Symbol
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Annotated { .. }
        | Node::Discarded
        | Node::Tagged { .. } => None,
    }
}

/// A top-level `(val name value)` / `(setv name value)` (read even if the name carries a type annotation `#^ T name`; the value is the form's spelling).
fn constant_of(rel: &str, source: &str, form: &Form) -> Option<Binding> {
    let items = form.paren_items()?.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect::<Vec<_>>();
    let [head, name, value] = items.as_slice() else { return None };
    if !matches!(head.node, Node::Symbol) || !matches!(text(source, head), "val" | "setv") {
        return None;
    }
    let name = match &name.node {
        Node::Symbol => text(source, name),
        Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => text(source, target),
        Node::Annotated { .. }
        | Node::Seq { .. }
        | Node::Keyword
        | Node::Str { .. }
        | Node::Number
        | Node::Prefixed { .. }
        | Node::Discarded
        | Node::Tagged { .. } => return None,
    };
    Some(Binding { name: name.to_string(), value: text(source, value).to_string(), form: BindingForm::Constant, origin: rel.to_string() })
}

/// Collect `:environ {"name" "value" …}` pairs in forms at any depth (the rows of a doeff-cluster job declaration — a defsystem's :environ accepts only a dict literal of
/// string keys and string values).
fn environ_pairs(rel: &str, source: &str, form: &Form, out: &mut Vec<Binding>) {
    let Some(items) = live_items(form) else {
        return;
    };
    for (index, item) in items.iter().enumerate() {
        let is_environ = matches!(item.node, Node::Keyword) && text(source, item) == ":environ";
        if let (true, Some(Node::Seq { delim: Delim::Brace, items: entries })) = (is_environ, items.get(index + 1).map(|f| &f.node)) {
            let entries: Vec<&Form> = entries.iter().filter(|i| !matches!(i.node, Node::Discarded)).collect();
            for pair in entries.chunks(2) {
                if let [key, value] = pair {
                    if let (Some(name), Some(value)) = (string_of(source, key), string_of(source, value)) {
                        out.push(Binding { name, value, form: BindingForm::Environ, origin: rel.to_string() });
                    }
                }
            }
        }
        environ_pairs(rel, source, item, out);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `:environ` pairs of declarations (string values) are read at any depth; `(val|setv name value)` (the value is the form's spelling, comments dropped) only at top level.
    #[test]
    fn bindings_are_environ_pairs_and_top_level_constants() {
        let source = "(defsystem s [f]\n  \"system\"\n  (job (job f) :replicas 1 :environ {\"KEY_REF\" \"secret:ns/vapid/k\" \"PORT\" \"8872\"}))\n\
                      (val KEY-REF-ENV \"KEY_REF\")\n(setv #^ str TYPED \"KEY_REF\")\n(val REFS #(KEY-REF-ENV ; note on KEY_REF\n  \"KEY_REF\"))\n\
                      (defk read [] (val local \"KEY_REF\") local)\n";
        let names: Vec<(String, String, BindingForm, String)> =
            bindings_of("s/keys.hy", source).unwrap().into_iter().map(|b| (b.name, b.value, b.form, b.origin)).collect();
        let at = |name: &str, value: &str, form: BindingForm| (name.to_string(), value.to_string(), form, "s/keys.hy".to_string());
        // A comment becomes as many spaces as its byte count (spelling_scope::code_text — positions are preserved).
        let refs = format!("#(KEY-REF-ENV {}\n  \"KEY_REF\")", " ".repeat("; note on KEY_REF".len()));
        assert_eq!(
            names,
            vec![
                at("KEY_REF", "secret:ns/vapid/k", BindingForm::Environ),
                at("PORT", "8872", BindingForm::Environ),
                at("KEY-REF-ENV", "\"KEY_REF\"", BindingForm::Constant),
                at("TYPED", "\"KEY_REF\"", BindingForm::Constant),
                at("REFS", &refs, BindingForm::Constant),
            ]
        );
        assert_eq!(bindings_of("s/keys.hy", "(val A \"x\""), None);
    }

    /// Derive names bound to values matching the group spellings, then also derive names bound to values matching the derived names (a declaration's reference name → the constant name holding it and its mangled name →
    /// the sequence listing that constant or the constructed name — after a blind review, it turned out that reading only constants whose values are literals silently drops the names of reference sequences).
    /// Names are matched at word boundaries (KEY_REF does not match KEY_REFS).
    #[test]
    fn names_bound_to_the_spelling_are_derived_until_none_grow() {
        let source = "(val KEY-REF-ENV \"KEY_REF\")\n(val OTHER-ENV \"PORT\")\n(val REFS #(KEY-REF-ENV))\n\
                      (val OLD-REF-ENV (+ KEY-REF-ENV \"_OLD\"))\n\
                      (defsystem s [f] \"system\" (job (job f) :replicas 1 :environ {\"KEY_REF\" \"secret:ns/vapid/k\" \"PORT\" \"8872\"}))\n";
        let bindings = bindings_of("s/keys.hy", source).unwrap();
        let derived = names_bound_to(&[r"vapid".to_string()], &bindings);
        let bound = |name: &str, patterns: &[&str]| BoundName {
            name: name.to_string(),
            patterns: patterns.iter().map(|p| p.to_string()).collect(),
            origin: "s/keys.hy".to_string(),
        };
        assert_eq!(
            derived,
            vec![
                bound("KEY_REF", &[r"\bKEY_REF\b"]),
                bound("KEY-REF-ENV", &[r"\bKEY\-REF\-ENV\b", r"\bKEY_REF_ENV\b"]),
                bound("REFS", &[r"\bREFS\b"]),
                bound("OLD-REF-ENV", &[r"\bOLD\-REF\-ENV\b", r"\bOLD_REF_ENV\b"]),
            ]
        );
        let all: Vec<regex::Regex> = derived.iter().flat_map(|n| n.patterns.iter()).map(|p| regex::Regex::new(p).unwrap()).collect();
        assert!(all.iter().any(|re| re.is_match("(supplied-setting KEY-REF-ENV)")));
        assert!(!all.iter().any(|re| re.is_match("KEY_REFSET is another name")));
        assert_eq!(names_bound_to(&[r"nowhere".to_string()], &bindings), vec![]);
    }
}
