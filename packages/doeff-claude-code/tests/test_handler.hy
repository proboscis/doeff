;; 本番の handler だけの検(替え玉の CLI)— 共通の筋書きに載らない handler の内側の約束:
;; 会話の process は手番をまたいで生き、閉じると降りる(#3672)・冷えた続きの前の 1 回きりの命令・起動の失敗の型。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import json)
(import os)
(import os.path)
(import pathlib [Path])
(import sys)
(import threading)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Listen SlogEffect])
(import doeff_core_effects.handlers [await-handler listen-handler slog-discard-handler])
(import doeff_core_effects.scheduler [CreateExternalPromise])
(import doeff_time [Delay DelayEffect GetMonotonic GetTime WaitWithin async-time-handler sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession ForkSession Rebuilt TurnInput])
(import doeff_claude_code.lines [BackendLost Completed Failed Interrupted PartialMessage Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeExportSession ClaudeCloseSession
                                   ClaudeSessionStatus ClaudeWarmSession TurnStarted LaunchFailed SessionExported SessionNotFound
                                   SessionClosed SessionWarmed ProcessStillAlive])
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess LiveProcess NoLiveProcess StopReason])
(import doeff_claude_code.argv [launch-key transcript-path])
(import doeff_claude_code.process [ClaudeProcess])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [CLI-TIMING-LOG ClaudeCodeHost Doorbell claude-code-handler wait-until])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [HOOK-PHRASE STREAM-PHRASE THINK-PHRASE THINKING-PIECES-PHRASE TOOL-INPUT-PIECES-PHRASE
                              reply-prompt sleep-prompt])
(import doeff_claude_code [lines])
(import tests.scenario_steps [TurnRecord live-process-until read-to-end read-to-tool-start])


(defn host-of [command [live-limit 8] [floor 7200.0] [launch-timeout 30.0]]
  ;; 検の host(上限の本数と資格の床は、それを撃つ検だけが小さくする — #3672 の D2)。
  (ClaudeCodeHost command (clock-of (sync-time-handler)) live-limit floor :launch-timeout launch-timeout))

(defn spec-in [#^ Path tmp-path [cold None]]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env ""))
                     :cwd (str work) :settings {"disableAllHooks" True} :cold-resume-prompt cold))

(defn with-real-handler [host program]
  ;; 本番の handler の計時の行(slog)の答え手を外側に置く(#3605)。
  (with_handlers [(sync-time-handler) slog-discard-handler (claude-code-handler host)] program))


(defk two-turns-then-close [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % tuple)]}
  "同じ会話を 2 手番続けてから閉じる。答え = 2 手番目の後の process の見え方・2 つの手番の process が同じだったか・閉じた後に
   process が降りたか。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "ONE") "r1")))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  (val first-process (. (.runtime host sid) process))
  (<- again (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "TWO") "r2")))
  (<- second (read-to-end again.turn 30.0))
  (assert (isinstance second.end Completed) (repr second.end))
  (<- view (ClaudeLiveProcess sid))
  (val same (is (. (.runtime host sid) process) first-process))
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  #(view same (not (.alive first-process))))


(deftest test-the-process-stays-for-the-next-turn-and-goes-down-when-closed [tmp-path]
  ;; 会話の process は手番をまたいで生き、同じ起こした時の条件の鍵の続きはその process へ入力を書く(#3672 — 起こし直さない)。
  ;; 会話を閉じると降りる(訳 SESSION-CLOSED)。
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- outcome (with-real-handler host (two-turns-then-close host (spec-in tmp-path) (str (uuid.uuid4)))))
  (val view (get outcome 0))
  (assert (= view (LiveProcess :launches 1)) (repr view))
  (assert (get outcome 1) "2 手番目が別の process で走った")
  (assert (get outcome 2) "会話を閉じても process が降りない"))


