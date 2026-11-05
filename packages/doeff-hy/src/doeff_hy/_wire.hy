;;; Runtime of defwire -- a generic decoder that converts between JSON values and values of declared types (defwire types).
;;;
;;; Decision: do not let callers use JsonValue directly; decode and validate it the way pydantic does. To that end, a dedicated def* macro is provided
;;; (option A: a separate macro, defwire).
;;;
;;; What it does: JSON is decoded and validated into a value of a declared type in one pass by doeff's own decoder (the type tree in doeff_hy/_wire_codec.py). This module
;;; does not import pydantic (it replaced pydantic's TypeAdapter; pydantic remains only as a
;;; counterpart for cross-checking, and the cross-check lives in doeff_hy/_wire_parity.py and packages/doeff-hy/tests/test_wire_parity.py). The only places allowed to
;;; touch JSON values (JsonValue) are this module's parse / dump and the foundation modules that do the actual sending and receiving (doeff-linter DOEFF120).
;;; protocol, intent, and core see only typed values. Do not write functions that take a JsonValue apart by hand.
;;;
;;;   (<- row (parse LandingRow raw))       ; decode and validate -- the answer is a LandingRow value or Malformed
;;;   (<- row (parse-json LandingRow text)) ; from a JSON string (bytes too)
;;;   (<- m (parse-as (get dict #(str OpaqueJson)) raw))  ; into a container type that is not a defwire (the answer is a value of that type or Malformed)
;;;   (<- m (parse-json-as (get list str) text))           ; likewise from a JSON string
;;;   (<- raw (dump row))                   ; to a JSON value (only the foundation that sends it out)
;;;   (<- schema (json-schema LandingRow))  ; JSON Schema (for checking against contract files)
;;;
;;; Arbitrary JSON whose shape the caller decides (tool arguments and results, memo values of durable runs) is carried not as JsonValue but as OpaqueJson (doeff_hy._json_value)
;;; -- a named type with no opening for taking it apart. If written in a defwire field, the decoder wraps and unwraps it. A reader that knows the shape reads it with (parse T opaque).
;;;
;;; How decoding works (the defwire expansion calls this module's wire-shape to put the wire shape on the type, and the type tree is built only once, on first use -- design record
;;; docs/design/defwire-decoder/design.md):
;;;   * Type checking is strict (JSON semantics): strings do not become numbers, booleans do not become numbers, an integer is accepted in a float field,
;;;     an array is accepted in a tuple field, and a defenum field is read by the spelling of its value.
;;;   * Field names accept only the wire names decided by defwire's :names (not the Python field names).
;;;   * Unknown fields are rejected with :unknown :reject, and read and discarded with :ignore.
;;;   * A defrecord's :check runs when the value is built, and if it fails the result is Malformed.
;;;   * Nested field types must also be defwire types (a plain defrecord has no wire shape of its own, so it is rejected when the tree is built).
;;;
;;; Writing (dump / dump-json): a field with a default value is an "omittable field" (it does not enter required in JSON Schema either). A field whose value equals its default is not written
;;; (the same for nested defwire-typed fields) -- the None in `(setv #^ (| str None) x None)` means "no field" and null is not written. Reading (parse)
;;; fills missing fields with the default, so dump and parse round-trip. A field with no default is written even when None (as null).
;;; A writer that needs "a field not written = deleted", such as the full picture of a table row, lays out all the wire names of the type's fields as None and overlays the dump on top
;;; (the fields' wire names are the type's __doeff_wire__ names). Origin: the nested agent declaration in the settings row of the screen (the contract
;;; represents an arbitrary field by the field's absence, not by null).
;;;
;;; A JSON answer of the wrong shape = Malformed (the fifth kind of failure -- the outside world returned something other than what was promised. A mismatch in the peer's version can happen in operation,
;;; so it is treated as a business failure, not as an implementation-bug exception). Until stage 2 of Absent / Raise lands on the main line, parse returns Malformed as a value
;;; (the answer type = (| T Malformed)), and the switch in stage 3 moves it to Raise(Malformed) (ADR-DOE-HY-007 R8, ADR-DOE-CORE-EFFECTS-003 R17).
;;;
;;; The type decoder is WireShape's codec (Codec in doeff_hy._wire_codec). The former pydantic TypeAdapter (WireShape's adapter) is gone -- places that read it
;;; directly move to the codec or the 5 APIs above. A defwire type still carries __pydantic_config__ (wire-config -- a config dict built without importing pydantic):
;;; if it were removed, readers that decode defwire types directly with pydantic's TypeAdapter (about 20 files in agora-controllers) would silently start reading with the Python
;;; field names and loose checking. When those readers move to the wire API, it is removed together with the expansion's setattr.
(require doeff-hy.macros [defk deff val <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import types)
(import typing [ClassVar Protocol runtime-checkable])
(import doeff_hy._frozen [FrozenMap])
(import doeff_hy._json_value [JsonValue OpaqueJson])
(import doeff_hy._wire_codec [codec-of])
;; Malformed and MalformedField live in a home that does not import pydantic (the in-house decoder wire_codec.py returns the same types).
(import doeff_hy._wire_malformed [Malformed MalformedField])

;; Spelling of how unknown fields are handled (the value of defwire's :unknown) -> pydantic's extra (read only by __pydantic_config__).
(setv UNKNOWN-FIELDS {"reject" "forbid" "ignore" "ignore"})

;; Spelling forms of the type that parse-as accepts: plain types, defenum, defwire types (type), generic types such as (get dict #(str T)) (GenericAlias),
;; and unions such as (| T None) (UnionType).
(setv SHAPE-FORMS (| type types.GenericAlias types.UnionType))


(defrecord WireShape
  "One defwire type's wire shape: names = Python field name -> wire field name (an index of all fields, by name) / unknown = how unknown fields are handled
   (\"reject\" / \"ignore\") / wire-type = that type. The decoder (type tree) is built by codec only once on first use and remembered on the type."
  {:tags {:context "wire" :role "type"}
   :check [(in unknown UNKNOWN-FIELDS)]}
  (#^ (get dict #(str str)) names)
  (#^ str unknown)
  (#^ type wire-type)

  (defn [property] codec [self]
    "The decoder for that type (Codec in doeff_hy._wire_codec -- builds the type tree once on first use and not again from the second use)."
    (codec-of self.wire-type)))


(defclass [runtime-checkable] WireValue [Protocol]
  "A value of a type built by defwire (the type has a WireShape in __doeff_wire__) -- what dump accepts."
  (#^ (get ClassVar WireShape) __doeff-wire__))


(deff wire-config [names unknown]  ; cannot be a defk: called when the defwire expansion loads the module (a place where a Program cannot be run)
  {:pre [(: names dict) (: unknown str) (in unknown UNKNOWN-FIELDS)]
   :post [(: % dict)]
   :tags {:context "wire" :role "judgment"}}
  "The defwire type's __pydantic_config__ (settings so that readers that decode defwire types directly with pydantic's TypeAdapter decode with the wire names and the strictness --
   a plain dict with the same keys as pydantic's ConfigDict, built without importing pydantic. The cross-check tool wire_parity.py also builds pydantic's decoder with this config).
   This module's API does not use it. After the readers move to the wire API, it is removed together with the defwire expansion's setattr."
  {"alias_generator" (. names __getitem__)
   "validate_by_alias" True
   "validate_by_name" False
   "serialize_by_alias" True
   "extra" (get UNKNOWN-FIELDS unknown)
   "strict" True
   "defer_build" True})


(deff wire-shape [wire-type names unknown]  ; cannot be a defk: called when the defwire expansion loads the module (a place where a Program cannot be run)
  {:pre [(: wire-type type) (: names dict) (: unknown str)]
   :post [(: % WireShape)]
   :tags {:context "wire" :role "judgment"}}
  "The defwire type's wire shape (put on the type's __doeff_wire__). The decoder is not built here -- it is built only once, at the first parse / dump / json-schema
   (the cost of building even unused types is not paid in every process that loads the module)."
  (WireShape :names names :unknown unknown :wire-type wire-type))


(defk parse [wire-type raw]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: raw (| JsonValue FrozenMap tuple OpaqueJson))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "Decode and validate a JSON value (frozen JSON -- FrozenMap, tuple -- and also OpaqueJson, carried without reading its contents) into a value of the defwire type wire-type.
   The answer is a wire-type value, or Malformed when the shape does not match (which type, which field, and why). The value goes straight through the tree without being rewritten to a string,
   and the answer is the same as passing the same value as a JSON string to parse-json (an array goes into a tuple field, and a value's spelling goes into a defenum field). OpaqueJson is
   the only opening through which a reader that knows the shape reads the contents (OpaqueJson in json_value.py)."
  (.decode (. wire-type __doeff_wire__ codec) raw))


(defk parse-json [wire-type text]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__") (: text (| str bytes))]
   :post [(: % (| WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "Decode and validate a JSON string (bytes too) into a value of the defwire type wire-type. A string that cannot be read as JSON is also Malformed (with an empty field)."
  (.decode-json (. wire-type __doeff_wire__ codec) text))


(defk parse-as [shape raw]
  {:pre [(: shape SHAPE-FORMS) (: raw (| JsonValue FrozenMap tuple OpaqueJson))]
   :post [(: % (| JsonValue tuple frozenset OpaqueJson WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "Decode and validate a JSON value into a value of a vocabulary type shape that is not a defwire (a container -- (get dict #(str object)), (get list str), (| str None),
   (get dict #(str OpaqueJson)), (get tuple #(SomeWire ...)), and so on. defwire types are accepted too). The answer is a shape value or
   Malformed (the type name is shape's spelling). The type tree and strictness are the same as parse (strings do not become numbers, booleans do not become numbers, an array goes into a tuple field).
   Origin: moving readers that decoded containers directly with pydantic's TypeAdapter to the wire API."
  (.decode (codec-of shape) raw))


(defk parse-json-as [shape text]
  {:pre [(: shape SHAPE-FORMS) (: text (| str bytes))]
   :post [(: % (| JsonValue tuple frozenset OpaqueJson WireValue Malformed))]
   :tags {:context "wire" :role "judgment"}}
  "Decode and validate a JSON string (bytes too) into a value of the vocabulary type shape (the string version of parse-as). A string that cannot be read as JSON is also Malformed."
  (.decode-json (codec-of shape) text))


(defk dump [value]
  {:pre [(: value WireValue)]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "A defwire type's value to a JSON value (fields use wire names, tuples become arrays, defenum becomes its value's spelling, fields equal to their default are not written -- nested type fields too).
   Called only by the foundation that sends it out."
  (.encode (. (type value) __doeff_wire__ codec) value))


(defk dump-json [value]
  {:pre [(: value WireValue)]
   :post [(: % str)]
   :tags {:context "wire" :role "judgment"}}
  "A defwire type's value to a JSON string (same shape as dump -- fields equal to their default are not written). An OpaqueJson field embeds the JSON string it holds
   into the output as is, without decoding it again (avoiding the double work of string -> json.loads -> write again. The meaning of the output as JSON is the same as dump)."
  (.encode-json (. (type value) __doeff_wire__ codec) value))


(defk json-schema [wire-type]
  {:pre [(: wire-type type) (hasattr wire-type "__doeff_wire__")]
   :post [(: % JsonValue)]
   :tags {:context "wire" :role "judgment"}}
  "A defwire type's JSON Schema (fields use wire names, a :unknown :reject type gets additionalProperties false) -- used when checking against contract files."
  (dict (.schema (. wire-type __doeff_wire__ codec))))
