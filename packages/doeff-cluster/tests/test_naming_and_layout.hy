;; 業務の事情を引数へ出した口(2026-09-25 — 業務の repo から切り出した時)。
;;   - ClusterNaming: Rollout が Deployment に付ける持ち主の annotation と、版の追随が読む image の LABEL は配備する側が決める
;;   - CodeLayout: 子 process の PYTHONPATH の根と、overlay で重ねる dir は worker の引数が決める。重ねる dir の無い worker は重ねる木を断る
(require doeff-hy.macros [deftest])
(import json)
(import subprocess)
(import time)
(import pytest)
(import doeff_cluster.cluster_model [ClusterNaming naming-from-json])
(import doeff_cluster.base_follow_policy [image-entry])
(import doeff_cluster.worker_model [CodeLayout CodeState])
(import doeff_cluster.handlers [CodeStore])
(import tests.test_rollout [Sim FORWARD DEP])


;; --- ClusterNaming ------------------------------------------------------------------------------

(deftest test-naming-from-json-takes-the-deployers-names-and-refuses-unknown-fields
  (setv n (naming-from-json (json.dumps {"ownerAnnotation" "example.org/owned-by" "ownerScope" "lab"
                                         "revisionLabel" "example.org/revision"
                                         "versionLabels" {"runtime" "example.org/runtime"}})))
  (assert (= n (ClusterNaming "example.org/owned-by" "lab" "example.org/revision" #(#("runtime" "example.org/runtime")))))
  ;; 書かなかった欄は既定のまま
  (assert (= (naming-from-json "{}") (ClusterNaming)))
  ;; 綴りを誤った欄を黙って捨てない(捨てると既定の名で annotation を付け・LABEL を読むことになる)
  (with [e (pytest.raises ValueError)] (naming-from-json (json.dumps {"revisionLable" "x"})))
  (assert (in "revisionLable" (str e.value)))
  (with [(pytest.raises ValueError)] (ClusterNaming :version-labels #(#("revision" "x")))))


(deftest test-image-entry-reads-the-deployers-labels
  (setv sha (* "a" 40)
        n (ClusterNaming :revision-label "example.org/revision" :version-labels #(#("runtime" "example.org/runtime"))))
  (assert (= (image-entry {"example.org/revision" sha "example.org/runtime" "r1"} 7 n)
             {"revision" sha "runtime" "r1" "at" 7}))
  ;; 既定の名の LABEL しか無い image は、名の違う配備では版が読めない(黙って別の LABEL を読まない)
  (assert (in "error" (image-entry {"org.opencontainers.image.revision" sha} 7 n))))


(deftest test-the-rollout-marks-the-deployment-with-the-deployers-annotation
  (setv sim (Sim))
  (setv sim.naming (ClusterNaming :owner-annotation "example.org/replicas-owned-by" :owner-scope "lab"))
  (sim.rollout "to-worker" (| FORWARD {"markDeployment" True}))
  (assert (= (sim.run-until "to-worker" #("Complete" "RolledBack")) "Complete"))
  (sim.step)
  (setv annotations (get sim.kube.deployments DEP "annotations"))
  (assert (= (get annotations "example.org/replicas-owned-by") "lab/Rollout/to-worker replicas=0") annotations)
  (assert (not-in "doeff-cluster/replicas-owned-by" annotations) annotations))


;; --- CodeLayout ---------------------------------------------------------------------------------

(deftest test-code-layout-builds-the-pythonpath-and-refuses-paths-outside-the-tree
  (assert (= (.pythonpath (CodeLayout) "/t") "/t"))
  (assert (= (.pythonpath (CodeLayout :import-roots #("." "vendor/hy")) "/t") "/t:/t/vendor/hy"))
  (assert (= (.roots-arg (CodeLayout :import-roots #("." "vendor/hy"))) ".,vendor/hy"))
  (for [bad [#() #("/abs") #("../up") #("a:b") #("a,b")]]
    (with [(pytest.raises ValueError)] (CodeLayout :import-roots bad)))
  (with [(pytest.raises ValueError)] (CodeLayout :overlay-path "../x")))

(deftest test-code-layout-puts-the-base-paths-after-the-tree-roots
  ;; 2026-09-26: host の worker は土台の package(image に焼かない物)の路を宣言する。木の根が先(業務の code は task の版が勝つ)・
  ;; 土台の路は機体の絶対 path だけ。宣言が無ければ今までと同じ(pod)。
  (setv layout (CodeLayout :import-roots #("." "vendor/hy") :base-paths #("/opt/base/sdk/python")))
  (assert (= (.pythonpath layout "/t") "/t:/t/vendor/hy:/opt/base/sdk/python"))
  (assert (= (.roots-arg layout) ".,vendor/hy"))
  (for [bad ["relative/sdk" "/a:/b" "/a,/b"]]
    (with [(pytest.raises ValueError)] (CodeLayout :base-paths #(bad)))))

(deftest test-process-host-records-the-tree-and-pid-of-each-started-job [tmp-path capfd]
  ;; 2026-09-26: worker は起こした job ごとに、版・木の path・子の pid・worker の pid を記録に 1 行書く(新しい版の job を
  ;; worker の再起動なしに版の木の子 process で走らせたことを、worker の記録で示すため)。子の PYTHONPATH は木の根 → 土台の路。
  (import os)
  (import time)
  (import doeff_cluster.handlers [ProcessHost])
  (import doeff_cluster.worker_model [JobSpec StartJob])
  (setv tree (/ tmp-path "tree") out (/ tmp-path "seen"))
  (.mkdir tree)
  (.write-text (/ tree "probe_entry.py")
               (+ "import os, pathlib\npathlib.Path(" (repr (str out)) ").write_text(os.environ['PYTHONPATH'] + '|' + os.getcwd())\n"))
  (setv host (ProcessHost (str (/ tmp-path "logs")) "hy" :layout (CodeLayout :base-paths #("/opt/base"))))
  (.start host (StartJob (JobSpec "task/t1" "probe_entry" #() "rev-a" :once True) 1 (str tree)))
  (setv pid (. (get (.observe host) 0) pid))
  (for [_ (range 200)] (when (.exists out) (break)) (time.sleep 0.05))
  (setv [pythonpath cwd] (.split (.read-text out) "|"))
  (assert (= pythonpath (+ (str tree) ":/opt/base")))
  (assert (= cwd (str tree)))
  (setv err (. (capfd.readouterr) err))
  (assert (in (.format "job-start name=task/t1 revision=rev-a tree={} pid={} worker-pid={}" tree pid (os.getpid)) err) err))


(defn git [repo #* args]
  (.strip (. (subprocess.run ["git" "-C" (str repo) #* args] :check True :capture-output True :text True) stdout)))


(defn test-a-worker-without-an-overlay-dir-refuses-a-layered-tree [tmp-path]
  ;; 重ねる dir を持たない worker に「<base>~<revision>」を求めたら、base の木だけで黙って完成させず、準備を失敗にする。
  (setv repo (/ tmp-path "repo") cache (/ tmp-path "cache"))
  (.mkdir repo)
  (git repo "init" "-q")
  (.write-text (/ repo "m.py") "V = 1\n")
  (git repo "add" "-A")
  (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-q" "-m" "a")
  (setv a (git repo "rev-parse" "HEAD"))
  (.write-text (/ repo "m.py") "V = 2\n")
  (git repo "-c" "user.name=t" "-c" "user.email=t@t" "commit" "-qam" "b")
  (setv b (git repo "rev-parse" "HEAD"))
  (setv store (CodeStore (str repo) (str cache) None) key (+ a "~" b))
  (.start store key)
  (setv deadline (+ (time.monotonic) 60) view None)
  (while (and (< (time.monotonic) deadline) (or (is view None) (= view.state CodeState.PREPARING)))
    (setv view (next (gfor v (.observe store) :if (= v.revision key) v) None))
    (time.sleep 0.05))
  (assert (= view.state CodeState.FAILED) view)
  (assert (in "重ねる dir が無い" view.detail) view.detail)
  (assert (not (.exists (/ cache key)))))
