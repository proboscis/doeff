;; 起動の断り(LaunchFailed)の検 — initialize の答えの前に降りる process(すぐ終わる替え玉)でも、断りが終了の code と stderr の末尾を運ぶ事。
;; process が降りた直後は終わりの callback(on-exit)がまだ code と stderr を記していない競り合いがあった(Mac の codex の断りが
;; `exit None`・stderr 空で出た)。何度か繰り返して、降りる速さによらず揃う事を見る。
(require doeff-hy.macros [deftest val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import sys)
(import doeff [run with_handlers])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [sync-time-handler])
(import doeff_codex [CodexSessionSpec CodexHome FreshThread CodexInput])
(import doeff_codex [ApprovalPolicy SandboxMode])
(import doeff_codex [CodexHost codex-handler])
(import doeff_codex [CodexStartTurn LaunchFailed])

;; 替え玉の app-server: stderr に 2 行書いて何も答えず code 3 ですぐ終わる。
(val DIES "import sys\nsys.stderr.write('first\\n')\nsys.stderr.write('boom\\n')\nsys.exit(3)\n")
(val ATTEMPTS 10)


(val TMP "/tmp")
(val SPEC (CodexSessionSpec :home (CodexHome :env {"PATH" "/usr/bin:/bin" "HOME" TMP "PYTHONDONTWRITEBYTECODE" "1" "CODEX_HOME" TMP})
                            :cwd TMP :approval-policy ApprovalPolicy.NEVER :sandbox SandboxMode.READ-ONLY))
(val HOST (CodexHost #(sys.executable "-c" DIES) :launch-timeout 30.0))


(deftest test-a-launch-refusal-from-a-process-that-dies-at-once-carries-its-exit-code-and-stderr
  (for [_ (range ATTEMPTS)]
    (setv refused (run (scheduled (with_handlers [(sync-time-handler) (codex-handler HOST)]
                                                 (CodexStartTurn (FreshThread) SPEC (CodexInput :text "hi"))))))
    (assert (isinstance refused LaunchFailed) refused)
    (assert (= refused.exit-code 3) refused)
    (assert (= refused.stderr-tail "first\nboom") refused)))
