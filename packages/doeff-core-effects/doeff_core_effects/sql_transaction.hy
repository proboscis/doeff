;;; SqlTransaction の約束 3 つ(sql_effects.hy の頭の註)の実現を 1 か所に置く — postgres-sql-handler と sqlite-sql-handler が同じ手順を使う
;;; (agora-redesign #802 便 3)。
;;;
;;; 実現: 答え手は接続 1 本に束ねた scope(SqlQuery / SqlInsertRows 専用の handler)を program に被せて走らせる。effect.program をそのまま
;;; 走らせると、中の SqlQuery は答え手の外側へ行き、同じ接続・同じ transaction に乗らない(doeff_core_effects.handlers の try_handler が
;;; 内側の handler を被せ直すのと同じ理由)。
;;;   - 中の SqlQuery / SqlInsertRows が失敗の値(SqlFailed / SqlUnreachable)を答えたら、scope は継続を捨てて(program を再開せずに)
;;;     TransactionAborted を答えにする。例外を program へ投げ込むと program が捕まえて続けられるので、投げ込まない(約束 1)。
;;;   - 他の effect は同じく継続を捨てて TransactionMisused を答えにし、run-in-transaction が rollback してから SqlTransactionMisuse を投げる
;;;     (約束 2・入れ子の SqlTransaction も同じ — 約束 3 の入れ子の断り)。
;;;   - program が例外を投げたら rollback して例外を通す。
;;; 継続を捨てる handler は defhandler の終端の検め(どの枝も resume / transfer / reperform / raise で終わる)が受けないので、scope は
;;; 素の handler の関数で書く。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import doeff [do :as program-handler Resume Program])
(import doeff_vm [WithHandler])
(import doeff_core_effects.sql_effects [SqlQuery SqlInsertRows SqlFailed SqlUnreachable SqlTransactionMisuse])


(defrecord TransactionAborted
  "scope が継続を捨てた印: 中の問い合わせが失敗した(failure = その失敗の値)。program が作れない型なので、program の答えと混ざらない。"
  (#^ (| SqlFailed SqlUnreachable) failure))


(defrecord TransactionMisused
  "scope が継続を捨てた印: 中で禁じた effect を出した(reason = 何を出したか)。"
  (#^ str reason))


(defn transaction-scope [#^ str database #^ Callable execute-query #^ Callable execute-insert]  ; defk にできない: 継続を捨てる handler の関数(頭の註)
  "接続 1 本に束ねた scope の handler を作るため。execute-query / execute-insert = (effect) → SqlRows | SqlFailed | SqlUnreachable の Program。"
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
      (| (SqlQuery :database name) (SqlInsertRows :database name))
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で別の database {!r} へ問い合わせた" database name)))
      _
        (return (TransactionMisused :reason (.format "database {!r} の transaction の中で {} を出した(出せるのは SqlQuery と SqlInsertRows と純粋な計算だけ)"
                                                     database (. (type effect) __name__))))))
  scope)


(defk run-in-transaction [database program execute-query execute-insert begin commit rollback]
  {:pre [(: database str) (: program Program) (: execute-query Callable) (: execute-insert Callable) (: begin Callable) (: commit Callable)
         (: rollback Callable)]
   :post [(: % "program の答え | SqlFailed | SqlUnreachable")]
   :tags {:context "sql" :role "foundation"}}
  "program を 1 つの transaction の中で走らせるため(頭の註)。begin / commit = () → None | SqlFailed | SqlUnreachable の Program・rollback = () → None
   の Program(rollback の失敗は答えにしない — 先に起きた事を答える)。"
  (<- began (begin))
  (when (is-not began None)
    (return began))
  (try
    (<- value (WithHandler (transaction-scope database execute-query execute-insert) program))
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
