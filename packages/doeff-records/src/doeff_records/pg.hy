;;; PostgreSQL の handler — 公開 effect 10 個に PostgreSQL の表で答える(本番の置き場)。
;;; PutRows は PutRow と同じ置き場の錠と transaction 1 つの中で、全部の行を検めてから書く(途中の失敗は transaction ごと戻る)。
;;;
;;; 判断(期待・書きの許可・保持・索引・頁)は admission.hy の純関数ちょうど 1 つ(memory の handler と同じ関数)。
;;; 文は pg_sql.hy(純粋・中立の記法)で、この file はそれを doeff の汎用の SQL の effect(SqlQuery・SqlTransaction)に載せる
;;; Program だけを持つ(#880 U5・U6)。接続・driver・方言への書き換えは答え手(doeff_core_effects の
;;; postgres-sql-handler / pooled-postgres-sql-handler)の持ち物で、組み立ての側(composition root)がこの handler の外側に置く。
;;;
;;; 失敗の読み替え:
;;;   - 答え手の SqlUnreachable(接続できない・切れた — 読み分けは答え手の postgres-failure)を、公開 effect の答え Unreachable にする。
;;;   - 答え手の SqlFailed(engine の失敗 — 文と表の形の食い違い)は実装の誤りとして RecordsSqlFailed を上げる(旧い版で psycopg の
;;;     接続の失敗でない例外を上げたのと同じ扱い)。
;;; 書きの手順(PutRow・PutRows・AppendEvent・版の繰り上げ・期限切れの回収・刈り取り)は SqlTransaction の locks = 書きの錠の中で流す
;;; (pg_sql.hy の頭の註の「書きの錠」— 置き場全体の錠〔旧い版と同じ錠の番号〕を共有で、触る錠の単位〔行の単位・列ごと〕を排他で持つ。
;;; 版の繰り上げだけが置き場全体を排他 — writer-locks)。本番の組み立て(handler の gated)では、書きは接続を借りる前に同じ process の同じ
;;; 単位の書きと 1 本ずつに並び(writer-gates)、並んだ秒と錠を持った秒を操作・単位ごとに計器に積む(locked-transaction)。
;;; transaction の中では SqlQuery / SqlBatch と純粋な計算だけを出す(時刻は前に読んで渡す)。版の繰り上げ・回収・刈り取りの transaction は既定の形(文 1 つずつ — writing)のまま。
;;; 書きの往復(#3605 — 記録の service と DB が別の機体に在ると、DB への往復 1 回が書き 1 回の待ちに乗る): PutRow・PutRows・AppendEvent・
;;; AppendEvents だけが往復をまとめる transaction(SqlTransaction の batched — batched-writing)を選び、DB へ 2 往復で書く(AppendEvents は束の
;;; 追記の数に依らず 2 往復 — 追記ごとの読みと書きを同じ往復に並べる・card ki-48c34994ed15)。1 往復目 = 判じるための読みの束
;;; (置き場の頭・錠つきの行 | 冪等キーの引き・鍵の覚え・組の期限を過ぎた出来事 —
;;; BEGIN と書きの錠は答え手が同じ往復の先頭に流す)、2 往復目 = 書きの束(片付け・行の書きと変更の列 | 出来事の書き — 合図と COMMIT も同じ
;;; 往復)。判じる事(Conflict・ExpectAbsent・断り・期限を過ぎた物の片付け)は 1 往復目の答えで決め、片付けの文は 2 往復目の先頭に入れる
;;; (往復を増やさない)。断り・衝突の時も 2 往復目(片付けと COMMIT)を流す。
;;; 読みの手順(行・一覧・変更の列・追記の読み)は SqlQuery を 1 文ずつ流す(自動 commit — 旧い版と同じ断面)。
;;; 変化の待ち(WatchChanges・WatchEvents — #3073): 書きの錠の transaction は置き場の通知の channel へ合図(SqlNotify)を出す(commit した時
;;; だけ届く — 巻き戻した書きの合図は届かない。batched の書きでは答え手が COMMIT と同じ往復で流す)。待ちは呼び鈴(SqlHangNotice)を掛けてから読み、静かなら呼び鈴か
;;; timeout を待って読み直す(watching.wait-for-signal)。読み直しの間隔で起きない。合図は中身を運ばず、待ち手が変更の列から読み直す。
;;; 待ち受けの接続が繋ぎ直した時も呼び鈴が鳴るので、その間の書きも読み直しで拾う。
;;; 合図と呼び鈴は名で絞る(#3688 — 前は書き 1 回が置き場の待ち手を全部起こした): 名 = 表は "table:" + 表の名・列は "stream:" + 列の名
;;; (notice-topics の 1 か所で綴る — 表と列が同じ綴りでも混ざらない)。書きは触った名を合図し(行の書き = その表・追記 = その列・保持の
;;; 刈り = 期限の在る表とその列)、待ちは待つ名で呼び鈴を掛ける(WatchChanges = 頼んだ表・WatchEvents = その列)。版の繰り上げと変更の
;;; 刈り取り(PruneChanges)は全部の待ち手に関わる(版と床は全部の表の位置を変える — 名の分からない合図 None)。memory の置き場の呼び鈴と
;;; 同じ分け方(memory.hy の ring-bells)。
;;; 読み手の窓の read-modify-write の競合(同じ読み手の位置を 2 つの要求が同時に読んで進める)は旧い版と同じで、ここでは直さない
;;; (#880 の構成のレビュー A4)。
;;; 保持の期限(#3561): 読みの効果(ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd・ReadEventByKey)は回収を流さず、期限を
;;; 過ぎた行と出来事を読みの文の条件で除く(回収と同じ境 — row-expiry・event-expiry・pg_sql の頭の註)。読み 1 回が流すのは読みの文だけ。
;;; 回収(期限を過ぎた行を消して変更の列に「消えた」を積み、出来事を冪等キーの覚えへ移す — purge-expired)は SweepExpired(手入れの係)の
;;; 時だけ流す。時間で起きて回収する loop は持たない。
;;; 書き(PutRow・PutRows・AppendEvent — #3605 の D)は回収を流さず、書きの transaction の文だけを流す — 記録の service と DB が別の機体に
;;; 在ると文 1 つが往復 1 回で、書きのたびの回収の候補の読み(1 + 期限つきの列の数)が書きの待ちに乗っていた。答えは回収の後の書きと同じに
;;; 保つ: 書きが触る物だけを同じ transaction で片付けてから判じる — 行の書きは錠つきで読んだ今の行が期限を過ぎていればその行を消して
;;; 「消えた」を積む文を 2 往復目に入れ、無い行として判じる(locked-row)。追記は冪等キーの単位(出来事ごとに数える列は鍵の出来事・組で
;;; 数える列は鍵の組)の期限を過ぎた出来事を 1 往復目に読み、それを捨てて鍵の覚えへ移した後の断面で前の使いを判じ、捨てる文と覚えの文を
;;; 2 往復目に入れる(earlier-use)。触らない行と出来事は SweepExpired まで置き場に残り(読みには出ない)、その行の「消えた」も SweepExpired が積む。
;;; 索引の用意(card ki-954c58540d0f — 2026-10-18 13:18〜14:26 JST に、索引を 1 つ宣言した配備で起動が落ち続けた): 表の用意
;;; (prepare-records-store)は宣言と置き場の差を観測して埋める形で、置き場の大きさに比例する仕事を起動の道に置かない。
;;;   - 移行の transaction は、表と行の文(CREATE TABLE IF NOT EXISTS・store_epoch の行)を流し、その transaction で生まれた表の索引だけを
;;;     作る(空の表 — 一瞬)。既に在る表の索引の文は流さない(在る表への CREATE INDEX は表に ShareLock を取り、その表の書きを止め、表の
;;;     大きさに比例する時間を query 用の文の上限〔60 秒〕の中で使う — 2026-10-18 は 1 本 3 分 23 秒で 60 秒ごとに cancel された)。
;;;     終わりに置き場の索引を観測し(pg_sql.indexes-statement)、宣言した索引の欄のうち使える索引の在る欄を覚え(IndexBook)に書く。
;;;   - 足りない索引は索引の用意の task(reconcile-indexes — main.hy の pg-handlers-of が serving と別の daemon の task で立てる)が、
;;;     transaction の外の CREATE INDEX CONCURRENTLY で作る(表の書きを止めない)。文は置き場の ddl-database に流す: 接続 1 本・文の上限
;;;     なし(main.hy の DDL-DATABASE — 作る時間は表の大きさで決まり、上限で切ると作りかけを残して同じ所からやり直すだけ)。同じ置き場の
;;;     2 つの process(本体と twin)が同時に作らないよう、索引の用意の錠(session の advisory lock — pg_sql.index-lock-key)を待たずに取り
;;;     (持たれていれば、持つ process が放した合図〔置き場の通知の channel・名 INDEX-TOPIC〕で観測し直す — 錠を PostgreSQL の錠の待ちで
;;;     待つと、待つ側の snapshot と作り手の CONCURRENTLY が deadlock になり作り手が捨てられる・pg_sql.index-lock-statement)、錠の中で
;;;     観測し直して残った差だけを作る。使えない形で残った同じ名の索引(CONCURRENTLY の作りかけ)は
;;;     消してから作る。落ちた回は名乗って INDEX-RETRY-SECONDS 秒の後に観測し直す(service を終えない)。
;;;   - 答えは用意の終わりを待たない: ListRows が名指す宣言した索引の欄に、使える索引がまだ無ければ NotIndexed で断る(使い手は
;;;     Unavailable として撃ち直す — 宣言の注記の形)。他の読みと書きは答える(索引は読みの速さで、答えの値を変えない)。
;;;   - 宣言した索引の欄の索引は表の欄の式の索引(pg_sql の頭の註 — card ki-2176012d1804)。旧い形の索引か前の部分索引(aliases)が在る欄は、
;;;     表の欄の式の索引を作り終えるまで前の文の形で照らして答え(NotIndexed にしない)、作り終えて表の標本を取り直した欄から表の欄の式で
;;;     照らす(IndexBook の scoped — ListRows と回収の終端の行の読みが文の形を決める)。索引の用意は aliases が在っても表の欄の式の索引を
;;;     作り、作り終えた回の終わりに表の標本を取り直して観測し直す。標本を取り直す前の表の欄の式の索引は、索引の式の統計が無く値の組の
;;;     見積もりが既定の選択度の積み上げになって表を頭から読む計画が選ばれる(本番の分布の置き場で値 995 個の組の 1 頁が 745 ms)ので、
;;;     観測は統計の在る索引(か、統計がまだ 1 つも無い表の索引)だけを表の欄の式で照らす欄に数える(pg_sql.indexes-statement)。
;;;   - 旧い形の索引と前の部分索引(pg_sql.retired-index? の名の形 — 宣言に無い)は、宣言の索引が全部使える形で在り、表の欄の式の索引の
;;;     欄が全部表の欄の式で照らせる(retirement-ready?)ようになった回に、索引の用意が錠の中で 1 本ずつ DROP INDEX CONCURRENTLY で外す
;;;     (pg_sql の頭の註の「旧い形の索引を外す」)。その前は前の文の形の読みが引くので外さず、INDEX-RETRY-SECONDS 秒の後に観測し直す。
;;;     宣言の索引が全部在る置き場でも、旧い形の索引が在れば記録の service は索引の用意の task を立てる(IndexBook の retired)。
;;;     旧い形でない宣言に無い索引(手で足した物)は外さない。
(require doeff-hy.macros [defeffect defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(import json)
(import sys)
(import dataclasses [dataclass])
(import doeff [Program])
(import doeff_hy.frozen [FrozenMap frozen-json-object])
(import doeff_time [Delay GetMonotonic GetTime WaitWithin])
(import doeff_core_effects.scheduler [ExternalPromise TaskCancelledError CreateSemaphore AcquireSemaphore ReleaseSemaphore])
(import doeff_core_effects.meter_effects [ObserveSecondsBatch])
(import doeff_core_effects.sql_effects [SqlQuery SqlBatch SqlTransaction SqlNotify SqlHangNotice SqlDropNotice SqlRows SqlFailed
                                        SqlUnreachable SqlLock])
(import doeff_records.request_timing [lock-observations])
(import doeff_records.values [RecordsSchema TableDecl StreamDecl KeepFor ByKeySuffix Row Missing Page Written WrittenRows RowChanged
                              RowRemoved Changes Appended AppendedEvents Conflict NotIndexed RetiredKey EventsMoved EventsQuiet StreamEnd StreamEmpty
                              EventAbsent EventRetired
                              StreamTail StreamTailEmpty
                              Event Events Reset WatchCursor ListCursor Refused Unreachable RowsConflict RowsRefused])
(import doeff_records.effects [ReadRow ListRows PutRow PutRows WatchChanges WatchEvents AppendEvent AppendEvents ReadEvents ReadStreamEnd
                               ReadEventByKey])
(import doeff_records.faults [AdvanceStoreEpoch])
(import doeff_records.event_source [RECORDS-SIGNAL-SOURCE ReadSignalSource])
(import doeff_records.maintenance [SweepExpired PruneChanges ReadChangeLog ChangeLog Swept Pruned])
(import doeff_records.admission [AppendReplay judge-expect judge-put judge-put-rows judge-append row-expired? retention-cutoff-ms
                                 where-refusal key-head-refusal listed-row key-text key-from-text canonical-json next-watch-sequence epoch-ms body-digest])
(import doeff_records.watching [wait-for-signal moved-of])
(import doeff_records.pg_sql [Statement RowExpiry EventExpiry TailRead DEFAULT-PREFIX checked-prefix writer-lock-key migrate-lock-key
                              unit-lock-key stream-unit ROWS-UNIT writer-lock-statement schema-steps drop-statements
                              IndexedField DeclaredIndex declared-indexes relations-statement indexes-statement
                              concurrent-index-statement drop-index-statement analyze-statement retired-index?
                              index-lock-statement index-unlock-statement
                              store-head-statement watch-head-statement read-row-statement lock-row-statement list-rows-statement top-rows-statement
                              terminal-rows-statement upsert-row-statement delete-row-statement append-change-statement
                              changes-statement change-log-statement advance-epoch-statement forget-changes-statement prune-changes-statement UNBOUNDED-SEQ find-event-statement
                              insert-event-statement HeadRead read-events-statement stream-end-statement event-by-key-statement
                              expire-chosen-events-statement touched-events-statement delete-events-statement expiring-candidates-statement
                              retire-keys-statement find-retired-key-statement])

(val MODULE-TAGS {:context "records" :role "foundation"})


(defclass RecordsSqlFailed [RuntimeError]
  "engine が答えた失敗(SqlFailed)— 文か表の形の食い違いで、実装の誤り。sqlstate = SQLSTATE(5 文字か None)。"
  (defn #^ None __init__ [self #^ (| str None) sqlstate #^ str reason]  ; defk にできない: 例外の class の初期化
    (.__init__ (super) (.format "PostgreSQL が断った({}): {}" sqlstate reason))
    (setv self.sqlstate sqlstate)))


;; 表の用意の transaction を deadlock(40P01)の後に流し直す回数の上限(prepare-records-store)。
(val PREPARE-ATTEMPTS 3)
(val DEADLOCK-SQLSTATE "40P01")
;; 索引の用意(頭の註)が、差を埋めきれなかった回(置き場に届かない・engine が断った)の後に置き場を観測し直すまでの秒。
;; ポーリングの訳: 失敗の終わり(DB の戻り・断りの訳の解け)を知らせる出来事が無いので、間を置いて撃ち直す。他の process が作っている間は
;; その process が錠を放した合図(NOTIFY)で起き、この秒は合図を出さずに落ちた process の錠が外れた後の観測し直しの上限にだけ使う。
;; 旧い形の索引を外す前に表の欄の式の索引が見積もれるようになる(autovacuum の analyze が表の標本を取る)のを待つ間も、標本が取れた事を
;; 知らせる出来事が無いので、同じ間で観測し直す。差も外す旧い形の索引も無くなれば task は終わる。
(val INDEX-RETRY-SECONDS 30.0)
;; 索引の用意の錠を放した合図の名(置き場の通知の channel — 表と列の待ち手の名 "table:" / "stream:" とは重ならない)。
(val INDEX-TOPIC "index-build")
;; 回収(purge-expired)が書きの錠の transaction 1 つで捨てる出来事の数の上限(錠の外で読む候補の数 — 錠を持つ間に触る行をこの数で縛る)。
;; これより多く期限を過ぎた列は、錠を取り直して続きを捨てる(錠と錠の間に他の書きが入れる)。
(val EXPIRE-BATCH 500)


(defclass StoreUnreachable [Exception]
  "文 1 つが置き場に届かなかった(SqlUnreachable)。handler の節の入口(reached)が捕まえて Unreachable の答えにする —
   Program の奥の手順から 1 つずつ答えを返して回らないための印で、handler の外へは出ない(用意と版の繰り上げを除く)。")


(defclass IndexBook []
  "宣言した索引の欄のうち、置き場に使える索引が在ると観測した欄の覚え(頭の註の「索引の用意」— 表の用意と索引の用意の task が書き、
   ListRows と回収が読む)。fields = 使える索引(表の欄の式の索引か、その aliases の旧い形の索引)の在る欄(IndexedField の集合)/
   scoped = 表の欄の式の索引が使える形で見積もれる (表, 欄)(IndexedField の集合 — 読みの文がその欄を表の欄の式で照らす・pg_sql の頭の註)/
   missing = 最後の観測で置き場に無かった宣言の索引の名の並び(空 = 宣言と置き場の差が無い)/ retired = 最後の観測で置き場に在った
   旧い形の索引の名の並び(名の順 — 索引の用意が外す・頭の註。空 = 無い)。資源なので値の型ではない(中身を書き換え、同一性で扱う)。
   書きも読みも scheduler の thread の上で、driver の thread は触らない。"

  (defn #^ None __init__ [self]  ; defk にできない: 資源の class の初期化
    "観測の前の覚え(使える索引の在る欄は無い)を作るため。"
    (setv self.fields (frozenset)
          self.scoped (frozenset)
          self.missing #()
          self.retired #()))

  (defn #^ bool covers [self #^ str table #^ str field]  ; defk にできない: ListRows の判断の中で同期に読む覚え(Program を返すと真偽にならない)
    "表 table の欄 field に、使える索引が在ると観測したかを読むため。"
    (in (IndexedField :table table :field field) self.fields))

  (defn #^ frozenset scoped-fields [self #^ str table]  ; defk にできない: 読みの文を組む前に同期に読む覚え(Program を返すと集合にならない)
    "表 table の欄のうち、表の欄の式の索引が在ると観測した欄の名の集合を読むため(読みの文の条件の形を決める — pg_sql.row-filter)。"
    (frozenset (gfor indexed self.scoped :if (= indexed.table table) indexed.field)))

  (defn #^ None observed [self #^ frozenset fields #^ frozenset scoped #^ tuple missing #^ tuple retired]  ; defk にできない: 観測した拍に同期に書き替える資源の覚え
    "置き場の索引を観測した断面で、覚えを書き替えるため(消えた索引の欄は外れる)。"
    (setv self.fields fields
          self.scoped scoped
          self.missing missing
          self.retired retired)
    None)

  (defn #^ None dropped [self #^ str name]  ; defk にできない: 旧い形の索引を外し終えた拍に同期に書き替える資源の覚え
    "索引の用意が外し終えた旧い形の索引 name を、外す物の並びから除くため(次の観測を待たずに、回が外し残しを数える)。"
    (setv self.retired (tuple (gfor retired self.retired :if (!= retired name) retired)))
    None)

  (defn #^ None added [self #^ str name #^ tuple fields]  ; defk にできない: 索引を作り終えた拍に同期に足す資源の覚え
    "索引の用意が作り終えた索引(名 name・答える欄 fields)を覚えに足すため(次の観測を待たずに ListRows が使う。表の欄の式で照らす欄
     scoped は、表の標本を取り直した後の観測が書く)。"
    (setv self.fields (| self.fields (frozenset fields))
          self.missing (tuple (gfor missing self.missing :if (!= missing name) missing)))
    None))


(defrecord PreparedStore
  "表を用意し終えた置き場: database = SqlQuery に書く database の名 / ddl-database = 索引の用意が CREATE INDEX CONCURRENTLY と索引の
   用意の錠を流す database の名(頭の註 — 文の上限の無い接続 1 本の貸し出し。錠は session の advisory lock なので、取る文と外す文が同じ
   接続に届く貸し出しでなければならない)/ schema = 宣言 / prefix = 表の名の接頭辞(検め済み)/ indexes = 使える索引の在る宣言した索引の
   欄の覚え(IndexBook)。作るのは prepare-records-store だけで、pg-records-handler はこれを受け取る — 表を用意せずに handler は組めず、
   handler は要求ごとに表を用意し直さない。"
  (#^ str database)
  (#^ str ddl-database)
  (#^ RecordsSchema schema)
  (#^ str prefix)
  (#^ IndexBook indexes))


(defrecord ObservedIndex
  "置き場の表に在る索引 1 つの観測(pg_index): name = 索引の名 / valid = 使える形か(CONCURRENTLY の作りかけは False で残る)/
   estimable = 見積もれるか(索引の式の統計が在るか、表に統計がまだ無い — pg_sql.indexes-statement)。"
  (#^ str name)
  (#^ bool valid)
  (#^ bool estimable))


(defrecord IndexGap
  "宣言の索引と置き場の差 1 つ(索引の用意が埋める): index = 作る索引 / invalid = 同じ名の索引が使えない形で残っている(消してから作る)。"
  (#^ DeclaredIndex index)
  (#^ bool invalid))


(defrecord StoreHead
  "置き場の頭: epoch = 置き場の版 / floor = 変更の列の最も古い番号の手前(これより前の位置は Reset)/ head = 最新の変更の番号。"
  (#^ int epoch)
  (#^ int floor)
  (#^ int head))


(defrecord ExpiredRow
  "保持の期限を過ぎた行 1 つ: table = 表の名 / record = state_rows の行(key payload version updated_ms)。"
  (#^ str table)
  (#^ tuple record))


(defrecord LockedRow
  "書きの判定に渡す今の行(1 往復目の錠つきの読みから — locked-row): current = 今の行(無い行・期限を過ぎて片付ける行は None)/
   cleanup = 期限を過ぎた行を消して「消えた」を積む文(2 往復目の先頭に流す — 片付けが要らなければ空)。"
  (#^ (| Row None) current)
  (#^ (get tuple #(Statement ...)) cleanup))


(defrecord StagedRow
  "判定を通った 1 行の書き(staged-row): statements = 行の書きと変更の列に 1 つ積む文(2 往復目に流す)/ written = その書きの答え。"
  (#^ (get tuple #(Statement ...)) statements)
  (#^ Written written))


(defrecord EarlierUse
  "追記の冪等キーの前の使い(1 往復目の読みから — earlier-use): previous = 生きた出来事 | 保持の期限で消した鍵の覚え | None /
   cleanup = 追記が触る単位の期限を過ぎた出来事を捨てて鍵の覚えへ移す文(2 往復目の先頭に流す — 片付けが要らなければ空)/
   removed = 追記が触る単位の期限を過ぎた出来事の番号(束の後の追記が同じ出来事を重ねて片付けないため — append-events-locked)。"
  (#^ (| Event RetiredKey None) previous)
  (#^ (get tuple #(Statement ...)) cleanup)
  (#^ (get frozenset int) removed))


(defrecord AppendReads
  "束の追記 1 つの、1 往復目の読みの束の中の在りか(append-events-locked): decl = 列の宣言・expiry = 期限の境(期限の無い列は None)・
   at = 鍵の生きた出来事の引きの位置(次が鍵の覚えの引き・その後に組の期限を過ぎた出来事の読みが grouped 個 — touched-reads)。"
  (#^ StreamDecl decl)
  (#^ (| EventExpiry None) expiry)
  (#^ int at)
  (#^ int grouped))


(defrecord InsertAt
  "束の追記のうち今積む物の、2 往復目の文の束の中の書きの文の位置(答えの番号はその文の答えの行から読む — append-events-locked)。"
  (#^ int index))


;; --- 文を流す ----------------------------------------------------------------------------------------------

(defk query-rows [database statement]
  {:pre [(: database str) (: statement Statement)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "文 1 つを流して行を返すため(届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる)。
   transaction の中では失敗の答えは program へ戻らない(答え手が rollback して SqlTransaction の答えにする)。"
  (<- answer (SqlQuery database statement.text statement.params))
  (match answer
    (SqlRows :rows rows) rows
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))))


(defk in-transaction [database lock-key program [batched False] [locks #()]]
  {:pre [(: database str) (: lock-key (| str None)) (: program Program) (: batched bool) (: locks tuple)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を錠 lock-key と錠の並び locks(SqlLock — 書きの錠は writer-locks)の transaction 1 つで流すため(届かなければ StoreUnreachable・
   engine の失敗は RecordsSqlFailed を上げる)。batched = 往復をまとめる transaction を選ぶか(既定 False — 文 1 つずつ・頭の註)。"
  (<- answer (SqlTransaction database program :lock-key lock-key :batched batched :locks locks))
  (match answer
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ answer))


(defk batch-rows [database statements commit]
  {:pre [(: database str) (: statements tuple) (: commit bool)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "書きの transaction の中で、文の並びを DB への 1 往復でまとめて流し(commit なら同じ往復の最後に COMMIT も)、文ごとの行の tuple を
   返すため(頭の註)。往復が落ちれば答え手が transaction を戻して SqlTransaction の答えにする(program は再開されない)。"
  (<- answers (SqlBatch database (tuple (gfor statement statements (SqlQuery database statement.text statement.params))) :commit commit))
  (tuple (gfor answer answers answer.rows)))


(defk committed [store statements answer]
  {:pre [(: store PreparedStore) (: statements tuple) (: answer (| Written WrittenRows Appended Conflict Refused RowsConflict RowsRefused))]
   :post [(: % (| Written WrittenRows Appended Conflict Refused RowsConflict RowsRefused))]
   :tags {:context "records" :role "foundation"}}
  "書きの 2 往復目: statements(片付けと書き)を合図と COMMIT と同じ往復で流し、書きの答え answer を返すため(頭の註 — 断り・衝突の時も
   片付けと COMMIT を流す)。"
  (<- (batch-rows store.database statements True))
  answer)


(defk notice-channel [store]
  {:pre [(: store PreparedStore)] :post [(: % str)]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel の名(表の名の接頭辞ごとに 1 つ — 同じ database の別の置き場の書きで起きない)。"
  (.format "{}changes" store.prefix))


(defk notice-topics [tables streams]
  {:pre [(: tables tuple) (: streams tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表 tables と列 streams を、合図と呼び鈴の名の並び(表 = \"table:\" + 表の名・列 = \"stream:\" + 列の名 — 名の順・重ねない)にするため
   (頭の註 — 書きの合図の名と待ちの呼び鈴の名をこの 1 か所で綴る)。"
  (tuple (sorted (| (sfor table tables (+ "table:" table)) (sfor stream streams (+ "stream:" stream))))))


(defk notice-raised [notified]
  {:pre [(: notified (| None SqlFailed SqlUnreachable))] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "SqlNotify の答えの失敗を、置き場の例外(StoreUnreachable・RecordsSqlFailed)にするため(signalled と signalled-ahead が共に使う)。"
  (match notified
    (SqlUnreachable :reason reason) (raise (StoreUnreachable reason))
    (SqlFailed :sqlstate sqlstate :reason reason) (raise (RecordsSqlFailed sqlstate reason))
    _ None))


(defk signalled [store topics program]
  {:pre [(: store PreparedStore) (: topics (| tuple None)) (: program Program)] :post [(: % "program の答え")]
   :tags {:context "records" :role "foundation"}}
  "program を流した後、同じ transaction の中で置き場の通知の channel へ、名 topics(None = 全部に関わる)の合図を出すため(commit した時だけ
   届く — 頭の註)。"
  (<- answer program)
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel topics))
  (<- (notice-raised notified))
  answer)


(defk signalled-ahead [store topics program]
  {:pre [(: store PreparedStore) (: topics (| tuple None)) (: program Program)] :post [(: % "program の答え")]
   :tags {:context "records" :role "foundation"}}
  "batched の transaction の中で、置き場の通知の channel へ名 topics の合図を出してから program を流すため(答え手は合図を覚えて COMMIT と
   同じ往復で流し、commit した時だけ届く — 頭の註。program が commit の束で終わるので、合図は program の前に出す)。"
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel topics))
  (<- (notice-raised notified))
  (<- answer program)
  answer)


(defk writer-locks [store units]
  {:pre [(: store PreparedStore) (: units tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "書き 1 回が取る錠の並び(SqlTransaction の locks — pg_sql.hy の頭の註の「書きの錠」)を作るため: units = 排他で持つ部分の錠の単位の名
   (空 = 置き場全体を排他)。置き場全体の錠を先に(部分の書きは共有で)、部分の錠を単位の名の順に並べる。"
  (<- whole (writer-lock-key store.prefix))
  (when (not units)
    (return #((SqlLock :key whole))))
  (var locks #((SqlLock :key whole :shared True)))
  (for [unit (sorted (set units))]
    (<- key (unit-lock-key store.prefix unit))
    (:= locks (+ locks #((SqlLock :key key)))))
  locks)


(defk locked-transaction [store gated operation units program batched]
  {:pre [(: store PreparedStore) (: gated bool) (: operation str) (: units tuple) (: program Program) (: batched bool)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を書きの錠(writer-locks)の transaction で流すため。gated(本番の組み立て — pg-records-handler の註)なら、接続を借りる前に同じ
   process の同じ単位の書きと 1 本ずつに並び(AdmitWriters — writer-gates)、並んで待った秒と錠を持った秒を操作 operation・単位ごとに
   計器に積む(錠を持った秒 = 並びを通ってから transaction の答えまで — 接続の許可の待ち・DB の錠の待ち・文の往復・COMMIT を含む)。
   置き場全体の書き(units が空)は並ばない(版の繰り上げ — 検の口だけ)。"
  (<- locks (writer-locks store units))
  (when (or (not gated) (not units))
    (<- direct (in-transaction store.database None program :batched batched :locks locks))
    (return direct))
  (val gates (tuple (sorted (set units))))
  (<- asked (GetMonotonic))
  (<- (AdmitWriters gates))
  ;; 通った後は、取り消し・例外でも必ず並びを返す(返さないと同じ単位の書きが止まり続ける)。
  (try
    (<- admitted (GetMonotonic))
    (<- answer (in-transaction store.database None program :batched batched :locks locks))
    (<- done (GetMonotonic))
    (finally
      (<- (ReleaseWriters gates))))
  (<- observations (lock-observations operation gates (- admitted asked) (- done admitted)))
  (<- (ObserveSecondsBatch observations))
  answer)


(defk writing [store gated operation units topics program]
  {:pre [(: store PreparedStore) (: gated bool) (: operation str) (: units tuple) (: topics (| tuple None)) (: program Program)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を錠の単位 units(空 = 置き場全体)の書きの錠の transaction で流し、同じ transaction で名 topics(None = 全部に関わる)の書きの
   合図を出すため(pg_sql.hy の頭の註の「書きの錠」— locked-transaction)。文 1 つずつの transaction(回収・刈り取り・版の繰り上げ)。"
  (<- answer (locked-transaction store gated operation units (signalled store topics program) False))
  answer)


(defk batched-writing [store gated operation units topics program]
  {:pre [(: store PreparedStore) (: gated bool) (: operation str) (: units tuple) (: topics tuple) (: program Program)]
   :post [(: % "program の答え") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "書きの効果(PutRow・PutRows・AppendEvent・AppendEvents)の program を、往復をまとめる(batched)書きの錠の transaction(錠の単位 units)で流し、
   同じ transaction で書きが触る名 topics の合図を出すため(頭の註の「書きの往復」— 錠は writing と同じ locked-transaction)。"
  (<- answer (locked-transaction store gated operation units (signalled-ahead store topics program) True))
  answer)


;; --- 同じ process の書きの並び(card ki-0306f5a614b4)-----------------------------------------------------------------------------

(defeffect AdmitWriters
  "錠の単位 units(名の順)の書きを、同じ process の同じ単位の書きと 1 本ずつに並べるため、単位ごとの並びを名の順に通る(writer-gates が
   答える — 通るまで待つ)。答え = None。通った書きは必ず ReleaseWriters で同じ単位を返す。"
  {:fields [(: units (get tuple #(str ...)))]
   :pre [(: units tuple) (= units (tuple (sorted (set units))))]
   :answer None
   :tags {:context "records" :role "foundation"}})


(defeffect ReleaseWriters
  "AdmitWriters で通った単位 units の並びを返すため。答え = None。"
  {:fields [(: units (get tuple #(str ...)))]
   :pre [(: units tuple)]
   :answer None
   :tags {:context "records" :role "foundation"}})


(defhandler writer-gates
  "同じ process の書きを、錠の単位ごとに 1 本ずつに並べる答え手(本番の組み立て — main.hy の records-connected が pg-records-handler と
   SQL の答え手より外側に 1 つ置く)。単位の錠を DB で待つ書きが接続を持ったまま並び、読みの接続を使い切るのを防ぐ(DB の錠の前に、
   接続を借りずに並ぶ — 同じ単位を DB で待つのは別の process の書きだけ)。違う単位の書きは互いを待たない。"
  {:tags {:context "records" :role "foundation"}}
  ;; gates = 錠の単位の名 → その単位の並び(scheduler の semaphore 1 つ — 初めて使う拍に作る)(session の値)。単位は宣言の表と列から
  ;; 決まる有限の名なので、並びは消さない。
  (session var gates {})
  (AdmitWriters [units]
    ;; 単位を名の順に 1 つずつ通る。途中で取り消されたら(後ろの単位を待つ間など)、通った単位を返してから取り消しを通す。
    (var taken #())
    (try
      (for [unit units]
        (when (not-in unit gates)
          (<- created (CreateSemaphore 1))
          ;; 作る間に他の書きが同じ単位の並びを作っていれば、先に置かれた並びを使う(右が勝つ合わせ)。
          (:= gates (| {unit created} gates)))
        (<- (AcquireSemaphore (get gates unit)))
        (:= taken (+ taken #(unit))))
      (except [BaseException]
        (for [unit (reversed taken)]
          (<- (ReleaseSemaphore (get gates unit))))
        (raise)))
    (resume None))
  (ReleaseWriters [units]
    (for [unit (reversed units)]
      (<- (ReleaseSemaphore (get gates unit))))
    (resume None)))


(defk hung-bell [store topics]
  {:pre [(: store PreparedStore) (: topics tuple)] :post [(: % (| ExternalPromise Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "置き場の通知の channel に、名 topics を待つ呼び鈴を掛けるため(待ち受けに届かなければ Unreachable)。"
  (<- channel (notice-channel store))
  (<- bell (SqlHangNotice store.database channel topics))
  (match bell
    (SqlUnreachable :reason reason) (Unreachable (.format "PostgreSQL に届かない: {}" reason))
    _ bell))


(defk dropped-bell [store bell]
  {:pre [(: store PreparedStore) (: bell ExternalPromise)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "鳴らなかった呼び鈴を外すため。"
  (<- channel (notice-channel store))
  (<- (SqlDropNotice store.database channel bell))
  None)


(defk reached [program]
  {:pre [(: program Program)] :post [(: % "program の答え | Unreachable") (not (isinstance % #(SqlFailed SqlUnreachable)))]
   :tags {:context "records" :role "foundation"}}
  "program を流し、置き場に届かなかった事を Unreachable の答えにするため(公開 effect の答えの境目 — handler の節が呼ぶ)。"
  (try
    (<- answer program)
    answer
    (except [error StoreUnreachable]
      (Unreachable (.format "PostgreSQL に届かない: {}" error)))))


(defk run-statements [database statements]
  {:pre [(: database str) (: statements tuple)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "文の列を順に流すため(用意と後片付け)。"
  (for [statement statements]
    (<- (query-rows database statement)))
  None)


;; --- 用意と後片付け -----------------------------------------------------------------------------------------

(defk prepare-records-store [database ddl-database schema prefix]
  {:pre [(: database str) (: ddl-database str) (: schema RecordsSchema) (: prefix str)] :post [(: % PreparedStore)]
   :tags {:context "records" :role "foundation"}}
  "表を用意する(移行)— process ごとに 1 度、composition root が要求を受ける前に流す。文は移行の錠の transaction の中で流す:
   同じ置き場を同時に用意する別の process(replicas > 1・入れ替えの重なり)は錠を待ち、先の commit の後で IF NOT EXISTS が
   何もしない(錠が無いと同時の CREATE INDEX IF NOT EXISTS が競って遅れた方が UniqueViolation)。同じ transaction の頭で置き場の
   書きの錠も取り、同じ置き場の書き(入れ替えの間も答える別の process)と表の lock を逆の順で取らない(pg_sql.writer-lock-statement)。
   索引の文はその transaction で生まれた表の物だけを流し、終わりに置き場の索引を観測して覚え(IndexBook)に書く — 既に在る表に足りない
   索引は作らず、索引の用意の task(reconcile-indexes)に残す(頭の註の「索引の用意」)。ddl-database = 索引の用意の文を流す database の名
   (PreparedStore の註)。錠の外の相手との deadlock(40P01)は transaction ごと戻るので、PREPARE-ATTEMPTS 回まで同じ transaction を
   流し直す(文はどれも冪等)。届かなければ StoreUnreachable・engine の失敗は RecordsSqlFailed を上げる(起動を止める)。"
  (<- checked (checked-prefix prefix))
  (<- steps (schema-steps checked schema))
  (<- writer-lock (writer-lock-statement checked))
  (<- key (migrate-lock-key checked))
  (<- found (prepared-tables database key writer-lock steps checked PREPARE-ATTEMPTS))
  (val indexes (IndexBook))
  (<- (index-observed indexes checked schema found))
  (PreparedStore :database database :ddl-database ddl-database :schema schema :prefix checked :indexes indexes))


(defk prepared-tables [database key writer-lock steps prefix attempts]
  {:pre [(: database str) (: key str) (: writer-lock Statement) (: steps tuple) (: prefix str) (: attempts int) (>= attempts 1)]
   :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表の用意(prepare-steps)を錠 key の transaction 1 つで流し、deadlock(40P01)で戻された時は残りの attempts 回まで流し直すため。
   答え = transaction の終わりの置き場の索引の観測の行。"
  (try
    (<- found (in-transaction database key (prepare-steps database prefix writer-lock steps)))
    found
    (except [error RecordsSqlFailed]
      (when (or (!= error.sqlstate DEADLOCK-SQLSTATE) (<= attempts 1))
        (raise))
      (<- again (prepared-tables database key writer-lock steps prefix (- attempts 1)))
      again)))


(defk prepare-steps [database prefix writer-lock steps]
  {:pre [(: database str) (: prefix str) (: writer-lock Statement) (: steps tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "移行の transaction の中身(頭の註の「索引の用意」): 置き場の書きの錠を取り、在る表を読み、表と行の文を流し、この transaction で
   生まれた表の索引の文だけを流して、終わりの置き場の索引を観測するため。答え = 索引の観測の行(名・使える形か)。"
  (<- (query-rows database writer-lock))
  (<- relations (relations-statement prefix))
  (<- present (query-rows database relations))
  (val existing (frozenset (gfor record present (get record 0))))
  (for [step steps]
    (when (or (is step.index None) (not-in (+ prefix step.index.relation) existing))
      (<- (query-rows database step.statement))))
  (<- observing (indexes-statement prefix))
  (<- found (query-rows database observing))
  found)


(defk observed-indexes [found]
  {:pre [(: found tuple)] :post [(: % (get tuple #(ObservedIndex ...)))]
   :tags {:context "records" :role "foundation"}}
  "索引の観測の行(名・使える形か・見積もれるか — pg_sql.indexes-statement の答え)を ObservedIndex の並びにするため。"
  (tuple (gfor #(name valid estimable) found (ObservedIndex :name name :valid (bool valid) :estimable (bool estimable)))))


(defk usable-index? [index usable]
  {:pre [(: index DeclaredIndex) (: usable frozenset)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "宣言の索引 index の読み(serves)に、置き場の使える索引の名 usable の中で答える物(同じ名か、作り終えるまで答える旧い形の名 —
   aliases)が在るかを判じるため。"
  (or (in index.name usable) (any (gfor alias index.aliases (in alias usable)))))


(defk index-gaps [declared observed]
  {:pre [(: declared tuple) (: observed tuple)] :post [(: % (get tuple #(IndexGap ...)))]
   :tags {:context "records" :role "foundation"}}
  "宣言の索引 declared のうち、置き場の観測 observed に同じ名の使える索引の無い物を差の並びにするため(宣言の順 — 同じ名の使えない
   索引が残っていれば invalid)。aliases の旧い形の索引が在っても差に数える(表の欄の式の索引でなければ等号 2 つの読みが索引を組めない —
   pg_sql の頭の註。旧い形の索引は作り終えるまでの読みに答える — usable-index?)。"
  (val usable (frozenset (gfor seen observed :if seen.valid seen.name)))
  (val unusable (frozenset (gfor seen observed :if (not seen.valid) seen.name)))
  (tuple (gfor index declared
               :if (not-in index.name usable)
               (IndexGap :index index :invalid (in index.name unusable)))))


(defk index-observed [book prefix schema found]
  {:pre [(: book IndexBook) (: prefix str) (: schema RecordsSchema) (: found tuple)] :post [(: % (get tuple #(IndexGap ...)))]
   :tags {:context "records" :role "foundation"}}
  "置き場の索引の観測の行 found で覚え book を書き替え、宣言の索引との差を返すため(表の用意の終わりと、索引の用意の回の頭と終わり)。
   表の欄の式で照らす欄(scoped)は、表の欄の式の索引が使える形で、見積もれる(索引の式の統計が在る — 表の標本を取り直す前は前の文の形で
   照らす・pg_sql.indexes-statement)欄だけ。外す旧い形の索引(retired)は、名が旧い形で宣言に無い観測の索引(使えない形を含む — 外しかけ)。"
  (<- declared (declared-indexes prefix schema))
  (<- observed (observed-indexes found))
  (<- gaps (index-gaps declared observed))
  (val declared-names (frozenset (gfor index declared index.name)))
  (var retired #())
  (for [seen observed]
    (<- former (retired-index? prefix seen.name))
    (when (and former (not-in seen.name declared-names))
      (:= retired (+ retired #(seen.name)))))
  (val usable (frozenset (gfor seen observed :if seen.valid seen.name)))
  (val estimable (frozenset (gfor seen observed :if (and seen.valid seen.estimable) seen.name)))
  (var served #())
  (for [index declared]
    (<- answers (usable-index? index usable))
    (when answers
      (:= served (+ served index.serves))))
  (val scoped (frozenset (gfor index declared
                               :if (in index.name (if index.sampled estimable usable))
                               indexed index.scoped
                               indexed)))
  (.observed book (frozenset served) scoped (tuple (gfor gap gaps gap.index.name)) (tuple (sorted retired)))
  gaps)


(defk retirement-ready? [book prefix schema]
  {:pre [(: book IndexBook) (: prefix str) (: schema RecordsSchema)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "旧い形の索引を外してよい断面かを判じるため(頭の註): 最後の観測で宣言の索引が全部使える形で在り(missing が空)、表の欄の式の索引の
   欄が全部表の欄の式で照らせる(scoped — 見積もれる)。崩れている間は、前の文の形の読み(ledger = :ledger と欄の式)が旧い形の索引を引く。"
  (<- declared (declared-indexes prefix schema))
  (and (not book.missing)
       (all (gfor index declared (<= (frozenset index.scoped) book.scoped)))))


;; --- 索引の用意 --------------------------------------------------------------------------------------------

(defk reconcile-indexes [store]
  {:pre [(: store PreparedStore)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の task(頭の註の「索引の用意」): 宣言の索引と置き場の索引の差と、外す旧い形の索引が無くなるまで、観測して埋める回
   (index-round)を繰り返すため。差を埋めきれなかった回(置き場に届かない・engine が断った・旧い形の索引をまだ外せない)の後は
   INDEX-RETRY-SECONDS 秒待って観測し直す。置き場の失敗も思わぬ
   例外も上げない(service を終えない — 名乗って次の回へ)。取り消しだけは通す。"
  (while True
    (<- settled (guarded-round store))
    (when settled
      (return None))
    (<- (Delay INDEX-RETRY-SECONDS))))


(defk guarded-round [store]
  {:pre [(: store PreparedStore)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の 1 回を流し、落ちた回を名乗って「埋めきれなかった」にするため(取り消しは通す)。答え = 差と外す旧い形の索引が無くなったか。"
  (try
    (<- settled (index-round store))
    settled
    (except [TaskCancelledError]
      (raise))
    (except [error Exception]
      (print (.format "記録の service: 索引の用意の回が落ちた({} 秒の後に観測し直す): {}: {}"
                      INDEX-RETRY-SECONDS (. (type error) __name__) error)
             :file sys.stderr :flush True)
      False)))


(defk index-round [store]
  {:pre [(: store PreparedStore)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の 1 回: 置き場の索引を観測して覚えに書き、差か外す旧い形の索引が在れば索引の用意の錠を待たずに取りに行き(index-turn)、
   取れたら錠の中で観測し直して残った差を CONCURRENTLY で埋め、旧い形の索引を外すため。他の process が錠を持つ間は、その process が錠を
   放した合図か INDEX-RETRY-SECONDS の早い方まで待って観測し直す(待つ間に他の process が作った索引は作らない)。錠を PostgreSQL の錠の
   待ちで待たない訳は pg_sql.index-lock-statement の註(待つ側が snapshot を持ったまま待ち、作り手の CONCURRENTLY と deadlock になる)。
   答え = 差と外す旧い形の索引が無くなったか(作れない索引か、まだ外せない旧い形の索引が在れば False — 後で観測し直す)。"
  (<- observing (indexes-statement store.prefix))
  (var claimed False)
  (while (not claimed)
    (<- found (query-rows store.database observing))
    (<- gaps (index-observed store.indexes store.prefix store.schema found))
    (when (and (not gaps) (not store.indexes.retired))
      (return True))
    (<- bell (index-turn store))
    (if (is bell None)
        (:= claimed True)
        (<- (holder-awaited store bell))))
  ;; 錠の中の失敗でも錠を外す(外さないと、この process が生きる間 twin の索引の用意が錠を待ち続ける)。取り消しは process の終わりで、
  ;; 錠は接続と一緒に消える。
  (try
    (<- built (locked-round store observing))
    (except [TaskCancelledError]
      (raise))
    (except [Exception]
      (<- (index-unlocked store))
      (raise)))
  (<- (index-unlocked store))
  built)


(defk locked-round [store observing]
  {:pre [(: store PreparedStore) (: observing Statement)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠の中: 置き場の索引を観測し直し、残った差を作って、式の索引を作った表の標本を取り直し、もう 1 度観測して覚えを書き
   替え(標本を取り直した表の欄の式の索引の欄から、読みが表の欄の式で照らす — index-observed の scoped)、旧い形の索引を外してよい
   断面なら(retirement-ready?)外して観測し直すため。答え = 全部を作り、外す旧い形の索引も残っていないか(まだ外せない断面は False)。"
  (<- held (query-rows store.database observing))
  (<- remaining (index-observed store.indexes store.prefix store.schema held))
  (<- built (built-gaps store remaining))
  (when built
    (<- (analyzed store remaining))
    (<- after (query-rows store.database observing))
    (<- (index-observed store.indexes store.prefix store.schema after)))
  (when (or (not built) (not store.indexes.retired))
    (return built))
  (<- ready (retirement-ready? store.indexes store.prefix store.schema))
  (when (not ready)
    (return False))
  (<- dropped (retired-dropped store))
  (<- final (query-rows store.database observing))
  (<- (index-observed store.indexes store.prefix store.schema final))
  (and dropped (not store.indexes.retired)))


(defk retired-dropped [store]
  {:pre [(: store PreparedStore)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "旧い形の索引(覚えの retired)を名の順に 1 本ずつ CONCURRENTLY で外し、書きのたびの索引の更新を減らすため(DROP INDEX CONCURRENTLY は
   表に SHARE UPDATE EXCLUSIVE だけを取り、その索引を見た transaction の終わりを待つ — 行の読み書きを止めない)。答え = 全部を外せたか
   (外せなかった索引は名乗り、残りは次の回へ — 錠を外す道を例外で飛ばさないよう、取り消しの外の例外を上げない)。"
  (for [name store.indexes.retired]
    (<- started float (GetMonotonic))
    (try
      (<- dropping (drop-index-statement name))
      (<- (query-rows store.ddl-database dropping))
      (except [TaskCancelledError]
        (raise))
      (except [error Exception]
        (print (.format "記録の service: 旧い形の索引 {} を外せなかった: {}: {}" name (. (type error) __name__) error)
               :file sys.stderr :flush True)
        (return False)))
    (<- done float (GetMonotonic))
    (.dropped store.indexes name)
    (print (.format "記録の service: 旧い形の索引 {} を {} 秒で外した(CONCURRENTLY)" name (round (- done started) 1))
           :file sys.stderr :flush True))
  True)


(defk index-unlocked [store]
  {:pre [(: store PreparedStore)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を外し、錠を待つ他の process へ合図(置き場の通知の channel・名 INDEX-TOPIC)を出すため(取った接続 — ddl-database の
   接続 1 本)。"
  (<- unlocking (index-unlock-statement store.prefix))
  (<- (query-rows store.ddl-database unlocking))
  (<- channel (notice-channel store))
  (<- notified (SqlNotify store.database channel #(INDEX-TOPIC)))
  (<- (notice-raised notified))
  None)


(defk index-lock-taken [store]
  {:pre [(: store PreparedStore)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を待たずに取りに行くため(真 = 取れた — ddl-database の接続 1 本が持つ)。"
  (<- locking (index-lock-statement store.prefix))
  (<- rows (query-rows store.ddl-database locking))
  (bool (get rows 0 0)))


(defk index-turn [store]
  {:pre [(: store PreparedStore)] :post [(: % (| ExternalPromise None))]
   :tags {:context "records" :role "foundation"}}
  "索引の用意の錠を取りに行き、取れたら None を、他の process が持っていれば、その process が錠を放した合図(index-unlocked)の呼び鈴を
   返すため。取れなかった時は呼び鈴を掛けてから錠をもう 1 度試す(掛ける前に放された合図は届かないので、その時は 2 度目で取れる)。
   呼び鈴(LISTEN の接続)は snapshot を持たずに待つ。待ち受けに届かなければ StoreUnreachable を上げる(回が落ちて観測し直す)。"
  (<- taken (index-lock-taken store))
  (when taken
    (return None))
  (<- bell (hung-bell store #(INDEX-TOPIC)))
  (when (isinstance bell Unreachable)
    (raise (StoreUnreachable bell.detail)))
  (<- again (index-lock-taken store))
  (when again
    (<- (dropped-bell store bell))
    (return None))
  bell)


(defk holder-awaited [store bell]
  {:pre [(: store PreparedStore) (: bell ExternalPromise)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "錠を持つ process が錠を放した合図か、INDEX-RETRY-SECONDS 秒の早い方まで待つため(合図を出さずに落ちた process の錠は PostgreSQL が
   接続と一緒に外すので、時間で観測し直す — ポーリングの訳は INDEX-RETRY-SECONDS の註)。"
  (<- (WaitWithin bell.future INDEX-RETRY-SECONDS))
  (<- (dropped-bell store bell))
  None)


(defk analyzed [store gaps]
  {:pre [(: store PreparedStore) (: gaps tuple)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "式の索引を作った表の標本を取り直すため(作ったばかりの索引の式の統計は空 — 表の欄の式の索引の読みの見積もりに要る・
   DeclaredIndex の sampled)。落ちても索引は使えるので、名乗って続ける(その欄は、次の autovacuum の analyze の後の観測まで前の文の形で
   照らす)。"
  (val relations (sorted (sfor gap gaps :if gap.index.sampled gap.index.relation)))
  (for [relation relations]
    (<- analyzing (analyze-statement store.prefix relation))
    (try
      (<- (query-rows store.ddl-database analyzing))
      (except [TaskCancelledError]
        (raise))
      (except [error Exception]
        (print (.format "記録の service: 表 {}{} の標本を取り直せなかった: {}: {}" store.prefix relation (. (type error) __name__) error)
               :file sys.stderr :flush True))))
  None)


(defk built-gaps [store gaps]
  {:pre [(: store PreparedStore) (: gaps tuple)] :post [(: % bool)]
   :tags {:context "records" :role "foundation"}}
  "差の索引を宣言の順に 1 つずつ CONCURRENTLY で作り(使えない形で残った同じ名の索引は先に消す)、作れた索引の答える欄を覚えに足す
   ため(表の欄の式で照らす欄は、表の標本を取り直した後の観測が足す — locked-round)。
   答え = 全部を作れたか(作れなかった索引は名乗り、残りは次の回へ — 錠を外す道を例外で飛ばさないよう、取り消しの外の例外を上げない)。"
  (for [gap gaps]
    (<- started float (GetMonotonic))
    (try
      (when gap.invalid
        (<- dropping (drop-index-statement gap.index.name))
        (<- (query-rows store.ddl-database dropping)))
      (<- creating (concurrent-index-statement gap.index))
      (<- (query-rows store.ddl-database creating))
      (except [TaskCancelledError]
        (raise))
      (except [error Exception]
        (print (.format "記録の service: 索引 {} を作れなかった: {}: {}" gap.index.name (. (type error) __name__) error)
               :file sys.stderr :flush True)
        (return False)))
    (<- done float (GetMonotonic))
    (.added store.indexes gap.index.name gap.index.serves)
    (print (.format "記録の service: 索引 {} を {} 秒で作った({}{}・CONCURRENTLY)"
                    gap.index.name (round (- done started) 1) store.prefix gap.index.relation)
           :file sys.stderr :flush True))
  True)


(defk drop-records-tables [store]
  {:pre [(: store PreparedStore)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "検の後片付け: この置き場の接頭辞の表を消す(検の解釈器だけが呼ぶ)。"
  (<- statements (drop-statements store.prefix))
  (<- (run-statements store.database statements))
  None)


;; --- 行の綴り ----------------------------------------------------------------------------------------------

(defk store-head [store]
  {:pre [(: store PreparedStore)] :post [(: % StoreHead)]
   :tags {:context "records" :role "foundation"}}
  "置き場の頭を読むため。"
  (<- statement (store-head-statement store.prefix))
  (<- rows (query-rows store.database statement))
  (<- head (head-of rows))
  head)


(defk head-of [rows]
  {:pre [(: rows tuple)] :post [(: % StoreHead)]
   :tags {:context "records" :role "foundation"}}
  "store-head-statement の答えの行を置き場の頭にするため(読みの 1 文と、書きの 1 往復目の束が同じ綴りを使う)。"
  (val record (get rows 0))
  (StoreHead :epoch (int (get record 0)) :floor (int (get record 1)) :head (int (get record 2))))


(defn #^ FrozenMap decoded-value [#^ str payload]  ; defk にできない: 頁の行を内包表記で 1 度に組む listed-rows-of が行ごとに呼ぶ純粋な綴り(defk にすると行ごとに Program を bind し、頁の行の数だけ費用が足される)
  "state_rows / row_changes の payload(JSON の綴り)を行の値(深く凍らせた写像)にするため。DB から読む境界はここ 1 か所。"
  (frozen-json-object (json.loads payload) "state_rows の payload"))


(defn #^ Row row-of [#^ tuple record]  ; defk にできない: decoded-value と同じ — listed-rows-of の内包表記が行ごとに呼ぶ純粋な綴り
  "state_rows の行(key payload version …)を Row にするため。"
  (Row (key-from-text (get record 0)) (decoded-value (get record 1)) (int (get record 2))))


(defk listed-rows-of [decl fields records]
  {:pre [(: decl TableDecl) (: fields (| tuple None)) (: records (| tuple list))] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "ListRows の頁に出す行(返す欄だけ)を state_rows の行の列から作るため — 鍵の順の読みと上から読む読みが同じ形で行を出す 1 点。
   頁の行は内包表記で 1 度に tuple にする(行ごとに tuple を継ぎ足すと頁の行の数の 2 乗の写しになり、4 万行の頁で 1 回の答えの
   7 割 — 10 秒 — をこの写しが使っていた。行ごとの Program の bind も 1 割を使っていた)。"
  (tuple (gfor record records (listed-row decl fields (row-of record)))))


(defk change-of [record]
  {:pre [(: record tuple)] :post [(: % (| RowChanged RowRemoved))]
   :tags {:context "records" :role "foundation"}}
  "row_changes の行(seq ledger key version payload at)を RowChanged | RowRemoved にするため。"
  (val payload (get record 4))
  (val key (key-from-text (get record 2)))
  (if (is payload None)
      (RowRemoved (get record 1) key (int (get record 0)))
      (RowChanged (get record 1) key (int (get record 3)) (decoded-value payload) (int (get record 0)) (int (get record 5)))))


(defk event-of [stream record]
  {:pre [(: stream str) (: record tuple)] :post [(: % Event)]
   :tags {:context "records" :role "foundation"}}
  "append_rows の行(seq at payload)を Event にするため。"
  (val decoded (json.loads (get record 2)))
  (Event stream (int (get record 0)) (get decoded "idempotencyKey") (get decoded "body") (get decoded "writer") (int (get record 1))))


;; --- 保持 ------------------------------------------------------------------------------------------------

(defk row-expiry [decl now-ms]
  {:pre [(: decl TableDecl) (: now-ms int)] :post [(: % (| RowExpiry None))]
   :tags {:context "records" :role "foundation"}}
  "表 decl の行の保持の期限の境(読みの文の条件 — pg_sql.RowExpiry)を、刻 now-ms で作るため(None = 期限の無い表・終端の語の無い表 —
   読みの文に条件を足さない)。境の刻は admission.retention-cutoff-ms(回収の row-expired? と同じ 1 つ)。"
  (val cutoff (retention-cutoff-ms decl now-ms))
  (if (and (is-not cutoff None) decl.terminal)
      (RowExpiry :table decl.name :state-field decl.state-field :terminal decl.terminal :before-at cutoff)
      None))


(defk event-expiry [decl now-ms]
  {:pre [(: decl StreamDecl) (: now-ms int)] :post [(: % (| EventExpiry None))]
   :tags {:context "records" :role "foundation"}}
  "追記の列 decl の出来事の保持の期限の境(pg_sql.EventExpiry — 境の刻と組で数える列の区切り)を、刻 now-ms で作るため(None = 期限の
   無い列)。読みの文の条件と回収の文(purge-expired)がこの 1 つを使う。境の刻は admission.retention-cutoff-ms。"
  (val cutoff (retention-cutoff-ms decl now-ms))
  (if (is cutoff None)
      None
      (EventExpiry :before-at cutoff
                   :separator (match decl.retention-group
                                (ByKeySuffix :separator text) text
                                _ None))))


(defk expired-rows [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "期限を過ぎた行(ExpiredRow)の列(表の名の順・鍵の順)を読むため。判断は admission.row-expired?。
   終端の語が空の表は文を流さない(`IN ()` は文にできない — 宣言は KeepFor の表に終端の語を求めるので、今は起きない)。"
  (var found [])
  (for [#(name decl) (sorted (.items store.schema.tables))]
    (when (and (isinstance decl.retention KeepFor) decl.terminal)
      (val scoped (in decl.state-field (.scoped-fields store.indexes name)))
      (<- statement (terminal-rows-statement store.prefix name decl.state-field decl.terminal scoped))
      (<- records (query-rows store.database statement))
      (for [record records]
        (val value (decoded-value (get record 1)))
        (when (row-expired? decl value (int (get record 3)) now-ms)
          (.append found (ExpiredRow :table name :record (tuple record)))))))
  (tuple found))


(defk removed-row-statements [store table text version now-ms epoch]
  {:pre [(: store PreparedStore) (: table str) (: text str) (: version int) (: now-ms int) (: epoch int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限を過ぎた行 1 つ(版 version)を消し、変更の列に「消えた」を積む 2 文を作るため(回収 remove-row と、期限を過ぎた行へ書く書きの
   片付け locked-row が同じ 2 文を使う)。"
  (<- delete (delete-row-statement store.prefix table text version))
  (<- change (append-change-statement store.prefix table text version None now-ms epoch))
  #(delete change))


(defk remove-row [store table text version now-ms epoch]
  {:pre [(: store PreparedStore) (: table str) (: text str) (: version int) (: now-ms int) (: epoch int)] :post [(: % None)]
   :tags {:context "records" :role "foundation"}}
  "回収(SweepExpired)の書きの錠の transaction の中で、保持の期限を過ぎた行 1 つを消し、変更の列に「消えた」を積むため。"
  (<- statements (removed-row-statements store table text version now-ms epoch))
  (<- (run-statements store.database statements))
  None)


(defk remove-expired-rows [store now-ms]
  {:pre [(: store PreparedStore) (: now-ms int)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、期限を過ぎた行を読み直して消し、変更の列に「消えた」を積むため。答え = 消した行の数。"
  (<- head (store-head store))
  (<- expired (expired-rows store now-ms))
  (for [row expired]
    (<- (remove-row store row.table (get row.record 0) (int (get row.record 2)) now-ms head.epoch)))
  (len expired))


(defk retired-keys [stream removed]
  {:pre [(: stream str) (: removed tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "保持の期限で捨てる出来事(append_rows の行 seq at payload の組 removed)の冪等キーの覚え(RetiredKey — 番号と本文の指紋)を作るため
   (#3022 — 捨てる transaction が同じ transaction で鍵だけの表へ入れ、消した後の同じ鍵の追記も append-locked が同じ規則で判じる。出来事
   だけ消えて覚えが無い断面を作らない)。回収(retire-expired-events)と書きの片付け(retired-events-statements)が使う。"
  (var kept #())
  (for [record removed]
    (<- event (event-of stream record))
    (:= kept (+ kept #((RetiredKey :idempotency-key event.idempotency-key :sequence event.sequence
                                   :body-digest (body-digest event.body))))))
  kept)


(defk retire-expired-events [store stream before-at separator sequences]
  {:pre [(: store PreparedStore) (: stream str) (: before-at int) (: separator (| str None)) (: sequences tuple) (> (len sequences) 0)]
   :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の transaction の中で、列 stream の回収の候補(番号 sequences — 錠の外で読んだ expiring-sequences の答え)のうち今も保持の期限を
   過ぎた出来事を捨て、同じ transaction で冪等キーの覚えへ移すため(回収 — retired-keys)。separator = None は出来事ごとに数える列・str は
   組で数える列の区切り。答え = 捨てた出来事の数。"
  (<- delete (expire-chosen-events-statement store.prefix stream before-at separator sequences))
  (<- removed (query-rows store.database delete))
  (<- kept (retired-keys stream removed))
  (when kept
    (<- retire (retire-keys-statement store.prefix stream kept))
    (<- (query-rows store.database retire)))
  (len removed))


(defk expiring-sequences [store stream expiry]
  {:pre [(: store PreparedStore) (: stream str) (: expiry EventExpiry)] :post [(: % (get tuple #(int ...)))]
   :tags {:context "records" :role "foundation"}}
  "列 stream の保持の期限(境 expiry)を過ぎた出来事の番号を EXPIRE-BATCH 個まで、書きの錠の外で読むため(回収の候補 — 空なら錠を取らない)。"
  (<- statement (expiring-candidates-statement store.prefix stream expiry.before-at expiry.separator EXPIRE-BATCH))
  (<- records (query-rows store.database statement))
  (tuple (gfor record records (int (get record 0)))))


(defk retire-stream [store gated stream expiry]
  {:pre [(: store PreparedStore) (: gated bool) (: stream str) (: expiry EventExpiry)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "列 stream の保持の期限(境 expiry)を過ぎた出来事を、EXPIRE-BATCH 個ずつ書きの錠の transaction を分けて捨てるため(錠の外で候補の番号を
   読み、錠の中ではその番号の出来事だけを条件を読み直して捨てる — 錠を持つ間に触る行を EXPIRE-BATCH で縛る・pg_sql の頭の註)。候補が
   無ければ錠を取らない。候補が上限より少なかった回(残りが無い)か、錠の中で 1 つも捨てなかった回(候補を読んだ後に組へ新しい出来事が
   積まれた — 続きは次の SweepExpired)で終える。答え = 捨てた出来事の数。"
  (<- topics (notice-topics #() #(stream)))
  (<- unit (stream-unit stream))
  (<- first (expiring-sequences store stream expiry))
  (var chosen first)
  (var total 0)
  (var going (bool chosen))
  (while going
    (<- removed (writing store gated "sweep-stream" #(unit) topics
                         (retire-expired-events store stream expiry.before-at expiry.separator chosen)))
    (:= total (+ total removed))
    (:= going (and (= (len chosen) EXPIRE-BATCH) (> removed 0)))
    (when going
      (<- more (expiring-sequences store stream expiry))
      (:= chosen more)
      (:= going (bool chosen))))
  total)


(defk purge-expired [store gated now-ms]
  {:pre [(: store PreparedStore) (: gated bool) (: now-ms int)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "期限を過ぎた行を消して変更の列に「消えた」を積み、期限を過ぎた出来事を捨てて冪等キーの覚えへ移すため(SweepExpired の掃除 — 読みは
   回収を待たずに期限を自分で見て、書きは自分が触る物だけを片付ける・頭の註)。候補が無ければ錠を取らない(行も出来事も、候補の読み 1 文の
   後に、候補が在る時だけ書きの錠の transaction を開く — 出来事は列ごとに retire-stream)。答え = 消した行の数。"
  (<- candidates (expired-rows store now-ms))
  (var removed 0)
  (when candidates
    ;; 合図の名 = 行を消し得る表(期限と終端の語の在る表 — 消す行は transaction の中で読み直すので、候補の表でなく、読み直しが読む表の全部)。
    (<- row-topics (notice-topics (tuple (gfor #(name decl) (sorted (.items store.schema.tables))
                                               :if (and (isinstance decl.retention KeepFor) decl.terminal)
                                               name))
                                  #()))
    (<- count (writing store gated "sweep-rows" #(ROWS-UNIT) row-topics (remove-expired-rows store now-ms)))
    (:= removed count))
  (for [#(name decl) (sorted (.items store.schema.streams))]
    (<- expiry (event-expiry decl now-ms))
    (when (is-not expiry None)
      (<- (retire-stream store gated name expiry))))
  removed)


;; --- 行 --------------------------------------------------------------------------------------------------

(defk pg-read-row [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadRow) (: now-ms int)] :post [(: % (| Row Missing))]
   :tags {:context "records" :role "foundation"}}
  "ReadRow に答えるため(刻 now-ms で保持の期限を過ぎた終端の行は、回収の前でも Missing — 文の条件・頭の註)。"
  (val decl (store.schema.table ask.table))
  (<- expiry (row-expiry decl now-ms))
  (<- statement (read-row-statement store.prefix ask.table (key-text ask.key) expiry))
  (<- records (query-rows store.database statement))
  (if records (row-of (get records 0)) (Missing)))


(defk unbuilt-fields [store decl names]
  {:pre [(: store PreparedStore) (: decl TableDecl) (: names tuple)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "records" :role "foundation"}}
  "ListRows が名指す欄 names のうち、宣言した索引の欄なのに置き場に使える索引がまだ無い欄を名の順に並べるため(索引の用意が作り
   終えるまで NotIndexed で断る — 頭の註の「索引の用意」。鍵の欄は索引を待たない)。"
  (tuple (sorted (sfor name names :if (and (in name decl.indexes) (not (.covers store.indexes decl.name name))) name))))


(defk pg-list-rows [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ListRows) (: now-ms int)] :post [(: % (| Page Reset NotIndexed))]
   :tags {:context "records" :role "foundation"}}
  "ListRows に答えるため(刻 now-ms で保持の期限を過ぎた終端の行は、回収の前でも頁に出さない — 文の条件・頭の註)。"
  (val decl (store.schema.table ask.table))
  (<- head (store-head store))
  (when (and (is-not ask.cursor None) (!= ask.cursor.epoch head.epoch))
    (return (Reset head.epoch head.floor)))
  (val refusal (where-refusal decl ask.where ask.where-any))
  (when refusal (return refusal))
  (<- unbuilt (unbuilt-fields store decl (+ (tuple ask.where) (tuple ask.where-any))))
  (when unbuilt (return (NotIndexed unbuilt)))
  (<- expiry (row-expiry decl now-ms))
  (val where-json (dfor #(name value) (.items ask.where) name (canonical-json value)))
  (val where-any-json (dfor #(name members) (.items ask.where-any) name (tuple (gfor value members (canonical-json value)))))
  ;; 表の欄の式の索引が在る欄は表の欄の式で照らす(作り終える前の欄は前の文の形 — pg_sql の頭の註)。
  (val scoped (.scoped-fields store.indexes ask.table))
  (when (is-not ask.descending-by None)
    ;; 上から読む読み(ListRows の註): cursor の位置の (値, 鍵の綴り) より小さい行から 1 頁 + 1 行(続きの有無を知るため)。表の上から読む
    ;; 欄は並べる式の索引の順に辿る形(pg_sql の頭の註)。
    (<- top-statement (top-rows-statement store.prefix ask.table decl.key-fields scoped ask.descending-by (in ask.descending-by decl.descending)
                                          ask.cursor where-json where-any-json ask.limit expiry))
    (<- top-records (query-rows store.database top-statement))
    (val top-taken (cut top-records ask.limit))
    (<- top-rows (listed-rows-of decl ask.fields top-taken))
    (when (<= (len top-records) ask.limit)
      (return (Page top-rows None head.epoch head.head)))
    ;; 位置の値は頁の最後の行の全部の欄から読む(返す欄 fields が並べる欄を外しても)。
    (val last-row (row-of (get top-taken -1)))
    (return (Page top-rows
                  (ListCursor head.epoch (get (get top-taken -1) 0) (get last-row.value ask.descending-by))
                  head.epoch
                  head.head)))
  (<- statement (list-rows-statement store.prefix ask.table decl.key-fields scoped
                                     (if (is ask.cursor None) None ask.cursor.after-key)
                                     where-json
                                     where-any-json
                                     ask.limit
                                     expiry))
  (<- records (query-rows store.database statement))
  (val taken (cut records ask.limit))
  (<- rows (listed-rows-of decl ask.fields taken))
  (Page rows
        (if (> (len records) ask.limit) (ListCursor head.epoch (get (get taken -1) 0)) None)
        head.epoch
        head.head))


(defk locked-row [store decl text records now-ms epoch]
  {:pre [(: store PreparedStore) (: decl TableDecl) (: text str) (: records tuple) (: now-ms int) (: epoch int)] :post [(: % LockedRow)]
   :tags {:context "records" :role "foundation"}}
  "書きの判定に渡す今の行を、1 往復目の行の錠(FOR UPDATE)つきの読み records から判じるため(書きの transaction の中)。読んだ行が刻
   now-ms で保持の期限を過ぎた終端の行なら(回収と同じ判定 admission.row-expired?)、回収と同じくその行を消して変更の列に「消えた」を積む
   文を片付けに添え(2 往復目の先頭に流す)、無い行として返す — 書きは回収の後と同じく無い行として判じる(#3605 の D・頭の註)。
   epoch = 「消えた」に刻む置き場の版。"
  (when (not records)
    (return (LockedRow :current None :cleanup #())))
  (val record (get records 0))
  (val row (row-of record))
  (when (row-expired? decl row.value (int (get record 3)) now-ms)
    (<- cleanup (removed-row-statements store decl.name text row.version now-ms epoch))
    (return (LockedRow :current None :cleanup cleanup)))
  (LockedRow :current row :cleanup #()))


(defk staged-row [store origin-host writer table text current value now-ms epoch]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: table str) (: text str) (: current (| Row None))
         (: value FrozenMap) (: now-ms int) (: epoch int)]
   :post [(: % StagedRow)]
   :tags {:context "records" :role "foundation"}}
  "判定を通った 1 行の書き(行の書きと変更の列に 1 つ積む文 — 2 往復目に流す)と、その答えを作るため(PutRow と PutRows の書きを 1 つにし、
   版と番号の採り方を揃える)。"
  (val version (if (is current None) 1 (+ current.version 1)))
  (val payload (canonical-json value))
  (<- upsert (upsert-row-statement store.prefix table text payload version now-ms writer origin-host epoch))
  (<- change (append-change-statement store.prefix table text version payload now-ms epoch))
  (StagedRow :statements #(upsert change) :written (Written version value)))


(defk put-row-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRow) (: now-ms int)]
   :post [(: % (| Written Conflict Refused))]
   :tags {:context "records" :role "foundation"}}
  "PutRow の書きの錠の中の手順を 2 往復で流すため(頭の註): 1 往復目に置き場の頭と今の行を錠つきで読み、判定し、2 往復目に片付けと
   (通れば)行の書きを COMMIT と同じ往復で流す。"
  (val decl (store.schema.table ask.table))
  (val text (key-text ask.key))
  (<- head-statement (store-head-statement store.prefix))
  (<- lock-statement (lock-row-statement store.prefix decl.name text))
  (<- read (batch-rows store.database #(head-statement lock-statement) False))
  (<- head (head-of (get read 0)))
  (<- found (locked-row store decl text (get read 1) now-ms head.epoch))
  (val conflict (judge-expect ask.expect found.current))
  (when conflict
    (<- collided (committed store found.cleanup conflict))
    (return collided))
  (val verdict (judge-put decl found.current ask.key ask.value))
  (when (isinstance verdict Refused)
    (<- refused (committed store found.cleanup verdict))
    (return refused))
  (<- staged (staged-row store origin-host writer ask.table text found.current verdict.value now-ms head.epoch))
  (<- written (committed store (+ found.cleanup staged.statements) staged.written))
  written)


(defk put-rows-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask PutRows) (: now-ms int)]
   :post [(: % (| WrittenRows RowsConflict RowsRefused))]
   :tags {:context "records" :role "foundation"}}
  "PutRows の束を全部か 0 で、行の数に依らず 2 往復で書くため(頭の註): 1 往復目に置き場の頭と全部の行を錠つきで読み(期限を過ぎた行は
   片付けて無い行として — locked-row)、判定(admission.judge-put-rows)が全部通った時だけ、2 往復目に片付けと束の順の書き・変更を COMMIT と
   同じ往復で流す(錠の中なので番号は束の中で続く)。往復の途中の失敗は transaction ごと戻る。"
  (for [write ask.writes] (store.schema.table write.table))
  (val texts (tuple (gfor write ask.writes (key-text write.key))))
  (<- head-statement (store-head-statement store.prefix))
  (var reads #(head-statement))
  (for [#(write text) (zip ask.writes texts :strict True)]
    (<- lock-statement (lock-row-statement store.prefix write.table text))
    (:= reads (+ reads #(lock-statement))))
  (<- read (batch-rows store.database reads False))
  (<- head (head-of (get read 0)))
  (var found #())
  (for [#(write text records) (zip ask.writes texts (cut read 1 None) :strict True)]
    (<- one (locked-row store (store.schema.table write.table) text records now-ms head.epoch))
    (:= found (+ found #(one))))
  (val cleanup (tuple (gfor one found statement one.cleanup statement)))
  (val currents (tuple (gfor one found one.current)))
  (val verdict (judge-put-rows store.schema ask.writes currents))
  (when (not (isinstance verdict tuple))
    (<- refused (committed store cleanup verdict))
    (return refused))
  (var statements cleanup)
  (var written #())
  (for [#(write text current admitted) (zip ask.writes texts currents verdict :strict True)]
    (<- staged (staged-row store origin-host writer write.table text current admitted.value now-ms head.epoch))
    (:= statements (+ statements staged.statements))
    (:= written (+ written #(staged.written))))
  (<- rows (committed store statements (WrittenRows written)))
  rows)


(defk table-expiries [store tables now-ms]
  {:pre [(: store PreparedStore) (: tables tuple) (: now-ms int)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "表 tables のうち保持の期限の在る表の境(RowExpiry の組 — 頼んだ表の順)を刻 now-ms で作るため(変更の列の読みの条件)。"
  (var expiries #())
  (for [name tables]
    (<- expiry (row-expiry (store.schema.table name) now-ms))
    (when (is-not expiry None)
      (:= expiries (+ expiries #(expiry)))))
  expiries)


(defk tail-reads [store streams now-ms]
  {:pre [(: store PreparedStore) (: streams (get tuple #(str ...))) (: now-ms int)] :post [(: % (get tuple #(TailRead ...)))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges が末尾を名指した列 streams の読みの材料(pg_sql.TailRead の組 — 名指した順)を刻 now-ms で作るため(#3718 — 期限の境は
   ReadStreamEnd と同じ event-expiry)。宣言に無い列は UndeclaredTable。"
  (var reads #())
  (for [name streams]
    (<- expiry (event-expiry (store.schema.stream name) now-ms))
    (:= reads (+ reads #((TailRead :stream name :expiry expiry)))))
  reads)


(defk tails-of [streams columns]
  {:pre [(: streams (get tuple #(str ...))) (: columns tuple) (= (len columns) (* 2 (len streams)))]
   :post [(: % (get tuple #((| StreamTail StreamTailEmpty) ...)))]
   :tags {:context "records" :role "foundation"}}
  "watch-head-statement の答えの行の 4 つ目からの列(名指した列ごとに seq・at の 2 つ — 生きている出来事の無い列は NULL)を、名指した順の
   tails(StreamTail | StreamTailEmpty)にするため(#3718)。"
  (var tails #())
  (for [#(stream #(sequence at)) (zip streams (zip (cut columns 0 None 2) (cut columns 1 None 2) :strict True) :strict True)]
    (:= tails (+ tails #((match sequence
                            None (StreamTailEmpty :stream stream)
                            _ (StreamTail :stream stream :sequence (int sequence) :at (int at)))))))
  tails)


(defk pg-watch-scan [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask WatchChanges) (: now-ms int)] :post [(: % (| Changes Reset))]
   :tags {:context "records" :role "foundation"}}
  "WatchChanges の 1 回ぶんの読み(待たない)を流すため。行の今の値が刻 now-ms で保持の期限を過ぎた終端の行である行の変わりは、回収の
   前でも出さない(文の条件・頭の註)。頼んだ表が空なら文を流さない(`IN ()` は文にできない — WatchChanges は空の組を作る時に断るので、
   今は起きない)。名指した列(ask.streams)の末尾は、置き場の頭と同じ 1 文(watch-head-statement — 同じ断面)で読み、変更の列は頭の番号
   までを読む(変更と末尾が同じ断面の物になる・往復は増えない — #3718)。"
  (for [name ask.tables] (store.schema.table name))
  (<- reads tuple (tail-reads store ask.streams now-ms))
  (<- head-statement Statement (watch-head-statement store.prefix reads))
  (<- head-rows tuple (query-rows store.database head-statement))
  (<- head StoreHead (head-of head-rows))
  (val cursor ask.cursor)
  (when (or (!= cursor.epoch head.epoch) (< cursor.sequence head.floor) (> cursor.sequence head.head))
    (return (Reset head.epoch head.floor)))
  (var items [])
  (when ask.tables
    (<- expiries (table-expiries store (tuple ask.tables) now-ms))
    (<- statement (changes-statement store.prefix cursor.sequence head.head ask.tables ask.limit expiries))
    (<- records (query-rows store.database statement))
    (for [record records]
      (<- change (change-of record))
      (.append items change)))
  (<- tails tuple (tails-of ask.streams (tuple (cut (get head-rows 0) 3 None))))
  (Changes (tuple items) (WatchCursor head.epoch (next-watch-sequence (tuple items) ask.limit head.head)) tails))


;; --- 追記の列 --------------------------------------------------------------------------------------------

(defk touched-reads [store decl expiry idempotency-key]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: expiry (| EventExpiry None)) (: idempotency-key str)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "追記の 1 往復目に足す、追記が触る組の期限を過ぎた出来事の読み(0 か 1 文)を作るため(#3605 の D): 組で数える列で期限の在る時だけ —
   組の他の鍵の出来事は鍵の引きでは分からない。出来事ごとに数える列は鍵の引き(find-event)の刻で判じるので文を足さない。"
  (if (and (is-not expiry None) (is-not expiry.separator None))
      #((! (touched-events-statement store.prefix decl.name expiry.before-at expiry.separator idempotency-key)))
      #()))


(defk retired-events-statements [store stream removed]
  {:pre [(: store PreparedStore) (: stream str) (: removed tuple)] :post [(: % tuple)]
   :tags {:context "records" :role "foundation"}}
  "追記が触る単位の期限を過ぎた出来事(1 往復目に読んだ行 removed)を捨てて冪等キーの覚えへ移す文を作るため(2 往復目の先頭に流す —
   回収と同じく、出来事だけ消えて覚えが無い断面を作らない・#3022)。捨てる物が無ければ空。"
  (when (not removed)
    (return #()))
  (<- kept (retired-keys stream removed))
  (<- delete (delete-events-statement store.prefix stream (tuple (gfor record removed (int (get record 0))))))
  (<- retire (retire-keys-statement store.prefix stream kept))
  #(delete retire))


(defk earlier-use [store decl expiry idempotency-key found kept grouped [scheduled (frozenset)]]
  {:pre [(: store PreparedStore) (: decl StreamDecl) (: expiry (| EventExpiry None)) (: idempotency-key str) (: found tuple) (: kept tuple)
         (: grouped tuple) (: scheduled frozenset)]
   :post [(: % EarlierUse)]
   :tags {:context "records" :role "foundation"}}
  "冪等キーの前の使いと、追記が触る単位の片付けの文を、追記の 1 往復目の読み(書きの錠の中)から判じるため(#3605 の D — 書きは置き場の
   全部を回収しない。答えは回収の後の追記と同じ: 期限を過ぎた鍵は覚えで判じ、期限を過ぎた組に新しい鍵を積んでも組の古い出来事は読みに
   戻らない)。found = 鍵の生きた出来事の引きの行・kept = 鍵の覚えの引きの行・grouped = touched-reads の答え(組で数える列だけ)。
   捨てる物 = 出来事ごとに数える列は鍵の出来事が境の刻(event-expiry — 回収と読みと同じ境)以前に積まれていればそれ・組で数える列は組の
   読みの答え。前の使い = 捨てずに残る生きた出来事・無ければ鍵の覚え(在った覚えは書き換えない — 回収の ON CONFLICT DO NOTHING と同じ)・
   それも無く鍵の出来事を今捨てるならその覚え・どれも無ければ None。出来事を消して覚えを入れる刈りも同じ書きの錠の transaction なので、
   読みと書きの間に刈りは挟まらない。scheduled = 同じ transaction の前の追記(束の前の追記)が既に片付ける出来事の番号 — 片付けの文から
   外す(前の使いの判じには数える — 束の順に AppendEvent を撃てば、前の追記の片付けの後の断面で判じるのと同じ)。"
  (val stream decl.name)
  (var live None)
  (when found
    (<- event (event-of stream (get found 0)))
    (:= live event))
  (val removed (cond
                 (is expiry None) #()
                 (is-not expiry.separator None) (get grouped 0)
                 (and (is-not live None) (<= live.at expiry.before-at)) found
                 True #()))
  (<- cleanup (retired-events-statements store stream (tuple (gfor record removed :if (not-in (int (get record 0)) scheduled) record))))
  (val removed-sequences (frozenset (gfor record removed (int (get record 0)))))
  (val previous (cond
                  (and (is-not live None) (not-in live.sequence removed-sequences)) live
                  kept (RetiredKey :idempotency-key idempotency-key :sequence (int (get kept 0 0)) :body-digest (get kept 0 1))
                  (is-not live None) (RetiredKey :idempotency-key live.idempotency-key :sequence live.sequence
                                                 :body-digest (body-digest live.body))
                  True None))
  (EarlierUse :previous previous :cleanup cleanup :removed removed-sequences))


(defk append-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask AppendEvent) (: now-ms int)]
   :post [(: % (| Appended Refused))]
   :tags {:context "records" :role "foundation"}}
  "AppendEvent の書きの錠の中の手順を 2 往復で流すため(頭の註): 1 往復目に置き場の頭・冪等キーの引き・鍵の覚え・(組で数える列は)組の期限を
   過ぎた出来事を読み、前の使いを判じ(earlier-use)、2 往復目に片付けと(新しければ)出来事の書きを COMMIT と同じ往復で流す。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- head-statement (store-head-statement store.prefix))
  (<- find (find-event-statement store.prefix ask.stream ask.idempotency-key))
  (<- lookup (find-retired-key-statement store.prefix ask.stream ask.idempotency-key))
  (<- grouping (touched-reads store decl expiry ask.idempotency-key))
  (<- read (batch-rows store.database (+ #(head-statement find lookup) grouping) False))
  (<- head (head-of (get read 0)))
  (<- use (earlier-use store decl expiry ask.idempotency-key (get read 1) (get read 2) (cut read 3 None)))
  (val verdict (judge-append decl ask.body use.previous))
  (when (isinstance verdict Refused)
    (<- refused (committed store use.cleanup verdict))
    (return refused))
  (when (isinstance verdict AppendReplay)
    (<- replayed (committed store use.cleanup (Appended verdict.sequence True)))
    (return replayed))
  (val payload (canonical-json {"idempotencyKey" ask.idempotency-key "writer" writer "body" ask.body}))
  (<- insert (insert-event-statement store.prefix ask.stream now-ms payload origin-host head.epoch))
  (<- rows (batch-rows store.database (+ use.cleanup #(insert)) True))
  (Appended (int (get (get rows -1) 0 0)) False))


(defk append-events-locked [store origin-host writer ask now-ms]
  {:pre [(: store PreparedStore) (: origin-host str) (: writer str) (: ask AppendEvents) (: now-ms int)]
   :post [(: % AppendedEvents)]
   :tags {:context "records" :role "foundation"}}
  "AppendEvents の束を、追記の数に依らず 2 往復で積むため(頭の註の「書きの往復」・card ki-48c34994ed15): 1 往復目に置き場の頭と、追記ごとの
   冪等キーの引き・鍵の覚え・(組で数える列は)組の期限を過ぎた出来事を読み、追記ごとに前の使いを判じ(earlier-use — 束の前の追記が片付ける
   出来事は重ねて片付けない)、2 往復目に片付けと今積む追記の書きを束の順に COMMIT と同じ往復で流す(書きの錠の中なので番号は束の順に増え、
   間に他の書きは入らない)。束の冪等キーは互いに違う(AppendEvents が作る時に断る)ので、追記ごとの判断は束の前の断面で決まり、束の順に
   AppendEvent を撃った答えと同じになる。断られた追記は他の追記を止めない。"
  (<- head-statement (store-head-statement store.prefix))
  (var reads #(head-statement))
  (var spans #())
  (for [one ask.appends]
    (val decl (store.schema.stream one.stream))
    (<- expiry (event-expiry decl now-ms))
    (<- find (find-event-statement store.prefix one.stream one.idempotency-key))
    (<- lookup (find-retired-key-statement store.prefix one.stream one.idempotency-key))
    (<- grouping (touched-reads store decl expiry one.idempotency-key))
    (:= spans (+ spans #((AppendReads :decl decl :expiry expiry :at (len reads) :grouped (len grouping)))))
    (:= reads (+ reads #(find lookup) grouping)))
  (<- read (batch-rows store.database reads False))
  (<- head (head-of (get read 0)))
  (var scheduled (frozenset))
  (var statements #())
  (var answers #())
  (for [#(one span) (zip ask.appends spans :strict True)]
    (val start (+ span.at 2))
    (<- use (earlier-use store span.decl span.expiry one.idempotency-key (get read span.at) (get read (+ span.at 1))
                         (tuple (cut read start (+ start span.grouped))) scheduled))
    (:= scheduled (| scheduled use.removed))
    (:= statements (+ statements use.cleanup))
    (val verdict (judge-append span.decl one.body use.previous))
    (when (isinstance verdict Refused)
      (:= answers (+ answers #(verdict)))
      (continue))
    (when (isinstance verdict AppendReplay)
      (:= answers (+ answers #((Appended verdict.sequence True))))
      (continue))
    (<- insert (insert-event-statement store.prefix one.stream now-ms
                                       (canonical-json {"idempotencyKey" one.idempotency-key "writer" writer "body" one.body})
                                       origin-host head.epoch))
    (:= answers (+ answers #((InsertAt :index (len statements)))))
    (:= statements (+ statements #(insert))))
  (<- rows (batch-rows store.database statements True))
  (AppendedEvents (tuple (gfor answer answers
                               (if (isinstance answer InsertAt) (Appended (int (get (get rows answer.index) 0 0)) False) answer)))))


(defk pg-read-events [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadEvents) (: now-ms int)] :post [(: % Events)]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents に答えるため(刻 now-ms で保持の期限を過ぎた出来事は、回収の前でも出さない — 文の条件・頭の註)。key-head を付けた読みは
   冪等キーの頭が key-head の出来事だけを読む(列が頭で引けるかは呼び手の節が key-head-refusal で先に判じる)。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (val head (if (and (is-not ask.key-head None) (is-not decl.key-lookup None))
                (HeadRead :separator decl.key-lookup.separator :head ask.key-head)
                None))
  (<- statement (read-events-statement store.prefix ask.stream ask.after ask.limit expiry head))
  (<- records (query-rows store.database statement))
  (var items [])
  (for [record records]
    (<- event (event-of ask.stream record))
    (.append items event))
  (Events (tuple items) (if items (. (get items -1) sequence) ask.after)))


(defk pg-read-stream-end [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadStreamEnd) (: now-ms int)] :post [(: % (| StreamEnd StreamEmpty))]
   :tags {:context "records" :role "foundation"}}
  "ReadStreamEnd に答えるため: 列の生きた出来事の max(seq) を 1 文で読む(刻 now-ms で保持の期限を過ぎた出来事は回収の前でも数えない —
   ReadEvents と同じ条件。生きた出来事が無ければ StreamEmpty)。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- statement (stream-end-statement store.prefix ask.stream expiry))
  (<- records (query-rows store.database statement))
  (val last (get (get records 0) 0))
  (if (is last None) (StreamEmpty) (StreamEnd (int last))))


(defk pg-read-event-by-key [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadEventByKey) (: now-ms int)] :post [(: % (| Event EventAbsent EventRetired))]
   :tags {:context "records" :role "foundation"}}
  "ReadEventByKey に答えるため: 冪等キーの出来事と、回収で消した鍵の覚えを 1 文で引く(event-by-key-statement — 一意の索引の引き 2 つで、
   列を辿らない)。生きた出来事は Event・保持の期限を過ぎた出来事(回収の前)と覚えの鍵は EventRetired・どちらも無ければ EventAbsent。
   消した鍵の再送は新しい出来事を積まない(judge-append)ので、出来事の行と覚えの行は同じ鍵で両方は来ない。"
  (val decl (store.schema.stream ask.stream))
  (<- expiry (event-expiry decl now-ms))
  (<- statement (event-by-key-statement store.prefix ask.stream ask.idempotency-key expiry))
  (<- records (query-rows store.database statement))
  (when (not records)
    (return (EventAbsent)))
  (val record (get records 0))
  ;; 覚えの行('retired')と、期限を過ぎた出来事の行(5 列目 = 過ぎたか)は、どちらも消えた出来事の番号で答える。
  (when (or (= (get record 0) "retired") (get record 4))
    (return (EventRetired (int (get record 1)))))
  (<- event (event-of ask.stream (tuple (cut record 1 4))))
  event)


(defk pg-answer-read-events [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask ReadEvents) (: now-ms int)] :post [(: % (| Events NotIndexed))]
   :tags {:context "records" :role "foundation"}}
  "公開の ReadEvents に答えるため: 頭で引けない列への頭の読みは文を流さずに断り(memory の置き場と同じ判じ admission.key-head-refusal)、
   ほかは pg-read-events で読む(列の待ち WatchEvents の読みは頭で引かないので pg-read-events を直に呼ぶ)。"
  (val refusal (key-head-refusal (store.schema.stream ask.stream) ask.key-head))
  (when refusal
    (return refusal))
  (<- events (pg-read-events store ask now-ms))
  events)


(defk moved-after [program]
  {:pre [(: program Program)] :post [(: % (| EventsMoved EventsQuiet Unreachable))]
   :tags {:context "records" :role "foundation"}}
  "ReadEvents(limit 1)の答えの Program を WatchEvents の 1 回ぶんの答えにするため。"
  (<- answer program)
  (<- moved (moved-of answer))
  moved)


(defk advance-epoch-locked [store]
  {:pre [(: store PreparedStore)] :post [(: % int)]
   :tags {:context "records" :role "foundation"}}
  "書きの錠の中で置き場の版を 1 進め、変更の列を忘れるため。答え = 新しい版。"
  (<- advance (advance-epoch-statement store.prefix))
  (<- rows (query-rows store.database advance))
  (<- forget (forget-changes-statement store.prefix))
  (<- (query-rows store.database forget))
  (int (get (get rows 0) 0)))


;; --- 手入れ ------------------------------------------------------------------------------------------------

(defk prune-locked [store ask now-ms]
  {:pre [(: store PreparedStore) (: ask PruneChanges) (: now-ms int)] :post [(: % Pruned)]
   :tags {:context "records" :role "foundation"}}
  "変更の列が際限なく伸びないように、keep-seconds より古い変更を(upto-seq が在ればその番号まで)消して floor を上げるため(書きの錠の
   中 — 読み手の断面と揃える)。"
  (<- statement (prune-changes-statement store.prefix (- now-ms (int (* 1000 ask.keep-seconds)))
                                         (if (is ask.upto-seq None) UNBOUNDED-SEQ ask.upto-seq)))
  (<- rows (query-rows store.database statement))
  (val record (get rows 0))
  (Pruned (int (get record 0)) (int (get record 1))))


(defk change-log [store ask]
  {:pre [(: store PreparedStore) (: ask ReadChangeLog)] :post [(: % ChangeLog)]
   :tags {:context "records" :role "foundation"}}
  "変更の列の ask.after より後の変更を番号の順に ask.limit 件まで、表と保持の期限で絞らずに読むため(手入れの送りの読み — 錠の外)。
   版は同じ読みの前に置き場の頭から読む(版を進めると変更の列は空になり、番号は進み続ける)。"
  (<- head (store-head store))
  (<- statement (change-log-statement store.prefix ask.after ask.limit))
  (<- rows (query-rows store.database statement))
  (var changes #())
  (for [record rows]
    (<- change (change-of record))
    (:= changes (+ changes #(change))))
  (ChangeLog changes head.epoch))


(defk swept-count [store gated now-ms]
  {:pre [(: store PreparedStore) (: gated bool) (: now-ms int)] :post [(: % Swept)]
   :tags {:context "records" :role "foundation"}}
  "SweepExpired の答え(消した行の数)を作るため。"
  (<- removed (purge-expired store gated now-ms))
  (Swept removed))


;; --- handler ------------------------------------------------------------------------------------------------

(defhandler pg-records-handler [#^ PreparedStore store #^ str writer #^ str origin-host #^ bool [gated False]]
  ;; 引数に残す理由: store は用意し終えた置き場(prepare-records-store の答え — 表の用意を済ませた証)で、置き場ごとに違う。
  ;; writer は呼び手が名乗った書き手の名(要求ごとに違う — 書き手の名を effect の引数にしない)。origin-host は行に刻む機体の名で、
  ;; 組み立ての側の設定。gated = 書きを同じ process の同じ錠の単位の書きと 1 本ずつに並べ(AdmitWriters)、並んだ秒と錠を持った秒を
  ;; 計器に積むか(本番の組み立てだけが True で、外側に writer-gates と計器を置く — locked-transaction)。
  "PostgreSQL の置き場の答え手(頭の註)。SqlQuery / SqlTransaction を外側の答え手へ出す。"
  {:tags {:context "records" :role "foundation"}}
  ;; 源の工場の問い(ReadSignalSource)には本番の源 RECORDS-SIGNAL-SOURCE で答える(#3127 — 源の WatchChanges・WatchEvents はこの置き場が答える)。
  (ReadSignalSource []
    (resume RECORDS-SIGNAL-SOURCE))
  ;; 読みの節は回収を流さず、刻を読みの文の条件へ渡す。書きの節(PutRow・PutRows・AppendEvent)も回収を流さず、書きの transaction の中で
  ;; 自分が触る物だけを片付ける。回収は SweepExpired だけ(頭の註・#3605 の D)。
  (ReadRow [table key]
    (<- now (GetTime))
    (<- answer (reached (pg-read-row store effect (epoch-ms now))))
    (resume answer))
  (ListRows [table where fields cursor limit]
    (<- now (GetTime))
    (<- answer (reached (pg-list-rows store effect (epoch-ms now))))
    (resume answer))
  ;; 書きの節は触る名(表 | 列)の合図を、待ちの節は待つ名の呼び鈴を使う(頭の註 — notice-topics)。
  (PutRow [table key value expect]
    (<- now (GetTime))
    (<- topics (notice-topics #(table) #()))
    (<- answer (reached (batched-writing store gated "put-row" #(ROWS-UNIT) topics
                                         (put-row-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (PutRows [writes]
    (<- now (GetTime))
    (<- topics (notice-topics (tuple (gfor write writes write.table)) #()))
    (<- answer (reached (batched-writing store gated "put-rows" #(ROWS-UNIT) topics
                                         (put-rows-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (WatchChanges [tables cursor timeout limit]
    ;; 呼び鈴の名は頼んだ表だけ — 末尾を名指した列(streams)への追記では待ち手を起こさない(tails は答えを返す時点の末尾・#3718)。
    (<- topics (notice-topics (tuple tables) #()))
    (<- answer (wait-for-signal (fn [now-ms] (reached (pg-watch-scan store effect now-ms)))
                                (fn [] (hung-bell store topics)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (WatchEvents [stream after timeout]
    ;; 列の待ちは呼び鈴が鳴るたびの ReadEvents(limit 1)の読み直し(WatchChanges と同じ呼び鈴の仕組みで、待つ名はその列 — 頭の註)。
    (val once (ReadEvents stream :after after :limit 1))
    (<- topics (notice-topics #() #(stream)))
    (<- answer (wait-for-signal (fn [now-ms] (moved-after (reached (pg-read-events store once now-ms))))
                                (fn [] (hung-bell store topics)) (fn [bell] (dropped-bell store bell)) timeout))
    (resume answer))
  (AppendEvent [stream idempotency-key body]
    (<- now (GetTime))
    (<- topics (notice-topics #() #(stream)))
    (<- unit (stream-unit stream))
    (<- answer (reached (batched-writing store gated "append-event" #(unit) topics
                                         (append-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (AppendEvents [appends]
    ;; 束の追記を 1 つの書きの錠の transaction で 2 往復で積み、束が触る列の名を合図する(append-events-locked)。
    (<- now (GetTime))
    (<- topics (notice-topics #() (tuple (gfor one appends one.stream))))
    (var units #())
    (for [one appends]
      (<- unit (stream-unit one.stream))
      (:= units (+ units #(unit))))
    (<- answer (reached (batched-writing store gated "append-events" units topics
                                         (append-events-locked store origin-host writer effect (epoch-ms now)))))
    (resume answer))
  (ReadEvents [stream after limit]
    (<- now (GetTime))
    (<- answer (reached (pg-answer-read-events store effect (epoch-ms now))))
    (resume answer))
  (ReadStreamEnd [stream]
    (<- now (GetTime))
    (<- answer (reached (pg-read-stream-end store effect (epoch-ms now))))
    (resume answer))
  (ReadEventByKey [stream idempotency-key]
    (<- now (GetTime))
    (<- answer (reached (pg-read-event-by-key store effect (epoch-ms now))))
    (resume answer))
  (AdvanceStoreEpoch []
    ;; 検の口: 届かなければ StoreUnreachable を上げる(答えの型は int だけ — 旧い版と同じ)。版は全部の待ち手に関わる(名の分からない合図)。
    (<- epoch (writing store gated "advance-epoch" #() None (advance-epoch-locked store)))
    (resume epoch))
  (SweepExpired []
    (<- now (GetTime))
    (<- answer (reached (swept-count store gated (epoch-ms now))))
    (resume answer))
  (PruneChanges [keep-seconds upto-seq]
    (<- now (GetTime))
    ;; 床は全部の表の待ち手の位置に関わる(名の分からない合図)。
    (<- answer (reached (writing store gated "prune-changes" #(ROWS-UNIT) None (prune-locked store effect (epoch-ms now)))))
    (resume answer))
  (ReadChangeLog [after limit]
    (<- answer (reached (change-log store effect)))
    (resume answer)))

