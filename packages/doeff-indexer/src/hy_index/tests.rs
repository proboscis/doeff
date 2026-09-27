//! `hy-index` の解析の検 — 契約の kind・container・docstring・params・import・参照・UTF-16 の列・
//! 壊れた入力で落ちないことを、source の文字列から確かめる。

use std::path::Path;

use super::*;

/// source を `/r/pkg/sample.hy` として索引する。
fn index(source: &str) -> HyFileIndex {
    index_source(Path::new("/r"), Path::new("/r/pkg/sample.hy"), source)
}

/// 名前で定義を引く(無ければ検を落とす)。
fn def<'a>(file: &'a HyFileIndex, name: &str) -> &'a Definition {
    file.definitions
        .iter()
        .find(|definition| definition.name == name)
        .unwrap_or_else(|| panic!("定義 {name} が無い: {:?}", names(file)))
}

/// 定義の (名前, kind, container) の一覧。
fn names(file: &HyFileIndex) -> Vec<(String, &'static str, Option<String>)> {
    file.definitions
        .iter()
        .map(|definition| (definition.name.clone(), definition.kind.as_str(), definition.container.clone()))
        .collect()
}

/// 契約の位置(行・UTF-16 の列)から source の byte の位置を引く(照合のため)。
fn offset_of(source: &str, position: Position) -> usize {
    let line_start: usize = source.split_inclusive('\n').take(position.line as usize).map(str::len).sum();
    let mut units = 0u32;
    for (offset, c) in source[line_start..].char_indices() {
        if units >= position.character {
            return line_start + offset;
        }
        units += c.len_utf16() as u32;
    }
    source.len()
}

/// 契約の範囲が指す source の文字列を切り出す。
fn slice(source: &str, range: Range) -> &str {
    &source[offset_of(source, range.start)..offset_of(source, range.end)]
}

