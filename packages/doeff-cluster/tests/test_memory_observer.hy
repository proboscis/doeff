;; 機体の memory の逼迫の観測: worker の観測のまとめ(本番と同じ local-host)が、機体の memory の見張りの入れ物
;; (MemoryCell.level)の様子を WorldView.memory-tight として読む(答え手 = worker/protocol/memory_observer)。CALM は偽・TIGHT と DANGER は真。
;; 判断の側の検(task を見送る)は test_policy.hy の末尾の節。
(require doeff-hy.macros [defhandler deftest <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff [run with-handlers])
(import doeff_cluster._worker.core.memory_rules [MemoryLevel])
(import doeff_cluster [ObserveWorld]
        doeff_cluster._worker.intent.worker_model [EnvDisk])
(import doeff_cluster [ObserveProcesses ObserveCode ObserveEnvs ObserveEnvDisk ObserveProbes ObserveWarmChildren ObserveLeaving ObservePrograms])
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
  (ObserveLeaving [] (resume False))
  (ObservePrograms [] (resume #())))


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


(deftest test-the-world-reads-the-jobs-layer-room-from-the-watch
  ;; 失敗ケース: jobs の層の上限と使いは見張りの入れ物の中にだけ在り、調整ループは入らない待ちの子を起こし、層の OOM kill で落ちた子を
  ;; 30 秒ごとに起こし直した(2026-11-06 の node-d〜f)。空き = 見張りの最後の回に決めた新しい受けの上限 − その回の working set(使い − inactive_file)。
  (val cell (MemoryCell))
  (val observe (with-handlers [(memory-observer cell) nothing-else-observed local-host] (ObserveWorld)))
  (assert (is (. (run observe) jobs-room) None) "見張りがまだ上限を掛けていなければ空きで縛らない")
  (setv cell.eviction.jobs-intake (* 896 1024 1024) cell.eviction.jobs-held (* 500 1024 1024))
  (assert (= (. (run observe) jobs-room) (* 396 1024 1024)) "上限 896 MiB・使い 500 MiB なら空き 396 MiB")
  (setv cell.eviction.jobs-held (* 900 1024 1024))
  (assert (= (. (run observe) jobs-room) (* -4 1024 1024)) "上限を使いより下げた続きの中は負の空き")
  (setv cell.eviction.jobs-held None)
  (assert (is (. (run observe) jobs-room) None) "使いを読めなかった回は空きで縛らない"))
