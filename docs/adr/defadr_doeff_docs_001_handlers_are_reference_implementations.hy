;;; Executable ADR: doeff に組み込みの handler・特別な handler は無い。doeff の package が持つ handler(reader・state・writer・
;;; scheduled・await_handler ほか)は参考実装で、利用者の handler と同じ形をしており、どれも利用者の handler に替えられる。
;;; 文書(README・docs の site の page・package の README・code の docstring と注釈)は、handler・effect・scheduler・interpreter を
;;; 組み込みの物・標準の物・特別な物として書かず、参考実装と書く。
;;;
;;; 出自 = 持ち主(利用者)の言葉 2026-10-20 13:10 JST(原文は :problem の fact)。公開の記事の下書きを読んで付けた注釈。
;;;
;;; 書くその場面の助言は doeff の外が持つ: agent-commons の defjevrule doeff-handlers-are-reference-implementations(doeff を
;;; 説明する文を書く時に、handler を組み込み・特別な物と書いたら思い出させる)。doeff の側には、読みの範囲の言い方を検める
;;; deftest を置く(R3・R4)。
;;;
;;; 戻し方: この file を足した commit を revert し、`make enforcement-ledger` で台帳を作り直す(code の振る舞いは変えていない —
;;; 直したのは文書と docstring の言い方だけ)。外すのは持ち主の言葉が在る時だけ。agent-commons の助言も同じ時に退ける。

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk val])
(import doeff [run])
(import pathlib [Path])
(import re)
(import subprocess)


(val REPO-ROOT (. (Path __file__) parent parent parent))
;; 読みの範囲の入口の file(repo の根)。
(val ENTRY-FILES #("README.md" "CONTRIBUTING.md"))
;; code の docstring と注釈を読む file の拡張子。
(val CODE-SUFFIXES #(".py" ".pyi" ".hy" ".rs"))
;; code の読みから外す dir(試験・例・試験の材料・測り — 反例や古い形を意図して持つ所)。
(val CODE-EXCLUDED-DIR (re.compile r"(?:^|/)(?:tests?|examples|fixtures|benchmarks)/"))
;; mkdocs.yml の exclude_docs の許す一覧の行(`  !/<page>.md`)— docs の site に入る page の組。
(val SITE-PAGE-LINE (re.compile r"^\s+!/(\S+\.md)$" re.M))
;; handler を組み込み・標準・特別な物として書く言い方(R2)。Python の組み込みの関数・型の話(builtins)は当てない。
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
       r"組み込みの?\s*(?:handler|ハンドラ|scheduler|スケジューラ|effect|エフェクト|interpreter)"
       r"標準の?\s*(?:handler|ハンドラ|effect|エフェクト|scheduler|スケジューラ)"
       r"特別な\s*(?:handler|ハンドラ)"])
    re.I))


(defk reading-scope [tracked mkdocs-yml]
  {:pre [(: tracked list) (: mkdocs-yml str)]
   :post [(: % list)]
   :tags {:context "doeff-docs-adr" :role "judgment"}}
  "利用者が読む文書の file の組(R3)— 入口の file・docs の site の page(mkdocs.yml の exclude_docs の許す一覧)・
  package の README・doeff と packages の code(試験・例・試験の材料・測りの dir を除く)。tracked は repo の file の path の列。"
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
  "1 つの file の文から、handler を組み込み・標準・特別な物として書いた所の列(空 = 無い)— 1 つは `<path>:<行>: <当たった句>`(R2)。"
  (lfor [number line] (enumerate (.splitlines text) 1)
        found (SPECIAL-HANDLER-WORDING.finditer line)
        (+ path ":" (str number) ": " (.group found 0))))