#[test]
fn every_kind_is_indexed() {
    let source = r#"(require doeff-hy.macros [defk deff defp defpp val var lazy session defhandler deftest])
(defn plain [a] a)
(defn/a fetch [url] url)
(defmacro my-macro [x] `(print ~x))
(defk step [state] {:pre [(: state dict)] :post [(: % int)]} state)
(deff pure-fn [x] {:pre [] :post []} x)
(defp my-program (step 1))
(defpp my-pretty (step 1))
(defclass Point [] (#^ int x) (defn norm [self] 0))
(defrecord Row #^ str key)
(defenum Phase PENDING (RUNNING "Running"))
(defhandler writes (PutRow [table key] (resume key)))
(deftest test-step (assert True))
(defadr ADR-X :title "t" :laws [(law law-one :statement "s")] :enforcement [(defsemgrep rule-one "id" [] [])])
(defpipeline daily [ohlc] (FetchOhlc))
(defworkflow flow (defphase Implement :stakes "normal"))
(defmcp-tool tool-one "desc" [q] q)
(deftype Alias int)
(defmain [#* args] (print args))
(setv top-var 1)
(val bound 2)
"#;
    let file = index(source);
    let kinds: Vec<&str> = file.definitions.iter().map(|definition| definition.kind.as_str()).collect();
    for kind in [
        "defn", "defn/a", "defmacro", "defk", "deff", "defp", "defpp", "defclass", "field", "method",
        "defrecord", "defenum", "enum-member", "defhandler", "effect-clause", "deftest", "defadr", "law",
        "defsemgrep", "defpipeline", "defworkflow", "defphase", "defmcp-tool", "deftype", "defmain", "variable",
    ] {
        assert!(kinds.contains(&kind), "kind {kind} が無い: {:?}", names(&file));
    }
    assert!(file.errors.is_empty(), "{:?}", file.errors);
    for definition in &file.definitions {
        assert_eq!(slice(source, definition.range), definition.name, "{} の range が名前を指さない", definition.name);
        let full = slice(source, definition.full_range);
        assert!(full.contains(&definition.name), "{} の full_range が名前を含まない", definition.name);
    }
    assert_eq!(def(&file, "top-var").mangled, "top_var");
    assert!(slice(source, def(&file, "plain").full_range).starts_with("(defn plain"));
}

#[test]
fn containers_of_nested_definitions() {
    let source = r#"(defclass [(dataclass :frozen True)] Run [Base]
  "A run."
  #^ int cycles
  (#^ (| str None) error None)
  (setv LIMIT 3)
  (defn total [self #^ int extra] (+ self.cycles extra)))
(defenum Phase PENDING (RUNNING "Running"))
(defhandler record-writes [store]
  "Records writes."
  (session var writes #())
  (session val client (Client))
  (lazy-var old-style 0)
  (PutRow [table key]
    (:= writes (+ writes #(table)))
    (resume key)))
"#;
    let file = index(source);
    let expect = |name: &str, kind: &str, container: Option<&str>| {
        let definition = def(&file, name);
        assert_eq!(definition.kind.as_str(), kind, "{name}");
        assert_eq!(definition.container.as_deref(), container, "{name}");
    };
    expect("Run", "defclass", None);
    expect("cycles", "field", Some("Run"));
    expect("error", "field", Some("Run"));
    expect("LIMIT", "field", Some("Run"));
    expect("total", "method", Some("Run"));
    expect("PENDING", "enum-member", Some("Phase"));
    expect("RUNNING", "enum-member", Some("Phase"));
    expect("record-writes", "defhandler", None);
    expect("writes", "variable", Some("record-writes"));
    expect("client", "variable", Some("record-writes"));
    expect("old-style", "variable", Some("record-writes"));
    expect("PutRow", "effect-clause", Some("record-writes"));
    assert_eq!(def(&file, "PutRow").params, vec!["table", "key"]);
    assert_eq!(def(&file, "total").params, vec!["self", "extra"]);
    assert_eq!(def(&file, "record-writes").params, vec!["store"]);
    assert_eq!(def(&file, "record-writes").docstring.as_deref(), Some("Records writes."));
    assert_eq!(def(&file, "Run").docstring.as_deref(), Some("A run."));
    // 関数の中の局所の束縛は入れない。
    assert!(!file.definitions.iter().any(|definition| definition.name == "table" && definition.kind.as_str() == "variable"));
}

#[test]
fn docstrings_including_after_contract_map() {
    let source = r#"(defk step [state]
  {:pre [(: state dict)] :post [(: % int)]}
  "One step.
   Second line."
  (<- now (GetTime))
  now)
(defn lone [] "not a docstring")
(defn with-doc [] "Doc \"quoted\"." 1)
(defhandler h "Handler doc." [x] (E [] (resume x)))
(defadr ADR-Y :title "Title of the ADR" :laws [(law l1 :statement "the law")])
(deftest t1 "Test doc." (assert True))
(defclass Empty [] #[[Bracket doc]])
"#;
    let file = index(source);
    assert_eq!(def(&file, "step").docstring.as_deref(), Some("One step.\nSecond line."));
    assert_eq!(def(&file, "lone").docstring, None);
    assert_eq!(def(&file, "with-doc").docstring.as_deref(), Some("Doc \"quoted\"."));
    assert_eq!(def(&file, "h").docstring.as_deref(), Some("Handler doc."));
    assert_eq!(def(&file, "h").params, vec!["x"]);
    assert_eq!(def(&file, "ADR-Y").docstring.as_deref(), Some("Title of the ADR"));
    assert_eq!(def(&file, "l1").docstring.as_deref(), Some("the law"));
    assert_eq!(def(&file, "l1").container.as_deref(), Some("ADR-Y"));
    assert_eq!(def(&file, "t1").docstring.as_deref(), Some("Test doc."));
    assert_eq!(def(&file, "Empty").docstring.as_deref(), Some("Bracket doc"));
}

#[test]
fn params_as_written() {
    let source = "(defn [staticmethod] #^ int f [a #^ str b [c 1] #* rest #** kw * d] a)\n\
                  (defmacro m [name #* body] name)\n\
                  (defclass [(dataclass)] #^ int Weird [])\n\
                  (defn :tp [T] g [#^ T x] x)\n";
    let file = index(source);
    assert_eq!(def(&file, "f").params, vec!["a", "b", "c", "rest", "kw", "d"]);
    assert_eq!(def(&file, "f").kind.as_str(), "defn");
    assert_eq!(def(&file, "m").params, vec!["name", "body"]);
    assert_eq!(def(&file, "g").params, vec!["x"]);
    assert!(def(&file, "Weird").params.is_empty());
}

/// import の照合の 1 行: (module, name, alias, is_require, range が指す文字列)。
type ImportRow = (String, Option<String>, Option<String>, bool, String);

#[test]
fn imports_with_alias_names_and_require() {
    let source = "(import os.path :as p)\n\
                  (import doeff [with_handlers EffectBase :as EB])\n\
                  (require doeff-hy.macros [defk val])\n\
                  (import os sys)\n\
                  (import helpers *)\n\
                  (require m :macros [mac] :readers [rd])\n\
                  (defn f [] (import json) (json.dumps 1))\n";
    let file = index(source);
    let summary: Vec<ImportRow> = file
        .imports
        .iter()
        .map(|import| {
            (import.module.clone(), import.name.clone(), import.alias.clone(), import.is_require, slice(source, import.range).to_string())
        })
        .collect();
    let row = |module: &str, name: Option<&str>, alias: Option<&str>, require: bool, text: &str| {
        (module.to_string(), name.map(str::to_string), alias.map(str::to_string), require, text.to_string())
    };
    assert_eq!(
        summary,
        vec![
            row("os.path", None, Some("p"), false, "p"),
            row("doeff", Some("with_handlers"), None, false, "with_handlers"),
            row("doeff", Some("EffectBase"), Some("EB"), false, "EB"),
            row("doeff-hy.macros", Some("defk"), None, true, "defk"),
            row("doeff-hy.macros", Some("val"), None, true, "val"),
            row("os", None, None, false, "os"),
            row("sys", None, None, false, "sys"),
            row("helpers", Some("*"), None, false, "*"),
            row("m", Some("mac"), None, true, "mac"),
            row("m", Some("rd"), None, true, "rd"),
            row("json", None, None, false, "json"),
        ]
    );
}

#[test]
fn references_have_qualifiers_and_skip_non_symbols() {
    let source = "(require doeff-hy.macros [val])\n\
                  ;; comment-name in a comment\n\
                  (defn f [obj]\n  (val total (obj.items.count :key True))\n  (.strip obj)\n  \
                  (print \"string-name\" 'quoted-name `(tmpl ~unquoted-name) f\"{fvalue} {(g h):>3}\" #_ discarded-name None)\n  (+ 1 total))\n";
    let file = index(source);
    let find = |name: &str| file.references.iter().filter(|reference| reference.name == name).collect::<Vec<_>>();
    let count = &find("count")[0];
    assert_eq!(count.qualifier.as_deref(), Some("obj.items"));
    assert_eq!(find("items")[0].qualifier.as_deref(), Some("obj"));
    assert!(find("obj").iter().all(|reference| reference.qualifier.is_none()));
    assert_eq!(find("strip")[0].qualifier, None);
    for present in ["print", "unquoted-name", "fvalue", "g", "h", "total", "f"] {
        assert!(!find(present).is_empty(), "参照 {present} が無い");
    }
    for absent in [
        "defn", "comment-name", "string-name", "quoted-name", "tmpl", "discarded-name", "None", "True", "+", "key",
        ":key", "_",
    ] {
        assert!(find(absent).is_empty(), "参照に {absent} が入っている");
    }
    for reference in &file.references {
        assert_eq!(slice(source, reference.range), reference.name);
    }
    assert_eq!(find("unquoted-name")[0].mangled, "unquoted_name");
    assert_eq!(find("val").len(), 1, "require の中の 1 件だけ(先頭の予約語の val は入れない)");
}

#[test]
fn required_word_macros_are_references_unless_required() {
    let file = index("(check x)\n");
    assert!(file.references.iter().any(|reference| reference.name == "check"));
    let file = index("(require doeff-hy.macros [check])\n(check x)\n");
    assert_eq!(file.references.iter().filter(|reference| reference.name == "check").count(), 1, "require の中の 1 件だけ");
}

#[test]
fn columns_count_utf16_code_units() {
    let source = ";; 日本語の註 😀\n(setv 名前 \"😀絵\") (defn 関数 [引数] (print 名前 引数))\n(setv 😀x 1)\n";
    let file = index(source);
    let name = def(&file, "名前");
    assert_eq!(name.range.start, Position { line: 1, character: 6 });
    assert_eq!(name.range.end, Position { line: 1, character: 8 });
    let function = def(&file, "関数");
    // `(setv 名前 "😀絵") (defn ` — 😀 は 2 単位。
    assert_eq!(function.range.start, Position { line: 1, character: 22 });
    assert_eq!(def(&file, "関数").params, vec!["引数"]);
    let emoji = def(&file, "😀x");
    assert_eq!(emoji.range.start, Position { line: 2, character: 6 });
    assert_eq!(emoji.range.end, Position { line: 2, character: 9 });
    for definition in &file.definitions {
        assert_eq!(slice(source, definition.range), definition.name);
    }
    let uses: Vec<_> = file.references.iter().filter(|reference| reference.name == "名前").collect();
    assert_eq!(uses.len(), 2);
    assert_eq!(uses[1].range.start, Position { line: 1, character: 37 });
}

#[test]
fn broken_sources_do_not_panic_and_report_errors() {
    for source in [
        "(defn f [x",
        "(setv x \"unterminated",
        "))) (foo",
        "#[[never closed",
        "(f #^",
        "f\"{(g",
        "#_",
        "(defn",
        "(defclass",
        "(defhandler h (E [",
        "(import",
        "(import m :as",
        "(require m [a :as",
        "\"\\",
        "\"日本\\",
        "(setv 名前 \"😀\\",
        "#",
        "#^ #^",
        "~@",
        "(defk f [x] {:pre",
        "(defn [] )",
        "(defadr A :laws [(law",
        "f\"{名前:}\"",
        "f\"{名}\"",
        "(defenum E (",
        "(session var)",
        "(lazy)",
        "(setv)",
    ] {
        let file = index(source);
        for definition in &file.definitions {
            let _ = slice(source, definition.range);
        }
    }
    let file = index("(defn ok [] 1)\n(defn broken [x]\n  (print x\n");
    assert_eq!(def(&file, "ok").kind.as_str(), "defn");
    assert_eq!(def(&file, "broken").params, vec!["x"]);
    assert_eq!(file.errors.len(), 2, "{:?}", file.errors);
    assert!(file.errors[0].contains("閉じていない"));
    let file = index("(foo))\n");
    assert_eq!(file.errors.len(), 1);
}

#[test]
fn module_names_follow_the_root() {
    let root = Path::new("/r");
    assert_eq!(module_name(root, Path::new("/r/controllers/agora_sim/classifier.hy")), "controllers.agora_sim.classifier");
    assert_eq!(module_name(root, Path::new("/r/controllers/agora_sim/__init__.hy")), "controllers.agora_sim");
    assert_eq!(module_name(root, Path::new("/r/top.hyk")), "top");
    assert_eq!(module_name(root, Path::new("/elsewhere/x.hy")), "x");
}

#[test]
fn mangle_follows_the_contract() {
    assert_eq!(mangle("classifier-peers"), "classifier_peers");
    assert_eq!(mangle("-private-name"), "-private_name");
    assert_eq!(mangle("->"), "->");
    assert_eq!(mangle("MemoryStore"), "MemoryStore");
}

#[test]
fn top_level_wrappers_and_destructuring() {
    let source = "(eval-and-compile (defn inner [] 1))\n(setv [a b] [1 2] c 3)\n(setv x.attr 1)\n(lazy val table (load))\n(when True (defn hidden [] 1))\n";
    let file = index(source);
    for name in ["inner", "a", "b", "c", "table"] {
        assert_eq!(def(&file, name).container, None);
    }
    assert!(!file.definitions.iter().any(|definition| definition.name == "hidden" || definition.name.contains("attr")));
}

/// 呼び出しを callee で引く(1 件もなければ検を落とす)。
fn calls_named<'a>(file: &'a HyFileIndex, callee: &str) -> Vec<&'a Call> {
    file.calls.iter().filter(|call| call.callee == callee).collect()
}

