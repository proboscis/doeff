;;; 表ごとの行の型で読み書きする層 — 汎用の effect(ReadRow / ListRows / PutRow)の上の Program と純関数。
;;;
;;; 汎用の層(_values.hy・_effects.hy)は行の値を「欄の名 → JSON の値」の凍らせた写像で持つ。表の欄は宣言(TableDecl)が決め、
;;; 置き場の handler(memory・PostgreSQL・写し)は表の中身を知らないので、その層は写像のままにする。業務の呼び手は写像を
;;; 見ずに、表ごとの行の型(素の defrecord — defwire でない frozen の dataclass)で読み書きする — その写しの 1 か所がこの file。
;;;
;;; 写し方: 行の型 ↔ 行の値は doeff_hy._wire_codec の行の解き手(row-codec-of — pydantic を読まない)が行う(読む = 型の strict な検め・
;;; 書く = JSON の値への書き出し)。欄の名は行の型の Python の欄の名で、表の宣言の欄の名と同じにする。知らない欄は読み捨てる(欄を
;;; 足した新しい書き手の行を古い読み手が断らない)。欄ごとの判じ方は defwire と同じ strict(文字列を数にしない・真偽を数にしない・
;;; 整数は float の欄に入る・配列は tuple の欄)— 設計の記録 docs/design/defwire-decoder/design.md の節 6。
;;; 型に合わない行は、純関数 row-value が Malformed の値で返し、行を型へ読む他の関数と Program は RowMalformed(ValueError — 同じ
;;; Malformed を運ぶ)を投げる(置き場の行が型と食い違う — 組み立ての誤り。行を読み飛ばす読み手は row-value か RowMalformed を読む)。書きは行の全体の像: 行の型の欄を全部載せ、値が None の欄は
;;; 「その欄を消す」(PutRow の差分の None)。値の変わらない欄は書き手の名簿で照らさない(admission)ので、自分の欄だけを変えた
;;; 像を書けばよい。handler は増やさない — どの handler の組の上でも同じに動く。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "records" :role "program"})
(import dataclasses)
(import dataclasses [dataclass])
(import functools)
(import types [NoneType])
(import typing [Generic TypeVar])
(import doeff_hy._frozen [FrozenMap frozen-json-object])
(import doeff_hy._wire_codec [Codec row-codec-of])
(import doeff_hy._wire_malformed [Malformed])
(import doeff_records._values [Row Missing Page Written Conflict RowChanged RowRemoved ListCursor WatchCursor ExpectAbsent
                              ExpectVersion ExpectAny checked-table-name])
(import doeff_records._effects [ReadRow ListRows PutRow DEFAULT-LIST-LIMIT])

(setv M (TypeVar "M"))


