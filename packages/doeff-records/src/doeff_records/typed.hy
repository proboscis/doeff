;;; 表ごとの行の型で読み書きする層 — 汎用の effect(ReadRow / ListRows / PutRow)の上の Program と純関数。
;;;
;;; 汎用の層(values.hy・effects.hy)は行の値を「欄の名 → JSON の値」の凍らせた写像で持つ。表の欄は宣言(TableDecl)が決め、
;;; 置き場の handler(memory・PostgreSQL・写し)は表の中身を知らないので、その層は写像のままにする。業務の呼び手は写像を
;;; 見ずに、表ごとの行の型(pydantic の BaseModel か dataclass)で読み書きする — その写しの 1 か所がこの file。
;;;
;;; 写し方: 行の型 ↔ 行の値は pydantic の TypeAdapter が行う(読む = 型の検め・書く = JSON の値への dump)。欄の名は行の型の
;;; 欄の名(alias が在れば alias)で、表の宣言の欄の名と同じにする。書きは行の全体の像: 行の型の欄を全部載せ、値が None の欄は
;;; 「その欄を消す」(PutRow の差分の None)。値の変わらない欄は書き手の名簿で照らさない(admission)ので、自分の欄だけを変えた
;;; 像を書けばよい。handler は増やさない — どの handler の組の上でも同じに動く。
(require doeff-hy.macros [defk <-])
(import dataclasses)
(import dataclasses [dataclass field])
(import types [NoneType])
(import typing [Generic TypeVar])
(import pydantic [TypeAdapter])
(import doeff_hy.frozen [FrozenMap frozen-json-object thaw-json])
(import doeff_records.values [Row Missing Page Written Conflict RowChanged RowRemoved ListCursor ExpectAbsent
                              ExpectVersion ExpectAny checked-table-name])
(import doeff_records.effects [ReadRow ListRows PutRow DEFAULT-LIST-LIMIT])

(setv M (TypeVar "M"))


