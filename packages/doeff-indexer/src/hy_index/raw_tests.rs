//! 生の副作用の証拠のテスト — Hy の source を索引してから証拠まで通す(doeff-runner の raw.test.ts の例を移したもの)。

use std::path::Path;

use super::model::{HyFileIndex, RawStrength};
use super::raw::{annotate, matches_pattern};
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

/// 局所の束縛の名が method の名(`stat`・`read-text`)と同じでも証拠にしない反例(agora-redesign #798 — agora-controllers の
/// daily_verify_translate.hy の kind-at で `(<- stat (StatPath …))` の `stat` が `.stat` の弱い証拠になっていた)。method の名は
/// `(.m x)` の頭・`x.m` の属性・`(. obj m)` / `(. obj (m …))` の属性にある時だけ method の証拠。
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

/// 3 つの file を索引し、目録(既定か、追加つき)で全体の証拠を集める。
fn judged(extra: Option<serde_json::Value>) -> Vec<HyFileIndex> {
    let root = Path::new("/r");
    let mut files = vec![
        index_source(root, Path::new("/r/pkg/io_handlers.hy"), IO),
        index_source(root, Path::new("/r/pkg/plain_open.hy"), PLAIN_OPEN),
        index_source(root, Path::new("/r/pkg/clock.hy"), CLOCK),
        index_source(root, Path::new("/r/pkg/members.hy"), MEMBERS),
    ];
    let bundled = RawCatalog::bundled().expect("同梱の目録が読める");
    let catalog = match extra {
        Some(value) => bundled.with_extra(&value).0,
        None => bundled,
    };
    annotate(&mut files, &catalog, true);
    files
}

/// 名前の定義を引く。
fn def<'a>(files: &'a [HyFileIndex], name: &str) -> &'a super::model::Definition {
    files.iter().flat_map(|f| &f.definitions).find(|d| d.name == name).unwrap_or_else(|| panic!("{} が無い", name))
}

/// 直接の証拠を「分類 名前(弱いなら ?)」の列にする。
fn direct(files: &[HyFileIndex], name: &str) -> Vec<String> {
    def(files, name)
        .raw
        .direct
        .iter()
        .map(|e| format!("{} {}{}", e.category.as_str(), e.name, if e.strength == RawStrength::Weak { "?" } else { "" }))
        .collect()
}

/// 経由の証拠を「経路 分類 名前」の列にする。
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
    assert!(direct(&files, "uses-open-var").is_empty(), "局所の変数 open は数えない");
}

#[test]
fn local_binding_names_are_not_method_evidence() {
    let files = judged(None);
    assert!(direct(&files, "kind-at").is_empty(), "局所の束縛 stat(<- の名・match の主語・stat.kind の頭)は .stat の証拠でない");
    assert!(
        direct(&files, "local-names").is_empty(),
        "引数・val・var・setv・for・let の名(read-text・iterdir・glob・rglob・stat・unlink)は method の証拠でない"
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
    assert_eq!(via(&files, "d1"), vec!["d2>d3>d4>d5 random random.random"], "d6 の uuid は 5 段目なので届かない");
}

#[test]
fn via_follows_calls_through_aliased_imports_across_files() {
    // 呼び出しの名(backtest-main)と定義の名(main)が違う別名の import も辿る(版 4 で経由の解決を qualify.rs の
    // 完全修飾名の引きへ一本化する前は、呼び出しの名が索引のどこにも定義されていないとして捨てていた)
    let root = Path::new("/r");
    let mut files = vec![
        index_source(root, Path::new("/r/pkg/entry.hy"), "(import time)\n(defn main [] (time.sleep 1))\n"),
        index_source(
            root,
            Path::new("/r/tests/test_entry.hy"),
            "(import pkg.entry [main :as backtest-main])\n(deftest runs-the-entry (backtest-main))\n",
        ),
    ];
    annotate(&mut files, &RawCatalog::bundled().expect("目録"), true);
    assert_eq!(via(&files, "runs-the-entry"), vec!["main time time.sleep"]);
}

#[test]
fn partial_runs_do_not_compute_via() {
    let root = Path::new("/r");
    let mut files = vec![index_source(root, Path::new("/r/pkg/io_handlers.hy"), IO)];
    annotate(&mut files, &RawCatalog::bundled().expect("目録"), false);
    assert!(def(&files, "via-handler").raw.via.is_empty());
    assert_eq!(direct(&files, "Fetch"), vec!["http httpx.post"]);
}

#[test]
fn catalog_extra_adds_names_and_reports_unknown_categories() {
    let extra = serde_json::json!({ "http": ["my_http_lib"], "time": [".tick"], "bogus": ["x"], "env": "os.getcwd" });
    let (_, problems) = RawCatalog::bundled().expect("目録").with_extra(&extra);
    assert_eq!(problems.len(), 2);
    assert!(problems.iter().any(|p| p.contains("\"bogus\" は知らない")));
    assert!(problems.iter().any(|p| p.contains("rawSideEffects.env が配列でない")));
    let before = judged(None);
    assert!(direct(&before, "custom-program").is_empty());
    let after = judged(Some(serde_json::json!({ "http": ["my_http_lib"], "time": [".tick"] })));
    assert_eq!(direct(&after, "custom-program"), vec!["http my_http_lib.get", "time .tick?"]);
}
