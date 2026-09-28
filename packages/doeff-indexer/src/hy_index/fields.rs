//! defrecord / defwire の欄の読み方の Rust 側の正本 — hy-index の record_def と doeff-linter の DOEFF204(class の欄の一覧)が
//! この 1 つを呼ぶ。Hy 側の正本は doeff-hy の `doeff_hy.declarations/field-targets`(defrecord と defwire が呼ぶ)。
//! 2 つの正本が同じ答えを出すことは、同じ入力の表 `packages/doeff-hy/tests/data/record_field_cases.json` を両側の検が読んで確かめる。
//!
//! 欄として数える形(書いた順): 裸の記号 `x`・`#^ T x`・`(#^ T x)`・`(setv #^ T1 a v1 #^ T2 b v2 …)` の注記つきの的(組ごとに 1 つ)。
//! 注記の無い `(setv x v)` の的は dataclass の欄ではない(class の属性)ので数えない。それ以外の form は欄ではない。

use super::reader::{Form, Node, Span};

/// 欄 1 つ(名の範囲と、注記の型の範囲 — 裸の記号は注記なし)。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FieldTarget {
    pub name: Span,
    pub annotation: Option<Span>,
    /// 欄を書いた form 全体(`(setv …)` に並べた欄はその setv の全体 — 既定値の式の中の証拠を欄に帰すため)。
    pub member: Span,
}

/// 列の中身から読み捨て(`#_`)を除く。
fn live(items: &[Form]) -> Vec<&Form> {
    items.iter().filter(|item| !matches!(item.node, Node::Discarded)).collect()
}

/// `#^ T x` の (名・型)。注記の形でなければ None。
fn annotated(form: &Form, member: Span) -> Option<FieldTarget> {
    match &form.node {
        Node::Annotated { annotation: Some(annotation), target: Some(target) } if matches!(target.node, Node::Symbol) => {
            Some(FieldTarget { name: target.span, annotation: Some(annotation.span), member })
        }
        _ => None,
    }
}

/// record の body の form の列から欄を書いた順に読む。`src` は記号の綴りを見るため(`setv` の頭と、演算子の記号を除くため)。
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

    /// 共通の検の表(Hy 側の正本 doeff_hy.declarations/field-targets の検も同じ表を読む)。
    fn cases() -> Vec<(String, Vec<String>)> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../doeff-hy/tests/data/record_field_cases.json");
        let table: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("共通の検の表が読めない")).expect("表が JSON でない");
        table["cases"]
            .as_array()
            .expect("cases が無い")
            .iter()
            .map(|case| {
                let names = case["names"].as_array().expect("names が無い").iter().map(|n| n.as_str().unwrap_or("").to_string()).collect();
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
        // hy-index の record_def もこの読み手を呼ぶ — 欄(kind field・container R)の名が表と揃う。
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
