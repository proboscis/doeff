;;; effect の記録の置き場の file の I/O(record_store.hy の effect の handler)。
;;; 置き方: <root>/<service>/<run>/<区切り 6 桁>.jsonl(書いている区切り)・.jsonl.gz(書き終わって圧縮した区切り)。
;;; 追記は 1 要求ごとに fsync してから返す(返事を済ませた行は Pod が落ちても残る)。圧縮した後に遅れて届いた行は .jsonl に
;;; 追記され、読みは .jsonl.gz → .jsonl の順につなぐ(同じ区切りの中の順は行の番号 e が持つ — 読む側が並べ直す)。
(require doeff-hy.macros [defhandler])
(import gzip)
(import json)
(import os)
(import shutil)
(import pathlib [Path])
(import doeff_cluster.record_store [AppendRecordLines ListRecordRuns ReadRecordRun CompactRecords PruneRecords])


(defn #^ str chunk-stem [#^ int chunk] (.format "{:06d}" chunk))

(defn chunk-files [#^ Path run-dir]
  "区切りの番号 → その区切りの file の list(.jsonl.gz を先に)。"
  (setv out {})
  (for [f (sorted (.iterdir run-dir))]
    (setv name f.name)
    (cond
      (.endswith name ".jsonl.gz") (.insert (.setdefault out (int (cut name 0 6)) []) 0 f)
      (.endswith name ".jsonl") (.append (.setdefault out (int (cut name 0 6)) []) f)))
  out)

(defn #^ str read-file [#^ Path f]
  (if (.endswith f.name ".gz")
      (with [h (gzip.open f "rt" :encoding "utf-8")] (.read h))
      (.read-text f :encoding "utf-8")))

(defn #^ dict run-view [#^ Path run-dir]
  (setv chunks (chunk-files run-dir) files (lfor fs (.values chunks) f fs f))
  (setv header None)
  (when chunks
    (setv first (get (get chunks (min chunks)) 0))
    ;; 先頭の 1 行だけ読む(区切りは数百 MB になりうる — 丸ごと読まない)。
    (try
      (with [h (if (.endswith first.name ".gz") (gzip.open first "rt" :encoding "utf-8") (open first "r" :encoding "utf-8"))]
        (setv header (json.loads (.readline h))))
      (except [Exception] None)))
  {"service" run-dir.parent.name "run" run-dir.name "chunks" (len chunks) "lastChunk" (if chunks (max chunks) None)
   "bytes" (sum (gfor f files (. (.stat f) st-size)))
   "compressedChunks" (len (lfor fs (.values chunks) :if (.endswith (. (get fs 0) name) ".gz") fs))
   "lastWriteMs" (if files (int (* 1000 (max (gfor f files (. (.stat f) st-mtime))))) None)
   "startedMs" (if (isinstance header dict) (.get header "startedMs") None)
   "header" (if (and (isinstance header dict) (= (.get header "k") "run")) header None)})


(defhandler record-files [#^ str root]
  (AppendRecordLines [service run chunk lines]
    (setv d (/ (Path root) service run))
    (.mkdir d :parents True :exist-ok True)
    (setv path (/ d (+ (chunk-stem chunk) ".jsonl")))
    (with [h (open path "a" :encoding "utf-8")]
      (for [line lines] (.write h line) (.write h "\n"))
      (.flush h)
      (os.fsync (.fileno h)))
    (resume (len lines)))

  (ListRecordRuns [service]
    (setv base (Path root) out [])
    (when (.exists base)
      (for [sd (sorted (.iterdir base))]
        (when (and (.is-dir sd) (or (is service None) (= sd.name service)))
          (for [rd (sorted (.iterdir sd))]
            (when (.is-dir rd) (.append out (run-view rd)))))))
    (resume out))

  (ReadRecordRun [service run from-chunk to-chunk]
    (setv d (/ (Path root) service run))
    (if (not (.is-dir d))
        (resume None)
        (do (setv parts [])
            (for [#(n files) (sorted (.items (chunk-files d)))]
              (when (and (or (is from-chunk None) (>= n from-chunk)) (or (is to-chunk None) (<= n to-chunk)))
                (for [f files]
                  (setv text (read-file f))
                  (when (and text (not (.endswith text "\n"))) (+= text "\n"))
                  (.append parts text))))
            (resume (.join "" parts)))))

  (CompactRecords [now-ms idle-ms]
    (setv n 0 base (Path root))
    (when (.exists base)
      (for [f (.glob base "*/*/*.jsonl")]
        (when (< (* 1000 (. (.stat f) st-mtime)) (- now-ms idle-ms))
          ;; 既に .gz が在れば gzip の member を足す(gzip は複数の member をつないで 1 つとして読める)。
          (setv gz (.with-name f (+ f.name ".gz")))
          (with [src (open f "rb") dst (gzip.open gz "ab")]
            (shutil.copyfileobj src dst))
          (with [h (open gz "rb")] (os.fsync (.fileno h)))
          (.unlink f)
          (+= n 1))))
    (resume n))

  (PruneRecords [now-ms retention-ms]
    (setv removed [] base (Path root))
    (when (.exists base)
      (for [sd (sorted (.iterdir base))]
        (when (.is-dir sd)
          (for [rd (sorted (.iterdir sd))]
            (when (.is-dir rd)
              (setv files (list (.iterdir rd)))
              (setv last (if files (max (gfor f files (* 1000 (. (.stat f) st-mtime)))) 0))
              (when (< last (- now-ms retention-ms))
                (shutil.rmtree rd)
                (.append removed [sd.name rd.name])))))))
    (resume removed)))


;; --- HTTP の受付(本文の大きさに上限) -------------------------------------------------------------

(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import threading)
(import urllib.parse [urlsplit parse-qsl])
(import doeff_cluster.cluster_model [Request PlainText])
(import doeff_cluster.coordinator [RequestInbox ReplySlot])

;; 1 要求の本文の上限。記録係は 1 回の送りを 4 MB で区切る(HttpSink の max-post-bytes)ので、これを超えるのは 1 行が巨大な時だけ。
;; 上限が無い最初の版は、古い記録係(1 回 500 行)が起点の一覧を貯めて一度に送った数百 MB の本文を JSON で読み、memory が 1.9 GB に
;; 跳ねて落ちた(2026-09-25 00:19 JST・上限 2Gi の Pod)。
(setv MAX-BODY-BYTES 64000000)


(defclass RecordInbox [RequestInbox]
  "coordinator の RequestInbox と同じ箱。違いは本文の上限(超えたら読まずに 413)だけ。"
  (defn start [self]
    (setv inbox self)
    (defclass Handler [BaseHTTPRequestHandler]
      (setv protocol-version "HTTP/1.1" timeout 120)
      (defn log-message [self #* args] None)
      (defn _handle [self method]
        (setv split (urlsplit self.path)
              length (int (or (.get self.headers "Content-Length") 0))
              slot (ReplySlot))
        (when (> length MAX-BODY-BYTES)
          (setv self.close-connection True)
          (return (.send self 413 {"error" (.format "本文が大きすぎる: {} byte(上限 {})" length MAX-BODY-BYTES)})))
        (setv raw (if (> length 0) (.read self.rfile length) b""))
        (try
          (setv body (if raw (json.loads raw) None))
          (except [error ValueError]
            (return (.send self 400 {"error" (.format "JSON を読めない: {}" error)}))))
        (setv raw None)
        (.put inbox.queue (Request method split.path (dict (parse-qsl split.query)) body slot
                                   :actor (.get self.headers "X-Actor") :peer (str (get self.client-address 0))))
        (if (.wait slot.done 60.0)
            (.send self slot.status slot.body)
            (.send self 503 {"error" "置き場の Program が返事をしない"})))
      (defn send [self status body]
        (setv #(data content-type)
              (if (isinstance body PlainText)
                  #((.encode body.text "utf-8") body.content-type)
                  #((.encode (json.dumps body :ensure-ascii False) "utf-8") "application/json; charset=utf-8")))
        (.send-response self status)
        (.send-header self "Content-Type" content-type)
        (.send-header self "Content-Length" (str (len data)))
        (.end-headers self)
        (.write self.wfile data))
      (defn do-GET [self] (._handle self "GET"))
      (defn do-POST [self] (._handle self "POST")))
    (setv self.server (ThreadingHTTPServer #("0.0.0.0" self.port) Handler))
    (setv self.server.daemon-threads True)
    (.start (threading.Thread :target self.server.serve-forever :daemon True))))
