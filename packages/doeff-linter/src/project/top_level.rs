//! 宣言が名指す定義を Hy の file の top level から探す共通の部品(DOEFF147・DOEFF159・DOEFF160・DOEFF161 — agora-redesign #1372・#1373)。
//!
//! 定義 = file の top level の form のうち、頭の記号が `def` で始まり(defk・deff・defn・defclass・defrecord …)、2 つ目の記号を mangle
//! した綴りが名を mangle した綴りと同じ物。索引(index_paths)は 2,000 行の file で秒のけたかかるので、読み取り器の木だけで探す。

use doeff_indexer::hy_index::mangle;
use doeff_indexer::hy_index::reader::{Form, Node};

/// forms(source を読んだ top level の form)のうち、名 name を定義する form。
pub fn definition<'f>(source: &str, forms: &'f [Form], name: &str) -> Option<&'f Form> {
    let wanted = mangle(name);
    let symbol = |form: &Form| matches!(form.node, Node::Symbol).then(|| &source[form.span.start..form.span.end]);
    forms.iter().find(|form| match form.paren_items() {
        Some([head, named, ..]) => symbol(head).is_some_and(|h| h.starts_with("def")) && symbol(named).is_some_and(|n| mangle(n) == wanted),
        _ => false,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use doeff_indexer::hy_index::reader::Reader;

    #[test]
    fn only_top_level_definitions_of_the_mangled_name_are_found() {
        let source = "(setv carry-to-inbox 1)\n(defk outer [] (defk carry-to-inbox []))\n(defk carry_to_inbox [] None)\n";
        let forms = Reader::new(source, 0, source.len()).read_all();
        let found = definition(source, &forms, "carry-to-inbox").expect("定義が見つかる");
        assert!(source[found.span.start..found.span.end].starts_with("(defk carry_to_inbox"));
        assert!(definition(source, &forms, "gone").is_none());
    }
}