/// 定義の名前から definitions の添字を引く。
fn index_of(file: &HyFileIndex, name: &str) -> usize {
    file.definitions
        .iter()
        .position(|definition| definition.name == name)
        .unwrap_or_else(|| panic!("定義 {name} が無い"))
}

#[test]
fn bases_of_classes_and_records() {
    let source = "(defclass [(dataclass :frozen True)] PutRow [EffectBase]\n  (#^ str table))\n\
                  (defclass Dotted [doeff.EffectBase Mixin :metaclass Meta])\n\
                  (defclass NoBases [] 1)\n\
                  (defclass Bare)\n\
                  (defrecord Row #^ str key)\n\
                  (defrecord WithBase [Base] #^ str key)\n\
                  (defn f [] 1)\n";
    let file = index(source);
    assert_eq!(def(&file, "PutRow").bases, vec!["EffectBase"]);
    assert_eq!(def(&file, "Dotted").bases, vec!["doeff.EffectBase", "Mixin"]);
    assert!(def(&file, "NoBases").bases.is_empty());
    assert!(def(&file, "Bare").bases.is_empty());
    assert!(def(&file, "Row").bases.is_empty());
    assert_eq!(def(&file, "WithBase").bases, vec!["Base"]);
    assert_eq!(def(&file, "key").container.as_deref(), Some("Row"));
    assert!(def(&file, "f").bases.is_empty());
    assert!(def(&file, "table").bases.is_empty());
}

