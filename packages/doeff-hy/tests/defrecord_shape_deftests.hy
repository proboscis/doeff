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
(import sys)
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
  ;; copy.replace(__replace__ の口)は Python 3.13 から。
  (when (>= sys.version-info #(3 13))
    (assert (= (copy.replace s :b 5) (Shape :a 1 :b 5))))
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
  ;; __repr__ は再帰の守り(reprlib.recursive_repr)に包まれる — 包まれた関数の code を見る。包みが __wrapped__ を持つのは Python 3.13
  ;; からで、それより前は包みの code(reprlib.py)しか見えないので、3.12 までは __repr__ を数えない。
  ;; __init__・__eq__・__hash__ は型ごとの関数で、__repr__ と凍結の 2 つは record_runtime の共有の 1 組(card ki-ff2c33449b3f)。
  (val own (lfor n #("__init__" "__eq__" "__hash__") #(n (. (getattr Shape n) __code__ co-filename))))
  (val shared (lfor n (+ #("__setattr__" "__delattr__") (if (>= sys.version-info #(3 13)) #("__repr__") #()))
                    #(n (. (getattr (getattr Shape n) "__wrapped__" (getattr Shape n)) __code__ co-filename))))
  (assert (all (gfor #(n source) own (.endswith source "defrecord_shape_deftests.hy"))) own)
  (assert (all (gfor #(n source) shared (.endswith source "record_runtime.py"))) shared))


(deftest test-records-share-one-set-of-methods-and-keep-no-runtime-text
  ;; 失敗ケース(直す前は赤): 型ごとに __repr__・__setattr__・__delattr__ の関数を書き、__init__ の引数に注記を書き、
  ;; docstring を型に持たせた — 型 約 1,760 の job で関数と注記と文が 10 MB を越えた(card ki-ff2c33449b3f)。
  (<- _ (Pure None))
  (val shared #("__repr__" "__setattr__" "__delattr__"))
  (assert (all (gfor n shared (is (getattr Shape n) (getattr Bare n) (getattr Empty n)))))
  (assert (= Shape.__init__.__annotations__ {}) Shape.__init__.__annotations__)
  ;; docstring は source と型検査の投影にだけ残り、実行時の __doc__ は欄の名から組む(dataclass が読み込みで署名を計算しない)。
  (assert (= Shape.__doc__ "Shape(*, a, b, c, d, e, f, job_id)") Shape.__doc__)
  (assert (= (. (Bare :x 1) __doc__) "Bare(*, x)"))
  (assert (= Empty.__doc__ "Empty()"))
  ;; 欄の型は class の注記に残る(dataclass と型検査が読む)。
  (assert (is (. (get (dataclasses.fields Bare) 0) type) int)))


(defclass Grown [Bare]
  "defrecord の型を継ぐ素の class(凍結の守りを持たない子の型)。")


(deftest test-a-plain-subclass-of-a-record-guards-only-the-fields
  ;; 共有の凍結の __setattr__ は、守りを持つ型そのものの値は全部の名を、守りを持たない子の型の値は欄の名だけを断る(dataclass の
  ;; frozen と同じ — 型ごとに書いた形は定義した型を __class__ で読んでいた)。
  (<- _ (Pure None))
  (val g (Grown :x 1))
  (setattr g "note" 2)
  (assert (= g.note 2))
  (delattr g "note")
  (with [(pytest.raises FrozenInstanceError)]
    (setattr g "x" 3))
  (assert (= (repr g) "Grown(x=1)"))
  (assert (!= g (Bare :x 1))))


(defrecord Checked
  "本体に注記つきの __post_init__ を書いた record(doeff-cluster の EnvVar・RepoCheckout・RuntimeEnv と同じ書き方)。"
  #^ int n
  (defn #^ None __post-init__ [self]
    (when (< self.n 0)
      (raise (ValueError "n は 0 以上")))))


(deftest test-an-annotated-post-init-written-in-the-body-still-runs
  ;; 失敗ケース(直す前は赤 — 日次 10-11 08:00 の doeff 044fd20a9・vg-w44 の知らせ): Hy は (defn #^ None __post-init__ …) の名を
  ;; (annotate __post-init__ None) と読む。展開の own-method? が記号の名しか見ないと、本体の __post_init__ を見落として展開が
  ;; __init__ を書き、__post_init__ が呼ばれない(doeff-cluster の入力の検査が効かず、断られるはずの値が通る)。
  (<- _ (Pure None))
  (assert (= (. (Checked :n 1) n) 1))
  (with [caught (pytest.raises ValueError)]
    (Checked :n -1))
  (assert (= (str caught.value) "n は 0 以上")))


(defrecord Shown
  "本体に注記つきの自前の __repr__ を書いた record。"
  #^ int x
  (defn #^ str __repr__ [self]
    f"<Shown {self.x}>"))


(deftest test-an-annotated-repr-written-in-the-body-is-kept
  ;; 注記つきで書いた自前の __repr__ も、展開が書く物で上書きしない(own-method? は __post_init__ に限らず展開が書く関数を全部見る)。
  (<- _ (Pure None))
  (assert (= (repr (Shown :x 3)) "<Shown 3>")))


(deftest test-the-seal-refuses-a-post-init-the-written-init-does-not-call
  ;; 失敗ケース(card ki-a49427e82a0b — 守りの 2 段目・直す前は赤): 展開が本体の自前の __post_init__ を見落として __init__ を書くと、
  ;; 入力の検査は黙って効かなくなる(424a87f58 の後退は日次の検 8 本の赤で見つかるまで本番の coordinator に載った)。展開が書いた
  ;; record を仕上げる seal_record は、型が __post_init__ を持つのに展開の __init__ が呼ばない時に読み込みで断る。
  (<- _ (Pure None))
  (import doeff_hy.record_runtime [seal-record])
  (defclass [(dataclass :frozen True :kw-only True)] Missed []
    #^ int n
    (defn __post-init__ [self] None))
  (with [caught (pytest.raises TypeError)]
    (seal-record Missed #() False))
  (assert (in "__post_init__" (str caught.value)) (str caught.value))
  (seal-record Missed #() True))
