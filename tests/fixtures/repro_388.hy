;;; Repro for #388 — (import ...) inside defp/defk corrupts return value.
;;;
;;; When (import ...) appears in the body, _parse-do-body marks it as
;;; a __plain__ statement. defp's expansion must emit it as-is, not
;;; wrap it in (yield ...).

(require doeff-hy.macros [defk defp <-])
(import doeff [do :as _doeff-do])
(import doeff_core_effects [Ask])


;; --- defp with (import ...) inside body ---

(defp import-inside-defp
  {:post [(not (is % None))]}
  (<- path (Ask "test"))
  (import json)
  (json.dumps {"a" (str path)}))


;; --- defk with (import ...) inside body ---

(defk import-inside-defk [path]
  {:pre [(: path str)]
   :post [(: % str)]}
  (import json)
  (json.dumps {"a" (str path)}))


;; --- defp with (import ...) and no bind after it ---

(defp import-only-defp
  {:post [(: % str)]}
  (import json)
  (json.dumps {"status" "ok"}))


;; --- defp with multiple imports ---

(defp multi-import-defp
  {:post [(not (is % None))]}
  (<- val (Ask "key"))
  (import json)
  (import os.path)
  (json.dumps {"val" (str val) "sep" os.path.sep}))
