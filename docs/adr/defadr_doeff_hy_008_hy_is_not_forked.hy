;;; Executable ADR: do not fork Hy and do not modify it. Where Hy's behavior needs a workaround, it lives on the doeff side.
;;;
;;; The decision (2026-09-29) is written in the :problem facts.
;;; doeff pinned and used a fork of Hy (proboscis/hy, 10 commits on top of upstream 1.3.1). The fork's 3 features are replaced by workarounds on the doeff side
;;; (the test index, not using stale bytecode after a macro change, and faster require).
;;;
;;; Time-limited exception: until the 3 workarounds landed on main and the Hy requirement was moved back to a PyPI release, the fork pin was
;;; registered as an exception. The pin was removed when the requirement went back to the PyPI release, so there are 0 exceptions (FORK-PIN = None, the import ledger is empty). If an exception is added, it turns red once the deadline
;;; (FORK-EXCEPTIONS-EXPIRE) passes, and it also turns red if an exception whose cause is gone stays in the ledger (the change that removes the cause deletes it from the ledger in the same commit).
;;;
;;; Rollback: revert the commit that added this ADR (the one ADR file disappears).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import doeff-hy [_light-stack :as light-stack])
(import ast)
(import builtins)
(import collections)
(import importlib.util)
(import inspect)
(import json)
(import os)
(import re)
(import shutil)
(import subprocess)
(import sys)
(import types)
(import tomllib)
(import datetime [date])
(import pathlib [Path])
(import packaging.requirements [Requirement])
(import packaging.utils [canonicalize-name])
(import hy)
(import pytest)


;; ---------------------------------------------------------------------------
;; Ledger of time-limited exceptions (until the Hy requirement is moved back to a PyPI release)

;; The rev of the fork pin that is an exception (None = no exception). adbe989a958935172f95220526362ccc103e6d47 was an exception until 2026-09-29 —
;; removed when doeff's and agora-controllers' Hy requirements were moved back to the PyPI release.
(val FORK-PIN None)

;; Deadline of the exceptions. The target for landing the 3 workarounds and the change back to PyPI Hy (2 weeks from the decision date).
;; To extend it, add the reason to :problem and change this value in the same commit.
(val FORK-EXCEPTIONS-EXPIRE (date 2026 10 13))

