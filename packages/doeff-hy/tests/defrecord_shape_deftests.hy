;;; defrecord の型の振る舞い(frozen・等価・hash・repr・既定値・kw-only・欄の表)が、同じ欄の標準の dataclass と同じ事の検
;;; (card acp:kanban-issue:ki-a0cca3bdb9a0 — 展開の時に関数を書き、読み込みの時に dataclass に関数の source を compile させない)。
;;;
;;; 確かめること: 欄の形を全部持つ record(既定値なし・字面の既定値・default-factory・repr を切る・比べを切る・init を切る・:check)と、
;;; 同じ欄を手で書いた標準の dataclass を、作る・比べる・hash・repr・凍結・kw-only・fields・replace・asdict・__match_args__・
;;; __dataclass_params__・:check の断りで突き合わせる。失敗ケース(直す前は赤): record の関数は module の code(.hy の file)で、
;;; dataclass が読み込みの時に exec した code(<string>)ではない。
;;; 公開は test_defrecord_shape.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses)
(import dataclasses [dataclass field FrozenInstanceError])
(import pickle)
(import copy)
(import pytest)
(import doeff [Pure])

(defrecord Shape
  "欄の形を全部持つ record。"
  {:check [(>= a 0)]}
  #^ int a
  (setv #^ int b 2)
  (setv #^ list c (field :default-factory list))
  (setv #^ int d (field :repr False :default 0))
  (setv #^ int e (field :compare False :default 1))
  (setv #^ int f (field :init False :default 9))
  (setv #^ str job-id "j"))

(defclass [(dataclass :frozen True :kw-only True)] Twin []
  "同じ欄を手で書いた標準の dataclass(比べる相手)。"
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
  "欄の無い record。")


(defk same-fields [left right]
  {:pre [(: left type) (: right type)] :post [(: % list)] :tags {:context "doeff-hy" :role "judgment"}}
  "2 つの型の欄の表(名・既定値の有無・default-factory の有無・init・repr・compare・hash・kw_only)を並べるため。"
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
  (assert (= Shape.b 2))
  (assert (= Shape.d 0))
  (assert (not (hasattr Shape "c")))
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
  (assert (= (copy.replace s :b 5) (Shape :a 1 :b 5)))
  (assert (= (pickle.loads (pickle.dumps (Bare :x 2))) (Bare :x 2)))
  (assert (= (copy.deepcopy s) s)))


(deftest test-a-record-still-refuses-a-value-out-of-shape
  (<- _ (Pure None))
  (with [caught (pytest.raises ValueError)]
    (Shape :a -1))
  (assert (= (str caught.value) "Shape の欄 a が検め (>= a 0) で落ちた: a=-1")))


(deftest test-a-record-methods-are-module-code-not-compiled-at-import
  ;; 失敗ケース(直す前は赤): dataclass は読み込みの時に関数の source を exec して作る(co_filename = "<string>")。直した後は
  ;; 展開が書いた関数が module の code として .pyc に入る。
  (<- _ (Pure None))
  (val names #("__init__" "__repr__" "__eq__" "__hash__" "__setattr__" "__delattr__"))
  ;; __repr__ は再帰の守り(reprlib.recursive_repr)に包まれる — 包まれた関数の code を見る。
  (val sources (lfor n names #(n (. (getattr (getattr Shape n) "__wrapped__" (getattr Shape n)) __code__ co-filename))))
  (assert (all (gfor #(n source) sources (.endswith source "defrecord_shape_deftests.hy"))) sources))
