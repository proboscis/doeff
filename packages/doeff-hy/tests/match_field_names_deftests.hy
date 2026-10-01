;;; defk の本体の match で、class pattern の keyword の欄の名を `-` で書いても当たる回避(doeff_hy/match_fields.py)の検。
;;;
;;; Hy 1.3.1 の match は class pattern の keyword の欄の名を mangle せずに Python の case へ出すので、
;;; (RecordWrite :ended-reason None) の節は値が何でも当たらず、黙って次の節へ倒れる(agora-redesign #2036)。defk は本体の
;;; match の class pattern の欄の名を hy.mangle で属性の名へ直す(Hy の本体は直さない — ADR-DOE-HY-008)。
;;; 確かめること: defk の中で `-` の綴りが当たる(agora-controllers #1962 の形)・入れ子の class pattern・| の中・:if の守りつき・
;;; `_` の綴りは変わらない・本体と守りの keyword は変わらない・defk の外の Hy の match は今も当たらない(Hy が直ったらこの検が
;;; 赤になる — 回避と doeff-linter の DOEFF169 を外す合図)。
;;; 公開は test_match_field_names.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hy)
(import doeff_hy.match_fields [mangle-match-fields])

(defrecord RecordWrite
  "記録の書き込み 1 件(agora-controllers #1962 の形)"
  (#^ (| str None) ended-reason)
  (#^ (| int None) usage))

(defrecord Envelope
  "書き込みを包む送り状(入れ子の class pattern を確かめるため)"
  (#^ RecordWrite inner-write)
  (#^ str sent-by))

(defk classify-write [write]
  {:pre [(: write RecordWrite)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "書き込みを、終わりの理由と使用量で分ける(#1962 の (RecordWrite :ended-reason None :usage None) の節を含む)。"
  (match write
    (RecordWrite :ended-reason None :usage None) "open"
    (RecordWrite :ended-reason reason) :if (= reason "cancelled") "cancelled"
    (| (RecordWrite :ended-reason "done") (RecordWrite :ended-reason "merged")) "finished"
    (RecordWrite :ended_reason "failed") "failed"
    _ "other"))

(defk classify-envelope [envelope]
  {:pre [(: envelope Envelope)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "送り状を、中の書き込みの形で分ける(class pattern の引数の中の class pattern)。"
  (match envelope
    (Envelope :inner-write (RecordWrite :ended-reason None) :sent-by who) (+ "open from " who)
    _ "other"))

(defk body-keyword [write]
  {:pre [(: write RecordWrite)] :post [(: % hy.models.Keyword)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "本体と守りに置いた keyword の値を返す(欄の名ではないので変わらない)。"
  (match write
    (RecordWrite :ended-reason None) :if (= (. :guard-kw name) "guard-kw") :ended-reason
    _ :fell-through))

(defk rewritten [source]
  {:pre [(: source str)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "source を読んで直した form の綴り。"
  (hy.repr (mangle-match-fields (hy.read source))))

(defk same-form [source]
  {:pre [(: source str)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "source を読んだままの form の綴り(rewritten と比べる期待の値)。"
  (hy.repr (hy.read source)))

(deftest test-hyphenated-field-names-match-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- open (classify-write (RecordWrite :ended-reason None :usage None)))
  (assert (= open "open") "#1962 の (RecordWrite :ended-reason None :usage None) の節が当たる")
  (<- used (classify-write (RecordWrite :ended-reason None :usage 3)))
  (assert (= used "other") "使用量の在る書き込みは open に当たらない"))

(deftest test-guarded-or-and-underscore-clauses-match-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- cancelled (classify-write (RecordWrite :ended-reason "cancelled" :usage 1)))
  (assert (= cancelled "cancelled") ":if の守りつきの節が当たる")
  (<- other-reason (classify-write (RecordWrite :ended-reason "timeout" :usage 1)))
  (assert (= other-reason "other") "守りが偽なら次の節へ進む")
  (<- done (classify-write (RecordWrite :ended-reason "done" :usage 1)))
  (<- merged (classify-write (RecordWrite :ended-reason "merged" :usage 1)))
  (assert (= #(done merged) #("finished" "finished")) "| の中の class pattern が当たる")
  (<- failed (classify-write (RecordWrite :ended-reason "failed" :usage 1)))
  (assert (= failed "failed") "`_` で書いた欄の名はそのまま当たる"))

(deftest test-nested-class-pattern-matches-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- open (classify-envelope (Envelope :inner-write (RecordWrite :ended-reason None :usage None) :sent-by "w1")))
  (assert (= open "open from w1") "class pattern の引数の中の class pattern の欄も当たる")
  (<- closed (classify-envelope (Envelope :inner-write (RecordWrite :ended-reason "done" :usage 1) :sent-by "w1")))
  (assert (= closed "other")))

(deftest test-body-and-guard-keywords-are-not-rewritten
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- hit (body-keyword (RecordWrite :ended-reason None :usage None)))
  (assert (= hit.name "ended-reason") "本体の keyword の値は欄の名ではないので変わらない")
  (<- fell (body-keyword (RecordWrite :ended-reason "done" :usage None)))
  (assert (= fell.name "fell-through"))
  (<- form (rewritten "(match r (Rec :a-b 1) :if (g :guard-kw 2) (f :body-kw 3) _ (Rec :call-kw 4))"))
  (<- expected (same-form "(match r (Rec :a_b 1) :if (g :guard-kw 2) (f :body-kw 3) _ (Rec :call-kw 4))"))
  (assert (= form expected) "直すのは pattern の欄の名だけで、守りと本体の呼びの keyword は変わらない"))

(deftest test-only-class-pattern-field-names-are-rewritten
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- nested (rewritten "(match r [(mod.Rec :a-b x :as y) #* rest] 0 (| (A :c-d (B :e-f 1)) (. mod CONST)) 1 {\"k\" (C :g-h 2)} 2 (D :kind :value-kw) 3 _ 4)"))
  (<- nested-expected (same-form "(match r [(mod.Rec :a_b x :as y) #* rest] 0 (| (A :c_d (B :e_f 1)) (. mod CONST)) 1 {\"k\" (C :g_h 2)} 2 (D :kind :value-kw) 3 _ 4)"))
  (assert (= nested nested-expected) "sequence・or・mapping・class pattern の引数の中の欄の名を直し、値の pattern の keyword は直さない")
  (<- captured (rewritten "(match r (Rec :a-b 1) :as whole :if whole.ok (f :body-kw whole) _ 0)"))
  (<- captured-expected (same-form "(match r (Rec :a_b 1) :as whole :if whole.ok (f :body-kw whole) _ 0)"))
  (assert (= captured captured-expected) "節の :as の後の :if の守りと本体も code として扱う")
  (<- quoted (rewritten "(f '(match r (Rec :a-b 1) 0) `(match r (Rec :a-b 1) 0))"))
  (<- quoted-expected (same-form "(f '(match r (Rec :a-b 1) 0) `(match r (Rec :a-b 1) 0))"))
  (assert (= quoted quoted-expected) "quote / quasiquote の中は値なので直さない")
  (<- inner (rewritten "(when ok (setv x (match r (Rec :a-b 1) 0 _ 1)))"))
  (<- inner-expected (same-form "(when ok (setv x (match r (Rec :a_b 1) 0 _ 1)))"))
  (assert (= inner inner-expected) "code の中の深い所の match も直す"))

(deftest test-forms-without-hyphenated-fields-are-returned-as-is
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (val form (hy.read "(match r (Rec :a_b 1 :c x) 0 _ (f :a-b 1))"))
  (assert (is (mangle-match-fields form) form) "直す所が無ければ同じ object を返す(位置もそのまま)"))

(deftest test-hy-match-outside-defk-still-misses-hyphenated-fields
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (val got (hy.eval (hy.read-many (+ "(import dataclasses [dataclass])\n"
                                      "(defclass [(dataclass :frozen True)] Rec [] (#^ (| str None) ended-reason))\n"
                                      "(match (Rec :ended-reason None) (Rec :ended-reason None) \"hit\" _ \"fell\")"))
                     :module (hy.I.types.ModuleType "probe")))
  (assert (= got "fell")
          (+ "Hy の match が class pattern の欄の名を mangle するようになった — defk の回避(doeff_hy/match_fields.py)と"
             " doeff-linter の DOEFF169 を外せるかを確かめる時(agora-redesign #2036)")))
