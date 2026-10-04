;;; worker と coordinator の起動の値(WorkerLaunch・CoordinatorLaunch — shared/intent/launch_model.hy)を、boot.sh が読む環境変数の
;;; 行へ写す(#3366 の単位 1)。配備する側の宣言を書く handler は、この写しから行を得る。手元の機体で boot.sh を起こす模擬
;;; (sim/machine.hy の worker-boot-env)は模擬の worker(SimWorker)から自分の行を組む — 寄せるのは別の変更。
(require doeff-hy.macros [val defk])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import doeff_core_effects.process_effects [EnvEntry])
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch])


(defk worker-launch-env [launch]
  {:pre [(: launch WorkerLaunch)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "worker 1 台の名乗りと版を、boot.sh が読む名の環境変数の行にするため(宣言の行を書く handler がこの値で行を作る)。順は boot.sh が
   読む順。exclusive が空なら WORKER_EXCLUSIVE の行を持たない(boot.sh は無い名を空として読む)。provides は書いた順のまま並べる。"
  (+ #((EnvEntry :name "WORKER_DOEFF_COMMIT" :value launch.doeff-commit)
       (EnvEntry :name "WORKER_NAME" :value launch.name)
       (EnvEntry :name "WORKER_PROVIDES" :value (.join "," launch.provides)))
     (if launch.exclusive
         #((EnvEntry :name "WORKER_EXCLUSIVE" :value (.join "," launch.exclusive)))
         #())
     #((EnvEntry :name "WORKER_CAPACITY" :value (str launch.capacity))
       (EnvEntry :name "WORKER_TASK_RESERVE" :value (str launch.task-reserve)))))


(defk coordinator-launch-env [launch]
  {:pre [(: launch CoordinatorLaunch)] :post [(: % (get tuple #(EnvEntry ...)))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "coordinator の版を、boot.sh が読む名の環境変数の行にするため。"
  #((EnvEntry :name "WORKER_DOEFF_COMMIT" :value launch.doeff-commit)))
