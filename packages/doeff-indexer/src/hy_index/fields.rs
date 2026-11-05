//! The Rust-side source of truth for how defrecord / defwire fields are read — hy-index's record_def and doeff-linter's DOEFF204 (the class field list) both
//! call this one reader. The Hy-side source of truth is doeff-hy's `doeff_hy.declarations/field-targets` (called by defrecord and defwire).
//! That the two sources give the same answer is checked by both sides' tests reading the same input table, `packages/doeff-hy/tests/data/record_field_cases.json`.
//!
//! Forms counted as fields (in written order): a bare symbol `x`, `#^ T x`, `(#^ T x)`, and the annotated targets of `(setv #^ T1 a v1 #^ T2 b v2 …)` (one per pair).
//! A target of `(setv x v)` with no annotation is not a dataclass field (it is a class attribute), so it is not counted. Any other form is not a field.

use super::reader::{Form, Node, Span};

/// One field (the range of its name and the range of its type annotation — a bare symbol has no annotation).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FieldTarget {
    pub name: Span,
    pub annotation: Option<Span>,
        /// The whole form that wrote the field (for a field listed in a `(setv …)`, the whole setv — so evidence inside a default-value expression is attributed to the field).
    pub member: Span,
}

/// Drops discards (`#_`) from the contents of a list.
fn live(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// The (name, type) of `#^ T x`. None if it is not an annotation form.
fn annotated(form: &Form, member: Span) -> Option<FieldTarget> {
    match &form.node {
        Node::Annotated { annotation: Some(annotation), target: Some(target) } if matches!(target.node, Node::Symbol) => {
            Some(FieldTarget { name: target.span, annotation: Some(annotation.span), member })
        }
        _ => None,
    }
}

/// Reads fields in written order from the list of forms in a record's body. `src` is for looking at symbol spellings (the head of `setv`, and to exclude operator symbols).
pub fn record_field_targets(src: &str, forms: &[&Form]) -> Vec<FieldTarget> {
    let text = |span: Span| src.get(span.start..span.end).unwrap_or("");
    let mut out = Vec::new();
    for form in forms {
        match &form.node {
            Node::Symbol => out.push(FieldTarget { name: form.span, annotation: None, member: form.span }),
            Node::Annotated { .. } => out.extend(annotated(form, form.span)),
            Node::Seq { .. } => {
                let Some(items) = form.paren_items().map(live) else { continue };
                match items.as_slice() {
                    [only] => out.extend(annotated(only, form.span)),
                    [head, rest @ ..] if matches!(head.node, Node::Symbol) && text(head.span) == "setv" => {
                        out.extend(rest.iter().step_by(2).filter_map(|target| annotated(target, form.span)));
                    }
                    _ => {}
                }
            }
            _ => {}
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::hy_index::reader::Reader;

        /// The shared test table (the Hy-side source of truth, doeff_hy.declarations/field-targets, reads the same table in its tests).
    fn cases() -> Vec<(String, Vec<String>)> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../doeff-hy/tests/data/record_field_cases.json");
                let table: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("cannot read the shared test table")).expect("the table is not JSON");
        table["cases"]
            .as_array()
                        .expect("no cases")
            .iter()
            .map(|case| {
                                let names = case["names"].as_array().expect("no names").iter().map(|n| n.as_str().unwrap_or("").to_string()).collect();
                (case["forms"].as_str().unwrap_or("").to_string(), names)
            })
            .collect()
    }

    #[test]
    fn record_field_targets_match_the_shared_case_table() {
        for (forms, names) in cases() {
            let mut reader = Reader::new(&forms, 0, forms.len());
            let read = reader.read_all();
            let refs: Vec<&Form> = read.iter().collect();
            let got: Vec<String> = record_field_targets(&forms, &refs).iter().map(|t| forms[t.name.start..t.name.end].to_string()).collect();
            assert_eq!(got, names, "{:?}", forms);
        }
    }

    #[test]
    fn hy_index_record_fields_match_the_shared_case_table() {
                // hy-index's record_def also calls this reader — the field (kind field, container R) names match the table.
        for (forms, names) in cases() {
            let source = format!("(defrecord R {{:tags {{:context \"c\" :role \"type\"}}}}\n{})\n", forms);
            let index = crate::hy_index::index_source(std::path::Path::new("/r"), std::path::Path::new("/r/m.hy"), &source);
            let got: Vec<String> = index
                .definitions
                .iter()
                .filter(|d| d.container.as_deref() == Some("R") && d.kind == crate::hy_index::DefinitionKind::Field)
                .map(|d| d.name.clone())
                .collect();
            assert_eq!(got, names, "{:?}", forms);
        }
    }
}
