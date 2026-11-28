//! DOEFF215 and DOEFF216: the width and the cohesion of a type the change made (docs/design/srp-design-gate, Tier 1 — decided from the source alone, without the facts).
//!
//! Both judge a type against its version at the base (`base` = the base's version of the same file; None = the file is new, so every type in it is new):
//! - DOEFF215 (Wide): a new type wider than `WIDTH_LIMIT` fields, or an existing type that gained fields and is wider than `WIDTH_LIMIT` after the change. A sum is
//!   judged per variant: one more variant is one more case, not one more fact, so only a variant's payload counts. The fields past the limit (a new type) or the grown
//!   ones (an existing type) pass only with an approval `ledger = "fields"` at the base for each (key `<rel>::<Type>`, value the field).
//! - DOEFF216 (UnusedInModule): a field an existing type gained that no function body of the type's own module reads or writes — the fact rides on this type for another
//!   module (a deterministic stand-in for LCOM4 ≥ 2). A module without function bodies (a `:types-only` layer, a module of types only) is not judged: its types are read
//!   elsewhere by design. A new type is not judged either: its fields are its design, which DOEFF212's evidence answers.

use doeff_indexer::hy_index::reader::{Form, Node, Reader};

use super::design_evidence::{live, payload_members, shapes_of, tokenize_rust, Hit, Problem, RecordShape, ShapeKind, TokenKind};
use super::ledger_guard::{ApprovalLedger, BaseApprovals};

/// The most fields a type may have (a record's fields, or a variant's payload members).
pub const WIDTH_LIMIT: usize = 5;

/// The heads of the Hy forms that hold a function body (where a field is read or written).
const HY_BODY_HEADS: [&str; 11] = ["defn", "defn/a", "defk", "deff", "defp", "defhandler", "defmacro", "defmethod", "defmain", "defprogram", "fn"];

/// The hits of DOEFF215 and DOEFF216 of one module (Err if the source or the base version cannot be read — the caller passes None only for a file the base does not have).
pub fn judge(rel: &str, source: &str, base: Option<&str>, approvals: &BaseApprovals) -> Result<Vec<Hit>, String> {
    let shapes = shapes_of(rel, source)?;
    let base_shapes = base.map(|was| shapes_of(rel, was)).transpose()?.unwrap_or_default();
    let bodies = function_bodies(rel, source)?;
    let mut hits = Vec::new();
    for shape in &shapes {
        let was = base_shapes.iter().find(|b| b.name == shape.name && b.kind == shape.kind);
        let key = format!("{}::{}", rel, shape.name);
        let hit = |problem: Problem| Hit { name: shape.name.clone(), start: shape.start, end: shape.end, problem };
        let (width, past) = past_the_limit(shape, was);
        let unapproved: Vec<String> = past.into_iter().filter(|f| !approvals.covers(ApprovalLedger::Fields, &key, f)).collect();
        if !unapproved.is_empty() {
            hits.push(hit(Problem::Wide { width, fields: unapproved }));
        }
        let Some(was) = was else { continue };
        if bodies.is_empty() {
            continue;
        }
        let unused: Vec<String> = shape.grown_against(was).into_iter().filter(|f| !read_in_bodies(rel, source, &bodies, member_name(f))).collect();
        if !unused.is_empty() {
            hits.push(hit(Problem::UnusedInModule { fields: unused }));
        }
    }
    Ok(hits)
}

/// The widest record or payload the change made past the limit, and the fields that put it there (empty = within the limit, or not made by the change).
fn past_the_limit(shape: &RecordShape, was: Option<&RecordShape>) -> (usize, Vec<String>) {
    match shape.kind {
        ShapeKind::Defrecord | ShapeKind::Struct => {
            let width = shape.fields.len();
            let past = match was {
                _ if width <= WIDTH_LIMIT => Vec::new(),
                None => shape.fields[WIDTH_LIMIT..].iter().map(|f| f.name.clone()).collect(),
                Some(was) => shape.grown_against(was),
            };
            (width, past)
        }
        ShapeKind::Enum => {
            let mut widest = 0;
            let mut past = Vec::new();
            for variant in &shape.fields {
                let members = payload_members(variant.type_text.as_deref());
                if members.len() <= WIDTH_LIMIT {
                    continue;
                }
                let before = was.and_then(|w| w.fields.iter().find(|v| v.name == variant.name)).map(|v| payload_members(v.type_text.as_deref()));
                let grown: Vec<String> = match before {
                    None => members[WIDTH_LIMIT..].to_vec(),
                    Some(before) => members.iter().filter(|m| !before.contains(m)).cloned().collect(),
                };
                if !grown.is_empty() {
                    widest = widest.max(members.len());
                    past.extend(grown.into_iter().map(|m| format!("{}.{}", variant.name, m)));
                }
            }
            (widest, past)
        }
    }
}

