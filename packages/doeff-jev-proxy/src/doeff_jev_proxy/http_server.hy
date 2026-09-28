;;; Jev の呼び出しを覚える代理の HTTP の口 — 標準の http.server で要求を受け、service.respond の Program を要求ごとに走らせる。
;;;
;;; 判断は持たない(流れは service.hy)。ここが持つのは I/O の配線だけ: 見出しと本文を ProxyRequest にし、runner(handler の組を
;;; 被せて run する関数)で respond を走らせ、答えを書く。要求ごとに thread を立てる(同じ鍵の同時の問いは single-flight-handler が
;;; 1 回にまとめる)。Authorization の見出しの値は log に書かない(要求の行の log を出さない)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import json)
(import sys)
(import threading)
(import traceback)
(import http.server [BaseHTTPRequestHandler ThreadingHTTPServer])
(import doeff_jev_proxy.values [ProxyRequest ProxyReply Header])

;; 受ける本文の上限(byte — Jev の問いは定義 1 つの source と問いの文で数 KB)。
(val REQUEST-MAX-BYTES (* 4 1024 1024))


(defrecord ProxyServerConfig
  "HTTP の口 1 つの組み立て: runner = ProxyRequest → ProxyReply(handler の組を被せて respond を走らせる関数)/ host・port(0 = 空いている port)。"
  (#^ Callable runner)
  (setv #^ str host "127.0.0.1")
  (setv #^ int port 0))


(defrecord RunningServer
  "開いた HTTP の口: url = 基の URL(http://host:port)/ server / thread。止めるのは stop-server。"
  (#^ str url)
  (#^ ThreadingHTTPServer server)
  (#^ threading.Thread thread))


(defn #^ ProxyReply plain-failure [#^ int status #^ str error #^ str reason]  ; defk にできない: Program の実行そのものが壊れた時にも答えるため、run を通さずに綴る
  "断りの答えを run を通さずに組む(500 の実装の誤りと、本文を読む前に断る上限超え)。"
  (ProxyReply :status status :headers #((Header :name "content-type" :value "application/json"))
              :body (.encode (json.dumps {"error" error "reason" reason} :ensure-ascii False) "utf-8")))


(defn #^ ProxyReply answer-request [#^ ProxyServerConfig config #^ ProxyRequest request]  ; defk にできない: http.server の callback(do_GET / do_POST)から呼ぶ
  "要求 1 つに答える。実装の誤りは 500 にする(口を落とさない・追跡は標準の誤りへ)。
   Exception 以外(BaseException の子 — doeff の VM の panic が Python に出る PanicException など)も 1 行を標準の誤りへ書いて 500 にする:
   受けなければ要求の thread が黙って落ち、接続は 1 byte も書かれずに切れる(呼び手には Connection reset しか見えない)。
   KeyboardInterrupt / SystemExit も投げ直さない — 代理を止める道は main の thread の signal(serve の threading.Event)だけで、
   Python は signal 由来の KeyboardInterrupt を main の thread にしか出さない。要求の thread で投げ直しても process は止まらず
   (threading は thread の中の SystemExit を黙って捨てる)、接続が黙って切れるだけになる。"
  (try
    (config.runner request)
    (except [error Exception]
      (traceback.print-exc)
      (plain-failure 500 "internal" (.format "{}: {}" (. (type error) __name__) error)))
    (except [error BaseException]
      (setv reason (.format "{}: {}" (. (type error) __name__) (get (.splitlines (+ (str error) "\n")) 0)))
      (print (.format "jev-proxy: 要求の処理が Exception 以外で落ちた — 500 で答える: {} {}: {}"
                      request.method request.path reason)
             :file sys.stderr :flush True)
      (plain-failure 500 "internal" reason))))


(defn #^ type request-handler-class [#^ ProxyServerConfig config]  ; defk にできない: http.server が要求ごとに作る class を返す
  "この口の組で答える BaseHTTPRequestHandler の class を作る。"
  (defclass ProxyRequestHandler [BaseHTTPRequestHandler]
    (setv protocol-version "HTTP/1.1")
    ;; 見出しと本文は別の write になる — 遅い網(tailnet の中継)で Nagle が本文の小さな segment を 1 往復待たせないように切る。
    (setv disable-nagle-algorithm True)

    (defn #^ None reply [self #^ ProxyReply answer]
      "答えを書く。"
      (.send-response self answer.status)
      (for [header answer.headers] (.send-header self header.name header.value))
      (.send-header self "Content-Length" (str (len answer.body)))
      (.end-headers self)
      (.write self.wfile answer.body))

    (defn #^ None handle-method [self #^ str method]
      "要求 1 つを ProxyRequest にして答える。"
      (setv path (get (.split self.path "?" 1) 0)
            length (int (or (.get self.headers "Content-Length") "0")))
      (when (> length REQUEST-MAX-BYTES)
        ;; 上限を超える本文は読まずに断る(接続は閉じる — 読み残しの本文を次の要求として読まないため)。
        (setv self.close-connection True)
        (return (.reply self (plain-failure 413 "too-large" (.format "本文が {} byte を超える" REQUEST-MAX-BYTES)))))
      (setv body (if (> length 0) (.read self.rfile length) b"")
            headers (tuple (gfor #(name value) (.items self.headers) (Header :name (.lower name) :value value))))
      (.reply self (answer-request config (ProxyRequest :method method :path path :headers headers :body body))))

    (defn #^ None do-GET [self] "GET を答える。" (.handle-method self "GET"))
    (defn #^ None do-POST [self] "POST を答える。" (.handle-method self "POST"))
    (defn #^ None do-DELETE [self] "DELETE を答える。" (.handle-method self "DELETE"))

    (defn #^ None log-message [self #^ str format #* args]
      "要求ごとの行を書かない(見出しの値を log に残さない・計器は /metrics)。"
      None))
  ProxyRequestHandler)


(defn #^ RunningServer start-proxy-server [#^ ProxyServerConfig config]  ; defk にできない: thread を立てて口を開く composition root の部品(返す物が開いた口)
  "HTTP の口を開き、受けの thread を立てる。"
  (setv server (ThreadingHTTPServer #(config.host config.port) (request-handler-class config)))
  (setv server.daemon-threads True)
  (setv thread (threading.Thread :target server.serve-forever :name "jev-proxy-http" :daemon True))
  (.start thread)
  (setv #(host port) (cut server.server-address 2))
  (RunningServer :url (.format "http://{}:{}" host port) :server server :thread thread))


(defn #^ None stop-server [#^ RunningServer running]  ; defk にできない: composition root が口を閉じる部品(検と SIGTERM の後始末)
  "口を止める(受けている要求を答え終えてから)。"
  (.shutdown running.server)
  (.server-close running.server)
  (.join running.thread))
