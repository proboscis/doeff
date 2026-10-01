;;; effect の記録の置き場の HTTP の受付(本文の大きさに上限)— coordinator の RequestInbox と同じ箱で、要求を並べて置き場の Program
;;; (record_store.core.program の store-loop)の NextRequests へ渡す(record_store_handlers.hy から分けた・agora-redesign #2030)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import json)

(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import threading)
(import urllib.parse [urlsplit parse-qsl])
;; 返事の本文の形の判断と書き出しは coordinator の受付と同じ関数(text-body?・encoded-reply)— この module は intent の型を読まない。
(import doeff_cluster.foundation.coordinator_inbox [RequestInbox ReplySlot http-request text-body? encoded-reply])

;; 1 要求の本文の上限。記録係は 1 回の送りを 4 MB で区切る(HttpSink の max-post-bytes)ので、これを超えるのは 1 行が巨大な時だけ。
;; 上限が無い最初の版は、古い記録係(1 回 500 行)が起点の一覧を貯めて一度に送った数百 MB の本文を JSON で読み、memory が 1.9 GB に
;; 跳ねて落ちた(2026-09-25 00:19 JST・上限 2Gi の Pod)。
(setv MAX-BODY-BYTES 64000000)


(defclass RecordInbox [RequestInbox]
  "coordinator の RequestInbox と同じ箱。違いは本文の上限(超えたら読まずに 413)だけ。"
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
          (return (.send self 413 {"error" (.format "本文が大きすぎる: {} byte(上限 {})" length MAX-BODY-BYTES)})))
        (setv raw (if (> length 0) (.read self.rfile length) b""))
        (try
          (setv body (if raw (json.loads raw) None))
          (except [error ValueError]
            (return (.send self 400 {"error" (.format "JSON を読めない: {}" error)}))))
        (setv raw None)
        (.put inbox.queue (http-request method split.path (dict (parse-qsl split.query)) body :slot slot
                                   :actor (.get self.headers "X-Actor") :peer (str (get self.client-address 0))))
        (if (.wait slot.done 60.0)
            (do (setv reply slot.body)
                ;; 置き場の答え(record_store.answer-request)の本文は表か PlainText だけ — 別の形なら送る前に名指して落ちる。
                (assert (or (isinstance reply dict) (text-body? reply)) (.format "記録の置き場の返事の本文の形が違う: {}" (type reply)))
                (.send self slot.status reply))
            (.send self 503 {"error" "置き場の Program が返事をしない"})))
      (defn #^ None send [self #^ int status #^ object body]
        (setv #(data content-type) (encoded-reply body))
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