/// The name a grown field is spelled by in code (`Variant.member` → `member`).
fn member_name(field: &str) -> &str {
    field.rsplit_once('.').map(|(_, m)| m).unwrap_or(field)
}

/// The byte ranges of the module's function bodies: the Hy forms whose head is a function definition, or the `{ … }` of each Rust `fn` that has one (a trait's `fn x();`
/// has none).
fn function_bodies(rel: &str, source: &str) -> Result<Vec<(usize, usize)>, String> {
    if rel.ends_with(".rs") {
        return Ok(rust_bodies(source));
    }
    let mut reader = Reader::new(source, 0, source.len());
    let forms = reader.read_all();
    if let Some(issue) = reader.issues.first() {
        return Err(format!("{} places the Hy reader cannot read (first = {:?})", reader.issues.len(), issue));
    }
    Ok(forms.iter().filter(|f| hy_head(source, f).is_some_and(|h| HY_BODY_HEADS.contains(&h))).map(|f| (f.span.start, f.span.end)).collect())
}

fn hy_head<'a>(source: &'a str, form: &Form) -> Option<&'a str> {
    let items = live(form.paren_items()?);
    let head = items.first().filter(|h| matches!(h.node, Node::Symbol))?;
    source.get(head.span.start..head.span.end)
}

fn rust_bodies(source: &str) -> Vec<(usize, usize)> {
    let tokens = tokenize_rust(source);
    let mut bodies = Vec::new();
    let mut i = 0;
    while i < tokens.len() {
        if !matches!(&tokens[i].kind, TokenKind::Ident(kw) if kw == "fn") {
            i += 1;
            continue;
        }
        let mut j = i + 1;
        let mut depth = 0i32;
        while j < tokens.len() {
            match tokens[j].kind {
                TokenKind::Punct('(') | TokenKind::Punct('[') => depth += 1,
                TokenKind::Punct(')') | TokenKind::Punct(']') => depth -= 1,
                TokenKind::Punct(';') if depth == 0 => break,
                TokenKind::Punct('{') if depth == 0 => break,
                _ => {}
            }
            j += 1;
        }
        if j >= tokens.len() || tokens[j].kind != TokenKind::Punct('{') {
            i = j + 1;
            continue;
        }
        let start = tokens[j].start;
        let mut braces = 0i32;
        while j < tokens.len() {
            match tokens[j].kind {
                TokenKind::Punct('{') => braces += 1,
                TokenKind::Punct('}') => {
                    braces -= 1;
                    if braces == 0 {
                        break;
                    }
                }
                _ => {}
            }
            j += 1;
        }
        let end = tokens.get(j).map(|t| t.end).unwrap_or(source.len());
        bodies.push((start, end));
        i = j + 1;
    }
    bodies
}

/// Whether a function body reads or writes the field: a Rust identifier of the name, or a Hy symbol part (`x.name`, `(. x name)`, `:name`) or a string that is exactly the
/// name (`getattr x "name"`), in either the Hy spelling or the mangled one. Comments and other strings (docstrings) do not count.
fn read_in_bodies(rel: &str, source: &str, bodies: &[(usize, usize)], field: &str) -> bool {
    if rel.ends_with(".rs") {
        return tokenize_rust(source)
            .iter()
            .any(|t| matches!(&t.kind, TokenKind::Ident(name) if name == field) && bodies.iter().any(|(s, e)| *s <= t.start && t.end <= *e));
    }
    let mangled = field.replace('-', "_");
    bodies.iter().any(|(s, e)| source.get(*s..*e).is_some_and(|body| hy_mentions(body, field, &mangled)))
}

