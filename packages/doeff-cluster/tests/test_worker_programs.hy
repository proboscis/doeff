;; worker が詰めた Program を受け取って子へ渡す所(ADR-DOE-CLUSTER-001 改訂 1 の F・G・H — 2026-09-27)。
;;
;; - CoordinatorLink.accept-programs: 宣言の job の置き場のキー(spec.program)の Program を coordinator の GET /programs/<sha> から取り、
;;   state dir の programs/<sha>.json に書く。中身の sha256 がキーと合わない物・取れない物は書かない。在る物は取り直さない。
;; - ProcessHost.launch: 子の引数に `--program <その file>`、子の環境に HOST-CONTRACT の program-env と宣言の environ を足す。
;;   CoordinatorLink と ProcessHost は main の置き方(state dir の logs・tasks)で同じ programs の dir を指す。
(require doeff-hy.macros [deftest defk deff <- val])
(import hashlib)
(import json)
(import pathlib [Path])
(import httpx)
(import doeff_cluster.handlers [CoordinatorLink ProcessHost program-file])
(import doeff_cluster.host_contract [HOST-CONTRACT])
(import doeff_cluster.worker_model [JobSpec])
(import doeff_cluster.cluster_policy [JOB-ENTRY])

(val BLOB "cHJvZ3JhbQ==")
(val SHA (.hexdigest (hashlib.sha256 (.encode BLOB "ascii"))))
(val FORGED (* "b" 64))            ; 取った中身の sha256 がこのキーと合わない
(val ABSENT (* "c" 64))            ; coordinator に置かれていない
(val VERSIONS {"doeff" "0.4.1"})


(defk served-programs [seen]
  {:pre [(: seen list)] :post [(: % httpx.MockTransport)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "coordinator の /programs/<sha> の偽の口(取った path を seen に積む)。"
  (deff answer [request]  ; defk にできない: httpx の MockTransport が呼ぶ素の callback
    {:pre [(: request httpx.Request)] :post [(: % httpx.Response)] :tags {:context "doeff-cluster-test" :role "entry"}}
    (.append seen request.url.path)
    (cond
      (.endswith request.url.path SHA) (httpx.Response 200 :json {"blob" BLOB "versions" VERSIONS})
      (.endswith request.url.path FORGED) (httpx.Response 200 :json {"blob" BLOB "versions" VERSIONS})
      True (httpx.Response 404 :json {"error" "置かれていない"})))
  (httpx.MockTransport answer))


(defk service-spec [name program]
  {:pre [(: name str) (: program (| str None))] :post [(: % JobSpec)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "Program の job の service の spec(置き場のキー program・子の環境変数 environ)。"
  (JobSpec name JOB-ENTRY #("service" "--identity" (* "0" 16)) "rev1" :program program :environ #(#("POLL" "5.0"))))


(deftest test-the-link-fetches-only-programs-whose-content-matches-the-key [tmp-path]
  (val seen [])
  (<- transport (served-programs seen))
  (val link (CoordinatorLink "http://coord" "zeus" #("net") 1 60000 :task-dir (str (/ tmp-path "tasks")) :transport transport))
  (<- good (service-spec "good" SHA))
  (<- forged (service-spec "forged" FORGED))
  (<- absent (service-spec "absent" ABSENT))
  (<- plain (service-spec "plain" None))
  (.accept-programs link #(good forged absent plain))
  (val dir (/ tmp-path "programs"))
  (assert (= (json.loads (.read-text (program-file dir SHA) :encoding "utf-8")) {"blob" BLOB "versions" VERSIONS}))
  ;; 中身の合わない物・置かれていない物は書かない(子は file が無いので起動の時に理由つきで落ちる)。置き場のキーの無い job は取らない。
  (assert (not (.exists (program-file dir FORGED))))
  (assert (not (.exists (program-file dir ABSENT))))
  (assert (= (sorted seen) (sorted (lfor s [SHA FORGED ABSENT] (+ "/programs/" s)))) seen)
  ;; 在る物は取り直さない(取れなかった物は次の拍で取り直す)。
  (.clear seen)
  (.accept-programs link #(good absent))
  (assert (= seen [(+ "/programs/" ABSENT)]) seen))


(deftest test-the-host-hands-the-program-file-and-the-environ-to-the-child [tmp-path]
  (val state-dir (/ tmp-path "state"))
  ;; main と同じ置き方(state dir の logs・tasks)で、取る側と渡す側が同じ programs の dir を指す。
  (val host (ProcessHost (str (/ state-dir "logs")) "hy"))
  (val link (CoordinatorLink "http://coord" "zeus" #("net") 1 60000 :task-dir (str (/ state-dir "tasks"))))
  (assert (= host.program-dir link.program-dir (/ state-dir "programs")))
  (<- spec (service-spec "svc" SHA))
  (val launched (.launch host spec (str tmp-path) "1-1" 1))
  (val argv (get launched 0))
  (val cwd (get launched 1))
  (val env (get launched 2))
  (val file (str (program-file host.program-dir SHA)))
  (assert (= (list (cut argv -5 None)) ["service" "--identity" (* "0" 16) "--program" file]) argv)
  (assert (= (get env HOST-CONTRACT.program-env) file) env)
  (assert (= (get env "POLL") "5.0") env)
  (assert (= cwd (str tmp-path)))
  ;; 置き場のキーを持たない worker の内部の JobSpec には足さない(宣言の job も task も置き場のキーを持つ — task は test_task_programs.hy)。
  (<- bare (service-spec "bare" None))
  (val bare-launched (.launch host bare (str tmp-path) "1-1" 1))
  (val bare-argv (get bare-launched 0))
  (val bare-env (get bare-launched 2))
  (assert (not-in "--program" bare-argv) bare-argv)
  (assert (not-in HOST-CONTRACT.program-env bare-env)))
