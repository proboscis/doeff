;;; Executable ADR: the remaining 2 Hy fences —
;;;   Fence 3, the TypeCheck effect: static type checking is always on; runtime checking is
;;;        switched by a handler (off by default, on in checks).
;;;   Fence 5, record-scaffold macros: the macros used by turn-record writers live in doeff-hy
;;;        together with their projection rules; no repo grows its own macros.
;;;
;;; Origin = agora-redesign stage 7 lane 7c (the rest of decision 1.4,
;;; `docs/plans/decisions-merge-2026-09-12.md`). Fence 1 (typed `<-` and defk
;;; signatures) landed in stage 0; fence 2 (ban on `"value"`, Any, object, bare dict / list) and
;;; the lint against I/O imports in the pure layer run in the shared quality checks' hy rules.
;;;
;;; Known shape: algebraic effects (request = value, handler = the place that executes, the composition
;;; root chooses the handler). Fence 3 moves "runtime checking" into that shape;
;;; it adds no new mechanism.
;;;
;;; Vocabulary: this is the "tool" layer. Turn appears as a mechanism term only in the record type name
;;; (TurnRecord) and is not carried into domain language.

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [do run])
(import re)
(import pathlib [Path])

(import doeff_hy [TypeCheckError type-check]
        doeff_hy._typecheck [type-check-verdict])
(import doeff_hy._typecheck_handlers [run-with-type-checks run-without-type-checks])


;; ---------------------------------------------------------------------------
;; Live probes — programs that check boundary values as requests (a demonstration of how fence 3 is used).
;; ---------------------------------------------------------------------------

(defk agreeing-boundary [value]
  {:pre [(: value int)]
   :post [(: % int)]}
  "A program where the boundary promise (int) and the request's expectation (int) agree."
  (<- _ (type-check "agreeing-boundary" "value" int value))
  value)


(defk mismatched-boundary [value]
  {:pre [(: value int)]
   :post [(: % int)]}
  "A program where the boundary promise (int) and the request's expectation (str) disagree.

   The defk contract is \"the promise to the caller\"; the TypeCheck request is a separate axis that looks at
   \"the value actually received at the boundary\" — the mismatch passes when off and goes red when on."
  (<- _ (type-check "mismatched-boundary" "value" str value))
  value)


;; ---------------------------------------------------------------------------
;; Frozen macro ledger — the files allowed to define `defmacro` (measured 2026-09-12).
;;
;; New macros go only into doeff-hy (the fence). Existing owners are frozen; a file outside
;; the roster that holds `defmacro` is red. Adding macros inside doeff-hy is
;; free (that is the home of macros).
;; ---------------------------------------------------------------------------

(setv MACRO-OWNER-ROSTER
  #{"packages/doeff-hy/src/doeff_hy/macros.hy"
    "packages/doeff-hy/src/doeff_hy/handle.hy"
    "packages/doeff-hy/src/doeff_hy/record.hy"
    ;; Move by the R5 ruling (the by-group option):
    ;; doeff-hy keeps the macros for writing effects, handlers, and tests plus the foundation; defsystem, which declares a system
    ;; (formerly doeff_hy/macros.hy), goes to doeff-cluster — its owning package.
    ;; Rollback = delete this line and move the macro back into doeff-hy (fix the users' require at the same pin).
    "packages/doeff-cluster/src/doeff_cluster/_shared/entry/macros.hy"
    "packages/doeff-adr/src/doeff_adr/macros.hy"
    "packages/doeff-domain/src/doeff_domain/macros.hy"
    ;; Addition by the R5 ruling: the event-waiting
    ;; loop macro event-loop (547314ac8) lives in the package of the effect it wraps, WaitForEvent.
    ;; Rollback = delete this line and move event-loop into doeff-hy (fix agora's users' require at the same pin).
    "packages/doeff-events/src/doeff_events/macros.hy"})

;; Trees excluded from the scan (temporary copies, build outputs, environments).
(setv SCAN-SKIP-PARTS
  #{".git" ".venv" ".claude" ".worktrees" "__pycache__" "node_modules"
    "dist" ".mypy_cache" ".pytest_cache" "scratchpad" "attic"})


