;;; Tests for defwire (doeff_hy/record.hy) and the decoder (doeff_hy/_wire.hy).
;;;
;;; What it checks: decoded values, the mapping of field names (:camel / :snake / :kebab / an explicit dict), refusal and discarding of unknown fields, :check failures,
;;; Malformed for type mismatches (a string for a number, a boolean for a number, a spelling outside the enum), decoding from frozen JSON and from JSON strings, the dump / parse round trip,
;;; JSON Schema, tags, and the forms refused at expansion time.
;;; Published by test_defwire.py (published as is without re-wrapping — ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defwire defenum])
(import dataclasses [dataclass FrozenInstanceError])
(import enum [StrEnum])
(import hy)
(import pytest)
(import doeff_hy._declarations [DefinitionTags])
(import doeff_hy._frozen [freeze-json])
(import doeff_hy._json_value [OpaqueJson])
(import doeff_hy._wire [parse parse-json dump dump-json json-schema Malformed MalformedField WireShape])
(import doeff_hy._wire_codec [Codec])


(defenum LandState QUEUED LANDED)

(defwire Note
  "One annotation"
  {:names :camel}
  (#^ str note-text))

(defwire LandingRow
  "One row of the intake ledger (JSON returned by the record service)"
  {:tags {:context "land-notice" :role "type"}
   :names :camel
   :unknown :reject
   :check [(.startswith lane-id "L")]}
  (#^ str lane-id)
  (#^ LandState state)
  (setv #^ (| int None) landed-at None)
  (setv #^ float ratio 0.0)
  (setv #^ (get tuple #(Note ...)) notes #()))

(defwire GroupedRow {:names :camel} #^ str lane-id (setv #^ int landed-at 0 #^ (| str None) note None))
(defwire SnakeRow {:names :snake} (#^ str lane-id))
(defwire KebabRow {:names :kebab} (#^ str lane-id))
(defwire ExplicitRow {:names {lane-id "LANE" size "n"}} (#^ str lane-id) (#^ int size))
(defwire LenientRow {:names :camel :unknown :ignore} (#^ str lane-id))


(defk refused [source]
  {:pre [(: source str)] :post [(: % (| str None))] :tags {:context "wire" :role "judgment"}}
  "Expand the source and return the message when it is refused (None if it is not refused)."
  (try
    (hy.eval (hy.read-many (+ "(require doeff-hy.record [defwire])\n(import dataclasses [dataclass])\n" source))
             :module (hy.I.types.ModuleType "probe"))
    None
    (except [e Exception]
      (str e))))


(deftest test-parse-gives-the-typed-value
  {:tags {:context "wire" :role "judgment"}}
  (<- row (parse LandingRow {"laneId" "L1" "state" "queued" "landedAt" 3 "ratio" 1 "notes" [{"noteText" "x"}]}))
  (assert (= row (LandingRow :lane-id "L1" :state LandState.QUEUED :landed-at 3 :ratio 1.0 :notes #((Note :note-text "x")))))
  ;; A field with a default value may be omitted; the value is frozen.
  (<- short (parse LandingRow {"laneId" "L2" "state" "landed"}))
  (assert (= #(short.landed-at short.notes) #(None #())))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr short "state" LandState.QUEUED)))


(deftest test-field-names-follow-names
  {:tags {:context "wire" :role "judgment"}}
  (assert (= LandingRow.__doeff_wire__.names {"lane_id" "laneId" "state" "state" "landed_at" "landedAt" "ratio" "ratio" "notes" "notes"}))
  (<- snake (parse SnakeRow {"lane_id" "L1"}))
  (<- kebab (parse KebabRow {"lane-id" "L1"}))
  (<- explicit (parse ExplicitRow {"LANE" "L1" "n" 2}))
  (assert (= #(snake.lane-id kebab.lane-id explicit.lane-id explicit.size) #("L1" "L1" "L1" 2)))
  ;; Python field names are not accepted (only wire names).
  (<- by-python-name (parse LandingRow {"lane_id" "L1" "state" "queued"}))
  (assert (isinstance by-python-name Malformed))
  (assert (in "laneId" (lfor f by-python-name.fields f.field))))


(deftest test-unknown-fields-are-refused-or-ignored
  {:tags {:context "wire" :role "judgment"}}
  (<- refused-row (parse LandingRow {"laneId" "L1" "state" "queued" "extra" 1}))
  (assert (= refused-row (Malformed :wire-type "LandingRow"
                                    :fields #((MalformedField :field "extra" :reason "Unexpected keyword argument [unexpected_keyword_argument]")))))
  (<- lenient (parse LenientRow {"laneId" "L1" "extra" 1}))
  (assert (= lenient (LenientRow :lane-id "L1"))))


(deftest test-a-failed-check-is-malformed
  {:tags {:context "wire" :role "judgment"}}
  (<- answer (parse LandingRow {"laneId" "X1" "state" "queued"}))
  (assert (isinstance answer Malformed))
  (assert (= (. (get answer.fields 0) field) ""))
  (assert (in "LandingRow: field lane-id failed check (.startswith lane-id \"L\")" (. (get answer.fields 0) reason))))


(deftest test-type-mismatches-are-malformed-with-the-field-named
  {:tags {:context "wire" :role "judgment"}}
  ;; A string is not a number, a boolean is not a number, a spelling outside the enum, the type of a nested field — every mismatch is named.
  (<- answer (parse LandingRow {"laneId" "L1" "state" "nope" "landedAt" "3" "ratio" True "notes" [{"noteText" 1}]}))
  (assert (= (lfor f answer.fields f.field) ["state" "landedAt" "ratio" "notes.0.noteText"]))
  (<- not-an-object (parse LandingRow [1 2]))
  (assert (= (lfor f not-an-object.fields f.field) [""])))


(deftest test-frozen-json-and-json-text-parse-the-same
  {:tags {:context "wire" :role "judgment"}}
  (val raw {"laneId" "L1" "state" "landed" "notes" [{"noteText" "y"}]})
  (<- from-value (parse LandingRow raw))
  (<- from-frozen (parse LandingRow (freeze-json raw)))
  (<- from-text (parse-json LandingRow "{\"laneId\": \"L1\", \"state\": \"landed\", \"notes\": [{\"noteText\": \"y\"}]}"))
  (<- from-bytes (parse-json LandingRow b"{\"laneId\": \"L1\", \"state\": \"landed\", \"notes\": [{\"noteText\": \"y\"}]}"))
  (assert (= from-value from-frozen from-text from-bytes))
  (<- broken (parse-json LandingRow "{bad"))
  (assert (= (. (get broken.fields 0) field) ""))
  (assert (in "json_invalid" (. (get broken.fields 0) reason))))


(deftest test-dump-and-parse-round-trip
  {:tags {:context "wire" :role "judgment"}}
  (val row (LandingRow :lane-id "L1" :state LandState.LANDED :landed-at 7 :notes #((Note :note-text "z"))))
  (<- raw (dump row))
  ;; A field equal to its default (ratio 0.0) is not written — reading fills in the default, so it round-trips.
  (assert (= raw {"laneId" "L1" "state" "landed" "landedAt" 7 "notes" [{"noteText" "z"}]}))
  (<- back (parse LandingRow raw))
  (assert (= back row))
  (<- text (dump-json row))
  (<- back-from-text (parse-json LandingRow text))
  (assert (= back-from-text row)))


(defwire Declared
  "A nested optional field (a contract shape expressed by the field being absent rather than null)"
  {:names :camel}
  (setv #^ (| str None) model None)
  (setv #^ (| str None) work-dir None))

(defwire Preference
  "A row with a nested-type field and a field that allows None with no default"
  {:names :camel}
  (#^ Declared agent)
  (#^ (| str None) note)
  (setv #^ (| str None) notify-view None))


(deftest test-dump-omits-fields-at-their-default-even-when-nested
  {:tags {:context "wire" :role "judgment"}}
  (val row (Preference :agent (Declared :model "m") :note None))
  ;; A field whose default is None writes no null and is absent as a field (nested-type fields too); a field with no default is written as null even when None.
  (<- raw (dump row))
  (assert (= raw {"agent" {"model" "m"} "note" None}))
  (<- text (dump-json row))
  (assert (= text "{\"agent\":{\"model\":\"m\"},\"note\":null}"))
  (<- back (parse Preference raw))
  (assert (= back row)))


(deftest test-json-schema-uses-wire-names
  {:tags {:context "wire" :role "judgment"}}
  (<- schema (json-schema LandingRow))
  (assert (= (sorted (get schema "properties")) ["landedAt" "laneId" "notes" "ratio" "state"]))
  (assert (= (get schema "required") ["laneId" "state"]))
  (assert (is (get schema "additionalProperties") False))
  (assert (= (get schema "description") "One row of the intake ledger (JSON returned by the record service)")))


(deftest test-tags-and-shape-are-readable-on-the-type
  {:tags {:context "wire" :role "judgment"}}
  (<- _ (parse Note {"noteText" "x"}))
  (assert (= LandingRow.__doeff_tags__ (DefinitionTags :context "land-notice" :role "type")))
  (assert (= LandingRow.__doeff_checks__ #("(.startswith lane-id \"L\")")))
  (assert (isinstance LandingRow.__doeff_wire__ WireShape))
  (assert (= LandingRow.__doeff_wire__.unknown "reject"))
  (assert (= LenientRow.__doeff_wire__.unknown "ignore")))


(deftest test-malformed-headers-are-refused-at-expansion
  {:tags {:context "wire" :role "judgment"}}
  (<- no-header (refused "(defwire A #^ str a)"))
  (assert (in "header dict" no-header))
  (<- no-names (refused "(defwire A {:unknown :reject} #^ str a)"))
  (assert (in ":names is required" no-names))
  (<- bad-names (refused "(defwire A {:names :pascal} #^ str a)"))
  (assert (in ":names must be" bad-names))
  (<- bad-unknown (refused "(defwire A {:names :camel :unknown :drop} #^ str a)"))
  (assert (in ":unknown must be" bad-unknown))
  (<- unknown-key (refused "(defwire A {:names :camel :doc \"x\"} #^ str a)"))
  (assert (in "is not accepted" unknown-key))
  (<- missing (refused "(defwire A {:names {a \"A\"}} #^ str a #^ str b)"))
  (assert (in "missing fields: b" missing))
  (<- extra (refused "(defwire A {:names {a \"A\" c \"C\"}} #^ str a)"))
  (assert (in "unknown fields: c" extra))
  (<- clash (refused "(defwire A {:names :camel} #^ str lane-id #^ str laneId)"))
  (assert (in "same wire name" clash))
  (<- unnamable (refused "(defwire A {:names :camel} #^ str ok?)"))
  (assert (in "cannot derive a wire name" unnamable))
  (<- fine (refused "(defwire A {:names :camel} #^ str a)"))
  (assert (is fine None)))


(defwire ToolCall
  "A row with an arbitrary-JSON field (OpaqueJson) whose shape the caller decides"
  {:names :camel :unknown :reject}
  (#^ str tool-name)
  (#^ OpaqueJson input)
  (setv #^ (| OpaqueJson None) output None))

(defwire ToolArgs
  "A type that a reader who knows the shape decodes the contents of OpaqueJson into"
  {:names :camel :unknown :ignore}
  (#^ str path))

;; A list of sum-type rows with OpaqueJson as the first arm (the same order as AnswerRowWire, the sum of entity_query answer rows — a sum type tries its arms from the left).
(setv MixedRow (| OpaqueJson Note ToolArgs))

(defwire ToolBatch
  "A list of sum-type rows mixing OpaqueJson and typed rows"
  {:names :camel}
  (#^ (get tuple #(MixedRow ...)) rows))


(deftest test-opaque-json-is-carried-unread-and-parsed-by-a-reader-that-knows-the-shape
  {:tags {:context "wire" :role "judgment"}}
  ;; An OpaqueJson written in a defwire field wraps arbitrary JSON, and dump returns it to the original JSON value (it round-trips).
  (val raw {"toolName" "Read" "input" {"path" "/a" "n" [1 {"x" None}]} "output" "ok"})
  (<- call (parse ToolCall raw))
  (assert (= call.input (OpaqueJson.of {"path" "/a" "n" [1 {"x" None}]})))
  (assert (= call.input.text "{\"path\":\"/a\",\"n\":[1,{\"x\":null}]}"))
  (assert (= call.input.encoded-size (len (.encode call.input.text "utf-8"))))
  (<- back (dump call))
  (assert (= back raw))
  ;; A reader who knows the shape decodes the contents into a type with parse — a different shape is Malformed.
  (<- args (parse ToolArgs call.input))
  (assert (= args (ToolArgs :path "/a")))
  (<- wrong (parse ToolArgs call.output))
  (assert (isinstance wrong Malformed))
  ;; Equality is equality of the minimal serialized string; the same value from frozen JSON too; an unreadable string is refused.
  (assert (= (OpaqueJson.of (freeze-json {"a" [1 2]})) (OpaqueJson.from-text "{ \"a\" : [1, 2] }")))
  (with [(pytest.raises ValueError)]
    (OpaqueJson "{bad"))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr call.input "text" "1")))


(deftest test-grouped-setv-fields-get-wire-names
  {:tags {:context "wire" :role "judgment"}}
  ;; Fields lined up in one setv also each have a wire name per pair (before, only the first pair did, and note dropped from the :names :camel mapping).
  (assert (= GroupedRow.__doeff_wire__.names {"lane_id" "laneId" "landed_at" "landedAt" "note" "note"}))
  (<- row (parse GroupedRow {"laneId" "L1" "landedAt" 3 "note" "x"}))
  (assert (= #(row.landed-at row.note) #(3 "x"))))



;; --- When the decoder is built (precedent #2327) -------------------------------------------
;; The decoder (the type tree of doeff_hy._wire_codec) is not built when the type is defined; it is built only once at the first parse / dump / json-schema and remembered on the type
;; (the type attribute __doeff_codec__). These 2 types are used by no other test (so as to copy a type before use).

(defwire FieldsDefinedBefore
  "A type whose field types are all defined before this line (buildable at definition time, but built at first use)"
  {:names :camel}
  (#^ str lane-id)
  (setv #^ (get tuple #(Note ...)) notes #()))

;; Field types are written as string forward references: through Python 3.13 the annotations in a class body are evaluated at definition time, so a bare name (#^ DefinedLater) makes
;; module loading fail with NameError (PEP 649, which evaluates annotations lazily, starts at 3.14). With a string, in every version the decoder resolves it at first use.
(defwire AheadOfItsField
  "A type whose field is a type not yet defined — it cannot be built at definition time, and is built at first use"
  {:names :camel}
  (#^ "DefinedLater" later))

(defwire DefinedLater
  "The field type of AheadOfItsField (defined after AheadOfItsField)"
  {:names :camel}
  (#^ int count))


(defk built-codec [wire-type]
  {:pre [(: wire-type type)] :post [(: % (| Codec None))] :tags {:context "wire" :role "judgment"}}
  "The decoder remembered on the type (None if not yet built) — a gauge that reflects when it was built and whether it was rebuilt."
  (.get (vars wire-type) "__doeff_codec__"))


(deftest test-the-solver-is-built-at-first-use-and-never-rebuilt-by-use
  {:tags {:context "wire" :role "judgment"}}
  ;; It is not built at definition time — so every process that loads the module does not pay the build cost of types it does not use (
  ;; of the 547 built in one run of test_emulated_screen.hy, 49 were used). Build only once at first use, and do not rebuild from the second time on.
  (<- before (built-codec FieldsDefinedBefore))
  (assert (is before None) before)
  (<- first (parse FieldsDefinedBefore {"laneId" "L1" "notes" [{"noteText" "x"}]}))
  (<- after-first (built-codec FieldsDefinedBefore))
  (assert (is-not after-first None))
  (<- text (dump-json first))
  (<- second (parse-json FieldsDefinedBefore text))
  (<- _ (dump second))
  (<- _ (json-schema FieldsDefinedBefore))
  (<- after-uses (built-codec FieldsDefinedBefore))
  (assert (= second first))
  ;; From the second use on it is not rebuilt (the decoder remembered on the type stays the same object).
  (assert (is after-uses after-first)))


(deftest test-a-type-ahead-of-its-field-type-is-built-at-first-use-and-not-rebuilt-after
  {:tags {:context "wire" :role "judgment"}}
  ;; A type whose field type is defined later cannot be built at definition time — build only once at first use, and do not rebuild from the second time on.
  (<- first (parse AheadOfItsField {"later" {"count" 1}}))
  (<- after-first (built-codec AheadOfItsField))
  (assert (is-not after-first None))
  (<- second (parse AheadOfItsField {"later" {"count" 2}}))
  (<- _ (dump-json second))
  (<- after-uses (built-codec AheadOfItsField))
  (assert (= #(first.later.count second.later.count) #(1 2)))
  (assert (is after-uses after-first)))
