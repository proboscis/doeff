;;; 汎用の SQL の問い合わせの effect(agora-redesign #802 便 3・消費者 = #783 便 2 の記録の service の翻訳)。業務の語を持たない土台の語彙で、
;;; HttpRequest(http_effects.hy)・RunProcess(process_effects.hy)と同じ段。答え手は仕組みごとに差し替える:
;;;   postgres-sql-handler         本物の PostgreSQL(psycopg 3・postgres_sql.hy)。psycopg はその module の中でだけ読む。driver の I/O は
;;;                                呼び 1 つに thread 1 本で回し、遅い問い合わせの間も scheduler の他の task が回る(#1215)
;;;   pooled-postgres-sql-handler  同じ本物の PostgreSQL を、接続の許可を scheduler の semaphore で待ち driver の I/O だけを呼び手の pool の
;;;                                thread で回して答える版(pooled_postgres_sql.hy)。thread の数を呼び手が抑える
;;;   clickhouse-http-sql-handler  本物の ClickHouse(urllib の HTTP 1 本 = 問い合わせ 1 つ・clickhouse_http_sql.hy)。transaction を持たない
;;;   sqlite-sql-handler           I/O なし — stdlib の sqlite3 の memory の DB(sqlite_sql.hy)。仮想の時計の模擬で使う
;;; 答え手は自分が宣言した database の名にだけ答え、他の名の effect は外側へ回す(PostgreSQL と ClickHouse の答え手を重ねて置ける)。
;;; 接続の宣言(DSN・URL・資格)は答え手の引数で、組み立ての側(composition root)が渡す。業務が見るのは database の名だけ。
;;;
;;; 失敗は値で答える(例外にしない — 成否と類の解釈は呼び手):
;;;   SqlFailed(sqlstate, reason)  engine が答えた失敗(SQLSTATE の 5 文字。driver が SQLSTATE を持たない時は類の code — 22000・23000 …
;;;                                ClickHouse は SQLSTATE を持たないので None)
;;;   SqlUnreachable(reason)       engine に届かない(接続できない・切れた・HTTP が届かない)
;;; 呼び手の誤り(引数の名の食い違い・閉じた集合の外の値・transaction の中の禁じた effect)は例外(ValueError・TypeError・SqlTransactionMisuse)。
;;;
;;; 文の書き方(3 方言で共通の約束):
;;;   - 引数は中立の記法 `:name` 1 つ(文字列の literal・引用した名・注釈の中は引数でない・`::` は型の変換で引数でない)。答え手が方言へ
;;;     書き換える: PostgreSQL = `%(name)s`(文の `%` は `%%` へ)・sqlite = `?`・ClickHouse = `{name:Type}`(型は値の Python の型から
;;;     閉じた表 CLICKHOUSE-TYPES で決める)。params の名と文の名は過不足なく一致させる(食い違いは ValueError)。
;;;   - 文の中で時計を読まない(now()・current_timestamp・today() を書かない)。刻は呼び手が GetTime 等の effect で読んで引数で渡す —
;;;     答え手を差し替えた模擬で、仮想の時計と DB の時計が食い違わないため。
;;;   - 引数と行の値は閉じた集合 SqlValue = int | float | str | bytes | bool | None。答え手が driver の値をこの集合へ正規化する
;;;     (Decimal → 整数なら int・他は float / 日時 → ISO 8601 の str / UUID → str / json → text)。集合の外の値は TypeError。
;;;
;;;   SqlQuery         文 1 つ。答え = SqlRows | SqlFailed | SqlUnreachable
;;;   SqlInsertRows    表へ行をまとめて入れる(PostgreSQL / sqlite = executemany・ClickHouse = JSONCompactEachRow の本文 1 つ)。答えは同じ
;;;   SqlTransaction   program を 1 つの transaction の中で走らせる。答え = program の答え | SqlFailed | SqlUnreachable。約束:
;;;                      (1) 中の SqlQuery / SqlInsertRows が最初に失敗したら rollback し、program を再開せずにその失敗を答えにする
;;;                      (2) 中で出せるのは同じ database への SqlQuery と SqlInsertRows と純粋な計算だけ。他の effect(入れ子の SqlTransaction・
;;;                          別の database・scheduler・状態)は答え手が被せる handler が断り、rollback して SqlTransactionMisuse を投げる
;;;                      (3) program が例外を投げたら rollback して例外を通す
;;;                    lock-key(str | None)= 同じ鍵の transaction を直列にする(PostgreSQL = pg_advisory_xact_lock(hashtext(鍵))・
;;;                    sqlite = 1 接続で transaction が割り込まれないので要らない)。ClickHouse の答え手は SqlFailed(0A000)で断る。
;;;   SqlEnsureTables  表・欄・索引の宣言(SqlTable)を方言の DDL に描いて流す(在れば何もしない)。答え = SqlSchemaApplied | SqlFailed |
;;;                    SqlUnreachable。PostgreSQL の DO $$・CONCURRENTLY、ClickHouse の engine の細目は宣言に載せない(共通の文にできない)
;;;
;;; I/O なしの置き場の語彙(本物には無い): SetSqlOutage = その database を不達にする / 戻す(sqlite-sql-handler だけが答える — 模擬の筋書きが
;;; 不達の枝を起こす口)。
(require doeff-hy.macros [defk deff defeffect <- val var])
(require doeff-hy.record [defrecord defenum])
(import re)
(import datetime)
(import decimal [Decimal])
(import uuid [UUID])
(import dataclasses [dataclass])
(import enum [StrEnum])
(import typing [TypeVar])
(import doeff [Program])

(val MODULE-TAGS {:context "sql" :role "foundation"})

;; SqlTransaction の答えの program の答えの型(program ごとに違う)。
(val T (TypeVar "T"))

;; 引数と行の値の閉じた集合(bool は int の下位の型なので先に並べる)。
(val SQL-VALUE-TYPES #(bool int float str bytes (type None)))

;; 中立の記法の字句: 文字列の literal・引用した名・注釈・$$ の本文・`::` はそのまま文、`:name` は引数、他は 1 字ずつ文。
(val TOKEN (re.compile r"(?P<text>'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|--[^\n]*|/\*.*?\*/|\$\$.*?\$\$|::)|:(?P<name>[A-Za-z_][A-Za-z0-9_]*)|(?P<plain>[^'\"\-/:$]+|.)"
                       re.DOTALL))

;; 表・欄・索引の名(DDL に描く名は引用しないので、この形だけを受ける)。
(val IDENTIFIER (re.compile r"[A-Za-z_][A-Za-z0-9_]*"))


;; --- 値 ---------------------------------------------------------------------------------------------------------------------

(defrecord SqlParam
  "文の引数 1 つ(name = 文の `:name` の名・value = SqlValue の閉じた集合の値)。"
  (#^ str name)
  (#^ (| int float str bytes bool None) value))


(defrecord SqlRows
  "問い合わせの答え。rows = 行の tuple(行 = 欄の値の tuple・値は SqlValue へ正規化済み)・rowcount = engine が数えた行の数
   (行を返す文は返した行の数・書く文は変えた行の数。ClickHouse の HTTP の問い合わせは数えないので None)。"
  (#^ (get tuple #((get tuple #((| int float str bytes bool None) ...)) ...)) rows)
  (#^ (| int None) rowcount))


(defrecord SqlFailed
  "engine が答えた失敗。sqlstate = SQLSTATE(5 文字 — driver が SQLSTATE を持たなければ類の code・ClickHouse は None)・reason = engine の文。"
  (#^ (| str None) sqlstate)
  (#^ str reason))


(defrecord SqlUnreachable
  "engine に届かない(接続できない・切れた・HTTP が届かない)。reason = driver の文。"
  (#^ str reason))


(defrecord SqlSchemaApplied
  "SqlEnsureTables の答え。statements = 流した DDL の文(方言で描いた後・流した順)。"
  (#^ (get tuple #(str ...)) statements))


(defclass SqlTransactionMisuse [TypeError]
  "SqlTransaction の中で禁じた effect を出した(約束 (2) — 頭の註)。transaction は rollback 済み。")


;; --- DDL の宣言 ------------------------------------------------------------------------------------------------------------

(defenum SqlColumnType INTEGER FLOAT TEXT BYTES BOOLEAN JSON)


(defrecord SqlColumn
  "欄 1 つ(type = 閉じた型 — INTEGER は 64 bit・JSON は text で出し入れ・nullable = NULL を受けるか)。"
  (#^ str name)
  (#^ SqlColumnType type)
  (setv #^ bool nullable False))


(defrecord SqlIndex
  "索引 1 つ(columns = 欄の名の並び・unique = 一意)。"
  (#^ str name)
  (#^ (get tuple #(str ...)) columns)
  (setv #^ bool unique False))


(defrecord SqlTable
  "表 1 つ(primary-key = 主鍵の欄の名の並び — ClickHouse では並びの鍵 ORDER BY・空なら主鍵なし)。"
  (#^ str name)
  (#^ (get tuple #(SqlColumn ...)) columns)
  (setv #^ (get tuple #(str ...)) primary-key #())
  (setv #^ (get tuple #(SqlIndex ...)) indexes #()))


;; --- effect ----------------------------------------------------------------------------------------------------------------

(defeffect SqlQuery
  "文 1 つを流す(頭の註)。文の中で時計を読まない — 刻は引数で渡す。"
  {:fields [(: database str) (: statement str) (: params (get tuple #(SqlParam ...)) #())]
   :pre [(: database str) (: statement str) (: params tuple) (all (gfor p params (isinstance p SqlParam)))]
   :answer (| SqlRows SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlInsertRows
  "表 table の欄 columns へ行 rows をまとめて入れる(頭の註)。rows の各行は columns と同じ長さの SqlValue の tuple(答え手が検める)。"
  {:fields [(: database str) (: table str) (: columns (get tuple #(str ...)))
            (: rows (get tuple #((get tuple #((| int float str bytes bool None) ...)) ...)))]
   :pre [(: database str) (: table str) (: columns tuple) (: rows tuple) (all (gfor row rows (isinstance row tuple)))]
   :answer (| SqlRows SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlTransaction
  "program を 1 つの transaction の中で走らせる(頭の註の約束 3 つ)。答え = program の答え(commit した後)| SqlFailed | SqlUnreachable。"
  {:fields [(: database str) (: program Program) (: lock-key (| str None) None)]
   :pre [(: database str) (: program Program) (: lock-key (| str None))]
   :answer (| T SqlFailed SqlUnreachable)
   :runs-carried [program]
   :tags {:context "sql" :role "foundation"}})


(defeffect SqlEnsureTables
  "表の宣言 tables を方言の DDL に描いて流す(在れば何もしない・頭の註)。"
  {:fields [(: database str) (: tables (get tuple #(SqlTable ...)))]
   :pre [(: database str) (: tables tuple) (all (gfor t tables (isinstance t SqlTable)))]
   :answer (| SqlSchemaApplied SqlFailed SqlUnreachable)
   :tags {:context "sql" :role "foundation"}})


(defeffect SetSqlOutage
  "I/O なしの置き場の口(sqlite-sql-handler だけが答える): down = True の間、database への effect は全部 SqlUnreachable で答える。"
  {:fields [(: database str) (: down bool)]
   :answer (type None)
   :tags {:context "sql" :role "foundation"}})


;; --- 中立の記法 -------------------------------------------------------------------------------------------------------------

(defrecord SqlText
  "文の中の引数でない部分(方言の書き換えでそのまま写す)。"
  (#^ str text))


(defrecord SqlPlaceholder
  "文の中の引数 `:name` 1 つ。"
  (#^ str name))


(defk split-statement [statement]
  {:pre [(: statement str)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "中立の記法の文を、文の部分(SqlText)と引数(SqlPlaceholder)の並びに割るため(文字列の literal・引用した名・注釈・`::` は文の部分)。"
  (var parts [])
  (var pending "")
  (for [found (.finditer TOKEN statement)]
    (val name (.group found "name"))
    (if (is name None)
        (:= pending (+ pending (.group found 0)))
        (do (when pending (.append parts (SqlText :text pending)))
            (:= pending "")
            (.append parts (SqlPlaceholder :name name)))))
  (when pending (.append parts (SqlText :text pending)))
  (tuple parts))


(defk checked-params [parts params]
  {:pre [(: parts tuple) (: params tuple)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "文の引数の名と params の名が過不足なく一致し、値が閉じた集合に入ることを確かめるため(食い違いは呼び手の誤り — ValueError / TypeError)。"
  (val given (lfor p params p.name))
  (when (!= (len given) (len (set given)))
    (raise (ValueError (.format "params の名が重なっている: {}" given))))
  (val used (sfor part parts :if (isinstance part SqlPlaceholder) part.name))
  (when (!= used (set given))
    (raise (ValueError (.format "文の引数と params の名が食い違う: 文 = {} / params = {}" (sorted used) (sorted given)))))
  (for [p params]
    (when (not (isinstance p.value SQL-VALUE-TYPES))
      (raise (TypeError (.format "引数 {} の値の型 {} は SqlValue の閉じた集合の外" p.name (. (type p.value) __name__))))))
  None)


(defk param-value [params name]
  {:pre [(: params tuple) (: name str)] :post [(: % (| int float str bytes bool None))]
   :tags {:context "sql" :role "foundation"}}
  "params から名 name の値を引くため(checked-params の後に呼ぶ)。"
  (next (gfor p params :if (= p.name name) p.value)))


(defk checked-identifier [name]
  {:pre [(: name str)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "DDL と投入の文に描く名が引用なしで安全な形であることを確かめるため(外れは ValueError)。"
  (when (not (.fullmatch IDENTIFIER name))
    (raise (ValueError (.format "名 {!r} は [A-Za-z_][A-Za-z0-9_]* の形でない" name))))
  name)


(defk checked-identifiers [names]
  {:pre [(: names tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "名の並び(欄・主鍵・索引の欄)の全部を checked-identifier で確かめ、書いた順の tuple で返すため(最初の外れで ValueError)。"
  (var checked [])
  (for [name names]
    (.append checked (! (checked-identifier name))))
  (tuple checked))


(defk checked-rows [columns rows]
  {:pre [(: columns tuple) (: rows tuple)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows の行が欄の数と同じ長さで、値が閉じた集合に入ることを確かめるため。"
  (for [row rows]
    (when (!= (len row) (len columns))
      (raise (ValueError (.format "行の長さ {} が欄の数 {} と違う: {!r}" (len row) (len columns) row))))
    (for [value row]
      (when (not (isinstance value SQL-VALUE-TYPES))
        (raise (TypeError (.format "行の値の型 {} は SqlValue の閉じた集合の外" (. (type value) __name__)))))))
  None)


;; --- 行の値の正規化 --------------------------------------------------------------------------------------------------------

(defk normalized-value [value]
  {:pre [(: value "driver の値(型は driver ごと — ここで検める)")] :post [(: % (| int float str bytes bool None))]
   :tags {:context "sql" :role "foundation"}}
  "driver の値を閉じた集合 SqlValue へ写すため(集合の外で写し方の決まっていない型は TypeError — 黙って str にしない)。
   value の型は driver ごとに違う(ここが境界で型を検める 1 点)。"
  (match value
    (| (bool) (int) (float) (str) (bytes) None) value
    (| (memoryview) (bytearray)) (bytes value)
    (Decimal) (if (= value (.to-integral-value value)) (int value) (float value))
    (| (datetime.datetime) (datetime.date) (datetime.time)) (.isoformat value)
    (UUID) (str value)
    _ (raise (TypeError (.format "driver の値の型 {} を SqlValue へ写す決まりが無い" (. (type value) __name__))))))


(defk normalized-rows [rows]
  {:pre [(: rows #(list tuple))] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "driver の行の列を SqlRows の rows(値を正規化した tuple の tuple)へ写すため。"
  (var normalized [])
  (for [row rows]
    (var values [])
    (for [value row]
      (.append values (! (normalized-value value))))
    (.append normalized (tuple values)))
  (tuple normalized))
