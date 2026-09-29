;;; PostgreSQL の handler の文(純粋 — 接続に触らない)。流すのは pg.hy の Program ちょうど 1 つで、doeff の汎用の SQL の effect
;;; (SqlQuery・SqlTransaction — doeff_core_effects.sql_effects)に載せる。文は中立の記法(引数 = `:name`・値 = SqlParam)で書き、
;;; PostgreSQL の方言への書き換えは答え手(postgres-sql-handler / pooled-postgres-sql-handler)が持つ(#880 U5)。
;;;
;;; 表の形(列の名と型)は別の置き場(状態の行の表 state_rows・追記の表 append_rows)と同じ形:
;;;   state_rows   PK = (ledger, key)・payload = 値の JSON の綴り・version は書くたびに 1 増える・epoch = 書いた時の置き場の版
;;;   append_rows  seq = bigserial・ledger = 追記の列の名・payload = {"idempotencyKey" "writer" "body"} の JSON の綴り
;;; 足した物(この package の意味に要る物だけ):
;;;   row_changes  変更の列(WatchChanges が読む)— seq = bigserial・payload NULL = 行が消えた
;;;   store_epoch  置き場の版と、変更の列を忘れた位置(floor)の 1 行
;;;   冪等キーの一意の索引(append_rows の ledger × payload の idempotencyKey)と、宣言した索引の欄の式の索引
;;; 表の名は接頭辞つき(既定 records_)— 同じ database に在る別の置き場の同名の表と混ざらない。
;;; DDL は SqlEnsureTables の宣言に書き換えない(式の索引と bigserial を宣言で表せない)— 旧い版と同じ字面のまま流す
;;; (検 test_pg_sql.hy が旧い版の字面と比べる)。どれも IF NOT EXISTS / ON CONFLICT DO NOTHING なので、字面が変わっても
;;; 旧い版へ戻せる(表の形は変わらない)。
;;;
;;; 書きは置き場ごとの advisory lock 1 つで直列にする: bigserial の番号は commit の順と揃わない(番号 5 を取った書きより先に
;;; 番号 6 の書きが commit すると、6 まで読んだ読み手は 5 を永久に取りこぼす)。番号を取ってから commit するまでを 1 つの lock の中に
;;; 置くと、読み手が見た最大の番号より小さい番号は全部 commit 済み、が成り立つ(WatchChanges の「ちょうど 1 回」の土台)。
;;; 代わりに書きの速さは置き場 1 つで 1 本に縛られる。錠は SqlTransaction の lock-key(答え手が pg_advisory_xact_lock(hashtext(鍵)) を
;;; 流す)で、鍵の値は旧い版の錠の文の引数と同じ字面(接頭辞 + "records-writer" / 接頭辞 + "records-migrate" — 区切りなし)。
;;; 鍵の字面を変えると、旧い版と新しい版が同じ置き場に重なった時に別々の錠を取り、変更の列の番号を取りこぼす(戻せない)。
;;;
;;; 配列の引数は持たない(SqlValue の閉じた集合に配列は無い): `= ANY(配列)` は `IN (:t0, :t1, …)` に展げる。空の組は文にできない
;;; (`IN ()` は構文の誤り)ので、in-list は空の組を断り、呼び手(pg.hy)が空の組なら文を流さない枝を持つ。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import re)
(import doeff_core_effects.sql_effects [SqlParam])
(import doeff_records.values [RecordsSchema])

(val MODULE-TAGS {:context "records" :role "foundation"})
(val PREFIX-PATTERN (re.compile "^[a-z_][a-z0-9_]{0,30}$"))
(val DEFAULT-PREFIX "records_")
;; 錠の鍵の接尾(接頭辞の後ろに区切りなしで続ける — 旧い版の錠の文の引数と同じ字面)。
(val WRITER-LOCK-SUFFIX "records-writer")
(val MIGRATE-LOCK-SUFFIX "records-migrate")


