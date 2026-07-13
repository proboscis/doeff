;;; 契約 schema 検証 = JSON Schema(確立した外部意味論の輸入)。
;;;
;;; 歴史(doeff#482 / ACP plan U1 裁定 2026-07-06): 初代 Rust agentd は
;;; 検証器を JSON Schema の無裁可 subset(oneOf/const/type/minLength/
;;; pattern/required/properties のみ、他キーワードは黙殺 = fail-open)で
;;; 実装し、C3 の旧実装 parity 移植がその省略をそのまま「契約」に昇格
;;; させた。ACP steward の実障害(items 違反 payload が InvocationSucceeded)
;;; で露呈。ユーザー裁定: 「JSON Schema で契約を書く」と決めた時点で意味論は
;;; 仕様のそれが唯一の正 — subset は逸脱であって代替仕様ではない。
;;;
;;; 本モジュールは jsonschema(参照実装、$schema 尊重・既定 draft 2020-12)
;;; への薄い adapter のみを持つ。検証意味論を自前実装しない。
;;;   - validate-against-schema: payload 検証(report_result 時)。
;;;     reason 文言は契約面(agent が読んで payload を直す)— json_path で
;;;     違反箇所を指す。
;;;   - schema-admission-error: schema 自体の meta-schema 検証
;;;     (session.launch 時 fail-closed — 壊れた契約で session を作らない)。
;;; 注意: report-result-mcp relay(relaymain.py)は stdlib-only の凍結物理
;;; (S1 boot latency)であり、検証は host 側のみで行う — ここに import を
;;; 足しても relay の依存は増えない。

(require doeff-hy.macros [deff])

(import jsonschema.exceptions [SchemaError best-match])
(import jsonschema.validators [validator-for])
(import referencing.exceptions [Unresolvable])


(setv JsonValue (| dict list str int float bool None))


(deff _located-reason [loc error]
  {:pre [(: loc str) (: error "value")]
   :post [(: % str)]}
  "ValidationError → 契約面の reason 文言。json_path の $ を loc に置換して
   違反箇所(例 payload.entries[0])を agent に指す。"
  (setv path error.json-path)
  (setv suffix (if (.startswith path "$") (cut path 1 None) path))
  f"'{loc}{suffix}': {error.message}")


(deff validate-against-schema [instance schema loc]
  {:pre [(: instance JsonValue) (: schema JsonValue) (: loc str)]
   :post [(: % (| str None))]}
  "instance を JSON Schema 意味論で検証する。戻り値 = None(適合)/
   reason 文字列(不適合)。schema の妥当性自体は launch 時に
   schema-admission-error が保証済みという前提(防御で SchemaError も
   reason 化する)。"
  (when (not (isinstance schema #(dict bool)))
    (return f"schema at '{loc}' is not a JSON Schema (object or boolean)"))
  (setv cls (validator-for schema))
  (try
    (.check-schema cls schema)
    (except [e SchemaError]
      (return f"schema at '{loc}' is itself invalid: {e.message}")))
  (try
    (setv errors (list (.iter-errors (cls schema) instance)))
    (except [e Unresolvable]
      ;; 契約 schema は自己完結が前提(remote $ref のレジストリを持たない)。
      ;; 壊れているのは payload でなく schema — 文言でそれを明示する。
      (return (+ f"schema at '{loc}' is broken (not the payload): "
                 f"unresolvable reference: {e} — contract schemas must be "
                 "self-contained"))))
  (if errors
      (_located-reason loc (best-match errors))
      None))


(deff schema-admission-error [schema]
  {:pre [(: schema "value")]
   :post [(: % (| str None))]}
  "payload_schema の受理判定(session.launch の fail-closed 面)。
   None = 受理 / 文字列 = 拒否理由。meta-schema 違反(壊れた schema)を
   持つ契約で session を作らない — 検証されない契約は存在させない。"
  (when (not (isinstance schema #(dict bool)))
    (return "payload_schema must be a JSON Schema (object or boolean)"))
  (try
    (.check-schema (validator-for schema) schema)
    (except [e SchemaError]
      (return f"payload_schema is not a valid JSON Schema: {e.message}")))
  None)
