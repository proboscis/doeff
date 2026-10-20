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
;;;   state_rows (表の状態の欄の式)    保持の期限の在る表の状態の欄が、その表の索引の欄に無い時だけ(式は宣言した索引の欄と同じ表の欄の式 —
;;;                                  終端の行の読み terminal-rows-statement が引く)
;;;   state_rows (並べる式 DESC NULLS LAST, key DESC)  表の上から読む欄(TableDecl.descending)ごとに 1 つ — 上から読む読み
;;;                                  (top-rows-statement)が索引の順に 1 頁だけ辿る。式は ordered-expression の 1 か所(下の註)
;;;   state_rows (ledger, key COLLATE "C")  鍵の綴りの順の頁読み(list-rows-statement の ledger = :ledger と key COLLATE "C" > :after・
;;;                                  ORDER BY key COLLATE "C")が索引の範囲を 1 頁 + 1 行だけ辿る(下の註)
;;; 欄の式の索引は表の欄の式の索引(card ki-2176012d1804): 宣言した表 1 つ × 欄 1 つに、表の欄の式
;;; `(CASE WHEN ledger = '<表>' THEN ((payload::jsonb) -> '<欄>') END)`(table-field-expression — その表の行の欄の値・他の表の行は NULL)の
;;; 索引 1 つ(名 = table-field-index-name)を張る。state_rows は全部の表の行を持つ 1 つの表なので、全部の行の payload を読み直す索引
;;; (旧い形 — 名 = index-name)を足す費用は置き場の全体の大きさに比例した(2026-10-18 に 444,566 行・6.3 GB の置き場で 1 本 3 分 23 秒)。
;;; 表の欄の式の索引は、その表の行の payload だけを読んで並べる(主の heap は全部の行を辿るが、他の表の行は CASE が payload を開かずに
;;; NULL にする)。
;;; 表の欄の式にする訳(2026-10-18 に本番の 44,296 行の表 message で計画を読んだ): 等号 2 つ(宛先と kind)の 1 頁は、宛先の索引で宛先の
;;; 行 5,283 行を引き、1 行ずつ payload を jsonb に読み直して kind で 5,249 行を捨てた(0.43〜9 秒)。2 つの欄の索引を組んで(BitmapAnd)
;;; 当たる 34 行だけを読む計画を、planner は選べなかった: 2 つの索引が同じ条件 ledger = '<表>' を使う(旧い形は列 ledger の条件・
;;; 前の部分索引 `… WHERE ledger = '<表>'`〔card ki-954c58540d0f〕は述語)と、planner は後の索引を前の索引と重なる物として組まない
;;; (PostgreSQL の choose_bitmap_and の重なりの判じ)。表の欄の式の索引は、条件が欄ごとに別の式なので組め、当たる行の数で決まる読みに
;;; なる(本番の分布の 46.8 万行の置き場で 280 ms → 6 ms)。読みの文は、絞りの欄が全部表の欄の式で照らせる時、条件 ledger = :ledger を
;;; 置かない(表の欄の式の条件がその表の行だけに合う — 置くと planner は ledger の選択度を式の選択度に重ねて掛け、ledger だけの索引を
;;; 組みに足す)。
;;; 部分索引にしない訳: 述語 ledger = '<表>' は上の重なりで組めず、述語 `<式> IS NOT NULL` は planner が部分索引の式の統計を読まないので
;;; 式の統計(CREATE STATISTICS)が要り、その統計は値の組の値 1 つずつの見積もりで読み直されて、値 995 個の組の計画に 55〜65 ms かかった
;;; (全部の行の索引の式の統計なら 3.5 ms)。全部の行の索引の他の表の行の項は NULL の 1 項(payload を開かない)で、本番の 44 万行で
;;; 1 本 約 10 MB(旧い形 1 本 約 56 MB)。式の統計は表の標本(ANALYZE)が索引の式から取る(sampled — 作った後に標本を取り直す)。
;;; 旧い形の索引と前の部分索引(名 = table-index-name)は aliases: 在る欄は、表の欄の式の索引を作り終えるまで、その欄を前の文の形
;;; (ledger = :ledger と欄の式 — field-expression)で照らす読みに答える(索引の用意は aliases が在っても表の欄の式の索引を作る)。
;;; 旧い形の索引を外す(2026-10-19): 宣言の索引が全部そろい(差が無い)、表の欄の式の索引の欄が全部表の欄の式で照らせる(見積もれる)
;;; ようになった後は、旧い形の索引と前の部分索引を引く読みは無い(planner が ledger で引ける小さい索引として選ぶ事はあるが、主キー
;;; (ledger, key) が同じ行を引く)。一方 payload を変える書きは HOT にならず、書くたびに表の全部の索引を更新する(本番の 68 万行の置き場で
;;; 旧い形 23 本・約 1.9 GB — state_rows の索引の 6 割・21 本は 13 時間 idx_scan 0)。索引の用意がその時から DROP INDEX CONCURRENTLY で外す
;;; (名の形で見分ける — retired-index?。宣言の索引の名には当たらない)。起動の道には置かない(表に SHARE UPDATE EXCLUSIVE だけを取り、
;;; 行の読み書きを止めない)。
;;; 上から読む欄の索引: 並べる式 ordered-expression は、表の行の欄の値が JSON の整数の時だけ numeric、他(他の表の行・欄の無い行・整数で
;;; ない値)は NULL(外の CASE が他の表の行の payload を開かない — 表の欄の式と同じ)。宣言の無い欄の上から読む読みは、表の全部の行の
;;; payload を jsonb に読み直して並べ替える(2026-10-19 — 4.7 万行の表 message で 1 頁 0.6〜0.8 秒・負荷の下で読みの期限を越えた)。
;;; 索引の在る形は、索引の順に 1 頁 + 1 行だけを辿る(同じ置き場で 2〜7 ms)。続きの位置は行の比べ (式, key) < (値, 鍵) — OR に分けた形は
;;; 索引の範囲にならず、位置より下の行を全部読んで並べ替える。文は条件 ledger = :ledger を置かない(式が NULL でない行は表の行だけ)。
;;; 鍵の綴りの順の索引: 列 key と主キー (ledger, key) の索引は database の既定の照合順(本番は en_US.utf8)で並び、頁読みの順
;;; key COLLATE "C"(memory の置き場と同じ符号点の順)では主キーを辿れない。索引の無い置き場の 1 頁は、表の全部の行を読んで並べ替えて
;;; 上から limit + 1 行を取る(2026-10-19 の本番 — 1.88 万行の表で 1 頁ごとに 1.88 万行・全頁の読みは行の数の 2 乗)。照合順 "C" の
;;; (ledger, key) の索引は 1 頁 + 1 行だけを辿る。列 key の照合順を "C" に替える形(ALTER COLUMN … COLLATE "C")にしない訳: 替える文は
;;; 表の排他の錠(ACCESS EXCLUSIVE)を持ったまま主キーと key を含む索引(上から読む欄の索引 — payload を開き直す式)を作り直し、その間
;;; 行の読み書きが全部止まる。足す索引は索引の用意が CONCURRENTLY で作り、読みを止めない(速さだけの索引 — 無くても答えは同じ)。
;;; 索引の用意(pg.hy の頭の註): 移行の transaction は、その transaction で生まれた表の索引だけを作る(空の表 — 一瞬)。既に在る表に
;;; 足りない索引は、索引の用意の task が transaction の外の CREATE INDEX CONCURRENTLY で作る(declared-indexes が宣言の索引の全部・
;;; indexes-statement が置き場の索引の観測)。
;;; 表の名は接頭辞つき(既定 records_)— 同じ database に在る別の置き場の同名の表と混ざらない。
;;; DDL は SqlEnsureTables の宣言に書き換えない(式の索引と bigserial を宣言で表せない)— 旧い版と同じ字面のまま流す
;;; (検 test_pg_sql.hy が旧い版の字面と比べる)。旧い版と字面が違うのは store_epoch の行を作る文の版の初めの値だけ(#3632)。
;;; どれも IF NOT EXISTS / ON CONFLICT DO NOTHING なので、字面が変わっても旧い版へ戻せ(表の形は変わらない)、既に在る置き場の
;;; store_epoch の行(旧い版が作った epoch 1 を含む)には触らない。
;;;
;;; 書きの錠(card ki-0306f5a614b4 — 前は置き場の書き全部を advisory lock 1 つで直列にし、受付の列の追記が他の表の書きと手入れの後ろで
;;; 待った): bigserial の番号は commit の順と揃わない(番号 5 を取った書きより先に番号 6 の書きが commit すると、6 まで読んだ読み手は 5 を
;;; 永久に取りこぼす)。番号の読みの約束 = 「読み手が見た最大の番号より小さい番号は、読み手が読む範囲で全部 commit 済み」(WatchChanges の
;;; 「ちょうど 1 回」と ReadEvents の位置の読みの土台)。約束が要るのは、同じ読みが 1 つの位置で読む範囲の中だけ:
;;;   - 変更の列(row_changes.seq)は全部の表で 1 つの番号の列で、WatchChanges は頼んだ表の全部を 1 つの位置で読み、ReadChangeLog は表で
;;;     絞らずに読み、置き場の頭(store-head-statement)は全部の表の最大の番号を読む → 行の書きは全部の表で 1 つの錠の単位(行の単位 ROWS-UNIT)
;;;   - 追記の列(append_rows.seq)の番号は全部の列で 1 つの bigserial だが、読みは全部が列 1 つの中(ledger = :ledger と seq > :after・
;;;     列の末尾・冪等キーの引き)→ 追記は列ごとの錠の単位(stream-unit)。他の列の番号が間に挟まっても、その列の読みには見えない
;;; 書き 1 回は、番号を取ってから commit するまで、触る単位の錠(部分の錠 — unit-lock-key)を排他で持つ。部分の錠の前に置き場全体の錠
;;; (writer-lock-key)を共有で持つ(SqlTransaction の locks — 共有どうしは並んで持てる)。置き場全体を変える書き(版の繰り上げ)と表の用意は
;;; 置き場全体の錠を排他で持ち、全部の部分の書きと互いを待つ。錠の順は置き場全体(共有か排他)→ 部分の錠(単位の名の順)の 1 つで、
;;; 束の追記(AppendEvents)が列を 2 つ以上触る時も名の順に取る(逆の順の取り合いを作らない)。
;;; 単位ごとの書きと錠の単位:
;;;   PutRow・PutRows・行の回収(SweepExpired の行)・変更の刈り取り(PruneChanges — 変更の列と床だけを触る)= 行の単位
;;;   AppendEvent・AppendEvents・出来事の回収(SweepExpired の列ごと)= 触る列の単位(束は触る列の全部)
;;;   版の繰り上げ(AdvanceStoreEpoch — 全部の行の版と変更の列を変える)= 置き場全体を排他
;;; 置き場全体の錠の鍵は旧い版の錠の文の引数と同じ字面(接頭辞 + "records-writer" / 接頭辞 + "records-migrate" — 区切りなし)。旧い版は
;;; 書きのたびに置き場全体の錠を排他で取るので、旧い版と新しい版が同じ置き場に重なった間(入れ替え)も、旧い版の書きは新しい版の全部の
;;; 書きと互いを待ち、番号を取りこぼさない。鍵の字面を変えると、重なった間に別々の錠を取り、変更の列の番号を取りこぼす(戻せない)。
;;; 部分の錠の鍵は置き場全体の鍵 + "/" + 単位の名(hashtext が 2 つの鍵を同じ数にしても、余計に待つだけで約束は破れない)。
;;;
;;; 配列の引数は持たない(SqlValue の閉じた集合に配列は無い): `= ANY(配列)` は `IN (:t0, :t1, …)` に展げる。空の組は文にできない
;;; (`IN ()` は構文の誤り)ので、in-list は空の組を断り、呼び手(pg.hy)が空の組なら文を流さない枝を持つ。
;;;
;;; 保持の期限(#3561): 読みの文(行・一覧・変更の列・追記の読み・列の末尾)は、回収を待たずに期限を過ぎた行と出来事を文の条件で除く
;;; (RowExpiry・EventExpiry の境 — 境の刻は admission.retention-cutoff-ms)。出来事の期限の条件は回収と読みで同じ expired-event-condition の
;;; 1 つ。期限の無い表と列では条件を足さない(文は前と同じ)。書き(#3605 の D)は回収の文を流さず、自分が触る単位の期限を過ぎた出来事だけを
;;; 同じ条件で読み(touched-events-statement — 書きの 1 往復目)、読んだ番号で捨てる(delete-events-statement — 2 往復目)。
;;; 回収(SweepExpired)も番号で捨てる: 書きの錠の外で期限を過ぎた出来事の番号を上限つきで読み(expiring-candidates-statement)、錠の中で
;;; その番号の出来事だけを、条件を読み直して捨てる(expire-chosen-events-statement)。錠の中の文が触る行は番号の数で縛られ、列の行の数の
;;; 見積もりに依らない(2026-10-18 — 列の全部を条件で消す 1 文は、planner が ledger と at の相関を知らず期限を過ぎた出来事を実際の数百倍に
;;; 見積もって列の生きた出来事を全部読む計画を選び、書きの錠を 13〜21 秒持って記録の service の要求を切らせた)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hashlib)
(import json)
(import re)
(import doeff_core_effects.sql_effects [SqlParam])
(import doeff_records.admission [key-text])
(import doeff_records.values [RecordsSchema RetiredKey KeepFor ByKeySuffix ListCursor FIELD-NAME-PATTERN SEPARATOR-PATTERN TABLE-NAME-PATTERN])

(val MODULE-TAGS {:context "records" :role "foundation"})
(val PREFIX-PATTERN (re.compile "^[a-z_][a-z0-9_]{0,30}$"))
;; 旧い形の索引の名の接頭辞の後ろ(index-name の ix_<欄の指紋> と table-index-name の ix_<欄の指紋>_<表の指紋> — retired-index?)。
(val RETIRED-INDEX-PATTERN (re.compile "ix_[0-9a-f]{12}(_[0-9a-f]{12})?"))
(val DEFAULT-PREFIX "records_")
;; 錠の鍵の接尾(接頭辞の後ろに区切りなしで続ける — 旧い版の錠の文の引数と同じ字面)。
(val WRITER-LOCK-SUFFIX "records-writer")
(val MIGRATE-LOCK-SUFFIX "records-migrate")
;; 置き場の部分の書きの錠の単位(頭の註): 行の単位(全部の表の行の書き — 変更の列の番号は全部の表で 1 つ)と、列の単位の名の頭(列ごと)。
;; 部分の錠の鍵 = 置き場全体の書きの錠の鍵 + 区切り + 単位の名。
(val ROWS-UNIT "rows")
(val STREAM-UNIT-PREFIX "stream:")
(val UNIT-LOCK-SEPARATOR "/")
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
   (表, 欄) の組の並び(宣言した索引の欄 — 無ければ NotIndexed。空 = 速さだけの索引)/ scoped = この索引が在れば読みの文が表の欄の式
   (table-field-expression)で照らす (表, 欄) の組の並び(表の欄の式の索引だけ)/ aliases = この索引を作り終えるまで serves の読みに答える
   旧い形の索引の名(頭の註 — 在っても作る)/ sampled = 見積もりが索引の式の統計(表の標本 — ANALYZE が取る)に依る索引か(作った後に
   表の標本を取り直す — 作ったばかりの索引の式の統計は空)。"
  (#^ str name)
  (#^ str relation)
  (#^ str text)
  (#^ (get tuple #(IndexedField ...)) serves)
  (#^ (get tuple #(IndexedField ...)) scoped)
  (#^ (get tuple #(str ...)) aliases)
  (#^ bool sampled))


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
  "置き場全体の書きの錠の鍵を作るため(頭の註 — 部分の書きは共有で、置き場全体を変える書きと表の用意は排他で持つ)。"
  (+ prefix WRITER-LOCK-SUFFIX))


(defk stream-unit [stream]
  {:pre [(: stream str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "列 stream の追記と回収が排他で持つ錠の単位の名を作るため(頭の註 — 列ごとに 1 つ)。"
  (+ STREAM-UNIT-PREFIX stream))


(defk unit-lock-key [prefix unit]
  {:pre [(: prefix str) (: unit str) (or (= unit ROWS-UNIT) (.startswith unit STREAM-UNIT-PREFIX))] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "置き場の部分の書きの錠(錠の単位 unit — 行の単位 ROWS-UNIT か列の単位 stream-unit)の鍵を作るため(頭の註)。"
  (+ prefix WRITER-LOCK-SUFFIX UNIT-LOCK-SEPARATOR unit))


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
  "旧い形の欄の式の索引(state_rows の全部の行 — 旧い版と同じ名)の名を作るため。表の欄の式の索引の aliases(頭の註)。"
  (<- digest (digest-of field-name))
  (+ prefix "ix_" digest))


(defk table-index-name [prefix table field-name]
  {:pre [(: prefix str) (: table str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "前の部分索引(表 table の欄 field-name の (欄の式) WHERE ledger = '<表>' — card ki-954c58540d0f)の名を作るため。表の欄の式の索引の
   aliases(頭の註)。"
  (<- field-digest (digest-of field-name))
  (<- table-digest (digest-of table))
  (.format "{}ix_{}_{}" prefix field-digest table-digest))


(defk retired-index? [prefix name]
  {:pre [(: prefix str) (: name str)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "置き場の索引の名 name が、宣言が張らなくなった旧い形の索引(index-name の全部の行の (ledger, 欄の式)・table-index-name の前の部分索引)の
   名かを判じるため(頭の註の「旧い形の索引を外す」— 索引の用意が外す物を見分ける)。宣言の索引の名(fx_・ox_・置き場の形の索引)には
   当たらない。"
  (and (.startswith name prefix)
       (is-not (.fullmatch RETIRED-INDEX-PATTERN (cut name (len prefix) None)) None)))


(defk table-field-index-name [prefix table field-name]
  {:pre [(: prefix str) (: table str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表 table の欄 field-name の表の欄の式の索引(頭の註)の名を作るため(接頭辞 31 字 + 30 字 — PostgreSQL の名の上限 63 字の内)。"
  (<- field-digest (digest-of field-name))
  (<- table-digest (digest-of table))
  (.format "{}fx_{}_{}" prefix field-digest table-digest))


(defk field-expression [payload field-name]
  {:pre [(: payload str) (: field-name str) (.match FIELD-NAME-PATTERN field-name)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "行の欄 field-name の値(jsonb)の式を作るため — 旧い形の索引と前の部分索引が引く読みの条件と、表の欄の式(table-field-expression)が
   この 1 つを使い、索引の式と文の式の字面を揃える(欄の名は宣言を通った英数字 — 文に直に置く)。payload = payload の列の綴り(索引は
   payload・文は別名つきでもよい)。"
  (.format "(({}::jsonb) -> '{}')" payload field-name))


(defk table-field-expression [alias table field-name]
  {:pre [(: alias str) (: table str) (.match TABLE-NAME-PATTERN table) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表 table の行の欄 field-name の値(jsonb — 他の表の行と欄の無い行は NULL)の式を作るため(頭の註の表の欄の式)。表の欄の式の索引
   (field-index)と、その索引で引く読みの条件(where-condition・any-of-filter・terminal-rows-statement)がこの 1 つを使い、索引の式と
   文の式の字面を揃える。alias = 列の別名(空 = 別名なし — 索引の式)。表の名は宣言を通った英小文字・数字・_・- だけ
   (values.TABLE-NAME-PATTERN — 引用符を含まないので文に直に置ける。引数にすると文の式が索引の式と揃わず索引に当たらない)。"
  (val columns (if alias (+ alias ".") ""))
  (<- value (field-expression (+ columns "payload") field-name))
  (.format "(CASE WHEN {c}ledger = '{t}' THEN {v} END)" :c columns :t table :v value))


(defk ordered-expression [table field-name]
  {:pre [(: table str) (.match TABLE-NAME-PATTERN table) (: field-name str) (.match FIELD-NAME-PATTERN field-name)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表 table の上から読む欄 field-name の並べる式を作るため(頭の註の上から読む欄の索引 — 値が JSON の整数の行は numeric・他は NULL。
   admission.descending-value? と同じ行を並べる)。上から読む欄の索引(ordered-index)と、その索引で辿る読みの文(top-rows-statement)が
   この 1 つを使い、索引の式と文の式の字面を揃える。"
  (<- value (field-expression "payload" field-name))
  (.format "(CASE WHEN ledger = '{t}' THEN (CASE WHEN jsonb_typeof({v}) = 'number' AND ({v})::text ~ '^-?[0-9]+$' THEN ({v})::text::numeric END) END)"
           :t table :v value))


(defk ordered-index-name [prefix table field-name]
  {:pre [(: prefix str) (: table str) (: field-name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "表 table の上から読む欄 field-name の索引(頭の註)の名を作るため(接頭辞 31 字 + 30 字 — PostgreSQL の名の上限 63 字の内)。"
  (<- field-digest (digest-of field-name))
  (<- table-digest (digest-of table))
  (.format "{}ox_{}_{}" prefix field-digest table-digest))


(defk ordered-index [prefix table field-name]
  {:pre [(: prefix str) (: table str) (.match TABLE-NAME-PATTERN table) (: field-name str)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "表 table の上から読む欄 field-name の索引 state_rows (並べる式 DESC NULLS LAST, key DESC) を作るため(頭の註 — 速さだけの索引: 無くても
   上から読む読みは同じ行を返すので、ListRows を待たせない)。見積もりは索引の式の統計(sampled — 表の標本が取る)。"
  (<- name (ordered-index-name prefix table field-name))
  (<- expression (ordered-expression table field-name))
  (DeclaredIndex :name name :relation "state_rows"
                 :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows ({e} DESC NULLS LAST, key COLLATE \"C\" DESC)"
                                :i name :p prefix :e expression)
                 :serves #()
                 :scoped #()
                 :aliases #()
                 :sampled True))


(defk field-index [prefix table field-name serves]
  {:pre [(: prefix str) (: table str) (.match TABLE-NAME-PATTERN table) (: field-name str) (: serves bool)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "表 table の欄 field-name の表の欄の式の索引 state_rows (表の欄の式) を作るため(頭の註 — 宣言した索引の欄と、その表の索引の欄に無い
   保持の期限の在る表の状態の欄)。部分索引にしない(頭の註 — 述語 ledger = '<表>' は同じ表の欄の索引どうしを planner が組まず、述語
   `<式> IS NOT NULL` は式の統計の読み直しで値の組の計画が重い)。見積もりは索引の式の統計(sampled — 表の標本が取る)。serves = 宣言した
   索引の欄か(ListRows がこの索引を待つ — NotIndexed の判断)。aliases = 旧い形の索引と前の部分索引(作り終えるまで前の文の形の読みに
   答える)。"
  (<- name (table-field-index-name prefix table field-name))
  (<- partial (table-index-name prefix table field-name))
  (<- legacy (index-name prefix field-name))
  (<- expression (table-field-expression "" table field-name))
  (val indexed (IndexedField :table table :field field-name))
  (DeclaredIndex :name name :relation "state_rows"
                 :text (.format "CREATE INDEX IF NOT EXISTS {i} ON {p}state_rows ({e})" :i name :p prefix :e expression)
                 :serves (if serves #(indexed) #())
                 :scoped #(indexed)
                 :aliases #(legacy partial)
                 :sampled True))


(defk fixed-index [name relation text]
  {:pre [(: name str) (: relation str) (: text str)] :post [(: % DeclaredIndex)]
   :tags {:context "records" :role "foundation"}}
  "宣言に依らない索引(置き場の形の索引 — 速さと一意の索引)1 つを作るため(読みを待たせない・旧い名の写しは無い)。"
  (DeclaredIndex :name name :relation relation :text text :serves #() :scoped #() :aliases #() :sampled False))


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
  "索引 1 つを表の用意の文にするため(移行の transaction は、索引の表がその transaction で生まれた時だけ流す)。"
  #((SchemaStep :statement (Statement :text index.text :params #()) :index index)))


(defk schema-steps [prefix schema]
  {:pre [(: prefix str) (: schema RecordsSchema)] :post [(: % (get tuple #(SchemaStep ...)))]
   :tags {:context "records" :role "foundation"}}
  "表を用意する文の列(何度流しても同じ — IF NOT EXISTS / ON CONFLICT DO NOTHING)を、索引の文かどうかの印つきで作るため。流すのは
   pg.prepare-records-store だけ(移行の錠の transaction の中で・process ごとに 1 度 — 既に在る表の索引の文は流さない・頭の註)。
   表と置き場の形の索引の字面と順は旧い版と同じ(頭の註)。宣言した索引の欄の索引は表の欄の式の索引(表の名の順・欄の名の順)。"
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
  ;; 宣言した索引の欄ごとに表の欄の式の索引 1 つ(欄の名は FIELD-NAME-PATTERN を通った英数字だけ — 文に直に置ける)。
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
    (<- added-made (index-steps index))
    (:= added-steps (+ added-steps added-made)))
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
   無い時だけ(在る欄はその表の宣言した索引の欄の表の欄の式の索引が答える)。続いて表の名の順・欄の名の順の上から読む欄の索引(頭の註)。
   最後に鍵の綴りの順の索引 state_rows (ledger, key COLLATE \"C\")(頭の註)。"
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
  (val ordered-fields (lfor table (sorted schema.tables)
                            field (sorted (. (get schema.tables table) descending))
                            #(table field)))
  (var ordered #())
  (for [#(table field) ordered-fields]
    (<- ordered-made (ordered-index p table field))
    (:= ordered (+ ordered #(ordered-made))))
  (+ #((! (fixed-index (+ p "append_rows_ledger_at") "append_rows"
                       (.format "CREATE INDEX IF NOT EXISTS {p}append_rows_ledger_at ON {p}append_rows (ledger, at)" :p p))))
     groups
     #((! (fixed-index (+ p "row_changes_at") "row_changes"
                       (.format "CREATE INDEX IF NOT EXISTS {p}row_changes_at ON {p}row_changes (at)" :p p))))
     states
     ordered
     #((! (fixed-index (+ p "state_rows_key_c") "state_rows"
                       (.format "CREATE INDEX IF NOT EXISTS {p}state_rows_key_c ON {p}state_rows (ledger, key COLLATE \"C\")" :p p))))))


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
  "置き場の表の索引の名と、使える形か(indisvalid — CONCURRENTLY の作りかけは False で残る)と、見積もれるか(索引の式の統計が在るか、
   表に統計がまだ 1 つも無いか — 既に統計の在る表に足して標本を取り直す前の式の索引は、値の組の見積もりが既定の選択度の積み上げになり、
   表の行を頭から読む計画が選ばれる・DeclaredIndex の sampled)を読む文を作るため(索引の観測)。統計は持ち主の役が読める pg_stats から
   読む(索引の式の統計の行の tablename は索引の名)。"
  (<- relations (relation-names prefix INDEXED-RELATIONS))
  (Statement :text (.format "SELECT c.relname, i.indisvalid,
                              EXISTS (SELECT 1 FROM pg_stats AS s WHERE s.schemaname = n.nspname AND s.tablename = c.relname)
                              OR NOT EXISTS (SELECT 1 FROM pg_stats AS t WHERE t.schemaname = n.nspname AND t.tablename = r.relname)
                         FROM pg_index AS i JOIN pg_class AS c ON c.oid = i.indexrelid JOIN pg_class AS r ON r.oid = i.indrelid
                              JOIN pg_namespace AS n ON n.oid = c.relnamespace
                       WHERE i.indrelid IN ({r})" :r relations.text)
             :params relations.params))


(defk concurrent-index-statement [index]
  {:pre [(: index DeclaredIndex)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "既に在る表に足りない索引を、表の書きを止めずに transaction の外で作る文(CREATE … INDEX CONCURRENTLY IF NOT EXISTS)を作るため
   (字面は移行の文の INDEX の後ろに CONCURRENTLY を挟んだ物 — 名と形は移行の文と同じ)。"
  (Statement :text (.replace index.text "INDEX IF NOT EXISTS" "INDEX CONCURRENTLY IF NOT EXISTS" 1) :params #()))


(defk analyze-statement [prefix relation]
  {:pre [(: prefix str) (: relation str) (in relation STORE-RELATIONS)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "式の索引を作った表の標本を取り直す文を作るため(作ったばかりの索引の式の統計は空で、次の autovacuum の analyze まで見積もりに
   効かない — DeclaredIndex の sampled)。"
  (Statement :text (.format "ANALYZE {}{}" prefix relation) :params #()))


(defk drop-index-statement [name]
  {:pre [(: name str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "索引 name(使えない形で残った宣言の索引〔CONCURRENTLY の作りかけ〕か、旧い形の索引 — retired-index?)を、表の読み書きを止めずに
   消す文を作るため(名は宣言から作った名か、旧い形の名の形を通った観測の名 — 英小文字・数字・_ だけなので文に直に置ける)。"
  (Statement :text (.format "DROP INDEX CONCURRENTLY IF EXISTS {}" name) :params #()))


(defk index-lock-key [prefix]
  {:pre [(: prefix str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠の鍵を作るため(同じ置き場の索引を同時に作るのを 1 つの process にする — 2 本の CREATE INDEX CONCURRENTLY は
   互いの snapshot を待ち合って deadlock になり、負けた方が使えない索引を残す)。"
  (+ prefix INDEX-LOCK-SUFFIX))


(defk index-lock-statement [prefix]
  {:pre [(: prefix str)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を待たずに取りに行く文(session の advisory lock — 答え = 取れたかの真偽 1 行)を作るため。錠を待つ文
   (SELECT pg_advisory_lock)にしない: 待つ文は snapshot を持ったまま待ち、錠を持つ process の CREATE INDEX CONCURRENTLY の最後の待ち
   (古い snapshot の transaction の終わりを待つ)と輪になって deadlock(40P01)になる。待つ側の deadlock の検めは待ち始めて 1 秒で
   1 度だけ走り、その時はまだ輪が無いので、捨てられるのは後から最後の待ちに入る作り手(作りかけの索引が残る)。取れなかった process は
   錠を持つ process が放した合図(pg.index-turn)で観測し直す。"
  (Statement :text "SELECT pg_try_advisory_lock(hashtext(:key))" :params (! (params-of #(#("key" (! (index-lock-key prefix))))))))


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
  "行の状態の欄 state-field が終端の語 terminal のどれかである条件を作るため(読みの文の期限の条件 expired-row-part の 1 つの形 — 行ごとに
   照らす条件で、索引では引かない)。欄の式は field-expression で、終端の語は JSON の値(jsonb)の引数 `:{stem}0::jsonb, …` にして比べる
   (memory の判定 admission.terminal-row? と同じく値そのものを比べる)。payload = payload の列の綴り(別名つきでもよい)。"
  (<- expression (field-expression payload state-field))
  (<- states (field-in-condition expression stem (! (terminal-words terminal))))
  states)


(defk terminal-words [terminal]
  {:pre [(: terminal tuple) (> (len terminal) 0)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "終端の語 terminal を、状態の欄の値と比べる JSON の綴りの組にするため(terminal-state-condition と terminal-rows-statement が同じ綴りで
   比べる)。"
  (tuple (gfor word terminal (json.dumps word :ensure-ascii False))))


(defk field-in-condition [expression stem encoded]
  {:pre [(: expression str) (: stem str) (: encoded tuple) (> (len encoded) 0)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "欄の値の式 expression(field-expression か table-field-expression の綴り)の値が JSON の値の組 encoded(JSON の綴りの空でない tuple)の
   どれかに等しい条件を作るため(終端の状態の条件と、ListRows の値の組の絞りの 1 つの形)。値は JSON の値(jsonb)の引数
   `:{stem}0::jsonb, …` にして比べる(memory の判定 admission.json-equal? と同じく値そのものを比べる)。空の組は文にできないので :pre が
   断る。"
  (<- members (in-list stem encoded))
  (Clause :text (.format "{e} IN ({s})" :e expression :s (.join ", " (gfor param members.params (.format ":{}::jsonb" param.name))))
          :params members.params))


(defk key-spellings [key-fields name encoded]
  {:pre [(: key-fields tuple) (: name str) (: encoded tuple) (> (len encoded) 0)] :post [(: % (| tuple None))]
   :tags {:context "records" :role "foundation"}}
  "ListRows の絞りが名指す欄 name が、鍵の欄が 1 つの表の鍵の欄で、値 encoded(JSON の綴りの空でない tuple)が全部文字列の時に、その値を
   鍵の綴り(admission.key-text — 行の key の列の綴り)にして返すため(主キー (ledger, key) の索引で引ける)。鍵の欄が 2 つ以上の表・
   鍵でない欄・文字列でない値が混ざる組は None(欄の式のまま — 文字列でない値は鍵の綴りに無いので、答えは memory と同じ)。"
  (when (or (!= (len key-fields) 1) (!= name (get key-fields 0)))
    (return None))
  (val values (tuple (gfor text encoded (json.loads text))))
  (if (all (gfor value values (isinstance value str)))
      (tuple (gfor value values (key-text #(value))))
      None))


(defrecord RowFilter
  "ListRows の絞り(where と where-any)を行の読みの文の条件にした物(row-filter の答え): clauses = 条件の綴りの並び(where の欄の名の順 →
   where-any の欄の名の順 — 文は AND で続ける)/ params = その引数 / ledger-free = 条件が 1 つ以上在り、全部が表の欄の式の条件で、文に
   条件 ledger = :ledger を置かない(頭の註 — 表の欄の式の条件がその表の行だけに合う)。"
  (#^ (get tuple #(str ...)) clauses)
  (#^ (get tuple #(SqlParam ...)) params)
  (#^ bool ledger-free))


(defk field-term [table scoped name]
  {:pre [(: table str) (: scoped frozenset) (: name str)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の絞りが名指す欄 name の値の式を作るため: 表 table の欄 name の表の欄の式の索引が在る(scoped = その欄の名の集合に在る)なら
   表の欄の式(table-field-expression)・無ければ前の文の形の欄の式(field-expression — 旧い形の索引か前の部分索引が引く・頭の註)。"
  (if (in name scoped)
      (! (table-field-expression "" table name))
      (! (field-expression "payload" name))))


(defk where-condition [table key-fields scoped i name encoded]
  {:pre [(: table str) (: key-fields tuple) (: scoped frozenset) (: i int) (: name str) (: encoded str)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の where の等号 1 つ(欄 name = 値 encoded〔JSON の綴り〕・引数の名 w{i})を、行の読みの文の条件にするため: 鍵の欄が 1 つの
   表の鍵の欄を文字列で名指す時は主キーの列で `key = :w{i}`(key-spellings)・それ以外は欄の値の式(field-term)の `<式> = :w{i}::jsonb`。"
  (<- spellings (key-spellings key-fields name #(encoded)))
  (val argument (.format "w{}" i))
  (if (is spellings None)
      (do (<- term (field-term table scoped name))
          (Clause :text (.format "{} = :{}::jsonb" term argument)
                  :params (! (params-of #(#(argument encoded))))))
      (Clause :text (.format "key = :{}" argument)
              :params (! (params-of #(#(argument (get spellings 0))))))))


(defk any-of-condition [table key-fields scoped i name encoded]
  {:pre [(: table str) (: key-fields tuple) (: scoped frozenset) (: i int) (: name str) (: encoded tuple)] :post [(: % Clause)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の値の組の絞り(where-any)の欄 1 つ(欄 name の値が組 encoded〔JSON の綴りの空でない tuple〕のどれか・引数の名 a{i}_0・a{i}_1 …
   — 文の他の引数と混ざらない)を、行の読みの文の条件にするため: 鍵の欄が 1 つの表の鍵の欄を文字列の組で名指す時は主キーの列で
   `key IN (…)`(key-spellings)・それ以外は欄の値の式(field-term)の field-in-condition。"
  (<- spellings (key-spellings key-fields name encoded))
  (val stem (.format "a{}_" i))
  (if (is spellings None)
      (do (<- term (field-term table scoped name))
          (<- condition (field-in-condition term stem encoded))
          condition)
      (do (<- members (in-list stem spellings))
          (Clause :text (.format "key IN ({})" members.text) :params members.params))))


(defk scoped-condition? [key-fields scoped name encoded]
  {:pre [(: key-fields tuple) (: scoped frozenset) (: name str) (: encoded tuple)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "絞りの欄 name(値の JSON の綴りの組 encoded)の条件が表の欄の式の条件になるかを判じるため(主キーの列で照らす鍵の欄の条件と、表の欄の
   式の索引がまだ無い欄の条件は、条件 ledger = :ledger を要る — RowFilter の ledger-free)。"
  (<- spellings (key-spellings key-fields name encoded))
  (and (is spellings None) (in name scoped)))


(defk row-filter [table key-fields scoped where-json where-any-json]
  {:pre [(: table str) (: key-fields tuple) (: scoped frozenset) (: where-json dict) (: where-any-json dict)] :post [(: % RowFilter)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の絞り(where と where-any)を、行の読みの文の条件(RowFilter)にするため(list-rows-statement と top-rows-statement が同じ
   条件を使う)。where-json = 欄 → 値の JSON の綴り・where-any-json = 欄 → 値の JSON の綴りの空でない tuple(欄の名は宣言を通った英数字)・
   scoped = 表の欄の式の索引が在る表 table の欄の名の集合(索引の用意の観測 — pg.IndexBook)。両方が空の写像なら条件は無い(文は前と同じ
   字面)。"
  (var clauses #())
  (var params #())
  (var every-scoped True)
  (for [#(i #(name encoded)) (enumerate (sorted (.items where-json)))]
    (<- condition (where-condition table key-fields scoped i name encoded))
    (<- scoped-here (scoped-condition? key-fields scoped name #(encoded)))
    (:= clauses (+ clauses #(condition.text)))
    (:= params (+ params condition.params))
    (:= every-scoped (and every-scoped scoped-here)))
  (for [#(i #(name members)) (enumerate (sorted (.items where-any-json)))]
    (<- any-of (any-of-condition table key-fields scoped i name members))
    (<- any-scoped (scoped-condition? key-fields scoped name members))
    (:= clauses (+ clauses #(any-of.text)))
    (:= params (+ params any-of.params))
    (:= every-scoped (and every-scoped any-scoped)))
  (RowFilter :clauses clauses :params params :ledger-free (and (> (len clauses) 0) every-scoped)))


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


(defk list-rows-statement [prefix table key-fields scoped after-key where-json where-any-json limit expiry]
  {:pre [(: prefix str) (: table str) (: key-fields tuple) (: scoped frozenset) (: after-key (| str None)) (: where-json dict)
         (: where-any-json dict) (: limit int) (: expiry (| RowExpiry None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "鍵の綴りの順(COLLATE \"C\" = 符号点の順・鍵の綴りは ASCII だけ)の 1 頁 + 1 行(続きの有無を知るため)を読む文を作るため。
   where-json = 欄 → 値の JSON の綴り(欄の名は宣言を通った英数字)。where-any-json = 欄 → 値の組の JSON の綴りの tuple(ListRows の
   where-any)。条件は row-filter(scoped = 表の欄の式の索引が在る欄の名の集合 — 在る欄は表の欄の式で照らし、絞りが全部そうなら
   ledger = :ledger を置かない・頭の註)。expiry = 表の保持の期限の境(None = 期限の無い表)— 期限を過ぎた終端の行は、回収の前でも頁に
   出さない(頁の上限の前に除く — 頁の続きの位置がずれない・#3561)。"
  (<- filter (row-filter table key-fields scoped where-json where-any-json))
  (val ledger-clauses (if filter.ledger-free #() #("ledger = :ledger")))
  (val ledger-pairs (if filter.ledger-free #() #(#("ledger" table))))
  (val after-clauses (if (is after-key None) #() #("key COLLATE \"C\" > :after")))
  (val after-pairs (if (is after-key None) #() #(#("after" after-key))))
  (val clauses (+ ledger-clauses after-clauses filter.clauses))
  (val pairs (+ ledger-pairs after-pairs #(#("limit" (+ limit 1)))))
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{u} ORDER BY key COLLATE \"C\" LIMIT :limit"
                            :p prefix :w (.join " AND " clauses) :u unexpired.text)
             :params (+ (! (params-of pairs)) filter.params unexpired.params)))


(defk top-rows-statement [prefix table key-fields scoped descending-by ordered cursor where-json where-any-json limit expiry]
  {:pre [(: prefix str) (: table str) (: key-fields tuple) (: scoped frozenset) (: descending-by str) (.match FIELD-NAME-PATTERN descending-by)
         (: ordered bool) (: cursor (| ListCursor None)) (or (is cursor None) (is-not cursor.after-value None))
         (: where-json dict) (: where-any-json dict) (: limit int) (: expiry (| RowExpiry None))]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "上から読む ListRows(descending-by — effects.hy の ListRows の註)の 1 頁 + 1 行(続きの有無を知るため)を読む文を作るため: 欄
   descending-by の値が JSON の整数の行(memory の置き場の descending-value? と同じ行 — 小数・真偽・文字列・欄の無い行を除く)を、
   (値, 鍵の綴り) の大きい順に。cursor = 前の頁の位置(None = 上から): (値, 鍵の綴り) が位置の (after-value, after-key) より小さい行
   だけ(keyset)。位置の値は文字列で渡して numeric に読む(JSON の整数は int8 を越え得る — 並べる式と同じ型で比べる)。where・where-any
   (row-filter・scoped)と保持の期限の条件は list-rows-statement と同じ。並べる欄の条件は行ごとに照らす(索引の欄でなくてよい — 表の行に
   限る条件は絞りが持つ)。ordered = 欄が表の上から読む欄(TableDecl.descending — 頭の註の上から読む欄の索引)か: 真なら並べる式
   ordered-expression で並べ、続きの位置を行の比べで照らし、条件 ledger = :ledger を置かない(索引の順に辿る形)。偽なら前の形。"
  (<- filter (row-filter table key-fields scoped where-json where-any-json))
  (val below-pairs (if (is cursor None) #() #(#("after_value" (str cursor.after-value)) #("after" cursor.after-key))))
  (val pairs-tail (+ below-pairs #(#("limit" (+ limit 1)))))
  (<- unexpired (unexpired-rows-filter "r" (if (is expiry None) #() #(expiry))))
  (when ordered
    (<- sorted-value (ordered-expression table descending-by))
    (val ordered-below (if (is cursor None)
                           #()
                           #((.format "({o}, key COLLATE \"C\") < (CAST(:after_value AS numeric), :after)" :o sorted-value))))
    (val ordered-clauses (+ filter.clauses #((.format "{o} IS NOT NULL" :o sorted-value)) ordered-below))
    (return (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{u}
                       ORDER BY {o} DESC NULLS LAST, key COLLATE \"C\" DESC LIMIT :limit"
                                      :p prefix :w (.join " AND " ordered-clauses) :u unexpired.text :o sorted-value)
                       :params (+ (! (params-of pairs-tail)) filter.params unexpired.params))))
  (val ledger-clauses (if filter.ledger-free #() #("ledger = :ledger")))
  (val ledger-pairs (if filter.ledger-free #() #(#("ledger" table))))
  (<- ordered-field (field-expression "payload" descending-by))
  (val below-clauses
    (if (is cursor None)
        #()
        #((.format "(({o})::text::numeric < CAST(:after_value AS numeric) OR (({o})::text::numeric = CAST(:after_value AS numeric) AND key COLLATE \"C\" < :after))"
                   :o ordered-field))))
  (val clauses (+ ledger-clauses
                  filter.clauses
                  #((.format "jsonb_typeof({o}) = 'number' AND ({o})::text ~ '^-?[0-9]+$'" :o ordered-field))
                  below-clauses))
  (val pairs (+ ledger-pairs pairs-tail))
  (Statement :text (.format "SELECT key, payload, version FROM {p}state_rows AS r WHERE {w}{u}
                       ORDER BY ({o})::text::numeric DESC, key COLLATE \"C\" DESC LIMIT :limit"
                            :p prefix :w (.join " AND " clauses) :u unexpired.text :o ordered-field)
             :params (+ (! (params-of pairs)) filter.params unexpired.params)))


(defk terminal-rows-statement [prefix table state-field terminal scoped]
  {:pre [(: prefix str) (: table str) (: state-field str) (: terminal tuple) (> (len terminal) 0) (: scoped bool)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "終端の状態の行(保持の期限の候補 — 期限の判断は admission.row-expired?)を読む文を作るため(terminal が空なら呼ばない)。状態の欄の条件は
   索引に当たる形で、表の終端でない行を読まない(#3614): scoped = 表の状態の欄の表の欄の式の索引が在る(索引は宣言した索引の欄か
   added-indexes が作る)なら表の欄の式の IN だけ・無ければ前の形(ledger = :ledger と欄の式の IN — 旧い形の索引か前の部分索引が引く)。"
  (<- words (terminal-words terminal))
  (if scoped
      (do (<- expression (table-field-expression "" table state-field))
          (<- states (field-in-condition expression "t" words))
          (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows
                       WHERE {s} ORDER BY key COLLATE \"C\""
                                    :p prefix :s states.text)
                     :params states.params))
      (do (<- states (terminal-state-condition "payload" state-field "t" terminal))
          (Statement :text (.format "SELECT key, payload, version, updated_at FROM {p}state_rows
                       WHERE ledger = :ledger AND {s} ORDER BY key COLLATE \"C\""
                                    :p prefix :s states.text)
                     :params (+ (! (params-of #(#("ledger" table)))) states.params)))))


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
   (ledger, 組の名の式) の索引に当たる(#3614 — 組の名の式は索引と同じ key-suffix-expression)。
   組の「新しい出来事」の照らしは値の副問い合わせ(`(SELECT 1 … LIMIT 1) IS NULL`)で書き、NOT EXISTS にしない: NOT EXISTS は planner が
   anti join に引き上げ、見積もりしだいで列の生きた出来事を全部読んで hash にする(ledger と at の相関を知らないので、期限を過ぎた
   出来事を実際の数百倍に見積もると選ぶ — 2026-10-18)。値の副問い合わせは join に引き上げられず、照らす出来事ごとに組の名の式の索引を
   引いて最初の 1 行で止まる(計画が見積もりに依らない)。"
  (if (is separator None)
      (.format "old.at <= :{b}" :b bound)
      (.format "old.at <= :{b} AND (
                         SELECT 1 FROM {p}append_rows AS young
                          WHERE young.ledger = old.ledger AND young.at > :{b} AND {young} = {old}
                          LIMIT 1) IS NULL"
               :b bound :p prefix :young (! (key-suffix-expression "young.payload" separator))
               :old (! (key-suffix-expression "old.payload" separator)))))


(defk expiring-where [prefix separator]
  {:pre [(: prefix str) (: separator (| str None))] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた列 :ledger の出来事 old の条件の文を作るため(引数 :ledger・:before_at — 期限の条件は
   expired-event-condition)。刈りの候補の読み(expiring-candidates-statement)と刈り(expire-chosen-events-statement)と書きが触る単位の
   読み(touched-events-statement)が同じ条件を使う。"
  (<- expired (expired-event-condition prefix separator "before_at"))
  (+ "old.ledger = :ledger AND " expired))


(defk expiring-params [stream before-at]
  {:pre [(: stream str) (: before-at int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "expiring-where の条件の引数を作るため。"
  (! (params-of #(#("ledger" stream) #("before_at" before-at)))))


(defk expiring-candidates-statement [prefix stream before-at separator limit]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator (| str None)) (: limit int) (> limit 0)] :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた出来事の番号を limit 個まで読む文を作るため(書きの錠の外で流す回収の候補 — 刈りの transaction は候補が在る時だけ
   開き、錠の中では読んだ番号の出来事だけを expire-chosen-events-statement で捨てる。行の刈りの expired-rows と同じ形)。separator =
   None は出来事ごとに数える列・str は組で数える列の区切り。積んだ刻の順に読むので (ledger, at) の索引を境の手前から辿り、limit 個で
   止まる(頭から読んで並べ替える計画を選ばせない — 期限を過ぎた出来事の見積もりが外れても読む行は候補と組の照らしだけ)。"
  (<- where (expiring-where prefix separator))
  (<- params (expiring-params stream before-at))
  (<- named (params-of #(#("limit" limit))))
  (Statement :text (.format "SELECT old.seq FROM {p}append_rows AS old WHERE {w} ORDER BY old.at LIMIT :limit" :p prefix :w where)
             :params (+ params named)))


(defk expire-chosen-events-statement [prefix stream before-at separator sequences]
  {:pre [(: prefix str) (: stream str) (: before-at int) (: separator (| str None)) (: sequences tuple) (> (len sequences) 0)]
   :post [(: % Statement)]
   :tags {:context "records" :role "foundation"}}
  "回収の候補(番号 sequences — expiring-candidates-statement の答え)のうち、今も保持の期限を過ぎた出来事を捨て、捨てた出来事(番号・刻・
   payload — read-events-statement と同じ列の並び)を返す文を作るため(書きの錠の transaction の中で流す。before-at = 今 − 保持の秒 —
   admission.event-expired? と同じ境界・separator は expiring-where と同じ)。条件は錠の中で読み直す(候補を読んだ後に組へ新しい出来事が
   積まれた組は捨てない)。番号は主鍵で引くので、触る行は番号の数で縛られる(列の行の数の見積もりに依らない — 頭の註)。返した行から
   鍵の覚えを作り、同じ transaction で retire-keys-statement が入れる(#3022)。空の組は文にできないので :pre が断る(呼び手が枝で避ける)。"
  (<- where (expiring-where prefix separator))
  (<- params (expiring-params stream before-at))
  (<- numbers (in-list "s" sequences))
  (Statement :text (.format "DELETE FROM {p}append_rows AS old WHERE old.seq IN ({s}) AND {w} RETURNING old.seq, old.at, old.payload"
                            :p prefix :s numbers.text :w where)
             :params (+ params numbers.params)))


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
