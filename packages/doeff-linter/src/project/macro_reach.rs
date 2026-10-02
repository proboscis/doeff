//! 層の宣言で DOEFF110(defn)から外した module の defn が、package のどれかの defmacro から届くか(agora-redesign #2913)。
//!
//! `:exempt [(rule "DOEFF110" "理由")]` で外す理由は「macro の展開の時点(と、macro が生成した code)に Python の関数として呼ばれ、
//! Program を返せない」こと。その主張を名の届きで確かめる: package(宣言の file の dir)の Hy の file の全部の defmacro の form に
//! 出る名を根にし、最上位の関数の定義(defn・defn/a・deff・defk・defp・defpp・`(setv 名 (fn …))`)の本体に出る名を辿って、届く名の
//! 集合を作る。外した module の defn でも、どの defmacro からも届かない物は実行の時点の定義なので、外さずに DOEFF110 で鳴らす
//! (#2877 の決め 2 の見張り — 外した module に実行の時点の定義が入ったら鳴る)。
//!
//! - defmacro の form は quasiquote の中も数える — 展開の時点の呼びと、展開した code が Python の関数として呼ぶ部品(defk の guard
//!   など)の両方が「macro の持ち主の関数」だから。
//! - 名は綴りだけで照らす(module を解かない)。同じ名の別の module の関数が届けば届いたと数える — 見逃す向きにだけ緩い。
//!   `a.b.c` の形の名は段ごとにも数える(`(doeff_hy.declarations.effect-types …)` を `effect-types` として拾う)。
//! - 検の dir(`tests` の段を持つ path)の file の defmacro は根に数えない(検の macro が呼ぶ関数は実行の時点の物と同じ扱い)。
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};

use doeff_indexer::hy_index::reader::{Form, Node, Reader};

use super::names::hy_mangle;

/// macro の定義の頭(defn_to_defk の MACROS と同じ)。
const MACRO_HEADS: &[&str] = &["defmacro", "defmacro/g!", "defreader"];
/// 名を辿る関数の定義の頭。
const FUNCTION_HEADS: &[&str] = &["defn", "defn/a", "deff", "defk", "defp", "defpp"];

/// 1 つの source の読み: defmacro の form に出る名と、関数の定義の名 → 本体に出る名。
#[derive(Debug, Default, PartialEq, Eq)]
pub struct SourceReach {
    pub roots: BTreeSet<String>,
    pub bodies: BTreeMap<String, BTreeSet<String>>,
}

/// source 1 つを読む(最上位と `do`・`eval-and-compile`・`eval-when-compile` の中を最上位として見る)。
pub fn read_source(src: &str) -> SourceReach {
    let forms = Reader::new(src, 0, src.len()).read_all();
    let mut reach = SourceReach::default();
    for form in &forms {
        top_level(src, form, &mut reach);
    }
    reach
}

fn top_level(src: &str, form: &Form, reach: &mut SourceReach) {
    let Some(items) = form.paren_items() else { return };
    let Some(head) = items.first().and_then(|h| symbol(src, h)) else { return };
    match head {
        "do" | "eval-and-compile" | "eval-when-compile" => {
            for inner in &items[1..] {
                top_level(src, inner, reach);
            }
        }
        _ if MACRO_HEADS.contains(&head) => reach.roots.extend(names_in(src, form)),
        _ if FUNCTION_HEADS.contains(&head) && items.len() >= 3 => {
            // `(defn [decorators] name …)` は decorator の list を飛ばして名を読む。
            let name_form = if items[1].bracket_items().is_some() { &items[2] } else { &items[1] };
            if let Some(name) = defined_name(src, name_form) {
                reach.bodies.entry(name).or_default().extend(names_in(src, form));
            }
        }
        "setv" | "val" if items.len() == 3 => {
            let is_fn = items[2].paren_items().and_then(|inner| inner.first()).and_then(|h| symbol(src, h)).is_some_and(|h| matches!(h, "fn" | "fn/a"));
            if let (true, Some(name)) = (is_fn, symbol(src, &items[1])) {
                reach.bodies.entry(hy_mangle(name)).or_default().extend(names_in(src, &items[2]));
            }
        }
        _ => {}
    }
}

/// 定義の名(`#^ T 名` の注記を外す)。
fn defined_name(src: &str, form: &Form) -> Option<String> {
    match &form.node {
        Node::Symbol => Some(hy_mangle(text(src, form))),
        Node::Annotated { target: Some(target), .. } => defined_name(src, target),
        _ => None,
    }
}

/// form の中に出る全部の名(mangle 済み・`a.b.c` は全体と段ごと)。
fn names_in(src: &str, form: &Form) -> BTreeSet<String> {
    let mut names = BTreeSet::new();
    walk(src, form, &mut names);
    names
}

