;;; Executable ADR: doeff has no built-in handlers and no special handlers. The handlers shipped by doeff's packages (reader, state, writer,
;;; scheduled, await_handler, and others) are reference implementations, have the same shape as users' handlers, and can each be replaced by a user's handler.
;;; Documentation (README, docs site pages, package READMEs, code docstrings and comments) does not describe handlers, effects, schedulers, or interpreters
;;; as built-in, standard, or special; it calls them reference implementations.
;;;
;;; Decided in the review of the draft of the published article (the decision is the :problem fact).
;;;
;;; Advice at the moment of writing lives outside doeff: agent-commons' defjevrule doeff-handlers-are-reference-implementations (when writing text that
;;; explains doeff, it reminds the writer if a handler is described as built-in or special). The doeff side holds a deftest that checks the wording
;;; in the reading scope (R3, R4).
;;;
;;; Rollback: revert the commit that added this file and rebuild the ledger with `make enforcement-ledger` (code behavior was not changed —
;;; only the wording of documentation and docstrings was fixed). Remove this only on the maintainer's word. Retire the agent-commons advice at the same time.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk val])
(import doeff [run])
(import pathlib [Path])
(import re)
(import subprocess)


(val REPO-ROOT (. (Path __file__) parent parent parent))
;; Entry files of the reading scope (repo root).
(val ENTRY-FILES #("README.md" "CONTRIBUTING.md"))
;; Suffixes of files whose docstrings and comments are read.
(val CODE-SUFFIXES #(".py" ".pyi" ".hy" ".rs"))
;; Dirs excluded from reading code (tests, examples, test fixtures, benchmarks — places that hold counterexamples and old shapes on purpose).
(val CODE-EXCLUDED-DIR (re.compile r"(?:^|/)(?:tests?|examples|fixtures|benchmarks)/"))
;; Lines of the allow list in mkdocs.yml's exclude_docs (`  !/<page>.md`) — the set of pages that go into the docs site.
(val SITE-PAGE-LINE (re.compile r"^\s+!/(\S+\.md)$" re.M))
;; Wordings that describe handlers as built-in, standard, or special (R2). Talk about Python's built-in functions and types (builtins) is not matched.
;; The last 3 alternatives match the Japanese wordings (built-in / standard / special + handler, scheduler, effect, interpreter), written as \u escapes.
(val SPECIAL-HANDLER-WORDING
  (re.compile
    (.join "|"
      [r"\bbuilt[- ]?in(?:\s+(?:default|reference))?\s+(?:handlers?|scheduler|effects?|interpreter|ones|reader|state|writer)\b"
       r"\bstandard\s+(?:handlers?|effects|interpreter|scheduler)\b"
       r"(?<![-_\w])core\s+(?:handlers?|effects)\b"
       r"\bstock\s+(?:handlers?|scheduler)\b"
       r"\bbatteries[- ]included\b"
       r"\bdefault\s+(?:handlers?|scheduler)\b"
       r"\b(?:special|privileged)\s+handlers?\b"
       r"\u7d44\u307f\u8fbc\u307f\u306e?\s*(?:handler|\u30cf\u30f3\u30c9\u30e9|scheduler|\u30b9\u30b1\u30b8\u30e5\u30fc\u30e9|effect|\u30a8\u30d5\u30a7\u30af\u30c8|interpreter)"
       r"\u6a19\u6e96\u306e?\s*(?:handler|\u30cf\u30f3\u30c9\u30e9|effect|\u30a8\u30d5\u30a7\u30af\u30c8|scheduler|\u30b9\u30b1\u30b8\u30e5\u30fc\u30e9)"
       r"\u7279\u5225\u306a\s*(?:handler|\u30cf\u30f3\u30c9\u30e9)"])
    re.I))


(defk reading-scope [tracked mkdocs-yml]
  {:pre [(: tracked list) (: mkdocs-yml str)]
   :post [(: % list)]
   :tags {:context "doeff-docs-adr" :role "judgment"}}
  "The set of documentation files users read (R3) — the entry files, the docs site pages (the allow list in mkdocs.yml's exclude_docs),
  package READMEs, and the code of doeff and packages (excluding the tests, examples, test fixtures, and benchmarks dirs). tracked is the list of the repo's file paths."
  (val tracked-set (set tracked))
  (val site-pages (lfor page (SITE-PAGE-LINE.findall mkdocs-yml) (+ "docs/" page)))
  (val package-readmes (lfor path tracked :if (re.fullmatch r"packages/[^/]+/README\.md" path) path))
  (val code (lfor path tracked
                  :if (and (or (.startswith path "doeff/") (.startswith path "packages/"))
                           (.endswith path CODE-SUFFIXES)
                           (is (CODE-EXCLUDED-DIR.search path) None))
                  path))
  (+ (lfor path ENTRY-FILES :if (in path tracked-set) path)
     (lfor path site-pages :if (in path tracked-set) path)
     package-readmes
     code))


(defk wording-findings [path text]
  {:pre [(: path str) (: text str)]
   :post [(: % list)]
   :tags {:context "doeff-docs-adr" :role "judgment"}}
  "From the text of one file, the list of places that describe a handler as built-in, standard, or special (empty = none) — each is `<path>:<line>: <matched phrase>` (R2)."
  (lfor [number line] (enumerate (.splitlines text) 1)
        found (SPECIAL-HANDLER-WORDING.finditer line)
        (+ path ":" (str number) ": " (.group found 0))))


(defadr ADR-DOE-DOCS-001
  :title "doeff has no built-in handlers and no special handlers. The handlers of doeff's packages are reference implementations, and documentation (README, docs site pages, package READMEs, code docstrings and comments) does not describe handlers as built-in, standard, or special but calls them reference implementations. A deftest checks that such wording does not return to the reading scope"
  :status "accepted"
  :scope ["README.md"
          "CONTRIBUTING.md"
          "docs/"
          "packages/*/README.md"
          "doeff/"
          "packages/"
          "docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy"]
  :problem
    [(fact
       "Decided in the review of the draft of the published article: no handler is built in; there are only reference implementations. Writing as if special handlers existed is forbidden.")
     (fact
       "On the implementation side no handler gets special treatment. `doeff.run` installs no handler at all and runs with only the handlers the program installs by calling them (`default_handlers` has been removed from doeff; calling it answers that handlers should be installed by calling them in the program). SPEC-009 R5-C also defines the scheduler as a user-space reference implementation."
       :evidence "run in doeff/run.py, default_handlers (_Removed) in doeff/__init__.py, docs/history/specs/vm/SPEC-009-rust-vm-migration.md R5-C")
     (fact
       "On main as of 2026-10-25 (24b3cf155), the README, CONTRIBUTING, docs site pages, package READMEs, and docstrings described handlers as built-in, standard, or special in 55 places, such as 'the built-in scheduler', 'built-in effects and handlers', 'the standard effects and handlers', 'Batteries included', 'Core handlers', 'Built-In Handlers', and 'built-in default interpreter'."
       :evidence "Result of applying this ADR's reading-scope and SPECIAL-HANDLER-WORDING to the tree of 24b3cf155 (55 places)")]
  :context
    [(interpretation
       "The cause was that there was neither a law nor a check binding the design 'no handler gets special treatment' to the wording of documentation. Text introducing package handlers kept using the words 'built-in' and 'standard', and readers read that doeff's core has special handlers.")
     (interpretation
       "Package handlers have the same shape as the handlers doeff users write (a `@do` function, `handler(raw)`), and work only when the program installs them by calling them. So documentation can say at most 'this package has a reference-implementation handler for this effect', and cannot say 'included in doeff' or 'standard'.")
     (interpretation
       "The interpreter the doeff run CLI uses when there is no marker (`doeff.cli.run_services:default_interpreter`) is also just one function that installs reference-implementation handlers, not a special handler. Documentation calls it the 'fallback interpreter'.")]
  :decision
    [(rule R1 "doeff has no built-in handlers and no special handlers. The handlers of doeff's packages (doeff-core-effects and others) are reference implementations, and each can be replaced by a user's handler.")
     (rule R2 "Documentation does not describe handlers, effects, schedulers, or interpreters as built-in, standard (standard, core, stock, default), special, or bundled conveniences (batteries included). It writes 'reference implementation' or 'a handler the package has'. Talk about Python's built-in functions and types (builtins) is outside this rule.")
     (rule R3 "The reading scope R2 applies to is: the entry files (README.md, CONTRIBUTING.md), the pages that go into the docs site (the allow list in mkdocs.yml's exclude_docs), package READMEs, and the docstrings and comments of the code of doeff and packages (excluding the tests, examples, test fixtures, and benchmarks dirs). Development records (docs/history, docs/design, docs/adr, and other docs outside the site) are kept as records of when they were written and are not fixed.")
     (rule R4 "Advice at the moment of writing lives outside doeff (agent-commons' defjevrule doeff-handlers-are-reference-implementations). The doeff side holds a deftest that checks the R3 scope.")]
  :laws
    [(law handlers-are-written-as-reference-implementations
       :statement "∀ file ∈ reading-scope(repo files, mkdocs.yml). wording-findings(file, text) = []"
       :counterexamples
         [(counterexample "The README's handler table says 'the built-in scheduler, `scheduled(...)`' — readers read that the scheduler is part of doeff's core and cannot be replaced")
          (counterexample "A package README opens with 'The standard effects and handlers for doeff' — it reads as if the package's handlers were doeff's standard and ranked above users' handlers")
          (counterexample "A docstring says 'Built-in handlers for notification effects' — the same wording also appears in the generated API docs")]
       :enforced-by ["docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-reading-scope-writes-handlers-as-reference-implementations"
                     "docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-special-handler-wording-is-named"
                     "docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-reading-scope-covers-the-published-files"]
       :wiring "Wired (2026-10-26) — this file's tests. Advice at the moment of writing is agent-commons' defjevrule (R4)")]
  :enforcement
    [(deftest test-adr-doe-docs-001-reading-scope-writes-handlers-as-reference-implementations
       ;; The reading scope of the current tree has no place that describes a handler as built-in, standard, or special (R2, R3).
       (val tracked (.split (. (subprocess.run ["git" "-C" (str REPO-ROOT) "ls-files"] :capture-output True :text True :check True) stdout)))
       (val scope (run (reading-scope tracked (.read-text (/ REPO-ROOT "mkdocs.yml") :encoding "utf-8"))))
       (val findings (lfor path scope
                           :if (.exists (/ REPO-ROOT path))
                           found (run (wording-findings path (.read-text (/ REPO-ROOT path) :encoding "utf-8" :errors "replace")))
                           found))
       (assert (= findings [])
               (+ "Some places describe a handler as built-in, standard, or special (ADR-DOE-DOCS-001 R2 — call package handlers reference implementations): "
                  (str findings))))
     (deftest test-adr-doe-docs-001-special-handler-wording-is-named
       ;; Name and refuse the counterexample wordings; let the reference-implementation wording and talk about Python builtins through (R2).
       ;; The Japanese wordings are written as \u escapes (built-in handler / standard handler / special handler).
       (assert (= (run (wording-findings "README.md" "| holds it | the built-in scheduler, `scheduled(...)` |"))
                  ["README.md:1: built-in scheduler"]))
       (assert (= (run (wording-findings "x.md" "a\nThe standard effects and handlers for doeff.\nBuilt-In Handlers\n"))
                  ["x.md:2: standard effects" "x.md:3: Built-In Handlers"]))
       (assert (= (run (wording-findings "x.py" "\"\"\"Core handlers — reader, state, writer.\"\"\""))
                  ["x.py:1: Core handlers"]))
       (assert (= (run (wording-findings "x.md" "- **Batteries included.**\nthe stock scheduler\nthe default handlers"))
                  ["x.md:1: Batteries included" "x.md:2: stock scheduler" "x.md:3: default handlers"]))
       (assert (= (run (wording-findings "x.md" "\u7d44\u307f\u8fbc\u307f\u306e\u30cf\u30f3\u30c9\u30e9\n\u6a19\u6e96\u306e handler\n\u7279\u5225\u306a\u30cf\u30f3\u30c9\u30e9"))
                  ["x.md:1: \u7d44\u307f\u8fbc\u307f\u306e\u30cf\u30f3\u30c9\u30e9" "x.md:2: \u6a19\u6e96\u306e handler" "x.md:3: \u7279\u5225\u306a\u30cf\u30f3\u30c9\u30e9"]))
       (assert (= (run (wording-findings "x.md" (+ "Reference implementations of effects and handlers.\n"
                                                    "doeff-core-effects ships the reference handler `state`.\n"
                                                    "a builtin class, Python's built-in functions, `doeff_core_effects`\n"
                                                    "the fallback interpreter")))
                  [])))
     (deftest test-adr-doe-docs-001-reading-scope-covers-the-published-files
       ;; The reading scope holds the entry files, site pages, package READMEs, and code, and does not hold tests or development records (R3).
       (val tracked ["README.md" "CONTRIBUTING.md" "docs/index.md" "docs/history/specs/x.md" "docs/design/y.md"
                     "packages/doeff-notify/README.md" "packages/doeff-notify/src/doeff_notify/_handlers/__init__.py"
                     "packages/doeff-notify/tests/test_handlers.py" "doeff/program.py" "doeff/cli/run_services.py"
                     "packages/doeff-vm/src/pyvm.rs" "examples/a.py" "packages/doeff-x/examples/b.py"])
       (val scope (run (reading-scope tracked "exclude_docs: |\n  *\n  !/index.md\n  !/missing.md\nnav:\n")))
       (assert (= scope ["README.md" "CONTRIBUTING.md" "docs/index.md" "packages/doeff-notify/README.md"
                         "packages/doeff-notify/src/doeff_notify/_handlers/__init__.py" "doeff/program.py"
                         "doeff/cli/run_services.py" "packages/doeff-vm/src/pyvm.rs"])))])
