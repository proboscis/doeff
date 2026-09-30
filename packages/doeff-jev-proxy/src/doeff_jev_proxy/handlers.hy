;;; Jev の呼び出しを覚える代理の答え手。
;;;
;;;   sqlite-store-handler   覚えた答えと計器の置き場(SQLite の file 1 つ — 土台の I/O)。PrepareStore・LookupAnswer・LookupAnswers・
;;;                          LoadRemembered・RememberAnswer・ForgetAnswer・ReadAnswer・Count・CountTimes・ReadCounters
;;;   remembered-index-handler  覚えた答えの memory の写し(置き場の内側 — 起動の時に置き場から読み、覚える・消す時に置き場と一緒に直す)。
;;;                          覚えている時だけの問いの束(LookupAnswers)は写しから答え、置き場を読まない(agora-redesign #1912)
;;;   jev-upstream-handler   AskJev を汎用の HttpRequest に出し直す(翻訳だけ — 実 I/O は外側の http-production-handler)。
;;;                          キーは本物の Jev への見出しにだけ載せ、答えにも理由の文にも載せない
;;;   single-flight-handler  Coalesce — 同じ鍵の同時の Program を 1 回だけ走らせる(錠と印 — 土台の同期。要求ごとの thread の間で共有する)
;;;   roster-handler         IdentifyCaller — Authorization の token を身元の名簿(sha256 だけを持つ)で引く
;;;
;;; 並び(外側が先): try・await・http-production → sqlite-store → remembered-index → jev-upstream → roster → single-flight → program。
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
(import doeff_jev_proxy.values [Event StoredAnswer Remembered UpstreamReply UpstreamUnreachable Coalesced Counters Caller Stranger])
(import doeff_jev_proxy.effects [PrepareStore LookupAnswer LookupAnswers LoadRemembered RememberAnswer ForgetAnswer ReadAnswer AskJev Coalesce
                                Count CountTimes ReadCounters IdentifyCaller])

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


(defrecord RememberedIndex
  "覚えた答えの memory の写し(agora-redesign #1912): answers = 鍵 → StoredAnswer / served = model → その model の今の版 /
   lock = 2 つの表の錠。要求ごとの thread の間で 1 つを共有する。置き場(SQLite)が正本で、写しは起動の時に置き場から読み、
   覚える・消す時に置き場へ書いた後で直す — 書くのはこの proxy の process だけ(replicas 1・Recreate)なので、写しは置き場とずれない。
   写しを持つ理由: 置き場を要求ごとに開き直すと、読みの速さが node の page cache 次第になる。memory の足りない node(k3s-1 —
   MemAvailable 250 MB・page cache を 20 秒ほどで捨てる)では 1 鍵ごとに網の向こうの Longhorn の replica から読み直して 1 鍵 約 12 ms、
   1000 鍵の束が 約 12 秒になり、linter の待ち(全部の束で 5 秒)を越えていた。"
  (#^ dict answers)
  (#^ dict served)
  (#^ _thread.LockType lock))


(defk new-remembered-index []
  {:pre [] :post [(: % RememberedIndex)]}
  "空の答えの写しを作るため(composition root が 1 つ作って remembered-index-handler に渡す — 中身は PrepareStore で置き場から読む)。"
  (RememberedIndex :answers {} :served {} :lock (threading.Lock)))


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
  (LoadRemembered []
    (with [connection (closing (sqlite3.connect path :timeout BUSY-SECONDS))]
      (val rows (.fetchall (.execute connection "SELECT key, model, served_model, body, hits FROM answers")))
      (val served (dict (.fetchall (.execute connection "SELECT model, served_model FROM served_models")))))
    (resume (Remembered :answers (tuple (gfor row rows (StoredAnswer :key (get row 0) :model (get row 1) :served-model (get row 2)
                                                                     :body (bytes (get row 3)) :hits (get row 4))))
                        :served served)))
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


(defhandler remembered-index-handler [index]
  ;; 引数に残す理由: 写しは要求ごとの run(と thread)をまたいで 1 つを共有する — session の値は run ごとに分かれて共有にならない。
  ;; 置き場(sqlite-store-handler)の内側に置き、置き場への読み書きは同じ effect を外側へ出し直して頼む(写しは I/O を持たない)。
  (PrepareStore []
    (<- (PrepareStore))
    (<- loaded (LoadRemembered))
    (with [_ index.lock]
      (.clear index.answers)
      (.update index.answers (dfor stored loaded.answers stored.key stored))
      (.clear index.served)
      (.update index.served loaded.served))
    (resume None))
  ;; 覚えている時だけの問いの束は写しから答える(置き場を読まない)。版の決まりは置き場の LOOKUP-MANY-SQL と同じ: その model の今の版が
  ;; 分からない・答えが版を名乗らない・答えの版が今の版と同じ、のどれかの答えだけを返す。
  (LookupAnswers [keys]
    (val found (with [_ index.lock]
                 (tuple (gfor key (dict.fromkeys keys)
                              :setv stored (.get index.answers key)
                              :if (and (is-not stored None)
                                       (or (not-in stored.model index.served)
                                           (= stored.served-model "")
                                           (= stored.served-model (get index.served stored.model))))
                              stored))))
    (resume found))
  ;; 1 件の問いは置き場で引く(答えの hits を置き場で数える)。引けた答えで写しの hits を置き場にそろえる。
  (LookupAnswer [key]
    (<- stored (LookupAnswer key))
    (when (is-not stored None)
      (with [_ index.lock]
        (setv (get index.answers key) stored)))
    (resume stored))
  (RememberAnswer [key model served-model request body]
    (<- (RememberAnswer key model served-model request body))
    (with [_ index.lock]
      (setv (get index.answers key) (StoredAnswer :key key :model model :served-model served-model :body body :hits 0))
      (when served-model
        (setv (get index.served model) served-model)))
    (resume None))
  (ForgetAnswer [key]
    (<- removed (ForgetAnswer key))
    (with [_ index.lock]
      (.pop index.answers key None))
    (resume removed)))


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
