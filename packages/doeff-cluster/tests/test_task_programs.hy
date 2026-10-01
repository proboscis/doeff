;;; task の Program も置き場 /programs/<sha> で運ぶ(ADR-DOE-CLUSTER-001 R3b — service と task で運び方を分けない・operator 逐語
;;; "i dont find any reason to have different api for services")。
;;;
;;;   送り手  … remote-cluster(remote.hy の program-put)・DetachedSender は詰めた Program を先に PUT /programs/<sha>(本文 {"blob" "versions"})で置き、task の本文
;;;             (POST /tasks・PUT /detached/<key>)は program に sha を書く。本文の blob・versions は 400(理由つき)。
;;;   coordinator … 置き場に sha が在る時だけ task を受け、task の版は置き場の版。heartbeat の返事は sha だけを運ぶ。掃除は task の行
;;;             (終わって結果を保持している行も)が参照する sha を残し、行が消えたら猶予の後に消す。状態を失った coordinator は worker の
;;;             写しの sha で走っている切り離した task を引き取る。保存の旧い行(blob を持つ)は終わっていなければ failed。
;;;   worker  … accept-programs が service と同じ仕組みで task の Program を cache へ取り、子は `task --result <file> --program <file>`。
;;;             返事から外れた task の Program の cache は消す(service の job の Program は消さない)。
(require doeff-hy.macros [deftest defk deff <- val var])
(import json)
(import os)
(import subprocess)
(import sys)
(import time)
(import pathlib [Path])
(import httpx)
(import doeff_cluster.shared.intent.protocol [ClusterTiming Request])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.foundation.coordinator_inbox [http-request])
(import doeff_cluster.coordinator.core.cluster_policy [state-to-json state-from-json])
(import doeff_cluster.coordinator.core.durable_kv [full-kv state-from-kv])
(import doeff_cluster.coordinator.core.api_policy [tick])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.coordinator.core.program_policy [PROGRAM-GRACE-MS])
(import tests.host_rig [host-settings launched])
(import tests.link_rig [LinkRig])
(import tests.link_rig [write-program-file] doeff_cluster.worker.core.launch [program-file])
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff [Program with-handlers])
(import doeff_core_effects.handlers [await-handler slog-handler])
(import doeff_core_effects.http_handlers [http-production-handler])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_time [async-time-handler])
(import doeff_cluster.shared.protocol.coordinator_route [CoordinatorRoute RouteCell RouteOptions route-of])
(import doeff_cluster.shared.protocol.remote [TaskSender task-submitted task-view task-dropped])
(import doeff_cluster.shared.intent.remote_model [TaskSucceeded encode-program decode-outcome program-sha])
(import doeff_cluster.foundation.process_versions [current-versions])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs JobStatus] doeff_cluster.shared.intent.job_model [JobSpec JobPhase])
(import doeff_cluster.coordinator.core.cluster_policy [JOB-ENTRY])
(import tests.program_rows [SAMPLE-TASK-PROGRAM program-placed])
(import tests.fixtures.entry_programs [based-add])

(val T (ClusterTiming))
(val V {"python" "3.14.0" "doeff" "1"})
(val OTHER {"python" "3.9.6" "doeff" "0"})
(val PACKAGE-ROOT (. (Path __file__) (resolve) parent parent))


