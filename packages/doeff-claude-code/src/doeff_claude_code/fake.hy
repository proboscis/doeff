;;; fake の handler — 本番の handler と同じ公開 effect に、memory の上で答える(設計 8 節)。
;;;
;;; 時刻は doeff-time(GetMonotonic で手番の筋書きを進め、GetTime で行の at を刻む)。仮想の時計(sim-time-handler)の下では
;;; 道具の秒数も待ちも一瞬で進む。筋書き = 入力の本文と会話の記憶(それまでの入力)→ FakeReply(返事の本文・道具の秒数・
;;; 許可の問いの要否)。行は本番と同じ ClaudeStreamLine / ClaudeLineKind の型で出す(型を 2 つ作らない)。
;;;
;;; 本番と共通の不変条件(1 つの会話に走る手番は多くとも 1 つ・StartTurn 1 回に終わりちょうど 1 つ・seq の単調増加・
;;; FreshSession の id の重複と ResumeSession の不在の断り・手番の外の足す / 止めるの断り・止めた後は Interrupted・閉じるは冪等・
;;; process の死の注入で BackendLost・次の ResumeSession は通る)を同じ筋書きの検で確かめる。
(require doeff-hy.macros [defhandler defk <-])
(import dataclasses [dataclass])
(import uuid)
(import doeff_time [Delay GetMonotonic GetTime])
(import doeff_claude_code.values [ClaudeTurn FreshSession ResumeSession ForkSession Rebuilt LinkFromHome IMAGE-MIMES])
(import doeff_claude_code.lines [ClaudeStreamLine Init AssistantMessage ToolResult InputFate PermissionRequested
                                 TaskEvent TurnResult Completed Interrupted BackendLost ClaudeLineKind ClaudeTurnEnd])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus
                                   TurnStarted InputQueued InterruptRequested TurnEventPage Answered SessionClosed
                                   SessionStatus Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   SessionNotFound SessionIdInUse TurnInFlight AttachmentRefused NoTurnInFlight
                                   UnknownTurn NoSuchRequest])
(import doeff_claude_code.faults [ClaudeDropProcess])

