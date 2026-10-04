;;; fake の handler — 本番の handler と同じ公開 effect に、memory の上で答える(設計 8 節)。
;;;
;;; 時刻は doeff-time(GetMonotonic で手番の筋書きを進め、GetTime で行の at を刻む)。仮想の時計(sim-time-handler)の下では
;;; 道具の秒数も待ちも一瞬で進む。筋書き = 入力の本文と会話の記憶(それまでの入力)→ FakeReply(返事の本文・道具の秒数・
;;; 許可の問いの要否・終わり方〔完了・失敗・process が消える〕・usage・途中の本文の行の数)。行は本番と同じ ClaudeStreamLine /
;;; ClaudeLineKind の型で出す(型を 2 つ作らない)。
;;;
;;; 世界 = 家の中身(transcripts・activity — disk の上の物)と process の中の会話(sessions)。restarted は同じ家の上で process だけを
;;; 作り直した世界(前の process の会話は前の世界で走り続け、新しい世界からは見えない — 上の層の process の作り直しの模擬)。
;;;
;;; 本番と共通の不変条件(1 つの会話に走る手番は多くとも 1 つ・StartTurn 1 回に終わりちょうど 1 つ・seq の単調増加・
;;; FreshSession の id の重複と ResumeSession の不在の断り・手番の外の足す / 止めるの断り・止めた後は Interrupted・閉じるは冪等・
;;; process の死の注入で BackendLost・次の ResumeSession は通る)を同じ筋書きの検で確かめる。
(require doeff-hy.macros [defhandler defk <- val])
(import dataclasses [dataclass field replace])
(import uuid)
(import doeff_time [GetMonotonic GetTime WaitWithin])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise])
(import doeff_hy.frozen [FrozenMap])
(import doeff_claude_code.values [ClaudeTurn FreshSession ResumeSession ForkSession Rebuilt LinkFromHome IMAGE-MIMES])
(import doeff_claude_code.lines [ClaudeStreamLine Init AssistantMessage ToolResult InputFate PermissionRequested
                                 TaskEvent TurnResult Completed Failed Interrupted BackendLost ClaudeLineKind ClaudeTurnEnd Usage])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested TurnEventPage Answered SessionClosed
                                   SessionStatus SessionExported Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   SessionNotFound SessionIdInUse TurnInFlight AttachmentRefused NoTurnInFlight
                                   UnknownTurn NoSuchRequest])
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeForgetSession])

