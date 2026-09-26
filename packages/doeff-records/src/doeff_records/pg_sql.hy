;;; PostgreSQL の handler の文(純粋 — 接続に触らない)。流すのは pg.hy の PgRecordsHost ちょうど 1 つ。
;;;
;;; 表の形(列の名と型)は別の置き場(状態の行の表 state_rows・追記の表 append_rows)と同じ形:
;;;   state_rows   PK = (ledger, key)・payload = 値の JSON の綴り・version は書くたびに 1 増える・epoch = 書いた時の置き場の版
;;;   append_rows  seq = bigserial・ledger = 追記の列の名・payload = {"idempotencyKey" "writer" "body"} の JSON の綴り
;;; 足した物(この package の意味に要る物だけ):
;;;   row_changes  変更の列(WatchChanges が読む)— seq = bigserial・payload NULL = 行が消えた
;;;   store_epoch  置き場の版と、変更の列を忘れた位置(floor)の 1 行
;;;   冪等キーの一意の索引(append_rows の ledger × payload の idempotencyKey)と、宣言した索引の欄の式の索引
;;; 表の名は接頭辞つき(既定 records_)— 同じ database に在る別の置き場の同名の表と混ざらない。
;;;
;;; 書きは置き場ごとの advisory lock 1 つで直列にする: bigserial の番号は commit の順と揃わない(番号 5 を取った書きより先に
;;; 番号 6 の書きが commit すると、6 まで読んだ読み手は 5 を永久に取りこぼす)。番号を取ってから commit するまでを 1 つの lock の中に
;;; 置くと、読み手が見た最大の番号より小さい番号は全部 commit 済み、が成り立つ(WatchChanges の「ちょうど 1 回」の土台)。
;;; 代わりに書きの速さは置き場 1 つで 1 本に縛られる。
(import dataclasses [dataclass])
(import hashlib)
(import re)
(import doeff_records.values [RecordsSchema])

(setv PREFIX-PATTERN (re.compile "^[a-z_][a-z0-9_]{0,30}$"))
(setv DEFAULT-PREFIX "records_")


