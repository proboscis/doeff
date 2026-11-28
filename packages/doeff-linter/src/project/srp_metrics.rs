//! DOEFF217: the cohesion of a type within the module that defines it (LCOM4 — Hitz & Montazeri 1995, after Chidamber & Kemerer 1994), and the fields of a type the
//! defining module never touches (the fact DOEFF216 and the Tier 2 review read). One reading of the module's functions serves both, so the reference analysis has
//! one source (docs/design/srp-design-gate/artifacts/v1/design.md of agora-controllers, S2).
//!
//! LCOM4 of a type T in its defining file:
//! - the functions of T are the file's top-level functions (Hy: `defn`, `defk`, `deff`, `defp`, `defh`, `defhandler`; Rust: `fn` at the top level or in an `impl`
//!   block — not in an inline `mod`) whose text names T (Rust: also `Self` in an `impl T`);
//! - a function touches a field `f` of T when its text reads or writes it — Hy: a dotted symbol `x.f` or `(. x f)`, a keyword `:f`, `(getattr x "f")`; Rust: `.f`,
//!   or `f` as a member of a `T { … }` / `Self { … }` literal or pattern (kebab and snake spellings are one name);
//! - two functions of T are joined when they touch a common field of T or one calls the other by name;
//! - LCOM4(T) is the number of connected groups that touch at least one field of T (a function that touches no field and joins nothing says nothing about how the
//!   fields cluster, so it is not a group of its own). 0 = the file has no function of T; 1 = cohesive; 2 or more = the fields fall into unrelated groups.
//!
//! A change degrades T (DOEFF217) when LCOM4 after the change is above max(1, LCOM4 at the base) — a new type counts its base as 0. Enums are not judged: their
//! "fields" are cases of a sum, not facts of a record.

use std::collections::{BTreeMap, BTreeSet};

use doeff_indexer::hy_index::reader::{Form, Node, Reader};

use super::design_evidence::{shapes_of, tokenize_rust, RecordShape, ShapeKind, Token, TokenKind};

/// The heads of the Hy forms that define a function.
const HY_FUNCTION_HEADS: &[&str] = &["defn", "defn/a", "defk", "deff", "defp", "defh", "defhandler"];
/// The Hy calls whose string argument names an attribute.
const HY_ATTRIBUTE_CALLS: &[&str] = &["getattr", "setattr", "hasattr", "delattr"];

/// The cohesion of one type in the file that defines it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Cohesion {
    pub type_name: String,
    /// The number of connected groups of the type's functions that touch a field of the type.
    pub lcom4: usize,
    /// The groups, each the sorted names of its functions (sorted by their first name — the order is part of the output, so it is fixed).
    pub groups: Vec<Vec<String>>,
    /// The fields no function of the file touches, in written order (empty when the file defines no function: a types-only module is not judged).
    pub unused_fields: Vec<String>,
}

/// A type whose cohesion the change made worse.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Lcom4Degraded {
    pub type_name: String,
    /// LCOM4 at the base (None = the base has no such type).
    pub before: Option<usize>,
    pub after: usize,
    pub groups: Vec<Vec<String>>,
}

/// One function of the file as the analysis reads it.
#[derive(Debug, Clone, Default)]
struct Function {
    name: String,
    /// The type names the function's text names (Rust: `Self` is spelled as the impl's type).
    types: BTreeSet<String>,
    /// The names it touches as a field (normalized: `-` read as `_`).
    members: BTreeSet<String>,
    /// The names it calls.
    calls: BTreeSet<String>,
}

fn normal(name: &str) -> String {
    name.replace('-', "_")
}

/// The cohesion of every record and struct the file defines (Err if the source cannot be read).
pub fn cohesion_of(rel: &str, source: &str) -> Result<Vec<Cohesion>, String> {
    let shapes = shapes_of(rel, source)?;
    let functions = if rel.ends_with(".rs") { rust_functions(source) } else { hy_functions(source)? };
    Ok(shapes.iter().filter(|s| s.kind != ShapeKind::Enum).map(|shape| cohesion(shape, &functions)).collect())
}

/// Whether LCOM4 `after` the change is worse than `before` (None = the base has no such type): above max(1, before).
pub fn degrades(before: Option<usize>, after: usize) -> bool {
    after > before.unwrap_or(0).max(1)
}