#[test]
fn call_callers_are_the_innermost_definition() {
    let source = r#"(require doeff-hy.macros [defk defhandler <-])
(setup-logging)
(defk step [state]
  {:pre [(: state dict)] :post [(: % int)]}
  (<- row (GetRow (key-of state)))
  (setv inner (fn [x] (helper x)))
  (compute row))
(defhandler writes
  (session var count (initial-count))
  (PutRow [table key]
    (log-write table)
    (resume key)))
(defclass Box [] (defn size [self] (len self.items)))
"#;
    let file = index(source);
    assert_eq!(calls_named(&file, "setup-logging")[0].caller, None);
    let step = Some(index_of(&file, "step"));
    for callee in ["GetRow", "key-of", "helper", "compute", "isinstance"] {
        if let Some(call) = calls_named(&file, callee).first() {
            assert_eq!(call.caller, step, "{callee}");
        }
    }
    assert_eq!(calls_named(&file, "helper")[0].caller, step, "入れ子の fn の中は外側の定義");
    let clause = file
        .definitions
        .iter()
        .position(|definition| definition.name == "PutRow" && definition.kind == DefinitionKind::EffectClause);
    assert_eq!(calls_named(&file, "log-write")[0].caller, clause);
    assert_eq!(calls_named(&file, "initial-count")[0].caller, Some(index_of(&file, "count")));
    assert_eq!(calls_named(&file, "len")[0].caller, Some(index_of(&file, "size")));
    // effect 節の頭・予約語・演算子・`(.method obj)` は呼び出しではない。
    for absent in ["PutRow", "defk", "defhandler", "<-", "fn", "setv", "resume", "session", ":", "defclass", "defn", "require"] {
        assert!(calls_named(&file, absent).is_empty(), "{absent} が calls に入っている");
    }
    for call in &file.calls {
        assert_eq!(slice(source, call.range), call.callee);
        if let Some(caller) = call.caller {
            let full = file.definitions[caller].full_range;
            assert!(full.start <= call.range.start && call.range.end <= full.end, "{} の caller の範囲の外", call.callee);
        }
    }
}

