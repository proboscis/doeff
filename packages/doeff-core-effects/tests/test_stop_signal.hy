(require doeff-hy.macros [defk defhandler deftest <-])

;;; 止めの合図の handler の本物(os-signal-stop-handler)だけの性質。本物と fake(scripted-stop-handler)が同じ答えを返すべき性質
;;; (合図の前は None・合図の後は理由・最初の理由を保つ・外側に state の handler が要る)は契約テスト test_stop_signal_contract.hy
;;; (agora-redesign #1159)。

(import pytest)
(import doeff [run with_handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.stop_signal_effects [AwaitStop RaiseStop])
(import doeff_core_effects.scheduler [CreatePromise SchedulerDeadlockError Spawn Wait scheduled])
(import doeff_core_effects.stop_signal_handlers [os-signal-stop-handler scripted-stop-handler])


(defk raise-under-os []
  {:pre [] :post [(: % None)] :tags {:context "stop-signal-test" :role "program"}}
  "本物の handler の下で RaiseStop を出す Program。"
  (<- (RaiseStop "scenario"))
  None)


(deftest test-counterexample-raise-stop-is-not-answered-by-the-os-handler
  ;; RaiseStop は scripted の口だけ — 本物の signal の handler は答えない(本番で筋書きの止めを起こせない)。
  (with [(pytest.raises UnhandledEffect :match "RaiseStop")]
    (run (with_handlers [(state) os-signal-stop-handler] (raise-under-os)))))


;; --- AwaitStop の反例(agora-redesign #2205)-----------------------------------------------------------------------------
;; 止めの合図で寝ている待ちを起こさない handler(RaiseStop で理由を覚えるだけ)だと、AwaitStop で寝た task は止めの後も起きず、
;; scheduler は待ちの行き詰まりで止まる。scripted-stop-handler が RaiseStop で promise を完了するのは、この形を作らないため。

(defhandler forgetful-stop-handler
  "反例: RaiseStop で理由を覚えるが、AwaitStop で寝ている待ちを起こさない(待ちは別の promise に寝る)。"
  (session var raised None)
  (RaiseStop [reason]
    (:= raised reason)
    (resume None))
  (AwaitStop []
    (when (is-not raised None)
      (return (resume raised)))
    (<- never (CreatePromise))
    (<- reason str (Wait never.future))
    (resume reason)))


(defk await-stop-once []
  {:pre [] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "AwaitStop を 1 度待つ task の中身。"
  (<- reason str (AwaitStop))
  reason)


(defk stop-while-waiting []
  {:pre [] :post [(: % str)] :tags {:context "stop-signal-test" :role "program"}}
  "AwaitStop で寝た task がある間に止めを起こし、その task の答えを待つ。"
  (<- waiter (Spawn (await-stop-once)))
  (<- step (Spawn (raise-under-os)))
  (<- (Wait step))
  (<- reason str (Wait waiter))
  reason)


(deftest test-counterexample-a-stop-that-does-not-wake-the-wait-stalls-the-run
  (with [(pytest.raises SchedulerDeadlockError)]
    (run (scheduled (with_handlers [(state) forgetful-stop-handler] (stop-while-waiting)))))
  ;; 正しい fake では同じ Program が止めの理由で起きる。
  (assert (= (run (scheduled (with_handlers [(state) scripted-stop-handler] (stop-while-waiting)))) "scenario")))
