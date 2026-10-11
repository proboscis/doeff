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
