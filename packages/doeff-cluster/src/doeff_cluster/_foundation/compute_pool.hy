;;; worker の純粋な計算(Compute)の本物の答え手 worker-compute-handler — 計算を scheduler の thread の外の pool(thread 1 本)で回し、
;;; 待つ間も調整ループ(tick と heartbeat)を回す。今の使い手は掃除の task の bytecode の保存先の選び(env_store の unused-store-entries —
;;; 数十万の entry を選ぶ計算が scheduler の thread で数秒かかり、掃除の終わり際に拍を止めた)1 つだけなので 1 本。thread は初めの仕事で
;;; 起きる。pool は process に 1 つ(入口の層は thread を直に作らない — main の production-handlers はこの答え手を並べるだけ)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects [thread-pool-compute-handler])

(val COMPUTE-POOL (ThreadPoolExecutor :max-workers 1 :thread-name-prefix "doeff-worker-compute"))
(val worker-compute-handler (thread-pool-compute-handler COMPUTE-POOL))
