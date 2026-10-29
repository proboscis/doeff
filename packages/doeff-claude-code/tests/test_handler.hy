;; 本番の handler だけの検(替え玉の CLI)— 共通の筋書きに載らない handler の内側の約束:
;; 会話の process は手番をまたいで生き、閉じると降りる(#3672)・冷えた続きの前の 1 回きりの命令・起動の失敗の型。
(require doeff-hy.macros [deftest defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass replace])
(import datetime [datetime timezone])
(import json)
(import stat)
(import os)
(import os.path)
(import pytest)
(import pathlib [Path])
(import sys)
(import threading)
(import uuid)
(import doeff [with_handlers])
(import doeff_core_effects [Listen SlogEffect])
(import doeff_core_effects [await-handler listen-handler slog-discard-handler])
(import doeff_core_effects [CreateExternalPromise Spawn Task Wait])
(import time)
(import doeff_time [Delay DelayEffect GetMonotonic GetTime WaitWithin async-time-handler epoch-ms-of sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession ResumeSession ForkSession Rebuilt TurnInput LentCredential])
(import doeff_claude_code.lines [BackendLost Completed Failed Interrupted PartialMessage Usage CostMark ModelUsage ModelUsageGap])
(import doeff_claude_code.effects [ClaudeReplaceSessionCredential CredentialReplaced
                                   ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeExportSession ClaudeCloseSession
                                   ClaudeSessionStatus ClaudeWarmSession TurnStarted LaunchFailed SessionExported SessionNotFound
                                   SessionClosed SessionWarmed ProcessStillAlive ClaudeReadTurnEvents TurnEventPage UnknownTurn
                                   Closed])
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeLiveProcess LiveProcess NoLiveProcess StopReason])
(import doeff_claude_code.argv [launch-key transcript-path])
(import doeff_claude_code.process [ClaudeProcess])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [CLI-TIMING-LOG COST-TAIL-BYTES KEPT-TURNS ClaudeCodeHost Doorbell SessionRuntime claude-code-handler elapsed-ms note-reused
                                   recorded-cost-mark wait-until wall-elapsed-ms])
(import tests.interpreters [STUB-PATH child-env])
(import tests.scenario_rules [HOOK-PHRASE STREAM-PHRASE THINK-PHRASE THINKING-PIECES-PHRASE TOOL-INPUT-PIECES-PHRASE
                              reply-prompt sleep-prompt])
(import doeff_claude_code [lines])
(import doeff_claude_code [handler :as handler-module])
(import tests.scenario_steps [TurnRecord live-process-until read-to-end read-to-tool-start read-until])


(defn host-of [command [live-limit 8] [floor 7200.0] [launch-timeout 30.0]]
  ;; 検の host(上限の本数と資格の床は、それを撃つ検だけが小さくする — #3672 の D2)。
  (ClaudeCodeHost command (clock-of (sync-time-handler)) live-limit floor :launch-timeout launch-timeout))

(defn spec-in [#^ Path tmp-path [cold None]]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env ""))
                     :cwd (str work) :settings {"disableAllHooks" True} :cold-resume-prompt cold))

;; 替え玉の CLI の手番 1 回の model ごとの増分(替え玉の charged — 道具なしの手番は本体の model だけ・額は手番の額 0.25 ちょうど —
;; card ki-1d986d4f0007)。
(val STUB-TURN-USAGE #((ModelUsage "claude-stub" :input-tokens 7 :output-tokens 6 :cache-read-input-tokens 1010
                                   :cache-creation-input-tokens 20 :web-search-requests 0 :cost-usd 0.25)))

(defn priced-like-one-stub-turn [end]
  "手番の終わりが替え玉の 1 手番分の model ごとの使用量を持ち、手番の額と照合が合ったか。"
  (and (= end.model-usage STUB-TURN-USAGE) (is end.model-usage-gap None) (= end.cost-usd 0.25)))

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


