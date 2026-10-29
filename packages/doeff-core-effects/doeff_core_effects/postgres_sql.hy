;;; 汎用の SQL の effect(sql_effects.hy)の本物の PostgreSQL の答え手 postgres-sql-handler(agora-redesign #802 便 3)。psycopg 3 を呼んで値を
;;; 詰め替えるだけで、判断を持たない。psycopg はこの module の I/O の関数の中でだけ読む(psycopg の無い環境でも、書き換えと写しの純関数は
;;; 読めて検を撃てる)。
;;;   - 接続の貸し出し PostgresConnections は組み立ての側(composition root)が database の宣言(PostgresDatabase — 名と DSN)から作って渡し、
;;;     止める時に close する。要求 1 つ = 接続 1 本(doeff-records の置き場もこの貸し出しを使う — 同時の要求の transaction を 1 本の接続で
;;;     混ぜない)。接続は自動 commit・json / jsonb の欄は text で読む(値の正規化の決まり)。切れた接続と transaction の途中で返った接続は
;;;     返す時に捨てる / rollback する。目安(LARGE-CARRY-BYTES)を越える値(送った引数の組か行 1 つ・受けた行 1 つ)を通した接続も返す時に
;;;     捨てる — libpq は接続ごとの送りと受けの buffer を通した一番大きな message まで伸ばして閉じるまで縮めないので、空きの列に戻すと大きな
;;;     本文 1 つで数十 MB を抱え続ける(card ki-1368e0d6b801)。SqlQuery の binary は答えの行を binary の形で受け、bytea を 16 進の 2 倍に
;;;     しない(大きな本文の読み)。
;;;   - 接続の上限(PostgresTimeouts・agora-redesign #1479): 接続は開く時の上限・TCP の keepalive・送った bytes の返事を待つ上限
;;;     (tcp_user_timeout)・文の上限(statement_timeout)を持つ。DB の pod が入れ替わると空きの接続は相手の居ない TCP のまま残り、上限が
;;;     無いと次の文が kernel の再送の限り(数十分)固まって許可を持ち続ける — 上限があれば文は OperationalError で落ち、SqlUnreachable を
;;;     答え、返す時に切れた接続として捨てられ、次の借りが新しく張る。
;;;   - 引数は中立の `:name` を `%(name)s` へ書き換え、文の `%` は `%%` にする(postgres-statement)。
;;;   - 失敗: engine の SQLSTATE をそのまま SqlFailed に。SQLSTATE の無い driver の誤りは類の表(DRIVER-CLASS-SQLSTATES — agora-controllers
;;;     services/record/handlers_wire.hy と同じ表)で類の code に。SQLSTATE の無い OperationalError / InterfaceError・接続できない時・
;;;     接続の例外の SQLSTATE(class 08 と 57P01・57P02・57P03)は SqlUnreachable(postgres-failure)。
;;;   - SqlTransaction = 接続 1 本で BEGIN → 錠(lock-key と locks の順 — 排他は pg_advisory_xact_lock・共有は pg_advisory_xact_lock_shared の
;;;     hashtext(鍵))→ program → COMMIT(sql_transaction.hy)。
;;;     既定(batched = False)は文 1 つを 1 回ずつ流す(run-in-transaction — BEGIN・錠・文・合図・COMMIT がそれぞれ往復 1 回)。
;;;     batched = True を選んだ transaction だけ(agora-redesign #3605): 往復 1 回(TransactionFlush — BEGIN と錠・文・合図・COMMIT)を psycopg 3 の
;;;     pipeline mode の 1 つの pipeline で送り、出口の sync 1 度で答えを順に受ける(postgres-flush・run-in-batched-transaction)。途中の文が
;;;     落ちると PostgreSQL は次の sync までの文を流さないので、答えは最初に落ちた文の失敗(TransactionAborted へ写る)で、ROLLBACK は
;;;     run-in-batched-transaction が流す。pipeline の出入りは with で必ず閉じ、返す接続が pipeline mode のまま残っていれば捨てる(release)。
;;;     束ねた transaction は、借りる・全部の往復と間の program・返すを driver の thread の仕事 1 つ(新しい VM)で回し、往復の間に scheduler の
;;;     thread へ戻らない(offloaded-batched-transaction — 錠を持つ間に scheduler の待ちを挟まない・card ki-7f9808c3c3d8)。
;;;     借りた接続への driver の呼び(文・往復・ROLLBACK・返却)は transaction ごとの錠で 1 本ずつ(offloaded-transaction — 取り消された往復の
;;;     thread が走り切る前に ROLLBACK が割り込まない)。pipeline mode を持たない psycopg / libpq(libpq 14 未満)では、接続の貸し出しを作る時
;;;     (起動の時)に名を挙げて落ちる — batched の transaction を文 1 つずつ流す道へ黙って倒さない。
;;;   - scheduler を塞がない(agora-redesign #1215): postgres-sql-handler は driver の I/O(接続の許可を待つ・接続を開く・文を流す・COMMIT・
;;;     ROLLBACK・接続を返す)を scheduler の thread で撃たず、空いた thread を使い回す Executor(offloaded_call.hy の ReusedThreads — 空きが
;;;     無い時だけ thread を起こす・agora card ki-8fc4f9017572)で回し、撃った task
;;;     だけが外から完了させる promise で待つ。遅い文の間も同じ run の他の task(待ち受けの /healthz・時計の刻み)は回る。同時に使う接続の
;;;     上限は PostgresConnections の許可(thread の間の錠)が持ち、許可を待つのも thread の中 — thread の数に上限を置かないので、許可を持つ
;;;     transaction の次の文が thread の空きを待って詰まることがない。手順(文・値の写し・transaction の段・取り消しの後始末)は
;;;     pooled-postgres-sql-handler と同じ offloaded-transaction と offloaded-statement を使い、違いは Executor と許可の待ち方だけ(pooled は呼び手の
;;;     pool と scheduler の semaphore)。外側に scheduled が要る(CreateExternalPromise と Wait)— session の値の置き場(state)は要らない。
;;;   - 通知(agora-redesign #3073・名で絞る形は #3688): SqlNotify = pg_notify(channel, 本文)を流す(transaction の中なら同じ接続で —
;;;     commit した時だけ届く)。本文(notice-payload)は JSON の object で、合図が関わる名(topics — 呼び手の決めた語・記録の置き場なら表と
;;;     列の名)と、合図を出した貸し出しの印(origin — PostgresConnections ごとの乱数)を運ぶ。SqlHangNotice = channel ごとに LISTEN を
;;;     張った接続 1 本を daemon の thread で持つ待ち受け(PostgresListener)に、待つ名と一緒に呼び鈴(外の promise)を掛ける。合図は名の
;;;     重なる呼び鈴だけを鳴らす。名の分からない合図(topics = None・読めない本文・名を載せると本文の上限 8000 bytes に届く合図)は全部の
;;;     待ち手に関わる合図という意味で、全部の呼び鈴を鳴らす(絞れない合図で起こし損ねない — 名の写しを間違えると永久に起きない待ち手が
;;;     できるので、分からない時は全部)。待つ名が None の呼び鈴は全部の合図で鳴る。
;;;     同じ process の呼び鈴は、合図を流した答え手が接続を返した後に、その場で鳴らす(ring-local — 書き手の残り〔接続を返す〕が、起こした
;;;     待ち手の読み直しの後ろに並ばない。NOTIFY の配達を待たないので、仮想の時計の下でも合図より先に待ちの期限が来ない)。待ち受けは
;;;     自分の印の通知を鳴らさない(同じ process の呼び鈴はもう鳴らした — 書き 1 回で待ち手を 2 回起こさない)。印で見分けるのは、
;;;     backend の pid の集合で見分けると、切れた接続の pid を別の process の backend が引き継いだ時にその process の合図を黙って捨てるため。
;;;     鳴らすのは transaction なら COMMIT を流した(流そうとした)時 — COMMIT の答えが失敗でも commit されたか分からない時があり(接続が
;;;     切れた)、自分の印の通知は待ち受けが鳴らさないので、鳴らし損ねる側に倒さない(鳴らしすぎは待ち手の読み直し 1 回で済む)。答えを
;;;     受ける前に取り消されても、接続を返す finally の中で鳴らす。transaction の外の SqlNotify も接続を返した後に鳴らす。
;;;     待ち受けは切れたら繋ぎ直し、繋ぎ直した時に掛かっている呼び鈴を全部鳴らす(切れていた間の通知は届かないので、待ち手に読み直させる)。
;;;     張れていない間の SqlHangNotice は SqlUnreachable。繋ぎ直しの間の秒は境界の答え手の中だけの時間で、呼び手には出ない。
;;;   - 取り消し(agora-redesign #2792): 文 1 つの effect も transaction と同じく「接続を借りる」と「流して返す」を別の仕事にする。許可を待って
;;;     いる間に取り消された要求は、許可が取れても文を流さずにすぐ返す(DB が止まった間に取り消された読みが、DB が戻った時にまとめて流れ、
;;;     後から来た要求を待たせない)。走り出した文は止めない。
(require doeff-hy.macros [defhandler defk deff <- val var])
(val MODULE-TAGS {:context "sql" :role "foundation"})
(require doeff-hy.record [defrecord defwire])
(import queue [Queue Empty])
(import threading)
(import time)
(import uuid)
(import weakref)
(import concurrent.futures [Executor])
(import collections.abc [Callable])
(import contextlib [AbstractContextManager])
(import typing [Protocol runtime-checkable])
(import dataclasses [dataclass field])
(import doeff [Program])
(import doeff_hy._wire [Malformed dump-json parse-json])
(import doeff_core_effects._offloaded_call [ReusedThreads :as _ReusedThreads offloaded run-detached keep-nothing])
(import doeff_core_effects._scheduler [CreateExternalPromise ExternalPromise TaskCancelledError])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlBatch SqlTransaction SqlEnsureTables SqlNotify SqlHangNotice SqlDropNotice
                                        SqlRows SqlFailed SqlUnreachable SqlLock
                                        SqlSchemaApplied SqlParam SqlColumnType SqlText SqlPlaceholder split-statement checked-params
                                        checked-identifier checked-identifiers checked-rows normalized-rows])
(import doeff_core_effects.sql_transaction [TransactionFlush run-in-transaction run-in-batched-transaction stray-batch])

;; SQLSTATE を持たない driver の誤りの類 → その類の SQLSTATE の class の code(psycopg が SQLSTATE から類を選ぶ表の逆)。
(val DRIVER-CLASS-SQLSTATES {"DataError" "22000" "IntegrityError" "23000" "ProgrammingError" "42000" "NotSupportedError" "0A000"
                             "InternalError" "XX000"})
;; SQLSTATE を持たなければ届かない(接続できない・切れた)と読む類。
(val UNREACHABLE-CLASSES #("OperationalError" "InterfaceError"))
;; SQLSTATE を持っていても届かない(接続が切れた・engine が接続を受けない)と読む code: class 08 = 接続の例外の全部(前方 2 文字)と、
;; 57P01 管理者による切断・57P02 crash による切断・57P03 起動中 / 停止中で接続を受けない(class 57 の他 — 57014 取り消し等 — は engine の答え)。
;; 業務の文の誤りではなく、時間を置いて撃ち直せば晴れる失敗なので SqlFailed にしない(agora-redesign #880 の裁定)。
(val UNREACHABLE-SQLSTATE-CLASS "08")
(val UNREACHABLE-SQLSTATES #("57P01" "57P02" "57P03"))
;; 同時に貸す接続の既定の上限(database ごと)。
(val DEFAULT-POOL-SIZE 8)
;; postgres-sql-handler が driver の I/O を回す Executor(空いた thread を使い回し、上限を置かない — 頭の註)。process に 1 つ。
(val DRIVER-THREADS (_ReusedThreads))

;; 待ち受けの接続が切れてから繋ぎ直すまでの秒(頭の註 — 境界の答え手の中だけの時間)。
(val LISTEN-RETRY-SECONDS 1)
;; 合図の本文(NOTIFY の payload)の bytes の上限(PostgreSQL の既定の build は 8000 bytes 未満を求める — 届く本文は名を載せない・頭の註)。
(val NOTICE-PAYLOAD-LIMIT 8000)

;; 大きな値を通した接続を返す時に閉じる目安(bytes — 文 1 つ・往復 1 回の、送った引数と行・受けた行の str / bytes の長さの和)。libpq の接続は
;; 送りと受けの buffer を通した一番大きな message の次の 2 の冪まで伸ばし、閉じるまで縮めない — 空きの列に戻すと、大きな本文 1 つで接続ごとに
;; 数十 MB を抱え続ける(card ki-1368e0d6b801・調べは ki-f62011080c16)。目安の下の値だけを通した接続の buffer は 1 本 数 MB に留まる。
(val LARGE-CARRY-BYTES (* 256 1024))
;; 目安を越える値を通した接続の印(release が空きの列に戻さず閉じる)。接続が消えれば印も消える(弱い参照)。
(val CARRIED-LARGE (weakref.WeakSet))
;; 束ねた transaction の文を名つきの prepared statement にした覚え(接続 → PreparedBook — postgres-flush の註)。接続を捨てれば覚えも消える。
;; 覚える文の数は接続ごとに PREPARED-MAX まで(越えた文は名を付けずに流す — 文の綴りは宣言から決まる有限の組だが、上限で守る)。
(val PREPARED (weakref.WeakKeyDictionary))
(val PREPARED-LOCK (threading.Lock))
(val PREPARED-MAX 256)

;; 宣言の欄の型 → PostgreSQL の型。
(val POSTGRES-TYPES {SqlColumnType.INTEGER "bigint" SqlColumnType.FLOAT "double precision" SqlColumnType.TEXT "text"
                     SqlColumnType.BYTES "bytea" SqlColumnType.BOOLEAN "boolean" SqlColumnType.JSON "json"})


(defclass [(dataclass :frozen True :kw-only True)] PostgresDatabase []
  "PostgreSQL の database の宣言(name = 業務が SqlQuery に書く名・dsn = psycopg の接続文字列 — 資格を含むので repr に出さない)。"
  #^ str name
  #^ str dsn
  (setv dsn (field :repr False)))


(defrecord PostgresTimeouts
  "接続の上限(頭の註): 開く時(秒)・keepalive の最初の問いまでの無音(秒)・問いの間隔(秒)・答えの無い問いの数・送った bytes の返事を
   待つ上限(ミリ秒)・文の上限(ミリ秒 — None = 付けない)・transaction の途中で何もしない上限(ミリ秒 — None = 付けない)。
   transaction の途中の上限は、BEGIN の後に program が進まなくなった接続(返す finally に届かない — 2026-09-30 の着地の台帳で、
   pg_advisory_xact_lock を持ったまま 6 分半 idle in transaction で残り、後続の書きが全部 statement timeout で落ちた・agora-redesign #1846)を
   engine が切り、transaction を戻して錠を外すため。"
  (#^ int connect-seconds)
  (#^ int keepalive-idle-seconds)
  (#^ int keepalive-interval-seconds)
  (#^ int keepalive-count)
  (#^ int unacknowledged-milliseconds)
  (#^ (| int None) statement-milliseconds)
  (#^ (| int None) idle-transaction-milliseconds))


;; 既定の上限: 死んだ相手は keepalive で 10 + 5 × 3 = 25 秒・文を送った後は 15 秒で見つかる。文の上限 60 秒は記録の service の文(短い
;; 読み書きと、錠を待つ表の用意)より十分長い。transaction の途中で何もしない上限 30 秒は、記録の service の transaction(文の間は純粋な
;; 計算だけ)より十分長く、錠を持ったまま止まった接続が後続の書きを塞ぐ時間を 30 秒で切る(agora-redesign #1846)。
(val DEFAULT-TIMEOUTS (PostgresTimeouts :connect-seconds 5 :keepalive-idle-seconds 10 :keepalive-interval-seconds 5 :keepalive-count 3
                                        :unacknowledged-milliseconds 15000 :statement-milliseconds 60000
                                        :idle-transaction-milliseconds 30000))


(defrecord PostgresStatement
  "psycopg へ渡す形に書き換えた文(text = `%(name)s` の文・params = 引数 — dict にするのは driver を呼ぶ 1 点だけ)。"
  (#^ str text)
  (#^ tuple params))


(defclass [(dataclass :kw-only True)] RaisedNotices []
  "transaction 1 つ(か transaction の外の SqlNotify 1 つ)が出した合図の覚え — 答え手が接続を返した後に同じ process の呼び鈴を鳴らすため
   (頭の註)。書き換えるのは答え手の手順だけなので値の型ではない。topics = channel → 合図が関わる名(同じ channel の合図を重ねた和・
   None = 名の分からない合図 = 全部 — dict は channel を鍵に引く覚えの索引で、外へ出さない)/ sent = 合図が届く所まで流した(transaction
   なら COMMIT を流した・流そうとした — 鳴らすかの印)。"
  (setv #^ dict topics (field :default-factory dict))
  (setv #^ bool sent False))

(defclass PostgresConnections []
  "接続の貸し出し(頭の註)。資源なので値の型ではない(中身を書き換え、同一性で扱う)。size = database ごとに同時に貸す接続の上限
   (pooled-postgres-sql-handler が scheduler の許可の数として読む)・timeouts = 開く接続に付ける上限・origin = この貸し出しが出す合図の
   印(合図を流した後に同じ process の呼び鈴を鳴らすのはこの貸し出しなので、待ち受けはこの印の通知を鳴らさない — 頭の註)。"

  (defn __init__ [self #^ tuple databases * [size DEFAULT-POOL-SIZE] [timeouts DEFAULT-TIMEOUTS]]  ; defk にできない: 資源の class の初期化
    "database の宣言の列と、database ごとに同時に貸す接続の上限と、接続の上限を受けるため。transaction の往復は pipeline mode で送る
     (頭の註)ので、psycopg と libpq が pipeline mode を持たなければ、ここ(組み立ての側が起動の時に作る所)で名を挙げて落ちる。"
    (import psycopg)
    (when (not (psycopg.Pipeline.is-supported))
      (raise (RuntimeError (.format (+ "PostgreSQL の答え手は transaction の往復を psycopg の pipeline mode で送るが、この環境は pipeline mode を"
                                       " 持たない(psycopg {} の実装 {}・libpq {} — libpq 14 以上が要る)")
                                    psycopg.__version__ psycopg.pq.__impl__ (psycopg.pq.version)))))
    (setv self.size size
          self.timeouts timeouts
          self.origin (. (uuid.uuid4) hex)
          self.databases (dfor d databases d.name d)
          self.idle (dfor d databases d.name (Queue))
          self.permits (dfor d databases d.name (threading.BoundedSemaphore size))
          self.listeners {}
          self.listeners-lock (threading.Lock)))

  ;; 答えの型は文字列の前方参照で書く: PostgresListener はこの class より後で定義し、Python 3.13 までは注記を定義の時に評価するので、
  ;; 裸の名は module の読み込みを NameError で落とす(注記を遅れて評価する PEP 649 は 3.14 から)。
  (defn #^ "PostgresListener" listener [self #^ str name #^ str channel]  ; defk にできない: 資源の待ち受けを引く(無ければ作って thread を起こす — VM の外の錠)
    "database name の channel の待ち受け(PostgresListener)を引くため(初めての channel なら作る)。"
    (with [_ self.listeners-lock]
      (setv key #(name channel))
      (when (not-in key self.listeners)
        (setv (get self.listeners key) (PostgresListener self name channel)))
      (get self.listeners key)))

  (defn #^ None ring-local [self #^ str name #^ RaisedNotices raised]  ; defk にできない: 答え手が接続を返す finally の中で同期に呼ぶ(VM の外の錠 — 取り消しの最中でも effect を出さずに鳴らす)
    "合図が届く所まで流れていれば(raised.sent)、同じ process で channel ごとに掛かっている呼び鈴のうち、合図の名に関わる物をその場で
     鳴らすため(頭の註 — 待ち受けがまだ無ければ掛かっている呼び鈴も無い)。"
    (when raised.sent
      (for [[channel topics] (.items raised.topics)]
        (with [_ self.listeners-lock]
          (setv listener (.get self.listeners #(name channel))))
        (when (is-not listener None)
          (.ring listener topics)))))

  (defn names [self]  ; defk にできない: 答え手の番(:when)で呼ぶ読み(Program を返すと真偽にならない)
    "この貸し出しが答える database の名の並びを読むため。"
    (tuple self.databases))

  (defn connection-options [self #^ str name]  ; defk にできない: 接続を開く thread の中で読む psycopg への引数(Program を返すと psycopg へ渡せない)
    "開く接続に付ける上限を libpq の接続の parameter の写像で読むため(connect の keyword に渡す — DSN に同じ名が在ればこちらが勝つ)。
     ただし options(-c を連ねた 1 本の値)は勝たせず、database name の DSN の options に engine の上限の -c を継ぎ足す — 勝たせると DSN の
     -c search_path などが黙って消える(agora-redesign #1771)。"
    (setv timeouts self.timeouts
          options {"connect_timeout" timeouts.connect-seconds
                   "keepalives" 1
                   "keepalives_idle" timeouts.keepalive-idle-seconds
                   "keepalives_interval" timeouts.keepalive-interval-seconds
                   "keepalives_count" timeouts.keepalive-count
                   "tcp_user_timeout" timeouts.unacknowledged-milliseconds})
    ;; engine の上限(-c を連ねる): 文の上限と、transaction の途中で何もしない上限(agora-redesign #1846)。None の上限は付けない。
    (setv bounds (lfor [setting value] [#("statement_timeout" timeouts.statement-milliseconds)
                                        #("idle_in_transaction_session_timeout" timeouts.idle-transaction-milliseconds)]
                       :if (is-not value None)
                       (.format "-c {}={}" setting value)))
    (when bounds
      (setv bound (.join " " bounds)
            dsn (. (get self.databases name) dsn)
            declared (when dsn
                       ;; DSN の読みは libpq の綴り(URI・key=value の両方)なので psycopg の読みに任せる(DSN が空なら読まない —
                       ;; psycopg は postgres の追加の依存で、接続を開く acquire と同じく使う時だけ読む)。
                       (import psycopg.conninfo [conninfo-to-dict])
                       (.get (conninfo-to-dict dsn) "options")))
      (setv (get options "options") (if declared (+ declared " " bound) bound)))
    options)

  (defn acquire [self #^ str name]  ; defk にできない: 資源の貸し出し(thread の間で blocking に待つ)
    "接続を 1 本借りるため(空きが無ければ開く・上限なら返されるまで待つ)。開けない時は psycopg の例外を通す。"
    (import psycopg)
    (import psycopg.types.string [TextLoader])
    (.acquire (get self.permits name))
    (try
      (try
        (.get-nowait (get self.idle name))
        (except [Empty]
          (setv connection (psycopg.connect (. (get self.databases name) dsn) :autocommit True
                                            #** (.connection-options self name)))
          (.register-loader connection.adapters "json" TextLoader)
          (.register-loader connection.adapters "jsonb" TextLoader)
          connection))
      (except [BaseException]
        (.release (get self.permits name))
        (raise))))

  (defn release [self #^ str name connection]  ; defk にできない: 資源の返却(with / finally から呼ぶ)
    "借りた接続を返すため(切れていれば捨てる・pipeline mode のまま残っていれば捨てる — 頭の註・目安を越える値を通していれば捨てる —
     CARRIED-LARGE・transaction の途中なら rollback し、できなければ捨てる)。"
    (import psycopg)
    (try
      (cond
        (or connection.closed connection.broken) (.close connection)
        (!= connection.pgconn.pipeline-status psycopg.pq.PipelineStatus.OFF) (.close connection)
        (in connection CARRIED-LARGE) (.close connection)
        (!= connection.info.transaction-status psycopg.pq.TransactionStatus.IDLE)
          (try
            (.rollback connection)
            (.put (get self.idle name) connection)
            (except [psycopg.Error] (.close connection)))
        True (.put (get self.idle name) connection))
      (finally (.release (get self.permits name)))))

  (defn close [self]  ; defk にできない: 組み立ての側が止める時に呼ぶ資源の後始末
    "空きの接続を全部閉じるため(貸している接続は返された時に閉じない — 止めた後に呼ぶ)。"
    (for [idle (.values self.idle)]
      (while (not (.empty idle))
        (.close (.get-nowait idle))))))


(defclass PostgresListener []
  "channel 1 つの待ち受け(頭の註): LISTEN を張った接続 1 本を daemon の thread で持ち、掛かっている呼び鈴(外の promise)のうち通知の名に
   関わる物を鳴らす(自分の貸し出しの印の通知は鳴らさない)。資源なので値の型ではない。bells = 呼び鈴 → 待つ名(frozenset | None = 全部の
   合図で鳴る — 掛けた順に鳴らす dict。set にすると順が object の番地で決まり、起きる順が走らせるたびに変わる)・listening = 今 LISTEN が
   張れているか・listened = 1 度でも張れたか・failure = 張れていない理由・settled = 最初の接続を試し終えた印。"

  (defn #^ None __init__ [self #^ PostgresConnections connections #^ str name #^ str channel]  ; defk にできない: 資源の class の初期化
    "待ち受けを作り、LISTEN の thread を起こすため。"
    (setv self.connections connections
          self.name name
          self.channel channel
          self.lock (threading.Lock)
          self.bells {}
          self.listening False
          self.listened False
          self.failure None
          self.settled (threading.Event))
    (.start (threading.Thread :target self.listen :name "doeff-postgres-listen" :daemon True)))

  (defn #^ None ring [self #^ (| frozenset None) topics]  ; defk にできない: 待ち受けの thread と接続を返した後の答え手が呼ぶ(VM の外)
    "名 topics の合図に関わる呼び鈴を鳴らして外すため(頭の註 — topics が None = 名の分からない合図なら全部・待つ名が None の呼び鈴は
     どの合図でも・他は名が重なる呼び鈴だけ。鳴った呼び鈴は True で完了する — WaitWithin の時間切れの None と見分ける)。"
    (with [_ self.lock]
      (setv bells (tuple (gfor [bell waiting] (.items self.bells)
                               :if (or (is topics None) (is waiting None) (not (.isdisjoint waiting topics)))
                               bell)))
      (for [bell bells]
        (.pop self.bells bell)))
    (for [bell bells]
      (.complete bell True)))

  (defn #^ (| str None) hang [self #^ ExternalPromise bell #^ (| frozenset None) topics]  ; defk にできない: driver の thread で回す(最初の接続を待つ)
    "1 度でも LISTEN が張れていれば、名 topics(None = 全部の合図)を待つ呼び鈴を掛けて None を、1 度も張れていなければ理由の文を返すため
     (最初の接続を試し終えるまで待つ)。繋ぎ直しの最中に掛けた呼び鈴は、繋ぎ直した時に鳴る(その間の通知は届かないので、待ち手は鳴った後に
     読み直す)。"
    (.wait self.settled)
    (with [_ self.lock]
      (if self.listened
          (do (setv (get self.bells bell) topics) None)
          (or self.failure "LISTEN が張れていない"))))

  (defn #^ None drop [self #^ ExternalPromise bell]  ; defk にできない: 答え手の節と取り消しの後始末が呼ぶ(VM の外の錠)
    "鳴らなかった呼び鈴を外し、その外の promise を終わらせるため(もう外れていれば外す所は何もしない)。待ち手はもう居ない(待ちを終えた
     後か、掛けるのを取り消した時に呼ぶ)ので、None で完了しても誰も起きない — 終わらせないと scheduler の promise の行が pending の
     まま残り、終わった物だけを消す掃除の外になる(#3508・#3494)。鳴って完了済みの呼び鈴への 2 度目の完了は scheduler が
     無視する。"
    (with [_ self.lock]
      (.pop self.bells bell None))
    (.complete bell None))

  (defn #^ None listen [self]  ; defk にできない: daemon の thread の本体(blocking に通知を待つ)
    "LISTEN を張って通知ごとに名の関わる呼び鈴を鳴らし(自分の貸し出しの印の通知は鳴らさない — 同じ process の呼び鈴は合図を流した
     答え手が鳴らし済み)、切れたら LISTEN-RETRY-SECONDS 置いて繋ぎ直すため(張った時にも鳴らす — 頭の註)。"
    (import psycopg)
    (import psycopg [sql])
    (while True
      (try
        (with [connection (psycopg.connect (. (get self.connections.databases self.name) dsn) :autocommit True
                                           #** (.connection-options self.connections self.name))]
          (.execute connection (.format (sql.SQL "LISTEN {}") (sql.Identifier self.channel)))
          ;; 張った時に鳴らすのは、張った瞬間に掛かっていた呼び鈴(繋ぎ直しの間の通知を取りこぼした待ち手)だけ — 写しを錠の中で
          ;; 取ってから settled を立てる。先に settled を立てて後から ring すると、最初の接続を待っていた hang が掛けたばかりの呼び鈴を
          ;; 合図なしに鳴らしていた(rollback の後に鳴る・日次の test_pg_notice が時刻しだいで赤・agora-redesign #3210)。
          (with [_ self.lock]
            (setv self.listening True
                  self.listened True
                  self.failure None)
            (setv pending (tuple self.bells))
            (.clear self.bells))
          (for [bell pending]
            (.complete bell True))
          (.set self.settled)
          (for [notice (.notifies connection)]
            (setv heard (run-detached (heard-notice notice.payload)))
            (when (!= heard.origin self.connections.origin)
              (.ring self (if (is heard.topics None) None (frozenset heard.topics))))))
        (except [error psycopg.Error]
          (with [_ self.lock]
            (setv self.listening False
                  self.failure (str error)))
          (.set self.settled)
          (.ring self None)
          (time.sleep LISTEN-RETRY-SECONDS))))))


(defk postgres-statement [statement params]
  {:pre [(: statement str) (: params tuple)] :post [(: % PostgresStatement)]
   :tags {:context "sql" :role "foundation"}}
  "中立の記法の文を psycopg の `%(name)s` の文へ書き換えるため(文の `%` は `%%` に・引数の名の食い違いは ValueError)。"
  (<- parts (split-statement statement))
  (<- (checked-params parts params))
  (var text "")
  (for [part parts]
    (match part
      (SqlText :text piece) (:= text (+ text (.replace piece "%" "%%")))
      (SqlPlaceholder :name name) (:= text (+ text (.format "%({})s" name)))))
  (PostgresStatement :text text :params params))


(defk postgres-insert-statement [table columns]
  {:pre [(: table str) (: columns tuple)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows の executemany の文(位置の `%s`)を描くため。"
  (.format "INSERT INTO {} ({}) VALUES ({})" (! (checked-identifier table)) (.join ", " (! (checked-identifiers columns)))
           (.join ", " (* ["%s"] (len columns)))))


(defk postgres-failure [sqlstate class-names message]
  {:pre [(: sqlstate (| str None)) (: class-names tuple) (: message str)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "psycopg の例外(SQLSTATE・類の名の並び = MRO の名・文)を失敗の値へ写すため(頭の註)。postgres-sql-handler と
   pooled-postgres-sql-handler が共に使う読み分けの 1 点。"
  (cond
    (and (is-not sqlstate None) (or (.startswith sqlstate UNREACHABLE-SQLSTATE-CLASS) (in sqlstate UNREACHABLE-SQLSTATES)))
      (SqlUnreachable :reason message)
    (is-not sqlstate None) (SqlFailed :sqlstate sqlstate :reason message)
    (any (gfor name class-names (in name UNREACHABLE-CLASSES))) (SqlUnreachable :reason message)
    True (SqlFailed :sqlstate (next (gfor name class-names :if (in name DRIVER-CLASS-SQLSTATES) (get DRIVER-CLASS-SQLSTATES name)) None)
                    :reason message)))


(defk postgres-schema-statements [tables]
  {:pre [(: tables tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を PostgreSQL の DDL(CREATE TABLE / INDEX IF NOT EXISTS)に描くため。"
  (var statements [])
  (for [table tables]
    (val table-name (! (checked-identifier table.name)))
    (var definitions [])
    (for [column table.columns]
      (.append definitions (.format "{} {}{}" (! (checked-identifier column.name)) (get POSTGRES-TYPES column.type)
                                    (if column.nullable "" " NOT NULL"))))
    (when table.primary-key
      (.append definitions (.format "PRIMARY KEY ({})" (.join ", " (! (checked-identifiers table.primary-key))))))
    (.append statements (.format "CREATE TABLE IF NOT EXISTS {} ({})" table-name (.join ", " definitions)))
    (for [index table.indexes]
      (.append statements (.format "CREATE {}INDEX IF NOT EXISTS {} ON {} ({})" (if index.unique "UNIQUE " "") (! (checked-identifier index.name))
                                   table-name (.join ", " (! (checked-identifiers index.columns)))))))
  (tuple statements))


(defk postgres-error [error]
  {:pre [(: error Exception)] :post [(: % (| SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "psycopg の例外を postgres-failure の入力に割るため。"
  (<- failure (postgres-failure (getattr error "sqlstate" None) (tuple (gfor c (. (type error) __mro__) c.__name__)) (str error)))
  failure)


;; defk にできない: 行の値の数だけ呼ぶ純関数(Program を返すと値ごとに VM を往復する)
(defn #^ int cell-bytes [#^ object value #^ bool doubled]
  "行の値 1 つが libpq の message に占める bytes を見積もるため(str = UTF-8 の長さ・bytes = 長さ — doubled なら text の形の 16 進で 2 倍・
   他の型は数えない)。"
  (match value
    (str) (if (.isascii value) (len value) (len (.encode value "utf-8")))
    (bytes) (if doubled (* 2 (len value)) (len value))
    _ 0))


(defk largest-message-bytes [rows doubled]
  {:pre [(: rows tuple) (: doubled bool)] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "行の並び rows(行 1 つ = libpq の message 1 つ — 送る引数の組か、受けた行)のうち、一番大きな message の bytes を見積もるため
   (LARGE-CARRY-BYTES と比べる — buffer は一番大きな message まで伸びる。doubled = bytes を text の形の 16 進で受けた)。"
  (max (gfor row rows (sum (gfor value row (cell-bytes value doubled)))) :default 0))


(defk noted-carry [connection carried]
  {:pre [(: connection "psycopg の接続") (: carried int)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "接続が通した message の bytes carried が目安(LARGE-CARRY-BYTES)を越えていれば、返す時に閉じる印(CARRIED-LARGE)を付けるため。"
  (when (>= carried LARGE-CARRY-BYTES)
    (.add CARRIED-LARGE connection))
  None)


(defk postgres-run [connection text params binary]
  {:pre [(: connection "psycopg の接続") (: text str) (: params (| tuple None)) (: binary bool)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つを流して答えの値にするため(params = None は引数の書き換えを通さない文 — DDL と transaction の区切り・binary = 答えの行を
   binary の形で受ける — SqlQuery の binary)。送る引数と受けた行が目安を越えれば、返す時に閉じる印を付ける(noted-carry — 送りは
   流す前に付ける: 落ちた文でも buffer は伸びている)。connection は psycopg の接続(psycopg を読まない環境でも module を読めるよう、型を
   契約に書かない)。"
  (import psycopg)
  (when (is-not params None)
    (<- sent (largest-message-bytes #((tuple (gfor p params p.value))) False))
    (<- (noted-carry connection sent)))
  (try
    (with [cursor (.cursor connection)]
      (.execute cursor text (if (is params None) None (dfor p params p.name p.value)) :binary binary)
      (if (is cursor.description None)
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None))
          (do (val rows (! (normalized-rows (.fetchall cursor))))
              (<- received (largest-message-bytes rows (not binary)))
              (<- (noted-carry connection received))
              (SqlRows :rows rows :rowcount (len rows)))))
    (except [error psycopg.Error]
      (<- failure (postgres-error error))
      failure)))


(defk postgres-query [connection request]
  {:pre [(: connection "psycopg の接続") (: request SqlQuery)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlQuery 1 つに答えるため。"
  (<- bound (postgres-statement request.statement request.params))
  (<- answer (postgres-run connection bound.text bound.params request.binary))
  answer)


(defk postgres-insert [connection request]
  {:pre [(: connection "psycopg の接続") (: request SqlInsertRows)] :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlInsertRows 1 つに executemany で答えるため(行 1 つ = 送る message 1 つ — 一番大きな行が目安を越えれば返す時に閉じる印を付ける)。"
  (import psycopg)
  (<- (checked-rows request.columns request.rows))
  (<- text (postgres-insert-statement request.table request.columns))
  (<- sent (largest-message-bytes request.rows False))
  (<- (noted-carry connection sent))
  (if (not request.rows)
      (SqlRows :rows #() :rowcount 0)
      (try
        (with [cursor (.cursor connection)]
          (.executemany cursor text (list request.rows))
          (SqlRows :rows #() :rowcount (if (>= cursor.rowcount 0) cursor.rowcount None)))
        (except [error psycopg.Error]
          (<- failure (postgres-error error))
          failure))))


(defk postgres-ensure-tables [connection tables]
  {:pre [(: connection "psycopg の接続") (: tables tuple)] :post [(: % (| SqlSchemaApplied SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "表の宣言を DDL に描いて順に流すため(最初の失敗で止める)。"
  (<- statements (postgres-schema-statements tables))
  (for [statement statements]
    (<- answer (postgres-run connection statement None False))
    (when (isinstance answer #(SqlFailed SqlUnreachable))
      (return answer)))
  (SqlSchemaApplied :statements statements))


;; 同じ鍵の transaction を直列にする錠の文(中立の記法)— transaction の最初の往復で BEGIN の次に流す。共有の錠(SqlLock の shared)は
;; LOCK-SHARED-STATEMENT(共有どうしは並んで持て、同じ鍵の排他の錠とだけ互いを待つ)。
(val LOCK-STATEMENT "SELECT pg_advisory_xact_lock(hashtext(:key))")
(val LOCK-SHARED-STATEMENT "SELECT pg_advisory_xact_lock_shared(hashtext(:key))")


(defk transaction-locks [lock-key locks]
  {:pre [(: lock-key (| str None)) (: locks tuple)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "SqlTransaction の錠(lock-key と locks — sql_effects.hy の頭の註)を、取る順の SqlLock の並びにするため(lock-key が先・排他)。"
  (+ (if (is lock-key None) #() #((SqlLock :key lock-key))) locks))


(defk lock-text [lock]
  {:pre [(: lock SqlLock)] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "錠 1 つを取る文の綴り(中立の記法・引数 :key = 錠の鍵 — 共有の錠は LOCK-SHARED-STATEMENT)を選ぶため。"
  (if lock.shared LOCK-SHARED-STATEMENT LOCK-STATEMENT))


;; --- 合図(頭の註)------------------------------------------------------------------------------------------------------------

;; channel へ合図を出す文(中立の記法)— 答え手は他の文と同じ postgres-query で流す(transaction の中なら commit した時だけ届く)。
;; 本文は notice-payload。
(val NOTICE-STATEMENT "SELECT pg_notify(:channel, :payload)")


(defwire NoticeWire
  "合図の本文(NOTIFY の payload — 頭の註)の JSON の object: origin = 合図を出して同じ process の呼び鈴を鳴らす貸し出しの印(None = 鳴らす
   貸し出しが無い)・topics = 合図が関わる名の並び(名の順・重ねない — None = 名の分からない合図 = 全部の待ち手に関わる)。"
  {:tags {:context "sql" :role "type" :reads "json"} :names :camel :unknown :reject}
  (setv #^ (| str None) origin None)
  (setv #^ (| (get tuple #(str ...)) None) topics None))


(defk notice-payload [origin topics]
  {:pre [(: origin (| str None)) (: topics (| tuple None))] :post [(: % str)]
   :tags {:context "sql" :role "foundation"}}
  "合図の本文(NOTIFY の payload — NoticeWire の JSON)を綴るため(頭の註)。名を載せると本文が NOTICE-PAYLOAD-LIMIT bytes に届く書き
   (多くの名を触る書き)は名を載せない — 全部に関わる合図にする(絞れない合図は全部を起こす側の意味 — 起こし損ねない)。"
  (<- named str (dump-json (NoticeWire :origin origin :topics (if (is topics None) None (tuple (sorted (set topics)))))))
  (<- marked str (dump-json (NoticeWire :origin origin)))
  (if (< (len (.encode named "utf-8")) NOTICE-PAYLOAD-LIMIT) named marked))


(defk heard-notice [payload]
  {:pre [(: payload str)] :post [(: % NoticeWire)]
   :tags {:context "sql" :role "foundation"}}
  "待ち受けが受けた合図の本文を読むため。NoticeWire の形でない本文(空・JSON でない・欄の形が違う — 印を持たない書き手や手で流した NOTIFY)は、
   印の無い・名の分からない合図 = 全部の待ち手に関わる合図と読む(頭の註の定義)。"
  (<- heard (| NoticeWire Malformed) (parse-json NoticeWire payload))
  (match heard
    (NoticeWire) heard
    (Malformed) (NoticeWire)))


(defk notice-params [origin notify]
  {:pre [(: origin (| str None)) (: notify SqlNotify)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "合図 notify を流す文(NOTICE-STATEMENT)の引数を作るため(本文は印 origin と合図の名 — notice-payload)。"
  (<- payload (notice-payload origin notify.topics))
  #((SqlParam :name "channel" :value notify.channel) (SqlParam :name "payload" :value payload)))


(defk postgres-notice [connection database origin notify]
  {:pre [(: connection PipelineConnection) (: database str) (: origin (| str None)) (: notify SqlNotify)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "合図 notify を接続で流すため(transaction の中なら commit した時だけ届く — 印 origin は合図の後に同じ process の呼び鈴を鳴らす貸し出しの
   印・None = 鳴らす貸し出しが無い)。"
  (<- params (notice-params origin notify))
  (<- answer (postgres-query connection (SqlQuery database NOTICE-STATEMENT params)))
  answer)


(defk noted-notice [raised notify]
  {:pre [(: raised RaisedNotices) (: notify SqlNotify)] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "合図 notify を覚え raised に足すため(接続を返した後に同じ process の呼び鈴を鳴らす — 同じ channel の名は和・どちらかが名の分からない
   合図 None なら None)。"
  (val held (.get raised.topics notify.channel (frozenset)))
  (setv (get raised.topics notify.channel)
        (if (or (is held None) (is notify.topics None)) None (| held (frozenset notify.topics))))
  None)


(defrecord PostgresStep
  "pipeline に積む文 1 つ(postgres-flush の往復の中の順): text = psycopg へ渡す文 / params = 名つきの引数(None = 引数の書き換えを
   通さない文 — BEGIN・COMMIT)/ rows = executemany で流す行(None = 文 1 つ)/ binary = 答えの行を binary の形で受ける(SqlQuery の
   binary)/ answered = その答えを往復の答え(requests の答え)に載せるか(BEGIN・錠・合図・COMMIT は載せない)。"
  (#^ str text)
  (#^ (| (get tuple #(SqlParam ...)) None) params)
  (#^ (| (get tuple #((get tuple #((| int float str bytes bool None) ...)) ...)) None) rows)
  (#^ bool binary)
  (#^ bool answered))


(defk step-sent-bytes [step]
  {:pre [(: step (| PostgresStep None))] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "pipeline に積む文 1 つが送る一番大きな message の bytes を見積もるため(引数の組 1 つか、executemany の行 1 つ — noted-carry へ渡す)。"
  (cond
    (is step None) 0
    (is-not step.rows None) (! (largest-message-bytes step.rows False))
    (is-not step.params None) (! (largest-message-bytes #((tuple (gfor p step.params p.value))) False))
    True 0))


(defk request-step [request]
  {:pre [(: request (| SqlQuery SqlInsertRows))] :post [(: % (| PostgresStep None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の中の文 1 つ(SqlQuery | SqlInsertRows)を pipeline の文にするため(引数と行の検めは送る前 — 誤りは ValueError / TypeError)。
   行の無い SqlInsertRows は文を流さないので None(答えは SqlRows の 0 行)。"
  (match request
    (SqlQuery :statement statement :params params :binary binary)
      (do (<- bound (postgres-statement statement params))
          (PostgresStep :text bound.text :params bound.params :rows None :binary binary :answered True))
    (SqlInsertRows :table table :columns columns :rows rows)
      (do (<- (checked-rows columns rows))
          (<- text (postgres-insert-statement table columns))
          (if rows (PostgresStep :text text :params None :rows rows :binary False :answered True) None))))


(defk flush-steps [locks origin flush]
  {:pre [(: locks tuple) (: origin (| str None)) (: flush TransactionFlush)] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "transaction の往復 1 回(TransactionFlush)を pipeline に積む文の並びにするため(順 = BEGIN・錠(locks の順 — transaction-locks)・文・合図・
   COMMIT)。requests の位置は答えの順と同じで、文を流さない request(行の無い SqlInsertRows)は None のまま並べる。合図の本文には貸し出しの
   印 origin を載せる(頭の註 — None = 合図の後に同じ process の呼び鈴を鳴らす貸し出しが無い)。"
  (var opening #())
  (when flush.opening
    (:= opening #((PostgresStep :text "BEGIN" :params None :rows None :binary False :answered False)))
    (for [claim locks]
      (<- text (lock-text claim))
      (<- lock (postgres-statement text #((SqlParam :name "key" :value claim.key))))
      (:= opening (+ opening #((PostgresStep :text lock.text :params lock.params :rows None :binary False :answered False))))))
  (var requests #())
  (for [request flush.requests]
    (<- step (request-step request))
    (:= requests (+ requests #(step))))
  (var notices #())
  (for [notify flush.notices]
    (<- params (notice-params origin notify))
    (<- notice (postgres-statement NOTICE-STATEMENT params))
    (:= notices (+ notices #((PostgresStep :text notice.text :params notice.params :rows None :binary False :answered False)))))
  (+ opening requests notices
     (if flush.closing #((PostgresStep :text "COMMIT" :params None :rows None :binary False :answered False)) #())))


(defclass [runtime-checkable] StatementCursor [Protocol]
  "pipeline に積んだ文 1 つの答えを受ける driver の cursor の形(psycopg 3 の Cursor がこの形を持つ — psycopg はこの module の I/O の関数の
   中でだけ読むので、契約は psycopg の型でなくこの形で書く)。description = 行を返す文の欄の並び(返さない文は None)・rowcount = engine が
   数えた行の数・fetchall = 行を読む・close = 閉じる・executemany = 同じ文を行ごとに積む。"
  #^ (| tuple list None) description
  #^ int rowcount
  #^ (get Callable #([] list)) fetchall
  #^ (get Callable #([] None)) close
  #^ (get Callable #([str list] None)) executemany)


(defclass [runtime-checkable] PipelineConnection [Protocol]
  "transaction の往復を 1 つの pipeline で流す driver の接続の形(psycopg 3 の Connection がこの形を持つ — StatementCursor と同じ理由で
   psycopg の型を契約に書かない)。pipeline = pipeline mode の出入り(with)・execute = 文 1 つを積んで cursor を返す・cursor = executemany
   を積む cursor を作る。"
  #^ (get Callable #([] AbstractContextManager)) pipeline
  #^ (get Callable #([str (| dict None)] StatementCursor)) execute
  #^ (get Callable #([] StatementCursor)) cursor)


(defk step-queries [transformer step]
  {:pre [(: transformer "psycopg の Transformer") (: step (| PostgresStep None))] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "pipeline に積む文 1 つを、libpq へ送る形(psycopg の PostgresQuery — 文の bytes・引数の bytes・型・形)の並びにするため(文 1 つは 1 つ・
   executemany の文は行ごとに 1 つ・文を流さない request は 0)。引数の書き換えと値の写しは psycopg の cursor の execute と同じ物を使う
   (同じ文・同じ値なら server に届く message は cursor の道と同じ)。"
  (import psycopg._queries [PostgresQuery])
  (when (is step None)
    (return #()))
  (val values (if (is step.rows None)
                  #((if (is step.params None) None (dfor p step.params p.name p.value)))
                  step.rows))
  (var queries #())
  (for [one values]
    (val query (PostgresQuery transformer))
    (.convert query step.text one)
    (:= queries (+ queries #(query))))
  queries)


(defclass [(dataclass :kw-only True)] PreparedBook []
  "接続 1 本で名つきにした文の覚え(postgres-flush の註): names = (文の bytes, 引数の型の oid の並び) → server の上の名・issued = 次に付ける
   名の番号。書き換えるのは接続を借りた driver の thread だけなので値の型ではない。"
  (setv #^ dict names (field :default-factory dict))
  (setv #^ int issued 0))


(defk prepared-book [connection]
  {:pre [(: connection "psycopg の接続")] :post [(: % PreparedBook)]
   :tags {:context "sql" :role "foundation"}}
  "接続の名つきの文の覚えを引くため(無ければ空で作る — 覚えの表は thread の間で分け合うので錠の中で)。"
  (with [_ PREPARED-LOCK]
    (when (not-in connection PREPARED)
      (setv (get PREPARED connection) (PreparedBook)))
    (get PREPARED connection)))


(defk socket-ready [pgconn events]
  {:pre [(: pgconn "psycopg の PGconn") (: events int)] :post [(: % int)]
   :tags {:context "sql" :role "foundation"}}
  "接続の socket が events(select の POLLIN | POLLOUT)のどれかになるまで driver の thread で待ち、起きた events を返すため(上限は持たない —
   死んだ相手は接続の上限 tcp_user_timeout と keepalive が socket の誤りにし、文の上限は server の statement_timeout が持つ — 頭の註)。"
  (import select)
  (val poller (select.poll))
  (.register poller pgconn.socket events)
  (var happened 0)
  (while (not happened)
    (:= happened (sum (gfor #(_ seen) (.poll poller) seen))))
  happened)


(defk libpq-sent [pgconn]
  {:pre [(: pgconn "psycopg の PGconn")] :post [(: % None)]
   :tags {:context "sql" :role "foundation"}}
  "libpq の送りの buffer に積んだ文を、全部 socket へ送り切るため(送れない間は書けるか読めるまで待ち、読める時は先に受けて server の
   送りが詰まらないようにする — libpq の PQflush の作法)。"
  (import select)
  (while (.flush pgconn)
    (<- happened (socket-ready pgconn (| select.POLLIN select.POLLOUT)))
    (when (& happened select.POLLIN)
      (.consume-input pgconn)))
  None)


(defk libpq-results [pgconn]
  {:pre [(: pgconn "psycopg の PGconn")] :post [(: % tuple)]
   :tags {:context "sql" :role "foundation"}}
  "pipeline の sync までの答え(文ごとの PGresult — 送った順・sync の印は含めない)を受けるため(受けるまで読めるのを待つ)。"
  (import select)
  (import psycopg [pq])
  (var results #())
  (while True
    (while (.is-busy pgconn)
      (<- (socket-ready pgconn select.POLLIN))
      (.consume-input pgconn))
    (val result (.get-result pgconn))
    (cond
      (is result None) None
      (= result.status pq.ExecStatus.PIPELINE-SYNC) (break)
      True (:= results (+ results #(result)))))
  results)


(defk result-failure [connection results]
  {:pre [(: connection "psycopg の接続") (: results tuple)] :post [(: % (| Exception None))]
   :tags {:context "sql" :role "foundation"}}
  "往復の答えのうち、最初に落ちた文の失敗を psycopg の例外にするため(落ちた文の後ろは sync まで PIPELINE_ABORTED — 落ちていなければ None)。"
  (import psycopg [pq errors])
  (val failed (next (gfor result results :if (= result.status pq.ExecStatus.FATAL-ERROR) result) None))
  (cond
    (is-not failed None) (errors.error-from-result failed :encoding connection.info.encoding)
    (any (gfor result results (= result.status pq.ExecStatus.PIPELINE-ABORTED)))
      (errors.PipelineAborted "pipeline の文が流されなかった(落ちた文の答えが無い)")
    True None))


(defk results-answer [transformer step results]
  {:pre [(: transformer "psycopg の Transformer") (: step (| PostgresStep None)) (: results tuple)] :post [(: % SqlRows)]
   :tags {:context "sql" :role "foundation"}}
  "pipeline の sync の後に、文 1 つの答え(PGresult の並び — executemany は行ごとに 1 つ)を SqlRows にするため(文を流さなかった request は 0 行・
   行を返さない文は engine が数えた行の数 — executemany は和)。"
  (import psycopg [pq])
  (cond
    (not results) (SqlRows :rows #() :rowcount 0)
    (!= (. (get results 0) status) pq.ExecStatus.TUPLES-OK)
      (do (val counts (tuple (gfor result results result.command-tuples)))
          (SqlRows :rows #() :rowcount (if (any (gfor count counts (is count None))) None (sum counts))))
    True (do (val result (get results 0))
             (.set-pgresult transformer result)
             (val rows (! (normalized-rows (tuple (.load-rows transformer 0 result.ntuples tuple)))))
             (SqlRows :rows rows :rowcount (len rows)))))


(defk postgres-flush [connection locks origin flush]
  {:pre [(: connection PipelineConnection) (: locks tuple) (: origin (| str None)) (: flush TransactionFlush)]
   :post [(: % (| tuple SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の往復 1 回(TransactionFlush)を 1 つの pipeline で送り、答えを順に受けるため(頭の註)。答え = requests と同じ順の SqlRows の
   tuple | 最初に落ちた文の失敗(BEGIN・錠・合図・COMMIT を含む — PostgreSQL は落ちた文から sync までを流さない)。driver の I/O なので
   driver の thread で回す。locks = 最初の往復で BEGIN の次に取る錠の並び・origin = 合図の本文に載せる貸し出しの印(flush-steps)。
   文は psycopg の cursor を通さず、libpq の口(接続の pgconn)で pipeline mode の送りの buffer に全部積み、sync を 1 度積んでから 1 度に
   送り切り、sync までの答えを順に受ける(card ki-f7a1ef77251f — cursor の pipeline は文 1 つごとに cursor・写しの組・socket の送りと
   待ちを作り、本番の free-threaded の Python の psycopg〔C の拡張の無い pure-python の libpq の口〕で文 1 つに約 0.25 ms の CPU を使い、
   DB は文が 1 つ届くごとに起きて次の文を待った〔pipeline の中の ClientRead〕。手元の 8 文の往復で client の CPU 2.09 → 0.62 ms)。
   文は接続ごとに名つきの prepared statement にして使い回す(PREPARED — 初めての文は同じ往復で用意してから流す。cursor の道でも psycopg が
   5 回目から用意していた。名を付けないと server が毎回 plan を組み、索引 76 本の表の行の錠つきの読みで 1 回 約 0.4 ms)。
   引数の書き換えと値の写し・答えの値の読みは cursor と同じ psycopg の部品(step-queries・results-answer)。pipeline mode は答えを sync まで
   受け切った後に出る(落ちた往復でも — 送りか受けの途中で接続が切れて出られずに残った接続は、返す時に捨てる)。"
  (import psycopg)
  (import psycopg.adapt [Transformer])
  (<- steps (flush-steps locks origin flush))
  ;; 送りの印は積む前に付ける(落ちた往復でも buffer は伸びている — postgres-run と同じ)。
  (for [step steps]
    (<- sent (step-sent-bytes step))
    (<- (noted-carry connection sent)))
  (val pgconn connection.pgconn)
  (val transformer (Transformer connection))
  (<- book (prepared-book connection))
  ;; この往復で新しく名を付けた文(往復が通った時だけ覚えに足す — 落ちた往復の名は server に残るが、名は番号で重ねない)。
  (var fresh {})
  ;; 文ごとの答えの数(名を付けた往復の文は、用意の答えと流した答えの 2 つ)— step の順・step の中の文の順。
  (var spans #())
  (var counts #())
  (var name None)
  (var made False)
  (try
    (with [_ connection.lock]
      (var queued #())
      (for [step steps]
        (<- queries (step-queries transformer step))
        (:= queued (+ queued #(queries))))
      (.enter-pipeline-mode pgconn)
      (for [#(step queries) (zip steps queued :strict True)]
        (:= counts #())
        (for [query queries]
          (val shape #(query.query (tuple (or query.types #()))))
          (:= name (or (.get book.names shape) (.get fresh shape)))
          (:= made False)
          (when (and (is name None) (< (+ (len book.names) (len fresh)) PREPARED-MAX))
            (:= name (.encode (.format "doeff_pq_{}" book.issued) "ascii"))
            (setv book.issued (+ book.issued 1))
            (setv (get fresh shape) name)
            (.send-prepare pgconn name query.query :param-types query.types)
            (:= made True))
          (val shaped (if step.binary psycopg.pq.Format.BINARY psycopg.pq.Format.TEXT))
          (if (is name None)
              (.send-query-params pgconn query.query query.params :param-types query.types :param-formats query.formats
                                  :result-format shaped)
              (.send-query-prepared pgconn name query.params :param-formats query.formats :result-format shaped))
          (:= counts (+ counts #((if made 2 1)))))
        (:= spans (+ spans #(counts))))
      (.pipeline-sync pgconn)
      (<- (libpq-sent pgconn))
      (<- results (libpq-results pgconn))
      (.exit-pipeline-mode pgconn))
    (except [error psycopg.Error]
      (<- failure (postgres-error error))
      (return failure)))
  (<- failed (result-failure connection results))
  (when (is-not failed None)
    (<- failure (postgres-error failed))
    (return failure))
  (.update book.names fresh)
  (var answers #())
  (var at 0)
  (var mine #())
  (for [#(step step-counts) (zip steps spans :strict True)]
    ;; 文ごとに、その文の答えの最後(名を付けた文は用意の答えの次)を取る。
    (:= mine #())
    (for [count step-counts]
      (:= mine (+ mine #((get results (+ at count -1)))))
      (:= at (+ at count)))
    (when (or (is step None) step.answered)
      (<- answer (results-answer transformer step mine))
      (<- received (largest-message-bytes answer.rows (not (and (is-not step None) step.binary))))
      (<- (noted-carry connection received))
      (:= answers (+ answers #(answer)))))
  answers)


(defk postgres-begin [connection locks]
  {:pre [(: connection PipelineConnection) (: locks tuple)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction を始め、錠の並び locks(transaction-locks)を順に取るため(成功は None — 既定の手順の BEGIN と錠 1 つずつの往復)。"
  (<- began (postgres-run connection "BEGIN" None False))
  (when (not (isinstance began SqlRows))
    (return began))
  (for [claim locks]
    (<- text (lock-text claim))
    (<- locked (postgres-query connection (SqlQuery "" text #((SqlParam :name "key" :value claim.key)))))
    (when (not (isinstance locked SqlRows))
      (return locked)))
  None)


(defk postgres-control [connection statement]
  {:pre [(: connection "psycopg の接続") (: statement str)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の区切りの文(COMMIT・ROLLBACK)を流すため(成功は None)。"
  (<- answer (postgres-run connection statement None False))
  (if (isinstance answer SqlRows) None answer))


(defk postgres-lease [connections database]
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "psycopg の接続 | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "接続を 1 本借りるため(開けなければ SqlUnreachable)。psycopg は借りが誤りで終わった時の読み分けでだけ読む — driver を開くのは貸し出しの
   acquire なので、借りが通る道は偽の貸し出しで撃てる(agora-redesign #2792 の検)。"
  (try
    (.acquire connections database)
    (except [error Exception]
      (import psycopg)
      (match error
        (psycopg.Error) (SqlUnreachable :reason (str error))
        _ (raise)))))


(deff lease-now [connections database]  ; defk にできない: driver の thread で回す入口(VM の外)
  {:pre [(: connections PostgresConnections) (: database str)] :post [(: % "psycopg の接続 | SqlUnreachable")]}
  "driver の thread で接続を 1 本借りるため(許可を待つのもこの thread — 開けなければ SqlUnreachable)。"
  (run-detached (postgres-lease connections database)))


(deff lease-run-return [connections database claim work]  ; defk にできない: driver の thread で回す入口
  {:pre [(: connections PostgresConnections) (: database str) (: claim "threading.Lock") (: work "(接続) → Program の callable")]
   :post [(: % "work の答え | SqlUnreachable | None")]}
  "driver の thread の仕事 1 つで、接続を借り、借りた接続で work(接続 → Program)を流し、必ず返すため(文 1 つの effect の答え —
   offloaded-statement の註)。借りられなければ SqlUnreachable。借りた後に claim を先に取れた時だけ流す — 取れなければ、待ち手が文の
   始まる前に取り消されているので、文を流さずにすぐ返す(答えは誰にも届かないので None)。"
  (setv leased (lease-now connections database))
  (cond
    (isinstance leased SqlUnreachable) leased
    (not (.acquire claim :blocking False)) (do (.release connections database leased) None)
    True (try
           (run-detached (work leased))
           (finally (.release connections database leased)))))


(defclass [(dataclass :kw-only True)] TransactionLease []
  "transaction 1 つが借りた接続の覚え(offloaded-transaction の註 — 接続を借りる仕事を最初の driver の呼びと、返す仕事を最後の呼びと同じ
   仕事にするため)。書き換えるのは答え手の手順と、錠 guard を握った driver の thread だけなので値の型ではない。connection = 借りた接続 |
   SqlUnreachable(借りられなかった)| None(まだ借りていない)/ submitted = driver の呼びを 1 つでも撃った / returned = 接続を返した
   (借りられなかった時も、もう借りない印として立てる)/ abandoned = 待ち手が去った(この後に撃たれた呼びは流さない)。"
  (setv #^ (| PipelineConnection SqlUnreachable None) connection None)
  (setv #^ bool submitted False)
  (setv #^ bool returned False)
  (setv #^ bool abandoned False))


(defrecord TransactionDriver
  "transaction 1 つの driver の呼びの道具(offloaded-transaction の註): connections = 接続の貸し出し・pool = driver の thread・database = 名・
   lease = 借りた接続の覚え・guard = 接続 1 本への driver の呼びを 1 本ずつ回す錠。"
  (#^ PostgresConnections connections)
  (#^ Executor pool)
  (#^ str database)
  (#^ TransactionLease lease)
  (#^ AbstractContextManager guard))


(deff drive-leased [driver work closing]  ; defk にできない: driver の thread で回す入口(VM の外)
  {:pre [(: driver TransactionDriver) (: work (get Callable #([PipelineConnection] Program))) (: closing bool)]
   :post [(: % "work の答え | SqlUnreachable | None")]}
  "driver の thread の仕事 1 つで、錠 guard の下で、まだ借りていなければ接続を借り、work(接続 → Program)を流し、closing(COMMIT・ROLLBACK を
   流す最後の呼び)なら同じ仕事で接続を返すため(offloaded-transaction の註)。待ち手が去った後・返した後の呼びは流さない(None — 答えは誰にも
   届かない)。借りられなかった後の呼びは流さずに SqlUnreachable を返す。"
  (setv lease driver.lease)
  (with [_ driver.guard]
    (when (or lease.abandoned lease.returned)
      (return None))
    (when (is lease.connection None)
      (setv lease.connection (lease-now driver.connections driver.database)))
    (setv held lease.connection)
    (when (isinstance held SqlUnreachable)
      (when closing
        (setv lease.returned True))
      (return held))
    (try
      (run-detached (work held))
      (finally
        (when closing
          (.release driver.connections driver.database held)
          (setv lease.returned True))))))


(deff abandon-lease [driver]  ; defk にできない: driver の thread で回す後始末の入口
  {:pre [(: driver TransactionDriver)] :post [(: % "None")]}
  "最後の呼びが返さなかった接続を返すため(錠 guard の下 — 走っている呼びの後・offloaded-transaction の註)。印 abandoned を立てるので、まだ
   始まっていない呼びは流さずに終わる(接続を借りる前の呼びなら、借りもしない)。"
  (setv lease driver.lease)
  (with [_ driver.guard]
    (setv lease.abandoned True)
    (setv held lease.connection)
    (when (and (not lease.returned) (is-not held None) (not (isinstance held SqlUnreachable)))
      (.release driver.connections driver.database held))
    (setv lease.returned True)))


(defk driven [driver work closing]
  {:pre [(: driver TransactionDriver) (: work Callable) (: closing bool)] :post [(: % "work の答え | SqlUnreachable | None")]
   :tags {:context "sql" :role "foundation"}}
  "transaction の接続 1 本への driver の呼び 1 つを、pool の thread の仕事 1 つ(まだなら借りる・流す・closing なら返す — drive-leased)で回し、
   撃った task だけが待つため(offloaded-transaction の註)。"
  (setv driver.lease.submitted True)
  (<- answer (offloaded driver.pool (fn [] (drive-leased driver work closing)) keep-nothing))
  answer)


(defclass TransactionAbandoned [Exception]
  "束ねた transaction を driver の thread で回す間に待ち手が去った印(次の往復を流さずに ROLLBACK へ倒す — offloaded-batched-transaction)。")


(defk leased-here [driver]
  {:pre [(: driver TransactionDriver)] :post [(: % (| PipelineConnection SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "束ねた transaction の driver の thread の中で、まだ借りていなければ接続を 1 本借りるため(最初の往復の時 — 文を流さない program は
   借りない・開けなければ SqlUnreachable)。"
  (when (is driver.lease.connection None)
    (<- leased (postgres-lease driver.connections driver.database))
    (setv driver.lease.connection leased))
  driver.lease.connection)


(defk flushed-here [driver locks origin raised flush]
  {:pre [(: driver TransactionDriver) (: locks tuple) (: origin str) (: raised RaisedNotices) (: flush TransactionFlush)]
   :post [(: % (| tuple SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "束ねた transaction の往復 1 回を、driver の thread の中でその場で流すため(offloaded-batched-transaction の註): 待ち手が去っていれば流さずに
   TransactionAbandoned を上げ(ROLLBACK へ倒す)、往復に載る合図を覚え、COMMIT を載せる往復なら流す前に「届く所まで流した」の印を立てる。"
  (when driver.lease.abandoned
    (raise (TransactionAbandoned "待ち手が去ったので次の往復を流さない")))
  (for [notify flush.notices]
    (<- (noted-notice raised notify)))
  (<- held (leased-here driver))
  (when (isinstance held SqlUnreachable)
    (return held))
  (when flush.closing
    (setv raised.sent True))
  (<- answer (postgres-flush held locks origin flush))
  answer)


(defk rolled-back-here [driver]
  {:pre [(: driver TransactionDriver)] :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "束ねた transaction の ROLLBACK を、driver の thread の中で借りた接続に流すため(借りていない・借りられなかった時は流す物が無い)。"
  (val held driver.lease.connection)
  (when (or (is held None) (isinstance held SqlUnreachable))
    (return None))
  (<- answer (postgres-control held "ROLLBACK"))
  answer)


(deff run-batched-here [driver locks origin raised program]  ; defk にできない: driver の thread で回す入口(VM の外から新しい VM を起こす)
  {:pre [(: driver TransactionDriver) (: locks tuple) (: origin str) (: raised RaisedNotices) (: program Program)]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable | None")]}
  "driver の thread の仕事 1 つで、錠 guard の下で、束ねた transaction の全部(program と往復・必要なら ROLLBACK)を新しい VM で回し、借りた
   接続を同じ仕事で返すため(offloaded-batched-transaction の註)。待ち手が始まる前に去っていれば何もしない(None — 答えは誰にも届かない)。"
  (setv lease driver.lease)
  (with [_ driver.guard]
    (when (or lease.abandoned lease.returned)
      (return None))
    (try
      (run-detached (run-in-batched-transaction driver.database program
                                                (fn [flush] (flushed-here driver locks origin raised flush))
                                                (fn [] (rolled-back-here driver))
                                                :accepts-notices True))
      (finally
        (setv held lease.connection)
        (when (and (is-not held None) (not (isinstance held SqlUnreachable)))
          (.release driver.connections driver.database held))
        (setv lease.returned True)))))


(deff abandoned-now [driver]  ; defk にできない: Executor の thread で回す止め方の入口(VM の外)
  {:pre [(: driver TransactionDriver)] :post [(: % "None")]}
  "走っている束ねた transaction に、待ち手が去った印を立てるため(錠 guard を取らない — 走っている仕事が guard を持ったまま次の往復の前に
   読む)。"
  (setv driver.lease.abandoned True)
  None)


(defk offloaded-batched-transaction [connections pool database program locks]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: locks tuple)]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "batched = True の transaction を、driver の thread の仕事 1 つ(借りる → program と往復 → 返す — run-batched-here)で回し、撃った task
   だけが待つため。往復の間に scheduler の thread へ戻らない: 束ねた transaction の program が出せるのは SQL の effect と純粋な計算だけ
   (sql_transaction.hy の scope — 他の effect は TransactionMisused)なので、program を driver の thread の新しい VM で回しても答えは変わらない。
   往復ごとに scheduler へ戻ると、1 往復目の答えから 2 往復目を撃つまでの間(scheduler の列の待ち + 判定)も置き場の書きの錠を持ち続け、
   scheduler が混むほど錠を持つ間が伸びて、同じ錠を待つ書きが積もった(agora-redesign の記録の service の PutRows・card ki-7f9808c3c3d8)。
   取り消し: 待ち手が去ったら、次の往復の前に印(abandoned)を読んで流さずに ROLLBACK し、接続を返す — 走っている往復は止めない(文 1 つずつの
   手順と同じ)。印は取り消しの callback(Executor の止め方 abandoned-now)と、取り消しを受けた task がその場で立てる。finally は錠 guard の
   下の返し abandon-lease を待つので、合図(ring-local)は走っていた往復(COMMIT を含む)の後に鳴る。"
  (val raised (RaisedNotices))
  (val driver (TransactionDriver :connections connections :pool pool :database database :lease (TransactionLease) :guard (threading.Lock)))
  (setv driver.lease.submitted True)
  (try
    (<- answer (offloaded pool (fn [] (run-batched-here driver locks connections.origin raised program)) keep-nothing
                          (fn [] (abandoned-now driver))))
    answer
    (except [TaskCancelledError]
      (setv driver.lease.abandoned True)
      (raise))
    (finally
      (try
        (when (not driver.lease.returned)
          (<- (offloaded pool (fn [] (abandon-lease driver)) keep-nothing)))
        (finally
          (.ring-local connections database raised))))))


(defk offloaded-transaction [connections pool database program locks batched]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: program Program) (: locks tuple) (: batched bool)]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "接続 1 本を借りて program を 1 つの transaction で回し、必ず接続を返すため(driver の I/O は pool の thread で — 頭の註)。postgres-sql-handler と
   pooled-postgres-sql-handler が共に使う。batched = True は往復 1 回を pipeline 1 つで送る run-in-batched-transaction を driver の仕事 1 つで
   回す(offloaded-batched-transaction — 往復の間に scheduler へ戻らない)。batched = False(既定)は文 1 つを 1 回ずつ流す run-in-transaction を
   この下の手順で回す:
   借りた接続への driver の呼び(文・ROLLBACK・返却)は錠 guard で 1 本ずつ回す(driven): 待ち手が取り消されても走り出した呼びの thread は
   走り切るので、その後の ROLLBACK と返却が別の thread から同じ接続に来る(psycopg の接続の錠が同じ順を守るので、guard は流す文と順を
   変えない)。接続を借りる仕事は最初の driver の呼び(BEGIN)と、返す仕事は最後の呼び(COMMIT か ROLLBACK を流す呼び)と同じ pool の仕事に
   する(#3688 の子 (3) — 別の仕事にすると、共有の loop と driver の thread の間の乗り換えが書き 1 回に 2 つ増える)。
   文を 1 つも流さない program は接続を借りない。借りられなければ、最初の呼びの答えが SqlUnreachable になり、transaction の答えもそれになる。
   最後の呼びまで来なかった transaction(取り消し・BEGIN の失敗・例外)は、finally が返しの仕事 abandon-lease を 1 つ撃つ — guard の下なので
   走っている呼びの後に返し、まだ始まっていない呼びは流さずに終わる。
   合図(頭の註): transaction が出した合図を覚え(raised)、接続を返した後に同じ process の呼び鈴を鳴らす — COMMIT を流した(流そうとした)
   transaction だけ。鳴らすのは finally の中なので、COMMIT の答えを受ける前・返却を待つ間に取り消されても鳴らす。"
  (when batched
    (<- whole (offloaded-batched-transaction connections pool database program locks))
    (return whole))
  (val raised (RaisedNotices))
  (val driver (TransactionDriver :connections connections :pool pool :database database :lease (TransactionLease) :guard (threading.Lock)))
  (try
    (<- answer (run-in-transaction database program
                                   (fn [request] (driven driver (fn [leased] (postgres-query leased request)) False))
                                   (fn [request] (driven driver (fn [leased] (postgres-insert leased request)) False))
                                   (fn [] (driven driver (fn [leased] (postgres-begin leased locks)) False))
                                   (fn [] (raised-commit driver raised))
                                   (fn [] (driven driver (fn [leased] (postgres-control leased "ROLLBACK")) True))
                                   :execute-notify (fn [request] (raised-notice driver connections.origin raised request))))
    answer
    (finally
      (try
        (when (and driver.lease.submitted (not driver.lease.returned))
          (<- (offloaded pool (fn [] (abandon-lease driver)) keep-nothing)))
        (finally
          (.ring-local connections database raised))))))


(defk raised-commit [driver raised]
  {:pre [(: driver TransactionDriver) (: raised RaisedNotices)]
   :post [(: % (| SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つずつの transaction の COMMIT を流すため(offloaded-transaction の註): 流す前に「届く所まで流した」の印を立て(COMMIT の答えを受ける
   前に取り消されても、接続を返した後に鳴らす)、同じ driver の仕事で接続を返す。"
  (setv raised.sent True)
  (<- answer (driven driver (fn [leased] (postgres-control leased "COMMIT")) True))
  answer)


(defk raised-notice [driver origin raised request]
  {:pre [(: driver TransactionDriver) (: origin str) (: raised RaisedNotices) (: request SqlNotify)]
   :post [(: % (| SqlRows SqlFailed SqlUnreachable None))]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つずつの transaction の中の SqlNotify を同じ接続で流し(commit した時だけ届く)、接続を返した後に鳴らすために覚えるため。"
  (<- (noted-notice raised request))
  (<- answer (driven driver (fn [leased] (postgres-notice leased driver.database origin request)) False))
  answer)


(defk offloaded-statement [connections pool database work]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: work "(接続) → Program の callable")]
   :post [(: % "work の答え | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "文 1 つの effect を、接続の借りと文と返却を pool の仕事 1 つ(lease-run-return)で答えるため(待つのは撃った task だけ — agora-redesign
   #2792)。借りと文を別の仕事にすると、scheduler の thread が外から完了させる promise の往復を文ごとに 2 度払う(transaction が借りを最初の
   driver の呼びと同じ仕事にするのと同じ訳 — agora card ki-5c3e73834e73)。
     - 許可を待っている間(借りの途中)に取り消された要求は、許可が取れても文を流さず、すぐ返す(DB が止まった間に取り消された要求が、
       DB が戻った時にまとめて文を流して後から来た要求を待たせない)。取り消しで抜ける finally が claim(錠)を先に取り、借りた仕事は
       claim を取れずに文を流さず返す。
     - 走り出した文は止めず、終わった thread が返す(取り消された task は文の終わりを待たない — 仕事が先に claim を取っている)。
     - 始まっていない仕事は offloaded が外す(借りない — 返す物も無い)。"
  (val claim (threading.Lock))
  (try
    (<- answer (offloaded pool (fn [] (lease-run-return connections database claim work)) keep-nothing))
    answer
    (finally
      (.acquire claim :blocking False))))


(defk notified [connections pool database notify]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: notify SqlNotify)]
   :post [(: % (| None SqlFailed SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "transaction の外の SqlNotify に答えるため(合図を流して接続を返した後に、同じ process の呼び鈴を鳴らす — 頭の註)。文の答えを受ける前に
   取り消されても鳴らし、文が届かなかった時も鳴らす(流れたか分からない時に鳴らし損ねる側に倒さない)。"
  (val raised (RaisedNotices :sent True))
  (<- (noted-notice raised notify))
  (try
    (<- answer (offloaded-statement connections pool database (fn [leased] (postgres-notice leased database connections.origin notify))))
    (if (isinstance answer SqlRows) None answer)
    (finally
      (.ring-local connections database raised))))


(defk hung-notice [connections pool database channel topics]
  {:pre [(: connections PostgresConnections) (: pool Executor) (: database str) (: channel str) (: topics (| tuple None))]
   :post [(: % (| ExternalPromise SqlUnreachable))]
   :tags {:context "sql" :role "foundation"}}
  "SqlHangNotice に答えるため(待ち受けに、名 topics を待つ呼び鈴を掛ける — None = 全部の合図で鳴る。最初の接続を待つのは pool の thread。
   取り消されたら掛けた呼び鈴を外す)。"
  (val listener (.listener connections database channel))
  (val waiting (if (is topics None) None (frozenset topics)))
  (<- bell ExternalPromise (CreateExternalPromise))
  (<- refused (offloaded pool (fn [] (.hang listener bell waiting)) (fn [_] (.drop listener bell))))
  (if (is refused None)
      bell
      (SqlUnreachable :reason (.format "PostgreSQL の LISTEN が張れていない: {}" refused))))


(defhandler postgres-sql-handler [#^ PostgresConnections connections]
  ;; 引数に残す理由: 接続の貸し出しは組み立ての側が作って閉じる資源で(DSN と資格を持つ)、答える database の名で ClickHouse の答え手と
  ;; 同じ組に並べ分ける。
  "本物の PostgreSQL の答え手(頭の註 — scheduler を塞がない)。connections が宣言した database の名にだけ答える(他の名は外側へ回す)。"
  {:tags {:context "sql" :role "foundation"}}
  (SqlQuery [database statement params binary] :when (in database (.names connections))
    (val request (SqlQuery database statement params binary))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-query leased request))))
    (resume answer))
  (SqlInsertRows [database table columns rows] :when (in database (.names connections))
    (val request (SqlInsertRows database table columns rows))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-insert leased request))))
    (resume answer))
  (SqlEnsureTables [database tables] :when (in database (.names connections))
    (<- answer (offloaded-statement connections DRIVER-THREADS database (fn [leased] (postgres-ensure-tables leased tables))))
    (resume answer))
  (SqlTransaction [database program lock-key batched locks] :when (in database (.names connections))
    (<- claims (transaction-locks lock-key locks))
    (<- answer (offloaded-transaction connections DRIVER-THREADS database program claims batched))
    (resume answer))
  ;; 束は transaction の中の往復をまとめる物 — transaction の中では SqlTransaction の scope が答え、ここへ来るのは外で出した束だけ。
  (SqlBatch [database queries commit] :when (in database (.names connections))
    (<- refusal (stray-batch database))
    (raise refusal))
  (SqlNotify [database channel topics] :when (in database (.names connections))
    (<- answer (notified connections DRIVER-THREADS database effect))
    (resume answer))
  (SqlHangNotice [database channel topics] :when (in database (.names connections))
    (<- answer (hung-notice connections DRIVER-THREADS database channel topics))
    (resume answer))
  (SqlDropNotice [database channel bell] :when (in database (.names connections))
    (.drop (.listener connections database channel) bell)
    (resume None)))
