;;; sqlite-sql-handler, the I/O-free answerer of the generic SQL effects (sql_effects.hy). It answers with an in-memory DB of stdlib sqlite3
;;; -- a simulation on a virtual clock that starts no real process and no external engine. It knows no business: the caller declares tables with SqlEnsureTables.
;;;   - One `:memory:` connection per database name (check_same_thread = False, autocommit -- transactions are explicit with BEGIN IMMEDIATE).
;;;     A connection is a session value, and the home of session values (the state of doeff_core_effects) is needed on the outside.
;;;   - Arguments rewrite the neutral `:name` to `?` (going through the same rewriting path as the real one, so a notation error is refused at the same place in the simulation too).
;;;   - Failures become SqlFailed via the table from exception class to SQLSTATE class (SQLITE-CLASS-SQLSTATES) -- so the simulation can trigger the branches of "refusal (22 / 23)" and "transient (40)".
;;;     A closed connection is SqlUnreachable. SetSqlOutage can make a database unreachable, and during that time everything is SqlUnreachable.
;;;   - The lock-key of SqlTransaction is not used: there is one connection, and inside a transaction there are only queries to the same database and pure computation, so
;;;     no other task of the scheduler interrupts (sql_transaction.hy). The default is one statement at a time (BEGIN IMMEDIATE -> statement -> COMMIT / ROLLBACK). The one round trip of a transaction that chose batched
;;;     (TransactionFlush -- BEGIN IMMEDIATE, statements, COMMIT) is flushed in order (sqlite-flush -- there is no notion of a round trip, so nothing is combined.
;;;     The answer is the same as the PostgreSQL answerer's). A SqlBatch outside a transaction is refused by name.
;;;   - The column type BOOLEAN does not exist in sqlite, so the read-back is mapped to bool by the declared type name (returning the same value as the real PostgreSQL).
;;; Dialect differences (statements that PostgreSQL / ClickHouse accept but sqlite does not) are outside this answerer: the caller writes statements that work on both (plan A of #802 batch 3).
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import sqlite3)
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SetSqlOutage SqlRows SqlFailed
                                        SqlUnreachable SqlSchemaApplied SqlColumnType SqlText SqlPlaceholder split-statement checked-params
                                        param-value checked-identifier checked-identifiers checked-rows normalized-rows SqlTable SqlParam
                                        SqlValue])
(import doeff_core_effects._sql_transaction [TransactionFlush run-in-transaction run-in-batched-transaction stray-batch])

;; Exception class -> SQLSTATE class (the inverse of the table by which psycopg chooses a class from SQLSTATE -- the same classes as
;; DRIVER-CLASS-SQLSTATES in agora-controllers services/record/handlers_wire.hy). OperationalError is split by statement (sqlite-failure).
(val SQLITE-CLASS-SQLSTATES {"IntegrityError" "23000" "DataError" "22000" "InterfaceError" "22000" "ProgrammingError" "42000"
                             "NotSupportedError" "0A000" "InternalError" "XX000"})
