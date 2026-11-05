//! Shared helper that looks up the definition a declaration names, from the top level of a Hy file (DOEFF147, DOEFF159, DOEFF160, DOEFF161).
//!
//! A definition = a top-level form of the file whose head symbol starts with `def` (defk, deff, defn, defclass, defrecord ...) and whose second symbol, once mangled, equals the mangled name.
//! Building the index (index_paths) takes seconds for a 2,000-line file, so the lookup uses only the reader's tree.

use doeff_indexer::hy_index::mangle;
use doeff_indexer::hy_index::reader::{Form, Node};

/// The form among `forms` (the top-level forms read from `source`) that defines the name `name`.
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
        let found = definition(source, &forms, "carry-to-inbox").expect("the definition is found");
        assert!(source[found.span.start..found.span.end].starts_with("(defk carry_to_inbox"));
        assert!(definition(source, &forms, "gone").is_none());
    }
}
