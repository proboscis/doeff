;;; sqlite-file-sql-handler, a sqlite answerer for the generic SQL effects (sql_effects.hy) that opens DB files (
;;; the body of the custody moves to doeff-cluster. It opens the file DB that the current body (Haskell StoreSqlite.hs) holds as it is and reads and writes it).
;;; It knows no business: the caller declares tables with SqlEnsureTables, or they already exist in the file.
;;;   - The assembling side (composition root) opens connections with open-sqlite-files (existing files, mode=rw), open-or-create-sqlite-files (creating missing files and parent dirs, mode=rwc),
;;;     or open-sqlite-readers (read-only WAL connections with PRAGMA query_only = ON and check_same_thread=True) from a dict of database name to file path
;;;     (SqliteFiles), passes them to the answerer, and closes them with close-sqlite-files when stopping. Effects after closing are SqlUnreachable.
;;;   - open-sqlite-files only opens an existing file: it opens with URI mode=rw, and if the file does not exist it does not create it and raises sqlite3.OperationalError at the opening (it does not recreate and
;;;     does not copy -- silently creating an empty DB would let the custody start running on an empty ledger).
;;;   - Same as the body, WAL (`PRAGMA journal_mode=WAL` -- refused at the opening if the answer is not wal), one connection per database, and writes serialized
;;;     (flushed in order on the scheduler's thread).
;;;   - It answers by the same path as the memory-DB answerer (sqlite_sql.hy) (sqlite-answer-query, -insert, -tables, -transaction, -read-transaction): rewriting `:name` arguments; failures are SqlFailed (the SQLSTATE class
;;;     table) / SqlUnreachable values (exceptions do not escape to the caller); BOOLEAN read-back; SqlTransaction is BEGIN IMMEDIATE ... COMMIT / ROLLBACK
;;;     (a SqlBatch inside is flushed in order -- sqlite-flush; a SqlBatch outside is refused by name); SqlReadTransaction is BEGIN DEFERRED ... COMMIT / ROLLBACK under PRAGMA query_only = ON
;;;     (using the optional readers SqliteFiles when supplied, so reads use a separate connection from writes).
;;;     It does not read the clock inside a statement (the caller passes the time as an argument). The lock-key of SqlTransaction is not used (serialized on one connection).
;;;   - For Compute pool threads (sqlite3 connections do not cross threads), sqlite-file-reader-handler takes a dict of database name to path and opens a query_only WAL connection on the calling thread
;;;     per call and closes it on exit; alternatively open-sqlite-readers / close-sqlite-files may be called inside the Compute program itself.
;;;   - SetSqlOutage can make a database unreachable, and during that time everything is SqlUnreachable (the unreachable mark is a session value -- the store state is needed on the outside).
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import sqlite3)
(import collections.abc [Callable])
(import pathlib [Path])
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlReadTransaction SqlEnsureTables SetSqlOutage
                                        SqlUnreachable])
(import doeff_core_effects._sql_transaction [stray-batch])
(import doeff_core_effects._sqlite_sql [SqliteConnection sqlite-connection connection-of outage-marked
                                       sqlite-answer-query sqlite-answer-insert sqlite-answer-tables sqlite-answer-transaction
                                       sqlite-answer-read-transaction])

;; The answer required of the journal_mode of an opened connection (same as StoreSqlite.hs of the body).
(val JOURNAL-MODE "wal")


