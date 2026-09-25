;;; 記録の仕組みの公開 effect 6 つ(lease は既存の doeff-cluster の LeaseOp / HeldLease を使い、ここには作らない)。
;;;
;;; 書き手の身元は effect の引数にしない — handler を組む時(composition root)に渡す。答えの型は values.hy。
(import dataclasses [dataclass field])
(import doeff [EffectBase])
(import doeff_records.values [ExpectAbsent ExpectVersion ExpectAny Approval WatchCursor ListCursor checked-table-name
                              checked-field-name])

(setv DEFAULT-LIST-LIMIT 500)
(setv DEFAULT-WATCH-LIMIT 1000)
(setv DEFAULT-READ-EVENTS-LIMIT 1000)


(defn #^ tuple checked-key [#^ object key #^ str what]
  (when (not (and (isinstance key tuple) key (all (gfor part key (isinstance part str)))))
    (raise (TypeError (.format "{} は文字列の空でない tuple: {!r}" what key))))
  key)


(defn #^ int checked-limit [#^ object limit #^ str what]
  (when (or (isinstance limit bool) (not (isinstance limit int)) (< limit 1))
    (raise (ValueError (.format "{} は 1 以上の整数: {!r}" what limit))))
  limit)


(defclass [(dataclass :frozen True)] ReadRow [EffectBase]
  "行を鍵で 1 つ読む。答え = Row | Missing | Unreachable。"
  (#^ str table)
  (#^ tuple key)
  (defn __post_init__ [self]
    (checked-table-name self.table "ReadRow.table")
    (checked-key self.key "ReadRow.key")))


(defclass [(dataclass :frozen True)] ListRows [EffectBase]
  "表の行を鍵の綴りの順に 1 頁読む。where = 欄 → 値(索引の欄の等号の AND)/ fields = 返す欄(None = 全部。鍵の欄は常に返す)/
   cursor = 前の頁の next-cursor(None = 最初から)/ limit = 頁の行数の上限。答え = Page | Reset | Unreachable | NotIndexed。"
  (#^ str table)
  (setv #^ dict where (field :default-factory dict))
  (setv #^ (| tuple None) fields None)
  (setv #^ (| ListCursor None) cursor None)
  (setv #^ int limit DEFAULT-LIST-LIMIT)
  (defn __post_init__ [self]
    (checked-table-name self.table "ListRows.table")
    (when (not (isinstance self.where dict)) (raise (TypeError "ListRows.where は dict")))
    (for [name self.where] (checked-field-name name "ListRows.where の欄"))
    (when (is-not self.fields None)
      (for [name self.fields] (checked-field-name name "ListRows.fields の欄")))
    (when (not (or (is self.cursor None) (isinstance self.cursor ListCursor)))
      (raise (TypeError "ListRows.cursor は ListCursor か None")))
    (checked-limit self.limit "ListRows.limit")))


(defclass [(dataclass :frozen True)] PutRow [EffectBase]
  "行を書く。value = 欄の差分(書く欄 → 値。書かない欄は今の値のまま)/ expect = ExpectAbsent | ExpectVersion | ExpectAny /
   approval = 承認の欄を書く時の印(任意)。答え = Written | Conflict | Refused | Unreachable。"
  (#^ str table)
  (#^ tuple key)
  (#^ dict value)
  (#^ object expect)
  (setv #^ (| Approval None) approval None)
  (defn __post_init__ [self]
    (checked-table-name self.table "PutRow.table")
    (checked-key self.key "PutRow.key")
    (when (not (isinstance self.value dict)) (raise (TypeError "PutRow.value は dict(欄 → 値)")))
    (for [name self.value] (checked-field-name name "PutRow.value の欄"))
    (when (not (isinstance self.expect #(ExpectAbsent ExpectVersion ExpectAny)))
      (raise (TypeError "PutRow.expect は ExpectAbsent | ExpectVersion | ExpectAny")))
    (when (not (or (is self.approval None) (isinstance self.approval Approval)))
      (raise (TypeError "PutRow.approval は Approval か None")))))


(defclass [(dataclass :frozen True)] WatchChanges [EffectBase]
  "cursor より後の確定した変更を、頼んだ表の分だけ返す。無ければ timeout 秒まで待つ。limit = 1 回に返す変更の上限。
   答え = Changes | Reset(cursor の epoch が置き場の版と違う)| Unreachable。"
  (#^ tuple tables)
  (#^ WatchCursor cursor)
  (setv #^ float timeout 0.0)
  (setv #^ int limit DEFAULT-WATCH-LIMIT)
  (defn __post_init__ [self]
    (when (not (and (isinstance self.tables tuple) self.tables)) (raise (TypeError "WatchChanges.tables は空でない tuple")))
    (for [name self.tables] (checked-table-name name "WatchChanges.tables の表"))
    (when (not (isinstance self.cursor WatchCursor)) (raise (TypeError "WatchChanges.cursor は WatchCursor")))
    (when (or (isinstance self.timeout bool) (not (isinstance self.timeout #(int float))) (< self.timeout 0))
      (raise (ValueError (.format "WatchChanges.timeout は 0 以上の秒: {!r}" self.timeout))))
    (checked-limit self.limit "WatchChanges.limit")))


(defclass [(dataclass :frozen True)] AppendEvent [EffectBase]
  "追記の列に出来事を 1 つ積む。同じ冪等キーの再送は前の sequence を返す(本文が違えば Refused)。
   答え = Appended | Refused | Unreachable。"
  (#^ str stream)
  (#^ str idempotency-key)
  (#^ object body)
  (defn __post_init__ [self]
    (checked-table-name self.stream "AppendEvent.stream")
    (when (not (and (isinstance self.idempotency-key str) self.idempotency-key))
      (raise (ValueError "AppendEvent.idempotency_key は空でない文字列")))))


(defclass [(dataclass :frozen True)] ReadEvents [EffectBase]
  "追記の列の after より後の出来事を limit まで読む。答え = Events | Unreachable。"
  (#^ str stream)
  (setv #^ int after 0)
  (setv #^ int limit DEFAULT-READ-EVENTS-LIMIT)
  (defn __post_init__ [self]
    (checked-table-name self.stream "ReadEvents.stream")
    (when (or (isinstance self.after bool) (not (isinstance self.after int)) (< self.after 0))
      (raise (ValueError (.format "ReadEvents.after は 0 以上の整数: {!r}" self.after))))
    (checked-limit self.limit "ReadEvents.limit")))
