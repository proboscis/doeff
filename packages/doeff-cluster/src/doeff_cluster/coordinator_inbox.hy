;;; coordinator の HTTP の受付と停止の合図(handler)— 調停ループの Program(coordinator.hy)が出す NextRequests / Reply /
;;; CoordinatorStopRequested に、本番の process で答える部品。別 thread の HTTP server が受けた要求を列に並べ、調停ループが
;;; まとめて取る。k8s の probe(/livez・/readyz)は列を通さずに受付の thread が答える。
;;; 2026-09-25 に coordinator.hy から分けた(handler の組 coordinator_handler_sets.hy がこの受付を本番の組に入れ、coordinator.hy の
;;; main がその組を選ぶ — 同じ file に置くと組の module と循環する)。coordinator.hy は以前の import の口のためにここの名を再び出す。
(require doeff-hy.macros [defhandler])
(import json)
(import queue)
(import typing [Callable])
(import sys)
(import threading)
(import time)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import urllib.parse [urlsplit parse-qsl])
(import .cluster_model [Request NextRequests Reply CoordinatorStopRequested PlainText])

;; probe の閾値(秒)。ループは要求が無くても 1 秒ごとに NextRequests を出すので、ふだんの「最後に取りに来てから」は 1 秒 + 1 まとまりの
;; 処理(fsync の実測の最大 2.9〜3.6 秒・longhorn の詰まりで最長 13 秒・k8s と registry の読みは各 3 秒で打ち切り)。
;; readiness はそれより十分長い 30 秒(worker の返事の上限 REPLY-SECONDS 15 秒の 2 倍)、liveness は「固まった」と言える 120 秒。
;; liveness が落ちると kubelet が container を作り直す(状態は耐久の置き場から読み直す)。
(setv READY-STALL-SECONDS 30.0)
(setv LIVE-STALL-SECONDS 120.0)