(defrecord SqliteFile
  "Declaration of a database it answers: a name (the effect's database) and the path of an existing DB file."
  (#^ str name)
  (#^ str path))


(defrecord SqliteFiles
  "Connections of the opened file DBs (one per database). Made by open-sqlite-files, open-or-create-sqlite-files, or open-sqlite-readers and closed by close-sqlite-files."
  (#^ (get tuple #(SqliteConnection ...)) connections))


(defk sqlite-open-file [file mode create-parents read-only check-same-thread]
  {:pre [(: file SqliteFile) (: mode str) (: create-parents bool) (: read-only bool) (: check-same-thread bool)]
   :post [(: % SqliteConnection)]
   :tags {:context "sql" :role "foundation"}}
  "Open one DB file with URI query mode, verify WAL journal_mode, and optionally set query_only = ON."
  (val target (.resolve (Path file.path)))
  (when create-parents
    (.mkdir target.parent :parents True :exist-ok True))
  (val uri (.format "{}?mode={}" (.as-uri target) mode))
  (val connection (! (sqlite-connection uri True :check-same-thread check-same-thread)))
  (try
    (val journal-pragma (if read-only "PRAGMA journal_mode" "PRAGMA journal_mode=WAL"))
    (val mode-answer (get (.fetchone (.execute connection journal-pragma)) 0))
    (when (!= mode-answer JOURNAL-MODE)
      (raise (sqlite3.OperationalError (.format "cannot open {} in WAL (journal_mode = {})" file.path mode-answer))))
    (when read-only
      (.execute connection "PRAGMA query_only = ON"))
    (except [Exception]
      (.close connection)
      (raise)))
  (SqliteConnection :name file.name :connection connection))


(defk sqlite-file-connection [file]
  {:pre [(: file SqliteFile)] :post [(: % SqliteConnection)]
   :tags {:context "sql" :role "foundation"}}
  "To open one existing DB file in WAL (header comment). If the file does not exist or does not become WAL, an exception at the opening."
  (<- opened (sqlite-open-file file "rw" False False False))
  opened)


(defk sqlite-reader-connection [file]
  {:pre [(: file SqliteFile)] :post [(: % SqliteConnection)]
   :tags {:context "sql" :role "foundation"}}
  "Open one existing WAL DB file read-only (mode=ro, PRAGMA query_only = ON, check_same_thread=True)."
  (<- opened (sqlite-open-file file "ro" False True True))
  opened)


(defk open-sqlite-connections [files opener]
  {:pre [(: files (of dict str str))
         (all (gfor [name path] (.items files) (and (isinstance name str) (isinstance path str))))
         (: opener Callable)]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "Open one connection per database using opener; if one fails midway, close those already opened and re-raise."
  (val declared (tuple (gfor [name path] (.items files) (SqliteFile :name name :path path))))
  (var opened #())
  (try
    (for [f declared]
      (<- connection (opener f))
      (:= opened (+ opened #(connection))))
    (except [Exception]
      (for [c opened]
        (.close c.connection))
      (raise)))
  (SqliteFiles :connections opened))


(defk open-sqlite-files [files]
  {:pre [(: files (of dict str str)) (all (gfor [name path] (.items files) (and (isinstance name str) (isinstance path str))))]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "To open connections from the databases to answer -- a dict of name (the effect's database) to the path of an existing DB file. If one cannot be opened
   midway, close those already opened and then let the exception through."
  (<- opened (open-sqlite-connections files sqlite-file-connection))
  opened)


(defk open-or-create-sqlite-files [files]
  {:pre [(: files (of dict str str)) (all (gfor [name path] (.items files) (and (isinstance name str) (isinstance path str))))]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "Open connections to DB files in WAL, creating parent directories and missing files (mode=rwc). If one fails midway, close those already opened and re-raise."
  (<- opened (open-sqlite-connections files (fn [f] (sqlite-open-file f "rwc" True False False))))
  opened)


(defk open-sqlite-readers [files]
  {:pre [(: files (of dict str str)) (all (gfor [name path] (.items files) (and (isinstance name str) (isinstance path str))))]
   :post [(: % SqliteFiles)]
   :tags {:context "sql" :role "foundation"}}
  "Open read-only WAL connections (mode=ro, PRAGMA query_only = ON, check_same_thread=True). Refuses missing or non-WAL files without creating or mutating them."
  (<- opened (open-sqlite-connections files sqlite-reader-connection))
  opened)


(defk close-sqlite-files [files]
  {:pre [(: files SqliteFiles)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "To close all opened connections (the assembling side calls it when stopping). Effects after closing answer SqlUnreachable."
  (for [c files.connections]
    (.close c.connection))
  None)


(defn #^ bool answers-database [#^ (| SqliteFiles None) files #^ str database]  ; cannot be a defk: guard predicate in defhandler :when
  "Whether files holds a connection for database."
  (and (is-not files None) (any (gfor c files.connections (= c.name database)))))


(defk with-reader-connection [database path body]
  {:tp [A]
   :pre [(: database str) (: path str) (: body (get Callable #([sqlite3.Connection] (get Program #(A object)))))]
   :post [(: % (| A SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "Open one read-only WAL connection on the calling thread, run (body connection), and close it on exit. Opening OperationalError becomes SqlUnreachable."
  (var opened None)
  (try
    (<- conn (sqlite-reader-connection (SqliteFile :name database :path path)))
    (:= opened conn)
    (except [error sqlite3.OperationalError]
      (return (SqlUnreachable :reason (str error)))))
  (try
    (<- answer (body opened.connection))
    answer
    (finally
      (.close opened.connection))))


(defhandler sqlite-file-sql-handler [#^ SqliteFiles files #^ (| SqliteFiles None) [readers None]]
  ;; Why it stays an argument: the opened connections are a resource held by the assembling side and closed outside the answerer (Ask cannot point to the same thing as the closing side).
  "The SQL answerer for DB files (header comment). Optional readers = separate query_only SqliteFiles used for SqlReadTransaction.
   Effects for databases whose names are not in files (or readers for SqlReadTransaction) are passed to the outside."
  {:tags {:context "sql" :role "foundation"}}
  (session var unreachable #())
  (SetSqlOutage [database down] :when (or (answers-database files database) (answers-database readers database))
    (<- marked (outage-marked unreachable database down))
    (:= unreachable marked)
    (resume None))
  (SqlQuery [database statement params] :when (answers-database files database)
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-query connection unreachable (SqlQuery database statement params)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (answers-database files database)
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-insert connection unreachable (SqlInsertRows database table columns rows)))
    (resume answer))
  (SqlEnsureTables [database tables] :when (answers-database files database)
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-tables connection unreachable database tables))
    (resume answer))
  (SqlTransaction [database program lock-key batched] :when (answers-database files database)
    (<- connection (connection-of files.connections database))
    (<- answer (sqlite-answer-transaction connection unreachable database program batched))
    (resume answer))
  (SqlReadTransaction [database program] :when (or (answers-database readers database) (answers-database files database))
    (val source (if (answers-database readers database) readers files))
    (<- connection (connection-of source.connections database))
    (<- answer (sqlite-answer-read-transaction connection unreachable database program))
    (resume answer))
  ;; A batch bundles the round trips inside a transaction -- inside a transaction the scope of SqlTransaction answers, and only batches issued outside come here.
  (SqlBatch [database queries commit] :when (answers-database files database)
    (<- refusal (stray-batch database))
    (raise refusal)))


(defhandler sqlite-file-reader-handler [#^ (get dict #(str str)) files]
  "Stateless thread-safe read-only SQLite answerer for SqlReadTransaction and SqlQuery (e.g. inside Compute pool threads).
   Opens a query_only WAL connection on the calling thread for each call and closes it on exit so no sqlite3.Connection crosses threads."
  {:tags {:context "sql" :role "foundation"}}
  (SqlReadTransaction [database program] :when (in database files)
    (<- answer (with-reader-connection database (get files database)
                 (fn [connection] (sqlite-answer-read-transaction connection #() database program))))
    (resume answer))
  (SqlQuery [database statement params] :when (in database files)
    (<- answer (with-reader-connection database (get files database)
                 (fn [connection] (sqlite-answer-query connection #() (SqlQuery database statement params)))))
    (resume answer)))
