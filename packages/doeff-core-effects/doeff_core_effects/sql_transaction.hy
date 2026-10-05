;;; SqlTransaction の約束 3 つ(sql_effects.hy の頭の註)の実現を 1 か所に置く — PostgreSQL の答え手 2 つと sqlite の答え手 2 つが同じ手順を
;;; 使う(agora-redesign #802 便 3)。手順は 2 つで、transaction を開く宣言(SqlTransaction の欄 batched)で選ぶ:
;;;   run-in-transaction          既定(batched = False)。文 1 つを 1 回ずつ流す: BEGIN → 錠 → program の文を出た順に 1 つずつ → SqlNotify も
;;;                               出た時にその場で → COMMIT / ROLLBACK。SQL を出さない program でも BEGIN・錠・COMMIT を流す。
;;;   run-in-batched-transaction  batched = True を選んだ transaction だけ(agora-redesign #3605 — 記録の service と DB が別の機体に在ると、DB への
;;;                               往復 1 回が書き 1 回の待ちに乗る)。往復をまとめる: 下の「束ねた transaction」。
;;;
;;; 実現: 答え手は接続 1 本に束ねた scope(SqlQuery / SqlInsertRows 専用の handler)を program に被せて走らせる。effect.program をそのまま
;;; 走らせると、中の SqlQuery は答え手の外側へ行き、同じ接続・同じ transaction に乗らない(doeff_core_effects.handlers の try_handler が
;;; 内側の handler を被せ直すのと同じ理由)。
;;;   - 中の SqlQuery / SqlInsertRows が失敗の値(SqlFailed / SqlUnreachable)を答えたら、scope は継続を捨てて(program を再開せずに)
;;;     TransactionAborted を答えにする。例外を program へ投げ込むと program が捕まえて続けられるので、投げ込まない(約束 1)。
;;;   - 他の effect は同じく継続を捨てて TransactionMisused を答えにし、run-in-transaction が rollback してから SqlTransactionMisuse を投げる
;;;     (約束 2・入れ子の SqlTransaction も同じ — 約束 3 の入れ子の断り)。SqlBatch は batched を選んだ transaction の中でだけ出せるので、
;;;     選んでいない transaction の中の SqlBatch も名指して断る。
;;;   - program が例外を投げたら rollback して例外を通す。
;;;   - SqlNotify(同じ database への合図)は、答え手が execute-notify を渡した時だけ同じ接続で流す(PostgreSQL の答え手 — commit した時だけ
;;;     届く)。渡さない答え手(sqlite)の下では他の禁じた effect と同じく TransactionMisused。
;;; 継続を捨てる handler は defhandler の終端の検め(どの枝も resume / transfer / reperform / raise で終わる)が受けないので、scope は
;;; 素の handler の関数で書く。
;;;
;;; 束ねた transaction(run-in-batched-transaction): 答え手は transaction の中の文を、1 回の往復でまとめて流す物 TransactionFlush(流す順に:
;;; opening = BEGIN と錠・requests = 文・notices = 合図・closing = COMMIT)で受けて答える(PostgreSQL = 1 つの pipeline・sqlite = 順に流す)。
;;;   - 始まり: BEGIN と錠を最初の文(SqlQuery・SqlInsertRows・SqlBatch)まで遅らせ、その文と同じ往復で流す。
;;;   - 合図: SqlNotify は覚えるだけで(答えは None)、COMMIT と同じ往復で流す — 合図は commit した時だけ届くので、流す時を遅らせても
;;;     届き方は変わらない。合図の文が落ちれば、その往復の失敗が transaction の答えになる(約束 1)。
;;;   - 終わり: commit = True の SqlBatch は、その文と COMMIT を同じ往復で流す。その後に program が SQL の effect を出せば断る(約束 2)。
;;;     program が commit の束を出さずに終われば、COMMIT(と覚えた合図)だけの往復を流す。SQL を 1 つも出さずに終われば BEGIN も COMMIT も
;;;     流さない(往復 0)。commit の束の後に program が例外を投げても、commit は戻らない(例外は通す)。
;;;   - 往復が落ちたら(BEGIN・錠・COMMIT を含むどの文でも)、BEGIN を流していれば ROLLBACK を流す。
;;;   - 進み(BEGIN を流したか・COMMIT を流したか・覚えた合図)は transaction 1 つに 1 つの TransactionProgress に置き、書き換えるのは scope
;;;     だけ。次の往復を作るのは純関数 planned-flush。
;;; transaction の外の SqlBatch は、答え手が stray-batch の断りを投げる(束は束ねた transaction の中の往復をまとめる物 — 外では意味を持たない)。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable Generator])
(import dataclasses [dataclass])
(import doeff [do :as program-handler Resume Program EffectBase])
(import doeff_vm [WithHandler K])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlBatch SqlNotify SqlRows SqlFailed SqlUnreachable SqlTransactionMisuse T])


