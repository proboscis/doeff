;;; fake の handler — 本番の handler と同じ公開 effect に、memory の上で答える(設計 8 節)。
;;;
;;; 時刻は doeff-time(GetMonotonic で手番の筋書きを進め、GetTime で行の at を刻む)。仮想の時計(sim-time-handler)の下では
;;; 道具の秒数も待ちも一瞬で進む。筋書き = 入力の本文と会話の記憶(それまでの入力)→ FakeReply(返事の本文・道具の秒数・
;;; 許可の問いの要否・終わり方〔完了・失敗・process が消える〕・usage・途中の本文の行の数・最後の本文を分ける差分の片の数)。
;;; 行は本番と同じ ClaudeStreamLine / ClaudeLineKind の型で出す(型を 2 つ作らない)。
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
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_claude_code.values [ClaudeTurn FreshSession ResumeSession ForkSession Rebuilt LinkFromHome IMAGE-MIMES])
(import doeff_claude_code.lines [ClaudeStreamLine Init AssistantMessage PartialMessage ToolCall ToolAnswer ToolResult InputFate PermissionRequested
                                 TaskEvent TurnResult Completed Failed Interrupted BackendLost ClaudeLineKind ClaudeTurnEnd Usage
                                 ModelWindow merged-windows])
(import doeff_claude_code.effects [ClaudeStartTurn ClaudeInjectInput ClaudeInterruptTurn ClaudeReadTurnEvents
                                   ClaudeAnswerPermission ClaudeCloseSession ClaudeSessionStatus ClaudeExportSession
                                   TurnStarted InputQueued InterruptRequested TurnEventPage Answered SessionClosed
                                   SessionStatus SessionExported Idle TurnRunning Closed TranscriptPresent TranscriptAbsent
                                   SessionNotFound SessionIdInUse TurnInFlight AttachmentRefused NoTurnInFlight
                                   UnknownTurn NoSuchRequest])
(import doeff_claude_code.faults [ClaudeDropProcess ClaudeForgetSession ClaudeLiveProcess ClaudeEmitOutsideTurn LiveProcess
                                  NoLiveProcess StopReason])
(import doeff_claude_code.argv [launch-key])

