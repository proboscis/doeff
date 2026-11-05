//! Tests for `hy-index` analysis: contract kind, container, docstring, params, import, references, UTF-16 columns,
//! and not crashing on broken input, all checked from source strings.

use std::path::Path;

use super::*;

/// Index the source as `/r/pkg/sample.hy`.
fn index(source: &str) -> HyFileIndex {
    index_source(Path::new("/r"), Path::new("/r/pkg/sample.hy"), source)
}

/// Look up a definition by name (fail the test if it is missing).
fn def<'a>(file: &'a HyFileIndex, name: &str) -> &'a Definition {
    file.definitions
        .iter()
        .find(|definition| definition.name == name)
        .unwrap_or_else(|| panic!("definition {name} not found: {:?}", names(file)))
}

/// List of (name, kind, container) for the definitions.
fn names(file: &HyFileIndex) -> Vec<(String, &'static str, Option<String>)> {
    file.definitions
        .iter()
        .map(|definition| (definition.name.clone(), definition.kind.as_str(), definition.container.clone()))
        .collect()
}

/// Look up the byte position in the source from a contract position (line, UTF-16 column), for cross-checking.
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

/// Cut out the source string that a contract range points at.
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
        assert!(kinds.contains(&kind), "kind {kind} not found: {:?}", names(&file));
    }
    assert!(file.errors.is_empty(), "{:?}", file.errors);
    for definition in &file.definitions {
        assert_eq!(slice(source, definition.range), definition.name, "range of {} does not point at the name", definition.name);
        let full = slice(source, definition.full_range);
        assert!(full.contains(&definition.name), "full_range of {} does not contain the name", definition.name);
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
    // Local bindings inside a function are not included.
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

/// One row of import checking: (module, name, alias, is_require, string the range points at).
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
        assert!(!find(present).is_empty(), "reference {present} not found");
    }
    for absent in [
        "defn", "comment-name", "string-name", "quoted-name", "tmpl", "discarded-name", "None", "True", "+", "key",
        ":key", "_",
    ] {
        assert!(find(absent).is_empty(), "reference {absent} is included");
    }
    for reference in &file.references {
        assert_eq!(slice(source, reference.range), reference.name);
    }
    assert_eq!(find("unquoted-name")[0].mangled, "unquoted_name");
    assert_eq!(find("val").len(), 1, "only the one inside require (the leading reserved word val is not included)");
}

#[test]
fn required_word_macros_are_references_unless_required() {
    let file = index("(check x)\n");
    assert!(file.references.iter().any(|reference| reference.name == "check"));
    let file = index("(require doeff-hy.macros [check])\n(check x)\n");
    assert_eq!(file.references.iter().filter(|reference| reference.name == "check").count(), 1, "only the one inside require");
}

#[test]
fn columns_count_utf16_code_units() {
    let source = ";; \u{65e5}\u{672c}\u{8a9e}\u{306e}\u{8a3b} 😀\n(setv \u{540d}\u{524d} \"😀\u{7d75}\") (defn \u{95a2}\u{6570} [\u{5f15}\u{6570}] (print \u{540d}\u{524d} \u{5f15}\u{6570}))\n(setv 😀x 1)\n";
    let file = index(source);
    let name = def(&file, "\u{540d}\u{524d}");
    assert_eq!(name.range.start, Position { line: 1, character: 6 });
    assert_eq!(name.range.end, Position { line: 1, character: 8 });
    let function = def(&file, "\u{95a2}\u{6570}");
    // `(setv <name> "<emoji><e>") (defn ` -- the emoji is 2 code units.
    assert_eq!(function.range.start, Position { line: 1, character: 22 });
    assert_eq!(def(&file, "\u{95a2}\u{6570}").params, vec!["\u{5f15}\u{6570}"]);
    let emoji = def(&file, "😀x");
    assert_eq!(emoji.range.start, Position { line: 2, character: 6 });
    assert_eq!(emoji.range.end, Position { line: 2, character: 9 });
    for definition in &file.definitions {
        assert_eq!(slice(source, definition.range), definition.name);
    }
    let uses: Vec<_> = file.references.iter().filter(|reference| reference.name == "\u{540d}\u{524d}").collect();
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
        "\"\u{65e5}\u{672c}\\",
        "(setv \u{540d}\u{524d} \"😀\\",
        "#",
        "#^ #^",
        "~@",
        "(defk f [x] {:pre",
        "(defn [] )",
        "(defadr A :laws [(law",
        "f\"{\u{540d}\u{524d}:}\"",
        "f\"{\u{540d}}\"",
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
    assert!(file.errors[0].contains("not closed"));
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

/// Look up calls by callee (fail the test if there are none).
fn calls_named<'a>(file: &'a HyFileIndex, callee: &str) -> Vec<&'a Call> {
    file.calls.iter().filter(|call| call.callee == callee).collect()
}

