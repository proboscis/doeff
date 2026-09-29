;;; 本物の最新の値の答え手 process-latest-handler だけの性質(agora-redesign #1440)。契約の性質は test_latest_contract.hy。
;;;
;;;   * 同じ名前で入れた答え手どうしは、別の run でも別の thread でも 1 つの置き場を読み書きする(処理ループの run が置き、probe の別の run が読む)
;;;   * 名前が違えば置き場も別
;;; 置き場は検の process の間ずっと残るので、検ごとに別の名前を使う。
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])
(import doeff_core_effects.process_latest [process-latest-handler])


(defrecord Progress
  "検の値: 同期の進み。"
  (#^ str waiting))


(defk publish [value]
  {:pre [(: value Progress)] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "value を置く(別の run で走らせる Program)。"
  (<- (PublishLatest value))
  None)


(defk read-progress []
  {:pre [] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Progress の最新の値を読む(別の run・別の thread で走らせる Program)。"
  (<- value (ReadLatest Progress))
  value)


(deftest test-two-runs-with-the-same-name-share-one-board
  (run (with_handlers [(state) (process-latest-handler "process-latest-shared")] (publish (Progress :waiting "畳んだ"))))
  (val value (run (with_handlers [(state) (process-latest-handler "process-latest-shared")] (read-progress))))
  (assert (= value (Progress :waiting "畳んだ")) (.format "別の run が置いた値の読みが {!r}" value)))


(deftest test-a-run-in-another-thread-reads-the-same-board
  (run (with_handlers [(state) (process-latest-handler "process-latest-thread")] (publish (Progress :waiting "畳んだ"))))
  (val seen [])
  (val reader (threading.Thread
                 :target (fn [] (.append seen (run (with_handlers [(state) (process-latest-handler "process-latest-thread")] (read-progress)))))))
  (.start reader)
  (.join reader 10.0)
  (assert (= seen [(Progress :waiting "畳んだ")]) (.format "別の thread の読みが {!r}" seen)))


(deftest test-different-names-do-not-share
  (run (with_handlers [(state) (process-latest-handler "process-latest-left")] (publish (Progress :waiting "畳んだ"))))
  (val value (run (with_handlers [(state) (process-latest-handler "process-latest-right")] (read-progress))))
  (assert (is value None) (.format "別の名前の置き場の読みが {!r}" value)))
