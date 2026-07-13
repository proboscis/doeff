;;; 直接束縛 deftest: 契約 schema 検証 = JSON Schema 仕様意味論(doeff#482 / U1)。
;;;
;;; 検証器は jsonschema(参照実装)への薄い adapter であり、ここで守るのは
;;; (1) adapter 契約 — 戻り値形(None | reason)・reason が違反箇所
;;;     (json_path)を指すこと・loc 接頭辞、
;;; (2) 事故クラスの回帰ピン — 旧 subset が黙殺した items / enum /
;;;     additionalProperties / minItems が実際に enforce されること
;;;     (ACP steward 実障害の形をそのまま固定)、
;;; (3) launch 時 fail-closed — 壊れた schema は admission で拒否。
;;; JSON Schema 仕様自体の網羅は jsonschema 側のテスト資産(公式 suite)に
;;; 委ね、ここで再実装しない。

(require doeff-hy.macros [deftest deff])

(import doeff_agents.sessionhost.schema
  [validate-against-schema schema-admission-error])


(setv ENTRY-SCHEMA
  {"type" "object"
   "required" ["decision"]
   "properties" {"decision" {"type" "string" "minLength" 1}}})

(setv REPORT-SCHEMA
  {"type" "object"
   "required" ["summary" "entries"]
   "properties" {"summary" {"type" "string"}
                 "entries" {"type" "array" "items" ENTRY-SCHEMA}}})


(deftest test-items-rejects-non-object-elements
  ;; ACP steward 実障害の形: 文字列要素は items(object)違反として弾く。
  (setv reason (validate-against-schema
                 {"summary" "s" "entries" ["not-an-object"]}
                 REPORT-SCHEMA "payload"))
  (assert (is-not reason None))
  (assert (in "entries[0]" reason)))


(deftest test-items-accepts-conforming-elements
  (assert (is (validate-against-schema
                {"summary" "s" "entries" [{"decision" "d1"} {"decision" "d2"}]}
                REPORT-SCHEMA "payload")
              None)))


(deftest test-items-inner-constraint-reaches-elements
  ;; 要素内の minLength まで届く(空文字 decision は違反)。
  (setv reason (validate-against-schema
                 {"summary" "s" "entries" [{"decision" ""}]}
                 REPORT-SCHEMA "payload"))
  (assert (is-not reason None))
  (assert (in "entries[0]" reason)))


(deftest test-enum-membership
  (setv schema {"enum" ["none" "stale_head" "validation_failed"]})
  (assert (is (validate-against-schema "stale_head" schema "payload.failureKind")
              None))
  (assert (is-not (validate-against-schema "bogus" schema "payload.failureKind")
                  None)))


(deftest test-additional-properties-false-rejects-extras
  (setv schema {"type" "object"
                "properties" {"ok" {"type" "boolean"}}
                "additionalProperties" False})
  (assert (is (validate-against-schema {"ok" True} schema "payload") None))
  (assert (is-not (validate-against-schema {"ok" True "extra" 1} schema "payload")
                  None)))


(deftest test-full-vocabulary-is-live-not-a-subset
  ;; 旧 subset に無かった語彙の代表(minItems)が enforce される —
  ;; 「書ける語彙 = enforce される語彙」の spot check。
  (setv schema {"type" "array" "minItems" 1})
  (assert (is (validate-against-schema ["x"] schema "payload") None))
  (assert (is-not (validate-against-schema [] schema "payload") None)))


(deftest test-json-type-distinctions-are-spec-accurate
  ;; Python の True == 1 に引きずられない(仕様: boolean と integer は別型)。
  (setv schema {"type" "integer"})
  (assert (is (validate-against-schema 1 schema "payload") None))
  (assert (is-not (validate-against-schema True schema "payload") None)))


(deftest test-reason-carries-location-prefix
  (setv reason (validate-against-schema
                 {"summary" 3 "entries" []}
                 REPORT-SCHEMA "payload"))
  (assert (is-not reason None))
  (assert (in "payload.summary" reason)))


(deftest test-admission-accepts-live-production-contracts
  ;; 生きている本番契約の語彙合併(ATTEND-REPORT / MERGE-RESULT /
  ;; IMPL-RESULT / conformance RESULT_SCHEMA)は受理される。
  (setv schema {"oneOf"
                [{"type" "object"
                  "required" ["summary"]
                  "properties" {"summary" {"type" "string" "minLength" 1}
                                "kind" {"enum" ["a" "b"]}
                                "sha" {"type" "string" "pattern" "^[0-9a-f]+$"}
                                "entries" {"type" "array" "items" ENTRY-SCHEMA}
                                "status" {"const" "ok"}}
                  "additionalProperties" False
                  "description" "annotation も仕様の一部"}]})
  (assert (is (schema-admission-error schema) None)))


(deftest test-admission-rejects-malformed-schemas
  ;; meta-schema 違反は launch 時 fail-closed(壊れた契約で session を
  ;; 作らない)。
  (assert (is-not (schema-admission-error {"type" 123}) None))
  (assert (is-not (schema-admission-error {"required" "not-an-array"}) None))
  (assert (is-not (schema-admission-error None) None))
  (assert (is-not (schema-admission-error "not-a-schema") None)))


(deftest test-boolean-schemas-are-spec-legal
  ;; JSON Schema の boolean form(true = 全通し / false = 全拒否)。
  (assert (is (schema-admission-error True) None))
  (assert (is (validate-against-schema {"anything" 1} True "payload") None))
  (assert (is-not (validate-against-schema {"anything" 1} False "payload") None)))

(deftest test-unresolvable-ref-is-fail-loud
  ;; 契約 schema は自己完結が前提。remote $ref は黙って素通しせず、
  ;; schema 側の欠陥として reason 化する(payload の欠陥と混同させない)。
  (setv reason (validate-against-schema
                 {"x" 1}
                 {"$ref" "http://example.invalid/nowhere.json"}
                 "payload"))
  (assert (is-not reason None))
  (assert (in "self-contained" reason)))


(deftest test-admission-rejects-noncompilable-regex
  ;; ECMA-262 の \\p{...} は Python re の外 — meta-schema の format:regex が
  ;; launch 時に fail-closed で弾く(静かな方言逸脱を契約にしない)。
  (assert (is-not (schema-admission-error {"pattern" "\\p{Letter}"}) None)))
