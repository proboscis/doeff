;;; Table of clause operations in defhandler / handle (resume, transfer, finish, reperform, pass, raise) -- the sole source of definition for operation names and properties.
;;;
;;; What one operation row (ClauseOp) holds:
;;;   name          the head name written inside a clause
;;;   ending        what follows after the clause ends (Ending) -- checked against the effect's resumption declaration (clause_endings.hy)
;;;   arity         number of arguments of the rewritten form (ignored for operations whose rewrite is None)
;;;   returns       whether control returns to the clause when the continuation finishes (resume) -- after rewriting an operation that returns, handle.hy sets the "resumed" mark
;;;                 (a run-time check. Only handle.hy knows the mark name; the row's rewrite does not)
;;;   tail          name of the operation to substitute at the clause tail position (resume -> transfer -- leaves no handler frame). None means no substitution
;;;   nodes         names of the nodes the run-time expansion imports from doeff at the head of the handler (bundled under the alias `_doeff_<name>` -- so as not to
;;;                 hide an effect of the same name the author wrote; doeff_hy/_expansion_names.py. The spelling rewrite reads also pulls the same alias via alias-symbol)
;;;   static-nodes  names of the nodes the type-check expansion (doeff_hy/_static_view.py) imports from doeff at the head of the handler (likewise aliased)
;;;   rewrite       (a function that rewrites the inside of the rewrite argument list) -> the form handed to the VM. None leaves it as written (Python raise)
;;;
;;; Where the table is read -- do not copy the list of operation names outside this table. When _terminates in handle.hy and ENDING-OPS / RESUMING-OPS in clause_endings.hy were
;;; copies, adding a new operation only to _terminates and the rewrite meant none of the checks -- expansion, handle evaluation, or run time -- stopped that operation in a Raise clause
;;; (failure example: in (Raise [reason] (throw …)) the continuation of the Raise ran).
;;;   handle.hy          _terminates (do all paths of the clause end), _rewrite-ops (rewrite and the marks of operations that return), _tail-resume-to-transfer
;;;                      (tail substitution), _vm-imports (node imports)
;;;   clause_endings.hy  ops-in / clause-ops (the operations a clause uses), check-clause-by-name / check-clause-endings (matching per ending)
;;;   doeff-linter       the judgment of clauses in business_fakes.rs (DOEFF143 etc.) -- Rust cannot evaluate Hy, so only the name, ending, and arity from the table are
;;;                      written out by linter-table-json to packages/doeff-linter/data/clause_ops.json, which is read with include_str!.
;;;                      The file is written by scripts/export_clause_ops.py --write, and tests/test_linter_clause_ops.py turns any mismatch with the table red.
;;; No module of doeff_hy writes an operation name outside the table as a string or quoted symbol (tests/test_handle_clause_ops.py picks up all
;;; modules -- a new reader that copies the set of names does not follow additions to or retirements from the table).
;;; The same goes for doeff-linter -- business_fakes.rs does not write operation names as literals (that file's check picks them up).
;;;
;;; Before adding a row (operation names are reserved as head names in clause bodies): if an author calls a function of the same name in a clause body (including inside a nested fn),
;;; the call is silently rewritten into the operation the moment the row is added (adding a `throw` row turned the author's calls to their function `throw` into ResumeThrow,
;;; and the checking function became a generator and the check disappeared). Collisions of node names are
;;; prevented by the aliases in expansion_names.py, but operation names are outside that mechanism -- a mechanical check cannot stop it (calls before and after adding cannot be told apart from the source),
;;; so before adding, search origin/main of agora, agora-controllers, doeff, and dotfiles for calls headed by that name in clause bodies (confirm 0 hits before adding).
;;; Adding a row also requires regenerating packages/doeff-linter/data/clause_ops.json (scripts/export_clause_ops.py --write).
;;;
;;; handle.hy imports this at the head of the module (it is read while macros.hy is requiring handle.hy), so it requires neither doeff-hy.macros nor
;;; doeff-hy.record -- it does not use defrecord, deff, or val, and is a plain dataclass like DefinitionTags in declarations.hy plus
;;; a module-level setv.

(import dataclasses [dataclass])
(import collections.abc [Callable])
(import json)
(import enum [StrEnum])
(import hy.models [Expression Symbol])
(import doeff-hy._static-view [static-view-enabled])
(import doeff-hy._expansion-names [alias-symbol import-form effect-symbol continuation-symbol])


(defclass Ending [StrEnum]
  "What follows after a clause ends (ADR-DOE-CORE-EFFECTS-003 R15)."
  ;; resume the continuation -- refused in Raise / Absent clauses
  (setv RESUMES "resumes")
  ;; discard the continuation and end the scope where the handler was placed with a value -- a clause for an ordinary effect needs :finish-reason
  (setv FINISHES "finishes")
  ;; hand the effect and the continuation to the outer handler
  (setv FORWARDS "forwards")
  ;; leave the clause with an exception
  (setv RAISES "raises"))


(defclass [(dataclass :frozen True :kw-only True)] ClauseOp []
  "One clause operation (see the head of the module for the meaning of the fields)."
  (#^ str name)
  (#^ Ending ending)
  (#^ (| int None) arity)
  (#^ bool returns)
  (#^ (| str None) tail)
  (#^ (get tuple #(str ...)) nodes)
  (#^ (get tuple #(str ...)) static-nodes)
  (#^ (| Callable None) rewrite))


(defn _resume [args recur]
  "(resume value) -> resume the continuation with the value (control returns to the clause when the continuation finishes -- the row's returns. handle.hy sets the mark after returning). The type-check
   expansion uses core's typed-resume, matching the value against the effect's answer type (T of EffectBase[T]) (the clause's effect is narrowed by the clause's
   `(isinstance _doeff_effect EffectType)`). The clause's effect and continuation are `_doeff_effect` and `_doeff_k` -- they do not share names with
   the author's field names (expansion_names.py)."
  (setv value (recur (get args 0)))
  (if (static-view-enabled)
      `(yield (do ~(import-form ["typed_resume"])
                  (~(alias-symbol "typed_resume") ~(effect-symbol) ~(continuation-symbol) ~value)))
      `(yield (~(alias-symbol "Resume") ~(continuation-symbol) ~value))))


(defn _transfer [args recur]
  "(transfer value) -> resume the continuation with the value and remove the handler (a tail call). The type-check expansion uses core's typed-transfer."
  (setv value (recur (get args 0)))
  (if (static-view-enabled)
      `(yield (do ~(import-form ["typed_transfer"])
                  (~(alias-symbol "typed_transfer") ~(effect-symbol) ~(continuation-symbol) ~value)))
      `(yield (~(alias-symbol "Transfer") ~(continuation-symbol) ~value))))


(defn _finish [args recur]
  "(finish value) -> discard the continuation and make the answer of the scope where the handler was placed the value (an ending that does not resume)."
  `(return ~(recur (get args 0))))


(defn _reperform [args recur]
  "(reperform effect) -> hand the effect and the continuation to the outer handler (OCaml 5 reperform). The effect handed over is the author's expression (if the field name is `effect`, the
   field value -- to hand over the received effect use `(pass)`), and the continuation is the clause continuation `_doeff_k`."
  `(yield (~(alias-symbol "Pass") ~(recur (get args 0)) ~(continuation-symbol))))


(defn _pass [args recur]
  "(pass) -> hand the clause's effect and continuation to the outer handler -- the old form, with a warning to move to (reperform effect)."
  (import warnings)
  (warnings.warn
    "(pass) is deprecated in defhandler — use (reperform effect) instead"
    DeprecationWarning)
  `(yield (~(alias-symbol "Pass") ~(effect-symbol) ~(continuation-symbol))))


;; The table of clause operations. Row order follows the order of node names imported at the head of the handler (node-names). Only functions of this module look up the table (callers neither
;; copy nor look up the table by name).
(setv CLAUSE-OPS
  #((ClauseOp :name "resume" :ending Ending.RESUMES :arity 1 :returns True :tail "transfer"
              :nodes #("Resume") :static-nodes #() :rewrite _resume)
    (ClauseOp :name "transfer" :ending Ending.RESUMES :arity 1 :returns False :tail None
              :nodes #("Transfer") :static-nodes #() :rewrite _transfer)
    (ClauseOp :name "finish" :ending Ending.FINISHES :arity 1 :returns False :tail None
              :nodes #() :static-nodes #() :rewrite _finish)
    (ClauseOp :name "reperform" :ending Ending.FORWARDS :arity 1 :returns False :tail None
              :nodes #("Pass") :static-nodes #("Pass") :rewrite _reperform)
    (ClauseOp :name "pass" :ending Ending.FORWARDS :arity 0 :returns False :tail None
              :nodes #("Pass") :static-nodes #("Pass") :rewrite _pass)
    (ClauseOp :name "raise" :ending Ending.RAISES :arity None :returns False :tail None
              :nodes #() :static-nodes #() :rewrite None)))


(defn named-op [name]
  "The row of the clause operation for a name (None for a name not in the table)."
  (next (gfor op CLAUSE-OPS :if (= op.name name) op) None))


(defn clause-op [form]
  "If form is an expression of clause-operation shape (head name is in the table), its row; otherwise None. The number of arguments is not asked -- the check on whether a clause ends and the
   set of operations a clause uses are decided by the head name alone (whether to rewrite is for the caller to match against arity)."
  (when (and (isinstance form Expression) (> (len form) 0) (isinstance (get form 0) Symbol))
    (named-op (str (get form 0)))))


(defn ops-ending [names ending]
  "Among the set of operation names, those whose ending is ending (a frozenset) -- to match the operations a clause uses against the effect's resumption declaration."
  (frozenset (gfor op CLAUSE-OPS :if (and (in op.name names) (= op.ending ending)) op.name)))


(defn node-names [static?]
  "The names of the clause-operation nodes that the handler expansion imports from doeff at the head (in table order, without duplicates)."
  (list (dict.fromkeys (gfor op CLAUSE-OPS
                             name (if static? op.static-nodes op.nodes)
                         name))))


(setv _LINTER-ABOUT
  (+ "Names, ending kinds, and argument counts of clause operations, written out from the rows of doeff-hy's CLAUSE-OPS table (packages/doeff-hy/src/doeff_hy/_clause_ops.hy). "
     "Generated file - do not edit by hand (fix the table, and regenerate with uv run python scripts/export_clause_ops.py --write). "
     "Read by doeff-linter's business_fakes (DOEFF143 etc.) to judge how a clause ends; the reading per ending kind lives in src/project/clause_ops.rs and business_fakes.rs."))


(defn linter-table-json []
  "The contents of the file doeff-linter reads for clause judgment (packages/doeff-linter/data/clause_ops.json) -- only the name, ending, and argument count from the table rows are
   written out (rewrite, node, returns, and tail belong to the run-time and type-check expansions, and the linter does not read them). The file is not one character different from this string
   (written by scripts/export_clause_ops.py --write and checked by tests/test_linter_clause_ops.py)."
  (setv rows (gfor op CLAUSE-OPS
                   (+ "  " (json.dumps (dict :name op.name :ending op.ending.value :arity op.arity) :ensure-ascii False))))
  (+ "{\n"
     " \"_about\": " (json.dumps _LINTER-ABOUT :ensure-ascii False) ",\n"
     " \"ops\": [\n"
     (.join ",\n" rows) "\n"
     " ]\n"
     "}\n"))