(setv QUICK-TURN-SECONDS 0.1)
;; 仮想の時計は datetime(マイクロ秒の刻み)なので、秒の小数の足し算の端数で「期限の直前」に留まらないように
;; 期限の比べに刻み 1 つ分の余裕を置き、眠りは刻み以上にする。
(setv CLOCK-TICK 1e-6)
(setv MIN-SLEEP 1e-3)
(setv FAKE-CAPABILITIES #("msg_lifecycle_v1" "interrupt_receipt_v1"))


(defclass [(dataclass :frozen True)] FakeReply []
  "筋書きの 1 手番の返事: text = 最後の本文・tool-seconds = 道具が走る秒数(0 = 道具なし)・
   needs-permission = 道具の前に許可の問いを出す。"
  (#^ str text)
  (setv #^ float tool-seconds 0.0)
  (setv #^ bool needs-permission False))


(defclass FakeTurn []
  (defn __init__ [self #^ int seq #^ float started-at reply refs]
    (setv self.seq seq
          self.started-at started-at
          self.reply reply
          self.refs (list refs)
          self.phase "quick"
          self.due-at (+ started-at QUICK-TURN-SECONDS)
          self.injections []
          self.permission None
          self.lines []
          self.end None)))


(defclass FakeSession []
  (defn __init__ [self #^ str session-id home #^ str cwd]
    (setv self.session-id session-id self.home home self.cwd cwd
          self.turns {} self.current-seq 0 self.next-line-seq 0 self.closed False))

  (defn running [self]
    (setv turn (.get self.turns self.current-seq))
    (if (and (is-not turn None) (is turn.end None)) turn None)))


(defclass FakeClaudeWorld []
  "fake の世界: 家ごとの transcript(入力の列)と会話の状態。responder = (本文 記憶) → FakeReply。"
  (defn __init__ [self responder]
    (setv self.responder responder
          self.transcripts {}
          self.activity {}
          self.sessions {}))

  (defn transcript-key [self home #^ str cwd #^ str session-id]
    #(home.config-dir cwd session-id)))


;; --- 行を出す ------------------------------------------------------------------------------------

(defk emit [#^ FakeSession session #^ FakeTurn turn kind]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kind ClaudeLineKind)] :post [(: % (type None))]}
  (<- at (GetTime))
  (.append turn.lines (ClaudeStreamLine :seq session.next-line-seq :at at :kind kind :raw (repr kind)))
  (+= session.next-line-seq 1)
  None)

(defk emit-all [#^ FakeSession session #^ FakeTurn turn #^ list kinds]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kinds list)] :post [(: % (type None))]}
  (for [kind kinds] (<- (emit session turn kind)))
  None)

(defk finish [#^ FakeSession session #^ FakeTurn turn end]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: end ClaudeTurnEnd)] :post [(: % (type None))]}
  (setv turn.end end turn.phase "done")
  None)

(defk complete-turn [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "道具の境界(か手番の終わり)で、読まれていない注入を読み、本文の返事で手番を終える。"
  (setv memory (tuple (.get world.transcripts (.transcript-key world session.home session.cwd session.session-id) [])))
  (setv extra [])
  (for [injection turn.injections]
    (when (= (get injection 2) "queued")
      (setv (get injection 2) "started")
      (<- (emit session turn (InputFate (get injection 0) "started")))
      (.append extra (. (world.responder (get injection 1) memory) text))))
  (setv text (.join " " (+ [turn.reply.text] extra)))
  (<- (emit-all session turn [(AssistantMessage :text text) (TurnResult "success" False :terminal-reason "completed")]))
  (for [injection turn.injections]
    (<- (emit session turn (InputFate (get injection 0) "completed"))))
  (<- (finish session turn (Completed :result-text text :input-refs (tuple turn.refs))))
  None)

(defk advance [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "今の時刻まで筋書きを進める。"
  (<- now (GetMonotonic))
  (when (and (in turn.phase #("quick" "tool")) (>= (+ now CLOCK-TICK) turn.due-at))
    (when (= turn.phase "tool")
      (<- (emit-all session turn [(ToolResult :tool-use-ids #("fake-tool")) (TaskEvent "fake-task" "completed")])))
    (<- (complete-turn world session turn)))
  None)

(defk begin-fake-turn [#^ FakeClaudeWorld world #^ FakeSession session reply #^ tuple refs #^ bool announce]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: reply FakeReply) (: refs tuple) (: announce bool)] :post [(: % FakeTurn)]}
  "手番を開いて最初の行を出す。announce = 入力の行の運命と init を出す(生き残った入力の手番は started から)。"
  (<- now (GetMonotonic))
  (+= session.current-seq 1)
  (setv turn (FakeTurn session.current-seq now reply refs))
  (setv (get session.turns turn.seq) turn)
  (for [ref refs]
    (when announce (<- (emit session turn (InputFate ref "queued"))))
    (<- (emit session turn (InputFate ref "started"))))
  (<- (emit session turn (Init :session-id session.session-id :capabilities FAKE-CAPABILITIES :model "fake")))
  (cond
    reply.needs-permission
      (do
        (setv turn.phase "permission" turn.permission (str (uuid.uuid4)))
        (<- (emit-all session turn [(AssistantMessage :tool-names #("Bash"))
                                    (PermissionRequested turn.permission "Bash" {"command" "fake"})])))
    (> reply.tool-seconds 0)
      (do
        (setv turn.phase "tool" turn.due-at (+ now reply.tool-seconds))
        (<- (emit-all session turn [(AssistantMessage :tool-names #("Bash")) (TaskEvent "fake-task" "started")]))))
  turn)


;; --- 節の中身 -----------------------------------------------------------------------------------

(defn transcript-of [#^ FakeClaudeWorld world home #^ str cwd #^ str session-id]
  (.get world.transcripts (.transcript-key world home cwd session-id)))

(defn carry-into [#^ FakeClaudeWorld world home #^ str cwd #^ str session-id carry]
  "持ち込み: 在れば上書きしない。Rebuilt は本文の行を入力として読む・LinkFromHome は元の家の transcript を写す。"
  (setv key (.transcript-key world home cwd session-id))
  (when (or (is carry None) (in key world.transcripts)) (return None))
  (cond
    (isinstance carry Rebuilt)
      (setv (get world.transcripts key) (lfor line (.splitlines carry.jsonl-text) :if (.strip line) line))
    (isinstance carry LinkFromHome)
      (do
        (setv source (transcript-of world carry.source-home cwd session-id))
        (when (is-not source None) (setv (get world.transcripts key) source)))))

(defk fake-start-turn [#^ FakeClaudeWorld world #^ ClaudeStartTurn request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeStartTurn)] :post [(: % "StartTurnOutcome")]}
  (setv origin request.origin spec request.spec input request.input)
  (setv refused (lfor item input.attachments :if (not-in item.mime IMAGE-MIMES) item.mime))
  (when refused (return (AttachmentRefused (get refused 0))))
  (setv target (if (isinstance origin ForkSession) origin.parent-session-id origin.session-id))
  (when (not (isinstance origin FreshSession)) (carry-into world spec.home spec.cwd target origin.carry))
  (setv existing (if (isinstance origin ForkSession) None (.get world.sessions target)))
  (setv present (is-not (transcript-of world spec.home spec.cwd target) None))
  (cond
    (and (isinstance origin FreshSession) (or existing present)) (return (SessionIdInUse target))
    (and (isinstance origin ResumeSession) existing (.running existing)) (return (TurnInFlight (ClaudeTurn target existing.current-seq)))
    (and (not (isinstance origin FreshSession)) (not present)) (return (SessionNotFound target)))
  (setv session-id (if (isinstance origin ForkSession) (str (uuid.uuid4)) target))
  (setv key (.transcript-key world spec.home spec.cwd session-id))
  (when (isinstance origin ForkSession)
    (setv (get world.transcripts key) (list (transcript-of world spec.home spec.cwd target))))
  (when (isinstance origin FreshSession) (setv (get world.transcripts key) []))
  (setv session (or existing (FakeSession session-id spec.home spec.cwd)))
  (setv session.closed False)
  (setv (get world.sessions session-id) session)
  (setv memory (tuple (get world.transcripts key)))
  (.append (get world.transcripts key) input.text)
  (<- now-time (GetTime))
  (setv (get world.activity key) (.timestamp now-time))
  (<- turn (begin-fake-turn world session (world.responder input.text memory) #(input.ref) True))
  (TurnStarted (ClaudeTurn session-id turn.seq) session-id))

(defn running-turn-of [#^ FakeClaudeWorld world #^ ClaudeTurn turn]
  (setv session (.get world.sessions turn.session-id))
  (setv running (if (is session None) None (.running session)))
  (if (and (is-not running None) (= running.seq turn.turn-seq)) running None))

(defk fake-inject [#^ FakeClaudeWorld world #^ ClaudeInjectInput request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInjectInput)] :post [(: % (| InputQueued NoTurnInFlight))]}
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (.append turn.injections [request.input.ref request.input.text "queued"])
  (.append turn.refs request.input.ref)
  (<- (emit (get world.sessions request.turn.session-id) turn (InputFate request.input.ref "queued")))
  (InputQueued request.input.ref))

(defk fake-interrupt [#^ FakeClaudeWorld world #^ ClaudeInterruptTurn request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInterruptTurn)] :post [(: % (| InterruptRequested NoTurnInFlight))]}
  "止める: 読まれていない注入が在れば、それを生き残った入力として次の手番で走らせる(control_request の形)。
   無ければ SIGINT の形(result は error_during_execution・aborted_streaming)で Interrupted。"
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (setv session (get world.sessions request.turn.session-id))
  (setv survivors (lfor injection turn.injections :if (= (get injection 2) "queued") injection))
  (if survivors
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_tools")))
        (setv memory (tuple (get world.transcripts (.transcript-key world session.home session.cwd session.session-id))))
        (setv reply (world.responder (.join "\n" (lfor injection survivors (get injection 1))) memory))
        (<- next-turn (begin-fake-turn world session (FakeReply reply.text) (tuple (lfor injection survivors (get injection 0))) False))
        (<- (finish session turn (Interrupted :surviving-refs (tuple next-turn.refs)
                                              :continued-by (ClaudeTurn session.session-id next-turn.seq)))))
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_streaming")))
        (<- (finish session turn (Interrupted)))))
  (InterruptRequested))

(defk fake-read-events [#^ FakeClaudeWorld world #^ ClaudeReadTurnEvents request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (setv session (.get world.sessions request.turn.session-id))
  (setv turn (if (is session None) None (.get session.turns request.turn.turn-seq)))
  (when (is turn None) (return (UnknownTurn request.turn)))
  (<- started (GetMonotonic))
  (setv deadline (+ started (float request.wait-up-to)))
  (while True
    (<- (advance world session turn))
    (setv lines (tuple (gfor line turn.lines :if (> line.seq request.after-seq) line)))
    (<- now (GetMonotonic))
    (when (or lines (is-not turn.end None) (>= now deadline))
      (return (TurnEventPage lines (if lines (. (get lines -1) seq) request.after-seq) turn.end)))
    (setv wake (if (in turn.phase #("quick" "tool")) (min turn.due-at deadline) deadline))
    (<- (Delay (max MIN-SLEEP (- wake now))))))

(defk fake-answer [#^ FakeClaudeWorld world #^ ClaudeAnswerPermission request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeAnswerPermission)] :post [(: % (| Answered NoSuchRequest))]}
  (setv turn (running-turn-of world request.turn))
  (when (or (is turn None) (!= turn.permission request.request-id) (!= turn.phase "permission"))
    (return (NoSuchRequest request.request-id)))
  (<- now (GetMonotonic))
  (setv turn.phase "tool" turn.due-at (+ now (max QUICK-TURN-SECONDS turn.reply.tool-seconds)) turn.permission None)
  (Answered))

(defk fake-close [#^ FakeClaudeWorld world #^ ClaudeCloseSession request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeCloseSession)] :post [(: % SessionClosed)]}
  (setv session (.get world.sessions request.session-id))
  (when (is session None) (return (SessionClosed False)))
  (setv running (.running session))
  (when (is-not running None)
    (<- (finish session running (Interrupted :dropped-refs (tuple (gfor injection running.injections
                                                                       :if (= (get injection 2) "queued")
                                                                       (get injection 0)))))))
  (setv session.closed True)
  (SessionClosed (is-not running None)))

(defn fake-status [#^ FakeClaudeWorld world #^ ClaudeSessionStatus request]
  (setv key (.transcript-key world request.home request.cwd request.session-id))
  (setv transcript (if (in key world.transcripts)
                       (TranscriptPresent (.get world.activity key 0.0))
                       (TranscriptAbsent)))
  (setv session (.get world.sessions request.session-id))
  (setv running (if (is session None) None (.running session)))
  (SessionStatus (cond
                   (is-not running None) (TurnRunning (ClaudeTurn request.session-id running.seq))
                   (and (is-not session None) session.closed) (Closed)
                   True (Idle))
                 transcript))

(defk fake-drop [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)]}
  (setv session (.get world.sessions session-id))
  (setv running (if (is session None) None (.running session)))
  (when (is running None) (return False))
  (<- (finish session running (BackendLost "process killed (fake)")))
  True)


;; --- handler -----------------------------------------------------------------------------------

(defhandler fake-claude-code-handler [world]
  (ClaudeStartTurn [origin spec input]
    (<- outcome (fake-start-turn world effect))
    (resume outcome))
  (ClaudeInjectInput [turn input]
    (<- outcome (fake-inject world effect))
    (resume outcome))
  (ClaudeInterruptTurn [turn]
    (<- outcome (fake-interrupt world effect))
    (resume outcome))
  (ClaudeReadTurnEvents [turn after-seq wait-up-to]
    (<- page (fake-read-events world effect))
    (resume page))
  (ClaudeAnswerPermission [turn request-id answer]
    (<- outcome (fake-answer world effect))
    (resume outcome))
  (ClaudeCloseSession [session-id reason]
    (<- closed (fake-close world effect))
    (resume closed))
  (ClaudeSessionStatus [home cwd session-id]
    (resume (fake-status world effect)))
  (ClaudeDropProcess [session-id]
    (<- dropped (fake-drop world session-id))
    (resume dropped)))
