;;; Executable ADR: there is one store to look up and add bytecode (the code inside a .pyc, and the import names the closure walk reads) — doeff-hy's
;;; doeff_hy_bytecode_guard/_code_store.py. The key is only the source content and what changes how that content is read (for Hy source: the module name and the Hy version; the Python version tag;
;;; the optimization level), independent of the version (commit) and the tree path. The content of the macro origins is not in the key; the record on the hit code is checked against
;;; the macro files of the current environment (rebaked on mismatch). One key holds entries side by side per macro environment, and rebaked code is added as the current environment's entry (R2b — it does
;;; not overwrite other environments' entries).
;;;
;;; This is fixed on the side of making runtime-environment preparation faster: the worker's bytecode storage is made content-addressable across versions.
;;;
;;; Rollback: revert the commit that added this ADR (one ADR file, and the worker's bytecode preparation goes back to carrying over from the previous root). The store's
;;; contents may be deleted (they are simply rebuilt when missing).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk val var <-])
(import doeff [run])
(import hy)
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Ledger

;; The store's entry point (the single definition of the layout, key, use marks and handling of broken entries).
(val ENTRY "packages/doeff-hy/src/doeff_hy_bytecode_guard/_code_store.py")

;; The worker's bytecode preparation tools (where the entry point is read by path as a file of the worker's version) and the boot script (where the store default is set).
(val PREPARE-TOOL "packages/doeff-cluster/src/doeff_cluster/_worker/entry/code_prepare.hy")
(val POOL-TOOL "packages/doeff-cluster/src/doeff_cluster/_foundation/bytecode_pool.hy")
(val BOOT-SCRIPT "packages/doeff-cluster/deploy/boot.sh")
(val UPKEEP "packages/doeff-cluster/src/doeff_cluster/_worker/core/env_upkeep.hy")

;; Marker of spelling a store entry (the kind-suffix strings .code and .imports — this spelling outside the entry point is a second store). doeff-hy-check's
;; expansion cache (doeff_hy/_static_cache — type-check expansions, .json and .txt) is not a bytecode store, so it does not match.
(val ENTRY-SUFFIX (re.compile r"[\"']\.(?:code|imports)[\"']"))
;; Marker of a tool argument that hands the tools a list of changed paths from outside (the old carry-over form — --changed). Since R3c the carry-over
;; decides what is unchanged by file identity inside the baking tool, so no such list exists.
(val CARRY-ARGUMENT (re.compile r"\"--changed\""))

;; Marker of knowledge of where .pyc files live (the __pycache__ dir name, the .pyc suffix, functions computing a .pyc path from a source). Inside doeff-cluster only the
;; baking tool's modules and the simulated world may spell it (R3b — if the tree-placing translation or a preparation program places .pyc, that is a second carry-over from the previous root).
(val PLACEMENT-TOKEN (re.compile r"[\"']__pycache__[\"']|[\"']\.pyc[\"']|\bpyc[-_]path\b|\bcache[-_]from[-_]source\b"))
(val CLUSTER-SRC "packages/doeff-cluster/src/doeff_cluster")
(val BYTECODE-TOOL-FILES #{"packages/doeff-cluster/src/doeff_cluster/_foundation/bytecode_pool.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_worker/entry/code_prepare.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_worker/core/code_prepare.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_worker/core/code_plan.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_worker/core/bake_plan.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_worker/intent/code_model.hy"
                           "packages/doeff-cluster/src/doeff_cluster/_sim/env_world.hy"})
;; Dirs the scan does not descend into (worktrees, venvs, build outputs), and where checks and docs live.
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__" "tests" "conformance"})
(val SCAN-SKIP-PREFIXES #("docs/" ".github/"))


;; ---------------------------------------------------------------------------
;; Judgments (pure functions)

(defk spells-store-layout [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether the source spells a store entry itself (the kind suffixes .code and .imports); comment lines starting with ; or # are not counted."
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) #(";" "#"))) line)))
  (bool (.search ENTRY-SUFFIX code)))


(defk layout-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "From the set found of files spelling a store entry, list those that are not the entry point (a second store), and the entry point not having the spelling."
  (+ (lfor rel (sorted found) :if (!= rel ENTRY)
           f"{rel}: a second store spelling bytecode store entries itself — use entry-path, CODE-SUFFIX and IMPORTS-SUFFIX of {ENTRY}")
     (if (in ENTRY found) [] [f"{ENTRY}: the entry point does not hold the store layout (the entry point is gone)"])))


(defk carries-from-a-previous-tree [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether the source of a bytecode preparation tool has an argument that takes a list of changed paths from outside (--changed — R3c); comment lines starting with ; are not counted."
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) ";")) line)))
  (bool (.search CARRY-ARGUMENT code)))


