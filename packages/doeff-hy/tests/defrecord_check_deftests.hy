;;; defrecord の頭の辞書 {:tags … :check […]}(doeff_hy/record.hy)の検(agora-redesign #798)。
;;;
;;; 確かめること: 検めが通る・落ちる・落ちた時の文言(型・欄・検め・値)・:tags が読める・
;;; 頭の辞書の無い前からの形がそのまま動く・展開の時に断る形・Program を返す検めを止める。
;;; 公開は test_defrecord_check.py(包み直さずそのまま公開する — ADR-DOE-HY-002)。

(require doeff-hy.macros [deftest defk <- val])
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

(defrecord Grouped
  "1 つの setv に既定値つきの欄を並べた形(agora の ProfileFact で見つかった欠陥 — 前は最初の組だけを欄に数え、:check の参照が落ちた)"
  {:check [(>= used 0) (<= used limit)]}
  #^ str name
  (setv #^ int used 0
        #^ int limit 10))

(defrecord Refused
  "検めの断り — 失敗の値の印つき"
  {:failure True}
  #^ str reason)

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


(deftest test-a-check-answering-true-skips-the-failure-path
  (<- _ (Pure None))
  ;; 数の検(agora-redesign #2421): 答えが True そのものの検めは require-check を呼ばない — record を作るたびの費用
  ;; (require-check の :pre の isinstance 5 つと値の辞書・DoExpr の metaclass を通る isinstance)を払わない。
  ;; 直しを外すと(検めごとに require-check を呼ぶ形)、通る Span 1 つで 2 回・通る ChatId で 1 回と数えて赤。
  ;; 断る物は変わらない: 偽の検めは require-check が ValueError にし、True でない真の答え(Match)は通る。
  (import doeff_hy.record :as record-module)
  (val original record-module.require-check)
  (val calls [0])
  (setattr record-module "require_check"
           (fn [#* args]
             (+= (get calls 0) 1)
             (original #* args)))
  (try
    (Span :start 0 :end 3)
    (ChatId :value "c-01M2QAJS4JHEH34YQ0T6S9TZXB")
    (assert (= (get calls 0) 0) (get calls 0))
    (with [caught (pytest.raises ValueError)]
      (Span :start 3 :end 1))
    (assert (= (get calls 0) 1) (get calls 0))
    (assert (= (str caught.value) "Span の欄 start・end が検め (<= start end) で落ちた: start=3 end=1"))
    (with [(pytest.raises ValueError)]
      (ChatId :value "chat-1"))
    (assert (= (get calls 0) 2) (get calls 0))
    (finally
      (setattr record-module "require_check" original))))


(deftest test-malformed-headers-are-refused-at-expansion
  (<- _ (Pure None))
  (assert (in "受けない" (refused "(defrecord A {:doc \"x\"} #^ str a)")))
  (assert (in ":check は検めの式の list" (refused "(defrecord A {:check (> a 0)} #^ int a)")))
  (assert (in "欄を 1 つも参照しない" (refused "(defrecord A {:check [(> b 0)]} #^ int a)")))
  (assert (in "欄の注記" (refused "(defrecord A {:check [(: a int)]} #^ int a)")))
  (assert (in ":role" (refused "(defrecord A {:tags {:context \"c\" :role \"nope\"}} #^ int a)")))
  (assert (in ":context と :role ちょうど" (refused "(defrecord A {:tags {:context \"c\"}} #^ int a)")))
  (assert (in ":failure は字面の True か False" (refused "(defrecord A {:failure 1} #^ int a)"))))


(deftest test-failure-mark-is-readable-on-the-class
  (<- _ (Pure None))
  ;; :failure True は失敗の型の印 — 属性に残り、値の作り方は変わらない。印の無い頭の辞書は False、頭の辞書の無い形は属性を持たない。
  (assert (is Refused.__doeff_failure__ True))
  (assert (= (. (Refused :reason "r") reason) "r"))
  (assert (is ChatId.__doeff_failure__ False))
  (assert (not (hasattr Plain "__doeff_failure__"))))


(deftest test-grouped-setv-fields-are-all-checked
  (<- _ (Pure None))
  ;; 2 つ目の組の欄 limit も :check の欄として数える(前は「欄を 1 つも参照しない」か、limit を束ねずに落ちた)。
  (assert (= (. (Grouped :name "a" :used 3 :limit 5) limit) 5))
  (assert (= (. (Grouped :name "a") limit) 10))
  (with [caught (pytest.raises ValueError)]
    (Grouped :name "a" :used 7 :limit 5))
  (assert (in "Grouped の欄 used・limit が検め (<= used limit) で落ちた" (str caught.value)))
  ;; 2 つ目の組の欄だけを参照する :check も展開できる。
  ;; (検の外の module で展開するので、dataclass の名が無い誤りは出てもよい — 欄の読みの誤りが無いことだけを見る)。
  (assert (not-in "欄を 1 つも参照しない" (or (refused "(defrecord Only {:check [(> b 0)]} (setv #^ int a 1 #^ int b 2))") ""))))



(deftest test-field-reading-matches-the-shared-case-table
  (<- _ (Pure None))
  ;; 欄の読み方の Hy 側の正本 field-targets が、Rust 側の正本(doeff-indexer の hy_index::fields)と同じ表で同じ答えを出す。
  (import json pathlib [Path])
  (import doeff_hy.declarations [field-targets])
  (setv table (json.loads (.read-text (/ (. (Path __file__) parent) "data" "record_field_cases.json") :encoding "utf-8")))
  (for [case (get table "cases")]
    (setv got (lfor target (field-targets (hy.read-many (get case "forms"))) (str target)))
    (assert (= got (get case "names")) (.format "{!r}: {} ≠ {}" (get case "forms") got (get case "names")))))
