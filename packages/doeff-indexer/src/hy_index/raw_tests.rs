//! Tests for raw side-effect evidence -- index Hy source and run it through to the evidence (ported from the examples in doeff-runner's raw.test.ts).

use std::collections::BTreeSet;
use std::path::Path;

use super::model::{HyFileIndex, RawStrength};
use super::raw::{annotate, matches_pattern, ViaTrace};
use super::raw_catalog::RawCatalog;
use super::index_source;

const IO: &str = r#"(import httpx)
(import asyncio [sleep])
(import time)
(import pathlib [Path])
(import os)
(import subprocess :as sp)
(import datetime [datetime])
(import random)
(import uuid)
(import my_http_lib)
(require doeff-hy.macros [defhandler defk])

(defhandler http-handler []
  (Fetch [url]
    (try (resume (httpx.post url))
      (except [httpx.ReadTimeout] (resume None)))))

(defhandler clock-handler []
  (Now []
    (resume #((time.monotonic) (datetime.now)))))

(defhandler file-handler []
  (ReadIt [p]
    (resume #((.read-text p) (open p)))))

(defn slow-helper [] (sleep 1))

(defhandler via-handler []
  (Tick [] (slow-helper) (resume None)))

(defn loop-a [] (loop-b))
(defn loop-b [] (loop-a) (sp.run ["ls"]))

(defk bad-program [] (os.environ.get "X"))
(defk performs-external [] (<- (Fetch "u")))
(defk performs-domain [] (<- (Store 1 2)))
(defk custom-program [c] (my_http_lib.get) (.tick c))

(defn d1 [] (d2))
(defn d2 [] (d3))
(defn d3 [] (d4))
(defn d4 [] (d5))
(defn d5 [] (d6) (random.random))
(defn d6 [] (uuid.uuid4))

(defhandler memory-handler [rows]
  (Store [k v] (resume (assoc rows k v))))
"#;

const PLAIN_OPEN: &str = r#"(defk uses-open-var []
  (setv open 1)
  open)
"#;

const CLOCK: &str = r#"(import time)
(import doeff_time [GetTime])

(defhandler sync-time []
  (GetTime [] (resume (time.time))))

(defk reads-clock [] (<- (GetTime)))
"#;

/// Counterexample: even when a local binding's name equals a method name (`stat`, `read-text`), it is not evidence (in agora-controllers'
/// daily_verify_translate.hy kind-at, the `stat` of `(<- stat (StatPath ...))` was treated as weak evidence of `.stat`). A method name is
/// method evidence only when it is at the head of `(.m x)`, the attribute of `x.m`, or the attribute of `(. obj m)` / `(. obj (m ...))`.
const MEMBERS: &str = r#"(import pathlib [PurePosixPath])
(import doeff_core_effects.file_effects [StatPath])
(require doeff-hy.macros [defk <- val var])

(defk kind-at [path]
  (<- stat (StatPath (str path)))
  (match stat
    _ stat.kind))

(defk local-names [read-text]
  (val iterdir 1)
  (var glob 2)
  (setv rglob 3)
  (for [stat [iterdir glob rglob]] stat)
  (let [unlink 4] unlink)
  read-text)

(defk method-head [path] (.stat path))

(defk attribute-of-local [path] (path.stat))

(defk attribute-access [path] (. path (stat)))

(defk bare-attribute-access [path] (. path stat))
"#;

/// Index three files and gather overall evidence with the catalog (default, or with additions).
fn judged(extra: Option<serde_json::Value>) -> Vec<HyFileIndex> {
    let root = Path::new("/r");
    let mut files = vec![
        index_source(root, Path::new("/r/pkg/io_handlers.hy"), IO),
        index_source(root, Path::new("/r/pkg/plain_open.hy"), PLAIN_OPEN),
        index_source(root, Path::new("/r/pkg/clock.hy"), CLOCK),
        index_source(root, Path::new("/r/pkg/members.hy"), MEMBERS),
    ];
    let bundled = RawCatalog::bundled().expect("the bundled catalog can be read");
    let catalog = match extra {
        Some(value) => bundled.with_extra(&value).0,
        None => bundled,
    };
    annotate(&mut files, &catalog, ViaTrace::Through { stops: &BTreeSet::new() });
    files
}

/// Look up a definition by name.
fn def<'a>(files: &'a [HyFileIndex], name: &str) -> &'a super::model::Definition {
    files.iter().flat_map(|f| &f.definitions).find(|d| d.name == name).unwrap_or_else(|| panic!("{} not found", name))
}

/// Turn direct evidence into a list of "classification name (? if weak)".
fn direct(files: &[HyFileIndex], name: &str) -> Vec<String> {
    def(files, name)
        .raw
        .direct
        .iter()
        .map(|e| format!("{} {}{}", e.category.as_str(), e.name, if e.strength == RawStrength::Weak { "?" } else { "" }))
        .collect()
}

/// Turn via evidence into a list of "path classification name".
fn via(files: &[HyFileIndex], name: &str) -> Vec<String> {
    def(files, name)
        .raw
        .via
        .iter()
        .map(|v| {
            let path: Vec<&str> = v.through.iter().map(|s| s.name.as_str()).collect();
            format!("{} {} {}", path.join(">"), v.evidence.category.as_str(), v.evidence.name)
        })
        .collect()
}

#[test]
fn bundled_catalog_is_readable_and_patterns_match_on_segment_boundaries() {
    assert!(RawCatalog::bundled().is_ok());
    assert!(matches_pattern("httpx.post", "httpx"));
    assert!(!matches_pattern("httpx_extra.post", "httpx"));
    assert!(matches_pattern("os.execv", "os.exec*"));
    assert!(!matches_pattern("time.time_ns", "time.time"));
    assert!(matches_pattern("os.environ.get", "os.environ"));
}

/// #1564: a generation that takes a seed and its instance are not external I/O. Classified the same across a cache round trip.
#[test]
fn seeded_random_is_local_including_variable_seed_and_instance_alias() {
    let cases = [
        "(import random)\n(defk sample [] (val rng (random.Random 7)) (rng.random))",
        "(import random :as r)\n(defk sample [seed] (val rng (r.Random seed)) (.randint rng 0 9))",
        "(import random [Random :as R])\n(defk sample [seed] (setv rng (R seed) alias rng) (alias.choice [1 2]))",
        "(import random)\n(defk sample [seed] (val rng (random.Random :x seed)) (. rng (random)))",
        "(defk sample [] (import random) (val rng (random.Random 619)) (.shuffle rng [1 2]))",
        "(import random)\n(defk sample [rng] {:pre [(: rng random.Random)]} (.random rng))",
        "(import random [Random])\n(defk sample [#^ Random rng] (.random rng))",
    ];
    for source in cases {
        let file = index_source(Path::new("/r"), Path::new("/r/sample.hy"), source);
        let encoded = bincode::serialize(&super::CachedHyFile::of(file)).unwrap();
        let cached: super::CachedHyFile = bincode::deserialize(&encoded).unwrap();
        let mut files = vec![cached.into_file().unwrap()];
        annotate(&mut files, &RawCatalog::bundled().unwrap(), ViaTrace::Through { stops: &BTreeSet::new() });
        assert!(direct(&files, "sample").is_empty(), "{source}: {:?}", direct(&files, "sample"));
    }
}

#[test]
fn entropy_random_stays_external_including_missing_or_none_seed() {
    let calls = [
        "(random.Random)", "(random.Random None)", "(random.Random :x None)",
        "(random.random)", "(random.randint 0 9)", "(uuid.uuid4)", "(secrets.token-hex 8)",
        "(random.SystemRandom 7)", "(random.Random (time.time))",
        "(random.Random #* args)", "(random.Random #** kwargs)",
    ];
    for call in calls {
        let source = format!("(import random uuid secrets time)\n(defk sample [] {call})");
        let mut files = vec![index_source(Path::new("/r"), Path::new("/r/sample.hy"), &source)];
        annotate(&mut files, &RawCatalog::bundled().unwrap(), ViaTrace::Through { stops: &BTreeSet::new() });
        assert!(!direct(&files, "sample").is_empty(), "external I/O of {call} disappeared");
    }
}

#[test]
fn qualified_call_counts_and_exception_type_does_not() {
    let files = judged(None);
    assert_eq!(direct(&files, "Fetch"), vec!["http httpx.post"]);
}

#[test]
fn names_are_expanded_through_imports() {
    let files = judged(None);
    assert_eq!(direct(&files, "Now"), vec!["time time.monotonic", "time datetime.datetime.now"]);
    assert_eq!(direct(&files, "slow-helper"), vec!["async asyncio.sleep"]);
    assert_eq!(direct(&files, "loop-b"), vec!["process subprocess.run"]);
    assert_eq!(direct(&files, "bad-program"), vec!["env os.environ.get"]);
}

#[test]
fn builtin_open_counts_only_at_call_head_and_pathlib_methods_are_weak() {
    let files = judged(None);
    assert_eq!(direct(&files, "ReadIt"), vec!["file .read_text?", "file open"]);
    assert!(direct(&files, "uses-open-var").is_empty(), "the local variable open is not counted");
}

#[test]
fn local_binding_names_are_not_method_evidence() {
    let files = judged(None);
    assert!(direct(&files, "kind-at").is_empty(), "a local binding stat (the name of <-, a match subject, the head of stat.kind) is not evidence of .stat");
    assert!(
        direct(&files, "local-names").is_empty(),
        "names bound by arguments, val, var, setv, for, let (read-text, iterdir, glob, rglob, stat, unlink) are not method evidence"
    );
}

#[test]
fn method_heads_and_attributes_stay_weak_method_evidence() {
    let files = judged(None);
    assert_eq!(direct(&files, "method-head"), vec!["file .stat?"]);
    assert_eq!(direct(&files, "attribute-of-local"), vec!["file .stat?"]);
    assert_eq!(direct(&files, "attribute-access"), vec!["file .stat?"]);
    assert_eq!(direct(&files, "bare-attribute-access"), vec!["file .stat?"]);
}

#[test]
fn handlers_include_their_clauses_evidence() {
    let files = judged(None);
    assert_eq!(direct(&files, "http-handler"), vec!["http httpx.post"]);
    assert!(direct(&files, "memory-handler").is_empty());
}

#[test]
fn via_follows_calls_stops_cycles_and_depth_four() {
    let files = judged(None);
    assert_eq!(via(&files, "via-handler"), vec!["slow-helper async asyncio.sleep"]);
    assert_eq!(via(&files, "loop-a"), vec!["loop-b process subprocess.run"]);
    assert_eq!(via(&files, "d1"), vec!["d2>d3>d4>d5 random random.random"], "the uuid in d6 is at the 5th level so it is not reached");
}

#[test]
fn via_follows_calls_through_aliased_imports_across_files() {
    // Aliased imports where the call's name (backtest-main) differs from the definition's name (main) are also followed (before version 4 unified via-resolution into qualify.rs's
    // fully qualified name lookup, they were dropped as the call's name not being defined anywhere in the index)
    let root = Path::new("/r");
    let mut files = vec![
        index_source(root, Path::new("/r/pkg/entry.hy"), "(import time)\n(defn main [] (time.sleep 1))\n"),
        index_source(
            root,
            Path::new("/r/tests/test_entry.hy"),
            "(import pkg.entry [main :as backtest-main])\n(deftest runs-the-entry (backtest-main))\n",
        ),
    ];
    annotate(&mut files, &RawCatalog::bundled().expect("catalog"), ViaTrace::Through { stops: &BTreeSet::new() });
    assert_eq!(via(&files, "runs-the-entry"), vec!["main time time.sleep"]);
}

#[test]
fn partial_runs_do_not_compute_via() {
    let root = Path::new("/r");
    let mut files = vec![index_source(root, Path::new("/r/pkg/io_handlers.hy"), IO)];
    annotate(&mut files, &RawCatalog::bundled().expect("catalog"), ViaTrace::Skip);
    assert!(def(&files, "via-handler").raw.via.is_empty());
    assert_eq!(direct(&files, "Fetch"), vec!["http httpx.post"]);
}

#[test]
fn catalog_extra_adds_names_and_reports_unknown_categories() {
    let extra = serde_json::json!({ "http": ["my_http_lib"], "time": [".tick"], "bogus": ["x"], "env": "os.getcwd" });
    let (_, problems) = RawCatalog::bundled().expect("catalog").with_extra(&extra);
    assert_eq!(problems.len(), 2);
        assert!(problems.iter().any(|p| p.contains("\"bogus\" is unknown")));
        assert!(problems.iter().any(|p| p.contains("rawSideEffects.env is not an array")));
    let before = judged(None);
    assert!(direct(&before, "custom-program").is_empty());
    let after = judged(Some(serde_json::json!({ "http": ["my_http_lib"], "time": [".tick"] })));
    assert_eq!(direct(&after, "custom-program"), vec!["http my_http_lib.get", "time .tick?"]);
}

/// With the catalog's argument cap (max_args), do not count methods of the same name on a different type (in doeff-cluster's process_host.hy,
/// the string's `(.replace name "/" "_")` was weak evidence of Path.replace inside a file that imports pathlib). Path.replace takes
/// 1 argument (target); str.replace takes 2 or more. A reference not at the head of a call has unknown arguments, so it is counted as before. The same across a cache round trip.
#[test]
fn method_calls_over_the_catalog_argument_limit_are_not_evidence() {
    let source = r#"(import pathlib [Path])
(require doeff-hy.macros [defk])
(defk string-replace [name] (.replace name "/" "_"))
(defk string-replace-dotted [s] (s.replace "a" "b"))
(defk path-replace [p t] (.replace p t))
(defk path-replace-dotted [p t] (p.replace t))
(defk path-replace-keyword [p t] (.replace p :target t))
(defk path-exists [p] (.exists p))
"#;
    let fresh = index_source(Path::new("/r"), Path::new("/r/host.hy"), source);
    let encoded = bincode::serialize(&super::CachedHyFile::of(fresh.clone())).unwrap();
    let cached: super::CachedHyFile = bincode::deserialize(&encoded).unwrap();
    for file in [fresh, cached.into_file().unwrap()] {
        let mut files = vec![file];
        annotate(&mut files, &RawCatalog::bundled().unwrap(), ViaTrace::Through { stops: &BTreeSet::new() });
        assert!(direct(&files, "string-replace").is_empty(), "{:?}", direct(&files, "string-replace"));
        assert!(direct(&files, "string-replace-dotted").is_empty(), "{:?}", direct(&files, "string-replace-dotted"));
        assert_eq!(direct(&files, "path-replace"), vec!["file .replace?"]);
        assert_eq!(direct(&files, "path-replace-dotted"), vec!["file .replace?"]);
        assert_eq!(direct(&files, "path-replace-keyword"), vec!["file .replace?"], "a keyword and value pair is 1 argument");
        assert_eq!(direct(&files, "path-exists"), vec!["file .exists?"], "a method with no cap is as before");
    }
}
