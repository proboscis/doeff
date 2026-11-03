;;; Executable ADR: the doeff-agents driver layer does not touch files, child processes, unix sockets,
;;; or environment variables itself. It issues requests in the `doeff_agents._io_effects` vocabulary,
;;; exactly one handler fulfils them (production `io_handlers` / test `io_fake`),
;;; and the composition root (``io_root``) chooses which one applies.
;;;
;;; Origin = agora-redesign stage 7 lane 7c (decision 1.3, `docs/plans/decisions-merge-2026-09-12.md`):
;;;   "Hy is limited in principle (B): new code is Hy. Existing doeff Python stays for now,
;;;    but the 8 files with direct I/O move to handlers"
;;;
;;; Known shape (known shapes ledger): algebraic effects — an effect is a request value,
;;; a handler is the home of execution, and the composition root chooses the home. This change stays
;;; inside that shape and adds no new mechanism.
;;;
;;; Vocabulary (agora-redesign master plan 0b): this is the "tool" layer. It brings in neither domain words
;;; (conversation, job, mail) nor mechanism words (turn).

(require doeff-adr.macros [defadr rule law])
(import doeff-adr.macros [fact interpretation counterexample])
(require doeff-hy.macros [deftest defk <-])
(import doeff [run])
(import re)
(import pathlib [Path])

(import doeff_agents._io_effects [read-text write-text which-executable run-process])
(import doeff_agents._io_fake [FakeIoWorld with-fake-io])


;; ---------------------------------------------------------------------------
;; Roster — the "8 files with direct I/O" counted by the round 3 inventory.
;;
;; Selection criterion (reproducible): files in the driver layer, i.e. the doeff-agents src minus
;; sessionhost (agentd, already compliant in Hy) and handlers (the side that fulfils I/O), that have
;; lines matching IO-CALL-PATTERNS below. Measured on 2026-09-12, this criterion counts
;; exactly 8 files (matching the round 3 count).
;; ---------------------------------------------------------------------------

(setv DRIVER-IO-ROSTER
  #{"_adapters/claude.py"
    "_adapters/codex.py"
    "_adapters/gemini.py"
    "_agentd_client.py"
    "_claude_home.py"
    "_session_backend.py"
    "_session_store.py"
    "_tmux.py"})

;; Literal shapes of direct I/O. 5 kinds: file, child process, unix socket, environment variable, PATH.
;; The clock (time.sleep / time.monotonic) is outside this ADR's population — turning the waits of the
;; remaining 2 driver-layer files (cli.py, session.py) into effects is a separate change, and extending
;; this law that far would bring not-yet-started work in as red.
;; `urllib.parse` is pure parsing of spelling, so it is not I/O (only `urllib.request` is).
(setv IO-CALL-PATTERNS
  ["(?<![\\w.])open\\("
   "(?<![\\w.])subprocess\\."
   "(?<![\\w.])socket\\.socket\\("
   "(?<![\\w.])shutil\\."
   "(?<![\\w.])urllib\\.request"
   "(?<![\\w.])tempfile\\."
   "(?<![\\w.])os\\.(?:environ|access|getpid|makedirs|replace|remove|symlink|listdir|chmod|kill)"
   "\\.read_text\\("
   "\\.write_text\\("
   "\\.mkdir\\("
   "\\.touch\\("
   "\\.chmod\\("
   "\\.rglob\\("
   "Path\\.home\\("])

