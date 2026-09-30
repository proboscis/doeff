;;; Jev の呼び出しを覚える代理の答え手。
;;;
;;;   sqlite-store-handler   覚えた答えと計器の置き場(SQLite の file 1 つ — 土台の I/O)。PrepareStore・LookupAnswer・LookupAnswers・
;;;                          RememberAnswer・ForgetAnswer・ReadAnswer・Count・CountTimes・ReadCounters
;;;   jev-upstream-handler   AskJev を汎用の HttpRequest に出し直す(翻訳だけ — 実 I/O は外側の http-production-handler)。
;;;                          キーは本物の Jev への見出しにだけ載せ、答えにも理由の文にも載せない
;;;   single-flight-handler  Coalesce — 同じ鍵の同時の Program を 1 回だけ走らせる(錠と印 — 土台の同期。要求ごとの thread の間で共有する)
;;;   roster-handler         IdentifyCaller — Authorization の token を身元の名簿(sha256 だけを持つ)で引く
;;;
;;; 並び(外側が先): try・await・http-production → sqlite-store → jev-upstream → roster → single-flight → program。
;;; single-flight は一番内側に置く: 先頭が走らせる Program の effect(LookupAnswer・AskJev・RememberAnswer)は、この handler の外側の
;;; 答え手が受ける。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import _thread)
(import sqlite3)
(import threading)
(import time)
(import contextlib [closing])
(import doeff [Try])
(import doeff_vm [Ok Err])
(import doeff_core_effects.http_effects [HttpRequest HttpFailed])
(import doeff_records.principals [Roster Principal Unauthorized identify])
(import doeff_jev.target [JevTarget])
(import doeff_jev_proxy.values [Event StoredAnswer UpstreamReply UpstreamUnreachable Coalesced Counters Caller Stranger])
(import doeff_jev_proxy.effects [PrepareStore LookupAnswer LookupAnswers RememberAnswer ForgetAnswer ReadAnswer AskJev Coalesce Count CountTimes
                                ReadCounters IdentifyCaller])

;; 置き場の表(起動の時に 1 度だけ流す — 既存の表を消さない・変えない)。
(val SCHEMA-STATEMENTS
  #("PRAGMA journal_mode=WAL"
    "CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, model TEXT NOT NULL, served_model TEXT NOT NULL,
       request BLOB NOT NULL, body BLOB NOT NULL, created_at REAL NOT NULL, hits INTEGER NOT NULL DEFAULT 0, last_hit_at REAL)"
    "CREATE TABLE IF NOT EXISTS served_models (model TEXT PRIMARY KEY, served_model TEXT NOT NULL, seen_at REAL NOT NULL)"
    "CREATE TABLE IF NOT EXISTS counters (event TEXT PRIMARY KEY, count INTEGER NOT NULL)"))