;; Exceptions that sqlite3 raises when binding arguments and that are not members of sqlite3.Error. OverflowError = an integer outside the INTEGER (64 bit) range
;; -- the real PostgreSQL refuses the same value with 22003 (numeric_value_out_of_range). If it were not turned into a value, the exception would escape into the caller's Program and the simulation and the real
;; answers would disagree.
(val BIND-ERRORS #(OverflowError))
(val BIND-CLASS-SQLSTATES {"OverflowError" "22003"})
;; SQLSTATE for lock contention (transient) -- serialization_failure.
(val BUSY-SQLSTATE "40001")
;; SQLSTATE for the rest of OperationalError, such as statement errors and missing tables.
(val OPERATIONAL-SQLSTATE "42000")

;; Declared column type -> sqlite type name (BOOLEAN is a name for mapping the read-back to bool; JSON is text).
(val SQLITE-TYPES {SqlColumnType.INTEGER "INTEGER" SqlColumnType.FLOAT "REAL" SqlColumnType.TEXT "TEXT" SqlColumnType.BYTES "BLOB"
                   SqlColumnType.BOOLEAN "BOOLEAN" SqlColumnType.JSON "TEXT"})

(sqlite3.register-converter "BOOLEAN" (fn [raw] (!= (int raw) 0)))


(defrecord SqliteStatement
  "The statement rewritten to the form passed to sqlite (text = the statement with `?`, values = the argument values in `?` order)."
  (#^ str text)
  (#^ (get tuple #(SqlValue ...)) values))


(defrecord SqliteConnection
  "A database name and the one connection of that DB (a session value in the memory-DB answerer; the content of SqliteFiles in the file-DB answerer)."
  (#^ str name)
  (#^ sqlite3.Connection connection))


(defk sqlite-statement [statement params]
  {:pre [(: statement str) (: params (of tuple SqlParam ...))] :post [(: % SqliteStatement)]
   :tags {:context "sql" :role "foundation"}}
  "To rewrite a statement in the neutral notation to a sqlite `?` statement (an argument-name mismatch is a ValueError). Values are lined up in `?` order from a table that looks each up from its name once
   (after checked-params, so all argument names of the statement are in params -- this saves the cost of calling param-value for each argument)."
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (val by-name (dfor p params p.name p.value))
  (SqliteStatement :text (.join "" (gfor part parts (if (isinstance part SqlPlaceholder) "?" part.text)))
                   :values (tuple (gfor part parts :if (isinstance part SqlPlaceholder) (get by-name part.name)))))


(defk sqlite-failure [class-names message]
  {:pre [(: class-names (of tuple str ...)) (: message str)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To map a sqlite3 exception (class-name list = MRO names, and the message) to a failure value (the table in the header comment)."
  (val lowered (.lower message))
  (cond
    (in "closed database" lowered) (SqlUnreachable :reason message)
    (and (in "OperationalError" class-names) (or (in "locked" lowered) (in "busy" lowered))) (SqlFailed :sqlstate BUSY-SQLSTATE :reason message)
    (in "OperationalError" class-names) (SqlFailed :sqlstate OPERATIONAL-SQLSTATE :reason message)
    (any (gfor name class-names (in name BIND-CLASS-SQLSTATES)))
      (SqlFailed :sqlstate (next (gfor name class-names :if (in name BIND-CLASS-SQLSTATES) (get BIND-CLASS-SQLSTATES name))) :reason message)
    True (SqlFailed :sqlstate (next (gfor name class-names :if (in name SQLITE-CLASS-SQLSTATES) (get SQLITE-CLASS-SQLSTATES name)) None)
                    :reason message)))


(defk sqlite-schema-statements [tables]
  {:pre [(: tables (of tuple SqlTable ...))] :post [(: % (of tuple str ...))]
   :tags {:context "sql" :role "foundation"}}
  "To render a table declaration as sqlite DDL (CREATE TABLE / INDEX IF NOT EXISTS)."
  (var statements [])
  (for [table tables]
    (val table-name (! (checked-identifier table.name)))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}{}" (! (checked-identifier column.name)) (get SQLITE-TYPES column.type)
                                    (if column.nullable "" " NOT NULL"))))
    (when table.primary-key
      (.append definitions (.format "PRIMARY KEY ({})" (.join ", " (! (checked-identifiers table.primary-key))))))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({})" table-name (.join ", " definitions)))
    (for [index table.indexes]
      (.append statements (.format "CREATE {}INDEX IF NOT EXISTS {} ON {} ({})" (if index.unique "UNIQUE " "") (! (checked-identifier index.name))
                                   table-name (.join ", " (! (checked-identifiers index.columns)))))))
  (tuple statements))


(defk sqlite-run [connection text values]
  {:pre [(: connection sqlite3.Connection) (: text str) (: values (of tuple SqlValue ...))] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To run one rewritten statement and make it an answer value (the rowcount of a row-returning statement is the number of returned rows)."
  (try
    (val cursor (.execute connection text values))
    (if (is cursor.description None)
        (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
        (do (val rows (! (normalized-rows (.fetchall cursor))))
            (SqlRows :rows rows :rowcount (len rows))))
    (except [error #(sqlite3.Error #* BIND-ERRORS)]
      (<- failure (sqlite-failure (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
      failure)))


(defk sqlite-query [connection request]
  {:pre [(: connection sqlite3.Connection) (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlQuery."
  (<- bound (sqlite-statement request.statement request.params))
  (<- answer (sqlite-run connection bound.text bound.values))
  answer)


(defk sqlite-insert [connection request]
  {:pre [(: connection sqlite3.Connection) (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlInsertRows with executemany."
  (<- (checked-rows request.columns request.rows))
  (val table (! (checked-identifier request.table)))
  (val names (! (checked-identifiers request.columns)))
  (val text (.format "INSERT INTO {} ({}) VALUES ({})" table (.join ", " names) (.join ", " (* ["?"] (len names)))))
  (try
    (val cursor (.executemany connection text (list request.rows)))
    (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
    (except [error #(sqlite3.Error #* BIND-ERRORS)]
      (<- failure (sqlite-failure (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
      failure)))


(defk sqlite-ensure-tables [connection tables]
  {:pre [(: connection sqlite3.Connection) (: tables (of tuple SqlTable ...))] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To render table declarations as DDL and run them in order (stopping at the first failure)."
  (<- statements (sqlite-schema-statements tables))
  (for [statement statements]
    (<- answer (sqlite-run connection statement #()))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


(defk sqlite-control [connection statement]
  {:pre [(: connection sqlite3.Connection) (: statement str)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "To run the statements that delimit a transaction (BEGIN IMMEDIATE, COMMIT, ROLLBACK) (success is None)."
  (<- answer (sqlite-run connection statement #()))
  (if (isinstance answer SqlRows) None answer))


(defk sqlite-flush [connection flush]
  {:pre [(: connection sqlite3.Connection) (: flush TransactionFlush) (not flush.notices)] :post [(: % (| tuple SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one round trip of a transaction (TransactionFlush) with the same answer as the PostgreSQL answerer (header comment -- sqlite has no round trip, so statements are flushed in order):
   opening = BEGIN IMMEDIATE, requests in order, closing = COMMIT. Answer = a tuple of SqlRows in the same order as requests | the first failure (later ones are not run).
   Signals are not accepted (run-in-transaction refuses SqlNotify, so notices is empty)."
  (when flush.opening
    (<- began (sqlite-control connection "BEGIN IMMEDIATE"))
    (when (is-not began None)
      (return began)))
  (var answers #())
  (for [request flush.requests]
    (<- answer (match request
                 (SqlQuery) (sqlite-query connection request)
                 (SqlInsertRows) (sqlite-insert connection request)))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer))
    (:= answers (+ answers #(answer))))
  (when flush.closing
    (<- committed (sqlite-control connection "COMMIT"))
    (when (is-not committed None)
      (return committed)))
  answers)


(defk sqlite-connection [target uri]
  {:pre [(: target str) (: uri bool)] :post [(: % sqlite3.Connection)]
   :tags {:context "sql" :role "foundation"}}
  "To open the answerer's connection (the memory-DB and file-DB answerers use the same settings): autocommit (transactions are explicit with BEGIN IMMEDIATE),
   usable from outside the scheduler's thread, reading declared types (BOOLEAN read-back). target = `:memory:` or a URI (uri = True)."
  (sqlite3.connect target :check-same-thread False :isolation-level None :detect-types sqlite3.PARSE-DECLTYPES :uri uri))


(defk with-connection [connections database]
  {:pre [(: connections (of tuple SqliteConnection ...)) (: database str)] :post [(: % (of tuple SqliteConnection ...))]
   :tags {:context "sql" :role "foundation"}}
  "To make sure the database's memory-DB connection is in the store, and if not, open it and return the store with it added. The session value is shared by all
   of the same answerers (the key is decided by the module and the answerer's name), so one connection is held per database name, not per answerer."
  (if (any (gfor c connections (= c.name database)))
      connections
      (+ connections #((SqliteConnection :name database :connection (! (sqlite-connection ":memory:" False)))))))


(defk connection-of [connections database]
  {:pre [(: connections (of tuple SqliteConnection ...)) (: database str)] :post [(: % sqlite3.Connection)]
   :tags {:context "sql" :role "foundation"}}
  "To look up the database's connection from the store (called after with-connection)."
  (next (gfor c connections :if (= c.name database) c.connection)))


(defk outage-marked [unreachable database down]
  {:pre [(: unreachable (of tuple str ...)) (: database str) (: down bool)] :post [(: % (of tuple str ...))]
   :tags {:context "sql" :role "foundation"}}
  "To map one SetSqlOutage to the list of unreachable database names (down = True adds, False removes)."
  (if down
      (tuple (sorted (| (set unreachable) #{database})))
      (tuple (gfor name unreachable :if (!= name database) name))))


(defk outage-of [unreachable database]
  {:pre [(: unreachable (of tuple str ...)) (: database str)] :post [(: % (| SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "To make the answer (SqlUnreachable) to an effect for a database marked unreachable. None if not marked."
  (if (in database unreachable) (SqlUnreachable :reason (.format "simulated unreachable: {}" database)) None))


;; The following four are the path that answers one effect with one connection (the memory-DB and file-DB answerers go through the same path). A database marked unreachable is
;; SqlUnreachable. They are split per effect so that the answer type is carried as the effect's answer type.

(defk sqlite-answer-query [connection unreachable request]
  {:pre [(: connection sqlite3.Connection) (: unreachable (of tuple str ...)) (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlQuery (after checking the unreachable mark)."
  (<- down (outage-of unreachable request.database))
  (when (is-not down None)
    (return down))
  (<- answer (sqlite-query connection request))
  answer)


(defk sqlite-answer-insert [connection unreachable request]
  {:pre [(: connection sqlite3.Connection) (: unreachable (of tuple str ...)) (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlInsertRows (after checking the unreachable mark)."
  (<- down (outage-of unreachable request.database))
  (when (is-not down None)
    (return down))
  (<- answer (sqlite-insert connection request))
  answer)


(defk sqlite-answer-tables [connection unreachable database tables]
  {:pre [(: connection sqlite3.Connection) (: unreachable (of tuple str ...)) (: database str) (: tables (of tuple SqlTable ...))]
   :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlEnsureTables (after checking the unreachable mark)."
  (<- down (outage-of unreachable database))
  (when (is-not down None)
    (return down))
  (<- answer (sqlite-ensure-tables connection tables))
  answer)


(defk sqlite-answer-transaction [connection unreachable database program batched]
  {:tp [A]
   :pre [(: connection sqlite3.Connection) (: unreachable (of tuple str ...)) (: database str) (: program (of Program A object)) (: batched bool)]
   :post [(: % (| A SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To answer one SqlTransaction with BEGIN IMMEDIATE ... COMMIT / ROLLBACK on one connection (after checking the unreachable mark; procedure in sql_transaction.hy --
   by default one statement at a time; a transaction that chose batched flushes one round trip in order with sqlite-flush)."
  (<- down (outage-of unreachable database))
  (when (is-not down None)
    (return down))
  (<- answer (if batched
                 (run-in-batched-transaction database program
                                             (fn [flush] (sqlite-flush connection flush))
                                             (fn [] (sqlite-control connection "ROLLBACK")))
                 (run-in-transaction database program
                                     (fn [request] (sqlite-query connection request))
                                     (fn [request] (sqlite-insert connection request))
                                     (fn [] (sqlite-control connection "BEGIN IMMEDIATE"))
                                     (fn [] (sqlite-control connection "COMMIT"))
                                     (fn [] (sqlite-control connection "ROLLBACK")))))
  answer)


(defhandler sqlite-sql-handler [#^ (get tuple #(str ...)) databases]
  ;; Why it stays an argument: it sorts into the same set as the PostgreSQL / ClickHouse answerers by the database names it answers (Ask cannot distinguish within the set).
  "The I/O-free SQL answerer (header comment). databases = a tuple of the database names it answers (effects for other names are passed to the outside)."
  {:tags {:context "sql" :role "foundation"}}
  (session var connections #())
  (session var unreachable #())
  ;; Rewrite the connection store only when it was opened -- with-connection returns the same tuple if the connection exists, so a write each time (a Put of the session value) is unneeded.
  (SetSqlOutage [database down] :when (in database databases)
    (<- marked (outage-marked unreachable database down))
    (:= unreachable marked)
    (resume None))
  (SqlQuery [database statement params] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (sqlite-answer-query connection unreachable (SqlQuery database statement params)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (sqlite-answer-insert connection unreachable (SqlInsertRows database table columns rows)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (sqlite-answer-tables connection unreachable database tables))
    (resume answer))
  (SqlTransaction [database program lock-key batched] :when (in database databases)
    (<- opened (with-connection connections database))
    (when (is-not opened connections) (:= connections opened))
    (<- connection (connection-of connections database))
    (<- answer (sqlite-answer-transaction connection unreachable database program batched))
    (resume answer))
  ;; A batch bundles the round trips inside a transaction -- inside a transaction the scope of SqlTransaction answers, and only batches issued outside come here.
  (SqlBatch [database queries commit] :when (in database databases)
    (<- refusal (stray-batch database))
    (raise refusal)))
