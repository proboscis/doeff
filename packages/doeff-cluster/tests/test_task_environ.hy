;; task(RemoteJob・SubmitDetached)の :environ — 子の環境変数を service の :environ と同じ規則・同じ路で運ぶ(2026-09-28)。
;;
;; 責務:
;;   検め      … 名の形・worker の予約(DOEFF_ ほか)・秘密の中身の名(_TOKEN・_KEY・_SECRET・_PASSWORD で終わり _FILE・_DIR・_PATH で
;;               終わらない)を断る規則は runtime_env_model.EnvVar 1 つ。effect の構成子・coordinator の本文の読み・service の job が同じ規則。
;;   送り手    … task-submit-body・detached-submit-body が本文の environ に載せる(空なら欄を置かない)。
;;   coordinator … TaskRecord.environ に持ち、heartbeat の返事の task の行に載せる。切り離した task の同じ key の送り直しは environ も
;;               比べる(違えば 409)。worker の写しから引き取る時も environ を持つ。欄の無い旧い行は空の environ で読む。
;;   worker    … task-spec が JobSpec.environ に写し、子 process の言い換え(job-launch)が service と同じ路で子の環境変数に置く。
;;   sim       … sim の宿が spec.environ の名の Ask に、本番の土台と同じ読みの定義(host_contract.environ-reader)で答える
;;               (本番の土台の (environ-reader) が外へ通した Ask)。値の字面どおりの読みの契約は test_environ_reader.hy。
(require doeff-hy.macros [deftest defk <- val var])
(import json)
(import os)
(import sys)
(import subprocess)
(import pathlib [Path])
(import httpx)
(import pytest)
(import doeff_time [SimClock])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState TaskRecord])
(import doeff_cluster.coordinator.core.cluster_json [task-record-to-json task-record-from-json])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.core.cluster_policy [adopted-task])
(import doeff_cluster.coordinator.intent.request_bodies [StatusRow])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import tests.link_rig [LinkRig])
(import doeff_cluster.worker.protocol.declared [task-spec] doeff_cluster.worker.core.launch [program-file])
(import tests.host_rig [host-settings launched])
(import doeff_cluster.shared.protocol.detached [detached-submitted detached-submit-body])
(import doeff [with-handlers])
(import doeff_time [sim-time-handler])
(import tests.transport_http [transport-http route-cell detached-sender TEST-ROUTE])
(import doeff_cluster.shared.protocol.remote [task-submit-body])
(import doeff_cluster.shared.intent.remote_model [RemoteJob TaskSucceeded encode-program decode-outcome])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.shared.intent.detached_model [SubmitDetached AwaitDetached DetachedSubmitted DetachedSucceeded])
(import doeff_cluster.shared.intent.runtime_env_model [EnvVar RuntimeEnvInvalid InvalidKind])
(import doeff_cluster.shared.entry.service_build [job system-of])
(import doeff_cluster.shared.intent.service_model [CallShape])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs] doeff_cluster.shared.intent.job_model [JobSpec])
(import doeff_cluster.sim.local [sim-cluster SimWorker])
(import tests.detached_rig [MemoryCoordinator RIG-PROVIDES])
(import tests.program_rows [program-placed])
(import tests.fixtures.entry_programs [environ-read based-add])

(val T (ClusterTiming))
(val V {"python" "3.14.0" "doeff" "1"})
(val LOCAL (frozenset RIG-PROVIDES))
(val NET (frozenset ["net"]))
;; 見本の設定の名(検の process の環境変数に無い名 — sim では (environ-reader) が外へ通し、sim の宿が答える)。
(val URL-NAME "TASK_ENVIRON_ROWS_URL")
(val URL "http://rows.invalid:8080")
(val ROOT (. (Path (os.path.abspath __file__)) parent parent))
(val HY (str (/ (. (Path sys.executable) parent) "hy")))


