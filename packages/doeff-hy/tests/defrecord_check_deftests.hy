;;; defrecord の頭の辞書 {:tags … :check […]}(doeff_hy/record.hy)の検(agora-redesign #798)。
;;;
;;; 確かめること: 検めが通る・落ちる・落ちた時の文言(型・欄・検め・値)・:tags が読める・
;;; 頭の辞書の無い前からの形がそのまま動く・展開の時に断る形・Program を返す検めを止める。
;;; 公開は test_defrecord_check.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defk <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass FrozenInstanceError])
(import re)
(import hy)
(import pytest)
(import doeff [Pure])
(import doeff_hy.declarations [DefinitionTags])

(setv CHAT-ID-PATTERN (re.compile r"c-[0-9A-HJKMNP-TV-Z]{26}"))
(setv KINDS (frozenset #("chat" "agent")))

(defrecord ChatId
  "chat の id"
  {:tags  {:context "chat" :role "type"}
   :check [(CHAT-ID-PATTERN.fullmatch value)]}
  (#^ str value))

(defrecord Span
  {:check [(>= start 0) (<= start end)]}
  #^ int start
  #^ int end)

(defrecord Named
  {:tags {:context "chat" :role "type"}}
  #^ str kind
  (setv #^ str note ""))

(defrecord Participant
  {:check [(in kind KINDS)]}
  #^ ChatId chat
  #^ str kind)

(defrecord Plain
  #^ str key
  #^ (| int None) size)

(defk always-true [x]
  {:pre [(: x str)] :post [(: % bool)]}
  True)

(defrecord Misused
  {:check [(always-true value)]}
  #^ str value)


(defn refused [#^ str source]  ; defk にできない: 検の中で展開の誤りを文字列で受けるだけの手元の関数
  "source を展開して、断った時の文言を返す(断らなければ None)。"
  (try
    (hy.eval (hy.read-many (+ "(require doeff-hy.record [defrecord])\n" source)) :module (hy.I.types.ModuleType "probe"))
    None
    (except [e Exception]
      (str e))))


(deftest test-check-passes-for-a-value-in-shape
  (<- chat (Pure (ChatId :value "c-01M2QAJS4JHEH34YQ0T6S9TZXB")))
  (assert (= chat.value "c-01M2QAJS4JHEH34YQ0T6S9TZXB"))
  (assert (= (Span :start 0 :end 3) (Span :start 0 :end 3)))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr chat "value" "c-x")))


(deftest test-check-fails-with-the-type-field-and-check-named
  (<- _ (Pure None))
  (with [caught (pytest.raises ValueError)]
    (ChatId :value "chat-1"))
  (assert (= (str caught.value)
             "ChatId の欄 value が検め (CHAT-ID-PATTERN.fullmatch value) で落ちた: value='chat-1'"))
  (with [caught (pytest.raises ValueError)]
    (Span :start 3 :end 1))
  (assert (= (str caught.value) "Span の欄 start・end が検め (<= start end) で落ちた: start=3 end=1")))


(deftest test-checks-run-in-written-order
  (<- _ (Pure None))
  ;; 両方とも偽の値 — 先に書いた (>= start 0) で落ちる。
  (with [caught (pytest.raises ValueError)]
    (Span :start -2 :end -5))
  (assert (in "(>= start 0)" (str caught.value))))


(deftest test-tags-and-checks-are-readable-on-the-class
  (<- _ (Pure None))
  (assert (= ChatId.__doeff_tags__ (DefinitionTags :context "chat" :role "type")))
  (assert (= ChatId.__doeff_checks__ #("(CHAT-ID-PATTERN.fullmatch value)")))
  (assert (= ChatId.__doc__ "chat の id"))
  (assert (is Span.__doeff_tags__ None))
  (assert (= Span.__doeff_checks__ #("(>= start 0)" "(<= start end)")))
  ;; :tags だけの形は検めを持たず、既定値の欄も欄として読む。
  (assert (= Named.__doeff_checks__ #()))
  (assert (= (. (Named :kind "chat") note) "")))


(deftest test-a-field-typed-as-a-checked-record-reuses-its-check
  (<- _ (Pure None))
  (setv chat (ChatId :value "c-01M2QAJS4JHEH34YQ0T6S9TZXB"))
  (assert (= (. (Participant :chat chat :kind "agent") kind) "agent"))
  (with [caught (pytest.raises ValueError)]
    (Participant :chat chat :kind "robot"))
  (assert (in "Participant の欄 kind が検め (in kind KINDS)" (str caught.value))))


(deftest test-record-without-a-header-keeps-the-old-shape
  (<- _ (Pure None))
  (setv row (Plain :key "k" :size None))
  (assert (= row.key "k"))
  (assert (not (hasattr Plain "__doeff_tags__")))
  (assert (not (hasattr Plain "__post_init__")))
  (assert (= (hy.repr (hy.macroexpand '(defrecord Plain #^ str key)))
             "'(defclass [(dataclass :frozen True :kw-only True)] Plain [] (annotate key str))")))


(deftest test-a-check-that-returns-a-program-is-refused
  (<- _ (Pure None))
  (with [caught (pytest.raises TypeError)]
    (Misused :value "x"))
  (assert (in "Program を返した" (str caught.value))))


(deftest test-malformed-headers-are-refused-at-expansion
  (<- _ (Pure None))
  (assert (in "受けない" (refused "(defrecord A {:doc \"x\"} #^ str a)")))
  (assert (in ":check は検めの式の list" (refused "(defrecord A {:check (> a 0)} #^ int a)")))
  (assert (in "欄を 1 つも参照しない" (refused "(defrecord A {:check [(> b 0)]} #^ int a)")))
  (assert (in "欄の注記" (refused "(defrecord A {:check [(: a int)]} #^ int a)")))
  (assert (in ":role" (refused "(defrecord A {:tags {:context \"c\" :role \"nope\"}} #^ int a)")))
  (assert (in ":context と :role ちょうど" (refused "(defrecord A {:tags {:context \"c\"}} #^ int a)"))))
