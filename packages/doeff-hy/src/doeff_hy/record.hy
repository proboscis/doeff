;;; Record scaffolding — macros used by writers of turn records (the TurnRecord equivalent) (fence 5).
;;;
;;; Origin = agora-redesign stage 7 lane 7c (the fence "record-scaffolding macro" of decision 1.4,
;;; "new macros go into doeff-hy together with projection rules" ·
;;; `docs/plans/decisions-merge-2026-09-12.md`)。
;;;
;;; What the scaffolding is for: the writer of turn records (agentd) writes one row per turn.
;;; Building that row as a bare dict breaks fence 2 (ban on `"value"`, Any, object, bare dict / list),
;;; and misspelled field names or wrong types stay invisible until runtime.
;;; `defrecord` builds a frozen record type that declares field names and types in one line —
;;; the writer builds that type instead of a dict.
;;;
;;; The **contents** of a record (which fields exist) are not decided here: the canonical schema of the turn record
;;; is ACP's `docs/contracts/`, and the writer declares the fields copied from there.
;;; This holds only the scaffolding of the shape (tool layer).
;;;
;;; Projection rule (why this macro can pass static checking): `defrecord` computes by reading
;;; the header dict at expansion time, so it is not a template macro (the same "fixed" as defenum).
;;; On the shared quality check side there is a dedicated reading rule (`quality.hy_record`'s
;;; record_declaration), which corresponds one to one with the expansion in this module's header note.
;;; The using module's contract needs no declaration — its origin is in the fixed table (`doeff_hy.record`).
;;;
;;; Usage (the form without a header dict — the same as before):
;;;   (require doeff-hy.record [defrecord])
;;;   (import dataclasses [dataclass])
;;;
;;;   (defrecord TurnRecordRow
;;;     #^ str conversation-id
;;;     #^ str turn-id
;;;     #^ str phase
;;;     #^ (| str None) ended-at)
;;;
;;;   (TurnRecordRow :conversation-id c :turn-id t :phase "Running" :ended-at None)
;;;
;;; Expansion: in the type-check projection (static-view), (defclass [(dataclass :frozen True :kw-only True)] Name [] fields …).
;;; At runtime, (defclass [(dataclass :init False :repr False :eq False :kw-only True)] Name [] (setv __slots__ …) fields … functions …) plus finishing (seal-record of record_runtime) —
;;; dataclass is made to build only the table of fields, and __init__ (kw-only), __repr__, __eq__, __hash__, and the frozen __setattr__ / __delattr__ are
;;; written by the expansion (the functions go into the .pyc as module code, and dataclass does not compile function source at load time — about 1 ms per type →
;;; about 0.1 ms). The functions written per type are only __init__ (no annotations), __eq__, __hash__, and the __post_init__ of :check;
;;; __repr__ and the frozen __setattr__ / __delattr__ point at the one shared set in record_runtime, and the type holds the sequence of field names as a tuple. The docstring is not put in the runtime expansion, and __doc__ is a
;;; shared descriptor built from the field names (per-type functions and strings held over 10 MB in a job's memory).
;;; Values hold fields in slots and have no __dict__ (a __dict__ per value held about 0.6 GB on the production screen —
;;; for a type with 35 fields, about 2,000 → about 410 bytes each). The class body declares the slots itself (dataclass with slots=True rebuilt every class — about
;;; 0.15 ms per type, about 1 second over the 6,300 types one agora-controllers check loads), so a field default is bound to a name of its own in the body and
;;; seal-record puts it into the field table. The weak-reference slot (__weakref__) remains. Field defaults live in the field table (__dataclass_fields__) and
;;; cannot be read from the type's attributes (same as dataclass with slots). pickle and copy restore values with the shared __setstate__ without going through the freeze.
;;; Behavior is the same as standard dataclass (frozen, kw_only) (tests/defrecord_shape_deftests.hy
;;; checks this). When the expansion cannot fully read the shape of a field (a field passed through a name, a non-literal :init / :repr / :compare / :hash, a field named self / object,
;;; or a body that writes __init__ and the like itself), it uses the same expansion as the projection (dataclass builds the functions).
;;; `dataclass` points at a name in the using side's scope (the using side imports it).
;;; The runtime expansion of a defrecord or defwire written inside a function body is put in an immediately-called function holding the expansion's imports (doeff_hy and others), the type, and bookkeeping,
;;; and only the type name is bound with (setv Name ((fn [] … Name))) (_scoped-definition of doeff_hy.macros — so imported names do not
;;; become locals of the outer function; tests/test_nested_definition_scope.py). The name of the wrapping function goes into the type's __qualname__
;;; (for defwire there are two, since the inner defrecord wraps once more). Module and class bodies and the type-check projection are not wrapped.
;;;
;;; ---------------------------------------------------------------------------
;;; Header dict {:tags … :check […]} — the definition tags and the checks at construction (both may be omitted)
;;; ---------------------------------------------------------------------------
;;;
;;; Rule: declarations that tools read are def*; data types are defrecord; no classes that carry behavior are made.
;;; The form that wrote a defn in a dataclass `__post_init__` to check values moves into a defrecord clause.
;;;
;;;   (setv CHAT-ID-PATTERN (re.compile r"c-[0-9A-HJKMNP-TV-Z]{26}"))
;;;
;;;   (defrecord ChatId
;;;     "chat id"
;;;     {:tags  {:context "chat" :role "type"}
;;;      :check [(CHAT-ID-PATTERN.fullmatch value)]}
;;;     (#^ str value))
;;;
;;; - The header dict is one, right after the name (and docstring). The only keys accepted are :tags, :check, and :failure (other keys are an expansion error).
;;; - :tags has the same contract as defk / deff ({:context "…" :role "…"}; roles are declarations.ROLES).
;;;   It is kept in the class attribute `__doeff_tags__` (DefinitionTags).
;;; - :check is a list of boolean expressions. Each expression refers to fields by field name, and is evaluated in the order written at construction.
;;;   If false, ValueError "ChatId: field value failed check (CHAT-ID-PATTERN.fullmatch value): value='x'".
;;;   An expression that references no field, or a type (: field type) (types are written in the field annotation), is an expansion error.
;;;   The sequence of expression spellings is kept in the class attribute `__doeff_checks__`.
;;; - :failure True marks "this type is a failure value" (the value is only a literal True / False). It is kept in the class attribute `__doeff_failure__`, and
;;;   doeff-linter reads it without running anything (so that DOEFF122, hand-written failure re-raising, knows the type of a match arm is a failure type — not
;;;   guessed from the type name. ADR-DOE-HY-007 R10). The failure of an effect answer is declared with defeffect's :failure / :absent (ADR-DOE-CORE-EFFECTS-003
;;;   R5) — this one is a marker for values that are not effect answers (such as a refusal returned by a check function).
;;;
;;; Where predicates used in checks live (the key to the design): __post_init__ cannot run a Program, so
;;; a defk cannot be called from :check (calling it returns a Program that looks true and silently passes — so
;;; a result that is a Program / effect stops with a TypeError). Do not make predicates functions:
;;;   1. Write the expression directly in :check (`(<= start end)`, `(in kind KINDS)`).
;;;   2. Put the values the expression uses in module constants (regular expressions, closed sets — `(setv CHAT-ID-PATTERN …)`).
;;;   3. When another type wants the same check, make that field's type an already-checked record
;;;      (`#^ ChatId chat` — do not line up the same predicate on string fields).
;;; Only when a named predicate is truly needed, add a reason comment to a pure deff
;;; (`; cannot be a defk: called by defrecord's :check at construction`) and :tags {… :role "judgment"}.
;;;
;;; Expansion (with header dict):
;;;   (do (import doeff_hy._declarations doeff_hy.record doeff_hy._record_check)
;;;       (defclass [(dataclass :frozen True :kw-only True)] ChatId []
;;;         "chat id"
;;;         ((annotate value str))
;;;         (defn __post-init__ [self]
;;;           (setv value self.value)
;;;           (setv _verdict (CHAT-ID-PATTERN.fullmatch value))
;;;           (when (is-not _verdict True)   ; if the answer is exactly True the rest is unnecessary (#2421)
;;;             (when (isinstance _verdict #(DoExpr EffectBase)) (raise (TypeError "… returned a Program …")))  ; runtime expansion only
;;;             (when (not _verdict)
;;;               (doeff_hy._record_check.require-check "ChatId" #("value") "(CHAT-ID-PATTERN.fullmatch value)"
;;;                                                     (bool _verdict) {"value" value})))
;;;           None))
;;;       (setattr ChatId "__doeff_tags__" (doeff_hy._declarations.DefinitionTags :context "chat" :role "type"))
;;;       (setattr ChatId "__doeff_checks__" #("(CHAT-ID-PATTERN.fullmatch value)")))
;;;
;;; ---------------------------------------------------------------------------
;;; `defwire` — a type at the JSON boundary (builds a type that decodes and validates JSON in one declaration)
;;; ---------------------------------------------------------------------------
;;;
;;; Rule: do not let bare dicts be used directly; decode and validate like pydantic. The def* macro for that is placed here.
;;; The canonical source of the rule = ADR-DOE-HY-007 R8.
;;;
;;; What the scaffolding is for: if JSON from outside is carried as JsonValue (a renamed bare dict) and taken apart by hand at each reading site,
;;; misspelled field names and wrong types stay invisible until the point of use. defwire declares the JSON shape as a type, and the decoder
;;; (parse / dump of doeff_hy._wire — the in-house type tree of doeff_hy/_wire_codec.py) decodes and validates once at one place on the boundary.
;;;
;;;   (require doeff-hy.record [defwire])
;;;   (import dataclasses [dataclass])
;;;   (import doeff_hy._wire [parse dump Malformed])
;;;
;;;   (defwire LandingRow
;;;     "one row of the ingest ledger (JSON returned by the record service)"
;;;     {:tags {:context "land-notice" :role "type"} :names :camel :unknown :reject
;;;      :check [(.startswith lane-id "L")]}
;;;     (#^ str lane-id)
;;;     (#^ LandState state)
;;;     (setv #^ (| int None) landed-at None))
;;;
;;;   (<- row (parse LandingRow raw))   ; answer = a LandingRow value or Malformed (which type, which field, why)
;;;   (<- raw (dump row))               ; to a JSON value (only the foundation that sends out)
;;;
;;; - Field shapes are the same as defrecord (`#^ T x`, `(#^ T x)`, defaults as `(setv #^ T x value)`). The expansion uses defrecord as is
;;;   (frozen, keyword arguments only, :tags, :check), so `dataclass` is imported by the using side, as with defrecord.
;;; - The header dict is required. The only keys accepted are :names, :unknown, :tags, and :check.
;;;   :names (required) = the copy of wire field names. :camel (lane-id → laneId), :snake (lane_id), :kebab (lane-id), or
;;;     an explicit dict {lane-id "LANE" …} (naming all fields exactly). Separators are - and _. Python field names are not accepted.
;;;   :unknown = how unknown fields are handled. :reject (default — Malformed) or :ignore (discard).
;;; - Type checks are strict (a string does not become a number, a bool does not become a number, arrays are tuple fields, defenum fields take the value spelling).
;;;   Nested field types must also be defwire types (a bare defrecord has no wire shape).
;;;
;;; Expansion (LandingRow above):
;;;   (do (import typing [ClassVar :as _doeff-ClassVar])
;;;       (import doeff_hy._wire)
;;;       (hy.R.doeff_hy/record.defrecord LandingRow "…" {:tags {…} :check […] :class-vars […]} fields …)
;;;       (setattr LandingRow "__pydantic_config__" (doeff_hy._wire.wire-config {"lane_id" "laneId" …} "reject"))
;;;       (setattr LandingRow "__doeff_wire__" (doeff_hy._wire.wire-shape LandingRow {"lane_id" "laneId" …} "reject" "…")))
;;;   The docstring stays out of the type (as with defrecord) and is held only by the wire shape, which json-schema reads as description (None when not written).
;;;
;;; Projection rule: maps to a class of the same shape as defrecord (:names and :unknown do not change the type's shape) — held in the same lane of the shared quality check's
;;; `quality.hy_record`. hy-index reads it as kind defrecord (record_def of doeff-indexer).
;;;
;;; ---------------------------------------------------------------------------
;;; `defenum` — build a closed set of values (a string Enum) in one line
;;; ---------------------------------------------------------------------------
;;;
;;; Rule: enums are also built with a macro.
;;; Design record = `docs/design/defenum/design.md` (value spelling, StrEnum, exhaustiveness check).
;;;
;;; What the scaffolding is for: holding "one of a fixed number of names" as a bare string makes misspellings
;;; invisible until runtime, and missed `match` branches are not caught by type checking. `defenum`
;;; builds an `enum.StrEnum` class — values are strings, so they can be written to JSON as is,
;;; and the type checker knows the set of members, so it can check `match` exhaustiveness.
;;;
;;; Usage:
;;;   (require doeff-hy.record [defenum])
;;;   (import enum [StrEnum])
;;;
;;;   (defenum PlacementMismatch KEY CONVERSATION KIND PHASE CANCELLED GUARANTEED)
;;;   (defenum Phase (PENDING "Pending") (RUNNING "Running"))   ; form with explicit values
;;;
;;; Expansion (values are derived from member names — lowercased, with `_` / `-` normalized to `-`):
;;;   (defclass PlacementMismatch [StrEnum]
;;;     (setv KEY "key" CONVERSATION "conversation" … GUARANTEED "guaranteed"))
;;;   `IN-PROGRESS` / `IN_PROGRESS` → attribute `IN_PROGRESS`, value `"in-progress"`.
;;;   A member of the form `(NAME "value")` uses the value as is (for values whose spelling is fixed
;;;   by an outside agreement, like k8s `"Running"`).
;;;
;;; Rejected at expansion time (forms where Enum would silently alias or misread are stopped first):
;;;   no members at all / a name that does not start with a letter `[A-Za-z][A-Za-z0-9_-]*` /
;;;   the same name twice (after mangling) / the same value twice (StrEnum would make the later one an
;;;   alias, and the member count would silently shrink).
;;;
;;; ⚠ `defenum` is not a template macro: values are derived from names, so
;;; computation is needed at expansion time (a verbatim quasiquote substitution cannot make `"key"` from `KEY`).
;;; So it is not in the shared quality check's template validation (`quality.hy_macro`),
;;; and the projection rule needs a dedicated rule on the checker side (expected to sit next to defrecord in `quality.hy_record` —
;;; the design record's "changes needed on the quality check side").
;;; The expansion's base `StrEnum` points at a name in the using side's
;;; scope, like defrecord's `dataclass` (`enum.StrEnum` on Python 3.11 and later).

(import dataclasses [dataclass])
(import doeff [DoExpr EffectBase])


;; The computation that reads the header dict at expansion time is placed only inside the macro body (as with defenum — if split out,
;; a function called at expansion time cannot be a defk and becomes a defn, and a defn with a quasiquote is not in the type projection).
;; How fields are read (same as record_def of hy-index and quality.hy_record): `#^ T x` = (annotate x T),
;; `(#^ T x)` = ((annotate x T)), `(setv #^ T x default #^ U y default …)` (one field per pair), a bare symbol x. Any other form is not a field.
;; The sequence of statements of the expansion (import of doeff_hy — record_runtime, the declaration of the header dict — and the type and bookkeeping) is assembled to match what _scoped-definition of doeff_hy.macros
;; (the one place all definition macros pass through) wrote: inside a function body, put them in an immediately-called function and bind only the type name —
;; if left side by side, the imported doeff_hy becomes a local of the whole outer function, and the module-level doeff_hy that the outer function reads first
;; failed with UnboundLocalError. In module and class bodies they are laid side by side as before.
(defmacro defrecord [_hy-compiler name #* forms]
  (import doeff_hy.macros [_scoped-definition])
  (import hy.models [Dict Expression Keyword List Sequence String Symbol])
  (import doeff_hy._declarations [declared-value field-targets refuse-unknown-keys tags-form])
  (import doeff_hy._expansion_names [import-form])
  (setv docstring None
        rest (list forms))
  (when (and rest (isinstance (get rest 0) String))
    (setv docstring (get rest 0)
          rest (cut rest 1 None)))
  (import doeff_hy._static_view [static-view-enabled])
  (setv static-view (static-view-enabled))
  ;; Class form of the runtime expansion (header note "functions are written at expansion time"): dataclass builds only the field table (__dataclass_fields__,
  ;; defaults, the default-factory of field, refusal of mutable defaults), and the expansion writes the functions here (__init__, __repr__, __eq__, __hash__, and the frozen
  ;; __setattr__ / __delattr__) — the functions go into the .pyc as module code, and dataclass does not
  ;; exec(compile) function source at load time (about 1 ms per type → about 0.1 ms). When the expansion cannot fully read the shape of a field (the refusal conditions below) it returns None,
  ;; and the caller uses the previous expansion (dataclass builds the functions). The type-check projection (static-view) stays dataclass (pyright reads it as a dataclass).
  (setv class-form
    (fn [docstring class-vars fields post-init]
      (setv generated #("__init__" "__repr__" "__eq__" "__hash__" "__setattr__" "__delattr__" "__post_init__")
            literal {"True" True "False" False "None" None}
            annotated (fn [form] (and (isinstance form Expression) (= (len form) 3) (= (str (get form 0)) "annotate")
                                      (isinstance (get form 1) Symbol)))
            field-call? (fn [form] (and (isinstance form Expression) form (isinstance (get form 0) Symbol)
                                        (in (str (get form 0)) #("field" "dataclasses.field"))))
            ;; Whether the body form writes a function the expansion writes (defn, defn/a, and others — the name is read even behind the decorator list). A name with an answer-type annotation
            ;; (defn #^ None __post-init__ …) is read by Hy as (annotate __post-init__ None), so read the name inside the annotation (
            ;; a form that looked only at symbol names missed the body's __post_init__, and the expansion's __init__ did not call it).
            own-method? (fn [form] (and (isinstance form Expression) (>= (len form) 2) (isinstance (get form 0) Symbol)
                                        (.startswith (str (get form 0)) "def")
                                        (do (setv written (if (and (isinstance (get form 1) List) (>= (len form) 3)) (get form 2) (get form 1))
                                                  named (if (annotated written) (get written 1) written))
                                            (and (isinstance named Symbol) (in (hy.mangle (str named)) generated)))))
            ;; Pairs of field target and default (target, default form, whether a default exists) in the order written (read the same way as field-targets).
            targets (sum (lfor form fields
                               (cond
                                 (annotated form) [#(form None False)]
                                 (and (isinstance form Expression) (= (len form) 1) (annotated (get form 0))) [#((get form 0) None False)]
                                 (and (isinstance form Expression) (>= (len form) 3) (= (str (get form 0)) "setv"))
                                   (lfor #(t v) (zip (cut form 1 None 2) (cut form 2 None 2)) :if (annotated t) #(t v True))
                                 True []))
                         [])
            ;; Keys and values of a field call (keys are mangled names). A call whose key count does not match gives None.
            call-args (fn [form] (setv args (list (cut form 1 None)))
                        (if (% (len args) 2)
                            None
                            (lfor #(k v) (zip (cut args None None 2) (cut args 1 None 2))
                                  #((if (isinstance k Keyword) (hy.mangle (str k.name)) None) v))))
            ;; Shape of one field — (symbol, python name, annotation, kind required / plain / call, whether it has a value, whether it has a factory, init, repr, compare, hash).
            ;; A field the expansion cannot fully read gives None (field call keys unknown, flags not literal, field named self / object).
            spec-of (fn [target default has-default]
                      (setv sym (get target 1)
                            py (hy.mangle (str sym))
                            call (and has-default (field-call? default))
                            pairs (if call (call-args default) []))
                      (cond
                        (in py #("self" "object")) None
                        (is pairs None) None
                        (any (gfor #(k v) pairs (not (in k #("init" "repr" "compare" "hash" "default" "default_factory" "metadata" "doc")))))
                          None
                        (any (gfor #(k v) pairs (and (in k #("init" "repr" "compare" "hash"))
                                                     (not (and (isinstance v Symbol) (in (str v) literal))))))
                          None
                        True
                          (do (setv flags (dfor #(k v) pairs :if (in k #("init" "repr" "compare" "hash")) k (get literal (str v)))
                                    keys (frozenset (gfor #(k v) pairs k)))
                              {"sym" sym "py" py "ann" (get target 2)
                               "kind" (cond call "call" has-default "plain" True "required")
                               "has-value" (if call (in "default" keys) has-default)
                               "has-factory" (in "default_factory" keys)
                               "init" (.get flags "init" True) "repr" (.get flags "repr" True)
                               "compare" (.get flags "compare" True) "hash" (.get flags "hash" None)})))
            specs (lfor #(t d h) targets (spec-of t d h)))
      ;; A private name `__x` is refused too: Python renames it in the class body and in __slots__, and the expansion writes the plain name.
      (when (or (any (gfor form fields (isinstance form Symbol))) (any (gfor form fields (own-method? form)))
                (any (gfor s specs (is s None)))
                (any (gfor s specs (and (.startswith (get s "py") "__") (not (.endswith (get s "py") "__"))))))
        (return None))
      ;; The class declares its slots in its body (so dataclass does not rebuild the class to add them — about 0.15 ms per type), and a slot cannot share
      ;; its name with a class attribute, so a field's default is not left as a class attribute: the body keeps the field annotation and binds the default to a
      ;; class-level name of its own (held — python name → that name, in field order), evaluated where the default was written. __init__ takes its argument defaults
      ;; from these names, seal-record puts the defaults into the field table, and the names are deleted at the end of the body.
      (setv paired? (fn [form] (and (isinstance form Expression) (>= (len form) 3) (= (str (get form 0)) "setv") (% (len form) 2)))
            pairs-of (fn [form] (list (zip (cut form 1 None 2) (cut form 2 None 2))))
            py-of (fn [target] (hy.mangle (str (get target 1))))
            held (dfor form fields :if (paired? form) #(t v) (pairs-of form) :if (annotated t)
                       (py-of t) (hy.gensym (py-of t)))
            body (sum (lfor form fields
                            (if (paired? form)
                                (sum (lfor #(t v) (pairs-of form)
                                           (if (annotated t) [t `(setv ~(get held (py-of t)) ~v)] [`(setv ~t ~v)]))
                                     [])
                                [form]))
                      []))
      ;; The functions it writes read the defined type not by name but by `__class__` (the closure of the defined class that Python makes for a method) and zero-argument super —
      ;; the name of a type written in a class body is an attribute of the outer class, and a method skips the class level when looking up a name, so reading by name made the frozen
      ;; __setattr__ and the __init__ of fields with default-factory or init turned off fail with NameError (tests/test_nested_definition_scope.py
      ;; test_class_body_type_methods_read_their_own_class)。
      (setv self-sym (hy.gensym "self")
            missing 'doeff_hy._record_runtime.FACTORY-DEFAULT
            factory (fn [py] `((. (get (. __class__ __dataclass_fields__) ~py) default-factory)))
            ;; The __init__ argument of one field (None = not an argument — a field with init turned off) and the expression that sets the value on self (None = not set).
            ;; No type annotation is written on arguments (see the note on the class form below).
            init-of (fn [s]
                      (setv sym (get s "sym") py (get s "py")
                            put (fn [value] `(object.__setattr__ ~self-sym ~py ~value)))
                      (cond
                        (not (get s "init"))
                          #(None (cond (get s "has-factory") (put (factory py)) (get s "has-value") (put `(. (get (. __class__ __dataclass_fields__) ~py) default)) True None))
                        (get s "has-factory")
                          #(`[~sym ~missing] (put `(if (is ~sym ~missing) ~(factory py) ~sym)))
                        (and (= (get s "kind") "call") (get s "has-value"))
                          #(`[~sym (. ~(get held py) default)] (put sym))
                        (= (get s "kind") "plain")
                          #(`[~sym ~(get held py)] (put sym))
                        True
                          #(sym (put sym))))
            inits (lfor s specs (init-of s))
            params (lfor #(p _) inits :if (is-not p None) p)
            sets (lfor #(_ v) inits :if (is-not v None) v)
            shown (lfor s specs :if (get s "repr") (get s "py"))
            compared (lfor s specs :if (get s "compare") (get s "py"))
            hashed (lfor s specs :if (if (is (get s "hash") None) (get s "compare") (get s "hash")) (get s "py"))
            plain `#(~@(lfor s specs :if (= (get s "kind") "plain") (String (get s "py"))))
            names-of (fn [names] `#(~@(lfor n names (String n))))
            other (hy.gensym "other"))
      ;; Written per type are __init__, __eq__, __hash__ (and the __post_init__ of :check). __repr__ and the frozen __setattr__ / __delattr__ point at
      ;; the one shared set in record_runtime, and the type holds the sequence of field names as a literal tuple (per-type functions exceeded 10 MB in a job with about
      ;; 1,760 types). __eq__ and __hash__ stay per type because they are called every time a value goes into a dict key or a set — making them one shared function
      ;; made them 1.3 to 3 times slower because of looping over field names (measured on Python 3.14t). No type annotations are written on __init__ arguments (nothing reads them at runtime, and each annotation leaves
      ;; an __annotate__ function — field types are held by the class annotations and the type-check projection). The docstring is not put in the runtime expansion (nothing reads it at runtime;
      ;; it remains in the type-check projection and the source), and __doc__ is a shared descriptor built from the field names when read (so dataclass does not
      ;; compute a signature at load time for a type without a docstring).
      `(do
       (defclass [(dataclass :init False :repr False :eq False :kw-only True)] ~name []
         (setv __slots__ ~(names-of [#* (lfor s specs (get s "py")) "__weakref__"])
               __doc__ doeff_hy._record_runtime.RECORD-DOC
               __doeff-fields__ ~(names-of (lfor s specs (get s "py")))
               __doeff-repr-fields__ ~(names-of shown)
               __repr__ doeff_hy._record_runtime.record-repr
               __setattr__ doeff_hy._record_runtime.record-setattr
               __delattr__ doeff_hy._record_runtime.record-delattr
               __setstate__ doeff_hy._record_runtime.record-setstate)
         ~@(or class-vars [])
         ~@body
         ~@post-init
         ~@(if held [`(setv __doeff-defaults__ #(~@(lfor #(py kept) (.items held) `#(~(String py) ~kept))))] [])
         (defn __init__ [~self-sym ~@(if params ['* #* params] [])]
           ~@sets
           ~@(if post-init [`(.__post-init__ ~self-sym)] [])
           None)
         ~@(if held [`(del ~@(.values held))] [])
         (defn __eq__ [~self-sym ~other]
           (cond
             (is ~self-sym ~other) True
             (is (. ~other __class__) (. ~self-sym __class__))
               (and ~@(or (lfor n compared `(= (. ~self-sym ~(Symbol n)) (. ~other ~(Symbol n)))) ['True]))
             True NotImplemented))
         (defn __hash__ [~self-sym]
           (hash #(~@(lfor n hashed `(. ~self-sym ~(Symbol n)))))))
       (doeff_hy._record_runtime.seal-record ~name ~plain ~(bool post-init)))))
  (when (not (and rest (isinstance (get rest 0) Dict)))
    (setv written (when (not static-view) (class-form docstring None rest [])))
    (return (_scoped-definition _hy-compiler name
              [(if (is written None)
                   `(defclass [(dataclass :frozen True :kw-only True)] ~name [] ~@forms)
                   `(do (import doeff_hy._record_runtime) ~written))]
              :nested-reads True)))
  (setv header (get rest 0)
        fields (cut rest 1 None)
        where (+ "defrecord " (str name)))
  (refuse-unknown-keys header #(":tags" ":check" ":failure" ":class-vars") where)
  (setv checks (declared-value header ":check")
        tags (declared-value header ":tags")
        failure (declared-value header ":failure")
        class-vars (declared-value header ":class-vars"))
  ;; :class-vars = an internal port that only the defwire expansion passes: a list of ClassVar annotations `#^ (get ClassVar T) name` placed in the class body.
  ;; Annotations to show attribute types to the type checker; carried separately from the field sequence (fields), so they do not mix into field reading, the names of :check, dataclass / pydantic fields,
  ;; __match_args__, or constructor arguments (dataclass does not make ClassVar annotations into fields).
  (when (and (is-not class-vars None)
             (not (and (isinstance class-vars List)
                       (all (gfor form class-vars
                                  (and (isinstance form Expression) (= (len form) 3)
                                       (= (str (get form 0)) "annotate") (isinstance (get form 1) Symbol)
                                       (isinstance (get form 2) Expression) (= (str (get (get form 2) 0)) "get")
                                       (.endswith (hy.repr (get (get form 2) 1)) "ClassVar")))))))
    (raise (SyntaxError (.format "{}: :class-vars must be a list of ClassVar annotations #^ (get ClassVar type) name: {}" where (hy.repr class-vars)))))
  (when (and (is-not failure None) (not (and (isinstance failure Symbol) (in (str failure) #("True" "False")))))
    (raise (SyntaxError (.format "{}: :failure must be a literal True or False (marker of a failure type): {}" where (hy.repr failure)))))
  (when (and (is-not checks None) (not (isinstance checks List)))
    (raise (SyntaxError (.format "{}: :check must be a list of check expressions (shape [(pred field) …]): {}" where (hy.repr checks)))))
  ;; The canonical source for how fields are read is field-targets of declarations (kept in line with defwire, hy-index, and doeff-linter).
  (setv names (lfor target (field-targets fields) (hy.mangle target)))
  (setv statements []
        bound [])
  (for [check (or checks [])]
    (when (and (isinstance check Expression) (>= (len check) 3)
               (isinstance (get check 0) Keyword) (= (str (get check 0)) ":"))
      (raise (SyntaxError (.format "{}: :check {} — write the field type in the field annotation #^ type (:check is only a boolean expression)" where (hy.repr check)))))
    ;; The fields the expression refers to (in field order). A dotted symbol such as `value.x` is read by its head name.
    (setv seen (set)
          pending [check])
    (while pending
      (setv form (.pop pending)
            head (when (isinstance form Symbol) (get (.split (str form) ".") 0)))
      (cond
        head (.add seen (hy.mangle head))
        (and (isinstance form Sequence) (not (isinstance form String))) (.extend pending form)))
    (setv used (lfor n names :if (in n seen) n))
    (when (not used)
      (raise (SyntaxError (.format "{}: :check {} references no field (fields = {})" where (hy.repr check) (.join " " names)))))
    (.extend bound (lfor n used :if (not-in n bound) n))
    ;; If the result is a Program / effect (the form that called a defk from :check), TypeError — a Program looks true and would silently pass.
    (setv verdict (hy.gensym "verdict")
          text (.lstrip (hy.repr check) "'"))
    ;; Most checks are boolean expressions (`(<= start end)`) whose answer is exactly True — then it is neither a Program nor false, so skip the other two
    ;; checks (the Program test and require-check). This runs once per check every time a record is built, and it was paying each time for the Program test
    ;; (isinstance of DoExpr goes through a Python metaclass) and the require-check call (five isinstance checks of :pre and a dict of values).
    ;; An answer that is not True (such as a regular-expression Match) is tested for Program as before, and
    ;; require-check is called only when false — require-check does nothing unless false (it just returns None when true), so what it refuses and its wording are the same.
    ;; The type-check expansion widens the answer to object — a check expression returning `Match | None` compared with `is not True` gives a red "always true"
    ;; (reportUnnecessaryComparison) that the writer cannot fix. The runtime expansion puts the answer as is.
    ;; The Program test goes only in the runtime expansion: put in the type-check expansion, pyright 1.1.414 read the type of
    ;; `isinstance(v, (DoExpr, EffectBase))` (DoExpr is a union of program nodes in type checking), depending on a file checked earlier in the same process,
    ;; as `tuple[UnionType, type[EffectBase[_Answer]]]` and raised a red reportArgumentType on class_or_tuple
    ;; (agora-controllers daily run — only when the whole repo was measured with `--threads 2`, and only one of two of the same shape in the same file). The writer cannot fix it.
    ;; The test exists only to refuse at runtime a :check that returned a Program, and adds nothing to the types of the type-check expansion (the answer is object).
    (.extend statements
      [`(setv ~verdict ~(if static-view `(_doeff-cast object ~check) check))
       `(when (is-not ~verdict True)
          ~@(if static-view
                []
                [`(when (isinstance ~verdict #(doeff_hy.record.DoExpr doeff_hy.record.EffectBase))
                    (raise (TypeError ~(.format "{} :check {} returned a Program — write :check as a pure expression (a defk cannot be called at construction)" name text))))])
          (when (not ~verdict)
            (doeff_hy._record_check.require-check
              ~(str name) #(~@(lfor n used (String (hy.unmangle n)))) ~text
              (bool ~verdict) {~@(sum (lfor n used [(String (hy.unmangle n)) (Symbol n)]) [])})))]))
  (setv post-init (if statements
                      [`(defn __post-init__ [self]
                          ~@(lfor n bound `(setv ~(Symbol n) (. self ~(Symbol n))))
                          ~@statements
                          None)]
                      []))
  (setv written (when (not static-view) (class-form docstring class-vars fields post-init)))
  (_scoped-definition _hy-compiler name
    [`(do
        ;; The require-check of :check lives in the inner doeff_hy._record_check (read by both the runtime and type-check expansions).
        (import doeff_hy._declarations doeff_hy.record ~@(if (is written None) [] ['doeff_hy._record_runtime]) ~@(if statements ['doeff_hy._record_check] []))
        ~@(if (and static-view statements) [(import-form ["cast"])] [])
        ~@(if (is written None)
              [`(defclass [(dataclass :frozen True :kw-only True)] ~name []
                  ~@(if (is docstring None) [] [docstring])
                  ~@(or class-vars [])
                  ~@fields
                  ~@post-init)]
              [written])
        (setattr ~name "__doeff_tags__" ~(tags-form tags where))
        (setattr ~name "__doeff_checks__" #(~@(lfor c (or checks []) (String (.lstrip (hy.repr c) "'")))))
        (setattr ~name "__doeff_failure__" ~(if (and (is-not failure None) (= (str failure) "True")) 'True 'False)))]
    :nested-reads True))


  ;; Type at the JSON boundary (the "defwire" of the header note). Fields are read the same way as defrecord (the expansion uses defrecord as is).
  ;; Computing wire names is needed only at expansion time, so it is placed inside the macro body (same reason as defrecord — splitting it out makes a defn).
(defmacro defwire [_hy-compiler name #* forms]
  (import re)
  (import hy.models [Dict Expression Keyword List String Symbol])
  (import doeff_hy._declarations [declared-value field-targets refuse-unknown-keys])
  (import doeff_hy._expansion_names [import-form])
  (import doeff_hy.macros [_scoped-definition])
  (when (not (isinstance name Symbol))
    (raise (SyntaxError (.format "defwire's first argument must be exactly one type name (symbol): {}" (hy.repr name)))))
  (setv where (+ "defwire " (str name))
        docstring None
        rest (list forms))
  (when (and rest (isinstance (get rest 0) String))
    (setv docstring (get rest 0)
          rest (cut rest 1 None)))
  (when (not (and rest (isinstance (get rest 0) Dict)))
    (raise (SyntaxError (.format "{}: a header dict {{:names … :unknown …}} is required right after the name (and docstring) — the copy of wire field names is not decided silently" where))))
  (setv header (get rest 0)
        fields (cut rest 1 None))
  (refuse-unknown-keys header #(":tags" ":check" ":names" ":unknown") where)
  (setv names-form (declared-value header ":names")
        unknown-form (declared-value header ":unknown"))
  (when (is names-form None)
    (raise (SyntaxError (.format "{}: :names is required — :camel / :snake / :kebab or {{field \"wire name\" …}} (all fields)" where))))
  (setv unknown (cond
                  (is unknown-form None) "reject"
                  (and (isinstance unknown-form Keyword) (in (str unknown-form) #(":reject" ":ignore"))) (cut (str unknown-form) 1 None)
                  True (raise (SyntaxError (.format "{}: :unknown must be :reject or :ignore: {}" where (hy.repr unknown-form))))))
  ;; Field names (read the same way as defrecord — in the order written).
  (setv targets (field-targets fields))
  ;; Field name → wire name. :camel = join with the head of each segment after the first capitalized (lane-id → laneId); :snake = separator _ (lane_id);
  ;; :kebab = separator - (lane-id). Separators = - and _. An explicit dict names all fields exactly (never silently falls back to the default copy).
  (setv wire-names {})
  (cond
    (isinstance names-form Dict)
      (do
        (setv explicit {})
        (for [#(key value) (zip (cut names-form None None 2) (cut names-form 1 None 2))]
          (when (not (and (isinstance key Symbol) (isinstance value String) (str value)))
            (raise (SyntaxError (.format "{}: the :names dict must be {{field \"wire name\" …}} (fields are symbols, wire names are non-empty strings): {} {}"
                                         where (hy.repr key) (hy.repr value)))))
          (setv (get explicit (hy.mangle key)) (str value)))
        (setv declared (lfor t targets (hy.mangle t))
              missing (lfor n declared :if (not-in n explicit) (hy.unmangle n))
              extra (lfor n explicit :if (not-in n declared) (hy.unmangle n)))
        (when missing
          (raise (SyntaxError (.format "{}: the :names dict is missing fields: {}" where (.join " " missing)))))
        (when extra
          (raise (SyntaxError (.format "{}: the :names dict has unknown fields: {}" where (.join " " extra)))))
        (for [t targets]
          (setv (get wire-names (hy.mangle t)) (get explicit (hy.mangle t)))))
    (and (isinstance names-form Keyword) (in (str names-form) #(":camel" ":snake" ":kebab")))
      (for [t targets]
        (when (not (re.fullmatch r"[A-Za-z][A-Za-z0-9_-]*" (str t)))
          (raise (SyntaxError (.format "{}: cannot derive a wire name from the name of field {} (only [A-Za-z0-9_-] starting with a letter) — write :names as a dict" where t))))
        (setv parts (lfor p (re.split r"[-_]" (str t)) :if p p)
              (get wire-names (hy.mangle t))
                (cond
                  (= (str names-form) ":camel") (+ (get parts 0) (.join "" (lfor p (cut parts 1 None) (+ (.upper (get p 0)) (cut p 1 None)))))
                  (= (str names-form) ":snake") (.join "_" parts)
                  True (.join "-" parts))))
    True
      (raise (SyntaxError (.format "{}: :names must be :camel / :snake / :kebab or {{field \"wire name\" …}}: {}" where (hy.repr names-form)))))
  (setv seen {})
  (for [#(field wire) (.items wire-names)]
    (when (in wire seen)
      (raise (SyntaxError (.format "{}: fields {} and {} map to the same wire name {!r}" where (hy.unmangle (get seen wire)) (hy.unmangle field) wire))))
    (setv (get seen wire) field))
  ;; Header dict passed to defrecord (only :tags and :check — :names and :unknown are the wire shape).
  ;; :class-vars puts the annotation `__doeff_wire__: ClassVar[WireShape]` in the type body (to show the type checker the type of the value the setattr below sets —
  ;; so the argument WireValue of dump is read as satisfied by a defwire type. setattr drops out of the type-check expansion as bookkeeping).
  (setv record-header (Dict (+ (sum (lfor key #(":tags" ":check")
                                          :if (is-not (declared-value header key) None)
                                          [(Keyword (cut key 1 None)) (declared-value header key)])
                                    [])
                               [(Keyword "class-vars")
                                `[#^ (get _doeff-ClassVar doeff_hy._wire.WireShape) __doeff-wire__]])))
  ;; Assembled to match where it was written (same as defrecord): inside a function body, put the imports below (`_doeff-ClassVar` and
  ;; doeff_hy), the type, and bookkeeping in an immediately-called function and bind only the type name. The inner defrecord wraps itself once more in that function
  ;; (two names of wrapping functions go into the type's __qualname__) — if the construction of the unwrapped type is split into another macro, that macro's reading
  ;; rule is not in the fixed table of the shared quality check (FIXED_MACROS of quality.hy_projection), and the check of record.hy goes unmeasured.
  (_scoped-definition _hy-compiler name [
     ;; Read the names the annotations read (up to Python 3.13 annotations are evaluated in the class body) before building the type. ClassVar is read as a from-import alias:
     ;; pyright recognizes a ClassVar dropped from dataclass fields only by the name `ClassVar` or the form `typing.ClassVar` (not by the re-exported
     ;; name of a module), and emitting `import typing` for every defwire makes strict flag the repeated import (reportDuplicateImport) in red.
     ;; The alias does not start with `__` (the name would be rewritten to private form in a class body and could not be read at runtime). The spelling and location are taken from the expansion's name table
     ;; (in the projection of a type written in a class body, post-processing moves the import with the table's spelling to the module level — static_check._synthesized_import).
     (import-form ["ClassVar"])
     '(import doeff_hy._wire)
     `(hy.R.doeff_hy/record.defrecord ~name ~@(if (is docstring None) [] [docstring]) ~record-header ~@fields)
     `(setattr ~name "__pydantic_config__" (doeff_hy._wire.wire-config ~(Dict (sum (lfor #(k v) (.items wire-names) [(String k) (String v)]) [])) ~unknown))
     `(setattr ~name "__doeff_wire__" (doeff_hy._wire.wire-shape ~name ~(Dict (sum (lfor #(k v) (.items wire-names) [(String k) (String v)]) [])) ~unknown ~(if (is docstring None) 'None docstring)))]
    :nested-reads True))


  ;; Compute the value from the member name at expansion time (outside the template-macro form — header note).
  ;; If a check helper is split into another function, the function called at expansion time cannot be a defk
  ;; (a defk returns a Program, and a macro needs a Hy model immediately), so it becomes a defn.
  ;; To avoid that, the computation is placed only inside the macro body.
(defmacro defenum [name #* members]
  (import re)
  (import hy.models [Expression String Symbol])
  (when (not (isinstance name Symbol))
    (raise (TypeError f"defenum's first argument must be an enum name (a symbol): {(hy.repr name)}")))
  (when (not members)
    (raise (ValueError f"defenum {name} has no members")))
  (setv pairs []
        seen-names {}
        seen-values {})
  (for [member members]
    (cond
      (isinstance member Symbol)
        (setv key member
              value (.replace (.lower (str member)) "_" "-"))
      (and (isinstance member Expression)
           (= (len member) 2)
           (isinstance (get member 0) Symbol)
           (isinstance (get member 1) String))
        (setv key (get member 0)
              value (str (get member 1)))
      True
        (raise (TypeError
                 f"defenum {name}: a member must be exactly NAME or (NAME \"value\"): {(hy.repr member)}")))
    (when (not (re.fullmatch r"[A-Za-z][A-Za-z0-9_-]*" (str key)))
      (raise (ValueError
               f"defenum {name}: a member name must start with a letter and match [A-Za-z][A-Za-z0-9_-]*: {key}")))
    (setv mangled (hy.mangle key))
    (when (in mangled seen-names)
      (raise (ValueError
               f"defenum {name}: member names collide: {(get seen-names mangled)} and {key}")))
    (when (in value seen-values)
      (raise (ValueError
               f"defenum {name}: value {(hy.repr value)} collides between {(get seen-values value)} and {key} (StrEnum would silently alias the later one)")))
    (setv (get seen-names mangled) key
          (get seen-values value) key)
    (.extend pairs [(Symbol mangled) (String value)]))
  `(defclass ~name [StrEnum] (setv ~@pairs)))
