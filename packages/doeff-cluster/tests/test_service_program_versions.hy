;;; service の Program の cache は (Program のキー・宣言の版) ごと(card ki-172e63fed4c7 — task の #3762 と同じ規則)。
;;;
;;; 置き場のキーは詰めた Program の sha256 なので、同じ Program を python の版の違う送り手が宣言し直すと、sha は同じで版だけが替わる
;;; (2026-10-10 13:58 — merge-queue の系 3 つを 3.14.7 の宣言の道具で詰めた後に 3.14.3t で宣言し直した)。worker の cache の file が
;;; sha だけで決まると、worker は前の版の file を持ち続け、子の入口は「版が違うので Program を解かない」で落ち続けた(14:00〜14:05・
;;; 配備の担当が cache を名で消して直した)。coordinator は Service の宣言の行の版(run.versions)を job の行に載せ、worker は task と
;;; 同じく版ごとの file に取る — 版が替われば別の file なので、取り直しの if を足さずに新しい版の Program を取る。
(require doeff-hy.macros [deftest defk deff <- val])
(import json)
(import os)
(import httpx)
(import doeff [run])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])
(import doeff_cluster.coordinator.intent.cluster_model [ClusterState])
(import doeff_cluster.shared.protocol.inbox [http-request])
(import doeff_cluster.coordinator.protocol.request_bodies [responded])
(import doeff_cluster.worker.protocol.declared [declared-reply-of-json declared-job-specs])
(import doeff_cluster.worker.core.launch [spec-program-file])
(import doeff_cluster.worker.entry.job_entry [read-program])
(import doeff_cluster.shared.intent.remote_model [VersionMismatch])
(import doeff_cluster.shared.protocol.program_codec [encode-program])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.foundation.process_versions [process-versions])
(import tests.link_rig [LinkRig])
(import tests.fixtures.entry_programs [based-add])

(val T (ClusterTiming))
;; 前の宣言の送り手の版(この process の版と食い違う — 本番の 3.14.7)。
(val OLD {"python" "3.9.6" "doeff" "0"})


(defk call [state method path body now]
  {:pre [(: state ClusterState) (: method str) (: path str) (: body (| dict None)) (: now int)] :post [(: % tuple)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の純粋な振り分け 1 件(送り手 c-test)→ #(次の状態 status 本文)。"
  (responded state (! (http-request method path {} body :actor "c-test")) now T))


(defk coordinator-programs [cell]
  {:pre [(: cell list)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の GET /programs/<sha> を、cell の先頭に持つ coordinator の状態の本物の振り分けで答える偽の網(状態は検が差し替える)。"
  (deff answer [request]  ; defk にできない: httpx の MockTransport が呼ぶ素の callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "entry"}}
    "Program の読み 1 件を coordinator の振り分けに渡し、その status と本文で答える。"
    (let [answered (run (call (get cell 0) "GET" request.url.path None 0))]
      (httpx.Response (get answered 1) :json (get answered 2))))
  (httpx.MockTransport answer))


(defk service-row [sha versions revision]
  {:pre [(: sha str) (: versions dict) (: revision str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言の道具が書く Program の job の Service の spec(run の versions = 詰めた送り手の版 — 置き場へ Program と一緒に置く版と同じ)。"
  {"run" {"kind" "service" "program" sha "identity" {"function" "m:f" "args" [] "kwargs" {}} "versions" versions "describe" "m:f()"}
   "revision" revision "needs" ["net"]})


(defk declared [state sha versions now]
  {:pre [(: state ClusterState) (: sha str) (: versions dict) (: now int)] :post [(: % ClusterState)]
   :tags {:context "doeff-cluster-test" :role "entry"}}
  "宣言の道具の順(declare.apply-declaration)で、詰めた Program を版 versions で置き、Service svc を宣言(無ければ作り、在れば読んだ
   版で書き直す)した後の状態。"
  (val blob (encode-program (based-add 3)))
  (<- placed tuple (call state "PUT" (+ "/programs/" sha) {"blob" blob "versions" versions} now))
  (assert (= (get placed 1) 200) placed)
  (val meta (.get (. (get placed 0) meta) "Service/svc"))
  (<- written tuple (if (is meta None)
                        (call (get placed 0) "POST" "/resources/Service" {"name" "svc" "spec" (! (service-row sha versions "r1"))} now)
                        (call (get placed 0) "PUT" "/resources/Service/svc"
                              {"spec" (! (service-row sha versions "r1")) "resourceVersion" meta.resource-version} now)))
  (assert (in (get written 1) #(200 201)) written)
  (get written 0))


(defk beat-specs [state mine]
  {:pre [(: state ClusterState) (: mine dict)] :post [(: % tuple)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "worker w(版 mine)の heartbeat 1 回 → #(次の状態 worker が読んだ job の列)(本番の worker の拍と同じ読み — declared-job-specs)。"
  (<- answer tuple (call state "POST" "/heartbeat"
                         {"name" "w" "provides" ["net"] "capacity" 10 "taskReserve" 0 "versions" mine "boot" "b1" "statuses" []} 5))
  (assert (= (get answer 1) 200) answer)
  (<- specs tuple (declared-job-specs (! (declared-reply-of-json (get answer 2)))))
  #((get answer 0) specs))


(deftest test-a-service-redeclared-with-the-same-program-from-another-version-is-fetched-again [tmp-path]
  ;; 版 OLD の送り手が Program を置いて svc を宣言 → worker が取って cache に置く(子の入口はこの process の版と比べて断る)→ 同じ
  ;; Program(同じ sha)をこの process の版の送り手が宣言し直す → worker は cache を手で消さずに新しい版の Program を子へ渡す。
  (val mine (! (process-versions os.environ)))
  (val sha (program-sha (encode-program (based-add 3))))
  (val worker (! (beat-specs (ClusterState) mine)))
  (val cell [(! (declared (get worker 0) sha OLD 1))])
  (val link (LinkRig "http://coord" "w" #("net") 10 0 60000 :task-dir (str (/ tmp-path "state" "tasks"))
                     :transport (! (coordinator-programs cell))))
  (val first (! (beat-specs (get cell 0) mine)))
  (.accept-programs link (get first 1))
  (val old-file (! (spec-program-file (.program-dir link) (get (get first 1) 0))))
  (assert (isinstance (get (read-program (str old-file) "") 1) VersionMismatch) "前の版の宣言はこの process の版と食い違うはず")
  ;; 宣言し直し(同じ sha・版だけが替わる)。
  (setv (get cell 0) (! (declared (get first 0) sha mine 10)))
  (val again (! (beat-specs (get cell 0) mine)))
  (.accept-programs link (get again 1))
  (val new-file (! (spec-program-file (.program-dir link) (get (get again 1) 0))))
  (assert (= (get (json.loads (.read-text new-file :encoding "utf-8")) "versions") mine)
          (.format "宣言し直した版の Program を取り直していない: {} の版 {}" new-file
                   (get (json.loads (.read-text new-file :encoding "utf-8")) "versions")))
  (val decoded (read-program (str new-file) ""))
  (assert (is (get decoded 1) None) (str (get decoded 1))))