/// The types of `after` whose LCOM4 is above max(1, their LCOM4 in `before`).
pub fn degraded(before: &[Cohesion], after: &[Cohesion]) -> Vec<Lcom4Degraded> {
    after
        .iter()
        .filter_map(|now| {
            let was = before.iter().find(|b| b.type_name == now.type_name).map(|b| b.lcom4);
            degrades(was, now.lcom4).then(|| Lcom4Degraded { type_name: now.type_name.clone(), before: was, after: now.lcom4, groups: now.groups.clone() })
        })
        .collect()
}

fn cohesion(shape: &RecordShape, functions: &[Function]) -> Cohesion {
    let fields: Vec<(String, String)> = shape.fields.iter().map(|f| (f.name.clone(), normal(&f.name))).collect();
    let field_names: BTreeSet<&str> = fields.iter().map(|(_, n)| n.as_str()).collect();
    let unused_fields = if functions.is_empty() {
        Vec::new()
    } else {
        fields.iter().filter(|(_, n)| !functions.iter().any(|f| f.members.contains(n))).map(|(name, _)| name.clone()).collect()
    };
    let of_type: Vec<&Function> = functions.iter().filter(|f| f.types.contains(&shape.name)).collect();
    let touched: Vec<BTreeSet<&str>> =
        of_type.iter().map(|f| f.members.iter().map(String::as_str).filter(|m| field_names.contains(m)).collect()).collect();
    // Union-find over the functions of the type.
    let mut parent: Vec<usize> = (0..of_type.len()).collect();
    fn root(parent: &mut [usize], mut i: usize) -> usize {
        while parent[i] != i {
            parent[i] = parent[parent[i]];
            i = parent[i];
        }
        i
    }
    let mut owner: BTreeMap<&str, usize> = BTreeMap::new();
    for (i, fields) in touched.iter().enumerate() {
        for field in fields {
            match owner.get(field) {
                Some(&j) => {
                    let (a, b) = (root(&mut parent, i), root(&mut parent, j));
                    parent[a] = b;
                }
                None => {
                    owner.insert(field, i);
                }
            }
        }
    }
    let index: BTreeMap<&str, usize> = of_type.iter().enumerate().map(|(i, f)| (f.name.as_str(), i)).collect();
    for (i, f) in of_type.iter().enumerate() {
        for j in f.calls.iter().filter_map(|c| index.get(c.as_str())) {
            let (a, b) = (root(&mut parent, i), root(&mut parent, *j));
            parent[a] = b;
        }
    }
    let mut groups: BTreeMap<usize, (bool, Vec<String>)> = BTreeMap::new();
    for (i, f) in of_type.iter().enumerate() {
        let r = root(&mut parent, i);
        let group = groups.entry(r).or_default();
        group.0 |= !touched[i].is_empty();
        group.1.push(f.name.clone());
    }
    let mut groups: Vec<Vec<String>> = groups
        .into_values()
        .filter(|(touches, _)| *touches)
        .map(|(_, mut names)| {
            names.sort();
            names.dedup();
            names
        })
        .collect();
    groups.sort();
    Cohesion { type_name: shape.name.clone(), lcom4: groups.len(), groups, unused_fields }
}

// ---- Hy ----

fn hy_functions(source: &str) -> Result<Vec<Function>, String> {
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("{} places the Hy reader cannot read (first = {:?})", reader.issues.len(), issue));
    }
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("");
    let mut out = Vec::new();
    for form in &forms {
        let Some(items) = form.paren_items() else { continue };
        let live: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
        let Some(head) = live.first() else { continue };
        if !matches!(head.node, Node::Symbol) || !HY_FUNCTION_HEADS.contains(&text(head)) {
            continue;
        }
        // The name follows the decorators `[…]`, and may carry its return type (`#^ T name`).
        let Some(at) = live.iter().skip(1).position(|f| !matches!(f.node, Node::Seq { .. })).map(|p| p + 1) else { continue };
        let name = match &live[at].node {
            Node::Symbol => live[at],
            Node::Annotated { target: Some(target), .. } if matches!(target.node, Node::Symbol) => target,
            _ => continue,
        };
        let mut function = Function { name: normal(text(name)), ..Function::default() };
        if let Node::Annotated { annotation: Some(annotation), .. } = &live[at].node {
            hy_walk(source, annotation, &mut function);
        }
        for item in &live[at + 1..] {
            hy_walk(source, item, &mut function);
        }
        out.push(function);
    }
    Ok(out)
}