(defn #^ tuple probe-verdict [#^ str path #^ (| float None) stalled-seconds]
  "純粋: probe の答え #(status 本文)。stalled-seconds = ループが最後に要求を取りに来てからの秒(まだ 1 度も来ていなければ None)。
   /livez はループが LIVE-STALL-SECONDS より長く止まった時だけ 503(起動直後で 1 度も来ていない時は 200 — 起動の遅さは
   startupProbe が見る)。/readyz は 1 度も来ていない・READY-STALL-SECONDS より長く止まった時に 503。"
  (setv limit (if (= path "/livez") LIVE-STALL-SECONDS READY-STALL-SECONDS))
  (cond
    (is stalled-seconds None)
      (if (= path "/livez")
          #(200 {"ok" True "reason" "調停ループはまだ始まっていない(起動中)"})
          #(503 {"ok" False "reason" "調停ループはまだ始まっていない(起動中)"}))
    (> stalled-seconds limit)
      #(503 {"ok" False "stalledSeconds" (round stalled-seconds 1)
             "reason" (.format "調停ループが {:.1f} 秒 要求を取りに来ていない(閾値 {} 秒)" stalled-seconds limit)})
    True #(200 {"ok" True "stalledSeconds" (round stalled-seconds 1)})))


;; --- handler: HTTP の受付 ---------------------------------------------------------------

(defclass ReplySlot []
  "server の thread が返事を待つ札。"
  (defn #^ None __init__ [self]
    (setv self.done (threading.Event) self.status 500 self.created (time.monotonic))
    (setv #^ object self.body None)))


(defclass RequestInbox []
  "HTTP server(別 thread)が受けた要求を並べる箱。調停ループは 1 件ずつ取り出して返事を置く。"
  (defn #^ None __init__ [self #^ int port #^ Callable [clock time.monotonic]]
    ;; last-take = 調停ループが最後に要求を取りに来た時刻(単調時計)。probe はこれだけで答える(ループを通さない)。
    (setv self.queue (queue.Queue) self.port port self.server None self.clock clock self.last-take None))

  (defn #^ tuple probe [self #^ str path]
    "k8s の probe(/livez・/readyz)の答え。HTTP の thread が直に答える — 調停ループの遅れ(fsync・k8s の API)に巻き込まれない。"
    (probe-verdict path (if (is self.last-take None) None (- (self.clock) self.last-take))))

  (defn #^ None start [self]
    (setv inbox self)
    (defclass Handler [BaseHTTPRequestHandler]
      ;; HTTP/1.1 = 接続を使い回す。HTTP/1.0 では要求ごとに接続を閉じ、client は毎回 TCP を張り直していた
      ;; (tailnet の経路が数秒途絶えると新しい接続は必ず失敗する — coordinator_http.py の説明)。
      ;; 使われなくなった接続の thread は timeout 秒で終わる(client の側は 5 秒で手放す)。
      (setv protocol-version "HTTP/1.1" timeout 120)
      (defn #^ None log-message [self #^ str format #^ object #* args] None)
      (defn #^ None _handle [self #^ str method]
        (setv split (urlsplit self.path))
        ;; probe は並べずに答える(調停ループが fsync や k8s の読みで数秒止まっても、probe が時間切れにならない)。
        (when (and (= method "GET") (in split.path #("/livez" "/readyz")))
          (setv #(status body) (.probe inbox split.path))
          (return (.send self status body)))
        (setv length (int (or (.get self.headers "Content-Length") 0))
              raw (if (> length 0) (.read self.rfile length) b"")
              slot (ReplySlot))
        (try
          (setv body (if raw (json.loads raw) None))
          (except [error ValueError]
            (return (.send self 400 {"error" (.format "JSON を読めない: {}" error)}))))
        (.put inbox.queue (Request method split.path (dict (parse-qsl split.query)) body slot
                                   :actor (.get self.headers "X-Actor")
                                   :peer (str (get self.client-address 0))))
        (if (.wait slot.done 30.0)
            (.send self slot.status slot.body)
            (.send self 503 {"error" "調停ループが返事をしない"})))
      (defn #^ None send [self #^ int status #^ object body]
        (setv #(data content-type)
              (if (isinstance body PlainText)
                  #((.encode body.text "utf-8") body.content-type)
                  #((.encode (json.dumps body :ensure-ascii False) "utf-8") "application/json; charset=utf-8")))
        (.send-response self status)
        (.send-header self "Content-Type" content-type)
        (.send-header self "Content-Length" (str (len data)))
        (.end-headers self)
        (.write self.wfile data)
        None)
      (defn #^ None do-GET [self] (._handle self "GET"))
      (defn #^ None do-PUT [self] (._handle self "PUT"))
      (defn #^ None do-POST [self] (._handle self "POST"))
      (defn #^ None do-DELETE [self] (._handle self "DELETE")))
    (setv self.server (ThreadingHTTPServer #("0.0.0.0" self.port) Handler))
    (setv self.server.daemon-threads True)
    (.start (threading.Thread :target self.server.serve-forever :daemon True)))

  (defn #^ list take [self #^ float timeout #^ int limit]
    "最初の 1 件を timeout 秒まで待ち、その時点で並んでいる要求を limit 件まで一緒に取る。"
    (setv self.last-take (self.clock))
    (try (setv first (.get self.queue :timeout timeout))
         (except [queue.Empty] (return [])))
    (setv batch [first])
    (while (< (len batch) limit)
      (try (.append batch (.get-nowait self.queue))
           (except [queue.Empty] (break))))
    batch))


(defhandler http-requests [#^ RequestInbox inbox]
  (NextRequests [timeout-seconds limit] (resume (.take inbox timeout-seconds limit)))
  (Reply [request status body]
    (setv slot request.slot)
    (assert (isinstance slot ReplySlot) "http-requests の要求の札は ReplySlot")
    ;; 返事まで 1 秒を超えた要求を 1 行出す(調停ループが何かを待って止まった時の手がかり)。
    (setv waited (- (time.monotonic) slot.created))
    (when (> waited 1.0)
      (print (.format "coordinator: 遅い返事 {:.1f} 秒: {} {}" waited request.method request.path) :file sys.stderr :flush True))
    (setv slot.status status slot.body body)
    (.set slot.done)
    (resume None)))


;; --- 停止・読み込み --------------------------------------------------------------------------

(defclass StopState []
  (defn #^ None __init__ [self] (setv self.requested False)))


(defhandler stop-flag [#^ StopState state]
  (CoordinatorStopRequested [] (resume state.requested)))