(defrecord TransactionAborted
  "scope が継続を捨てた印: 中の問い合わせが失敗した(failure = その失敗の値 — 束ねた transaction では最初に落ちた文の失敗)。program が作れない
   型なので、program の答えと混ざらない。"
  (#^ (| SqlFailed SqlUnreachable) failure))


(defrecord TransactionMisused
  "scope が継続を捨てた印: 中で禁じた effect を出した(reason = 何を出したか)。"
  (#^ str reason))


(defn #^ Callable transaction-scope [#^ str database #^ Callable execute-query #^ Callable execute-insert #^ (| Callable None) execute-notify]  ; defk にできない: 継続を捨てる handler の関数(頭の註)
  "接続 1 本に束ねた scope の handler を作るため。execute-query / execute-insert = (effect) → SqlRows | SqlFailed | SqlUnreachable の Program・
   execute-notify = (effect) → SqlRows | SqlFailed | SqlUnreachable の Program か None(合図を受けない答え手 — 成功は None で再開する)。"
  (defn [program-handler] scope [effect k]  ; defk にできない: handler の関数(effect と継続 k を受ける)
    (match effect
      (SqlQuery :database name) :if (= name database)
        (do (setv answer (yield (execute-query effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k answer)))))
      (SqlInsertRows :database name) :if (= name database)
        (do (setv answer (yield (execute-insert effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k answer)))))
      (SqlNotify :database name) :if (and (= name database) (is-not execute-notify None))
        (do (setv answer (yield (execute-notify effect)))
            (if (isinstance answer #(SqlFailed SqlUnreachable))
                (return (TransactionAborted :failure answer))
                (return (yield (Resume k None)))))
      (SqlBatch :database name) :if (= name database)
        (return (TransactionMisused :reason (.format (+ "database {!r} の transaction は batched を選んでいないので SqlBatch を出せない"
                                                        "(SqlTransaction の :batched True で開いた transaction の中でだけ出せる)")
                                                     database)))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlNotify :database name) (SqlBatch :database name)) :if (!= name database)
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で別の database {!r} へ問い合わせた" database name)))
      _
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で {} を出した(出せるのは SqlQuery と SqlInsertRows と — 答え手が受ければ — SqlNotify と純粋な計算だけ)"
                                                     database (. (type effect) __name__))))))
  scope)


