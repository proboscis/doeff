;;; 制約付き JSON-Schema subset の検証(ADR 0035 / DOE-004 C3)。
;;;
;;; oracle: main.rs validate_against_schema(:4000-4117)の verbatim 移植。
;;; サポートする語彙は oneOf / const / type / minLength / pattern / required /
;;; properties のみ — 評価順とエラー文言まで oracle parity(agent は文言を
;;; 読んで payload を直すので、reason は契約面)。検証は純関数で、reject の
;;; 記録(event)や -32002 への写像は呼び手(host の report_result)所有。

(require doeff-hy.macros [deff])

(import json)
(import re)


(setv JsonValue (| dict list str int float bool None))


(deff json-repr [value]
  {:pre [(: value JsonValue)]
   :post [(: % str)]}
  "serde の Display(compact JSON)相当 — const / pattern の文言に使う。"
  (json.dumps value :separators #("," ":") :ensure-ascii False))

(deff is-json-integer [value]
  {:pre [(: value JsonValue)]
   :post [(: % bool)]}
  ;; serde: is_i64/is_u64。Python の bool は int の subclass なので除外。
  (and (isinstance value int) (not (isinstance value bool))))

(deff type-matches [ty instance loc]
  {:pre [(: ty str) (: instance JsonValue) (: loc str)]
   :post [(: % bool)]}
  (cond
    (= ty "object") (isinstance instance dict)
    (= ty "array") (isinstance instance list)
    (= ty "string") (isinstance instance str)
    (= ty "number") (and (isinstance instance #(int float))
                         (not (isinstance instance bool)))
    (= ty "integer") (is-json-integer instance)
    (= ty "boolean") (isinstance instance bool)
    (= ty "null") (is instance None)
    True (raise (ValueError f"schema at '{loc}' uses unsupported type '{ty}'"))))


(deff validate-against-schema [instance schema loc]
  {:pre [(: instance JsonValue) (: schema JsonValue) (: loc str)]
   :post [(: % (| str None))]}
  "instance を schema で検証する。戻り値 = None(適合)/ reason 文字列
   (不適合 — oracle の Err(String) と同文言)。loc は dotted breadcrumb。"
  (when (not (isinstance schema dict))
    (return f"schema at '{loc}' is not a JSON object"))

  ;; oneOf: 正確に 1 branch が適合(集約 reason 付き)。
  (when (in "oneOf" schema)
    (setv one-of (get schema "oneOf"))
    (when (not (isinstance one-of list))
      (return f"'oneOf' at '{loc}' must be an array"))
    (setv matched 0)
    (setv branch-errors [])
    (for [[i branch] (enumerate one-of)]
      (setv err (validate-against-schema instance branch loc))
      (if (is err None)
          (+= matched 1)
          (.append branch-errors f"  variant {i}: {err}")))
    (cond
      (= matched 1) None
      (= matched 0)
      (do
        (setv joined (.join "\n" branch-errors))
        (setv total (len one-of))
        (return (+ f"value at '{loc}' matched none of the {total} allowed variants:\n"
                   joined)))
      True
      (return (+ f"value at '{loc}' matched {matched} variants "
                 "but exactly one is allowed"))))

  ;; const: 値の等値。
  (when (in "const" schema)
    (setv expected (get schema "const"))
    (when (!= instance expected)
      (setv rendered (json-repr expected))
      (return f"'{loc}' must equal {rendered}")))

  ;; type: JSON 型タグ。
  (when (and (in "type" schema) (isinstance (get schema "type") str))
    (setv ty (get schema "type"))
    (setv ok
          (try
            (type-matches ty instance loc)
            (except [e ValueError]
              (return (str e)))))
    (when (not ok)
      (return f"'{loc}' must be of type {ty}")))

  ;; minLength(string のみに適用 — 他型は素通し、oracle と同じ)。
  (when (and (in "minLength" schema) (isinstance instance str))
    (setv min-len (get schema "minLength"))
    (when (and (isinstance min-len int) (not (isinstance min-len bool))
               (>= min-len 0))
      (setv got (len instance))
      (when (< got min-len)
        (return (+ f"'{loc}' must be a string of at least length {min-len} "
                   f"(got {got} chars)")))))

  ;; pattern(identity field 用。不正 pattern は fail-closed)。
  (when (and (in "pattern" schema) (isinstance (get schema "pattern") str)
             (isinstance instance str))
    (setv pattern (get schema "pattern"))
    (setv rendered (json-repr pattern))
    (try
      (setv compiled (re.compile pattern))
      (except [e re.error]
        (return f"schema at '{loc}' has invalid pattern {rendered}: {e}")))
    (when (is (.search compiled instance) None)
      (return f"'{loc}' must match pattern {rendered}")))

  ;; required: object 上の必須 field。
  (when (and (in "required" schema) (isinstance (get schema "required") list))
    (for [key (get schema "required")]
      (when (isinstance key str)
        (setv present (and (isinstance instance dict) (in key instance)))
        (when (not present)
          (return f"'{loc}' is missing required field '{key}'")))))

  ;; properties: 存在する child のみ再帰(不在は required の管轄)。
  (when (and (in "properties" schema) (isinstance (get schema "properties") dict)
             (isinstance instance dict))
    (for [[key subschema] (.items (get schema "properties"))]
      (when (in key instance)
        (setv err (validate-against-schema (get instance key) subschema
                                           f"{loc}.{key}"))
        (when (is-not err None)
          (return err)))))

  None)