#[test]
fn performed_calls() {
    let source = r#"(require doeff-hy.macros [defk <-])
(defk run [x]
  {:pre [] :post []}
  (<- (Log (fmt x)))
  (<- a (Ask "k"))
  (<- b int (Get (key-for x)))
  (yield (Put "k" 1))
  (yield-from (sub-program x))
  (plain-call (Nested x))
  (.method x)
  (. x attr (other-method 1))
  (mod.sub.fn 1)
  (match x (Point :x px) px _ 0)
  #^ (of list int) typed)
"#;
    let file = index(source);
    let performed = |callee: &str| calls_named(&file, callee).first().map(|call| call.performed);
    for callee in ["Log", "Ask", "Get", "Put", "sub-program"] {
        assert_eq!(performed(callee), Some(true), "{callee}");
    }
    for callee in ["fmt", "key-for", "plain-call", "Nested"] {
        assert_eq!(performed(callee), Some(false), "{callee}");
    }
    assert!(calls_named(&file, "method").is_empty());
    assert!(calls_named(&file, "other-method").is_empty());
    assert!(calls_named(&file, "Point").is_empty(), "match の pattern は呼び出しではない");
    assert!(calls_named(&file, "of").is_empty(), "型注釈の中は呼び出しではない");
    let dotted = &calls_named(&file, "fn")[0];
    assert_eq!(dotted.qualifier.as_deref(), Some("mod.sub"));
    assert_eq!(slice(source, dotted.range), "fn");
    assert_eq!(calls_named(&file, "Log")[0].mangled, "Log");
    assert_eq!(calls_named(&file, "key-for")[0].mangled, "key_for");
    // 参照は今までどおり入る。
    assert!(file.references.iter().any(|reference| reference.name == "method"));
    assert!(file.references.iter().any(|reference| reference.name == "Point"));
}

#[test]
fn bang_performs_its_direct_call() {
    let source = "(defk run [x] {:pre [] :post []} (f (! (X (g a)))) (h (! x)))\n";
    let file = index(source);
    assert!(calls_named(&file, "X")[0].performed);
    for callee in ["f", "g", "h"] {
        assert!(!calls_named(&file, callee)[0].performed, "{callee}");
    }
    assert!(calls_named(&file, "!").is_empty());
    assert!(calls_named(&file, "x").is_empty(), "(! x) の記号だけの形は呼び出しではない");
}

#[test]
fn calls_survive_broken_sources() {
    for source in ["(<-", "(<- (", "(yield", "(defhandler h (E [", "(. x (", "(match x (P", "(f (g", "(a.b."] {
        let file = index(source);
        for call in &file.calls {
            if let Some(caller) = call.caller {
                assert!(caller < file.definitions.len());
            }
        }
    }
    let file = index("(a.b. 1)\n");
    assert_eq!(calls_named(&file, "b")[0].qualifier.as_deref(), Some("a"));
}
