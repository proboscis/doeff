;;; 汎用の SQL の effect(sql_effects.hy)の本物の ClickHouse の答え手 clickhouse-http-sql-handler(agora-redesign #802 便 3)。client の library を
;;; 使わず、stdlib の urllib の HTTP POST 1 本 = 問い合わせ 1 つ。値の詰め替えだけで判断を持たない。要求の組み立てと答えの写しは純関数に切って
;;; ある(clickhouse-query-request・clickhouse-insert-request・clickhouse-rows・clickhouse-failure — 実 DB なしで検を撃てる)。
;;;   - database の宣言 ClickHouseDatabase(名・URL・ClickHouse の中の database・資格)は組み立ての側が渡す。
;;;   - 引数は中立の `:name` を `{name:Type}` へ書き換え、値は URL の `param_name` で送る(型は値の Python の型から閉じた表 CLICKHOUSE-TYPES)。
;;;   - 答えの形は JSONCompactEachRow(64 bit の整数を quote しない設定)。rowcount は HTTP が数えないので None(投入は
;;;     X-ClickHouse-Summary の written_rows)。wait_end_of_query = 1 で、誤りを答えの途中ではなく HTTP の status で受ける。
;;;   - SqlInsertRows = `INSERT INTO t (欄) FORMAT JSONCompactEachRow` の本文 1 つ。JSON は bytes を運べないので bytes の値は ValueError。
;;;   - SqlTransaction は SqlFailed(0A000)で断る(transaction の無い置き場 — program を走らせない)。
;;;   - 失敗: X-ClickHouse-Exception-Code の在る答えは SqlFailed(sqlstate = None — ClickHouse は SQLSTATE を持たない・reason に code と文)。
;;;     HTTP が届かない・code の無い 5xx(前段の proxy)は SqlUnreachable。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import functools)
(import json)
(import urllib.error)
(import urllib.parse)
(import urllib.request)
(import dataclasses [dataclass field])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlColumnType SqlText SqlPlaceholder split-statement checked-params param-value
                                        checked-identifier checked-identifiers checked-rows normalized-rows])

