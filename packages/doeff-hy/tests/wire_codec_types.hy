;;; Types of defwire used to check the hand-written decoder (doeff_hy/_wire_codec.py).
;;; The checks are in test_wire_codec.py - the same types are also passed through the current pydantic decoder (parse-json in doeff_hy._wire) and the answers compared.

(require doeff-hy.record [defwire defrecord defenum])
(require doeff-hy.macros [val])
(import dataclasses [dataclass field])
(import datetime [datetime])
(import enum [StrEnum])
(import typing [Annotated Literal Optional])
(import doeff_hy._json_value [OpaqueJson])


(defenum Color (RED "red") (BLUE "blue"))
(defenum LeaseState (HELD "held") (FREE "free") (GONE "gone"))
(val KIND "plain")

(defwire Note {:names :camel} (#^ str note-text))

;; Type shapes from the prototype measurement (table 3 of artifact 2026-10-27-defwire-base-measurements.html): a lease answer and a list of handles.
(defwire Holder {:names :camel :unknown :reject} (#^ str holder-id) (#^ float expires-at))
(defwire LeaseAnswer
  {:names :camel :unknown :reject}
  (#^ str lease-id)
  (#^ LeaseState state)
  (setv #^ (get tuple #(Holder ...)) holders #()))

;; A type with one vocabulary type per field (every field has a default - the check feeds in one field at a time).
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
    (assert (< self.n 10) "n must be less than 10")))

;; How a union arm is chosen (rule 3 of section 3) - two :ignore types; B has field y with a default.
(defwire ArmA {:names :camel :unknown :ignore} (#^ int x))
(defwire ArmB {:names :camel :unknown :ignore} (#^ int x) (setv #^ int y 0))
(defwire Arms {:names :camel}
  (setv #^ (| ArmA ArmB None) ab None
        #^ (| (get dict #(str int)) ArmA None) da None))

;; A union mixing an accept-anything arm with other arms (rule 4 of section 3 - the answer that differs from pydantic is taken as correct, so it is checked by unit tests alone).
(defwire Mixed {:names :camel}
  (setv #^ (| str OpaqueJson None) so None
        #^ (| OpaqueJson ArmA) oa (OpaqueJson.of None)
        #^ (| int object) io 0))

;; Alias of a union (the shape of val - read as the content type when the tree is built).
(val Scalar (| str int float bool None))
(defwire WithAlias {:names :camel} (#^ Scalar v))

;; Outside the vocabulary - types rejected when the tree is built.
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


;; Types used for the writer and the JSON Schema check (test_wire_codec_write.py).
;; Fields per default-value shape (nested defwire, defenum, frozenset, NaN, OpaqueJson, object, maker) and how the title of (| T None) is attached.
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

;; A type with fields of different types of the same name (Note in wire_codec_types_other) - the module is attached to the $defs name.
(import wire_codec_types_other)
(defwire BothNotes {:names :camel} (#^ Note mine) (#^ wire_codec_types_other.Note theirs))

;; Types for the differences the parity test (test_wire_parity.py) found: a StrEnum with a doc (the JSON Schema description) and
;; a dict whose value type can be anything (additionalProperties true).
(defclass Shade [StrEnum]
  "Brightness (a sample for the parity test - appears as a description in the JSON Schema $defs)."
  (setv LIGHT "light"
        DARK "dark"))
(defwire Documented
  {:names :camel :unknown :reject}
  (setv #^ Shade shade Shade.LIGHT
        #^ (get dict #(str object)) loose (field :default-factory dict)
        #^ (get dict #(str OpaqueJson)) opaque-by-key (field :default-factory dict)))
