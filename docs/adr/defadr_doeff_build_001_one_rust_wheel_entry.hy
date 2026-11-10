;;; Executable ADR: there is one entry that builds and fetches the Rust components (doeff-vm, doeff-vm-core, doeff-linter,
;;; doeff-effect-analyzer, doeff-indexer) — the build backend tools/doeff_cargo_backend.py always looks up "the wheel store keyed by the hash of the source contents" before building.
;;; The Rust CLI component that answered the workflow list was removed on 2026-10-30 (ADR-DOE-API-001 R28).
;;;
;;; Keep one entry, and remove the old path in the same change that replaces it.
;;;
;;; Entries to be moved (PENDING-ENTRIES): one place still owns its key and store — the binary location of doeff-linter for the commit hook
;;; (linter_snapshot). A new entry not in the ledger is red; a change that removes an entry but leaves its ledger row is red (the removing change deletes the row in the same commit).
;;; The native wheel of the doeff-cluster worker (git tree hash key, state/wheels — the runtime-environment preparation and the boot script) was reduced to
;;; only passing through the entry with `uv build --wheel`, and its 4 ledger rows were deleted (R7).
;;;
;;; Rollback: revert the commit that added this ADR (the one ADR file, and the store shape of the build backend, go back to before).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <- val var])
(import doeff [run])
(import ast)
(import re)
(import pathlib [Path])


;; ---------------------------------------------------------------------------
;; Ledger

;; The entry itself (the backend that looks up the store before building).
(val ENTRY "tools/doeff_cargo_backend.py")

