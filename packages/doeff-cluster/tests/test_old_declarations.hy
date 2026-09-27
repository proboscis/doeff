;;; 旧い宣言の形を断る coordinator 側の入口(ADR-DOE-CLUSTER-001・計画 2.8 の入口 5・6・10 — operator 2026-09-27 「旧い宣言は受け付けない」)。
;;;
;;;   5 = 書きの口(PUT /jobs・POST/PUT /resources/Service)は旧い本文を 400 と理由で断る。run の無い生の entry と args の job
;;;       (Program でない job)も同じ — 移行の期間は置かない(ADR-DOE-CLUSTER-001 R1・R7)。
;;;   6 = 読み直し(state file・durable KV)の旧い Service の行は落とさず RefusedJob にし、資源の口に status.refused で出し、
;;;       新しい形の PUT で受け付けた job に置き換え、DELETE で消せる。保存し直しても元の行のまま残る。生の entry の行も同じ。
;;;  10 = worker の起動の旧い --labels は起動しない(理由を stderr に)。宣言の file から job を直に起こす旧い --desired も無い。
(require doeff-hy.macros [deftest defk <- val var])
(import os)
(import subprocess)
(import sys)
(import pathlib [Path])
(import doeff_cluster.cluster_model [ClusterTiming ClusterState Request RefusedJob])
(import doeff_cluster.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.durable_kv [full-kv state-from-kv])
(import doeff_cluster.api_policy [respond])
(import doeff_cluster.runtime_env_model [RepoCheckout PythonProject RuntimeEnv EnvVar runtime-env->json])
(import tests.program_rows [SAMPLE-RUN program-run])

(val T (ClusterTiming))
(val PACKAGE-ROOT (. (Path __file__) (resolve) parent parent))
(val ROW {"revision" "r1" "needs" ["net"] "run" SAMPLE-RUN})
;; 2026-09-27 より前の coordinator が書いた Service の行(関数の参照 + handler の組の import path + 設定)。
(val OLD-RUN {"kind" "service" "factory" "m:f" "env" "m:e" "config" {"step" 1}})
;; run の無い生の entry と args の job(worker に module と引数を直に起こさせる — Program でない job)。
(val RAW-ENTRY {"revision" "r1" "needs" ["net"] "entry" "m" "args" ["--x" "1"]})