/// Look up the definitions index from a definition name.
fn index_of(file: &HyFileIndex, name: &str) -> usize {
    file.definitions
        .iter()
        .position(|definition| definition.name == name)
        .unwrap_or_else(|| panic!("definition {name} not found"))
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
    assert_eq!(calls_named(&file, "helper")[0].caller, step, "inside a nested fn the caller is the enclosing definition");
    let clause = file
        .definitions
        .iter()
        .position(|definition| definition.name == "PutRow" && definition.kind == DefinitionKind::EffectClause);
    assert_eq!(calls_named(&file, "log-write")[0].caller, clause);
    assert_eq!(calls_named(&file, "initial-count")[0].caller, Some(index_of(&file, "count")));
    assert_eq!(calls_named(&file, "len")[0].caller, Some(index_of(&file, "size")));
    // The head of an effect clause, reserved words, operators, and `(.method obj)` are not calls.
    for absent in ["PutRow", "defk", "defhandler", "<-", "fn", "setv", "resume", "session", ":", "defclass", "defn", "require"] {
        assert!(calls_named(&file, absent).is_empty(), "{absent} is included in calls");
    }
    for call in &file.calls {
        assert_eq!(slice(source, call.range), call.callee);
        if let Some(caller) = call.caller {
            let full = file.definitions[caller].full_range;
            assert!(full.start <= call.range.start && call.range.end <= full.end, "{} is outside the caller's range", call.callee);
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
    assert!(calls_named(&file, "Point").is_empty(), "a match pattern is not a call");
    assert!(calls_named(&file, "of").is_empty(), "inside a type annotation is not a call");
    let dotted = &calls_named(&file, "fn")[0];
    assert_eq!(dotted.qualifier.as_deref(), Some("mod.sub"));
    assert_eq!(slice(source, dotted.range), "fn");
    assert_eq!(calls_named(&file, "Log")[0].mangled, "Log");
    assert_eq!(calls_named(&file, "key-for")[0].mangled, "key_for");
    // References are still included as before.
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
    assert!(calls_named(&file, "x").is_empty(), "the symbol-only form (! x) is not a call");
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

#[test]
fn defeffect_is_a_definition_with_fields_bases_docstring_and_tags() {
    let source = r#"(defeffect BorrowToken "Borrow a token from the custody" {:fields [#^ str profile ttl] :answer str :tags {:context "custody" :role "intent"}})
(defeffect Bare {:fields []})
(defk plan [x] "plan" {:pre [] :tags {:context "billing" :role "program" :n 1}} x)
(defhandler h {:tags {}} (E [e k] (k 1)))
(defn f [] 1)
"#;
    let file = index(source);
    let effect = def(&file, "BorrowToken");
    assert_eq!(effect.kind.as_str(), "defeffect");
    assert_eq!(effect.bases, vec!["EffectBase"]);
    assert_eq!(effect.params, vec!["profile", "ttl"]);
    assert_eq!(effect.docstring.as_deref(), Some("Borrow a token from the custody"));
    let tags = effect.tags.as_ref().expect("tags");
    assert_eq!(tags.get("context").map(String::as_str), Some("custody"));
    assert_eq!(tags.get("role").map(String::as_str), Some("intent"));
    assert!(def(&file, "Bare").tags.is_none());
    // The contract dict's :tags (only keys with string values). An empty dict, or a definition with no contract dict, is null.
    let plan = def(&file, "plan").tags.as_ref().expect("tags of plan");
    assert_eq!(plan.len(), 2);
    assert!(def(&file, "h").tags.is_none());
    assert!(def(&file, "f").tags.is_none());
    // The head of defeffect is not included in calls (reserved word).
    assert!(!file.calls.iter().any(|call| call.callee == "defeffect"));
}

#[test]
fn defrecord_header_gives_tags_and_checks() {
    // The head dict {:tags ... :check [...]} of doeff-hy's defrecord.
    let source = r#"(defrecord ChatId
  "id of a chat"
  {:tags {:context "chat" :role "type"}
   :check [(CHAT-ID-PATTERN.fullmatch value)]}
  (#^ str value))
(defrecord Span {:check [(>= start 0) (<= start end)]} #^ int start #^ int end)
(defrecord Named {:tags {:context "chat" :role "type"}} #^ str kind)
(defrecord Plain #^ str key)
"#;
    let file = index(source);
    let chat = def(&file, "ChatId");
    assert_eq!(chat.docstring.as_deref(), Some("id of a chat"));
    let tags = chat.tags.as_ref().expect("tags of ChatId");
    assert_eq!(tags.get("context").map(String::as_str), Some("chat"));
    assert_eq!(tags.get("role").map(String::as_str), Some("type"));
    assert_eq!(chat.checks.as_deref(), Some(&["(CHAT-ID-PATTERN.fullmatch value)".to_string()][..]));
    // The head dict is not a field. Fields are read after the dict.
    assert_eq!(def(&file, "value").container.as_deref(), Some("ChatId"));
    let span = def(&file, "Span");
    assert!(span.tags.is_none());
    assert_eq!(span.checks.as_ref().map(Vec::len), Some(2));
    assert_eq!(def(&file, "end").container.as_deref(), Some("Span"));
    // A head dict with only :tags gives an empty checks list; a form with no head dict has no checks (the JSON field is not emitted at all).
    assert_eq!(def(&file, "Named").checks.as_ref().map(Vec::len), Some(0));
    let plain = def(&file, "Plain");
    assert!(plain.tags.is_none() && plain.checks.is_none());
    let json = serde_json::to_value(plain).expect("JSON");
    assert!(json.get("checks").is_none());
}

#[test]
fn defwire_is_read_as_a_record() {
    // doeff-hy's defwire (the type at a JSON boundary) expands to defrecord. kind, docstring, and fields are read the same as defrecord.
    let source = r#"(defwire LandingRow
  "one row of the intake ledger"
  {:tags {:context "land-notice" :role "type"} :names :camel :unknown :reject}
  (#^ str lane-id)
  (setv #^ (| int None) landed-at None))
"#;
    let file = index(source);
    let row = def(&file, "LandingRow");
    assert_eq!(row.kind.as_str(), "defrecord");
    assert_eq!(row.docstring.as_deref(), Some("one row of the intake ledger"));
    assert_eq!(def(&file, "lane-id").container.as_deref(), Some("LandingRow"));
    assert_eq!(def(&file, "landed-at").container.as_deref(), Some("LandingRow"));
    // The head dict's :tags are the definition's tags just like defrecord, and an empty list if there is no :check (:names and :unknown are not fields).
    let tags = row.tags.as_ref().expect("tags of LandingRow");
    assert_eq!(tags.get("context").map(String::as_str), Some("land-notice"));
    assert_eq!(row.checks.as_ref().map(Vec::len), Some(0));
    // The head of defwire is not included in calls (reserved word).
    assert!(!file.calls.iter().any(|call| call.callee == "defwire"));
}

#[test]
fn format_strings_skip_strings_and_brackets_inside_replacement_fields() {
    // The shape of agora's controllers/durable/protocol/contract.hy (a3f3b062) -- a "; " inside a replacement field and a nested f-string closed the string
    // early (3 places were reported as unclosed parens or strings, and the violations came out empty). Like Hy's reader (hy.read_many), it is a single expression to the end.
    let cases = [
        r#"(raise (ValueError f"bad: {(.join "; " (gfor f parsed.fields f"{f.field}: {f.reason}"))}"))"#,
        r#"(print f"{{literal}} {x} {(get d "k")} {{ and }}")"#,
        r#"(print f"{(dict :a "}")} end")"#,
        r#"(print f"{x !r} {y :>10} \" quote")"#,
    ];
    for source in cases {
        let mut reader = crate::hy_index::reader::Reader::new(source, 0, source.len());
        let forms = reader.read_all();
        assert!(reader.issues.is_empty(), "{:?}: {:?}", source, reader.issues);
        assert_eq!(forms.len(), 1, "{:?}", source);
        assert_eq!(forms[0].span.end, source.len(), "{:?}", source);
    }
    // An unclosed f-string is still recorded as an unreadable place, as before.
    let broken = r#"(print f"{(get d "k")}"#;
    let mut reader = crate::hy_index::reader::Reader::new(broken, 0, broken.len());
    reader.read_all();
    assert!(!reader.issues.is_empty());
}

#[test]
fn defrecord_field_names_are_definitions_not_references() {
    let file = index("(import app.board [old-serving])\n(defrecord Obs (#^ int old-serving) #^ str other bare)\n(defk use [] (old-serving))\n");
    let lines: Vec<_> = file.references.iter().filter(|r| r.name == "old-serving").map(|r| r.range.start.line).collect();
    // The field declaration (line 1) is not a reference -- only the import (line 0) and the caller (line 2) remain.
    assert_eq!(lines, vec![0, 2]);
    assert!(file.definitions.iter().any(|d| d.name == "old-serving" && d.container.as_deref() == Some("Obs")));
}
