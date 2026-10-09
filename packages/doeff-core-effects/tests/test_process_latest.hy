;;; 本物の最新の値の答え手 process-latest-handler だけの性質(agora-redesign #1440)。契約の性質は test_latest_contract.hy。
;;;
;;;   * 同じ名前で入れた答え手どうしは、別の run でも別の thread でも 1 つの置き場を読み書きする(処理ループの run が置き、probe の別の run が読む)
;;;   * 名前が違えば置き場も別
;;;   * 別の thread の run が置いた値で、この run の変わるまでの待ち(AwaitLatest)が起きる・取り消された待ちは呼び鈴を残さない
;;; 置き場は検の process の間ずっと残るので、検ごとに別の名前を使う。
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import threading)
(import doeff [run with_handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects.process_latest [process-latest-handler BELLS])
(import doeff_core_effects.scheduler [scheduled Spawn Wait Cancel CreatePromise CompletePromise])


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


;; --- 変わるまでの待ち(AwaitLatest — agora-redesign #4296)の本物だけの性質 -----------------------------------------------------
;;   * 別の thread の run が置いた値で、この run の待ちが起きる(処理ループの run が置いた断面で、probe の別の run の待ちが起きる形)
;;   * 取り消された待ちは呼び鈴を残さない(process に 1 つの呼び鈴の保存先が空に戻る)

(defk awaited [seen]
  {:pre [(: seen (| Progress None))] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Progress の最新の値が seen と別の物になるまで待ち、その値を答える。"
  (<- value (AwaitLatest Progress seen))
  value)


(deftest test-a-publish-from-another-thread-wakes-the-await
  ;; 失敗ケース = 置く側が呼び鈴を鳴らさない(または外からの完了が scheduler を起こさない)形では、待ちが起きず join の上限で止まる。
  (val name "process-latest-await-thread")
  (val first (Progress :waiting "一覧"))
  (run (with_handlers [(state) (process-latest-handler name)] (publish first)))
  (val timer (threading.Timer 0.05 (fn [] (run (with_handlers [(state) (process-latest-handler name)] (publish (Progress :waiting "まとめた")))))))
  (setv timer.daemon True)
  (.start timer)
  (val seen [])
  (val waiter (threading.Thread
                 :target (fn [] (.append seen (run (scheduled (with_handlers [(state) (process-latest-handler name)] (awaited first))))))
                 :daemon True))
  (.start waiter)
  (.join waiter 10.0)
  (assert (= seen [(Progress :waiting "まとめた")]) (.format "別の thread が置いた後の待ちの答えが {!r}" seen)))


(defk let-others-run []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "先に spawn した task(待ちに入る・取り消しで巻き戻る)を回すため(後に spawn した task が終わるのを待つ — 時計を使わない)。"
  (<- promise (CreatePromise))
  (<- helper (Spawn (CompletePromise promise None)))
  (<- (Wait helper))
  None)


(defk cancelled-then-published [name first]
  {:pre [(: name str) (: first Progress)] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "待ちを spawn して呼び鈴の待ちに入れてから取り消し、その後に値を置くため(取り消しの callback が呼び鈴を外したかは呼び手が確かめる)。"
  (<- waiter (Spawn (awaited first)))
  (<- (let-others-run))
  (assert (= (len (.get (get BELLS name) Progress #())) 1) (.format "待ちに入った後の呼び鈴が {!r}" (get BELLS name)))
  (<- (Cancel waiter))
  ;; 取り消した task が巻き戻り終えるまで回す(root が先に返ると、巻き戻しが走りかけのまま残る)。
  (<- (let-others-run))
  None)


(deftest test-a-cancelled-await-leaves-no-bell
  ;; 失敗ケース = 取り消しの callback が無い形では、取り消した待ちの呼び鈴が保存先に残り、置くたびに待つ側の居ない約束を鳴らし続ける。
  (val name "process-latest-await-cancel")
  (val first (Progress :waiting "一覧"))
  (run (with_handlers [(state) (process-latest-handler name)] (publish first)))
  (run (scheduled (with_handlers [(state) (process-latest-handler name)] (cancelled-then-published name first))))
  (assert (= (.get (get BELLS name) Progress #()) #()) (.format "取り消した後の呼び鈴が {!r}" (get BELLS name))))
