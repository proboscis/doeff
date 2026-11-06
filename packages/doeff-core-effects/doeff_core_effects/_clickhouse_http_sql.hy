;;; Real ClickHouse answerer clickhouse-http-sql-handler of the generic SQL effects (sql_effects.hy). Uses no client library;
;;; one HTTP POST of stdlib http.client = one query. It only repacks values and holds no judgment. Building requests and mapping answers are cut out as pure functions
;;; (clickhouse-query-request, clickhouse-insert-request, clickhouse-kill-request, clickhouse-rows, clickhouse-failure -- so the
;;; checks can run without a real DB).
;;;   - The database declaration ClickHouseDatabase (name, URL, the database inside ClickHouse, credentials, server-side deadline) is passed by the assembling side.
;;;   - Arguments rewrite the neutral `:name` to `{name:Type}`, and values are sent in the URL's `param_name` (the type comes from the closed table CLICKHOUSE-TYPES by the value's Python type).
;;;   - The answer shape is JSONCompactEachRow (with the setting that does not quote 64-bit integers). rowcount is None because HTTP does not count it (for inserts,
;;;     written_rows of X-ClickHouse-Summary). With wait_end_of_query = 1, errors are received as the HTTP status, not in the middle of the answer.
;;;   - SqlInsertRows = one body of `INSERT INTO t (columns) FORMAT JSONCompactEachRow`. JSON cannot carry bytes, so a bytes value is a ValueError.
;;;   - SqlTransaction is refused with SqlFailed (0A000) (a store with no transactions -- the program is not run).
;;;   - Failures: an answer with X-ClickHouse-Exception-Code is SqlFailed (sqlstate = None -- ClickHouse has no SQLSTATE; reason carries the code and text).
;;;     An unreachable HTTP, or a 5xx with no code (a front proxy), is SqlUnreachable.
;;;   - The client does not leave the connection until the server answers (proboscis-ema ISSUE-TRD-199): ClickHouse 24.11 crashes where it writes a result or exception
;;;     to a connection the client has left (HTTPHandler::trySendExceptionToClient). So per request it sends a server-side deadline (max_execution_time = the declared
;;;     deadline, timeout_overflow_mode = throw) and a query_id, and waits for the start of the answer for "deadline + margin (WAIT-MARGIN-SECONDS)" seconds --
;;;     a slow query is refused by the server at the deadline (TIMEOUT_EXCEEDED), and the client receives that answer as SqlFailed while it is still waiting. The wait is not
;;;     placed in the declaration but derived from the deadline (clickhouse-waits), so a pair whose wait is at or below the deadline cannot be built. When the wait is exceeded (network cut, server stopped),
;;;     before closing the original connection it fires `KILL QUERY WHERE query_id = ...` on another connection, reads the original connection's answer (the cancellation exception) for KILL-GRACE-SECONDS, and then
;;;     closes (clickhouse-abandon). Only when it cannot be read does it leave with SqlUnreachable. A request that could not connect had nothing run on the server,
;;;     so it is SqlUnreachable as is.
;;;   - Does not block the scheduler: HTTP round trips (queries, inserts, the DDL sequence of table declarations) are not fired on the scheduler's thread but run in a new VM with one thread per call
;;;     (ThreadPerCall of offloaded_call.hy), and only the task that fired waits on a promise completed from outside (the same convention as postgres-sql-handler).
;;;     Even during a slow query, the other tasks of the same run (statements to another store, the listener's /healthz, clock ticks) keep running. scheduled is needed
;;;     outside. It holds neither connections nor leases, so no cleanup is needed for an answer that did not reach a cancelled wait (keep-nothing). Even if the waiter is cancelled,
;;;     the running round trip is not stopped (stopping it would leave the server's query behind) -- the round trip ends within the seconds of the wait promise above.
;;;   - The end of the process also does not leave a round trip behind: the round-trip thread is a daemon, so when the process ends it
;;;     vanishes in the middle of a round trip, and the server wrote an answer to a connection the OS had closed the socket of and crashed (10-24 08:23 JST -- the SELECT of the record
;;;     service that ended when a worker Pod was replaced). Running round trips are put on the ledger (ROUND-TRIPS) and settled at the end of the process (Python's atexit at exit, the default
;;;     SIGTERM) (settle-round-trips): wait for EXIT-SETTLE-SECONDS for round trips to end by themselves, fire KILL QUERY at the remaining round trips, and
;;;     wait EXIT-KILL-GRACE-SECONDS for the round-trip threads to finish reading the cancellation answers on the original connections. A round trip after settling has begun is
;;;     not started (SqlUnreachable). The total seconds are kept short enough to fit within the grace from the stop signal to KILL (it does not wait for the round trip's deadline).
;;;     A process with a SIGTERM receiver installed (os-signal-stop-handler etc.) settles in atexit after the receiver has ended the Program.
(require doeff-hy.macros [defhandler defk deff <- val var])
(require doeff-hy.record [defrecord])
(import atexit)
(import functools)
(import http.client)
(import json)
(import math)
(import os)
(import re)
(import selectors)
(import signal)
(import threading)
(import time)
(import types)
(import uuid)
(import urllib.parse)
(import concurrent.futures [Future])
(import dataclasses [dataclass field])
(import doeff [Program])
(import doeff_core_effects._sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlColumnType SqlParam SqlTable SqlText SqlPlaceholder split-statement checked-params
                                        param-value
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects._offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])

;; Python type of a value -> ClickHouse type of an argument (a closed table; bool is looked up before int). None is Nullable(String) since its type is unknown.
(val CLICKHOUSE-TYPES #(#(bool "Bool") #(int "Int64") #(float "Float64") #(str "String") #(bytes "String") #((type None) "Nullable(String)")))
;; Declaration column type -> ClickHouse type.
(val CLICKHOUSE-COLUMN-TYPES {SqlColumnType.INTEGER "Int64" SqlColumnType.FLOAT "Float64" SqlColumnType.TEXT "String"
                              SqlColumnType.BYTES "String" SqlColumnType.BOOLEAN "Bool" SqlColumnType.JSON "String"})
;; The answer shape, and the settings attached per query (the deadline and query_id are per-request values, so clickhouse-deadline-pairs adds them).
(val ANSWER-FORMAT "JSONCompactEachRow")
(val QUERY-SETTINGS #(#("default_format" ANSWER-FORMAT) #("output_format_json_quote_64bit_integers" "0") #("wait_end_of_query" "1")))
;; The shape in which the server refuses a query that exceeded its deadline (names the default throw -- if the user's profile is break, rows cut off midway by the deadline would be silently returned).
(val TIMEOUT-OVERFLOW-MODE "throw")
;; Wait margin (header comment): the seconds the client waits for the start of the answer = deadline + these seconds. The server reads the deadline only between processing steps, so an answer refused at the deadline arrives
;; slightly after the deadline -- keep that delay within these seconds. The declaration is this one place (not changed per user).
(val WAIT-MARGIN-SECONDS 15.0)
;; After the wait is exceeded (header comment): the seconds to wait for the KILL QUERY round trip, and the seconds to read the cancellation answer on the original connection (each).
(val KILL-GRACE-SECONDS 15.0)
;; In settling at the end of the process (header comment): the seconds to wait for running round trips to end by themselves, and then the seconds allowed
;; each for the KILL QUERY round trip and for reading the cancellation answer. The sum (6 seconds) is kept within the grace between the doeff-cluster worker sending the stop signal to the job's process group and KILLing it (the shim's
;; 8.5 seconds = --stop-grace 10 seconds - 1.5 seconds of cleanup margin) -- exceeding it would cut the connection by KILL before settling finishes, causing the same crash.
(val EXIT-SETTLE-SECONDS 2.0)
(val EXIT-KILL-GRACE-SECONDS 2.0)
;; The User-Agent the client announces (http_user_agent in the server's query_log -- distinguishes this client's requests from other HTTP clients).
(val USER-AGENT "doeff-clickhouse-http-sql")
;; The shape of query_id (a uuid4 the client makes -- written directly into the KILL QUERY statement, so values outside this shape are refused).
(val QUERY-ID-SHAPE (re.compile "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
;; The SQLSTATE for refusing features ClickHouse lacks (transactions, unique indexes) (feature_not_supported).
(val NOT-SUPPORTED-SQLSTATE "0A000")
;; The Executor that runs HTTP round trips (header comment -- one thread per call).
(val CLICKHOUSE-THREADS (ThreadPerCall))
;; Bytes to escape in the escaped form of an argument value (the same as ClickHouse's TabSeparated).
(val ESCAPES #(#(b"\\" b"\\\\") #(b"\t" b"\\t") #(b"\n" b"\\n") #(b"\r" b"\\r")))


(defclass [(dataclass :frozen True :kw-only True)] ClickHouseDatabase []
  "Declaration of a ClickHouse database (name = the name the business writes in SqlQuery, url = the HTTP endpoint (http://host:8123), database = the database inside
   ClickHouse, user / password = credentials -- password is not shown in repr, deadline = the server-side deadline in seconds for one request -- sent per request as max_execution_time,
   a finite value greater than 0). The client's wait is not held in the declaration but derived from the deadline (header comment, clickhouse-waits)."
  #^ str name
  #^ str url
  #^ str database
  #^ str user
  #^ str password
  (setv password (field :repr False))
  #^ float deadline
  (setv deadline 30.0)
  (defn #^ None __post-init__ [self]  ; defk にできない: a check called when the dataclass builds it (it would not run if it returned a Program)
    ;; 0 means "no deadline" in ClickHouse -- the server would not refuse, so the client's wait would run out first, making a pair that leaves the query behind.
    (when (or (isinstance self.deadline bool) (not (isinstance self.deadline #(int float))) (not (< 0 self.deadline math.inf)))
      (raise (ValueError f"ClickHouseDatabase.deadline must be a finite number of seconds > 0, got {self.deadline !r}")))))


(defrecord ClickHouseWaits
  "The wait pair of one request (header comment): deadline = the seconds sent to the server as max_execution_time, wait = the seconds the client waits for the start of the answer (longer than the deadline
   -- so an answer refused at the deadline is received within the wait), grace = the seconds allowed, after the wait is exceeded, each for the KILL QUERY round trip and for reading the cancellation answer.
   A pair whose wait is at or below the deadline cannot be built (ValueError on construction)."
  (#^ float deadline)
  (#^ float wait)
  (#^ float grace)
  (defn #^ None __post-init__ [self]  ; defk にできない: a check called when the dataclass builds it (it would not run if it returned a Program)
    (when (not (< 0 self.deadline self.wait math.inf))
      (raise (ValueError f"ClickHouseWaits.wait must outlast the deadline, got deadline {self.deadline !r} and wait {self.wait !r}")))
    (when (not (< 0 self.grace math.inf))
      (raise (ValueError f"ClickHouseWaits.grace must be a finite number of seconds > 0, got {self.grace !r}")))))


(defrecord ClickHouseParam
  "One argument sent in the URL (name = the argument's name, text = the value's byte string in escaped form)."
  (#^ str name)
  (#^ bytes text))


(defrecord ClickHouseStatement
  "The statement rewritten to ClickHouse's `{name:Type}`, and the arguments sent in the URL."
  (#^ str text)
  (#^ (get tuple #(ClickHouseParam ...)) params))


(defrecord ClickHouseHeader
  "One HTTP header."
  (#^ str name)
  (#^ str value))


(defrecord ClickHouseRequest
  "One HTTP POST sent to ClickHouse (url = the URL carrying settings and arguments, body = the body, headers = the credential headers, query-id = the server's query name
   -- the same value as the URL's query_id, waits = the wait pair -- the same deadline as the URL's max_execution_time)."
  (#^ str url)
  (#^ bytes body)
  (#^ (get tuple #(ClickHouseHeader ...)) headers)
  (#^ str query-id)
  (#^ ClickHouseWaits waits))


(defrecord ClickHouseKill
  "One HTTP POST of KILL QUERY that stops the query of a request whose wait was exceeded (url = the endpoint carrying no settings, body = the statement, headers = the same credential headers as the original request)."
  (#^ str url)
  (#^ bytes body)
  (#^ (get tuple #(ClickHouseHeader ...)) headers))


(defrecord ClickHouseResponse
  "ClickHouse's HTTP answer (status, body, exception-code = X-ClickHouse-Exception-Code, summary = X-ClickHouse-Summary)."
  (#^ int status)
  (#^ bytes body)
  (#^ (| str None) exception-code)
  (#^ (| str None) summary))


(defclass RoundTrips []
  "Ledger of running HTTP round trips (header comment -- to settle at the end of the process). running = query_id -> #(request, end marker), closing = the marker that the end
   settling has begun (round trips after that are not put on the ledger = not started). Touched under a lock between the round-trip thread and the end procedure. It is a resource keeper, so it is not
   a value type."

  (deff __init__ [self]  ; defk にできない: initialization of a resource class
    {:pre [(: self RoundTrips)] :post [(: % "None")]}
    "To start with an empty ledger."
    (setv self.lock (threading.Lock)
          self.running {}
          self.closing False))

  (deff enter [self request]  ; defk にできない: the ledger's entry point touched by both the round-trip thread and the finishing procedure (a resource outside the VM)
    {:pre [(: self RoundTrips) (: request ClickHouseRequest)] :post [(: % (| threading.Event None))]}
    "To put a round trip on the ledger before starting it, and to receive the marker raised at the end of the round trip (None after settling has begun and nothing is put -- the caller does not start the round trip)."
    (with [_ self.lock]
      (if self.closing
          None
          (do (setv done (threading.Event))
              (setv (get self.running request.query-id) #(request done))
              done))))

  (deff leave [self request done]  ; defk にできない: the ledger's entry point touched by both the round-trip thread and the finishing procedure (a resource outside the VM)
    {:pre [(: self RoundTrips) (: request ClickHouseRequest) (: done threading.Event)] :post [(: % "None")]}
    "To remove a round trip that closed its connection from the ledger and raise the end marker."
    (with [_ self.lock]
      (.pop self.running request.query-id None))
    (.set done)
    None)

  (deff close [self]  ; defk にできない: the ledger's entry point touched by both the round-trip thread and the finishing procedure (a resource outside the VM)
    {:pre [(: self RoundTrips)] :post [(: % tuple)]}
    "At the start of settling, to refuse later round trips and answer the running round trips (pairs of request and end marker)."
    (with [_ self.lock]
      (setv self.closing True)
      (tuple (.values self.running)))))


;; The process's round-trip ledger (header comment).
(val ROUND-TRIPS (RoundTrips))


(defk clickhouse-type [value]
  {:pre [(: value (| int float str bytes bool None))] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "To look up the ClickHouse type from the Python type of an argument value (the closed table CLICKHOUSE-TYPES)."
  (next (gfor #(kind name) CLICKHOUSE-TYPES :if (isinstance value kind) name)))


(defk clickhouse-param-text [value]
  {:pre [(: value (| int float str bytes bool None))] :post [(: % bytes)]
   :tags {:context "sql" :role "foundation"}}
  "To turn an argument value into the byte string in escaped form sent as the URL's param_* (NULL is \\N)."
  (val raw (match value
             None None
             (bool) (if value b"true" b"false")
             (| (int) (float)) (.encode (repr value) "ascii")
             (str) (.encode value "utf-8")
             (bytes) value))
  (if (is raw None)
      b"\\N"
      (functools.reduce (fn [text pair] (.replace text (get pair 0) (get pair 1))) ESCAPES raw)))


(defk clickhouse-statement [statement params]
  {:pre [(: statement str) (: params tuple)] :post [(: % ClickHouseStatement)]
   :tags {:context "sql" :role "foundation"}}
  "To rewrite a statement in the neutral notation into a ClickHouse `{name:Type}` statement and URL arguments (a mismatch of argument names is a ValueError)."
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (var text "")
  (for [part parts]
    (match part
      (SqlText :text piece) (:= text (+ text piece))
      (SqlPlaceholder :name name) (:= text (+ text (.format "{{{}:{}}}" name (! (clickhouse-type (! (param-value params name)))))))))
  (var sent [])
  (for [p params]
    (.append sent (ClickHouseParam :name p.name :text (! (clickhouse-param-text p.value)))))
  (ClickHouseStatement :text text :params (tuple sent)))


(defk clickhouse-url [database pairs]
  {:pre [(: database ClickHouseDatabase) (: pairs list)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "To put settings and arguments on the database's URL (pairs = a sequence of name-value pairs -- the single point passed to urlencode)."
  (+ (.rstrip database.url "/") "/?" (urllib.parse.urlencode (+ [#("database" database.database)] pairs))))


(defk clickhouse-headers [database]
  {:pre [(: database ClickHouseDatabase)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "To build the credential headers."
  #((ClickHouseHeader :name "X-ClickHouse-User" :value database.user) (ClickHouseHeader :name "X-ClickHouse-Key" :value database.password)))


(defk clickhouse-waits [database]
  {:pre [(: database ClickHouseDatabase)] :post [(: % ClickHouseWaits)]
   :tags {:context "sql" :role "foundation"}}
  "To derive a request's wait pair from the database's deadline (header comment -- wait = deadline + WAIT-MARGIN-SECONDS, grace = KILL-GRACE-SECONDS)."
  (ClickHouseWaits :deadline (float database.deadline) :wait (+ database.deadline WAIT-MARGIN-SECONDS) :grace KILL-GRACE-SECONDS))


(defk clickhouse-seconds-text [seconds]
  {:pre [(: seconds float)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "To turn seconds into the value of a URL setting (integer seconds have no decimal point -- 30.0 is 30)."
  (if (.is-integer seconds) (str (int seconds)) (repr seconds)))


(defk clickhouse-deadline-pairs [waits query-id]
  {:pre [(: waits ClickHouseWaits) (: query-id str)] :post [(: % list)]
   :tags {:context "sql" :role "foundation"}}
  "To build the settings added to the URL per request (the server-side deadline, the form that refuses on exceeding it, and the query name) (header comment)."
  [#("max_execution_time" (! (clickhouse-seconds-text waits.deadline))) #("timeout_overflow_mode" TIMEOUT-OVERFLOW-MODE)
   #("query_id" query-id)])


(defk new-query-id []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "To build the query name of one request (uuid4 -- used to name it with KILL QUERY when the wait is exceeded)."
  (str (uuid.uuid4)))


(defk clickhouse-query-request [database statement params]
  {:pre [(: database ClickHouseDatabase) (: statement str) (: params (get tuple #(SqlParam ...)))] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "foundation"}}
  "To turn one SqlQuery into one HTTP POST (the statement is the body, settings and arguments are in the URL -- the deadline and query_id are per request)."
  (<- bound (clickhouse-statement statement params))
  (<- waits (clickhouse-waits database))
  (<- query-id (new-query-id))
  (val pairs (+ (lfor #(name value) QUERY-SETTINGS #(name value)) (! (clickhouse-deadline-pairs waits query-id))
                (lfor p bound.params #((+ "param_" p.name) p.text))))
  (ClickHouseRequest :url (! (clickhouse-url database pairs)) :body (.encode bound.text "utf-8") :headers (! (clickhouse-headers database))
                     :query-id query-id :waits waits))


(defk clickhouse-insert-request [database table columns rows]
  {:pre [(: database ClickHouseDatabase) (: table str) (: columns tuple) (: rows tuple)] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "foundation"}}
  "To turn SqlInsertRows into one HTTP POST of `INSERT ... FORMAT JSONCompactEachRow` (the body is JSON rows -- a bytes value is a ValueError;
   the deadline and query_id are per request)."
  (<- (checked-rows columns rows))
  (for [row rows]
    (when (any (gfor value row (isinstance value bytes)))
      (raise (ValueError (.format "ClickHouse JSON insertion cannot carry bytes values (table {})" table)))))
  (val query (.format "INSERT INTO {} ({}) FORMAT JSONCompactEachRow" (! (checked-identifier table))
                      (.join ", " (! (checked-identifiers columns)))))
  (val body (.join "" (gfor row rows (+ (json.dumps (list row) :ensure-ascii False) "\n"))))
  (<- waits (clickhouse-waits database))
  (<- query-id (new-query-id))
  (ClickHouseRequest :url (! (clickhouse-url database (+ [#("query" query)] (! (clickhouse-deadline-pairs waits query-id)))))
                     :body (.encode body "utf-8") :headers (! (clickhouse-headers database)) :query-id query-id :waits waits))


(defk clickhouse-kill-request [request]
  {:pre [(: request ClickHouseRequest)] :post [(: % ClickHouseKill)]
   :tags {:context "sql" :role "foundation"}}
  "To build the POST of KILL QUERY that stops the request's query (the same endpoint and credentials, ASYNC -- only sends the stop signal, and the cancellation answer is read on the original connection).
   query_id is a uuid4 the client made, so it is written directly into the statement (a value outside the shape is a ValueError)."
  (when (is (.fullmatch QUERY-ID-SHAPE request.query-id) None)
    (raise (ValueError (.format "query_id {!r} cannot be written into KILL QUERY" request.query-id))))
  (val target (urllib.parse.urlsplit request.url))
  (ClickHouseKill :url (urllib.parse.urlunsplit #(target.scheme target.netloc target.path "" ""))
                  :body (.encode (.format "KILL QUERY WHERE query_id = '{}' ASYNC" request.query-id) "ascii")
                  :headers request.headers))


(defk clickhouse-rows [body]
  {:pre [(: body bytes)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "To map the body of a JSONCompactEachRow answer to a tuple of rows (values are normalized to SqlValue -- an array or Map column is a TypeError).
   Rows are separated only by \\n -- splitlines also splits at U+0085, U+2028, U+2029, which arrive raw in values."
  (! (normalized-rows (lfor line (.split (.decode body "utf-8") "\n") :if (.strip line) (json.loads line)))))


(defk clickhouse-written-rows [summary]
  {:pre [(: summary (| str None))] :post [(: % (| int None))]
   :tags {:context "sql" :role "foundation"}}
  "To read written_rows of X-ClickHouse-Summary (None if absent)."
  (if (is summary None)
      None
      (do (val written (.get (json.loads summary) "written_rows"))
          (if (is written None) None (int written)))))


(defk clickhouse-failure [response]
  {:pre [(: response ClickHouseResponse)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To map a non-200 HTTP answer to a failure value (header comment)."
  (val text (.strip (.decode response.body "utf-8" "replace")))
  (if (is response.exception-code None)
      (SqlUnreachable :reason (.format "HTTP {}: {}" response.status text))
      (SqlFailed :sqlstate None :reason text)))


(defk clickhouse-schema-statements [tables]
  {:pre [(: tables (get tuple #(SqlTable ...)))] :post [(: % (| (get tuple #(str ...)) SqlFailed))]
   :tags {:context "sql" :role "foundation"}}
  "To render table declarations as ClickHouse DDL (MergeTree, ORDER BY = primary key, indexes are minmax data skipping). Unique indexes cannot be had, so
   SqlFailed (0A000)."
  (var statements [])
  (for [table tables]
    (when (any (gfor index table.indexes index.unique))
      (return (SqlFailed :sqlstate NOT-SUPPORTED-SQLSTATE :reason (.format "ClickHouse cannot have unique indexes (table {})" table.name))))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}" (! (checked-identifier column.name))
                                    (if column.nullable
                                        (.format "Nullable({})" (get CLICKHOUSE-COLUMN-TYPES column.type))
                                        (get CLICKHOUSE-COLUMN-TYPES column.type)))))
    (for [index table.indexes]
      (.append definitions (.format "INDEX {} ({}) TYPE minmax GRANULARITY 1" (! (checked-identifier index.name))
                                    (.join ", " (! (checked-identifiers index.columns))))))
    (val order (if table.primary-key
                   (.format "({})" (.join ", " (! (checked-identifiers table.primary-key))))
                   "tuple()"))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({}) ENGINE = MergeTree ORDER BY {}" (! (checked-identifier table.name))
                                 (.join ", " definitions) order)))
  (tuple statements))


(defk _clickhouse-transaction-refusal [database]
  {:pre [(: database str)] :post [(: % SqlFailed)]
   :tags {:context "sql" :role "foundation"}}
  "The refusal for SqlTransaction (header comment -- the single place where the real answerer and the mock clickhouse_mock_sql return the same value)."
  (SqlFailed :sqlstate NOT-SUPPORTED-SQLSTATE
             :reason (.format "ClickHouse database {!r} has no transactions (SqlTransaction is refused)" database)))


(defk clickhouse-connection [url seconds]
  {:pre [(: url str) (: seconds float)] :post [(: % http.client.HTTPConnection)]
   :tags {:context "sql" :role "foundation"}}
  "To make an HTTP connection to the URL's endpoint (not yet connected) (for https, TLS; the wait for one socket read or write is seconds seconds)."
  (val target (urllib.parse.urlsplit url))
  (if (= target.scheme "https")
      (http.client.HTTPSConnection target.hostname target.port :timeout seconds)
      (http.client.HTTPConnection target.hostname target.port :timeout seconds)))


(defk clickhouse-path [url]
  {:pre [(: url str)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "To get from the URL the path and settings part to write on the HTTP request line."
  (val target (urllib.parse.urlsplit url))
  (urllib.parse.urlunsplit #("" "" (or target.path "/") target.query "")))


(defk clickhouse-post [connection url body headers]
  {:pre [(: connection http.client.HTTPConnection) (: url str) (: body bytes) (: headers tuple)] :post [(: % (type None))]
   :tags {:context "sql" :role "foundation"}}
  "To send the POST head and body on the connected connection (the answer is not read -- clickhouse-read reads it)."
  (.request connection "POST" (! (clickhouse-path url)) :body body
            :headers (| (dfor h headers h.name h.value) {"User-Agent" USER-AGENT}))
  None)


(defk answer-started [connection seconds]
  {:pre [(: connection http.client.HTTPConnection) (: seconds float)] :post [(: % bool)]
   :tags {:context "sql" :role "foundation"}}
  "To learn, without reading the answer, whether the answer's bytes for the sent request have arrived (waits up to seconds seconds until they arrive or the connection is closed) (the wait of the header comment)."
  (if (is connection.sock None)
      False
      (with [selector (selectors.DefaultSelector)]
        (.register selector connection.sock selectors.EVENT-READ)
        (bool (.select selector seconds)))))


(defk clickhouse-read [connection]
  {:pre [(: connection http.client.HTTPConnection)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To read the started answer through to the head and body (the wait for each read is the connection's socket seconds -- if cut midway, SqlUnreachable)."
  (try
    (with [answer (.getresponse connection)]
      (ClickHouseResponse :status answer.status :body (.read answer)
                          :exception-code (.getheader answer "X-ClickHouse-Exception-Code")
                          :summary (.getheader answer "X-ClickHouse-Summary")))
    (except [error #(OSError http.client.HTTPException)]
      (SqlUnreachable :reason (.format "Reading the answer was cut off: {!r}" error)))))


(defk clickhouse-kill [kill seconds]
  {:pre [(: kill ClickHouseKill) (: seconds float)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To send one KILL QUERY POST on another connection and receive its answer (an ASYNC KILL answers at once -- up to seconds seconds)."
  (<- connection (clickhouse-connection kill.url seconds))
  (try
    (try
      (.connect connection)
      (<- (clickhouse-post connection kill.url kill.body kill.headers))
      (except [error OSError]
        (return (SqlUnreachable :reason (.format "Cannot send KILL QUERY: {!r}" error)))))
    (<- answer (clickhouse-read connection))
    answer
    (finally (.close connection))))


(defk clickhouse-abandon [connection request why]
  {:pre [(: connection http.client.HTTPConnection) (: request ClickHouseRequest) (: why str)]
   :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To leave, from a request whose wait was exceeded, after stopping the server's query (header comment): fire KILL QUERY on another connection, and wait on the original connection
   for the grace seconds to read the cancellation answer. If it can be read, that answer (the cancellation exception -- the caller maps it to SqlFailed; if the query ended before the KILL, that answer),
   if not, SqlUnreachable (with the KILL outcome in the reason). Closing the original connection is the caller's job."
  (<- kill (clickhouse-kill-request request))
  (<- killed (clickhouse-kill kill request.waits.grace))
  (val kill-outcome (match killed
                      (SqlUnreachable :reason reason) reason
                      (ClickHouseResponse :status 200) "KILL QUERY was accepted"
                      (ClickHouseResponse :status status :body body)
                        (.format "KILL QUERY was refused (HTTP {}: {})" status (.strip (.decode body "utf-8" "replace")))))
  (when (is-not connection.sock None)
    (.settimeout connection.sock request.waits.grace))
  (<- started (answer-started connection request.waits.grace))
  (when started
    (<- answer (clickhouse-read connection))
    (when (isinstance answer ClickHouseResponse)
      (return answer)))
  (SqlUnreachable :reason (.format "{} (query_id {}) -- the cancellation answer did not arrive within {} seconds; {}" why request.query-id
                                   (! (clickhouse-seconds-text request.waits.grace)) kill-outcome)))


(defk clickhouse-send [request]
  {:pre [(: request ClickHouseRequest)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To send one HTTP POST and receive the answer (the wait promise of the header comment): wait waits.wait seconds for the start of the answer, and if exceeded, stop
   the query with clickhouse-abandon and leave. If it cannot connect (nothing is running on the server), it is SqlUnreachable as is. The round trip is on the ledger
   ROUND-TRIPS until the connection is closed, and is not started after settling at the end of the process has begun (header comment)."
  (val done (.enter ROUND-TRIPS request))
  (when (is done None)
    (return (SqlUnreachable :reason (.format "The process is ending, so the round trip is not started (query_id {})" request.query-id))))
  (try
    (<- connection (clickhouse-connection request.url request.waits.wait))
    (try
      (try
        (.connect connection)
        (except [error OSError]
          (return (SqlUnreachable :reason (.format "Cannot connect: {!r}" error)))))
      (try
        (<- (clickhouse-post connection request.url request.body request.headers))
        (except [error OSError]
          (<- broken (clickhouse-abandon connection request (.format "Cut off while sending the request: {!r}" error)))
          (return broken)))
      (<- started (answer-started connection request.waits.wait))
      (if started
          (do (<- answer (clickhouse-read connection))
              answer)
          (do (<- abandoned (clickhouse-abandon connection request
                                                (.format "The answer did not start within the {}-second wait" (! (clickhouse-seconds-text request.waits.wait)))))
              abandoned))
      (finally (.close connection)))
    (finally (.leave ROUND-TRIPS request done))))


(defk clickhouse-exit-kill [request grace]
  {:pre [(: request ClickHouseRequest) (: grace float)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "In settling at the end of the process (header comment), to stop with KILL QUERY the query of a round trip that did not end by itself (the round-trip thread reads the cancellation answer
   on the original connection)."
  (<- kill (clickhouse-kill-request request))
  (<- answer (clickhouse-kill kill grace))
  answer)


(deff submit-exit-kill [request grace]  ; defk にできない: a part of the entry point called from outside the VM at process end (atexit, signal receiver)
  {:pre [(: request ClickHouseRequest) (: grace float)] :post [(: % Future)]
   :tags {:context "sql" :role "foundation"}}
  "In settling at the end of the process, to fire the KILL QUERY of one round trip on a thread of CLICKHOUSE-THREADS and run it in parallel with the KILLs of other round trips."
  (.submit CLICKHOUSE-THREADS (fn [] (run-detached (clickhouse-exit-kill request grace)))))


(deff settle-round-trips [settle-seconds grace]  ; defk にできない: the entry point called from outside the VM at process end (atexit, signal receiver)
  {:pre [(: settle-seconds float) (: grace float)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "To settle running round trips before the process ends (header comment): refuse later round trips, wait settle-seconds for round trips to end by themselves, fire
   KILL QUERY at the remaining ones (up to grace seconds each -- in parallel on separate threads), and wait until grace seconds after the KILL for the round-trip threads to finish reading the cancellation answers.
   Answer = the requests of round trips that still could not be settled (the process ends leaving them behind). The total is at most settle-seconds + 2 x grace seconds."
  (setv started (time.monotonic))
  (setv running (.close ROUND-TRIPS))
  (for [#(_ done) running]
    (.wait done (max 0.0 (- (+ started settle-seconds) (time.monotonic)))))
  (setv stuck (tuple (gfor #(request done) running :if (not (.is-set done)) #(request done))))
  (setv asked (time.monotonic))
  (for [#(request _) stuck]
    (submit-exit-kill request grace))
  (for [#(_ done) stuck]
    (.wait done (max 0.0 (- (+ asked grace grace) (time.monotonic)))))
  (tuple (gfor #(request done) stuck :if (not (.is-set done)) request)))


(deff settle-at-exit []  ; defk にできない: a plain callback called by Python's exit (atexit)
  {:pre [] :post [(: % "None")]
   :tags {:context "sql" :role "foundation"}}
  "To settle running round trips when the Program ends and Python ends (header comment -- daemon round-trip threads vanish without being waited for)."
  (settle-round-trips EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS)
  None)


(defclass SettleThenTerminate []
  "A receiver for a process that ends on the default (receiverless) SIGTERM, to settle running round trips and then end with the same SIGTERM (header comment).
   A process with a receiver installed (os-signal-stop-handler etc.) replaces the receiver, so this does not work (then atexit settles). It is a resource keeper,
   so it is not a value type."

  (deff __init__ [self]  ; defk にできない: initialization of a resource class
    {:pre [(: self SettleThenTerminate)] :post [(: % "None")]}
    "To start with the marker that no signal has been received yet."
    (setv self.received False))

  (deff receive [self number frame]  ; defk にできない: Python's signal receiver (a plain callback on the main thread)
    {:pre [(: self SettleThenTerminate) (: number int) (: frame (| types.FrameType None))] :post [(: % "None")]}
    "On receiving SIGTERM, to settle round trips, return to the default receiver, and end with the same signal (a second SIGTERM that arrives during settling only
     makes settling wait, and does not end it earlier)."
    (when (not self.received)
      (setv self.received True)
      (settle-round-trips EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS)
      (signal.signal number signal.SIG-DFL)
      (os.kill (os.getpid) number))
    None))


(deff install-exit-settle []  ; defk にできない: the entry point that installs Python's exit hook and the signal receiver just once when the module is loaded
  {:pre [] :post [(: % "None")]
   :tags {:context "sql" :role "foundation"}}
  "To settle running round trips on the two paths at the end of the process (Python's exit and the default SIGTERM) (header comment). The SIGTERM receiver is installed only in
   the main thread, where signals can be installed, and only when the receiver is still the default (the behavior of a process holding an installed receiver is not changed)."
  (atexit.register settle-at-exit)
  (when (and (is (threading.current-thread) (threading.main-thread)) (= (signal.getsignal signal.SIGTERM) signal.SIG-DFL))
    (signal.signal signal.SIGTERM (. (SettleThenTerminate) receive)))
  None)


(install-exit-settle)


(defk clickhouse-answer [request insert]
  {:pre [(: request ClickHouseRequest) (: insert bool)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To send a request and turn the answer into SqlRows or a failure value (insert = an insert -- it does not read rows but counts written_rows)."
  (<- response (clickhouse-send request))
  (match response
    (SqlUnreachable) (return response)
    (ClickHouseResponse :status status) :if (!= status 200) (do (<- failure (clickhouse-failure response)) (return failure))
    _ None)
  (if insert
      (do (<- written (clickhouse-written-rows response.summary))
          (SqlRows :rows #() :rowcount written))
      (do (<- rows (clickhouse-rows response.body))
          (SqlRows :rows rows :rowcount None))))


(defk clickhouse-ensure-tables [database tables]
  {:pre [(: database ClickHouseDatabase) (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "To render table declarations into DDL and run them in order (stop at the first failure)."
  (<- statements (clickhouse-schema-statements tables))
  (when (isinstance statements SqlFailed)
    (return statements))
  (for [statement statements]
    (<- request (clickhouse-query-request database statement #()))
    (<- answer (clickhouse-answer request False))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


(defk off-scheduler [program]
  {:pre [(: program Program)] :post [(: % (| SqlRows SqlFailed SqlUnreachable SqlSchemaApplied))]
   :tags {:context "sql" :role "foundation"}}
  "To turn a Program that has HTTP round trips into a value outside the scheduler's thread (a new VM of CLICKHOUSE-THREADS), with only the task that fired waiting (header comment)."
  (<- answer (offloaded CLICKHOUSE-THREADS (fn [] (run-detached program)) keep-nothing))
  answer)


(defhandler clickhouse-http-sql-handler [#^ (get tuple #(ClickHouseDatabase ...)) databases]
  ;; Why it stays an argument: the database declarations (URL and credentials) are values passed by the assembling side, and are sorted into the same set as the PostgreSQL answerer by the name of the database answered.
  "The real ClickHouse answerer (header comment). databases = a tuple of ClickHouseDatabase (answers only declared names, and passes other names outside)."
  {:tags {:context "sql" :role "foundation"}}
  (SqlQuery [database statement params] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (<- request (clickhouse-query-request declared statement params))
    (<- answer (off-scheduler (clickhouse-answer request False)))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (if (not rows)
        (resume (SqlRows :rows #() :rowcount 0))
        (do (<- request (clickhouse-insert-request declared table columns rows))
            (<- answer (off-scheduler (clickhouse-answer request True)))
            (resume answer))))
  (SqlEnsureTables [database tables] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (<- answer (off-scheduler (clickhouse-ensure-tables declared tables)))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (any (gfor d databases (= d.name database)))
    (<- refusal (_clickhouse-transaction-refusal database))
    (resume refusal)))
