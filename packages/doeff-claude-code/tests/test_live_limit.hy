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

(defn fake-stack [#^ int limit]
  [(sim-time-handler :clock (SimClock)) (host-hears) listen-handler
   (fake-claude-code-handler (FakeClaudeWorld fake-responder :live-limit limit))])

(defn stub-stack [#^ int limit]
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
  (<- heard (with_handlers (fake-stack 2) (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
  (assert-idle-over-the-limit (get heard 0) (get heard 1) ids))


(deftest test-the-handler-over-its-live-limit-stops-no-process-and-tells-the-host-and-the-log [tmp-path]
  (val ids (tuple (gfor _ (range 4) (str (uuid.uuid4)))))
  (<- heard (with_handlers (stub-stack 2) (Listen (four-sessions-over-a-limit-of-two (spec-in tmp-path) ids) :types HEARD)))
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
  (<- heard (with_handlers (fake-stack 1)
              (Listen (second-turn-beside-a-running-turn (spec-in tmp-path) first-id second-id) :types HEARD)))
  (assert-beside-a-running-turn (get heard 0) (get heard 1) second-id))


(deftest test-the-handler-over-its-live-limit-starts-beside-a-running-turn-and-tells-the-host-and-the-log [tmp-path]
  (val first-id (str (uuid.uuid4)))
  (val second-id (str (uuid.uuid4)))
  (<- heard (with_handlers (stub-stack 1)
              (Listen (second-turn-beside-a-running-turn (spec-in tmp-path) first-id second-id) :types HEARD)))
  (assert-beside-a-running-turn (get heard 0) (get heard 1) second-id)
  (assert (= (limit-logs (get heard 1)) [#(second-id 2 1 False)]) (repr (limit-logs (get heard 1)))))