(defclass [(dataclass :frozen True)] RowType [(get Generic M)]
  "表 1 つの行の型: table = 表の名 / model = 行の値の型(pydantic の BaseModel か dataclass — 欄の名は表の宣言の欄の名で、
   鍵の欄も含む)。adapter = 写しに使う pydantic の TypeAdapter(作る時に組む)。"
  (#^ str table)
  (#^ (get type M) model)
  (setv #^ (get TypeAdapter M) adapter (field :init False :repr False :compare False))
  (defn __post_init__ [self]
    (checked-table-name self.table "RowType.table")
    (when (not (isinstance self.model type))
      (raise (TypeError (.format "RowType.model は型(pydantic の BaseModel か dataclass): {!r}" self.model))))
    (object.__setattr__ self "adapter" (TypeAdapter self.model))))


(defclass [(dataclass :frozen True)] TypedRow [(get Generic M)]
  "行 1 つ: key = 鍵 / value = 行の型の値 / version = 版。"
  (#^ tuple key)
  (#^ M value)
  (#^ int version))

(defclass [(dataclass :frozen True)] TypedPage [(get Generic M)]
  "list-typed の 1 頁: rows = TypedRow の tuple / next-cursor・epoch・sequence は Page と同じ。"
  (#^ tuple rows)
  (#^ (| ListCursor None) next-cursor)
  (#^ int epoch)
  (#^ int sequence))

(defclass [(dataclass :frozen True)] TypedWritten [(get Generic M)]
  "put-typed が確定した: version = 新しい版 / value = 確定した行の行の型の値。"
  (#^ int version)
  (#^ M value))

(defclass [(dataclass :frozen True)] TypedConflict [(get Generic M)]
  "put-typed の期待が今の行と合わない(Conflict の今の行を行の型にした物)。current = TypedRow | Missing。"
  (#^ (| TypedRow Missing) current))

(defclass [(dataclass :frozen True)] TypedRowChanged [(get Generic M)]
  "変更 1 つ(RowChanged の行の値を行の型にした物)。"
  (#^ str table)
  (#^ tuple key)
  (#^ int version)
  (#^ M value)
  (#^ int sequence))


;; --- 写し(純関数)------------------------------------------------------------------------------------------

(defn #^ tuple model-field-names [#^ type model]
  "純粋: 行の型の欄の名(pydantic の BaseModel は alias が在れば alias・dataclass は欄の名)。"
  (if (dataclasses.is-dataclass model)
      (tuple (gfor f (dataclasses.fields model) f.name))
      (tuple (gfor #(name info) (.items model.model-fields) (or info.alias name)))))


(defn #^ M value-of-fields [#^ (get RowType M) row-type #^ FrozenMap fields]
  "行の値(欄 → JSON の値)→ 行の型の値。書きで値が None の欄は消える(PutRow の差分の None)ので、置き場に無い欄は None と読む —
   既定値の無い None を許す欄(例 = dataclass の `(| str None)` の欄)も書いて読み戻せる。型に合わなければ pydantic の ValidationError
   (置き場の行が型と食い違う — 組み立ての誤り)。"
  (setv value (thaw-json fields))
  (.validate-python row-type.adapter (| (dfor name (model-field-names row-type.model) :if (not-in name value) name None) value)))


(defn #^ FrozenMap fields-of-value [#^ (get RowType M) row-type #^ M value]
  "行の型の値 → 書きの像(行の型の欄を全部・値が None の欄は None = その欄を消す)。"
  (frozen-json-object (.dump-python row-type.adapter value :mode "json" :by-alias True) "行の型の dump"))


(defn #^ (get TypedRow M) typed-row [#^ (get RowType M) row-type #^ Row row]
  (TypedRow row.key (value-of-fields row-type row.value) row.version))


(defn #^ (| TypedRow Missing) typed-current [#^ (get RowType M) row-type #^ (| Row Missing) current]
  "Conflict の今の行(Row | Missing)を行の型へ(TypedConflict の current)。"
  (if (isinstance current Row) (typed-row row-type current) current))


(defn #^ (| TypedRowChanged RowRemoved) typed-change [#^ (get RowType M) row-type #^ (| RowChanged RowRemoved) change]
  "WatchChanges の変更 1 つを行の型へ: RowChanged → TypedRowChanged・RowRemoved はそのまま。
   別の表の変更は ValueError(表ごとに行の型を選ぶのは呼び手)。"
  (when (!= change.table row-type.table)
    (raise (ValueError (.format "表 {} の変更を表 {} の行の型で読もうとした" change.table row-type.table))))
  (if (isinstance change RowChanged)
      (TypedRowChanged change.table change.key change.version (value-of-fields row-type change.value) change.sequence)
      change))


;; --- 読み書き(汎用の effect の上の Program)----------------------------------------------------------------

(defk read-typed [#^ RowType row-type #^ tuple key]
  {:pre [(: row-type RowType) (: key tuple)] :post [(: % "TypedRow | Missing | Unreachable")]}
  "行を鍵で 1 つ読み、行の型で返す。答え = TypedRow | Missing | Unreachable。"
  (<- answer (ReadRow row-type.table key))
  (if (isinstance answer Row) (typed-row row-type answer) answer))


(defk list-typed [#^ RowType row-type * [where None] [cursor None] [limit DEFAULT-LIST-LIMIT]]
  {:pre [(: row-type RowType) (: where (| FrozenMap NoneType)) (: cursor (| ListCursor NoneType)) (: limit int)] :post [(: % "TypedPage | Reset | Unreachable | NotIndexed")]}
  "表の行を 1 頁読み、行の型で返す。where = 欄 → 値(索引の欄の等号の AND・None = 絞らない)/ cursor・limit は ListRows と同じ。
   行の型は欄を全部読むので、返す欄の絞り(ListRows.fields)は使わない。答え = TypedPage | Reset | Unreachable | NotIndexed。"
  (<- answer (ListRows row-type.table :where (if (is where None) (FrozenMap) where) :cursor cursor :limit limit))
  (if (isinstance answer Page)
      (TypedPage (tuple (gfor row answer.rows (typed-row row-type row))) answer.next-cursor answer.epoch answer.sequence)
      answer))


(defk put-typed [#^ (get RowType M) row-type #^ tuple key #^ M value #^ object expect]
  {:pre [(: row-type RowType) (: key tuple) (: value row-type.model) (: expect (| ExpectAbsent ExpectVersion ExpectAny))]
   :post [(: % "TypedWritten | TypedConflict | Refused | Unreachable")]}
  "行の全体の像を書く: value = 行の型の値(値が None の欄は消す)/ expect = ExpectAbsent | ExpectVersion | ExpectAny。
   答え = TypedWritten | TypedConflict | Refused | Unreachable。"
  (<- answer (PutRow row-type.table key (fields-of-value row-type value) expect))
  (cond
    (isinstance answer Written) (TypedWritten answer.version (value-of-fields row-type answer.value))
    (isinstance answer Conflict) (TypedConflict (typed-current row-type answer.current))
    True answer))
