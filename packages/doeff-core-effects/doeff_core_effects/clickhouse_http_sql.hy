;;; 汎用の SQL の effect(sql_effects.hy)の本物の ClickHouse の答え手 clickhouse-http-sql-handler(agora-redesign #802 便 3)。client の library を
;;; 使わず、stdlib の http.client の HTTP POST 1 本 = 問い合わせ 1 つ。値の詰め替えだけで判断を持たない。要求の組み立てと答えの写しは純関数に
;;; 切ってある(clickhouse-query-request・clickhouse-insert-request・clickhouse-kill-request・clickhouse-rows・clickhouse-failure — 実 DB なしで
;;; 検を撃てる)。
;;;   - database の宣言 ClickHouseDatabase(名・URL・ClickHouse の中の database・資格・server 側の期限)は組み立ての側が渡す。
;;;   - 引数は中立の `:name` を `{name:Type}` へ書き換え、値は URL の `param_name` で送る(型は値の Python の型から閉じた表 CLICKHOUSE-TYPES)。
;;;   - 答えの形は JSONCompactEachRow(64 bit の整数を quote しない設定)。rowcount は HTTP が数えないので None(投入は
;;;     X-ClickHouse-Summary の written_rows)。wait_end_of_query = 1 で、誤りを答えの途中ではなく HTTP の status で受ける。
;;;   - SqlInsertRows = `INSERT INTO t (欄) FORMAT JSONCompactEachRow` の本文 1 つ。JSON は bytes を運べないので bytes の値は ValueError。
;;;   - SqlTransaction は SqlFailed(0A000)で断る(transaction の無い置き場 — program を走らせない)。
;;;   - 失敗: X-ClickHouse-Exception-Code の在る答えは SqlFailed(sqlstate = None — ClickHouse は SQLSTATE を持たない・reason に code と文)。
;;;     HTTP が届かない・code の無い 5xx(前段の proxy)は SqlUnreachable。
;;;   - client は server が答えるまで接続を離れない(card ki-42d653b1470f・proboscis-ema ISSUE-TRD-199): ClickHouse 24.11 は、client が離れた
;;;     接続へ結果か例外を書く所(HTTPHandler::trySendExceptionToClient)で落ちる。そこで要求ごとに server 側の期限(max_execution_time = 宣言の
;;;     deadline・timeout_overflow_mode = throw)と query_id を送り、答えの始まりを「期限 + 余白(WAIT-MARGIN-SECONDS)」の秒だけ待つ —
;;;     遅い問い合わせは server が期限で断り(TIMEOUT_EXCEEDED)、その答えを client がまだ待っている間に SqlFailed として受ける。待ちは宣言に
;;;     置かず期限から導く(clickhouse-waits)ので、待ちが期限以下の組は作れない。待ちを越えた時(網の切れ・server の止まり)は、元の接続を
;;;     閉じる前に別の接続で `KILL QUERY WHERE query_id = …` を撃ち、元の接続の答え(取り消しの例外)を KILL-GRACE-SECONDS の間 読んでから
;;;     閉じる(clickhouse-abandon)。読めなかった時だけ SqlUnreachable で離れる。接続できなかった要求は server で何も走っていないので、
;;;     そのまま SqlUnreachable。
;;;   - scheduler を塞がない: HTTP の往復(問い合わせ・投入・表の宣言の DDL の列)は scheduler の thread で撃たず、呼び 1 つに thread 1 本
;;;     (offloaded_call.hy の ThreadPerCall)の新しい VM で回し、撃った task だけが外から完了させる promise で待つ(postgres-sql-handler と
;;;     同じ作法)。遅い問い合わせの間も、同じ run の他の task(別の置き場への文・待ち受けの /healthz・時計の刻み)は回る。外側に scheduled が
;;;     要る。接続も貸し出しも持たないので、取り消された待ちに届かなかった答えの後始末は要らない(keep-nothing)。待ち手が取り消されても
;;;     走っている往復は止めない(止めると server の問い合わせを置いて離れる)— 往復は上の待ちの約束の秒の内に終わる。
;;;   - process の終わりも往復を置いて離れない(card ki-41b402ad072d): 往復の thread は daemon なので、process が終わると往復の途中で
;;;     消え、OS が socket を閉じた接続へ server が答えを書いて落ちた(10-24 08:23 JST — worker の Pod の入れ替えで終わった record の
;;;     service の SELECT)。走っている往復は台帳(ROUND-TRIPS)に載せ、process の終わり(Python の終わりの atexit・既定のままの
;;;     SIGTERM)に読み終える(settle-round-trips): EXIT-SETTLE-SECONDS の間 往復が自ら終わるのを待ち、残った往復へ KILL QUERY を撃ち、
;;;     取り消しの答えを往復の thread が元の接続で読み終えるのを EXIT-KILL-GRACE-SECONDS の間 待つ。読み終えが始まった後の往復は
;;;     始めない(SqlUnreachable)。全体の秒は、止めの合図から KILL までの猶予の内に収まる短さに置く(往復の期限までは待たない)。
;;;     SIGTERM の受け手を据えた process(os-signal-stop-handler など)は、受け手が Program を終わらせた後の atexit で読み終える。
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
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlTransaction SqlEnsureTables SqlRows SqlFailed SqlUnreachable
                                        SqlSchemaApplied SqlColumnType SqlText SqlPlaceholder split-statement checked-params param-value
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects._offloaded_call [ThreadPerCall offloaded run-detached keep-nothing])