;; 値の Python の型 → 引数の ClickHouse の型(閉じた表・bool は int より先に引く)。None は型が分からないので Nullable(String)。
(val CLICKHOUSE-TYPES #(#(bool "Bool") #(int "Int64") #(float "Float64") #(str "String") #(bytes "String") #((type None) "Nullable(String)")))
;; 宣言の欄の型 → ClickHouse の型。
(val CLICKHOUSE-COLUMN-TYPES {SqlColumnType.INTEGER "Int64" SqlColumnType.FLOAT "Float64" SqlColumnType.TEXT "String"
                              SqlColumnType.BYTES "String" SqlColumnType.BOOLEAN "Bool" SqlColumnType.JSON "String"})
;; 答えの形と、問い合わせごとに付ける設定。
(val ANSWER-FORMAT "JSONCompactEachRow")
(val QUERY-SETTINGS #(#("default_format" ANSWER-FORMAT) #("output_format_json_quote_64bit_integers" "0") #("wait_end_of_query" "1")))
;; ClickHouse に無い機能(transaction・一意の索引)を断る時の SQLSTATE(feature_not_supported)。
(val NOT-SUPPORTED-SQLSTATE "0A000")
;; 引数の値の escaped の形で escape する byte(ClickHouse の TabSeparated と同じ)。
(val ESCAPES #(#(b"\\" b"\\\\") #(b"\t" b"\\t") #(b"\n" b"\\n") #(b"\r" b"\\r")))


(defclass [(dataclass :frozen True :kw-only True)] ClickHouseDatabase []
  "ClickHouse の database の宣言(name = 業務が SqlQuery に書く名・url = HTTP の口(http://host:8123)・database = ClickHouse の中の
   database・user / password = 資格 — password は repr に出さない・timeout = 1 問い合わせの秒)。"
  #^ str name
  #^ str url
  #^ str database
  #^ str user
  #^ str password
  (setv password (field :repr False))
  #^ float timeout
  (setv timeout 30.0))


(defrecord ClickHouseParam
  "URL で送る引数 1 つ(name = 引数の名・text = escaped の形の値の byte 列)。"
  (#^ str name)
  (#^ bytes text))


(defrecord ClickHouseStatement
  "ClickHouse の `{name:Type}` へ書き換えた文と、URL で送る引数。"
  (#^ str text)
  (#^ (get tuple #(ClickHouseParam ...)) params))


(defrecord ClickHouseHeader
  "HTTP の頭 1 つ。"
  (#^ str name)
  (#^ str value))


(defrecord ClickHouseRequest
  "ClickHouse へ送る HTTP POST 1 本(url = 設定と引数を載せた URL・body = 本文・headers = 資格の頭)。"
  (#^ str url)
  (#^ bytes body)
  (#^ (get tuple #(ClickHouseHeader ...)) headers))


(defrecord ClickHouseResponse
  "ClickHouse の HTTP の答え(status・body・exception-code = X-ClickHouse-Exception-Code・summary = X-ClickHouse-Summary)。"
  (#^ int status)
  (#^ bytes body)
  (#^ (| str None) exception-code)
  (#^ (| str None) summary))


(defk clickhouse-type [value]
  {:pre [(: value (| int float str bytes bool None))] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "引数の値の Python の型から ClickHouse の型を引くため(閉じた表 CLICKHOUSE-TYPES)。"
  (next (gfor #(kind name) CLICKHOUSE-TYPES :if (isinstance value kind) name)))


(defk clickhouse-param-text [value]
  {:pre [(: value (| int float str bytes bool None))] :post [(: % bytes)]
   :tags {:context "sql" :role "foundation"}}
  "引数の値を URL の param_* で送る escaped の形の byte 列にするため(NULL は \\N)。"
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
  "中立の記法の文を ClickHouse の `{name:Type}` の文と URL の引数へ書き換えるため(引数の名の食い違いは ValueError)。"
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
  "database の URL に設定と引数を載せるため(pairs = 名と値の組の列 — urlencode へ渡す 1 点)。"
  (+ (.rstrip database.url "/") "/?" (urllib.parse.urlencode (+ [#("database" database.database)] pairs))))


(defk clickhouse-headers [database]
  {:pre [(: database ClickHouseDatabase)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "資格の頭を作るため。"
  #((ClickHouseHeader :name "X-ClickHouse-User" :value database.user) (ClickHouseHeader :name "X-ClickHouse-Key" :value database.password)))


(defk clickhouse-query-request [database statement params]
  {:pre [(: database ClickHouseDatabase) (: statement str) (: params tuple)] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "foundation"}}
  "SqlQuery 1 つを HTTP POST 1 本にするため(文は本文・設定と引数は URL)。"
  (<- bound (clickhouse-statement statement params))
  (val pairs (+ (lfor #(name value) QUERY-SETTINGS #(name value)) (lfor p bound.params #((+ "param_" p.name) p.text))))
  (ClickHouseRequest :url (! (clickhouse-url database pairs)) :body (.encode bound.text "utf-8") :headers (! (clickhouse-headers database))))


(defk clickhouse-insert-request [database table columns rows]
  {:pre [(: database ClickHouseDatabase) (: table str) (: columns tuple) (: rows tuple)] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows を `INSERT … FORMAT JSONCompactEachRow` の HTTP POST 1 本にするため(JSON の行の本文 — bytes の値は ValueError)。"
  (<- (checked-rows columns rows))
  (for [row rows]
    (when (any (gfor value row (isinstance value bytes)))
      (raise (ValueError (.format "ClickHouse の JSON の投入は bytes の値を運べない(表 {})" table)))))
  (val query (.format "INSERT INTO {} ({}) FORMAT JSONCompactEachRow" (! (checked-identifier table))
                      (.join ", " (! (checked-identifiers columns)))))
  (val body (.join "" (gfor row rows (+ (json.dumps (list row) :ensure-ascii False) "\n"))))
  (ClickHouseRequest :url (! (clickhouse-url database [#("query" query)])) :body (.encode body "utf-8")
                     :headers (! (clickhouse-headers database))))


(defk clickhouse-rows [body]
  {:pre [(: body bytes)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "JSONCompactEachRow の答えの本文を行の tuple へ写すため(値は SqlValue へ正規化 — 配列・Map の欄は TypeError)。"
  (! (normalized-rows (lfor line (.splitlines (.decode body "utf-8")) :if (.strip line) (json.loads line)))))


(defk clickhouse-written-rows [summary]
  {:pre [(: summary (| str None))] :post [(: % (| int None))]
   :tags {:context "sql" :role "foundation"}}
  "X-ClickHouse-Summary の written_rows を読むため(無ければ None)。"
  (if (is summary None)
      None
      (do (val written (.get (json.loads summary) "written_rows"))
          (if (is written None) None (int written)))))


(defk clickhouse-failure [response]
  {:pre [(: response ClickHouseResponse)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "200 でない HTTP の答えを失敗の値へ写すため(頭の註)。"
  (val text (.strip (.decode response.body "utf-8" "replace")))
  (if (is response.exception-code None)
      (SqlUnreachable :reason (.format "HTTP {}: {}" response.status text))
      (SqlFailed :sqlstate None :reason text)))


(defk clickhouse-schema-statements [tables]
  {:pre [(: tables tuple)] :post [(: % (| tuple SqlFailed))]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を ClickHouse の DDL(MergeTree・ORDER BY = 主鍵・索引は minmax の data skipping)に描くため。一意の索引は持てないので
   SqlFailed(0A000)。"
  (var statements [])
  (for [table tables]
    (when (any (gfor index table.indexes index.unique))
      (return (SqlFailed :sqlstate NOT-SUPPORTED-SQLSTATE :reason (.format "ClickHouse は一意の索引を持てない(表 {})" table.name))))
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


(defk clickhouse-send [request timeout]
  {:pre [(: request ClickHouseRequest) (: timeout float)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "HTTP POST 1 本を送って答えを受けるため(届かなければ SqlUnreachable)。"
  (val sent (urllib.request.Request request.url :data request.body :method "POST"
                                    :headers (dfor h request.headers h.name h.value)))
  (try
    (with [answer (urllib.request.urlopen sent :timeout timeout)]
      (ClickHouseResponse :status answer.status :body (.read answer)
                          :exception-code (.get answer.headers "X-ClickHouse-Exception-Code")
                          :summary (.get answer.headers "X-ClickHouse-Summary")))
    (except [error urllib.error.HTTPError]
      (ClickHouseResponse :status error.code :body (.read error)
                          :exception-code (.get error.headers "X-ClickHouse-Exception-Code")
                          :summary (.get error.headers "X-ClickHouse-Summary")))
    (except [error #(urllib.error.URLError OSError)]
      (SqlUnreachable :reason (str error)))))


(defk clickhouse-answer [request timeout insert]
  {:pre [(: request ClickHouseRequest) (: timeout float) (: insert bool)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "要求を送り、答えを SqlRows か失敗の値にするため(insert = 投入 — 行を読まず written_rows を数える)。"
  (<- response (clickhouse-send request timeout))
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
  "表の宣言を DDL に描いて順に流すため(最初の失敗で止める)。"
  (<- statements (clickhouse-schema-statements tables))
  (when (isinstance statements SqlFailed)
    (return statements))
  (for [statement statements]
    (<- request (clickhouse-query-request database statement #()))
    (<- answer (clickhouse-answer request database.timeout False))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


(defhandler clickhouse-http-sql-handler [#^ tuple databases]
  ;; 引数に残す理由: database の宣言(URL と資格)は組み立ての側が渡す値で、答える database の名で PostgreSQL の答え手と同じ組に並べ分ける。
  "本物の ClickHouse の答え手(頭の註)。databases = ClickHouseDatabase の tuple(宣言した名にだけ答え、他の名は外側へ回す)。"
  {:tags {:context "sql" :role "foundation"}}
  (SqlQuery [database statement params] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (<- request (clickhouse-query-request declared statement params))
    (<- answer (clickhouse-answer request declared.timeout False))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (if (not rows)
        (resume (SqlRows :rows #() :rowcount 0))
        (do (<- request (clickhouse-insert-request declared table columns rows))
            (<- answer (clickhouse-answer request declared.timeout True))
            (resume answer))))
  (SqlEnsureTables [database tables] :when (any (gfor d databases (= d.name database)))
    (val declared (next (gfor d databases :if (= d.name database) d)))
    (<- answer (clickhouse-ensure-tables declared tables))
    (resume answer))
  (SqlTransaction [database program lock-key] :when (any (gfor d databases (= d.name database)))
    (resume (SqlFailed :sqlstate NOT-SUPPORTED-SQLSTATE
                       :reason (.format "ClickHouse の database {!r} は transaction を持たない(SqlTransaction を断る)" database)))))
