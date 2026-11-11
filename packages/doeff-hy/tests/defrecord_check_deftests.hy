;;; Tests for the defrecord header dict {:tags … :check […]} (doeff_hy/record.hy).
;;;
;;; What is checked: checks pass and fail, the failure message (type, field, check, value), :tags can be read,
;;; the older form with no header dict still works as is, forms refused at expansion time, and checks that return a Program are rejected.
;;; It is exposed by test_defrecord_check.py (exposed as is without re-wrapping -- ADR-DOE-HY-002).

(require doeff-hy.macros [deftest defk <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass FrozenInstanceError])
(import re)
(import hy)
(import pytest)
(import doeff [Pure])
(import doeff_hy._declarations [DefinitionTags])
(import doeff_hy._static_view [static-view])

(setv CHAT-ID-PATTERN (re.compile r"chat-[0-9]{4}"))
(setv KINDS (frozenset #("chat" "agent")))

(defrecord ChatId
  "chat id"
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
  "A form with fields that have defaults listed in one setv (a defect found in agora's ProfileFact -- previously only the first pair was counted as a field, and the :check reference failed)"
  {:check [(>= used 0) (<= used limit)]}
  #^ str name
  (setv #^ int used 0
        #^ int limit 10))

(defrecord Refused
  "Refusal of a check -- marked with the failing value"
  {:failure True}
  #^ str reason)

(defk always-true [x]
  {:pre [(: x str)] :post [(: % bool)]}
  True)

(defrecord Misused
  {:check [(always-true value)]}
  #^ str value)


(defn refused [#^ str source]  ; cannot be a defk: a local helper that only receives the expansion error as a string inside the test
  "Expand source and return the message when it was refused (None if not refused)."
  (try
    (hy.eval (hy.read-many (+ "(require doeff-hy.record [defrecord])\n" source)) :module (hy.I.types.ModuleType "probe"))
    None
    (except [e Exception]
      (str e))))


(deftest test-check-passes-for-a-value-in-shape
  (<- chat (Pure (ChatId :value "chat-0001")))
  (assert (= chat.value "chat-0001"))
  (assert (= (Span :start 0 :end 3) (Span :start 0 :end 3)))
  (with [(pytest.raises FrozenInstanceError)]
    (setattr chat "value" "c-x")))


(deftest test-check-fails-with-the-type-field-and-check-named
  (<- _ (Pure None))
  (with [caught (pytest.raises ValueError)]
    (ChatId :value "chat-1"))
  (assert (= (str caught.value)
             "ChatId: field value failed check (CHAT-ID-PATTERN.fullmatch value): value='chat-1'"))
  (with [caught (pytest.raises ValueError)]
    (Span :start 3 :end 1))
  (assert (= (str caught.value) "Span: fields start, end failed check (<= start end): start=3 end=1")))


(deftest test-checks-run-in-written-order
  (<- _ (Pure None))
  ;; Both are false values -- it fails at the (>= start 0) written first.
  (with [caught (pytest.raises ValueError)]
    (Span :start -2 :end -5))
  (assert (in "(>= start 0)" (str caught.value))))


(deftest test-tags-and-checks-are-readable-on-the-class
  (<- _ (Pure None))
  (assert (= ChatId.__doeff_tags__ (DefinitionTags :context "chat" :role "type")))
  (assert (= ChatId.__doeff_checks__ #("(CHAT-ID-PATTERN.fullmatch value)")))
  ;; The docstring remains only in the source and the type-checking projection -- the runtime __doc__ is built from the field names.
  (assert (= ChatId.__doc__ "ChatId(*, value)"))
  (assert (is Span.__doeff_tags__ None))
  (assert (= Span.__doeff_checks__ #("(>= start 0)" "(<= start end)")))
  ;; A form with only :tags has no checks, and fields with defaults are also read as fields.
  (assert (= Named.__doeff_checks__ #()))
  (assert (= (. (Named :kind "chat") note) "")))


(deftest test-a-field-typed-as-a-checked-record-reuses-its-check
  (<- _ (Pure None))
  (setv chat (ChatId :value "chat-0001"))
  (assert (= (. (Participant :chat chat :kind "agent") kind) "agent"))
  (with [caught (pytest.raises ValueError)]
    (Participant :chat chat :kind "robot"))
  (assert (in "Participant: field kind failed check (in kind KINDS)" (str caught.value))))


(deftest test-record-without-a-header-keeps-the-old-shape
  (<- _ (Pure None))
  (setv row (Plain :key "k" :size None))
  (assert (= row.key "k"))
  (assert (not (hasattr Plain "__doeff_tags__")))
  (assert (not (hasattr Plain "__post_init__")))
  ;; The type-checking projection expansion stays as before (pyright reads it as a dataclass). The runtime expansion makes the dataclass build only the field table, and the function is written
  ;; by the expansion (that the behaviour is the same is checked by defrecord_shape_deftests.hy). Outside a function body
  ;; _scoped-definition only lines the statement sequence up into a do (the Python shape is the same).
  (with [_ (static-view)]
    (assert (= (hy.repr (hy.macroexpand '(defrecord Plain #^ str key)))
               "'(do (defclass [(dataclass :frozen True :kw-only True)] Plain [] (annotate key str)))")))
  ;; The runtime class declares its slots in its body (dataclass does not rebuild the class to add them).
  (setv runtime (hy.repr (hy.macroexpand '(defrecord Plain #^ str key))))
  (assert (in "(dataclass :init False :repr False :eq False :kw-only True)" runtime))
  (assert (in "(setv __slots__ #(\"key\" \"__weakref__\")" runtime)))


(deftest test-a-check-that-returns-a-program-is-refused
  (<- _ (Pure None))
  (with [caught (pytest.raises TypeError)]
    (Misused :value "x"))
  (assert (in "returned a Program" (str caught.value))))


(deftest test-a-check-answering-true-skips-the-failure-path
  (<- _ (Pure None))
  ;; Counting test: a check whose answer is exactly True does not call require-check -- it avoids the per-record cost
  ;; (5 isinstance calls in require-check's :pre, the value dict, and an isinstance going through the DoExpr metaclass).
  ;; Without the fix (calling require-check for each check) it counts 2 calls for one passing Span and 1 for a passing ChatId, so it is red.
  ;; What is refused does not change: a false check is made a ValueError by require-check, and a truthy answer that is not True (Match) passes.
  (import doeff_hy._record_check :as record-module)   ; where the expansion looks up require-check
  (val original record-module.require-check)
  (val calls [0])
  (setattr record-module "require_check"
           (fn [#* args]
             (+= (get calls 0) 1)
             (original #* args)))
  (try
    (Span :start 0 :end 3)
    (ChatId :value "chat-0001")
    (assert (= (get calls 0) 0) (get calls 0))
    (with [caught (pytest.raises ValueError)]
      (Span :start 3 :end 1))
    (assert (= (get calls 0) 1) (get calls 0))
    (assert (= (str caught.value) "Span: fields start, end failed check (<= start end): start=3 end=1"))
    (with [(pytest.raises ValueError)]
      (ChatId :value "chat-1"))
    (assert (= (get calls 0) 2) (get calls 0))
    (finally
      (setattr record-module "require_check" original))))


(deftest test-malformed-headers-are-refused-at-expansion
  (<- _ (Pure None))
  (assert (in "is not accepted" (refused "(defrecord A {:doc \"x\"} #^ str a)")))
  (assert (in ":check must be a list of check expressions" (refused "(defrecord A {:check (> a 0)} #^ int a)")))
  (assert (in "references no field" (refused "(defrecord A {:check [(> b 0)]} #^ int a)")))
  (assert (in "field annotation" (refused "(defrecord A {:check [(: a int)]} #^ int a)")))
  (assert (in ":role" (refused "(defrecord A {:tags {:context \"c\" :role \"nope\"}} #^ int a)")))
  (assert (in "exactly :context and :role" (refused "(defrecord A {:tags {:context \"c\"}} #^ int a)")))
  (assert (in ":failure must be a literal True or False" (refused "(defrecord A {:failure 1} #^ int a)"))))


(deftest test-failure-mark-is-readable-on-the-class
  (<- _ (Pure None))
  ;; :failure True is a marker of a failure type -- it stays as an attribute and does not change how values are made. A header dict without the marker is False, and a form with no header dict has no attribute.
  (assert (is Refused.__doeff_failure__ True))
  (assert (= (. (Refused :reason "r") reason) "r"))
  (assert (is ChatId.__doeff_failure__ False))
  (assert (not (hasattr Plain "__doeff_failure__"))))


(deftest test-grouped-setv-fields-are-all-checked
  (<- _ (Pure None))
  ;; The field limit of the second pair is also counted as a :check field (previously it was "references no field" or failed without binding limit).
  (assert (= (. (Grouped :name "a" :used 3 :limit 5) limit) 5))
  (assert (= (. (Grouped :name "a") limit) 10))
  (with [caught (pytest.raises ValueError)]
    (Grouped :name "a" :used 7 :limit 5))
  (assert (in "Grouped: fields used, limit failed check (<= used limit)" (str caught.value)))
  ;; A :check that references only the field of the second pair can also be expanded.
  ;; (It expands in a module outside the test, so an error about a missing dataclass name is fine -- we only check there is no error in reading the fields.)
  (assert (not-in "references no field" (or (refused "(defrecord Only {:check [(> b 0)]} (setv #^ int a 1 #^ int b 2))") ""))))



(deftest test-field-reading-matches-the-shared-case-table
  (<- _ (Pure None))
  ;; The Hy-side source of truth for how fields are read, field-targets, gives the same answers as the Rust-side source of truth (hy_index::fields in doeff-indexer) from the same table.
  (import json pathlib [Path])
  (import doeff_hy._declarations [field-targets])
  (setv table (json.loads (.read-text (/ (. (Path __file__) parent) "data" "record_field_cases.json") :encoding "utf-8")))
  (for [case (get table "cases")]
    (setv got (lfor target (field-targets (hy.read-many (get case "forms"))) (str target)))
    (assert (= got (get case "names")) (.format "{!r}: {} ≠ {}" (get case "forms") got (get case "names")))))
