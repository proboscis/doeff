;; 手入れの「消す前に送る」(maintenance.hy の頭の註 — card ki-c32e43651f2e・2026-10-11 11:21 に使い手の記録の Postgres の
;; disk が 100% になった事故の根の直し): 送りの関数を持つ計画の手入れ 1 回は、送りの答えの番号を刈りの上限に渡し、送り先に届かなかった
;; 回は刈らない。送りを持たない計画は上限なしで刈る(今までと同じ)。置き場の handler は撃たれた PruneChanges を控えるだけの顔。
(require doeff-hy.macros [deftest defhandler defk <- val var])
(import collections.abc [Callable])
(import doeff [with_handlers])
(import doeff_records.values [Unreachable])
(import doeff_records.maintenance [ArchivedThrough MaintenanceReport PruneChanges Pruned SweepExpired Swept maintenance-tick])


(defhandler recorder [#^ list asked]
  ;; 引数に残す理由: asked は検が読む控え — 置き場の顔が撃たれた PruneChanges をそこへ積む。回収は何もしない。
  (SweepExpired []
    (resume (Swept 0)))
  (PruneChanges [keep-seconds upto-seq]
    (.append asked effect)
    (resume (Pruned (or upto-seq 99) 1))))


(defk pruned-asks [archive]
  {:pre [(: archive (| Callable None))] :post [(: % tuple)]}
  "送りの関数 archive の計画で手入れを 1 回撃ち、#(答え 撃たれた PruneChanges の並び) を返すため(置き場は控えるだけの顔)。"
  (val asked [])
  (<- report (with_handlers [(recorder asked)] (maintenance-tick 60.0 archive)))
  #(report (tuple asked)))


(defk archived-through [seq]
  {:pre [(: seq int)] :post [(: % Callable)]}
  "送りの関数(答え = ArchivedThrough seq)を作るため。"
  (defk archive [keep-seconds]
    {:pre [(: keep-seconds float)] :post [(: % ArchivedThrough)]}
    "送り先に seq まで在ると答える送り。"
    (ArchivedThrough seq))
  archive)


(defk unreachable-archive [keep-seconds]
  {:pre [(: keep-seconds float)] :post [(: % Unreachable)]}
  "送り先に届かない送り。"
  (Unreachable "cold に届かない"))


(deftest test-the-tick-prunes-only-up-to-the-archived-seq
  ;; 失敗ケース = 送りの答えを刈りに渡さない(送っていない古い変更まで消す)。
  (<- archive (archived-through 7))
  (<- seen tuple (pruned-asks archive))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #((PruneChanges 60.0 :upto-seq 7))) asked)
  (assert (and (= report.archived (ArchivedThrough 7)) (= report.pruned (Pruned 7 1))) report))


(deftest test-the-tick-does-not-prune-when-the-archive-is-unreachable
  ;; 失敗ケース = 送り先に届かない回にも時の境だけで刈る(送れていない変更が消える)。
  (<- seen tuple (pruned-asks unreachable-archive))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #()) asked)
  (assert (and (isinstance report.archived Unreachable) (isinstance report.pruned Unreachable)) report))


(deftest test-a-plan-without-archive-prunes-without-a-bound
  (<- seen tuple (pruned-asks None))
  (val report (get seen 0))
  (val asked (get seen 1))
  (assert (= asked #((PruneChanges 60.0))) asked)
  (assert (is report.archived None) report))