(defadr ADR-DOE-DOCS-001
  :title "doeff に組み込みの handler・特別な handler は無い。doeff の package の handler は参考実装で、文書(README・docs の site の page・package の README・code の docstring と注釈)は handler を組み込み・標準・特別な物として書かず、参考実装と書く。読みの範囲に その言い方が戻らない事を deftest が確かめる"
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
       "持ち主の言葉 2026-10-20 13:10 JST(公開の記事の下書きを読んで付けた注釈・一字も変えない): 「すべてのハンドラは組み込みではない。ただ参考実装が存在するだけ、特別なハンドラが存在するかのような記述は禁止」"
       :evidence "持ち主の内部の追跡に在る注釈(2026-10-20 13:10 JST)")
     (fact
       "実装の側に特別扱いの handler は無い。`doeff.run` は handler を 1 つも積まず、program に呼んで積んだ handler だけで走る(`default_handlers` は doeff から削除済みで、呼ぶと handler を program に呼んで積めと答える)。SPEC-009 R5-C も scheduler を user-space の参考実装と定める。"
       :evidence "doeff/run.py の run・doeff/__init__.py の default_handlers(_Removed)・docs/history/specs/vm/SPEC-009-rust-vm-migration.md R5-C")
     (fact
       "2026-10-25 の main(24b3cf155)の README・CONTRIBUTING・docs の site の page・package の README・docstring は、「the built-in scheduler」「built-in effects and handlers」「the standard effects and handlers」「Batteries included」「Core handlers」「Built-In Handlers」「built-in default interpreter」など 55 か所で、handler を組み込み・標準・特別な物として書いていた。"
       :evidence "この ADR の reading-scope と SPECIAL-HANDLER-WORDING を 24b3cf155 の木に当てた結果(55 か所)")]
  :context
    [(interpretation
       "原因は、handler に特別扱いが無いという設計を、文書の言い方として縛る法も検めも無かった事。package の handler を紹介する文が「built-in」「standard」の語を使い続け、読み手は doeff の核に特別な handler が在ると読む。")
     (interpretation
       "package の handler は doeff の利用者が書く handler と同じ形(`@do` の関数・`handler(raw)`)で、program に呼んで積んだ時だけ働く。だから文書が言えるのは「この package がこの effect の参考実装の handler を持つ」までで、「doeff に入っている」「標準の」とは言えない。")
     (interpretation
       "doeff run の CLI が marker の無い時に使う interpreter(`doeff.cli.run_services:default_interpreter`)も、参考実装の handler を積む 1 つの関数で、特別な handler ではない。文書は「fallback interpreter」と書く。")]
  :decision
    [(rule R1 "doeff に組み込みの handler・特別な handler は無い。doeff の package(doeff-core-effects ほか)の handler は参考実装で、どれも利用者の handler に替えられる。")
     (rule R2 "文書は、handler・effect・scheduler・interpreter を組み込みの物(built-in)・標準の物(standard・core・stock・default)・特別な物(special)・同梱の便利な物(batteries included)として書かない。「参考実装(reference implementation)」「package が持つ handler」と書く。Python の組み込みの関数・型(builtins)の話はこの外。")
     (rule R3 "R2 を当てる読みの範囲は、入口の file(README.md・CONTRIBUTING.md)・docs の site に入る page(mkdocs.yml の exclude_docs の許す一覧)・package の README・doeff と packages の code の docstring と注釈(試験・例・試験の材料・測りの dir は除く)。開発の記録(docs/history・docs/design・docs/adr ほか site の外の docs)は書いた時の記録として残し、直さない。")
     (rule R4 "書くその場面の助言は doeff の外(agent-commons の defjevrule doeff-handlers-are-reference-implementations)が持つ。doeff の側には R3 の範囲を検める deftest を置く。")]
  :laws
    [(law handlers-are-written-as-reference-implementations
       :statement "∀ file ∈ reading-scope(repo の file, mkdocs.yml). wording-findings(file, 文) = []"
       :counterexamples
         [(counterexample "README の handler の表に「the built-in scheduler, `scheduled(...)`」と書く — 読み手は scheduler が doeff の核の一部で、替えられないと読む")
          (counterexample "package の README の頭を「The standard effects and handlers for doeff」と書く — package の handler が doeff の標準で、利用者の handler より格が上だと読める")
          (counterexample "docstring に「Built-in handlers for notification effects」と書く — API の文書の生成物にも同じ言い方が出る")]
       :enforced-by ["docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-reading-scope-writes-handlers-as-reference-implementations"
                     "docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-special-handler-wording-is-named"
                     "docs/adr/defadr_doeff_docs_001_handlers_are_reference_implementations.hy::test-adr-doe-docs-001-reading-scope-covers-the-published-files"]
       :wiring "配線済み(2026-10-26)— この file のテスト。書く場面の助言は agent-commons の defjevrule(R4)")]
  :enforcement
    [(deftest test-adr-doe-docs-001-reading-scope-writes-handlers-as-reference-implementations
       ;; 今の木の読みの範囲に、handler を組み込み・標準・特別な物として書いた所が無い(R2・R3)。
       (val tracked (.split (. (subprocess.run ["git" "-C" (str REPO-ROOT) "ls-files"] :capture-output True :text True :check True) stdout)))
       (val scope (run (reading-scope tracked (.read-text (/ REPO-ROOT "mkdocs.yml") :encoding "utf-8"))))
       (val findings (lfor path scope
                           :if (.exists (/ REPO-ROOT path))
                           found (run (wording-findings path (.read-text (/ REPO-ROOT path) :encoding "utf-8" :errors "replace")))
                           found))
       (assert (= findings [])
               (+ "handler を組み込み・標準・特別な物として書いた所が在る(ADR-DOE-DOCS-001 R2 — package の handler は参考実装と書く): "
                  (str findings))))
     (deftest test-adr-doe-docs-001-special-handler-wording-is-named
       ;; 反例の言い方を名指して断り、参考実装の言い方と Python の組み込みの話は通す(R2)。
       (assert (= (run (wording-findings "README.md" "| holds it | the built-in scheduler, `scheduled(...)` |"))
                  ["README.md:1: built-in scheduler"]))
       (assert (= (run (wording-findings "x.md" "a\nThe standard effects and handlers for doeff.\nBuilt-In Handlers\n"))
                  ["x.md:2: standard effects" "x.md:3: Built-In Handlers"]))
       (assert (= (run (wording-findings "x.py" "\"\"\"Core handlers — reader, state, writer.\"\"\""))
                  ["x.py:1: Core handlers"]))
       (assert (= (run (wording-findings "x.md" "- **Batteries included.**\nthe stock scheduler\nthe default handlers"))
                  ["x.md:1: Batteries included" "x.md:2: stock scheduler" "x.md:3: default handlers"]))
       (assert (= (run (wording-findings "x.md" "組み込みのハンドラ\n標準の handler\n特別なハンドラ"))
                  ["x.md:1: 組み込みのハンドラ" "x.md:2: 標準の handler" "x.md:3: 特別なハンドラ"]))
       (assert (= (run (wording-findings "x.md" (+ "Reference implementations of effects and handlers.\n"
                                                    "doeff-core-effects ships the reference handler `state`.\n"
                                                    "a builtin class, Python's built-in functions, `doeff_core_effects`\n"
                                                    "the fallback interpreter")))
                  [])))
     (deftest test-adr-doe-docs-001-reading-scope-covers-the-published-files
       ;; 読みの範囲が、入口の file・site の page・package の README・code を持ち、試験と開発の記録を持たない(R3)。
       (val tracked ["README.md" "CONTRIBUTING.md" "docs/index.md" "docs/history/specs/x.md" "docs/design/y.md"
                     "packages/doeff-notify/README.md" "packages/doeff-notify/src/doeff_notify/_handlers/__init__.py"
                     "packages/doeff-notify/tests/test_handlers.py" "doeff/program.py" "doeff/cli/run_services.py"
                     "packages/doeff-vm/src/pyvm.rs" "examples/a.py" "packages/doeff-x/examples/b.py"])
       (val scope (run (reading-scope tracked "exclude_docs: |\n  *\n  !/index.md\n  !/missing.md\nnav:\n")))
       (assert (= scope ["README.md" "CONTRIBUTING.md" "docs/index.md" "packages/doeff-notify/README.md"
                         "packages/doeff-notify/src/doeff_notify/_handlers/__init__.py" "doeff/program.py"
                         "doeff/cli/run_services.py" "packages/doeff-vm/src/pyvm.rs"])))])
