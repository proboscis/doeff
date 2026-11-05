;;; Do not return a PostgreSQL connection that carried a large value to the idle list: libpq grows the per-connection send and receive buffers to the largest
;;; message passed through and does not shrink them until closed, so a connection that carried a value over the guideline (LARGE-CARRY-BYTES) is closed on return. The binary of SqlQuery
;;; receives the answer rows in binary form and does not turn a bytea into hexadecimal text (twice the body).
;;;   - Connect to a disposable PostgreSQL (postgres_support/disposable_postgres.py) through a relay (statement_wire_tap.py). Whether a connection was reused is seen from
;;;     the value of pg_backend_pid() (the next borrow after a closed connection is a new backend). The size of the answer rows is seen from the server -> client bytes the relay counted.
;;;   - With both PostgreSQL answerers (postgres-sql-handler, pooled-postgres-sql-handler), fire a single statement (send and receive), a non-batched
;;;     transaction, and a batched transaction.
(require doeff-hy.macros [deftest defk <- val var with-handler])
(import os)
(import concurrent.futures [ThreadPoolExecutor])
(import doeff_core_effects [state])
(import doeff_core_effects [SqlQuery SqlTransaction SqlEnsureTables SqlParam SqlRows SqlTable SqlColumn SqlColumnType])
(import doeff_core_effects [postgres-sql-handler PostgresDatabase PostgresConnections])
(import doeff_core_effects._postgres_sql [LARGE-CARRY-BYTES largest-message-bytes])
(import doeff_core_effects [pooled-postgres-sql-handler])
(import disposable_postgres [session-dsn session-postgres-skip-reason])
(import statement_wire_tap [StatementWireTap])

(val DB "store")
(val POSTGRES-DSN (session-dsn "DOEFF_SQL_TEST_POSTGRES_DSN"))
(val POSTGRES-SKIP-REASON (session-postgres-skip-reason "DOEFF_SQL_TEST_POSTGRES_DSN"))

