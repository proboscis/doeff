;; 実 I/O の handler の失敗経路。失敗の報告を組み立てる所で worker ごと落ちないこと。
(require doeff-hy.macros [deftest])
(import time)
(import doeff_cluster.worker_model [DesiredJobs DesiredUnreadable])
(import doeff_cluster.handlers [parse-desired CoordinatorLink])

(deftest test-broken-declaration-is-reported-not-raised
  (setv result (parse-desired "{"))
  (assert (isinstance result DesiredUnreadable))
  (assert (in "JSONDecodeError" result.reason)))

(deftest test-unreachable-coordinator-is-unreadable-then-fences
  ;; 閉じた port へ向ける。fence 前は「読めない」(直前の宣言を続ける)、fence を超えたら空(全部止める)。
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" {} 1 60000))
  (setv first (.poll link))
  (assert (isinstance first DesiredUnreadable))
  (assert (in "coordinator に届かない" first.reason))
  (setv link.fence-ms 0 link.last-ok (- (time.monotonic) 1))
  (assert (= (.poll link) (DesiredJobs #()))))

(import pathlib [Path])
(import doeff_cluster.worker_model [JobStatus JobPhase])
(import doeff_cluster.handlers [task-spec])
(import doeff_cluster.code_prepare [module-name carry-pairs compile-plan cache-rel])

(deftest test-task-files-are-written-reported-and-cleaned [tmp-path]
  (setv link (CoordinatorLink "http://127.0.0.1:9" "w" {} 1 60000 :task-dir (str tmp-path)))
  (setv task {"id" "t7" "env" "m:e" "revision" "r" "versions" {"b" "2" "a" "1"} "blob" "QkxPQg=="})
  (setv #(spec) (.accept-tasks link [task]))
  ;; 名前と引数は task の id と版で決まる(毎拍同じ形 = 起動し直さない)
  (assert (= spec (task-spec task tmp-path)))
  (assert spec.once)
  (assert (= (.read-text (/ tmp-path "t7.blob")) "QkxPQg=="))
  (.write-text (/ tmp-path "t7.result") "RESULT")
  (setv #(row) (.report link #((JobStatus "task/t7" JobPhase.FINISHED "r" None None 1))))
  (assert (= (get row "result") "RESULT"))
  ;; 宣言から外れた task の file は消す
  (.accept-tasks link [])
  (assert (= (list (.iterdir tmp-path)) [])))

(deftest test-code-prepare-plans-are-pure
  (assert (= (module-name "app/wrap/__init__.py") "app.wrap"))
  ;; 根を足すと、その下はその根からの名で読む。根 `.` はほかの根の先頭の dir の下を数えない。
  (assert (= (module-name "vendor/hy/lib/core.hy" #("." "vendor/hy")) "lib.core"))
  (assert (= (module-name "vendor/hy/lib/core.hy") "vendor.hy.lib.core"))
  (assert (is (module-name "vendor/x.py" #("." "vendor/hy")) None))
  (setv tag (cut (cache-rel "a/m.py") (len "a/__pycache__/m") None))
  (assert (= (carry-pairs [(+ "a/__pycache__/m" tag) (+ "a/__pycache__/n" tag)]
                          (frozenset ["a/m.py" "a/n.hy"]) (frozenset ["a/m.py" "a/n.hy"]) (frozenset [])
                          (frozenset ["a/n.hy"]))
             [(+ "a/__pycache__/m" tag)]))
  (assert (= (compile-plan ["a/m.py" "vendor/x.py"] (frozenset [(cache-rel "a/m.py")]) #("." "vendor/hy")) [])))

(import json)
(import threading)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import doeff_cluster.cluster_model [ClusterTiming])

(deftest test-default-timing-outlasts-the-measured-tailnet-outage
  ;; 2026-09-23 の newmac の tailnet の途絶は最長 約 13 秒。自己停止はそれより長く、移し替えは自己停止より十分長い。
  (setv t (ClusterTiming))
  (assert (= #(t.fence-ms t.reassign-after-ms) #(20000 45000))))

(deftest test-worker-adopts-the-fence-announced-by-the-coordinator
  ;; 自己停止の時間の定義点は coordinator の ClusterTiming。worker は heartbeat の返事の timing に合わせる。
  (defclass Reply [BaseHTTPRequestHandler]
    (defn log-message [self #* args] None)
    (defn do-POST [self]
      (.read self.rfile (int (get self.headers "Content-Length")))
      (setv data (.encode (json.dumps {"jobs" [] "tasks" [] "timing" {"fence_ms" 20000 "reassign_after_ms" 45000}})))
      (.send-response self 200)
      (.send-header self "Content-Length" (str (len data)))
      (.end-headers self)
      (.write self.wfile data)))
  (setv server (ThreadingHTTPServer #("127.0.0.1" 0) Reply))
  (.start (threading.Thread :target server.serve-forever :daemon True))
  (try
    (setv link (CoordinatorLink f"http://127.0.0.1:{(get server.server-address 1)}" "w" {} 1 10000))
    (assert (= (.poll link) (DesiredJobs #())))
    (assert (= link.fence-ms 20000))
    (finally (.shutdown server))))


;; --- 終わった process の lease を返す(2026-09-24)・image の LABEL を読む -----------------------------------------

(import json)
(import httpx)
(import doeff_cluster.handlers [release-leases])
(import doeff_cluster.semaphore_model [drop-holders])
(import doeff_cluster.image_handlers [RegistryClient])

(deftest test-release-leases-drops-only-the-finished-process-holders-on-an-old-coordinator
  ;; 盤の semaphore の行から、token が「<worker>/<世代の名>/」で始まる担い手だけを外す(他の process の lease は残す)。
  (setv board {"semaphore/app-writer" {"permits" 1 "holders" {"zeus/1-old/ab12/1" 99 "zeus/2-new/cd34/1" 88}}
               "semaphore/other" {"permits" 1 "holders" {"atlas/1-old/ee/1" 77}}}
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
  (setv link (CoordinatorLink "http://coord" "zeus" {} 1 60000 :transport (httpx.MockTransport handle)))
  (release-leases link "1-old")
  (assert (= (get board "semaphore/app-writer" "holders") {"zeus/2-new/cd34/1" 88}))
  (assert (= (get board "semaphore/other" "holders") {"atlas/1-old/ee/1" 77}) "別の worker の同じ名の世代は触らない")
  (assert (= puts ["semaphore/app-writer"]))
  (assert (is (drop-holders {"permits" 1 "holders" {"x/1/a" 1}} "y/") None)))

(deftest test-registry-client-reads-labels-through-an-index
  (setv seen [])
  (defn handle [request]
    (.append seen request.url.path)
    (cond
      (.endswith request.url.path "/manifests/20260924-305aac4")
        (httpx.Response 200 :json {"mediaType" "application/vnd.oci.image.index.v1+json"
                                   "manifests" [{"digest" "sha256:arm" "platform" {"os" "linux" "architecture" "arm64"}}
                                                {"digest" "sha256:amd" "platform" {"os" "linux" "architecture" "amd64"}}]})
      (.endswith request.url.path "/manifests/sha256:amd")
        (httpx.Response 200 :json {"mediaType" "application/vnd.oci.image.manifest.v1+json" "config" {"digest" "sha256:cfg"}})
      (.endswith request.url.path "/blobs/sha256:cfg")
        (httpx.Response 200 :json {"config" {"Labels" {"org.opencontainers.image.revision" "abc"}}})
      True (httpx.Response 404)))
  (setv client (RegistryClient :transport (httpx.MockTransport handle)))
  (assert (= (.labels client "zeus:5000/app:20260924-305aac4") {"org.opencontainers.image.revision" "abc"}))
  (assert (= (get seen 0) "/v2/app/manifests/20260924-305aac4")))