fn hy_mentions(body: &str, field: &str, mangled: &str) -> bool {
    let named = |word: &str| word == field || word == mangled;
    let mut chars = body.char_indices().peekable();
    let mut word_start: Option<usize> = None;
    let word_names = |from: Option<usize>, to: usize| from.is_some_and(|s| body[s..to].split('.').any(|part| named(part.trim_start_matches(':'))));
    while let Some((i, c)) = chars.next() {
        let delimiter = c.is_whitespace() || "()[]{}'`~,@^#;\"".contains(c);
        if !delimiter {
            word_start.get_or_insert(i);
            continue;
        }
        if word_names(word_start.take(), i) {
            return true;
        }
        match c {
            ';' => {
                for (_, c) in chars.by_ref() {
                    if c == '\n' {
                        break;
                    }
                }
            }
            '"' => {
                let from = i + 1;
                let mut to = body.len();
                while let Some((j, c)) = chars.next() {
                    match c {
                        '\\' => {
                            chars.next();
                        }
                        '"' => {
                            to = j;
                            break;
                        }
                        _ => {}
                    }
                }
                if named(&body[from..to]) {
                    return true;
                }
            }
            _ => {}
        }
    }
    word_names(word_start, body.len())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::ledger_guard::Approvals;

    /// The base of a `LendingState` of six fields, each read by a function of its module (the case this rule was written for).
    const LENDING_BASE: &str = r#"
(defrecord HeldLease (#^ str holder) (#^ str lease-id))

(defrecord LendingState
  "the lending state of one machine"
  (#^ (get tuple #(ReturnRun ...)) returning)
  (#^ (get tuple #(UndeliveredReturn ...)) unreturned)
  (#^ (get tuple #(ExtendRun ...)) extending)
  (#^ (get tuple #(TouchRun ...)) touching)
  (#^ int last-seq)
  (#^ (get tuple #(HeldLease ...)) leases))

(val LENDING-START (LendingState :returning #() :unreturned #() :extending #() :touching #() :last-seq 0 :leases #()))

(deff lease-of [state holder]
  "the lease of a holder — paused-credentials is not read here"
  (lease-in state.leases holder))

(deff next-seq [state]
  ; paused-credentials is not read here
  (LendingState :returning state.returning :unreturned state.unreturned :extending state.extending
                :touching state.touching :last-seq (+ 1 (. state last-seq)) :leases state.leases))
"#;

    /// The change that was sent back by review: a seventh field that only another module reads.
    fn lending_grown() -> String {
        LENDING_BASE.replace("(#^ (get tuple #(HeldLease ...)) leases))", "(#^ (get tuple #(HeldLease ...)) leases)\n  (setv #^ (get frozenset PausedCredential) paused-credentials (frozenset)))")
    }

    fn problems(rel: &str, source: &str, base: Option<&str>, approvals: &BaseApprovals) -> Vec<(String, Problem)> {
        judge(rel, source, base, approvals).expect("readable").into_iter().map(|h| (h.name, h.problem)).collect()
    }

    fn approved(key: &str, value: &str) -> BaseApprovals {
        Approvals::parse(&format!("[[approval]]\nledger = \"fields\"\nkey = \"{}\"\nvalue = \"{}\"\nreview = \"req-1\"\nwhy = \"the user approved\"\n", key, value))
            .unwrap()
            .spelled(None)
    }

    #[test]
    fn lending_state_paused_credentials_is_refused_by_both_rules() {
        let found = problems("core/cli_lending.hy", &lending_grown(), Some(LENDING_BASE), &BaseApprovals::default());
        let field = vec!["paused-credentials".to_string()];
        assert_eq!(
            found,
            vec![
                ("LendingState".to_string(), Problem::Wide { width: 7, fields: field.clone() }),
                ("LendingState".to_string(), Problem::UnusedInModule { fields: field }),
            ]
        );
        assert_eq!(found[0].1.rule(), "DOEFF215");
        assert_eq!(found[1].1.rule(), "DOEFF216");
    }

    #[test]
    fn the_unchanged_base_and_a_grown_field_its_module_reads_are_not_unused() {
        assert_eq!(problems("core/cli_lending.hy", LENDING_BASE, Some(LENDING_BASE), &BaseApprovals::default()), vec![]);
        let used = format!("{}\n(deff paused-of [state]\n  (get state \"paused-credentials\"))\n", lending_grown());
        let found = problems("core/cli_lending.hy", &used, Some(LENDING_BASE), &BaseApprovals::default());
        assert!(matches!(found.as_slice(), [(_, Problem::Wide { .. })]), "only the width is left: {:?}", found);
        let attr = format!("{}\n(deff paused-of [state]\n  state.paused-credentials)\n", lending_grown());
        let found = problems("core/cli_lending.hy", &attr, Some(LENDING_BASE), &BaseApprovals::default());
        assert!(matches!(found.as_slice(), [(_, Problem::Wide { .. })]), "{:?}", found);
    }

    #[test]
    fn an_approval_at_the_base_lets_the_width_pass_but_not_the_unused_field() {
        let approvals = approved("core/cli_lending.hy::LendingState", "paused-credentials");
        let found = problems("core/cli_lending.hy", &lending_grown(), Some(LENDING_BASE), &approvals);
        assert!(matches!(found.as_slice(), [(_, Problem::UnusedInModule { .. })]), "{:?}", found);
    }

    #[test]
    fn a_new_type_past_the_limit_is_wide_and_one_within_it_is_not() {
        let six = "(defrecord Six (#^ str a) (#^ str b) (#^ str c) (#^ str d) (#^ str e) (#^ str f))\n(deff use [x] x.a)\n";
        let found = problems("core/m.hy", six, None, &BaseApprovals::default());
        assert_eq!(found, vec![("Six".to_string(), Problem::Wide { width: 6, fields: vec!["f".to_string()] })], "a new type is not judged for unused fields");
        let five = "(defrecord Five (#^ str a) (#^ str b) (#^ str c) (#^ str d) (#^ str e))\n";
        assert_eq!(problems("core/m.hy", five, None, &BaseApprovals::default()), vec![]);
        let base = "(defrecord Five (#^ str a))\n";
        assert_eq!(problems("core/m.hy", six.replace("Six", "Five").as_str(), Some(base), &BaseApprovals::default()).len(), 2, "a type the base had under another name");
    }

    #[test]
    fn an_existing_wide_type_that_does_not_grow_is_the_bases_debt() {
        let seven = "(defrecord Wide (#^ str a) (#^ str b) (#^ str c) (#^ str d) (#^ str e) (#^ str f) (#^ str g))\n(deff use [x] x.a)\n";
        let retyped = seven.replace("(#^ str g)", "(#^ int g)");
        assert_eq!(problems("core/m.hy", &retyped, Some(seven), &BaseApprovals::default()), vec![]);
        let shrunk = seven.replace(" (#^ str g)", "");
        assert_eq!(problems("core/m.hy", &shrunk, Some(seven), &BaseApprovals::default()), vec![]);
    }

    #[test]
    fn a_types_only_module_has_no_false_hit() {
        let intent_base = "(defrecord ReviewId (#^ str id))\n(defrecord SrpTarget (#^ str card) (#^ str rel))\n";
        let intent_now = "(defrecord ReviewId (#^ str id))\n(defrecord SrpTarget (#^ str card) (#^ str rel) (#^ str base))\n";
        assert_eq!(problems("intent/types.hy", intent_now, Some(intent_base), &BaseApprovals::default()), vec![]);
        let rust_base = "pub struct SrpTarget { pub card: String }\npub trait Store { fn read(&self, id: &str) -> Option<SrpTarget>; }\n";
        let rust_now = "pub struct SrpTarget { pub card: String, pub rel: String }\npub trait Store { fn read(&self, id: &str) -> Option<SrpTarget>; }\n";
        assert_eq!(problems("intent/src/lib.rs", rust_now, Some(rust_base), &BaseApprovals::default()), vec![]);
    }

    #[test]
    fn rust_fields_are_judged_by_the_bodies_of_their_module() {
        let base = "pub struct Item { pub key: String }\nfn show(i: &Item) -> &str { &i.key }\n";
        let unused = "pub struct Item { pub key: String, pub owner: String }\n// owner\nfn show(i: &Item) -> &str { &i.key }\n";
        let found = problems("src/item.rs", unused, Some(base), &BaseApprovals::default());
        assert_eq!(found, vec![("Item".to_string(), Problem::UnusedInModule { fields: vec!["owner".to_string()] })]);
        let used = "pub struct Item { pub key: String, pub owner: String }\nfn show(i: &Item) -> String { let Item { key, owner } = i; format!(\"{key}{owner}\") }\n";
        assert_eq!(problems("src/item.rs", used, Some(base), &BaseApprovals::default()), vec![]);
    }

    #[test]
    fn a_sum_is_judged_by_its_payloads_not_its_variants() {
        let base = "pub enum E { A, B, C, D, E }\nfn f(e: &E) -> u8 { 0 }\n";
        let more = "pub enum E { A, B, C, D, E, F, G }\nfn f(e: &E) -> u8 { 0 }\n";
        assert_eq!(problems("src/e.rs", more, Some(base), &BaseApprovals::default()), vec![], "a new variant is a new case");
        let payload = "pub enum E { A { a: u8, b: u8, c: u8, d: u8, e: u8, f: u8 }, B }\nfn f(e: &E) -> u8 { match e { E::A { f, .. } => *f, E::B => 0 } }\n";
        let found = problems("src/e.rs", payload, None, &BaseApprovals::default());
        assert_eq!(found, vec![("E".to_string(), Problem::Wide { width: 6, fields: vec!["A.f".to_string()] })]);
    }
}
