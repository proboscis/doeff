//! Tests for fully qualified names (version 4) -- index Hy source and check a definition's `qualified_name` and a call's `target`.

use std::path::Path;

use super::analyze;
use super::index_source;
use super::model::{Call, HyFileIndex};
use super::qualify;

/// Index the source as `/r/<path>` (root is `/r`).
fn index_at(path: &str, source: &str) -> HyFileIndex {
    index_source(Path::new("/r"), &Path::new("/r").join(path), source)
}

/// Look up one call by the call head's name (last segment) and its qualifier.
fn call<'a>(file: &'a HyFileIndex, qualifier: Option<&str>, callee: &str) -> &'a Call {
    file.calls
        .iter()
        .find(|c| c.callee == callee && c.qualifier.as_deref() == qualifier)
        .unwrap_or_else(|| panic!("call {:?}.{callee} not found", qualifier))
}

/// Look up the fully qualified name of a definition by name.
fn qualified<'a>(file: &'a HyFileIndex, name: &str) -> &'a str {
    &file.definitions.iter().find(|d| d.name == name).unwrap_or_else(|| panic!("definition {name} not found")).qualified_name
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
    assert_eq!(qualified(&file, "put"), "pkg.sub.sample.Store.put", "a method has its container class's name in between");
    assert_eq!(qualified(&file, "PutRow"), "pkg.sub.sample.store_handler.PutRow", "an effect clause has the handler's name in between");
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
    // Definitions in the same file -- unqualified is top level, `q.name` is inside container q
    assert_eq!(target(None, "local-fn").as_deref(), Some("pkg.sub.sample.local_fn"));
    assert_eq!(target(Some("Store"), "put").as_deref(), Some("pkg.sub.sample.Store.put"));
    assert_eq!(target(None, "Store").as_deref(), Some("pkg.sub.sample.Store"));
    // Names and aliases from import (module spellings are mangled)
    assert_eq!(target(None, "MemoryStore").as_deref(), Some("doeff_records.memory.MemoryStore"));
    assert_eq!(target(None, "put").as_deref(), Some("doeff_records.memory.put_row"), "an alias is mapped back to the original name");
    // dotted module import
    assert_eq!(target(Some("pkg.util"), "fmt").as_deref(), Some("pkg.util.fmt"));
    assert_eq!(target(Some("pkg.util.deep"), "inner").as_deref(), Some("pkg.util.deep.inner"));
    // `helpers.go` from `(import pkg [helpers])` and `Class.open` from `(import m [Class])` have the same shape
    assert_eq!(target(Some("helpers"), "go").as_deref(), Some("pkg.helpers.go"));
    assert_eq!(target(Some("MemoryStore"), "open").as_deref(), Some("doeff_records.memory.MemoryStore.open"));
    // Relative imports are resolved against the package of the file where they are written
    assert_eq!(target(None, "near-fn").as_deref(), Some("pkg.sub.sibling.near_fn"));
    assert_eq!(target(None, "far-fn").as_deref(), Some("pkg.parent_mod.far_fn"));
}

#[test]
fn unresolvable_calls_have_no_target() {
    let file = index_at("pkg/sub/sample.hy", SAMPLE);
    assert_eq!(call(&file, None, "f").target, None, "function passed as an argument");
    assert_eq!(call(&file, None, "print").target, None, "builtin");
    assert_eq!(call(&file, Some("pkg"), "util").target, None, "`(pkg.util)` under `(import pkg.util)` is the module itself");
    // PutRow in a call inside an effect clause is not an effect clause in the same file (only top-level definitions with no container are looked up)
    assert_eq!(call(&file, None, "PutRow").target, None);
}

#[test]
fn require_is_not_a_call_target_and_module_itself_is_null() {
    let file = index_at(
        "pkg/m.hy",
        "(require doeff-hy.macros [my-macro])\n(import json)\n(defn g [] (my-macro 1) (json 2) (json.dumps 3))\n",
    );
    assert_eq!(call(&file, None, "my-macro").target, None, "a required macro is not treated as a callee definition");
    assert_eq!(call(&file, None, "json").target, None, "the module itself is not a callee definition");
    assert_eq!(call(&file, Some("json"), "dumps").target.as_deref(), Some("json.dumps"), "Python functions are also emitted as fully qualified names");
}

