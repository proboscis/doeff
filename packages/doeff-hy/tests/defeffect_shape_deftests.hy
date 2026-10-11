;;; defeffect の型の振る舞い(frozen・位置と名前の引数・既定値・等価・hash・repr・欄の表・:pre の断り)が、同じ欄の標準の frozen の
;;; dataclass と同じ事の検(card acp:kanban-issue:ki-e77d44aa61e5 — defrecord と同じく展開の時に関数を書き、読み込みの時に dataclass に
;;; 関数の source を compile させない)。
;;;
;;; 確かめること: 欄の形を持つ effect(既定値なし・字面の既定値・式の既定値・:pre)と、同じ欄を手で書いた EffectBase の上の標準の
;;; dataclass を、作る(位置・名前)・比べる・hash・repr・凍結・fields・replace・__match_args__・__dataclass_params__・:pre の断りで突き合わせる。
;;; 失敗ケース(直す前は赤): effect の関数は module の code(.hy の file)で、dataclass が読み込みの時に exec した code(<string>)ではない。
;;; 公開は test_defeffect_shape.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defeffect <- val])
(import dataclasses)
(import dataclasses [dataclass FrozenInstanceError])
(import copy)
(import pickle)
(import pytest)
(import doeff [Pure EffectBase])

(defeffect Probe
  "欄の形を持つ effect。"
  {:fields [(: table str) (: key tuple) (: limit int 10) (: label str (+ "a" "b"))]
   :pre [(>= limit 0)]
   :answer (| int None)
   :tags {:context "doeff-hy" :role "intent"}})

(defclass [(dataclass :frozen True)] Twin [EffectBase]
  "同じ欄を手で書いた標準の dataclass(比べる相手)。"
  #^ str table
  #^ tuple key
  (setv #^ int limit 10)
  (setv #^ str label (+ "a" "b")))

(defeffect Bare
  {:answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Holder
  "値を 1 つ持つ effect(等価の比べ方を見る)。"
  {:fields [(: item object)]
   :answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defclass [(dataclass :frozen True)] HolderTwin [EffectBase]
  "Holder と同じ欄を手で書いた標準の dataclass。"
  #^ object item)

(defclass Loose []
  "== が真偽でない値を返す値(numpy の配列と同じ形)。"
  (defn __eq__ [self other] "loose")
  (setv __hash__ None))

(defeffect Named
  "組み込みの名(object・type)を欄の名に持つ effect。"
  {:fields [(: object int) (: type str "t")]
   :answer None
   :tags {:context "doeff-hy" :role "intent"}})

(defeffect Dashed
  "名に - を含む欄を :pre が読む effect。"
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
  ;; 失敗ケース(直す前は赤): Python 3.14 の dataclass の __eq__ は「同じ物なら真・欄ごとの == を and で繋ぐ」で、組どうしの比較
  ;; (要素が同じ物なら == を呼ばない・答えを真偽にする)と、NaN と真偽でない値を返す == で答えが違う。
  (<- _ (Pure None))
  (val nan (float "nan"))
  (assert (= (= (Holder nan) (Holder nan)) (= (HolderTwin nan) (HolderTwin nan)) False))
  (val h (Holder nan))
  (assert (= h h))
  (assert (= (.__eq__ (Holder (Loose)) (Holder (Loose))) (.__eq__ (HolderTwin (Loose)) (HolderTwin (Loose))) "loose")))


(deftest test-an-effect-with-builtin-field-names-builds-like-its-dataclass
  ;; 失敗ケース(直す前は赤): 展開の __init__ が object.__setattr__ で欄を置くと、欄の引数 object が組み込みの object を隠す
  ;; (dataclass の __init__ は __dataclass_builtins_object__ の別名で置くので隠されない)。
  (<- _ (Pure None))
  (val n (Named 1))
  (assert (= #(n.object n.type) #(1 "t")))
  (assert (= (Named :object 2 :type "u") (Named 2 "u"))))


(deftest test-an-effect-runs-its-pre-on-every-way-of-making-it
  ;; 424a87f58 の後退(展開が __post_init__ を呼ばず、入力の検査が黙って効かない)と同じ穴が defeffect に無い事: :pre は位置・名前・
  ;; 既定値・replace のどの作り方でも走り、名に - を含む欄も読む。
  (<- _ (Pure None))
  (assert (= (. (Dashed :max-count 2) max-count) 2))
  (for [make [(fn [] (Dashed -1)) (fn [] (Dashed :max-count -1)) (fn [] (Dashed -1 "y"))
              (fn [] (dataclasses.replace (Dashed 1) :max-count -1)) (fn [] (copy.replace (Dashed 1) :max-count -1))]]
    (with [(pytest.raises Exception)]
      (make))))


(deftest test-an-effect-pickles-like-its-twin
  (<- _ (Pure None))
  (val p (Probe "t" #("k") 3))
  (assert (= (pickle.loads (pickle.dumps p)) p))
  (assert (= (pickle.loads (pickle.dumps (Dashed 1))) (Dashed 1))))


(deftest test-the-seal-refuses-a-post-init-the-written-init-does-not-call
  ;; 守りの 2 段目(defrecord の ec08cd77 と同じ): effect 型を仕上げる seal_effect は、型が __post_init__ を持つのに展開の __init__ が
  ;; 呼ばない時に読み込みで断る。
  (<- _ (Pure None))
  (import doeff_hy.record_runtime [seal-effect])
  (defclass [(dataclass :init False :repr False :eq False)] Missed [EffectBase]
    #^ int n
    (defn __init__ [self n] (object.__setattr__ self "n" n) (.__post-init__ self))
    (defn __post-init__ [self] None))
  (with [(pytest.raises TypeError :match "__post_init__")]
    (seal-effect Missed #("n") False))
  (seal-effect Missed #("n") True))


(deftest test-the-seal-refuses-fields-the-expansion-read-differently
  ;; 守りの 2 段目: 展開は欄の型と既定値を字面で読む。名を介した field の印(既定値)や ClassVar の型は展開に見えず、展開の書いた
  ;; __init__ が dataclass の欄の表と違う形になる — 読み込みで断る(黙って違う値を置かない)。
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
  (assert (= (copy.replace p :table "u") (Probe "u" #())))
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
  ;; 失敗ケース(直す前は赤): dataclass は読み込みの時に関数の source を exec して作る(co_filename = "<string>")。直した後は
  ;; 展開が書いた関数が module の code として .pyc に入る。
  (<- _ (Pure None))
  (val names #("__init__" "__repr__" "__eq__" "__hash__" "__setattr__" "__delattr__"))
  ;; __repr__ は再帰の守り(reprlib.recursive_repr)に包まれる — 包まれた関数の code を見る。
  (val sources (lfor n names #(n (. (getattr (getattr Probe n) "__wrapped__" (getattr Probe n)) __code__ co-filename))))
  (assert (all (gfor #(n source) sources (.endswith source "defeffect_shape_deftests.hy"))) sources))