(defk call [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の純粋な振り分け 1 件(送り手 c-test)→ #(次の状態 status 本文)。"
  (responded state (http-request method path {} body :actor "c-test") now T))


(defk beat [state now]
  {:pre [(: state ClusterState) (: now int)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w(能力 net・版 V・世代 b1)の heartbeat 1 回 → #(次の状態 status 返事)。"
  (<- answer tuple (call state "POST" "/heartbeat"
                         {"name" "w" "provides" ["net"] "capacity" 10 "versions" V "boot" "b1" "statuses" []} now))
  answer)


;; --- 検め: service と task で同じ規則 1 つ ----------------------------------------------------------------

(deftest test-the-task-effects-refuse-reserved-and-secret-names-like-a-service
  ;; 予約の名(DOEFF_・PYTHON・PATH)・秘密の中身の名(_TOKEN ほか)・形の違う名・文字列でない値は、送る前に構成子が断る。
  ;; 置き場を名指す名(_TOKEN_FILE・_KEY_DIR)は通る。既定は空。
  (val program (based-add 1))
  (assert (= (. (RemoteJob program :needs NET) environ) {}))
  (assert (= (. (SubmitDetached program "k" :needs NET) environ) #()))
  (for [bad [{"DOEFF_WORKER_JOB" "x"} {"PYTHONPATH" "/x"} {"PATH" "/bin"} {"ROWS_TOKEN" "t"} {"API_KEY" "k"}
             {"DB_PASSWORD" "p"} {"HOOK_SECRET" "s"} {"lower_case" "x"} {"ROWS_URL" 1}]]
    (with [raised (pytest.raises TypeError)]
      (RemoteJob program :needs NET :environ bad))
    (assert (in "RemoteJob.environ" (str raised.value)) raised.value)
    ;; SubmitDetached は EnvVar の tuple で受ける(#2179)— 名と値の規則は EnvVar を作る所で同じく断る。
    (with [(pytest.raises #(TypeError RuntimeEnvInvalid))]
      (SubmitDetached program "k" :needs NET :environ (tuple (gfor #(k v) (.items bad) (EnvVar :name k :value v))))))
  (val fine {"ROWS_TOKEN_FILE" "/etc/rows/token" "ROWS_KEY_DIR" "/etc/rows" URL-NAME URL})
  (val fine-vars (tuple (gfor k (sorted fine) (EnvVar :name k :value (get fine k)))))
  (assert (= (. (RemoteJob program :needs NET :environ fine) environ) fine))
  (assert (= (. (SubmitDetached program "k" :needs NET :environ fine-vars) environ) fine-vars)))


(deftest test-submit-detached-refuses-a-mapping-and-a-repeated-name
  ;; 失敗ケース(#2179): SubmitDetached.environ に名 → 値の写像をそのまま渡すと作る時に断る(型の組だけを受ける)。名が重なる組も
  ;; 断る(本文の写像に綴ると片方が黙って消える)。
  (val program (based-add 1))
  (with [raised (pytest.raises TypeError)]
    (SubmitDetached program "k" :needs NET :environ {URL-NAME URL}))
  (assert (in "EnvVar の tuple" (str raised.value)) raised.value)
  (with [raised (pytest.raises TypeError)]
    (SubmitDetached program "k" :needs NET :environ #((EnvVar :name URL-NAME :value URL) (EnvVar :name URL-NAME :value "other"))))
  (assert (in "名が重なる" (str raised.value)) raised.value))


(deftest test-the-secret-name-rule-is-the-one-of-env-var-and-the-service-environ
  ;; 同じ規則 1 つ: 実行環境の env-vars(EnvVar)と service の :environ も秘密の中身の名を断る。
  (with [raised (pytest.raises RuntimeEnvInvalid)]
    (EnvVar :name "ROWS_TOKEN" :value "t"))
  (assert (= raised.value.kind InvalidKind.SECRET-ENV-VAR) raised.value)
  (assert (= (. (EnvVar :name "ROWS_TOKEN_FILE" :value "/t") name) "ROWS_TOKEN_FILE"))
  (val program (based-add 1))
  (with [(pytest.raises RuntimeEnvInvalid)]
    (job "svc" program :call (CallShape :function based-add :args [1] :kwargs {}) :needs #{"net"}
         :environ {"ROWS_TOKEN" "t"})))


(deftest test-the-coordinator-refuses-a-task-body-with-a-reserved-or-secret-name
  ;; POST /tasks・PUT /detached のどちらも、本文の environ の誤りは 400 と理由で、状態を変えない。実行環境の env-vars と同じ名も断る
  ;; (子の環境変数の足し口を 1 つにする — service の宣言の行と同じ)。
  (<- placed tuple (program-placed (ClusterState) V))
  (val state (get placed 0))
  (val sha (get placed 1))
  (val base {"program" sha "revision" "r" "needs" ["net"] "leaseSeconds" 10.0})
  (for [#(method path) [#("POST" "/tasks") #("PUT" "/detached/job-a")]]
    (for [#(environ word) [#({"DOEFF_WORKER_JOB" "x"} "DOEFF_WORKER_JOB")
                           #({"ROWS_TOKEN" "t"} "ROWS_TOKEN")
                           #({"ROWS_URL" 1} "文字列")
                           #(["ROWS_URL"] "object")]]
      (<- refused tuple (call state method path (| base {"environ" environ}) 10))
      (assert (= (get refused 1) 400) #(method environ refused))
      (assert (in word (get refused 2 "error")) #(method environ refused))
      (assert (= (get refused 0) state) #(method environ)))))


;; --- 送り手 → coordinator → heartbeat の返事 → worker の子の環境 -------------------------------------------------

(deftest test-the-sender-bodies-carry-the-environ-only-when-given
  (val with-env (task-submit-body (* "a" 64) "r" NET "n" 10.0 None {URL-NAME URL}))
  (assert (= (get with-env "environ") {URL-NAME URL}) with-env)
  (assert (not-in "environ" (task-submit-body (* "a" 64) "r" NET "n" 10.0 None {})))
  (val detached (detached-submit-body (* "a" 64) "r" NET "n" 10.0 60.0 None {URL-NAME URL}))
  (assert (= (get detached "environ") {URL-NAME URL}) detached)
  (assert (not-in "environ" (detached-submit-body (* "a" 64) "r" NET "n" 10.0 60.0 None {}))))


(deftest test-the-coordinator-carries-the-environ-to-the-worker-child-like-a-service [tmp-path]
  ;; 両方の task の行が environ を持ち、heartbeat の返事の行に載り、worker の task-spec が JobSpec.environ に写し、ProcessHost が
  ;; service と同じ路で子の環境変数に置く。environ の無い task の行と返事は欄を持たない(以前と同じ形)。
  (<- worker tuple (beat (ClusterState) 0))
  (<- placed tuple (program-placed (get worker 0) V))
  (val sha (get placed 1))
  (val body {"program" sha "revision" "r" "needs" ["net"] "leaseSeconds" 10.0 "environ" {URL-NAME URL}})
  (<- remote tuple (call (get placed 0) "POST" "/tasks" body 10))
  (<- detached tuple (call (get remote 0) "PUT" "/detached/job-e" body 10))
  (<- plain tuple (call (get detached 0) "POST" "/tasks" (| body {"environ" {}}) 10))
  (<- reply tuple (beat (get plain 0) 20))
  (val rows (dfor row (get reply 2 "tasks") (get row "id") row))
  (for [id [(get remote 2 "task") (get detached 2 "task")]]
    (assert (= (. (get (. (get reply 0) tasks) id) environ) #(#(URL-NAME URL))))
    (assert (= (get rows id "environ") {URL-NAME URL}) (get rows id)))
  (assert (not-in "environ" (get rows (get plain 2 "task"))) rows)
  (val spec (task-spec (get rows (get remote 2 "task")) (/ tmp-path "tasks")))
  (assert (= spec.environ #(#(URL-NAME URL))) spec)
  (<- settings (host-settings tmp-path))
  (<- plan tuple (launched settings spec (str tmp-path) "1-1" 1))
  (assert (= (get (get plan 2) URL-NAME) URL)))


(deftest test-the-same-key-with-another-environ-is-other-work
  ;; 冪等の鍵: 同じ key・同じ environ の送り直しは同じ行(created = false)、違う environ は 409(別の仕事 — Program の読む設定が違う)。
  (<- placed tuple (program-placed (ClusterState) V))
  (val state (get placed 0))
  (val sha (get placed 1))
  (val body {"program" sha "revision" "r" "needs" ["net"] "environ" {URL-NAME URL}})
  (<- first tuple (call state "PUT" "/detached/job-k" body 10))
  (assert (= (get first 1) 200) first)
  (<- again tuple (call (get first 0) "PUT" "/detached/job-k" body 11))
  (assert (= (get again 1) 200) again)
  (assert (not (get again 2 "created")) again)
  (for [other [{URL-NAME "http://other.invalid"} {}]]
    (<- clash tuple (call (get first 0) "PUT" "/detached/job-k" (| body {"environ" other}) 12))
    (assert (= (get clash 1) 409) #(other clash))
    (assert (in URL-NAME (get clash 2 "error")) clash)))


(deftest test-saved-rows-keep-the-environ-and-old-rows-read-as-empty
  ;; 保存の往復で environ が残る。欄の無い行(2026-09-28 より前)は空の environ で読み、旧い形とは数えない(終わっていない行も failed に
  ;; しない)。
  (val row (TaskRecord "t1" "n" (* "a" 64) "r" #() #("net") 1000 2000 0 :environ #(#(URL-NAME URL))))
  (val saved (task-record-to-json row))
  (assert (= (get saved "environ") {URL-NAME URL}) saved)
  (assert (= (task-record-from-json (json.loads (json.dumps saved))) row))
  (val old (dfor #(k v) (.items saved) :if (!= k "environ") k v))
  (val read (task-record-from-json old))
  (assert (= read.environ #()) read)
  (assert (= read.phase "queued") read))


(deftest test-a-saved-row-with-a-wrongly-typed-field-names-the-field
  ;; 保存の行の欄は型を確かめて読む(#1662)。以前は #** で辞書を渡していたので、型の違う値(文字列の lease_ms・null の
  ;; revision)が黙って欄に入り、使う所で初めて落ちた。どの欄がどう違うかを名乗る ValueError にする。必須の欄が無い行も同じ。
  (val saved (json.loads (json.dumps (task-record-to-json (TaskRecord "t1" "n" (* "a" 64) "r" #() #("net") 1000 2000 0)))))
  (for [#(key value words) [#("lease_ms" "1000" "lease_ms は整数") #("revision" None "revision は文字列")
                            #("detached" "yes" "detached は真偽値") #("started_ms" True "started_ms は整数か null")
                            #("avoid" "w1" "avoid は配列") #("runtime_env" [] "runtime_env は object か null")]]
    (with [raised (pytest.raises ValueError)]
      (task-record-from-json (| saved {key value})))
    (assert (in words (str raised.value)) #(key raised.value)))
  (with [raised (pytest.raises ValueError)]
    (task-record-from-json (dfor #(k v) (.items saved) :if (!= k "submitted_ms") k v)))
  (assert (in "submitted_ms が無い" (str raised.value)) raised.value))


(deftest test-an-adopted-detached-task-keeps-its-environ
  ;; 状態を失った coordinator が worker の写しから引き取る行も、写しの environ を持つ(担い手の子は同じ環境で走っている)。
  (val echo {"id" "t9" "name" "n" "detached" True "key" "job-z" "leaseMs" 1000 "retainMs" 0 "revision" "r"
             "needs" ["net"] "versions" V "program" (* "a" 64) "environ" {URL-NAME URL}})
  (val task (adopted-task (ClusterState) "w" "b1" (StatusRow :name "task/t9" :phase "running" :task echo) 5))
  (assert (is-not task None))
  (assert (= task.environ #(#(URL-NAME URL))) task))


(deftest test-a-detached-task-child-answers-the-environ-name-through-the-environ-reader [tmp-path]
  ;; 本番の形の通し: 本物の送り手(detached-submitted)が :environ つきで送り、本物の coordinator の判断(MemoryCoordinator)が返事に載せ、本物の
  ;; coordinator への口が Program を cache へ取り、ProcessHost が組んだ子の環境で job_entry の task 入口の子 process が走る。
  ;; Program の名の Ask に (environ-reader)(本番の土台の読み)が environ の値で答える。
  (val coordinator (MemoryCoordinator (SimClock)))
  (val transport (httpx.MockTransport coordinator.handle))
  (val link (LinkRig "http://coordinator" "w1" RIG-PROVIDES 10 60000 :task-dir (str (/ tmp-path "state" "tasks"))
                             :versions (current-versions) :transport transport))
  (.poll link)
  (<- submitted (with-handlers [(sim-time-handler :clock (SimClock)) (transport-http transport)]
                  (detached-submitted (route-cell) TEST-ROUTE (detached-sender "r") "job-env" (encode-program (environ-read URL-NAME)) LOCAL
                                      "env" 60.0 600.0 {URL-NAME URL})))
  (assert submitted.created submitted)
  (val desired (.poll link))
  (assert (isinstance desired DesiredJobs) desired)
  (val spec (next (gfor j desired.jobs :if (.startswith j.name "task/") j)))
  (assert (= spec.environ #(#(URL-NAME URL))) spec)
  (<- settings (host-settings (/ tmp-path "state")))
  (<- planned tuple (launched settings spec (str tmp-path) "1-1" 1))
  (val env (| (get planned 2) {"PYTHONPATH" (str ROOT)}))
  (assert (not-in URL-NAME os.environ))
  (val done (subprocess.run [HY "-m" spec.entry #* spec.args "--program" (str (program-file (.program-dir link) spec.program))]
                            :cwd (str ROOT) :env env :capture-output True :text True :timeout 120))
  (assert (= done.returncode 0) done.stderr)
  (val outcome (decode-outcome (.read-text (Path (get spec.args 2)) :encoding "ascii")))
  (assert (= outcome (TaskSucceeded URL)) outcome))


;; --- sim: 同じ Program を sim-cluster の task で -----------------------------------------------------------------

(val NO-JOBS (system-of "environ-scenarios" #()))


(defk sim-environ-scenario []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "program"}}
  "筋書き: 同じ Program((environ-reader) で名を読む)を RemoteJob と SubmitDetached で :environ つきで送り、答えを返す。"
  (<- remote str (RemoteJob (environ-read URL-NAME) :needs LOCAL :environ {URL-NAME URL}))
  (<- submitted DetachedSubmitted (SubmitDetached (environ-read URL-NAME) :key "sim-env" :needs LOCAL
                                                  :environ #((EnvVar :name URL-NAME :value "http://detached.invalid"))))
  (<- awaited (AwaitDetached "sim-env"))
  #(remote submitted awaited))


(deftest test-a-sim-task-child-answers-the-environ-name-from-the-host
  ;; sim の子では (environ-reader) が環境に無い名を外へ通し、sim の宿が同じ読みの定義で spec.environ から答える(本番と同じ Program・同じ :environ)。
  (<- answer tuple (sim-cluster NO-JOBS (sim-environ-scenario) :workers #((SimWorker :name "w1" :provides LOCAL))))
  (assert (= (get answer 0) URL) answer)
  (assert (= (get answer 1) (DetachedSubmitted "sim-env" True)) answer)
  (assert (= (get answer 2) (DetachedSucceeded "http://detached.invalid")) answer))