(defn #^ tuple macro-owning-files [#^ Path repo-root]
  "The list of .hy files that hold `defmacro` (the shared yardstick for the probe and the ledger)."
  (setv found [])
  (for [source (sorted (.rglob repo-root "*.hy"))]
    (setv rel (.relative-to source repo-root))
    (when (& (set rel.parts) SCAN-SKIP-PARTS)
      (continue))
    ;; Only the line-leading form counts as a definition — "(defmacro …)" inside a check's sample string is not a definition
    (when (re.search r"(?m)^\s*\(defmacro\s"
                     (.read-text source :encoding "utf-8" :errors "replace"))
      (.append found (str rel))))
  (tuple found))


(defadr ADR-DOE-HY-005
  :title "The remaining 2 Hy fences: runtime type checking is a TypeCheck request switched by a handler (off by default; static is always on). Record-scaffold macros live in doeff-hy together with their projection rules; no repo grows its own macros (existing owners are in a frozen ledger)"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_hy_005_typecheck_and_record_fences.hy"
          "packages/doeff-hy/src/doeff_hy/_typecheck.hy"
          "packages/doeff-hy/src/doeff_hy/_typecheck_handlers.hy"
          "packages/doeff-hy/src/doeff_hy/record.hy"]
  :problem
    [(fact
       "In agora-redesign decision 1.4, the remaining 4 of the 7 Hy fences are: the TypeCheck effect (static always on, runtime checking switched by a handler), record-scaffold macros, not placing read-side projections in operator, and placing new macros in doeff-hy together with their projection rules."
       :evidence "agora-redesign docs/plans/decisions-merge-2026-09-12.md 1.4")
     (fact
       "defk / deff :pre / :post expand into unconditional assert (isinstance …). With no switch, types that are statically known are paid for on the hot path every time."
       :evidence "_expand-check in packages/doeff-hy/src/doeff_hy/macros.hy")
     (fact
       "When a turn-record writer builds a row as a bare dict, it breaks fence 2 (ban on `\"value\"`, Any, object, bare dict / list), and neither misspelled fields nor wrong types are visible until runtime."
       :evidence "Fence 2 of decision 1.4 (running in the hy rules of the shared quality checks agent/quality)")
     (fact
       "When macros grow per repo, static checking does not know their meaning and goes blind. The shared quality checks do not execute macros, so a macro without projection rules (how to read it) on the checker side falls outside checking as 'unsupported'."
       :evidence "The DOEFF_MACROS fixed table and declaration_import in ~/dotfiles/agent/quality/hy_projection.py")
     (fact
       "Measured 2026-09-12: 7 .hy files in doeff hold `defmacro` (doeff-hy 4 / doeff-adr 1 / doeff-domain 1 / doeff-docker 1)."
       :evidence "This ADR's MACRO-OWNER-ROSTER (a frozen snapshot under the same scan conditions)")]
  :context
    [(interpretation
       "Type checking splits into two layers. Static checking projects the source and always applies; it should not get a switch (a check that can be switched off will be switched off). Runtime checking looks at the values themselves and is needed only at boundaries invisible to static checking (values from outside, rows read from a ledger) — so it is issued as a request and the handler decides whether to perform it.")
     (interpretation
       "The reason for off by default is meaning, not speed. Re-checking at runtime on a surface where static checking always applies just says the same thing twice and gives red two sources. Having only checks turn it on makes 'which boundaries are invisible to static checking' explicit on the check side.")
     (interpretation
       "The record scaffold holds only the \"shape\", not the \"content\". The source of truth for turn-record fields is the ACP contract (docs/contracts); all doeff-hy may know is the shape 'build a frozen record that declares field names and types'. Writing the fields here would create two sources of truth.")
     (interpretation
       "The reason to gather macros in doeff-hy is the reach of checking, not house style. As long as a macro and its projection rules ship 1:1 in the same change, a using repo passes static checking without writing a single declaration. If each repo grows its own macros, every time it brings the effort of declaring macros in that repo's contract and the limit on how far the checker can verify the actual shape.")]
  :decision
    [(rule R1 "Runtime type checking is issued as the TypeCheck request of `doeff_hy._typecheck`. There are 2 handlers that perform it (`type-checks-off` / `type-checks-on`), and the composition root chooses which to install. The default is off.")
     (rule R2 "Do not create a switch for static type checking. Hy surfaces are registered in the shared quality checks' module contract with `typed: true`, keeping projection and pyright always applied.")
     (rule R3 "The single definition point of the red message (which type was expected for what, and what it was) is `type-check-verdict`. Do not embed the message inside handlers.")
     (rule R4 "Record-scaffold macros are owned by `doeff_hy.record` and ship in the same change as the shared quality checks' projection rules (`quality.hy_record`). Record fields (content) are not written here.")
     (rule R5 "New macros go only into doeff-hy. A file outside MACRO-OWNER-ROSTER holding `defmacro` is red. The roster grows only when the administrator rules so.")]
  :laws
    [(law runtime-type-checks-are-off-by-default
       :statement "for_all program p with a broken TypeCheck: run(type_checks_off, p) returns the value unchanged and run(type_checks_on, p) raises TypeCheckError"
       :counterexamples
         [(counterexample "The default handler looks at values — statically known types are paid twice on the hot path, and red has two sources, static and runtime")
          (counterexample "The on handler does not look at values (passes the request through) — checks can claim 'the boundary was verified' while nothing was verified (an empty check)")])
     ;; The module contract of the shared quality checks is kept outside this repository together with the other private
     ;; tool settings (2026-11-05), so the probe of this law (the fence modules are in the contract exactly once with
     ;; typed: true) runs beside the contract, not in this repository's tests.
     (law static-type-check-is-always-on
       :statement "for_all hy module m in doeff_hy fences: contract(m).typed = true — static checking has no switch"
       :counterexamples
         [(counterexample "A fence module is registered with typed: false — with runtime already off, static checking is dropped too and no type guarantee remains")])
     (law new-macros-live-only-in-doeff-hy
       :statement "for_all hy_file f with defmacro: f in MACRO-OWNER-ROSTER — no macro grows outside the roster"
       :counterexamples
         [(counterexample "A module inside a repo defines its own macro — the shared quality checks do not execute macros, so users go blind until macros are declared in that repo's contract (the reach of checking silently drops)")])]
  :enforcement
    [(deftest test-adr-doe-hy-005-runtime-type-check-is-switched-by-the-handler
       ;; Probe: run the same program under 2 handlers; the default (off) passes through, checks (on) go red.
       (assert (= (run-without-type-checks (agreeing-boundary 7)) 7))
       (assert (= (run-without-type-checks (mismatched-boundary 7)) 7)
               "The default handler is looking at values (ADR-DOE-HY-005 R1 — default is off)")
       (assert (= (run-with-type-checks (agreeing-boundary 7)) 7))
       (setv raised None)
       (try
         (run-with-type-checks (mismatched-boundary 7))
         (except [error TypeCheckError] (setv raised error)))
       (assert (is-not raised None)
               "The check handler is not looking at values (ADR-DOE-HY-005 R1 — on in checks)")
       (assert (in "mismatched-boundary" (str raised)))
       (assert (in "value" (str raised))))
     (deftest test-adr-doe-hy-005-verdict-is-the-single-definition-point
       ;; Probe: the message has one definition point (the same judgment can be fired without going through a handler).
       (assert (is (type-check-verdict "owner" "x" str "ok") None))
       (setv verdict (type-check-verdict "owner" "x" str 7))
       (assert (is-not verdict None))
       (assert (in "owner" verdict))
       (assert (in "int" verdict)))
     (deftest test-adr-doe-hy-005-macros-live-only-in-doeff-hy
       ;; Probe: measured = macro-owning-files, law = equality with MACRO-OWNER-ROSTER.
       (setv repo-root (. (Path __file__) parent parent parent))
       (setv found (macro-owning-files repo-root))
       (setv strays (sorted (lfor rel found :if (not-in rel MACRO-OWNER-ROSTER) rel)))
       (assert (= strays [])
               (+ "A file outside the roster defines a macro (ADR-DOE-HY-005 R5 — "
                  "new macros go into doeff-hy together with their projection rules): " (str strays)))
       (setv stale (sorted (lfor rel MACRO-OWNER-ROSTER :if (not-in rel found) rel)))
       (assert (= stale [])
               (+ "Stale ledger entries (ADR-DOE-HY-005 R5 — a change that deletes a macro removes it from the roster"
                  " in the same change): " (str stale))))]
  :plans [])