;; Exceptions for importing names missing from PyPI Hy — #(path module name). Empty: the 5 present when this ADR was written (the 4 in doeff_hy/_pytest_items.py and
;; read_valid_records in doeff_adr/_lazy_collection.py) were removed in main's c6783e8e2 (before this ADR landed).
;; Only add entries that will be removed within the deadline (FORK-EXCEPTIONS-EXPIRE), and add the reason to :problem.
(val FORK-ONLY-IMPORT-EXCEPTIONS #{})

;; The PyPI Hy version used for matching is read from the hy row of uv.lock (locked-hy-version) — no copied constant. It used to be the constant "hy==1.3.1",
;; and raising Hy in uv.lock left the matching at 1.3.1 (missing names of hy.scoping that disappear in a new version).
;; Reading is delegated to the one place in doeff that reads versions from uv.lock (locked_version in scripts/semgrep_locked.py — also read by the commit hook's semgrep, pre-commit, and
;; packages/doeff-hy/tests/test_bytecode_guard.py); no copy of that reading is kept here.
(val LOCK-READER (/ (. (Path __file__) parent parent parent) "scripts" "semgrep_locked.py"))

;; The one place that holds Hy's upper bound (R6) — doeff-hy's dependency declaration. The bound is exactly the hy version in uv.lock (`<=<version>`); no copied constant is kept,
;; and the check compares it with uv.lock.
(val HY-CAP-DECLARATION "packages/doeff-hy/pyproject.toml")

;; How the fork is referenced in dependency declarations (a delimiter must follow github.com/proboscis/hy — proboscis/hyper and the like do not match).
(val FORK-URL-PATTERN (re.compile r"github\.com/proboscis/hy(?:\.git|[?#/\"'\s]|$)"))

;; Marker of Hy source that may import hy.* (after `(import`, the name hy without crossing a paren) — narrows the files read with the reader.
;; The char before hy is neither a word char nor `-` (`doeff-hy.macros` does not match — matching it would read almost every .hy file with the reader, and one check took 15 seconds).
(val HY-IMPORT-HINT (re.compile r"\(import\b[^()]*?(?<![\w-])hy[.\s\[\])]"))

;; Marker of Python source that may import from hy.* (`from hy` / `from hy.X` after leading whitespace on a line) — narrows the files read with ast.
(val PY-HY-IMPORT-HINT (re.compile r"^\s*from\s+hy[.\s]" re.MULTILINE))

;; Dirs the scan does not descend into (worktrees, venvs, build outputs), and design records (including blind-test copies and repro scripts).
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__"})
(val SCAN-SKIP-PREFIXES #("docs/design/"))

;; R7 — reliances on Hy internals that are not imports. They are counted from the shape of the source; no copied ledger is kept.
;; Marker of Python source that may assign to a hy.* name (`hy.X = …` at the start of a line) — narrows the files read with ast.
(val PY-HY-ASSIGN-HINT (re.compile r"^\s*hy(?:\.\w+)+\s*=(?!=)" re.MULTILINE))

;; Marker of Hy source that writes the name by which Hy passes the compiler to a macro (for a macro whose first argument is `_hy-compiler`, Hy 1.3.1's hy.macros.macroexpand
;; passes the compiler) — narrows the files read with the reader (as of 2026-10-12, the 2 files macros.hy and handle.hy).
(val HY-COMPILER-HINT (re.compile r"(?<![\w-])_hy[-_]compiler(?![\w-])"))

;; Names of symbols that refer to the compiler a macro received. The helper functions in macros.hy take the received compiler under the name `compiler`.
(val COMPILER-SYMBOLS #{"compiler" "_hy-compiler" "_hy_compiler"})



;; ---------------------------------------------------------------------------
;; Judgments (pure functions)

(defk dependency-violations [mentions today pin]
  {:pre [(: mentions list) (: today date) (: pin (| str None))]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Lists the fork references #(path line) in dependency declarations that are not exceptions (the pinned rev pin, within the deadline) — the R3 judgment (all of them if pin is None)."
  (lfor #(rel line) mentions
        :if (or (is pin None) (not-in pin line) (> today FORK-EXCEPTIONS-EXPIRE))
        f"{rel}: {(.strip line)}"))

(defk import-violations [missing today exceptions]
  {:pre [(: missing list) (: today date) (: exceptions set)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Lists the imports of names missing from PyPI Hy #(path module name) that are not exceptions (in the ledger, within the deadline) — the R4 judgment."
  (lfor #(rel module name) missing
        :if (or (not-in #(rel module name) exceptions) (> today FORK-EXCEPTIONS-EXPIRE))
        f"{rel}: from {module} import {name}"))

(defk stale-exceptions [missing mentions]
  {:pre [(: missing list) (: mentions list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Lists exceptions still in the ledger whose cause is gone — so that the change removing the cause deletes them from the ledger in the same commit (a rotten ledger silently extends exceptions)."
  (val still-missing (sfor #(rel module name) missing #(rel module name)))
  (+ (lfor entry (sorted FORK-ONLY-IMPORT-EXCEPTIONS)
           :if (not-in entry still-missing)
           f"import exception {entry} no longer imports a name missing from PyPI Hy — remove it from FORK-ONLY-IMPORT-EXCEPTIONS")
     (if (and (not mentions) (is-not FORK-PIN None))
         ["the dependency declarations no longer pin the fork — set FORK-PIN to None and close the time-limited exception"]
         [])))

(defk hy-cap-violations [requirements hy-version]
  {:pre [(: requirements list) (: hy-version str)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Lists the reasons why the hy row of doeff-hy's dependency declaration (a list of PEP 508 strings) does not put the upper bound at exactly the uv.lock version (`<=<version>`) — the R6 judgment.
   No upper bound, an upper bound different from the uv.lock version (including ones that let unverified patch releases through, such as the minor-version bound `<1.4`), a declaration that does not admit the uv.lock version,
   and a row with a condition (marker) (like `; python_version < '3.15'`, the bound does not apply in environments outside the condition — design-validation blind test B) are red."
  (val rows (lfor text requirements
                  :setv requirement (Requirement text)
                  :if (= (canonicalize-name requirement.name) "hy")
                  requirement))
  (val cap f"<={hy-version}")
  (if (!= (len rows) 1)
      [f"doeff-hy's dependency declaration does not have exactly 1 hy row ({(len rows)} rows) — cannot decide which row holds the upper bound"]
      (lfor #(broken message) [#((not-in cap (lfor spec (. (get rows 0) specifier) (str spec)))
                                  f"hy's upper bound is not exactly the uv.lock version ({cap}): {(get rows 0)}")
                                #((not (.contains (. (get rows 0) specifier) hy-version :prereleases True))
                                  f"hy's declaration does not admit the uv.lock version {hy-version}: {(get rows 0)}")
                                #((is-not (. (get rows 0) marker) None)
                                  f"hy's upper-bound row carries a condition (marker) — the bound does not apply in environments outside the condition: {(get rows 0)}")]
            :if broken
            message)))

(defk attribute-parts [node]
  {:pre [(: node ast.AST)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Turns a chain of attributes starting from a name (`hy.importer._could_be_hy_src`) into a list of names — [] if it is not a chain starting from a name."
  (var parts [])
  (var current node)
  (while (isinstance current ast.Attribute)
    (:= parts (+ [current.attr] parts))
    (:= current current.value))
  (if (isinstance current ast.Name) (+ [current.id] parts) []))

(defk python-assignment-reliances [rel tree]
  {:pre [(: rel str) (: tree ast.Module)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "From the tree of a Python source, lists assignments to hy.* names (replacements — `hy.importer._could_be_hy_src = …`) as
   #(path \"replaced\" module name) — the R7 way of counting. The shape that replaces after checking a condition (the install of ast_unparse) is counted too:
   if the replaced name disappears in a new version, that is the signal to remove the patch along with it."
  (val found [])
  (for [node (ast.walk tree)
        :if (isinstance node #(ast.Assign ast.AugAssign ast.AnnAssign))
        target (if (isinstance node ast.Assign) node.targets [node.target])]
    (<- parts (attribute-parts target))
    (when (and (> (len parts) 1) (= (get parts 0) "hy"))
      (.append found #(rel "replaced" (.join "." (cut parts 0 -1)) (get parts -1)))))
  found)

(defk hy-form-reliances [rel forms]
  {:pre [(: rel str) (: forms list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "From the list of forms of a Hy source, lists reliances on the compiler a macro receives as #(path kind target name) — the R7 way of counting:
   a defmacro whose first argument is `_hy-compiler` (Hy's contract of passing the compiler — compiler-parameter), and reads of compiler attributes
   `(. compiler scope)` (the reader reads `compiler.filename` as the same form) and method calls `(._syntax-error compiler …)`
   (the reader reads it as `((. None _syntax-error) compiler …)` — previously not counted, and blind test B's failure case passed the check green) — compiler-attribute."
  (val found [])
  (var pending (list forms))
  (var form None)
  (while pending
    (:= form (.pop pending))
    (when (isinstance form hy.models.Sequence)
      (.extend pending form))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 2)
               (= (get form 0) (hy.models.Symbol "defmacro"))
               (isinstance (get form 2) hy.models.List)
               (get form 2)
               (in (str (get (get form 2) 0)) COMPILER-SYMBOLS))
      (.append found #(rel "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 2)
               (= (get form 0) (hy.models.Symbol "."))
               (isinstance (get form 1) hy.models.Symbol)
               (in (str (get form 1)) COMPILER-SYMBOLS)
               (isinstance (get form 2) hy.models.Symbol))
      (.append found #(rel "compiler-attribute" "_hy_compiler" (hy.mangle (str (get form 2))))))
    (when (and (isinstance form hy.models.Expression)
               (> (len form) 1)
               (isinstance (get form 0) hy.models.Expression)
               (= (len (get form 0)) 3)
               (= (get (get form 0) 0) (hy.models.Symbol "."))
               (= (get (get form 0) 1) (hy.models.Symbol "None"))
               (isinstance (get (get form 0) 2) hy.models.Symbol)
               (isinstance (get form 1) hy.models.Symbol)
               (in (str (get form 1)) COMPILER-SYMBOLS))
      (.append found #(rel "compiler-attribute" "_hy_compiler" (hy.mangle (str (get (get form 0) 2)))))))
  (sorted (set found)))

(defk row-read-gives-frame [use parents row]
  {:pre [(: use ast.Attribute) (: parents dict) (: row tuple)]
   :post [(: % bool)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "For one use of `inspect.stack`, `inspect.stack(…)[n]<read>`, does applying the read (`[k]` or `.attribute`) to the row that light_stack returns yield a frame? Which reads
   a row answers is owned by light_stack, so the judgment does not hold a copy of the read's text (`[0]` etc.) but applies it to the real row
   (previously only `[n][0]` passed, and it stayed red even after light_stack was changed to rows that also answer `.frame` — blind test A's failure case)."
  (val call (.get parents (id use)))
  (val index (.get parents (id call)))
  (val reading (.get parents (id index)))
  (val read
    (cond (not (and (isinstance call ast.Call) (is call.func use)
                    (isinstance index ast.Subscript) (is index.value call)))
            None
          (and (isinstance reading ast.Subscript) (is reading.value index)
               (isinstance reading.slice ast.Constant) (isinstance reading.slice.value int))
            (try (get row reading.slice.value)
                 (except [#(IndexError KeyError TypeError)] None))
          (and (isinstance reading ast.Attribute) (is reading.value index))
            (if (hasattr row reading.attr) (getattr row reading.attr) None)
          True
            None))
  (isinstance read types.FrameType))

(defk stack-call-violations [rel module source row]
  {:pre [(: rel str) (: module str) (: source (| str None)) (: row tuple)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Checks how a Hy module for which light_stack returns the light version (source is the new version's; None = the module does not exist) uses `inspect.stack` —
   the R7 judgment. row is one row that light_stack returns for calls from that module (light-stack-sample-row). Every use's read of
   `inspect.stack(…)[n]` must return a frame from row, and there must be at least one use. If a read does not return a frame, require breaks under light_stack;
   if there are 0 uses, the patch hits nothing. A source that cannot be read as Python (a module in the Hy language) cannot have its shape measured, so it is red."
  (val parsed (if (is source None)
                  None
                  (try (ast.parse source)
                       (except [SyntaxError] None))))
  (cond
    (is source None)
      [f"{rel}: there is no module {module}, which light_stack patches — review _HY_CALLER_MODULES"]
    (is parsed None)
      [f"{rel}: the source of module {module}, which light_stack patches, cannot be read as Python (a module in the Hy language?) — cannot measure how inspect.stack is read"]
    True
      (do
        (val tree parsed)
        (val parents (dfor node (ast.walk tree) child (ast.iter-child-nodes node) (id child) node))
        (val uses (lfor node (ast.walk tree)
                        :if (and (isinstance node ast.Attribute)
                                 (= node.attr "stack")
                                 (isinstance node.value ast.Name)
                                 (= node.value.id "inspect"))
                        node))
        (val lines (.splitlines source))
        (val found (if uses
                       []
                       [f"{rel}: {module} does not use inspect.stack — light_stack's patch hits nothing (review _HY_CALLER_MODULES)"]))
        (for [use uses]
          (<- gives-frame (row-read-gives-frame use parents row))
          (when (not gives-frame)
            (.append found f"{rel}: the read `{(.strip (get lines (- use.lineno 1)))}` at line {use.lineno} of {module} does not return a frame from the row light_stack returns ({(. (type row) __name__)})")))
        found)))

(defk reliance-violations [missing hy-version compiler-failure]
  {:pre [(: missing list) (: hy-version str) (: compiler-failure (| str None))]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Turns reliances #(path kind target name) that were missing from PyPI Hy (version hy-version) into sentences naming them — the R7 judgment. compiler-failure is
   a summary of the exception when a macro whose first argument is _hy-compiler could not be defined or expanded in that version (None if it could)."
  (val reason (if (is compiler-failure None) "" f" (cannot define or expand the macro: {compiler-failure})"))
  (lfor #(rel kind target name) missing
        (cond (= kind "replaced")
                f"{rel}: the replaced {target}.{name} does not exist in Hy {hy-version}"
              (= kind "compiler-parameter")
                f"{rel}: {target} in Hy {hy-version} does not pass the compiler to a macro whose first argument is {name}{reason}"
              (= kind "compiler-attribute")
                f"{rel}: the compiler ({target}) that a macro receives in Hy {hy-version} has no attribute {name}"
              True
                (raise (ValueError f"unknown reliance kind {kind} ({rel})")))))

(defk hy-caps-outside-doeff-hy [declarations]
  {:pre [(: declarations list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Of the hy declarations outside doeff-hy #(path PEP 508 string) (the caller passes them excluding declarations of distributions that do not pull in doeff-hy — those are judged by
   hy-mirror-violations), lists the ones that constrain the version from above (<, <=, ==, ===, ~=) — the R6 judgment 'the upper bound lives in one place, doeff-hy'. A copy of the upper bound in a distribution
   that pulls in doeff-hy silently stays stale in the change that raises doeff-hy's bound and the lock, and becomes a second source of definition (design-validation blind test B)."
  (lfor #(rel text) declarations
        :setv requirement (Requirement text)
        :if (any (gfor spec requirement.specifier (in spec.operator #("<" "<=" "==" "===" "~="))))
        f"{rel}: {text} — hy's upper bound is placed only in one place, {HY-CAP-DECLARATION}"))

(defk hy-mirror-violations [mirrors requirements]
  {:pre [(: mirrors list) (: requirements list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "Of the hy declarations #(path PEP 508 string) of distributions that declare Hy without pulling in doeff-hy, lists the ones whose version specification (the set of specifiers — the textual order
   does not matter) differs from the hy row of doeff-hy's dependency declaration requirements, and the ones with a condition (marker) — the R6 judgment 'the upper bound also applies to distributions
   that do not pull in doeff-hy'. When doeff-hy does not have exactly 1 hy row, hy-cap-violations names it, so nothing is listed here."
  (val rows (lfor text requirements
                  :setv requirement (Requirement text)
                  :if (= (canonicalize-name requirement.name) "hy")
                  requirement))
  (if (!= (len rows) 1)
      []
      (lfor #(rel text) mirrors
            :setv requirement (Requirement text)
            :if (or (!= requirement.specifier (. (get rows 0) specifier)) (is-not requirement.marker None))
            f"{rel}: {text} — the hy declaration of a distribution that does not pull in doeff-hy uses the same version specification as the hy row of {HY-CAP-DECLARATION} ({(get rows 0)}), with no condition")))


;; ---------------------------------------------------------------------------
;; Foundation (files and child processes)

(defk scan-relative-files [repo-root suffixes]
  {:pre [(: repo-root Path) (: suffixes tuple)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the files in the repo whose suffix is in suffixes as #(path relative-path) (excluding worktrees, venvs, and design records).
   Dirs not to descend into are removed from os.walk's dirnames so their contents are not walked (walking into .venv and target took about 2 seconds per run)."
  (sorted
    (lfor #(dirpath dirnames filenames) (os.walk repo-root)
          :do (setv (cut dirnames 0 None) (lfor d dirnames :if (not-in d SCAN-SKIP-PARTS) d))
          name filenames
          :setv p (/ (Path dirpath) name)
          :setv rel (.as-posix (.relative-to p repo-root))
          :if (and (in p.suffix suffixes)
                   (not-in name SCAN-SKIP-PARTS)
                   (not (.startswith rel SCAN-SKIP-PREFIXES))
                   (.is-file p))
          #(p rel))))

(defk fork-mentions [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the lines in dependency declarations (pyproject.toml and uv.lock) that reference the fork, as #(path line)."
  (<- files (scan-relative-files repo-root #(".toml" ".lock")))
  (lfor #(p rel) files
        :if (in p.name #("pyproject.toml" "uv.lock"))
        line (.splitlines (.read-text p :encoding "utf-8"))
        :if (.search FORK-URL-PATTERN line)
        #(rel line)))

(defk scan-hy-imports [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the names that doeff's Python and Hy sources import from hy.* as #(path module name) (a statement importing only a module has 0 names)."
  (<- python-files (scan-relative-files repo-root #(".py")))
  (<- hy-files (scan-relative-files repo-root #(".hy" ".hyk" ".hyp")))
  (val python-imports
    (lfor #(p rel) python-files
          :setv text (.read-text p :encoding "utf-8" :errors "replace")
          ;; Only files that may import from hy.* are read with ast (reading every file takes about 3 seconds — the narrowing is a superset of what must be read).
          :if (.search PY-HY-IMPORT-HINT text)
          :setv tree (try (ast.parse text)
                          (except [SyntaxError] None))
          ;; A file that cannot be read as Python (check data) cannot be imported either, so it cannot use hy.* names — not counted.
          :if (is-not tree None)
          node (ast.walk tree)
          :if (and (isinstance node ast.ImportFrom)
                   (= node.level 0)
                   (is-not node.module None)
                   (or (= node.module "hy") (.startswith node.module "hy.")))
          alias node.names
          #(rel node.module alias.name)))
  (<- hy-imports (hy-source-imports hy-files))
  (sorted (set (+ python-imports hy-imports))))

(defk hy-source-imports [files]
  {:pre [(: files list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the names in (import hy.X [a b]) of Hy sources (a list of #(path relative-path)) as #(path module name) — read with the Hy reader, so shapes inside comments and strings are not counted."
  (val found [])
  (var pending [])
  (var form None)
  (for [#(p rel) files
         :setv text (.read-text p :encoding "utf-8" :errors "replace")
         ;; Only files that may import hy.* are read with the reader (reading every file takes tens of seconds — the narrowing is a superset of what must be read).
         :if (.search HY-IMPORT-HINT text)]
    (:= pending (list (hy.read-many text :filename (str p))))
    (while pending
      (:= form (.pop pending))
      (when (isinstance form hy.models.Sequence)
        (.extend pending form))
      (when (and (isinstance form hy.models.Expression)
                 form
                 (= (get form 0) (hy.models.Symbol "import")))
        (<- names (import-form-names rel (list (cut form 1 None))))
        (.extend found names))))
  found)

(defk import-form-names [rel arguments]
  {:pre [(: rel str) (: arguments list)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "judgment"}}
  "From the arguments of (import …), lists the names in the [...] after a hy.* module as #(path module name) (:as and its alias are not names).
   The Hy reader reads the dotted name `hy.scoping` not as one symbol but as the form `(. hy scoping)`, so the module is read both from a symbol and from a `.` form
   consisting only of symbols — previously only symbols were looked at, and not a single `(import hy.X [...])` in Hy sources was counted
   (the 3 names of hy.scoping in macros.hy slipped through the matching)."
  (lfor #(i module) (enumerate arguments)
        :setv parts (cond (isinstance module hy.models.Symbol)
                            [module]
                          (and (isinstance module hy.models.Expression)
                               (> (len module) 2)
                               (= (get module 0) (hy.models.Symbol "."))
                               (all (gfor part (cut module 1 None) (isinstance part hy.models.Symbol))))
                            (list (cut module 1 None))
                          True
                            [])
        :if (and parts
                 (= (str (get parts 0)) "hy")
                 (< (+ i 1) (len arguments))
                 (isinstance (get arguments (+ i 1)) hy.models.List))
        :setv dotted (.join "." (gfor part parts (hy.mangle (str part))))
        :setv names (list (get arguments (+ i 1)))
        #(j name) (enumerate names)
        :if (and (isinstance name hy.models.Symbol)
                 (or (= j 0) (not (isinstance (get names (- j 1)) hy.models.Keyword))))
        #(rel dotted (hy.mangle (str name)))))

(defk locked-hy-version [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % str)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "The Hy version that doeff installs, as decided by repo-root's uv.lock — the source of definition for the matching version of R4, R6, and R7 is uv.lock (a change that raises Hy also
   raises the matching version). Reading is LOCK-READER's locked_version: when there is not exactly 1 hy row, or it is not a version installed from an index (such as the fork's
   git — R3), the matching version cannot be decided, so it stops naming the reason (SystemExit)."
  (val spec (importlib.util.spec-from-file-location "semgrep_locked" LOCK-READER))
  (val reader (importlib.util.module-from-spec spec))
  (.exec-module spec.loader reader)
  (.locked-version reader repo-root "hy"))

(defk doeff-hy-requirements [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Reads doeff-hy's dependency declaration ([project] dependencies — a list of PEP 508 strings) — where the R6 upper bound lives."
  (get (tomllib.loads (.read-text (/ repo-root HY-CAP-DECLARATION) :encoding "utf-8")) "project" "dependencies"))

(defk hy-declarations-outside-doeff-hy [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the hy declarations in the pyproject.toml of root and packages/*/ (excluding doeff-hy) as #(path PEP 508 string) — reads the string items of [project] dependencies,
   optional-dependencies, and [dependency-groups] ({include-group …} tables inside a group are not strings and are not read)."
  (val files (sorted (lfor p [(/ repo-root "pyproject.toml") #* (.glob (/ repo-root "packages") "*/pyproject.toml")]
                           :setv rel (.as-posix (.relative-to p repo-root))
                           :if (!= rel HY-CAP-DECLARATION)
                           #(p rel))))
  (lfor #(p rel) files
        :setv data (tomllib.loads (.read-text p :encoding "utf-8"))
        :setv project (.get data "project" {})
        group [(.get project "dependencies" [])
               #* (.values (.get project "optional-dependencies" {}))
               #* (.values (.get data "dependency-groups" {}))]
        text group
        :if (and (isinstance text str) (= (canonicalize-name (. (Requirement text) name)) "hy"))
        #(rel text)))

(defk doeff-hy-free-hy-declarations [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Of the pyproject.toml of root and packages/*/, for distributions (excluding doeff-hy) that do not reach doeff-hy even when following [project] dependencies between the workspace's
   distributions, lists the hy declarations in [project] dependencies as #(path PEP 508 string) — the declarations that R6 requires to have the same
   version specification as doeff-hy's (currently doeff-domain; ADR-DOE-DOMAIN-001 R1 forbids it from depending on doeff-hy). extras and
   dependency-groups are not followed (the consumer decides whether to install them, so the bound must apply on the side of dependencies that are always installed). Counted from the shape of the dependencies; no copied ledger is kept."
  (val files (lfor p [(/ repo-root "pyproject.toml") #* (sorted (.glob (/ repo-root "packages") "*/pyproject.toml"))]
                   #((.as-posix (.relative-to p repo-root)) (.get (tomllib.loads (.read-text p :encoding "utf-8")) "project" {}))))
  (val needs (dfor #(rel project) files
                   :if (in "name" project)
                   (canonicalize-name (get project "name"))
                   (sfor text (.get project "dependencies" []) (canonicalize-name (. (Requirement text) name)))))
  ;; Closure of the distributions that reach doeff-hy — each sweep widens it by at least one level, so sweeping as many times as there are distributions is enough.
  (val reaching #{"doeff-hy"})
  (for [_ (range (len needs))]
    (.update reaching (sfor #(name deps) (.items needs) :if (& deps reaching) name)))
  (lfor #(rel project) files
        :if (and (!= rel HY-CAP-DECLARATION)
                 (not-in (canonicalize-name (.get project "name" "")) reaching))
        text (.get project "dependencies" [])
        :if (= (canonicalize-name (. (Requirement text) name)) "hy")
        #(rel text)))

(defk missing-on-pypi-hy [imports hy-version]
  {:pre [(: imports list) (: hy-version str)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the imports #(path module name) missing from PyPI Hy (version hy-version — the uv.lock version) — checked by actually importing them in a throwaway
   environment (uv) with that Hy version installed (it does not use the worktree's venv, so it measures the new version even before the lock is raised and synced)."
  (val probe (+ "import importlib, json, sys\n"
                 "missing = []\n"
                 "for rel, module, name in json.load(sys.stdin):\n"
                 "    try:\n"
                 "        target = importlib.import_module(module)\n"
                 "    except ImportError:\n"
                 "        missing.append([rel, module, name]); continue\n"
                 "    if hasattr(target, name):\n"
                 "        continue\n"
                 "    try:\n"
                 "        importlib.import_module(module + '.' + name)\n"
                 "    except ImportError:\n"
                 "        missing.append([rel, module, name])\n"
                 "print(json.dumps(missing))\n"))
  (<- missing (pypi-hy-run hy-version probe (lfor #(rel module name) imports [rel module name])))
  (lfor row missing (tuple row)))

(defk pypi-hy-run [hy-version probe payload]
  {:pre [(: hy-version str) (: probe str) (: payload list)]
   :post [(: % (| list dict))]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Runs a Python probe in a throwaway environment (uv) with only PyPI Hy (version hy-version) installed, passes payload as JSON on stdin, and
   returns the JSON on stdout — the foundation of the R4 and R7 matching (it does not use the worktree's venv, so it measures the new version even before the lock is raised and synced)."
  (val uv (shutil.which "uv"))
  (assert (is-not uv None) "uv is not on PATH — cannot create a throwaway environment with PyPI Hy")
  (val env (dfor #(key value) (.items os.environ)
                  :if (not-in key #("VIRTUAL_ENV" "CONDA_PREFIX" "PYTHONPATH" "PYTHONPYCACHEPREFIX"))
                  key value))
  (val completed
    (subprocess.run [uv "run" "--no-project" "--isolated"
                     "--python" f"{sys.version-info.major}.{sys.version-info.minor}"
                     "--with" (+ "hy==" hy-version) "python" "-B" "-c" probe]
                    :input (json.dumps payload)
                    :capture-output True :text True :env env :timeout 300 :check False))
  (assert (= completed.returncode 0) completed.stderr)
  (json.loads completed.stdout))

(defk scan-hy-reliances [repo-root]
  {:pre [(: repo-root Path)]
   :post [(: % list)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Lists the Hy internals that doeff's source relies on in ways other than imports as #(path kind target name) — the R7 population: assignments to hy.* names
   (Python source), the compiler a macro receives and its attributes (Hy source), and the modules that light_stack makes light (_HY_CALLER_MODULES)."
  (val found [])
  (<- python-files (scan-relative-files repo-root #(".py")))
  (for [#(p rel) python-files
        :setv text (.read-text p :encoding "utf-8" :errors "replace")
        ;; Only files that may assign to hy.* names are read with ast (the narrowing is a superset of what must be read).
        :if (.search PY-HY-ASSIGN-HINT text)
        :setv tree (try (ast.parse text)
                        (except [SyntaxError] None))
        ;; A file that cannot be read as Python (check data) is never executed, so it never replaces anything either — not counted.
        :if (is-not tree None)]
    (<- rows (python-assignment-reliances rel tree))
    (.extend found rows))
  (<- hy-files (scan-relative-files repo-root #(".hy" ".hyk" ".hyp")))
  (for [#(p rel) hy-files
        :setv text (.read-text p :encoding "utf-8" :errors "replace")
        ;; Only files that write the name of the compiler-receiving contract are read with the reader (reading every file takes tens of seconds).
        :if (.search HY-COMPILER-HINT text)]
    (<- rows (hy-form-reliances rel (list (hy.read-many text :filename (str p)))))
    (.extend found rows))
  (val light-stack-rel (.as-posix (.relative-to (.resolve (Path light-stack.__file__)) (.resolve repo-root))))
  (.extend found (lfor module (sorted light-stack._HY_CALLER_MODULES)
                       #(light-stack-rel "light-stack" module "inspect.stack")))
  (sorted (set found)))

(defk light-stack-sample-row [module]
  {:pre [(: module str)]
   :post [(: % tuple)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "The first row that doeff-hy's light_stack returns for a call to inspect.stack from module — the real thing the R7 judgment applies Hy's reads to
   (the shape of the row is owned by light_stack, and the judgment holds no copy). light_stack hands off to the light version based on the caller's global __name__, so
   it calls inspect.stack(0) in a globals dict whose __name__ is module. The replacement is installed before the call (the same thing doeff_hy's import installs; the second time does nothing)."
  (light-stack.install)
  (get (builtins.eval "inspect.stack(0)" {"__name__" module "inspect" inspect}) 0))

(defk probe-reliances [reliances hy-version]
  {:pre [(: reliances list) (: hy-version str)]
   :post [(: % dict)]
   :tags {:context "doeff-hy-adr" :role "foundation"}}
  "Measures reliances #(path kind target name) against PyPI Hy (version hy-version — the uv.lock version) — {\"missing\" list of missing reliances
   \"sources\" {name of a module light_stack patches: its source (None if the module does not exist)}}. For the compiler, a macro whose first argument is
   _hy-compiler is defined with the public defmacro and expanded, and what Hy passed is read (the probe itself does not use Hy's internal names)."
  (val probe (+ "import importlib, inspect, json, sys, types\n"
                 "import hy\n"
                 "module = types.ModuleType('doeff_hy_reliance_probe')\n"
                 "module.received = []\n"
                 "failure = None\n"
                 "try:\n"
                 "    hy.eval(hy.read('(defmacro doeff-hy-reliance-probe [[_hy-compiler None]] (.append received _hy-compiler) 0)'), module=module)\n"
                 "    hy.eval(hy.read('(doeff-hy-reliance-probe)'), module=module)\n"
                 "except Exception as error:\n"
                 "    lines = [line for line in str(error).splitlines() if line.strip()]\n"
                 "    failure = type(error).__name__ + (': ' + lines[-1].strip() if lines else '')\n"
                 "compiler = module.received[0] if module.received else None\n"
                 "passed = compiler is not None and not isinstance(compiler, hy.models.Object)\n"
                 "missing = []\n"
                 "sources = {}\n"
                 "for rel, kind, target, name in json.load(sys.stdin):\n"
                 "    if kind == 'light-stack':\n"
                 "        try:\n"
                 "            sources[target] = inspect.getsource(importlib.import_module(target))\n"
                 "        except ImportError:\n"
                 "            sources[target] = None\n"
                 "        continue\n"
                 "    if kind == 'replaced':\n"
                 "        try:\n"
                 "            present = hasattr(importlib.import_module(target), name)\n"
                 "        except ImportError:\n"
                 "            present = False\n"
                 "    elif kind == 'compiler-parameter':\n"
                 "        present = passed\n"
                 "    elif kind == 'compiler-attribute':\n"
                 "        present = passed and hasattr(compiler, name)\n"
                 "    else:\n"
                 "        raise ValueError(kind)\n"
                 "    if not present:\n"
                 "        missing.append([rel, kind, target, name])\n"
                 "print(json.dumps({'missing': missing, 'sources': sources, 'compiler_failure': failure}))\n"))
  (<- probed (pypi-hy-run hy-version probe (lfor #(rel kind target name) reliances [rel kind target name])))
  {"missing" (lfor row (get probed "missing") (tuple row))
   "sources" (get probed "sources")
   "compiler-failure" (get probed "compiler_failure")})


(defadr ADR-DOE-HY-008
  :title "Do not fork Hy and do not modify it. Where Hy's behavior needs a workaround, it lives on the doeff side. Dependency declarations do not name proboscis/hy, and nothing imports from hy.* a name missing from PyPI Hy (the fork pin has been removed). doeff-hy's upper bound on Hy is exactly the uv.lock version that doeff has verified"
  :status "accepted"
  :scope ["pyproject.toml"
          "uv.lock"
          "packages"
          "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy"]
  :problem
    [(fact
       "Decided on 2026-09-29: add workarounds that avoid forking Hy, and do not modify Hy at all. The trigger was that, once it had been decided to cache the test index, it was doubtful whether a Hy fork was needed, and it was judged that everything, including faster require, could be worked around without forking Hy.")
     (fact
       "The cost of the fork (an actual case on 2026-09-29): doeff's and agora-controllers' Hy pins had to be raised at the same time, and every agora merge failed from 13:16 to 13:45. Early that morning, a change in the bytecode format made the image fail to build.")
     (fact
       "The fork is 10 commits and 6 files on top of upstream 1.3.1 (hy/importer.py +439 lines). The upstream maintainers are cautious even about an 8-line change (hylang/hy#2728), so upstreaming cannot be expected.")
     (fact
       "Measured on 2026-09-29: doeff's dependency declarations pointed at the fork's rev adbe989a (pyproject.toml's [tool.uv.sources] and uv.lock), and doeff's code imported 5 names missing from PyPI Hy 1.3.1 (the 4 in packages/doeff-hy/src/doeff_hy/_pytest_items.py and read_valid_records in packages/doeff-adr/src/doeff_adr/_lazy_collection.py). The 5 were removed in main's c6783e8e2 (the same day) — this ADR's R5 check confirmed it by turning the 5 left in the ledger red as 'exceptions whose cause is gone'."
       :evidence "output of this ADR's scan-hy-imports and missing-on-pypi-hy")
     (fact
       "Measured on 2026-10-12: the R4 check counted not a single `(import hy.X [...])` in Hy sources — Hy 1.3.1's reader reads `hy.scoping` as the form `(. hy scoping)`, and import-form-names looked only at symbols. The 3 names macros.hy imports from hy.scoping, which is not in Hy's public API (nearest-python-scope, is-function-scope, ScopeGlobal), slipped through the matching. The matching version was also a copied constant \"hy==1.3.1\", and raising Hy in uv.lock left the matching at 1.3.1. Both are the shape where the check misses a name that disappeared in a Hy upgrade, loading macros.hy fails with ImportError, and everything using doeff-hy stops."
       :evidence "0 hy.scoping entries among the 70 of scan-hy-imports before the fix")
     (fact
       "Measured on 2026-10-12: doeff-hy's Hy declaration was hy>=1.2.0 with no upper bound. The Hy declarations of other repos using doeff-hy were hy>=1.0 (agora-controllers' controllers/kanban and others), hy>=1.3 (the agora-controllers root, custody's custody-scripted), hy==1.3.0 (merge-queue, code-quality), and hy>=1.2.0 (agent-commons, dotfiles' agentcli), with locks at 1.3.1 (agora-controllers, custody, agent-commons) and 1.3.0 (merge-queue's deploy/ledger/runtime, code-quality). Any repo could raise Hy with just its own lock, and that version does not go through doeff's checks (R4 measures with the version in doeff's uv.lock). Hy has been semver since 1.0.0, but hy.scoping and other things doeff-hy relies on are outside the public API, and the patch release 1.3.1 also fixed Hy's importer (a regression in importing Python inside a ZIP). The 131 names doeff imports from hy.* existed in every version from 1.0.0 to 1.3.1."
       :evidence "pyproject.toml and uv.lock on origin/main of each repo, the NEWS.rst of PyPI Hy 1.3.1, and the output of running scan-hy-imports and missing-on-pypi-hy against 1.0.0–1.3.1")
     (fact
       "Measured on 2026-10-12: doeff-hy also relies on Hy internals in ways other than imports — doeff_hy/__init__.py replaces hy.importer._could_be_hy_src (if the name changes, the assignment just creates a new attribute, and .hyk / .hyp get read as Python); the reads of scope, this, and filename on the compiler received through a macro's first argument _hy-compiler (macros.hy, handle.hy — Hy 1.3.1's hy.macros.macroexpand passes the compiler only to macros whose first argument is named _hy_compiler); and light_stack.py's patch that returns a one-frame tuple to the 4 places in hy.compiler and hy.macros that read inspect.stack()[n][0] (if Hy changes to read [n].frame, require fails with AttributeError). The R4 check matched only imported names; these were not covered by the pre-registration checks and were measured only by the daily verification after merge."
       :evidence "Hy 1.3.1 importer.py:118-126, compiler.py:55, 345-357, macros.py:153, 179, 203, 405")
     (fact
       "Measured on 2026-10-12 (doeff d406b2891, Python 3.14.3t): doeff-hy's checks (packages/doeff-hy/tests, packages/doeff-hy-pytest/tests, ADR-DOE-HY-001–008) were run against 3 Hy versions — 1.3.1 (the lock): 842 passed, 2 failed; 1.3.0 (a worktree with the lock set to 1.3.0): 841 passed, 3 failed; 1.2.0 (the lock left at 1.3.1 and only the environment's Hy swapped — doeff's workspace cannot resolve its lock at 1.2.0 because of hy>=1.3 in doeff-claude-code and others): 832 passed, 12 failed. The 2 common to all 3 are main's reds unrelated to Hy, and the 3rd one on 1.3.0 is the R6 check naming the mismatch between the lock version and the upper bound. The 10 only on 1.2.0 share one cause — Hy 1.2.0's hy.macros.require, in a namespace without a module (passing a dict to hy.eval), passes the target module to find_spec while still None and fails with AttributeError (1.3.0's derive_target_module was fixed to use the namespace's __name__). The R4 and R7 matching (142 imports, 9 internal reliances) was green on all 3 versions; this difference was invisible to name matching and only became visible by running the checks. doeff-hy's declared lower bound was 1.2.0, and doeff-domain's declaration (hy>=1.2.0) does not pull in doeff-hy, so R6's upper bound did not apply to it."
       :evidence "pytest summaries of the 3 worktrees, and the diff of hy/macros.py in the site-packages of 1.2.0 and 1.3.0")
     (fact
       "Measured on 2026-10-12: the bytecode wrapper (doeff_hy_bytecode_guard — R2) also relies on Hy internals in ways other than imports — the names of a module's macro tables _hy_macros and _hy_reader_macros; the fact that Hy 1.3.1's hy.macros.macroexpand looks up those tables with `name in table` and `table[name]` (a table that remembers looked-up names picks up the macros used); and the shapes of gensym names `_hy_gensym_<base>_<count>` and of bindings renamed by let `_hy_let_<original name>_<number>` (read by the canonicalization at the compile entry point). The R7 check does not count these. Measured with failure cases that change only one place in Hy 1.3.1: with a version that looks up the table with .get and a version that renames _hy_macros, 9 of the wrapper's checks (packages/doeff-hy/tests/test_bytecode_guard.py — selected by changes to uv.lock) were red with 'changing a macro leaves the user on the stale expansion', but with a version that renames _hy_reader_macros, one that changes the shape of gensym names, and one that changes the shape of get_anon_var names, all 20 were green (a reader macro change silently uses the stale expansion; the code wobbles with compile order).")
     (fact
       "Measured on 2026-10-12: outside the bytecode wrapper there were also 3 places relying on Hy's internal names that were not covered by the checks measured at the uv.lock version — doeff_effect_analyzer._result_cache reads the module-namespace table names _hy_macros and _hy_reader_macros as strings (to put the macro provider in the analysis key); doeff_hy._static_baseline holds the shapes of Hy's temporary names _hy_anon_<number> and _hy_let_first_<number> in a regex with the prefix split off; and macro_use._exported holds the string of Hy's export name table _hy_export_macros (a source of the name's definition outside records). doeff_hy._static_stub also held the string prefix _hy_. In all 3 places the checks stay green even when Hy is raised and the names change, in the shape where the key drops the macro provider, the baseline key wobbles with numbers, and every require is miscounted."
       :evidence "the findings of whoever did the previous measurement (the bytecode wrapper's reliances), and result_cache.py:395, _HY_GENERATED_NUMBER in static_baseline.py, macro_use.py:384, static_stub.py:51 on origin/main fb4dd0f3b")]
  :context
    [(interpretation
       "If doeff can produce the same correctness from outside Hy, there is no reason to pay for maintaining the fork (conflicts on every upstream update, raising the pins of 2 repos at the same time). The workarounds become doeff's own, and doeff's checks can guard them.")
     (interpretation
       "There are 2 ways to measure 'do not modify Hy': dependency declarations do not point at the fork (what gets installed is upstream), and doeff's code does not use names missing from upstream (going back to upstream does not break imports). Workarounds that read Hy's internal names (reading _hy_macros in a module namespace, replacing hy.importer._could_be_hy_src) do not violate these 2 as long as they use only names that also exist upstream.")
     (interpretation
       "doeff's checks verify only one Hy, the uv.lock version, so the Hy that doeff-hy may admit is also up to that version. The upper bound is in one place, doeff-hy's metadata, and uv's resolution enforces it in every repo that uses doeff-hy. Running the same check as R4 on the other repos' side without an upper bound would multiply copies of the check by the number of repos, and would not go through doeff's other checks (macro expansion, the bytecode wrapper, the ast_unparse patch). An upper bound at the minor version (<1.4) admits unverified patch releases, and pinning one version with == cannot be resolved when merge-queue and code-quality, which declare hy==1.3.0, raise doeff's rev. The lower bound follows the same reasoning and does not admit versions older than one on which doeff-hy's checks ran green (on 1.2.0, 10 checks are red — the lower bound is 1.3.0, the version in merge-queue's and code-quality's locks). The upper bound is defined in one place, doeff-hy's declaration, and only distributions that do not pull in doeff-hy (doeff-domain — ADR-DOE-DOMAIN-001 R1 forbids it from depending on doeff-hy) place a copy of the same version specification — the check compares the copy with doeff-hy's declaration, so if it stays stale in the change that raises the bound it turns red and does not become a second source of definition. Without the copy, the upper bound would not apply in an environment that pulls in only doeff-domain.")
     (interpretation
       "Reliances that are not imports are counted from the shape of the source, not from a copied ledger (R7) — a ledger silently rots when a change that adds a reliance forgets to record it. 3 shapes are counted: assignments to hy.* names (`hy.X.Y = …`); defmacros whose first argument is _hy-compiler and reads of compiler attributes (`(. compiler name)`, `compiler.name`, method calls `(.name compiler …)`); and light_stack's _HY_CALLER_MODULES. light_stack's patch is measured by applying Hy's inspect.stack reads to the real rows light_stack returns — which reads a row answers is owned by light_stack, and the judgment holds no copy (fixed after the failure cases of blind tests A and B). Assignments to a module received under an alias and names given as getattr strings are not counted. R6's upper bound keeps the other repos' Hy at or below the uv.lock version, so when R7 measures at the uv.lock version it also covers the other repos' Hy.")
     (interpretation
       "The bytecode wrapper's reliances (the table names and lookup style, the export name table, the shapes of gensym and let names, the entry point that opens the local macro table inside a function) are not added to R7's population as copies of names or shapes; instead the wrapper's checks measure them by running the wrapper itself with the uv.lock version of Hy — the reliance is not whether a name exists but the behavior: under a real expansion, whether the wrapper's table remembers the macros used and the names not in the table, and whether the compile entry point renames names; a probe that matches names cannot tell apart a version that looks up the table with .get. Like R4 and R7, the wrapper's checks read the uv.lock version and are selected by changes to uv.lock. Checks in the same process in the same file measure the venv's Hy, so every check in the file first confirms that the venv's Hy version matches the uv.lock version — the contents of a PyPI release do not change per release, so if they match, it is measuring the uv.lock version. In a worktree where Hy was raised but not synced it turns red and names the sync (design-validation blind tests A and B).")
     (interpretation
       "The source of definition of Hy's internal names (the name of a module's macro table, the name of the reader macro table, the name of the export name table, the prefixes and shapes Hy gives temporary names) is one place, doeff_hy_bytecode_guard._records (the constants MACRO_TABLE, READER_TABLE, EXPORT_NAMES, GENERATED_NAME_PREFIX, ANON_VAR_NAME and the functions macro_tables, exported_macro_names, fold_anon_var_numbers), and users outside the wrapper (the analysis key of doeff_effect_analyzer._result_cache, the baseline key of doeff_hy._static_baseline, macro_use's judgment of every require, the names doeff_hy._static_stub hides) look them up there. If the name strings become a second source of definition, there are 2 or more places to fix when Hy renames something, and the measuring checks look only at records' reads, so the copies go stale unmeasured. The measurement is the checks that apply records' reads to the real Hy module (test_records_reads_… and test_the_temporaries_hy_names_… in test_bytecode_guard.py — the table and export reads match Hy's own require behavior, and temporary names keep a shape fold_anon_var_numbers can fold) and the 2 analyzer checks that measure that the key follows both tables, selected by changes to uv.lock. The scan in packages/doeff-hy/tests/test_hy_internal_names_have_one_home.py turns red if the strings exist outside records (it counts only spelled-out names — other ways of writing them are not counted, and the reliance itself is measured by the checks above).")]
  :decision
    [(rule R1 "Do not modify Hy at all. Do not add commits to proboscis/hy. The end state is PyPI Hy.")
     (rule R2 "Where Hy's behavior needs a workaround, it lives on the doeff side: the test index, the judgment that avoids stale bytecode after a macro change (doeff_hy_bytecode_guard wraps Python's standard SourceFileLoader), and faster require.")
     (rule R3 "doeff's and agora-controllers' dependency declarations (pyproject.toml, uv.lock) do not name proboscis/hy. There are 0 exceptions (the pin of rev adbe989a was an exception until 2026-09-29 — removed when the Hy requirement went back to the PyPI release). doeff's checks read doeff's tree — agora-controllers' declarations are guarded by checks on the agora-controllers side.")
     (rule R4 "doeff's code does not import from hy.* names missing from PyPI Hy (the version uv.lock pins). The check reads the matching version from uv.lock, and on a change to uv.lock `make test-changed` selects this check ([[tool.doeff.contract-tests]] in the root pyproject.toml) — a change that raises Hy confirms before registration that the names exist in the new version. The exception ledger (FORK-ONLY-IMPORT-EXCEPTIONS) is empty (cleared in c6783e8e2). If entries are added, the deadline is the same as R3.")
     (rule R5 "Exceptions in the ledger are deleted in the same commit once their cause is gone (the change that removes the fork pin deletes FORK-PIN; the change that removes an import deletes its row in FORK-ONLY-IMPORT-EXCEPTIONS). An exception whose cause is gone that stays in the ledger is red.")
     (rule R6 "The upper bound of doeff-hy's Hy dependency declaration is exactly the hy version in doeff's uv.lock (`<=<version>`) — this keeps the Hy that other repos using doeff-hy (agora-controllers, custody, merge-queue, code-quality, agent-commons, dotfiles) can raise with their own locks at or below the version doeff's checks verified (the upper bound also applies to other repos' uv resolution). The upper bound is defined in one place, doeff-hy (every doeff package that uses Hy pulls in doeff-hy except doeff-domain, and uv installs only one Hy per environment — hy declarations of distributions that pull in doeff-hy do not constrain the version from above), and the upper-bound row carries no condition (marker) (the bound would stop applying in environments outside the condition). The hy declaration of a distribution that does not reach doeff-hy even when following [project] dependencies (currently doeff-domain) uses the same version specification as doeff-hy's hy row with no condition, and the check compares them. The lower bound is a version on which doeff-hy's checks ran green (currently 1.3.0 — measured 2026-10-12). A change that raises Hy raises doeff's uv.lock and the upper bound in the same commit (a change to the bound's file or to uv.lock makes `make test-changed` select this check and the R4 check), and other repos first raise their doeff version (doeff-min-commit, the git rev) to one including that commit, then raise Hy in their own lock.")
     (rule R7 "The Hy internals that doeff's code relies on in ways other than imports — the replaced hy.* name (hy.importer._could_be_hy_src), Hy's contract of passing the compiler to a macro's first argument _hy-compiler and the compiler's attributes (scope, this, filename), and the calls for which light_stack returns the light version (the read inspect.stack(…)[n] in the _HY_CALLER_MODULES modules returns a frame from the row light_stack returns) — are also checked to exist in PyPI Hy (the version uv.lock pins). As with R4, the matching version is read from uv.lock, and on a change to uv.lock `make test-changed` selects this check. Reliances are counted from the shape of the source; no copied ledger is kept. The reliances of the bytecode wrapper (R2) — the names of a module's macro tables (MACRO_TABLE, READER_TABLE in doeff_hy_bytecode_guard._records) and the expansion's lookup of the table with `name in table` and `table[name]`, the table of public macro names that Hy's export writes, the shapes of gensym names and of bindings renamed by let, and the local table into which a require inside a function puts macros (the compiler's entry point new_local_state that opens local state, and that state's macro-table key records.LOCAL_MACRO_TABLE) — are measured by the wrapper's checks (packages/doeff-hy/tests/test_bytecode_guard.py) by running the wrapper with the uv.lock version of Hy (checks in a throwaway environment install that version of PyPI Hy, and checks in the same process first confirm that the venv's Hy is that version; selected by changes to uv.lock). The source of definition of these names is one place, records, and users outside the wrapper (doeff_effect_analyzer._result_cache, doeff_hy._static_baseline, macro_use's judgment of every require, doeff_hy._static_stub) also look them up through records' constants and functions and do not hold the strings (a check in the same file applies records' reads to the real Hy, and copies of the strings are counted by test_hy_internal_names_have_one_home.py).")]
  :laws
    [(law dependency-declarations-name-no-hy-fork
       :statement "for_all line in (pyproject.toml ∪ uv.lock) of doeff and agora-controllers: names(line, github.com/proboscis/hy) => (FORK-PIN is not None and rev(line) == FORK-PIN and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "Raise the Hy pin to a new rev of the fork (the shape in which every agora merge failed from 13:16 to 13:45 on 2026-09-29)")
          (counterexample "After the deadline, hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"adbe989a…\" } still remains in pyproject.toml's [tool.uv.sources]")])
     (law imports-exist-in-pypi-hy
       :statement "for_all (module, name) imported by doeff from hy.*: exists_in(PyPI hy at version(uv.lock, hy), module, name) or ((path, module, name) in FORK-ONLY-IMPORT-EXCEPTIONS and today <= FORK-EXCEPTIONS-EXPIRE)"
       :counterexamples
         [(counterexample "Add from hy.importer import read_valid_records to a new file — going back to PyPI Hy breaks the import")
          (counterexample "Add (import hy.importer [add-compile-record]) to a Hy source")
          (counterexample "Raise hy in uv.lock to a version where hy.scoping has no nearest-python-scope — loading macros.hy fails with ImportError")])
     (law doeff-hy-caps-hy-at-the-locked-version
       :statement "for the single hy requirement r in dependencies(packages/doeff-hy/pyproject.toml): ('<=' + version(uv.lock, hy)) in specifiers(r) and version(uv.lock, hy) satisfies r and marker(r) is None; and for_all hy requirement m in [project].dependencies of a doeff distribution d != doeff-hy whose [project].dependencies do not reach doeff-hy: specifiers(m) == specifiers(r) and marker(m) is None; and for_all other hy requirement q in the other pyproject.toml of doeff (root and packages/*): no specifier of q has an operator in {<, <=, ==, ===, ~=}"
       :counterexamples
         [(counterexample "doeff-hy's declaration stays hy>=1.2.0 (no upper bound) — when agora-controllers runs `uv lock --upgrade-package hy` on its own lock, a version that has not gone through doeff's checks gets in")
          (counterexample "Set the upper bound to <1.4 — lets a patch release 1.3.2 that doeff has not verified into other repos' locks (the patch release 1.3.1 also fixed Hy's importer)")
          (counterexample "Raise the upper bound to <=1.4.0 and leave hy in uv.lock at 1.3.1 — the R4 check measures only at 1.3.1, and other repos can install 1.4.0")
          (counterexample "Make the upper-bound row hy>=1.2.0,<=1.3.1; python_version < '3.15' — in a Python 3.15 environment the bound disappears and Hy is decided by the consumers' declarations alone (design-validation blind test B)")
          (counterexample "Copy the upper bound hy>=1.2.0,<=1.3.1 into the declaration of doeff-adr (which pulls in doeff-hy) — the copy stays stale in the change that raises doeff-hy's bound and the lock, and becomes a second source of definition (same)")
          (counterexample "Leave the declaration of doeff-domain (which does not pull in doeff-hy) at hy>=1.2.0 — in an environment that pulls in only doeff-domain the upper bound does not apply, and a Hy doeff has not measured gets in")
          (counterexample "Raise doeff-hy's upper bound and lock to 1.3.2 and leave doeff-domain's declaration at hy>=1.3.0,<=1.3.1 — doeff-domain's copy stays stale")])
     (law internal-reliances-exist-in-pypi-hy
       :statement "for_all reliance in assignments(doeff, hy.*) ∪ compiler_reads(doeff) ∪ light_stack._HY_CALLER_MODULES: holds(PyPI hy at version(uv.lock, hy), reliance)"
       :counterexamples
         [(counterexample "Raise hy in uv.lock to a version that renamed hy.importer._could_be_hy_src — doeff_hy's assignment just creates a new attribute, and .hyk / .hyp get read as Python, causing SyntaxError")
          (counterexample "Raise hy in uv.lock to a version where the compiler's scope has a different name — _in-function-body? in defk and others raises AttributeError at expansion time")
          (counterexample "Raise hy in uv.lock to a version where hy.macros reads inspect.stack()[1].frame — light_stack's one-element tuple has no .frame, and require raises AttributeError (green if light_stack's row is changed to one that also answers .frame — the judgment applies reads to the real row)")
          (counterexample "A macro helper calls a private method of the compiler with (._syntax-error compiler form message) — unless the method-call shape is also counted as a compiler attribute, the check stays green even when a Hy upgrade removes the name (blind test B)")])]
  :enforcement
    [(deftest test-adr-doe-hy-008-dependency-declarations-name-no-hy-fork
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val violations (run (dependency-violations mentions (date.today) FORK-PIN)))
       (assert (= violations [])
               (+ "dependency declarations point at the Hy fork (ADR-DOE-HY-008 R3 — point at PyPI Hy. "
                  "exception pin = " (str FORK-PIN) "): " (str violations))))
     (deftest test-adr-doe-hy-008-imports-exist-in-pypi-hy
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val imports (run (scan-hy-imports repo-root)))
       (val missing (run (missing-on-pypi-hy imports hy-version)))
       (val violations (run (import-violations missing (date.today) FORK-ONLY-IMPORT-EXCEPTIONS)))
       (assert (= violations [])
               (+ "importing from hy.* names missing from PyPI Hy " hy-version " (the uv.lock version) "
                  "(ADR-DOE-HY-008 R4 — keep it on the doeff side): " (str violations))))
     (deftest test-adr-doe-hy-008-internal-reliances-exist-in-pypi-hy
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val reliances (run (scan-hy-reliances repo-root)))
       (val probed (run (probe-reliances reliances hy-version)))
       (val violations
         (+ (run (reliance-violations (get probed "missing") hy-version (get probed "compiler-failure")))
            (sum (lfor #(rel kind module name) reliances
                       :if (= kind "light-stack")
                       (run (stack-call-violations rel module (get (get probed "sources") module)
                                                   (run (light-stack-sample-row module)))))
                 [])))
       (assert (= violations [])
               (+ "PyPI Hy " hy-version " (the uv.lock version) lacks Hy internals that doeff relies on in ways other than imports "
                  "(ADR-DOE-HY-008 R7 — adapt the patch to the new version, or move Hy back): " (str violations))))
     (deftest test-adr-doe-hy-008-reliance-probe-names-what-pypi-hy-lacks
       ;; Counterexample: names, attributes, and modules that do not exist turn red, naming them, when measured against the uv.lock version of PyPI Hy.
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val rows [#("a.py" "replaced" "hy.importer" "_could_be_hy_src")
                  #("a.py" "replaced" "hy.importer" "_doeff_no_such_name")
                  #("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")
                  #("m.hy" "compiler-attribute" "_hy_compiler" "scope")
                  #("m.hy" "compiler-attribute" "_hy_compiler" "doeff_no_such_attribute")
                  #("l.py" "light-stack" "hy.macros" "inspect.stack")
                  #("l.py" "light-stack" "hy.doeff_no_such_module" "inspect.stack")])
       (val probed (run (probe-reliances rows hy-version)))
       (assert (= (get probed "missing")
                  [#("a.py" "replaced" "hy.importer" "_doeff_no_such_name")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "doeff_no_such_attribute")]))
       (assert (is (get probed "compiler-failure") None))
       (val messages (run (reliance-violations (get probed "missing") hy-version None)))
       (assert (in "hy.importer._doeff_no_such_name" (get messages 0)) messages)
       (assert (in "doeff_no_such_attribute" (get messages 1)) messages)
       (assert (= (run (stack-call-violations "l.py" "hy.macros" (get (get probed "sources") "hy.macros")
                                              (run (light-stack-sample-row "hy.macros"))))
                  []))
       (assert (is (get (get probed "sources") "hy.doeff_no_such_module") None)))
     (deftest test-adr-doe-hy-008-exceptions-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val mentions (run (fork-mentions repo-root)))
       (val hy-version (run (locked-hy-version repo-root)))
       (val missing (run (missing-on-pypi-hy (run (scan-hy-imports repo-root)) hy-version)))
       (val stale (run (stale-exceptions missing mentions)))
       (assert (= stale []) (+ "exceptions whose cause is gone remain in the ledger (ADR-DOE-HY-008 R5): " (str stale))))
     (deftest test-adr-doe-hy-008-doeff-hy-caps-hy-at-the-locked-version
       (val repo-root (. (Path __file__) parent parent parent))
       (val hy-version (run (locked-hy-version repo-root)))
       (val requirements (run (doeff-hy-requirements repo-root)))
       (val mirrors (run (doeff-hy-free-hy-declarations repo-root)))
       (val violations (+ (run (hy-cap-violations requirements hy-version))
                          (run (hy-mirror-violations mirrors requirements))
                          (run (hy-caps-outside-doeff-hy (lfor declaration (run (hy-declarations-outside-doeff-hy repo-root))
                                                               :if (not-in declaration mirrors)
                                                               declaration)))))
       (assert (= violations [])
               (+ "Hy's upper bound violates R6 (ADR-DOE-HY-008 R6 — put the bound in " HY-CAP-DECLARATION ", with no condition, at exactly the uv.lock version "
                  hy-version "; distributions that do not pull in doeff-hy use the same version specification; a change that raises Hy raises the bound and uv.lock "
                  "in the same commit): " (str violations))))
     (deftest test-adr-doe-hy-008-judgments-reject-the-counterexamples [tmp-path]
       ;; Counterexample: another rev, after the deadline, and an import not in the ledger are red; an import in the ledger within the deadline is green.
       (val before (date 2026 9 30))
       (val after (date 2026 12 31))
       (val pin "adbe989a958935172f95220526362ccc103e6d47")
       (val pin-line f"hy = {{ git = \"https://github.com/proboscis/hy.git\", rev = \"{pin}\" }}")
       (val other-line "hy = { git = \"https://github.com/proboscis/hy.git\", rev = \"0123456789abcdef\" }")
       (assert (= (run (dependency-violations [#("pyproject.toml" pin-line)] before pin)) []))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" other-line)] before pin))) 1))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" pin-line)] after pin))) 1))
       (assert (= (len (run (dependency-violations [#("pyproject.toml" pin-line)] before None))) 1))
       (val registered #("packages/doeff-adr/src/doeff_adr/_lazy_collection.py" "hy.importer" "read_valid_records"))
       (val unregistered #("packages/doeff-hy/src/doeff_hy/new_module.py" "hy.importer" "read_valid_records"))
       (val ledger #{registered})
       (assert (= (run (import-violations [registered] before ledger)) []))
       (assert (= (len (run (import-violations [unregistered] before ledger))) 1))
       (assert (= (len (run (import-violations [registered] after ledger))) 1))
       (assert (not (.search FORK-URL-PATTERN "https://github.com/proboscis/hyper.git")))
       (assert (.search FORK-URL-PATTERN "https://github.com/proboscis/hy.git?rev=adbe989a"))
       ;; Counterexample: names of dotted modules in Hy sources (which the reader reads as (. hy scoping)) are counted too,
       ;; while :as aliases and non-hy modules (doeff-hy.macros) are not. The marker does not match doeff-hy.macros either.
       (val import-form (hy.read "(import hy.scoping [nearest-python-scope is-function-scope] hy [mangle :as m] doeff-hy.macros [defk])"))
       (assert (= (run (import-form-names "macros.hy" (list (cut import-form 1 None))))
                  [#("macros.hy" "hy.scoping" "nearest_python_scope")
                   #("macros.hy" "hy.scoping" "is_function_scope")
                   #("macros.hy" "hy" "mangle")]))
       (assert (.search HY-IMPORT-HINT "(import hy.scoping [ScopeGlobal])"))
       (assert (not (.search HY-IMPORT-HINT "(import doeff-hy.macros [defk])")))
       ;; Counterexample: the matching version is read from the hy row of uv.lock (raising the lock raises the matching too); stop when there is not exactly 1 hy row or it is not a version installed
       ;; from an index (the reading is in one place, LOCK-READER).
       (val lock-text (+ "version = 1\n\n[[package]]\nname = \"hy\"\nversion = \"1.4.0\"\n"
                         "source = { registry = \"https://pypi.org/simple\" }\n"))
       (val lock (/ tmp-path "uv.lock"))
       (.write-text lock lock-text :encoding "utf-8")
       (assert (= (run (locked-hy-version tmp-path)) "1.4.0"))
       (.write-text lock (.replace lock-text "registry = \"https://pypi.org/simple\""
                                   "git = \"https://github.com/proboscis/hy.git?rev=adbe989a\"")
                    :encoding "utf-8")
       (with [(pytest.raises SystemExit :match "index \u304b\u3089\u5165\u308c\u308b\u7248\u3067\u306a\u3044")]
         (run (locked-hy-version tmp-path)))
       (.write-text lock (.replace lock-text "name = \"hy\"" "name = \"hyrule\"") :encoding "utf-8")
       (with [(pytest.raises SystemExit :match "1 \u3064\u306b\u6c7a\u307e\u3089\u306a\u3044")]
         (run (locked-hy-version tmp-path)))
       ;; Counterexample: no upper bound, a minor-version bound (<1.4), a bound different from uv.lock, and a declaration with no hy row are red; a bound at exactly the uv.lock version is green.
       (assert (= (run (hy-cap-violations ["doeff>=0.2.0" "hy>=1.2.0,<=1.3.1"] "1.3.1")) []))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<1.4"] "1.3.1"))) 1))
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1"] "1.4.0"))) 2))
       (assert (= (len (run (hy-cap-violations ["pydantic>=2.11"] "1.3.1"))) 1))
       ;; Counterexample: the R7 way of counting — assignments to hy.* names are counted; assignments to non-hy things and to hy itself are not.
       (assert (= (run (python-assignment-reliances
                         "a.py" (ast.parse "import hy.importer\nhy.importer._could_be_hy_src = f\nx.y = 1\nhy = 2\ndef g():\n    hy.compat.rewriting_unparse = h\n")))
                  [#("a.py" "replaced" "hy.importer" "_could_be_hy_src")
                   #("a.py" "replaced" "hy.compat" "rewriting_unparse")]))
       ;; A defmacro receiving the compiler and reads of compiler attributes (the reader also reads dotted symbols as (. compiler name)) and method calls
       ;; (._syntax-error compiler …) (blind test B's failure case) are counted; attributes and method calls of things other than the compiler, and defmacros whose first argument is not the compiler,
       ;; are not.
       (assert (= (run (hy-form-reliances
                         "m.hy" (list (hy.read-many (+ "(defmacro m [_hy-compiler x] (. _hy-compiler this))\n"
                                                       "(defn f [compiler] compiler.filename (isinstance (. compiler scope) X) (. other scope))\n"
                                                       "(defn g [compiler form] (raise (._syntax-error compiler form \"m\")) (.append other compiler))\n"
                                                       "(defmacro n [x] x.y)\n")))))
                  [#("m.hy" "compiler-attribute" "_hy_compiler" "_syntax_error")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "filename")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "scope")
                   #("m.hy" "compiler-attribute" "_hy_compiler" "this")
                   #("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")]))
       (assert (.search HY-COMPILER-HINT "(defmacro val [_hy-compiler #* args]"))
       (assert (not (.search HY-COMPILER-HINT "(defn f [my_hy_compiler_x])")))
       (assert (.search PY-HY-ASSIGN-HINT "hy.importer._could_be_hy_src = _could_be_hy_src"))
       (assert (not (.search PY-HY-ASSIGN-HINT "hy.importer._could_be_hy_src == f")))
       ;; light_stack's patch: green if applying Hy's read to the row light_stack returns yields a frame. The shape of the row is owned by light_stack —
       ;; for a one-frame tuple row only [n][0] is green and [n].frame and [n][3] are red; for a row that also answers .frame, [n].frame is green too
       ;; (blind test A's failure case: [n][0] used to be held as a copy, and it stayed red even after the row was fixed). 0 uses, no module, and Hy-language source are red.
       (val frame (sys._getframe))
       (val tuple-row #(frame))
       (val named-row ((collections.namedtuple "NamedRow" ["frame"]) frame))
       (val frame-only "import inspect\ndef f(n):\n    return inspect.stack(0)[n + 1][0]\n")
       (val attribute-source (.replace frame-only "[n + 1][0]" "[n + 1].frame"))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" frame-only tuple-row)) []))
       (val attribute-read (run (stack-call-violations "l.py" "hy.macros" attribute-source tuple-row)))
       (assert (and (= (len attribute-read) 1) (in "inspect.stack(0)[n + 1].frame" (get attribute-read 0))) attribute-read)
       (assert (= (len (run (stack-call-violations "l.py" "hy.macros" (.replace frame-only "[n + 1][0]" "[n + 1][3]") tuple-row))) 1))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" attribute-source named-row)) []))
       (assert (= (run (stack-call-violations "l.py" "hy.macros" frame-only named-row)) []))
       (assert (= (len (run (stack-call-violations "l.py" "hy.macros" "import inspect\nrows = inspect.stack()\n" tuple-row))) 1))
       (assert (in "does not use inspect.stack" (get (run (stack-call-violations "l.py" "hy.macros" "def f():\n    pass\n" tuple-row)) 0)))
       (assert (in "there is no module hy.macros" (get (run (stack-call-violations "l.py" "hy.macros" None tuple-row)) 0)))
       (assert (in "cannot be read as Python" (get (run (stack-call-violations "l.py" "hy.core.util" "(get (.stack inspect) (+ n 1) 0)" tuple-row)) 0)))
       ;; The row passed to the judgment is the real one from doeff-hy's light_stack (the row returned for calls from hy.compiler), and the current light_stack row returns a frame at [0].
       (assert (isinstance (get (run (light-stack-sample-row "hy.compiler")) 0) types.FrameType))
       (assert (in "scope" (get (run (reliance-violations [#("m.hy" "compiler-attribute" "_hy_compiler" "scope")] "9.9.9" None)) 0)))
       (val not-passed (run (reliance-violations [#("m.hy" "compiler-parameter" "hy.macros.macroexpand" "_hy_compiler")]
                                                 "0.27.0" "HyEvalError: 'NoneType' object has no attribute '__dict__'")))
       (assert (and (in "_hy_compiler" (get not-passed 0)) (in "HyEvalError" (get not-passed 0))) not-passed)
       ;; Counterexample (design-validation blind test B): an upper-bound row with a condition (marker), and a copy of the upper bound in a distribution that pulls in doeff-hy, are red. Declarations that do not constrain from above are green.
       (assert (= (len (run (hy-cap-violations ["hy>=1.2.0,<=1.3.1; python_version < '3.15'"] "1.3.1"))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy>=1.2.0,<=1.3.1")]))) 1))
       (assert (= (len (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy~=1.3")]))) 1))
       (assert (= (run (hy-caps-outside-doeff-hy [#("packages/doeff-adr/pyproject.toml" "hy>=1.2.0")
                                                  #("pyproject.toml" "hy>=1.2.0,!=1.3.0")])) []))
       ;; Counterexample: the hy declaration of a distribution that does not pull in doeff-hy (doeff-domain) is red unless it has the same version specification as doeff-hy's hy row
       ;; — no upper bound, a different bound, with a condition. A difference only in textual order is the same specification and green.
       (val cap-rows ["doeff>=0.2.0" "hy>=1.3.0,<=1.3.1"])
       (val domain "packages/doeff-domain/pyproject.toml")
       (assert (= (run (hy-mirror-violations [#(domain "hy<=1.3.1,>=1.3.0")] cap-rows)) []))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.2.0")] cap-rows))) 1))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.3.0,<=1.3.0")] cap-rows))) 1))
       (assert (= (len (run (hy-mirror-violations [#(domain "hy>=1.3.0,<=1.3.1; python_version < '3.15'")] cap-rows))) 1))
       ;; How distributions that do not reach doeff-hy are counted: hy in distributions that reach it through [project] dependencies (including those reaching at the 2nd level) is not counted; hy in those that reach it only
       ;; through an extra, and in those that do not reach it, is counted. doeff-hy itself is not counted.
       (val repo (/ tmp-path "repo"))
       (for [#(rel text) [#("pyproject.toml" "[project]\nname = \"root\"\ndependencies = [\"hy>=1.0\"]\n[dependency-groups]\ndev = [\"doeff-hy\"]\n")
                          #("packages/doeff-hy/pyproject.toml" "[project]\nname = \"doeff-hy\"\ndependencies = [\"hy>=1.3.0,<=1.3.1\"]\n")
                          #("packages/near/pyproject.toml" "[project]\nname = \"near\"\ndependencies = [\"hy>=1.0\", \"doeff_hy\"]\n")
                          #("packages/far/pyproject.toml" "[project]\nname = \"far\"\ndependencies = [\"hy>=1.0\", \"near[x]>=0.1\"]\n")
                          #("packages/extra/pyproject.toml" "[project]\nname = \"extra\"\ndependencies = [\"hy>=1.0\"]\n[project.optional-dependencies]\nx = [\"doeff-hy\"]\n")]]
         (.mkdir (. (/ repo rel) parent) :parents True :exist-ok True)
         (.write-text (/ repo rel) text :encoding "utf-8"))
       (assert (= (run (doeff-hy-free-hy-declarations repo))
                  [#("pyproject.toml" "hy>=1.0") #("packages/extra/pyproject.toml" "hy>=1.0")])))]
  :plans ["docs/design/hy-fresh-bytecode/artifacts/v1/design.md"])