;; 今の版の model の答えだけを引く(model の今の版が分からない・答えが版を名乗らない時は当てる)。
(val LOOKUP-SQL
  "SELECT a.key, a.model, a.served_model, a.body, a.hits FROM answers a LEFT JOIN served_models s ON s.model = a.model
   WHERE a.key = ? AND (s.served_model IS NULL OR a.served_model = '' OR a.served_model = s.served_model)")
(val READ-SQL "SELECT key, model, served_model, body, hits FROM answers WHERE key = ?")
;; 鍵の束を引く(LOOKUP-SQL と同じ版の決まり・{} に鍵の数だけの ? を入れる)。
(val LOOKUP-MANY-SQL
  "SELECT a.key, a.model, a.served_model, a.body, a.hits FROM answers a LEFT JOIN served_models s ON s.model = a.model
   WHERE a.key IN ({}) AND (s.served_model IS NULL OR a.served_model = '' OR a.served_model = s.served_model)")
;; 鍵の束を 1 度の SELECT で引く数(SQLite の変数の数の上限 999 より小さく)。
(val LOOKUP-CHUNK 500)
;; 同じ SQLite の file を複数の thread が開く時の待ちの上限(秒)。
(val BUSY-SECONDS 30.0)
;; 同時の問いの相乗りが先頭の答えを待つ上限(秒 — 本物の Jev の時間切れより長く)。
(val JOIN-SECONDS 180.0)


(defrecord Flight
  "走っている取りに行き 1 つ: done = 先頭が答えを置いたら立つ印 / outcome = 先頭の答え(Ok か Err を 1 つ — 置くまで空の list)。"
  (#^ threading.Event done)
  (#^ list outcome))


(defrecord Flights
  "同じ鍵の走っている取りに行きの表(鍵 → Flight)と、その表の錠。要求ごとの thread の間で 1 つを共有する。"
  (#^ dict table)
  (#^ _thread.LockType lock))


(defk new-flights []
  {:pre [] :post [(: % Flights)]}
  "空の相乗りの表を作るため(composition root が 1 つ作って single-flight-handler に渡す)。"
  (Flights :table {} :lock (threading.Lock)))


(defhandler sqlite-store-handler [path]
  ;; 引数に残す理由: 要求ごとに別の run で答える(HTTP の要求 1 つ = run 1 つ)ので、全部の run が同じ置き場を指すよう組み立ての時に 1 度だけ渡す
  (PrepareStore []
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (for [statement SCHEMA-STATEMENTS] (.execute connection statement))
      (.commit connection))
    (resume None))
  (LookupAnswer [key]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val row (.fetchone (.execute connection LOOKUP-SQL #(key))))
      (when (is-not row None)
        (.execute connection "UPDATE answers SET hits = hits + 1, last_hit_at = ? WHERE key = ?" #((time.time) key))
        (.commit connection)))
    (resume (if (is row None)
                None
                (StoredAnswer :key (get row 0) :model (get row 1) :served-model (get row 2) :body (bytes (get row 3))
                              :hits (+ (get row 4) 1)))))
  ;; 覚えている時だけの問いの束は読むだけ — 答えごとの hits を書かない(数は計器 peek-hit が持つ)。書くと 1000 鍵の束が 1000 行の
  ;; UPDATE と commit になり、Longhorn の volume の上で 1 束 約 5 秒・並べた束は書きの錠で順番待ちになって、linter の待ち(全部の束で
  ;; 5 秒)に収まらなかった(agora-redesign #1885 — 読み 0.01 秒・JSON 0.01 秒に対し 5.4 秒)。
  (LookupAnswers [keys]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val chunks (lfor start (range 0 (len keys) LOOKUP-CHUNK) (cut keys start (+ start LOOKUP-CHUNK))))
      (val rows (lfor chunk chunks
                      row (.fetchall (.execute connection (.format LOOKUP-MANY-SQL (.join "," (* ["?"] (len chunk)))) chunk))
                      row)))
    (resume (tuple (gfor row rows (StoredAnswer :key (get row 0) :model (get row 1) :served-model (get row 2) :body (bytes (get row 3))
                                                :hits (get row 4))))))
  (RememberAnswer [key model served-model request body]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val now (time.time))
      (.execute connection
                "INSERT OR REPLACE INTO answers (key, model, served_model, request, body, created_at, hits) VALUES (?, ?, ?, ?, ?, ?, 0)"
                #(key model served-model request body now))
      (when served-model
        (.execute connection "INSERT OR REPLACE INTO served_models (model, served_model, seen_at) VALUES (?, ?, ?)"
                  #(model served-model now)))
      (.commit connection))
    (resume None))
  (ForgetAnswer [key]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val removed (. (.execute connection "DELETE FROM answers WHERE key = ?" #(key)) rowcount))
      (.commit connection))
    (resume (> removed 0)))
  (ReadAnswer [key]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val row (.fetchone (.execute connection READ-SQL #(key)))))
    (resume (if (is row None)
                None
                (StoredAnswer :key (get row 0) :model (get row 1) :served-model (get row 2) :body (bytes (get row 3))
                              :hits (get row 4)))))
  (Count [event]
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (.execute connection "INSERT INTO counters (event, count) VALUES (?, 1) ON CONFLICT(event) DO UPDATE SET count = count + 1"
                #((str event)))
      (.commit connection))
    (resume None))
  (CountTimes [event times]
    (when (> times 0)
      (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
        (.execute connection
                  "INSERT INTO counters (event, count) VALUES (?, ?) ON CONFLICT(event) DO UPDATE SET count = count + excluded.count"
                  #((str event) times))
        (.commit connection)))
    (resume None))
  (ReadCounters []
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val counts (dict (.fetchall (.execute connection "SELECT event, count FROM counters"))))
      (val answers (get (.fetchone (.execute connection "SELECT COUNT(*) FROM answers")) 0)))
    (resume (Counters :counts counts :answers answers))))


(defhandler jev-upstream-handler [target timeout-seconds]
  ;; 引数に残す理由: 宛先とキーは起動の時に 1 度だけ解く(キーの file を要求ごとに読まない)。時間切れは宛先と組で決まる
  (AskJev [body]
    (val headers (| {"content-type" "application/json"}
                    (if target.api-key {"authorization" (+ "Bearer " target.api-key)} {})))
    (<- response (HttpRequest "POST" target.base-url :headers headers :body body :timeout-seconds timeout-seconds
                              :max-retries 0 :failures-as-values True))
    (resume (if (isinstance response HttpFailed)
                (UpstreamUnreachable :detail response.detail)
                (UpstreamReply :status response.status :body response.content)))))


(defhandler single-flight-handler [flights]
  ;; 引数に残す理由: 相乗りの表は要求ごとの run(と thread)をまたいで 1 つを共有する — session の値は run ごとに分かれて共有にならない
  (Coalesce [key program]
    (val claimed (with [_ flights.lock]
                   (if (in key flights.table)
                       #((get flights.table key) False)
                       (do
                         (setv (get flights.table key) (Flight :done (threading.Event) :outcome []))
                         #((get flights.table key) True)))))
    (val flight (get claimed 0))
    (if (get claimed 1)
        (do
          (<- outcome (Try program))
          (with [_ flights.lock]
            (.append flight.outcome outcome)
            (.pop flights.table key None))
          (.set flight.done)
          (if (isinstance outcome Ok)
              (resume (Coalesced :value outcome.value :joined False))
              (raise outcome.error)))
        (do
          (when (not (.wait flight.done JOIN-SECONDS))
            (raise (TimeoutError (.format "同じ鍵の先頭の問いが {} 秒で終わらない" JOIN-SECONDS))))
          (val joined (get flight.outcome 0))
          (if (isinstance joined Ok)
              (resume (Coalesced :value joined.value :joined True))
              (raise joined.error))))))


(defhandler roster-handler [roster admins]
  ;; 引数に残す理由: 名簿と管理者の名は起動の時に Secret の file から 1 度だけ読む(要求ごとに読まない)
  (IdentifyCaller [authorization]
    (<- found (identify roster authorization))
    (resume (if (isinstance found Principal)
                (Caller :name found.name :admin (in found.name admins))
                (Stranger :reason found.reason)))))
