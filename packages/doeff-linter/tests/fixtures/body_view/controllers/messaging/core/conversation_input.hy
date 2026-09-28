;;; 会話の入力の処理ステージの手順(agora-redesign #808 入力の便 — 5 層の core・旧 controllers/conversation/input_stage.hy と
;;; handlers_input.hy の係の 1 周と常駐)。判断は conversation_input_judgment.hy の judge、外へ求める事は intent の effect
;;; (列 intake = ReadIntake・SettleIntake — controllers/messaging/intent/intake.hy・入力の行 = ReadInput・WriteInput —
;;; controllers/messaging/intent/conversation_input.hy)と時計(doeff-time の GetTime と共有の換算 epoch-seconds-of-time・doeff-time の Delay)。
;;;
;;; 1 周(input-round): 列を前の位置から読み足す → 結末の無い input-* の要求を畳む → 名乗りの合わない要求は断りの結末を積む → 残りを
;;; 受けた順に撃つ(上限 INPUT-REQUESTS-PER-ROUND 件 — 残りは次の周)。
;;; 要求 1 つの手順(run-input-request): 名指した行を読む → 判断 → 書き(高々 1 つ)→ 結末を積む。
;;;   * 書きが競合(WriteConflict)→ 読み直して判じ直す(1 回だけ — 手番の「取った」との競合はここで 1 つに決まる: 取られていれば
;;;     2 度目の判断は input-not-pending)。2 度目も競合なら結末を積まずに次の周へ。
;;;   * 書きを置き場が断った(WriteRefused)→ rejected{writer-refused: 断りの逐語}。
;;;   * 行が読めない・書きが届かない・結末を積めない → 結末を積まずに次の周へ(読めないを「無い」と数えない)。
;;;   * 見送った要求と同じ入力を名指す後ろの要求はこの周では撃たない(send → edit → withdraw の順を守る)。
;;; どの手も冪等(send は行が在れば done・edit は最後の直しが同じ editId なら done・withdraw は既に withdrawn なら done)なので、書きの後・
;;; 結末の前に落ちても次の周が同じ終状態へ寄せる。
;;; 結末を積んだ要求の requestId は finished に持つ(列の読みに結末の出来事が現れるまでの周に同じ要求を撃ち直さない)。finished は畳みに
;;; 残っている要求だけ保つ。畳みは結末の無い要求だけを残す(常駐の畳みが列の長さだけ育たない)。
;;;
;;; 名乗りの検め: 本文の by を名乗ってよいのは置き場が認証した書き手自身か受け口だけ — 判定の 1 点は共有の core の identity-problem
;;; (controllers/shared/core/intake_rules.hy — 郵便の旧い置き場 intake_stream を経由せず、定義の在る所から直に読む)。断りの語
;;; identity-not-delegated と担い手の名 agora-conversation は列 intake の語彙(controllers/messaging/intent/intake_types.hy — 同じ日に
;;; 振り分けの表の旧い置き場 intake_model.hy・intake_routes.hy から移した)から読む。
;;;
;;; 置き場の移り: 2026-09-28 に controllers/core/conversation_input.hy から移した(agora-redesign #797)。旧い module は同じ名で出す。
(require doeff-hy.macros [defk <- val var])
(import doeff_time [GetTime Delay])
(import datetime [datetime])
;; いまの時刻は doeff-time の GetTime で読み、契約の物差しへの換算は共有の置き場の式で読む(agora-redesign #797)。
(import controllers.shared.core.time_values [epoch-seconds-of-time])
(import controllers.shared.core.intake_rules [identity-problem])
(import controllers.messaging.intent.intake_types [REASON-IDENTITY ROUTE-INPUT])
(import controllers.messaging.intent.intake [IntakeRequested IntakeConcluded IntakePage IntakeUnreachable IntakeSettlement
                                             IntakeSettleLanded IntakeSettleRefused IntakeOutcome ReadIntake SettleIntake])
(import controllers.messaging.intent.conversation_input [InputRow InputAbsent InputUnreadable ReadInput WriteInput
                                                         WriteLanded WriteConflict WriteRefused WriteUnreachable InputDone InputRejected])
(import controllers.messaging.core.conversation_input_types [InputRequest Judgment RunOutcome InputQueue InputTally InputRound])
(import controllers.messaging.core.conversation_input_judgment [INPUT-REQUESTS-PER-ROUND REJECT-WRITER-REFUSED input-request? request-of
                                                                target-of judge settlement-of])

(val MODULE-TAGS {:context "messaging" :role "program"})

;; 競合の読み直しの回数(最初の 1 回 + やり直し 1 回)。
(val ATTEMPTS 2)


(defk judged [request]
  {:pre [(: request InputRequest)] :post [(: % (| Judgment None))]
   :effects [ReadInput] :tags {:context "messaging" :role "program"}}
  "名指した行を読んで判じるため。読めなかった(届かない)= None(この周は見送る)。"
  (<- target (| str None) (target-of request))
  (var row None)
  (when (is-not target None)
    (<- answer (| InputRow InputAbsent InputUnreadable) (ReadInput target))
    (match answer
      (InputUnreadable) (return None)
      (InputRow) (:= row answer)
      (InputAbsent) None))
  (<- judgment Judgment (judge request row))
  judgment)


(defk outcome-of [request]
  {:pre [(: request InputRequest)] :post [(: % (| InputDone InputRejected None))]
   :effects [ReadInput WriteInput] :tags {:context "messaging" :role "program"}}
  "要求 1 つを判じて書き、積む結末を決めるため。None = 結末を積まずに次の周へ(読めない・届かない・競合が続いた)。"
  (for [_ (range ATTEMPTS)]
    (<- judgment (| Judgment None) (judged request))
    (when (is judgment None)
      (return None))
    (when (is judgment.write None)
      (return judgment.outcome))
    (<- answer (| WriteLanded WriteConflict WriteRefused WriteUnreachable) (WriteInput judgment.write))
    (match answer
      (WriteLanded) (return judgment.outcome)
      (WriteRefused) (return (InputRejected :reason REJECT-WRITER-REFUSED :detail answer.detail))
      (WriteUnreachable) (return None)
      (WriteConflict) None))
  None)


(defk run-input-request [request]
  {:pre [(: request InputRequest)] :post [(: % RunOutcome)]
   :effects [ReadInput WriteInput SettleIntake] :tags {:context "messaging" :role "program"}}
  "要求 1 つを実体化して結末を積むため。答え = done / rejected(結末を積めた)/ deferred(次の周へ)。"
  (<- outcome (| InputDone InputRejected None) (outcome-of request))
  (when (is outcome None)
    (return RunOutcome.DEFERRED))
  (<- settlement IntakeSettlement (settlement-of request.request-id ROUTE-INPUT outcome))
  (<- settled (| IntakeSettleLanded IntakeSettleRefused IntakeUnreachable) (SettleIntake settlement))
  (match settled
    (IntakeSettleLanded) (match outcome
                           (InputDone) RunOutcome.DONE
                           (InputRejected) RunOutcome.REJECTED)
    _ RunOutcome.DEFERRED))
