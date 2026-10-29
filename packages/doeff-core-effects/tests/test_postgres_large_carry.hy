;;; 大きな値を通した PostgreSQL の接続を空きの列に戻さない(card ki-1368e0d6b801): libpq は接続ごとの送りと受けの buffer を通した一番大きな
;;; message まで伸ばして閉じるまで縮めないので、目安(LARGE-CARRY-BYTES)を越える値を通した接続は返す時に閉じる。SqlQuery の binary は
;;; 答えの行を binary の形で受け、bytea を 16 進の text(本文の 2 倍)にしない。
;;;   - 使い捨ての PostgreSQL(postgres_support/disposable_postgres.py)へ、中継(statement_wire_tap.py)を挟んで繋ぐ。接続を使い回したかは
;;;     pg_backend_pid() の値で見る(閉じた接続の次の借りは新しい backend)。答えの行の大きさは中継が数えた server → client の bytes で見る。
;;;   - 2 つの PostgreSQL の答え手(postgres-sql-handler・pooled-postgres-sql-handler)の両方で、文 1 つ(送り・受け)・batched でない
;;;     transaction・batched の transaction を撃つ。
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import os)
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects [state])
(import doeff_core_effects.sql_effects [SqlQuery SqlTransaction SqlEnsureTables SqlParam SqlRows SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects.postgres_sql [postgres-sql-handler PostgresDatabase PostgresConnections LARGE-CARRY-BYTES largest-message-bytes])
(import doeff_core_effects.pooled_postgres_sql [pooled-postgres-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])
(import statement_wire_tap [StatementWireTap])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))

(val BLOBS (SqlTable :name "carry_blobs"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "body" :type SqlColumnType.BYTES)
                                (SqlColumn :name "label" :type SqlColumnType.TEXT) (SqlColumn :name "ok" :type SqlColumnType.BOOLEAN))
                     :primary-key #("id")))