(defclass [(dataclass :frozen True)] RowType [(get Generic M)]
  "表 1 つの行の型: table = 表の名 / model = 行の値の型(素の defrecord — 欄の名は表の宣言の欄の名で、鍵の欄も含む)。
   codec = 写しに使う行の解き手(doeff_hy._wire_codec の row-codec-of — 最初に写す時に組んで型に覚える)。"
  (#^ str table)
  (#^ (get type M) model)
  (defn __post_init__ [self]
    (checked-table-name self.table "RowType.table")
    (when (not (and (isinstance self.model type) (dataclasses.is-dataclass self.model)))
      (raise (TypeError (.format "RowType.model は行の型(素の defrecord): {!r}" self.model)))))
  (defn [functools.cached-property] #^ (get Codec M) codec [self]
    ;; 作る時に組まない: 行の型は使い手の module の読み込みの時に作られ、写さない表の分まで読み込みの固定費に載る
    ;; (使い手の画面の検で 29 表・ki-dd5358f7eb46)。
    (row-codec-of self.model)))


(defclass RowMalformed [ValueError]
  "置き場の行が行の型に合わない(組み立ての誤り — 行を型へ読む関数と Program が投げる)。table = 表の名 / malformed = 合わない所
   (row-value が返す Malformed と同じ値)。ValueError の派生にする: 以前の pydantic の ValidationError も ValueError の派生だった。"
  (defn __init__ [self #^ str table #^ Malformed malformed]
    (.__init__ (super) (.format "表 {} の行が行の型 {} に合わない: {}" table malformed.wire-type
                                (.join "; " (gfor f malformed.fields (.format "{}: {}" (or f.field "(全体)") f.reason)))))
    (setv self.table table
          self.malformed malformed)))


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
  "put-typed が確定した: version = 新しい版 / value = 確定した行の行の型の値 / position = その書きの変更の位置(Written.position と同じ —
   None = 位置を持たない答え)。"
  (#^ int version)
  (#^ M value)
  (setv #^ (| WatchCursor None) position None))

(defclass [(dataclass :frozen True)] TypedConflict [(get Generic M)]
  "put-typed の期待が今の行と合わない(Conflict の今の行を行の型にした物)。current = TypedRow | Missing。"
  (#^ (| TypedRow Missing) current))

(defclass [(dataclass :frozen True)] TypedRowChanged [(get Generic M)]
  "変更 1 つ(RowChanged の行の値を行の型にした物)。"
  (#^ str table)
  (#^ tuple key)
  (#^ int version)
  (#^ M value)
  (#^ int sequence)
  (#^ int at))


;; --- 写し(純関数)------------------------------------------------------------------------------------------

(defn [functools.cache] #^ tuple model-field-names [#^ type model]
  "純粋: 行の型の欄の名(dataclass の欄の名)。
   型ごとに 1 回だけ数える(行を型へ写す読みのたびに dataclasses.fields を回すと、2026-09-28 の実測で自動処理の係の模擬の検 1 本
   26 万回・本体の 1 割になった — 型の欄は作った後に変わらない)。"
  (tuple (gfor f (dataclasses.fields model) f.name)))


(defn #^ (| M Malformed) row-value [#^ (get RowType M) row-type #^ FrozenMap fields]
  "純粋: 行の値(欄 → JSON の値)→ 行の型の値か Malformed(行が行の型に合わない — 行を読み飛ばす読み手が読む)。書きで値が None の
   欄は消える(PutRow の差分の None)ので、置き場に無い欄は None と読む — 既定値の無い None を許す欄(例 = `(| str None)` の欄)も
   書いて読み戻せる。置き場に無い欄の型が None を許さなければ Malformed(欄の型の誤り)。"
  (setv missing (tuple (gfor name (model-field-names row-type.model) :if (not-in name fields) name)))
  (.decode row-type.codec (if missing (| (dict.fromkeys missing) (dict fields)) fields)))


(defn #^ M value-of-fields [#^ (get RowType M) row-type #^ FrozenMap fields]
  "行の値(欄 → JSON の値)→ 行の型の値(row-value と同じ読み)。型に合わなければ RowMalformed(置き場の行が型と食い違う —
   組み立ての誤り)。"
  (setv value (row-value row-type fields))
  (when (isinstance value Malformed)
    (raise (RowMalformed row-type.table value)))
  value)


(defn #^ FrozenMap fields-of-value [#^ (get RowType M) row-type #^ M value]
  "行の型の値 → 書きの像(行の型の欄を全部・既定値と等しい欄も・値が None の欄は None = その欄を消す)。"
  (frozen-json-object (.encode row-type.codec value) "行の型の書き出し"))


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
      (TypedRowChanged change.table change.key change.version (value-of-fields row-type change.value) change.sequence
                       change.at)
      change))


;; --- 読み書き(汎用の effect の上の Program)----------------------------------------------------------------

(defk read-typed [#^ RowType row-type #^ tuple key]
  {:pre [(: row-type RowType) (: key tuple)] :post [(: % "TypedRow | Missing | Unreachable")]}
  "行を鍵で 1 つ読み、行の型で返す。答え = TypedRow | Missing | Unreachable。"
  (<- answer (ReadRow row-type.table key))
  (if (isinstance answer Row) (typed-row row-type answer) answer))


(defk list-typed [#^ RowType row-type * [where None] [cursor None] [limit DEFAULT-LIST-LIMIT] [where-any None]]
  {:pre [(: row-type RowType) (: where (| FrozenMap NoneType)) (: cursor (| ListCursor NoneType)) (: limit int)
         (: where-any (| FrozenMap NoneType))]
   :post [(: % "TypedPage | Reset | Unreachable | NotIndexed")]}
  "表の行を 1 頁読み、行の型で返す。where = 欄 → 値(索引の欄の等号の AND・None = 絞らない)/ where-any = 欄 → 値の組(行の欄の値が組の
   どれかに等しい行 — ListRows.where-any と同じ・None = 絞らない)/ cursor・limit は ListRows と同じ。
   行の型は欄を全部読むので、返す欄の絞り(ListRows.fields)は使わない。答え = TypedPage | Reset | Unreachable | NotIndexed。"
  (<- answer (ListRows row-type.table :where (if (is where None) (FrozenMap) where) :cursor cursor :limit limit
                       :where-any (if (is where-any None) (FrozenMap) where-any)))
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
    (isinstance answer Written) (TypedWritten answer.version (value-of-fields row-type answer.value) answer.position)
    (isinstance answer Conflict) (TypedConflict (typed-current row-type answer.current))
    True answer))
