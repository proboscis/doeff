;;; Tests for the workaround (doeff_hy/_match_fields.py) that makes a class-pattern keyword field name written with `-` match in the match of a defk body.
;;;
;;; Hy 1.3.1's match emits class-pattern keyword field names to Python's case without mangling them,
;;; so the clause (RecordWrite :ended-reason None) never matches whatever the value and silently falls to the next clause. defk rewrites the field names of class patterns in the body's
;;; match to attribute names with hy.mangle (it does not fix Hy itself — ADR-DOE-HY-008).
;;; What it checks: the `-` spelling matches inside defk, nested class patterns, inside |, with an :if guard,
;;; the `_` spelling is unchanged, keywords in the body and guard are unchanged, and Hy's match outside defk still does not match (when Hy is fixed this test
;;; goes red — the signal to remove the workaround and doeff-linter's DOEFF169).
;;; Published by test_match_field_names.py (published as is without re-wrapping — ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import hy)
(import doeff_hy._match_fields [mangle-match-fields])

(defrecord RecordWrite
  "One record write"
  (#^ (| str None) ended-reason)
  (#^ (| int None) usage))

(defrecord Envelope
  "A cover slip wrapping a write (to check nested class patterns)"
  (#^ RecordWrite inner-write)
  (#^ str sent-by))

(defk classify-write [write]
  {:pre [(: write RecordWrite)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "Split a write by ended reason and usage (including the clause (RecordWrite :ended-reason None :usage None) of #1962)."
  (match write
    (RecordWrite :ended-reason None :usage None) "open"
    (RecordWrite :ended-reason reason) :if (= reason "cancelled") "cancelled"
    (| (RecordWrite :ended-reason "done") (RecordWrite :ended-reason "merged")) "finished"
    (RecordWrite :ended_reason "failed") "failed"
    _ "other"))

(defk classify-envelope [envelope]
  {:pre [(: envelope Envelope)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "Split a cover slip by the shape of the write inside (a class pattern inside a class pattern argument)."
  (match envelope
    (Envelope :inner-write (RecordWrite :ended-reason None) :sent-by who) (+ "open from " who)
    _ "other"))

(defk body-keyword [write]
  {:pre [(: write RecordWrite)] :post [(: % hy.models.Keyword)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "Return the value of a keyword placed in the body and guard (it is not a field name, so it is unchanged)."
  (match write
    (RecordWrite :ended-reason None) :if (= (. :guard-kw name) "guard-kw") :ended-reason
    _ :fell-through))

(defk rewritten [source]
  {:pre [(: source str)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "The spelling of the form after reading the source and rewriting it."
  (hy.repr (mangle-match-fields (hy.read source))))

(defk same-form [source]
  {:pre [(: source str)] :post [(: % str)] :tags {:context "doeff-hy-match" :role "judgment"}}
  "The spelling of the form as read from the source (the expected value to compare with rewritten)."
  (hy.repr (hy.read source)))

(deftest test-hyphenated-field-names-match-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- open (classify-write (RecordWrite :ended-reason None :usage None)))
  (assert (= open "open") "the clause (RecordWrite :ended-reason None :usage None) of #1962 matches")
  (<- used (classify-write (RecordWrite :ended-reason None :usage 3)))
  (assert (= used "other") "a write with usage does not match open"))

(deftest test-guarded-or-and-underscore-clauses-match-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- cancelled (classify-write (RecordWrite :ended-reason "cancelled" :usage 1)))
  (assert (= cancelled "cancelled") "a clause with an :if guard matches")
  (<- other-reason (classify-write (RecordWrite :ended-reason "timeout" :usage 1)))
  (assert (= other-reason "other") "if the guard is false, it goes to the next clause")
  (<- done (classify-write (RecordWrite :ended-reason "done" :usage 1)))
  (<- merged (classify-write (RecordWrite :ended-reason "merged" :usage 1)))
  (assert (= #(done merged) #("finished" "finished")) "a class pattern inside | matches")
  (<- failed (classify-write (RecordWrite :ended-reason "failed" :usage 1)))
  (assert (= failed "failed") "a field name written with `_` matches as is"))

(deftest test-nested-class-pattern-matches-inside-defk
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- open (classify-envelope (Envelope :inner-write (RecordWrite :ended-reason None :usage None) :sent-by "w1")))
  (assert (= open "open from w1") "a field of a class pattern inside a class pattern argument also matches")
  (<- closed (classify-envelope (Envelope :inner-write (RecordWrite :ended-reason "done" :usage 1) :sent-by "w1")))
  (assert (= closed "other")))

(deftest test-body-and-guard-keywords-are-not-rewritten
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- hit (body-keyword (RecordWrite :ended-reason None :usage None)))
  (assert (= hit.name "ended-reason") "the keyword value in the body is not a field name, so it is unchanged")
  (<- fell (body-keyword (RecordWrite :ended-reason "done" :usage None)))
  (assert (= fell.name "fell-through"))
  (<- form (rewritten "(match r (Rec :a-b 1) :if (g :guard-kw 2) (f :body-kw 3) _ (Rec :call-kw 4))"))
  (<- expected (same-form "(match r (Rec :a_b 1) :if (g :guard-kw 2) (f :body-kw 3) _ (Rec :call-kw 4))"))
  (assert (= form expected) "only the pattern's field names are rewritten; the keywords in the guard and body calls are unchanged"))

(deftest test-only-class-pattern-field-names-are-rewritten
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (<- nested (rewritten "(match r [(mod.Rec :a-b x :as y) #* rest] 0 (| (A :c-d (B :e-f 1)) (. mod CONST)) 1 {\"k\" (C :g-h 2)} 2 (D :kind :value-kw) 3 _ 4)"))
  (<- nested-expected (same-form "(match r [(mod.Rec :a_b x :as y) #* rest] 0 (| (A :c_d (B :e_f 1)) (. mod CONST)) 1 {\"k\" (C :g_h 2)} 2 (D :kind :value-kw) 3 _ 4)"))
  (assert (= nested nested-expected) "rewrite the field names inside sequence, or, mapping, and class-pattern arguments, and do not rewrite the keywords of value patterns")
  (<- captured (rewritten "(match r (Rec :a-b 1) :as whole :if whole.ok (f :body-kw whole) _ 0)"))
  (<- captured-expected (same-form "(match r (Rec :a_b 1) :as whole :if whole.ok (f :body-kw whole) _ 0)"))
  (assert (= captured captured-expected) "the guard after :as of a clause and the body are also treated as code")
  (<- quoted (rewritten "(f '(match r (Rec :a-b 1) 0) `(match r (Rec :a-b 1) 0))"))
  (<- quoted-expected (same-form "(f '(match r (Rec :a-b 1) 0) `(match r (Rec :a-b 1) 0))"))
  (assert (= quoted quoted-expected) "inside quote / quasiquote it is a value, so it is not rewritten")
  (<- inner (rewritten "(when ok (setv x (match r (Rec :a-b 1) 0 _ 1)))"))
  (<- inner-expected (same-form "(when ok (setv x (match r (Rec :a_b 1) 0 _ 1)))"))
  (assert (= inner inner-expected) "a deeply nested match inside code is also rewritten"))

(deftest test-forms-without-hyphenated-fields-are-returned-as-is
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (val form (hy.read "(match r (Rec :a_b 1 :c x) 0 _ (f :a-b 1))"))
  (assert (is (mangle-match-fields form) form) "if there is nothing to rewrite, return the same object (positions unchanged too)"))

(deftest test-hy-match-outside-defk-still-misses-hyphenated-fields
  {:tags {:context "doeff-hy-match" :role "judgment"}}
  (val got (hy.eval (hy.read-many (+ "(import dataclasses [dataclass])\n"
                                      "(defclass [(dataclass :frozen True)] Rec [] (#^ (| str None) ended-reason))\n"
                                      "(match (Rec :ended-reason None) (Rec :ended-reason None) \"hit\" _ \"fell\")"))
                     :module (hy.I.types.ModuleType "probe")))
  (assert (= got "fell")
          (+ "Hy's match started mangling class-pattern field names — time to check whether the defk workaround (doeff_hy/_match_fields.py) and"
             " doeff-linter's DOEFF169 can be removed")))
