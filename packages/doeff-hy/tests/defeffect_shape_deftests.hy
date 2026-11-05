;;; Checks that the behavior of a defeffect type (frozen, positional and named arguments, defaults, equality, hash, repr, field table, :pre rejection) is the same as a standard frozen
;;; dataclass with the same fields (like defrecord, the functions are written at expansion time, and load time does not make dataclass
;;; compile function source).
;;;
;;; What is checked: an effect with field shapes (no default, literal default, expression default, :pre), and a standard dataclass over EffectBase with the same fields
;;; written by hand, are compared on construction (positional and named), comparison, hash, repr, freezing, fields, replace, __match_args__, __dataclass_params__, and :pre rejection.
;;; Failure case (red before the fix): the effect's functions live in the module's code (the .hy file), not in code that dataclass exec'd at load time (<string>).
;;; Exposed through test_defeffect_shape.py (exposed as is, without rewrapping - ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defeffect <- val])
(import dataclasses)
(import dataclasses [dataclass FrozenInstanceError])
(import copy)
(import pickle)
(import sys)
(import pytest)
(import doeff [Pure EffectBase])

(defeffect Probe
  "An effect with field shapes."
  {:fields [(: table str) (: key tuple) (: limit int 10) (: label str (+ "a" "b"))]
   :pre [(>= limit 0)]
   :answer (| int None)
   :tags {:context "doeff-hy" :role "intent"}})

