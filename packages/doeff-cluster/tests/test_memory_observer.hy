;; 機体の memory の逼迫の観測: worker の観測のまとめ(本番と同じ local-host)が、機体の memory の見張りの入れ物
;; (MemoryCell.level)の様子を WorldView.memory-tight として読む(答え手 = worker/protocol/memory_observer)。CALM は偽・TIGHT と DANGER は真。
;; 判断の側の検(task を見送る)は test_policy.hy の末尾の節。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with-handlers])
(import doeff_cluster._worker.core.memory_rules [MemoryLevel])
(import doeff_cluster [ObserveWorld]
        doeff_cluster._worker.intent.worker_model [EnvDisk])
(import doeff_cluster [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveLeaving])
(import doeff_cluster._worker.protocol.memory_watch [MemoryCell])
(import doeff_cluster [memory-observer])
(import doeff_cluster [local-host])


(defhandler nothing-else-observed
  ;; 機体の memory のほかの観測は空。
  (ObserveCode [] (resume #()))
  (ObserveEnvs [] (resume #()))
  (ObserveProcesses [] (resume #()))
  (ObserveProbes [] (resume #()))
  (ObserveEnvDisk [] (resume (EnvDisk :free 10 :sweep-wanted False :pinned (frozenset))))
  (ObserveWarmChildren [] (resume #()))
  (ObserveLeaving [] (resume False)))


(deftest test-the-world-reads-the-watch-level-as-memory-tightness
  ;; 失敗ケース: 見張りの様子は見張りの入れ物の中にだけ在り、調整ループの観測に載っていなかった(worker は逼迫を知らずに task を起こした)。
  (val cell (MemoryCell))
  (val observe (with-handlers [(memory-observer cell) nothing-else-observed local-host] (ObserveWorld)))
  (assert (not (. (run observe) memory-tight)) "CALM(読む前の既定)は逼迫ではない")
  (setv cell.level MemoryLevel.TIGHT)
  (assert (. (run observe) memory-tight) "TIGHT は逼迫")
  (setv cell.level MemoryLevel.DANGER)
  (assert (. (run observe) memory-tight) "DANGER も逼迫(止めの最中に task を足さない)")
  (setv cell.level MemoryLevel.CALM)
  (assert (not (. (run observe) memory-tight)) "余裕が戻れば解ける"))