(defk spells-bytecode-placement [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether the source spells knowledge of where .pyc files live (PLACEMENT-TOKEN); comment lines starting with ; or # are not counted — the R3b judgment."
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) #(";" "#"))) line)))
  (bool (.search PLACEMENT-TOKEN code)))


(defk placement-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "From the set found of files inside doeff-cluster spelling where .pyc files live, list those that are not the baking tool's modules or the simulated world (BYTECODE-TOOL-FILES)."
  (lfor rel (sorted found) :if (not-in rel BYTECODE-TOOL-FILES)
        f"{rel}: spells where .pyc lives (__pycache__, .pyc, pyc-path) — only the baking tool places .pyc in a root (via code_store's link-pyc). This becomes a second path carrying .pyc over from the previous root"))

(defk module-name-of [part]
  {:pre [(: part hy.models.Object)] :post [(: % (| str None))] :tags {:context "doeff-build-adr" :role "judgment"}}
  "If one part of an import or require form is a module name, return its spelling (a.b.c) — Hy reads a dotted name a.b as the form (. a b),
   so looking only at Symbols misses dotted names. Parts that are not names (a list of imported names [x y] and so on) give None."
  (cond (isinstance part hy.models.Symbol) (str part)
        (and (isinstance part hy.models.Expression) (> (len part) 1) (= (str (get part 0)) ".")
             (all (gfor p (cut part 1 None) (isinstance p hy.models.Symbol))))
        (.join "." (gfor p (cut part 1 None) (str p)))
        True None))


