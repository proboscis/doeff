;;; coordinator と記録の置き場の HTTP の受付(汎用の I/O)— 別 thread の HTTP server が受けた要求を、まだ解かない生の形(RawRequest)で
;;; 列に並べ、置かれた返事の byte をそのまま書き返す。k8s の probe(/livez・/readyz)は列を通さずに受付の thread が答える。
;;; 要求を Request に解く・返事の本文を byte にする・NextRequests / Reply / CoordinatorStopRequested に答える handler は
;;; shared/protocol/inbox.hy(層 foundation は intent の型を読まない — #2563・#2445 の「protocol の 1 点で解く」と同じ形)。
;;; 2026-09-25 に coordinator.hy から分けた(handler の組 coordinator/entry/handler_sets.hy がこの受付を本番の組に入れる)。
(require doeff-hy.macros [defk deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import json)
(import queue)
(import signal)
(import sys)
(import typing [Callable Protocol runtime-checkable])
(import threading)
(import time)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import urllib.parse [urlsplit parse-qsl])


(defclass ReplySlot []
  "server の thread が返事を待つ札。返事の本文は protocol が送る byte(data)と content-type にして置く。"
  (defn #^ None __init__ [self]
    (setv self.done (threading.Event) self.status 500 self.created (time.monotonic))
    (setv #^ bytes self.data b"" #^ str self.content-type "application/json; charset=utf-8")))


(defclass RawRequest []
  "受付が受けた HTTP 要求 1 件のまだ解かない形(method・path・query・JSON を読んだ本文・返事の札・名乗り・相手)。
   調停ループ(と記録の置き場の Program)へは shared/protocol/inbox.hy の http-requests が Request に解いて渡す。"
  (defn #^ None __init__ [self #^ str method #^ str path #^ dict query #^ object body #^ ReplySlot slot
                          #^ (| str None) actor #^ str peer]
    (setv self.method method self.path path self.query query self.body body self.slot slot self.actor actor self.peer peer)))


(defn #^ tuple json-reply [#^ object body]
  "受付の thread が自分で答える返事(probe・読めない本文・時間切れ)を、送る byte と content-type の組にするため。"
  #((.encode (json.dumps body :ensure-ascii False) "utf-8") "application/json; charset=utf-8"))


;; probe の閾値(秒)。ループは要求が無くても 1 秒ごとに NextRequests を出すので、ふだんの「最後に取りに来てから」は 1 秒 + 1 まとまりの
;; 処理(fsync の実測の最大 2.9〜3.6 秒・longhorn の詰まりで最長 13 秒・k8s の読みは 3 秒で打ち切り)。
;; readiness はそれより十分長い 30 秒(worker の返事の上限 REPLY-SECONDS 15 秒の 2 倍)、liveness は「固まった」と言える 120 秒。
;; liveness が落ちると kubelet が container を作り直す(状態は耐久の置き場から読み直す)。
(setv READY-STALL-SECONDS 30.0)
(setv LIVE-STALL-SECONDS 120.0)


(deff probe-verdict [#^ str path #^ (| float None) stalled-seconds]  ; defk にできない: 受け口の HTTP の thread(Program の外)が probe ごとに呼ぶ純粋な綴り
  {:pre [(: path str) (: stalled-seconds (| float None))] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation" :spells "json"}}
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


(defclass RequestInbox []
  "HTTP server(別 thread)が受けた要求を生の形で並べる箱。調停ループは 1 件ずつ取り出して返事を置く。
   formats = probe が名乗る本文の形の版の受け入れる範囲(coordinator の entry が cluster_model の ACCEPTED-FORMATS を渡す — この
   module は intent の型を読まない)。"
  (defn #^ None __init__ [self #^ int port #^ Callable [clock time.monotonic] #^ tuple [formats #()]]
    ;; last-take = 調停ループが最後に要求を取りに来た時刻(単調時計)。probe はこれだけで答える(ループを通さない)。
    (setv self.queue (queue.Queue) self.port port self.server None self.clock clock self.last-take None self.formats formats))

  (defn #^ tuple probe [self #^ str path]
    "k8s の probe(/livez・/readyz)の答え。HTTP の thread が直に答える — 調停ループの遅れ(fsync・k8s の API)に巻き込まれない。"
    ;; 本文の形の版の受け入れる範囲も名乗る(送り手と worker が自分の版を合わせられるように)。
    (setv #(status body) (probe-verdict path (if (is self.last-take None) None (- (self.clock) self.last-take))))
    #(status (| body {"formats" (list self.formats)})))

  (defn #^ None start [self]
    (setv inbox self)
    (defclass Handler [BaseHTTPRequestHandler]
      ;; HTTP/1.1 = 接続を使い回す。HTTP/1.0 では要求ごとに接続を閉じ、client は毎回 TCP を張り直していた
      ;; (tailnet の経路が数秒途絶えると新しい接続は必ず失敗する — coordinator_http.py の説明)。
      ;; 使われなくなった接続の thread は timeout 秒で終わる。client の側(doeff の http-client-factory)は 60 秒で手放すので、timeout は
      ;; それより長く置く — client が先に閉じられた接続へ書く形を起こさない(doeff-core-effects の _http_handlers_impl.hy の頭の註の契約)。
      (setv protocol-version "HTTP/1.1" timeout 120)
      (defn #^ None log-message [self #^ str format #^ object #* args] None)
      (defn #^ None _handle [self #^ str method]
        (setv split (urlsplit self.path))
        ;; probe は並べずに答える(調停ループが fsync や k8s の読みで数秒止まっても、probe が時間切れにならない)。
        (when (and (= method "GET") (in split.path #("/livez" "/readyz")))
          (setv #(status body) (.probe inbox split.path))
          (return (.send self status #* (json-reply body))))
        (setv length (int (or (.get self.headers "Content-Length") 0))
              raw (if (> length 0) (.read self.rfile length) b"")
              slot (ReplySlot))
        (try
          (setv body (if raw (json.loads raw) None))
          (except [error ValueError]
            (return (.send self 400 #* (json-reply {"error" (.format "JSON を読めない: {}" error)})))))
        (.put inbox.queue (RawRequest method split.path (dict (parse-qsl split.query)) body slot
                                      (.get self.headers "X-Actor") (str (get self.client-address 0))))
        (if (.wait slot.done 30.0)
            (do
              ;; 返事まで 1 秒を超えた要求を 1 行出す(調停ループが何かを待って止まった時の手がかり)。版の変化を待つ読み(GET /watch)は
              ;; 待つのが仕事なので出さない(#1933)。札を作った時刻と同じ単調な時計で、この thread が測る。
              (setv waited (- (time.monotonic) slot.created))
              (when (and (> waited 1.0) (!= split.path "/watch"))
                (print (.format "coordinator: 遅い返事 {:.1f} 秒: {} {}" waited method split.path) :file sys.stderr :flush True))
              (.send self slot.status slot.data slot.content-type))
            (.send self 503 #* (json-reply {"error" "調停ループが返事をしない"}))))
      (defn #^ None send [self #^ int status #^ bytes data #^ str content-type]
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
    "最初の 1 件を timeout 秒まで待ち、その時点で並んでいる生の要求を limit 件まで一緒に取る。"
    (setv self.last-take (self.clock))
    (try (setv first (.get self.queue :timeout timeout))
         (except [queue.Empty] (return [])))
    (setv batch [first])
    (while (< (len batch) limit)
      (try (.append batch (.get-nowait self.queue))
           (except [queue.Empty] (break))))
    batch))


;; --- 停止 --------------------------------------------------------------------------------------

(defclass StopState []
  "停止の合図(SIGTERM の handler が requested を立て、shared/protocol/inbox.hy の stop-flag が読む)。"
  (defn #^ None __init__ [self] (setv self.requested False)))


(defclass [runtime-checkable] StopMark [Protocol]
  "信号で立てる止めの印の形(この module の StopState と worker/protocol/stop の StopState — 層の向きで 1 つの型に寄せられない)。"
  (setv #^ bool requested False))


(defk stop-on-signals [stop]
  {:pre [(: stop StopMark)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "process の入口(coordinator・記録の置き場・worker の main)が SIGTERM と SIGINT を受けたら、渡された止めの印(この module の StopState か
   worker/protocol/stop の StopState — どちらも requested を持つ)を立てるため。3 つの main が同じ signal.signal の 2 行と信号の関数を
   入口の層で直に書いていた — 生の副作用(signal)は foundation に置く(DOEFF106)。signal の登録は main の thread からだけ通るので、
   入口の main が run で 1 度だけ呼ぶ。"
  (signal.signal signal.SIGTERM (fn [signum frame] (setv stop.requested True)))
  (signal.signal signal.SIGINT (fn [signum frame] (setv stop.requested True)))
  None)