(defk run-in-transaction [database program execute-query execute-insert begin commit rollback [execute-notify None]]
  {:pre [(: database str) (: program Program) (: execute-query Callable) (: execute-insert Callable) (: begin Callable) (: commit Callable)
         (: rollback Callable) (: execute-notify (| Callable None))]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "program を 1 つの transaction の中で、文 1 つを 1 回ずつ流して走らせるため(既定の手順 — 頭の註)。begin / commit = () → None | SqlFailed |
   SqlUnreachable の Program・rollback = () → None の Program(rollback の失敗は答えにしない — 先に起きた事を答える)。"
  (<- began (begin))
  (when (is-not began None)
    (return began))
  (try
    (<- value (WithHandler (transaction-scope database execute-query execute-insert execute-notify) program))
    (except [Exception]
      (<- (rollback))
      (raise)))
  (match value
    (TransactionAborted :failure failure)
      (do (<- (rollback)) failure)
    (TransactionMisused :reason reason)
      (do (<- (rollback)) (raise (SqlTransactionMisuse reason)))
    _
      (do (<- committed (commit))
          (if (is committed None) value committed))))


;; --- 束ねた transaction(batched = True を選んだ transaction だけ — 頭の註)---------------------------------------------------------

(defrecord TransactionFlush
  "束ねた transaction で、答え手が 1 回の往復で流す物(流す順に — 頭の註): opening = 先に BEGIN と錠を流す / requests = 文(SqlQuery |
   SqlInsertRows — 答えはこの順の SqlRows の tuple)/ notices = 出す合図(SqlNotify の順 — channel と関わる名・closing の時だけ載る)/
   closing = 最後に COMMIT を流す。"
  (#^ bool opening)
  (#^ (get tuple #((| SqlQuery SqlInsertRows) ...)) requests)
  (#^ (get tuple #(SqlNotify ...)) notices)
  (#^ bool closing))


;; 束ねた transaction の scope が流した Program から受ける値(次の往復の計画・往復の答え・再開した program の答え T — T は sql_effects の
;; SqlTransaction の答えの型の変数)。
(val ScopeReceived (| TransactionFlush tuple SqlFailed SqlUnreachable T None))


(defclass [(dataclass :kw-only True)] TransactionProgress []
  "束ねた transaction 1 つの進み(頭の註 — 書き換えるのは scope だけなので値の型ではない): opened = BEGIN を流した(流そうとした)/ closed =
   COMMIT を流した(流そうとした — 後の SQL の effect は断る)/ notices = まだ流していない合図(SqlNotify の順)。"
  (setv #^ bool opened False)
  (setv #^ bool closed False)
  (setv #^ (get tuple #(SqlNotify ...)) notices #()))


(defk planned-flush [progress requests closing]
  {:pre [(: progress TransactionProgress) (: requests tuple) (: closing bool)] :post [(: % (| TransactionFlush None))]
   :tags {:context "sql" :role "foundation"}}
  "束ねた transaction で、requests(closing なら最後に COMMIT)を流す次の往復を作るため(頭の註): BEGIN をまだ流していなければ同じ往復の先頭に
   BEGIN と錠を・closing なら覚えた合図を載せる。流す物が無ければ None — 文の無い束と、BEGIN も合図も流していない transaction の commit
   (往復 0)。"
  (cond
    (and (not requests) (not closing)) None
    (and (not requests) (not progress.opened) (not progress.notices)) None
    True (TransactionFlush :opening (not progress.opened) :requests requests :notices (if closing progress.notices #()) :closing closing)))


(defk stray-batch [database]
  {:pre [(: database str)] :post [(: % SqlTransactionMisuse)]
   :tags {:context "sql" :role "foundation"}}
  "transaction の外で出した SqlBatch の断り(答え手が投げる — 頭の註)を作るため。"
  (SqlTransactionMisuse (.format "database {!r} への SqlBatch を transaction の外で出した(SqlBatch は SqlTransaction の :batched True で開いた transaction の中でだけ出せる)"
                                 database)))


(defn #^ Callable batched-transaction-scope [#^ str database #^ Callable flush #^ bool accepts-notices #^ TransactionProgress progress]  ; defk にできない: 継続を捨てる handler の関数(頭の註)
  "束ねた transaction の、接続 1 本に束ねた scope の handler を作るため。flush = (TransactionFlush) → requests と同じ順の SqlRows の tuple |
   最初の失敗(SqlFailed | SqlUnreachable)の Program・accepts-notices = SqlNotify を覚えて COMMIT の往復で流すか・progress = この transaction の
   進み(scope だけが書き換える)。"
  (defn [program-handler] #^ (get Generator #(Program ScopeReceived (| TransactionAborted TransactionMisused T))) scope [#^ EffectBase effect #^ K k]  ; defk にできない: handler の関数(effect と継続 k を受ける)
    (match effect
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name) (SqlNotify :database name))
        :if (and (= name database) progress.closed)
        (return (TransactionMisused :reason (.format "database {!r} の transaction は commit の束で終わった後に {} を出した"
                                                     database (. (type effect) __name__))))
      (SqlNotify :database name) :if (and (= name database) accepts-notices)
        (do (setv progress.notices (+ progress.notices #(effect)))
            (return (yield (Resume k None))))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name)) :if (= name database)
        (do (setv batch (isinstance effect SqlBatch)
                  closing (and batch effect.commit)
                  planned (yield (planned-flush progress (if batch effect.queries #(effect)) closing)))
            (when (is planned None)
              (setv progress.closed (or progress.closed closing))
              (return (yield (Resume k #()))))
            (setv progress.opened True
                  progress.closed closing
                  progress.notices (if closing #() progress.notices)
                  answer (yield (flush planned)))
            (cond
              (isinstance answer #(SqlFailed SqlUnreachable)) (return (TransactionAborted :failure answer))
              batch (return (yield (Resume k answer)))
              True (return (yield (Resume k (get answer 0))))))
      (| (SqlQuery :database name) (SqlInsertRows :database name) (SqlBatch :database name) (SqlNotify :database name)) :if (!= name database)
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で別の database {!r} へ問い合わせた" database name)))
      _
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で {} を出した(出せるのは SqlQuery と SqlInsertRows と SqlBatch と — 答え手が受ければ — SqlNotify と純粋な計算だけ)"
                                                     database (. (type effect) __name__))))))
  scope)


(defk run-in-batched-transaction [database program flush rollback [accepts-notices False]]
  {:pre [(: database str) (: program Program) (: flush Callable) (: rollback Callable) (: accepts-notices bool)]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "batched = True を選んだ transaction の program を、往復をまとめて走らせるため(頭の註の「束ねた transaction」)。flush = (TransactionFlush) →
   requests と同じ順の SqlRows の tuple | 最初の失敗の Program・rollback = () → None の Program(BEGIN を流した時だけ呼ぶ。rollback の失敗は
   答えにしない — 先に起きた事を答える)。"
  (val progress (TransactionProgress))
  (try
    (<- value (WithHandler (batched-transaction-scope database flush accepts-notices progress) program))
    (except [Exception]
      (when progress.opened
        (<- (rollback)))
      (raise)))
  (match value
    (TransactionAborted :failure failure)
      (do (when progress.opened
            (<- (rollback)))
          failure)
    (TransactionMisused :reason reason)
      (do (when progress.opened
            (<- (rollback)))
          (raise (SqlTransactionMisuse reason)))
    _
      (do (when progress.closed
            (return value))
          (<- planned (planned-flush progress #() True))
          (if (is planned None)
              value
              (do (<- committed (flush planned))
                  (if (isinstance committed tuple)
                      value
                      (do (<- (rollback))
                          committed)))))))
