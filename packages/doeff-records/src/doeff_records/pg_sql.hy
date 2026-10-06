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
;;;   row_changes (at)                変更の列の刈り(prune-changes-statement の at <= 境)
;;;   state_rows (ledger, 状態の欄の式) 保持の期限の在る表の状態の欄が、どの表の索引の欄にも無い時だけ(名と式は宣言した索引の欄と同じ — 終端の行の
;;;                                  読み terminal-rows-statement が引く)
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
(import doeff_records.values [RecordsSchema RetiredKey KeepFor ByKeySuffix FIELD-NAME-PATTERN SEPARATOR-PATTERN])

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


(defk field-expression [payload field-name]
  {:pre [(: payload str) (: field-name str) (.match FIELD-NAME-PATTERN field-name)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "行の欄 field-name の値(jsonb)の式を作るため — 欄の式の索引(field-index-statement)と、その索引で引く文の条件(terminal-state-condition)が
   この 1 つを使い、索引の式と文の式の字面を揃える(欄の名は宣言を通った英数字 — 文に直に置く)。payload = payload の列の綴り(索引は
   payload・文は別名つきでもよい)。"
  (.format "(({}::jsonb) -> '{}')" payload field-name))


(defk field-index-statement [prefix field-name]
  {:pre [(: prefix str) (: field-name str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "state_rows の欄 field-name の式の索引 (ledger, 欄の式) を作る文を作るため(宣言した索引の欄と、索引の欄に無い保持の期限の在る表の状態の欄 —
   名も字面も旧い版の宣言した索引の欄の文と同じ)。"
  (<- name (index-name prefix field-name))
  (<- expression (field-expression "payload" field-name))
  (Statement :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows (ledger, {e})" :i name :p prefix :e expression) :params #()))


(defk group-index-statement [prefix separator]
  {:pre [(: prefix str) (: separator str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "組で数える列の区切り separator の組の名の式の索引 append_rows (ledger, 組の名の式) を作る文を作るため(区切りごとに 1 つ — 同じ区切りの列は
   索引を分け合う)。式は文の組の照らしと同じ key-suffix-expression。"
  (val name (+ prefix "append_rows_group_" (cut (.hexdigest (hashlib.sha1 (.encode separator "utf-8"))) 12)))
  (<- group (key-suffix-expression "payload" separator))
  (Statement :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}append_rows (ledger, {g})" :i name :p prefix :g group) :params #()))


(defk schema-statements [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表を用意する文の列(何度流しても同じ — IF NOT EXISTS / ON CONFLICT DO NOTHING)。流すのは pg.prepare-records-store だけ
   (移行の錠の transaction の中で・process ごとに 1 度)。字面は旧い版と同じ(頭の註)。"
  (val p prefix)
  (val fields (sorted (sfor decl (.values schema.tables) name decl.indexes name)))
  (val tables
    #((Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}state_rows (
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
      ;; 版の初めの値 = 行を作った時の server の時計の ms(頭の註の store_epoch・#3632)。行が在れば版に触らない。
      (Statement :text (.format "INSERT INTO {p}store_epoch (id, epoch, floor)
           VALUES (1, (extract(epoch from clock_timestamp()) * 1000)::bigint, 0) ON CONFLICT (id) DO NOTHING" :p p)
                 :params #())))
  ;; 宣言した索引の欄ごとに式の索引 1 つ(欄の名は FIELD-NAME-PATTERN を通った英数字だけ — 文に直に置ける)。
  (var indexes #())
  (for [name fields]
    (<- statement (field-index-statement p name))
    (:= indexes (+ indexes #(statement))))
  ;; 旧い版より後に足した表と索引は旧い版の文の後ろに足した順に並べる(旧い版の文の字面と順はそのまま — 検 test_pg_sql.hy)。
  (<- added (added-index-statements p schema))
  (+ tables
     indexes
     #((Statement :text (.format "CREATE TABLE IF NOT EXISTS {p}retired_keys (
           ledger text NOT NULL, idempotency_key text NOT NULL, seq bigint NOT NULL, body_digest text NOT NULL,
           PRIMARY KEY (ledger, idempotency_key))" :p p) :params #()))
     added))


(defk added-index-statements [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "旧い版より後に足した索引の文の列を宣言 schema から作るため(#3614 — 頭の註の 4 つ。どれも CREATE INDEX IF NOT EXISTS で、表の欄と
   データに触らない)。並びは (ledger, at)・区切りの順の組の名の式・変更の列の刻・欄の名の順の状態の欄の式。状態の欄の索引は、保持の期限の在る
   表(KeepFor かつ終端の語を持つ — 回収の候補の読み terminal-rows-statement を流す表)の状態の欄のうち、どの表の索引の欄にも無い物だけ
   (在る欄は旧い版の文が同じ名で作る)。"
  (val p prefix)
  (val separators (sorted (sfor decl (.values schema.streams) :if (isinstance decl.retention-group ByKeySuffix)
                                decl.retention-group.separator)))
  (val indexed (sfor decl (.values schema.tables) name decl.indexes name))
  (val state-fields (sorted (sfor decl (.values schema.tables)
                                  :if (and (isinstance decl.retention KeepFor) decl.terminal (not-in decl.state-field indexed))
                                  decl.state-field)))
  (var groups #())
  (for [separator separators]
    (<- statement (group-index-statement p separator))
    (:= groups (+ groups #(statement))))
  (var states #())
  (for [field state-fields]
    (<- state-index (field-index-statement p field))
    (:= states (+ states #(state-index))))
  (+ #((Statement :text (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger_at ON {p}append_rows (ledger, at)" :p p) :params #()))
     groups
     #((Statement :text (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_at ON {p}row_changes (at)" :p p) :params #()))
     states))


(defk drop-statements [prefix]
  {:pre [(: prefix str)] :post [(: % tuple)]
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
  (<- expression (field-expression payload state-field))
  (<- states (in-list stem (tuple (gfor word terminal (json.dumps word :ensure-ascii False)))))
  (Clause :text (.format "{e} IN ({s})" :e expression :s (.join ", " (gfor param states.params (.format ":{}::jsonb" param.name))))
          :params states.params))


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


(defk list-rows-statement [prefix table after-key where-json limit expiry]
  {:pre [(: prefix str) (: table str) (: after-key (| str None)) (: where-json dict) (: limit int) (: expiry (| RowExpiry None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "鍵の綴りの順(COLLATE \"C\" = 符号点の順・鍵の綴りは ASCII だけ)の 1 頁 + 1 行(続きの有無を知るため)を読む文を作るため。
   where-json = 欄 → 値の JSON の綴り(欄の名は宣言を通った英数字)。expiry = 表の保持の期限の境(None = 期限の無い表)— 期限を過ぎた
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
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{u} ORDER BY key COLLATE \"C\" LIMIT :limit"
                            :p prefix :w (.join " AND " clauses) :u unexpired.text)
             :params (+ (! (params-of (tuple pairs))) unexpired.params)))


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


(defk read-events-statement [prefix stream after limit expiry]
  {:pre [(: prefix str) (: stream str) (: after int) (: limit int) (: expiry (| EventExpiry None))] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "追記の列の after より後を読む文を作るため。expiry = 列の保持の期限の境(None = 期限の無い列)— 期限を過ぎた出来事は、回収の前でも
   読みに出さない(上限の前に除く)。"
  (<- living (living-events-filter prefix expiry "before_at"))
  (Statement :text (.format "SELECT old.seq, old.at, old.payload FROM {p}append_rows AS old
                       WHERE old.ledger = :ledger AND old.seq > :after{l} ORDER BY old.seq LIMIT :limit"
                            :p prefix :l living.text)
             :params (+ (! (params-of #(#("ledger" stream) #("after" after) #("limit" limit)))) living.params)))


(defk stream-end-statement [prefix stream expiry stem]
  {:pre [(: prefix str) (: stream str) (: expiry (| EventExpiry None)) (: stem str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "追記の列 stream の最後の生きている出来事の番号と刻(seq・at — 生きている出来事が無ければ 0 行)を、(ledger, seq) の索引を後ろから 1 行で
   引く文を作るため(ReadStreamEnd と、WatchChanges の頭の文 watch-head-statement の列ごとの副問い合わせが、この 1 つを使う・#3718)。末尾は
   保持の期限で変わる(期限を過ぎた出来事は数えない — 全部過ぎれば 0 行 = StreamEmpty)ので、ReadEvents と同じ条件で期限を過ぎた出来事を
   除く(expiry = None は期限の無い列)。stem = 引数の名の頭(1 つの文に列を並べても名が混ざらない — ReadStreamEnd は空)。
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
   stream-end-statement の文を LEFT JOIN … ON TRUE の副問い合わせにする — 生きている出来事の無い列は NULL の組)。変更の列の読み
   (changes-statement)は頭の番号までなので、番号を取ってから commit するまでを書きの錠の中に置く約束(頭の註)の下で、変更と末尾は同じ
   断面の物になる。READ COMMITTED では文ごとに断面が変わるので、末尾を別の文に分けない。tails が空なら store-head-statement と同じ答えの組。"
  (var columns "")
  (var joins "")
  (var params #())
  (for [#(index tail) (enumerate tails)]
    (<- select Statement (stream-end-statement prefix tail.stream tail.expiry (.format "t{}_" index)))
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