;; 値の Python の型 → 引数の ClickHouse の型(閉じた表・bool は int より先に引く)。None は型が分からないので Nullable(String)。
(val CLICKHOUSE-TYPES #(#(bool "Bool") #(int "Int64") #(float "Float64") #(str "String") #(bytes "String") #((type None) "Nullable(String)")))
;; 宣言の欄の型 → ClickHouse の型。
(val CLICKHOUSE-COLUMN-TYPES {SqlColumnType.INTEGER "Int64" SqlColumnType.FLOAT "Float64" SqlColumnType.TEXT "String"
                              SqlColumnType.BYTES "String" SqlColumnType.BOOLEAN "Bool" SqlColumnType.JSON "String"})
;; 答えの形と、問い合わせごとに付ける設定(期限と query_id は要求ごとの値なので clickhouse-deadline-pairs が足す)。
(val ANSWER-FORMAT "JSONCompactEachRow")
(val QUERY-SETTINGS #(#("default_format" ANSWER-FORMAT) #("output_format_json_quote_64bit_integers" "0") #("wait_end_of_query" "1")))
;; 期限を越えた問い合わせを server が断る形(既定の throw を名乗る — user の profile が break なら、期限で切った途中の行を黙って返す)。
(val TIMEOUT-OVERFLOW-MODE "throw")
;; 待ちの余白(頭の註): client が答えの始まりを待つ秒 = 期限 + この秒。server は期限を処理の区切りの間でしか読まないので、期限で断った答えは
;; 期限より少し後に届く — その遅れをこの秒の内に収める。宣言はこの 1 か所(使い手ごとに変えない)。
(val WAIT-MARGIN-SECONDS 15.0)
;; 待ちを越えた後(頭の註)、KILL QUERY の往復を待つ秒と、元の接続で取り消しの答えを読む秒(それぞれ)。
(val KILL-GRACE-SECONDS 15.0)
;; process の終わりの読み終え(頭の註)で、走っている往復が自ら終わるのを待つ秒と、その後 KILL QUERY の往復と取り消しの答えの読みに
;; それぞれ許す秒。和(6 秒)は、doeff-cluster の worker が job の process group へ止めの合図を送ってから KILL するまでの猶予(shim の
;; 8.5 秒 = --stop-grace 10 秒 − 掃除の余裕 1.5 秒)の内に置く — 越えると読み終える前に KILL で接続が切れ、同じ落ちになる。
(val EXIT-SETTLE-SECONDS 2.0)
(val EXIT-KILL-GRACE-SECONDS 2.0)
;; client が名乗る User-Agent(server の query_log の http_user_agent — この client の要求を他の HTTP の client と見分ける)。
(val USER-AGENT "doeff-clickhouse-http-sql")
;; query_id の形(client が作る uuid4 — KILL QUERY の文へ直に書くので、この形の外の値は断る)。
(val QUERY-ID-SHAPE (re.compile "[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"))
;; ClickHouse に無い機能(transaction・一意の索引)を断る時の SQLSTATE(feature_not_supported)。
(val NOT-SUPPORTED-SQLSTATE "0A000")
;; HTTP の往復を回す Executor(頭の註 — 呼び 1 つに thread 1 本)。
(val CLICKHOUSE-THREADS (ThreadPerCall))
;; 引数の値の escaped の形で escape する byte(ClickHouse の TabSeparated と同じ)。
(val ESCAPES #(#(b"\\" b"\\\\") #(b"\t" b"\\t") #(b"\n" b"\\n") #(b"\r" b"\\r")))


(defclass [(dataclass :frozen True :kw-only True)] ClickHouseDatabase []
  "ClickHouse の database の宣言(name = 業務が SqlQuery に書く名・url = HTTP の口(http://host:8123)・database = ClickHouse の中の
   database・user / password = 資格 — password は repr に出さない・deadline = 1 要求の server 側の期限の秒 — max_execution_time として
   要求ごとに送る・0 より大きい有限の値)。client の待ちは宣言に持たず期限から導く(頭の註・clickhouse-waits)。"
  #^ str name
  #^ str url
  #^ str database
  #^ str user
  #^ str password
  (setv password (field :repr False))
  #^ float deadline
  (setv deadline 30.0)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass が作る時に呼ぶ検め(Program を返すと実行されない)
    ;; 0 は ClickHouse の「期限なし」— server が断らないので、client の待ちが先に尽きて問い合わせを置いて離れる組になる。
    (when (or (isinstance self.deadline bool) (not (isinstance self.deadline #(int float))) (not (< 0 self.deadline math.inf)))
      (raise (ValueError f"ClickHouseDatabase.deadline must be a finite number of seconds > 0, got {self.deadline !r}")))))


(defrecord ClickHouseWaits
  "要求 1 つの待ちの組(頭の註): deadline = server へ max_execution_time として送る秒・wait = client が答えの始まりを待つ秒(期限より
   長い — 期限で断った答えを待ちの内に受ける)・grace = 待ちを越えた後、KILL QUERY の往復と取り消しの答えの読みにそれぞれ許す秒。
   待ちが期限以下の組は作れない(作る時に ValueError)。"
  (#^ float deadline)
  (#^ float wait)
  (#^ float grace)
  (defn #^ None __post-init__ [self]  ; defk にできない: dataclass が作る時に呼ぶ検め(Program を返すと実行されない)
    (when (not (< 0 self.deadline self.wait math.inf))
      (raise (ValueError f"ClickHouseWaits.wait must outlast the deadline, got deadline {self.deadline !r} and wait {self.wait !r}")))
    (when (not (< 0 self.grace math.inf))
      (raise (ValueError f"ClickHouseWaits.grace must be a finite number of seconds > 0, got {self.grace !r}")))))


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
  "ClickHouse へ送る HTTP POST 1 本(url = 設定と引数を載せた URL・body = 本文・headers = 資格の頭・query-id = server の問い合わせの名
   — URL の query_id と同じ値・waits = 待ちの組 — URL の max_execution_time と同じ期限)。"
  (#^ str url)
  (#^ bytes body)
  (#^ (get tuple #(ClickHouseHeader ...)) headers)
  (#^ str query-id)
  (#^ ClickHouseWaits waits))


(defrecord ClickHouseKill
  "待ちを越えた要求の問い合わせを止める KILL QUERY の HTTP POST 1 本(url = 設定を載せない口・body = 文・headers = 元の要求と同じ資格の頭)。"
  (#^ str url)
  (#^ bytes body)
  (#^ (get tuple #(ClickHouseHeader ...)) headers))


(defrecord ClickHouseResponse
  "ClickHouse の HTTP の答え(status・body・exception-code = X-ClickHouse-Exception-Code・summary = X-ClickHouse-Summary)。"
  (#^ int status)
  (#^ bytes body)
  (#^ (| str None) exception-code)
  (#^ (| str None) summary))


(defclass RoundTrips []
  "走っている HTTP の往復の台帳(頭の註 — process の終わりに読み終えるため)。running = query_id → #(要求 終わりの印)・closing = 終わりの
   読み終えが始まった印(その後の往復は台帳に載せない = 始めない)。往復の thread と終わりの手順の間で錠を取って触る。資源の係なので値の型では
   ない。"

  (deff __init__ [self]  ; defk にできない: 資源の class の初期化
    {:pre [(: self RoundTrips)] :post [(: % "None")]}
    "空の台帳で始めるため。"
    (setv self.lock (threading.Lock)
          self.running {}
          self.closing False))

  (deff enter [self request]  ; defk にできない: 往復の thread と終わりの手順が共に触る台帳の口(VM の外の資源)
    {:pre [(: self RoundTrips) (: request ClickHouseRequest)] :post [(: % (| threading.Event None))]}
    "往復を始める前に台帳へ載せ、往復の終わりに立てる印を受けるため(読み終えが始まった後は載せず None — 呼び手は往復を始めない)。"
    (with [_ self.lock]
      (if self.closing
          None
          (do (setv done (threading.Event))
              (setv (get self.running request.query-id) #(request done))
              done))))

  (deff leave [self request done]  ; defk にできない: 往復の thread と終わりの手順が共に触る台帳の口(VM の外の資源)
    {:pre [(: self RoundTrips) (: request ClickHouseRequest) (: done threading.Event)] :post [(: % "None")]}
    "接続を閉じた往復を台帳から外し、終わりの印を立てるため。"
    (with [_ self.lock]
      (.pop self.running request.query-id None))
    (.set done)
    None)

  (deff close [self]  ; defk にできない: 往復の thread と終わりの手順が共に触る台帳の口(VM の外の資源)
    {:pre [(: self RoundTrips)] :post [(: % tuple)]}
    "読み終えの始まりに、それより後の往復を断り、走っている往復(要求と終わりの印の組)を答えるため。"
    (with [_ self.lock]
      (setv self.closing True)
      (tuple (.values self.running)))))


;; process の往復の台帳(頭の註)。
(val ROUND-TRIPS (RoundTrips))


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


(defk clickhouse-waits [database]
  {:pre [(: database ClickHouseDatabase)] :post [(: % ClickHouseWaits)]
   :tags {:context "sql" :role "foundation"}}
  "database の期限から要求の待ちの組を導くため(頭の註 — 待ち = 期限 + WAIT-MARGIN-SECONDS・猶予 = KILL-GRACE-SECONDS)。"
  (ClickHouseWaits :deadline (float database.deadline) :wait (+ database.deadline WAIT-MARGIN-SECONDS) :grace KILL-GRACE-SECONDS))


(defk clickhouse-seconds-text [seconds]
  {:pre [(: seconds float)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "秒を URL の設定の値にするため(整数の秒は小数点なし — 30.0 は 30)。"
  (if (.is-integer seconds) (str (int seconds)) (repr seconds)))


(defk clickhouse-deadline-pairs [waits query-id]
  {:pre [(: waits ClickHouseWaits) (: query-id str)] :post [(: % list)]
   :tags {:context "sql" :role "foundation"}}
  "要求ごとに URL へ足す設定(server 側の期限・期限を越えたら断る形・問い合わせの名)を作るため(頭の註)。"
  [#("max_execution_time" (! (clickhouse-seconds-text waits.deadline))) #("timeout_overflow_mode" TIMEOUT-OVERFLOW-MODE)
   #("query_id" query-id)])


(defk new-query-id []
  {:pre [] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "要求 1 つの問い合わせの名を作るため(uuid4 — 待ちを越えた時に KILL QUERY で名指す)。"
  (str (uuid.uuid4)))


(defk clickhouse-query-request [database statement params]
  {:pre [(: database ClickHouseDatabase) (: statement str) (: params tuple)] :post [(: % ClickHouseRequest)]
   :tags {:context "sql" :role "foundation"}}
  "SqlQuery 1 つを HTTP POST 1 本にするため(文は本文・設定と引数は URL — 期限と query_id は要求ごと)。"
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
  "SqlInsertRows を `INSERT … FORMAT JSONCompactEachRow` の HTTP POST 1 本にするため(JSON の行の本文 — bytes の値は ValueError・
   期限と query_id は要求ごと)。"
  (<- (checked-rows columns rows))
  (for [row rows]
    (when (any (gfor value row (isinstance value bytes)))
      (raise (ValueError (.format "ClickHouse の JSON の投入は bytes の値を運べない(表 {})" table)))))
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
  "要求の問い合わせを止める KILL QUERY の POST を作るため(同じ口と資格・ASYNC — 止めの合図だけを送り、取り消しの答えは元の接続で読む)。
   query_id は client が作った uuid4 なので文へ直に書く(形の外の値は ValueError)。"
  (when (is (.fullmatch QUERY-ID-SHAPE request.query-id) None)
    (raise (ValueError (.format "KILL QUERY に書けない query_id {!r}" request.query-id))))
  (val target (urllib.parse.urlsplit request.url))
  (ClickHouseKill :url (urllib.parse.urlunsplit #(target.scheme target.netloc target.path "" ""))
                  :body (.encode (.format "KILL QUERY WHERE query_id = '{}' ASYNC" request.query-id) "ascii")
                  :headers request.headers))


(defk clickhouse-rows [body]
  {:pre [(: body bytes)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "JSONCompactEachRow の答えの本文を行の tuple へ写すため(値は SqlValue へ正規化 — 配列・Map の欄は TypeError)。
   行の区切りは \\n だけ — splitlines は値に生で来る U+0085・U+2028・U+2029 でも切る。"
  (! (normalized-rows (lfor line (.split (.decode body "utf-8") "\n") :if (.strip line) (json.loads line)))))


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


(defk clickhouse-transaction-refusal [database]
  {:pre [(: database str)] :post [(: % SqlFailed)]
   :tags {:context "sql" :role "foundation"}}
  "SqlTransaction への断り(頭の註 — 本物の答え手と模擬 clickhouse_mock_sql が同じ値を返す 1 か所)。"
  (SqlFailed :sqlstate NOT-SUPPORTED-SQLSTATE
             :reason (.format "ClickHouse の database {!r} は transaction を持たない(SqlTransaction を断る)" database)))


(defk clickhouse-connection [url seconds]
  {:pre [(: url str) (: seconds float)] :post [(: % http.client.HTTPConnection)]
   :tags {:context "sql" :role "foundation"}}
  "URL の口への HTTP の接続(まだ繋がない)を作るため(https なら TLS・socket の 1 回の読み書きの待ちは seconds 秒)。"
  (val target (urllib.parse.urlsplit url))
  (if (= target.scheme "https")
      (http.client.HTTPSConnection target.hostname target.port :timeout seconds)
      (http.client.HTTPConnection target.hostname target.port :timeout seconds)))


(defk clickhouse-path [url]
  {:pre [(: url str)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "URL から HTTP の要求の行に書く path と設定の部分を取るため。"
  (val target (urllib.parse.urlsplit url))
  (urllib.parse.urlunsplit #("" "" (or target.path "/") target.query "")))


(defk clickhouse-post [connection url body headers]
  {:pre [(: connection http.client.HTTPConnection) (: url str) (: body bytes) (: headers tuple)] :post [(: % (type None))]
   :tags {:context "sql" :role "foundation"}}
  "繋いだ接続へ POST の頭と本文を送るため(答えは読まない — 読むのは clickhouse-read)。"
  (.request connection "POST" (! (clickhouse-path url)) :body body
            :headers (| (dfor h headers h.name h.value) {"User-Agent" USER-AGENT}))
  None)


(defk answer-started [connection seconds]
  {:pre [(: connection http.client.HTTPConnection) (: seconds float)] :post [(: % bool)]
   :tags {:context "sql" :role "foundation"}}
  "送った要求の答えの byte が届いたか(届くか閉じられるまで最長 seconds 秒待つ)を、答えを読まずに知るため(頭の註の待ち)。"
  (if (is connection.sock None)
      False
      (with [selector (selectors.DefaultSelector)]
        (.register selector connection.sock selectors.EVENT-READ)
        (bool (.select selector seconds)))))


(defk clickhouse-read [connection]
  {:pre [(: connection http.client.HTTPConnection)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "始まった答えを頭と本文まで読み切るため(読みの 1 回ごとの待ちは接続の socket の秒・途中で切れたら SqlUnreachable)。"
  (try
    (with [answer (.getresponse connection)]
      (ClickHouseResponse :status answer.status :body (.read answer)
                          :exception-code (.getheader answer "X-ClickHouse-Exception-Code")
                          :summary (.getheader answer "X-ClickHouse-Summary")))
    (except [error #(OSError http.client.HTTPException)]
      (SqlUnreachable :reason (.format "答えの読みが切れた: {!r}" error)))))


(defk clickhouse-kill [kill seconds]
  {:pre [(: kill ClickHouseKill) (: seconds float)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "KILL QUERY の POST 1 本を別の接続で送り、その答えを受けるため(ASYNC の KILL はすぐ答える — 最長 seconds 秒)。"
  (<- connection (clickhouse-connection kill.url seconds))
  (try
    (try
      (.connect connection)
      (<- (clickhouse-post connection kill.url kill.body kill.headers))
      (except [error OSError]
        (return (SqlUnreachable :reason (.format "KILL QUERY を送れない: {!r}" error)))))
    (<- answer (clickhouse-read connection))
    answer
    (finally (.close connection))))


(defk clickhouse-abandon [connection request why]
  {:pre [(: connection http.client.HTTPConnection) (: request ClickHouseRequest) (: why str)]
   :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "待ちを越えた要求から、server の問い合わせを止めてから離れるため(頭の註): 別の接続で KILL QUERY を撃ち、元の接続で取り消しの答えを
   猶予の秒だけ待って読む。読めればその答え(取り消しの例外 — 呼び手が SqlFailed へ写す。KILL の前に終わった問い合わせなら、その答え)、
   読めなければ SqlUnreachable(理由に KILL の結末)。元の接続を閉じるのは呼び手。"
  (<- kill (clickhouse-kill-request request))
  (<- killed (clickhouse-kill kill request.waits.grace))
  (val kill-outcome (match killed
                      (SqlUnreachable :reason reason) reason
                      (ClickHouseResponse :status 200) "KILL QUERY は受けられた"
                      (ClickHouseResponse :status status :body body)
                        (.format "KILL QUERY が断られた(HTTP {}: {})" status (.strip (.decode body "utf-8" "replace")))))
  (when (is-not connection.sock None)
    (.settimeout connection.sock request.waits.grace))
  (<- started (answer-started connection request.waits.grace))
  (when started
    (<- answer (clickhouse-read connection))
    (when (isinstance answer ClickHouseResponse)
      (return answer)))
  (SqlUnreachable :reason (.format "{}(query_id {})— 取り消しの答えが {} 秒の内に届かない・{}" why request.query-id
                                   (! (clickhouse-seconds-text request.waits.grace)) kill-outcome)))


(defk clickhouse-send [request]
  {:pre [(: request ClickHouseRequest)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "HTTP POST 1 本を送って答えを受けるため(頭の註の待ちの約束): 答えの始まりを waits.wait 秒待ち、越えたら clickhouse-abandon で
   問い合わせを止めてから離れる。繋げなければ(server で何も走っていない)そのまま SqlUnreachable。往復は接続を閉じるまで台帳
   ROUND-TRIPS に載り、process の終わりの読み終えが始まった後は始めない(頭の註)。"
  (val done (.enter ROUND-TRIPS request))
  (when (is done None)
    (return (SqlUnreachable :reason (.format "process が終わる所なので往復を始めない(query_id {})" request.query-id))))
  (try
    (<- connection (clickhouse-connection request.url request.waits.wait))
    (try
      (try
        (.connect connection)
        (except [error OSError]
          (return (SqlUnreachable :reason (.format "接続できない: {!r}" error)))))
      (try
        (<- (clickhouse-post connection request.url request.body request.headers))
        (except [error OSError]
          (<- broken (clickhouse-abandon connection request (.format "要求を送る途中で切れた: {!r}" error)))
          (return broken)))
      (<- started (answer-started connection request.waits.wait))
      (if started
          (do (<- answer (clickhouse-read connection))
              answer)
          (do (<- abandoned (clickhouse-abandon connection request
                                                (.format "待ち {} 秒の内に答えが始まらない" (! (clickhouse-seconds-text request.waits.wait)))))
              abandoned))
      (finally (.close connection)))
    (finally (.leave ROUND-TRIPS request done))))


(defk clickhouse-exit-kill [request grace]
  {:pre [(: request ClickHouseRequest) (: grace float)] :post [(: % (| ClickHouseResponse SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "process の終わりの読み終え(頭の註)で、自ら終わらなかった往復の問い合わせを KILL QUERY で止めるため(取り消しの答えは往復の thread が
   元の接続で読む)。"
  (<- kill (clickhouse-kill-request request))
  (<- answer (clickhouse-kill kill grace))
  answer)


(deff submit-exit-kill [request grace]  ; defk にできない: process の終わり(atexit・信号の受け手)に VM の外から呼ぶ入口の部品
  {:pre [(: request ClickHouseRequest) (: grace float)] :post [(: % Future)]
   :tags {:context "sql" :role "foundation"}}
  "process の終わりの読み終えで、往復 1 つの KILL QUERY を CLICKHOUSE-THREADS の thread で撃ち、他の往復の KILL と並べるため。"
  (.submit CLICKHOUSE-THREADS (fn [] (run-detached (clickhouse-exit-kill request grace)))))


(deff settle-round-trips [settle-seconds grace]  ; defk にできない: process の終わり(atexit・信号の受け手)に VM の外から呼ぶ入口
  {:pre [(: settle-seconds float) (: grace float)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "process が終わる前に、走っている往復を読み終えるため(頭の註): 後の往復を断り、settle-seconds の間 往復が自ら終わるのを待ち、残った往復へ
   KILL QUERY を撃ち(1 本に最長 grace 秒・別々の thread で並べて)、取り消しの答えを往復の thread が読み終えるのを KILL から grace 秒の
   後まで待つ。答え = それでも読み終えなかった往復の要求(process はそれを置いて終わる)。全体は最長 settle-seconds + 2 × grace 秒。"
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


(deff settle-at-exit []  ; defk にできない: Python の終わり(atexit)が呼ぶ素の callback
  {:pre [] :post [(: % "None")]
   :tags {:context "sql" :role "foundation"}}
  "Program が終わって Python が終わる時に、走っている往復を読み終えるため(頭の註・daemon の往復の thread は待たれずに消える)。"
  (settle-round-trips EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS)
  None)


(defclass SettleThenTerminate []
  "既定のまま(受け手の無い)の SIGTERM で終わる process が、走っている往復を読み終えてから同じ SIGTERM で終わるための受け手(頭の註)。
   受け手を据えた process(os-signal-stop-handler など)は受け手を置き換えるので、これは働かない(その時は atexit が読み終える)。資源の係
   なので値の型ではない。"

  (deff __init__ [self]  ; defk にできない: 資源の class の初期化
    {:pre [(: self SettleThenTerminate)] :post [(: % "None")]}
    "まだ信号を受けていない印で始めるため。"
    (setv self.received False))

  (deff receive [self number frame]  ; defk にできない: Python の信号の受け手(main の thread の素の callback)
    {:pre [(: self SettleThenTerminate) (: number int) (: frame (| types.FrameType None))] :post [(: % "None")]}
    "SIGTERM を受けたら往復を読み終え、既定の受け手に戻して同じ信号で終わるため(読み終えの間に届いた 2 つ目の SIGTERM は読み終えを
     待たせるだけで、先に終わらせない)。"
    (when (not self.received)
      (setv self.received True)
      (settle-round-trips EXIT-SETTLE-SECONDS EXIT-KILL-GRACE-SECONDS)
      (signal.signal number signal.SIG-DFL)
      (os.kill (os.getpid) number))
    None))


(deff install-exit-settle []  ; defk にできない: module を読む時に 1 度だけ Python の終わりと信号の受け手を据える入口
  {:pre [] :post [(: % "None")]
   :tags {:context "sql" :role "foundation"}}
  "process の終わりの 2 つの道(Python の終わり・既定のままの SIGTERM)で、走っている往復を読み終えるため(頭の註)。SIGTERM の受け手は、
   信号を据えられる main の thread で、受け手がまだ既定のままの時だけ据える(据えた受け手を持つ process の振る舞いは変えない)。"
  (atexit.register settle-at-exit)
  (when (and (is (threading.current-thread) (threading.main-thread)) (= (signal.getsignal signal.SIGTERM) signal.SIG-DFL))
    (signal.signal signal.SIGTERM (. (SettleThenTerminate) receive)))
  None)


(install-exit-settle)


(defk clickhouse-answer [request insert]
  {:pre [(: request ClickHouseRequest) (: insert bool)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "要求を送り、答えを SqlRows か失敗の値にするため(insert = 投入 — 行を読まず written_rows を数える)。"
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
  "表の宣言を DDL に描いて順に流すため(最初の失敗で止める)。"
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
  "HTTP の往復を持つ Program を scheduler の thread の外(CLICKHOUSE-THREADS の新しい VM)で値にし、撃った task だけが待つため(頭の註)。"
  (<- answer (offloaded CLICKHOUSE-THREADS (fn [] (run-detached program)) keep-nothing))
  answer)


(defhandler clickhouse-http-sql-handler [#^ tuple databases]
  ;; 引数に残す理由: database の宣言(URL と資格)は組み立ての側が渡す値で、答える database の名で PostgreSQL の答え手と同じ組に並べ分ける。
  "本物の ClickHouse の答え手(頭の註)。databases = ClickHouseDatabase の tuple(宣言した名にだけ答え、他の名は外側へ回す)。"
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
    (<- refusal (clickhouse-transaction-refusal database))
    (resume refusal)))
