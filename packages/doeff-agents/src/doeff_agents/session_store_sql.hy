;;; agent runtime の session の行を SQL の表に置くための純粋な判断(agora-redesign #608)。
;;;
;;; 既知の形: repository の中の「何を書くか」と「実行」を分ける。この module は
;;; 表の定義(DDL)・書きと読みの文・行と `AgentSessionSnapshot` の相互の写しだけを持ち、
;;; 接続には触らない。文を流すのは `doeff_agents.handlers.session_store` の
;;; `SqlAgentSessionRepository` ちょうど 1 つ(ADR-DOE-AGENTS-013 R4: 判断は driver の
;;; file に純粋な関数として残し、I/O は handlers の側)。
;;;
;;; 方言は 2 つ: PostgreSQL(クラスタの配備・psycopg 3 の接続)と SQLite(手元・標準の
;;; sqlite3)。違いは値の置き場所の印と、時刻と JSON の列の型だけで、文の形は同じ
;;; (`INSERT … ON CONFLICT … DO UPDATE` は PostgreSQL 9.5+ と SQLite 3.24+ の両方が持つ)。
;;;
;;; 語彙: library の語だけを使う。呼び手の識別子(`caller_ref`)・最後の手番
;;; (`last_turn`)・transcript の所在(`transcript_ref`)は library にとって不透明な値。

(require doeff-hy.macros [deff])

(import dataclasses [dataclass fields])
(import datetime [datetime timezone])
(import json)
(import re)
(import pathlib [Path])

(import doeff_agents.adapters.base [AgentSessionLifecycle AgentType])
(import doeff_agents.monitor [SessionStatus])
(import doeff_agents.effects [AgentSessionQuery AgentSessionSnapshot TranscriptRef TurnRef])


;; ---------------------------------------------------------------------------
;; 値
;; ---------------------------------------------------------------------------

(defclass [(dataclass :frozen True :kw-only True)] SqlDialect []
  "SQL の方言の違い。文の形は共通で、値の置き場所の印と列の型だけが違う。"
  #^ str name
  #^ str placeholder
  #^ str time-type
  #^ str json-type)

(setv POSTGRES (SqlDialect :name "postgres" :placeholder "%s"
                           :time-type "timestamptz" :json-type "jsonb"))
(setv SQLITE (SqlDialect :name "sqlite" :placeholder "?"
                         :time-type "text" :json-type "text"))

(defclass [(dataclass :frozen True :kw-only True)] SqlStatement []
  "流す文 1 つと、その値の並び(置き場所の印の順)。"
  #^ str text
  #^ tuple params)

;; 表の既定の名。agora の配備では agora の状態の正本の PostgreSQL に別の表として置く
;; (operator 8.1.5 の 5f)。sessionhost の SQLite の表 `agent_sessions` とは別物。
(setv DEFAULT-TABLE "agent_runtime_sessions")

(setv _TABLE-NAME-PATTERN (re.compile "^[a-z_][a-z0-9_]{0,62}$"))


;; ---------------------------------------------------------------------------
;; 列(順番がそのまま INSERT と SELECT の並び)
;; ---------------------------------------------------------------------------

;; 列の種類: "text" / "integer" / "time" / "json"。NOT NULL の列は印 True。
(setv COLUMNS
  #(#("session_id" "text" True)
    #("session_name" "text" True)
    #("agent_type" "text" True)
    #("work_dir" "text" True)
    #("status" "text" True)
    #("lifecycle" "text" True)
    #("backend_kind" "text" True)
    #("backend_ref" "json" True)
    #("started_at" "time" True)
    #("last_observed_at" "time" False)
    #("finished_at" "time" False)
    #("cleaned_at" "time" False)
    #("output_snippet" "text" False)
    #("caller_ref" "text" False)
    #("node" "text" False)
    #("last_turn_id" "text" False)
    #("last_turn_attempt" "integer" False)
    #("transcript_node" "text" False)
    #("transcript_path" "text" False)
    #("last_event" "text" True)
    #("last_event_details" "json" True)))

(setv COLUMN-NAMES (tuple (gfor column COLUMNS (get column 0))))
(setv COLUMN-LIST (.join ", " COLUMN-NAMES))

;; 索引を張る列(呼び手の識別子で引く・機体で引く・状態で引く)。
(setv INDEXED-COLUMNS #("caller_ref" "node" "status"))

;; `AgentSessionQuery` の欄 → 列と、値を列の綴りへ写す関数。
;; 欄が増えて写しが無いと、SQL の答えが黙って広がる。import の時に落とす。
(defn _enum-value [value] value.value)
(defn _same [value] value)

(setv QUERY-COLUMNS
  {"status" #("status" _enum-value)
   "agent_type" #("agent_type" _enum-value)
   "backend_kind" #("backend_kind" _same)
   "lifecycle" #("lifecycle" _enum-value)
   "caller_ref" #("caller_ref" _same)
   "node" #("node" _same)})

(setv _UNMAPPED-QUERY-FIELDS
  (- (sfor field (fields AgentSessionQuery) field.name) (set QUERY-COLUMNS)))
(when _UNMAPPED-QUERY-FIELDS
  (raise (RuntimeError
           f"AgentSessionQuery の欄に SQL の列の写しが無い: {(sorted _UNMAPPED-QUERY-FIELDS)}")))


;; ---------------------------------------------------------------------------
;; 表の定義
;; ---------------------------------------------------------------------------

(deff checked-table-name [table]
  {:pre [(: table str)]
   :post [(: % str)]}
  "表の名を検める。文に直に埋めるので、小文字・数字・下線だけを通す。"
  (when (not (.match _TABLE-NAME-PATTERN table))
    (raise (ValueError f"表の名に使えない綴り: {table !r}")))
  table)


(defn _column-type [dialect kind]
  (cond (= kind "time") dialect.time-type
        (= kind "json") dialect.json-type
        True kind))


(defn _column-definition [dialect column]
  (setv #(name kind required) column)
  (setv suffix (cond (= name "session_id") " PRIMARY KEY"
                     required " NOT NULL"
                     True ""))
  f"{name} {(_column-type dialect kind)}{suffix}")


(deff session-table-ddl [dialect table]
  {:pre [(: dialect SqlDialect) (: table str)]
   :post [(: % tuple)]}
  "表と索引を作る文の並び。何度流しても同じ(IF NOT EXISTS)。"
  (setv name (checked-table-name table))
  (setv columns (.join ", " (gfor column COLUMNS (_column-definition dialect column))))
  (+ #((SqlStatement :text f"CREATE TABLE IF NOT EXISTS {name} ({columns})" :params #()))
     (tuple (gfor column INDEXED-COLUMNS
                  (SqlStatement
                    :text f"CREATE INDEX IF NOT EXISTS {name}_{column}_idx ON {name} ({column})"
                    :params #())))))


;; ---------------------------------------------------------------------------
;; 値の写し(snapshot → 列の値)
;; ---------------------------------------------------------------------------

(deff encode-time [value]
  {:pre [(: value (| datetime None))]
   :post [(: % (| str None))]}
  "時刻を ISO 8601 の綴りへ。時差の無い時刻は置き場所ごとに意味が変わるので断る。"
  (cond (is value None) None
        (is value.tzinfo None) (raise (ValueError f"時差の無い時刻は置けない: {value !r}"))
        True (.isoformat (.astimezone value timezone.utc))))


(defn _encode-json [value]
  (json.dumps value :ensure-ascii False :sort-keys True))


(defn _placeholder [dialect kind]
  (cond (= kind "time") f"CAST({dialect.placeholder} AS {dialect.time-type})"
        (= kind "json") f"CAST({dialect.placeholder} AS {dialect.json-type})"
        True dialect.placeholder))


(deff snapshot-row-values [snapshot event-type details]
  {:pre [(: snapshot AgentSessionSnapshot) (: event-type str) (: details dict)]
   :post [(: % tuple) (= (len %) (len COLUMNS))]}
  "snapshot 1 つを列の順の値へ。"
  (setv turn snapshot.last-turn)
  (setv transcript snapshot.transcript-ref)
  #(snapshot.session-id
    snapshot.session-name
    snapshot.agent-type.value
    (str snapshot.work-dir)
    snapshot.status.value
    snapshot.lifecycle.value
    snapshot.backend-kind
    (_encode-json (dict snapshot.backend-ref))
    (encode-time snapshot.started-at)
    (encode-time snapshot.last-observed-at)
    (encode-time snapshot.finished-at)
    (encode-time snapshot.cleaned-at)
    snapshot.output-snippet
    snapshot.caller-ref
    snapshot.node
    (if (is turn None) None turn.turn-id)
    (if (is turn None) None turn.attempt)
    (if (is transcript None) None transcript.node)
    (if (is transcript None) None transcript.path)
    event-type
    (_encode-json details)))


(deff upsert-statement [dialect table snapshot event-type details]
  {:pre [(: dialect SqlDialect) (: table str) (: snapshot AgentSessionSnapshot)
         (: event-type str) (: details dict)]
   :post [(: % SqlStatement)]}
  "同じ session id の行を置き換える書き(無ければ足す)。"
  (setv name (checked-table-name table))
  (setv placeholders (.join ", " (gfor #(_ kind _) COLUMNS (_placeholder dialect kind))))
  (setv updates (.join ", " (gfor column (cut COLUMN-NAMES 1 None)
                                  f"{column} = excluded.{column}")))
  (SqlStatement
    :text (+ f"INSERT INTO {name} ({COLUMN-LIST}) VALUES ({placeholders}) "
             f"ON CONFLICT (session_id) DO UPDATE SET {updates}")
    :params (snapshot-row-values snapshot event-type details)))


;; ---------------------------------------------------------------------------
;; 読みの文
;; ---------------------------------------------------------------------------

(deff select-one-statement [dialect table session-id]
  {:pre [(: dialect SqlDialect) (: table str) (: session-id str)]
   :post [(: % SqlStatement)]}
  "session id で 1 行を読む。"
  (setv name (checked-table-name table))
  (SqlStatement
    :text f"SELECT {COLUMN-LIST} FROM {name} WHERE session_id = {dialect.placeholder}"
    :params #(session-id)))


(defn _query-conditions [query]
  "query の決まった欄ごとに (列, 値)。"
  (if (is query None)
      #()
      (tuple (gfor field (fields AgentSessionQuery)
                   :setv expected (getattr query field.name)
                   :if (is-not expected None)
                   :setv mapping (get QUERY-COLUMNS field.name)
                   #((get mapping 0) ((get mapping 1) expected))))))


(deff select-many-statement [dialect table query]
  {:pre [(: dialect SqlDialect) (: table str) (: query (| AgentSessionQuery None))]
   :post [(: % SqlStatement)]}
  "query に合う行を session id の順に読む。決めていない欄は条件にしない。"
  (setv name (checked-table-name table))
  (setv conditions (_query-conditions query))
  (setv where (if conditions
                  (+ " WHERE " (.join " AND " (gfor #(column _) conditions
                                                    f"{column} = {dialect.placeholder}")))
                  ""))
  (SqlStatement
    :text f"SELECT {COLUMN-LIST} FROM {name}{where} ORDER BY session_id"
    :params (tuple (gfor #(_ value) conditions value))))


;; ---------------------------------------------------------------------------
;; 値の写し(列の値 → snapshot)
;; ---------------------------------------------------------------------------

(deff decode-time [value]
  {:pre [(: value (| datetime str None))]
   :post [(: % (| datetime None))]}
  "driver が返す時刻(PostgreSQL は datetime・SQLite は綴り)を UTC の datetime へ。"
  (cond (is value None) None
        (isinstance value datetime) (.astimezone value timezone.utc)
        True (.astimezone (datetime.fromisoformat value) timezone.utc)))


(deff decode-required-time [value]
  {:pre [(: value (| datetime str))]
   :post [(: % datetime)]}
  "NOT NULL の時刻の列を読む。"
  (setv decoded (decode-time value))
  (when (is decoded None)
    (raise (ValueError "NOT NULL の時刻の列が空")))
  decoded)


(defn _decode-json [value]
  (if (isinstance value str) (json.loads value) value))


(defn _optional-turn [turn-id attempt]
  (if (is turn-id None) None (TurnRef :turn-id turn-id :attempt (int attempt))))


(defn _optional-transcript [node path]
  (if (is node None) None (TranscriptRef :node node :path path)))


(deff row-to-snapshot [row]
  {:pre [(: row (| tuple list)) (= (len row) (len COLUMNS))]
   :post [(: % AgentSessionSnapshot)]}
  "列の順の 1 行を snapshot へ。"
  (setv values (dict (zip COLUMN-NAMES row)))
  (AgentSessionSnapshot
    :session-id (get values "session_id")
    :session-name (get values "session_name")
    :agent-type (AgentType (get values "agent_type"))
    :work-dir (Path (get values "work_dir"))
    :status (SessionStatus (get values "status"))
    :lifecycle (AgentSessionLifecycle (get values "lifecycle"))
    :backend-kind (get values "backend_kind")
    :backend-ref (dict (_decode-json (get values "backend_ref")))
    :started-at (decode-required-time (get values "started_at"))
    :last-observed-at (decode-time (get values "last_observed_at"))
    :finished-at (decode-time (get values "finished_at"))
    :cleaned-at (decode-time (get values "cleaned_at"))
    :output-snippet (get values "output_snippet")
    :caller-ref (get values "caller_ref")
    :node (get values "node")
    :last-turn (_optional-turn (get values "last_turn_id") (get values "last_turn_attempt"))
    :transcript-ref (_optional-transcript (get values "transcript_node")
                                          (get values "transcript_path"))))
