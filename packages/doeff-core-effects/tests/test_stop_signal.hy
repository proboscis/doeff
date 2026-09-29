(require doeff-hy.macros [defk deftest <-])

;;; 止めの合図の handler の本物(os-signal-stop-handler)だけの性質。本物と fake(scripted-stop-handler)が同じ答えを返すべき性質
;;; (合図の前は None・合図の後は理由・最初の理由を保つ・外側に state の handler が要る)は契約テスト test_stop_signal_contract.hy
;;; (agora-redesign #1159)。

(import pytest)
(import doeff [run with_handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.stop_signal_effects [RaiseStop])
(import doeff_core_effects.stop_signal_handlers [os-signal-stop-handler])


(defk raise-under-os []
  {:pre [] :post [(: % None)] :tags {:context "stop-signal-test" :role "program"}}
  "本物の handler の下で RaiseStop を出す Program。"
  (<- (RaiseStop "scenario"))
  None)


(deftest test-counterexample-raise-stop-is-not-answered-by-the-os-handler
  ;; RaiseStop は scripted の口だけ — 本物の signal の handler は答えない(本番で筋書きの止めを起こせない)。
  (with [(pytest.raises UnhandledEffect :match "RaiseStop")]
    (run (with_handlers [(state) os-signal-stop-handler] (raise-under-os)))))
