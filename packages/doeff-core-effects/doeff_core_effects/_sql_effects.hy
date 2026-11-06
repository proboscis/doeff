;;; Generic SQL query effects (consumer = the translation of the record service in #783 batch 2). A foundation vocabulary with no business terms,
;;; at the same level as HttpRequest (http_effects.hy) and RunProcess (process_effects.hy). The responder is swapped per backend:
;;;   postgres-sql-handler         Real PostgreSQL (psycopg 3, postgres_sql.hy). psycopg is imported only inside that module. Driver I/O runs
;;;                                on one thread per call, so other scheduler tasks keep running during a slow query (#1215)
;;;   pooled-postgres-sql-handler  The same real PostgreSQL, but waits for connection permits on a scheduler semaphore and runs only the driver I/O on
;;;                                the caller's pool threads (pooled_postgres_sql.hy). The caller limits the number of threads
;;;   clickhouse-http-sql-handler  Real ClickHouse (one HTTP request over http.client = one query, clickhouse_http_sql.hy). Has no transactions.
;;;                                The HTTP round trip runs on one thread per call and does not block the scheduler. Sends a server-side deadline with each request and does not leave before the answer
;;;   sqlite-sql-handler           No I/O - stdlib sqlite3 in-memory DB (sqlite_sql.hy). Used for virtual-clock simulation
;;; A responder answers only the database names it declared; effects for other names are passed to the outer handler (PostgreSQL and ClickHouse responders can be stacked).
;;; Connection declarations (DSN, URL, credentials) are responder arguments, supplied by the composition root. Business code sees only the database name.
;;;
;;; Failures are answered as values (not exceptions - interpreting success/failure and the class is up to the caller):
;;;   SqlFailed(sqlstate, reason)  A failure the engine answered (5-character SQLSTATE. If the driver has no SQLSTATE, the class code - 22000, 23000 ...;
;;;                                None for ClickHouse, which has no SQLSTATE)
;;;   SqlUnreachable(reason)       The engine cannot be reached (cannot connect, disconnected, HTTP did not arrive)
;;; Caller errors (mismatched parameter names, values outside the closed set, forbidden effects inside a transaction) are exceptions (ValueError, TypeError, SqlTransactionMisuse).
;;;
;;; How to write statements (a convention shared by the 3 dialects):
;;;   - A parameter uses the neutral notation `:name` (inside string literals, quoted names, and comments it is not a parameter; `::` is a type cast, not a parameter). The responder
;;;     rewrites it for the dialect: PostgreSQL = `%(name)s` (`%` in the statement becomes `%%`), sqlite = `?`, ClickHouse = `{name:Type}` (the type is decided from the value's Python type
;;;     by the closed table CLICKHOUSE-TYPES). The names in params and in the statement must match exactly, with nothing missing or extra (a mismatch is a ValueError).
;;;   - Do not read the clock inside a statement (do not write now(), current_timestamp, today()). The caller reads the time with an effect such as GetTime and passes it as a parameter -
;;;     so that in a simulation with swapped responders the virtual clock and the DB clock do not diverge.
;;;   - Parameter and row values are the closed set SqlValue = int | float | str | bytes | bool | None. The responder normalizes driver values into this set
;;;     (Decimal -> int if integral, otherwise float / datetime -> ISO 8601 str / UUID -> str / json -> text). A value outside the set is a TypeError.
;;;
;;;   SqlQuery         One statement. Answer = SqlRows | SqlFailed | SqlUnreachable. binary (bool, default False) = a flag to receive the answer rows in the driver's binary form
;;;                    (PostgreSQL = receive the result in binary form so that bytea does not become hex text [twice the length of the body] - for reading large bodies).
;;;                    The rules for answer values are the same. Columns are limited to bytea, text, numbers, booleans, and datetimes (for other types the binary-form copy may differ from the text form -
;;;                    json is read as an object and is rejected with the copy's TypeError). Responders with no distinction of form (sqlite, ClickHouse) ignore it
;;;   SqlInsertRows    Insert rows into a table in bulk (PostgreSQL / sqlite = executemany, ClickHouse = one JSONCompactEachRow body). The answer is the same
;;;   SqlTransaction   Run a program inside one transaction. Answer = the program's answer | SqlFailed | SqlUnreachable. Guarantees:
;;;                      (1) If the first SqlQuery / SqlInsertRows (/ SqlBatch) inside fails, roll back and make that failure the answer without resuming the program
;;;                      (2) Inside, only SqlQuery and SqlInsertRows to the same database (SqlBatch too if batched) and pure computation can be issued. Other effects
;;;                          (nested SqlTransaction, another database, scheduler, state) are rejected by the handler the responder wraps around, which rolls back and
;;;                          raises SqlTransactionMisuse
;;;                      (3) If the program raises an exception, roll back and propagate the exception
;;;                    lock-key (str | None) = serializes transactions with the same key (PostgreSQL = pg_advisory_xact_lock(hashtext(key)),
;;;                    sqlite = not needed since one connection means transactions are never interrupted). The ClickHouse responder rejects it with SqlFailed(0A000).
;;;                    locks (tuple of SqlLock, default empty) = locks taken in order after lock-key (shared = a shared lock - shared holders can coexist,
;;;                    waiting only on exclusive locks with the same key. PostgreSQL = pg_advisory_xact_lock_shared(hashtext(key))). Used so that writes holding the whole-store lock shared and
;;;                    a part-of-store lock exclusive run side by side per part, while waiting on maintenance that holds the whole-store lock exclusive.
;;;                    The caller decides the lock order (transactions taking the same set of keys must order them the same way - the reverse order deadlocks). The responder treats them
;;;                    like lock-key (not needed for sqlite, rejected by ClickHouse).
;;;                    batched (bool, default False) = a declaration of how statements are sent (steps are in the header comment of sql_transaction.hy):
;;;                      False  Send one statement per round trip - BEGIN -> locks -> statements in the order issued -> SqlNotify on the spot -> COMMIT / ROLLBACK (even a program
;;;                             that issues no SQL sends BEGIN, locks, and COMMIT)
;;;                      True   Combine round trips - BEGIN and locks share the round trip of the first statement, SqlNotify is remembered and shares the round trip of COMMIT,
;;;                             a commit bundle (SqlBatch with commit = True) shares the round trip of COMMIT, and a program issuing no SQL at all takes 0 round trips
;;;   SqlBatch         Inside the program of a SqlTransaction with batched = True, sends the statement sequence queries in a single round trip (PostgreSQL = one
;;;                    pipeline, sqlite = in order). Answer = a tuple of SqlRows in the same order as queries (if any fails, guarantee (1) applies - the program is not
;;;                    resumed). If commit = True, COMMIT is sent at the end of the same round trip to end the transaction (issuing a SQL effect after that is
;;;                    rejected under guarantee (2)). Issuing it inside a non-batched transaction or outside a transaction is rejected by name with SqlTransactionMisuse.
;;;                    The ClickHouse responder does not answer it.
;;;   SqlEnsureTables  Renders the declarations of tables, columns, and indexes (SqlTable) as the dialect's DDL and sends them (does nothing if they exist). Answer = SqlSchemaApplied | SqlFailed |
;;;                    SqlUnreachable. PostgreSQL's DO $$ and CONCURRENTLY and ClickHouse's engine details are not part of the declaration (they cannot be a common statement)
;;;
;;;   SqlNotify        Emits a signal on the notification channel for the relevant names (topics - None = a signal whose names are unknown = relevant to all) (inside a transaction it
;;;                    is delivered only on commit) - PostgreSQL's pg_notify. Can be issued inside a SqlTransaction (the exception to guarantee (2) - if batched it is remembered and
;;;                    sent in the same round trip as COMMIT). After returning the connection, the responder rings in place the bells of the same process whose names overlap
;;;   SqlHangNotice    Hangs a bell (an external promise) on the notification channel for the names to wait for (topics - None = all signals) - PostgreSQL's LISTEN.
;;;                    Answer = bell | SqlUnreachable. SqlDropNotice = removes a bell that did not ring. Only the 2 PostgreSQL responders answer all three
;;;
;;; Vocabulary for the I/O-free store (not in real backends): SetSqlOutage = make that database unreachable / restore it (only sqlite-sql-handler answers - the hook by which a simulation scenario
;;; triggers the unreachable branch).
(require doeff-hy.macros [defk deff defeffect <- val var])
(require doeff-hy.record [defrecord defenum])
(import re)
(import datetime)
(import decimal [Decimal])
(import uuid [UUID])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import collections.abc [Sequence])
(import itertools [groupby])
(import typing [TypeGuard TypeVar])
(import doeff [Program])
(import doeff_core_effects._scheduler [ExternalPromise])

(val MODULE-TAGS {:context "sql" :role "foundation"})

;; The type of a SqlTransaction answer, i.e. the program's answer (differs per program). A TypeVar read by the runtime field and the answer type; for type checking, the expansion of
;; SqlTransaction's :tp [T] sets the class's type parameter T (#2939).
(val T (TypeVar "T"))

;; Constants that carry a type annotation `#^ type name` (#2939): values whose type is not determined by the expression (empty dict, result of compiling a regex, tuple of types). Without the annotation, the constants in the type declaration
;; (.pyi) become Incomplete, and the types of expressions on the using side become "partly unknown". The runtime value is the value of the expression regardless of the annotation.
;; The closed set of parameter and row values (bool is a subtype of int, so it is listed first). The annotation of the tuple of types is `tuple[type, ...]` - a tuple passed to isinstance at runtime,
;; and its contents are not fixed statically (fixing them would make the isinstance check against values of the closed set "always true" and red).
(val #^ (get tuple #(type ...)) SQL-VALUE-TYPES #(bool int float str bytes (type None)))
;; A marker for types that are exactly a type of the closed set (excluding subclasses) - used by normalized-rows to decide to skip the call (subclasses such as enum values go to
;; the match in normalized-value).
(val #^ (get frozenset type) PLAIN-VALUE-TYPES (frozenset SQL-VALUE-TYPES))

;; An alias of the same closed-set type (the name callers use in annotations - the same as SqlValue in the type declaration sql_effects.pyi).
(val SqlValue (| int float str bytes bool None))
;; Among values the driver answers, the types with a rule for mapping into the closed set SqlValue (the types normalized-value accepts - the same as DriverValue in the type declaration sql_effects.pyi).
;; datetime.datetime is a subtype of datetime.date. Values of types outside this are rejected by normalized-rows with a TypeError.
(val DriverValue (| int float str bytes bool None memoryview bytearray Decimal datetime.date datetime.time UUID))

;; Tokens of the neutral notation: string literals, quoted names, comments, $$ bodies, and `::` are statement text as is; `:name` is a parameter; anything else is statement text one character at a time.
(val #^ (get re.Pattern str) TOKEN
  (re.compile r"(?P<text>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/|\$\$.*?\$\$|::)|:(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<plain>[^'\"\-/:$]+|.)"
              re.DOTALL))

;; Names of tables, columns, and indexes (names rendered in DDL are not quoted, so only this form is accepted).
(val #^ (get re.Pattern str) IDENTIFIER (re.compile r"[A-Za-z_][A-Za-z0-9_]*"))


;; --- Values ---------------------------------------------------------------------------------------------------------------------

(defrecord SqlLock
  "One lock of a SqlTransaction (locks in the header comment): key = the lock key, shared = whether it is a shared lock (default False = exclusive)."
  (#^ str key)
  (setv #^ bool shared False))


(defrecord SqlParam
  "One statement parameter (name = the `:name` name in the statement, value = a value of the closed set SqlValue)."
  (#^ str name)
  (#^ SqlValue value))


(defrecord SqlRows
  "A query answer. rows = tuple of rows (a row = tuple of column values, already normalized to SqlValue), rowcount = number of rows the engine counted
   (for row-returning statements the number of rows returned, for writing statements the number of rows changed. ClickHouse HTTP queries do not count, so None)."
  (#^ (get tuple #((get tuple #(SqlValue ...)) ...)) rows)
  (#^ (| int None) rowcount))


(defrecord SqlFailed
  "A failure the engine answered. sqlstate = SQLSTATE (5 characters - the class code if the driver has no SQLSTATE, None for ClickHouse), reason = the engine's message."
  (#^ (| str None) sqlstate)
  (#^ str reason))


(defrecord SqlUnreachable
  "The engine cannot be reached (cannot connect, disconnected, HTTP did not arrive). reason = the driver's message."
  (#^ str reason))


(defrecord SqlSchemaApplied
  "The answer of SqlEnsureTables. statements = the DDL statements sent (after rendering in the dialect, in the order sent)."
  (#^ (get tuple #(str ...)) statements))


(defclass SqlTransactionMisuse [TypeError]
  "A forbidden effect was issued inside a SqlTransaction (guarantee (2) - header comment). The transaction has been rolled back.")


;; --- DDL declarations ------------------------------------------------------------------------------------------------------------

(defenum SqlColumnType INTEGER FLOAT TEXT BYTES BOOLEAN JSON)
(setv SqlColumnType.__doc__ "Closed column types (SqlColumn.type): INTEGER = 64-bit integer, FLOAT = floating point, TEXT = string, BYTES = byte string,
   BOOLEAN = boolean, JSON = JSON stored and retrieved as text. The responder renders them as the dialect's types.")


(defrecord SqlColumn
  "One column (type = a closed type - INTEGER is 64-bit, JSON is stored and retrieved as text; nullable = whether NULL is accepted)."
  (#^ str name)
  (#^ SqlColumnType type)
  (setv #^ bool nullable False))


(defrecord SqlIndex
  "One index (columns = sequence of column names, unique = whether unique)."
  (#^ str name)
  (#^ (get tuple #(str ...)) columns)
  (setv #^ bool unique False))


(defrecord SqlTable
  "One table (primary-key = sequence of primary key column names - in ClickHouse the sort key ORDER BY, no primary key if empty)."
  (#^ str name)
  (#^ (get tuple #(SqlColumn ...)) columns)
  (setv #^ (get tuple #(str ...)) primary-key #())
  (setv #^ (get tuple #(SqlIndex ...)) indexes #()))


;; --- effect ----------------------------------------------------------------------------------------------------------------

(defeffect SqlQuery
  "Send one statement (header comment). Do not read the clock inside the statement - pass the time as a parameter."
  {:fields [(: database str) (: statement str) (: params (get tuple #(SqlParam ...)) #()) (: binary bool False)]
   :pre [(: database str) (: statement str) (: params tuple) (all (gfor p params (isinstance p SqlParam))) (: binary bool)]
   :answer (| SqlRows SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlInsertRows
  "Insert rows rows in bulk into columns columns of table table (header comment). Each row of rows is a tuple of SqlValue of the same length as columns (the responder checks)."
  {:fields [(: database str) (: table str) (: columns (get tuple #(str ...)))
            (: rows (get tuple #((get tuple #(SqlValue ...)) ...)))]
   :pre [(: database str) (: table str) (: columns tuple) (: rows tuple) (all (gfor row rows (isinstance row tuple)))]
   :answer (| SqlRows SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlTransaction
  "Run program inside one transaction (the 3 guarantees in the header comment). Answer = the program's answer (after commit) | SqlFailed | SqlUnreachable.
   batched = a declaration of how statements are sent (default False = one statement per round trip, True = combine round trips - header comment). locks = locks taken in order after lock-key
   (shared or exclusive - header comment)."
  {:tp [T]
   :fields [(: database str) (: program (get Program #(T object))) (: lock-key (| str None) None) (: batched bool False)
            (: locks (get tuple #(SqlLock ...)) #())]
   :pre [(: database str) (: program Program) (: lock-key (| str None)) (: batched bool) (: locks tuple)
         (all (gfor lock locks (isinstance lock SqlLock)))]
   :answer (| T SqlFailed SqlUnreachable)
   :runs-carried [program]
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlBatch
  "Inside the program of a SqlTransaction with batched = True, send the statement sequence queries to the same database in a single round trip (header comment). Answer = a tuple of SqlRows in the same order as
   queries. If commit = True, COMMIT is sent at the end of the same round trip (issuing a SQL effect after that is a SqlTransactionMisuse)."
  {:fields [(: database str) (: queries (get tuple #(SqlQuery ...))) (: commit bool False)]
   :pre [(: database str) (: queries tuple) (: commit bool) (all (gfor q queries (and (isinstance q SqlQuery) (= q.database database))))]
   :answer (get tuple #(SqlRows ...))
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlEnsureTables
  "Render the table declarations tables as the dialect's DDL and send them (does nothing if they exist - header comment)."
  {:fields [(: database str) (: tables (get tuple #(SqlTable ...)))]
   :pre [(: database str) (: tables tuple) (all (gfor t tables (isinstance t SqlTable)))]
   :answer (| SqlSchemaApplied SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlNotify
  "Emit a signal on the database's notification channel (PostgreSQL's pg_notify - carries no row contents). topics = sequence of names the signal is relevant to (words chosen by the caller - for a record
   store, the names of the tables and columns a write touched). Only bells (SqlHangNotice) with overlapping names ring. None = a signal whose names are unknown = a signal relevant to all bells
   (so a waiter is never left asleep by a signal that cannot be narrowed - getting a name mapping wrong would create a waiter that never wakes). If issued inside a SqlTransaction it is
   delivered only on commit, and signals of rolled-back transactions are never delivered. After returning the connection the responder rings the bells of the same process in place, and
   NOTIFY reaches other processes (one's own NOTIFY does not ring the bells of the same process again). Answer = None | SqlFailed | SqlUnreachable (only the 2 PostgreSQL
   responders answer)."
  {:fields [(: database str) (: channel str) (: topics (| (get tuple #(str ...)) None))]
   :pre [(: database str) (: channel str) (: topics (| tuple None))]
   :answer (| None SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlHangNotice
  "Hang a bell on the database's notification channel waiting for names topics (PostgreSQL's LISTEN / NOTIFY - only postgres-sql-handler and
   pooled-postgres-sql-handler answer). The external promise in the answer completes with True when, after it is hung, a signal whose names overlap topics (or a signal with unknown names) arrives on the channel,
   or when the listening connection reconnects (notifications in between are not delivered) (distinguishable from the timeout None of doeff-time's WaitWithin).
   topics = None rings on all signals. By the time the answer returns, LISTEN is in place - the caller hangs first and then reads, and if quiet waits on the bell, so a write between the read and
   the hang is not missed. A signal only means \"it may have changed\" and carries no contents (the receiver rereads). To end a wait without a ring,
   remove it with SqlDropNotice."
  {:fields [(: database str) (: channel str) (: topics (| (get tuple #(str ...)) None))]
   :pre [(: database str) (: channel str) (: topics (| tuple None))]
   :answer (| (get ExternalPromise bool) SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlDropNotice
  "Remove the bell bell hung with SqlHangNotice that did not ring (answers without doing anything even if it has already rung)."
  {:fields [(: database str) (: channel str) (: bell (get ExternalPromise bool))]
   :pre [(: database str) (: channel str) (: bell ExternalPromise)]
   :answer None
   :tags {:context "sql" :role "foundation"}})


(defeffect SetSqlOutage
  "The hook of the I/O-free store (only sqlite-sql-handler answers): while down = True, every effect to the database is answered with SqlUnreachable."
  {:fields [(: database str) (: down bool)]
   :answer None
   :tags {:context "sql" :role "foundation"}})


;; --- Neutral notation -------------------------------------------------------------------------------------------------------------

(defrecord SqlText
  "The non-parameter part of a statement (copied as is in dialect rewriting)."
  (#^ str text))


(defrecord SqlPlaceholder
  "One parameter `:name` in a statement."
  (#^ str name))


;; Memo of statement splitting (statement -> split sequence). Splitting is a pure computation determined by the statement alone, and the answer is a tuple of frozen records, so even if the same object is returned many times
;; callers cannot rewrite it. dict get / assignment are atomic one at a time, so even with overlapping threads the same statement is merely split twice and nothing diverges
;; (splitting is about 20% of the roughly 100 us of one SqlQuery).
;; The memo size is bounded by the number of parts (SqlText, SqlPlaceholder). Statements that are code literals have up to a few dozen parts (about 40 for an 18-column INSERT), but
;; statements that line up as many parameters as values (`IN (:k0, :k1, ...)`, an INSERT lining up VALUES per row) become a different statement per number of values and have hundreds of parts each.
;; Bounding only by the number of statements held about 4096 statements x an average of about 120 parts = about 500,000 records and made one GC heavy.
;; So statements whose parts exceed SPLIT-MEMO-STATEMENT-PARTS are not remembered (statements that vary with the number of values - unlikely to repeat), and once the total number of remembered parts
;; reaches SPLIT-MEMO-PART-LIMIT remembering stops (after that every call splits). With overlapping threads the additions to the total can interleave, so the limit can be exceeded by up to one statement per thread
;; (the excess stops at SPLIT-MEMO-STATEMENT-PARTS x the number of threads).
(val SPLIT-MEMO-LIMIT 4096)
(val SPLIT-MEMO-STATEMENT-PARTS 64)
(val SPLIT-MEMO-PART-LIMIT 32768)
(val #^ (get dict #(str (get tuple #((| SqlText SqlPlaceholder) ...)))) SPLIT-MEMO {})
;; Total number of remembered parts (one cell - only split-statement adds to it).
(val #^ (get list int) SPLIT-MEMO-PARTS [0])


(defk split-statement [statement]
  {:pre [(: statement str)] :post [(: % (get tuple #((| SqlText SqlPlaceholder) ...)))]
   :tags {:context "sql" :role "foundation"}}
  "Split a statement in neutral notation into a sequence of statement parts (SqlText) and parameters (SqlPlaceholder) (string literals, quoted names, comments, and `::` are statement parts).
   From the second time for the same statement, returns the remembered sequence (see the note on SPLIT-MEMO)."
  (val remembered (.get SPLIT-MEMO statement))
  (when (is-not remembered None)
    (return remembered))
  ;; Turn the tokens (parameter name | None, spelling) into one SqlText per group of consecutive statement tokens and one SqlPlaceholder per parameter token. Statements that are not remembered
  ;; (hundreds of parts) are split on every call, so build the tuple once instead of growing it by addition (quadratic in the number of parts). Tokens are never empty (every branch of TOKEN matches at least 1 character).
  (val tokens (lfor found (.finditer TOKEN statement) #((.group found "name") (.group found 0))))
  (val #^ (get tuple #((| SqlText SqlPlaceholder) ...)) parts
    (tuple (gfor #(is-text run) (groupby tokens (fn [token] (is (get token 0) None)))
                 part (if is-text
                          #((SqlText :text (.join "" (gfor token run (get token 1)))))
                          (gfor token run (SqlPlaceholder :name (get token 0))))
                 part)))
  (when (and (<= (len parts) SPLIT-MEMO-STATEMENT-PARTS)
             (< (len SPLIT-MEMO) SPLIT-MEMO-LIMIT)
             (<= (+ (get SPLIT-MEMO-PARTS 0) (len parts)) SPLIT-MEMO-PART-LIMIT))
    (setv (get SPLIT-MEMO-PARTS 0) (+ (get SPLIT-MEMO-PARTS 0) (len parts)))
    (setv (get SPLIT-MEMO statement) parts))
  parts)


(defk checked-params [parts params]
  {:pre [(: parts (get tuple #((| SqlText SqlPlaceholder) ...))) (: params (get tuple #(SqlParam ...)))] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "Check that the parameter names in the statement and the names in params match exactly with nothing missing or extra, and that values fall in the closed set (a mismatch is a caller error - ValueError / TypeError)."
  (val given (lfor p params p.name))
  (when (!= (len given) (len (set given)))
    (raise (ValueError (.format "duplicate names in params: {}" given))))
  (val used (sfor part parts :if (isinstance part SqlPlaceholder) part.name))
  (when (!= used (set given))
    (raise (ValueError (.format "statement parameters and params names differ: statement = {} / params = {}" (sorted used) (sorted given)))))
  (for [p params]
    (when (not (isinstance p.value SQL-VALUE-TYPES))
      (raise (TypeError (.format "type {} of the value for parameter {} is outside the closed set SqlValue" (. (type p.value) __name__) p.name)))))
  None)


(defk param-value [params name]
  {:pre [(: params (get tuple #(SqlParam ...))) (: name str)] :post [(: % SqlValue)]
   :tags {:context "sql" :role "foundation"}}
  "Look up the value for name name in params (call after checked-params)."
  (next (gfor p params :if (= p.name name) p.value)))


(defk checked-identifier [name]
  {:pre [(: name str)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "Check that a name rendered into DDL and insert statements is in a form that is safe unquoted (a violation is a ValueError)."
  (when (not (.fullmatch IDENTIFIER name))
    (raise (ValueError (.format "name {!r} is not of the form [A-Za-z_][A-Za-z0-9_]*" name))))
  name)


(defk checked-identifiers [names]
  {:pre [(: names (get tuple #(str ...)))] :post [(: % (get tuple #(str ...)))]
   :tags {:context "sql" :role "foundation"}}
  "Check every name in a sequence of names (columns, primary key, index columns) with checked-identifier and return them as a tuple in the written order (ValueError at the first violation)."
  (var #^ (get list str) checked [])
  (for [name names]
    (.append checked (! (checked-identifier name))))
  (tuple checked))


(defk checked-rows [columns rows]
  {:pre [(: columns (get tuple #(str ...))) (: rows (get tuple #((get tuple #(SqlValue ...)) ...)))] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "Check that each row of SqlInsertRows has the same length as the number of columns and that values fall in the closed set."
  (for [row rows]
    (when (!= (len row) (len columns))
      (raise (ValueError (.format "row length {} differs from column count {}: {!r}" (len row) (len columns) row))))
    (for [value row]
      (when (not (isinstance value SQL-VALUE-TYPES))
        (raise (TypeError (.format "type {} of a row value is outside the closed set SqlValue" (. (type value) __name__)))))))
  None)


;; --- Normalizing row values --------------------------------------------------------------------------------------------------------

(defk normalized-value [value]
  {:pre [(: value DriverValue)] :post [(: % SqlValue)]
   :effects []
   :tags {:context "sql" :role "foundation"}}
  "Map a driver value that has a mapping rule (DriverValue) into the closed set SqlValue. The mapping issues no effect (:effects [] - in the type declaration the answer Program
   has Never in the effect position). Types without a mapping rule are never silently turned into str -
   a value of such a type is rejected with a TypeError by normalized-rows, which receives driver rows (the one place that checks driver value types)."
  (match value
    (| (bool) (int) (float) (str) (bytes) None) value
    (| (memoryview) (bytearray)) (bytes value)
    (Decimal) (if (= value (.to-integral-value value)) (int value) (float value))
    (| (datetime.datetime) (datetime.date) (datetime.time)) (.isoformat value)
    (UUID) (str value)))


;; --- Boundary between runtime checks and static types ----------------------------------------------------------------
;; Driver values have different types per driver and look like object to the type checker. Two predicates are placed here that convey the result of checking a value's type at runtime
;; as the static type of the checked value (no escape-hatch cast; the check and the type correspond at exactly one point each - #2939).

;; cannot be a defk: a predicate that provides type narrowing (TypeGuard) - returning a Program would be neither a boolean nor a type narrowing.
(defn #^ (get TypeGuard DriverValue) is-driver-value [#^ object value]
  "Check whether a driver value is of a type with a rule for mapping into the closed set (DriverValue). If true, the type checker is told the value is a DriverValue
   (isinstance narrowing would turn the generic memoryview into an unknown type, making the place that passes it to the mapping function red)."
  (isinstance value DriverValue))


;; cannot be a defk: a predicate that provides type narrowing (TypeGuard) - returning a Program would be neither a boolean nor a type narrowing.
(defn #^ (get TypeGuard (get Sequence (get Sequence SqlValue))) is-plain-rows
  [#^ (| (get list (get tuple #(object ...))) (get tuple #((get tuple #(object ...)) ...))) rows]
  "Check whether the types of all values in the columns of a driver row are exactly types of the closed set (PLAIN-VALUE-TYPES). If true, the row's columns are conveyed to the type checker as a sequence of sequences of SqlValue
   - the boundary between runtime checks and static types is this one point only (the result of checking value types one by one becomes the type proof - #2939)."
  (all (gfor row rows (all (gfor value row (in (type value) PLAIN-VALUE-TYPES))))))


(defk normalized-rows [rows]
  {:pre [(: rows (| (get list (get tuple #(object ...))) (get tuple #((get tuple #(object ...)) ...))))]
   :post [(: % (get tuple #((get tuple #(SqlValue ...)) ...)))]
   :tags {:context "sql" :role "foundation"}}
  "Map the columns of driver rows into the rows of SqlRows (a tuple of tuples of normalized values). If the types of all values are exactly types of the closed set
   (PLAIN-VALUE-TYPES - the same value even through normalized-value), map without calling per value (sqlite answers always take this form).
   Value types differ per driver - types with no mapping rule (outside DriverValue) are rejected here with a TypeError (the one place that checks driver value types)."
  (when (is-plain-rows rows)
    (return (tuple (gfor row rows (tuple row)))))
  (var #^ (get list (get tuple #(SqlValue ...))) normalized [])
  (for [row rows]
    (var #^ (get list SqlValue) values [])
    (for [value row]
      (when (not (is-driver-value value))
        (raise (TypeError (.format "no rule for mapping driver value type {} into SqlValue" (. (type value) __name__)))))
      (.append values (! (normalized-value value))))
    (.append normalized (tuple values)))
  (tuple normalized))