;; Backends that only hand the entry "how to build when missing" (their build hooks call the entry's wheel_from_store and editable_from_store).
(val COMPILERS #{"packages/doeff-indexer/doeff_indexer_build_backend.py"})

;; Entries to be moved — path → reason (the removing change deletes the row in the same commit).
(val PENDING-ENTRIES
  {"packages/doeff-linter/scripts/linter_snapshot.py"
   "binary location of doeff-linter for the commit hook (key of sha and inputs, cargo build --release) — to be moved to the entry's store in a separate change"})

;; Dirs the scan does not descend into (worktrees, venvs, build outputs), and where checks and docs live (checks call the entry to verify it; docs are copies of procedures).
(val SCAN-SKIP-PARTS #{".git" ".venv" ".worktrees" "node_modules" "target" "__pycache__" "tests" "conformance"})
(val SCAN-SKIP-PREFIXES #("docs/" ".github/"))

;; Markers of the shapes that build Rust or make a key. `uv build --wheel` is a call that goes through the entry (the build backend), so it is not counted (the worker and the boot script
;; prepare the wheel with this alone).
;;   - Python: calling maturin's build hooks (maturin.build_wheel, maturin.build_editable), a command list with both "build" and "--release"
;;     (cargo build --release), a function definition whose name contains native_key.
;;   - Hy: the sequence "build" "--release" on a non-comment line, a definition (defk native-key.
(val MATURIN-HOOKS #{"build_wheel" "build_editable"})
(val BUILD-FLAGS #{"--release"})
(val HY-BUILD-ARGV (re.compile r"\"build\"\s+\"--release\""))
(val HY-KEY-DEFINITION (re.compile r"\(defk\s+native-key\b"))


;; ---------------------------------------------------------------------------
;; Judgments (pure functions)

(defk python-builds-rust [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether a Python source has a shape that builds Rust or makes a key (comments and docstrings do not count — read with ast)."
  (val tree (try (ast.parse text) (except [SyntaxError] None)))
  (when (is tree None)
    (return False))
  (for [node (ast.walk tree)]
    (match node
      (ast.Attribute :value (ast.Name :id "maturin") :attr attr) :if (in attr MATURIN-HOOKS)
        (return True)
      (| (ast.List :elts elts) (ast.Tuple :elts elts))
        (let [words (sfor e elts :if (and (isinstance e ast.Constant) (isinstance e.value str)) e.value)]
          (when (and (in "build" words) (& words BUILD-FLAGS))
            (return True)))
      (ast.FunctionDef :name name) :if (in "native_key" name)
        (return True)
      _ None))
  False)

(defk hy-builds-rust [text]
  {:pre [(: text str)] :post [(: % bool)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether a Hy source has a shape that builds Rust or makes a key (comment lines starting with ; do not count)."
  (val code (.join "\n" (gfor line (.splitlines text) :if (not (.startswith (.lstrip line) ";")) line)))
  (bool (or (.search HY-BUILD-ARGV code) (.search HY-KEY-DEFINITION code))))

(defk entry-violations [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Among the set found of files that build Rust or make a key, list those that are none of the entry, a compiler backend, or the to-be-moved ledger (a second entry), and
   report when the entry is not found."
  (+ (lfor rel (sorted found)
           :if (and (!= rel ENTRY) (not-in rel COMPILERS) (not-in rel PENDING-ENTRIES))
           f"{rel}: a second entry that builds Rust or makes a key — hand the build to {ENTRY}'s wheel_from_store and editable_from_store")
     (if (in ENTRY found) [] [f"{ENTRY}: the entry has no shape that builds Rust (the entry is gone)"])))

(defk stale-pending [found]
  {:pre [(: found frozenset)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "List files that are in the to-be-moved ledger but no longer have a shape that builds Rust or makes a key (moved to the entry, or removed) — so that the moving change deletes them
   from the ledger in the same commit."
  (lfor rel (sorted PENDING-ENTRIES)
        :if (not-in rel found)
        f"{rel}: no longer builds on its own — delete it from PENDING-ENTRIES"))

(defk compiler-hooks-use-the-store [text]
  {:pre [(: text str)] :post [(: % list)] :tags {:context "doeff-build-adr" :role "judgment"}}
  "Whether the build hooks (build_wheel, build_editable) of a compiler backend call the entry's store (wheel_from_store, editable_from_store). Answer =
   the names of the hooks that do not."
  (val wanted {"build_wheel" "wheel_from_store" "build_editable" "editable_from_store"})
  (lfor node (. (ast.parse text) body)
        :if (and (isinstance node ast.FunctionDef) (in node.name wanted))
        :if (not (any (gfor inner (ast.walk node)
                            (and (isinstance inner ast.Call) (isinstance inner.func ast.Name)
                                 (= inner.func.id (get wanted node.name))))))
        node.name))


;; ---------------------------------------------------------------------------
;; Foundation (reads files)

(defk rust-building-files [repo-root]
  {:pre [(: repo-root Path)] :post [(: % frozenset)] :tags {:context "doeff-build-adr" :role "foundation"}}
  "Collect the relative paths of the Python and Hy files in the repo that have a shape that builds Rust or makes a key (excluding checks, docs, worktrees and venvs)."
  (var found #{})
  (for [p (sorted (.rglob repo-root "*"))
        :setv rel (.relative-to p repo-root)
        :if (and (in p.suffix #(".py" ".hy"))
                 (not (& (set rel.parts) SCAN-SKIP-PARTS))
                 (not (.startswith (.as-posix rel) SCAN-SKIP-PREFIXES))
                 (not (.is-symlink p))
                 (.is-file p))]
    (<- hit bool (match p.suffix
                   ".py" (python-builds-rust (.read-text p :encoding "utf-8" :errors "replace"))
                   _ (hy-builds-rust (.read-text p :encoding "utf-8" :errors "replace"))))
    (when hit
      (:= found (| found #{(.as-posix rel)}))))
  (frozenset found))


(defadr ADR-DOE-BUILD-001
  :title "There is one entry that builds and fetches the Rust components: the build backend tools/doeff_cargo_backend.py. Before building, the backend always looks up the wheel store keyed by the hash of the source contents, and both the wheel hook and the editable hook answer from the same wheel in the same store. Do not create a second entry with its own key or store"
  :status "accepted"
  :scope ["tools/doeff_cargo_backend.py"
          "tools/rust_wheel_release.py"
          ".github/workflows/rust-wheels.yml"
          "packages/doeff-indexer/doeff_indexer_build_backend.py"
          "packages/doeff-vm/pyproject.toml"
          "packages/doeff-cluster/src/doeff_cluster/_shared/core/native_wheel.py"
          "packages/doeff-cluster/src/doeff_cluster/_worker/entry/boot_wheel.py"
          "docs/adr/defadr_doeff_build_001_one_rust_wheel_entry.hy"]
  :problem
    [(fact
       "Every update to some doeff package required building all of doeff and baking it into an image, which makes no sense. A change to doeff-cluster or another Python package should almost never need to rebuild doeff's Rust VM.")
     (fact
       "Building images also uses a Makefile and cache layers, so as not to waste rebuilds.")
     (fact
       "Counts on 2026-10-06 (investigation cc1-w38, confirmed by log lines): the daily full verification creates a new HOME per task and does not pass the wheel store to children, so it rebuilt doeff-vm about 100 times and doeff-linter 14 times (initial dependency install 6m15s to 9m30s). Only the wheel hook looked up the build backend's store; the editable hook (agora-controllers' doeff-vm, a member of the doeff workspace) fired maturin every time. The doeff-indexer backend bypassed the store and built the CLI binary and the extension module every time."
       :evidence "the header comment of tools/doeff_cargo_backend.py as of 2026-10-06: \"editable and sdist do not look up\"")
     (fact
       "Three places owned their own key and store: the build backend (hash of contents, $XDG_CACHE_HOME/doeff-cargo-wheels/<package>/<hash>/), the doeff-cluster worker (git tree hash of doeff-vm and doeff-vm-core, state/wheels/doeff-vm-<key>/), and the commit-hook linter (sha and input object ids, doeff-linter-snapshots). The worker's key added only the Python version and the machine, and ignored the rustc and maturin versions, the environment variables that change the build, and uncommitted changes in the worktree."
       :evidence "packages/doeff-cluster/src/doeff_cluster/_shared/core/native_wheel.py, packages/doeff-linter/scripts/linter_snapshot.py")
     (fact
       "2026-10-07 19:47 and 18:5x: when one machine's follow arm (a user service) ran uv sync --frozen on doeff 074c29efe in the ai live-plane slot, the build backend started `rustc -V` to make the store key, and since there was no rustc on the arm's PATH (the arm's job environment) it failed with FileNotFoundError, rc=1. The store had held a doeff-vm wheel for the same source since 05:41. Running the same argv in a hand shell (which has ~/.cargo/bin) gave rc=0 (using the stored wheel, 1 second)."
       :evidence "the 2026-10-07 investigation of the slot sync (cc3-w56), R4b")
     (fact
       "2026-10-22: when the merge-queue's declared environment installed doeff as a git dependency (rev 38bd248df), the doeff-effect-analyzer dist-info claimed 38bd248df, but the Python inside (program_effects.py, handler_effects.py) was from bba41285 — the analyzer declared no cache-keys and made its key with the default glob (Rust inputs only), so the wheel hook copied the 10-11 wheel whose Rust had not changed as-is. The fix 2641decc8 that reads clause effect aliases was missing, and 6 closure checks of the merge-queue were red. The off-machine location uses the same key names, so CI does not re-upload a wheel for a version that changed only Python."
       :evidence "the deploy/ledger/runtime venv of merge-queue 043e356, /work/state/wheels/doeff-effect-analyzer-b1fdd886a41b96a96a46248a7c5829eb")]
  :context
    [(interpretation
       "Not rebuilding Rust on a version bump where the Rust source did not change can be guaranteed only when every place that builds, however many there are, first looks up the same store with the same key. With two entries, either one entry's key misses inputs (rustc version, uncommitted changes) and fetches a stale wheel, or only one has a store and the other builds every time. The build backend is the place that uv sync, uv build, the worker's uv build --wheel and the boot script all always pass through, so placing the entry here affects all of them without counting the callers.")
     (interpretation
       "Keep the build backend's key (source contents, tool versions, machine, Python ABI, environment variables that change the build, build settings). The content hash needs no git objects and also picks up uncommitted changes. The tool.uv.cache-keys declaration becomes the one definition of both \"whether uv calls the backend\" and \"the store key\". Files under python-source are installed from the worktree by both hooks, not taken from the stored wheel (R2, R2b) — even though the key does not look at Python, stale Python is not delivered, and a Python-only change does not rebuild Rust.")
     (interpretation
       "The store layout is the same as the worker's, <package>-<key>/<wheel> with a usage marker .used, and the daily verification hands the worker's /work/state/wheels as the location. The worker's cleanup (removing dirs unused for 7 days) applies as-is, so there are not two store cleanups. The cargo target stays a temporary dir used only while building (a shared target confuses builds of worktrees with different contents, and once that wheel is placed under a key, every build with the same key fetches it).")]
  :decision
    [(rule R1 "The one entry that builds and fetches the Rust components is stored_wheel in tools/doeff_cargo_backend.py. Both build_wheel and build_editable look up the store first, and if the wheel is there, fire neither cargo nor maturin. Only when it is missing or broken do they build, store, then use it.")
     (rule R2 "The editable hook answers from the stored wheel: it does not place build products (the native extension module and what maturin's include puts into the wheel) in the source tree; it installs them as the editable wheel's contents into __editable__.<name>.native/ under the venv's site-packages (held by uv's RECORD), and returns a dist-info wheel with a .pth that adds python-source to the path and starts the finder, and a finder whose package search order is build-products dir → worktree dir. Reason: a copy that deletes files not in the tree's git listing (dotfiles remote_check) deleted the build products in the source tree, uv's records did not know whether they existed, the next uv sync did not rebuild, and import failed (the 2026-10-07 04:00 daily run). A package without python-source returns the stored wheel as-is.")
     (rule R2b "Addendum to R2 (2026-10-22): the wheel hook too, for a package with python-source, takes only the build products, data and dist-info (except RECORD) from the stored wheel, re-installs the files of the Python packages (and root modules) that the stored wheel holds from the worktree, and rewrites RECORD. It does not include the worktree's __pycache__ and .pyc, nor build products that the former shape left in the tree. Reason: for a package whose key does not look at python-source (doeff-effect-analyzer), the wheel of the same earlier Rust version was handed to a git-dependency install with the earlier Python (the 2026-10-22 fact above). A package without python-source hands over the stored wheel as-is. Failure case = test_the_wheel_hook_takes_the_python_source_from_the_tree_and_only_the_build_products_from_the_store in tests/test_wheel_store_entry.py.")
     (rule R3 "A package whose build is not maturin alone (doeff-indexer) hands how to build when missing (Compile) to wheel_from_store and editable_from_store. It owns no store or key of its own, and its build hooks do not fire maturin directly.")
     (rule R4 "Key = the contents and relative paths of the files in tool.uv.cache-keys (the default glob if not declared), the rustc and maturin versions, the machine, the ABI of the Python being built for, the environment variables that change the build, and the build settings. Every file that affects files handed over from the stored wheel (build products, data, what goes into dist-info — license-files too) is listed in the declaration. Files under python-source come from the worktree as in R2 and R2b, so they are not needed in the key (the .py, .pyi and py.typed under doeff_vm/ in doeff-vm's declaration remain as the signal for uv to redo a non-editable path install).")
     (rule R4b "Addendum to R4 (2026-10-07): drop the rustc version from the key material, and make the key without starting a child process (read the maturin version from the distribution record importlib.metadata). Reason: the key is needed before looking up the store, so with the shape that starts `rustc -V`, an environment without rustc on PATH failed at making the key even when the store had a wheel for the same source. What determines the wheel's contents is the source, the Python ABI, the machine, the environment variables that change the build (RUSTC, RUSTFLAGS, CARGO_PROFILE_* are in the key as in R4) and the build settings; the ABI exposed to Python is the C-API, so the rustc version does not leak outside the wheel. Failure case = test_the_store_answers_without_rustc_on_the_path_and_without_a_child_process in tests/test_wheel_store_entry.py.")
     (rule R4c "Addendum 2 to R4 (2026-10-11, design docs/design/rust-wheel-distribution): add the libc name and version (platform.libc_ver) to the key material. Reason: R5b's remote wheels are built on CI's glibc (ubuntu-24.04 = 2.39), so a machine with an older glibc fetching them under the same key fails at import. Only once, when the key changes, does every machine rebuild once or fetch from the remote location. Failure case = test_another_libc_points_at_another_slot in tests/test_wheel_slot_key.py.")
     (rule R5 "Store = <package>-<key>/<wheel> and .used under env DOEFF_WHEEL_CACHE (if absent, $XDG_CACHE_HOME/doeff-cargo-wheels). Before use, verify with the RECORD hashes; if broken, emit one line naming it, discard that wheel, and rebuild.")
     (rule R5b "Addendum to R5 (2026-10-11; the remote location is a public release — only doeff's CI writes it and anyone can fetch it. No credentials or Pods are added, and empty machines no longer build Rust): the entry looks up local store → off-machine location → build, in that order. Location = the asset <package>-<key>.whl of the public GitHub release rust-wheels of doeff (env DOEFF_WHEEL_REMOTE replaces the prefix; an empty value disables the lookup). A fetched wheel is verified with the RECORD hashes, its real file name is rebuilt from the dist-info and the WHEEL Tag, and it is placed in the local store. When it cannot be fetched (404, network failure, 20-second timeout, broken wheel), emit one line naming it and build. Only doeff's GitHub Actions (.github/workflows/rust-wheels.yml → tools/rust_wheel_release.py) writes to the location, and it too passes through the entry with `uv build --wheel` and uploads only wheels whose origin in the entry's report is built (it does not overwrite the same name, and deletes the oldest first beyond 800). The entry does not write to the location. uv's find-links and index are not used (uv does not use them for path, editable or git dependencies). The one-line report adds the origin source (local, remote, built), and the built field stays for R7's readers. Failure case = tests/test_wheel_remote_store.py (on an empty machine the fake cargo is never called — acceptance c1f9f7ae2).")
     (rule R6 "Entries to be moved (the commit-hook linter binary) are placed in the ledger PENDING-ENTRIES with a reason. A new entry not in the ledger is red. A change that moves an entry to the entry or removes it deletes the ledger row in the same commit (leaving it is red).")
     (rule R7 "The doeff-cluster worker (EnsureNativeWheel in the runtime-environment preparation) and the boot script (worker/entry/boot_wheel) only hand the native wheel to the entry with `uv build --wheel`, and own no key (git tree hash) or location of their own. The store is the entry's DOEFF_WHEEL_CACHE (the worker's state/wheels). The wheel to install is the file uv build wrote to the --out-dir under the root (native_wheel.wheel_out_dir), which appears with any version of the entry (the old doeff root of a declaration passes in the same shape too). The one line the entry writes to the file in env DOEFF_WHEEL_REPORT (whether it built) is observation only; with an entry version that does not write it, the origin is the closed-type UNREPORTED, preparation is not stopped, and it is filled in as neither built nor used — the single definition point of the reading is native_wheel.reported_built.")]
  :laws
    [(law rust-builds-pass-through-one-entry
       :statement "for_all file f in doeff (excluding tests and docs): builds_or_keys_rust(f) => f == ENTRY or f in COMPILERS or f in PENDING-ENTRIES; and builds_or_keys_rust(ENTRY)"
       :counterexamples
         [(counterexample "a package's build backend calls maturin.build_editable directly without going through tools/doeff_cargo_backend.py (the editable shape until 2026-10-06 — the daily verification built doeff-vm about 100 times a day)")
          (counterexample "a new script makes a wheel location with its own key (native_key — git tree hash) (the same shape as the worker's native_wheel before it moved to the entry)")
          (counterexample "a script fires cargo build --release directly (not going through the entry's store)")])
     (law compiler-hooks-read-the-store
       :statement "for_all hook h in {build_wheel, build_editable} of every file in COMPILERS: calls(h, the matching store function of ENTRY)"
       :counterexamples
         [(counterexample "doeff-indexer's build_editable stays as with cargo_target_dir(): maturin.build_editable(...) (builds every time without going through the store)")])
     (law pending-entries-do-not-outlive-their-cause
       :statement "for_all f in PENDING-ENTRIES: builds_or_keys_rust(f)"
       :counterexamples
         [(counterexample "the change that moved the worker's native_wheel to the entry leaves its PENDING-ENTRIES row (the ledger silently permits the next entry)")])]
  :enforcement
    [(deftest test-adr-doe-build-001-rust-builds-pass-through-one-entry
       (val repo-root (. (Path __file__) parent parent parent))
       (val found (run (rust-building-files repo-root)))
       (val violations (run (entry-violations found)))
       (assert (= violations []) (+ "a second entry that builds Rust or makes a key (ADR-DOE-BUILD-001 R1, R6): " (str violations))))
     (deftest test-adr-doe-build-001-compiler-hooks-read-the-store
       (val repo-root (. (Path __file__) parent parent parent))
       (val missing (lfor rel (sorted COMPILERS)
                          hook (run (compiler-hooks-use-the-store (.read-text (/ repo-root rel) :encoding "utf-8")))
                          f"{rel}: {hook}"))
       (assert (= missing []) (+ "a compiler backend's build hook does not go through the store (ADR-DOE-BUILD-001 R3): " (str missing))))
     (deftest test-adr-doe-build-001-pending-entries-do-not-outlive-their-cause
       (val repo-root (. (Path __file__) parent parent parent))
       (val stale (run (stale-pending (run (rust-building-files repo-root)))))
       (assert (= stale []) (+ "a moved entry remains in the ledger (ADR-DOE-BUILD-001 R6): " (str stale))))
     (deftest test-adr-doe-build-001-judgments-reject-the-counterexamples
       ;; Counterexamples: shapes that make two entries (a backend calling maturin's hook directly, a script firing cargo build --release, its own key native_key, Hy's
       ;; native-key) are red as a second entry. uv build --wheel is a call through the entry, so it is not counted. Spellings inside comments and docstrings are not counted.
       ;; A ledger row is red once its cause is gone.
       (assert (run (python-builds-rust "import maturin\ndef build_editable(d, c=None, m=None):\n    return maturin.build_editable(d, c, m)\n")))
       (assert (run (python-builds-rust "import subprocess\nsubprocess.run(['cargo', 'build', '--release'])\n")))
       (assert (not (run (python-builds-rust "import subprocess\nsubprocess.run(['uv', 'build', '--wheel', '--out-dir', 'x', 'pkg'])\n"))))
       (assert (run (python-builds-rust "def native_key(package, trees):\n    return package\n")))
       (assert (not (run (python-builds-rust "\"\"\"a note on cargo build --release\"\"\"\n# does not call maturin.build_wheel\nx = ['build', 'docs']\n"))))
       (assert (run (hy-builds-rust "(defk native-key [wheel] wheel)\n")))
       (assert (run (hy-builds-rust "(RunProcess :argv #(cargo \"build\" \"--release\"))\n")))
       (assert (not (run (hy-builds-rust "(RunProcess :argv #(uv \"build\" \"--wheel\" \"--out-dir\" tmp src))\n"))))
       (assert (not (run (hy-builds-rust ";; does not fire uv \"build\" \"--wheel\"\n"))))
       (val second "packages/doeff-new/build_backend.py")
       (assert (= (len (run (entry-violations (frozenset #{ENTRY second})))) 1))
       (assert (= (run (entry-violations (frozenset (| #{ENTRY} COMPILERS (set PENDING-ENTRIES))))) []))
       (assert (= (len (run (entry-violations (frozenset #{second})))) 2))
       (assert (= (len (run (stale-pending (frozenset #{ENTRY})))) (len PENDING-ENTRIES)))
       (assert (= (run (compiler-hooks-use-the-store "def build_editable(d, c=None, m=None):\n    with cargo_target_dir():\n        return maturin.build_editable(d, c, m)\n"))
                  ["build_editable"]))
       (assert (= (run (compiler-hooks-use-the-store "def build_editable(d, c=None, m=None):\n    return editable_from_store(d, c, compile_it)\n")) [])))]
  :plans [])
