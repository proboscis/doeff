;; 公開 effect の筋書き — fake と、本番の handler + 替え玉の app-server(録った実物の行を返す)の 2 つで同じ Program を走らせる。
;; 筋書きは handler を知らない: ScenarioSettings で宣言を読み、公開 effect だけを撃つ。
;;
;; 守る事: 答えの文字の途中(TextDelta)がターンの出来事の頁に順に載り、ターンの終わりはちょうど 1 つ届く。止めたターンは INTERRUPTED で
;; 終わり、同じ process が次のターンを受ける。これが崩れると、上の層(doeff-agents の adapter)は答えの途中を画面へ流せない
;; (決まりの元 = 利用者 2026-09-10「どの会話も、文字が届くたびに 1 文字ずつ更新されない」・2026-10-10 21:27 の答え A)。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "codex-test" :role "program"})
(import doeff_codex.values [FreshThread ResumeThread])
(import doeff_codex.lines [TextDelta AgentMessageDone TurnEnded TurnStatus])
(import doeff_codex.effects [CodexStartTurn CodexInterruptTurn CodexCloseSession CodexLaunchCount TurnStarted InterruptRequested
                             SessionClosed TurnInFlight BackendLost])
(import tests.scenario_steps [settings read-to-end read-to-first-delta records-of])


(deftest test-the-text-deltas-reach-the-page-in-order-and-the-turn-ends-once
  {:interpreters ["fake" "stub"]}
  (<- s (settings))
  (<- started (CodexStartTurn (FreshThread) s.spec "say hello"))
  (assert (isinstance started TurnStarted) started)
  (<- so-far (read-to-end started.turn s.turn-timeout))
  ;; 途中の文字は順に頁に載り、連ねると答えの全文。
  (<- deltas (records-of so-far TextDelta))
  (assert (= (tuple (gfor delta deltas delta.text)) #("Hel" "lo, " "wor" "ld.")) deltas)
  (<- answers (records-of so-far AgentMessageDone))
  (assert (= (tuple (gfor answer answers answer.text)) #("Hello, world.")) answers)
  ;; 終わりはちょうど 1 つで、頁の end と同じ。出来事の seq は単調に増え、途中の文字は終わりより前。
  (<- ends (records-of so-far TurnEnded))
  (assert (= (len ends) 1) ends)
  (assert (= so-far.end (get ends 0)) so-far.end)
  (assert (= so-far.end.status TurnStatus.COMPLETED) so-far.end)
  (val seqs (tuple (gfor event so-far.events event.seq)))
  (assert (= seqs (tuple (sorted (set seqs)))) seqs)
  (val end-seq (next (gfor event so-far.events :if (isinstance event.record TurnEnded) event.seq)))
  (assert (all (gfor event so-far.events :if (isinstance event.record TextDelta) (< event.seq end-seq))) so-far.events))


(deftest test-an-interrupted-turn-ends-interrupted-and-the-process-takes-the-next-turn
  {:interpreters ["fake" "stub"]}
  (<- s (settings))
  (<- started (CodexStartTurn (FreshThread) s.spec "SLOW please"))
  (assert (isinstance started TurnStarted) started)
  ;; 途中の文字が届いてから止める。
  (<- before (read-to-first-delta started.turn s.turn-timeout))
  (<- early (records-of before TextDelta))
  (assert early before.events)
  (assert (is before.end None) before.end)
  (<- asked (CodexInterruptTurn started.turn))
  (assert (isinstance asked InterruptRequested) asked)
  (<- stopped (read-to-end started.turn s.turn-timeout))
  (assert (and (isinstance stopped.end TurnEnded) (= stopped.end.status TurnStatus.INTERRUPTED)) stopped.end)
  ;; 同じ会話の次のターンは、同じ process が受けて終わりまで走る(起こした process は 1 つのまま)。
  (<- next-turn (CodexStartTurn (ResumeThread :thread-id started.turn.thread-id) s.spec "say hello again"))
  (assert (isinstance next-turn TurnStarted) next-turn)
  (assert (= next-turn.turn.thread-id started.turn.thread-id) next-turn)
  (assert (!= next-turn.turn.turn-id started.turn.turn-id) next-turn)
  (<- finished (read-to-end next-turn.turn s.turn-timeout))
  (assert (and (isinstance finished.end TurnEnded) (= finished.end.status TurnStatus.COMPLETED)) finished.end)
  (<- launches (CodexLaunchCount started.turn.thread-id))
  (assert (= launches 1) launches))


(deftest test-a-running-turn-refuses-a-second-start-and-closing-ends-it
  {:interpreters ["fake" "stub"]}
  (<- s (settings))
  (<- started (CodexStartTurn (FreshThread) s.spec "SLOW please"))
  (assert (isinstance started TurnStarted) started)
  ;; 走っているターンが在る会話の 2 つ目の始めは断る(1 つの会話に走っているターンは多くとも 1 つ)。
  (<- again (CodexStartTurn (ResumeThread :thread-id started.turn.thread-id) s.spec "say hello"))
  (assert (= again (TurnInFlight :turn started.turn)) again)
  ;; 閉じると、走っていたターンは BackendLost で終わり、続きは新しい process で thread を続ける。
  (<- closed (CodexCloseSession started.turn.thread-id "筋書きの終わり"))
  (assert (= closed (SessionClosed :was-running True)) closed)
  (<- resumed (CodexStartTurn (ResumeThread :thread-id started.turn.thread-id) s.spec "say hello"))
  (assert (isinstance resumed TurnStarted) resumed)
  (<- finished (read-to-end resumed.turn s.turn-timeout))
  (assert (and (isinstance finished.end TurnEnded) (= finished.end.status TurnStatus.COMPLETED)) finished.end)
  (<- launches (CodexLaunchCount started.turn.thread-id))
  (assert (= launches 2) launches))
