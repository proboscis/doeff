;; worker と coordinator の起動の値(shared/intent/launch_model.hy)と、boot.sh が読む環境変数の行への写し(shared/core/launch_rules.hy)の検
;; (#3366 の単位 1)。起動の時に worker の入口が断る値(task-reserve が範囲の外・版が sha でない)を、宣言の時に断る事と、行の名と値の
;; 綴りが boot.sh の名と同じ事を確かめる。
(require doeff-hy.macros [deftest defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import pytest)
(import doeff_cluster.shared.intent.launch_model [WorkerLaunch CoordinatorLaunch DesireWorker DesireCoordinator])
(import doeff_cluster.shared.core.launch_rules [worker-launch-env coordinator-launch-env])

(val SHA "90fd9a81d97ddf1cf5ae13a4036fa615108abbe7")


(defk pairs-of [launch]
  {:pre [(: launch WorkerLaunch)] :post [(: % tuple)]}
  "行を (名 値) の対の列にする(期待の表と比べるため)。"
  (<- env tuple (worker-launch-env launch))
  (tuple (gfor e env #(e.name e.value))))


(deftest test-a-worker-launch-becomes-the-boot-env-lines-in-boot-order
  (<- plain tuple (pairs-of (WorkerLaunch :name "w2" :provides #("agent" "host-w2" "boundary-personal") :exclusive #()
                                          :capacity 19 :task-reserve 3 :doeff-commit SHA)))
  ;; provides は書いた順のまま(名の順に並べ替えない — 宣言の行が順を持つ)・exclusive が空なら行を持たない。
  (assert (= plain #(#("WORKER_DOEFF_COMMIT" SHA) #("WORKER_NAME" "w2") #("WORKER_PROVIDES" "agent,host-w2,boundary-personal")
                     #("WORKER_CAPACITY" "19") #("WORKER_TASK_RESERVE" "3")))
          plain)
  (<- dedicated tuple (pairs-of (WorkerLaunch :name "web" :provides #("webapp" "host-web") :exclusive #("webapp")
                                              :capacity 2 :task-reserve 0 :doeff-commit SHA)))
  (assert (in #("WORKER_EXCLUSIVE" "webapp") dedicated) dedicated)
  (<- coord tuple (coordinator-launch-env (CoordinatorLaunch :doeff-commit SHA)))
  (assert (= (tuple (gfor e coord #(e.name e.value))) #(#("WORKER_DOEFF_COMMIT" SHA)))))


(deftest test-values-the-worker-refuses-at-boot-are-refused-at-declaration
  ;; 失敗ケース: worker の入口(main.hy)が起動の時に exit 2 で断る値を、宣言の値の時に断る — 断らないと、宣言が main に入り
  ;; Pod が作り直された後で初めて止まる。
  (val base {"name" "w" "provides" #("verify") "exclusive" #() "capacity" 1 "task_reserve" 0 "doeff_commit" SHA})
  (for [#(what change) #(#("reserve が capacity を越える" {"task_reserve" 2})
                         #("reserve が負" {"task_reserve" -1})
                         #("capacity が 0" {"capacity" 0 "task_reserve" 0})
                         #("版が短い sha" {"doeff_commit" "90fd9a81"})
                         #("provides が空" {"provides" #()})
                         #("名が空" {"name" ""}))]
    (with [(pytest.raises ValueError)]
      (WorkerLaunch #** (| base change))))
  (with [(pytest.raises ValueError)]
    (CoordinatorLaunch :doeff-commit "main"))
  ;; 境界の値は通る(reserve = capacity)。
  (assert (= (. (WorkerLaunch #** (| base {"capacity" 3 "task_reserve" 3})) task-reserve) 3)))


(deftest test-the-desire-effects-carry-the-launch
  (val launch (WorkerLaunch :name "w" :provides #("verify") :exclusive #() :capacity 1 :task-reserve 0 :doeff-commit SHA))
  (assert (is (. (DesireWorker launch) launch) launch))
  (assert (= (. (DesireCoordinator (CoordinatorLaunch :doeff-commit SHA)) launch doeff-commit) SHA)))
