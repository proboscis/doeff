//! 完全修飾名(版 4)のテスト — 定義の `qualified_name` と呼び出しの `target` を、Hy の source を索引して確かめる。

use std::path::Path;

use super::analyze;
use super::index_source;
use super::model::{Call, HyFileIndex};
use super::qualify;

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

/// 値として名指した handler(呼び出しにならない — `(with-handlers [os-file-handler] …)`)の完全修飾名を、呼び出しと同じ解決で
/// 参照の `target` に埋める(外の世界に触れる handler の名指しを数えるため)。
#[test]
fn references_resolve_like_calls() {
    let source = r#"(import doeff-core-effects.os-file [os-file-handler :as files])
(import doeff-cluster.cluster-foundation)
(require doeff-hy.macros [defk <-])
(defk outer [body]
  (with-handlers [files doeff-cluster.cluster-foundation.with-cluster-handlers local] body))
"#;
    let file = index_at("app/foundation/host.hy", source);
    let target_of = |name: &str| {
        file.references.iter().find(|r| r.name == name).unwrap_or_else(|| panic!("参照 {name} が無い")).target.clone()
    };
    assert_eq!(target_of("files").as_deref(), Some("doeff_core_effects.os_file.os_file_handler"));
    assert_eq!(target_of("with-cluster-handlers").as_deref(), Some("doeff_cluster.cluster_foundation.with_cluster_handlers"));
    assert_eq!(target_of("local"), None, "解決できない名に target を付けた");
}

/// 解決の順の約束(書き換えても変えない): 同じ名を束ねる import は先に書いた方が勝ち、最初に当たる import が module そのものなら未解決。
/// 同じ file の top level の定義は import に勝つ。
#[test]
fn the_first_import_that_binds_a_name_decides_and_a_local_definition_beats_an_import() {
    let file = index_at(
        "pkg/order.hy",
        "(import a.m [x])\n(import b.m [x])\n(import foo)\n(import c.m [foo])\n(import d [y])\n(defn y [] 1)\n(defn g [] (x 1) (foo 2) (y 3))\n",
    );
    assert_eq!(call(&file, None, "x").target.as_deref(), Some("a.m.x"), "先に書いた import");
    assert_eq!(call(&file, None, "foo").target, None, "最初に当たる import が module そのもの(後の `[foo]` は見ない)");
    assert_eq!(call(&file, None, "y").target.as_deref(), Some("pkg.order.y"), "同じ file の定義が import に勝つ");
}

/// 修飾つきの呼びも、import を書いた順で最初に合う物を使う — module の import は修飾の前方一致(`a.b` の下の `a.b.c.d`)でも合う。
#[test]
fn a_qualified_call_uses_the_first_import_in_order_that_fits() {
    let file = index_at("pkg/q.hy", "(import m)\n(import n [m])\n(import a.b)\n(defn g [] (m.f 1) (a.b.c.d 2))\n");
    assert_eq!(call(&file, Some("m"), "f").target.as_deref(), Some("m.f"), "module の import が先");
    assert_eq!(call(&file, Some("a.b.c"), "d").target.as_deref(), Some("a.b.c.d"), "修飾の前方一致");
    let swapped = index_at("pkg/q.hy", "(import n [m])\n(import m)\n(defn g [] (m.f 1))\n");
    assert_eq!(call(&swapped, Some("m"), "f").target.as_deref(), Some("n.m.f"), "`[m]` の import が先");
}

/// 名前の解決の仕事は file の大きさに比例する — 「呼びの数 × (定義 + import の数)」にならない(10,000 行・呼び 5 万の file で `index_source` が 4.3 秒かかっていた:
/// 呼びごとに定義と import の全部を回し、import ごとに mangle の確保をしていた)。
#[test]
fn linking_costs_the_size_of_the_file_not_calls_times_imports() {
    const IMPORTS: usize = 40;
    const NAMES: usize = 20;
    const ROUNDS: usize = 20;
    let mut source = String::new();
    for i in 0..IMPORTS {
        source.push_str(&format!("(import pkg{i}.mod{i} [fn{i}])\n(import util{i})\n"));
    }
    for i in 0..NAMES {
        source.push_str(&format!("(defn local-{i} [x] x)\n"));
    }
    source.push_str("(defn run []\n");
    for round in 0..ROUNDS {
        for i in 0..NAMES {
            source.push_str(&format!("  (unknown-{i} {round})\n  (local-{i} 1)\n  (util{i}.go {round})\n  (fn{i} 2)\n"));
        }
    }
    source.push_str(")\n");
    let mut file = index_at("pkg/many.hy", &source);
    let calls = file.calls.len();
    assert!(calls >= ROUNDS * NAMES * 4, "呼びが少なすぎる: {calls}");
    let before = analyze::mangle_calls();
    qualify::link(&mut file);
    let spent = analyze::mangle_calls() - before;
    let size = file.imports.len() + file.definitions.len() + NAMES * 4;
    assert!(
        spent <= 8 * size,
        "link が mangle を {spent} 回呼んだ(file の大きさ {size} の 8 倍 = {} を越えた — 呼び {calls} 個ごとに全部の定義と import を回している)",
        8 * size
    );
    // 答えは変わらない: 同じ名の呼びは、何回目も同じ target。
    let targets = |name: &str, qualifier: Option<&str>| -> Vec<Option<String>> {
        file.calls.iter().filter(|c| c.callee == name && c.qualifier.as_deref() == qualifier).map(|c| c.target.clone()).collect()
    };
    assert_eq!(targets("local-3", None), vec![Some("pkg.many.local_3".to_string()); ROUNDS]);
    assert_eq!(targets("fn3", None), vec![Some("pkg3.mod3.fn3".to_string()); ROUNDS]);
    assert_eq!(targets("go", Some("util3")), vec![Some("util3.go".to_string()); ROUNDS]);
    assert_eq!(targets("unknown-3", None), vec![None; ROUNDS]);
}
