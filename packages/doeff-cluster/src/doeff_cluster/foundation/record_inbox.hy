;;; effect の記録の置き場の HTTP の受付(本文の大きさに上限)— coordinator の RequestInbox と同じ箱で、要求を並べて置き場の Program
;;; (record_store.core.program の store-loop)の NextRequests へ渡す(record_store_handlers.hy から分けた・#2030)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import json)

(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import threading)
(import urllib.parse [urlsplit parse-qsl])
;; 生の要求の並べ方と返事の byte の書き方は coordinator の受付と同じ(RawRequest・json-reply)— 要求を解く・返事の本文を byte にする
;; のは shared/protocol/inbox.hy の http-requests(この module は intent の型を読まない・#2563)。
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox ReplySlot RawRequest json-reply])

;; 1 要求の本文の上限。記録係は 1 回の送りを 4 MB で区切る(HttpSink の max-post-bytes)ので、これを超えるのは 1 行が巨大な時だけ。
;; 上限が無い最初の版は、古い記録係(1 回 500 行)が起点の一覧を貯めて一度に送った数百 MB の本文を JSON で読み、memory が 1.9 GB に
;; 跳ねて落ちた(2026-09-25 00:19 JST・上限 2Gi の Pod)。
(setv MAX-BODY-BYTES 64000000)
;; 置き場の Program の返事を待つ打ち切り(秒)— 書きの fsync と詰めの間も待つ。coordinator の ClusterTiming の外の値(#3865 で名を付けた)。
(setv RECORD-REPLY-SECONDS 60.0)


(defclass RecordInbox [RequestInbox]
  "coordinator の RequestInbox と同じ箱。違いは本文の上限(超えたら読まずに 413)と、置き場の Program の返事を待つ打ち切り
   (RECORD-REPLY-SECONDS — coordinator の ClusterTiming の外)。"
  (defn #^ None __init__ [self #^ int port]
    (.__init__ (super) port RECORD-REPLY-SECONDS)
    None)

  (defn #^ None start [self]
    (setv inbox self)
    (defclass Handler [BaseHTTPRequestHandler]
      (setv protocol-version "HTTP/1.1" timeout 120)
      (defn #^ None log-message [self #^ str format #^ (| str int) #* args] None)
      (defn _handle [self method]
        (setv split (urlsplit self.path)
              length (int (or (.get self.headers "Content-Length") 0))
              slot (ReplySlot))
        (when (> length MAX-BODY-BYTES)
          (setv self.close-connection True)
          (return (.send self 413 #* (json-reply {"error" (.format "本文が大きすぎる: {} byte(上限 {})" length MAX-BODY-BYTES)}))))
        (setv raw (if (> length 0) (.read self.rfile length) b""))
        (try
          (setv body (if raw (json.loads raw) None))
          (except [error ValueError]
            (return (.send self 400 #* (json-reply {"error" (.format "JSON を読めない: {}" error)})))))
        (setv raw None)
        (.offer inbox (RawRequest method split.path (dict (parse-qsl split.query)) body slot
                                  (.get self.headers "X-Actor") (str (get self.client-address 0))))
        ;; 置き場の答えの本文の形(表か PlainText だけ)は、返事を出す record_store/core/program.hy の store-loop が検める。
        (if (.wait slot.done inbox.reply-seconds)
            (.send self slot.status slot.data slot.content-type)
            (.send self 503 #* (json-reply {"error" "置き場の Program が返事をしない"}))))
      (defn #^ None send [self #^ int status #^ bytes data #^ str content-type]
        (.send-response self status)
        (.send-header self "Content-Type" content-type)
        (.send-header self "Content-Length" (str (len data)))
        (.end-headers self)
        (.write self.wfile data)
        None)
      (defn #^ None do-GET [self] (._handle self "GET"))
      (defn #^ None do-POST [self] (._handle self "POST")))
    (setv self.server (ThreadingHTTPServer #("0.0.0.0" self.port) Handler))
    (setv self.server.daemon-threads True)
    (.start (threading.Thread :target self.server.serve-forever :daemon True))))
