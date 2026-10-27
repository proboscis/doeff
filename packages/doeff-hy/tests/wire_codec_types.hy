;;; 自前の解き手(doeff_hy/wire_codec.py)の検に使う defwire の型(card ki-923c16d33551)。
;;; 検は test_wire_codec.py — 同じ型を今の pydantic の解き手(doeff_hy.wire の parse-json)にも通して答えを比べる。

(require doeff-hy.record [defwire defrecord defenum])
(require doeff-hy.macros [val])
(import dataclasses [dataclass field])
(import datetime [datetime])
(import enum [StrEnum])
(import typing [Annotated Literal Optional])
(import doeff_hy.json_value [OpaqueJson])


(defenum Color (RED "red") (BLUE "blue"))
(defenum LeaseState (HELD "held") (FREE "free") (GONE "gone"))
(val KIND "plain")

(defwire Note {:names :camel} (#^ str note-text))

;; 試作の測り(artifact 2026-10-27-defwire-base-measurements.html の表 3)の型の形: lease の答えと持ち手の列。
(defwire Holder {:names :camel :unknown :reject} (#^ str holder-id) (#^ float expires-at))
(defwire LeaseAnswer
  {:names :camel :unknown :reject}
  (#^ str lease-id)
  (#^ LeaseState state)
  (setv #^ (get tuple #(Holder ...)) holders #()))

;; 語彙の型を 1 つずつ欄に持つ型(欄は全部既定値を持つ — 検は 1 欄ずつ入れる)。
(defwire Vocab
  {:names :camel :unknown :reject}
  (setv #^ str s ""
        #^ int i 0
        #^ float f 0.0
        #^ bool b False
        #^ (| int None) oi None
        #^ Color color Color.RED
        #^ (get Literal #("x" "y")) lit "x"
        #^ (get Literal KIND) named-lit "plain"
        #^ (get tuple #(int ...)) ti #()
        #^ (get tuple #(int str)) tf #(0 "")
        #^ (get list str) ls (field :default-factory list)
        #^ (get frozenset str) fs (frozenset)
        #^ (get dict #(str int)) di (field :default-factory dict)
        #^ dict bare (field :default-factory dict)
        #^ object any-value None
        #^ OpaqueJson opaque (OpaqueJson.of None)
        #^ Note note (Note :note-text "")
        #^ (| int str) u 0
        #^ (| float int) fi 0
        #^ (| Color str) cs ""
        #^ (| (get list int) (get tuple #(str ...))) arr (field :default-factory list)))

(defwire Required {:names :camel} (#^ str lane-id) (#^ (| int None) landed-at) (setv #^ int n 0))
(defwire Lenient {:names :snake :unknown :ignore} (#^ str lane-id) (setv #^ int n 0))
(defwire Inner {:names :camel :unknown :reject} (#^ int n-val))
(defwire Outer {:names :camel} (#^ Inner inner) (setv #^ (get dict #(str Inner)) by-key (field :default-factory dict)))
(defwire Checked {:names :camel :check [(> n 0)]} (#^ int n))
(defwire HoldsChecked {:names :camel} (#^ Checked k))
(defwire Asserting {:names :camel} (#^ int n)
  (defn __post-init__ [self]
    (assert (< self.n 10) "n は 10 より小さい")))

;; union の腕の選び方(節 3 の規則 3)— :ignore の 2 型・B は既定値の欄 y を持つ。
(defwire ArmA {:names :camel :unknown :ignore} (#^ int x))
(defwire ArmB {:names :camel :unknown :ignore} (#^ int x) (setv #^ int y 0))
(defwire Arms {:names :camel}
  (setv #^ (| ArmA ArmB None) ab None
        #^ (| (get dict #(str int)) ArmA None) da None))

;; 何でも受ける腕と他の腕が混ざった union(節 3 の規則 4 — pydantic と違う答えを正とするので単体の検だけで確かめる)。
(defwire Mixed {:names :camel}
  (setv #^ (| str OpaqueJson None) so None
        #^ (| OpaqueJson ArmA) oa (OpaqueJson.of None)
        #^ (| int object) io 0))

;; union の別名(val の形 — 木を組む時に中身の型として読む)。
(val Scalar (| str int float bool None))
(defwire WithAlias {:names :camel} (#^ Scalar v))

;; 語彙の外 — 木を組む時に断る型。
(defrecord PlainRecord (#^ int n))
(defwire HasDatetime {:names :camel} (#^ datetime at))
(defwire HasAnnotated {:names :camel} (#^ (get Annotated #(int "meta")) n))
(defwire HasPlainRecord {:names :camel} (#^ PlainRecord inner))
(defwire HasIntLiteral {:names :camel} (#^ (get Literal #(1 2)) n))
(defwire HasIntKeys {:names :camel} (#^ (get dict #(int str)) m))
(defwire HasBareList {:names :camel} (#^ list xs))
(defwire SelfRef {:names :camel} (setv #^ (get Optional "SelfRef") child None))
(defwire LoopA {:names :camel} (setv #^ (get Optional "LoopB") b None))
(defwire LoopB {:names :camel} (setv #^ (| LoopA None) a None))
(defwire Unresolved {:names :camel} (#^ "NoSuchType" x))


;; 書き手と JSON Schema の検(test_wire_codec_write.py・card ki-f6551d717654)に使う型。
;; 既定値の形ごとの欄(入れ子の defwire・defenum・frozenset・NaN・OpaqueJson・object・作り手)と、(| T None) の title の付け方。
(defwire Leaf {:names :camel} (#^ str leaf-name) (setv #^ int n 0))
(defwire Defaults
  {:names :camel :unknown :reject}
  (#^ (| Leaf None) maybe-leaf)
  (#^ (| Color None) maybe-color)
  (#^ Color color)
  (setv #^ Leaf leaf (Leaf :leaf-name "a")
        #^ (get tuple #(Leaf ...)) leaves #((Leaf :leaf-name "b" :n 2))
        #^ (get dict #(str Leaf)) by-name (field :default-factory dict)
        #^ (get list (| int None)) holes (field :default-factory list)
        #^ (get tuple #()) empty #()
        #^ (| (get Literal "a") int) lit-or-int 0
        #^ (| None str) none-first None
        #^ (get frozenset int) ids (frozenset #(3 1 2))
        #^ float not-a-number (float "nan")
        #^ float infinite (float "inf")
        #^ Color light Color.BLUE
        #^ (| Color int) color-or-int 1
        #^ OpaqueJson spec (OpaqueJson.of {"k" [1 2]})
        #^ object pair #(1 2)
        #^ dict table (field :default-factory (fn [] {"a" 1}))
        #^ (get Optional (get tuple #(int str))) maybe-pair None))

;; 同じ名の違う型(wire_codec_types_other の Note)を欄に持つ型 — $defs の名に module を付ける。
(import wire_codec_types_other)
(defwire BothNotes {:names :camel} (#^ Note mine) (#^ wire_codec_types_other.Note theirs))

;; 照らしの test(test_wire_parity.py・card ki-b7701d5fc0dd)が見つけた差の型: doc を持つ StrEnum(JSON Schema の description)・
;; 値の型が何でもよい dict(additionalProperties true)。
(defclass Shade [StrEnum]
  "明るさ(照らしの test の見本 — JSON Schema の $defs に description として出る)。"
  (setv LIGHT "light"
        DARK "dark"))
(defwire Documented
  {:names :camel :unknown :reject}
  (setv #^ Shade shade Shade.LIGHT
        #^ (get dict #(str object)) loose (field :default-factory dict)
        #^ (get dict #(str OpaqueJson)) opaque-by-key (field :default-factory dict)))
