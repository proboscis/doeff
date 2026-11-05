;;; Checks that the behavior of a defrecord type (frozen, equality, hash, repr, defaults, kw-only, field table) is the same as a standard dataclass with the same fields
;;; (the functions are written at expansion time, and load time does not make dataclass compile function source).
;;;
;;; What is checked: a record with every field shape (no default, literal default, default-factory, repr off, compare off, init off, :check), and
;;; a standard dataclass with the same fields written by hand, are compared on construction, comparison, hash, repr, freezing, kw-only, fields, replace, asdict, __match_args__,
;;; __dataclass_params__, and :check rejection. Failure case (red before the fix): the record's functions live in the module's code (the .hy file),
;;; not in code that dataclass exec'd at load time (<string>).
;;; Exposed through test_defrecord_shape.py (exposed as is, without rewrapping - ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses)
(import dataclasses [dataclass field FrozenInstanceError])
(import pickle)
(import copy)
(import sys)
(import pytest)
(import doeff [Pure])

(defrecord Shape
  "A record with every field shape."
  {:check [(>= a 0)]}
  #^ int a
  (setv #^ int b 2)
  (setv #^ list c (field :default-factory list))
  (setv #^ int d (field :repr False :default 0))
  (setv #^ int e (field :compare False :default 1))
  (setv #^ int f (field :init False :default 9))
  (setv #^ str job-id "j"))

(defclass [(dataclass :frozen True :kw-only True)] Twin []
  "A standard dataclass with the same fields written by hand (the one to compare against)."
  #^ int a
  (setv #^ int b 2)
  (setv #^ list c (field :default-factory list))
  (setv #^ int d (field :repr False :default 0))
  (setv #^ int e (field :compare False :default 1))
  (setv #^ int f (field :init False :default 9))
  (setv #^ str job-id "j"))

(defrecord Bare
  #^ int x)

(defrecord Empty
  "A record with no fields.")


(defk same-fields [left right]
  {:pre [(: left type) (: right type)] :post [(: % list)] :tags {:context "doeff-hy" :role "judgment"}}
  "To line up the field tables of the two types (name, has default, has default-factory, init, repr, compare, hash, kw_only)."
  (lfor #(l r) (zip (dataclasses.fields left) (dataclasses.fields right))
        #((= l.name r.name) (= (is l.default dataclasses.MISSING) (is r.default dataclasses.MISSING))
          (= l.default r.default) (= (is l.default-factory dataclasses.MISSING) (is r.default-factory dataclasses.MISSING))
          (= l.init r.init) (= l.repr r.repr) (= l.compare r.compare) (= l.hash r.hash) (= l.kw-only r.kw-only))))


(deftest test-a-record-builds-like-its-dataclass-twin
  (<- _ (Pure None))
  (val s (Shape :a 1))
  (val t (Twin :a 1))
  (<- rows list (same-fields Shape Twin))
  (assert (= (dataclasses.asdict s) (dataclasses.asdict t)) #((dataclasses.asdict s) (dataclasses.asdict t)))
  (assert (is-not (. (Shape :a 1) c) (. (Shape :a 1) c)))
  (assert (= (. (Shape :a 1 :c [5]) c) [5]))
  (assert (= (. (Shape :a 1) f) 9))
  (assert (= (lfor f (dataclasses.fields Shape) f.name) (lfor f (dataclasses.fields Twin) f.name)))
  (assert (all (gfor row rows (all row))) rows)
  ;; Field defaults are held by the field table (for a slots type, the class attribute named after a field is a slot descriptor - same as dataclass slots=True).
  (val defaults (dfor f (dataclasses.fields Shape) f.name f.default))
  (assert (= #((get defaults "b") (get defaults "d")) #(2 0)) defaults)
  (assert (is (get defaults "c") dataclasses.MISSING))
  (assert (dataclasses.is-dataclass Shape))
  (assert (= Shape.__match_args__ Twin.__match_args__ #()))
  (assert (= (repr Shape.__dataclass_params__) (repr Twin.__dataclass_params__)) #(Shape.__dataclass_params__ Twin.__dataclass_params__)))


(deftest test-a-record-compares-hashes-and-prints-like-its-twin
  (<- _ (Pure None))
  (assert (= (Shape :a 1 :e 5) (Shape :a 1 :e 6)))
  (assert (= (Twin :a 1 :e 5) (Twin :a 1 :e 6)))
  (assert (!= (Shape :a 1) (Shape :a 2)))
  (assert (!= (Shape :a 1) (Twin :a 1)))
  (assert (is (.__eq__ (Shape :a 1) 3) NotImplemented))
  (assert (= (hash (Bare :x 3)) (hash (Bare :x 3))))
  (assert (= (hash (Shape :a 1 :c #())) (hash (Shape :a 1 :c #() :e 7))))
  (assert (= (.replace (repr (Shape :a 1)) "Shape" "Twin") (repr (Twin :a 1))) #((repr (Shape :a 1)) (repr (Twin :a 1))))
  (assert (= (repr (Empty)) "Empty()"))
  (assert (= (Empty) (Empty))))


(deftest test-a-record-is-frozen-and-keyword-only-like-its-twin
  (<- _ (Pure None))
  (val s (Shape :a 1))
  (with [caught (pytest.raises FrozenInstanceError)]
    (setattr s "a" 2))
  (assert (= (str caught.value) "cannot assign to field 'a'"))
  (with [caught (pytest.raises FrozenInstanceError)]
    (delattr s "b"))
  (assert (= (str caught.value) "cannot delete field 'b'"))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr s "other" 1))
  (with [(pytest.raises TypeError)]
    (Bare 3))
  (with [(pytest.raises TypeError)]
    (Bare))
  (with [(pytest.raises TypeError)]
    (Shape :a 1 :f 3)))


(deftest test-a-record-copies-replaces-and-pickles-like-its-twin
  (<- _ (Pure None))
  (val s (Shape :a 1 :b 4))
  (assert (= (dataclasses.replace s :a 3) (Shape :a 3 :b 4)))
  ;; copy.replace (the __replace__ entry point) is available from Python 3.13.
  (when (>= sys.version-info #(3 13))
    (assert (= (copy.replace s :b 5) (Shape :a 1 :b 5))))
  (assert (= (pickle.loads (pickle.dumps (Bare :x 2))) (Bare :x 2)))
  (assert (= (copy.deepcopy s) s)))


(deftest test-a-record-still-refuses-a-value-out-of-shape
  (<- _ (Pure None))
  (with [caught (pytest.raises ValueError)]
    (Shape :a -1))
  (assert (= (str caught.value) "Shape: field a failed check (>= a 0): a=-1")))


(deftest test-a-record-methods-are-module-code-not-compiled-at-import
  ;; Failure case (red before the fix): dataclass builds functions by exec of their source at load time (co_filename = "<string>"). After the fix,
  ;; the functions written by the expansion go into the .pyc as the module's code.
  (<- _ (Pure None))
  ;; __repr__ is wrapped by a recursion guard (reprlib.recursive_repr) - look at the code of the wrapped function. The wrapper has __wrapped__ only from Python 3.13
  ;; on; before that only the wrapper's code (reprlib.py) is visible, so up to 3.12 __repr__ is not counted.
  ;; __init__, __eq__, and __hash__ are per-type functions, while __repr__ and freezing are one shared pair in record_runtime.
  (val own (lfor n #("__init__" "__eq__" "__hash__") #(n (. (getattr Shape n) __code__ co-filename))))
  (val shared (lfor n (+ #("__setattr__" "__delattr__") (if (>= sys.version-info #(3 13)) #("__repr__") #()))
                    #(n (. (getattr (getattr Shape n) "__wrapped__" (getattr Shape n)) __code__ co-filename))))
  (assert (all (gfor #(n source) own (.endswith source "defrecord_shape_deftests.hy"))) own)
  (assert (all (gfor #(n source) shared (.endswith source "record_runtime.py"))) shared))


(deftest test-records-share-one-set-of-methods-and-keep-no-runtime-text
  ;; Failure case (red before the fix): __repr__, __setattr__, and __delattr__ functions were written per type, annotations were written on __init__ arguments,
  ;; and the type carried a docstring - with about 1,760 types in a job, functions, annotations, and statements exceeded 10 MB.
  (<- _ (Pure None))
  (val shared #("__repr__" "__setattr__" "__delattr__"))
  (assert (all (gfor n shared (is (getattr Shape n) (getattr Bare n) (getattr Empty n)))))
  (assert (= Shape.__init__.__annotations__ {}) Shape.__init__.__annotations__)
  ;; The docstring remains only in the source and the type-check projection; the runtime __doc__ is built from the field names (dataclass does not compute the signature at load).
  (assert (= Shape.__doc__ "Shape(*, a, b, c, d, e, f, job_id)") Shape.__doc__)
  (assert (= (. (Bare :x 1) __doc__) "Bare(*, x)"))
  (assert (= Empty.__doc__ "Empty()"))
  ;; Field types remain in the class annotations (read by dataclass and the type checker).
  (assert (is (. (get (dataclasses.fields Bare) 0) type) int)))


(defclass Grown [Bare]
  "A plain class that subclasses a defrecord type (a child type without the freeze guard).")


(deftest test-a-plain-subclass-of-a-record-guards-only-the-fields
  ;; The shared frozen __setattr__ rejects every name for values of the guarded type itself, and only field names for values of an unguarded child type (same as dataclass
  ;; frozen - the per-type form read the defined type through __class__).
  (<- _ (Pure None))
  (val g (Grown :x 1))
  (setattr g "note" 2)
  (assert (= g.note 2))
  (delattr g "note")
  (with [(pytest.raises FrozenInstanceError)]
    (setattr g "x" 3))
  (assert (= (repr g) "Grown(x=1)"))
  (assert (!= g (Bare :x 1))))


(deftest test-a-record-value-keeps-its-fields-in-slots-not-a-dict
  ;; Failure case (red before the fix): the runtime expansion had no slots, and each value carried a __dict__ dict (record __dict__ was
  ;; about 0.6 GB on the production screen). After the fix the fields are in slots and values have no __dict__. The weakref entry point is kept.
  (<- _ (Pure None))
  (import weakref)
  (val s (Shape :a 1 :b 4 :c [1]))
  (assert (not (hasattr s "__dict__")))
  (assert (= (set Shape.__slots__) #{"a" "b" "c" "d" "e" "f" "job_id" "__weakref__"}) Shape.__slots__)
  (assert (is (weakref.ref s) (weakref.ref s)))
  ;; A field with init off gets the default from the type's field table (a slots type does not keep field defaults as class attributes).
  (assert (= s.f 9))
  ;; pickle and copy restore values without going through the frozen __setattr__.
  (assert (= (pickle.loads (pickle.dumps s)) s))
  (assert (= (copy.copy s) s))
  ;; A value pickled before the move to slots (state is the __dict__ dictionary) can also be restored.
  (val old (.__new__ object Shape))
  (.__setstate__ old {"a" 1 "b" 4 "c" [1] "d" 0 "e" 1 "f" 9 "job_id" "j"})
  (assert (= old s))
  ;; For a value of an unguarded child type, names placed in the child's __dict__ are restored too.
  (val g (Grown :x 1))
  (setattr g "note" 2)
  (val back (pickle.loads (pickle.dumps g)))
  (assert (= #(back.x back.note) #(1 2))))


(defrecord Checked
  "A record whose body has a __post_init__ with annotations (same style as doeff-cluster's EnvVar, RepoCheckout, RuntimeEnv)."
  #^ int n
  (defn #^ None __post-init__ [self]
    (when (< self.n 0)
      (raise (ValueError "n must be 0 or more")))))


(deftest test-an-annotated-post-init-written-in-the-body-still-runs
  ;; Failure case (red before the fix - daily 10-11 08:00 doeff 044fd20a9, notice of vg-w44): Hy reads the name of (defn #^ None __post-init__ ...) as
  ;; (annotate __post-init__ None). If the expansion's own-method? looks only at symbol names, it overlooks the body's __post_init__ and the expansion
  ;; writes __init__, so __post_init__ is never called (doeff-cluster's input check does not work and values that should be rejected pass).
  (<- _ (Pure None))
  (assert (= (. (Checked :n 1) n) 1))
  (with [caught (pytest.raises ValueError)]
    (Checked :n -1))
  (assert (= (str caught.value) "n must be 0 or more")))


(defrecord Shown
  "A record whose body has its own __repr__ with annotations."
  #^ int x
  (defn #^ str __repr__ [self]
    f"<Shown {self.x}>"))


(deftest test-an-annotated-repr-written-in-the-body-is-kept
  ;; A custom __repr__ written with annotations is also not overwritten by what the expansion writes (own-method? looks at all functions the expansion writes, not only __post_init__).
  (<- _ (Pure None))
  (assert (= (repr (Shown :x 3)) "<Shown 3>")))


(deftest test-the-seal-refuses-a-post-init-the-written-init-does-not-call
  ;; Failure case (second line of defense, red before the fix): if the expansion overlooks the body's own __post_init__ and writes __init__,
  ;; the input check silently stops working (the regression in 424a87f58 reached the production coordinator until found by 8 red daily checks). seal_record, which finishes the record
  ;; the expansion wrote, rejects at load time when the type has __post_init__ but the expansion's __init__ does not call it.
  (<- _ (Pure None))
  (import doeff_hy._record_runtime [seal-record])
  (defclass [(dataclass :frozen True :kw-only True)] Missed []
    #^ int n
    (defn __post-init__ [self] None))
  (with [caught (pytest.raises TypeError)]
    (seal-record Missed #() False))
  (assert (in "__post_init__" (str caught.value)) (str caught.value))
  (seal-record Missed #() True))