(val BLOBS (SqlTable :name "carry_blobs"
                     :columns #((SqlColumn :name "id" :type SqlColumnType.INTEGER) (SqlColumn :name "body" :type SqlColumnType.BYTES)
                                (SqlColumn :name "label" :type SqlColumnType.TEXT) (SqlColumn :name "ok" :type SqlColumnType.BOOLEAN))
                     :primary-key #("id")))

;; Body sizes: a send over the guideline / larger than half the guideline (received as hexadecimal text it is twice that and exceeds the guideline, received as binary it does not) / 1 MiB
;; for measuring the size on the wire.
(val LARGE-BODY (os.urandom (+ LARGE-CARRY-BYTES 1)))
(val HALF-BODY (os.urandom (+ (// LARGE-CARRY-BYTES 2) 4096)))
(val MEASURED-BODY (os.urandom (* 1024 1024)))


(deftest test-largest-message-bytes-estimates-the-biggest-row
  ;; The estimate is the maximum of the sum of str (UTF-8 length) and bytes (twice that if received in hexadecimal) per row; other types are not counted.
  (<- plain (largest-message-bytes #(#("abc" b"12345" 7 None) #("é" True)) False))
  (assert (= plain 8) plain)
  (<- doubled (largest-message-bytes #(#("abc" b"12345" 7 None) #("é" True)) True))
  (assert (= doubled 13) doubled)
  (<- none (largest-message-bytes #() True))
  (assert (= none 0) none))


;; --- Real PostgreSQL ----------------------------------------------------------------------------------------------------------

(defk backend-pid []
  {:pre [] :post [(: % int)]
   :tags {:context "sql" :role "program"}}
  "To read the pid of the backend of the connection just borrowed (to see whether a connection was reused)."
  (<- answer (SqlQuery DB "SELECT pg_backend_pid()" #()))
  (get answer.rows 0 0))


(defk stored [id body]
  {:pre [(: id int) (: body bytes)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To write the body body to row id (a single statement -- the body is sent as an argument)."
  (<- answer (SqlQuery DB "INSERT INTO carry_blobs (id, body, label, ok) VALUES (:id, :body, :label, :ok)"
                       #((SqlParam :name "id" :value id) (SqlParam :name "body" :value body) (SqlParam :name "label" :value "tag")
                         (SqlParam :name "ok" :value True))))
  (assert (isinstance answer SqlRows) answer)
  None)


(defk read-row [id binary]
  {:pre [(: id int) (: binary bool)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read row id (binary = receive the answer rows in binary form)."
  (<- answer (SqlQuery DB "SELECT id, body, label, ok, length(body) FROM carry_blobs WHERE id = :id" #((SqlParam :name "id" :value id))
                       binary))
  (assert (isinstance answer SqlRows) answer)
  (get answer.rows 0))


(defk stored-in-transaction [id body batched]
  {:pre [(: id int) (: body bytes) (: batched bool)] :post [(: % None)]
   :tags {:context "sql" :role "program"}}
  "To write the body body to row id inside a transaction (batched = a transaction that bundles round trips)."
  (<- answer (SqlTransaction DB (stored id body) None batched))
  (assert (is answer None) answer)
  None)


(defk read-in-transaction [id binary]
  {:pre [(: id int) (: binary bool)] :post [(: % tuple)]
   :tags {:context "sql" :role "program"}}
  "To read row id inside a batched transaction (to see whether binary reaches the pipeline statements too)."
  (<- row (SqlTransaction DB (read-row id binary) None True))
  row)


(defk reused-after [program]
  {:pre [(: program "Program")] :post [(: % bool)]
   :tags {:context "sql" :role "program"}}
  "To answer whether the next borrow after firing the program is the same connection (same backend) as before (lending limit of 1 -- is the previous borrow's connection in the idle list or
   closed?)."
  (<- before (backend-pid))
  (<- program)
  (<- after (backend-pid))
  (= before after))


(defk carried [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % dict)]
   :tags {:context "sql" :role "program"}}
  "To collect, per way of firing, whether the next borrow reused the same connection, the values of the answer rows, and the size on the wire."
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
  "To check the carried answers against the guideline rule (a connection over it is closed, one under it is reused) and the binary read rule."
  (val reuse (dfor key #("small" "large-send" "text-read" "binary-read" "unbatched" "batched" "batched-text-read" "batched-binary-read")
                   key (get seen key)))
  (assert (= reuse {"small" True "large-send" False "text-read" False "binary-read" True "unbatched" False "batched" False
                    "batched-text-read" False "batched-binary-read" True})
          reuse)
  ;; The values of rows received in binary form are the same as in text form (bytea, text, boolean, number).
  (val expected #(1 HALF-BODY "tag" True (len HALF-BODY)))
  (assert (= (get seen "text-row") expected) (get seen "text-row" 4))
  (assert (= (get seen "binary-row") expected) (get seen "binary-row" 4))
  (assert (= (get seen "pipelined-row") expected) (get seen "pipelined-row" 4))
  ;; Size of the answer on the wire: text form exceeds twice the body in hexadecimal, binary form stays within 1.01 times the body.
  (val body (len MEASURED-BODY))
  (assert (> (get seen "text-wire") (* 2 body)) #((get seen "text-wire") body))
  (assert (< (get seen "binary-wire") (* 1.01 body)) #((get seen "binary-wire") body))
  None)


(defk tapped-dsn [tap]
  {:pre [(: tap StatementWireTap)] :post [(: % str)]
   :tags {:context "sql" :role "program"}}
  "To rewrite the test DSN into a DSN that connects to the relay's port (no SSL or GSS negotiation -- the relay passes plain messages)."
  (import psycopg.conninfo [make-conninfo])
  (make-conninfo (or POSTGRES-DSN "") :host "127.0.0.1" :port (str tap.port) :sslmode "disable" :gssencmode "disable"))


(defk tap-target []
  {:pre [] :post [(: % (| str tuple))]
   :tags {:context "sql" :role "program"}}
  "To read the port of the PostgreSQL the test DSN points at (a unix socket path or #(host port))."
  (import psycopg.conninfo [conninfo-to-dict])
  (val given (conninfo-to-dict (or POSTGRES-DSN "")))
  (val host (.get given "host" "127.0.0.1"))
  (val port (.get given "port" "5432"))
  (if (.startswith host "/")
      (.format "{}/.s.PGSQL.{}" host port)
      #(host (int port))))


(deftest test-postgres-closes-connections-that-carried-large-values
  {:skip-if (bool POSTGRES-SKIP-REASON) :skip-reason POSTGRES-SKIP-REASON}
  ;; A connection that sent or received a value over the guideline is closed on return with both PostgreSQL answerers (the next borrow is a new backend). A connection under it is
  ;; reused. The binary read gives the same values as the text form and is not twice the body on the wire.
  (<- target (tap-target))
  (val tap (StatementWireTap target))
  (<- dsn (tapped-dsn tap))
  (val pool (ThreadPoolExecutor :max-workers 2))
  (try
    (val plain (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- direct (with-handler [(postgres-sql-handler plain)] (carried tap)))
      ;; Closed connections are counted under reason large. With a lending limit of 1, each close lets the next borrow open one (if the last request's
      ;; connection was closed, the number opened and the number closed are equal).
      (val tallies (.tallies plain))
      (assert (and (> tallies.large 0) (<= tallies.large tallies.opened (+ tallies.large 1))) tallies)
      (finally (.close plain)))
    (val shared (PostgresConnections #((PostgresDatabase :name DB :dsn dsn)) :size 1))
    (try
      (<- pooled (with-handler [(state) (pooled-postgres-sql-handler shared pool)] (carried tap)))
      (finally (.close shared)))
    (finally (.shutdown pool) (.close tap)))
  (<- (asserted-carried direct))
  (<- (asserted-carried pooled)))