(setv QUICK-TURN-SECONDS 0.1)
;; 仮想の時計は datetime(マイクロ秒の刻み)なので、秒の小数の足し算の端数で「期限の直前」に留まらないように
;; 期限の比べに刻み 1 つ分の余裕を置き、眠りは刻み以上にする。
(setv CLOCK-TICK 1e-6)
(setv MIN-SLEEP 1e-3)
(setv FAKE-CAPABILITIES #("msg_lifecycle_v1" "interrupt_receipt_v1"))
;; 止めるの受理(interrupt_receipt_v1)を名乗らない CLI の process の能力(FakeReply の interrupt-receipt が偽の手番)。
(val NO-RECEIPT-CAPABILITIES #("msg_lifecycle_v1"))


(defclass [(dataclass :frozen True)] FakeReply []
  "筋書きの 1 手番の返事: text = 最後の本文・tool-seconds = 道具が走る秒数(0 = 道具なし)・
   needs-permission = 道具の前に許可の問いを出す・fail = 期限で Failed(detail = この文)で終わる・lose = 期限で process が消えて
   BackendLost(detail = この文)で終わる(fail と lose は多くとも 1 つ)・usage = Completed / Failed に載せる usage・
   cost-usd = Completed / Failed に載せる手番の額(USD — 本番の handler が累積の額の差から数える値の代わり。None = 名乗らない)・
   lines = 始めてから期限までの前半に、本文の行(AssistantMessage)を lines 行ほど等間隔に出す(出来事の量の多い手番)・
   think-seconds = 道具を使わずに考える秒(道具の行を出さずに長く走る手番)・
   interrupt-receipt = この手番の CLI の process が init で interrupt_receipt_v1 を名乗るか(本物の handler は手番ごとに process を
   起こす)。偽 = 止めるは SIGINT の形で、読まれていない注入を捨てた入力(dropped-refs)として終える — 本物の対話の解釈
   (dialogue.hy の interrupt と on-result)が受理を名乗らない CLI を止める道(#3467)。"
  (#^ str text)
  (setv #^ float tool-seconds 0.0)
  (setv #^ bool needs-permission False)
  (setv #^ (| str None) fail None)
  (setv #^ (| str None) lose None)
  (setv #^ Usage usage (field :default-factory Usage))
  (setv #^ (| float None) cost-usd None)
  (setv #^ int lines 0)
  ;; 道具なしで考える秒(0 = 既定の短い手番)。本文の行だけで、道具の行を出さずにこの秒まで走る。
  (setv #^ float think-seconds 0.0)
  ;; この手番の CLI が止めるの受理を名乗るか(偽 = 止めるは SIGINT の形で、読まれていない注入を捨てる)。
  (setv #^ bool interrupt-receipt True)
  (defn #^ None __post-init__ [self]
    (when (and (is-not self.fail None) (is-not self.lose None))
      (raise (ValueError "FakeReply の fail と lose は多くとも 1 つ")))
    (when (< self.lines 0)
      (raise (ValueError (+ "FakeReply の lines は 0 以上: " (str self.lines)))))))


(defclass [(dataclass :frozen True)] FakeInjection []
  "手番に足した入力 1 つ: ref = 入力の行の名 / text = 本文 / fate = 運命(queued → started → completed)。
   運命が進む時は replace で作り直して差し替える。"
  (#^ str ref)
  (#^ str text)
  (setv #^ str fate "queued"))


(defclass FakeTurn []
  "fake の手番 1 つ: phase = quick / tool / permission / done・permission = 答え待ちの許可の問いの id(無ければ None)・
   end = 手番の終わり(まだなら None)・bells = 出来事の読み(ClaudeReadTurnEvents)の待ち手が掛けた呼び鈴(新しい行か終わりで鳴らして外す)。"
  (defn __init__ [self #^ int seq #^ float started-at #^ FakeReply reply #^ (get tuple #(str ...)) refs]
    (setv #^ int self.seq seq)
    (setv #^ float self.started-at started-at)
    (setv #^ FakeReply self.reply reply)
    (setv #^ (get list str) self.refs (list refs))
    (setv #^ str self.phase "quick")
    (setv #^ float self.due-at (+ started-at (max QUICK-TURN-SECONDS reply.think-seconds)))
    (setv #^ int self.lines-emitted 0)
    (setv #^ (get list FakeInjection) self.injections [])
    (setv #^ (| str None) self.permission None)
    (setv #^ (get list ClaudeStreamLine) self.lines [])
    (setv #^ (| Completed Failed Interrupted BackendLost None) self.end None)
    (setv #^ (get tuple #((get ExternalPromise None) ...)) self.bells #())))


(defclass FakeSession []
  (defn __init__ [self #^ str session-id home #^ str cwd]
    (setv self.session-id session-id self.home home self.cwd cwd
          self.current-seq 0 self.next-line-seq 0 self.closed False)
    (setv #^ (get dict #(int FakeTurn)) self.turns {}))

  (defn running [self]
    (setv turn (.get self.turns self.current-seq))
    (if (and (is-not turn None) (is turn.end None)) turn None)))


(defclass FakeClaudeWorld []
  "fake の世界: 家ごとの transcript(入力の列)と会話の状態。返事の作り方はちょうど 1 つ:
   responder = (本文 記憶) → FakeReply の同期の関数(効果を出さない筋書き)・
   respond = (本文 記憶) → FakeReply の Program の kleisli(defk — 返事を作る時に効果を出してよい。効果は fake の handler の外側が
   答える。上の層の相手役が、いま始めている手番を自分の handler の状態から効果で読むため)。"
  (defn __init__ [self [responder None] * [respond None]]
    (when (= (is responder None) (is respond None))
      (raise (ValueError "FakeClaudeWorld は responder(同期)と respond(kleisli)のちょうど 1 つを受ける")))
    (setv self.responder responder
          self.respond respond
          self.transcripts {}
          self.activity {}
          self.sessions {}))

  (defn restarted [self]
    "同じ家の上で process を作り直した世界: transcript と activity(家の中身)は同じ物を共有し、会話(process の中の状態)は空。
     前の世界の走っている手番は前の世界で走り続ける(子 process は上の層の process の作り直しで止まらない)。"
    (setv world (FakeClaudeWorld self.responder :respond self.respond))
    (setv world.transcripts self.transcripts world.activity self.activity)
    world)

  (defn transcript-key [self home #^ str cwd #^ str session-id]
    #(home.config-dir cwd session-id)))


(defk reply-of [#^ FakeClaudeWorld world #^ str text #^ tuple memory]
  {:pre [(: world FakeClaudeWorld) (: text str) (: memory tuple)] :post [(: % FakeReply)]}
  "筋書きの返事を 1 つ作る(返事を作る所はここ 1 つ): respond が在れば、その Program を走らせた答え・無ければ同期の responder の答え。"
  (if (is world.respond None)
      (world.responder text memory)
      (do (<- reply FakeReply (world.respond text memory))
          reply)))


;; --- 行を出す ------------------------------------------------------------------------------------

;; 読みの待ち手の呼び鈴: 本番の CLI の行の流れは、行が出た時と process が終わった時(消えた・止めた・閉じた)にその場で読み手へ届く。
;; fake の読みも、期限まで眠らずに、行を出した時と手番を終えた時に鳴らす呼び鈴で起きる(待ちの外から手番を終える故障の注入・止める・
;; 閉じるでも、読み手は次の筋書きの刻まで眠り続けない — #3130)。

(defk ring-turn [#^ FakeTurn turn]
  {:pre [(: turn FakeTurn)] :post [(: % (type None))]}
  "手番 turn に掛かった読みの呼び鈴を全部鳴らして外す(新しい行か終わりが出た)。"
  (setv bells turn.bells)
  (setv turn.bells #())
  (for [bell bells]
    (.complete bell None))
  None)

(defk emit [#^ FakeSession session #^ FakeTurn turn kind]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kind ClaudeLineKind)] :post [(: % (type None))]}
  (<- at (GetTime))
  (.append turn.lines (ClaudeStreamLine :seq session.next-line-seq :at at :kind kind :raw (repr kind)))
  (+= session.next-line-seq 1)
  (<- (ring-turn turn))
  None)

(defk emit-all [#^ FakeSession session #^ FakeTurn turn #^ list kinds]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kinds list)] :post [(: % (type None))]}
  (for [kind kinds] (<- (emit session turn kind)))
  None)

(defk finish [#^ FakeSession session #^ FakeTurn turn end]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: end ClaudeTurnEnd)] :post [(: % (type None))]}
  (setv turn.end end turn.phase "done")
  (<- (ring-turn turn))
  None)

(defk complete-turn [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "道具の境界(か手番の終わり)で、読まれていない注入を読み、本文の返事で手番を終える。"
  (setv memory (tuple (.get world.transcripts (.transcript-key world session.home session.cwd session.session-id) [])))
  (setv extra [])
  (for [#(index injection) (enumerate turn.injections)]
    (when (= injection.fate "queued")
      (setv (get turn.injections index) (replace injection :fate "started"))
      (<- (emit session turn (InputFate injection.ref "started")))
      (<- extra-reply FakeReply (reply-of world injection.text memory))
      (.append extra extra-reply.text)))
  (setv text (.join " " (+ [turn.reply.text] extra)))
  (<- (emit-all session turn [(AssistantMessage :text text)
                              (TurnResult "success" False :terminal-reason "completed" :usage turn.reply.usage)]))
  (for [injection turn.injections]
    (<- (emit session turn (InputFate injection.ref "completed"))))
  (<- (finish session turn (Completed :result-text text :usage turn.reply.usage :cost-usd turn.reply.cost-usd
                                      :input-refs (tuple turn.refs))))
  None)

(defn #^ float line-due-at [#^ FakeTurn turn #^ int index]
  "本文の行 index(0 から)を出す時刻: 始めてから期限までの前半に等間隔。"
  (+ turn.started-at (* (/ (- turn.due-at turn.started-at) 2) (/ index (max turn.reply.lines 1)))))

(defn #^ (| float None) next-line-at [#^ FakeTurn turn]
  "まだ出していない本文の行の次の時刻(無ければ None)。"
  (if (and (in turn.phase #("quick" "tool")) (< turn.lines-emitted turn.reply.lines))
      (line-due-at turn turn.lines-emitted)
      None))

(defk emit-due-lines [#^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]}
  "now までに来た本文の行を出す。"
  (while (and (< turn.lines-emitted turn.reply.lines) (>= (+ now CLOCK-TICK) (line-due-at turn turn.lines-emitted)))
    (<- (emit session turn (AssistantMessage :text (+ "line " (str turn.lines-emitted)))))
    (+= turn.lines-emitted 1))
  None)

(defk end-scripted [#^ FakeSession session #^ FakeTurn turn]
  {:pre [(: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "筋書きが失敗か process の消失で終わる手番の終わり(注入は読まない)。"
  (setv reply turn.reply)
  (if (is-not reply.fail None)
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "failed" :usage reply.usage)))
        (<- (finish session turn (Failed reply.fail :terminal-reason "failed" :usage reply.usage :cost-usd reply.cost-usd
                                         :input-refs (tuple turn.refs)))))
      (<- (finish session turn (BackendLost reply.lose))))
  None)

(defk advance [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "今の時刻まで筋書きを進める。"
  (<- now (GetMonotonic))
  (when (in turn.phase #("quick" "tool"))
    (<- (emit-due-lines session turn now)))
  (when (and (in turn.phase #("quick" "tool")) (>= (+ now CLOCK-TICK) turn.due-at))
    (when (= turn.phase "tool")
      (<- (emit-all session turn [(ToolResult :tool-use-ids #("fake-tool")) (TaskEvent "fake-task" "completed")])))
    (if (or (is-not turn.reply.fail None) (is-not turn.reply.lose None))
        (<- (end-scripted session turn))
        (<- (complete-turn world session turn))))
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
  (<- (emit session turn (Init :session-id session.session-id
                               :capabilities (if reply.interrupt-receipt FAKE-CAPABILITIES NO-RECEIPT-CAPABILITIES)
                               :model "fake")))
  (cond
    reply.needs-permission
      (do
        (setv request-id (str (uuid.uuid4)))
        (setv turn.phase "permission" turn.permission request-id)
        (<- (emit-all session turn [(AssistantMessage :tool-names #("Bash"))
                                    (PermissionRequested request-id "Bash" (FrozenMap {"command" "fake"}))])))
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
    (setv parent (transcript-of world spec.home spec.cwd target))
    (when (is parent None)
      (raise (RuntimeError (.format "枝分かれの元の会話 {} の transcript が無い(在ることは上で確かめた)" target))))
    (setv (get world.transcripts key) (list parent)))
  (when (isinstance origin FreshSession) (setv (get world.transcripts key) []))
  (setv session (or existing (FakeSession session-id spec.home spec.cwd)))
  (setv session.closed False)
  (setv (get world.sessions session-id) session)
  (setv memory (tuple (get world.transcripts key)))
  (.append (get world.transcripts key) input.text)
  (<- now-time (GetTime))
  (setv (get world.activity key) (.timestamp now-time))
  (<- reply FakeReply (reply-of world input.text memory))
  (<- turn (begin-fake-turn world session reply #(input.ref) True))
  (TurnStarted (ClaudeTurn session-id turn.seq) session-id))

(defn running-turn-of [#^ FakeClaudeWorld world #^ ClaudeTurn turn]
  (setv session (.get world.sessions turn.session-id))
  (setv running (if (is session None) None (.running session)))
  (if (and (is-not running None) (= running.seq turn.turn-seq)) running None))

(defk fake-inject [#^ FakeClaudeWorld world #^ ClaudeInjectInput request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInjectInput)] :post [(: % (| InputQueued NoTurnInFlight))]}
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (.append turn.injections (FakeInjection request.input.ref request.input.text))
  (.append turn.refs request.input.ref)
  (<- (emit (get world.sessions request.turn.session-id) turn (InputFate request.input.ref "queued")))
  (InputQueued request.input.ref))

(defk fake-interrupt [#^ FakeClaudeWorld world #^ ClaudeInterruptTurn request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeInterruptTurn)] :post [(: % (| InterruptRequested NoTurnInFlight))]}
  "止める(本物の対話の解釈 dialogue.hy の interrupt と同じ分け方): 読まれていない注入が在り、この手番の CLI が止めるの受理
   (interrupt_receipt_v1)を名乗っていれば control_request の形 — 注入を生き残った入力として次の手番で走らせる。それ以外は SIGINT の形
   (result は error_during_execution・aborted_streaming)で Interrupted — 読まれていない注入は捨てた入力(dropped-refs・on-result の
   StopSignal の道)。捨てた注入の行方の行は出さない(SIGINT の時に CLI が名乗る行は実測に無く、本物の handler も終わりだけを使う)。"
  (setv turn (running-turn-of world request.turn))
  (when (is turn None) (return (NoTurnInFlight request.turn.session-id)))
  (setv session (get world.sessions request.turn.session-id))
  (setv queued (lfor injection turn.injections :if (= injection.fate "queued") injection))
  (if (and queued turn.reply.interrupt-receipt)
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_tools")))
        (setv memory (tuple (get world.transcripts (.transcript-key world session.home session.cwd session.session-id))))
        (<- reply FakeReply (reply-of world (.join "\n" (lfor injection queued injection.text)) memory))
        (<- next-turn (begin-fake-turn world session (FakeReply reply.text) (tuple (lfor injection queued injection.ref)) False))
        (<- (finish session turn (Interrupted :surviving-refs (tuple next-turn.refs)
                                              :continued-by (ClaudeTurn session.session-id next-turn.seq)))))
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_streaming")))
        (<- (finish session turn (Interrupted :dropped-refs (tuple (lfor injection queued injection.ref)))))))
  (InterruptRequested))

(defk fake-read-events [#^ FakeClaudeWorld world #^ ClaudeReadTurnEvents request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeReadTurnEvents)] :post [(: % (| TurnEventPage UnknownTurn))]}
  (setv session (.get world.sessions request.turn.session-id))
  (when (is session None) (return (UnknownTurn request.turn)))
  (setv turn (.get session.turns request.turn.turn-seq))
  (when (is turn None) (return (UnknownTurn request.turn)))
  (<- started (GetMonotonic))
  (setv deadline (+ started (float request.wait-up-to)))
  (while True
    (<- (advance world session turn))
    (setv lines (tuple (gfor line turn.lines :if (> line.seq request.after-seq) line)))
    (<- now (GetMonotonic))
    (when (or lines (is-not turn.end None) (>= now deadline))
      (return (TurnEventPage lines (if lines (. (get lines -1) seq) request.after-seq) turn.end)))
    (setv line-at (next-line-at turn))
    (setv wake (if (in turn.phase #("quick" "tool"))
                   (min turn.due-at deadline (if (is line-at None) deadline line-at))
                   deadline))
    ;; 筋書きの次の刻(行・期限)か、待ちの外で行か終わりが出て呼び鈴が鳴るまで眠る(呼び鈴は読み直す前に掛け、鳴らずに起きたら外す)。
    (<- bell (CreateExternalPromise))
    (setv turn.bells (+ turn.bells #(bell)))
    (<- _woke (WaitWithin bell.future (max MIN-SLEEP (- wake now)) :park True))
    (setv turn.bells (tuple (gfor other turn.bells :if (is-not other bell) other)))))

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
                                                                       :if (= injection.fate "queued")
                                                                       injection.ref))))))
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

(defk fake-export [#^ FakeClaudeWorld world #^ ClaudeExportSession request]
  {:pre [(: world FakeClaudeWorld) (: request ClaudeExportSession)] :post [(: % (| SessionExported SessionNotFound))]}
  "transcript の写し: 行を改行で結んだ本文(carry-into の Rebuilt が同じ行の列へ読み戻す形)。無い・空 = SessionNotFound。"
  (val lines (transcript-of world request.home request.cwd request.session-id))
  (val text (if (is lines None) "" (.join "" (gfor line lines (+ line "\n")))))
  (if (.strip text) (SessionExported text) (SessionNotFound request.session-id)))

(defk fake-drop [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)]}
  (setv session (.get world.sessions session-id))
  (when (is session None) (return False))
  (setv running (.running session))
  (when (is running None) (return False))
  (<- (finish session running (BackendLost "process killed (fake)")))
  True)

(defk fake-forget [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)]}
  "家から会話を消す(家を空にした形): 走っている手番は BackendLost で終わり(終わりは読める)、その会話の transcript を忘れる。
   以後の ResumeSession は SessionNotFound(写しを持ち込めば続く)。答え = 忘れた transcript が在ったか。"
  (setv session (.get world.sessions session-id))
  (when (is-not session None)
    (setv running (.running session))
    (when (is-not running None)
      (<- (finish session running (BackendLost "home emptied (fake)")))))
  (setv keys (lfor key world.transcripts :if (= (get key 2) session-id) key))
  (for [key keys]
    (del (get world.transcripts key))
    (.pop world.activity key None))
  (bool keys))


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
  (ClaudeExportSession [home cwd session-id]
    (<- exported (fake-export world effect))
    (resume exported))
  (ClaudeDropProcess [session-id]
    (<- dropped (fake-drop world session-id))
    (resume dropped))
  (ClaudeForgetSession [session-id]
    (<- forgotten (fake-forget world session-id))
    (resume forgotten)))
