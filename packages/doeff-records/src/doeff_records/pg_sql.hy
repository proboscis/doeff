;;; PostgreSQL の handler の文(純粋 — 接続に触らない)。流すのは pg.hy の Program ちょうど 1 つで、doeff の汎用の SQL の effect
;;; (SqlQuery・SqlTransaction — doeff_core_effects.sql_effects)に載せる。文は中立の記法(引数 = `:name`・値 = SqlParam)で書き、
;;; PostgreSQL の方言への書き換えは答え手(postgres-sql-handler / pooled-postgres-sql-handler)が持つ(#880 U5)。
;;;
;;; 表の形(列の名と型)は別の置き場(状態の行の表 state_rows・追記の表 append_rows)と同じ形:
;;;   state_rows   PK = (ledger, key)・payload = 値の JSON の綴り・version は書くたびに 1 増える・epoch = 書いた時の置き場の版
;;;   append_rows  seq = bigserial・ledger = 追記の列の名・payload = {"idempotencyKey" "writer" "body"} の JSON の綴り
;;; 足した物(この package の意味に要る物だけ):
;;;   row_changes  変更の列(WatchChanges が読む)— seq = bigserial・payload NULL = 行が消えた
;;;   store_epoch  置き場の版と、変更の列を忘れた位置(floor)の 1 行。版の初めの値は行を作った時の server の時計の ms
;;;                (#3632 — 空から作り直した置き場の版が前の置き場の版と重ならず、前の置き場の位置 (epoch, sequence) で読む読み手は
;;;                新しい置き場の番号が古い位置を越えた後でも Reset になる)。版を進める文(advance-epoch-statement)は +1 のまま。
;;;   冪等キーの一意の索引(append_rows の ledger × payload の idempotencyKey)と、宣言した索引の欄の式の索引
;;;   retired_keys 保持の期限で出来事を消した冪等キーの覚え — PK = (ledger, idempotency_key)・seq = 消した出来事の番号・
;;;                body_digest = 本文の指紋(admission.body-digest)。出来事を消す transaction が同じ transaction で入れ、消さない(#3022)。
;;;                旧い版の文の後ろに足した表(旧い版は読まない・IF NOT EXISTS)。
;;; 旧い版より後に足した索引(#3614 — どれも宣言から作る CREATE INDEX IF NOT EXISTS・旧い版の文の後ろ):
;;;   append_rows (ledger, at)        期限の境(at <= 境)で引く文 — 回収の候補の読み・組の「新しい出来事」の照らし・読みの期限の条件
;;;   append_rows (ledger, 組の名の式) 組で数える列(ByKeySuffix)の区切りごとに 1 つ — 書きが触る組の読み(touched-events-statement)と
;;;                                  組の照らし(expired-event-condition)が引く。式は key-suffix-expression の 1 か所から作り、区切りは宣言の値を
;;;                                  文に直に置く(引数にすると文の式が索引の式と揃わず、索引に当たらない — 区切りの字は values.SEPARATOR-PATTERN)
;;;   append_rows (ledger, 頭の式, seq) 頭で引ける列(key-lookup の ByKeyHead)の区切りごとに 1 つ — 頭で引く読み(read-events-statement の
;;;                                  key-head)が引く。式は key-head-expression の 1 か所から作り、区切りは宣言の値を文に直に置く(組の名の式と同じ)
;;;   row_changes (at)                変更の列の刈り(prune-changes-statement の at <= 境)
;;;   state_rows (ledger, 状態の欄の式) 保持の期限の在る表の状態の欄が、その表の索引の欄に無い時だけ(式は宣言した索引の欄と同じ — 終端の行の
;;;                                  読み terminal-rows-statement が引く)
;;; 欄の式の索引は表ごとの部分索引(card ki-954c58540d0f): 宣言した表 1 つ × 欄 1 つに `… WHERE ledger = '<表>'` の索引 1 つ(名 =
;;; table-index-name)。state_rows は全部の表の行を持つ 1 つの表なので、全部の行に張る索引(旧い形 — 名 = index-name)を足す費用は
;;; 置き場の全体の大きさに比例した(2026-10-18 に 444,566 行・6.3 GB の置き場で 1 本 3 分 23 秒)。部分索引は、その表の行の payload
;;; だけを読んで並べる(主の heap は全部の行を辿るが、TOAST に在る payload を開いて jsonb にするのはその表の行だけ)。旧い形の索引が
;;; 在る欄は、それが同じ読みに答える(aliases — 在れば作らない。旧い 21 本は作り直さない)。
;;; 索引の用意(pg.hy の頭の註): 移行の transaction は、その transaction で生まれた表の索引だけを作る(空の表 — 一瞬)。既に在る表に
;;; 足りない索引は、索引の用意の task が transaction の外の CREATE INDEX CONCURRENTLY で作る(declared-indexes が宣言の索引の全部・
;;; indexes-statement が置き場の索引の観測)。
;;; 表の名は接頭辞つき(既定 records_)— 同じ database に在る別の置き場の同名の表と混ざらない。
;;; DDL は SqlEnsureTables の宣言に書き換えない(式の索引と bigserial を宣言で表せない)— 旧い版と同じ字面のまま流す
;;; (検 test_pg_sql.hy が旧い版の字面と比べる)。旧い版と字面が違うのは store_epoch の行を作る文の版の初めの値だけ(#3632)。
;;; どれも IF NOT EXISTS / ON CONFLICT DO NOTHING なので、字面が変わっても旧い版へ戻せ(表の形は変わらない)、既に在る置き場の
;;; store_epoch の行(旧い版が作った epoch 1 を含む)には触らない。
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
;;;
;;; 保持の期限(#3561): 読みの文(行・一覧・変更の列・追記の読み・列の末尾)は、回収を待たずに期限を過ぎた行と出来事を文の条件で除く
;;; (RowExpiry・EventExpiry の境 — 境の刻は admission.retention-cutoff-ms)。出来事の期限の条件は回収と読みで同じ expired-event-condition の
;;; 1 つ。期限の無い表と列では条件を足さない(文は前と同じ)。書き(#3605 の D)は回収の文を流さず、自分が触る単位の期限を過ぎた出来事だけを
;;; 同じ条件で読み(touched-events-statement — 書きの 1 往復目)、読んだ番号で捨てる(delete-events-statement — 2 往復目)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import re)
(import doeff_core_effects.sql_effects [SqlParam])
(import doeff_records.values [RecordsSchema RetiredKey KeepFor ByKeySuffix FIELD-NAME-PATTERN SEPARATOR-PATTERN TABLE-NAME-PATTERN])

(val MODULE-TAGS {:context "records" :role "foundation"})
(val PREFIX-PATTERN (re.compile "^[a-z_][a-z0-9_]{0,30}$"))
(val DEFAULT-PREFIX "records_")
;; 錠の鍵の接尾(接頭辞の後ろに区切りなしで続ける — 旧い版の錠の文の引数と同じ字面)。
(val WRITER-LOCK-SUFFIX "records-writer")
(val MIGRATE-LOCK-SUFFIX "records-migrate")
;; 索引の用意の錠の鍵の接尾(session の advisory lock — 同じ置き場の索引を 2 つの process が同時に CONCURRENTLY で作らない)。
(val INDEX-LOCK-SUFFIX "records-indexes")
;; 置き場の表(接頭辞の後ろの名)— 表の用意の観測(relations-statement)と索引の観測(indexes-statement)が引く。
(val STORE-RELATIONS #("state_rows" "append_rows" "row_changes" "store_epoch" "retired_keys"))
(val INDEXED-RELATIONS #("state_rows" "append_rows" "row_changes"))


(defrecord Statement
  "流す文 1 つ(text = 中立の記法の文・params = 文の `:name` に当たる SqlParam の並び)。"
  (#^ str text)
  (#^ (get tuple #(SqlParam ...)) params))


(defrecord InList
  "`IN (…)` に展げた組(text = `:t0, :t1, …`・params = その SqlParam の並び)。"
  (#^ str text)
  (#^ (get tuple #(SqlParam ...)) params))


(defrecord Clause
  "文の条件の断片(text = 中立の記法の条件の綴り・params = その `:name` に当たる SqlParam の並び)。読みの文が WHERE の後ろに足す
   保持の期限の条件(空の text = 足す条件が無い — 文を変えない)。"
  (#^ str text)
  (#^ (get tuple #(SqlParam ...)) params))


(defrecord RowExpiry
  "表 1 つの行の保持の期限の境(読みの文の条件 — 回収の判定 admission.row-expired? の文の形・#3561): table = 表の名 /
   state-field = 状態の欄 / terminal = 終端の語(空でない)/ before-at = 境の刻(admission.retention-cutoff-ms — 最後に書いた刻がこれ以下の
   終端の行は期限を過ぎた)。"
  (#^ str table)
  (#^ str state-field)
  (#^ tuple terminal)
  (#^ int before-at))


(defrecord EventExpiry
  "追記の列 1 つの出来事の保持の期限の境(読みの文の条件 — 回収と同じ expired-event-condition・#3561): before-at = 境の刻
   (admission.retention-cutoff-ms — 積んだ刻〔組で数える列は組の最後の出来事の刻〕がこれ以下の出来事は期限を過ぎた)/
   separator = 組で数える列(ByKeySuffix)の区切り(None = 出来事ごとに数える — 文に直に置く宣言の値・字は values.SEPARATOR-PATTERN)。"
  (#^ int before-at)
  (#^ (| str None) separator))


(defrecord IndexedField
  "宣言した索引の欄 1 つ(表 table の欄 field — ListRows の where・where-any が名指す欄): 索引の用意の観測が「使える索引が在る」と
   覚える単位。"
  (#^ str table)
  (#^ str field))


(defrecord DeclaredIndex
  "宣言から作る索引 1 つ(頭の註の「索引の用意」): name = 索引の名 / relation = 張る表(接頭辞の後ろの名 — STORE-RELATIONS のどれか)/
   text = 移行の transaction の中で作る文(CREATE [UNIQUE] INDEX IF NOT EXISTS …)/ serves = この索引が在って初めて答える ListRows の
   (表, 欄) の組の並び(宣言した索引の欄 — 無ければ NotIndexed。空 = 速さだけの索引)/ aliases = 同じ読みに答える旧い形の索引の名
   (在れば作らない)/ statistics = 索引の前に作る式の統計の文(CREATE STATISTICS IF NOT EXISTS … — 部分索引の式の見積もり・None = 要らない)。"
  (#^ str name)
  (#^ str relation)
  (#^ str text)
  (#^ (get tuple #(IndexedField ...)) serves)
  (#^ (get tuple #(str ...)) aliases)
  (#^ (| str None) statistics))


(defrecord SchemaStep
  "表の用意の文 1 つ(schema-steps の並び): statement = 流す文 / index = 索引を作る文ならその索引(表と行の文は None — 移行の
   transaction は、表がその transaction で生まれた時だけ索引の文を流す)。"
  (#^ Statement statement)
  (#^ (| DeclaredIndex None) index))


(val NO-CLAUSE (Clause :text "" :params #()))


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


(defk writer-lock-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "表の用意(移行)の transaction の中で、移行の錠の次に置き場の書きの錠を取る文を作るため(答え手が lock-key に流す文と同じ形 —
   pg_advisory_xact_lock(hashtext(鍵)))。用意の CREATE INDEX は表に ShareLock を取って transaction の終わりまで持ち、書き(書きの錠の中)は
   行の表に RowExclusiveLock を取るので、書きの錠を取らない用意と同じ置き場の書き(入れ替えの間も答える同じ置き場の別の process・旧い版の終わりの書き)は
   表の lock を逆の順で取って deadlock(40P01)になる(2026-10-16 の入れ替えで起動が 1 回落ちた)。錠の順は移行の錠 → 書きの錠 → 表の lock の 1 つ。"
  (Statement :text "SELECT pg_advisory_xact_lock(hashtext(:key))"
             :params (! (params-of #(#("key" (! (writer-lock-key prefix))))))))


(defk in-list [stem values]
  {:pre [(: stem str) (: values tuple) (> (len values) 0)] :post [(: % InList)]
   :tags {:context "records" :role "foundation"}}
  "値の組を `IN (…)` の中身(`:stem0, :stem1, …`)と引数に展げるため(空の組は文にできないので :pre が断る — 呼び手が枝で避ける)。"
  (val names (lfor i (range (len values)) (.format "{}{}" stem i)))
  (InList :text (.join ", " (gfor name names (+ ":" name)))
          :params (! (params-of (tuple (zip names values :strict True))))))


(defk digest-of [text]
  {:pre [(: text str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "索引の名に入れる指紋(sha1 の頭 12 字)を作るため。"
  (cut (.hexdigest (hashlib.sha1 (.encode text "utf-8"))) 12))


(defk index-name [prefix field-name]
  {:pre [(: prefix str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "旧い形の欄の式の索引(state_rows の全部の行 — 旧い版と同じ名)の名を作るため。表ごとの部分索引の aliases(頭の註)。"
  (<- digest (digest-of field-name))
  (+ prefix "ix_" digest))


(defk table-index-name [prefix table field-name]
  {:pre [(: prefix str) (: table str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表 table の欄 field-name の部分索引(頭の註)の名を作るため(接頭辞 31 字 + 30 字 — PostgreSQL の名の上限 63 字の内)。"
  (<- field-digest (digest-of field-name))
  (<- table-digest (digest-of table))
  (.format "{}ix_{}_{}" prefix field-digest table-digest))


(defk field-expression [payload field-name]
  {:pre [(: payload str) (: field-name str) (.match FIELD-NAME-PATTERN field-name)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "行の欄 field-name の値(jsonb)の式を作るため — 欄の式の索引(field-index)と、その索引で引く文の条件(terminal-state-condition)が
   この 1 つを使い、索引の式と文の式の字面を揃える(欄の名は宣言を通った英数字 — 文に直に置く)。payload = payload の列の綴り(索引は
   payload・文は別名つきでもよい)。"
  (.format "(({}::jsonb) -> '{}')" payload field-name))


(defk field-index [prefix table field-name serves]
  {:pre [(: prefix str) (: table str) (.match TABLE-NAME-PATTERN table) (: field-name str) (: serves bool)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "表 table の欄 field-name の式の部分索引 state_rows (欄の式) WHERE ledger = '<表>' を作るため(頭の註 — 宣言した索引の欄と、その表の
   索引の欄に無い保持の期限の在る表の状態の欄)。列に ledger を持たない: 条件の ledger = '<表>' が索引の述語に含まれるので、列に置くと
   planner は列の条件を述語に任せて外し、頭の列の条件の無い索引として値の組(IN)の読みに使わない。部分索引の式の統計は planner が
   読まない(全部の行に張った索引の式の統計だけを読む)ので、欄の式の統計(statistics — 欄ごとに 1 つ・全部の行の標本)を索引の前に
   作る — 無いと値の組の見積もりが既定の選択度(1 値 0.5%)の積み上げになり、表を頭から読む計画が選ばれる。serves = 宣言した索引の
   欄か(ListRows がこの索引を待つ — NotIndexed の判断)。表の名は宣言を通った英小文字・数字・_・- だけ(values.TABLE-NAME-PATTERN —
   引用符を含まないので文に直に置ける)。"
  (<- name (table-index-name prefix table field-name))
  (<- legacy (index-name prefix field-name))
  (<- digest (digest-of field-name))
  (<- expression (field-expression "payload" field-name))
  (DeclaredIndex :name name :relation "state_rows"
                 :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows ({e}) WHERE ledger = '{t}'"
                                :i name :p prefix :e expression :t table)
                 :serves (if serves #((IndexedField :table table :field field-name)) #())
                 :aliases #(legacy)
                 :statistics (.format "CREATE STATISTICS IF NOT EXISTS {p}st_{d} ON {e} FROM {p}state_rows" :p prefix :d digest :e expression)))


(defk fixed-index [name relation text]
  {:pre [(: name str) (: relation str) (: text str)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "宣言に依らない索引(置き場の形の索引 — 速さと一意の索引)1 つを作るため(読みを待たせない・旧い名の写しは無い)。"
  (DeclaredIndex :name name :relation relation :text text :serves #() :aliases #() :statistics None))


(defk group-index [prefix separator]
  {:pre [(: prefix str) (: separator str)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "組で数える列の区切り separator の組の名の式の索引 append_rows (ledger, 組の名の式) を作るため(区切りごとに 1 つ — 同じ区切りの列は
   索引を分け合う)。式は文の組の照らしと同じ key-suffix-expression。"
  (<- digest (digest-of separator))
  (val name (+ prefix "append_rows_group_" digest))
  (<- group (key-suffix-expression "payload" separator))
  (! (fixed-index name "append_rows"
                  (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}append_rows (ledger, {g})" :i name :p prefix :g group))))


(defk head-index [prefix separator]
  {:pre [(: prefix str) (: separator str)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "頭で引ける列の区切り separator の頭の式の索引 append_rows (ledger, 頭の式, seq) を作るため(区切りごとに 1 つ — 同じ区切りの列は
   索引を分け合う)。頭で引く読み(read-events-statement の key-head)が、列の他の頭の出来事を辿らずに、その頭の after より後を番号の順に
   上から読む。式は文と同じ key-head-expression。"
  (<- digest (digest-of separator))
  (val name (+ prefix "append_rows_head_" digest))
  (<- head (key-head-expression "payload" separator))
  (! (fixed-index name "append_rows"
                  (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}append_rows (ledger, {h}, seq)" :i name :p prefix :h head))))


(defk table-step [text]
  {:pre [(: text str)] :post [(: % SchemaStep)]
   :tags {:context "records" :role "foundation"}}
  "表と行の文 1 つを表の用意の文にするため(いつも流す — IF NOT EXISTS / ON CONFLICT DO NOTHING)。"
  (SchemaStep :statement (Statement :text text :params #()) :index None))


(defk index-steps [index]
  {:pre [(: index DeclaredIndex)] :post [(: % (get tuple #(SchemaStep ...)))]
   :tags {:context "records" :role "foundation"}}
  "索引 1 つを表の用意の文(式の統計が要れば統計 → 索引の 2 つ)にするため(移行の transaction は、索引の表がその transaction で生まれた
   時だけ流す)。"
  (val made (SchemaStep :statement (Statement :text index.text :params #()) :index index))
  (if (is index.statistics None)
      #(made)
      #((SchemaStep :statement (Statement :text index.statistics :params #()) :index index) made)))


(defk schema-steps [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % (get tuple #(SchemaStep ...)))]
   :tags {:context "records" :role "foundation"}}
  "表を用意する文の列(何度流しても同じ — IF NOT EXISTS / ON CONFLICT DO NOTHING)を、索引の文かどうかの印つきで作るため。流すのは
   pg.prepare-records-store だけ(移行の錠の transaction の中で・process ごとに 1 度 — 既に在る表の索引の文は流さない・頭の註)。
   表と置き場の形の索引の字面と順は旧い版と同じ(頭の註)。宣言した索引の欄の索引は表ごとの部分索引(表の名の順・欄の名の順)。"
  (val p prefix)
  (val tables
    #((! (table-step (.format "CREATE TABLE IF NOT EXISTS {p}state_rows (
           ledger text NOT NULL, key text NOT NULL, payload text NOT NULL, version bigint NOT NULL,
           updated_at bigint NOT NULL, updated_by text NOT NULL, origin_host text NOT NULL, epoch bigint NOT NULL,
           PRIMARY KEY (ledger, key))" :p p)))
      (! (table-step (.format "CREATE TABLE IF NOT EXISTS {p}append_rows (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, at bigint NOT NULL, payload text NOT NULL,
           origin_host text NOT NULL, epoch bigint NOT NULL)" :p p)))
      #* (! (index-steps (! (fixed-index (+ p "append_rows_ledger") "append_rows"
                                         (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger ON {p}append_rows (ledger, seq)" :p p)))))
      #* (! (index-steps (! (fixed-index (+ p "append_rows_idempotency") "append_rows"
                                         (.format "CREATE UNIQUE INDEX IF NOT EXISTS {p}append_rows_idempotency
           ON {p}append_rows (ledger, ((payload::jsonb) ->> 'idempotencyKey'))" :p p)))))
      (! (table-step (.format "CREATE TABLE IF NOT EXISTS {p}row_changes (
           seq bigserial PRIMARY KEY, ledger text NOT NULL, key text NOT NULL, version bigint NOT NULL,
           payload text, at bigint NOT NULL, epoch bigint NOT NULL)" :p p)))
      #* (! (index-steps (! (fixed-index (+ p "row_changes_ledger") "row_changes"
                                         (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_ledger ON {p}row_changes (ledger, seq)" :p p)))))
      (! (table-step (.format "CREATE TABLE IF NOT EXISTS {p}store_epoch (
           id smallint PRIMARY KEY CHECK (id = 1), epoch bigint NOT NULL, floor bigint NOT NULL)" :p p)))
      ;; 版の初めの値 = 行を作った時の server の時計の ms(頭の註の store_epoch・#3632)。行が在れば版に触らない。
      (! (table-step (.format "INSERT INTO {p}store_epoch (id, epoch, floor)
           VALUES (1, (extract(epoch from clock_timestamp()) * 1000)::bigint, 0) ON CONFLICT (id) DO NOTHING" :p p)))))
  ;; 宣言した索引の欄ごとに表ごとの部分索引 1 つ(欄の名は FIELD-NAME-PATTERN を通った英数字だけ — 文に直に置ける)。
  (var indexes #())
  (for [table (sorted schema.tables)]
    (for [name (sorted (. (get schema.tables table) indexes))]
      (<- index (field-index p table name True))
      (<- made (index-steps index))
      (:= indexes (+ indexes made))))
  ;; 旧い版より後に足した表と索引は旧い版の文の後ろに足した順に並べる(旧い版の文の字面と順はそのまま — 検 test_pg_sql.hy)。
  (<- added (added-indexes p schema))
  (var added-steps #())
  (for [index added]
    (<- made (index-steps index))
    (:= added-steps (+ added-steps made)))
  (+ tables
     indexes
     #((! (table-step (.format "CREATE TABLE IF NOT EXISTS {p}retired_keys (
           ledger text NOT NULL, idempotency_key text NOT NULL, seq bigint NOT NULL, body_digest text NOT NULL,
           PRIMARY KEY (ledger, idempotency_key))" :p p))))
     added-steps))


(defk schema-statements [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % (get tuple #(Statement ...)))]
   :tags {:context "records" :role "foundation"}}
  "空の置き場を作る文の列(schema-steps の文の全部 — 表がどれも無い置き場の移行が流す文と同じ並び)。"
  (<- steps (schema-steps prefix schema))
  (tuple (gfor step steps step.statement)))


(defk declared-indexes [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % (get tuple #(DeclaredIndex ...)))]
   :tags {:context "records" :role "foundation"}}
  "置き場に在るべき索引の全部を作るため(schema-steps の索引の文の並び — 索引の用意が置き場の索引と比べる宣言・頭の註)。"
  (<- steps (schema-steps prefix schema))
  (var declared #())
  (for [step steps]
    (when (and (is-not step.index None) (not-in step.index declared))
      (:= declared (+ declared #(step.index)))))
  declared)


(defk added-indexes [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % (get tuple #(DeclaredIndex ...)))]
   :tags {:context "records" :role "foundation"}}
  "旧い版より後に足した索引の列を宣言 schema から作るため(#3614 — 頭の註の 4 つ。どれも CREATE INDEX IF NOT EXISTS で、表の欄と
   データに触らない)。並びは (ledger, at)・区切りの順の組の名の式・区切りの順の頭の式・変更の列の刻・表の名の順の状態の欄の式。状態の欄の索引は、
   保持の期限の在る表(KeepFor かつ終端の語を持つ — 回収の候補の読み terminal-rows-statement を流す表)の状態の欄が、その表の索引の欄に
   無い時だけ(在る欄はその表の宣言した索引の欄の部分索引が答える)。"
  (val p prefix)
  (val separators (sorted (sfor decl (.values schema.streams) :if (isinstance decl.retention-group ByKeySuffix)
                                decl.retention-group.separator)))
  (val head-separators (sorted (sfor decl (.values schema.streams) :if (is-not decl.key-lookup None) decl.key-lookup.separator)))
  (val state-tables (lfor table (sorted schema.tables)
                          :setv decl (get schema.tables table)
                          :if (and (isinstance decl.retention KeepFor) decl.terminal (not-in decl.state-field decl.indexes))
                          #(table decl.state-field)))
  (var groups #())
  (for [separator separators]
    (<- index (group-index p separator))
    (:= groups (+ groups #(index))))
  (for [separator head-separators]
    (<- head (head-index p separator))
    (:= groups (+ groups #(head))))
  (var states #())
  (for [#(table field) state-tables]
    (<- state-index (field-index p table field False))
    (:= states (+ states #(state-index))))
  (+ #((! (fixed-index (+ p "append_rows_ledger_at") "append_rows"
                       (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger_at ON {p}append_rows (ledger, at)" :p p))))
     groups
     #((! (fixed-index (+ p "row_changes_at") "row_changes"
                       (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_at ON {p}row_changes (at)" :p p))))
     states))


(defk relation-names [prefix relations]
  {:pre [(: prefix str) (: relations tuple) (> (len relations) 0)] :post [(: % InList)]
   :tags {:context "records" :role "foundation"}}
  "置き場の表の名(接頭辞つき)を、catalog の oid の組 `to_regclass(:r0), …` に展げるため(search_path で引く — 表を作る文と同じ引き方)。"
  (<- names (in-list "r" (tuple (gfor relation relations (+ prefix relation)))))
  (InList :text (.join ", " (gfor param names.params (.format "to_regclass(:{})" param.name))) :params names.params))


(defk relations-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の表のうち在る物の名(接頭辞つき)を読む文を作るため(移行の transaction の頭 — この transaction で生まれる表を見分ける)。"
  (<- relations (relation-names prefix STORE-RELATIONS))
  (Statement :text (.format "SELECT c.relname FROM pg_class AS c WHERE c.oid IN ({r})" :r relations.text) :params relations.params))


(defk indexes-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の表の索引の名と、使える形か(indisvalid — CONCURRENTLY の作りかけは False で残る)を読む文を作るため(索引の観測)。"
  (<- relations (relation-names prefix INDEXED-RELATIONS))
  (Statement :text (.format "SELECT c.relname, i.indisvalid FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid
                       WHERE i.indrelid IN ({r})" :r relations.text)
             :params relations.params))


(defk concurrent-index-statement [index]
  {:pre [(: index DeclaredIndex)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "既に在る表に足りない索引を、表の書きを止めずに transaction の外で作る文(CREATE … INDEX CONCURRENTLY IF NOT EXISTS)を作るため
   (字面は移行の文の INDEX の後ろに CONCURRENTLY を挟んだ物 — 名と形は移行の文と同じ)。"
  (Statement :text (.replace index.text "INDEX IF NOT EXISTS" "INDEX CONCURRENTLY IF NOT EXISTS" 1) :params #()))


(defk statistics-statement [index]
  {:pre [(: index DeclaredIndex) (is-not index.statistics None)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "索引の式の統計を作る文(CREATE STATISTICS IF NOT EXISTS — catalog だけの一瞬の文)を作るため。"
  (Statement :text index.statistics :params #()))


(defk analyze-statement [prefix relation]
  {:pre [(: prefix str) (: relation str) (in relation STORE-RELATIONS)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "式の統計を作った表の標本を取り直す文を作るため(作ったばかりの統計は空で、次の autovacuum の analyze まで見積もりに効かない)。"
  (Statement :text (.format "ANALYZE {}{}" prefix relation) :params #()))


(defk drop-index-statement [index]
  {:pre [(: index DeclaredIndex)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "使えない形で残った索引(CONCURRENTLY の作りかけ)を、表の書きを止めずに消す文を作るため。"
  (Statement :text (.format "DROP INDEX CONCURRENTLY IF EXISTS {}" index.name) :params #()))


(defk index-lock-key [prefix]
  {:pre [(: prefix str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠の鍵を作るため(同じ置き場の索引を同時に作るのを 1 つの process にする — 2 本の CREATE INDEX CONCURRENTLY は
   互いの snapshot を待ち合って deadlock になり、負けた方が使えない索引を残す)。"
  (+ prefix INDEX-LOCK-SUFFIX))


(defk index-lock-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を取る文(session の advisory lock — 他の process が持てば外れるまで待つ)を作るため。"
  (Statement :text "SELECT pg_advisory_lock(hashtext(:key))" :params (! (params-of #(#("key" (! (index-lock-key prefix))))))))


(defk index-unlock-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を外す文を作るため。"
  (Statement :text "SELECT pg_advisory_unlock(hashtext(:key))" :params (! (params-of #(#("key" (! (index-lock-key prefix))))))))


(defk drop-statements [prefix]
  {:pre [(: prefix str)] :post [(: % (get tuple #(Statement ...)))]
   :tags {:context "records" :role "foundation"}}
  "検の後片付けの文(この接頭辞の表を消す)を作るため。"
  (tuple (gfor table ["state_rows" "append_rows" "row_changes" "store_epoch" "retired_keys"]
               (Statement :text (.format "DROP TABLE IF EXISTS {}{}" prefix table) :params #()))))


(defk store-head-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の版・忘れた位置・変更の列の先頭の番号を 1 文 = 1 つの断面で読む文を作るため。"
  (Statement :text (.format "SELECT epoch, floor, greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       FROM {p}store_epoch WHERE id = 1" :p prefix)
             :params #()))


(defk expired-row-clause [alias expiries]
  {:pre [(: alias str) (: expiries tuple) (> (len expiries) 0) (all (gfor expiry expiries (isinstance expiry RowExpiry)))]
   :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "state_rows の行 alias が保持の期限を過ぎた終端の行である条件(表ごとの境 expiries の OR — 回収の判定 admission.row-expired? の文の形)を
   作るため。空の組は文にできないので :pre が断る(呼び手が枝で避ける)。"
  (var texts #())
  (var params #())
  (for [#(index expiry) (enumerate expiries)]
    (<- part (expired-row-part alias (.format "x{}" index) expiry))
    (:= texts (+ texts #(part.text)))
    (:= params (+ params part.params)))
  (Clause :text (.format "({})" (.join " OR " texts)) :params params))


(defk expired-row-part [alias stem expiry]
  {:pre [(: alias str) (: stem str) (: expiry RowExpiry)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "表 1 つ(expiry)について、行 alias がその表の保持の期限を過ぎた終端の行である条件を作るため。終端の語の照らしは回収の候補の読み
   terminal-rows-statement と同じ terminal-state-condition・刻の照らしは最後に書いた刻 ≦ 境の刻。引数の名は stem
   (表ごとに x0・x1 …)を頭に付ける(同じ文の他の引数と混ざらない)。"
  (<- states (terminal-state-condition (+ alias ".payload") expiry.state-field (+ stem "t") expiry.terminal))
  (<- named (params-of #(#((+ stem "l") expiry.table) #((+ stem "b") expiry.before-at))))
  (Clause :text (.format "({a}.ledger = :{s}l AND {t} AND {a}.updated_at <= :{s}b)" :a alias :s stem :t states.text)
          :params (+ named states.params)))


(defk terminal-state-condition [payload state-field stem terminal]
  {:pre [(: payload str) (: state-field str) (: stem str) (: terminal tuple) (> (len terminal) 0)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "行の状態の欄 state-field が終端の語 terminal のどれかである条件を作るため(回収の候補の読み terminal-rows-statement と、読みの文の期限の
   条件 expired-row-part の 1 つの形)。欄の式は field-expression(欄の式の索引 field-index-statement と同じ字面 — 索引に当たる)で、
   終端の語は JSON の値(jsonb)の引数 `:{stem}0::jsonb, …` にして比べる(memory の判定 admission.terminal-row? と同じく値そのものを比べる)。
   payload = payload の列の綴り(別名つきでもよい)。"
  (<- states (field-in-condition payload state-field stem (tuple (gfor word terminal (json.dumps word :ensure-ascii False)))))
  states)


(defk field-in-condition [payload field-name stem encoded]
  {:pre [(: payload str) (: field-name str) (: stem str) (: encoded tuple) (> (len encoded) 0)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "行の欄 field-name の値が JSON の値の組 encoded(JSON の綴りの空でない tuple)のどれかに等しい条件を作るため(終端の状態の条件
   terminal-state-condition と、ListRows の値の組の絞り any-of-filter の 1 つの形)。欄の式は field-expression(欄の式の索引
   field-index-statement と同じ字面 — 索引に当たる)で、値は JSON の値(jsonb)の引数 `:{stem}0::jsonb, …` にして比べる(memory の判定
   admission.json-equal? と同じく値そのものを比べる)。payload = payload の列の綴り(別名つきでもよい)。空の組は文にできないので :pre が断る。"
  (<- expression (field-expression payload field-name))
  (<- members (in-list stem encoded))
  (Clause :text (.format "{e} IN ({s})" :e expression :s (.join ", " (gfor param members.params (.format ":{}::jsonb" param.name))))
          :params members.params))


(defk any-of-filter [where-any-json]
  {:pre [(: where-any-json dict)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の値の組の絞り(where-any)を、行の読みの文の WHERE の後ろに足す条件にするため: 欄ごと(名の順)に field-in-condition を AND で
   続ける(欄 i の引数の名は a{i}_0・a{i}_1 … — 文の他の引数と混ざらない)。where-any-json = 欄 → 値の JSON の綴りの空でない tuple
   (欄の名は宣言を通った英数字)。空の写像なら空の断片(文を変えない — 組を使わない読みの文は前と同じ字面)。"
  (var text "")
  (var params #())
  (for [#(i #(name encoded)) (enumerate (sorted (.items where-any-json)))]
    (<- condition (field-in-condition "payload" name (.format "a{}_" i) encoded))
    (:= text (+ text " AND " condition.text))
    (:= params (+ params condition.params)))
  (Clause :text text :params params))


(defk unexpired-rows-filter [alias expiries]
  {:pre [(: alias str) (: expiries tuple)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "行の読みの文の WHERE の後ろに足す「保持の期限を過ぎた終端の行を除く」条件を作るため(expiries が空なら空の断片 — 文を変えない)。
   条件の値が NULL(状態の欄の無い行)の行は除かない(IS NOT TRUE)。"
  (when (not expiries)
    (return NO-CLAUSE))
  (<- expired (expired-row-clause alias expiries))
  (Clause :text (.format " AND {} IS NOT TRUE" expired.text) :params expired.params))


(defk read-row-statement [prefix table key expiry]
  {:pre [(: prefix str) (: table str) (: key str) (: expiry (| RowExpiry None))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "行 1 つを読む文を作るため。expiry = 表の保持の期限の境(None = 期限の無い表)— 期限を過ぎた終端の行は、回収の前でも読みに出さない
   (#3561)。"
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows AS r WHERE ledger = :ledger AND key = :key{u}"
                            :p prefix :u unexpired.text)
             :params (+ (! (params-of #(#("ledger" table) #("key" key)))) unexpired.params)))


(defk lock-row-statement [prefix table key]
  {:pre [(: prefix str) (: table str) (: key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "書きの判定に渡す今の行を行の錠(FOR UPDATE)つきで読む文を作るため。"
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows WHERE ledger = :ledger AND key = :key FOR UPDATE"
                            :p prefix)
             :params (! (params-of #(#("ledger" table) #("key" key))))))


(defk list-rows-statement [prefix table after-key where-json where-any-json limit expiry]
  {:pre [(: prefix str) (: table str) (: after-key (| str None)) (: where-json dict) (: where-any-json dict) (: limit int)
         (: expiry (| RowExpiry None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "鍵の綴りの順(COLLATE \"C\" = 符号点の順・鍵の綴りは ASCII だけ)の 1 頁 + 1 行(続きの有無を知るため)を読む文を作るため。
   where-json = 欄 → 値の JSON の綴り(欄の名は宣言を通った英数字)。where-any-json = 欄 → 値の組の JSON の綴りの tuple(ListRows の
   where-any — 条件は any-of-filter・空の写像は文を変えない)。expiry = 表の保持の期限の境(None = 期限の無い表)— 期限を過ぎた
   終端の行は、回収の前でも頁に出さない(頁の上限の前に除く — 頁の続きの位置がずれない・#3561)。"
  (var clauses ["ledger = :ledger"])
  (var pairs [#("ledger" table)])
  (when (is-not after-key None)
    (.append clauses "key COLLATE \"C\" > :after")
    (.append pairs #("after" after-key)))
  (for [#(i #(name encoded)) (enumerate (sorted (.items where-json)))]
    (.append clauses (.format "(payload::jsonb) -> '{}' = :w{}::jsonb" name i))
    (.append pairs #((.format "w{}" i) encoded)))
  (.append pairs #("limit" (+ limit 1)))
  (<- any-of (any-of-filter where-any-json))
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{a}{u} ORDER BY key COLLATE \"C\" LIMIT :limit"
                            :p prefix :w (.join " AND " clauses) :a any-of.text :u unexpired.text)
             :params (+ (! (params-of (tuple pairs))) any-of.params unexpired.params)))


(defk top-rows-statement [prefix table descending-by where-json where-any-json limit expiry]
  {:pre [(: prefix str) (: table str) (: descending-by str) (.match FIELD-NAME-PATTERN descending-by) (: where-json dict)
         (: where-any-json dict) (: limit int) (: expiry (| RowExpiry None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "上から読む ListRows(descending-by — effects.hy の ListRows の註)の 1 頁を読む文を作るため: 欄 descending-by の値が JSON の整数の行
   (memory の置き場の descending-value? と同じ行 — 小数・真偽・文字列・欄の無い行を除く)を、(値, 鍵の綴り) の大きい順に limit 件。続きを
   返さないので 1 行多く読まない。where・where-any と保持の期限の条件は list-rows-statement と同じ。"
  (val wheres (tuple (enumerate (sorted (.items where-json)))))
  (<- ordered (field-expression "payload" descending-by))
  (val clauses (+ #("ledger = :ledger")
                  (tuple (gfor #(i #(name _)) wheres (.format "(payload::jsonb) -> '{}' = :w{}::jsonb" name i)))
                  #((.format "jsonb_typeof({o}) = 'number' AND ({o})::text ~ '^-?[0-9]+$'" :o ordered))))
  (val pairs (+ #(#("ledger" table))
                (tuple (gfor #(i #(_ encoded)) wheres #((.format "w{}" i) encoded)))
                #(#("limit" limit))))
  (<- any-of (any-of-filter where-any-json))
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{a}{u}
                       ORDER BY ({o})::text::numeric DESC, key COLLATE \"C\" DESC LIMIT :limit"
                            :p prefix :w (.join " AND " clauses) :a any-of.text :u unexpired.text :o ordered)
             :params (+ (! (params-of pairs)) any-of.params unexpired.params)))


(defk terminal-rows-statement [prefix table state-field terminal]
  {:pre [(: prefix str) (: table str) (: state-field str) (: terminal tuple) (> (len terminal) 0)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "終端の状態の行(保持の期限の候補 — 期限の判断は admission.row-expired?)を読む文を作るため(terminal が空なら呼ばない)。状態の欄の条件は
   (ledger, 状態の欄の式) の索引に当たる形(terminal-state-condition — 索引は宣言した索引の欄か added-index-statements が作る)で、表の
   終端でない行を読まない(#3614)。"
  (<- states (terminal-state-condition "payload" state-field "t" terminal))
  (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows
                       WHERE ledger = :ledger AND {s} ORDER BY key COLLATE \"C\""
                            :p prefix :s states.text)
             :params (+ (! (params-of #(#("ledger" table)))) states.params)))


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


(defk hidden-changes-filter [prefix expiries]
  {:pre [(: prefix str) (: expiries tuple)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "変更の列の読み(changes-statement)の WHERE の後ろに足す「行の今の値が保持の期限を過ぎた終端の行である、その行の変わり(RowChanged)を
   除く」条件を作るため(expiries が空なら空の断片 — 文を変えない)。行の今の値は state_rows の行で判じ(条件は expired-row-clause)、
   消えた(payload が NULL の RowRemoved)は除かない — 期限を過ぎた行の消えた は回収(SweepExpired)か、その行への書きが積む(#3561・
   #3605 の D)。"
  (when (not expiries)
    (return NO-CLAUSE))
  (<- expired (expired-row-clause "r" expiries))
  (Clause :text (.format " AND NOT (c.payload IS NOT NULL AND EXISTS (SELECT 1 FROM {p}state_rows AS r
                                                              WHERE r.ledger = c.ledger AND r.key = c.key AND {x}))"
                         :p prefix :x expired.text)
          :params expired.params))


(defk changes-statement [prefix after head tables limit expiries]
  {:pre [(: prefix str) (: after int) (: head int) (: tables tuple) (> (len tables) 0) (: limit int) (: expiries tuple)]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "変更の列の after より後・head まで・頼んだ表の分を読む文を作るため(tables が空なら呼ばない)。expiries = 頼んだ表のうち保持の期限の
   在る表の境(RowExpiry の組・空 = 無し)— 行の今の値が期限を過ぎた終端の行である行の変わりは、回収の前でも出さない(上限の前に除く —
   次の位置がずれない・hidden-changes-filter)。"
  (<- ledgers (in-list "t" tables))
  (<- hidden (hidden-changes-filter prefix expiries))
  (Statement :text (.format "SELECT seq, ledger, key, version, payload, at FROM {p}row_changes AS c
                       WHERE seq > :after AND seq <= :head AND ledger IN ({t}){h} ORDER BY seq LIMIT :limit"
                            :p prefix :t ledgers.text :h hidden.text)
             :params (+ (! (params-of #(#("after" after) #("head" head) #("limit" limit)))) ledgers.params hidden.params)))


(defk advance-epoch-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "置き場の版を 1 進め、floor を変更の列の先頭まで上げる文を作るため。"
  (Statement :text (.format "UPDATE {p}store_epoch SET epoch = epoch + 1,
                         floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM {p}row_changes))
                       WHERE id = 1 RETURNING epoch" :p prefix)
             :params #()))


(defk change-log-statement [prefix after limit]
  {:pre [(: prefix str) (: after int) (: limit int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "変更の列の after より後の変更を番号の順に limit 件まで、表と保持の期限で絞らずに読む文を作るため(手入れの送りの読み
   ReadChangeLog — 主鍵 seq の索引で引く)。行の並びは changes-statement と同じ(change-of が読む)。"
  (Statement :text (.format "SELECT seq, ledger, key, version, payload, at FROM {p}row_changes WHERE seq > :after ORDER BY seq LIMIT :limit"
                            :p prefix)
             :params (! (params-of #(#("after" after) #("limit" limit))))))


;; 上限の無い刈りの番号の上限(bigint の最大 — 送りを持たない計画の PruneChanges)。
(val UNBOUNDED-SEQ 9223372036854775807)


(defk prune-changes-statement [prefix before-at upto-seq]
  {:pre [(: prefix str) (: before-at int) (: upto-seq int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "刻が before-at 以下の変更のうち最大の番号まで(ただし upto-seq — 送って確かめた番号の上限 — を越えない)を変更の列から消し(刈る範囲は
   番号の前方の連なり — 刻が番号の順と揃わなくても floor の下に取り残しを作らない)、floor をそこまで上げ、#(floor 消した数) を返す文を
   作るため(書きの錠の中で流す — 錠の外だと、消した後・floor を上げる前に読んだ読み手が消えた変更を黙って飛ばす)。上限の無い刈りは
   呼び手が UNBOUNDED-SEQ を渡す。"
  (Statement :text (.format "WITH removed AS (DELETE FROM {p}row_changes
                                        WHERE seq <= least(:upto_seq,
                                                           (SELECT coalesce(max(seq), 0) FROM {p}row_changes WHERE at <= :before_at))
                                        RETURNING seq),
                            raised AS (UPDATE {p}store_epoch
                                       SET floor = greatest(floor, (SELECT coalesce(max(seq), 0) FROM removed))
                                       WHERE id = 1 RETURNING floor)
                       SELECT (SELECT floor FROM raised), (SELECT count(*) FROM removed)" :p prefix)
             :params (! (params-of #(#("before_at" before-at) #("upto_seq" upto-seq))))))


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


(defk living-events-filter [prefix expiry bound]
  {:pre [(: prefix str) (: expiry (| EventExpiry None)) (: bound str)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "追記の列の読みの文(出来事 old)の WHERE の後ろに足す「保持の期限を過ぎた出来事を除く」条件を作るため(expiry = None は期限の無い列 —
   空の断片で文を変えない)。条件は回収と同じ expired-event-condition(#3561 — 読みは回収を待たずに同じ境で期限を見る)。bound = 境の刻の
   引数の名(1 つの文に列を並べる読み — watch-head-statement — が列ごとに別の名を使う)。"
  (when (is expiry None)
    (return NO-CLAUSE))
  (<- expired (expired-event-condition prefix expiry.separator bound))
  (<- named (params-of #(#(bound expiry.before-at))))
  (Clause :text (.format " AND NOT ({})" expired) :params named))


(defrecord HeadRead
  "頭で引く読みの材料(read-events-statement に渡す): separator = 列の宣言の頭の区切り / head = 引く頭(ReadEvents の key-head)。"
  (#^ str separator)
  (#^ str head))


(defk read-events-statement [prefix stream after limit expiry head]
  {:pre [(: prefix str) (: stream str) (: after int) (: limit int) (: expiry (| EventExpiry None)) (: head (| HeadRead None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "追記の列の after より後を読む文を作るため。expiry = 列の保持の期限の境(None = 期限の無い列)— 期限を過ぎた出来事は、回収の前でも
   読みに出さない(上限の前に除く)。head = 頭で引く読み(None = 列の全部の出来事)— 冪等キーの頭が head.head の出来事だけを、頭の式の
   索引 (ledger, 頭の式, seq) で番号の順に読む(条件の式は索引と同じ key-head-expression)。"
  (<- living (living-events-filter prefix expiry "before_at"))
  (var head-clause NO-CLAUSE)
  (when (is-not head None)
    (<- head-of (key-head-expression "old.payload" head.separator))
    (<- head-params (params-of #(#("key_head" head.head))))
    (:= head-clause (Clause :text (.format " AND {} = :key_head" head-of) :params head-params)))
  (Statement :text (.format "SELECT old.seq, old.at, old.payload FROM {p}append_rows AS old
                       WHERE old.ledger = :ledger{h} AND old.seq > :after{l} ORDER BY old.seq LIMIT :limit"
                            :p prefix :h head-clause.text :l living.text)
             :params (+ (! (params-of #(#("ledger" stream) #("after" after) #("limit" limit)))) head-clause.params living.params)))


(defk stream-end-statement [prefix stream expiry]
  {:pre [(: prefix str) (: stream str) (: expiry (| EventExpiry None))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "追記の列の最後の出来事の番号(出来事が無ければ NULL)を 1 文で読む文を作るため(ReadStreamEnd)。末尾は保持の期限で変わる(期限を
   過ぎた出来事は数えない — 全部過ぎれば NULL = StreamEmpty)ので、ReadEvents と同じ条件で期限を過ぎた出来事を除く(expiry = None は
   期限の無い列)。"
  (<- living (living-events-filter prefix expiry "before_at"))
  (Statement :text (.format "SELECT max(old.seq) FROM {p}append_rows AS old WHERE old.ledger = :ledger{l}" :p prefix :l living.text)
             :params (+ (! (params-of #(#("ledger" stream)))) living.params)))


(defk event-by-key-statement [prefix stream idempotency-key expiry]
  {:pre [(: prefix str) (: stream str) (: idempotency-key str) (: expiry (| EventExpiry None))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "冪等キー 1 つの出来事を、列を辿らずに 1 文で引く文を作るため(ReadEventByKey)。行は 2 種で、1 列目の札で分ける: 'event' = 生きた
   出来事の行(seq・at・payload と、保持の期限を過ぎたか — 回収の前でも過ぎた刻から。判定は読みと回収と同じ expired-event-condition・
   expiry = None は期限の無い列で過ぎない)/ 'retired' = 回収で消した鍵の覚え(retired_keys の seq)。どちらも一意の索引
   (append_rows_idempotency・retired_keys の主キー)に当たるので、列の長さに依らない。どちらにも無ければ 0 行。"
  (var expired "false")
  (var bounds #())
  (when (is-not expiry None)
    (<- condition (expired-event-condition prefix expiry.separator "before_at"))
    (:= expired condition)
    (<- named (params-of #(#("before_at" expiry.before-at))))
    (:= bounds named))
  (Statement :text (.format "SELECT 'event', old.seq, old.at, old.payload, ({e}) FROM {p}append_rows AS old
                       WHERE old.ledger = :ledger AND (old.payload::jsonb) ->> 'idempotencyKey' = :idempotency_key
                     UNION ALL
                     SELECT 'retired', kept.seq, CAST(NULL AS bigint), CAST(NULL AS text), true FROM {p}retired_keys AS kept
                       WHERE kept.ledger = :ledger AND kept.idempotency_key = :idempotency_key" :p prefix :e expired)
             :params (+ (! (params-of #(#("ledger" stream) #("idempotency_key" idempotency-key)))) bounds)))


(defk stream-tail-statement [prefix stream expiry stem]
  {:pre [(: prefix str) (: stream str) (: expiry (| EventExpiry None)) (: stem str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges の頭の文(watch-head-statement)が列ごとに並べる副問い合わせ — 追記の列 stream の最後の生きている出来事の番号と刻
   (seq・at — 生きている出来事が無ければ 0 行)を、(ledger, seq) の索引を後ろから 1 行で引く文を作るため(#3718)。期限の条件は ReadEvents
   と同じ(expiry = None は期限の無い列)。stem = 引数の名の頭(1 つの文に列を並べても名が混ざらない)。
   列の照らしを等号でなく両端の同じ範囲(ledger >= :l AND ledger <= :l — 決まった照合の text では等号と同じ行)で書き、並びを (ledger, seq) の
   索引の並びのまま書くのは、PostgreSQL に (ledger, seq) の索引を後ろから引かせるため: 等号だと列の名が定数とみなされて並びから落ち、
   planner は主鍵 seq を後ろから読んで列の名で濾す道を選ぶ — 末尾より後に他の列の出来事が多いほど読む行が増える(使い捨ての置き場の
   EXPLAIN で、2000 行の列の末尾の読みが 4005 行を読み、範囲の形は 1 行 — test_pg_index_reads.hy が測る)。"
  (<- living (living-events-filter prefix expiry (+ stem "before_at")))
  (Statement :text (.format "SELECT old.seq, old.at FROM {p}append_rows AS old
                       WHERE old.ledger >= :{s}ledger AND old.ledger <= :{s}ledger{l}
                       ORDER BY old.ledger DESC, old.seq DESC LIMIT 1"
                            :p prefix :s stem :l living.text)
             :params (+ (! (params-of #(#((+ stem "ledger") stream)))) living.params)))


(defrecord TailRead
  "WatchChanges が末尾を名指した列 1 つの読みの材料(watch-head-statement に渡す・#3718): stream = 列の名 / expiry = 列の保持の期限の境
   (None = 期限の無い列 — 読みの条件を足さない)。"
  (#^ str stream)
  (#^ (| EventExpiry None) expiry))


(defk watch-head-statement [prefix tails]
  {:pre [(: prefix str) (: tails tuple) (all (gfor tail tails (isinstance tail TailRead)))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges の 1 回ぶんの読みの頭を 1 文 = 1 つの断面で読む文を作るため(#3718): 置き場の版・忘れた位置・変更の列の先頭の番号
   (store-head-statement と同じ 3 つ)の後ろに、末尾を名指した列 tails ごとに最後の生きている出来事の番号と刻の 2 つを並べる(列ごとに
   stream-tail-statement の文を LEFT JOIN … ON TRUE の副問い合わせにする — 生きている出来事の無い列は NULL の組)。変更の列の読み
   (changes-statement)は頭の番号までなので、番号を取ってから commit するまでを書きの錠の中に置く約束(頭の註)の下で、変更と末尾は同じ
   断面の物になる。READ COMMITTED では文ごとに断面が変わるので、末尾を別の文に分けない。tails が空なら store-head-statement と同じ答えの組。"
  (var columns "")
  (var joins "")
  (var params #())
  (for [#(index tail) (enumerate tails)]
    (<- select Statement (stream-tail-statement prefix tail.stream tail.expiry (.format "t{}_" index)))
    (:= columns (+ columns (.format ", t{i}.seq, t{i}.at" :i index)))
    (:= joins (+ joins (.format "\n                       LEFT JOIN ({s}) AS t{i} ON TRUE" :s select.text :i index)))
    (:= params (+ params select.params)))
  (Statement :text (.format "SELECT e.epoch, e.floor, greatest(e.floor, (SELECT coalesce(max({p}row_changes.seq), 0) FROM {p}row_changes)){c}
                       FROM {p}store_epoch AS e{j}
                       WHERE e.id = 1" :p prefix :c columns :j joins)
             :params params))


(defk find-retired-key-statement [prefix stream idempotency-key]
  {:pre [(: prefix str) (: stream str) (: idempotency-key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限で出来事を消した冪等キーの覚え(番号と本文の指紋)を主鍵で 1 行引く文を作るため(#3022)。"
  (Statement :text (.format "SELECT seq, body_digest FROM {p}retired_keys WHERE ledger = :ledger AND idempotency_key = :idempotency_key"
                            :p prefix)
             :params (! (params-of #(#("ledger" stream) #("idempotency_key" idempotency-key))))))


(defk retire-keys-statement [prefix stream keys]
  {:pre [(: prefix str) (: stream str) (: keys tuple) (> (len keys) 0) (all (gfor key keys (isinstance key RetiredKey)))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限で消した出来事の冪等キーの覚え keys(values.RetiredKey の組)を鍵だけの表へ入れる文を作るため(#3022)。配列の引数は
   持たないので、覚えを JSON の配列の引数 1 つ(要素 = idempotency_key・seq・body_digest の object)で運び、jsonb_to_recordset で行に展げる
   (消した出来事の数に依らず文 1 つ)。覚えが既に在る鍵は書き換えない(ON CONFLICT DO NOTHING — memory の drop-events と同じ)。
   空の組は文にしない(呼び手が枝で避ける)。"
  (val retired (json.dumps (lfor key keys {"idempotency_key" key.idempotency-key "seq" key.sequence "body_digest" key.body-digest})
                           :separators #("," ":") :ensure-ascii False))
  (Statement :text (.format "INSERT INTO {p}retired_keys (ledger, idempotency_key, seq, body_digest)
                       SELECT CAST(:ledger AS text), kept.idempotency_key, kept.seq, kept.body_digest
                         FROM jsonb_to_recordset(CAST(:retired AS jsonb)) AS kept(idempotency_key text, seq bigint, body_digest text)
                       ON CONFLICT (ledger, idempotency_key) DO NOTHING" :p prefix)
             :params (! (params-of #(#("ledger" stream) #("retired" retired))))))


(defk expired-event-condition [prefix separator bound]
  {:pre [(: prefix str) (: separator (| str None)) (: bound str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "出来事 old が保持の期限を過ぎた条件の文を作るため(境の刻の引数 :{bound} — 回収と 1 列の読みは before_at・列を並べる読みは列ごとの名。
   列の照らし old.ledger は含まない)。
   separator = None は出来事ごとに数える列(積んだ刻 ≦ 境の刻)・str は組で数える列(ByKeySuffix)の区切りで、組の出来事が全部 before-at
   以下に積まれた組だけ(= 組の最後の出来事から保持の秒 — admission.event-expired? と retention-group-of と同じ境)。回収(expiring-where)と
   読み(living-events-filter)がこの 1 つを使う(#3561)。刻の照らしは append_rows (ledger, at)・組の「新しい出来事」の照らしは
   (ledger, 組の名の式) の索引に当たる(#3614 — 組の名の式は索引と同じ key-suffix-expression)。"
  (if (is separator None)
      (.format "old.at <= :{b}" :b bound)
      (.format "old.at <= :{b} AND NOT EXISTS (
                         SELECT 1 FROM {p}append_rows AS young
                          WHERE young.ledger = old.ledger AND young.at > :{b} AND {young} = {old})"
               :b bound :p prefix :young (! (key-suffix-expression "young.payload" separator))
               :old (! (key-suffix-expression "old.payload" separator)))))


(defk expiring-where [prefix separator]
  {:pre [(: prefix str) (: separator (| str None))] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた列 :ledger の出来事 old の条件の文を作るため(引数 :ledger・:before_at — 期限の条件は
   expired-event-condition)。刈りの候補の読み(expiring-events-statement)と刈り(expire-events-statement・expire-event-groups-statement)が
   同じ条件を使う。"
  (<- expired (expired-event-condition prefix separator "before_at"))
  (+ "old.ledger = :ledger AND " expired))


(defk expiring-params [stream before-at]
  {:pre [(: stream str) (: before-at int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "expiring-where の条件の引数を作るため。"
  (! (params-of #(#("ledger" stream) #("before_at" before-at)))))


(defk expiring-events-statement [prefix stream before-at separator]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator (| str None))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた出来事が在るかを読む文を作るため(在れば 1 行)— 刈りの transaction(書きの錠)は、候補が在る時だけ開く
   (行の刈りの expired-rows と同じ形・候補の無い要求は今までどおり文 1 つ)。"
  (<- where (expiring-where prefix separator))
  (<- params (expiring-params stream before-at))
  (Statement :text (.format "SELECT 1 FROM {p}append_rows AS old WHERE {w} LIMIT 1" :p prefix :w where) :params params))


(defk expire-events-statement [prefix stream before-at]
  {:pre [(: prefix str) (: stream str) (: before-at int)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "積んだ刻が before-at 以下の出来事を捨て、捨てた出来事(番号・刻・payload — read-events-statement と同じ列の並び)を返す文を
   作るため(before-at = 今 − 保持の秒 — admission.event-expired? と同じ境界)。返した行から鍵の覚えを作り、同じ transaction で
   retire-keys-statement が入れる(#3022)。"
  (<- where (expiring-where prefix None))
  (<- params (expiring-params stream before-at))
  (Statement :text (.format "DELETE FROM {p}append_rows AS old WHERE {w} RETURNING old.seq, old.at, old.payload" :p prefix :w where)
             :params params))


(defk key-suffix-of [key separator]
  {:pre [(: key str) (: separator str) (.match SEPARATOR-PATTERN separator)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "冪等キーの式 key の最初の区切り separator より後ろ・区切りを含まないキーはキー全体(admission.retention-group-of と同じ組の名)の
   式を作るため。出来事の冪等キー(key-suffix-expression)と、書きが触る鍵の引数(touched-events-statement)が同じ 1 つを使う。
   区切りは宣言の値を文字列の literal で文に直に置く(引数にすると、文の式が組の名の式の索引 group-index-statement の式と揃わず索引に
   当たらない — #3614)。置けるのは values.SEPARATOR-PATTERN の字(引用符・逆斜線・空白・ASCII の外を含まない)だけで、区切りの長さは
   文字の数 = Python の len と同じ。"
  (.format "(CASE WHEN strpos({k}, '{s}') > 0
                 THEN substr({k}, strpos({k}, '{s}') + {n})
                 ELSE {k} END)" :k key :s separator :n (len separator)))


(defk key-suffix-expression [payload separator]
  {:pre [(: payload str) (: separator str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "出来事の payload の列(綴り payload — 索引は payload・文は old.payload / young.payload)の冪等キーの組の名の式を作るため(key-suffix-of)。
   組の名の式の索引(group-index-statement)と、組を照らす文(expired-event-condition・touched-events-statement)がこの 1 つを使う。"
  (<- suffix (key-suffix-of (.format "(({}::jsonb) ->> 'idempotencyKey')" payload) separator))
  suffix)


(defk key-head-expression [payload separator]
  {:pre [(: payload str) (: separator str) (.match SEPARATOR-PATTERN separator)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "出来事の payload の列(綴り payload — 索引は payload・文は old.payload)の冪等キーの頭(最初の区切り separator より前・区切りを含まない
   キーはキー全体 — admission.key-head-of と同じ頭)の式を作るため。頭の式の索引(head-index-statement)と頭で引く読み
   (read-events-statement)がこの 1 つを使う。区切りは宣言の値を文字列の literal で文に直に置く(key-suffix-of と同じ — 引数にすると文の
   式が索引の式と揃わず索引に当たらない)。"
  (.format "split_part((({}::jsonb) ->> 'idempotencyKey'), '{}', 1)" payload separator))


(defk expire-event-groups-statement [prefix stream before-at separator]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "組で数える列(ByKeySuffix)の刈り: 組の出来事が全部 before-at 以下に積まれた組を捨て、捨てた出来事(番号・刻・payload)を返す
   文を作るため(= 組の最後の出来事から保持の秒 — admission.event-expired? と retention-group-of と同じ境界。返した行は
   expire-events-statement と同じく鍵の覚えになる — #3022)。"
  (<- where (expiring-where prefix separator))
  (<- params (expiring-params stream before-at))
  (Statement :text (.format "DELETE FROM {p}append_rows AS old WHERE {w} RETURNING old.seq, old.at, old.payload" :p prefix :w where)
             :params params))


(defk touched-events-statement [prefix stream before-at separator idempotency-key]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator (| str None)) (: idempotency-key str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "書き(AppendEvent)が触る単位の保持の期限を過ぎた出来事(番号・刻・payload — read-events-statement と同じ列の並び)を読む文を作るため
   (#3605 の D — 書きは置き場の全部を回収せず、自分が触る単位だけを片付ける。書きの錠の transaction の 1 往復目に読み、読んだ物を 2 往復目の
   delete-events-statement で捨てて鍵の覚えへ移す — 同じ錠の中なので、読んだ物がそのまま捨てる物)。単位 = 出来事ごとに数える列
   (separator None)は冪等キー idempotency-key の出来事・組で数える列(ByKeySuffix)はその鍵の組の出来事(組の名の式 key-suffix-of を鍵の
   引数にも当てる)。期限の条件は回収と読みと同じ expiring-where。単位の照らしは索引に当たる: 出来事ごとは冪等キーの一意の索引・組は
   (ledger, 組の名の式) の索引(#3614 — 受付の列への追記ごとに流れるので、列の全部を読まない事が要る)。"
  (<- where (expiring-where prefix separator))
  (<- params (expiring-params stream before-at))
  (val unit (if (is separator None)
                "(old.payload::jsonb) ->> 'idempotencyKey' = :idempotency_key"
                (.format "{} = {}" (! (key-suffix-expression "old.payload" separator))
                         (! (key-suffix-of "CAST(:idempotency_key AS text)" separator)))))
  (<- named (params-of #(#("idempotency_key" idempotency-key))))
  (Statement :text (.format "SELECT old.seq, old.at, old.payload FROM {p}append_rows AS old WHERE {w} AND {u} ORDER BY old.seq"
                            :p prefix :w where :u unit)
             :params (+ params named)))


(defk delete-events-statement [prefix stream sequences]
  {:pre [(: prefix str) (: stream str) (: sequences tuple) (> (len sequences) 0)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "書き(AppendEvent)が 1 往復目に読んだ、触る単位の期限を過ぎた出来事(番号 sequences — touched-events-statement の答え)を捨てる文を作るため
   (#3605 — 同じ書きの錠の transaction の 2 往復目に流す。番号は主鍵で引く)。空の組は文にできないので :pre が断る(呼び手が枝で避ける)。"
  (<- numbers (in-list "s" sequences))
  (Statement :text (.format "DELETE FROM {p}append_rows WHERE ledger = :ledger AND seq IN ({s})" :p prefix :s numbers.text)
             :params (+ (! (params-of #(#("ledger" stream)))) numbers.params)))