(defk call [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の純粋な振り分け 1 件(送り手 c-test)→ #(次の状態 status 本文)。"
  (responded state (http-request method path {} body :actor "c-test") now T))


(defk beat [state now statuses]
  {:pre [(: state ClusterState) (: now int) (: statuses list)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w(能力 net・版 V・世代 b1)の heartbeat 1 回 → #(次の状態 status 返事)。"
  (<- answer tuple (call state "POST" "/heartbeat"
                         {"name" "w" "provides" ["net"] "capacity" 10 "versions" V "boot" "b1" "statuses" statuses} now))
  answer)


;; --- coordinator: 本文は置き場のキーだけを運ぶ -----------------------------------------------------------------

(deftest test-a-task-body-carries-only-the-key-of-a-placed-program
  ;; POST /tasks・PUT /detached のどちらも: 本文の blob(旧い形)・versions(版の写し)・置き場のキーの欠けと形の誤り・置き場に無い sha は
  ;; 400 と理由で、状態を変えない。置いた sha は通り、task の行は sha と置き場の版を持つ。
  (<- placed tuple (program-placed (ClusterState) V))
  (val state (get placed 0))
  (val sha (get placed 1))
  (val base {"revision" "r" "needs" ["net"] "leaseSeconds" 10.0})
  (val bad [#({"blob" "QkxPQg==" "versions" V} "旧い形の blob")
            #({"program" sha "blob" "QkxPQg=="} "旧い形の blob")
            #({"program" sha "versions" V} "本文の versions")
            #({} "program は詰めた Program の置き場のキー")
            #({"program" "not-a-sha"} "program は詰めた Program の置き場のキー")
            #({"program" (* "f" 64)} "置き場に無い")])
  (for [#(method path) [#("POST" "/tasks") #("PUT" "/detached/job-a")]]
    (for [#(extra word) bad]
      (<- refused tuple (call state method path (| base extra) 10))
      (assert (= (get refused 1) 400) #(method extra refused))
      (assert (in word (get refused 2 "error")) #(method extra refused))
      (assert (= (get refused 0) state) #(method extra)))
    (<- accepted tuple (call state method path (| base {"program" sha}) 10))
    (assert (= (get accepted 1) 200) #(method accepted))
    (val row (next (gfor t (.values (. (get accepted 0) tasks)) t)))
    (assert (= row.program sha) row)
    ;; task の版は置き場に Program と一緒に置いた版(本文は版を運ばない)。
    (assert (= (dict row.versions) V) row.versions)))


(deftest test-the-task-versions-come-from-the-placed-program
  ;; 置く worker との版の突き合わせ(cloudpickle は版をまたいで復元できる保証が無い)は、置き場の版で行う。
  (<- worker tuple (beat (ClusterState) 0 []))
  (<- same tuple (program-placed (get worker 0) V "c2FtZQ=="))
  (<- other tuple (program-placed (get same 0) OTHER "b3RoZXI="))
  (<- a tuple (call (get other 0) "POST" "/tasks" {"program" (get same 1) "revision" "r" "needs" ["net"]} 10))
  (<- b tuple (call (get a 0) "POST" "/tasks" {"program" (get other 1) "revision" "r" "needs" ["net"]} 10))
  (val tasks (. (get b 0) tasks))
  (assert (= (. (get tasks (get a 2 "task")) phase) "assigned") tasks)
  (val refused (get tasks (get b 2 "task")))
  (assert (= refused.phase "failed") refused)
  (assert (in "python=3.14.0" refused.detail) refused.detail))


(deftest test-the-heartbeat-reply-carries-only-the-program-key-of-each-task
  (<- worker tuple (beat (ClusterState) 0 []))
  (<- placed tuple (program-placed (get worker 0) V))
  (<- remote tuple (call (get placed 0) "POST" "/tasks" {"program" (get placed 1) "revision" "r" "needs" ["net"]} 10))
  (<- detached tuple (call (get remote 0) "PUT" "/detached/job-b" {"program" (get placed 1) "revision" "r" "needs" ["net"]} 10))
  (<- reply tuple (beat (get detached 0) 20 []))
  (val rows (get reply 2 "tasks"))
  (assert (= (len rows) 2) rows)
  (for [row rows]
    (assert (= (get row "program") (get placed 1)) row)
    (assert (not-in "blob" row) row)))


;; --- coordinator: 置き場の掃除は task の行の参照も数える -----------------------------------------------------------

(deftest test-the-sweep-keeps-the-programs-that-task-rows-reference
  ;; 参照の無い Program は置いてから PROGRAM-GRACE-MS の後に消える。task の行(待ち・走っている・終わって結果を保持している)が参照する
  ;; Program は残り、行が消えたら猶予の後に消える。
  (val later (+ PROGRAM-GRACE-MS 1000))
  (<- worker tuple (beat (ClusterState) 0 []))
  (<- kept tuple (program-placed (get worker 0) V "a2VwdA=="))
  (<- remote-program tuple (program-placed (get kept 0) V "cmVtb3Rl"))
  (<- loose tuple (program-placed (get remote-program 0) V "bG9vc2U="))
  (<- detached tuple (call (get loose 0) "PUT" "/detached/job-k"
                           {"program" (get kept 1) "revision" "r" "needs" ["net"] "leaseSeconds" 60.0
                            "retainSeconds" (/ (* 3 PROGRAM-GRACE-MS) 1000)} 10))
  (val id (get detached 2 "task"))
  (<- remote tuple (call (get detached 0) "POST" "/tasks"
                         {"program" (get remote-program 1) "revision" "r" "needs" ["net"] "leaseSeconds" 3600.0} 10))
  ;; 担い手が終わりを報告する(切り離した task は結果を保持する)。
  (<- finished tuple (beat (get remote 0) 1000 [{"name" (+ "task/" id) "phase" "finished" "result" "R" "detail" ""}]))
  (<- alive tuple (beat (get finished 0) (- later 500) []))
  (val swept (tick (get alive 0) later T))
  (assert (= (. (get swept.tasks id) phase) "finished"))
  (assert (in (get kept 1) swept.programs) "結果を保持している task の Program が消えた")
  (assert (in (get remote-program 1) swept.programs) "走っている task の Program が消えた")
  (assert (not-in (get loose 1) swept.programs) "参照の無い Program が猶予の後も残った")
  ;; 行が消えたら(保持を解く・呼び手が task を落とす)、置いてから猶予を過ぎた Program は消える。
  (<- released tuple (call swept "DELETE" "/detached/job-k" None (+ later 1000)))
  (<- dropped tuple (call (get released 0) "DELETE" (+ "/tasks/" (get remote 2 "task")) None (+ later 1000)))
  (val gone (tick (get dropped 0) (+ later 2000) T))
  (assert (not-in (get kept 1) gone.programs) gone.programs)
  (assert (not-in (get remote-program 1) gone.programs) gone.programs))


;; --- coordinator: 状態を失った coordinator の引き取り・保存の旧い行 ---------------------------------------------

(deftest test-an-empty-coordinator-adopts-a-running-detached-task-by-its-program-key [tmp-path]
  ;; worker は切り離した task の返事の行(置き場のキー program を含む)を状態の報告に写す。状態を失った coordinator は同じ sha の行を
  ;; 引き取り、同じ返事に載せる(担い手の cache の Program で走り続ける — 置き場に Program が無くてよい)。
  (<- worker tuple (beat (ClusterState) 0 []))
  (<- placed tuple (program-placed (get worker 0) V))
  (<- put tuple (call (get placed 0) "PUT" "/detached/job-c" {"program" (get placed 1) "revision" "r" "needs" ["net"]} 10))
  (val id (get put 2 "task"))
  (<- reply tuple (beat (get put 0) 20 []))
  (val link (LinkRig "http://127.0.0.1:9" "w" #() 10 60000 :task-dir (str (/ tmp-path "tasks"))))
  (.accept-tasks link (get reply 2 "tasks"))
  (val row (get (.report link #((JobStatus (+ "task/" id) JobPhase.RUNNING "r" "r" 42 1))) 0))
  (assert (= (get row "task" "program") (get placed 1)) row)
  (<- fresh tuple (beat (ClusterState) 5000 [row]))
  (assert (= (. (get (. (get fresh 0) tasks) id) program) (get placed 1)))
  (assert (= (lfor t (get fresh 2 "tasks") (get t "program")) [(get placed 1)]) (get fresh 2))
  (assert (= (. (get fresh 0) programs) {}) "置き場を失った coordinator は Program を持たない")
  ;; 写しに置き場のキーが無い(blob を運んでいた旧い worker)・形の違う報告は引き取らない。
  (for [echo [(dfor #(k v) (.items (get row "task")) :if (!= k "program") k v)
              (| (get row "task") {"program" "not-a-sha"})
              (| (get row "task") {"blob" "QkxPQg=="} {"program" None})]]
    (<- refused tuple (beat (ClusterState) 5000 [(| row {"task" echo})]))
    (assert (= (get refused 2 "tasks") []) echo)
    (assert (not-in id (. (get refused 0) tasks)) echo)))


(deftest test-saved-task-rows-that-carry-a-blob-are-read-without-a-program
  ;; 置き場 /programs の前の coordinator が書いた task の行(詰めた Program を行の blob に持つ)は、読み直しで coordinator を落とさない:
  ;; 終わっていない行は failed(理由つき — 走らせない)、終わった行は Program 無し(program None)で結果を保つ。
  (<- worker tuple (beat (ClusterState) 0 []))
  (<- placed tuple (program-placed (get worker 0) V))
  (<- open tuple (call (get placed 0) "PUT" "/detached/job-open" {"program" (get placed 1) "revision" "r" "needs" ["net"]} 10))
  (<- done tuple (call (get open 0) "PUT" "/detached/job-done" {"program" (get placed 1) "revision" "r" "needs" ["net"]} 10))
  (val done-id (get done 2 "task"))
  (<- reported tuple (beat (get done 0) 20 [{"name" (+ "task/" done-id) "phase" "finished" "result" "R" "detail" ""}]))
  (val data (state-to-json (get reported 0)))
  (val old (| data {"tasks" (lfor t (get data "tasks")
                                  (| (dfor #(k v) (.items t) :if (!= k "program") k v) {"blob" "QkxPQg=="}))}))
  (for [again [(state-from-json old 100) (state-from-kv (full-kv (state-from-json old 100)) 100)]]
    (val rows (dfor t (.values again.tasks) t.key t))
    (assert (= (. (get rows "job-open") phase) "failed") (get rows "job-open"))
    (assert (in "旧い形の task(詰めた Program を行に持つ blob)" (. (get rows "job-open") detail)))
    (assert (= #((. (get rows "job-done") phase) (. (get rows "job-done") result)) #("finished" "R")) (get rows "job-done"))
    (assert (= (lfor t (.values again.tasks) t.program) [None None]) again.tasks)))


;; --- worker: task の Program も accept-programs の同じ仕組み ------------------------------------------------------

(val TASK-BLOB "dGFzay1wcm9ncmFt")
(val TASK-SHA (program-sha TASK-BLOB))
(val SERVICE-BLOB "c2VydmljZS1wcm9ncmFt")
(val SERVICE-SHA (program-sha SERVICE-BLOB))


(defk served-programs [seen]
  {:pre [(: seen list)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の /programs/<sha> の偽の口(task と service の 2 つを持つ・取った path を seen に積む)。"
  (val table {TASK-SHA TASK-BLOB SERVICE-SHA SERVICE-BLOB})
  (deff answer [request]  ; defk にできない: httpx の MockTransport が呼ぶ素の callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "entry"}}
    "置き場の読み 1 件の答え(在れば {\"blob\" \"versions\"}・無ければ 404)。"
    (.append seen request.url.path)
    (let [sha (get (.split request.url.path "/") -1)]
      (if (in sha table)
          (httpx.Response 200 :json {"blob" (get table sha) "versions" V})
          (httpx.Response 404 :json {"error" "置かれていない"}))))
  (httpx.MockTransport answer))


(deftest test-the-worker-fetches-a-task-program-like-a-service-and-drops-it-when-the-task-leaves [tmp-path]
  (val seen [])
  (<- transport httpx.MockTransport (served-programs seen))
  (val state-dir (/ tmp-path "state"))
  (val link (LinkRig "http://coord" "w" #("net") 10 60000 :task-dir (str (/ state-dir "tasks")) :transport transport))
  (<- host (host-settings state-dir))
  (val service (JobSpec "svc" JOB-ENTRY #("service" "--identity" (* "0" 16)) "rev1" :program SERVICE-SHA))
  (val tasks (.accept-tasks link [{"id" "t1" "name" "n" "revision" "r" "versions" V "program" TASK-SHA}]))
  (.accept-programs link (+ #(service) tasks))
  ;; service と同じく cache の file({"blob" "versions"})に取る(返事の行は Program を運ばない)。
  (assert (= (sorted seen) (sorted [(+ "/programs/" TASK-SHA) (+ "/programs/" SERVICE-SHA)])) seen)
  (val cached (program-file (.program-dir link) TASK-SHA))
  (assert (= (json.loads (.read-text cached :encoding "utf-8")) {"blob" TASK-BLOB "versions" V}))
  ;; 子の入口は `task --result <file> --program <cache の file>`(宿の契約の Program の path も同じ file)。
  (<- planned tuple (launched host (get tasks 0) (str tmp-path) "1-1" 1))
  (assert (= (list (cut (get planned 0) -5 None))
             ["task" "--result" (str (/ state-dir "tasks" "t1.result")) "--program" (str cached)])
          (get planned 0))
  (assert (= (get (get planned 2) HOST-CONTRACT.program-env) (str cached)))
  ;; 在る物は取り直さない。
  (.clear seen)
  (.accept-programs link (+ #(service) tasks))
  (assert (= seen []) seen)
  ;; task が返事から外れたら、その Program の cache を消す(service の job の Program は消さない)。
  (.accept-tasks link [])
  (.accept-programs link #(service))
  (assert (not (.exists cached)) "返事から外れた task の Program の cache が残った")
  (assert (.exists (program-file (.program-dir link) SERVICE-SHA)) "service の job の Program の cache が消えた")
  (assert (= (list (.iterdir (/ state-dir "tasks"))) []))
  ;; service の job と同じ Program を指す task は、task が外れても今の job が参照するので残す。
  (val shared (.accept-tasks link [{"id" "t2" "name" "n" "revision" "r" "versions" V "program" SERVICE-SHA}]))
  (.accept-programs link (+ #(service) shared))
  (.accept-tasks link [])
  (.accept-programs link #(service))
  (assert (.exists (program-file (.program-dir link) SERVICE-SHA))))


;; --- 通しの検: 本物の coordinator の process・本物の送り手(remote.hy の task-submitted ほか)・coordinator への口・job_entry の子 process ----

;; 共有の coordinator の上で他の検の worker に置かれないよう、この検だけの能力を要る(名も他の検と重ならない)。
(val NEED "served-task-program-e2e")
(val WORKER "served-task-program-worker")


(defk assigned-task [link id]
  {:pre [(: link LinkRig) (: id str)] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "heartbeat を送り、返事にこの task が載るまで待つ(載った JobSpec・30 秒で断念)。"
  (val deadline (+ (time.monotonic) 30))
  (var found None)
  (while (and (is found None) (< (time.monotonic) deadline))
    (val desired (.poll link))
    (assert (isinstance desired DesiredJobs) desired)
    (:= found (next (gfor j desired.jobs :if (= j.name (+ "task/" id)) j) None))
    (when (is found None) (time.sleep 0.2)))
  (assert (is-not found None) "30 秒のうちに返事に task が載らなかった")
  found)


(val SEND-OPTIONS (RouteOptions :reply-seconds 15.0 :connect-seconds 2.0 :connect-retries 4 :recheck-ms 60000 :actor "served-task-program"))


(defk over-network [program]
  {:pre [(: program Program)] :post [(: % "program の答え")] :tags {:context "doeff-cluster-test" :role "foundation"}}
  "送り手の Program を本物の網の上で走らせるため(本番の土台と同じ HTTP の答え手・await・壁の時計・log の答え手)。"
  (<- answer (scheduled (with-handlers [(await-handler) slog-handler (http-production-handler) (async-time-handler)] program)))
  answer)


(defk finished-view [cell link id]
  {:pre [(: cell RouteCell) (: link LinkRig) (: id str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "担い手が終わりの報告を送り、呼び手の問い合わせが finished を返すまで待つ(その答え・30 秒で断念)。"
  (val deadline (+ (time.monotonic) 30))
  (var view {})
  (while (and (!= (.get view "phase") "finished") (< (time.monotonic) deadline))
    (.poll link)
    (<- seen dict (over-network (task-view cell SEND-OPTIONS id)))
    (:= view seen)
    (when (!= (.get view "phase") "finished") (time.sleep 0.2)))
  view)


(deftest test-a-task-program-reaches-the-worker-and-job-entry-writes-its-result [served-coordinator tmp-path]
  ;; 送り手(task-submitted)が Program を置き場に置いて sha だけの task を出し、worker(本物の coordinator への口)が返事の sha の Program を
  ;; cache へ取り、job_entry の task 入口の子 process が走らせて結果の file を書き、終わりの報告で呼び手に結果が届く。
  ;; fixture の値は検査器から型が見えない(repo の fixture は object)— conftest の served_coordinator の答え(str)をここで確かめる(test_served_program.hy と同じ)。
  (assert (isinstance served-coordinator str) served-coordinator)
  (val link (LinkRig served-coordinator WORKER #(NEED) 10 60000
                             :task-dir (str (/ tmp-path "state" "tasks")) :versions (current-versions)))
  (val sender (TaskSender :revision "r-served" :versions (current-versions) :runtime-env None))
  (<- route CoordinatorRoute (route-of served-coordinator (int (* (time.time) 1000))))
  (val cell (RouteCell route))
  ;; 担い手を先に名乗らせる(置ける worker の無い task は置かれずに失敗する)。
  (.poll link)
  (val blob (encode-program (based-add 3)))
  (<- id str (over-network (task-submitted cell SEND-OPTIONS sender blob (frozenset [NEED]) "served-task" 60.0 {})))
  (try
    (do
      (<- spec JobSpec (assigned-task link id))
      (assert (= spec.program (program-sha blob)) spec)
      (assert (is-not spec.program None) spec)
      (val cached (program-file (.program-dir link) spec.program))
      (assert (= (json.loads (.read-text cached :encoding "utf-8")) {"blob" blob "versions" (current-versions)}))
      ;; 子 process: worker と同じ引数(task --result <file>)に cache の file を --program で渡す(ProcessHost が足すのと同じ)。
      (val done (subprocess.run [sys.executable "-m" "hy" "-m" spec.entry #* spec.args "--program" (str cached)]
                                :cwd (str PACKAGE-ROOT) :capture-output True :text True :timeout 120
                                :env (| (dict os.environ) {"PYTHONPATH" (str PACKAGE-ROOT) "DOEFF_WORKER_JOB" spec.name})))
      (assert (= done.returncode 0) done.stderr)
      (assert (in "TaskSucceeded" done.stderr) done.stderr)
      (setv link.state.statuses (.report link #((JobStatus spec.name JobPhase.FINISHED "r-served" "r-served" None 1))))
      (<- view dict (finished-view cell link id))
      (assert (= (get view "phase") "finished") view)
      (val outcome (decode-outcome (get view "result")))
      (assert (= outcome (TaskSucceeded 103)) outcome))
    (finally
      (<- (over-network (task-dropped cell SEND-OPTIONS id))))))
