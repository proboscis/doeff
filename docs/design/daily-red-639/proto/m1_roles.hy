;; 試作(設計の最小実験): ADR-012 R49 の巡回の検査を「呼び先の定義の引数の並び」と「呼びの引数」から役で読む。
;; 字面の部分一致ではなく Hy の reader で form を読む(註・文字列・折れ方・引数の追加に依らない)。
(import hy pathlib [Path] sys)

(defn read-top-form [#^ Path path #^ str head]
  "file の中の `(<head> ` で始まる頂点の form を 1 つ Hy の reader で読む。無ければ None。"
  (setv text (.read-text path :encoding "utf-8"))
  (setv at (.find text (+ "\n(" head " ")))
  (when (< at 0) (return None))
  (hy.read (cut text (+ at 1) None)))

(defn defk-param-names [form]
  "(defk name [a b [c None]] ...) の引数の名の列(既定値つきの引数も名だけ)。"
  (lfor p (get form 2)
        (if (isinstance p hy.models.List) (str (get p 0)) (str p))))

(defn calls-of [form #^ str callee]
  "form の中の `(callee …)` の呼びを全部(入れ子も)。"
  (setv out [])
  (defn walk [node]
    (when (isinstance node hy.models.Sequence)
      (when (and (isinstance node hy.models.Expression) node
                 (isinstance (get node 0) hy.models.Symbol) (= (str (get node 0)) callee))
        (.append out node))
      (for [child node] (walk child))))
  (walk form)
  out)

(defn call-roles [#^ list params call]
  "呼びの引数を引数の名 → form へ写す(位置の引数と :keyword の引数)。"
  (setv roles {})
  (setv args (list (cut call 1 None)))
  (setv i 0)
  (setv pos 0)
  (while (< i (len args))
    (setv a (get args i))
    (if (isinstance a hy.models.Keyword)
        (do (setv (get roles (. a name)) (get args (+ i 1))) (setv i (+ i 2)))
        (do (setv (get roles (get params pos)) a) (setv pos (+ pos 1)) (setv i (+ i 1)))))
  roles)

(defn is-none [form] (and (isinstance form hy.models.Symbol) (= (str form) "None")))
(defn is-empty-tuple [form] (and (isinstance form hy.models.Tuple) (= (len form) 0)))

(defn sweep-violations [#^ Path acp-dir]
  (setv params (defk-param-names (read-top-form (/ acp-dir "judgment.hy") "defk turn-record-ended-status")))
  (for [role ["usage" "entries" "responses"]]
    (when (not-in role params)
      (return [f"turn-record-ended-status に役 {role} の引数が無い — 役の名が変わったなら R49 の検査も直す"])))
  (setv sweep (read-top-form (/ acp-dir "agentd.hy") "defk sweep-turn-records"))
  (setv calls (calls-of sweep "turn-record-ended-status"))
  (when (not calls) (return ["巡回が turn-record-ended-status を呼んでいない(記録を閉じない)"]))
  (setv out [])
  (for [c calls]
    (setv roles (call-roles params c))
    (when (not (is-none (.get roles "usage" (hy.models.Symbol "None"))))
      (.append out f"巡回が usage に {(hy.repr (get roles "usage"))} を渡している(R49 — 消費の和は手番の終わりの 1 回)"))
    (when (not (is-empty-tuple (.get roles "entries" (hy.models.Tuple []))))
      (.append out f"巡回が entries に {(hy.repr (get roles "entries"))} を渡している(R49)"))
    (when (not (is-none (.get roles "responses" (hy.models.Symbol "None"))))
      (.append out f"巡回が responses に {(hy.repr (get roles "responses"))} を渡している(R49)")))
  out)

(when (= __name__ "__main__")
  (setv v (sweep-violations (Path (get sys.argv 1))))
  (print (if v (+ "RED: " (.join " / " v)) "GREEN"))
  (sys.exit (if v 1 0)))
