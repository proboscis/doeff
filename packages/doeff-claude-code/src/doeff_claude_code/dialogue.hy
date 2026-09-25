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
;;; - 手番の終わり = process を降ろす(close)。手番の境界の所有者は host — CLI に result の後の手番を持たせない(#517)。
;;;   control_request で止めて注入が生き残った時だけ、process は生き残った入力の手番を走らせてから降りる(continues)。
;;;
;;; stdout の行は lines.hy が分類した型(ClaudeLineKind)で受ける — JSON の読みは lines.hy の 1 か所だけ。
;;; stdin へ書く行の綴り(JSON へ書く境界)はこの file の dumps の 1 か所。
(import dataclasses [dataclass replace])
(import json)
(import typing [NamedTuple])
(import doeff_hy.frozen [thaw-json])
(import doeff_claude_code.values [TurnInput Allow Deny])
(import doeff_claude_code.lines [Completed Failed Interrupted BackendLost Init InputFate ControlResponse PermissionRequested
                                 TurnResult INPUT-FATES INPUT-FATE-TERMINAL])

;; CLI が system/init の capabilities で名乗る能力(実測 2.1.282)。
(setv LIFECYCLE-CAPABILITY "msg_lifecycle_v1")
(setv INTERRUPT-RECEIPT-CAPABILITY "interrupt_receipt_v1")
;; CLI が自分で起こした手番の result の origin.kind(本文の手番の result は origin を持たない)。
(setv CLI-OWN-TURN-ORIGINS (frozenset #{"task-notification"}))


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
   permissions = 答え待ちの許可の問い(PermissionRequested)。"
  (setv #^ str session-id "")
  (setv #^ bool in-flight False)
  (setv #^ bool cli-turn-open False)
  (setv #^ bool lifecycle False)
  (setv #^ bool interrupt-receipt False)
  (setv #^ (get tuple #(str ...)) turn-refs #())
  (setv #^ (get tuple #(Injection ...)) injections #())
  (setv #^ (| NoStop StopSignal StopControl) stop (NoStop))
  (setv #^ (| TurnResult None) deferred-result None)
  (setv #^ (get tuple #(PermissionRequested ...)) permissions #()))

(defclass [(dataclass :frozen True)] Transition []
  "遷移の答え: 次の状態・stdin へ書く行・host の手番の終わり(無ければ None)・close(この行で process を降ろす)・
   continues(終わった手番の後に、生き残った入力の手番が同じ process で続く)・signal(SIGINT を送る)・
   session-id(この行で知った会話の id)。"
  (#^ DialogueState state)
  (setv #^ (get tuple #(str ...)) sends #())
  (setv #^ (| Completed Failed Interrupted BackendLost None) end None)
  (setv #^ bool close False)
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
  "result の行 → Completed | Failed。誤りの detail は CLI が名乗った文ちょうど(無ければ subtype)。"
  (setv refs (result-input-refs result state))
  (if result.is-error
      (Failed :detail (or (.strip result.result-text) result.subtype "error")
              :api-error-status result.api-error-status
              :terminal-reason result.terminal-reason
              :input-refs refs)
      (Completed :result-text result.result-text
                 :usage result.usage
                 :cost-usd result.cost-usd
                 :input-refs refs)))

(defn #^ DialogueState closed-turn [#^ DialogueState state]
  "host の手番を閉じた状態(会話の id と CLI の能力は保つ)。"
  (replace state :in-flight False :cli-turn-open False :turn-refs #() :injections #() :stop (NoStop)
           :deferred-result None :permissions #()))

(defn ended [#^ DialogueState state end [close True]]
  (Transition :state (closed-turn state) :end end :close close))


;; --- 遷移(呼び手の操作) -----------------------------------------------------------------------------

(defn begin-turn [#^ DialogueState state #^ TurnInput input]
  "host の手番を始める: 入力の行を stdin へ。"
  (Transition :state (replace (closed-turn state) :in-flight True :cli-turn-open True :turn-refs #(input.ref))
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
      (ended state (Interrupted :dropped-refs (queued-refs state)) :close False)
      (Transition :state (closed-turn state))))

(defn on-exit [#^ DialogueState state #^ (| int None) exit-code #^ str stderr-tail]
  "process が降りた(stdout の EOF の後)。手番の途中なら: 止めるを求めていれば Interrupted、注入を待って飲んだ result が
   在ればその result の終わり、どちらでもなければ BackendLost(終わりの行を読む前に process が消えた)。"
  (cond
    (not state.in-flight) (Transition :state state)
    (not (isinstance state.stop NoStop))
      (ended state (Interrupted :dropped-refs (queued-refs state)) :close False)
    (is-not state.deferred-result None)
      (ended state (end-of-result state.deferred-result state) :close False)
    True
      (ended state (BackendLost :detail (.format "process exited with code {} before the turn ended{}" exit-code
                                                 (if stderr-tail (+ ": " stderr-tail) "")))
             :close False)))


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
      (ended moved (if (is-not moved.deferred-result None)
                       (end-of-result moved.deferred-result moved)
                       (Failed :detail (.format "injected input {} was {}" ref fate) :input-refs moved.turn-refs)))
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
  "result の行(規則は冒頭)。"
  (when (or (in result.origin-kind CLI-OWN-TURN-ORIGINS) (not state.in-flight))
    (return (Transition :state state)))
  (setv open-closed (replace state :cli-turn-open False))
  (setv queued (queued-refs open-closed))
  (setv stop open-closed.stop)
  (cond
    (isinstance stop StopSignal)
      (ended open-closed (Interrupted :dropped-refs queued))
    (isinstance stop StopControl)
      (do
        (setv survivors (if (is stop.still-queued None) queued
                            (tuple (gfor ref queued :if (in ref stop.still-queued) ref))))
        (setv dropped (tuple (gfor ref queued :if (not-in ref survivors) ref)))
        (if survivors
            (Transition :state (replace (closed-turn open-closed) :in-flight True :turn-refs survivors
                                        :injections (tuple (gfor ref survivors (Injection ref "queued"))))
                        :end (Interrupted :surviving-refs survivors :dropped-refs dropped)
                        :continues True)
            (ended open-closed (Interrupted :dropped-refs dropped))))
    queued
      (Transition :state (replace open-closed :deferred-result result))
    True
      (ended open-closed (end-of-result result open-closed))))

(defn on-record [#^ DialogueState state kind]
  "stdout の 1 行を読んだ遷移。kind = lines.hy が分類した行の型(ClaudeLineKind)— 状態機械が読む型の外は何もしない。"
  (cond
    (isinstance kind Init) (on-init state kind)
    (isinstance kind InputFate) (on-lifecycle state kind)
    (isinstance kind ControlResponse) (on-control-response state kind)
    (isinstance kind PermissionRequested) (on-permission-request state kind)
    (isinstance kind TurnResult) (on-result state kind)
    True (Transition :state state)))