(defk two-turns [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  (setv ends [])
  (for [#(origin word) [#((FreshSession sid) "ONE") #((ResumeSession sid) "TWO")]]
    (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
    (<- done (read-to-end started.turn 30.0))
    (.append ends done.end))
  ends)


(defk two-turns-closed-between [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  "1 手番目の後に会話を閉じて(process が降りる)から続ける。答え = 2 つの手番の終わり。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "ONE") (str (uuid.uuid4)))))
  (<- one (read-to-end started.turn 30.0))
  (<- (ClaudeCloseSession sid "test"))
  (<- again (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "TWO") (str (uuid.uuid4)))))
  (<- two (read-to-end again.turn 30.0))
  [one.end two.end])


(deftest test-the-cold-resume-command-runs-once-before-a-resume [tmp-path]
  ;; spec.cold-resume-prompt が在れば、降りた会話を --resume で起こす前に 1 回きりの print mode の命令を走らせる(生きて待つ process を
  ;; 使い回す続きでは走らせない — #3672)。替え玉はその命令を transcript に 1 行記す(新しい会話の最初の手番では走らせない)。
  (setv spec (spec-in tmp-path "/compact if-cold"))
  (setv sid (str (uuid.uuid4)))
  (<- ends (with-real-handler (host-of #(sys.executable "-m" "hy" STUB-PATH)) (two-turns-closed-between spec sid)))
  (assert (all (gfor end ends (isinstance end Completed))) (repr ends))
  (setv lines (.splitlines (.read-text (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))))
  (assert (= (lfor line lines :if (in "one-shot" line) line)
             ["{\"type\": \"user\", \"text\": \"one-shot: /compact if-cold\"}"])
          lines))


(deftest test-each-turn-carries-its-own-cost-across-processes [tmp-path]
  ;; 替え玉の CLI の total_cost_usd は実物と同じく会話の累積(1 手番 0.25 — 2 手番目の --resume の process は 0.5 を名乗る)。
  ;; 手番の額は累積の差: どちらの手番も 0.25(累積の 0.5 をそのまま運ばない・#883)。
  (val spec (spec-in tmp-path))
  (<- ends (with-real-handler (host-of #(sys.executable "-m" "hy" STUB-PATH)) (two-turns spec (str (uuid.uuid4)))))
  (assert (all (gfor end ends (isinstance end Completed))) (repr ends))
  (assert (= (lfor end ends end.cost-usd) [0.25 0.25]) (repr ends))
  (assert (= (lfor end ends end.usage) [(Usage :input-tokens 1 :output-tokens 1) (Usage :input-tokens 1 :output-tokens 1)])
          (repr ends)))


(defk killed-then-two-turns [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  "1 手番目の道具の途中で process を SIGKILL で消し、同じ会話を 2 手番続ける。答え = 3 つの手番の終わり。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- dropped (ClaudeDropProcess sid))
  (assert (is dropped True))
  (<- lost (read-to-end started.turn 30.0))
  (val ends [lost.end])
  (for [word ["BACK" "AGAIN"]]
    (<- resumed (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
    (<- done (read-to-end resumed.turn 30.0))
    (.append ends done.end))
  ends)


(deftest test-a-killed-process-leaves-the-count-where-it-started [tmp-path]
  ;; SIGKILL で消えた process は額を transcript に記さない(実物も替え玉も)ので、次の process の CLI はその process が始まった時の
  ;; 額から数える。handler も起点をそこへ戻す: 消えた後の手番の額は 0.25(起点を分からないとして捨てず、ずらしもしない)。
  (val sid (str (uuid.uuid4)))
  (<- ends (with-real-handler (host-of #(sys.executable "-m" "hy" STUB-PATH)) (killed-then-two-turns (spec-in tmp-path) sid)))
  (assert (isinstance (get ends 0) BackendLost) (repr ends))
  (assert (= (lfor end (cut ends 1 None) end.cost-usd) [0.25 0.25]) (repr ends)))


(defk killed-after-a-result-then-resumed [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  "同じ process の中で result の行を読んだ後に SIGKILL で消えた会話を続ける(足してから止めて、生き残った入力の手番の道具の途中で消す)。
   答え = 止めた手番・消えた手番・続きの手番の終わり。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- (ClaudeInjectInput started.turn (TurnInput (sleep-prompt 40 "INNER") "inj-survivor")))
  (<- (ClaudeInterruptTurn started.turn))
  (<- stopped (read-to-end started.turn 30.0))
  (assert (isinstance stopped.end Interrupted) (repr stopped.end))
  (<- (read-to-tool-start stopped.end.continued-by 30.0))
  (<- (ClaudeDropProcess sid))
  (<- lost (read-to-end stopped.end.continued-by 30.0))
  (<- resumed (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "BACK") (str (uuid.uuid4)))))
  (<- done (read-to-end resumed.turn 30.0))
  [stopped.end lost.end done.end])


(deftest test-a-process-killed-after-a-result-counts-from-where-it-started [tmp-path]
  ;; 止めた手番の result の行(累積 0.25)で起点が動いた後に process が SIGKILL で消えた。CLI は額を記していないので、次の process は
  ;; 消えた process が始まった時の額(0)から数え、続きの手番の累積は 0.25。handler の起点も消えた process の始まり(0)へ戻す —
  ;; 動いた後の起点(0.25)を使うと額が 0 になり、分からないとして捨てると None になる(どちらも誤り)。
  (<- ends (with-real-handler (host-of #(sys.executable "-m" "hy" STUB-PATH))
                              (killed-after-a-result-then-resumed (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (isinstance (get ends 1) BackendLost) (repr ends))
  (assert (isinstance (get ends 2) Completed) (repr ends))
  (assert (= (. (get ends 2) cost-usd) 0.25) (repr ends)))


(defk turn-until-down [#^ ClaudeCodeHost host origin #^ ClaudeSessionSpec spec #^ str word]
  {:pre [(: host ClaudeCodeHost) (: origin (| FreshSession ResumeSession ForkSession)) (: spec ClaudeSessionSpec)
         (: word str)]
   :post [(: % (| Completed Failed Interrupted BackendLost))]}
  "手番を 1 つ最後まで読み、会話を閉じて process が降りる(CLI が会話の累積の額を transcript に記す)まで待つ — 次の手番を別の host
   で起こす検のため(process は手番をまたいで生きるので、閉じて降ろす — #3672)。答え = 手番の終わり。"
  (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done (read-to-end started.turn 30.0))
  (val process (. (.runtime host started.session-id) process))
  (<- closed (ClaudeCloseSession started.session-id "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  (assert (not (.alive process)) "会話を閉じても手番の process が降りない")
  done.end)

;; 替え玉の CLI を撃つ命令(host をこの命令で作り直すと、手番ごとに handler を作り直す使い手の形 — 前の process を見ていない)。
(val STUB-COMMAND #(sys.executable "-m" "hy" STUB-PATH))


(deftest test-a-new-host-resuming-the-session-counts-from-the-recorded-cost [tmp-path]
  ;; 手番ごとに handler(host)を作り直す使い手: 2 手番目の host はこの会話の前の process を見ていない。起点は transcript の最後の
  ;; cost-state の額(替え玉の CLI が 1 手番目の後に記した 0.25)で、2 手番目の額は累積 0.5 からの差 0.25(#883)。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val first-host (host-of STUB-COMMAND))
  (<- one (with-real-handler first-host (turn-until-down first-host (FreshSession sid) spec "ONE")))
  (val second-host (host-of STUB-COMMAND))
  (<- two (with-real-handler second-host (turn-until-down second-host (ResumeSession sid) spec "TWO")))
  (assert (= [one.cost-usd two.cost-usd] [0.25 0.25]) (repr [one two]))
  (val third-host (host-of STUB-COMMAND))
  (<- three (with-real-handler third-host (turn-until-down third-host (ResumeSession sid) spec "THREE")))
  (assert (= three.cost-usd 0.25) (repr three)))


(deftest test-a-new-host-resuming-a-transcript-without-a-cost-line-gives-no-cost [tmp-path]
  ;; 額の行が無い transcript(CLI が額を記す前の版・記す前に消えた会話)の、この host の知らない続き: 起点が分からないので額は None
  ;; (0 から数えたことにしない)。次の手番は CLI が記した額から数える。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (.mkdir path.parent :parents True :exist-ok True)
  (.write-text path "{\"type\": \"user\", \"text\": \"earlier\"}\n" :encoding "utf-8")
  (val host (host-of STUB-COMMAND))
  (<- two (with-real-handler host (turn-until-down host (ResumeSession sid) spec "TWO")))
  (assert (isinstance two Completed) (repr two))
  (assert (is two.cost-usd None) (repr two))
  (val next-host (host-of STUB-COMMAND))
  (<- three (with-real-handler next-host (turn-until-down next-host (ResumeSession sid) spec "THREE")))
  (assert (= three.cost-usd 0.25) (repr three)))


(deftest test-a-carried-transcript-and-a-fork-count-from-the-recorded-cost [tmp-path]
  ;; 持ち込んだ transcript(Rebuilt — 写しに額の行が入る)の続きと、枝(ForkSession — 枝の CLI も親の transcript の最後の額から数える。
  ;; 実測 2.1.283: 枝の最初の累積 0.0421482 = 親の最後の額 0.0389113 + その手番の 0.0032369)は、どちらも新しい host で起点を読む。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val first-host (host-of STUB-COMMAND))
  (<- one (with-real-handler first-host (turn-until-down first-host (FreshSession sid) spec "ONE")))
  (<- exported (with-real-handler first-host (ClaudeExportSession spec.home spec.cwd sid)))
  (assert (isinstance exported SessionExported) (repr exported))
  (val elsewhere (replace spec :home (ClaudeHome (str (/ tmp-path "other-home")) (child-env ""))))
  (val carry-host (host-of STUB-COMMAND))
  (<- carried (with-real-handler carry-host
                (turn-until-down carry-host (ResumeSession sid :carry (Rebuilt exported.jsonl-text)) elsewhere "CARRIED")))
  (val fork-host (host-of STUB-COMMAND))
  (<- forked (with-real-handler fork-host (turn-until-down fork-host (ForkSession sid) spec "FORKED")))
  (assert (= [one.cost-usd carried.cost-usd forked.cost-usd] [0.25 0.25 0.25]) (repr [one carried forked])))


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


(deftest test-export-reads-the-transcript-of-the-real-path-of-the-cwd [tmp-path]
  ;; 写しの取り出し: 置き場は cwd の実体の path(realpath)で決まる — symlink の cwd で頼んでも同じ transcript を読む。
  ;; 本文は jsonl ちょうど。無い id・空の transcript は SessionNotFound。
  (val spec (spec-in tmp-path))
  (val alias (/ tmp-path "alias"))
  (.symlink-to alias spec.cwd)
  (val sid (str (uuid.uuid4)))
  (val empty (str (uuid.uuid4)))
  (val missing (str (uuid.uuid4)))
  (val body "{\"type\":\"user\",\"text\":\"日本語の行\"}\n{\"type\":\"assistant\"}\n")
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (.mkdir path.parent :parents True)
  (.write-text path body :encoding "utf-8")
  (.write-text (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) empty)) "\n" :encoding "utf-8")
  (val host (host-of #("/nonexistent/claude-binary")))
  (<- exported (with-real-handler host (ClaudeExportSession spec.home (str alias) sid)))
  (assert (= exported (SessionExported body)) (repr exported))
  (<- blank (with-real-handler host (ClaudeExportSession spec.home spec.cwd empty)))
  (assert (= blank (SessionNotFound empty)) (repr blank))
  (<- absent (with-real-handler host (ClaudeExportSession spec.home spec.cwd missing)))
  (assert (= absent (SessionNotFound missing)) (repr absent)))


;; 替え玉の CLI が最後の本文を何片の差分で流すか(#3628)と、その本文(片の数以上の字数)。
(val STREAMED-PIECES 5)
(val STREAMED-WORD "PIECEWISE-REPLY")

(defk streamed-then-plain [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)] :tags {:context "claude-code" :role "program"}}
  "最後の本文を STREAMED-PIECES 片の差分で流す手番と、差分を流さない手番を同じ会話で続けて最後まで読む。答え = 2 つの手番の読み。"
  (<- streamed (ClaudeStartTurn (FreshSession sid) spec
                                (TurnInput (+ (reply-prompt STREAMED-WORD) " . " (.format STREAM-PHRASE STREAMED-PIECES))
                                           (str (uuid.uuid4)))))
  (<- streamed-read TurnRecord (read-to-end streamed.turn 30.0))
  (<- plain (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "PLAIN") (str (uuid.uuid4)))))
  (<- plain-read TurnRecord (read-to-end plain.turn 30.0))
  [streamed-read plain-read])


(deftest test-the-turn-end-timing-line-counts-the-text-deltas-the-cli-sent [tmp-path]
  ;; 手番の終わりを上の層へ渡した所の計時の行(event turn-end — #3628)は、その手番で CLI から受けた本文の差分の行(text_delta)の数
  ;; partial_lines と、process を起こし始めてから最初の差分の行を読むまでの ms first_partial_since_launch_ms を持つ — 手番の文の途中が
  ;; 画面に出なかった時に、CLI が差分を出さなかったのか、出したが上で運ばれなかったのかを分けるため。替え玉の CLI が 5 片で流した手番は
  ;; 5(差分を挟む content_block_start / stop は text_delta でないので数えない)・差分の無い手番は 0 と None。上の層へ渡した頁の差分の
  ;; 行の数とも同じ。失敗ケース = 変更前は手番の終わりの計時の行が無い(欄が無い)。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (streamed-then-plain (spec-in tmp-path) (str (uuid.uuid4))) :types #(SlogEffect))))
  (val reads (get heard 0))
  (val ends (lfor effect (get heard 1) :if (and (= effect.msg CLI-TIMING-LOG) (= (.get effect.kwargs "event") "turn-end"))
                  effect.kwargs))
  (assert (= (lfor end ends (.get end "partial_lines")) [STREAMED-PIECES 0]) (repr ends))
  (val delivered (lfor read reads
                       (len (lfor line read.lines :if (and (isinstance line.kind PartialMessage) line.kind.text-delta) line))))
  (assert (= delivered [STREAMED-PIECES 0]) (repr delivered))
  (val streamed (get ends 0))
  (val plain (get ends 1))
  (val first-partial (.get streamed "first_partial_since_launch_ms"))
  (assert (isinstance first-partial int) (repr streamed))
  (assert (<= 0 first-partial (.get streamed "since_launch_ms")) (repr streamed))
  (assert (is (.get plain "first_partial_since_launch_ms") None) (repr plain)))


;; 替え玉の CLI に、考えている間の差分(thinking_delta)と道具の命令の差分(input_json_delta)を何片ずつ出させるか(#3746 (a))。
(val THINKING-PIECES 4)
(val SECOND-THINKING-PIECES 2)
(val TOOL-INPUT-PIECES 3)

(defk thinking-and-tool-input-over-three-turns [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % tuple)] :tags {:context "claude-code" :role "program"}}
  "考えている間の差分と道具の命令の差分を流す道具の手番・考えている間の差分だけを流す手番・どちらも流さない手番を、同じ会話の生きた
   process(使い回し)で続けて最後まで読む。答え = 3 つの手番の読みの列と、最後の process の見え方。"
  (<- opening (ClaudeStartTurn (FreshSession sid) spec
                               (TurnInput (.join " . " [(sleep-prompt 1 "TOOLED") (.format THINKING-PIECES-PHRASE THINKING-PIECES)
                                                        (.format TOOL-INPUT-PIECES-PHRASE TOOL-INPUT-PIECES)])
                                          (str (uuid.uuid4)))))
  (<- first-read TurnRecord (read-to-end opening.turn 30.0))
  (<- second (ClaudeStartTurn (ResumeSession sid) spec
                              (TurnInput (+ (reply-prompt "AGAIN") " . " (.format THINKING-PIECES-PHRASE SECOND-THINKING-PIECES))
                                         (str (uuid.uuid4)))))
  (<- second-read TurnRecord (read-to-end second.turn 30.0))
  (<- third (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "PLAIN") (str (uuid.uuid4)))))
  (<- third-read TurnRecord (read-to-end third.turn 30.0))
  (<- view (ClaudeLiveProcess sid))
  #([first-read second-read third-read] view))


(deftest test-the-turn-end-timing-line-counts-the-thinking-and-tool-input-deltas-per-turn [tmp-path]
  ;; #3746 (a): 手番の終わりの計時の行(event turn-end)は、その手番で CLI から受けた考えている間の差分の行の数 thinking_deltas と
  ;; 道具の命令の差分の行の数 tool_input_deltas、それぞれの最初の行を読むまでの ms(first_thinking_since_launch_ms・
  ;; first_tool_input_since_launch_ms — 差分の無い手番は欄を出さない)を持つ。本文は載せない。本文の差分の欄(partial_lines・
  ;; first_partial_since_launch_ms)は今のまま。手番の後も process が生きる形(3 手番を 1 つの process で)で、数と最初の刻は手番ごとに
  ;; 数え直す(前の手番の分を足さない)。失敗ケース = text_delta 以外の差分を本文の空の行に畳む分類では欄が無くて赤。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (thinking-and-tool-input-over-three-turns (spec-in tmp-path) (str (uuid.uuid4))) :types #(SlogEffect))))
  (val reads (get (get heard 0) 0))
  (val view (get (get heard 0) 1))
  (assert (= view (LiveProcess :launches 1)) (repr view))
  (val ends (lfor effect (get heard 1) :if (and (= effect.msg CLI-TIMING-LOG) (= (.get effect.kwargs "event") "turn-end"))
                  effect.kwargs))
  (assert (= (lfor end ends (.get end "thinking_deltas")) [THINKING-PIECES SECOND-THINKING-PIECES 0]) (repr ends))
  (assert (= (lfor end ends (.get end "tool_input_deltas")) [TOOL-INPUT-PIECES 0 0]) (repr ends))
  (assert (= (lfor end ends (.get end "partial_lines")) [0 0 0]) (repr ends))
  (assert (= (lfor end ends #((in "first_thinking_since_launch_ms" end) (in "first_tool_input_since_launch_ms" end)))
             [#(True True) #(True False) #(False False)])
          (repr ends))
  (for [#(end name) [#((get ends 0) "first_thinking_since_launch_ms") #((get ends 0) "first_tool_input_since_launch_ms")
                     #((get ends 1) "first_thinking_since_launch_ms")]]
    (assert (and (isinstance (get end name) int) (<= 0 (get end name) (get end "since_launch_ms"))) (repr end)))
  ;; 上の層へ渡した頁の行の差分の種類の数とも同じ。
  (assert (= (lfor read reads (len (lfor line read.lines :if (and (isinstance line.kind PartialMessage)
                                                                   (= line.kind.delta lines.DeltaKind.THINKING))
                                         line)))
             [THINKING-PIECES SECOND-THINKING-PIECES 0])
          (repr reads)))


(val THINK-SECONDS 0.6)
(val HOOK-SECONDS 0.4)

(defk thinking-then-plain-then-silent [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)] :tags {:context "claude-code" :role "program"}}
  "init の後の hook を待ってから考えて本文を流す手番・考えずに本文を流す手番(使い回しの道)・本文を流さない手番を同じ会話で続けて
   最後まで読む。"
  (<- thinking (ClaudeStartTurn (FreshSession sid) spec
                                (TurnInput (.join " . " [(reply-prompt "THOUGHT") (.format HOOK-PHRASE HOOK-SECONDS)
                                                         (.format THINK-PHRASE THINK-SECONDS) (.format STREAM-PHRASE STREAMED-PIECES)])
                                           (str (uuid.uuid4)))))
  (<- thinking-read TurnRecord (read-to-end thinking.turn 30.0))
  (<- plain (ClaudeStartTurn (ResumeSession sid) spec
                             (TurnInput (+ (reply-prompt "QUICK") " . " (.format STREAM-PHRASE STREAMED-PIECES)) (str (uuid.uuid4)))))
  (<- plain-read TurnRecord (read-to-end plain.turn 30.0))
  (<- silent (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "SILENT") (str (uuid.uuid4)))))
  (<- silent-read TurnRecord (read-to-end silent.turn 30.0))
  [thinking-read plain-read silent-read])


(deftest test-the-first-text-timing-line-splits-the-thinking-from-the-hooks [tmp-path]
  ;; 本文の最初の差分を上の層へ初めて渡した所の計時の行(event first-text — #3696)は、手番ごとに 1 度だけ出て、init の後の最初の stream
  ;; の行(実物では message_start)の刻 stream_start_at_ms と、そこから最初の本文の差分を読むまでの ms after_stream_start_ms を持つ —
  ;; 入力ごとの hook と API へ出すまで・モデルが考えた秒・CLI が最初の文字を受けてから画面に出るまで(区間 H)の起点を、task の log だけで
  ;; 割るため。init の次の行(first-reply の行)は hook の知らせで message_start ではない(cluster の実測で kind = Other)ので、hook の秒は
  ;; after_stream_start_ms に入らず、first-reply の行から stream_start_at_ms までに入る。考えない手番(使い回しの process の道)はほぼ 0。
  ;; 本文の差分の無い手番には行が無い。失敗ケース = 起点を init の次の行にしていた形(#3696 の最初の版)は、hook の秒が考えた秒に混ざって赤。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (thinking-then-plain-then-silent (spec-in tmp-path) (str (uuid.uuid4))) :types #(SlogEffect))))
  (val timings (lfor effect (get heard 1) :if (= effect.msg CLI-TIMING-LOG) effect.kwargs))
  (val firsts (lfor timing timings :if (= (.get timing "event") "first-text") timing))
  (assert (= (len firsts) 2) (repr firsts))
  (val thought (get firsts 0))
  (val quick (get firsts 1))
  (assert (= (lfor first firsts (.get first "turn_seq")) (sorted (lfor first firsts (.get first "turn_seq")))) (repr firsts))
  (val thought-reply (next (gfor timing timings :if (and (= (.get timing "event") "first-reply")
                                                          (= (.get timing "turn_seq") (.get thought "turn_seq")))
                                 timing)))
  (assert (>= (.get thought "after_stream_start_ms") (* 0.8 THINK-SECONDS 1000)) (repr thought))
  (assert (< (.get thought "after_stream_start_ms") (* 0.9 (+ THINK-SECONDS HOOK-SECONDS) 1000)) (repr thought))
  (assert (>= (- (.get thought "stream_start_at_ms") (.get thought-reply "line_at_ms")) (* 0.8 HOOK-SECONDS 1000))
          (repr #(thought thought-reply)))
  (assert (< (.get quick "after_stream_start_ms") 200) (repr quick))
  (assert (<= (.get thought "after_stream_start_ms") (.get thought "since_launch_ms")) (repr thought)))


;; --- 入力の前に事前起動して待たせる process(ClaudeWarmSession)の、最初の入力の前の行と片づけ ------------------------------------
;; 替え玉の CLI は env STUB_CLI_AT_START(JSON の object — lines = 起動の直後に stdout へ出す行の列・marker = 出し終えた後に作る
;; file の path)を読む。marker は「行を出し終えた」のマークで、テストはそれを待ってから次へ進む(読み取りの thread が行をターンの前に
;; 読んだ状態を作るため — ターンの後に読むとターンの行に混ざり、守りを確かめられない)。

;; 本物の CLI を入力なしで起動した時に stdout へ出る行(SessionStart の hook の開始と応答 — 実物の stream-json の形。JSON の境界の値
;; なので写像のまま)。
(val SESSION-START-LINES
  [{"type" "system" "subtype" "hook_started" "hook_id" "hook-1" "hook_name" "SessionStart:startup" "hook_event" "SessionStart"
    "uuid" "u-1"}
   {"type" "system" "subtype" "hook_response" "hook_id" "hook-1" "hook_name" "SessionStart:startup" "hook_event" "SessionStart"
    "output" "" "stdout" "" "stderr" "" "exit_code" 0 "outcome" "success" "uuid" "u-2"}])
;; 行を出し終えたマークを見てから、読み取りの thread が行を処理し終えるのを待つ秒(マークの後の行の読み取りは 1 ms 未満 — 余裕を
;; 持たせる)。
(val SETTLE-SECONDS 0.3)

(defrecord StartLinesSpec
  "替え玉の CLI が起動の直後に行を出す会話の宣言(spec)と、出し終えたマークの file の path(marker)。"
  (#^ ClaudeSessionSpec spec)
  (#^ str marker))

(defk spec-with-start-lines [#^ Path tmp-path #^ str name #^ list start-lines]
  {:pre [(: tmp-path Path) (: name str) (: start-lines list)] :post [(: % StartLinesSpec)]
   :tags {:context "claude-code" :role "program"}}
  "替え玉の CLI が起動の直後に start-lines を出す会話の宣言と、出し終えたマークの file の path を作るため。"
  (val marker (str (/ tmp-path (+ name ".started"))))
  (val base (spec-in tmp-path))
  (val env (| (dict base.home.env) {"STUB_CLI_AT_START" (json.dumps {"lines" start-lines "marker" marker})}))
  (StartLinesSpec :spec (replace base :home (ClaudeHome base.home.config-dir env)) :marker marker))

(defk file-appears [#^ str path #^ float timeout]
  {:pre [(: path str) (: timeout float)] :post [(: % None)] :tags {:context "claude-code" :role "program"}}
  "替え玉の CLI が行を出し終えたマークの file が現れるまで待つため(上限 timeout 秒 — 越えたらテストの誤り)。"
  (<- started (GetMonotonic))
  (while (not (os.path.exists path))
    (<- now (GetMonotonic))
    (assert (< (- now started) timeout) (.format "{} 秒の内に {} が現れない" timeout path))
    (<- (Delay 0.05)))
  None)

(defk process-down [#^ ClaudeProcess process #^ float timeout]
  {:pre [(: process ClaudeProcess) (: timeout float)] :post [(: % bool)] :tags {:context "claude-code" :role "program"}}
  "host が停止を始めた process が終了するまで待つため(上限 timeout 秒)。結果 = 終了したか。"
  (<- started (GetMonotonic))
  (while (.alive process)
    (<- now (GetMonotonic))
    (when (>= (- now started) timeout) (return False))
    (<- (Delay 0.05)))
  True)


(defrecord WarmFirstTurn
  "事前起動してから最初のターンを走らせたシナリオの観測: before = ターンの前の状態・warm-process = 事前起動した process・done = ターンの
   読み取り結果・after = ターンの後の状態・turn-process = ターンを走らせた process。"
  (#^ (| LiveProcess NoLiveProcess) before)
  (#^ ClaudeProcess warm-process)
  (#^ TurnRecord done)
  (#^ (| LiveProcess NoLiveProcess) after)
  (#^ (| ClaudeProcess None) turn-process))

(defk warm-then-first-turn [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid #^ str marker]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str) (: marker str)] :post [(: % WarmFirstTurn)]
   :tags {:context "claude-code" :role "program"}}
  "新しい会話を入力なしで起動し、CLI が起動の直後の行を出し終えて読み取りの thread が読んだ後に、同じ id の最初のターンを走らせるため。"
  (<- warmed (ClaudeWarmSession (FreshSession sid) spec))
  (assert (= warmed (SessionWarmed :session-id sid)) (repr warmed))
  (<- (file-appears marker 30.0))
  (<- (Delay SETTLE-SECONDS))
  (<- before (ClaudeLiveProcess sid))
  (val warm-process (. (.runtime host sid) process))
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "FIRST") "first-1")))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done TurnRecord (read-to-end started.turn 30.0))
  (<- after (ClaudeLiveProcess sid))
  (val turn-process (. (.runtime host sid) process))
  (<- (ClaudeCloseSession sid "test"))
  (WarmFirstTurn :before before :warm-process warm-process :done done :after after :turn-process turn-process))


(deftest test-a-warm-process-that-only-ran-the-session-start-hooks-serves-the-first-turn [tmp-path]
  ;; 入力なしで起動した CLI が最初の入力の前に SessionStart の hook の 2 行を出しても、host は停止しない。同じ id・同じ起動条件の最初の
  ;; ターンはその process に入力を書く(新しい process を起動しない — 起動回数 1)。事前起動は新しい会話の --session-id で起動し
  ;; (--resume ではない — まだ記録が無い)、最初のターンの起動条件のキーは事前起動の時と同じ。hook の 2 行はターンの前に読んだ行なので
  ;; ターンの行に入らない。失敗ケース = 最初の入力の前の行を全部「ターンの外の出力」と数える形では、hook の行で process が停止し
  ;; (理由 OUTSIDE-TURN-OUTPUT)、ターンは新しい process で走って起動回数が 2 になる。
  (val host (host-of STUB-COMMAND))
  (val sid (str (uuid.uuid4)))
  (<- made StartLinesSpec (spec-with-start-lines tmp-path "hooks" SESSION-START-LINES))
  (<- seen WarmFirstTurn (with-real-handler host (warm-then-first-turn host made.spec sid made.marker)))
  (assert (= seen.before (LiveProcess :launches 1)) (repr seen.before))
  (assert (isinstance seen.done.end Completed) (repr seen.done.end))
  (assert (not (any (gfor line seen.done.lines (isinstance line.kind lines.HookNotice)))) (repr seen.done.lines))
  (assert (= seen.after (LiveProcess :launches 1)) (repr seen.after))
  (assert (is seen.turn-process seen.warm-process) "最初のターンが事前起動した process を使わなかった")
  (val argv (list seen.warm-process.argv))
  (assert (= (get argv (+ (.index argv "--session-id") 1)) sid) (repr argv))
  (assert (not-in "--resume" argv) (repr argv))
  (<- turn-key (launch-key STUB-COMMAND made.spec))
  (assert (= (. (.runtime host sid) launch-key) turn-key)))


(defrecord ForbiddenLine
  "最初の入力の前に出たら停止する行 1 つ: name = シナリオの名前・line = 行(JSON の境界の値)。"
  (#^ str name)
  (#^ dict line))

;; 最初の入力の前に出たら停止する行: assistant の行(ターンの外で model が応答した形)と、SessionStart 以外の hook の行。
(val FORBIDDEN-BEFORE-INPUT
  [(ForbiddenLine :name "assistant"
                  :line {"type" "assistant" "parent_tool_use_id" None
                         "message" {"role" "assistant" "model" "claude-stub" "content" [{"type" "text" "text" "unasked"}]}})
   (ForbiddenLine :name "other-hook"
                  :line {"type" "system" "subtype" "hook_response" "hook_id" "hook-2" "hook_name" "Notification"
                         "hook_event" "Notification" "output" "" "stdout" "" "stderr" "" "exit_code" 0 "outcome" "success"})])

(defrecord WarmStopped
  "事前起動した process を host が停止したシナリオの観測: gone = 停止した後の状態・down = 事前起動した process が終了したか。"
  (#^ (| LiveProcess NoLiveProcess) gone)
  (#^ bool down))

(defk warm-until-stopped [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % WarmStopped)]
   :tags {:context "claude-code" :role "program"}}
  "新しい会話を入力なしで起動し、host がその process を停止するまで見るため。"
  (<- warmed (ClaudeWarmSession (FreshSession sid) spec))
  (assert (= warmed (SessionWarmed :session-id sid)) (repr warmed))
  (val warm-process (. (.runtime host sid) process))
  (<- gone (live-process-until sid (fn [view] (isinstance view NoLiveProcess)) 30.0))
  (<- down (process-down warm-process 30.0))
  (WarmStopped :gone gone :down down))


(deftest test-a-warm-process-that-prints-anything-else-before-the-first-input-is-stopped [tmp-path]
  ;; 最初の入力の前に SessionStart の hook の行以外の行(assistant の行・別の hook の行)を 1 行でも出した process は、今までどおり
  ;; ターンの外の出力として停止する(理由 OUTSIDE-TURN-OUTPUT)。SessionStart の hook の行が先に在っても同じ。
  (val host (host-of STUB-COMMAND))
  (for [forbidden FORBIDDEN-BEFORE-INPUT]
    (<- made StartLinesSpec (spec-with-start-lines tmp-path forbidden.name (+ SESSION-START-LINES [forbidden.line])))
    (<- seen WarmStopped (with-real-handler host (warm-until-stopped host made.spec (str (uuid.uuid4)))))
    (assert (= seen.gone (NoLiveProcess :launches 1 :stopped-because StopReason.OUTSIDE-TURN-OUTPUT))
            (repr #(forbidden.name seen.gone)))
    (assert seen.down forbidden.name)))


(defrecord WarmClosed
  "事前起動した process をターンなしで閉じたシナリオの観測: before = 閉じる前の状態・warm-process = 事前起動した process・closed = 閉じた
   結果・after = 閉じた後の状態。"
  (#^ (| LiveProcess NoLiveProcess) before)
  (#^ ClaudeProcess warm-process)
  (#^ (| SessionClosed ProcessStillAlive) closed)
  (#^ (| LiveProcess NoLiveProcess) after))

(defk warm-then-close [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % WarmClosed)]
   :tags {:context "claude-code" :role "program"}}
  "新しい会話を入力なしで起動し、ターンを 1 度も走らせずに閉じるため。"
  (<- (ClaudeWarmSession (FreshSession sid) spec))
  (<- before (ClaudeLiveProcess sid))
  (val warm-process (. (.runtime host sid) process))
  (<- closed (ClaudeCloseSession sid "test"))
  (<- after (ClaudeLiveProcess sid))
  (WarmClosed :before before :warm-process warm-process :closed closed :after after))


(deftest test-a-warm-process-closed-without-a-turn-leaves-no-child-process [tmp-path]
  ;; 事前起動した process をターンなしで閉じると、閉じる結果を返す前に終了しきる(子 process は残らない — pid がもう無い)。
  (val host (host-of STUB-COMMAND))
  (<- seen WarmClosed (with-real-handler host (warm-then-close host (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (= seen.before (LiveProcess :launches 1)) (repr seen.before))
  (assert (= seen.closed (SessionClosed False)) (repr seen.closed))
  (assert (not (.alive seen.warm-process)) "閉じても事前起動した process が終了しない")
  (var gone False)
  (try
    (os.kill seen.warm-process.pid 0)
    (except [ProcessLookupError] (:= gone True)))
  (assert gone (.format "事前起動した process の pid {} が残る" seen.warm-process.pid))
  (assert (= seen.after (NoLiveProcess :launches 1 :stopped-because StopReason.SESSION-CLOSED)) (repr seen.after)))


(defrecord WarmReplaced
  "事前起動した後に起動条件の違うターンを走らせたシナリオの観測: warm-process = 事前起動した process・done = ターンの読み取り結果・
   after = ターンの後の状態・turn-process = ターンを走らせた process・stopped-because = 事前起動した process を停止した理由。"
  (#^ ClaudeProcess warm-process)
  (#^ TurnRecord done)
  (#^ (| LiveProcess NoLiveProcess) after)
  (#^ (| ClaudeProcess None) turn-process)
  (#^ (| StopReason None) stopped-because))

(defk warm-then-turn-with-another-model [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % WarmReplaced)]
   :tags {:context "claude-code" :role "program"}}
  "新しい会話を入力なしで起動した後に、model の違う最初のターンを走らせるため。"
  (<- (ClaudeWarmSession (FreshSession sid) spec))
  (val warm-process (. (.runtime host sid) process))
  (<- started (ClaudeStartTurn (FreshSession sid) (replace spec :model "another-model") (TurnInput (reply-prompt "CHANGED") "c-1")))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done TurnRecord (read-to-end started.turn 30.0))
  (<- after (ClaudeLiveProcess sid))
  (val runtime (.runtime host sid))
  (val seen (WarmReplaced :warm-process warm-process :done done :after after :turn-process runtime.process
                          :stopped-because runtime.stopped-because))
  (<- (ClaudeCloseSession sid "test"))
  seen)


(deftest test-a-warm-process-with-other-launch-conditions-goes-down-for-a-new-one [tmp-path]
  ;; 事前起動した後に model の違う最初のターンが来たら、事前起動した process を停止し(理由 LAUNCH-CHANGED)、新しい会話の同じ id の
  ;; まま(--session-id)新しい process を起動してターンを走らせる。
  (val host (host-of STUB-COMMAND))
  (val sid (str (uuid.uuid4)))
  (<- seen WarmReplaced (with-real-handler host (warm-then-turn-with-another-model host (spec-in tmp-path) sid)))
  (assert (isinstance seen.done.end Completed) (repr seen.done.end))
  (assert (= seen.after (LiveProcess :launches 2)) (repr seen.after))
  (assert (is-not seen.turn-process seen.warm-process) "model の違うターンが事前起動した process を使った")
  (assert (not (.alive seen.warm-process)) "事前起動した process が終了していない")
  (assert (= seen.stopped-because StopReason.LAUNCH-CHANGED) (repr seen.stopped-because))
  (val argv (list seen.turn-process.argv))
  (assert (= (get argv (+ (.index argv "--session-id") 1)) sid) (repr argv))
  (assert (= (get argv (+ (.index argv "--model") 1)) "another-model") (repr argv)))


;; --- 生かす本数の上限と資格の床で降ろす(#3672 の D2 — 止める判断は host の 1 か所)------------------------------------------

(defk one-turn [#^ ClaudeSessionSpec spec origin #^ str word]
  {:pre [(: spec ClaudeSessionSpec) (: origin (| FreshSession ResumeSession)) (: word str)]
   :post [(: % (| Completed Failed Interrupted BackendLost))]}
  "検の会話の手番を 1 つ始めて最後まで読むため。答え = 手番の終わり。"
  (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done (read-to-end started.turn 30.0))
  done.end)

(defk three-sessions-over-a-limit-of-two [#^ ClaudeSessionSpec spec #^ tuple ids]
  {:pre [(: spec ClaudeSessionSpec) (: ids tuple)] :post [(: % tuple)]}
  "上限 2 の host で会話 3 つの手番を 1 つずつ順に走らせるため。答え = 3 つの会話の process の見え方。"
  (for [sid ids]
    (<- end (one-turn spec (FreshSession sid) "X"))
    (assert (isinstance end Completed) (repr end)))
  (<- first-view (ClaudeLiveProcess (get ids 0)))
  (<- second-view (ClaudeLiveProcess (get ids 1)))
  (<- third-view (ClaudeLiveProcess (get ids 2)))
  #(first-view second-view third-view))


(deftest test-the-least-recently-used-idle-process-goes-down-at-the-live-limit [tmp-path]
  ;; 生かす本数が上限(2)に来たら、新しく起こす前に、手番を走らせていない物のうち一番長く使われていない物(一番前に手番を始めた
  ;; 会話)を降ろす(訳 LIVE-LIMIT)。ほかの生きた process は残る。
  (val ids (tuple (gfor _ (range 3) (str (uuid.uuid4)))))
  (val host (host-of STUB-COMMAND :live-limit 2))
  (<- views (with-real-handler host (three-sessions-over-a-limit-of-two (spec-in tmp-path) ids)))
  (assert (= (get views 0) (NoLiveProcess :launches 1 :stopped-because StopReason.LIVE-LIMIT)) (repr views))
  (assert (= (get views 1) (LiveProcess :launches 1)) (repr views))
  (assert (= (get views 2) (LiveProcess :launches 1)) (repr views)))


(defk second-session-while-the-first-runs [#^ ClaudeSessionSpec spec #^ str first-id #^ str second-id]
  {:pre [(: spec ClaudeSessionSpec) (: first-id str) (: second-id str)] :post [(: % tuple)]}
  "上限 1 の host で、1 つ目の会話の長い手番の途中に 2 つ目の会話の手番を頼むため。答え = 2 つ目の始まりの答え・1 つ目の手番の
   終わり・2 つ目を頼んだ時に 1 つ目の手番が終わっていたか・1 つ目の process の見え方。"
  (<- started (ClaudeStartTurn (FreshSession first-id) spec (TurnInput (sleep-prompt 3 "SLOW") "slow-1")))
  (<- (read-to-tool-start started.turn 30.0))
  (<- second (ClaudeStartTurn (FreshSession second-id) spec (TurnInput (reply-prompt "NEXT") "next-1")))
  (<- first-end (read-to-end started.turn 30.0))
  (<- first-view (ClaudeLiveProcess first-id))
  #(second first-end.end first-view))


(deftest test-a-launch-at-the-live-limit-waits-for-a-turn-to-end-and-never-stops-a-running-turn [tmp-path]
  ;; 上限に来た時に全部の process が手番を走らせていれば、空く(手番が終わる)まで起こすのを待つ。走っている手番は止めない —
  ;; 1 つ目の手番は Completed で終わり、終わった後に一番長く使われていない物として降りる(訳 LIVE-LIMIT)。
  (val host (host-of STUB-COMMAND :live-limit 1))
  (<- seen (with-real-handler host (second-session-while-the-first-runs (spec-in tmp-path) (str (uuid.uuid4)) (str (uuid.uuid4)))))
  (assert (isinstance (get seen 0) TurnStarted) (repr seen))
  (assert (isinstance (get seen 1) Completed) (repr seen))
  (assert (= (get seen 2) (NoLiveProcess :launches 1 :stopped-because StopReason.LIVE-LIMIT)) (repr seen)))


(deftest test-a-launch-that-waits-past-the-limit-is-a-named-launch-failure [tmp-path]
  ;; 空きを待つのは launch-timeout まで。越えたら起こさず、上限で待ったことを名指した LaunchFailed。
  (val host (host-of STUB-COMMAND :live-limit 1 :launch-timeout 2.0))
  (val first-id (str (uuid.uuid4)))
  (<- outcome (with-real-handler host
                (launch-over-a-busy-limit (spec-in tmp-path) first-id (str (uuid.uuid4)))))
  (assert (isinstance outcome LaunchFailed) (repr outcome))
  (assert (in "live-limit" outcome.stderr-tail) (repr outcome)))


(defk launch-over-a-busy-limit [#^ ClaudeSessionSpec spec #^ str first-id #^ str second-id]
  {:pre [(: spec ClaudeSessionSpec) (: first-id str) (: second-id str)] :post [(: % (| TurnStarted LaunchFailed))]}
  "上限 1 の host で 1 つ目の会話に長い手番を走らせたまま 2 つ目を頼み、その答えを返すため(終わりに 1 つ目の会話を閉じる)。"
  (<- started (ClaudeStartTurn (FreshSession first-id) spec (TurnInput (sleep-prompt 20 "LONG") "long-1")))
  (<- (read-to-tool-start started.turn 30.0))
  (<- outcome (ClaudeStartTurn (FreshSession second-id) spec (TurnInput (reply-prompt "WAIT") "wait-1")))
  (<- (ClaudeCloseSession first-id "test"))
  outcome)


(defk expiring-in [#^ ClaudeSessionSpec base #^ float seconds]
  {:pre [(: base ClaudeSessionSpec) (: seconds float)] :post [(: % ClaudeSessionSpec)]}
  "検の spec に、今から seconds 秒後に切れる資格の期限を添えるため(時刻は時間の答え手の内側で読む)。"
  (<- now (GetTime))
  (replace base :credential-expires-at (+ (.timestamp now) seconds)))

(defk turn-then-resume-under-the-floor [#^ ClaudeSessionSpec base #^ str sid]
  {:pre [(: base ClaudeSessionSpec) (: sid str)] :post [(: % tuple)]}
  "資格の期限が床の内側(100 秒後に切れる・床 200 秒)の spec で手番を 1 つ走らせ、続けて同じ会話を続けるため。答え = 1 手番目の
   後の見え方・続きの後の見え方。"
  (<- spec (expiring-in base 100.0))
  (<- end (one-turn spec (FreshSession sid) "ONE"))
  (assert (isinstance end Completed) (repr end))
  (<- after-first (ClaudeLiveProcess sid))
  (<- again (one-turn spec (ResumeSession sid) "TWO"))
  (assert (isinstance again Completed) (repr again))
  (<- after-second (ClaudeLiveProcess sid))
  #(after-first after-second))


(deftest test-a-process-whose-credential-is-under-the-floor-goes-down-at-the-turn-boundary [tmp-path]
  ;; 資格の期限 − 床 を過ぎた process は、手番の境で止める(訳 CREDENTIAL-FLOOR — 呼び手はこの訳を読んで借りた資格を返す)。
  ;; 次の手番は起こし直す(使い回さない)。
  (val host (host-of STUB-COMMAND :floor 200.0))
  (<- views (with-real-handler host (turn-then-resume-under-the-floor (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (= (get views 0) (NoLiveProcess :launches 1 :stopped-because StopReason.CREDENTIAL-FLOOR)) (repr views))
  (assert (= (get views 1) (NoLiveProcess :launches 2 :stopped-because StopReason.CREDENTIAL-FLOOR)) (repr views)))


(defk idle-until-the-floor [#^ ClaudeSessionSpec base #^ str sid #^ float wait-seconds]
  {:pre [(: base ClaudeSessionSpec) (: sid str) (: wait-seconds float)] :post [(: % tuple)]}
  "手番の後に生きて待つ process の資格が、待つ間に床を切る筋書きのため(20 秒後に切れる・床 15 秒 — 手番の後はまだ床の外)。答え =
   手番の後の見え方・待った後に host を呼んだ後の見え方。"
  (<- spec (expiring-in base 20.0))
  (<- end (one-turn spec (FreshSession sid) "ONE"))
  (assert (isinstance end Completed) (repr end))
  (<- after-turn (ClaudeLiveProcess sid))
  (<- (Delay wait-seconds))
  (<- (ClaudeSessionStatus spec.home spec.cwd sid))
  (<- after-wait (ClaudeLiveProcess sid))
  #(after-turn after-wait))


(deftest test-an-idle-process-whose-credential-crosses-the-floor-goes-down-at-the-next-call [tmp-path]
  ;; 手番を走らせていない process の資格が待つ間に床を切ったら、host が次に呼ばれた時(手番を始める・行を読む・会話の状態を読む)に
  ;; 止める(訳 CREDENTIAL-FLOOR)。
  (val host (host-of STUB-COMMAND :floor 15.0))
  (<- views (with-real-handler host (idle-until-the-floor (spec-in tmp-path) (str (uuid.uuid4)) 7.0)))
  (assert (= (get views 0) (LiveProcess :launches 1)) (repr views))
  (assert (= (get views 1) (NoLiveProcess :launches 1 :stopped-because StopReason.CREDENTIAL-FLOOR)) (repr views)))


;; --- 待ちは呼び鈴で起きる(時間で起きて確かめない)---------------------------------------------------------------

(defk hooked-and-thinking-turn [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % TurnRecord)] :tags {:context "claude-code" :role "program"}}
  "init の後に hook の秒と考える秒を置いてから本文を差分で出す手番を、起こしてから最後まで読んで閉じるため(起動の待ち・頁の読みの
   待ち・閉じる待ちのどれもが、まだ来ていない行や終わりを待つ形)。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec
                               (TurnInput (.join " " [(.format HOOK-PHRASE 0.2) (.format THINK-PHRASE 0.2) (.format STREAM-PHRASE 3)
                                                     (reply-prompt "WAITED")])
                                          (str (uuid.uuid4)))))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done TurnRecord (read-to-end started.turn 30.0))
  (<- (ClaudeCloseSession sid "test"))
  done)


(deftest test-the-waits-wake-on-the-reader-thread-not-on-a-clock-tick [tmp-path]
  ;; 起動の待ち・頁の読みの待ち・閉じる待ちは、読み手の thread(と process の終わり)が状態を変えた時に鳴らす呼び鈴で起きる —
  ;; 時間の刻み(Delay)で起きて確かめない。刻みで起きる形は行が来てから最大 1 刻みだけ遅れて待ちが抜け、待つ間も刻みごとに起きる。
  ;; 失敗ケース = 0.05 秒ごとに Delay で起きて確かめる形では、この手番の間に handler が Delay を何度も撃つ。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (hooked-and-thinking-turn (spec-in tmp-path) (str (uuid.uuid4))) :types #(DelayEffect))))
  (val done (get heard 0))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (= (len (get heard 1)) 0) (.format "待ちの間に handler が Delay を {} 回撃った" (len (get heard 1)))))


(defrecord WaitOutcome
  "wait-until を 1 回待たせた観測: woke = 条件が真になって抜けたか・seconds = 待った秒。"
  (#^ bool woke)
  (#^ float seconds))

(defk wait-through-a-change-before-the-bell [#^ float seconds]
  {:pre [(: seconds float)] :post [(: % WaitOutcome)] :tags {:context "claude-code" :role "program"}}
  "状態が「待つ手が条件を読んだ直後・呼び鈴を掛ける前」に変わり、その変化の呼び鈴が誰も掛けていない所で鳴る形で wait-until を
   待たせるため(上限 seconds 秒)。"
  (val doorbell (Doorbell))
  (val read-once (threading.Event))
  (val changed (threading.Event))
  (<- started (GetMonotonic))
  (<- woke (wait-until (fn [bell] (.hang doorbell bell))
                       (fn [] (if (.is-set read-once)
                                  (.is-set changed)
                                  (do (.set read-once) (.set changed) (.ring doorbell) False)))
                       seconds))
  (<- finished (GetMonotonic))
  (WaitOutcome :woke woke :seconds (- finished started)))


(deftest test-a-change-just-before-the-bell-is-hung-does-not-wait-out-the-deadline [tmp-path]
  ;; 待つ手は呼び鈴を掛けてから条件を読み直すので、読んだ直後・掛ける前に状態が変わって呼び鈴が空振りしても、期限まで待たずに抜ける
  ;; (取りこぼしが無い)。時間の刻み(Delay)でも起きない。失敗ケース = 掛ける前に読んだ条件だけで眠る形は 5 秒の期限まで眠る・
  ;; 刻みで起きて確かめる形は Delay を撃つ。
  (<- heard (with_handlers [(sync-time-handler) listen-handler]
              (Listen (wait-through-a-change-before-the-bell 5.0) :types #(DelayEffect))))
  (val outcome (get heard 0))
  (assert outcome.woke (repr outcome))
  (assert (< outcome.seconds 1.0) (repr outcome))
  (assert (= (len (get heard 1)) 0) (.format "待ちの間に Delay を {} 回撃った" (len (get heard 1)))))


;; --- 本番の CLI の組(await-handler・async-time-handler・scheduled)の下の待ち ------------------------------------------
;; 本番の CLI の host は scheduled の中で [(await-handler) … (async-time-handler) …] の組で動く(上の層の composition root)。待ちの部品
;; (別の thread が完了させる約束を WaitWithin :park True で待つ)と、直した handler の手番 1 本を、その組の下でも確かめる。

(defn production-stack [#* inner]
  ;; 本番の CLI の組の時間と待ちの答え手(順も同じ — await-handler が外・async-time-handler が内)と、検が足す内側の答え手。
  (+ [(await-handler) slog-discard-handler (async-time-handler)] (list inner)))

(defk wait-for-a-bell-from-another-thread [#^ (| float None) ring-after #^ float seconds]
  {:pre [(: ring-after (| float None)) (: seconds float)] :post [(: % WaitOutcome)] :tags {:context "claude-code" :role "program"}}
  "呼び鈴(CreateExternalPromise の約束)を、別の thread が ring-after 秒後に完了させる(None なら誰も鳴らさない)形で、WaitWithin
   :park True を上限 seconds 秒で待たせるため。答え = 鳴って起きたか・待った秒。"
  (<- bell (CreateExternalPromise))
  (when (is-not ring-after None)
    (val timer (threading.Timer ring-after (fn [] (.complete bell True))))
    (setv timer.daemon True)
    (.start timer))
  (<- started (GetMonotonic))
  (<- answer (WaitWithin bell.future seconds :park True))
  (<- finished (GetMonotonic))
  (WaitOutcome :woke (is answer True) :seconds (- finished started)))


(deftest test-a-bell-from-another-thread-wakes-the-wait-under-the-production-stack [tmp-path]
  ;; 本番の CLI の組の下で、別の thread が 50 ms 後に完了させた呼び鈴は、上限 5 秒の WaitWithin をその刻に起こす(読み手の thread が
  ;; 鳴らす呼び鈴の形)。失敗ケース = 外からの完了が scheduler を起こさない組では、上限の 5 秒まで眠る。
  (<- outcome WaitOutcome (with_handlers (production-stack) (wait-for-a-bell-from-another-thread 0.05 5.0)))
  (assert outcome.woke (repr outcome))
  (assert (<= 0.04 outcome.seconds 0.5) (repr outcome)))


(deftest test-an-unrung-bell-answers-none-at-the-deadline-under-the-production-stack [tmp-path]
  ;; 誰も鳴らさない呼び鈴の待ちは、本番の CLI の組の下でも期限(5 秒)で None を答えて抜ける(待ちの上限の秒が効く)。失敗ケース =
  ;; :park True の待ちが期限の時計を止める組では、期限が来ずに抜けない。
  (<- outcome WaitOutcome (with_handlers (production-stack) (wait-for-a-bell-from-another-thread None 5.0)))
  (assert (not outcome.woke) (repr outcome))
  (assert (<= 4.9 outcome.seconds 6.0) (repr outcome)))


(deftest test-a-turn-reads-to-the-end-without-a-clock-tick-under-the-production-stack [tmp-path]
  ;; 直した handler の手番 1 本(替え玉の CLI)は、本番の CLI の組の下でも最後まで読め、待ちの間に Delay を撃たない。失敗ケース =
  ;; 0.05 秒ごとに Delay で起きて確かめる形では、この組の下でも handler が Delay を何度も撃つ。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers (production-stack listen-handler (claude-code-handler host))
              (Listen (hooked-and-thinking-turn (spec-in tmp-path) (str (uuid.uuid4))) :types #(DelayEffect))))
  (val done (get heard 0))
  (assert (isinstance done.end Completed) (repr done.end))
  (assert (= (len (get heard 1)) 0) (.format "待ちの間に handler が Delay を {} 回撃った" (len (get heard 1)))))