(defk root-doeff-names [text]
  {:pre [(: text str)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "List the module names starting with doeff among those the top-level import and require forms of a Hy source pull in — the R7 judgment (the baking tool runs on the root's
   venv python, so a doeff name it pulls in is the root version's doeff)."
  (var names [])
  (for [form (hy.read-many text)
        :if (and (isinstance form hy.models.Expression) form (in (str (get form 0)) #("import" "require")))
        part (cut form 1 None)]
    (<- name (module-name-of part))
    (when (and (is-not name None) (.startswith (hy.mangle name) "doeff"))
      (:= names (+ names [name]))))
  names)

;; ---------------------------------------------------------------------------
;; Foundation (reads files)

(defk store-layout-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "Collect the relative paths of Python and Hy files in the repo that spell a store entry (excluding checks, docs, worktrees and venvs)."
  (var found #{})
  (for [p (sorted (.rglob repo-root "*"))
        :setv rel (.relative-to p repo-root)
        :if (and (in p.suffix #(".py" ".hy"))
                 (not (& (set rel.parts) SCAN-SKIP-PARTS))
                 (not (.startswith (.as-posix rel) SCAN-SKIP-PREFIXES))
                 (not (.is-symlink p))
                 (.is-file p))]
    (<- hit bool (spells-store-layout (.read-text p :encoding "utf-8" :errors "replace")))
    (when hit
      (:= found (| found #{(.as-posix rel)}))))
  (frozenset found))


(defk placement-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "Collect the relative paths of doeff-cluster source files (Python and Hy under CLUSTER-SRC) that spell knowledge of where .pyc files live."
  (var found #{})
  (for [p (sorted (.rglob (/ repo-root CLUSTER-SRC) "*"))
        :if (and (in p.suffix #(".py" ".hy")) (not (.is-symlink p)) (.is-file p) (not-in "__pycache__" p.parts))]
    (<- hit bool (spells-bytecode-placement (.read-text p :encoding "utf-8" :errors "replace")))
    (when hit
      (:= found (| found #{(.as-posix (.relative-to p repo-root))}))))
  (frozenset found))


(defadr ADR-DOE-BUILD-002
  :title "There is one store to look up and add bytecode: doeff-hy's code_store. The key is only the source content and what changes how it is read, independent of the version, and macro origins are checked through the record on the hit code. The worker's bytecode preparation writes from the store and bakes only what changed in content; on top of the store it carries over the unchanged files from the root the tree was copied from (R3c)"
  :status "accepted"
  :scope ["packages/doeff-hy/src/doeff_hy_bytecode_guard/_code_store.py"
          "packages/doeff-hy/src/doeff_hy_bytecode_guard/_loader_hooks.py"
          "packages/doeff-cluster/src/doeff_cluster/_worker/entry/code_prepare.hy"
          "packages/doeff-cluster/src/doeff_cluster/_foundation/bytecode_pool.hy"
          "packages/doeff-cluster/src/doeff_cluster/_worker/core/bake_plan.hy"
          "packages/doeff-cluster/deploy/boot.sh"
          "docs/adr/defadr_doeff_build_002_one_bytecode_store.hy"]
  :problem
    [(fact
       "The full test runs at most once every 4 hours, and it is made certain that no single test exceeds 30 seconds.")
     (fact
       "Building images also uses the Makefile and cache layers, with no wasted rebuilds.")
     (fact
       "The fix (decided 2026-10-07 01:5x, reversible): test_same_program_daily.hy is fixed on the side of making runtime-environment preparation faster — the worker's bytecode storage is made content-addressable across versions (when the version changes, only changed files are rebaked). Dependency and copy storage also persist between daily runs. For the failure-case tests, the restart limit inside the test is shortened.")
     (fact
       "Measured (daily Pod): of 162 seconds for the normal case of test_same_program_daily.hy, 148.6 seconds were runtime-environment preparation, and bytecode preparation of 1,130 files took 94.7 seconds. On node-h locally (nice 10) too, of 153.8 seconds for the normal case, the bytecode processing stage took 123.9 seconds (closure walk 61.3 seconds, baking 59.0 seconds). The test worker started on an empty /work and had no root to carry over from, so it baked everything."
       :evidence "The bytecode field of the completion marker of the local measurement")
     (fact
       "The form before the fix: worker preparation placed .pyc only inside the root's tree, and the next version hardlinked the .pyc of unchanged files (outside git diff) from the completed root's tree. The closure walk's import names were also reused from a table left at the root of the carry-over tree. Choosing the carry-over source was limited to the same Python and Hy versions and a completed root of the same repo, so a new worker, an empty /work, after a root was deleted, and a single-node test cluster rebaked everything. A store for identical code content existed only in doeff-hy's import hook (DOEFF_HY_CODE_STORE), and the preparation tool did not use it."
       :evidence "carry-source in packages/doeff-cluster/src/doeff_cluster/_worker/core/env_prepare.hy (the version before this change), --from and --changed in worker/entry/code_prepare.hy")
     (fact
       "Measured (2026-10-12 09:0x JST, node-h): runtime-environment preparation rebaked 100-600 Hy bytecode files per run even for source of the same version (before 02:53 JST, 0-10 files and 10-40 seconds of bytecode stage → after, 30-600 files and 40-550 seconds; in the 06:00 hour, 6 runs rebaked 2,763 files in 1,366 seconds). With one entry per key, the preparation (the worker's tool — record version 6) and imports in worktrees (inheriting DOEFF_HY_CODE_STORE from the worker; the doeff version differs per worktree — record version 5 and others) each saw the other's entry for the same key as stale, rebaked and overwrote each other. Checking the entries of the 1,094 Hy sources of the latest root in that root's environment: 797 usable, 295 unusable with record version 5 (all written by worktrees), 2 with mismatched digests.")]
  :context
    [(interpretation
       "Not rebaking source whose content is unchanged across versions can be guaranteed only when a store keyed by content alone is looked up through the same single entry point by both the preparation tool and the import hook. Carrying over from the previous tree works only when the source root exists, and whether it exists depends on worker state and cleanup — tying the key to a version or a tree always leaves cases where it does not work (new workers, tests).")
     (interpretation
       "Expanding Hy source depends on the content of the macro origins, but the origins are not known until compiling. So origin content is not in the key; the record at the end of the code's constants (doeff-hy's records — the origin module names and the sha256 of their content) is checked at use time against the files re-resolved through the current environment's import path. On mismatch, rebake and rewrite the entry of the same key. Compiling Python source does not depend on the module name or the Hy version, so its key is only the content, the Python version tag and the optimization level.")
     (interpretation
       "The preparation tool runs on the venv python of the root being prepared and imports the root version's doeff. The root version's doeff-hy may be a version without the store's entry point, so the tool reads the worker version's doeff-hy code_store.py by path (placed the same way as bake_plan). The entry point imports only the standard library.")
     (interpretation
       "The store's layout and cleanup follow the same practice as the native wheel store (ADR-DOE-BUILD-001): <dir>/<key head>/<key rest><kind> (source code goes under the key's dir per macro environment — R2b), the time is advanced on every use, the worker's cleanup removes what is unused for 7 days, writes replace from a temporary file, and broken entries are named, removed and rebuilt. The store dir is env DOEFF_HY_CODE_STORE, and the worker's boot script defaults it to $WORK_DIR/state/doeff-hy-code-store (the same dir as the daily full-verification task).")]
  :decision
    [(rule R1 "There is one store to look up and add bytecode: doeff-hy's code_store.py. Only it spells the layout, key, use marks and handling of broken entries; the import hook (loader_hooks) and the worker's preparation tools (code_prepare, bytecode_pool) call its functions.")
     (rule R2 "Code key = source content, Python version tag and optimization level; for Hy source also the module name and the Hy version. The content of the macro origins is verified by checking the record on the hit code against the files of the current environment; on mismatch, rebake and rewrite the entry of the same key.")
     (rule R2b "Addendum to R2 (2026-10-12, root fix): one key's dir holds code entries side by side per macro environment — <dir>/<first 2 chars of key>/<key rest>/<record fingerprint>.code (key tag doeff-hy/code-store/3). Record fingerprint = the first 32 chars of the sha256 of the end of the code's constants (doeff-hy's record) (code_store.code-fingerprint — it does not interpret the record's fields, so the same construction works for records of a different record version). The reader checks in order of most recent use, uses the first that matches, and advances only that entry's time. Rebaked code is added as the current environment's entry, without overwriting other environments' entries (entries with unreadable records are not removed either — they belong to writers of other versions). An added entry's time is set before every other entry of the key (but not more than 1 day before now), and moves forward only once checked and used. One key holds up to 16 entries (CODE-ENTRIES-PER-KEY); beyond that, the entry just added is kept and the oldest by time are removed — entries of one-off environments (checks in worktrees editing macros) remove each other and do not push out entries in continuing use. Even if more environments than the limit use the same key in turn, only the excess misses (design-validation blind test A — in a form that put the added entry at the head, with 17 environments every round missed entirely). R2's 'rewrite the entry of the same key' is read as rewriting the entry with the same record. Reason: with one entry per key, writers on different doeff versions (runtime-environment preparation and worktree imports) overwrote the same key alternately and rebaked each other's work (the measurement above). Failure cases = test_two_macro_versions_taking_turns_on_one_store_stop_expanding_from_the_second_round and test_two_record_generations_taking_turns_on_one_store_keep_each_others_entries (blind test B) in packages/doeff-hy/tests/test_bytecode_guard.py, test_e_two_macro_versions_taking_turns_do_not_rebake_each_other in packages/doeff-cluster/tests/test_code_prepare_content_store.py, and packages/doeff-hy/tests/test_code_store_entries.py.")
     (rule R2c "Addendum to R2b (2026-10-12): when a worktree's .pyc matches the source but that .pyc's record does not match the current environment's macros, the import hook looks up the key's entries with the same check as R2b before recompiling. If a matching entry exists it uses it without compiling and rewrites the .pyc in the same scheme (timestamp or hash) as the .pyc it read. If none matches, it compiles as today and adds the entry as the current environment's. Reason: when the store was not looked up merely because a .pyc existed, the first import in a worktree reused while swapping the sibling doeff (the deployment-declaration tree, a long-lived conversation tree) recompiled every Hy module of the tree even though the store had entries of the same version (2026-10-12: about 880 files and about 670 CPU seconds for collecting one agora_sim check). Failure cases = test_a_stale_pyc_in_a_reused_tree_takes_the_stores_matching_entry_without_compiling and test_a_stale_checked_hash_pyc_takes_the_stores_matching_entry_and_is_rewritten_in_the_same_form in packages/doeff-hy/tests/test_bytecode_guard.py (the no-matching-entry side = test_a_stale_pyc_with_no_matching_entry_in_the_store_compiles_and_adds_the_entry).")
     (rule R3 "The worker's bytecode preparation carries over neither .pyc nor import names from the previous version's tree (it has no --from, --changed or carry-over source selection). .pyc are written from the store's code, and only what is missing or does not match the current macros is baked and added. The closure's import names are also looked up from the store by the key of the source content (kind .imports).")
     (rule R3b "Addendum to R3 (2026-10-17, design-validation blind test B): only the baking tool places .pyc in a root's tree. Inside doeff-cluster, only the baking tool's modules (bytecode_pool, code_prepare, code_plan, bake_plan, code_model) and the simulated world hold knowledge of where .pyc lives (the spellings \"__pycache__\" and \".pyc\", pyc-path, cache_from_source). Reason: an implementation that hardlinked the source root's .pyc into a new root through a separate effect of the tree-placing translation passed both the R3 check (the tool's --from, --changed) and the tree-shape check. Carried-over .pyc do not advance the store's use marks, so the cleanup deleted the .pyc and code of the store the root used after 7 days, and stale .pyc of changed Python sources remained in the completed root (measured in blind test B — store .pyc 24 → 0, new bytes of the next root 188,416). Failure cases = this file's test-adr-doe-build-002-only-the-bytecode-tool-places-pyc and the blind-test-B lines of judgments-reject-the-counterexamples.")
     (rule R3c "Amendment to R3 (2026-11-12, root fix): the store stays the one place to look code up, and on top of it the baking tool carries over from the root the new tree was copied from, so the work of preparing a new commit is the size of the difference. The runtime-environment preparation names, per tree, the tree it was copied from (--inherit) only when every repo's tree was copied from one completed root whose platform and dependency key (dependency-key — uv.lock, the other repos' commits, python, import roots) equal the new root's: the venv, the Hy version and the macro providers outside the trees are then the same. The preparation tool keeps the closure's import names in a table at each tree's root (.doeff-closure-table.json — module name and import names per visited source, tagged with the Hy version) and, for a source that is the same file (same inode) as the source tree's, takes them from the source tree's table without reading the source or the store; the others are read as in R3. From the walk it derives the changed modules (visited sources that are not the same file, and modules of the source table not confirmed the same — removed, changed or out of the closure) and hands them to the baking tool. The baking tool hardlinks the source tree's .pyc into the new tree for every source that is the same file (it alone places .pyc — R3b stands; the tree-placing translation still copies only tracked paths), and keeps a placed .pyc without the full check when its PEP 552 header is the checked-hash header of the current source and the root guard's bytecode_dependency_modules (the modules of the record's macro, provider, value and file rows — empty for Python source) shares no name with the changed modules; a .pyc whose record cannot be judged by its modules (an environment read, a mid-import digest, another Hy version, an old root guard without the function) or that depends on a changed module gets the full check (bytecode_is_current) and, if stale, the store path. For each kept .pyc whose time is older than the store's touch interval it advances the store's marks (the .pyc's time — the store's .pyc is the same file by R6 — and the code entry through code_store.keep_code, which adds the entry back when the sweep removed it), so the R3b failure (entries in use expiring) does not return. Without a source tree (new workers, an empty /work, a different dependency key) everything is written from the store as in R3. The tools take no list of changed paths from outside (law the-prepare-tool-takes-no-list-of-changed-paths). Reason: with R3 alone the work was the size of the closure, not of the commit — every new commit's root looked up and checked about 3,250 .pyc and re-read about 3,250 import-name entries although 0-8 were rebaked, which took 53-145 seconds for a root on a machine with saturated CPUs (bytecode 36-109 s, closure 7-72 s — 2026-11-10; a single thread's speed fell to 2.5-2.8 GHz, so priorities could not shorten it). Failure cases = test_a_five_changed_sources_are_the_only_work_and_the_rest_is_reused_unchecked, test_b_a_changed_macro_definition_checks_its_users_again_instead_of_reusing and test_c_without_a_source_tree_the_preparation_writes_from_the_store_as_before in packages/doeff-cluster/tests/test_code_prepare_inherits.py, and packages/doeff-hy/tests/test_bytecode_dependency_modules.py.")
     (rule R4 "The store dir is env DOEFF_HY_CODE_STORE. The worker's boot script defaults it to $WORK_DIR/state/doeff-hy-code-store and lets the caller's value take precedence (local single-node cluster tests pass the same dir across cases).")
     (rule R5 "Entries unused for 7 days are removed by the worker's cleanup (the same 7 days as native wheels — CODE-STORE-UNUSED-SECONDS = WHEEL-UNUSED-SECONDS).")
     (rule R5b "Addendum to R5 (2026-10-12): the cleanup removes, as a whole, a store dir in which neither it nor anything below it has moved for 7 days (env_store's unused-store-names). Dirs containing used entries and freshly created empty dirs are kept. Reason: R2b's key dirs grow with the number of sources per version, so removing only entries leaves empty dirs forever and the number the store walk reads keeps growing. Failure case = test-the-sweep-removes-unused-code-entries-in-key-dirs-and-the-dirs-left-unused in packages/doeff-cluster/tests/test_env_store.hy.")
     (rule R6 "Addendum to R3 (2026-10-17): the worker's baking tool places a tree's .pyc as a hardlink to a file of the same content in the store — kind .pyc, name = sha256 of the .pyc content, read-only (code_store.link-pyc). A .pyc placed from the store's code puts the entry's content right after the header as-is (code_store.current-entry — no re-marshal), and the entry of baked code is exactly the bytes after the header of the baked .pyc (add-code's data), so the .pyc of the same code is one file across roots. When it cannot be placed (a different file system, the link-count limit) it writes the content as today and logs the reason once each in the preparation record. The store's .pyc are also removed by R5's cleanup (the tree's names remain). Every hand that rewrites a tree's .pyc (the baking tool, the import hook, Python's import) replaces from a temporary file, so the hardlink targets (other trees' .pyc and the store) do not change. Nothing is carried over from the previous version's tree (R3 stands — what is shared is the store). Reason: writing .pyc of identical content anew per root meant that after a root was deleted macOS local snapshots kept that content for 24 hours, and the free space on the Mac worker's work disk dropped with each root rebuild (on 10-17 one Mac root privately held 100.6 MB of tree .pyc). Failure cases = test_f_trees_baked_from_the_store_share_one_file_per_pyc, test_g_rebaking_one_tree_does_not_write_through_the_shared_pyc and test_h_a_one_commit_root_writes_a_tenth_or_less_where_cp_refuses_gnu_options in packages/doeff-cluster/tests/test_code_prepare_content_store.py, and packages/doeff-hy/tests/test_code_store_pyc_links.py.")
     (rule R7 "Addendum to R1 (2026-11-01): the worker's baking tool (bytecode_pool) runs on the root's venv python but imports no names from the root version's doeff packages (not doeff, doeff_core_effects, or doeff-hy's macros — only the standard library and hy). The tool builds the .pyc path and the PEP 552 header itself. Compiling Hy source (attaching the record) and checking existing .pyc and store code use the root's guard (the record form and the way of checking are the root version's); the guard is not looked up by module name — the tool takes from sys.modules the module owning the hooks the root's guard installed on SourceFileLoader at startup (get_code, source_to_code), and calls its bytecode_is_current and source_to_code_as_import. On the guard side, the module owning the hooks keeps exporting these two names with the same argument shapes (the owner module's name may change). Reason: the tool imported guard and python_bytecode by module name, so the worker version's and the root version's doeff became coupled; on a root of a doeff that had moved those names inside (2026-10-29), the old worker's preparation always failed with an import error, and a root of the new version could not be placed for an hour (2026-11-01). Failure cases = packages/doeff-cluster/tests/test_bytecode_pool_reads_no_root_doeff.py, packages/doeff-hy/tests/test_guard_hook_owner_contract.py, and this file's test-adr-doe-build-002-the-bake-tool-imports-no-doeff-name.")
     (rule R8 "Addendum to R1 (2026-11-08, root fix — the store was closed per machine): the source-code entries are also kept in one shared store for the cluster — a bucket of an S3-API object store named by the file env DOEFF_HY_CODE_SHARE points at (three lines: http(s)://<host>/<bucket>[/<prefix>][?region=], key id, secret key); unset or no file = no shared store. The worker's boot script defaults the path to code-share in the read-key dir (the Secret worker-repos on k8s), so adding that key reaches running workers at their next preparation without recreating Pods (a running container's environment never changes). Object names mirror the store dir (<prefix>/<first 2 chars of key>/<key rest>/<record fingerprint>.code) and only code_store spells them (share-from-env, fetch-shared-code, publish-code — R1). The worker's baking tool, for an entry missing or not current in the machine's dir, fetches the key's shared entries newest first before baking, uses one only after the same record check as a local entry (and keeps it in the machine's dir as the local copy), and puts each entry it bakes right away. It prepares in two passes (first write everything the two stores have without baking, then bake the rest, fetching once more just before each) so that a machine with few bake jobs takes what a machine with many jobs, preparing the same root at the same time, has put. An entry that is not code or whose name is not its code's fingerprint is not used (named; the next writer of that environment overwrites it). A shared store that is not reachable, refuses the key or answers with a server error is named and not asked again for the run (a child process: for its rest), and the machine bakes by itself — preparation never fails because of the shared store. The report lines carry fetched= (tree line after failed=, total line at the end) and the preparation record's bake line carries fetched=. Only the baking tool uses the shared store; the import hook looks up the machine's dir only. Reason: the key does not depend on the machine, yet every machine baked the same entries (2026-11-07: node-d, node-f, node-g and node-b each baked the same 245 files of root 699218c4 in 166-979 seconds), so a small node's preparation time was the number of misses times its own per-file time (one job, ~0.69 s — ~2,190 s for the full ~3,150-file plan, over the round's 1,800-second warm stage).")]
  :laws
    [(law one-bytecode-store-entry
       :statement "for_all file f in doeff (excluding tests and docs): spells_store_layout(f) => f == ENTRY; and spells_store_layout(ENTRY)"
       :counterexamples
         [(counterexample "The import hook (loader_hooks) spells os.path.join(store, key[:2], key[2:] + \".code\") itself (the form before this change — the preparation tool could not look up the same store)")
          (counterexample "A worker tool puts a .pyc cache in its own dir (a third store)")])
     (law the-prepare-tool-takes-no-list-of-changed-paths
       :statement "not carries_from_a_previous_tree(PREPARE-TOOL) and not carries_from_a_previous_tree(POOL-TOOL)"
       :counterexamples
         [(counterexample "--changed <list of diffs> among the tools' arguments (the pre-#3858 form: a list handed in by the caller decided what was unchanged, a second account of the tree beside the tree itself — R3c decides by file identity inside the baking tool)")])
     (law only-the-bytecode-tool-places-pyc
       :statement "for_all file f in packages/doeff-cluster/src: spells_bytecode_placement(f) => f in BYTECODE-TOOL-FILES"
       :counterexamples
         [(counterexample "Adding (val PYCACHE-DIR \"__pycache__\") to the tree-placing translation (env_translation) and an effect answer that places the source tree's .pyc into the new root with LinkFile (blind test B — it passed the R3 check and the tree-shape check, the store's use marks did not advance, and the cleanup deleted .pyc in use)")])
     (law the-worker-defaults-the-store-under-its-state
       :statement "boot.sh exports DOEFF_HY_CODE_STORE defaulting to $WORK_DIR/state/doeff-hy-code-store, and the prepare tool reads the store entry from the worker's own doeff-hy"
       :counterexamples
         [(counterexample "The boot script sets no default and the worker tool writes to the cache in the user's HOME (a dir that disappears in a Pod)")])
     (law the-bake-tool-imports-no-doeff-name
       :statement "root_doeff_names(POOL-TOOL) == []"
       :counterexamples
         [(counterexample "The baking tool pulls the check in with (import doeff_hy_bytecode_guard [bytecode-is-current]) (the 2026-11-01 form — preparation always failed on a root of a doeff that had moved the entry's names inside)")
          (counterexample "The baking tool writes definitions with (require doeff-hy.macros [defk val]) (the macros become the root version's doeff-hy's)")])]
  :enforcement
    [(deftest test-adr-doe-build-002-one-bytecode-store-entry
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (store-layout-files repo-root)))
       (val violations (run (layout-violations found)))
       (assert (= violations []) (+ "a second bytecode store (ADR-DOE-BUILD-002 R1): " (str violations))))
     (deftest test-adr-doe-build-002-the-prepare-tool-takes-no-list-of-changed-paths
       (val repo-root (. (Path __file__) parent parent parent))
       (for [rel #(PREPARE-TOOL POOL-TOOL)]
         (assert (not (run (carries-from-a-previous-tree (.read-text (/ repo-root rel) :encoding "utf-8"))))
                 (+ rel ": an argument taking a list of changed paths from outside remains (ADR-DOE-BUILD-002 R3c)"))))
     (deftest test-adr-doe-build-002-only-the-bytecode-tool-places-pyc
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (placement-files repo-root)))
       (val violations (run (placement-violations found)))
       (assert (= violations []) (+ "a second path placing .pyc in a root (ADR-DOE-BUILD-002 R3b): " (str violations)))
       (assert (in "packages/doeff-cluster/src/doeff_cluster/_foundation/bytecode_pool.hy" found) "the baking tool's spelling was not found — the scan range is broken"))
     (deftest test-adr-doe-build-002-the-worker-defaults-the-store-under-its-state
       (val repo-root (. (Path __file__) parent parent parent))
       (val boot (.read-text (/ repo-root BOOT-SCRIPT) :encoding "utf-8"))
       (assert (in "export DOEFF_HY_CODE_STORE=\"${DOEFF_HY_CODE_STORE:-$WORK_DIR/state/doeff-hy-code-store}\"" boot)
               "the boot script does not default the store under the worker's state (ADR-DOE-BUILD-002 R4)")
       (val tool (.read-text (/ repo-root PREPARE-TOOL) :encoding "utf-8"))
       (assert (in "\"doeff-hy\" \"src\" \"doeff_hy_bytecode_guard\" \"_code_store.py\"" tool)
               "the preparation tool does not read the store entry point of the worker version's doeff-hy (ADR-DOE-BUILD-002 R1)")
       (val upkeep (.read-text (/ repo-root UPKEEP) :encoding "utf-8"))
       (assert (in "(val CODE-STORE-UNUSED-SECONDS WHEEL-UNUSED-SECONDS)" upkeep)
               "the store's cleanup days are not the same as native wheels' (ADR-DOE-BUILD-002 R5)"))
     (deftest test-adr-doe-build-002-the-bake-tool-imports-no-doeff-name
       (val repo-root (. (Path __file__) parent parent parent))
       (<- names list (root-doeff-names (.read-text (/ repo-root POOL-TOOL) :encoding "utf-8")))
       (assert (= names []) (+ POOL-TOOL ": imports names of the root version's doeff (ADR-DOE-BUILD-002 R7): " (str names)))
       ;; Counterexample: the 2026-11-01 form (looking up guard by module name) and requiring doeff-hy's macros are red. Names inside comments are not counted.
       (<- old list (root-doeff-names (+ "(require doeff-hy.macros [defk val])\n(import os)\n(import doeff_hy_bytecode_guard [bytecode-is-current])\n"
                                         "(import doeff_core_effects._python_bytecode [pyc-path])\n")))
       (assert (= old ["doeff-hy.macros" "doeff_hy_bytecode_guard" "doeff_core_effects._python_bytecode"]) (str old))
       (<- clean list (root-doeff-names ";; (import doeff [run])\n(import importlib.machinery)\n(import hy)\n"))
       (assert (= clean []) (str clean)))
     (deftest test-adr-doe-build-002-judgments-reject-the-counterexamples
       ;; Counterexample: spelling an entry outside the entry point (the .code and .imports suffixes in Python and Hy) is red as a second store. Spellings on comment lines are not counted.
       ;; Arguments carrying over from the previous version's tree are red.
       (assert (run (spells-store-layout "entry = os.path.join(store, key[:2], key[2:] + '.code')\n")))
       (assert (run (spells-store-layout "(val path (posixpath.join store (cut key 0 2) (+ (cut key 2 None) \".imports\")))\n")))
       (assert (not (run (spells-store-layout "# the \".code\" entry is spelled by code_store\n"))))
       (assert (not (run (spells-store-layout "return cache_dir / key[:2] / f\"{key}.json\"\n"))))
       (val second "packages/doeff-cluster/src/doeff_cluster/_worker/core/pyc_cache.hy")
       (assert (= (len (run (layout-violations (frozenset #{ENTRY second})))) 1))
       (assert (= (run (layout-violations (frozenset #{ENTRY}))) []))
       (assert (= (len (run (layout-violations (frozenset #{second})))) 2))
       (assert (run (carries-from-a-previous-tree "(.add-argument parser \"--changed\" :action \"append\")\n")))
       (assert (not (run (carries-from-a-previous-tree ";; formerly carried over with \"--changed\"\n"))))
       (assert (not (run (carries-from-a-previous-tree "(.add-argument parser \"--inherit\" :action \"append\")\n"))))
       ;; Counterexample (R3b, blind test B): a line where the tree-placing translation spells where .pyc lives is red. Comment lines and the word __pycache__ inside prose (without quotes) are not counted.
       (assert (run (spells-bytecode-placement "(val PYCACHE-DIR \"__pycache__\")\n")))
       (assert (run (spells-bytecode-placement "(when (.endswith e.name \".pyc\") (<- (LinkFile a b)))\n")))
       (assert (run (spells-bytecode-placement "(import doeff_core_effects._python_bytecode [pyc-path])\n")))
       (assert (run (spells-bytecode-placement "cache = importlib.util.cache_from_source(path)\n")))
       (assert (not (run (spells-bytecode-placement ";; untracked things (.venv, __pycache__) are not copied\n"))))
       (assert (not (run (spells-bytecode-placement "\"untracked things (.venv, __pycache__, baked marks) are not copied\"\n"))))
       (val tree "packages/doeff-cluster/src/doeff_cluster/_worker/protocol/env_translation.hy")
       (assert (= (len (run (placement-violations (frozenset #{tree "packages/doeff-cluster/src/doeff_cluster/_foundation/bytecode_pool.hy"})))) 1))
       (assert (= (run (placement-violations (frozenset #{"packages/doeff-cluster/src/doeff_cluster/_worker/core/code_plan.hy"}))) [])))]
  :plans [])
