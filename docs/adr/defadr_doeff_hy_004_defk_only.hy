;;; Executable ADR: the function vocabulary is defk only — defn is forbidden (including at the Python boundary;
;;; the only exception is functions called at macro-expansion time), and deff is allowed only as an escape hatch with a
;;; "cannot be defk" reason comment and :tags. deff without a reason comment is made to decrease monotonically via a frozen
;;; ledger (ratchet). :tags is required in the contract dict of defk / deff.
;;;
;;; The "reason comment" is `; <reason marker>: <reason>` on the definition line. The reason marker is DEFF-REASON-MARK below,
;;; which equals doeff-linter's `definitions.deff_reason_marker` (a Japanese phrase meaning "cannot be defk").
;;;
;;; First version (2026-08-21): forbid all deff via defadr and use only defk.
;;; Revision (2026-09-27): forbid defn and allow only defk, allowing deff only where unavoidable
;;;   — to force definitions to carry tags.
;;;   (R4 "delete the deff macro once the ledger is empty" is withdrawn — deff remains as the escape hatch where unavoidable)
;;;
;;; Addendum (2026-09-27; what was decided is in the :problem facts): reading settings, check helpers, and framework and
;;;   process entry points are not to be reasons for deff. Acceptance of a deff reason is judged by doeff-linter's DOEFF203 (Jev) (R6-R9).
;;;
;;; Rollback: revert this revision's commit (the ADR's clauses, probe, and ledger counting return to the 2026-08-21 version).
;;;
;;; Two coexisting vocabularies split the calling convention (direct call vs <- bind), and agent authors copying
;;; existing in-file practice keep reproducing deff — it does not converge without a forbidding law and
;;; a mechanical probe (measured: the W1b/W2 batches themselves added 4 new deffs).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import os)
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Live probe — demonstration of the migration recipe: pure validation logic can be written as-is in the degenerate
;; form of defk (zero binds) and runs directly under run with zero handlers. No shape exists that can only be
;; written as deff (executable evidence that there is no expressiveness counterexample).
;; ---------------------------------------------------------------------------