;; Excluded from the scan: agentd (sessionhost — has its own substrate vocabulary under the
;; frozen contract of ADR-DOE-AGENTS-004), handlers (the side that fulfils I/O), and
;; the 2 homes that fulfil driver-layer I/O.
(setv SCAN-SKIP-DIRS #{"_sessionhost" "_handlers" "__pycache__"})
(setv IO-HOME-FILES #{"_io_handlers.hy" "_io_fake.hy"})


(defk driver-source-root [package-root]
  {:pre [(: package-root Path)]
   :post [(: % Path)]}
  "Root of the driver-layer source. Fails immediately if missing — if the law went green
   because the scan was empty, the check would protect nothing."
  (setv root (/ package-root "packages" "doeff-agents" "src" "doeff_agents"))
  (when (not (.is-dir root))
    (raise (AssertionError f"driver-layer source root is missing: {root}")))
  root)


(defk scan-driver-io [package-root]
  {:pre [(: package-root Path)]
   :post [(: % dict)]}
  "Per-file count of direct I/O lines in the driver layer (the shared measure for the check and the law)."
  (setv pattern (re.compile (.join "|" IO-CALL-PATTERNS)))
  (setv counts {})
  (setv root (run (driver-source-root package-root)))
  (for [source (sorted (+ (list (.rglob root "*.py")) (list (.rglob root "*.hy"))))]
    (setv rel (.relative-to source root))
    (when (or (& (set rel.parts) SCAN-SKIP-DIRS) (in rel.name IO-HOME-FILES))
      (continue))
    (setv hits (len (lfor line (.splitlines (.read-text source :encoding "utf-8" :errors "replace"))
                          :if (.search pattern line)
                          line)))
    (when (> hits 0)
      (setv (get counts (str rel)) hits)))
  counts)


(defk io-home-holds-the-syscalls [package-root]
  {:pre [(: package-root Path)]
   :post [(: % bool)]}
  "Whether the production home actually holds the raw syscalls (the falsifier that keeps the law from being vacuous)."
  (setv pattern (re.compile (.join "|" IO-CALL-PATTERNS)))
  (setv home (/ (run (driver-source-root package-root)) "_io_handlers.hy"))
  (> (len (lfor line (.splitlines (.read-text home :encoding "utf-8" :errors "replace"))
                :if (.search pattern line)
                line))
     0))


(defadr ADR-DOE-AGENTS-013
  :title "The doeff-agents driver layer does not touch files, child processes, unix sockets, or environment variables itself: it issues requests in the io_effects vocabulary, the homes that fulfil them are two — production io_handlers and test io_fake — and the composition root's ``io_root`` chooses which applies. Decisions (building argv, interpreting the protocol, deciding what to write) stay in the driver files"
  :status "accepted"
  :scope ["docs/adr/defadr_doeff_agents_013_driver_io_through_handlers.hy"
          "packages/doeff-agents/src/doeff_agents"
          "packages/doeff-agents/tests"]
  :problem
    [(fact
       "agora-redesign stage 7 decision 1.3: Hy is limited in principle (B) — new code is Hy; existing doeff Python stays for now, but the 8 files with direct I/O move to handlers."
       :evidence "agora-redesign docs/plans/decisions-merge-2026-09-12.md 1.3 / docs/design/known-shapes-2026-09-12.md:59")
     (fact
       "Round 3 inventory (2026-09-12): the doeff-agents sessionhost is 9,351 lines of Hy and already compliant. The driver layer outside it is 8,600 lines of Python; 22 files were written with doeff effects and 8 files had direct I/O."
       :evidence "agora-redesign docs/design/discuss-remaining-decisions-round3-2026-09-12.html:82")
     (fact
       "Direct I/O lived together with decisions: e.g. the send-confirmation loop in tmux.py (capture → judge → resend Enter → wait) held subprocess.run and time.sleep directly, and tests could only exercise it by monkeypatching `doeff_agents._tmux.subprocess.run` (7 sites)."
       :evidence "monkeypatch sites in packages/doeff-agents/tests/test_session_backend.py before the change")
     (fact
       "monkeypatch depends on the module spelling, so every time the I/O home moves, tests silently pass through: `agentd_client._start_agentd_process` was patched with raising=False, so when the function name changed, a real Popen ran."
       :evidence "11 monkeypatch sites (raising=False) in packages/doeff-agents/tests/test_agentd_client.py before the change")]
  :context
    [(interpretation
       "An effect is a \"request value\" and a handler is the \"home of execution\". Once the driver files only emit values, decisions stay where they are and only execution is swapped between production and tests — a change inside a known shape (algebraic effects) that adds no new mechanism.")
     (interpretation
       "The agentd (sessionhost) substrate vocabulary is a contract surface that ADR-DOE-AGENTS-004 froze against the Rust oracle, and it lacks the driver layer's requests (executable lookup, home copying, a unix socket round trip). Folding the two vocabularies into one now would shake the frozen contract, so the driver layer keeps its own vocabulary and unification is registered as a separate change.")
     (interpretation
       "The composition root is the side that holds \"which home applies\" as a single value (``io_root``). If the place that assembles backend, handler, and client receives that value, tests can run the same program against an in-memory world — unlike monkeypatch, tests do not silently pass through when the home moves.")]
  :decision
    [(rule R1 "Files in the driver layer (the tree packages/doeff-agents/src/doeff_agents minus sessionhost / handlers) do not touch files, child processes, unix sockets, environment variables, or PATH directly. Requests are issued in the `doeff_agents._io_effects` vocabulary.")
     (rule R2 "Exactly one module fulfils driver-layer I/O in the real world: `doeff_agents._io_handlers`. The test home is `doeff_agents._io_fake`, which fulfils the same vocabulary with an in-memory file system and scripted child processes.")
     (rule R3 "The composition root chooses which home applies via ``io_root``. The default is the production home; tests pass the fake. When adding a new I/O request, add it to both homes in the same change.")
     (rule R4 "Decisions (building argv, reading output, building and interpreting the protocol, deciding what to write) stay in the driver files as pure functions. Do not move decisions into handlers (handlers pass answers through).")
     (rule R5 "Tests do not swap I/O with monkeypatch. What is swapped is the home (``io_root``); tests that need a real socket or real file record only the one needed point with a layered handler (`recorded-spawn-handler`).")]
  :laws
    [(law driver-io-goes-through-effects
       :statement "for_all source f in driver_layer: count_direct_io_calls(f) = 0 — not a single line in the driver layer directly touches files, child processes, unix sockets, or environment variables"
       :counterexamples
         [(counterexample "tmux.py calls subprocess.run directly — tests can only monkeypatch the module spelling (doeff_agents._tmux.subprocess.run), and when the I/O home moves, tests silently pass through (7 sites measured before the change on 2026-09-12)")
          (counterexample "agentd_client.py opens socket.socket directly — decisions (building and interpreting JSON) and the physics of the round trip live in the same function, and protocol tests require a real socket")])
     (law driver-io-has-one-production-home
       :statement "exists_unique module m in driver_layer: m holds the raw syscalls, and m = doeff_agents._io_handlers"
       :counterexamples
         [(counterexample "A second home appears (a raw subprocess.run in another module) — the same request is fulfilled with different physics depending on the place, and the composition root's choice stops taking effect")
          (counterexample "The law exists but the home is empty — if the files excluded from the scan actually hold no syscalls, the law protects nothing (vacuous-law detection is needed)")])
     (law driver-io-is-drivable-by-a-fake-home
       :statement "for_all driver_program p: run(fake_home, p) terminates without touching the real world, and answers from the in-memory world"
       :counterexamples
         [(counterexample "The fake home drops a request (the handler has no clause for it) — the program fails with UnhandledEffect, and the test cannot show that it \"does not touch the real world\"")])]
  :enforcement
    [(deftest test-adr-doe-agents-013-driver-layer-has-no-direct-io
       ;; Check: measured = scan-driver-io, law = 0 lines.
       (setv package-root (. (Path __file__) parent parent parent))
       (setv counts (run (scan-driver-io package-root)))
       (assert (= counts {})
               (+ "direct I/O remains in the driver layer (ADR-DOE-AGENTS-013 R1 — "
                  "issue requests in the doeff_agents._io_effects vocabulary; they are fulfilled by "
                  "_io_handlers / _io_fake): " (str (sorted (.items counts))))))
     (deftest test-adr-doe-agents-013-roster-files-are-in-the-driver-layer
       ;; The 8 roster files exist and are inside the scan population (detects roster rot).
       (setv package-root (. (Path __file__) parent parent parent))
       (setv root (run (driver-source-root package-root)))
       (setv missing (sorted (lfor rel DRIVER-IO-ROSTER
                                   :if (not (.exists (/ root rel)))
                                   rel)))
       (assert (= missing [])
               (+ "roster files do not exist (ADR-DOE-AGENTS-013 — when moving them, "
                  "fix DRIVER-IO-ROSTER in the same change): " (str missing)))
       (assert (= (len DRIVER-IO-ROSTER) 8)
               "roster size differs from the 8 files counted by the round 3 inventory"))
     (deftest test-adr-doe-agents-013-production-home-is-not-empty
       ;; Vacuous-law detection: the production home actually holds the raw syscalls.
       (setv package-root (. (Path __file__) parent parent parent))
       (assert (run (io-home-holds-the-syscalls package-root))
               (+ "the production home has no raw syscalls (ADR-DOE-AGENTS-013 R2 — "
                  "if the home is empty, the law protects nothing)")))
     (deftest test-adr-doe-agents-013-fake-home-drives-the-same-program
       ;; The test home fulfils the whole vocabulary and answers without touching the real world.
       (setv world (FakeIoWorld :files {"/agent/in.txt" "hello"}
                                :which {"tmux" "/usr/bin/tmux"}))
       (assert (= (run (with-fake-io world (read-text "/agent/in.txt"))) "hello"))
       (assert (is (run (with-fake-io world (read-text "/agent/missing.txt"))) None))
       (assert (= (run (with-fake-io world (which-executable "tmux"))) "/usr/bin/tmux"))
       (assert (is (run (with-fake-io world (which-executable "nope"))) None))
       (run (with-fake-io world (write-text "/agent/out.txt" "written")))
       (assert (= (get world.files "/agent/out.txt") "written"))
       (assert (not (.exists (Path "/agent/out.txt")))
               "the test home created a real file (it must fulfil requests only in the in-memory world)")
       (val outcome (run (with-fake-io world (run-process #("tmux" "-V")))))
       (assert (= outcome.exit-code 127)
               "a command not in the script returns \"no such command\" (it must not silently succeed)")
       (assert (= (len world.commands) 1)))]
  :plans [])