;; 本文の大きさ: 目安を越える送り / 目安の半分より大きい(16 進の text で受けると 2 倍で目安を越え、binary で受ければ越えない)/ wire の
;; 大きさを測る 1 MiB。
(val LARGE-BODY (os.urandom (+ LARGE-CARRY-BYTES 1)))
(val HALF-BODY (os.urandom (+ (// LARGE-CARRY-BYTES 2) 4096)))
(val MEASURED-BODY (os.urandom (* 1024 1024)))


(deftest test-largest-message-bytes-estimates-the-biggest-row
  ;; 見積もりは行ごとの str(UTF-8 の長さ)と bytes(16 進で受けたなら 2 倍)の和の最大で、他の型は数えない。
  (<- plain (largest-message-bytes #(#("abc" b"12345" 7 None) #("é" True)) False))
  (assert (= plain 8) plain)
  (<- doubled (largest-message-bytes #(#("abc" b"12345" 7 None) #("é" True)) True))
  (assert (= doubled 13) doubled)
  (<- none (largest-message-bytes #() True))
  (assert (= none 0) none))


;; --- 実 PostgreSQL ----------------------------------------------------------------------------------------------------------

(defk backend-pid []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "今 借りた接続の backend の pid を読むため(接続を使い回したかを見る)。"
  (<- answer (SqlQuery DB "SELECT pg_backend_pid()" #()))
  (get answer.rows 0 0))


(defk stored [id body]
  {:pre [(: id int) (: body bytes)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "本文 body を行 id に書くため(文 1 つ — 引数で本文を送る)。"
  (<- answer (SqlQuery DB "INSERT INTO carry_blobs (id, body, label, ok) VALUES (:id, :body, :label, :ok)"
                       #((SqlParam :name "id" :value id) (SqlParam :name "body" :value body) (SqlParam :name "label" :value "札")
                         (SqlParam :name "ok" :value True))))
  (assert (isinstance answer SqlRows) answer)
  None)


(defk read-row [id binary]
  {:pre [(: id int) (: binary bool)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "行 id を読むため(binary = 答えの行を binary の形で受ける)。"
  (<- answer (SqlQuery DB "SELECT id, body, label, ok, length(body) FROM carry_blobs WHERE id = :id" #((SqlParam :name "id" :value id))
                       binary))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0))


(defk stored-in-transaction [id body batched]
  {:pre [(: id int) (: body bytes) (: batched bool)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "本文 body を transaction の中で行 id に書くため(batched = 往復をまとめる transaction)。"
  (<- answer (SqlTransaction DB (stored id body) None batched))
  (assert (is answer None) answer)
  None)


(defk read-in-transaction [id binary]
  {:pre [(: id int) (: binary bool)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "行 id を batched の transaction の中で読むため(pipeline の文にも binary が届くかを見る)。"
  (<- row (SqlTransaction DB (read-row id binary) None True))
  row)


(defk reused-after [program]
  {:pre [(: program "Program")] :post [(: % bool)]
   :tags {:context "sql" :role "program"}}
  "program を撃った後の次の借りが、前と同じ接続(同じ backend)かを答えるため(貸し出しの上限 1 本 — 前の借りの接続は空きの列に在るか、
   閉じられたか)。"
  (<- before (backend-pid))
  (<- program)
  (<- after (backend-pid))
  (= before after))


(defk carried [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "撃ち方ごとに、次の借りが同じ接続を使い回したか・答えの行の値・wire の大きさを集めるため。"
  (<- (SqlEnsureTables DB #(BLOBS)))
  (<- (SqlQuery DB "DELETE FROM carry_blobs" #()))
  (<- (stored 1 HALF-BODY))
  (<- (stored 2 MEASURED-BODY))
  (<- small (reused-after (read-row 1 True)))
  (<- large-send (reused-after (stored 10 LARGE-BODY)))
  (<- text-read (reused-after (read-row 1 False)))
  (<- binary-read (reused-after (read-row 1 True)))
  (<- unbatched (reused-after (stored-in-transaction 11 LARGE-BODY False)))
  (<- batched (reused-after (stored-in-transaction 12 LARGE-BODY True)))
  (<- batched-text-read (reused-after (read-in-transaction 1 False)))
  (<- batched-binary-read (reused-after (read-in-transaction 1 True)))
  (<- text-row (read-row 1 False))
  (<- binary-row (read-row 1 True))
  (<- pipelined-row (read-in-transaction 1 True))
  (.reset tap)
  (<- (read-row 2 False))
  (val text-wire (.downstream-bytes tap))
  (.reset tap)
  (<- (read-row 2 True))
  (val binary-wire (.downstream-bytes tap))
  {"small" small "large-send" large-send "text-read" text-read "binary-read" binary-read "unbatched" unbatched "batched" batched
   "batched-text-read" batched-text-read "batched-binary-read" batched-binary-read
   "text-row" text-row "binary-row" binary-row "pipelined-row" pipelined-row "text-wire" text-wire "binary-wire" binary-wire})


(defk asserted-carried [seen]
  {:pre [(: seen dict)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "carried の答えを、目安の決まり(越えた接続は閉じる・越えない接続は使い回す)と binary の読みの決まりに照らすため。"
  (val reuse (dfor key #("small" "large-send" "text-read" "binary-read" "unbatched" "batched" "batched-text-read" "batched-binary-read")
                   key (get seen key)))
  (assert (= reuse {"small" True "large-send" False "text-read" False "binary-read" True "unbatched" False "batched" False
                    "batched-text-read" False "batched-binary-read" True})
          reuse)
  ;; binary の形で受けた行の値は text の形と同じ(bytea・text・真偽・数)。
  (val expected #(1 HALF-BODY "札" True (len HALF-BODY)))
  (assert (= (get seen "text-row") expected) (get seen "text-row" 4))
  (assert (= (get seen "binary-row") expected) (get seen "binary-row" 4))
  (assert (= (get seen "pipelined-row") expected) (get seen "pipelined-row" 4))
  ;; wire の上の答えの大きさ: text の形は 16 進で本文の 2 倍を越え、binary の形は本文の 1.01 倍に収まる。
  (val body (len MEASURED-BODY))
  (assert (> (get seen "text-wire") (* 2 body)) #((get seen "text-wire") body))
  (assert (< (get seen "binary-wire") (* 1.01 body)) #((get seen "binary-wire") body))
  None)


(defk tapped-dsn [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "検の DSN を、中継の口へ繋ぐ DSN に書き換えるため(SSL・GSS の交渉をしない — 中継は平文の message を渡す)。"
  (import psycopg.conninfo [make-conninfo])
  (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1" :port (str tap.port) :sslmode "disable" :gssencmode "disable"))


(defk tap-target []
  {:pre [] :post [(: % (| str tuple))]
   :tags {:context "sql" :role "program"}}
  "検の DSN が指す PostgreSQL の口(unix socket の path か #(host port))を読むため。"
  (import psycopg.conninfo [conninfo-to-dict])
  (val given (conninfo-to-dict (or POSTGRES-DSN "")))
  (val host (.get given "host" "127.0.0.1"))
  (val port (.get given "port" "5432"))
  (if (.startswith host "/")
      (.format "{}/.s.PGSQL.{}" host port)
      #(host (int port))))


(deftest test-postgres-closes-connections-that-carried-large-values
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; 目安を越える値を送った・受けた接続は、2 つの PostgreSQL の答え手とも返す時に閉じる(次の借りは新しい backend)。越えない接続は
  ;; 使い回す。binary の読みは値が text の形と同じで、wire の上で本文の 2 倍にならない。
  (<- target (tap-target))
  (val tap (StatementWireTap target))
  (<- dsn (tapped-dsn tap))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (carried tap)))
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (carried tap)))
      (finally (.close shared)))
    (finally (.shutdown pool) (.close tap)))
  (<- (asserted-carried direct))
  (<- (asserted-carried pooled)))
