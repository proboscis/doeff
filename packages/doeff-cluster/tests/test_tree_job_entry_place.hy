;; 子へ送る入口の名の選び(worker/core/launch の child-entry-place)を、入口の検めと子の起動が同じに使い、木の job(実行環境の宣言の
;; 無い job)にも worker の hy で解ける名を送ることの検。
;;
;; 何のため: coordinator は返事の行の入口を指紋の綴り(置き場 LEGACY-ENTRY-PLACE = doeff_cluster.worker.entry — 048fd044b)で送る。
;; 置き場を替えて送るのは実行環境の job(root の完成の印の置き場)だけで、木の job は返事の名のまま検め・起こしていた。その名は移し替えの
;; module worker.py が解いていただけなので、d6a39a3b7 で worker.py を消すと、木の Service は入口の検めで `No module named
;; 'doeff_cluster.worker'` になり起きなかった(日次の test_local_machine・test_served_program の赤)。
;; 筋書き:
;;   1. root ごとの選び: 木の job → worker 自身の置き場・印に置き場を書いた新しい root → その置き場・欄の無い古い root → 旧い置き場・
;;      印の読めない root → 返事の名のまま。どの組でも検めの対象と起こす名が同じで、指紋は返事の名の指紋のまま。
;;   2. 本物の検め: 木の束の検めの本体(PROBE-PROGRAM を probe-command の形で worker の hy から)を撃ち、選んだ名は解け、置き場を替えない
;;      返事の名は解けない(変換を外した負例 — 消した転送 module を戻さずに起きる形はこれしか無い)。
(require doeff-hy.macros [deftest defk val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import json)
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff_cluster [JobSpec spec-hash])
(import doeff_cluster._coordinator.core.cluster_policy [JOB-ENTRY])
(import doeff_cluster._shared.intent.job_model [CHILD-ENTRY-PLACES LEGACY-ENTRY-PLACE])
(import doeff_cluster._worker.core.launch [WORKER-ENTRY-PLACE child-entry-place entry-place-of-marker])
(import doeff_cluster._worker.core.probe_rules [probe-command probe-results probe-targets])
(import doeff_cluster._worker.intent.worker_model [CodeLayout])

(val HY (str (/ (. (Path sys.executable) parent) "hy")))
(val PACKAGE-ROOT (. (Path __file__) (resolve) parent parent))
(val ENV "{\"repos\":[]}")
;; 返事の行の読み(declared の declared-job-specs)と同じ形の spec: 入口は coordinator の JOB-ENTRY の綴り。
(val TREE-SPEC (JobSpec "svc" JOB-ENTRY #("service" "--identity" "abc") "rev1"))
(val ENV-SPEC (JobSpec "svc" JOB-ENTRY #("service" "--identity" "abc") "rev1" :runtime-env ENV))


(defk sent-entry [spec root-marker]
  {:pre [(: spec JobSpec) (: root-marker (| str None))] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "judgment"}}
  "worker が spec に送る入口の名を、検め(probes の launch-probe)と起動(process_host の start-job)の読みで 1 つずつ組むため
   (root-marker = root の完成の印の中身 — 宿が env_store の root-entry-place で読む物。木の job は読まない)→ #(検めの対象 起こす名)。"
  (val root-place (if spec.runtime-env (! (entry-place-of-marker root-marker)) None))
  (val place (! (child-entry-place spec.runtime-env root-place)))
  #((tuple (probe-targets spec place)) (.entry-at spec place)))


(deftest test-the-probe-and-the-start-send-the-same-name-for-each-root
  (assert (= JOB-ENTRY (+ LEGACY-ENTRY-PLACE ".job_entry")) "返事の綴りが指紋の綴りでない — 古い worker の指紋が変わる")
  (assert (= WORKER-ENTRY-PLACE (get CHILD-ENTRY-PLACES 0) "doeff_cluster._worker.entry") WORKER-ENTRY-PLACE)
  (for [#(spec marker wanted) #(;; 木の job: worker の hy で起きるので worker 自身の置き場(印は読まない)。
                                #(TREE-SPEC None "doeff_cluster._worker.entry.job_entry")
                                ;; 新しい root: 準備の確かめが印へ書いた置き場。
                                #(ENV-SPEC (json.dumps {"entryPlace" "doeff_cluster._worker.entry"}) "doeff_cluster._worker.entry.job_entry")
                                ;; 古い root: 欄の無い印 — 旧い置き場(その頃の doeff の root はその名を解く)。
                                #(ENV-SPEC (json.dumps {"format" 1}) "doeff_cluster.worker.entry.job_entry")
                                #(ENV-SPEC (json.dumps {"entryPlace" "doeff_cluster.worker.entry"}) "doeff_cluster.worker.entry.job_entry")
                                ;; 印の読めない root: 返事の名のまま。
                                #(ENV-SPEC None JOB-ENTRY))]
    (val sent (! (sent-entry spec marker)))
    (val targets (get sent 0))
    (val started (get sent 1))
    (assert (= targets #(started)) #("検めた名と起こす名が分かれた" spec.runtime-env marker targets started))
    (assert (= started wanted) #(spec.runtime-env marker started))
    ;; 指紋は送る名に依らず返事の名の指紋(置き場は LEGACY-ENTRY-PLACE の綴りで材料に入る)— 古い worker・新しい worker の混在で同じ。
    (assert (= (spec-hash (JobSpec spec.name started spec.args spec.revision :runtime-env spec.runtime-env)) (spec-hash spec))
            #("送る名で指紋が変わった" started))))


(defk tree-probe-results [targets]
  {:pre [(: targets tuple)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "木の束の検めの本体を worker の起こし方(probe-command の木の道 — worker の hy・木の PYTHONPATH・cwd = 木)で撃ち、対象ごとの結果を
   返すため(木 = この package の根 — 検の app と同じく doeff_cluster を持たない木で、子は worker の venv の doeff を解く)。shim は
   挟まない(shim の下の起こしは検め全体の検 test_entry_probe の側)。"
  (val plan (! (probe-command (str PACKAGE-ROOT) None targets :hy-command HY :layout (CodeLayout) :allowed-env {}
                              :probe-dir (str PACKAGE-ROOT))))
  (val done (subprocess.run (list plan.argv) :cwd plan.cwd :capture-output True :text True :timeout 120
                            :env (| (dict os.environ) (dfor e plan.env e.name e.value))))
  (assert (= done.returncode 0) done.stderr)
  (! (probe-results done.stdout)))


(deftest test-the-tree-probe-resolves-the-chosen-name-but-not-the-wire-name
  (val sent (! (sent-entry TREE-SPEC None)))
  (val started (get sent 1))
  (val results (! (tree-probe-results (+ (get sent 0) #(TREE-SPEC.entry)))))
  ;; 選んだ名は木の hy で解ける(子の起動もこの名)。
  (assert (is (get results started) None) results)
  ;; 失敗ケース(変換を外した形 — d6a39a3b7 の後の日次の赤): 返事の名をそのまま検めると、移し替えの module の無い doeff では解けない。
  (val refused (get results TREE-SPEC.entry))
  (assert (and (isinstance refused str) (in "doeff_cluster.worker" refused)) results))
