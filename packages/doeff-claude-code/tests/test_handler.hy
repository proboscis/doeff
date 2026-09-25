;; 本番の handler だけの検(替え玉の CLI)— 共通の筋書きに載らない handler の内側の約束:
;; 手番の終わりで process が降りる(#517)・冷えた続きの前の 1 回きりの命令・起動の失敗の型。
(require doeff-hy.macros [deftest defk <-])
(import os.path)
(import pathlib [Path])
(import sys)
(import uuid)
(import doeff [with_handlers])
(import doeff_time [Delay sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession TurnInput])
(import doeff_claude_code.lines [Completed])
(import doeff_claude_code.effects [ClaudeStartTurn TurnStarted LaunchFailed])
(import doeff_claude_code.argv [transcript-path])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [reply-prompt])
(import tests.scenario_steps [read-to-end])


(defn host-of [command]
  (ClaudeCodeHost command (clock-of (sync-time-handler)) :launch-timeout 30.0))

(defn spec-in [#^ Path tmp-path [cold None]]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env ""))
                     :cwd (str work) :settings {"disableAllHooks" True} :cold-resume-prompt cold))

(defn with-real-handler [host program]
  (with_handlers [(sync-time-handler) (claude-code-handler host)] program))


(defk turn-then-wait-down [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % bool)]}
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "DOWN") "r1")))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  (setv process (. (.runtime host sid) process))
  (setv waited 0)
  (while (and (.alive process) (< waited 100))
    (<- (Delay 0.1))
    (+= waited 1))
  (not (.alive process)))


(deftest test-the-process-goes-down-at-the-end-of-the-turn [tmp-path]
  ;; 手番の境界の持ち主は host: result の行で stdin に EOF を出し、process は降りる(次の手番は --resume の新しい process)。
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- down (with-real-handler host (turn-then-wait-down host (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert down))


(defk two-turns [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  (setv ends [])
  (for [#(origin word) [#((FreshSession sid) "ONE") #((ResumeSession sid) "TWO")]]
    (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
    (<- done (read-to-end started.turn 30.0))
    (.append ends done.end))
  ends)


(deftest test-the-cold-resume-command-runs-once-before-a-resume [tmp-path]
  ;; spec.cold-resume-prompt が在れば、降りた会話を --resume で起こす前に 1 回きりの print mode の命令を走らせる。
  ;; 替え玉はその命令を transcript に 1 行記す(新しい会話の最初の手番では走らせない)。
  (setv spec (spec-in tmp-path "/compact if-cold"))
  (setv sid (str (uuid.uuid4)))
  (<- ends (with-real-handler (host-of #(sys.executable "-m" "hy" STUB-PATH)) (two-turns spec sid)))
  (assert (all (gfor end ends (isinstance end Completed))) (repr ends))
  (setv lines (.splitlines (.read-text (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))))
  (assert (= (lfor line lines :if (in "one-shot" line) line)
             ["{\"type\": \"user\", \"text\": \"one-shot: /compact if-cold\"}"])
          lines))


(deftest test-a-missing-executable-is-a-launch-failure [tmp-path]
  (<- outcome (with-real-handler (host-of #("/nonexistent/claude-binary"))
                (ClaudeStartTurn (FreshSession (str (uuid.uuid4))) (spec-in tmp-path) (TurnInput "x" "r"))))
  (assert (isinstance outcome LaunchFailed) (repr outcome))
  (assert (in "nonexistent" outcome.stderr-tail)))


(deftest test-a-process-that-exits-before-init-is-a-launch-failure-and-the-id-stays-free [tmp-path]
  ;; init の行の前に降りた process = LaunchFailed(終了コードと stderr の末尾)。新しい会話の id は使われていない扱いに戻る。
  (setv sid (str (uuid.uuid4)))
  (setv spec (spec-in tmp-path))
  (setv host (host-of #(sys.executable "-c" "import sys; sys.stderr.write('boom\\n'); sys.exit(3)")))
  (<- outcome (with-real-handler host (ClaudeStartTurn (FreshSession sid) spec (TurnInput "x" "r"))))
  (assert (= outcome (LaunchFailed 3 "boom")) (repr outcome))
  (assert (is (.runtime host sid) None)))