fn hy_walk(source: &str, form: &Form, function: &mut Function) {
    let text = |f: &Form| source.get(f.span.start..f.span.end).unwrap_or("");
    match &form.node {
        Node::Symbol => {
            let symbol = text(form);
            let parts: Vec<&str> = symbol.split('.').collect();
            if let Some(last) = parts.last() {
                function.types.insert(last.to_string());
            }
            if parts.len() > 1 {
                function.members.extend(parts[1..].iter().filter(|p| !p.is_empty()).map(|p| normal(p)));
            }
        }
        Node::Keyword => {
            function.members.insert(normal(text(form).trim_start_matches(':')));
        }
        Node::Seq { items, .. } => {
            let live: Vec<&Form> = items.iter().filter(|f| !matches!(f.node, Node::Discarded)).collect();
            if let Some(head) = live.first().filter(|h| matches!(h.node, Node::Symbol)) {
                let head = text(head);
                if head == "." {
                    function.members.extend(live.iter().skip(2).filter(|f| matches!(f.node, Node::Symbol)).map(|f| normal(text(f))));
                } else if HY_ATTRIBUTE_CALLS.contains(&head) {
                    if let Some(Node::Str { body, .. }) = live.get(2).map(|f| &f.node) {
                        function.members.insert(normal(source.get(body.start..body.end).unwrap_or("")));
                    }
                } else if form.paren_items().is_some() {
                    function.calls.insert(normal(head));
                }
            }
            for item in live {
                hy_walk(source, item, function);
            }
        }
        Node::Prefixed { inner, .. } | Node::Tagged { inner } => {
            if let Some(inner) = inner {
                hy_walk(source, inner, function);
            }
        }
        Node::Annotated { annotation, target } => {
            for part in [annotation, target].into_iter().flatten() {
                hy_walk(source, part, function);
            }
        }
        Node::Str { .. } | Node::Number | Node::Discarded => {}
    }
}

// ---- Rust ----

fn rust_functions(source: &str) -> Vec<Function> {
    let tokens = tokenize_rust(source);
    let ident = |i: usize| match tokens.get(i).map(|t| &t.kind) {
        Some(TokenKind::Ident(name)) => Some(name.as_str()),
        _ => None,
    };
    let punct = |i: usize, c: char| matches!(tokens.get(i).map(|t| &t.kind), Some(TokenKind::Punct(p)) if *p == c);
    let text = |i: usize| tokens.get(i).map(|t| source.get(t.start..t.end).unwrap_or("")).unwrap_or("");
    // The index of the `}` matching the `{` at `open`.
    let close = |open: usize| {
        let mut depth = 0i32;
        for (i, t) in tokens.iter().enumerate().skip(open) {
            match t.kind {
                TokenKind::Punct('{') => depth += 1,
                TokenKind::Punct('}') => {
                    depth -= 1;
                    if depth == 0 {
                        return i;
                    }
                }
                _ => {}
            }
        }
        tokens.len().saturating_sub(1)
    };
    let mut out = Vec::new();
    // (end of the impl block, the impl's type)
    let mut impls: Vec<(usize, String)> = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        impls.retain(|(end, _)| *end > i);
        match ident(i) {
            Some("mod") if ident(i + 1).is_some() && punct(i + 2, '{') => {
                i = close(i + 2) + 1;
                continue;
            }
            Some("impl") => {
                let mut j = i + 1;
                let (mut angle, mut target) = (0i32, None::<String>);
                while j < tokens.len() && !(angle == 0 && punct(j, '{')) && !punct(j, ';') {
                    match &tokens[j].kind {
                        TokenKind::Punct('<') => angle += 1,
                        TokenKind::Punct('>') if !punct(j.saturating_sub(1), '-') => angle -= 1,
                        TokenKind::Ident(name) if angle == 0 && name == "for" => {
                            target = None;
                        }
                        TokenKind::Ident(name) if angle == 0 && name != "dyn" && name != "where" && target.is_none() => {
                            target = Some(name.clone());
                        }
                        TokenKind::Punct(':') if angle == 0 && punct(j + 1, ':') => target = None,
                        _ => {}
                    }
                    j += 1;
                }
                if punct(j, '{') {
                    if let Some(target) = target {
                        impls.push((close(j), target));
                    }
                }
                i = j + 1;
                continue;
            }
            Some("fn") if ident(i + 1).is_some() => {
                let name = text(i + 1).to_string();
                let mut j = i + 2;
                let mut paren = 0i32;
                while j < tokens.len() && !(paren == 0 && (punct(j, '{') || punct(j, ';'))) {
                    if punct(j, '(') {
                        paren += 1;
                    } else if punct(j, ')') {
                        paren -= 1;
                    }
                    j += 1;
                }
                if !punct(j, '{') {
                    i = j + 1;
                    continue;
                }
                let end = close(j);
                let mut function = Function { name, ..Function::default() };
                let own_type = impls.last().map(|(_, t)| t.clone());
                rust_read(&tokens, source, i + 2, j, end, own_type.as_deref(), &mut function);
                out.push(function);
                i = end + 1;
                continue;
            }
            _ => {}
        }
        i += 1;
    }
    out
}

