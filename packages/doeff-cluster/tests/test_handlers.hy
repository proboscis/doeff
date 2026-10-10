;; 実 I/O の handler の失敗経路。失敗の報告を組み立てる所で worker ごと落ちないこと。
(require doeff-hy.macros [deftest val])
(import time)
(import httpx)
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable CutOff])
(import tests.link_rig [LinkRig] doeff_cluster.worker.core.launch [spec-program-name])

(deftest test-broken-declaration-is-reported-not-raised
  ;; worker が job を受けるのは coordinator の返事からだけ(宣言の file を直に読む口は無い)。読めない返事は例外を上げず「読めない」に
  ;; なる(fence の前は直前の宣言を続ける)。
  (val link (LinkRig "http://coord" "w" #() 1 0 60000
                             :transport (httpx.MockTransport (fn [request] (httpx.Response 200 :text "{")))))
  (val result (.poll link))
  (assert (isinstance result DesiredUnreadable) result)
  (assert (in "JSONDecodeError" result.reason) result.reason))

(deftest test-unreachable-coordinator-is-unreadable-then-fences
  ;; 閉じた port へ向ける。fence 前は「読めない」(直前の宣言を続ける)、fence を超えたら途絶でも残す job(入れ替えの書き手・切り離した
  ;; task・途絶しても動かし続けてよい印の在る job — #2804)だけの宣言になる。ここは宣言を受け取っていないので空(全部止める)。
  (setv link (LinkRig "http://127.0.0.1:9" "w" #() 1 0 60000))
  (setv first (.poll link))
  (assert (isinstance first DesiredUnreadable))
  (assert (in "coordinator に届かない" first.reason))
  (setv link.state.fence-ms 0 link.state.last-ok-ms (- (int (* 1000 (time.time))) (int (* 1000 1))))
  (val cut (.poll link))
  ;; 途絶で絞った宣言は、止めの訳 CutOff を運ぶ(#3713 — worker が止めた job の行に cut-off と黙った秒を出す)
  (assert (= cut.jobs #()))
  (assert (isinstance cut.cut-off CutOff)))

(import pathlib [Path])
(import doeff_cluster.worker.intent.worker_model [JobStatus] doeff_cluster.shared.intent.job_model [JobPhase])
(import doeff_cluster.worker.protocol.declared [task-spec])
(import doeff_cluster.worker.core.code_plan [module-name carry-pairs compile-plan cache-rel])

(deftest test-task-files-are-written-reported-and-cleaned [tmp-path]
  (val tasks (/ tmp-path "tasks"))
  (val link (LinkRig "http://127.0.0.1:9" "w" #() 1 0 60000 :task-dir (str tasks)))
  (val sha (* "d" 64))
  (val task {"id" "t7" "revision" "r" "versions" {"b" "2" "a" "1"} "program" sha})
  (val specs (.accept-tasks link [task]))
  (val spec (get specs 0))
  ;; 名前と引数は task の id で決まる(毎拍同じ形 = 起動し直さない)。詰めた Program は置き場のキーで持つ(引数に載せない)。
  (assert (= spec (! (task-spec task tasks))))
  (assert spec.once)
  (assert (= spec.program sha) spec)
  (assert (= spec.args #("task" "--result" (str (/ tasks "t7.result")))) spec.args)
  ;; 本文に Program は無い — その task の Program の cache の file(Program の cache の dir からの相対 path <版の指紋>/<sha>.json —
  ;; Program のキーと task の行の版で決まる・#3762)の印だけを残す(返事から外れた task の cache を消すため)。
  (assert (= (.read-text (/ tasks "t7.program")) (! (spec-program-name spec))))
  (assert (.endswith (.read-text (/ tasks "t7.program")) (+ "/" sha ".json")))
  (.write-text (/ tasks "t7.result") "RESULT")
  (val rows (.report link #((JobStatus "task/t7" JobPhase.FINISHED "r" None None 1))))
  (assert (= (get rows 0 "result") "RESULT"))
  ;; 宣言から外れた task の file は消す(結果は accept-tasks・印は accept-programs)
  (.accept-tasks link [])
  (.accept-programs link #())
  (assert (= (list (.iterdir tasks)) [])))

(deftest test-code-prepare-plans-are-pure
  (assert (= (module-name "app/wrap/__init__.py") "app.wrap"))
  ;; 根を足すと、その下はその根からの名で読む。根 `.` はほかの根の先頭の dir の下を数えない。
  (assert (= (module-name "vendor/hy/lib/core.hy" #("." "vendor/hy")) "lib.core"))
  (assert (= (module-name "vendor/hy/lib/core.hy") "vendor.hy.lib.core"))
  (assert (is (module-name "vendor/x.py" #("." "vendor/hy")) None))
  (setv tag (cut (cache-rel "a/m.py") (len "a/__pycache__/m") None))
  (assert (= (! (carry-pairs [(+ "a/__pycache__/m" tag) (+ "a/__pycache__/n" tag)]
                          (frozenset ["a/m.py" "a/n.hy"]) (frozenset ["a/m.py" "a/n.hy"]) (frozenset [])
                          (frozenset ["a/n.hy"])))
             [(+ "a/__pycache__/m" tag)]))
  (assert (= (! (compile-plan ["a/m.py" "vendor/x.py"] (frozenset [(cache-rel "a/m.py")]) #("." "vendor/hy"))) [])))

(import json)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import doeff_cluster.shared.intent.protocol [ClusterTiming])

(deftest test-default-timing-outlasts-the-measured-tailnet-outage
  ;; 2026-09-23 の newmac の tailnet の途絶は最長 約 13 秒。自己停止はそれより長く、移し替えは自己停止より十分長い(worker の止め切りより
  ;; 後かは条 C4 の検 tests/test_cluster_timing.hy が本番の定数で判じる — 60 秒は #2806)。
  (setv t (ClusterTiming))
  (assert (= #(t.fence-ms t.reassign-after-ms) #(20000 60000))))

(deftest test-worker-adopts-the-fence-announced-by-the-coordinator
  ;; 自己停止の時間の定義点は coordinator の ClusterTiming。worker は heartbeat の返事の timing に合わせる。
  (defclass Reply [BaseHTTPRequestHandler]
    (defn #^ None log-message [self #^ str format #^ object #* args] None)
    (defn #^ None do-POST [self]
      (.read self.rfile (int (get self.headers "Content-Length")))
      (setv data (.encode (json.dumps {"jobs" [] "tasks" [] "timing" {"fence_ms" 20000 "reassign_after_ms" 45000} "draining" False})))
      (.send-response self 200)
      (.send-header self "Content-Length" (str (len data)))
      (.end-headers self)
      (.write self.wfile data)
      None))
  (setv server (ThreadingHTTPServer #("127.0.0.1" 0) Reply))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (try
    (setv link (LinkRig f"http://127.0.0.1:{(get server.server-address 1)}" "w" #() 1 0 10000))
    (assert (= (.poll link) (DesiredJobs #())))
    (assert (= link.state.fence-ms 20000))
    (finally (.shutdown server))))


;; --- 終わった process の lease を返す(2026-09-24) -----------------------------------------

(import json)
(import httpx)
(import tests.transport_http [released-through])
(import doeff_cluster.shared.core.lease_rules [drop-holders])

(deftest test-release-leases-drops-only-the-finished-process-holders-on-an-old-coordinator
  ;; 盤の semaphore の行から、token が子の名乗った担い手の頭「<job>/<世代の名>/」(lease_rules.lease-holder)で始まる担い手だけを
  ;; 外す(他の process の lease は残す)。
  (setv board {"semaphore/app-writer" {"permits" 1 "holders" {"app-writer/1-old/1" 99 "app-writer/2-new/1" 88}}
               "semaphore/other" {"permits" 1 "holders" {"other-job/1-old/1" 77}}}
        puts [])
  (defn #^ httpx.Response handle [#^ httpx.Request request]
    (cond
      (= request.method "GET")
        (httpx.Response 200 :json (dfor #(k v) (.items board) :if (.startswith k (get request.url.params "prefix")) k v))
      ;; 旧い coordinator(2026-09-25 より前)は /leases の口を持たない → 盤の compare-and-set で外す
      (.startswith request.url.path "/leases/") (httpx.Response 404 :json {})
      True (do (setv body (json.loads request.content) key (cut request.url.path (len "/board/") None))
               (.append puts key)
               (if (= (get board key) (get body "expect"))
                   (do (setv (get board key) (get body "value")) (httpx.Response 200 :json {}))
                   (httpx.Response 409 :json {})))))
  (<- (released-through (httpx.MockTransport handle) "app-writer" "1-old"))
  (assert (= (get board "semaphore/app-writer" "holders") {"app-writer/2-new/1" 88}))
  (assert (= (get board "semaphore/other" "holders") {"other-job/1-old/1" 77}) "別の job の同じ名の世代は触らない")
  (assert (= puts ["semaphore/app-writer"]))
  (assert (is (drop-holders {"permits" 1 "holders" {"x/1/a" 1}} "y/") None)))
