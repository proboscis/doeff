;; 業務の事情を引数へ出した口(2026-09-25 — 業務の repo から切り出した時)。
;;   - ClusterNaming: Rollout が Deployment に付ける持ち主の annotation と、node の label から導く能力は配備する側が決める。
;;     image の版を追う係の欄(revisionLabel・versionLabels — 2026-09-28 に係ごと消した)は理由つきで断る
;;   - CodeLayout: 子 process の PYTHONPATH の根と土台の import の路は worker の引数が決める
(require doeff-hy.macros [deftest val])
(import json)
(import pytest)
(import dataclasses [fields])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterNaming])
(import doeff_cluster.coordinator.core.cluster_json [naming-from-json])
(import doeff_cluster.worker_model [CodeLayout])
(import tests.test_rollout [Sim FORWARD DEP])


;; --- ClusterNaming ------------------------------------------------------------------------------

(deftest test-naming-from-json-takes-the-deployers-names-and-refuses-unknown-fields
  (val n (naming-from-json (json.dumps {"ownerAnnotation" "example.org/owned-by" "ownerScope" "lab"
                                        "nodeCapabilities" [{"label" "example.org/gpu" "value" "true" "capability" "gpu"}]})))
  (assert (= n (ClusterNaming :owner-annotation "example.org/owned-by" :owner-scope "lab"
                              :node-capabilities #(#("example.org/gpu" "true" "gpu"))))
          n)
  ;; 書かなかった欄は既定のまま
  (assert (= (naming-from-json "{}") (ClusterNaming)))
  ;; 綴りを誤った欄を黙って捨てない(捨てると既定の名で annotation を付けることになる)
  (with [e (pytest.raises ValueError)] (naming-from-json (json.dumps {"ownerAnnotaton" "x"})))
  (assert (in "ownerAnnotaton" (str e.value))))

(deftest test-naming-refuses-the-fields-of-the-removed-image-follower
  ;; image の版を追う係(image の LABEL を読んで Service の土台の commit を進める)は消した。その係の欄を書いた naming は、黙って
  ;; 捨てず理由つきで断る(coordinator は起動しない — Program の job は宣言した commit でだけ解く)。
  (for [field ["revisionLabel" "versionLabels"]]
    (with [e (pytest.raises ValueError)]
      (naming-from-json (json.dumps {"ownerScope" "lab" field (if (= field "revisionLabel") "example.org/revision" {})})))
    (assert (in field (str e.value)) (str e.value))
    (assert (in "image の版を追う係は消した" (str e.value)) (str e.value))))


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
  ;; 以前の重ねる dir(overlay-path — 定義だけを別の commit で重ねる木)は消した。欄の一覧に無いことを直に確かめる(frozen の
  ;; dataclass なので欄の外の名を渡せば TypeError。無い欄を名指して呼ぶ書き方は型検査が断るので一覧を読む)。
  (assert (not-in "overlay_path" (lfor f (fields CodeLayout) f.name))))

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
  (import doeff_cluster.worker_model [StartJob] doeff_cluster.shared.intent.job_model [JobSpec])
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

