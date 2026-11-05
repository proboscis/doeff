;;; sqlite-file-sql-handler, a sqlite answerer for the generic SQL effects (sql_effects.hy) that opens an existing DB file (
;;; the body of the custody moves to doeff-cluster. It opens the file DB that the current body (Haskell StoreSqlite.hs) holds as it is and reads and writes it).
;;; It knows no business: the caller declares tables with SqlEnsureTables, or they already exist in the file.
;;;   - The assembling side (composition root) opens connections with open-sqlite-files from a tuple of database declarations (SqliteFile -- name and file path)
;;;     (SqliteFiles), passes them to the answerer, and closes them with close-sqlite-files when stopping. Effects after closing are SqlUnreachable.
;;;   - It only opens an existing file: it opens with URI mode=rw, and if the file does not exist it does not create it and raises sqlite3.OperationalError at the opening (it does not recreate and
;;;     does not copy -- silently creating an empty DB would let the custody start running on an empty ledger).
;;;   - Same as the body, WAL (`PRAGMA journal_mode=WAL` -- refused at the opening if the answer is not wal), one connection per database, and writes serialized
;;;     (flushed in order on the scheduler's thread).
;;;   - It answers by the same path as the memory-DB answerer (sqlite_sql.hy) (sqlite-answer-query, -insert, -tables, -transaction): rewriting `:name` arguments; failures are SqlFailed (the SQLSTATE class
;;;     table) / SqlUnreachable values (exceptions do not escape to the caller); BOOLEAN read-back; SqlTransaction is BEGIN IMMEDIATE ... COMMIT / ROLLBACK
;;;     (a SqlBatch inside is flushed in order -- sqlite-flush; a SqlBatch outside is refused by name).
;;;     It does not read the clock inside a statement (the caller passes the time as an argument). The lock-key of SqlTransaction is not used (serialized on one connection).
;;;   - SetSqlOutage can make a database unreachable, and during that time everything is SqlUnreachable (the unreachable mark is a session value -- the store state is needed on the outside).
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import sqlite3)
(import pathlib [Path])
(import dataclasses [dataclass])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SetSqlOutage])
(import doeff_core_effects._sql_transaction [stray-batch])
(import doeff_core_effects._sqlite_sql [SqliteConnection sqlite-connection connection-of outage-marked
                                       sqlite-answer-query sqlite-answer-insert sqlite-answer-tables sqlite-answer-transaction])

;; The answer required of the journal_mode of an opened connection (same as StoreSqlite.hs of the body).
(val JOURNAL-MODE "wal")


(defrecord SqliteFile
  "Declaration of a database it answers: a name (the effect's database) and the path of an existing DB file."
  (#^ str name)
  (#^ str path))


(defrecord SqliteFiles
  "Connections of the opened file DBs (one per database). Made by open-sqlite-files and closed by close-sqlite-files."
  (#^ (get tuple #(SqliteConnection ...)) connections))


(defk sqlite-file-connection [file]
  {:pre [(: file SqliteFile)] :post [(: % SqliteConnection)]
   :tags {:context "sql" :role "foundation"}}
  "To open one existing DB file in WAL (header comment). If the file does not exist or does not become WAL, an exception at the opening."
  (val connection (! (sqlite-connection (+ (.as-uri (.resolve (Path file.path))) "?mode=rw") True)))
  (val mode (get (.fetchone (.execute connection "PRAGMA journal_mode=WAL")) 0))
  (when (!= mode JOURNAL-MODE)
    (.close connection)
    (raise (sqlite3.OperationalError (.format "cannot open {} in WAL (journal_mode = {})" file.path mode))))
  (SqliteConnection :name file.name :connection connection))


(defk open-sqlite-files [files]
  {:pre [(: files (of tuple SqliteFile ...)) (all (gfor f files (isinstance f SqliteFile)))
         (= (len files) (len (set (gfor f files f.name))))]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "To open connections from a list of database declarations (names must not repeat). If one cannot be opened midway, close those already opened and then let the exception through."
  (var opened #())
  (try
    (for [f files]
      (<- connection (sqlite-file-connection f))
      (:= opened (+ opened #(connection))))
    (except [Exception]
      (for [c opened]
        (.close c.connection))
      (raise)))
  (SqliteFiles :connections opened))


(defk close-sqlite-files [files]
  {:pre [(: files SqliteFiles)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "To close all opened connections (the assembling side calls it when stopping). Effects after closing answer SqlUnreachable."
  (for [c files.connections]
    (.close c.connection))
  None)


(defhandler sqlite-file-sql-handler [#^ SqliteFiles files]
  ;; Why it stays an argument: the opened connections are a resource held by the assembling side and closed outside the answerer (Ask cannot point to the same thing as the closing side).
  "The SQL answerer for existing DB files (header comment). Effects for databases whose names are not in files are passed to the outside."
  {:tags {:context "sql" :role "foundation"}}
  (session var unreachable #())
  (SetSqlOutage [database down] :when (any (gfor c files.connections (= c.name database)))
    (<- marked (outage-marked unreachable database down))
    (:= unreachable marked)
    (resume None))
  (SqlQuery [database statement params] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-query connection unreachable (SqlQuery database statement params)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-insert connection unreachable (SqlInsertRows database table columns rows)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-tables connection unreachable database tables))
    (resume answer))
  (SqlTransaction [database program lock-key batched] :when (any (gfor c files.connections (= c.name database)))
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-transaction connection unreachable database program batched))
    (resume answer))
  ;; A batch bundles the round trips inside a transaction -- inside a transaction the scope of SqlTransaction answers, and only batches issued outside come here.
  (SqlBatch [database queries commit] :when (any (gfor c files.connections (= c.name database)))
    (<- refusal (stray-batch database))
    (raise refusal)))