(defk call [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の純粋な振り分け 1 件(送り手 c-me)→ #(次の状態 status 本文)。"
  (respond state (Request method path {} body :actor "c-me" :peer "10.0.0.9") now T))


(defk declared-env-json []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "子の環境変数 POLL を宣言した実行環境の JSON(environ との名の重なりの反例に使う)。"
  (<- env-json dict (runtime-env->json
                      (RuntimeEnv :repos #((RepoCheckout :name "app" :url "https://example.invalid/app.git" :commit (* "a" 40)))
                                  :project (PythonProject :repo "app" :path "." :lock-sha256 (* "0" 64) :python "3.14")
                                  :import-roots #("app/.")
                                  :env-vars #((EnvVar :name "POLL" :value "1")))))
  env-json)


(defk bad-rows []
  {:pre [] :post [(: % list)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "#(何の反例か 行 理由に含む語) の列 — どれも新しい形の行から 1 か所だけ崩した物。"
  (<- env-json dict (declared-env-json))
  [#("run の旧い欄" (| ROW {"run" OLD-RUN}) "run の factory・env・config")
   #("生の entry の job(run が無い)" RAW-ENTRY "生の entry の job")
   #("旧い run と新しい欄の混在" (| ROW {"run" (| SAMPLE-RUN {"config" {}})}) "run の config")
   #("requires" (| ROW {"requires" {"kind" "k3s"}}) "requires")
   #("baseFrom" (| ROW {"baseFrom" {"kind" "Deployment" "namespace" "n" "name" "d"}}) "baseFrom・overlay")
   #("overlay" (| ROW {"overlay" (* "b" 40)}) "overlay")
   #("置き場のキーの形" (| ROW {"run" (| SAMPLE-RUN {"program" "not-a-sha"})}) "run.program")
   #("identity の欠け" (| ROW {"run" (dfor #(k v) (.items SAMPLE-RUN) :if (!= k "identity") k v)}) "run.identity")
   #("environ の値が文字列でない" (| ROW {"environ" {"POLL" 5}}) "environ の POLL")
   #("environ の名が予約" (| ROW {"environ" {"DOEFF_WORKER_NAME" "x"}}) "DOEFF_WORKER_NAME")
   #("environ の名の形" (| ROW {"environ" {"1BAD" "x"}}) "1BAD")
   #("実行環境の envVars と同じ名" (| ROW {"environ" {"POLL" "2"} "runtimeEnv" env-json}) "env-vars と同じ名")])


(deftest test-entry-5-old-service-bodies-are-refused-with-400-at-every-write
  ;; 入口 5: POST /resources/Service・PUT /resources/Service/<名>・PUT /jobs のどれも、崩した行を 400 と理由で断り、状態を変えない。
  (<- rows list (bad-rows))
  (<- created tuple (call (ClusterState) "POST" "/resources/Service" {"name" "a" "spec" ROW} 1000))
  (val base (get created 0))
  (assert (= (get created 1) 201))
  (val version (get base.meta "Service/a" "resourceVersion"))
  (for [#(what row word) rows]
    (<- posted tuple (call (ClusterState) "POST" "/resources/Service" {"name" "b" "spec" row} 1000))
    (assert (= (get posted 1) 400) #(what posted))
    (assert (in word (get posted 2 "error")) #(what (get posted 2)))
    (assert (= (. (get posted 0) jobs) #()) what)
    (<- put tuple (call base "PUT" "/resources/Service/a" {"spec" row "resourceVersion" version} 2000))
    (assert (= (get put 1) 400) #(what put))
    (assert (in word (get put 2 "error")) #(what (get put 2)))
    (assert (is (get put 0) base) what)
    (<- legacy tuple (call (ClusterState) "PUT" "/jobs" {"jobs" [(| row {"name" "c"})]} 1000))
    (assert (= (get legacy 1) 400) #(what legacy))
    (assert (in word (get legacy 2 "error")) #(what (get legacy 2))))
  ;; 見本の新しい形は通る(反例が形そのものを断っていないことの確かめ)。
  (<- fresh tuple (call (ClusterState) "PUT" "/jobs" {"jobs" [(| ROW {"name" "c" "environ" {"POLL" "2"}})]} 1000))
  (assert (= (get fresh 1) 200) fresh))


(defk saved-with-old-row []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "受け付けた Service 2 つ(new・old)を持つ coordinator の state file の形で、old の行だけを旧い宣言の形へ書き換えた物
   (旧い coordinator が書いた置き場の写し — 資源の版の meta も在る)。"
  (<- a tuple (call (ClusterState) "POST" "/resources/Service" {"name" "new" "spec" ROW} 1000))
  (<- b tuple (call (get a 0) "POST" "/resources/Service" {"name" "old" "spec" ROW} 1000))
  (val data (state-to-json (get b 0)))
  (| data {"jobs" (lfor row (get data "jobs")
                        (if (= (get row "name") "old")
                            (| (dfor #(k v) (.items row) :if (!= k "run") k v) {"run" OLD-RUN})
                            row))}))


(defk check-refused-state [state]
  {:pre [(: state ClusterState)] :post [(: % bool)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "読み直した状態: new は受け付け、old は RefusedJob(元の行と理由)。"
  (assert (= (lfor j state.jobs j.spec.name) ["new"]) state.jobs)
  (val refused (get state.refused "old"))
  (assert (isinstance refused RefusedJob))
  (assert (= (get refused.row "run") OLD-RUN) refused.row)
  (assert (in "旧い宣言の形" refused.reason) refused.reason)
  True)


(defk saved-with-raw-entry-row []
  {:pre [] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "受け付けた Service 2 つ(new・raw)を持つ state file の形で、raw の行だけを run の無い生の entry の job へ書き換えた物
   (生の entry の job を受けていた coordinator が書いた置き場の写し)。"
  (<- a tuple (call (ClusterState) "POST" "/resources/Service" {"name" "new" "spec" ROW} 1000))
  (<- b tuple (call (get a 0) "POST" "/resources/Service" {"name" "raw" "spec" ROW} 1000))
  (val data (state-to-json (get b 0)))
  (| data {"jobs" (lfor row (get data "jobs")
                        (if (= (get row "name") "raw")
                            (| (dfor #(k v) (.items row) :if (!= k "run") k v) {"entry" "m" "args" ["--x" "1"]})
                            row))}))


(deftest test-entry-6-a-raw-entry-service-row-is-read-as-refused-from-the-state-file-and-the-durable-kv
  ;; 入口 6(生の entry の job): 読み直しは落ちず、生の entry の行を RefusedJob(元の行と理由)にする — worker に直に起こさせない。
  (<- data dict (saved-with-raw-entry-row))
  (for [state [(state-from-json data 5000) (state-from-kv (full-kv (state-from-json data 5000)) 5000)]]
    (assert (= (lfor j state.jobs j.spec.name) ["new"]) state.jobs)
    (val refused (get state.refused "raw"))
    (assert (isinstance refused RefusedJob) state.refused)
    (assert (= (get refused.row "entry") "m") refused.row)
    (assert (in "生の entry の job" refused.reason) refused.reason)
    ;; 置き先を持たない(どの worker にも起こさせない)。
    (assert (not-in "raw" state.placements) state.placements)))


(deftest test-entry-6-an-old-service-row-in-the-state-file-is-read-as-refused
  (<- data dict (saved-with-old-row))
  (val state (state-from-json data 5000))
  (<- ok bool (check-refused-state state))
  (assert ok)
  ;; 保存し直しても元の行のまま残り、読み直しても同じ理由で受け付けない。
  (val again (state-to-json state))
  (assert (in {"name" "old"} (lfor r (get again "jobs") {"name" (get r "name")})))
  (<- still bool (check-refused-state (state-from-json again 6000)))
  (assert still))


(deftest test-entry-6-an-old-service-row-in-the-durable-kv-is-read-as-refused
  (<- data dict (saved-with-old-row))
  (val kv (full-kv (state-from-json data 5000)))
  (assert (= (get kv "service/old" "run") OLD-RUN) "refused の行は元の行のまま durable KV に書く")
  (<- ok bool (check-refused-state (state-from-kv kv 5000)))
  (assert ok))


(deftest test-entry-6-a-refused-service-is-shown-replaced-by-put-and-deleted
  (<- data dict (saved-with-old-row))
  (val state (state-from-json data 5000))
  ;; GET /resources/Service に status.refused が出る(spec は元の行のまま)。
  (<- listed tuple (call state "GET" "/resources/Service" None 5000))
  (val items (dfor item (get listed 2 "items") (get item "name") item))
  (assert (in "旧い宣言の形" (get items "old" "status" "refused")) (get items "old"))
  (assert (= (get items "old" "spec" "run") OLD-RUN))
  (assert (not-in "refused" (get items "new" "status")))
  ;; 新しい形の PUT で受け付けた job に置き換わる(refused から消える)。
  (<- one tuple (call state "GET" "/resources/Service/old" None 5000))
  (<- run dict (program-run "m:g" 1))
  (<- put tuple (call state "PUT" "/resources/Service/old"
                      {"spec" (| ROW {"run" run}) "resourceVersion" (get one 2 "resourceVersion")} 6000))
  (assert (= (get put 1) 200) put)
  (val replaced (get put 0))
  (assert (= (sorted (gfor j replaced.jobs j.spec.name)) ["new" "old"]))
  (assert (= replaced.refused {}))
  ;; DELETE で消せる(置き換えずに捨てる道)。
  (<- deleted tuple (call state "DELETE" "/resources/Service/old" None 6000))
  (assert (= (get deleted 1) 200) deleted)
  (assert (= (. (get deleted 0) refused) {}))
  (assert (= (lfor j (. (get deleted 0) jobs) j.spec.name) ["new"])))


(deftest test-a-worker-has-no-entrance-that-reads-jobs-from-a-declaration-file
  ;; 宣言の file から生の entry の job を直に起こす旧い口 --desired は無い(worker は coordinator からだけ job を受ける — R1)。
  (val done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.main" "--desired" "desired.json"
                             "--repo" "." "--state-dir" "/nonexistent"]
                            :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120))
  (assert (= done.returncode 2) done.stderr)
  (assert (in "--coordinator" done.stderr) done.stderr)
  (assert (or (in "unrecognized arguments: --desired" done.stderr) (in "required: --coordinator" done.stderr)) done.stderr))


(deftest test-entry-10-a-worker-started-with-old-labels-does-not-start
  ;; 入口 10: 旧い --labels(置き場所の label)で起こした worker は argparse の error で止まり、理由を stderr に出す。
  (val done (subprocess.run [sys.executable "-m" "hy" "-m" "doeff_cluster.main" "--coordinator" "http://127.0.0.1:9"
                             "--name" "w" "--labels" "kind=k3s" "--repo" "." "--state-dir" "/nonexistent"]
                            :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120))
  (assert (= done.returncode 2) done.stderr)
  (assert (in "旧い --labels は受け付けない" done.stderr) done.stderr))
