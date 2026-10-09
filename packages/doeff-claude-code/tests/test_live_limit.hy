;; 生かす本数の上限(#4072 の E1b)— 本番の handler(替え玉の CLI)と fake に同じ筋書きを当てる。
;;
;; 上限を越える起動でも、handler は process を止めず、起動を待たせず、失敗にもしない。越えた事は知らせ ClaudeLiveLimitExceeded で
;; ホストへ届け、本番の handler は log の 1 行も出す。止める CLI を選ぶのは上の層のホストの 1 か所だけ。
;; 失敗ケース = 変更前の本番の handler は、上限で手番を走らせていない一番古い process を黙って止め(訳 LIVE-LIMIT・知らせも log も無い)、
;; 全部が手番を走らせていれば空くまで起動を待った。fake は上限を持たなかった。
(require doeff-hy.macros [defhandler deftest defk <- val])
(import sys)
(import uuid)
(import pathlib [Path])
(import pytest)
(import doeff [with_handlers])
(import doeff_core_effects.effects [Listen SlogEffect])
(import doeff_core_effects.handlers [listen-handler slog-discard-handler])
(import doeff_time [SimClock sim-time-handler sync-time-handler])
(import doeff_claude_code.values [ClaudeHome ClaudeSessionSpec FreshSession TurnInput])
(import doeff_claude_code.lines [Completed])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeWarmSession ClaudeCloseSession ClaudeSessionStatus ClaudeLiveLimitExceeded
                                   TurnStarted SessionWarmed TurnRunning])
(import doeff_claude_code.faults [ClaudeLiveProcess LiveProcess])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [LIVE-LIMIT-LOG ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.fake [FakeClaudeWorld fake-claude-code-handler])
(import tests.interpreters [STUB-PATH child-env fake-responder])
(import tests.scenario_rules [reply-prompt sleep-prompt])
(import tests.scenario_steps [read-to-end read-to-tool-start])