(defrecord KeptTurns
  "同じ process で手番を重ねてから閉じた観測: live = 最後の手番の後に会話が持つ手番の番号・held = 閉じた後もホストが会話の生きた
   状態を持つか・last = 閉じた後に最後の手番を読み直した答え。"
  (#^ list live)
  (#^ bool held)
  (#^ object last))

(defk turns-then-close [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid #^ int count]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str) (: count int)] :post [(: % KeptTurns)]
   :tags {:context "claude-code-test" :role "program"}}
  "同じ会話の process で count 手番を続けてから閉じ、会話が手番の記録をいくつ持ち続けるかを見るため。"
  (var turn None)
  (for [i (range count)]
    (<- started (ClaudeStartTurn (if (= i 0) (FreshSession sid) (ResumeSession sid)) spec
                                 (TurnInput (reply-prompt (+ "W" (str i))) (+ "r" (str i)))))
    (<- done (read-to-end started.turn 30.0))
    (assert (isinstance done.end Completed) (repr done.end))
    (:= turn started.turn))
  (val live (sorted (. (.runtime host sid) turns)))
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  (val held (is-not (.runtime host sid) None))
  (<- last (ClaudeReadTurnEvents turn -1 0.0))
  (KeptTurns :live live :held held :last last))

(deftest test-the-session-keeps-a-few-recent-turns-and-none-once-closed [tmp-path]
  ;; 手番の記録(stdout の行の全部)は、読み手が今の手番と続きの手番しか読まないので、最近の KEPT-TURNS 本だけ持つ。最後の手番の
  ;; 終わりを読み手が読んだ後に閉じた会話は、手番の記録ごと生きた状態をホストから外す(読み手は終わりを読んだ手番を読み直さない —
  ;; 読み直すと知らない手番)— 会話の数と手番の数だけ ホストの process の memory が伸び続けない(zeus の turn-host の本体・
  ;; lt-KATGT933TATG1ESPPSW9QGFM7K・lt-5611P8B4MJS6CZQZ1809H94JPQ)。
  ;; 生きた会話ごとに持つ本数は数本(16 本では、生きた会話の数 × 16 手番分の行をホストが持った)。
  (assert (<= KEPT-TURNS 4) KEPT-TURNS)
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- seen (with-real-handler host (turns-then-close host (spec-in tmp-path) (str (uuid.uuid4)) (+ KEPT-TURNS 2))))
  (assert (= seen.live (list (range 3 (+ KEPT-TURNS 3)))) (repr seen.live))
  (assert (not seen.held) "閉じた会話の生きた状態をホストが持ち続けた")
  (assert (isinstance seen.last UnknownTurn) (repr seen.last)))


(defrecord ClosedHosts
  "会話を何本か開いて閉じた観測: held = 閉じた後もホストが生きた状態を持つ会話の数・states = 閉じた会話の様子・views = 閉じた会話の
   process の見え方・resumed = 閉じた会話の 1 本を続けた手番・resumed-end = その終わり・resumed-view = 続けた後の process の見え方・
   seq-grew = 続けた手番の行の seq が閉じる前の最後の行より後か。"
  (#^ int held)
  (#^ tuple states)
  (#^ tuple views)
  (#^ object resumed)
  (#^ object resumed-end)
  (#^ object resumed-view)
  (#^ bool seq-grew))

(defk sessions-closed-then-one-resumed [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ list sids]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sids list)] :post [(: % ClosedHosts)]
   :tags {:context "claude-code-test" :role "program"}}
  "会話ごとに 2 手番を終わりまで読んで閉じ、閉じた会話をホストが持つか・閉じた様子を答えるか・最初の会話を続けられるかを見るため。"
  (var last-seq -1)
  (for [sid sids]
    (for [#(origin word) [#((FreshSession sid) "ONE") #((ResumeSession sid) "TWO")]]
      (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
      (<- done (read-to-end started.turn 30.0))
      (assert (isinstance done.end Completed) (repr done.end))
      (when (= sid (get sids 0))
        (:= last-seq (. (get done.lines -1) seq))))
    (<- closed (ClaudeCloseSession sid "test"))
    (assert (isinstance closed SessionClosed) (repr closed)))
  (val held (len (lfor sid sids :if (is-not (.runtime host sid) None) sid)))
  (var states #())
  (var views #())
  (for [sid sids]
    (<- status (ClaudeSessionStatus spec.home spec.cwd sid))
    (<- view (ClaudeLiveProcess sid))
    (:= states (+ states #(status.state)))
    (:= views (+ views #(view))))
  (val first (get sids 0))
  (<- again (ClaudeStartTurn (ResumeSession first) spec (TurnInput (reply-prompt "THREE") (str (uuid.uuid4)))))
  (<- third (read-to-end again.turn 30.0))
  (<- resumed-view (ClaudeLiveProcess first))
  (ClosedHosts :held held :states states :views views :resumed again :resumed-end third.end :resumed-view resumed-view
               :seq-grew (> (. (get third.lines 0) seq) last-seq)))

(deftest test-closed-sessions-leave-no-runtime-and-resume-where-they-stopped [tmp-path]
  ;; 閉じた会話の生きた状態(手番の記録 — stdout の行の全部)をホストが持ち続けると、閉じた会話の数だけ turn-host の memory が伸び、
  ;; CLI の数が減っても memory が減らない(10-19 の本番 pid 1518593: CLI 96→57 本で ClaudeStreamLine +49,501 個・RssAnon +55 MB —
  ;; lt-5611P8B4MJS6CZQZ1809H94JPQ)。最後の手番の終わりを読んだ会話は、閉じたらホストから外す。外しても閉じた様子と process の
  ;; 見え方(起こした数・降ろした訳)は同じに答え、続きは手番の番号・行の seq・起こした数を続ける。
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (val sids (lfor _ (range 3) (str (uuid.uuid4))))
  (<- seen (with-real-handler host (sessions-closed-then-one-resumed host (spec-in tmp-path) sids)))
  (assert (= seen.held 0) (repr seen.held))
  (assert (all (gfor state seen.states (isinstance state Closed))) (repr seen.states))
  (assert (= seen.views (* #((NoLiveProcess :launches 1 :stopped-because StopReason.SESSION-CLOSED)) 3)) (repr seen.views))
  (assert (isinstance seen.resumed TurnStarted) (repr seen.resumed))
  (assert (= seen.resumed.turn.turn-seq 3) (repr seen.resumed))
  (assert (isinstance seen.resumed-end Completed) (repr seen.resumed-end))
  (assert (= seen.resumed-view (LiveProcess :launches 2)) (repr seen.resumed-view))
  (assert seen.seq-grew "続けた手番の行の seq が閉じる前の行の seq から続かない"))


(defrecord ReadTurnsOnALiveSession
  "閉じていない会話で turn を重ねた観測: held-lines = 全部の turn の終わりを読んだ後に会話の turn の記録が持つ行の数・again = 読み終えた
   turn を読み直した答え。"
  (#^ int held-lines)
  (#^ object again))

(defk read-turns-on-a-live-session [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % ReadTurnsOnALiveSession)]
   :tags {:context "claude-code-test" :role "program"}}
  "閉じない会話で 3 turn を終わりまで読み、読み終えた turn の行を host が持ち続けるかを見るため。"
  (var first None)
  (for [#(i origin) (enumerate [(FreshSession sid) (ResumeSession sid) (ResumeSession sid)])]
    (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt (+ "W" (str i))) (+ "r" (str i)))))
    (<- done (read-to-end started.turn 30.0))
    (assert (isinstance done.end Completed) (repr done.end))
    (when (is first None) (:= first started.turn)))
  (val held (sum (gfor log (.values (. (.runtime host sid) turns)) (len log.lines))))
  (<- again (ClaudeReadTurnEvents first -1 0.0))
  (<- (ClaudeCloseSession sid "test"))
  (ReadTurnsOnALiveSession :held-lines held :again again))

(deftest test-a-live-session-drops-the-lines-of-turns-whose-end-was-read [tmp-path]
  ;; 閉じていない会話も、読み手が終わりまで読んだ turn の行(stdout の行の全部)を持ち続けない — 読み手は終わりを読んだ turn を読み直さない。
  ;; 生きた会話ごとに最近の KEPT-TURNS 本の行を持つと、CLI が減っても running の turn が減っても、本番の turn-host の ClaudeStreamLine が
  ;; 増え続けた(10-19 の pid 1135549: running の turn 88→72 の区間で +39,677 個 — card ki-ff61c7e52cb9)。読み直しには行の無い頁と終わりを答える
  ;; (知らない turn にはしない)。
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- seen (with-real-handler host (read-turns-on-a-live-session host (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (= seen.held-lines 0) (repr seen.held-lines))
  (assert (isinstance seen.again TurnEventPage) (repr seen.again))
  (assert (= seen.again.lines #()) (repr seen.again))
  (assert (isinstance seen.again.end Completed) (repr seen.again)))


(defrecord ClosedMidTurn
  "走っている手番の途中で閉じた観測: held-before = 終わりを読む前にホストが持つ手番の番号(持たなければ None)・end = 読んだ終わり・
   held-after = 終わりを読んだ後もホストが生きた状態を持つか。"
  (#^ object held-before)
  (#^ object end)
  (#^ bool held-after))

(defk closed-mid-turn [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % ClosedMidTurn)]
   :tags {:context "claude-code-test" :role "program"}}
  "道具の途中で会話を閉じ、閉じた後に読み手が最後の手番の終わりを読めるか・読んだ後にホストが会話を外すかを見るため。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  (val runtime (.runtime host sid))
  (val held-before (if (is runtime None) None (sorted runtime.turns)))
  (<- done (read-to-end started.turn 30.0))
  (ClosedMidTurn :held-before held-before :end done.end :held-after (is-not (.runtime host sid) None)))

(deftest test-a-session-closed-mid-turn-is-released-once-its-end-is-read [tmp-path]
  ;; 閉じた時に走っていた手番は、上の層が閉じた後にその終わりを読む(doeff-agents の headless の stop)。終わりを読むまでは、その手番の
  ;; 記録だけを持ち、読んだらホストから外す。
  (setv host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- seen (with-real-handler host (closed-mid-turn host (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (= seen.held-before [1]) (repr seen.held-before))
  (assert (isinstance seen.end Interrupted) (repr seen.end))
  (assert (not seen.held-after) "終わりを読んだ後も閉じた会話の生きた状態をホストが持ち続けた"))


(defk two-turns [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]}
  (setv ends [])
  (for [#(origin word) [#((FreshSession sid) "ONE") #((ResumeSession sid) "TWO")]]
    (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
    (<- done (read-to-end started.turn 30.0))
    (.append ends done.end))
  ends)


(defk two-turns-on [origin #^ ClaudeSessionSpec spec]
  {:pre [(: origin (| FreshSession ResumeSession)) (: spec ClaudeSessionSpec)] :post [(: % list)]
   :tags {:context "claude-code" :role "program"}}
  "origin から起こした process で同じ会話を 2 手番続けるため(2 手番目は同じ process を使い回す)。答え = 2 つの手番の終わり。"
  (val sid origin.session-id)
  (val ends [])
  (for [#(next-origin word) [#(origin "ONE") #((ResumeSession sid) "TWO")]]
    (<- started (ClaudeStartTurn next-origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
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
          (repr ends))
  ;; 同じ process の 2 手番目の modelUsage は 1 手番目との和(実物と同じ累積)。手番の model ごとの使用量は額と同じ起点の差で 1 手番分、
  ;; 和は手番の額と合う(card ki-1d986d4f0007)。
  (assert (all (gfor end ends (priced-like-one-stub-turn end))) (repr ends)))


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
  (assert (= (lfor end (cut ends 1 None) end.cost-usd) [0.25 0.25]) (repr ends))
  ;; model ごとの起点も額と同じく消えた process の始まりへ戻る(card ki-1d986d4f0007)。
  (assert (all (gfor end (cut ends 1 None) (priced-like-one-stub-turn end))) (repr ends)))


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
  (assert (= (. (get ends 2) cost-usd) 0.25) (repr ends))
  (assert (priced-like-one-stub-turn (get ends 2)) (repr ends)))


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
  (assert (= three.cost-usd 0.25) (repr three))
  ;; --resume の process の modelUsage も前の process が記した累積から数え続ける。起点は cost-state の行の額と modelUsage の組で、
  ;; どの手番の model ごとの使用量も 1 手番分(card ki-1d986d4f0007)。
  (assert (all (gfor end [one two three] (priced-like-one-stub-turn end))) (repr [one two three])))


(deftest test-a-new-host-resuming-a-transcript-without-a-cost-line-counts-from-zero [tmp-path]
  ;; card ki-4b9be8d95d9f: 額の行が 1 つも無い transcript(額を記す前に消えた process の会話 — host の起き直しで切れた
  ;; aj-667MHNMS57VHYM4NXBEPBWKHBD)の、この host の知らない続き。CLI は 0 から数える(実測 2.1.292: 偽の Messages API で、行を全部
  ;; 消した transcript の続きの total_cost_usd はその呼びの分だけ。替え玉も同じ)ので、起点は 0 で手番の額はその手番の分。
  ;; 失敗ケース = 行が無い時に起点を None にする形では、額も model ごとの使用量も出ない(UNKNOWN-MARK)。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (.mkdir path.parent :parents True :exist-ok True)
  (.write-text path "{\"type\": \"user\", \"text\": \"earlier\"}\n" :encoding "utf-8")
  (val host (host-of STUB-COMMAND))
  (<- two (with-real-handler host (turn-until-down host (ResumeSession sid) spec "TWO")))
  (assert (isinstance two Completed) (repr two))
  (assert (= two.cost-usd 0.25) (repr two))
  (assert (priced-like-one-stub-turn two) (repr two))
  (val next-host (host-of STUB-COMMAND))
  (<- three (with-real-handler next-host (turn-until-down next-host (ResumeSession sid) spec "THREE")))
  (assert (= three.cost-usd 0.25) (repr three))
  (assert (priced-like-one-stub-turn three) (repr three)))


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
  (assert (= [one.cost-usd carried.cost-usd forked.cost-usd] [0.25 0.25 0.25]) (repr [one carried forked]))
  (assert (all (gfor end [one carried forked] (priced-like-one-stub-turn end))) (repr [one carried forked])))


(defn spec-with-stub-model-usage [#^ Path tmp-path #^ str shape]
  "替え玉の CLI に実物と違う modelUsage の形(env STUB_CLI_MODEL_USAGE — drift・bare-cost-state)を出させる会話の宣言。"
  (setv base (spec-in tmp-path))
  (replace base :home (ClaudeHome base.home.config-dir (| (dict base.home.env) {"STUB_CLI_MODEL_USAGE" shape}))))


(defk signalled-then-resumed [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % list)]
   :tags {:context "claude-code" :role "program"}}
  "注入の無い手番を道具の途中で止め(SIGINT の形 — 替え玉は result の行を出し、累積を cost-state に記して降りる)、同じ会話を続けるため。
   答え = 止めた手番と続きの手番の終わり。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (sleep-prompt 40 "NEVER") (str (uuid.uuid4)))))
  (<- (read-to-tool-start started.turn 30.0))
  (<- (ClaudeInterruptTurn started.turn))
  (<- stopped (read-to-end started.turn 30.0))
  (<- resumed (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "BACK") (str (uuid.uuid4)))))
  (<- done (read-to-end resumed.turn 30.0))
  [stopped.end done.end])


(deftest test-a-stopped-turn-is-left-out-of-the-next-turns-model-usage [tmp-path]
  ;; card ki-1d986d4f0007: 止めた手番(SIGINT の形)の額と model ごとの使用量は数えずに捨て、次の手番の起点へ動かす。続きの手番の
  ;; model ごとの使用量はその手番の分だけ(止めた手番の分 — 替え玉では 1 手番分の累積 — を混ぜない)で、和は手番の額と合う。
  (val host (host-of STUB-COMMAND))
  (<- ends (with-real-handler host (signalled-then-resumed host (spec-in tmp-path) (str (uuid.uuid4)))))
  (assert (isinstance (get ends 0) Interrupted) (repr ends))
  (assert (priced-like-one-stub-turn (get ends 1)) (repr ends)))


(deftest test-a-cost-state-without-model-usage-gives-the-cost-but-says-the-model-mark-is-unknown [tmp-path]
  ;; card ki-1d986d4f0007: modelUsage を記さない cost-state の行(古い CLI の形)から続けた手番は、額は前と同じく数えるが、model ごとの
  ;; 起点が分からないので増分を推測で作らず訳 UNKNOWN-MARK を記す。同じ process の次の手番は result の行の累積を起点に数え直す。
  (val spec (spec-with-stub-model-usage tmp-path "bare-cost-state"))
  (val sid (str (uuid.uuid4)))
  (val first-host (host-of STUB-COMMAND))
  (<- one (with-real-handler first-host (turn-until-down first-host (FreshSession sid) spec "ONE")))
  (assert (priced-like-one-stub-turn one) (repr one))
  (val second-host (host-of STUB-COMMAND))
  (<- ends (with-real-handler second-host (two-turns-on (ResumeSession sid) spec)))
  (assert (= (lfor end ends end.cost-usd) [0.25 0.25]) (repr ends))
  (assert (is (. (get ends 0) model-usage) None) (repr ends))
  (assert (= (. (get ends 0) model-usage-gap) ModelUsageGap.UNKNOWN-MARK) (repr ends))
  (assert (priced-like-one-stub-turn (get ends 1)) (repr ends)))


(deftest test-model-costs-that-drift-from-the-turn-cost-are-recorded-as-a-gap [tmp-path]
  ;; card ki-1d986d4f0007: model ごとの額の和(替え玉の drift = 半分の 0.125)が手番の額 0.25 と 0.01 USD を越えて違う手番は、増分を
  ;; そのまま運び、訳 SUM-MISMATCH を記す(合わせるために額を書き換えない)。
  (val host (host-of STUB-COMMAND))
  (<- one (with-real-handler host (turn-until-down host (FreshSession (str (uuid.uuid4))) (spec-with-stub-model-usage tmp-path "drift")
                                                   "ONE")))
  (assert (= one.cost-usd 0.25) (repr one))
  (assert (= (lfor usage one.model-usage usage.cost-usd) [0.125]) (repr one))
  (assert (= one.model-usage-gap ModelUsageGap.SUM-MISMATCH) (repr one)))


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


;; 読み手の thread の死(card ki-bd3aa18e3e71): on-line が例外を投げた process は降ろされ、持ち主は期限を待たずに訳つきの失敗を受ける。
;; 失敗ケース = 読み手が try / finally だけで生きた CLI を待つ形では、init の前は launch-timeout の「no init line」、使い回しの process
;; の手番は終わりの行が来ないまま読みの期限まで返らない(10-18 の scheduled() の RuntimeError の形)。どちらの CLI も、読み手が死んだ
;; 行の後は stdout へ書かずに stdin を待って生きる(本物の CLI と同じ — 書けば broken pipe で自分から降り、期限の待ちを写せない)。
(val READER-DEATH-DEADLINE 60.0)
(val READER-DEATH-WITHIN 30.0)
(val READER-DEATH-REASON "stdout reader failed: RuntimeError: scheduled() outside the scheduler")
;; init の前の行を 1 行だけ書き、stdin の EOF まで生きる CLI。
(val HOOK-THEN-WAIT
     (+ "import sys\n"
        "sys.stdout.write('{\"type\":\"system\",\"subtype\":\"hook_started\"}\\n'); sys.stdout.flush()\n"
        "sys.stdin.read()\n"))

(defn reader-dies [#* args]
  (raise (RuntimeError "scheduled() outside the scheduler")))

(val on-line-of-handler handler-module.on-line)

(defn reader-dies-at-the-result [runtime binding clock raw]
  ;; 手番の終わりの行(替え玉はその後 stdin を待つ)で死ぬ読み手。
  (if (= (.get (json.loads raw) "type") "result")
      (reader-dies)
      (on-line-of-handler runtime binding clock raw)))

(deftest test-a-reader-that-dies-before-init-is-a-launch-failure-with-its-reason [tmp-path monkeypatch]
  (.setattr monkeypatch handler-module "on_line" reader-dies)
  (val sid (str (uuid.uuid4)))
  (val host (host-of #(sys.executable "-c" HOOK-THEN-WAIT) :launch-timeout READER-DEATH-DEADLINE))
  (val started (time.monotonic))
  (<- outcome (with-real-handler host (ClaudeStartTurn (FreshSession sid) (spec-in tmp-path) (TurnInput (reply-prompt "ONE") "r1"))))
  (val seconds (- (time.monotonic) started))
  (assert (isinstance outcome LaunchFailed) (repr outcome))
  (assert (in READER-DEATH-REASON outcome.stderr-tail) (repr outcome))
  (assert (< seconds READER-DEATH-WITHIN) (.format "読み手の死の後 {:.1f} 秒 待った" seconds))
  (assert (is (.runtime host sid) None)))

(defk turn-then-reader-dies [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid #^ pytest.MonkeyPatch monkeypatch]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str) (: monkeypatch pytest.MonkeyPatch)] :post [(: % tuple)]
   :tags {:context "claude-code-test" :role "program"}}
  "1 手番を読み終えてから読み手を死なせ、同じ process を使い回す 2 手番目を読むため。答え = 2 手番目の記録・その読みの秒数・
   2 手番目が 1 手番目と同じ process で始まったか。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "ONE") "r1")))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  (val first-process (. (.runtime host sid) process))
  (.setattr monkeypatch handler-module "on_line" reader-dies-at-the-result)
  (val begun (time.monotonic))
  (<- again (ClaudeStartTurn (ResumeSession sid) spec (TurnInput (reply-prompt "TWO") "r2")))
  (val same (is (. (.runtime host sid) process) first-process))
  (<- second (read-to-end again.turn READER-DEATH-DEADLINE))
  #(second (- (time.monotonic) begun) same))

(deftest test-a-reader-that-dies-in-a-reused-process-fails-the-turn-with-its-reason [tmp-path monkeypatch]
  (val host (host-of #(sys.executable "-m" "hy" STUB-PATH)))
  (<- outcome (with-real-handler host (turn-then-reader-dies host (spec-in tmp-path) (str (uuid.uuid4)) monkeypatch)))
  (val second (get outcome 0))
  (val seconds (get outcome 1))
  (val same (get outcome 2))
  (assert same "2 手番目が process を使い回さなかった")
  (assert (isinstance second.end BackendLost) (repr second.end))
  (assert (in READER-DEATH-REASON second.end.stderr-tail) (repr second.end))
  (assert (< seconds READER-DEATH-WITHIN) (.format "読み手の死の後 {:.1f} 秒 待った" seconds)))


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


;; 書き出しの読みを止めておく上限の秒(合図が来なければこの秒で読みが進む)。
(val HELD-READ-SECONDS 10.0)


(defk export-while-another-runs [#^ ClaudeExportSession request #^ threading.Event gate]
  {:pre [(: request ClaudeExportSession) (: gate threading.Event)] :post [(: % "書き出しの答え")] :tags {:context "claude-code" :role "program"}}
  "合図まで止まる transcript の読みを別の task で書き出し、外の合図を待ってから合図を出し、書き出しの答えを返すため。読みが scheduler の
   thread で待つと、この task は外の合図の後に戻れず、読みの上限の秒まで合図を出せない。"
  (<- exporting Task (Spawn request))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- exported (Wait exporting))
  exported)


(deftest test-another-task-runs-while-the-transcript-is-read-for-export [tmp-path monkeypatch]
  ;; transcript(数十 MB の jsonl)の読みは scheduler の thread の外で待つ(card ki-24ee30105583 — turn-host の手番の終わりの書き出しが、
  ;; 詰まった disk の上で同じ process の全部の手番を止めた)。読みは合図まで Path.read_text の頭で止まり、合図を出すのは同じ scheduler の
  ;; 別の task。失敗ケース = handler の節が scheduler の thread で読む形では、合図は読みの上限の 10 秒の後にしか出ない。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val body "{\"type\":\"user\",\"text\":\"held\"}\n")
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (.mkdir path.parent :parents True)
  (.write-text path body :encoding "utf-8")
  (val gate (threading.Event))
  (val original Path.read-text)
  (.setattr monkeypatch Path "read_text" (fn [self #* args #** kwargs] (.wait gate HELD-READ-SECONDS) (original self #* args #** kwargs)))
  (val host (host-of #("/nonexistent/claude-binary")))
  (val started (time.monotonic))
  (<- exported (with-real-handler host (export-while-another-runs (ClaudeExportSession spec.home spec.cwd sid) gate)))
  (val seconds (- (time.monotonic) started))
  (assert (= exported (SessionExported body)) (repr exported))
  (assert (< seconds (/ HELD-READ-SECONDS 2)) (.format "書き出しの読みが scheduler の thread を {:.1f} 秒塞いだ" seconds)))


(defk written-transcript [#^ ClaudeSessionSpec spec #^ str sid #^ str body]
  {:pre [(: spec ClaudeSessionSpec) (: sid str) (: body str)] :post [(: % Path)] :tags {:context "claude-code" :role "program"}}
  "読みの test の前に、会話 sid の transcript を body で置くため(置き場は handler と同じ transcript-path)。答え = その path。"
  (val path (Path (transcript-path spec.home.config-dir (os.path.realpath spec.cwd) sid)))
  (.mkdir path.parent :parents True :exist-ok True)
  (.write-text path body :encoding "utf-8")
  path)


(deftest test-export-with-tail-bytes-reads-only-the-whole-last-lines [tmp-path]
  ;; 末尾だけの取り出し(card ki-a264a73efcce): 末尾の tail-bytes byte に丸ごと収まる行だけを、行の頭から返す。切り口がちょうど行の頭なら
  ;; その行も残す。file より広い幅は全部。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val lines ["{\"type\":\"user\",\"text\":\"一つ目\"}\n" "{\"type\":\"assistant\",\"text\":\"二つ目\"}\n" "{\"type\":\"user\",\"text\":\"三\"}\n"])
  (<- (written-transcript spec sid (.join "" lines)))
  (val last-two (.join "" (cut lines 1 None)))
  (val host (host-of #("/nonexistent/claude-binary")))
  (<- exact (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :tail-bytes (len (.encode last-two "utf-8")))))
  (assert (= exact (SessionExported last-two)) (repr exact))
  (<- inside (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :tail-bytes (- (len (.encode last-two "utf-8")) 1))))
  (assert (= inside (SessionExported (get lines 2))) (repr inside))
  (<- wide (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :tail-bytes 1000000)))
  (assert (= wide (SessionExported (.join "" lines))) (repr wide)))


(deftest test-export-from-byte-reads-only-what-follows-it [tmp-path]
  ;; 伸びた分だけの取り出し(card ki-3b521394a063): from-byte byte 目から末尾までを返す(行の途中からでも字の頭なら読める)。file より
  ;; 長い from-byte・字の途中の from-byte は SessionNotFound。tail-bytes と一緒には名指せない。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (val body "{\"type\":\"user\",\"text\":\"一つ目\"}\n{\"type\":\"assistant\",\"text\":\"二つ目\"}\n")
  (<- (written-transcript spec sid body))
  (val whole (.encode body "utf-8"))
  (val cut-at (.index whole (.encode "二つ目" "utf-8")))
  (val host (host-of #("/nonexistent/claude-binary")))
  (<- after (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :from-byte cut-at)))
  (assert (= after (SessionExported (.decode (cut whole cut-at None) "utf-8"))) (repr after))
  (<- from-head (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :from-byte 0)))
  (assert (= from-head (SessionExported body)) (repr from-head))
  (<- beyond (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :from-byte (+ (len whole) 1))))
  (assert (= beyond (SessionNotFound sid)) (repr beyond))
  (<- inside (with-real-handler host (ClaudeExportSession spec.home spec.cwd sid :from-byte (+ cut-at 1))))
  (assert (= inside (SessionNotFound sid)) (repr inside))
  (assert (try
            (do (ClaudeExportSession spec.home spec.cwd sid :tail-bytes 10 :from-byte 0) False)
            (except [ValueError] True))))


(deftest test-the-recorded-cost-is-read-from-the-tail-and-widens-until-found [tmp-path]
  ;; 費用の起点は transcript の末尾から読む(card ki-a264a73efcce)。最後の cost-state が最初の幅(64 KiB)に在ればそれ。末尾の 64 KiB に
  ;; 無ければ幅を広げて探す(前の cost-state を見落とさない)。頭から全部読んでも行が無ければ 0(CLI が 0 から数える — card
  ;; ki-4b9be8d95d9f)。file が無ければ None。
  (val spec (spec-in tmp-path))
  (val canonical (os.path.realpath spec.cwd))
  (val filler (* "{\"type\":\"assistant\",\"text\":\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\"}\n" 2000))
  (assert (> (len filler) 65536))
  (val near (str (uuid.uuid4)))
  (<- (written-transcript spec near (+ "{\"type\":\"cost-state\",\"totalCostUSD\":0.5}\n" filler "{\"type\":\"cost-state\",\"totalCostUSD\":1.25}\n")))
  (val far (str (uuid.uuid4)))
  (<- (written-transcript spec far (+ "{\"type\":\"cost-state\",\"totalCostUSD\":0.5}\n" filler)))
  (val none (str (uuid.uuid4)))
  (<- (written-transcript spec none filler))
  (<- near-mark (recorded-cost-mark spec.home canonical near))
  (assert (= near-mark (CostMark 1.25 None)) (repr near-mark))
  (<- far-mark (recorded-cost-mark spec.home canonical far))
  (assert (= far-mark (CostMark 0.5 None)) (repr far-mark))
  (<- none-mark (recorded-cost-mark spec.home canonical none))
  (assert (= none-mark (CostMark 0.0 #())) (repr none-mark))
  (<- missing-mark (recorded-cost-mark spec.home canonical (str (uuid.uuid4))))
  (assert (is missing-mark None) (repr missing-mark)))


(deftest test-the-recorded-cost-beyond-the-widest-tail-is-read-from-the-whole-file [tmp-path]
  ;; card ki-4b9be8d95d9f: 16 MiB の末尾に cost-state の行が無い transcript は、頭から全部を読んで「もっと前に在る」と「1 つも無い」を
  ;; 分ける。失敗ケース = 末尾の幅だけで止める形では、どちらも None(続きの手番の額が出ない)。
  (val spec (spec-in tmp-path))
  (val canonical (os.path.realpath spec.cwd))
  (val line "{\"type\":\"assistant\",\"text\":\"xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\"}\n")
  (val filler (* line (+ (// (get COST-TAIL-BYTES -2) (len line)) 2)))
  (assert (> (len filler) (get COST-TAIL-BYTES -2)))
  (val early (str (uuid.uuid4)))
  (<- (written-transcript spec early (+ "{\"type\":\"cost-state\",\"totalCostUSD\":0.5}\n" filler)))
  (val never (str (uuid.uuid4)))
  (<- (written-transcript spec never filler))
  (<- early-mark (recorded-cost-mark spec.home canonical early))
  (assert (= early-mark (CostMark 0.5 None)) (repr early-mark))
  (<- never-mark (recorded-cost-mark spec.home canonical never))
  (assert (= never-mark (CostMark 0.0 #())) (repr never-mark)))


(defk cost-mark-while-another-runs [#^ ClaudeSessionSpec spec #^ str sid #^ threading.Event gate]
  {:pre [(: spec ClaudeSessionSpec) (: sid str) (: gate threading.Event)] :post [(: % (| CostMark None))]
   :tags {:context "claude-code" :role "program"}}
  "合図まで止まる transcript の読みで費用の起点を別の task に読ませ、外の合図を待ってから合図を出し、起点の答えを返すため。読みが
   scheduler の thread で待つと、この task は外の合図の後に戻れず、読みの上限の秒まで合図を出せない。"
  (<- reading Task (Spawn (recorded-cost-mark spec.home (os.path.realpath spec.cwd) sid)))
  (<- promise (CreateExternalPromise))
  (.start (threading.Timer 0.2 (fn [] (.complete promise None))))
  (<- (Wait promise.future))
  (.set gate)
  (<- mark (Wait reading))
  mark)


(deftest test-another-task-runs-while-the-recorded-cost-is-read [tmp-path monkeypatch]
  ;; 起こす前の費用の起点の読み(recorded-cost-mark)も scheduler の thread の外で待つ(card ki-14b918a38cd5 — 206 MB の transcript の会話を
  ;; 起こす準備の間、turn-host の log が 81〜130 秒 1 行も出なかった)。読みは合図まで read-transcript-bytes の頭で止まり、合図を出すのは
  ;; 同じ scheduler の別の task。失敗ケース = 起点を scheduler の thread で読む形では、合図は読みの上限の 10 秒の後にしか出ない。
  (val spec (spec-in tmp-path))
  (val sid (str (uuid.uuid4)))
  (<- (written-transcript spec sid "{\"type\":\"cost-state\",\"totalCostUSD\":0.75}\n"))
  (val gate (threading.Event))
  (val original handler-module.read-transcript-bytes)
  (.setattr monkeypatch handler-module "read_transcript_bytes"
            (fn [path tail-bytes] (.wait gate HELD-READ-SECONDS) (original path tail-bytes)))
  (val host (host-of #("/nonexistent/claude-binary")))
  (val started (time.monotonic))
  (<- mark (with-real-handler host (cost-mark-while-another-runs spec sid gate)))
  (val seconds (- (time.monotonic) started))
  (assert (= mark (CostMark 0.75 None)) (repr mark))
  (assert (< seconds (/ HELD-READ-SECONDS 2)) (.format "費用の起点の読みが scheduler の thread を {:.1f} 秒塞いだ" seconds)))


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


(defrecord DeltasReadMidTurn
  "走っている手番を途中まで読んだ観測: delivered = 道具が始まるまでに読み手へ渡した行・held = その時に手番の記録が持っていた行・
   rest = 終わりまで読んだ残りの行と終わり。"
  (#^ tuple delivered)
  (#^ tuple held)
  (#^ TurnRecord rest))

(defk deltas-read-mid-turn [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % DeltasReadMidTurn)]
   :tags {:context "claude-code-test" :role "program"}}
  "考えの差分と道具の命令の差分を流してから道具を数秒走らせ、本文も差分で流す手番を、道具が始まるまで読み(手番はまだ走っている)、
   その時に手番の記録が持つ行を控えてから終わりまで読むため。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec
                               (TurnInput (.join " . " [(sleep-prompt 2 "DRAINED") (.format THINKING-PIECES-PHRASE THINKING-PIECES)
                                                        (.format TOOL-INPUT-PIECES-PHRASE TOOL-INPUT-PIECES)
                                                        (.format STREAM-PHRASE STREAMED-PIECES)])
                                          (str (uuid.uuid4)))))
  (<- head TurnRecord (read-to-tool-start started.turn 30.0))
  (val runtime (.runtime host sid))
  (val held (with [runtime.lock] (tuple (. (get runtime.turns started.turn.turn-seq) lines))))
  (<- rest TurnRecord (read-until started.turn (fn [lines end] (is-not end None)) 30.0 head.after))
  (<- (ClaudeCloseSession sid "test"))
  (DeltasReadMidTurn :delivered head.lines :held held :rest rest))

(deftest test-a-running-turn-drops-the-delta-lines-it-has-handed-to-the-reader [tmp-path]
  ;; 走っている手番も、読み手に渡した差分の行(PartialMessage — stream_event)を終わりまで持たない(card ki-27d4694258b0)。差分は答えの
  ;; 途中の写しで、同じ中身は後の assistant の行が運び、読み手は渡された所より後しか読まない。本番の turn-host は running の手番 1 本
  ;; あたり約 900 行を持ち、その 8 割 余りが差分の行だった(10-20 の pid 3619915 の heap-census: ClaudeStreamLine 14,721・PartialMessage
  ;; 12,399・running 16)。答えを組み立てる行(init・道具の呼びの assistant の行)は残し、まだ渡していない行も残す。手番の終わりの計時の行の
  ;; 差分の数と最初の刻は、行を捨てた後も、捨てない時と同じに数える。失敗ケース = 直す前は渡した差分の行を全部持っていて赤。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (deltas-read-mid-turn host (spec-in tmp-path) (str (uuid.uuid4))) :types #(SlogEffect))))
  (val seen (get heard 0))
  (val delivered-seqs (sfor line seen.delivered line.seq))
  (val delivered-deltas (lfor line seen.delivered :if (isinstance line.kind PartialMessage) line))
  (assert (>= (len delivered-deltas) (+ THINKING-PIECES TOOL-INPUT-PIECES)) (repr seen.delivered))
  (val held-deltas (lfor line seen.held :if (and (in line.seq delivered-seqs) (isinstance line.kind PartialMessage)) line))
  (assert (= held-deltas []) (.format "渡した差分の行を {} 本持っている" (len held-deltas)))
  (val kept (lfor line seen.delivered :if (not (isinstance line.kind PartialMessage)) line.seq))
  (assert (= (lfor line seen.held :if (in line.seq delivered-seqs) line.seq) kept) (repr seen.held))
  (assert (isinstance seen.rest.end Completed) (repr seen.rest.end))
  (val ends (lfor effect (get heard 1) :if (and (= effect.msg CLI-TIMING-LOG) (= (.get effect.kwargs "event") "turn-end"))
                  effect.kwargs))
  (assert (= (len ends) 1) (repr ends))
  (val end (get ends 0))
  (assert (= #((get end "thinking_deltas") (get end "tool_input_deltas") (get end "partial_lines"))
             #(THINKING-PIECES TOOL-INPUT-PIECES STREAMED-PIECES))
          (repr end))
  (for [name ["first_thinking_since_launch_ms" "first_tool_input_since_launch_ms" "first_partial_since_launch_ms"]]
    (assert (and (isinstance (get end name) int) (<= 0 (get end name) (get end "since_launch_ms"))) (repr end))))


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


;; 替え玉の CLI が名乗る計時の値(tests/stub_cli/claude.hy の STUB-TTFT-MS・STUB-STARTUP と同じ — 替え玉は script なので import しない)。
(val STUB-TTFT-MS 7)
(val STUB-STARTUP-PHASES {"node_boot_ms" 30 "hooks_init_ms" 20 "input_ready_ms" 60})
(val STUB-STARTUP-STARTS {"node_boot_ms" 0 "hooks_init_ms" 30 "input_ready_ms" 0})

(deftest test-the-timing-lines-carry-the-cli-own-timing [tmp-path]
  ;; #3855: 送ってから最初の字までを割るため、計時の行は CLI が名乗った計時の欄も運ぶ。init の行 = 起動の区間(handler が起こす process の
  ;; env に CLAUDE_CODE_EMIT_STARTUP_TIMING を置くので、替え玉も実物と同じく最初の init に startup_timing を出す)・first-text の行 =
  ;; message_start の ttft_ms(要求を送ってから message_start まで)・turn-end の行 = result の time_to_request_ms などと、要求までの
  ;; 区間(入力ごとの hook が input_hooks — 和が time_to_request_ms)。失敗ケース = 欄を捨てていた形と、env を足さない形は欄が無くて赤。
  (val host (host-of STUB-COMMAND))
  (<- heard (with_handlers [(sync-time-handler) slog-discard-handler listen-handler (claude-code-handler host)]
              (Listen (thinking-then-plain-then-silent (spec-in tmp-path) (str (uuid.uuid4))) :types #(SlogEffect))))
  (val timings (lfor effect (get heard 1) :if (= effect.msg CLI-TIMING-LOG) effect.kwargs))
  (val inits (lfor timing timings :if (= (.get timing "event") "init") timing))
  (assert (= (len inits) 1) (repr inits))
  (val init (get inits 0))
  (assert (= (json.loads (.get init "cli_startup_phases" "null")) STUB-STARTUP-PHASES) (repr init))
  (assert (= (json.loads (.get init "cli_startup_phase_starts" "null")) STUB-STARTUP-STARTS) (repr init))
  (assert (= (.get init "cli_startup_origin_ms") 1791385479741) (repr init))
  ;; 考えた手番だけが message_start を出す(替え玉の形)— 考えない手番の init の後の最初の stream の行は block の始まりで ttft_ms を名乗らない。
  (val firsts (lfor timing timings :if (= (.get timing "event") "first-text") timing))
  (assert (= (lfor first firsts (.get first "request_to_stream_start_ms")) [STUB-TTFT-MS None]) (repr firsts))
  (val ends (lfor timing timings :if (= (.get timing "event") "turn-end") timing))
  (assert (= (len ends) 3) (repr ends))
  (val thought (get ends 0))
  (val phases (json.loads (.get thought "cli_request_phases" "{}")))
  (assert (>= (.get phases "input_hooks" 0) (* 0.8 HOOK-SECONDS 1000)) (repr thought))
  (assert (= (sum (.values phases)) (.get thought "cli_time_to_request_ms")) (repr thought))
  (assert (<= (.get thought "cli_time_to_request_ms") (.get thought "cli_ttft_stream_ms") (.get thought "cli_duration_ms"))
          (repr thought))
  (assert (all (gfor end ends (isinstance (.get end "cli_time_to_request_ms") int))) (repr ends))
  ;; message_start を出さない手番は ttft_stream_ms を名乗らない — 欄は None(0 を発明しない)。
  (assert (is (.get (get ends 1) "cli_ttft_stream_ms" "absent") None) (repr ends)))


;; 同じ秒の中の 2 つの刻(#3855 の日次の赤の形): CLI へ書き始めた刻と、その後に答えが返った刻。
(val WRITTEN-AT (datetime 2026 10 7 3 0 0 329600 :tzinfo timezone.utc))
(val ANSWERED-AT (datetime 2026 10 7 3 0 0 329900 :tzinfo timezone.utc))

(deftest test-a-written-stamp-is-not-seen-after-a-later-stamp-in-the-same-second
  ;; 計時の行の壁の時刻の欄は、行を並べる側と同じ doeff-time の epoch-ms-of(床へ)で綴る(#3855): 329.6 ms に CLI へ書いた行
  ;; (reused)の wall_ms は、その後の 329.9 ms を epoch-ms-of で綴った刻より後に見えず、2 つの壁の時刻の差の欄(wall-elapsed-ms)は
  ;; 2 つの刻の欄の差と同じ。失敗ケース = 変更前は書いた刻を四捨五入して 330・後の刻は床で 329 と綴られ、順が逆に見えた。
  (val runtime (SessionRuntime "sid" (ClaudeHome "/h/.claude" {}) "/w"))
  (<- heard (with_handlers [slog-discard-handler listen-handler]
              (Listen (note-reused runtime 1 0.0 0.0 WRITTEN-AT) :types #(SlogEffect))))
  (val written (. (get (get heard 1) 0) kwargs))
  (val answered (epoch-ms-of ANSWERED-AT))
  (assert (= (.get written "event") "reused") (repr written))
  (assert (<= (.get written "wall_ms") answered) (repr #(written answered)))
  (<- gap (wall-elapsed-ms WRITTEN-AT ANSWERED-AT))
  (assert (= gap (- answered (.get written "wall_ms"))) (repr #(gap written answered))))


(deftest test-the-elapsed-fields-of-adjacent-intervals-add-up-to-the-whole
  ;; 経過の欄(GetMonotonic の 2 つの読みの差)は、読みをそれぞれミリ秒へ床へ丸めてから引く(#3855): 続く 2 つの区間の和が通しの区間と
  ;; 同じ。失敗ケース = 変更前は差を四捨五入したので、0.4 ms ずつの 2 つの区間が 0 + 0、通しの 0.8 ms が 1 になった。
  (<- first (elapsed-ms 10.0004 10.0008))
  (<- second (elapsed-ms 10.0008 10.0012))
  (<- whole (elapsed-ms 10.0004 10.0012))
  (assert (= (+ first second) whole) (repr #(first second whole))))


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
   読み取り結果・after = ターンの後の状態・turn-process = ターンを走らせた process・launch-key = ターンの後(閉じる前 — 閉じて読み終えた
   会話はホストから外れる)の起動条件のキー。"
  (#^ (| LiveProcess NoLiveProcess) before)
  (#^ ClaudeProcess warm-process)
  (#^ TurnRecord done)
  (#^ (| LiveProcess NoLiveProcess) after)
  (#^ (| ClaudeProcess None) turn-process)
  (#^ (| str None) launch-key))

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
  (val turn-key (. (.runtime host sid) launch-key))
  (<- (ClaudeCloseSession sid "test"))
  (WarmFirstTurn :before before :warm-process warm-process :done done :after after :turn-process turn-process
                 :launch-key turn-key))


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
  (assert (= seen.launch-key turn-key)))


(defrecord ForbiddenLine
  "最初の入力の前に出たら停止する行 1 つ: name = シナリオの名前・line = 行(JSON の境界の値)。"
  (#^ str name)
  (#^ dict line))

;; 最初の入力の前に出たら停止する行: assistant の行(ターンの外で model が応答した形)と、model の手番の中で走る hook の行
;; (UserPromptSubmit ほか — Notification・SessionEnd の hook の行はターンの外でも止めない・ki-d8b473480303)。
(val FORBIDDEN-BEFORE-INPUT
  [(ForbiddenLine :name "assistant"
                  :line {"type" "assistant" "parent_tool_use_id" None
                         "message" {"role" "assistant" "model" "claude-stub" "content" [{"type" "text" "text" "unasked"}]}})
   (ForbiddenLine :name "other-hook"
                  :line {"type" "system" "subtype" "hook_response" "hook_id" "hook-2" "hook_name" "UserPromptSubmit"
                         "hook_event" "UserPromptSubmit" "output" "" "stdout" "" "stderr" "" "exit_code" 0 "outcome" "success"})])

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


;; --- 資格の床で降ろす(#3672 の D2 — 生かす本数の上限は tests/test_live_limit.hy・#4072 の E1b)------------------------------

(defk one-turn [#^ ClaudeSessionSpec spec origin #^ str word]
  {:pre [(: spec ClaudeSessionSpec) (: origin (| FreshSession ResumeSession)) (: word str)]
   :post [(: % (| Completed Failed Interrupted BackendLost))]}
  "検の会話の手番を 1 つ始めて最後まで読むため。答え = 手番の終わり。"
  (<- started (ClaudeStartTurn origin spec (TurnInput (reply-prompt word) (str (uuid.uuid4)))))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done (read-to-end started.turn 30.0))
  done.end)

(defk expiring-in [#^ ClaudeSessionSpec base #^ float seconds]
  {:pre [(: base ClaudeSessionSpec) (: seconds float)] :post [(: % ClaudeSessionSpec)]}
  "検の spec に、今から seconds 秒後に切れる借りた資格を添えるため(時刻は時間の答え手の内側で読む・token は偽の文字列)。"
  (<- now (GetTime))
  (replace base :credential (LentCredential :access-token "fake-token-floor" :expires-at (+ (.timestamp now) seconds))))

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


;; --- 借りた資格の家(card ki-4ffdafa021e5)--------------------------------------------------------------
;; 借りた資格は子の env に置かず、その process だけの設定 dir(資格の家)の .credentials.json で渡す。走っている process の資格は
;; ClaudeReplaceSessionCredential で替わり、資格だけが替わった次の手番は process を使い回してその手番の資格を家の file に書く。
;; process が降りたら家は消える。token はどれも偽の文字列。

(val LENT-TOKENS #("fake-lent-token-1" "fake-lent-token-2" "fake-lent-token-3"))
;; 検の資格の期限(2100-01-01 の epoch 秒 — 床で止めない遠い先)。
(val LENT-EXPIRES-AT 4102444800.0)

(defk lent-of [#^ str token]
  {:pre [(: token str)] :post [(: % LentCredential)] :tags {:context "claude-code" :role "program"}}
  "検の借りた資格(偽の token・遠い先の期限・契約の種類と階級つき)を作るため。"
  (LentCredential :access-token token :expires-at LENT-EXPIRES-AT :subscription-type "max" :rate-limit-tier "fake-tier"))

(defk process-environ [#^ int pid]
  {:pre [(: pid int)] :post [(: % dict)] :tags {:context "claude-code" :role "program"}}
  "走っている子の process の env を /proc から読むため(Linux — 子の env に token が無い事を外から確かめる)。"
  (val raw (.read-bytes (Path (.format "/proc/{}/environ" pid))))
  (dfor item (.split raw b"\x00") :if (in b"=" item)
        :setv pair (.split (.decode item "utf-8" "replace") "=" 1)
        (get pair 0) (get pair 1)))

(defk mode-of [#^ str path]
  {:pre [(: path str)] :post [(: % int)] :tags {:context "claude-code" :role "program"}}
  "file・dir の権限の bit(0o600 など)を読むため。"
  (stat.S-IMODE (. (os.stat path) st-mode)))

(defk credential-file-of [#^ str home]
  {:pre [(: home str)] :post [(: % dict)] :tags {:context "claude-code" :role "program"}}
  "資格の家の .credentials.json を JSON として読むため(検が中身を期待の形と比べる — JSON の境界)。権限が 0o600 でなければ落とす。"
  (val path (os.path.join home ".credentials.json"))
  (<- mode (mode-of path))
  (assert (= mode 0o600) (oct mode))
  (json.loads (.read-text (Path path) :encoding "utf-8")))

(defk expected-document [#^ str token]
  {:pre [(: token str)] :post [(: % dict)] :tags {:context "claude-code" :role "program"}}
  "lent-of の資格を置いた .credentials.json の期待の中身(CLI 2.1.292 が読む形 — refreshToken は null・期限は epoch ミリ秒・scope は
   user:inference)。"
  {"claudeAiOauth" {"accessToken" token "refreshToken" None "expiresAt" (int (* LENT-EXPIRES-AT 1000)) "scopes" ["user:inference"]
                    "subscriptionType" "max" "rateLimitTier" "fake-tier"}})

(defk lent-credential-walk [#^ ClaudeCodeHost host #^ ClaudeSessionSpec base #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: base ClaudeSessionSpec) (: sid str)] :post [(: % str)]
   :tags {:context "claude-code" :role "program"}}
  "借りた資格の会話を、1 手番・資格の差し替え・資格だけが替わった続きの手番・閉じるの順に進め、各所で資格の家と子の env を確かめる
   ため。答え = 資格の家の path(閉じた後に消えた事は呼び手が確かめる)。"
  (<- first-lent (lent-of (get LENT-TOKENS 0)))
  (val spec (replace base :credential first-lent))
  (val shared spec.home.config-dir)
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "ONE") "r1")))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  (val runtime (.runtime host sid))
  (val binding runtime.binding)
  (val process binding.process)
  (val home binding.credential-home)
  ;; 家は共有の設定 dir の .credential-homes の下・0700。子の CLAUDE_CONFIG_DIR は家を指し、env に token は無い。
  (assert (= (os.path.dirname home) (os.path.join shared ".credential-homes")) home)
  (<- home-mode (mode-of home))
  (assert (= home-mode 0o700) (oct home-mode))
  (<- environ (process-environ process.pid))
  (assert (= (get environ "CLAUDE_CONFIG_DIR") home) (get environ "CLAUDE_CONFIG_DIR"))
  (assert (not-in "CLAUDE_CODE_OAUTH_TOKEN" environ) "子の env に資格の env の名が在る")
  (for [token LENT-TOKENS]
    (assert (not (any (gfor value (.values environ) (in token value)))) "子の env に token の値が在る"))
  (<- document (credential-file-of home))
  (<- wanted (expected-document (get LENT-TOKENS 0)))
  (assert (= document wanted) document)
  ;; 会話の記録は共有の設定 dir に残る(家の projects は共有の projects への link)。
  (assert (os.path.islink (os.path.join home "projects")))
  (assert (os.path.exists (transcript-path shared (os.path.realpath spec.cwd) sid)) "会話の記録が共有の設定 dir に無い")
  ;; 走っている process の資格を替える: 同じ家の file が新しい token になり、別名の書き掛けは残らない。
  (<- second-lent (lent-of (get LENT-TOKENS 1)))
  (<- replaced (ClaudeReplaceSessionCredential sid second-lent))
  (assert (= replaced (CredentialReplaced :session-id sid)) replaced)
  (<- document-2 (credential-file-of home))
  (<- wanted-2 (expected-document (get LENT-TOKENS 1)))
  (assert (= document-2 wanted-2) document-2)
  (assert (= (lfor name (os.listdir home) :if (.startswith name ".credentials.json.") name) []) (os.listdir home))
  ;; 資格だけが替わった次の手番は process を使い回し(起こした数は 1 のまま)、その手番の資格を家の file に書く。
  (<- third-lent (lent-of (get LENT-TOKENS 2)))
  (<- again (ClaudeStartTurn (ResumeSession sid) (replace spec :credential third-lent) (TurnInput (reply-prompt "TWO") "r2")))
  (<- second (read-to-end again.turn 30.0))
  (assert (isinstance second.end Completed) (repr second.end))
  (<- view (ClaudeLiveProcess sid))
  (assert (= view (LiveProcess :launches 1)) view)
  (assert (is (. (.runtime host sid) process) process) "資格だけが替わった手番で process を起こし直した")
  (<- document-3 (credential-file-of home))
  (<- wanted-3 (expected-document (get LENT-TOKENS 2)))
  (assert (= document-3 wanted-3) document-3)
  ;; 閉じると process が降り、家が消える(消した後に呼び鈴が鳴る)。
  (<- closed (ClaudeCloseSession sid "test"))
  (assert (isinstance closed SessionClosed) (repr closed))
  (<- gone (wait-until (fn [bell] (.hang runtime.doorbell bell)) (fn [] (not (os.path.exists home))) 10.0))
  (assert gone (.format "process が降りても資格の家 {} が残った" home))
  home)


(deftest test-a-lent-credential-rides-on-a-per-process-config-dir-and-is-replaced-in-place [tmp-path]
  ;; card ki-4ffdafa021e5: 起こした CLI の env に token は無く、process だけの設定 dir の .credentials.json(0600)が
  ;; 期待どおりの中身を持つ。差し替えの effect の後と、資格だけが替わった次の手番の後に、同じ file が新しい token になる(process は
  ;; 起こし直さない)。process が降りたら dir は消え、共有の設定 dir の .credential-homes は空に戻る。
  (val host (host-of STUB-COMMAND))
  (val base (spec-in tmp-path))
  (<- home (with-real-handler host (lent-credential-walk host base (str (uuid.uuid4)))))
  (assert (not (os.path.exists home)))
  (assert (= (os.listdir (os.path.dirname home)) []) (os.listdir (os.path.dirname home))))


(defk home-credential-turn [#^ ClaudeCodeHost host #^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: host ClaudeCodeHost) (: spec ClaudeSessionSpec) (: sid str)] :post [(: % dict)]
   :tags {:context "claude-code" :role "program"}}
  "借りた資格の無い宣言で 1 手番を走らせ、閉じる前の子の env を返すため。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "HOME") "r1")))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  (val binding (. (.runtime host sid) binding))
  (assert (is binding.credential-home None) binding.credential-home)
  (<- environ (process-environ binding.process.pid))
  (<- (ClaudeCloseSession sid "test"))
  environ)


(deftest test-a-session-without-a-lent-credential-runs-on-the-home-config-dir
  [tmp-path]
  ;; 手で開いた機体の会話の路(借りた資格の無い起動)は今までどおり: 子の CLAUDE_CONFIG_DIR は家の設定 dir で、資格の家を作らない。
  (val host (host-of STUB-COMMAND))
  (val spec (spec-in tmp-path))
  (<- environ (with-real-handler host (home-credential-turn host spec (str (uuid.uuid4)))))
  (assert (= (get environ "CLAUDE_CONFIG_DIR") spec.home.config-dir) (get environ "CLAUDE_CONFIG_DIR"))
  (assert (not (os.path.exists (os.path.join spec.home.config-dir ".credential-homes")))))
