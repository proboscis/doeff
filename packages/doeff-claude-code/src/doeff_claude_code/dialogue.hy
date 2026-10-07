;;; claude の print mode(--input-format stream-json / --output-format stream-json)の作法の状態機械 — 純関数だけ(I/O なし)。
;;;
;;; 「stdin に何を書くか・stdout の 1 行を読んだら手番は終わったか・止めるにはどう伝えるか」の判断をここに集める。
;;; 実際に書く・信号を送る・process を降ろすのは handler(handler.hy)と器(process.hy)。
;;;
;;; 規則(doeff-agents の headless_protocol.ClaudeDialogue から移した物と、#603 の直し):
;;; - host から見た手番の終わりはちょうど 1 つ。CLI 自身が起こした手番の result(origin.kind = task-notification)は終わりにしない。
;;; - result の時点でまだ読まれていない注入(InputFate が queued)が在れば、CLI がそれを次の CLI の手番として走らせるので、
;;;   host の手番は続く(中間の result)。
;;; - 止める: まだ読まれていない注入が在り CLI が interrupt_receipt_v1 を名乗った時だけ control_request interrupt(process は残り、
;;;   答えの still_queued の注入が次の CLI の手番として走る)。それ以外は SIGINT。
;;; - #603 の直し: claude 2.1.282 の SIGINT は result(subtype error_during_execution・terminal_reason aborted_streaming)を出して
;;;   から降りる。止めるを求めた後の result は失敗ではなく Interrupted に写す(求めていない aborted_streaming は Failed のまま・
;;;   terminal_reason つき)。
;;; - 手番の境界の所有者は host — CLI に result の後の手番を持たせない(#517)。会話の process は手番の終わりで降ろさず、次の手番まで
;;;   生かして待たせる(#3672 — 起こし直しと記録の読み直しの 1.6〜2.6 秒を消す)。host の手番の外で CLI が出した行(手番の外で起きた
;;;   model の出力)を読んだら、その process を降ろす(retire = OUTSIDE-TURN-OUTPUT)— host の頼みの無い手番は誰の物でもない。
;;;   外とみなさない行は、host が書いた入力の運命と control の答え(OUTSIDE-TURN-QUIET-KINDS — model を動かさない作法の行)だけ。
;;;   例外は入力を書かずに事前起動した process(ClaudeWarmSession)の最初の入力の前だけで、SessionStart の hook の開始と応答の行も
;;;   外とみなさない(quiet-before-first-input — この 2 種の行のほかは広げない。最初の入力の後の待ちは前と同じ)。
;;;   control_request で止めて注入が生き残った時は、生き残った入力の手番が同じ process で続く(continues)。
;;; - 手番の額(#883): result の行の total_cost_usd は会話の累積で、usage はその CLI の手番 1 回分(実測 2.1.283 —
;;;   同じ process の 2 つ目の result の行は 1 つ目の額との和を名乗り、--resume で起こした process は前の process が降りる時に
;;;   transcript へ記した額から数え続ける)。だから host の手番の額 = 手番を閉じた result の行の累積 − 手番の起点(cost-mark = 前の手番を
;;;   閉じた result の行の累積)、usage = 手番の途中に読んだ result の行の usage の和 — 額と usage を同じ行の集まりで数える。
;;;   起点が分からなければ額は None(次の手番からは読んだ行の累積で数え直す)。起点を次の process へ引き継ぐのは handler(spawn-turn)。
;;;
;;; stdout の行は lines.hy が分類した型(ClaudeLineKind)で受ける — JSON の読みは lines.hy の 1 か所だけ。
;;; stdin へ書く行の綴り(JSON へ書く境界)はこの file の dumps の 1 か所。
(require doeff-hy.macros [defk])
(import dataclasses [dataclass field replace])
(import json)
(import doeff [run])
(import typing [NamedTuple])
(import doeff_hy.frozen [thaw-json])
(import doeff_claude_code.values [TurnInput Allow Deny])
(import doeff_claude_code.lines [Completed Failed Interrupted BackendLost Init InputFate ControlResponse PermissionRequested
                                 AssistantMessage TurnResult Usage ModelWindow HookNotice ClaudeLineKind INPUT-FATES
                                 INPUT-FATE-TERMINAL merged-windows RateLimit AccountLimitHit RATE-LIMIT-REJECTED
                                 ASSISTANT-ERROR-RATE-LIMIT])
(import doeff_claude_code.faults [StopReason])

;; CLI が system/init の capabilities で名乗る能力(実測 2.1.282)。
(setv LIFECYCLE-CAPABILITY "msg_lifecycle_v1")
(setv INTERRUPT-RECEIPT-CAPABILITY "interrupt_receipt_v1")
;; CLI が自分で起こした手番の result の origin.kind(本文の手番の result は origin を持たない)。
(setv CLI-OWN-TURN-ORIGINS (frozenset #{"task-notification"}))
;; host の手番の外で読んでも、手番の外の出力とみなさない行の型: host が書いた入力の運命と control の答え(model を動かさない作法の行)。
(setv OUTSIDE-TURN-QUIET-KINDS #(InputFate ControlResponse))


;; --- 状態 ---------------------------------------------------------------------------------------

(defclass Injection [NamedTuple]
  "足した入力 1 つ: ref = 入力の行の名 / fate = CLI が名乗った運命(INPUT-FATES の語)。"
  (#^ str ref)
  (#^ str fate))

(defclass [(dataclass :frozen True)] NoStop [])

(defclass [(dataclass :frozen True)] StopSignal []
  "SIGINT を送った。")

(defclass [(dataclass :frozen True)] StopControl []
  "control_request interrupt を書いた。still-queued = 答えが名指した生き残る注入(None = まだ答えを読んでいない)。"
  (#^ str request-id)
  (setv #^ (| (get tuple #(str ...)) None) still-queued None))

(defclass [(dataclass :frozen True)] DialogueState []
  "session-id = init で知った会話の id / in-flight = host の手番の途中 / cli-turn-open = CLI の手番が開いている
   (入力を書いてから、か init から result まで)/ lifecycle・interrupt-receipt = CLI が名乗った能力 /
   turn-refs = この host の手番に入れた入力の ref / injections = 足した入力(Injection)の列 /
   stop = 止めるの求め / deferred-result = 注入を待って飲んだ result の行(TurnResult)/
   permissions = 答え待ちの許可の問い(PermissionRequested)/
   cost-mark = 手番の額の起点(CLI の累積の額 total_cost_usd の、前の手番を閉じた result の行の値。process の始まりは handler が
   引き継いだ値 — None = 分からない)/ start-mark = この process の始まりの cost-mark(この process が額を記さずに消えた時、次の
   process の CLI が数え始める額 — handler が読む)/ turn-usage = この host の手番の途中に読んだ result の行の usage の和 /
   last-call-usage・last-call-model = この host の手番に読んだ本体の会話(parent_tool_use_id が null)の最後の assistant の行の usage と
   model(まだ無ければ None)/ turn-windows = この host のターンに読んだ result の行の model ごとの窓(ターンの終わりの 3 欄 — #3744)/
   awaiting-first-input = 入力を書かずに事前起動した process が最初の入力を待っている(その間だけ SessionStart の hook の行をターンの
   外の出力と数えない — quiet-before-first-input。最初のターンを始めると消える)/ limit-hit = この host の手番に読んだ、口座の限度に
   当たった事実(拒まれた限度の行と、限度の答え — AccountLimitHit・まだ無ければ None・#3983)。"
  (setv #^ str session-id "")
  (setv #^ bool in-flight False)
  (setv #^ bool cli-turn-open False)
  (setv #^ bool lifecycle False)
  (setv #^ bool interrupt-receipt False)
  (setv #^ (get tuple #(str ...)) turn-refs #())
  (setv #^ (get tuple #(Injection ...)) injections #())
  (setv #^ (| NoStop StopSignal StopControl) stop (NoStop))
  (setv #^ (| TurnResult None) deferred-result None)
  (setv #^ (get tuple #(PermissionRequested ...)) permissions #())
  (setv #^ (| float None) cost-mark None)
  (setv #^ (| float None) start-mark None)
  (setv #^ Usage turn-usage (field :default-factory Usage))
  (setv #^ (| Usage None) last-call-usage None)
  (setv #^ (| str None) last-call-model None)
  (setv #^ (get tuple #(ModelWindow ...)) turn-windows #())
  (setv #^ bool awaiting-first-input False)
  (setv #^ (| AccountLimitHit None) limit-hit None))

(defclass [(dataclass :frozen True)] Transition []
  "遷移の答え: 次の状態・stdin へ書く行・host の手番の終わり(無ければ None)・retire(この行で process を降ろす訳 — 無ければ
   None)・continues(終わった手番の後に、生き残った入力の手番が同じ process で続く)・signal(SIGINT を送る)・
   session-id(この行で知った会話の id)。"
  (#^ DialogueState state)
  (setv #^ (get tuple #(str ...)) sends #())
  (setv #^ (| Completed Failed Interrupted BackendLost None) end None)
  (setv #^ (| StopReason None) retire None)
  (setv #^ bool continues False)
  (setv #^ bool signal False)
  (setv #^ (| str None) session-id None))


;; --- stdin の行の綴り(この package でここ 1 か所) ----------------------------------------------------

(defn #^ str dumps [#^ dict value]
  "stdin の 1 行の綴り。凍らせた値(道具の入力など)はここで JSON の形へ戻す。"
  (json.dumps (thaw-json value) :ensure-ascii False :separators #("," ":")))

(defn #^ dict image-block [attachment]
  "添付 1 つ = Messages API と同じ image の block(実測 2026-09-14・doeff-agents conformance/attachment-physics.md)。"
  {"type" "image" "source" {"type" "base64" "media_type" attachment.mime "data" attachment.data-base64}})

(defn #^ str user-line [#^ TurnInput input]
  "stdin の 1 行 = user の message。ref は最上位の uuid(CLI が command_lifecycle でこの綴りを名乗る)。
   添付が無ければ content は素の文字列、在れば text → image の block の列。"
  (setv content
        (if input.attachments
            (+ (if input.text [{"type" "text" "text" input.text}] [])
               (lfor attachment input.attachments (image-block attachment)))
            input.text))
  (dumps {"type" "user" "message" {"role" "user" "content" content} "uuid" input.ref}))

(defn #^ str interrupt-request-line [#^ str request-id]
  (dumps {"type" "control_request" "request_id" request-id "request" {"subtype" "interrupt"}}))

(defn #^ str permission-response-line [#^ str request-id #^ dict response]
  (dumps {"type" "control_response"
          "response" {"subtype" "success" "request_id" request-id "response" response}}))


;; --- 読み ---------------------------------------------------------------------------------------

(defn #^ tuple queued-refs [#^ DialogueState state]
  "まだ model に読まれていない注入の ref(足した順)。"
  (tuple (gfor injection state.injections :if (= injection.fate "queued") injection.ref)))

(defn #^ tuple set-fate [#^ tuple injections #^ str ref #^ str fate]
  (tuple (gfor injection injections (if (= injection.ref ref) (Injection ref fate) injection))))

(defn #^ bool knows-injection [#^ DialogueState state #^ str ref]
  (any (gfor injection state.injections (= injection.ref ref))))


;; --- 手番の終わりの組み立て --------------------------------------------------------------------------

(defn #^ tuple result-input-refs [#^ TurnResult result #^ DialogueState state]
  "result が名乗る入力の ref(user_message_uuids)。名乗らない版ではこの手番に入れた ref。"
  (if result.input-refs result.input-refs state.turn-refs))

(defn end-of-result [#^ TurnResult result #^ DialogueState state]
  "result の行 → Completed | Failed。誤りの detail は CLI が名乗った文ちょうど(無ければ subtype)。
   usage = 手番の途中に読んだ result の行の usage の和(state.turn-usage — この行の分を足した後の状態を渡す)・
   cost-usd = この行の累積の額 − 手番の起点(state.cost-mark)。どちらかが分からない・差が負(CLI が起点の額から数えていない)なら None。"
  (setv refs (result-input-refs result state))
  (setv total result.cost-usd mark state.cost-mark)
  (setv cost (if (or (is total None) (is mark None) (< total mark)) None (- total mark)))
  (if result.is-error
      (Failed :detail (or (.strip result.result-text) result.subtype "error")
              :api-error-status result.api-error-status
              :terminal-reason result.terminal-reason
              :usage state.turn-usage
              :cost-usd cost
              :input-refs refs)
      (Completed :result-text result.result-text
                 :usage state.turn-usage
                 :cost-usd cost
                 :input-refs refs)))

(defn #^ DialogueState closed-turn [#^ DialogueState state]
  "host の手番を閉じた状態(会話の id・CLI の能力・額の起点は保つ。最後の呼びと窓は次の手番へ持ち越さない)。"
  (replace state :in-flight False :cli-turn-open False :turn-refs #() :injections #() :stop (NoStop)
           :deferred-result None :permissions #() :turn-usage (Usage)
           :last-call-usage None :last-call-model None :turn-windows #() :limit-hit None))

(defn with-last-call [end #^ DialogueState state]
  "手番の終わりに、その手番で読んだ本体の最後の呼びの usage と model・model ごとの窓を載せるため(どの終わり方でも同じ 3 欄 — #3744)。
   CLI が終えた手番(Completed・Failed)には、口座の限度に当たった事実も載せる(#3983 — 止めた・消えた手番は限度で終わったのではない)。"
  (setv called (replace end :last-call-usage state.last-call-usage :last-call-model state.last-call-model
                        :model-windows state.turn-windows))
  (match called
    (| (Completed) (Failed)) (replace called :account-limit state.limit-hit)
    _ called))

(defn ended [#^ DialogueState state end #^ (| TurnResult None) [priced-by None] #^ (| StopReason None) [retire None]]
  "host の手番を end で閉じる遷移(process は降ろさない — 次の手番まで生きて待つ。retire が在れば、その訳で降ろす)。priced-by = 手番を
   閉じた result の行(在ればその行の累積の額が次の手番の額の起点 — 止めた手番の額は数えずに捨て、次の手番へ混ぜない。行が無い
   終わりは起点を動かさない)。終わりには閉じる前の state の最後の呼びと窓を載せる(with-last-call)。"
  (setv closed (closed-turn state))
  (Transition :state (if (is priced-by None) closed (replace closed :cost-mark priced-by.cost-usd))
              :end (with-last-call end state) :retire retire))


;; --- 遷移(呼び手の操作) -----------------------------------------------------------------------------

(defn begin-turn [#^ DialogueState state #^ TurnInput input]
  "host のターンを始める: 入力の行を stdin へ(最初の入力を待つマークは消える)。"
  (Transition :state (replace (closed-turn state) :in-flight True :cli-turn-open True :turn-refs #(input.ref)
                              :awaiting-first-input False)
              :sends #((user-line input))))

(defn inject [#^ DialogueState state #^ TurnInput input]
  "走っている手番へ入力を足す(同じ user の行 — CLI が次の道具の境界で読む)。手番の外なら None
   (書くと誰のでもない手番になる)。運命を追うのは CLI が msg_lifecycle_v1 を名乗った時だけ
   (名乗らない CLI で追うと queued のままの注入が result を飲み続けて手番が終わらない)。"
  (when (not state.in-flight) (return None))
  (setv tracked (if state.lifecycle (+ state.injections #((Injection input.ref "queued"))) state.injections))
  (Transition :state (replace state :injections tracked :turn-refs (+ state.turn-refs #(input.ref)))
              :sends #((user-line input))))

(defn interrupt [#^ DialogueState state #^ str request-id]
  "止める: 手番の外なら None。既に求めていれば何も書かない。まだ読まれていない注入が在り CLI が interrupt_receipt_v1 を
   名乗ったなら control_request interrupt(request-id で答えを結ぶ)、それ以外は SIGINT。"
  (cond
    (not state.in-flight) None
    (not (isinstance state.stop NoStop)) (Transition :state state)
    (and (queued-refs state) state.interrupt-receipt)
      (Transition :state (replace state :stop (StopControl :request-id request-id))
                  :sends #((interrupt-request-line request-id)))
    True (Transition :state (replace state :stop (StopSignal)) :signal True)))

(defn answer-permission [#^ DialogueState state #^ str request-id answer]
  "許可の問いに答える。答え待ちに無い request-id なら None。"
  (setv asked (lfor request state.permissions :if (= request.request-id request-id) request))
  (when (not asked) (return None))
  (setv request (get asked 0))
  (setv response
        (if (isinstance answer Allow)
            {"behavior" "allow"
             "updatedInput" (if (is answer.updated-input None) request.input answer.updated-input)}
            {"behavior" "deny" "message" answer.message}))
  (Transition :state (replace state :permissions (tuple (gfor known state.permissions
                                                              :if (!= known.request-id request-id) known)))
              :sends #((permission-response-line request-id response))))

(defn close-session [#^ DialogueState state]
  "会話を閉じる: 走っている手番は Interrupted(読まれていない注入は捨てた側)で終わる。"
  (if state.in-flight
      (ended state (Interrupted :process-kept False :dropped-refs (queued-refs state)))
      (Transition :state (closed-turn state))))

(defn on-exit [#^ DialogueState state #^ (| int None) exit-code #^ str stderr-tail]
  "process が降りた(stdout の EOF の後)。手番の途中なら: 止めるを求めていれば Interrupted、注入を待って飲んだ result が
   在ればその result の終わり、どちらでもなければ BackendLost(終わりの行を読む前に process が消えた)。"
  (cond
    (not state.in-flight) (Transition :state state)
    (not (isinstance state.stop NoStop))
      (ended state (Interrupted :process-kept False :dropped-refs (queued-refs state)))
    (is-not state.deferred-result None)
      (ended state (end-of-result state.deferred-result state) :priced-by state.deferred-result)
    True
      (ended state (BackendLost :detail (.format "process exited with code {} before the turn ended{}" exit-code
                                                 (if stderr-tail (+ ": " stderr-tail) ""))))))


;; --- 遷移(stdout の 1 行) ------------------------------------------------------------------------

(defn on-init [#^ DialogueState state #^ Init init]
  (setv capabilities init.capabilities)
  (setv session-id init.session-id)
  (Transition :state (replace state :cli-turn-open True
                              :session-id (or session-id state.session-id)
                              :lifecycle (in LIFECYCLE-CAPABILITY capabilities)
                              :interrupt-receipt (in INTERRUPT-RECEIPT-CAPABILITY capabilities))
              :session-id (or session-id None)))

(defn on-lifecycle [#^ DialogueState state #^ InputFate input-fate]
  "足した入力の運命を写す。注入を待って result を飲んだ後に、その注入が走らずに終わり(cancelled / discarded / refused)、
   ほかに読まれていない注入も無ければ、手番は飲んだ result で終わる。"
  (setv ref input-fate.ref)
  (setv fate input-fate.state)
  (when (or (not ref) (not-in fate INPUT-FATES) (not (knows-injection state ref)))
    (return (Transition :state state)))
  (setv moved (replace state :injections (set-fate state.injections ref fate)))
  (if (and moved.in-flight (not moved.cli-turn-open)
           (in fate INPUT-FATE-TERMINAL) (!= fate "completed")
           (not (queued-refs moved)))
      (if (is-not moved.deferred-result None)
          (ended moved (end-of-result moved.deferred-result moved) :priced-by moved.deferred-result)
          (ended moved (Failed :detail (.format "injected input {} was {}" ref fate) :input-refs moved.turn-refs)))
      (Transition :state moved)))

(defn on-control-response [#^ DialogueState state #^ ControlResponse response]
  "止めるの答え: success なら still_queued を覚える。error なら control_request は効かなかった — SIGINT へ倒す。"
  (setv stop state.stop)
  (when (not (and (isinstance stop StopControl) (= response.request-id stop.request-id)))
    (return (Transition :state state)))
  (if (= response.subtype "success")
      (Transition :state (replace state :stop (replace stop :still-queued response.still-queued)))
      (Transition :state (replace state :stop (StopSignal)) :signal True)))

(defn on-permission-request [#^ DialogueState state #^ PermissionRequested request]
  (Transition :state (replace state :permissions (+ state.permissions #(request)))))

(defn on-result [#^ DialogueState state #^ TurnResult result]
  "result の行(規則は冒頭)。手番の途中に読んだ result の行は、CLI が自分で起こした手番の物も usage を手番に足す
   (累積の額の差にはどの行の分も入るので、usage も同じ行の集まりで数える)。"
  (when (not state.in-flight)
    (return (Transition :state state)))
  (setv counted (replace state :turn-usage (+ state.turn-usage result.usage)
                               :turn-windows (run (merged-windows state.turn-windows result.model-windows))))
  (when (in result.origin-kind CLI-OWN-TURN-ORIGINS)
    (return (Transition :state counted)))
  (setv open-closed (replace counted :cli-turn-open False))
  (setv queued (queued-refs open-closed))
  (setv stop open-closed.stop)
  (cond
    (isinstance stop StopSignal)
      (ended open-closed (Interrupted :process-kept False :dropped-refs queued) :priced-by result :retire StopReason.INTERRUPT-SIGNAL)
    (isinstance stop StopControl)
      (do
        (setv survivors (if (is stop.still-queued None) queued
                            (tuple (gfor ref queued :if (in ref stop.still-queued) ref))))
        (setv dropped (tuple (gfor ref queued :if (not-in ref survivors) ref)))
        (if survivors
            (Transition :state (replace (closed-turn open-closed) :in-flight True :turn-refs survivors
                                        :injections (tuple (gfor ref survivors (Injection ref "queued")))
                                        :cost-mark result.cost-usd)
                        :end (with-last-call (Interrupted :process-kept True :surviving-refs survivors :dropped-refs dropped)
                                             open-closed)
                        :continues True)
            (ended open-closed (Interrupted :process-kept True :dropped-refs dropped) :priced-by result)))
    queued
      (Transition :state (replace open-closed :deferred-result result))
    True
      (ended open-closed (end-of-result result open-closed) :priced-by result)))

(defn on-assistant [#^ DialogueState state #^ AssistantMessage message]
  "assistant の行: 本体の会話(parent_tool_use_id が null)の行なら、その usage と model を手番の最後の呼びとして覚える(1 つの呼びの
   block ごとの行は同じ usage を名乗るので、最後の行で置き換えてよい)。subagent の行は覚えない — 会話の context の大きさは本体の呼びの
   入力の側で数えるため(#3744)。本体の行が error rate_limit(CLI が答えの代わりに出した限度の文)なら、口座の限度に当たった事実に
   その文を足す(#3983 — 限度の種類と戻る刻は拒まれた限度の行から)。"
  (cond
    (is-not message.parent-tool-use-id None) (Transition :state state)
    (= message.error ASSISTANT-ERROR-RATE-LIMIT)
      (Transition :state (replace state :last-call-usage message.usage :last-call-model message.model
                                  :limit-hit (replace (or state.limit-hit (AccountLimitHit)) :text message.text)))
    True (Transition :state (replace state :last-call-usage message.usage :last-call-model message.model))))

(defn on-rate-limit [#^ DialogueState state #^ RateLimit limit]
  "rate_limit_event の行: 拒まれた(status rejected)なら、口座の限度に当たった事実に限度の種類と戻る刻を置く(#3983 — 限度の文は
   限度の答えの行から)。許された行は何も変えない。"
  (if (= limit.status RATE-LIMIT-REJECTED)
      (Transition :state (replace state :limit-hit (replace (or state.limit-hit (AccountLimitHit)) :window limit.window
                                                                  :resets-at limit.resets-at)))
      (Transition :state state)))

(defk quiet-before-first-input [kind]
  {:pre [(: kind ClaudeLineKind)] :post [(: % bool)] :tags {:context "claude-code" :role "judgment"}}
  "入力を書かずに事前起動した process が最初の入力の前に出した行を、ターンの外の出力と数えずに通してよいかを判定するため(この 1 か所
   だけ — 広げない)。通すのは SessionStart の hook の開始と応答の行(HookNotice — 段階は閉じた語彙の 2 つ)だけ。本物の CLI を入力
   なしで起動して 30 秒待たせた計測では、入力の前の行はこの 2 行だけで、init・assistant・rate_limit_event の行は 0 本(model への要求も
   0 — init は入力の後に出る)。ほかの行はターンの外で model が動いた兆候になり得るので通さない(背景の仕事がターンの外で model を
   呼ばないための守り)。"
  (match kind
    (HookNotice :event "SessionStart") True
    _ False))

(defn on-record [#^ DialogueState state kind]
  "stdout の 1 行を読んだ遷移。kind = lines.hy が分類した行の型(ClaudeLineKind)— 状態機械が読む型の外は何もしない。
   host のターンの外で読んだ行は、作法の行(OUTSIDE-TURN-QUIET-KINDS)と、最初の入力を待つ間の SessionStart の hook の行
   (quiet-before-first-input)を除いて、process を停止する理由 OUTSIDE-TURN-OUTPUT(冒頭のコメント)。"
  (cond
    (and (not state.in-flight) (not (isinstance kind OUTSIDE-TURN-QUIET-KINDS))
         (not (and state.awaiting-first-input (run (quiet-before-first-input kind)))))
      (Transition :state state :retire StopReason.OUTSIDE-TURN-OUTPUT)
    (isinstance kind Init) (on-init state kind)
    (isinstance kind InputFate) (on-lifecycle state kind)
    (isinstance kind ControlResponse) (on-control-response state kind)
    (isinstance kind PermissionRequested) (on-permission-request state kind)
    (isinstance kind TurnResult) (on-result state kind)
    (isinstance kind AssistantMessage) (on-assistant state kind)
    (isinstance kind RateLimit) (on-rate-limit state kind)
    True (Transition :state state)))
