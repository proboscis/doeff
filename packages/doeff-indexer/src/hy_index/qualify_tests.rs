//! 完全修飾名(版 4)のテスト — 定義の `qualified_name` と呼び出しの `target` を、Hy の source を索引して確かめる。

use std::path::Path;

use super::index_source;
use super::model::{Call, HyFileIndex};

/// source を `/r/<path>` として索引する(root は `/r`)。
fn index_at(path: &str, source: &str) -> HyFileIndex {
    index_source(Path::new("/r"), &Path::new("/r").join(path), source)
}

/// 呼び出しの頭の名前(最後の区切り)と修飾で呼び出しを 1 つ引く。
fn call<'a>(file: &'a HyFileIndex, qualifier: Option<&str>, callee: &str) -> &'a Call {
    file.calls
        .iter()
        .find(|c| c.callee == callee && c.qualifier.as_deref() == qualifier)
        .unwrap_or_else(|| panic!("呼び出し {:?}.{callee} が無い", qualifier))
}

/// 名前で定義の完全修飾名を引く。
fn qualified<'a>(file: &'a HyFileIndex, name: &str) -> &'a str {
    &file.definitions.iter().find(|d| d.name == name).unwrap_or_else(|| panic!("定義 {name} が無い")).qualified_name
}

const SAMPLE: &str = r#"(import doeff-records.memory [MemoryStore put-row :as put])
(import pkg.util)
(import pkg [helpers])
(import .sibling [near-fn])
(import ..parent-mod [far-fn])
(require doeff-hy.macros [defk defhandler <-])

(defclass Store []
  (defn put [self x] x))

(defhandler store-handler []
  (PutRow [k v] (resume (local-fn k))))

(defn local-fn [x] (Store.put x) (Store x))

(defk run-it [f]
  (<- (PutRow 1 2))
  (put 1)
  (MemoryStore)
  (pkg.util.fmt 1)
  (pkg.util.deep.inner 2)
  (helpers.go 3)
  (MemoryStore.open 4)
  (near-fn)
  (far-fn)
  (f 1)
  (print 2)
  (when True (pkg.util)))
"#;

#[test]
fn definitions_carry_module_container_and_mangled_name() {
    let file = index_at("pkg/sub/sample.hy", SAMPLE);
    assert_eq!(qualified(&file, "local-fn"), "pkg.sub.sample.local_fn");
    assert_eq!(qualified(&file, "run-it"), "pkg.sub.sample.run_it");
    assert_eq!(qualified(&file, "Store"), "pkg.sub.sample.Store");
    assert_eq!(qualified(&file, "put"), "pkg.sub.sample.Store.put", "method は入れ物の class の名を挟む");
    assert_eq!(qualified(&file, "PutRow"), "pkg.sub.sample.store_handler.PutRow", "effect 節は handler の名を挟む");
}

#[test]
fn root_package_init_has_no_leading_dot() {
    let file = index_at("__init__.hy", "(defn top [] 1)\n");
    assert_eq!(file.module, "");
    assert_eq!(qualified(&file, "top"), "top");
}

#[test]
fn calls_resolve_by_local_definitions_then_imports() {
    let file = index_at("pkg/sub/sample.hy", SAMPLE);
    let target = |qualifier: Option<&str>, callee: &str| call(&file, qualifier, callee).target.clone();
    // 同じ file の定義 — 修飾なしは top level、`q.name` は入れ物 q の中
    assert_eq!(target(None, "local-fn").as_deref(), Some("pkg.sub.sample.local_fn"));
    assert_eq!(target(Some("Store"), "put").as_deref(), Some("pkg.sub.sample.Store.put"));
    assert_eq!(target(None, "Store").as_deref(), Some("pkg.sub.sample.Store"));
    // import の名前・別名(module の綴りは mangle する)
    assert_eq!(target(None, "MemoryStore").as_deref(), Some("doeff_records.memory.MemoryStore"));
    assert_eq!(target(None, "put").as_deref(), Some("doeff_records.memory.put_row"), "別名は元の名へ戻す");
    // module の import の dotted
    assert_eq!(target(Some("pkg.util"), "fmt").as_deref(), Some("pkg.util.fmt"));
    assert_eq!(target(Some("pkg.util.deep"), "inner").as_deref(), Some("pkg.util.deep.inner"));
    // `(import pkg [helpers])` の helpers.go と `(import m [Class])` の Class.open は同じ形
    assert_eq!(target(Some("helpers"), "go").as_deref(), Some("pkg.helpers.go"));
    assert_eq!(target(Some("MemoryStore"), "open").as_deref(), Some("doeff_records.memory.MemoryStore.open"));
    // 相対 import は書いた file の package を基準に直す
    assert_eq!(target(None, "near-fn").as_deref(), Some("pkg.sub.sibling.near_fn"));
    assert_eq!(target(None, "far-fn").as_deref(), Some("pkg.parent_mod.far_fn"));
}

#[test]
fn unresolvable_calls_have_no_target() {
    let file = index_at("pkg/sub/sample.hy", SAMPLE);
    assert_eq!(call(&file, None, "f").target, None, "引数の関数");
    assert_eq!(call(&file, None, "print").target, None, "組み込み");
    assert_eq!(call(&file, Some("pkg"), "util").target, None, "`(import pkg.util)` の下の `(pkg.util)` は module そのもの");
    // effect 節の中の呼び出しの PutRow は同じ file の effect 節ではない(入れ物の無い top level の定義だけを引く)
    assert_eq!(call(&file, None, "PutRow").target, None);
}

#[test]
fn require_is_not_a_call_target_and_module_itself_is_null() {
    let file = index_at(
        "pkg/m.hy",
        "(require doeff-hy.macros [my-macro])\n(import json)\n(defn g [] (my-macro 1) (json 2) (json.dumps 3))\n",
    );
    assert_eq!(call(&file, None, "my-macro").target, None, "require した macro は呼び先の定義にしない");
    assert_eq!(call(&file, None, "json").target, None, "module そのものは呼び先の定義ではない");
    assert_eq!(call(&file, Some("json"), "dumps").target.as_deref(), Some("json.dumps"), "Python の関数も完全修飾名で出す");
}

#[test]
fn target_of_a_caller_matches_qualified_name_across_files() {
    // 呼び手の file と呼び先の file を別々に索引しても(= `--stdin` の 1 file の実行でも)文字列が一致する
    let callee = index_at("pkg/screen/react.hy", "(defk view-body [x] x)\n(defclass Panel [] (defn draw [self] 1))\n");
    let caller = index_at(
        "tests/test_react.hy",
        "(import pkg.screen.react [view-body Panel])\n(import pkg.screen [react :as r])\n\
         (deftest renders (view-body 1) (Panel.draw p) (r.view-body 2))\n",
    );
    let body = qualified(&callee, "view-body");
    let draw = qualified(&callee, "draw");
    let targets: Vec<Option<&str>> = caller.calls.iter().map(|c| c.target.as_deref()).collect();
    assert_eq!(targets.iter().filter(|t| **t == Some(body)).count(), 2, "{targets:?}");
    assert!(targets.contains(&Some(draw)), "{targets:?}");
    // 呼び手は deftest の定義(caller の添字)
    let from = call(&caller, None, "view-body").caller.expect("caller");
    assert_eq!(caller.definitions[from].name, "renders");
}
