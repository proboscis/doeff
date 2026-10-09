;;; memory の答え手 memory-latest-handler だけの性質(本物と同じ契約は test_latest_contract.hy・本物だけの性質は test_process_latest.hy)。
;;;
;;;   * 置く・読む 1 回ごとの session の出し入れ(state へ行く Get・Put)は 1 回 — 保存先は 1 つの session の値で、節は中身を書き換える。
;;;     置くたびに session の値を 2 つ取り出す形・値を置き直す形は、置く回数の多い使い手の検の歩数をその分だけ増やす(呼び鈴の組を
;;;     2 つ目の session の値に持った 7d38d5bce の後、agora-controllers の画面とターンの模擬の検が +4,732・+4,852 歩)。
(require doeff-hy.macros [defk defhandler deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff [run with_handlers])
(import doeff_core_effects.effects [Get Put])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])
(import doeff_core_effects.memory_latest [memory-latest-handler])


(defrecord Progress
  "検の値: 同期の進み。"
  (#^ str waiting))


;; 計器の数を置く state の鍵(計器はこの鍵の出し入れを数えない)。
(val COUNTED "test-memory-latest/session-accesses")
(val TIMES 10)


(defk counted-once []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "計器の数(state の鍵 COUNTED)を 1 つ進めるため。"
  (<- seen int (Get COUNTED))
  (<- (Put COUNTED (+ seen 1)))
  None)


(defhandler session-accesses
  "検の計器: 内側の答え手から state へ行く Get・Put を数え、そのまま外の state へ渡す。"
  (Get [key]
    (when (!= key COUNTED)
      (<- (counted-once)))
    (reperform effect))
  (Put [key value]
    (when (!= key COUNTED)
      (<- (counted-once)))
    (reperform effect)))


(defk touches-of-publish-and-read [times]
  {:pre [(: times int)] :post [(: % int)] :tags {:context "latest-test" :role "program"}}
  "保存先を作る 1 度目の置きの後、置く・読むを times 回ずつ行い、その間に state へ行った Get・Put の数を答えるため。"
  (<- (Put COUNTED 0))
  (<- (PublishLatest (Progress :waiting "作る")))
  (<- before int (Get COUNTED))
  (for [n (range times)]
    (<- (PublishLatest (Progress :waiting (str n))))
    (<- (ReadLatest Progress)))
  (<- after int (Get COUNTED))
  (- after before))


(deftest test-each-publish-and-read-touches-the-session-once
  (val touched (run (with_handlers [(state) session-accesses memory-latest-handler] (touches-of-publish-and-read TIMES))))
  (assert (= touched (* 2 TIMES))
          (.format "置く {} 回と読む {} 回の session の出し入れが {} 回(1 回ずつなら {})" TIMES TIMES touched (* 2 TIMES))))