fn walk(src: &str, form: &Form, names: &mut BTreeSet<String>) {
    match &form.node {
        Node::Symbol => {
            let spelled = text(src, form);
            names.insert(hy_mangle(spelled));
            if spelled.contains('.') {
                names.extend(spelled.split('.').filter(|part| !part.is_empty()).map(hy_mangle));
            }
        }
        Node::Seq { items, .. } => items.iter().for_each(|item| walk(src, item, names)),
        Node::Prefixed { inner: Some(inner), .. } | Node::Tagged { inner: Some(inner) } => walk(src, inner, names),
        Node::Annotated { annotation, target } => {
            annotation.iter().chain(target.iter()).for_each(|part| walk(src, part, names));
        }
        _ => {}
    }
}

fn symbol<'a>(src: &'a str, form: &Form) -> Option<&'a str> {
    matches!(form.node, Node::Symbol).then(|| text(src, form))
}

fn text<'a>(src: &'a str, form: &Form) -> &'a str {
    &src[form.span.start..form.span.end]
}

/// 根(defmacro に出る名)から関数の本体を辿って届く名の全部。
pub fn reached(sources: &[SourceReach]) -> BTreeSet<String> {
    // 呼びの辺(定義の名, 本体に出る名)— 同じ名の定義が 2 つの file に在れば両方の辺を持つ。
    let edges: BTreeSet<(&str, &str)> = sources
        .iter()
        .flat_map(|source| source.bodies.iter())
        .flat_map(|(name, body)| body.iter().map(move |callee| (name.as_str(), callee.as_str())))
        .collect();
    let roots: BTreeSet<String> = sources.iter().flat_map(|source| source.roots.iter().cloned()).collect();
    let mut seen = roots.clone();
    let mut frontier: Vec<String> = roots.into_iter().collect();
    while let Some(name) = frontier.pop() {
        let next: Vec<String> = edges
            .range((name.as_str(), "")..)
            .take_while(|(caller, _)| *caller == name.as_str())
            .map(|(_, callee)| *callee)
            .filter(|callee| !seen.contains(*callee))
            .map(str::to_string)
            .collect();
        seen.extend(next.iter().cloned());
        frontier.extend(next);
    }
    seen
}

/// 宣言の file の dir(package)の Hy の file を読んで、defmacro から届く名(同じ package は 1 回の実行で 1 度だけ読む)。
/// 検の dir(`tests` の段)の file は数えない。読めない file は飛ばす(届かない向き = 鳴る向きにだけ倒れる)。
pub fn reached_in_package(package: &Path) -> Arc<BTreeSet<String>> {
    static MEMO: OnceLock<Mutex<HashMap<PathBuf, Arc<BTreeSet<String>>>>> = OnceLock::new();
    let memo = MEMO.get_or_init(Default::default);
    if let Some(found) = memo.lock().expect("届きの memo").get(package) {
        return Arc::clone(found);
    }
    let sources: Vec<SourceReach> = super::hy_files::collect(package)
        .into_iter()
        .filter(|path| !path.strip_prefix(package).unwrap_or(path).components().any(|part| part.as_os_str() == "tests"))
        .filter_map(|path| std::fs::read_to_string(&path).ok())
        .map(|src| read_source(&src))
        .collect();
    let found = Arc::new(reached(&sources));
    memo.lock().expect("届きの memo").insert(package.to_path_buf(), Arc::clone(&found));
    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_defn_named_in_a_macro_and_its_callees_are_reached_and_others_are_not() {
        let macros = read_source(
            "(defn _expand [form] (_spell form))\n\
             (defn _spell [form] form)\n\
             (defmacro m [form] (_expand form))\n\
             (defmacro g [] `(do (doeff_hy.guards._guard 1)))\n",
        );
        let runtime = read_source("(defn thaw [x] x)\n(defn _guard [x] x)\n");
        let found = reached(&[macros, runtime]);
        // 展開の時点の呼び(_expand)と、その本体が呼ぶ物(_spell)と、展開した code の呼び(quasiquote の中の段 _guard)は届く。
        for name in ["_expand", "_spell", "_guard"] {
            assert!(found.contains(name), "{name} が届かない: {found:?}");
        }
        // 失敗ケース: どの macro からも届かない実行の時点の関数は届かない。
        assert!(!found.contains("thaw"), "{found:?}");
    }

    #[test]
    fn annotated_names_decorators_and_compile_time_blocks_are_read_as_definitions() {
        let source = read_source(
            "(eval-and-compile (defn #^ str _quoted [x] (_inner x)))\n\
             (defn [staticmethod] _decorated [x] x)\n\
             (defn _inner [x] x)\n\
             (defmacro q [x] (_quoted x))\n",
        );
        assert!(source.bodies.contains_key("_quoted"), "{source:?}");
        assert!(source.bodies.contains_key("_decorated"), "{source:?}");
        let found = reached(&[source]);
        assert!(found.contains("_inner"), "{found:?}");
        assert!(!found.contains("_decorated"), "{found:?}");
    }
}
