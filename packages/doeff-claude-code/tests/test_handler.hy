;; 本番の handler だけの検(替え玉の CLI)— 共通の筋書きに載らない handler の内側の約束:
;; 会話の process は手番をまたいで生き、閉じると降りる(#3672)・冷えた続きの前の 1 回きりの命令・起動の失敗の型。
(require doeff-hy.macros [deftest defk <- val var])
(import dataclasses [replace])
(import os.path)
(import pathlib [Path])
(import sys)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Listen SlogEffect])
(import doeff_core_effects.handlers [listen-handler slog-discard-handler])
(import doeff_time [Delay sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession ForkSession Rebuilt TurnInput])
(import doeff_claude_code.lines [BackendLost Completed Failed Interrupted PartialMessage Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeExportSession ClaudeCloseSession
                                   TurnStarted LaunchFailed SessionExported SessionNotFound SessionClosed])
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess LiveProcess])
(import doeff_claude_code.argv [transcript-path])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [CLI-TIMING-LOG ClaudeCodeHost claude-code-handler])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [STREAM-PHRASE reply-prompt sleep-prompt])
(import tests.scenario_steps [TurnRecord read-to-end read-to-tool-start])


(defn host-of [command]
  (ClaudeCodeHost command (clock-of (sync-time-handler)) :launch-timeout 30.0))

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
