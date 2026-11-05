;;; Frozen mapping -- an immutable mapping from string keys to values (FrozenMap), plus functions that deeply freeze and thaw JSON values.
;;;
;;; Why: if a dict is placed in a field of a frozen dataclass, the dataclass is frozen but anyone can still mutate the
;;; contents (an effect's input, answer, row, or settings silently change after creation). Values whose field names and
;;; types are fixed are represented with a dataclass or a pydantic model. Even so, "mappings with an open key set" --
;;; values whose inner shape is decided by the other party (CLI, store declaration), such as env, CLI settings, tool
;;; input, and the field -> value of a record row -- stay as mappings. Those mappings are frozen here with
;;; FrozenMap.
;;;
;;; How JSON values are frozen: freeze-json deeply turns object -> FrozenMap and array -> tuple (even the inner values cannot be changed).
;;; At the boundary where JSON is written (json.dumps, HTTP, DB), thaw-json turns them back into dict / list -- the conversion to dict happens at that one boundary only.
;;;
;;; Equality: that of collections.abc.Mapping (equal to a dict with the same key/value pairs). The hash works only when the inner values are hashable
;;; (JSON values frozen by freeze-json are always hashable). pickle and copy are preserved by rebuilding.
(import collections.abc [ItemsView Mapping Iterator ValuesView])
(import copy)
(import json)
(import typing [TypeVar])

(setv V (TypeVar "V"))


(defclass FrozenMap [(get Mapping #(str V))]
  "An immutable mapping from string keys to values. At construction it copies source (a mapping or a sequence of key/value pairs) and cannot be changed afterward.
   The inner values are kept as they are (deeply freezing JSON values is freeze-json's job)."
  (setv __slots__ #("_entries" "_hash" "_deep"))
  (#^ (get dict #(str V)) _entries)
  (#^ (| int None) _hash)
  ;; Whether the inner values have also been deeply frozen with freeze-json (true only when freeze-json or frozen-json-object built it). A frozen value cannot change,
  ;; so true stays correct -- calls that re-freeze a deeply frozen value (each time a row is listed, Row's __post_init__) return it as is without walking the inside.
  ;; Measured 2026-09-28: reading the record emulation's listing ran re-freezing 2.82 million times, taking 40% of the body of the automation worker emulation's check.
  (#^ bool _deep)

  (defn __init__ [self [source None]]
    (setv entries (if (is source None) {} (dict source)))
    (for [key entries]
      (when (not (isinstance key str))
        (raise (TypeError (.format "FrozenMap keys must be strings: {!r}" key)))))
    (object.__setattr__ self "_entries" entries)
    (object.__setattr__ self "_hash" None)
    (object.__setattr__ self "_deep" False))

  (defn #^ V __getitem__ [self #^ str key]
    (get self._entries key))

  (defn #^ (get Iterator str) __iter__ [self]
    (iter self._entries))

  (defn #^ int __len__ [self]
    (len self._entries))

  (defn #^ (get ItemsView #(str V)) items [self]
    "A view of key/value pairs = the inner dict's view (contents and order are the same as Mapping's default; the view is read-only and cannot change the inner dict). Mapping's default
     ItemsView calls __getitem__ in Python once per key, so turning a frozen value back into JSON (thaw-json) slows down in proportion to the number of keys
     (measured: +299,152 __getitem__ calls in the screen stats scenario)."
    (.items self._entries))

  (defn #^ (get ValuesView V) values [self]
    "A view of values = the inner dict's view (same reason as items)."
    (.values self._entries))

  (defn #^ "FrozenMap" updated [self #^ (get Mapping #(str V)) changes]
    "A new mapping with the keys of changes replaced or added (the original is unchanged). The inner dict is copied in one step, and only the keys of changes are checked -- copying with dict(mapping)
     calls Mapping's __getitem__ in Python once per key, so places that grow a mapping one entry at a time (the instrument cross-section -- it copied everything on every observation;
     2.68 million __getitem__ calls for 12,176 observations) slow down in proportion to the number of keys."
    (setv added (dict changes))
    (for [key added]
      (when (not (isinstance key str))
        (raise (TypeError (.format "FrozenMap keys must be strings: {!r}" key)))))
    (setv made (FrozenMap))
    (object.__setattr__ made "_entries" (| self._entries added))
    made)

  (defn #^ int __hash__ [self]
    (setv cached self._hash)
    (when (is cached None)
      (setv cached (hash (frozenset (.items self._entries))))
      (object.__setattr__ self "_hash" cached))
    cached)

  (defn #^ str __repr__ [self]
    (.format "FrozenMap({!r})" self._entries))

  (defn __setattr__ [self #^ str name #^ object value]
    (raise (AttributeError (.format "FrozenMap is immutable (tried to write field {!r})" name))))

  (defn __delattr__ [self #^ str name]
    (raise (AttributeError (.format "FrozenMap is immutable (tried to delete field {!r})" name))))

  (defn __reduce__ [self]
    #(FrozenMap #((dict self._entries))))

  (defn #^ "FrozenMap" __copy__ [self]
    "A shallow copy = itself (the mapping cannot change, so there is no need to separate the copy from the original -- same as copying a str or tuple)."
    self)

  (defn #^ "FrozenMap" __deepcopy__ [self #^ (get dict #(int object)) memo]
    "Deep copy: a deeply frozen mapping (built by freeze-json or frozen-json-object) cannot change even inside, so it returns itself; any other mapping builds a new mapping with
     its inner values deeply copied (it may hold mutable values). Deeply frozen mappings are shared rather than copied because the test foundation deep-copied a 50,000-row store
     for every test, rebuilding even the immutable row values (#2670 -- about half of the 2-second copy)."
    (if self._deep
        self
        (FrozenMap (copy.deepcopy self._entries memo)))))


;; JSON leaf types (values of exactly these types pass through unchanged in both freezing and thawing) -- subtypes (such as children of bool) are handled by the general branches below.
(setv JSON-LEAF-TYPES (frozenset #(str int float bool (type None))))


;; Name of the type of deeply frozen JSON values (the values inside mappings built by freeze-json and frozen-json-object). Type checking reads FrozenJson in the type declaration frozen.pyi
;; (a recursive alias for deeply frozen JSON values: mappings are FrozenMap and sequences are tuple). This is the runtime name that lets a Hy module write `(get FrozenMap FrozenJson)`
;; in a field annotation; its value is a one-level union of the outer types only (a recursive alias cannot be written at Python 3.10 runtime). Previously there was no runtime
;; name, so a field for a deeply frozen JSON mapping could only be written as FrozenMap with no type argument (#4257).
;; Do not put generic types (FrozenMap[object], tuple[object, ...]) in the union: the contract (: x FrozenJson) and the binding (<- x FrozenJson e) pass the name straight to
;; isinstance (type_forms.py maps only literal generic types to the outer type), so it raised TypeError for every value (agora's reception
;; returned 500). Only the outer type is checked at runtime (the decision of #1790).
(setv FrozenJson (| FrozenMap tuple str int float bool None))


(defn #^ bool deeply-frozen? [#^ object value]
  "Whether value is a FrozenMap deeply frozen by freeze-json (to skip re-freezing)."
  (and (isinstance value FrozenMap) value._deep))


(defn #^ FrozenMap deep-frozen-map [#^ object value]
  "Build a FrozenMap whose inner values are deeply frozen and mark it deeply frozen (the single point shared by freeze-json and frozen-json-object).
   Among the inner values, leaves (exactly the JSON-LEAF-TYPES types) and deeply frozen FrozenMaps are returned in place, and freeze-json is called only for the rest --
   the layers of the write path (dump of the row type -> decision diff -> value to commit -> listed row) rebuild new mappings from the elements of deeply frozen values and re-freeze them,
   so starting freeze-json for every element would walk the same value 5 times per row (
   function starts). The order of checks is the same as freeze-json."
  (setv frozen (FrozenMap (gfor #(key item) (.items value)
                                #(key (if (or (in (type item) JSON-LEAF-TYPES)
                                              (and (is (type item) FrozenMap) item._deep))
                                          item
                                          (freeze-json item))))))
  (object.__setattr__ frozen "_deep" True)
  frozen)


(defn #^ object freeze-json [#^ object value]
  "Deeply freeze a JSON value: object (mapping) -> FrozenMap, array (list / tuple) -> tuple. Other values pass through.
   Passing an already frozen value returns the same shape (idempotent) -- a deeply frozen FrozenMap is returned as is without walking the inside.
   Concrete types (FrozenMap, dict) are checked before the abstract Mapping -- each isinstance against Mapping starts an ABC check in Python.
   Leaves inside a sequence and deeply frozen FrozenMaps are returned without calling itself, as in deep-frozen-map."
  (setv kind (type value))
  (cond
    (in kind JSON-LEAF-TYPES) value
    (is kind FrozenMap) (if value._deep value (deep-frozen-map value))
    (is kind dict) (deep-frozen-map value)
    (deeply-frozen? value) value
    (isinstance value Mapping) (deep-frozen-map value)
    (isinstance value #(list tuple)) (tuple (gfor item value
                                                  (if (or (in (type item) JSON-LEAF-TYPES)
                                                          (and (is (type item) FrozenMap) item._deep))
                                                      item
                                                      (freeze-json item))))
    True value))


(defn #^ object freeze-json-text [#^ str text]
  "Parse a JSON string into a deeply frozen JSON value (object -> FrozenMap, array -> tuple). A string that cannot be parsed raises json.JSONDecodeError.
   The answer of json.loads is typed Any, so passing it straight to freeze-json at the call site cannot pick among the overloaded type declarations, and the answer spreads as Unknown.
  Combining parsing and freezing into one function fixes the answer type as FrozenJson."
  (freeze-json (json.loads text)))


(defn #^ object thaw-json [#^ object value]
  "Turn a frozen JSON value back into a form json.dumps accepts: mapping -> dict, tuple / list -> list (new values -- the original is unchanged).
   Call it only at the boundary where JSON is written.
   Leaves (string, number, boolean, None) and FrozenMap are separated first by concrete type -- each isinstance against the abstract Mapping
   goes through an ABC check, so walking deeply every time a record row is read into a type is slow (measured 2026-09-28: 4.2 million times in one automation worker emulation check).
   Leaves inside a container (exactly the JSON-LEAF-TYPES types) are returned in place, and it calls itself only for containers -- calling itself for every leaf would start
   as many function calls as there are JSON nodes each time a record row is read into a type (measured: about 36 per row in the screen stats scenario).
   The mapping branch and the sequence branch are merged into one each, and the order of checks (FrozenMap -> plain list/tuple -> abstract Mapping -> subclasses of list/tuple) is unchanged."
  (setv kind (type value))
  (when (in kind JSON-LEAF-TYPES)
    (return value))
  (setv entries (cond
                  (is kind FrozenMap) value._entries
                  (in kind #(list tuple)) None
                  (isinstance value Mapping) value
                  True None))
  (cond
    (is-not entries None) (dfor #(key item) (.items entries)
                                key (if (in (type item) JSON-LEAF-TYPES) item (thaw-json item)))
    (isinstance value #(list tuple)) (lfor item value (if (in (type item) JSON-LEAF-TYPES) item (thaw-json item)))
    True value))


(defn #^ FrozenMap frozen-json-object [#^ object value #^ str what]
  "A FrozenMap that deeply freezes a JSON object (mapping). Raises TypeError if it is not a mapping (what = the field name used in the error message).
   Used by a frozen dataclass's __post_init__ to freeze the mapping it received."
  (setv kind (type value))
  (when (not (or (is kind FrozenMap) (is kind dict) (isinstance value Mapping)))
    (raise (TypeError (.format "{} must be a mapping (field name -> JSON value): {!r}" what value))))
  (if (if (is kind FrozenMap) value._deep (deeply-frozen? value)) value (deep-frozen-map value)))


(defn #^ FrozenMap frozen-map-of [#^ object value #^ str what]
  "A FrozenMap that shallowly copies a mapping (inner values are kept as they are -- for when the values are frozen types such as dataclasses). Raises TypeError if it is not a mapping."
  (when (not (isinstance value Mapping))
    (raise (TypeError (.format "{} must be a mapping: {!r}" what value))))
  (if (isinstance value FrozenMap) value (FrozenMap value)))
