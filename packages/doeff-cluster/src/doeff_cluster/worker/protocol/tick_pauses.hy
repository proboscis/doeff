;;; worker の拍と拍の間の待ち(AwaitNextTick)の本番の答え手 — 拍 tick-seconds を宣言の変化の呼び鈴と競わせて眠る(core/program.tick-pause・
;;; #2692)。本番の組(entry/main.production-handlers)と、拍を 1 つずつ打つ偽の宿の組が置く。模擬の時計の下の宿(sim/local.hy)は、
;;; 先の拍を本番の判断で試して静かな拍を一度に眠る(#2781)。
(require doeff-hy.macros [defhandler <- val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster.worker.intent.worker_model [AwaitNextTick])
(import doeff_cluster.worker.core.program [tick-pause])


(defhandler tick-pauses
  ;; 引数なし: 拍の長さと呼び鈴は effect の欄(policy・changed)が運ぶ。state は読まない(模擬の宿の材料)。
  (AwaitNextTick [policy changed state]
    (<- (tick-pause policy changed))
    (resume None)))
