;;; 記録の service の HTTP の口を開く部品 — 標準の http.server で要求を受け、service.respond の Program を要求ごとに走らせる。
;;;
;;; 判断は持たない(流れは service.hy)。ここが持つのは I/O の配線だけ: 見出しと本文を HttpRequest にし、要求ごとに
;;; handler の組を借り(lease-handlers — PostgreSQL なら接続を 1 本借りる)、runner(時計と scheduler を被せて run する関数)で
;;; respond を走らせ、答えを書く。composition root(main.hy・検の解釈器)がこの部品に置き場と時計を渡す。
;;;
;;; concurrent = True で要求ごとに thread を立てる(PostgreSQL の置き場 — 接続は要求ごとに借りる)。memory の置き場は
;;; thread で守られていないので False(1 本の thread で順に答える)。
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import json)
(import traceback)
(import threading)
(import http.server [BaseHTTPRequestHandler HTTPServer ThreadingHTTPServer])
(import doeff_records.values [RecordsSchema])
(import doeff_records.principals [Roster])
(import doeff_records.service [HttpRequest HttpAnswer RecordsService respond REQUEST-MAX-BYTES])
(import doeff_records.wire [ERROR-INTERNAL ERROR-MALFORMED STATUS-OF-ERROR])

(setv AUTH-HEADER "Authorization")
(setv JSON-CONTENT-TYPE "application/json; charset=utf-8")


(defclass [(dataclass :frozen True)] RecordsServerConfig []
  "HTTP の口 1 つの組み立て: schema = 置き場の宣言 / roster = 身元の名簿 / lease-handlers = () → with で使う物(入ると 書き手の名 → handler の関数を返す)/
   runner = Program → 答え(時計と scheduler を被せて run する)/ host・port(0 = 空いている port)/ concurrent = 要求ごとに thread。"
  (#^ RecordsSchema schema)
  (#^ Roster roster)
  (#^ Callable lease-handlers)
  (#^ Callable runner)
  (setv #^ str host "127.0.0.1")
  (setv #^ int port 0)
  (setv #^ bool concurrent True))


(defclass RunningServer []
  "開いた HTTP の口: url = 基の URL(http://host:port)/ close() = 止めて port を返す。"
  (defn #^ None __init__ [self #^ str url #^ HTTPServer server #^ threading.Thread thread]
    (setv self.url url self.server server self.thread thread))

  (defn #^ None close [self]
    "口を止める(受けている要求を答え終えてから)。"
    (.shutdown self.server)
    (.server-close self.server)
    (.join self.thread)))


(defn #^ HttpAnswer plain-refusal [#^ str error #^ str reason]  ; defk にできない: Program の実行そのものが壊れた時にも答えるため、run を通さずに綴る
  "断りの答えを run を通さずに組む(500 の実装の誤りと、本文を読む前に断る上限超え)。"
  (HttpAnswer (get STATUS-OF-ERROR error) (json.dumps {"error" error "reason" reason} :ensure-ascii False :separators #("," ":"))))


(defn #^ HttpAnswer answer-request [#^ RecordsServerConfig config #^ HttpRequest request]  ; defk にできない: http.server の callback(do_GET / do_POST)から呼ぶ
  "要求 1 つに答える: handler の組を借り、respond を runner で走らせる。実装の誤りは 500 にする(口を落とさない)。"
  (try
    (with [handler-for (config.lease-handlers)]
      (config.runner (respond (RecordsService config.schema config.roster handler-for) request)))
    (except [error Exception]
      ;; 実装の誤りは口を落とさず 500 で答え、追跡は標準の誤りへ残す(黙って捨てない)。
      (traceback.print-exc)
      (plain-refusal ERROR-INTERNAL (.format "{}: {}" (. (type error) __name__) error)))))


(defn #^ (get type BaseHTTPRequestHandler) request-handler-class [#^ RecordsServerConfig config]  ; defk にできない: http.server が要求ごとに作る class を返す
  "この口の組で答える BaseHTTPRequestHandler の class を作る。"
  (defclass RecordsRequestHandler [BaseHTTPRequestHandler]
    (setv protocol-version "HTTP/1.1")


    (defn #^ None reply [self #^ HttpAnswer answer]
      "答えを書く。"
      (setv payload (.encode answer.body "utf-8"))
      (.send-response self answer.status)
      (.send-header self "Content-Type" JSON-CONTENT-TYPE)
      (.send-header self "Content-Length" (str (len payload)))
      (.end-headers self)
      (.write self.wfile payload))

    (defn #^ None handle-method [self #^ str method]
      "要求 1 つを HttpRequest にして答える。"
      (setv path (get (.split self.path "?" 1) 0)
            length (int (or (.get self.headers "Content-Length") "0")))
      (when (> length REQUEST-MAX-BYTES)
        ;; 上限を超える本文は読まずに断る(接続は閉じる — 読み残しの本文を次の要求として読まないため)。
        (setv self.close-connection True)
        (return (.reply self (plain-refusal ERROR-MALFORMED (.format "本文が {} byte を超える" REQUEST-MAX-BYTES)))))
      (setv body (if (> length 0) (.read self.rfile length) b""))
      (.reply self (answer-request config (HttpRequest method path (.get self.headers AUTH-HEADER) body))))

    (defn #^ None do-GET [self] "GET を答える。" (.handle-method self "GET"))
    (defn #^ None do-POST [self] "POST を答える。" (.handle-method self "POST"))
    (defn #^ None do-PUT [self] "PUT を答える(route が無いので断る)。" (.handle-method self "PUT"))
    (defn #^ None do-DELETE [self] "DELETE を答える(route が無いので断る)。" (.handle-method self "DELETE"))

    (defn #^ None log-message [self #^ str format #^ object #* args]
      "要求ごとの行を標準の誤りへ書かない(計器と slog は composition root の持ち物)。"
      None))
  RecordsRequestHandler)


(defn #^ RunningServer start-records-server [#^ RecordsServerConfig config]  ; defk にできない: thread を立てて口を開く composition root の部品(返す物が開いた口)
  "HTTP の口を開き、受けの thread を立てる。答え = RunningServer。"
  (setv server-class (if config.concurrent ThreadingHTTPServer HTTPServer)
        server (server-class #(config.host config.port) (request-handler-class config)))
  (setv thread (threading.Thread :target server.serve-forever :name "doeff-records-http" :daemon True))
  (.start thread)
  (setv #(host port) (cut server.server-address 2))
  (RunningServer (.format "http://{}:{}" host port) server thread))