(defclass [(dataclass :frozen True)] Twin [EffectBase]
  "A standard dataclass with the same fields written by hand (the one to compare against)."
  #^ str table
  #^ tuple key
  (setv #^ int limit 10)
  (setv #^ str label (+ "a" "b")))

(defeffect Bare
  {:answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Holder
  "An effect holding one value (to look at how equality compares)."
  {:fields [(: item object)]
   :answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defclass [(dataclass :frozen True)] HolderTwin [EffectBase]
  "A standard dataclass with the same fields as Holder, written by hand."
  #^ object item)

(defclass Loose []
  "A value whose == returns a non-boolean (same shape as a numpy array)."
  (defn __eq__ [self other] "loose")
  (setv __hash__ None))

(defeffect Named
  "An effect that has builtin names (object, type) as field names."
  {:fields [(: object int) (: type str "t")]
   :answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Dashed
  "An effect whose :pre reads a field whose name contains -."
  {:fields [(: max-count int) (: label str "x")]
   :pre [(>= max-count 0)]
   :answer None
   :tags {:context "doeff-hy" :role "intent"}})


(deftest test-an-effect-builds-like-its-dataclass-twin
  (<- _ (Pure None))
  (val p (Probe "t" #("k")))
  (val t (Twin "t" #("k")))
  (assert (= #(p.table p.key p.limit p.label) #(t.table t.key t.limit t.label)))
  (assert (= (Probe :table "t" :key #("k") :limit 3) (Probe "t" #("k") 3)))
  (assert (= (lfor f (dataclasses.fields Probe) #(f.name (is f.default dataclasses.MISSING) f.default f.init f.repr f.compare f.kw-only))
             (lfor f (dataclasses.fields Twin) #(f.name (is f.default dataclasses.MISSING) f.default f.init f.repr f.compare f.kw-only))))
  (assert (= Probe.__match_args__ Twin.__match_args__))
  (assert (dataclasses.is-dataclass Probe))
  (assert (isinstance p EffectBase))
  (assert (= (repr Probe.__dataclass_params__) (repr Twin.__dataclass_params__)) #(Probe.__dataclass_params__ Twin.__dataclass_params__))
  (with [(pytest.raises TypeError)]
    (Probe "t"))
  (with [(pytest.raises TypeError)]
    (Probe "t" #() 1 "x" "extra")))


(deftest test-an-effect-compares-hashes-and-prints-like-its-twin
  (<- _ (Pure None))
  (assert (= (Probe "t" #("k")) (Probe "t" #("k"))))
  (assert (!= (Probe "t" #("k")) (Probe "u" #("k"))))
  (assert (!= (Probe "t" #("k")) (Twin "t" #("k"))))
  (assert (is (.__eq__ (Probe "t" #()) 3) NotImplemented))
  (assert (= (hash (Probe "t" #("k"))) (hash (Twin "t" #("k")))))
  (assert (= (.replace (repr (Probe "t" #("k"))) "Probe" "Twin") (repr (Twin "t" #("k")))) #((repr (Probe "t" #("k"))) (repr (Twin "t" #("k")))))
  (assert (= (repr (Bare)) "Bare()"))
  (assert (= (Bare) (Bare))))


(deftest test-an-effect-compares-field-by-field-like-its-twin
  ;; Failure case (red before the fix): Python 3.14's dataclass __eq__ is "true if same object, otherwise chain per-field == with and", and it differs from tuple comparison
  ;; (which does not call == for identical elements and coerces the answer to bool) for NaN and for a == that returns a non-boolean.
  ;; dataclass __eq__ compares per field only from Python 3.13 - the twin up to 3.12 compares as tuples, so the effect's answer is
  ;; compared against a fixed value, and "same as the twin" is checked only from 3.13.
  (<- _ (Pure None))
  (val nan (float "nan"))
  (val twin-compares-fields (>= sys.version-info #(3 13)))
  (assert (= (= (Holder nan) (Holder nan)) False))
  (assert (or (not twin-compares-fields) (= (= (HolderTwin nan) (HolderTwin nan)) False)))
  (val h (Holder nan))
  (assert (= h h))
  (assert (= (.__eq__ (Holder (Loose)) (Holder (Loose))) "loose"))
  (assert (or (not twin-compares-fields) (= (.__eq__ (HolderTwin (Loose)) (HolderTwin (Loose))) "loose"))))


(deftest test-an-effect-with-builtin-field-names-builds-like-its-dataclass
  ;; Failure case (red before the fix): if the expansion's __init__ sets fields with object.__setattr__, the field argument object shadows the builtin object
  ;; (dataclass's __init__ sets them through the alias __dataclass_builtins_object__, so it is not shadowed).
  (<- _ (Pure None))
  (val n (Named 1))
  (assert (= #(n.object n.type) #(1 "t")))
  (assert (= (Named :object 2 :type "u") (Named 2 "u"))))


(deftest test-an-effect-runs-its-pre-on-every-way-of-making-it
  ;; Check that defeffect has no hole like the 424a87f58 regression (the expansion did not call __post_init__ and the input check silently stopped working): :pre runs for every way of constructing - positional, named,
  ;; default, and replace - and also reads fields whose names contain -.
  (<- _ (Pure None))
  (assert (= (. (Dashed :max-count 2) max-count) 2))
  (for [make [(fn [] (Dashed -1)) (fn [] (Dashed :max-count -1)) (fn [] (Dashed -1 "y"))
              (fn [] (dataclasses.replace (Dashed 1) :max-count -1))
              ;; copy.replace (the __replace__ entry point) is available from Python 3.13 - before that it cannot be called (do not let an AttributeError silently pass).
              #* (if (>= sys.version-info #(3 13)) [(fn [] (copy.replace (Dashed 1) :max-count -1))] [])]]
    (with [(pytest.raises Exception)]
      (make))))


(deftest test-an-effect-pickles-like-its-twin
  (<- _ (Pure None))
  (val p (Probe "t" #("k") 3))
  (assert (= (pickle.loads (pickle.dumps p)) p))
  (assert (= (pickle.loads (pickle.dumps (Dashed 1))) (Dashed 1))))


(deftest test-the-seal-refuses-a-post-init-the-written-init-does-not-call
  ;; Second line of defense (same as ec08cd77 for defrecord): seal_effect, which finishes the effect type, rejects at load time when the type has __post_init__ but the expansion's __init__ does not
  ;; call it.
  (<- _ (Pure None))
  (import doeff_hy._record_runtime [seal-effect])
  (defclass [(dataclass :init False :repr False :eq False)] Missed [EffectBase]
    #^ int n
    (defn __init__ [self n] (object.__setattr__ self "n" n) (.__post-init__ self))
    (defn __post-init__ [self] None))
  (with [(pytest.raises TypeError :match "__post_init__")]
    (seal-effect Missed #("n") False))
  (seal-effect Missed #("n") True))


(deftest test-the-seal-refuses-fields-the-expansion-read-differently
  ;; Second line of defense: the expansion reads field types and defaults literally. A field mark (default) given through a name, or a ClassVar type, is invisible to the expansion, and the __init__ the expansion wrote
  ;; would differ in shape from the dataclass field table - reject at load time (do not silently place a different value).
  (<- _ (Pure None))
  (with [(pytest.raises TypeError :match "field")]
    (hy.eval (hy.read "(do (require doeff-hy.macros [defeffect]) (import dataclasses [field])
                           (setv shared (field :default 5))
                           (defeffect Shared {:fields [(: n int shared)] :answer None :tags {:context \"doeff-hy\" :role \"intent\"}}))")
             :globals {"__name__" "defeffect_shape_shared"}))
  (with [(pytest.raises TypeError :match "ClassVar")]
    (hy.eval (hy.read "(do (require doeff-hy.macros [defeffect]) (import typing [ClassVar])
                           (defeffect Classy {:fields [(: n (get ClassVar int) 1)] :answer None :tags {:context \"doeff-hy\" :role \"intent\"}}))")
             :globals {"__name__" "defeffect_shape_classy"})))


(deftest test-an-effect-is-frozen-and-replaceable-like-its-twin
  (<- _ (Pure None))
  (val p (Probe "t" #()))
  (with [caught (pytest.raises FrozenInstanceError)]
    (setattr p "table" "u"))
  (assert (= (str caught.value) "cannot assign to field 'table'"))
  (with [caught (pytest.raises FrozenInstanceError)]
    (delattr p "limit"))
  (assert (= (str caught.value) "cannot delete field 'limit'"))
  (assert (= (dataclasses.replace p :limit 4) (Probe "t" #() 4)))
  ;; copy.replace (the __replace__ entry point) is available from Python 3.13.
  (when (>= sys.version-info #(3 13))
    (assert (= (copy.replace p :table "u") (Probe "u" #()))))
  (assert (= (copy.deepcopy p) p)))


(deftest test-an-effect-still-refuses-a-value-out-of-its-pre
  (<- _ (Pure None))
  (with [(pytest.raises Exception)]
    (Probe "t" #() -1)))


(deftest test-an-effect-keeps-its-answer-and-marks
  (<- _ (Pure None))
  (assert (is Probe.__doeff_defeffect__ True))
  (assert (= (. Probe __doeff_tags__) (. Bare __doeff_tags__))))


(deftest test-an-effect-methods-are-module-code-not-compiled-at-import
  ;; Failure case (red before the fix): dataclass builds functions by exec of their source at load time (co_filename = "<string>"). After the fix,
  ;; the functions written by the expansion go into the .pyc as the module's code.
  (<- _ (Pure None))
  ;; __repr__ is wrapped by a recursion guard (reprlib.recursive_repr) - look at the code of the wrapped function. The wrapper has __wrapped__ only from Python 3.13
  ;; on; before that only the wrapper's code (reprlib.py) is visible, so up to 3.12 __repr__ is not counted.
  (val names (+ #("__init__" "__eq__" "__hash__" "__setattr__" "__delattr__")
                (if (>= sys.version-info #(3 13)) #("__repr__") #())))
  (val sources (lfor n names #(n (. (getattr (getattr Probe n) "__wrapped__" (getattr Probe n)) __code__ co-filename))))
  (assert (all (gfor #(n source) sources (.endswith source "defeffect_shape_deftests.hy"))) sources))