(defk probe-pure-validator [value]
  {:pre [(: value str)]
   :post [(: % bool)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  (and (> (len value) 0) (not (.startswith value "/"))))


;; ---------------------------------------------------------------------------
;; ratchet ledger — measured as of 2026-08-21 (23 files, 227 definitions). Per file, the probe enforces
;; "current count <= ledger count": a new one is always red, a reduction is green (the ledger is
;; trimmed by the conversion batch in the same batch — R3). deff in a file not listed here must be 0.
;; Measurement is textual (regex) — it also counts "open paren + deff + whitespace" inside comments and strings
;; (the ratchet stays consistent as long as the ledger and the probe use the same yardstick).
;; Since the 2026-09-27 revision, a deff with a "; <reason marker>: <reason>" comment on the same line is
;; not counted (R1's escape hatch — a deff that names its reason is allowed outside the ledger). The ledger counts
;; did not change because at the time of the revision 0 deffs had a reason comment.
;; 2026-10-01: "the same line" means the deff's definition lines = from `(deff` to the line where the argument list closes
;; (the same range in which doeff-linter's DOEFF111 looks for the comment). A deff whose arguments are folded over several lines puts the comment on the line where the list closes,
;; so looking only at the first `(deff` line counted deffs that had a comment (6 definitions in doeff-cluster; red in the daily root run).
;; A comment-only line just before is still counted as before (the linter accepts it too, but this ledger is limited to the definition lines).
;; ---------------------------------------------------------------------------

(setv DEFF-ROSTER
  {"docs/adr/defadr_doeff_hy_003_bang_evaluation_position.hy" 1})

;; Trees excluded from the scan: temporary copies (worktree/scratchpad), build outputs, environments,
;; packages/doeff-hy (the macro owner — the deff definition and its own tests verifying its
;; semantics. The deff macro remains under R4's revision, so the text there is legitimate).
(setv SCAN-SKIP-PARTS
  #{".git" ".venv" ".claude" ".worktrees" "__pycache__" "node_modules"
    "dist" ".mypy_cache" ".pytest_cache" "scratchpad"})

(setv DEFF-REASON-MARK "defk \u306b\u3067\u304d\u306a\u3044")
(setv DEFF-HEAD-PATTERN (re.compile r"\(deff\s"))
;; Tokens used when counting brackets of the argument list: a string or a comment is one token (brackets inside are not counted); brackets are one character each.
(setv DEFF-HEAD-TOKEN (re.compile r"\"(?:\\.|[^\"\\])*\"|;[^\n]*|[\[\]]"))

(defk deff-head [text start]
  {:pre [(: text str) (: start int)]
   :post [(: % str)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "To cut out a deff's definition lines — from `(deff` to the end of the line where the argument list closes (the same range as the
   'definition lines (from the head to the end of the arguments)' in which doeff-linter's DOEFF111 looks for the comment. A deff with many arguments folds the list over several lines and puts the comment on the line where the list closes).
   When the argument list does not open on the `(deff` line (the text '(deff' inside a comment or prose), only that one line."
  (val newline (.find text "\n" start))
  (val line-end (if (= newline -1) (len text) newline))
  (val open (.find text "[" start line-end))
  (var close None)
  (var depth 0)
  (when (!= open -1)
    (for [m (.finditer DEFF-HEAD-TOKEN text open)]
      (:= depth (+ depth (match (.group m) "[" 1 "]" -1 _ 0)))
      (when (= depth 0)
        (:= close (.start m))
        (break))))
  (val head-end (if (is close None) line-end (.find text "\n" close)))
  (cut text start (if (= head-end -1) (len text) head-end)))

(defk count-unexcused-deff [text]
  {:pre [(: text str)]
   :post [(: % int)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "The number of deff definitions without a reason comment (the reason marker on the definition lines) — the ledger's yardstick."
  (var n 0)
  (for [m (.finditer DEFF-HEAD-PATTERN text)]
    (<- head (deff-head text (.start m)))
    (when (not-in DEFF-REASON-MARK head)
      (:= n (+ n 1))))
  n)

(defk scan-deff-counts [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % dict)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Count, per file, the deff definitions without a reason comment in the repo's .hy files (the yardstick shared by the probe and the ledger)."
  (setv counts {})
  (for [p (sorted (.rglob repo-root "*.hy"))]
    (setv rel (str (.relative-to p repo-root)))
    (when (or (& (set (. (.relative-to p repo-root) parts)) SCAN-SKIP-PARTS)
              (.startswith rel "packages/doeff-hy/"))
      (continue))
    (<- n (count-unexcused-deff
            (.read-text p :encoding "utf-8" :errors "replace")))
    (when (> n 0)
      (setv (get counts rel) n)))
  counts)


(defadr ADR-DOE-HY-004
  :title "The function vocabulary is defk only: defn is forbidden (including at the Python boundary; the only exception is functions called at macro-expansion time), deff is allowed only as an escape hatch where defk is impossible, with a same-line reason comment and :tags, and :tags is required in the contract dict of defk / deff. Existing deff without a reason comment is decreased monotonically via a frozen-ledger ratchet, and conversion ships in one batch including the calling convention (<- bind), trimming the ledger in the same batch — coexisting vocabularies self-propagate by copying in-file practice, and definitions that cannot carry tags cannot be listed and browsed, so it does not converge without a law and a probe"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_004_defk_only.hy"
          "docs/adr"
          "tests"
          "packages/doeff-linter"]
  :problem
    [(fact
       "doeff-hy has two vocabularies for contracted functions: deff (plain function, direct call) and defk (kleisli program, composed with <- bind). They share the same :pre/:post contract surface, yet only the calling convention is split."
       :evidence "packages/doeff-hy/src/doeff_hy/macros.hy:447(deff)/ :498(defk)")
     (fact
       "Policy of the first version (2026-08-21): forbid all deff via defadr and use only defk.")
     (fact
       "Measured 2026-08-21: the doeff repo has 23 files and 227 deff definitions (packages 219 / docs-adr 5 / tests 3). Downstream agent-control-plane apps have 0 deff and 3,065 defk — the converged end state is already proven in production downstream."
       :evidence "This ADR's DEFF-ROSTER (a frozen snapshot under identical scan conditions)")
     (fact
       "Left alone, the two vocabularies self-propagate: the W1b/W2 batches of 2026-08-20 to 21 themselves copied existing in-file practice (the admission group and effect constructor group were deff) and added 4 new deffs. The authors are agents, and copying local practice is their default behavior."
       :evidence "doeff 92dc4fbb (admit-context-file) / 84ada9b2 (admit-workspace-seed, git-run, and others)")
     (fact
       "A deff->defk conversion is not complete by rewriting the definition alone: the call changes from a direct call to a <- bind, so the caller itself must be a program, and the conversion cascades up the call tree. A mechanical bulk replacement breaks."
       :evidence "defk expansion = @do generator (macros.hy:498-) — a direct call only returns a Program value and does not execute")
     (fact
       "Policy of the revision (2026-09-27): forbid defn and allow only defk, allowing deff only where unavoidable — to force definitions to carry tags. Background: directories are split service -> layer, so that the VS Code navigator can list components by tag.")
     (fact
       "The contract dict of defk / deff / defp / defhandler accepts :tags {:context … :role …} and keeps it in the definition's attribute __doeff_tags__. defn has no contract dict, so it cannot carry :tags and does not appear in browsing that lists definitions by tag (the VS Code panel)."
       :evidence "packages/doeff-hy/src/doeff_hy/_declarations.hy (CONTRACT-KEYS, TAG-KEYS, ROLES)")
     (fact
       "Measured 2026-09-27: the .hy files under packages, docs, and tests of the doeff repo contain 269 files and 2,135 defn / defn/a definitions (including the macro implementation in packages/doeff-hy). R1 of the 2026-08-21 version excluded defn at the Python boundary."
       :evidence "grep -rEc '\\(defn(/a)?\\s' --include='*.hy' packages docs tests")
     (fact
       "Policy of the addendum (2026-09-27): reading settings is a job for an effect such as Ask and is not a reason for deff. Checks that are not deftest are forbidden. doeff-cluster's process entry points are reshaped so they do not require non-defk functions, and doeff-cluster's job API is discussed separately. Whether the text of a deff reason is accepted is judged by Jev.")
     (fact
       "On agora-controllers main d99194b4 there are 682 reason-marker comments, with an estimated breakdown of check helpers 166, assembly 127, other 112, process entry points 82, 'ditto' 81, external library callbacks 71, framework entry points 43. Cases that truly need a plain callable were not distinguished from cases where making the caller a Program suffices (assembly = handlers-of, checks = deftest)."
       :evidence "commit message of doeff 683dd0e8 (wt/hy-reason-kinds, doeff-linter's DOEFF111 / DOEFF203)")]
  :context
    [(interpretation
       "With one vocabulary there is one calling convention, and the room for agent authors to err disappears structurally. Pure logic can be written as-is in defk's degenerate form (zero binds) and runs under run with zero handlers — there is no shape that can only be written as deff, so the cost of unification is only the migration, with no loss of expressiveness.")
     (interpretation
       "A big-bang conversion is dangerous because of the calling-convention cascade. The ratchet (new ones immediately red via the probe, existing ones frozen in the ledger, the conversion batch trims the ledger in the same batch) is the only shape that achieves both zero regressions and gradual burn-down.")
     (interpretation
       "The 2026-08-21 version read the end state of 'disallow' as deleting the deff macro and put it in R4. The 2026-09-27 decision revises this: external library callbacks, boundaries called synchronously from Python, and framework conventions such as pytest fixtures require a plain callable, not a Program, so they cannot be defk. Using defn there leaves definitions that cannot carry tags. deff is a plain callable yet can have a contract dict (including :tags), so deff is kept as the escape hatch where unavoidable, and defn is the one removed. The focus of the ban moves from 'the deff vocabulary' to 'definitions that cannot carry tags (defn) and deff that does not name its reason'.")
     (interpretation
       "The source of truth for judgment is doeff-linter (the defn ban, the deff reason comment, required :tags). This ADR's probe (the DEFF-ROSTER ratchet) holds only the count of deff without a reason comment and does not double-count with the linter rules. Until the linter rules land on main, the corresponding laws are explicitly marked unwired (no enforcement).")]
  :decision
    [(rule R1 "Functions are written with defk (repo-wide — production, tests, docs/adr; the only exclusion is the macro owner packages/doeff-hy). Handlers are defhandler, test bodies are deftest, entry points are defp. defn / defn/a are forbidden, including at the Python boundary (interop). The only exception is functions called at macro-expansion time (inside eval-and-compile / eval-when-compile). Where a plain callable is needed (external library callbacks, boundaries called synchronously from Python, framework conventions such as pytest fixtures), use deff as the escape hatch and write '; <reason marker>: <reason>' on the same line. The 2026-08-21 version's 'plain defn at the Python interop boundary is out of scope' is withdrawn (2026-09-27 revision).")
     (rule R1b "The contract dict of defk and deff requires :tags {:context <context> :role <role>}. The lists of keys and roles follow doeff-hy's declarations (TAG-KEYS, ROLES) and each repo's doeff-linter settings (added 2026-09-27).")
     (rule R2 "Existing deff without a reason comment (227 definitions as of 2026-08-21) is frozen in DEFF-ROSTER. The probe enforces current count <= ledger count per file, and enforces 0 deff without a reason comment in files outside the ledger — any new addition or relocation is red. The ledger moves only in the direction of 'reducing things that can be defk'.")
     (rule R3 "A conversion (burn-down) ships, in one batch, the definition's change to defk, the change of every call site to <- bind, and the reduction of the corresponding DEFF-ROSTER line. The ledger decrease and the actual deletion are always in sync.")
     (rule R4 "[Withdrawn 2026-09-27] Old text: once DEFF-ROSTER is empty, delete the deff macro itself and its semantics tests from packages/doeff-hy. After revision: the deff macro remains as R1's escape hatch. Even when DEFF-ROSTER is empty the macro is not deleted (empty ledger = the state where deff that does not name its reason is 0).")
     (rule R5 "Increasing a ledger count or adding a new exclusion happens only when the maintainer explicitly approves it. The same applies to changing the probe's scan conditions (SCAN-SKIP-PARTS, the doeff-hy exclusion).")
     (rule R6 "Reading settings or environment (env values, settings files) is not a reason for deff. Reads are written with an effect such as Ask, and the handler answers the value (added 2026-09-27).")
     (rule R7 "Checks (tests) are written only with deftest. Plain pytest test functions and plain check-helper functions (value builders, fixture substitutes) are forbidden; helpers are made defk and received with <- from the deftest body. R1's 'framework conventions such as pytest fixtures' is removed from the examples of deff reasons (added 2026-09-27).")
     (rule R8 "A deff reason (the same-line '; <reason marker>: <reason>') is written as free text. Whether it is accepted is decided by doeff-linter's DOEFF203 (Jev), by checking it against the acceptable reasons declared in that repo's architecture.hy. The presence and shape of the comment are checked by DOEFF111 (added 2026-09-27).")
     (rule R9 "[Revised 2026-09-28] Old text: framework and process entry points (such as the env function of doeff-cluster's job_entry) are not to be reasons for deff. Until the doeff-cluster job API discussion is settled, existing plain entry-point functions are kept in a registry and no new ones are added (added 2026-09-27). After revision: doeff-cluster job entry points are not a reason for deff — a job accepts only a Program (ADR-DOE-CLUSTER-001), so entry-point env functions and functions returning a handler list are made defk and called inside the job's Program. Entry points that a framework calls as plain functions, such as pytest hooks, remain an accepted reason (R10). Origin of the revision = aligning with :plain-callable-reasons (L1443) in agora-controllers' architecture.hy (2026-09-28).")
     (rule R10 "Only 3 kinds are accepted as deff reasons (same as :plain-callable-reasons in agora-controllers' architecture.hy; DOEFF203's Jev checks the reason text against this list): (1) callbacks that an external library calls as plain functions (sorted's key, pydantic validators, playwright handlers, functions passed to with_handlers, thread or process launch points), (2) entry points that a framework calls as plain functions (pytest hooks, the outer shell of fixtures, a console script's main) — not including doeff-cluster job entry points, (3) functions that run at macro-expansion time (meaningful only outside a Program). Rejected kinds (the same 7 as :rejected-plain-callable-reasons, with fixes): assembly (handler lists or foundations — handlers-of, foundation, settings-of -> make it defk and call it inside the job's Program) / reading settings, environment variables, or credential files (-> a defk that reads via an effect such as Ask; R6) / check helpers, check assembly, scenario parts (-> make it defk and call it with <- inside deftest; R7) / it is pure so I want to call it from inside a comprehension or mid-expression (-> make it defk and rewrite the caller with for and <- / !) / speed (-> make it defk, and if slow, lower the cost of defk calls on the doeff side) / preserving the calling style, or other branches or callers call it plainly (-> make it defk and fix the callers with <- / !) / it is called outside doeff execution (a Python entry point receives a handler list, used outside run -> make the caller a Program; job entry points take R9's shape). The source of truth for the list is the repo's architecture.hy, and this clause is its doeff-side copy — do not fix only one side (2026-09-28).")]
  :laws
    [(law defk-only-vocabulary
       :statement "for_all hy_file f in repo \\ {packages/doeff-hy}: count_deff(f) <= DEFF-ROSTER.get(f, 0) — the ledger is monotonically non-increasing, and no new deff can exist"
       :counterexamples
         [(counterexample "2026-08-20, the W1b batch added admit-context-file as a new deff — measured evidence that copying in-file practice reproduces deff (does not converge without a probe)")
          (counterexample "A mechanical bulk deff->defk replacement — direct-call sites only receive a Program value without executing it and silently become no-ops (a conversion ignoring the calling-convention cascade breaks)")]
       :enforced-by ["test-adr-doe-hy-004-deff-ratchet" "test-adr-doe-hy-004-reasoned-deff-is-outside-the-roster"])
     (law defn-is-forbidden
       :statement "for_all hy_file f in repo \\ {packages/doeff-hy}: defn / defn/a definitions are 0 — except inside eval-and-compile / eval-when-compile (functions called at macro-expansion time)"
       :counterexamples
         [(counterexample "Writing a boundary function called synchronously from Python as defn — if a plain callable is needed, write a deff with a reason comment and :tags (defn cannot have a contract dict and cannot be browsed by tag)")
          (counterexample "Writing a small pure function as defn — it can be written in defk's degenerate form (zero binds)")]
       :enforced-by ["doeff-linter DOEFF110"]
       :wiring "Unwired (2026-09-27) — DOEFF110 is being added to doeff-linter by another owner and has not landed on main. Until it lands there is no probe that mechanically checks this law")
     (law deff-names-its-reason
       :statement "for_all deff definition d: the definition lines (from `(deff` to the line where the argument list closes — the same range as doeff-linter's DOEFF111) contain '; <reason marker>: <reason>', or d is counted in DEFF-ROSTER's frozen portion"
       :counterexamples
         [(counterexample "Adding an external library callback as a new deff without a reason comment — a deff that does not name its reason cannot be told apart from something that could be defk")]
       :enforced-by ["doeff-linter DOEFF111" "test-adr-doe-hy-004-deff-ratchet"]
       :wiring "Partially wired (2026-09-27) — the ledger ratchet (this ADR's deftest) turns an increase in deff without a reason comment red. The per-definition judgment DOEFF111 has not landed in doeff-linter")
     (law definitions-carry-tags
       :statement "for_all defk / deff definition d: d's contract dict has :tags {:context … :role …}"
       :counterexamples
         [(counterexample "A defk whose contract dict has only :pre / :post — it does not appear in browsing that lists by tag")]
       :enforced-by ["doeff-linter DOEFF112"]
       :wiring "Unwired (2026-09-27) — DOEFF112 has not landed in doeff-linter. Most existing defk do not yet have :tags")
     (law deff-reason-is-accepted-by-jev
       :statement "for_all deff definition d: d has a reason comment ∧ DOEFF203 (Jev) judges d's reason to be one of the acceptable reasons in architecture.hy. Reading settings or environment, check helpers, and assembly are not acceptable reasons"
       :counterexamples
         [(counterexample "Writing a function that reads an env value as a deff with the reason 'because it reads settings' — the read can be written with the Ask effect (reading settings is not a reason for deff — R6)")
          (counterexample "Writing a helper that builds values for a deftest as a deff with the reason 'check helper' — the helper is made defk and received with <- from the deftest")
          (counterexample "Writing an assembly that lists a set of handlers as deff — assembly can be written as defk (handlers-of)")]
       :enforced-by ["doeff-linter DOEFF111" "doeff-linter DOEFF203"]
       :wiring "Unwired (2026-09-27) — the new shape of DOEFF111 and DOEFF203 are in doeff-linter's wt/hy-reason-kinds and have not landed on main")
     (law checks-are-deftest-only
       :statement "for_all check t: t is written with deftest — plain test functions and plain check-helper functions are 0"
       :counterexamples
         [(counterexample "Checking a doeff Program with a plain pytest test function (def test_… / defn test-…)")]
       :enforced-by ["doeff-linter DOEFF118"]
       :wiring "Unwired (2026-09-27) — DOEFF118 has not landed in doeff-linter (the number was assigned by the coordinator; the rule is being added by another owner)")]
  :enforcement
    [(deftest test-adr-doe-hy-004-deff-ratchet
       ;; Probe: measurement = scan-deff-counts, law = per-file comparison against DEFF-ROSTER.
       (setv repo-root (. (Path __file__) parent parent parent))
       (setv counts (run (scan-deff-counts repo-root)))
       (setv violations [])
       (for [[rel n] (sorted (.items counts))]
         (setv allowed (.get DEFF-ROSTER rel 0))
         (when (> n allowed)
           (.append violations f"{rel}: {n} > ledger {allowed}")))
       (assert (= violations [])
               (+ "New deff definitions are forbidden (ADR-DOE-HY-004 R1 — write defk. "
                  "Conversions of existing ones trim the ledger in the same batch via R3's one-batch shipping): "
                  (str violations)))
       ;; Stale-ledger detection: a file in the ledger whose actual count dropped below the ledger
       ;; means the ledger trim was forgotten (an R3 sync violation) — make it red, not a warning (a stale ledger
       ;; hides the next new one in that file).
       (setv stale [])
       (for [[rel allowed] (sorted (.items DEFF-ROSTER))]
         (setv n (.get counts rel 0))
         (when (< n allowed)
           (.append stale f"{rel}: actual {n} < ledger {allowed}")))
       (assert (= stale [])
               (+ "Ledger trim forgotten (ADR-DOE-HY-004 R3 — a conversion batch trims DEFF-ROSTER "
                  "in the same batch): " (str stale))))
     (deftest test-adr-doe-hy-004-reasoned-deff-is-outside-the-roster
       ;; R1's escape hatch: a deff with a reason comment on its definition lines (from `(deff` to the line where the argument list closes) is not counted by the ledger's yardstick.
       ;; A deff without a comment, and a deff whose comment is outside the definition lines (the line before, contract or body lines), are counted.
       (setv reasoned (+ "(" "deff on-sort-key [row]  ; defk \u306b\u3067\u304d\u306a\u3044: sorted's key does not run a Program\n"))
       (setv bare (+ "(" "deff on-sort-key [row]\n"))
       (setv detached (+ "; defk \u306b\u3067\u304d\u306a\u3044: a comment on another line\n(" "deff on-sort-key [row]\n"))
       (assert (= (run (count-unexcused-deff reasoned)) 0))
       (assert (= (run (count-unexcused-deff bare)) 1))
       (assert (= (run (count-unexcused-deff detached)) 1))
       ;; A deff with a folded argument list: a comment on the line where the list closes is a definition-line comment (the shape of the 6 doeff-cluster definitions).
       (setv folded (+ "(" "deff on-sort-key [#^ str key\n"
                       "                  #^ int rank]  ; defk \u306b\u3067\u304d\u306a\u3044: sorted's key does not run a Program\n"
                       "  {:pre [(: key str)] :post [(: % str)]}\n  key)\n"))
       (assert (= (run (count-unexcused-deff folded)) 0))
       ;; A comment on a line after the list closes (contract dict, body) is outside the definition lines.
       (setv late (+ "(" "deff on-sort-key [#^ str key\n                  #^ int rank]\n"
                     "  {:pre [(: key str)]}  ; defk \u306b\u3067\u304d\u306a\u3044: a line after the list\n  key)\n"))
       (assert (= (run (count-unexcused-deff late)) 1))
       ;; Brackets inside strings and comments do not count toward closing the list — a default value "]" does not cut the head early.
       (setv quoted (+ "(" "deff on-sort-key [#^ str [key \"]\"]\n"
                       "                  #^ int rank]  ; defk \u306b\u3067\u304d\u306a\u3044: sorted's key does not run a Program\n  key)\n"))
       (assert (= (run (count-unexcused-deff quoted)) 0))
       ;; Text where the list does not open (the text '(deff' inside comment prose) looks at that one line only — it does not pick up the comment on the next line.
       (setv prose (+ ";; old (" "deff call explanation\n(" "deff on-sort-key [row]  ; defk \u306b\u3067\u304d\u306a\u3044: sorted's key\n"))
       (assert (= (run (count-unexcused-deff prose)) 1)))
     (deftest test-adr-doe-hy-004-pure-logic-lives-in-defk
       ;; Demonstration of the migration recipe: pure validation can be written in defk's degenerate form and runs
       ;; directly under run with zero handlers — there is no shape that can only be written as deff.
       (assert (= (run (probe-pure-validator "abc")) True))
       (assert (= (run (probe-pure-validator "/abs")) False))
       (assert (= (run (probe-pure-validator "")) False)))]
  :plans [])