(setv QUICK-TURN-SECONDS 0.1)
;; 仮想の時計は datetime(マイクロ秒の刻み)なので、秒の小数の足し算の端数で「期限の直前」に留まらないように
;; 期限の比べに刻み 1 つ分の余裕を置き、眠りは刻み以上にする。
(setv CLOCK-TICK 1e-6)
(setv MIN-SLEEP 1e-3)
;; 最後の本文の差分(PartialMessage の text_delta)どうしの間隔の秒。本物の CLI(--include-partial-messages)は生成の途中の本文を
;; 数十ミリ秒ごとの片で出し、確定の本文(assistant の行)はその後に出す。
(val DELTA-SECONDS 0.05)
(setv FAKE-CAPABILITIES #("msg_lifecycle_v1" "interrupt_receipt_v1"))
;; 止めるの受理(interrupt_receipt_v1)を名乗らない CLI の process の能力(FakeReply の interrupt-receipt が偽の手番)。
(val NO-RECEIPT-CAPABILITIES #("msg_lifecycle_v1"))
;; 偽の手番が呼ぶ道具の 1 つの呼び(tool_use の id と道具の名)。道具の結果の行(ToolResult)の答えは同じ id を名指す。呼びの命令と
;; 結果の中身は手番の返事(FakeReply の tool-input・tool-output・tool-error)が決める。
(val FAKE-TOOL-USE-ID "fake-tool")
(val FAKE-TOOL-NAME "Bash")


(defclass [(dataclass :frozen True)] FakeReply []
  "筋書きの 1 手番の返事: text = 最後の本文・tool-seconds = 道具が走る秒数(0 = 道具なし)・
   needs-permission = 道具の前に許可の問いを出す・fail = 期限で Failed(detail = この文)で終わる・lose = 期限で process が消えて
   BackendLost(detail = この文)で終わる(fail と lose は多くとも 1 つ)・usage = Completed / Failed に載せる usage・
   cost-usd = Completed / Failed に載せる手番の額(USD — 本番の handler が累積の額の差から数える値の代わり。None = 名乗らない)・
   lines = 始めてから期限までの前半に、本文の行(AssistantMessage)を lines 行ほど等間隔に出す(出来事の量の多い手番)・
   think-seconds = 道具を使わずに考える秒(道具の行を出さずに長く走る手番)・
   interrupt-receipt = この手番の CLI の process が init で interrupt_receipt_v1 を名乗るか(本物の handler は手番ごとに process を
   起こす)。偽 = 止めるは SIGINT の形で、読まれていない注入を捨てた入力(dropped-refs)として終える — 本物の対話の解釈
   (dialogue.hy の interrupt と on-result)が受理を名乗らない CLI を止める道(#3467)・
   deltas = 最後の本文を何片の差分(PartialMessage の text_delta)に分けて、確定の本文(AssistantMessage)の前に DELTA-SECONDS ごとに
   出すか(0 = 差分を出さない。本物の CLI の --include-partial-messages の行の順 — 差分の列 → 確定の本文 → result)。片の連結は
   確定の本文と同じで、どの片も空でない(deltas は本文の字数以下)。失敗と消失(fail・lose)の手番は本文を出さないので組まない・
   tool-input = 道具の呼び(と許可の問い)の命令 — tool_use の block の input と同じ JSON の object(深く凍らせる)・
   tool-output = 道具の結果の中身の本文・tool-error = 道具が誤りで終えたか(tool-input から tool-error は、道具を呼ぶ手番 —
   tool-seconds > 0 か needs-permission — の呼びと結果の行に載る。上の層が道具の命令と出力を運ぶ事を確かめるため・#3744)・
   last-call-usage・last-call-model = この手番の CLI が本体の会話の assistant の行で名乗る呼びの usage と model・model-windows = result の
   行で名乗る model ごとの窓(本物の CLI が全部の行で名乗るのと同じに、この手番の全部の assistant の行・result の行に載せる。手番の
   終わりの 3 欄は、本番の状態機械と同じ規則で出した行から数える — 上の層が会話の context の大きさを運ぶ事を確かめるため・#3744)。"
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
  ;; 最後の本文を分ける差分の片の数(0 = 差分を出さない)。
  (setv #^ int deltas 0)
  ;; 道具の呼びの命令(tool_use の input と同じ JSON の object)と、結果の中身の本文・誤りの印。
  (setv #^ FrozenMap tool-input (field :default-factory (fn [] (FrozenMap {"command" "fake"}))))
  (setv #^ str tool-output "")
  (setv #^ bool tool-error False)
  ;; 本体の会話の呼びの usage と model、model ごとの窓(名乗らない = None・空)。
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) model-windows #())
  (defn #^ None __post-init__ [self]
    (object.__setattr__ self "tool_input" (frozen-json-object self.tool-input "FakeReply.tool_input"))
    (when (and (is-not self.fail None) (is-not self.lose None))
      (raise (ValueError "FakeReply の fail と lose は多くとも 1 つ")))
    (when (< self.lines 0)
      (raise (ValueError (+ "FakeReply の lines は 0 以上: " (str self.lines)))))
    (when (< self.deltas 0)
      (raise (ValueError (+ "FakeReply の deltas は 0 以上: " (str self.deltas)))))
    (when (> self.deltas (len self.text))
      (raise (ValueError (.format "FakeReply の deltas は本文の字数以下(どの片も空でない): deltas {} / 本文 {} 字"
                                  self.deltas (len self.text)))))
    (when (and (> self.deltas 0) (or (is-not self.fail None) (is-not self.lose None)))
      (raise (ValueError "FakeReply の deltas は本文で終わる手番だけ(fail・lose の手番は本文を出さない)")))))


(defclass [(dataclass :frozen True)] FakeInjection []
  "手番に足した入力 1 つ: ref = 入力の行の名 / text = 本文 / fate = 運命(queued → started → completed)。
   運命が進む時は replace で作り直して差し替える。"
  (#^ str ref)
  (#^ str text)
  (setv #^ str fate "queued"))


(defclass FakeTurn []
  "fake の手番 1 つ: phase = quick / tool / permission / text(最後の本文を差分で書いている — 期限 due-at は確定の本文を出す時刻)/ done・
   permission = 答え待ちの許可の問いの id(無ければ None)・text = 最後の本文(text の相に入る時に決まる)・deltas-emitted = 出した差分の片の数・
   end = 手番の終わり(まだなら None)・bells = 出来事の読み(ClaudeReadTurnEvents)の待ち手が掛けた呼び鈴(新しい行か終わりで鳴らして外す)・
   last-call-usage・last-call-model・model-windows = 出した行から数えた本体の最後の呼びと窓(終わりに載せる — 本番の状態機械と同じ規則)。"
  (defn __init__ [self #^ int seq #^ float started-at #^ FakeReply reply #^ (get tuple #(str ...)) refs]
    (setv #^ int self.seq seq)
    (setv #^ float self.started-at started-at)
    (setv #^ FakeReply self.reply reply)
    (setv #^ (get list str) self.refs (list refs))
    (setv #^ str self.phase "quick")
    (setv #^ float self.due-at (+ started-at (max QUICK-TURN-SECONDS reply.think-seconds)))
    (setv #^ int self.lines-emitted 0)
    (setv #^ str self.text "")
    (setv #^ int self.deltas-emitted 0)
    (setv #^ (get list FakeInjection) self.injections [])
    (setv #^ (| str None) self.permission None)
    (setv #^ (get list ClaudeStreamLine) self.lines [])
    (setv #^ (| Completed Failed Interrupted BackendLost None) self.end None)
    (setv #^ (get tuple #((get ExternalPromise None) ...)) self.bells #())
    (setv #^ (| Usage None) self.last-call-usage None)
    (setv #^ (| str None) self.last-call-model None)
    (setv #^ (get tuple #(ModelWindow ...)) self.model-windows #())))


(defclass FakeSession []
  "launches = この会話で起こした process の数(本番の handler と同じ規則 — 同じ起こした時の条件の鍵の続きは生きた process を使い回し、
   生き残った入力の手番も同じ process)・alive = process が生きている(手番をまたいで生きて待つ — #3672)・launch-key = 今の process の
   起こした時の条件の鍵・stopped-because = 最後の process を降ろした訳。"
  (defn __init__ [self #^ str session-id home #^ str cwd]
    (setv self.session-id session-id self.home home self.cwd cwd
          self.current-seq 0 self.next-line-seq 0 self.closed False)
    (setv #^ int self.launches 0)
    (setv #^ bool self.alive False)
    (setv #^ (| str None) self.launch-key None)
    (setv #^ (| StopReason None) self.stopped-because None)
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
          self.activity {})
    (setv #^ (get dict #(str FakeSession)) self.sessions {}))

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

(defk said-by-reply [#^ FakeReply reply kind]
  {:pre [(: reply FakeReply) (: kind ClaudeLineKind)] :post [(: % ClaudeLineKind)] :tags {:context "claude-code" :role "foundation"}}
  "fake の CLI がこの手番の行で名乗る呼びの値を行に載せるため(本物の CLI が全部の assistant の行で usage と model を、result の行で
   modelUsage を名乗るのと同じ — #3744): assistant の行には返事の last-call-usage と last-call-model、result の行には返事の
   model-windows。ほかの行はそのまま。"
  (cond
    (isinstance kind AssistantMessage) (replace kind :usage reply.last-call-usage :model reply.last-call-model)
    (isinstance kind TurnResult) (replace kind :model-windows reply.model-windows)
    True kind))

(defk emit [#^ FakeSession session #^ FakeTurn turn kind]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kind ClaudeLineKind)] :post [(: % (type None))]}
  "手番の行を 1 つ出すため。出した行から、本番の状態機械(dialogue.hy の on-assistant・on-result)と同じ規則で本体の最後の呼びと
   model ごとの窓を手番に覚える(終わりに載せるのは finish)。"
  (<- at (GetTime))
  (<- said (said-by-reply turn.reply kind))
  (.append turn.lines (ClaudeStreamLine :seq session.next-line-seq :at at :kind said :raw (repr said)))
  (+= session.next-line-seq 1)
  (cond
    (and (isinstance said AssistantMessage) (is said.parent-tool-use-id None))
      (setv turn.last-call-usage said.usage turn.last-call-model said.model)
    (isinstance said TurnResult)
      (setv turn.model-windows (merged-windows turn.model-windows said.model-windows)))
  (<- (ring-turn turn))
  None)

(defk emit-all [#^ FakeSession session #^ FakeTurn turn #^ list kinds]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: kinds list)] :post [(: % (type None))]}
  (for [kind kinds] (<- (emit session turn kind)))
  None)

(defk finish [#^ FakeSession session #^ FakeTurn turn end]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: end ClaudeTurnEnd)] :post [(: % (type None))]}
  "手番を end で閉じるため。process は手番の終わりで降ろさない(次の手番まで生きて待つ)— 消えた process の終わり(BackendLost)だけ
   process が無くなる(訳は付けない — 降ろしたのでなく自分で消えた)。終わりには手番に覚えた本体の最後の呼びと窓を載せる(本番の
   状態機械の ended と同じ — どの終わり方でも同じ 3 欄)。"
  (setv turn.end (replace end :last-call-usage turn.last-call-usage :last-call-model turn.last-call-model
                              :model-windows turn.model-windows)
        turn.phase "done")
  (when (isinstance end BackendLost) (setv session.alive False))
  (<- (ring-turn turn))
  None)

(defk read-injections [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % tuple)]
   :tags {:context "claude-code" :role "foundation"}}
  "読まれていない注入(queued)を読む — 本物の CLI が道具の境界と本文の後に足された入力を読むのと同じに、注入の started の行を出し、
   それぞれの筋書きの返事の本文を足された順に返す(最後の本文へ続けるため)。"
  (val memory (tuple (.get world.transcripts (.transcript-key world session.home session.cwd session.session-id) [])))
  (var extra #())
  (for [#(index injection) (enumerate turn.injections)]
    (when (= injection.fate "queued")
      (setv (get turn.injections index) (replace injection :fate "started"))
      (<- (emit session turn (InputFate injection.ref "started")))
      (<- extra-reply FakeReply (reply-of world injection.text memory))
      (:= extra (+ extra #(extra-reply.text)))))
  extra)

(defk begin-text [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "道具の境界(か手番の終わり)で最後の本文を決め、本文を書く相(text)に入る — 本物の CLI が最後の本文を差分で流してから確定するのを
   模すため。本文 = 返事の本文 + 読まれていない注入の返事。確定の本文は deltas 片の差分を DELTA-SECONDS ごとに出し終えた刻
   (deltas = 0 なら今)に出す。"
  (<- extra (read-injections world session turn))
  (setv turn.text (.join " " (+ #(turn.reply.text) extra))
        turn.phase "text"
        turn.due-at (+ now (* DELTA-SECONDS turn.reply.deltas)))
  None)

(defk next-delta-at [#^ FakeTurn turn]
  {:pre [(: turn FakeTurn)] :post [(: % (| float None))] :tags {:context "claude-code" :role "foundation"}}
  "まだ出していない次の差分の時刻(本文を書く相の外か、出し終えていれば None)— 差分を出す刻と、読みの待ち手が起きる刻を 1 か所で決めるため。
   差分 k(0 から)は確定の本文の時刻 due-at の (deltas − k) × DELTA-SECONDS 前(最初の片は相に入った刻)。"
  (if (and (= turn.phase "text") (< turn.deltas-emitted turn.reply.deltas))
      (- turn.due-at (* DELTA-SECONDS (- turn.reply.deltas turn.deltas-emitted)))
      None))

(defk emit-due-deltas [#^ FakeSession session #^ FakeTurn turn #^ float now]
  {:pre [(: session FakeSession) (: turn FakeTurn) (: now float)] :post [(: % (type None))]
   :tags {:context "claude-code" :role "foundation"}}
  "now までに来た本文の差分を PartialMessage の text_delta で出す — 上の層が確定の本文より先に書きかけの本文を読めるように。本文を
   deltas 片に字数でほぼ等分する(片 k = 本文の [k × 字数 / deltas, (k + 1) × 字数 / deltas) — 片の連結は本文と同じ・どの片も空でない)。"
  (val pieces turn.reply.deltas)
  (val size (len turn.text))
  (var due (! (next-delta-at turn)))
  (while (and (is-not due None) (>= (+ now CLOCK-TICK) due))
    (<- (emit session turn (PartialMessage :text-delta (cut turn.text
                                                            (// (* turn.deltas-emitted size) pieces)
                                                            (// (* (+ turn.deltas-emitted 1) size) pieces)))))
    (setv turn.deltas-emitted (+ turn.deltas-emitted 1))
    (<- following (next-delta-at turn))
    (:= due following))
  None)

(defk complete-turn [#^ FakeClaudeWorld world #^ FakeSession session #^ FakeTurn turn]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: turn FakeTurn)] :post [(: % (type None))]}
  "本文を書き終えた刻に、確定の本文の返事で手番を終える。本文を書く間に足された注入もここで読み、その返事を本文の後に続ける。"
  (<- late (read-injections world session turn))
  (val text (.join " " (+ #(turn.text) late)))
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
      (<- (emit-all session turn [(ToolResult :answers #((ToolAnswer :id FAKE-TOOL-USE-ID :text turn.reply.tool-output
                                                                      :is-error turn.reply.tool-error)))
                                  (TaskEvent "fake-task" "completed")])))
    (if (or (is-not turn.reply.fail None) (is-not turn.reply.lose None))
        (<- (end-scripted session turn))
        (<- (begin-text world session turn now))))
  (when (= turn.phase "text")
    (<- (emit-due-deltas session turn now))
    (when (>= (+ now CLOCK-TICK) turn.due-at)
      (<- (complete-turn world session turn))))
  None)

(defk begin-fake-turn [#^ FakeClaudeWorld world #^ FakeSession session reply #^ tuple refs #^ bool announce #^ bool launched]
  {:pre [(: world FakeClaudeWorld) (: session FakeSession) (: reply FakeReply) (: refs tuple) (: announce bool) (: launched bool)]
   :post [(: % FakeTurn)]}
  "手番を開いて最初の行を出す。announce = 入力の行の運命と init を出す(頼まれた手番 — 使い回した process も入力ごとに init を出す。
   生き残った入力の手番は started から)/ launched = この手番のために process を起こした(使い回し・生き残った入力の手番は起こさない)。"
  (<- now (GetMonotonic))
  (+= session.current-seq 1)
  (when launched (+= session.launches 1))
  (setv session.alive True)
  (setv turn (FakeTurn session.current-seq now reply refs))
  (setv (get session.turns turn.seq) turn)
  (for [ref refs]
    (when announce (<- (emit session turn (InputFate ref "queued"))))
    (<- (emit session turn (InputFate ref "started"))))
  (<- (emit session turn (Init :session-id session.session-id
                               :capabilities (if reply.interrupt-receipt FAKE-CAPABILITIES NO-RECEIPT-CAPABILITIES)
                               :model "fake")))
  ;; 道具の呼び: 命令は返事の tool-input(許可の問いも同じ命令を問う — 本物の CLI の can_use_tool の input は tool_use の input)。
  (val call (ToolCall FAKE-TOOL-USE-ID FAKE-TOOL-NAME reply.tool-input))
  (cond
    reply.needs-permission
      (do
        (setv request-id (str (uuid.uuid4)))
        (setv turn.phase "permission" turn.permission request-id)
        (<- (emit-all session turn [(AssistantMessage :tool-calls #(call))
                                    (PermissionRequested request-id FAKE-TOOL-NAME reply.tool-input)])))
    (> reply.tool-seconds 0)
      (do
        (setv turn.phase "tool" turn.due-at (+ now reply.tool-seconds))
        (<- (emit-all session turn [(AssistantMessage :tool-calls #(call)) (TaskEvent "fake-task" "started")]))))
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
  ;; 本番の handler と同じ規則(decision.start-decision): 続き(ResumeSession)で、生きて待つ process の起こした時の条件の鍵が同じなら
  ;; 使い回す。違えば降ろしてから起こす(訳 LAUNCH-CHANGED)。fake の process に実行ファイルは無いので command は空。
  (<- wanted (launch-key #() spec))
  (setv reuse (and (isinstance origin ResumeSession) session.alive (= session.launch-key wanted)))
  (when (and session.alive (not reuse))
    (setv session.alive False session.stopped-because StopReason.LAUNCH-CHANGED))
  (setv session.launch-key wanted)
  (setv memory (tuple (get world.transcripts key)))
  (.append (get world.transcripts key) input.text)
  (<- now-time (GetTime))
  (setv (get world.activity key) (.timestamp now-time))
  (<- reply FakeReply (reply-of world input.text memory))
  (<- turn (begin-fake-turn world session reply #(input.ref) True (not reuse)))
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
        (<- next-turn (begin-fake-turn world session (FakeReply reply.text) (tuple (lfor injection queued injection.ref)) False False))
        (<- (finish session turn (Interrupted :process-kept True :surviving-refs (tuple next-turn.refs)
                                              :continued-by (ClaudeTurn session.session-id next-turn.seq)))))
      (do
        (<- (emit session turn (TurnResult "error_during_execution" True :terminal-reason "aborted_streaming")))
        (<- (finish session turn (Interrupted :process-kept False :dropped-refs (tuple (lfor injection queued injection.ref)))))
        ;; SIGINT の形の CLI は result の後に自分で降りる(本番の handler も降ろす — 訳 INTERRUPT-SIGNAL)。
        (setv session.alive False session.stopped-because StopReason.INTERRUPT-SIGNAL)))
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
    ;; 次の刻 = 本文の行(quick・tool の相)か差分(text の相)の次の時刻(無ければ期限)と、相の期限 due-at の早い方。
    (setv line-at (next-line-at turn))
    (<- delta-at (next-delta-at turn))
    (setv next-at (if (is line-at None) delta-at line-at))
    (setv wake (if (in turn.phase #("quick" "tool" "text"))
                   (min turn.due-at deadline (if (is next-at None) deadline next-at))
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
    (<- (finish session running (Interrupted :process-kept False :dropped-refs (tuple (gfor injection running.injections
                                                                       :if (= injection.fate "queued")
                                                                       injection.ref))))))
  (setv session.closed True)
  (when session.alive
    (setv session.alive False session.stopped-because StopReason.SESSION-CLOSED))
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
  "会話の process を消すため(検の口 — 本番の答え手と同じく、手番を走らせていない生きた process も消す。走っている手番は
   BackendLost で終わる)。自分で消えた扱いで、降ろした訳は付けない。答え = 消す process が在ったか。"
  (setv session (.get world.sessions session-id))
  (when (is session None) (return False))
  (setv running (.running session))
  (when (is-not running None)
    (<- (finish session running (BackendLost "process killed (fake)")))
    (return True))
  (when (not session.alive) (return False))
  (setv session.alive False)
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

(defk fake-live-process [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % (| LiveProcess NoLiveProcess))]
   :tags {:context "claude-code" :role "foundation"}}
  "会話の process の見え方を答えるため(本番の handler と同じ筋書きで、使い回しと守りを確かめる口 — #3672)。"
  (val session (.get world.sessions session-id))
  (cond
    (is session None) (NoLiveProcess :launches 0 :stopped-because None)
    session.alive (LiveProcess :launches session.launches)
    True (NoLiveProcess :launches session.launches :stopped-because session.stopped-because)))

(defk fake-emit-outside [#^ FakeClaudeWorld world #^ str session-id]
  {:pre [(: world FakeClaudeWorld) (: session-id str)] :post [(: % bool)] :tags {:context "claude-code" :role "foundation"}}
  "生きていて手番を走らせていない process に手番の外の出力をさせるため(守りの筋書きの口)。本番の handler と同じく、手番の外で
   出力した process は降ろす(訳 OUTSIDE-TURN-OUTPUT)。答え = 出させる process が在ったか。"
  (val session (.get world.sessions session-id))
  (when (or (is session None) (not session.alive) (is-not (.running session) None))
    (return False))
  (setv session.alive False session.stopped-because StopReason.OUTSIDE-TURN-OUTPUT)
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
  (ClaudeExportSession [home cwd session-id]
    (<- exported (fake-export world effect))
    (resume exported))
  (ClaudeDropProcess [session-id]
    (<- dropped (fake-drop world session-id))
    (resume dropped))
  (ClaudeLiveProcess [session-id]
    (<- view (fake-live-process world session-id))
    (resume view))
  (ClaudeEmitOutsideTurn [session-id]
    (<- emitted (fake-emit-outside world session-id))
    (resume emitted))
  (ClaudeForgetSession [session-id]
    (<- forgotten (fake-forget world session-id))
    (resume forgotten)))
