;;; Home of Malformed, the answer for JSON of the wrong shape.
;;;
;;; Both wire.hy (the pydantic decoder) and wire_codec.py (the hand-written decoder, which does not import pydantic) return the same type, so it lives
;;; in this module, which does not import pydantic. wire.hy imports it from here and exposes the name as before (readers of `(import doeff_hy._wire [Malformed])` are unaffected).
;;; Design record: docs/design/defwire-decoder/design.md (section 3, "Where Malformed's fields live" and "The spelling of reason").
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])


(defrecord MalformedField
  "One place where the shape does not match: field = the location of the field (the wire name; nested fields and array positions are joined with `.`; the whole value is the empty string) / reason = why
   (`<msg> [<kind>]`; the kind is the spelling of the pydantic error kind)."
  {:tags {:context "wire" :role "type"}}
  (#^ str field)
  (#^ str reason))


(defrecord Malformed
  "JSON from outside does not have the shape the type promises: the fifth kind of failure (ADR-DOE-CORE-EFFECTS-003 R17): wire-type = the name of the type being decoded /
   fields = the places that do not match (one or more, in the order the decoder found them)."
  {:tags {:context "wire" :role "type"}
   :check [(> (len fields) 0)]}
  (#^ str wire-type)
  (#^ (get tuple #(MalformedField ...)) fields))