(defclass [(dataclass :frozen True)] Statement []
  "流す文 1 つと値の並び(psycopg の %s の順)。"
  (#^ str text)
  (#^ tuple params))


(defn #^ str checked-prefix [#^ str prefix]
  (when (not (and (isinstance prefix str) (.match PREFIX-PATTERN prefix)))
    (raise (ValueError (.format "表の接頭辞は英小文字か _ で始まる英小文字・数字・_ の 31 字まで: {!r}" prefix))))
  prefix)


(defn #^ str index-name [#^ str prefix #^ str field-name]
  (+ prefix "ix_" (cut (.hexdigest (hashlib.sha1 (.encode field-name "utf-8"))) 12)))


(defn #^ tuple schema-statements [#^ str prefix #^ RecordsSchema schema]
  "表を用意する文の列(何度流しても同じ — IF NOT EXISTS / ON CONFLICT DO NOTHING)。"
  (setv p prefix
        fields (sorted (sfor decl (.values schema.tables) name decl.indexes name)))
  (tuple
    (+ [(Statement (.format "CREATE TABLE IF NOT EXISTS {p}state_rows (
           ledger text NOT NULL, key text NOT NULL, payload text NOT NULL, version bigint NOT NULL,
           updated_at bigint NOT NULL, updated_by text NOT NULL, origin_host text NOT NULL, epoch bigint NOT NULL,
           PRIMARY KEY (ledger, key))" :p p) #())
        (Statement (.format "CREATE TABLE IF NOT EXISTS {p}append_rows (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, at bigint NOT NULL, payload text NOT NULL,
           origin_host text NOT NULL, epoch bigint NOT NULL)" :p p) #())
        (Statement (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger ON {p}append_rows (ledger, seq)" :p p) #())
        (Statement (.format "CREATE UNIQUE INDEX IF NOT EXISTS {p}append_rows_idempotency
           ON {p}append_rows (ledger, ((payload::jsonb) ->> 'idempotencyKey'))" :p p) #())
        (Statement (.format "CREATE TABLE IF NOT EXISTS {p}row_changes (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, key text NOT NULL, version bigint NOT NULL,
           payload text, at bigint NOT NULL, epoch bigint NOT NULL)" :p p) #())
        (Statement (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_ledger ON {p}row_changes (ledger, seq)" :p p) #())
        (Statement (.format "CREATE TABLE IF NOT EXISTS {p}store_epoch (
           id smallint PRIMARY KEY CHECK (id = 1), epoch bigint NOT NULL, floor bigint NOT NULL)" :p p) #())
        (Statement (.format "INSERT INTO {p}store_epoch (id, epoch, floor) VALUES (1, 1, 0) ON CONFLICT (id) DO NOTHING" :p p) #())]
       ;; 宣言した索引の欄ごとに式の索引 1 つ(欄の名は FIELD-NAME-PATTERN を通った英数字だけ — 文に直に置ける)。
       (lfor name fields
             (Statement (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows (ledger, ((payload::jsonb) -> '{f}'))"
                                 :i (index-name p name) :p p :f name)
                        #())))))


(defn #^ tuple drop-statements [#^ str prefix]
  (tuple (gfor table ["state_rows" "append_rows" "row_changes" "store_epoch"]
               (Statement (.format "DROP TABLE IF EXISTS {}{}" prefix table) #()))))


(defn #^ Statement lock-statement [#^ str prefix]
  "置き場の書きの lock(transaction の終わりで自動で外れる)。"
  (Statement "SELECT pg_advisory_xact_lock(hashtext(%s))" #((+ prefix "records-writer"))))


(defn #^ Statement store-head-statement [#^ str prefix]
  "置き場の版・忘れた位置・変更の列の先頭の番号(1 文 = 1 つの断面)。"
  (Statement (.format "SELECT epoch, floor, greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       FROM {p}store_epoch WHERE id = 1" :p prefix)
             #()))


(defn #^ Statement read-row-statement [#^ str prefix #^ str table #^ str key]
  (Statement (.format "SELECT key, payload, version, updated_at FROM {p}state_rows WHERE ledger = %s AND key = %s" :p prefix)
             #(table key)))


(defn #^ Statement lock-row-statement [#^ str prefix #^ str table #^ str key]
  (Statement (.format "SELECT key, payload, version, updated_at FROM {p}state_rows WHERE ledger = %s AND key = %s FOR UPDATE"
                      :p prefix)
             #(table key)))


(defn #^ Statement list-rows-statement [#^ str prefix #^ str table #^ (| str None) after-key #^ dict where-json #^ int limit]
  "鍵の綴りの順(COLLATE \"C\" = 符号点の順・鍵の綴りは ASCII だけ)の 1 頁 + 1 行(続きの有無を知るため)。
   where-json = 欄 → 値の JSON の綴り(欄の名は宣言を通った英数字)。"
  (setv #^ list clauses ["ledger = %s"])
  (setv #^ list params [table])
  (when (is-not after-key None)
    (.append clauses "key COLLATE \"C\" > %s")
    (.append params after-key))
  (for [#(name encoded) (sorted (.items where-json))]
    (.append clauses (.format "(payload::jsonb) -> '{}' = %s::jsonb" name))
    (.append params encoded))
  (.append params (+ limit 1))
  (Statement (.format "SELECT key, payload, version FROM {p}state_rows WHERE {w} ORDER BY key COLLATE \"C\" LIMIT %s"
                      :p prefix :w (.join " AND " clauses))
             (tuple params)))


(defn #^ Statement terminal-rows-statement [#^ str prefix #^ str table #^ str state-field #^ tuple terminal]
  "終端の状態の行(保持の期限の候補 — 期限の判断は admission.row-expired?)。"
  (Statement (.format "SELECT key, payload, version, updated_at FROM {p}state_rows
                       WHERE ledger = %s AND (payload::jsonb) ->> %s = ANY(%s) ORDER BY key COLLATE \"C\"" :p prefix)
             #(table state-field (list terminal))))


(defn #^ Statement upsert-row-statement [#^ str prefix #^ str table #^ str key #^ str payload #^ int version #^ int at
                                         #^ str writer #^ str origin-host #^ int epoch]
  (Statement (.format "INSERT INTO {p}state_rows (ledger, key, payload, version, updated_at, updated_by, origin_host, epoch)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (ledger, key) DO UPDATE SET payload = EXCLUDED.payload, version = EXCLUDED.version,
                         updated_at = EXCLUDED.updated_at, updated_by = EXCLUDED.updated_by,
                         origin_host = EXCLUDED.origin_host, epoch = EXCLUDED.epoch" :p prefix)
             #(table key payload version at writer origin-host epoch)))


(defn #^ Statement delete-row-statement [#^ str prefix #^ str table #^ str key #^ int version]
  (Statement (.format "DELETE FROM {p}state_rows WHERE ledger = %s AND key = %s AND version = %s" :p prefix)
             #(table key version)))


(defn #^ Statement append-change-statement [#^ str prefix #^ str table #^ str key #^ int version #^ (| str None) payload
                                            #^ int at #^ int epoch]
  (Statement (.format "INSERT INTO {p}row_changes (ledger, key, version, payload, at, epoch)
                       VALUES (%s, %s, %s, %s, %s, %s) RETURNING seq" :p prefix)
             #(table key version payload at epoch)))


(defn #^ Statement changes-statement [#^ str prefix #^ int after #^ int head #^ tuple tables #^ int limit]
  (Statement (.format "SELECT seq, ledger, key, version, payload, at FROM {p}row_changes
                       WHERE seq > %s AND seq <= %s AND ledger = ANY(%s) ORDER BY seq LIMIT %s" :p prefix)
             #(after head (list tables) limit)))


(defn #^ Statement advance-epoch-statement [#^ str prefix]
  (Statement (.format "UPDATE {p}store_epoch SET epoch = epoch + 1,
                         floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       WHERE id = 1 RETURNING epoch" :p prefix)
             #()))


(defn #^ Statement prune-changes-statement [#^ str prefix #^ int before-at]
  "刻が before-at 以下の変更のうち最大の番号までを変更の列から消し(刈る範囲は番号の前方の連なり — 刻が番号の順と揃わなくても
   floor の下に取り残しを作らない)、floor をそこまで上げ、#(floor 消した数) を返す(書きの lock の中で流す —
   lock の外だと、消した後・floor を上げる前に読んだ読み手が消えた変更を黙って飛ばす)。"
  (Statement (.format "WITH removed AS (DELETE FROM {p}row_changes
                                        WHERE seq <= (SELECT coalesce(max(seq), 0) FROM {p}row_changes WHERE at <= %s)
                                        RETURNING seq),
                            raised AS (UPDATE {p}store_epoch
                                       SET floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM removed))
                                       WHERE id = 1 RETURNING floor)
                       SELECT (SELECT floor FROM raised), (SELECT count(*) FROM removed)" :p prefix)
             #(before-at)))


(defn #^ Statement forget-changes-statement [#^ str prefix]
  (Statement (.format "DELETE FROM {p}row_changes" :p prefix) #()))


(defn #^ Statement find-event-statement [#^ str prefix #^ str stream #^ str idempotency-key]
  (Statement (.format "SELECT seq, at, payload FROM {p}append_rows
                       WHERE ledger = %s AND (payload::jsonb) ->> 'idempotencyKey' = %s" :p prefix)
             #(stream idempotency-key)))


(defn #^ Statement insert-event-statement [#^ str prefix #^ str stream #^ int at #^ str payload #^ str origin-host #^ int epoch]
  (Statement (.format "INSERT INTO {p}append_rows (ledger, at, payload, origin_host, epoch) VALUES (%s, %s, %s, %s, %s)
                       RETURNING seq" :p prefix)
             #(stream at payload origin-host epoch)))


(defn #^ Statement read-events-statement [#^ str prefix #^ str stream #^ int after #^ int limit]
  (Statement (.format "SELECT seq, at, payload FROM {p}append_rows WHERE ledger = %s AND seq > %s ORDER BY seq LIMIT %s"
                      :p prefix)
             #(stream after limit)))


(defn #^ Statement expire-events-statement [#^ str prefix #^ str stream #^ int before-at]
  "積んだ刻が before-at 以下の出来事を捨てる(before-at = 今 − 保持の秒 — admission.event-expired? と同じ境界)。"
  (Statement (.format "DELETE FROM {p}append_rows WHERE ledger = %s AND at <= %s" :p prefix)
             #(stream before-at)))