fn rust_read(tokens: &[Token], source: &str, from: usize, body: usize, to: usize, own_type: Option<&str>, function: &mut Function) {
    let kind = |i: usize| tokens.get(i).map(|t| &t.kind);
    let punct = |i: usize, c: char| matches!(kind(i), Some(TokenKind::Punct(p)) if *p == c);
    let spelled = |i: usize| tokens.get(i).map(|t| source.get(t.start..t.end).unwrap_or("")).unwrap_or("");
    if let Some(own) = own_type {
        function.types.insert(own.to_string());
    }
    let mut i = from;
    while i < to {
        match kind(i) {
            Some(TokenKind::Ident(name)) => {
                let name = if name == "Self" { own_type.unwrap_or("Self") } else { name.as_str() };
                function.types.insert(name.to_string());
                let after_dot = punct(i.wrapping_sub(1), '.') && !punct(i.wrapping_sub(2), '.');
                if after_dot {
                    function.members.insert(name.to_string());
                }
                if punct(i + 1, '(') {
                    function.calls.insert(name.to_string());
                }
                // A literal or pattern `T { a, b: x, .. }` in the body (a return type before the body`s `{` is not one): its members at depth 1.
                if i > body && name.chars().next().is_some_and(char::is_uppercase) && punct(i + 1, '{') {
                    let mut depth = 0i32;
                    let mut j = i + 1;
                    while j < to {
                        match kind(j) {
                            Some(TokenKind::Punct('{' | '(' | '[')) => depth += 1,
                            Some(TokenKind::Punct('}' | ')' | ']')) => {
                                depth -= 1;
                                if depth == 0 {
                                    break;
                                }
                            }
                            Some(TokenKind::Ident(member))
                                if depth == 1
                                    && (punct(j - 1, '{') || punct(j - 1, ','))
                                    && ((punct(j + 1, ':') && !punct(j + 2, ':')) || punct(j + 1, ',') || punct(j + 1, '}')) =>
                            {
                                function.members.insert(member.clone());
                            }
                            _ => {}
                        }
                        j += 1;
                    }
                }
            }
            // A positional field: `.0`.
            Some(TokenKind::Literal) if punct(i.wrapping_sub(1), '.') && !punct(i.wrapping_sub(2), '.') => {
                if let Some(index) = spelled(i).split('.').next().filter(|s| s.chars().all(|c| c.is_ascii_digit())) {
                    function.members.insert(index.to_string());
                }
            }
            _ => {}
        }
        i += 1;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const HY_COHESIVE: &str = r#"
(defrecord LendingState
  (#^ str holder)
  (#^ int count))

(defn lending-move [#^ LendingState state]
  (LendingState :holder state.holder :count (+ state.count 1)))

(defn lending-holder [#^ LendingState state]
  (. state holder))
"#;

    const HY_SPLIT: &str = r#"
(defrecord LendingState
  (#^ str holder)
  (#^ int count)
  (#^ bool paused-credentials))

(defn lending-move [#^ LendingState state]
  (LendingState :holder state.holder :count (+ state.count 1) :paused-credentials False))

(defn lending-holder [#^ LendingState state]
  (. state holder))

(defn pause-note [#^ LendingState state]
  (getattr state "paused-credentials"))
"#;

    const HY_SPLIT_ALONE: &str = r#"
(defrecord LendingState
  (#^ str holder)
  (#^ int count)
  (#^ bool paused-credentials))

(defn lending-move [#^ LendingState state]
  (LendingState :holder state.holder :count (+ state.count 1)))

(defn pause-note [#^ LendingState state]
  state.paused-credentials)
"#;

    const RS_COHESIVE: &str = r#"
pub struct Lending { holder: String, count: u32 }

impl Lending {
    fn bump(&mut self) { self.count += 1; }
    fn holder(&self) -> &str { if self.count > 0 { &self.holder } else { "" } }
}

fn fresh() -> Lending { Lending { holder: String::new(), count: 0 } }
"#;

    const RS_SPLIT: &str = r#"
pub struct Lending { holder: String, count: u32, paused: bool }

impl Lending {
    fn bump(&mut self) { self.count += 1; }
    fn holder(&self) -> &str { if self.count > 0 { &self.holder } else { "" } }
    fn pause(&mut self) { self.paused = true; }
}

#[cfg(test)]
mod tests {
    fn uses(l: &super::Lending) -> bool { l.paused && l.count > 0 }
}
"#;

    fn one(rel: &str, source: &str) -> Cohesion {
        cohesion_of(rel, source).unwrap().into_iter().next().unwrap()
    }

    #[test]
    fn hy_lcom4_goes_from_1_to_2_when_a_field_only_a_new_function_touches_is_added() {
        let before = cohesion_of("a.hy", HY_COHESIVE).unwrap();
        assert_eq!(before[0].lcom4, 1, "{:?}", before);
        let after = cohesion_of("a.hy", HY_SPLIT_ALONE).unwrap();
        assert_eq!(after[0].lcom4, 2, "{:?}", after);
        let hits = degraded(&before, &after);
        assert_eq!(hits.len(), 1);
        assert_eq!((hits[0].before, hits[0].after), (Some(1), 2));
        assert_eq!(hits[0].groups, vec![vec!["lending_move".to_string()], vec!["pause_note".to_string()]]);
    }

    #[test]
    fn hy_a_shared_field_keeps_lcom4_at_1_and_getattr_and_keywords_are_references() {
        let after = one("a.hy", HY_SPLIT);
        assert_eq!(after.lcom4, 1, "{:?}", after);
        assert!(after.unused_fields.is_empty(), "{:?}", after);
        assert!(degraded(&cohesion_of("a.hy", HY_COHESIVE).unwrap(), &[after]).is_empty());
    }

    #[test]
    fn rust_lcom4_goes_from_1_to_2_and_a_test_module_does_not_join_the_groups() {
        let before = cohesion_of("a.rs", RS_COHESIVE).unwrap();
        assert_eq!(before[0].lcom4, 1, "{:?}", before);
        let after = cohesion_of("a.rs", RS_SPLIT).unwrap();
        assert_eq!(after[0].lcom4, 2, "{:?}", after);
        let hits = degraded(&before, &after);
        assert_eq!((hits[0].before, hits[0].after), (Some(1), 2));
        assert_eq!(hits[0].groups[1], vec!["pause".to_string()]);
    }

    #[test]
    fn rust_a_cohesive_change_passes_and_a_literal_names_its_members() {
        let grown = RS_COHESIVE.replace("count: u32 }", "count: u32, since: u64 }").replace("count: 0 }", "count: 0, since: 0 }")
            .replace("self.count += 1;", "self.count += 1; self.since += 1;");
        let after = cohesion_of("a.rs", &grown).unwrap();
        assert_eq!(after[0].lcom4, 1, "{:?}", after);
        assert!(after[0].unused_fields.is_empty(), "{:?}", after);
        assert!(degraded(&cohesion_of("a.rs", RS_COHESIVE).unwrap(), &after).is_empty());
    }

    #[test]
    fn a_field_no_function_touches_is_unused_and_a_types_only_module_is_not_judged() {
        let unused = one("a.rs", "struct S { a: u8, b: u8 }\nfn f(s: S) -> u8 { s.a }\n");
        assert_eq!(unused.unused_fields, vec!["b".to_string()]);
        let types_only = one("a.hy", "(defrecord S (#^ int a))\n");
        assert_eq!((types_only.lcom4, types_only.unused_fields.len()), (0, 0));
    }

    #[test]
    fn a_new_type_with_two_groups_is_degraded_from_nothing() {
        let after = cohesion_of("a.rs", RS_SPLIT).unwrap();
        let hits = degraded(&[], &after);
        assert_eq!((hits[0].before, hits[0].after), (None, 2));
    }
}
