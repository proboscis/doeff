;;; defsystem と :needs の形の検(doeff-hy の macro — ADR-DOE-CLUSTER-001 R4b・doeff-hy に検の収集が無いのでここに置く)。
;;;
;;; - defk / defhandler の頭の :needs は __doeff_needs__(frozenset)に残る。形は文字列の literal の集合だけ。
;;; - defsystem は静的に決まる形だけを受け、外れれば展開の時に断る。関数には静的な記述 __doeff_system__ が付く。
(require doeff-hy.macros [deftest defk <- val])
(import hy)

(val PRELUDE "
(require doeff-hy.macros [defk defsystem <-])
(import collections.abc [Callable])
(require doeff-hy.handle [defhandler])
(defclass Ping [])
")


(defk evaluate [source]
  {:pre [(: source str)] :post [(: % dict)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "PRELUDE と source を 1 つの名前空間で評価し、名前空間を返す(宣言の属性を読むため)。"
  (val namespace {"__name__" "defsystem_probe"})
  (hy.eval (hy.read-many (+ PRELUDE source)) namespace)
  namespace)


(defk refusal [source]
  {:pre [(: source str)] :post [(: % str)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "source の評価が断られることを確かめ、誤りの文を返す(断られなければ AssertionError)。"
  (var message None)
  (try
    (hy.eval (hy.read-many (+ PRELUDE source)) {"__name__" "defsystem_probe"})
    (except [error Exception]
      (:= message (str error))))
  (assert (is-not message None) (+ "断られなかった: " source))
  message)


(deftest test-needs-are-kept-on-defk-and-defhandler
  (<- ns (evaluate "
(defk cluster-foundation []
  {:pre [] :post [(: % list)] :needs #{\"pg-network\" \"claude-cli\"} :tags {:context \"myapp\" :role \"foundation\"}}
  [])
(defhandler records-http
  {:needs #{\"pg-network\"}}
  (Ping [] (resume None)))
(defk plain [] {:pre [] :post [(: % int)]} 1)
"))
  (assert (= (. (get ns "cluster_foundation") __doeff_needs__) (frozenset ["pg-network" "claude-cli"])))
  (assert (= (. (get ns "records_http") __doeff_needs__) (frozenset ["pg-network"])))
  (assert (is (. (get ns "plain") __doeff_needs__) None)))


(deftest test-needs-must-be-a-set-of-string-literals
  (<- a (refusal "(defk f [] {:pre [] :post [(: % int)] :needs [\"pg-network\"]} 1)"))
  (assert (in ":needs は能力の名の文字列の集合" a) a)
  (<- b (refusal "(defk f [] {:pre [] :post [(: % int)] :needs #{network}} 1)"))
  (assert (in ":needs の要素は空でない文字列の literal" b) b))


(deftest test-defsystem-keeps-a-static-description
  (<- ns (evaluate "
(defk notice [foundation poll] {:pre [(: foundation Callable) (: poll float)] :post [(: % int)]} 1)
(defsystem land [foundation]
  \"着地の系\"
  (land-notice (notice foundation 5.0)
    :needs #{\"pg-network\"} :replicas 1 :readiness {\"windowSeconds\" 30} :update \"handoff\" :environ {\"POLL\" \"5.0\"}))
"))
  (val land (get ns "land"))
  (assert (= land.__doc__ "着地の系"))
  (assert (= land.__doeff_system__
             {"name" "land" "params" ["foundation"]
              "jobs" [{"name" "land-notice" "function" "notice" "needs" ["pg-network"] "replicas" 1
                       "readiness" {"windowSeconds" 30} "update" "handoff" "environ" {"POLL" "5.0"}}]})
          land.__doeff_system__)
  (assert (= land.__doeff_tags__.role "entry")))


(deftest test-defsystem-keeps-the-type-of-a-typed-foundation
  ;; 型の注記つきの引数 [#^ T foundation] は展開でき(前は SyntaxError)、記述の params は名の列のまま・型は param_types(名 →
  ;; module:qualname)に残り、系の関数の引数にも注記が付く — 汎用の模擬の検が系を呼ばずに土台の型を読むため。
  ;; 型の無い引数は param_types に載らない(型の無い系の記述は上の検のとおり今と同じ)。
  (<- ns (evaluate "
(defk notice [foundation poll] {:pre [(: foundation Ping) (: poll float)] :post [(: % int)]} 1)
(defsystem land [#^ Ping foundation extra]
  (land-notice (notice foundation 5.0) :replicas 1))
"))
  (val land (get ns "land"))
  (assert (= (get land.__doeff_system__ "params") ["foundation" "extra"]) land.__doeff_system__)
  (assert (= (get land.__doeff_system__ "param_types") {"foundation" "defsystem_probe:Ping"}) land.__doeff_system__)
  (assert (is (get land.__annotations__ "foundation") (get ns "Ping")) land.__annotations__))


(deftest test-defsystem-refuses-a-type-that-is-not-a-name
  ;; 反例: 型の注記は module の最上位の型の名だけ — 和の型などの式は記述に module:qualname を残せないので展開の時に断る。
  (<- a (refusal "(defsystem s [#^ (| Ping None) foundation] (job (make foundation)))"))
  (assert (in "の型は module の最上位の型の名" a) a)
  (<- b (refusal "(defsystem s [\"foundation\"] (job (make foundation)))"))
  (assert (in "引数は記号か #^ 型 記号" b) b))


(deftest test-defsystem-refuses-forms-that-are-not-static
  (<- a (refusal "(defsystem s [foundation] (job (make (compute foundation))))"))
  (assert (in "Program の引数は系の引数" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :requires {\"kind\" \"k3s\"}))"))
  (assert (in "鍵 :requires は受けない" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :update \"rolling\"))"))
  (assert (in ":update は" c) c)
  (<- d (refusal "(defsystem s [foundation] (job (make foundation) :environ {\"POLL\" 5}))"))
  (assert (in ":environ の値は文字列" d) d)
  (<- e (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1) (job (make foundation) :replicas 1))"))
  (assert (in "job の名 job が 2 回ある" e) e)
  (<- f (refusal "(defsystem s [foundation] (job make))"))
  (assert (in "Program は (関数の記号 引数…) の呼び出し" f) f)
  (<- g (refusal "(defsystem s [foundation] \"説明だけ\")"))
  (assert (in "job の行が 1 つも無い" g) g))


(deftest test-defsystem-requires-the-replicas-of-every-job
  ;; job の望む台数(:replicas)は必ず書く — 省くと宣言し直しの時に Service へ書く台数が決まらない(#3487)。0 / 1 の外の値・文字列も断る。
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :needs #{\"net\"}))"))
  (assert (in ":replicas が無い" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :replicas 2))"))
  (assert (in ":replicas は 0 / 1 のどれか" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas \"1\"))"))
  (assert (in ":replicas は 0 / 1 のどれか" c) c))