(val STUB-COMMAND #(sys.executable "-m" "hy" STUB-PATH))


(defhandler host-hears []
  ;; ホストの役: 知らせに None で答える(何が届いたかは内側の listen-handler が通り道で覚える)。
  (ClaudeLiveLimitExceeded [session-id live limit warm]
    (resume None)))

(defn spec-in [#^ Path tmp-path]
  (setv work (/ tmp-path "work"))
  (.mkdir work :parents True :exist-ok True)
  (ClaudeSessionSpec :home (ClaudeHome (str (/ tmp-path "home")) (child-env ""))
                     :cwd (str work) :settings {"disableAllHooks" True}))

(defk fake-stack [limit]
  {:pre [(: limit (| int None))] :post [(: % list)] :tags {:context "claude-code" :role "program"}}
  "fake の世界(生かす本数の上限 limit — None は上限を宣言しない)の handler の並びを作るため。外側にホストの役(host-hears)と、
   通り道で log の行・知らせを覚える listen-handler を置く。"
  [(sim-time-handler :clock (SimClock)) (host-hears) listen-handler
   (fake-claude-code-handler (FakeClaudeWorld fake-responder :live-limit limit))])

(defk stub-stack [limit]
  {:pre [(: limit (| int None))] :post [(: % list)] :tags {:context "claude-code" :role "program"}}
  "本番の handler(替え玉の CLI・生かす本数の上限 limit — None は上限を宣言しない)の handler の並びを作るため。外側の並びは
   fake-stack と同じで、加えて log の行の答え手を置く。"
  [(sync-time-handler) slog-discard-handler (host-hears) listen-handler
   (claude-code-handler (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) limit 7200.0 :launch-timeout 30.0))])

(defn notices-in [heard]
  (lfor effect heard :if (isinstance effect ClaudeLiveLimitExceeded) effect))

(defn limit-logs [heard]
  (lfor effect heard :if (and (isinstance effect SlogEffect) (= effect.msg LIVE-LIMIT-LOG))
        #((.get effect.kwargs "session_id") (.get effect.kwargs "live") (.get effect.kwargs "limit") (.get effect.kwargs "warm"))))

(val HEARD #(SlogEffect ClaudeLiveLimitExceeded))


;; --- 手番を走らせていない process が上限を埋めている時 --------------------------------------------------------------

(defk one-turn [#^ ClaudeSessionSpec spec #^ str sid]
  {:pre [(: spec ClaudeSessionSpec) (: sid str)] :post [(: % None)]}
  "新しい会話の手番を 1 つ最後まで走らせるため。"
  (<- started (ClaudeStartTurn (FreshSession sid) spec (TurnInput (reply-prompt "X") (str (uuid.uuid4)))))
  (assert (isinstance started TurnStarted) (repr started))
  (<- done (read-to-end started.turn 30.0))
  (assert (isinstance done.end Completed) (repr done.end))
  None)

(defk four-sessions-over-a-limit-of-two [#^ ClaudeSessionSpec spec #^ tuple ids]
  {:pre [(: spec ClaudeSessionSpec) (: ids tuple)] :post [(: % tuple)]}
  "上限 2 で、会話 2 つの手番の後に 3 つ目を事前起動し、4 つ目の手番を走らせるため。答え = 4 つの会話の process の見え方
   (閉じる前)。終わりに全部の会話を閉じる。"
  (<- (one-turn spec (get ids 0)))
  (<- (one-turn spec (get ids 1)))
  (<- warmed (ClaudeWarmSession (FreshSession (get ids 2)) spec))
  (assert (isinstance warmed SessionWarmed) (repr warmed))
  (<- (one-turn spec (get ids 3)))
  (<- a (ClaudeLiveProcess (get ids 0)))
  (<- b (ClaudeLiveProcess (get ids 1)))
  (<- c (ClaudeLiveProcess (get ids 2)))
  (<- d (ClaudeLiveProcess (get ids 3)))
  (for [sid ids]
    (<- (ClaudeCloseSession sid "test")))
  #(a b c d))

(defn assert-idle-over-the-limit [views heard #^ tuple ids]
  (assert (= views (tuple (gfor _ ids (LiveProcess :launches 1)))) (repr views))
  (assert (= (notices-in heard) [(ClaudeLiveLimitExceeded :session-id (get ids 2) :live 3 :limit 2 :warm True)
                   (ClaudeLiveLimitExceeded :session-id (get ids 3) :live 4 :limit 2 :warm False)])
          (repr (notices-in heard))))


(deftest test-the-fake-over-its-live-limit-stops-no-process-and-tells-the-host [tmp-path]
  (val ids (tuple (gfor _ (range 4) (str (uuid.uuid4)))))
  (<- stack (fake-stack 2))
  (<- heard (with_handlers stack (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
  (assert-idle-over-the-limit (get heard 0) (get heard 1) ids))


(deftest test-the-handler-over-its-live-limit-stops-no-process-and-tells-the-host-and-the-log [tmp-path]
  (val ids (tuple (gfor _ (range 4) (str (uuid.uuid4)))))
  (<- stack (stub-stack 2))
  (<- heard (with_handlers stack (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
  (assert-idle-over-the-limit (get heard 0) (get heard 1) ids)
  (assert (= (limit-logs (get heard 1)) [#((get ids 2) 3 2 True) #((get ids 3) 4 2 False)]) (repr (limit-logs (get heard 1)))))


;; --- 全部の process が手番を走らせている時 ------------------------------------------------------------------------

(defk second-turn-beside-a-running-turn [#^ ClaudeSessionSpec spec #^ str first-id #^ str second-id]
  {:pre [(: spec ClaudeSessionSpec) (: first-id str) (: second-id str)] :post [(: % tuple)]}
  "上限 1 で 1 つ目の会話の道具の手番の途中に 2 つ目の会話の手番を頼むため。答え = 2 つ目を始めた直後の 1 つ目の会話の状態・
   2 つ目の手番の終わり・1 つ目の手番の終わり・1 つ目の process の見え方。終わりに両方の会話を閉じる。"
  (<- first (ClaudeStartTurn (FreshSession first-id) spec (TurnInput (sleep-prompt 3 "SLOW") "slow-1")))
  (<- (read-to-tool-start first.turn 30.0))
  (<- second (ClaudeStartTurn (FreshSession second-id) spec (TurnInput (reply-prompt "NEXT") "next-1")))
  (assert (isinstance second TurnStarted) (repr second))
  (<- status (ClaudeSessionStatus spec.home spec.cwd first-id))
  (<- second-end (read-to-end second.turn 30.0))
  (<- first-end (read-to-end first.turn 30.0))
  (<- first-view (ClaudeLiveProcess first-id))
  (<- (ClaudeCloseSession first-id "test"))
  (<- (ClaudeCloseSession second-id "test"))
  #(first.turn status.state second-end.end first-end.end first-view))

(defn assert-beside-a-running-turn [seen heard #^ str second-id]
  (setv #(first-turn state second-end first-end first-view) seen)
  ;; 2 つ目は 1 つ目の手番の終わりを待たずに始まり、1 つ目の process は止められずに手番を終えて生きている。
  (assert (= state (TurnRunning first-turn)) (repr state))
  (assert (isinstance second-end Completed) (repr second-end))
  (assert (isinstance first-end Completed) (repr first-end))
  (assert (= first-view (LiveProcess :launches 1)) (repr first-view))
  (assert (= (notices-in heard) [(ClaudeLiveLimitExceeded :session-id second-id :live 2 :limit 1 :warm False)])
          (repr (notices-in heard))))


(deftest test-the-fake-over-its-live-limit-starts-beside-a-running-turn-and-tells-the-host [tmp-path]
  (val first-id (str (uuid.uuid4)))
  (val second-id (str (uuid.uuid4)))
  (<- stack (fake-stack 1))
  (<- heard (with_handlers stack
              (Listen (second-turn-beside-a-running-turn (spec-in tmp-path) first-id second-id) :types HEARD)))
  (assert-beside-a-running-turn (get heard 0) (get heard 1) second-id))


(deftest test-the-handler-over-its-live-limit-starts-beside-a-running-turn-and-tells-the-host-and-the-log [tmp-path]
  (val first-id (str (uuid.uuid4)))
  (val second-id (str (uuid.uuid4)))
  (<- stack (stub-stack 1))
  (<- heard (with_handlers stack
              (Listen (second-turn-beside-a-running-turn (spec-in tmp-path) first-id second-id) :types HEARD)))
  (assert-beside-a-running-turn (get heard 0) (get heard 1) second-id)
  (assert (= (limit-logs (get heard 1)) [#(second-id 2 1 False)]) (repr (limit-logs (get heard 1)))))


;; --- 上限を宣言しないホスト(live-limit が None — #4282)--------------------------------------------------------------
;; 使い手が同時に生かす本数を別の物差し(機体の memory の余白)で決める時は、この層に本数を渡さない(None)。比べる数が無いので、
;; process を何本起こしても log の行も知らせ ClaudeLiveLimitExceeded も出ない。整数を渡すホストの検査(1 以上・bool を断る)と、
;; 上の 2 つの筋書き(整数の上限を越えたら知らせる)は変わらない。
;; 失敗ケース = 変更前の ClaudeCodeHost は None を 1 と比べて TypeError で落ち、上限を宣言しないホストを作れなかった。fake の世界は
;; 前から None を受ける(同じ筋書きを両方に当てて、本番の handler を fake に揃える)。

(defk four-processes-live-and-nothing-told [#^ tuple seen]
  {:pre [(: seen tuple)] :post [(: % None)] :tags {:context "claude-code" :role "program"}}
  "Listen の答え(4 つの会話の process の見え方 と 通り道で覚えた log の行・知らせ)から、4 つの process がどれも止まらずに生きていて、
   上限越えの知らせも log の行も 1 つも出ていない事を確かめるため。"
  (assert (= (get seen 0) (tuple (gfor _ (range 4) (LiveProcess :launches 1)))) (repr (get seen 0)))
  (assert (= (notices-in (get seen 1)) []) (repr (notices-in (get seen 1))))
  (assert (= (limit-logs (get seen 1)) []) (repr (limit-logs (get seen 1))))
  None)


;; 上限を宣言しないホストを作れる(欄 live-limit は None のまま持つ — 0 や大きい数に言い換えない)。
(deftest test-the-host-is-built-without-a-live-limit
  (val host (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) None 7200.0))
  (assert (is host.live-limit None) (repr host.live-limit)))


;; 整数を渡すホストの検査は、None を受けるようになっても同じ(0・負の数・bool は作る時に断る)。
(deftest test-the-host-refuses-a-live-limit-below-one-and-a-bool
  (for [bad #(0 -1 True False)]
    (with [(pytest.raises ValueError :match "live_limit")]
      (ClaudeCodeHost STUB-COMMAND (clock-of (sync-time-handler)) bad 7200.0))))


;; fake: 上限を宣言しない世界は、上限 2 の筋書きと同じ 4 つの会話を起こしても何も知らせない。
(deftest test-the-fake-without-a-live-limit-keeps-four-processes-and-tells-nothing [tmp-path]
  (val ids (tuple (gfor _ (range 4) (str (uuid.uuid4)))))
  (<- stack (fake-stack None))
  (<- seen (with_handlers stack (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
  (<- (four-processes-live-and-nothing-told seen)))


;; 本番の handler: 上限を宣言しないホストは、同じ 4 つの会話の process を起こしても log の行も知らせも出さない。
(deftest test-the-handler-without-a-live-limit-keeps-four-processes-and-tells-nothing [tmp-path]
  (val ids (tuple (gfor _ (range 4) (str (uuid.uuid4)))))
  (<- stack (stub-stack None))
  (<- seen (with_handlers stack (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
  (<- (four-processes-live-and-nothing-told seen)))