#[test]
fn target_of_a_caller_matches_qualified_name_across_files() {
    // The strings match even when the caller's file and the callee's file are indexed separately (= even in a single-file `--stdin` run)
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
    // The caller is a deftest definition (index of caller)
    let from = call(&caller, None, "view-body").caller.expect("caller");
    assert_eq!(caller.definitions[from].name, "renders");
}

/// The fully qualified name of a handler named as a value (not a call -- `(with-handlers [os-file-handler] ...)`), by the same resolution as calls,
/// is filled into the reference's `target` (to count handlers that touch the outside world being named).
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
        file.references.iter().find(|r| r.name == name).unwrap_or_else(|| panic!("reference {name} not found")).target.clone()
    };
    assert_eq!(target_of("files").as_deref(), Some("doeff_core_effects.os_file.os_file_handler"));
    assert_eq!(target_of("with-cluster-handlers").as_deref(), Some("doeff_cluster.cluster_foundation.with_cluster_handlers"));
    assert_eq!(target_of("local"), None, "a target was attached to a name that cannot be resolved");
}

/// Promise on resolution order (do not change when rewriting): among imports that bind the same name, the one written first wins; if the first matching import is the module itself, it is unresolved.
/// A top-level definition in the same file wins over an import.
#[test]
fn the_first_import_that_binds_a_name_decides_and_a_local_definition_beats_an_import() {
    let file = index_at(
        "pkg/order.hy",
        "(import a.m [x])\n(import b.m [x])\n(import foo)\n(import c.m [foo])\n(import d [y])\n(defn y [] 1)\n(defn g [] (x 1) (foo 2) (y 3))\n",
    );
    assert_eq!(call(&file, None, "x").target.as_deref(), Some("a.m.x"), "the import written first");
    assert_eq!(call(&file, None, "foo").target, None, "the first matching import is the module itself (the later `[foo]` is not looked at)");
    assert_eq!(call(&file, None, "y").target.as_deref(), Some("pkg.order.y"), "a definition in the same file wins over an import");
}

/// Qualified calls also use the first match in import order -- a module import also matches by qualifier prefix (`a.b.c.d` under `a.b`).
#[test]
fn a_qualified_call_uses_the_first_import_in_order_that_fits() {
    let file = index_at("pkg/q.hy", "(import m)\n(import n [m])\n(import a.b)\n(defn g [] (m.f 1) (a.b.c.d 2))\n");
    assert_eq!(call(&file, Some("m"), "f").target.as_deref(), Some("m.f"), "the module import comes first");
    assert_eq!(call(&file, Some("a.b.c"), "d").target.as_deref(), Some("a.b.c.d"), "prefix match on the qualifier");
    let swapped = index_at("pkg/q.hy", "(import n [m])\n(import m)\n(defn g [] (m.f 1))\n");
    assert_eq!(call(&swapped, Some("m"), "f").target.as_deref(), Some("n.m.f"), "the `[m]` import comes first");
}

/// The name-resolution work is proportional to the file size -- it must not become "calls x (definitions + imports)" (`index_source` took 4.3 seconds on a file of 10,000 lines and 50,000 calls:
/// it went over all definitions and imports for every call, and allocated a mangle for every import).
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
    assert!(calls >= ROUNDS * NAMES * 4, "too few calls: {calls}");
    let before = analyze::mangle_calls();
    qualify::link(&mut file);
    let spent = analyze::mangle_calls() - before;
    let size = file.imports.len() + file.definitions.len() + NAMES * 4;
    assert!(
        spent <= 8 * size,
        "link called mangle {spent} times (exceeded 8x the file size {size} = {} -- it is going over all definitions and imports for each of the {calls} calls)",
        8 * size
    );
    // The answers do not change: calls with the same name always get the same target.
    let targets = |name: &str, qualifier: Option<&str>| -> Vec<Option<String>> {
        file.calls.iter().filter(|c| c.callee == name && c.qualifier.as_deref() == qualifier).map(|c| c.target.clone()).collect()
    };
    assert_eq!(targets("local-3", None), vec![Some("pkg.many.local_3".to_string()); ROUNDS]);
    assert_eq!(targets("fn3", None), vec![Some("pkg3.mod3.fn3".to_string()); ROUNDS]);
    assert_eq!(targets("go", Some("util3")), vec![Some("util3.go".to_string()); ROUNDS]);
    assert_eq!(targets("unknown-3", None), vec![None; ROUNDS]);
}
