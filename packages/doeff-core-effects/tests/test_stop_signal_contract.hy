;;; 止めの合図の契約テスト — 同じ効果 StopRequested に答える本物(os-signal-stop-handler)と fake(scripted-stop-handler)が、
;;; 同じ deftest を通る(agora-redesign #1159)。解釈器の組み立ては stop_contract_handlers.hy。
;;;
;;; 合図を起こす手段(SendStop — 本物は process へ本当の signal、fake は RaiseStop)だけが解釈器ごとに違い、読む側の性質を共通の
;;; deftest で見る:
;;;   * 合図の無い間 StopRequested は None
;;;   * 合図の後は理由の文字列(本物の箱と同じ "signal <番号>")
;;;   * 最初の理由を保つ(2 度目の合図で変わらない)
;;;   * 外側に state の handler が要る(無ければ session の値の置き場が無く、大きな音で落ちる)
;;; 本物だけの性質(RaiseStop に答えない)は test_stop_signal.hy。
(require doeff-hy.macros [defk deftest <-])
(import signal)
(import pytest)
(import doeff [run with_handlers])
(import doeff_vm [UnhandledEffect])
(import doeff_core_effects.stop_signal_effects [StopRequested])
(import stop_contract_handlers [SendStop StopHandlerUnderTest signal-reason])


(defk ask-twice []
  {:pre [] :post [(: % tuple)] :tags {:context "stop-signal-test" :role "program"}}
  "StopRequested を 2 度読む(外側に state の handler が無い反例で走らせる Program)。"
  (<- first (| str None) (StopRequested))
  (<- second (| str None) (StopRequested))
  #(first second))


(deftest test-no-stop-before-any-signal
  {:interpreters ["os-signal" "scripted"]}
  (<- before (| str None) (StopRequested))
  (<- again (| str None) (StopRequested))
  (assert (is before None) (.format "合図の前の StopRequested が {!r}" before))
  (assert (is again None) (.format "合図の前に 2 度読んだ StopRequested が {!r}" again)))


(deftest test-a-signal-is-read-as-its-reason
  {:interpreters ["os-signal" "scripted"]}
  (<- before (| str None) (StopRequested))
  (<- (SendStop signal.SIGTERM))
  (<- after (| str None) (StopRequested))
  (<- wanted str (signal-reason signal.SIGTERM))
  (assert (is before None) (.format "合図の前の StopRequested が {!r}" before))
  (assert (= after wanted) (.format "SIGTERM の後の StopRequested が {!r}(期待 {!r})" after wanted)))


(deftest test-the-first-reason-is-kept
  {:interpreters ["os-signal" "scripted"]}
  (<- (StopRequested))
  (<- (SendStop signal.SIGINT))
  (<- (SendStop signal.SIGTERM))
  (<- after (| str None) (StopRequested))
  (<- later (| str None) (StopRequested))
  (<- wanted str (signal-reason signal.SIGINT))
  (assert (= after wanted) (.format "SIGINT・SIGTERM の順の後の StopRequested が {!r}(最初の {!r} を保つ)" after wanted))
  (assert (= later wanted) (.format "もう一度読んだ StopRequested が {!r}" later)))


(deftest test-a-state-handler-must-be-outside
  {:interpreters ["os-signal" "scripted"]}
  (<- handler (StopHandlerUnderTest))
  (with [(pytest.raises UnhandledEffect :match "stop-handler/")]
    (run (with_handlers [handler] (ask-twice)))))