(defrecord Statement
  "流す文 1 つ(text = 中立の記法の文・params = 文の `:name` に当たる SqlParam の並び)。"
  (#^ str text)
  (#^ (get tuple #(SqlParam ...)) params))


(defrecord InList
  "`IN (…)` に展げた組(text = `:t0, :t1, …`・params = その SqlParam の並び)。"
  (#^ str text)
  (#^ (get tuple #(SqlParam ...)) params))


(defk params-of [pairs]
  {:pre [(: pairs tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "名と値の組の並びを SqlParam の並びにするため。"
  (tuple (gfor #(name value) pairs (SqlParam :name name :value value))))


(defk checked-prefix [prefix]
  {:pre [(: prefix str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表の接頭辞が文に直に置ける形であることを確かめるため(外れは ValueError)。"
  (when (not (.match PREFIX-PATTERN prefix))
    (raise (ValueError (.format "表の接頭辞は英小文字か _ で始まる英小文字・数字・_ の 31 字まで: {!r}" prefix))))
  prefix)


(defk writer-lock-key [prefix]
  {:pre [(: prefix str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "置き場の書きの錠の鍵(SqlTransaction の lock-key)を作るため。"
  (+ prefix WRITER-LOCK-SUFFIX))


(defk migrate-lock-key [prefix]
  {:pre [(: prefix str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表の用意(移行)の錠の鍵を作るため。同じ置き場を同時に用意する別の接続・別の process を 1 本ずつにする:
   IF NOT EXISTS は同時の CREATE の競り合いを防がない(2 本とも「無い」を見て作り、遅れた方が catalog の一意の索引で UniqueViolation)。"
  (+ prefix MIGRATE-LOCK-SUFFIX))


(defk in-list [stem values]
  {:pre [(: stem str) (: values tuple) (> (len values) 0)] :post [(: % InList)]
   :tags {:context "records" :role "foundation"}}
  "値の組を `IN (…)` の中身(`:stem0, :stem1, …`)と引数に展げるため(空の組は文にできないので :pre が断る — 呼び手が枝で避ける)。"
  (val names (lfor i (range (len values)) (.format "{}{}" stem i)))
  (InList :text (.join ", " (gfor name names (+ ":" name)))
          :params (! (params-of (tuple (zip names values :strict True))))))


(defk index-name [prefix field-name]
  {:pre [(: prefix str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "宣言した索引の欄の式の索引の名を作るため(旧い版と同じ名)。"
  (+ prefix "ix_" (cut (.hexdigest (hashlib.sha1 (.encode field-name "utf-8"))) 12)))


(defk schema-statements [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表を用意する文の列(何度流しても同じ — IF NOT EXISTS / ON CONFLICT DO NOTHING)。流すのは pg.prepare-records-store だけ
   (移行の錠の transaction の中で・process ごとに 1 度)。字面は旧い版と同じ(頭の註)。"
  (val p prefix)
  (val fields (sorted (sfor decl (.values schema.tables) name decl.indexes name)))
  (var statements
    [(Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}state_rows (
           ledger text NOT NULL, key text NOT NULL, payload text NOT NULL, version bigint NOT NULL,
           updated_at bigint NOT NULL, updated_by text NOT NULL, origin_host text NOT NULL, epoch bigint NOT NULL,
           PRIMARY KEY (ledger, key))" :p p) :params #())
     (Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}append_rows (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, at bigint NOT NULL, payload text NOT NULL,
           origin_host text NOT NULL, epoch bigint NOT NULL)" :p p) :params #())
     (Statement :text (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger ON {p}append_rows (ledger, seq)" :p p) :params #())
     (Statement :text (.format "CREATE UNIQUE INDEX IF NOT EXISTS {p}append_rows_idempotency
           ON {p}append_rows (ledger, ((payload::jsonb) ->> 'idempotencyKey'))" :p p) :params #())
     (Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}row_changes (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, key text NOT NULL, version bigint NOT NULL,
           payload text, at bigint NOT NULL, epoch bigint NOT NULL)" :p p) :params #())
     (Statement :text (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_ledger ON {p}row_changes (ledger, seq)" :p p) :params #())
     (Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}store_epoch (
           id smallint PRIMARY KEY CHECK (id = 1), epoch bigint NOT NULL, floor bigint NOT NULL)" :p p) :params #())
     (Statement :text (.format "INSERT INTO {p}store_epoch (id, epoch, floor) VALUES (1, 1, 0) ON CONFLICT (id) DO NOTHING" :p p)
                :params #())])
  ;; 宣言した索引の欄ごとに式の索引 1 つ(欄の名は FIELD-NAME-PATTERN を通った英数字だけ — 文に直に置ける)。
  (for [name fields]
    (<- index (index-name p name))
    (.append statements (Statement :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows (ledger, ((payload::jsonb) -> '{f}'))"
                                                  :i index :p p :f name)
                                   :params #())))
  (tuple statements))


(defk drop-statements [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "検の後片付けの文(この接頭辞の表を消す)を作るため。"
  (tuple (gfor table ["state_rows" "append_rows" "row_changes" "store_epoch"]
               (Statement :text (.format "DROP TABLE IF EXISTS {}{}" prefix table) :params #()))))


(defk store-head-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の版・忘れた位置・変更の列の先頭の番号を 1 文 = 1 つの断面で読む文を作るため。"
  (Statement :text (.format "SELECT epoch, floor, greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       FROM {p}store_epoch WHERE id = 1" :p prefix)
             :params #()))


(defk read-row-statement [prefix table key]
  {:pre [(: prefix str) (: table str) (: key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "行 1 つを読む文を作るため。"
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows WHERE ledger = :ledger AND key = :key" :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key))))))


(defk lock-row-statement [prefix table key]
  {:pre [(: prefix str) (: table str) (: key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "書きの判定に渡す今の行を行の錠(FOR UPDATE)つきで読む文を作るため。"
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows WHERE ledger = :ledger AND key = :key FOR UPDATE"
                            :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key))))))


(defk list-rows-statement [prefix table after-key where-json limit]
  {:pre [(: prefix str) (: table str) (: after-key (| str None)) (: where-json dict) (: limit int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "鍵の綴りの順(COLLATE \"C\" = 符号点の順・鍵の綴りは ASCII だけ)の 1 頁 + 1 行(続きの有無を知るため)を読む文を作るため。
   where-json = 欄 → 値の JSON の綴り(欄の名は宣言を通った英数字)。"
  (var clauses ["ledger = :ledger"])
  (var pairs [#("ledger" table)])
  (when (is-not after-key None)
    (.append clauses "key COLLATE \"C\" > :after")
    (.append pairs #("after" after-key)))
  (for [#(i #(name encoded)) (enumerate (sorted (.items where-json)))]
    (.append clauses (.format "(payload::jsonb) -> '{}' = :w{}::jsonb" name i))
    (.append pairs #((.format "w{}" i) encoded)))
  (.append pairs #("limit" (+ limit 1)))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows WHERE {w} ORDER BY key COLLATE \"C\" LIMIT :limit"
                            :p prefix :w (.join " AND " clauses))
             :params (! (params-of (tuple pairs)))))


(defk terminal-rows-statement [prefix table state-field terminal]
  {:pre [(: prefix str) (: table str) (: state-field str) (: terminal tuple) (> (len terminal) 0)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "終端の状態の行(保持の期限の候補 — 期限の判断は admission.row-expired?)を読む文を作るため(terminal が空なら呼ばない)。"
  (<- states (in-list "t" terminal))
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows
                       WHERE ledger = :ledger AND (payload::jsonb) ->> :state_field IN ({s}) ORDER BY key COLLATE \"C\""
                            :p prefix :s states.text)
             :params (+ (! (params-of #(#("ledger" table) #("state_field" state-field)))) states.params)))


(defk upsert-row-statement [prefix table key payload version at writer origin-host epoch]
  {:pre [(: prefix str) (: table str) (: key str) (: payload str) (: version int) (: at int) (: writer str) (: origin-host str)
         (: epoch int)]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "行 1 つを書く(在れば置き換える)文を作るため。"
  (Statement :text (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       VALUES (:ledger, :key, :payload, :version, :at, :writer, :origin_host, :epoch)
                       ON CONFLICT (ledger, key) DO UPDATE SET payload = EXCLUDED.payload, version = EXCLUDED.version,
                         updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by,
                         origin_host = EXCLUDED.origin_host, epoch = EXCLUDED.epoch" :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key) #("payload" payload) #("version" version) #("at" at)
                                     #("writer" writer) #("origin_host" origin-host) #("epoch" epoch))))))


(defk delete-row-statement [prefix table key version]
  {:pre [(: prefix str) (: table str) (: key str) (: version int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "版を名指して行 1 つを消す文を作るため。"
  (Statement :text (.format "DELETE FROM {p}state_rows WHERE ledger = :ledger AND key = :key AND version = :version" :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key) #("version" version))))))


(defk append-change-statement [prefix table key version payload at epoch]
  {:pre [(: prefix str) (: table str) (: key str) (: version int) (: payload (| str None)) (: at int) (: epoch int)]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "変更の列に 1 つ積む文を作るため(payload None = 行が消えた)。"
  (Statement :text (.format "INSERT INTO {p}row_changes (ledger, key, version, payload, at, epoch)
                       VALUES (:ledger, :key, :version, :payload, :at, :epoch) RETURNING seq" :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key) #("version" version) #("payload" payload) #("at" at)
                                     #("epoch" epoch))))))


(defk changes-statement [prefix after head tables limit]
  {:pre [(: prefix str) (: after int) (: head int) (: tables tuple) (> (len tables) 0) (: limit int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "変更の列の after より後・head まで・頼んだ表の分を読む文を作るため(tables が空なら呼ばない)。"
  (<- ledgers (in-list "t" tables))
  (Statement :text (.format "SELECT seq, ledger, key, version, payload, at FROM {p}row_changes
                       WHERE seq > :after AND seq <= :head AND ledger IN ({t}) ORDER BY seq LIMIT :limit" :p prefix :t ledgers.text)
             :params (+ (! (params-of #(#("after" after) #("head" head) #("limit" limit)))) ledgers.params)))


(defk advance-epoch-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の版を 1 進め、floor を変更の列の先頭まで上げる文を作るため。"
  (Statement :text (.format "UPDATE {p}store_epoch SET epoch = epoch + 1,
                         floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       WHERE id = 1 RETURNING epoch" :p prefix)
             :params #()))


(defk prune-changes-statement [prefix before-at]
  {:pre [(: prefix str) (: before-at int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "刻が before-at 以下の変更のうち最大の番号までを変更の列から消し(刈る範囲は番号の前方の連なり — 刻が番号の順と揃わなくても
   floor の下に取り残しを作らない)、floor をそこまで上げ、#(floor 消した数) を返す文を作るため(書きの錠の中で流す —
   錠の外だと、消した後・floor を上げる前に読んだ読み手が消えた変更を黙って飛ばす)。"
  (Statement :text (.format "WITH removed AS (DELETE FROM {p}row_changes
                                        WHERE seq <= (SELECT coalesce(max(seq), 0) FROM {p}row_changes WHERE at <= :before_at)
                                        RETURNING seq),
                            raised AS (UPDATE {p}store_epoch
                                       SET floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM removed))
                                       WHERE id = 1 RETURNING floor)
                       SELECT (SELECT floor FROM raised), (SELECT count(*) FROM removed)" :p prefix)
             :params (! (params-of #(#("before_at" before-at))))))


(defk forget-changes-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "変更の列を全部忘れる文を作るため(置き場の版を進める時)。"
  (Statement :text (.format "DELETE FROM {p}row_changes" :p prefix) :params #()))


(defk find-event-statement [prefix stream idempotency-key]
  {:pre [(: prefix str) (: stream str) (: idempotency-key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "冪等キーで出来事を引く文を作るため。"
  (Statement :text (.format "SELECT seq, at, payload FROM {p}append_rows
                       WHERE ledger = :ledger AND (payload::jsonb) ->> 'idempotencyKey' = :idempotency_key" :p prefix)
             :params (! (params-of #(#("ledger" stream) #("idempotency_key" idempotency-key))))))


(defk insert-event-statement [prefix stream at payload origin-host epoch]
  {:pre [(: prefix str) (: stream str) (: at int) (: payload str) (: origin-host str) (: epoch int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "出来事を 1 つ積んで番号を返す文を作るため。"
  (Statement :text (.format "INSERT INTO {p}append_rows (ledger, at, payload, origin_host, epoch)
                       VALUES (:ledger, :at, :payload, :origin_host, :epoch)
                       RETURNING seq" :p prefix)
             :params (! (params-of #(#("ledger" stream) #("at" at) #("payload" payload) #("origin_host" origin-host) #("epoch" epoch))))))


(defk read-events-statement [prefix stream after limit]
  {:pre [(: prefix str) (: stream str) (: after int) (: limit int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "追記の列の after より後を読む文を作るため。"
  (Statement :text (.format "SELECT seq, at, payload FROM {p}append_rows WHERE ledger = :ledger AND seq > :after ORDER BY seq LIMIT :limit"
                            :p prefix)
             :params (! (params-of #(#("ledger" stream) #("after" after) #("limit" limit))))))


(defk expire-events-statement [prefix stream before-at]
  {:pre [(: prefix str) (: stream str) (: before-at int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "積んだ刻が before-at 以下の出来事を捨てる文を作るため(before-at = 今 − 保持の秒 — admission.event-expired? と同じ境界)。"
  (Statement :text (.format "DELETE FROM {p}append_rows WHERE ledger = :ledger AND at <= :before_at" :p prefix)
             :params (! (params-of #(#("ledger" stream) #("before_at" before-at))))))


(defk key-suffix-expression [alias]
  {:pre [(: alias str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "出来事 alias の冪等キーの最初の区切り(引数 :separator)より後ろ・区切りを含まないキーはキー全体(admission.retention-group-of と
   同じ組の名)の式を作るため。"
  (.format "(CASE WHEN strpos(({a}.payload::jsonb) ->> 'idempotencyKey', :separator) > 0
                 THEN substr(({a}.payload::jsonb) ->> 'idempotencyKey',
                             strpos(({a}.payload::jsonb) ->> 'idempotencyKey', :separator) + length(:separator))
                 ELSE ({a}.payload::jsonb) ->> 'idempotencyKey' END)" :a alias))


(defk expire-event-groups-statement [prefix stream before-at separator]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "組で数える列(ByKeySuffix)の刈り: 組の出来事が全部 before-at 以下に積まれた組を捨てる文を作るため(= 組の最後の出来事から保持の秒 —
   admission.event-expired? と retention-group-of と同じ境界)。"
  (Statement :text (.format "DELETE FROM {p}append_rows AS old WHERE old.ledger = :ledger AND old.at <= :before_at AND NOT EXISTS (
                         SELECT 1 FROM {p}append_rows AS young
                          WHERE young.ledger = old.ledger AND young.at > :before_at AND {young} = {old})"
                            :p prefix :young (! (key-suffix-expression "young")) :old (! (key-suffix-expression "old")))
             :params (! (params-of #(#("ledger" stream) #("before_at" before-at) #("separator" separator))))))
