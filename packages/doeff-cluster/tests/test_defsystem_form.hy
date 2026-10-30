;;; defsystem と :needs の形の検(defsystem は doeff-cluster の macro・:needs は doeff-hy の defk / defhandler — ADR-DOE-CLUSTER-001 R4b)。
;;;
;;; - defk / defhandler の頭の :needs は __doeff_needs__(frozenset)に残る。形は文字列の literal の集合だけ。
;;; - defsystem は静的に決まる形だけを受け、外れれば展開の時に断る。関数には静的な記述 __doeff_system__ が付く。
(require doeff-hy.macros [deftest defk <- val])
(import hy)

(val PRELUDE "
(require doeff-hy.macros [defk <-])
(require doeff-cluster.shared.entry.macros [defsystem])
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
    :needs #{\"pg-network\"} :replicas 1 :peak-mib 200 :settle-mib 100 :readiness {\"windowSeconds\" 30} :update \"handoff\" :environ {\"POLL\" \"5.0\"}))
"))
  (val land (get ns "land"))
  (assert (= land.__doc__ "着地の系"))
  (assert (= land.__doeff_system__
             {"name" "land" "params" ["foundation"]
              "jobs" [{"name" "land-notice" "function" "notice" "needs" ["pg-network"] "replicas" 1 "peakMiB" 200 "settleMiB" 100
                       "readiness" {"windowSeconds" 30} "update" "handoff" "environ" {"POLL" "5.0"}}]})
          land.__doeff_system__)
  (assert (= land.__doeff_tags__.role "entry"))
  ;; 系の関数の答えの型の注記は System — 型検査が系の値を System と読み、使い手の検査器が「公開の関数に答えの型が無い」と
  ;; 言わないため(#3366 — 注記の無い展開では、使い手の検査器の「公開の関数に型の注記が無い」の規則が全部の defsystem に当たった)。
  (import doeff_cluster.shared.intent.service_model [System])
  (assert (is (get land.__annotations__ "return") System) land.__annotations__))


(deftest test-defsystem-keeps-a-readiness-field-named-by-the-environ
  ;; readiness の欄は数の代わりに同じ job の :environ の名の文字列で書ける(readiness_model の註)— 記述には名のまま残り、数への読みは
  ;; 宣言の組み立て(system-declaration)がする。数でも文字列でもない値は今どおり展開の時に断る。
  (<- ns (evaluate "
(defk notice [foundation poll] {:pre [(: foundation Callable) (: poll float)] :post [(: % int)]} 1)
(defsystem land [foundation]
  (land-notice (notice foundation 5.0)
    :needs #{\"pg-network\"} :replicas 1 :peak-mib 200 :settle-mib 100 :readiness {\"windowSeconds\" 30 \"retiredSeconds\" \"RETIRED_SECONDS\"} :update \"handoff\"
    :environ {\"RETIRED_SECONDS\" \"600\"}))
"))
  (val job (get (. (get ns "land") __doeff_system__) "jobs" 0))
  (assert (= (get job "readiness") {"windowSeconds" 30 "retiredSeconds" "RETIRED_SECONDS"}) job)
  (assert (= (get job "environ") {"RETIRED_SECONDS" "600"}) job)
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :readiness {\"windowSeconds\" soon}))"))
  (assert (in ":readiness の値は数か :environ の名の文字列" a) a))


;; card ki-02e836a9c628: 「既定を空にした」事実と「宣言の時に埋める」印を別の所(使い手の手書きの表)で持つと、表への書き漏れが宣言する者の
;; 確かめの穴になった(使い手の 1 系だけの宣言が、埋める名を空のまま出した)。:environ の値を既定の値と埋める名の 2 つの型に分ける。
(deftest test-defsystem-carries-the-names-filled-at-declaration
  (<- ns (evaluate "
(defk notice [foundation poll] {:pre [(: foundation Callable) (: poll float)] :post [(: % int)]} 1)
(defsystem land [foundation]
  (land-notice (notice foundation 5.0)
    :needs #{\"pg-network\"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"POLL\" \"5.0\" \"BROKER\" (filled) \"PROXY\" (empty) \"ADDRESS\" (filled)}))
"))
  (val land (get ns "land"))
  (val job (get (. land __doeff_system__) "jobs" 0))
  ;; 静的な記述: 埋める名と (empty) の既定の値は空の文字列・埋める名は "filled" に、空を「無し」と読む名は "empty" に名の順で載る。
  (assert (= (get job "environ") {"POLL" "5.0" "BROKER" "" "PROXY" "" "ADDRESS" ""}) job)
  (assert (= (get job "filled") ["ADDRESS" "BROKER"]) job)
  (assert (= (get job "empty") ["PROXY"]) job)
  ;; 印の無い job の記述は鍵 "filled" も "empty" も持たない。
  (<- plain (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem s [foundation] (job (notice foundation) :needs #{\"n\"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"POLL\" \"5.0\"}))
"))
  (val described (get (. (get plain "s") __doeff_system__) "jobs" 0))
  (assert (and (not-in "filled" described) (not-in "empty" described)) described))


(deftest test-defsystem-refuses-an-unmarked-empty-default
  ;; 反例: 印の無い空の文字列(空白だけも)は型を決めていない既定 — 展開の時に名を名指して断り、(filled) か (empty) で書けと言う(頭の註)。
  (<- bare (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"POLL\" \"5.0\" \"BROKER\" \"\"}))"))
  (assert (in ":environ の BROKER の既定が印の無い空の文字列" bare) bare)
  (assert (and (in "(filled)" bare) (in "(empty)" bare)) bare)
  (<- blank (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"PROXY\" \"  \"}))"))
  (assert (in ":environ の PROXY の既定が印の無い空の文字列" blank) blank))


(defk probe-program []
  {:pre [] :post [(: % int)] :tags {:context "doeff-cluster-test" :role "entry"}}
  "構成子 job に渡す Program の値を作るため(呼び出しの形が module の最上位の関数を名指す)。"
  1)


(deftest test-defsystem-refuses-marks-other-than-filled-and-empty
  ;; 反例: 印は (filled) と (empty) の 2 つだけ・引数を取らない。
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"BROKER\" (missing)}))"))
  (assert (in ":environ の BROKER の値は文字列か (filled) か (empty)" c) c)
  (<- d (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :environ {\"BROKER\" (filled \"x\")}))"))
  (assert (in ":environ の BROKER の値は文字列か (filled) か (empty)" d) d))


(deftest test-job-refuses-filled-names-outside-the-environ-or-with-a-default
  ;; 構成子 job は埋める名を Job の filled に載せ((empty) の名と埋める名の無い job は載せない)、埋める名が :environ に無い・既定の値を
  ;; 持つ形を断る(展開の外から呼ぶ時)。
  (import doeff_cluster.shared.entry.service_build [job])
  (import doeff_cluster.shared.intent.service_model [CallShape])
  (val call (CallShape :function probe-program :args [] :kwargs {}))
  (val built (job "j" (probe-program) :call call :needs #{"n"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {"POLL" "5.0" "BROKER" "" "PROXY" ""}
                  :filled (frozenset ["BROKER"])))
  (assert (= built.filled (frozenset ["BROKER"])) built)
  (assert (= (dfor v built.environ v.name v.value) {"BROKER" "" "POLL" "5.0" "PROXY" ""}) built.environ)
  (assert (= (. (job "j" (probe-program) :call call :needs #{"n"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {"POLL" "5.0"}) filled) (frozenset)))
  (var stranger None)
  (try (job "j" (probe-program) :call call :needs #{"n"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {"POLL" "5.0"} :filled (frozenset ["BROKER"]))
       (except [error ValueError] (:= stranger (str error))))
  (assert (and stranger (in "BROKER" stranger) (in ":environ に無い" stranger)) stranger)
  (var defaulted None)
  (try (job "j" (probe-program) :call call :needs #{"n"} :replicas 1 :peak-mib 200 :settle-mib 100 :environ {"BROKER" "redis://x"} :filled (frozenset ["BROKER"]))
       (except [error ValueError] (:= defaulted (str error))))
  (assert (and defaulted (in "既定の値を持つ" defaulted)) defaulted))


(deftest test-defsystem-keeps-the-type-of-a-typed-foundation
  ;; 型の注記つきの引数 [#^ T foundation] は展開でき(前は SyntaxError)、記述の params は名の列のまま・型は param_types(名 →
  ;; module:qualname)に残り、系の関数の引数にも注記が付く — 汎用の模擬の検が系を呼ばずに土台の型を読むため。
  ;; 型の無い引数は param_types に載らない(型の無い系の記述は上の検のとおり今と同じ)。
  (<- ns (evaluate "
(defk notice [foundation poll] {:pre [(: foundation Ping) (: poll float)] :post [(: % int)]} 1)
(defsystem land [#^ Ping foundation extra]
  (land-notice (notice foundation 5.0) :replicas 1 :peak-mib 200 :settle-mib 100))
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
  (assert (in ":environ の値は文字列か (filled) か (empty)" d) d)
  (<- e (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100) (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100))"))
  (assert (in "job の名 job が 2 回ある" e) e)
  (<- f (refusal "(defsystem s [foundation] (job make))"))
  (assert (in "Program は (関数の記号 引数…) の呼び出し" f) f)
  (<- g (refusal "(defsystem s [foundation] \"説明だけ\")"))
  (assert (in "job の行が 1 つも無い" g) g))


(deftest test-defsystem-requires-the-replicas-of-every-job
  ;; job の望む台数(:replicas)は必ず書く — 省くと宣言し直しの時に Service へ書く台数が決まらない(#3487)。0 / 1 の外の値・文字列も断る。
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :needs #{\"net\"}))"))
  (assert (in ":replicas が無い" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :replicas 2 :peak-mib 200 :settle-mib 100))"))
  (assert (in ":replicas は 0 / 1 のどれか" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas \"1\"))"))
  (assert (in ":replicas は 0 / 1 のどれか" c) c))


(deftest test-defsystem-requires-the-memory-of-every-job
  ;; job の memory(:peak-mib・:settle-mib — card ki-2acb63aec0fd)は必ず書く。既定の値で埋めない: 省いた鍵・負の数・真偽・文字列・
  ;; 山を越える落ち着きは、系と job を名指して展開の時に断る。:single-host の空の訳も断る。
  ;; 失敗ケース = 省いた job に既定の値を入れて通す・片方だけの job を通す・True を 1 と読む。
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :settle-mib 100))"))
  (assert (in "defsystem s の job job" a) a)
  (assert (in ":peak-mib が無い" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200))"))
  (assert (in "defsystem s の job job" b) b)
  (assert (in ":settle-mib が無い" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 100 :settle-mib 200))"))
  (assert (in ":settle-mib 200 が :peak-mib 100 を越える" c) c)
  (<- d (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib -1 :settle-mib 0))"))
  (assert (in ":peak-mib は 0 以上の整数の literal" d) d)
  (<- e (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib True :settle-mib 0))"))
  (assert (in ":peak-mib は 0 以上の整数の literal" e) e)
  (<- f (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib \"200\" :settle-mib 100))"))
  (assert (in ":peak-mib は 0 以上の整数の literal" f) f)
  (<- g (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :single-host \"\"))"))
  (assert (in ":single-host は 1 台の worker に縛ってよい訳の空でない文字列" g) g)
  (<- h (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :peak-mib 300))"))
  (assert (in "鍵 :peak-mib が 2 回ある" h) h))


(deftest test-defsystem-keeps-the-memory-and-the-single-host-reason-in-the-static-description
  ;; memory は静的な記述に peakMiB・settleMiB(どの job にも)で、1 台に縛る訳は singleHost(書いた job にだけ)で載る。
  (<- ns (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem store [foundation]
  (keeper (notice foundation) :replicas 1 :peak-mib 900 :settle-mib 400 :single-host \"機体の disk の data を持つ\")
  (plain (notice foundation) :replicas 1 :peak-mib 0 :settle-mib 0))
"))
  (val jobs (get (. (get ns "store") __doeff_system__) "jobs"))
  (assert (= (get jobs 0) {"name" "keeper" "function" "notice" "replicas" 1 "peakMiB" 900 "settleMiB" 400
                           "singleHost" "機体の disk の data を持つ"})
          jobs)
  (assert (= (get jobs 1) {"name" "plain" "function" "notice" "replicas" 1 "peakMiB" 0 "settleMiB" 0}) jobs))


(deftest test-defsystem-keeps-the-store-reads-and-writes-in-the-static-description
  ;; job が外の置き場から読む・書く物(:reads / :writes — 任意の欄・#3495)は静的な記述の job の dict に整列した名の列で載り
  ;; (doeff-linter が綴りで読む)、書かない job の dict には鍵が無い。空の集合は「読み書きしない」の宣言として載る。
  (<- ns (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem shop [foundation]
  (reader (notice foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads #{\"store:orders\" \"events:audit\"} :writes #{})
  (quiet (notice foundation) :replicas 0 :peak-mib 200 :settle-mib 100))
"))
  (val jobs (get (. (get ns "shop") __doeff_system__) "jobs"))
  (assert (= (get jobs 0) {"name" "reader" "function" "notice" "replicas" 1 "peakMiB" 200 "settleMiB" 100
                           "reads" ["events:audit" "store:orders"] "writes" []})
          jobs)
  (assert (= (get jobs 1) {"name" "quiet" "function" "notice" "replicas" 0 "peakMiB" 200 "settleMiB" 100}) jobs))


(deftest test-defsystem-refuses-store-names-that-are-not-store-colon-name
  ;; 反例: `:` の無い綴り・2 つある綴り・片側が空の綴り・集合でない値・文字列でない要素・同じ鍵の 2 回目は展開で断る — linter が
  ;; 実行せずに読める綴りだけを通すため(#3495)。置き場の名そのものは照らさない(使い手の語彙)。
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads #{\"orders\"}))"))
  (assert (in ":reads の綴りは <置き場>:<名>(`:` がちょうど 1 つ・両側が空でない)" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :writes #{\"store:\"}))"))
  (assert (in ":writes の綴りは <置き場>:<名>" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :writes #{\"store:orders:x\"}))"))
  (assert (in ":writes の綴りは <置き場>:<名>" c) c)
  (<- d (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads [\"store:orders\"]))"))
  (assert (in ":reads は置き場の名の文字列の集合" d) d)
  (<- e (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads #{orders}))"))
  (assert (in ":reads の要素は文字列の literal" e) e)
  (<- f (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads #{} :reads #{}))"))
  (assert (in "鍵 :reads が 2 回ある" f) f))


(deftest test-defsystem-keeps-the-served-entries-in-the-static-description
  ;; job が開いて待ち受ける入口(:serves — 任意の欄)は静的な記述の job の dict に整列した名の列で載り、書かない job の dict には
  ;; 鍵が無い — 使い手が同じ入口を開く job の組を、job の形から推し量らずに記述から引くため。
  (<- ns (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem shop [foundation]
  (front (notice foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :serves #{\"shop:http\" \"shop:admin\"} :reads #{\"store:orders\"})
  (sweeper (notice foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :reads #{\"store:orders\"}))
"))
  (val jobs (get (. (get ns "shop") __doeff_system__) "jobs"))
  (assert (= (get jobs 0) {"name" "front" "function" "notice" "replicas" 1 "peakMiB" 200 "settleMiB" 100
                           "serves" ["shop:admin" "shop:http"] "reads" ["store:orders"]})
          jobs)
  (assert (= (get jobs 1) {"name" "sweeper" "function" "notice" "replicas" 1 "peakMiB" 200 "settleMiB" 100 "reads" ["store:orders"]}) jobs)
  (assert (not-in "serves" (get jobs 1)) jobs))


(deftest test-defsystem-refuses-served-entries-that-are-not-store-colon-entry
  ;; 反例: :serves も :reads と同じ綴りの検めを通る — `:` の無い綴り・集合でない値・同じ鍵の 2 回目は展開で断る。
  (<- a (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :serves #{\"http\"}))"))
  (assert (in ":serves の綴りは <置き場>:<名>(`:` がちょうど 1 つ・両側が空でない)" a) a)
  (<- b (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :serves [\"shop:http\"]))"))
  (assert (in ":serves は置き場の名の文字列の集合" b) b)
  (<- c (refusal "(defsystem s [foundation] (job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100 :serves #{\"shop:http\"} :serves #{\"shop:http\"}))"))
  (assert (in "鍵 :serves が 2 回ある" c) c))


(deftest test-defsystem-declares-the-task-limits-of-the-system
  ;; 系の頭の :task-limits(2026-10-12・card ki-b0e8028c8c8d)は、系の値の task-limits(GroupLimit)と静的な記述の task_limits に載る —
  ;; 組の上限の値の持ち主は系の宣言 1 つで、task は組の名だけを名乗る。:task-limits の無い系の静的な記述は前と同じ形(鍵を書かない)。
  (<- ns (evaluate "
(defk notice [foundation] {:pre [(: foundation Callable)] :post [(: % int)]} 1)
(defsystem land [foundation]
  \"着地の系\"
  :task-limits {\"heavy\" {\"scope\" \"worker\" \"limit\" 6}
                \"heavy-caller\" {\"scope\" \"cluster\" \"limit\" 2 \"perPart\" True}}
  (land-notice (notice foundation) :needs #{\"net\"} :replicas 1 :peak-mib 200 :settle-mib 100))
"))
  (val land (get ns "land"))
  (assert (= (get land.__doeff_system__ "task_limits")
             {"heavy" {"scope" "worker" "limit" 6 "perPart" False}
              "heavy-caller" {"scope" "cluster" "limit" 2 "perPart" True}})
          land.__doeff_system__)
  (import doeff_cluster.shared.intent.detached_model [GroupLimit])
  (import sys types)
  ;; 系の値を作るには job の関数が module:qualname で引ける要る(identity)— 評価した名前空間を module として置いてから呼ぶ。
  (val module (types.ModuleType "defsystem_probe"))
  (.update module.__dict__ ns)
  (setv (get sys.modules "defsystem_probe") module)
  (val system (try (land (get ns "notice")) (finally (.pop sys.modules "defsystem_probe" None))))
  (assert (= system.task-limits #((GroupLimit :group "heavy" :scope "worker" :limit 6 :system "land")
                                  (GroupLimit :group "heavy-caller" :scope "cluster" :limit 2 :per-part True :system "land")))
          system.task-limits)
  (assert (= (len system.jobs) 1) system.jobs))


(deftest test-defsystem-refuses-task-limits-that-are-not-static-or-not-valid
  ;; 反例: 範囲の名・0 の上限・整数でない上限・知らない欄・「/」を含む組の名・dict でない値・知らない頭の鍵・2 回目の鍵は展開で断る —
  ;; linter が実行せずに読める形だけを通し、値の規則は coordinator の受け口と同じにするため。
  (val job-row "(job (make foundation) :replicas 1 :peak-mib 200 :settle-mib 100)")
  (for [#(limits word) [#("{\"g\" {\"scope\" \"node\" \"limit\" 1}}" "\"scope\" は worker / cluster のどれか")
                        #("{\"g\" {\"scope\" \"worker\" \"limit\" 0}}" "\"limit\" は 1 以上の整数の literal")
                        #("{\"g\" {\"scope\" \"worker\" \"limit\" \"6\"}}" "\"limit\" は 1 以上の整数の literal")
                        #("{\"g\" {\"scope\" \"worker\" \"limit\" 1 \"max\" 2}}" "受けない欄 ['max']")
                        #("{\"g\" {\"scope\" \"worker\" \"limit\" 1 \"system\" \"other\"}}" "受けない欄 ['system']")
                        #("{\"a/b\" {\"scope\" \"worker\" \"limit\" 1}}" "組の名は「/」を含まない")
                        #("[\"g\"]" ":task-limits は文字列の鍵の dict の literal")]]
    (<- message (refusal (.format "(defsystem s [foundation] :task-limits {} {})" limits job-row)))
    (assert (in word message) #(word message)))
  (<- unknown (refusal (.format "(defsystem s [foundation] :limits {{}} {})" job-row)))
  (assert (in "系の頭の鍵 :limits は受けない" unknown) unknown)
  (<- twice (refusal (.format "(defsystem s [foundation] :task-limits {{}} :task-limits {{}} {})" job-row)))
  (assert (in "系の頭の鍵 :task-limits が 2 回ある" twice) twice))
