;;; defsystem の形の読みと展開(macro は macros.hy の defsystem — ここは展開の時に呼ぶ関数だけを置く)。
;;;
;;;   (defsystem agora-land [foundation]
;;;     "着地の報せの系"
;;;     (land-notice (land-notice foundation)
;;;       :needs #{"pg-network"} :replicas 1 :readiness {"windowSeconds" 30} :update "handoff" :environ {"POLL" "5.0"}))
;;;
;;; 系 = 土台(handler の組を返す module の最上位の関数)を引数に受け、名前 → Program と約束(needs・replicas・readiness・update・environ)の組を
;;; 返す関数(baseFrom・overlay は Program の job に無い — 詰めた commit と別の commit で解くことになるため)。job の Program は doeff-cluster の job API が受ける値 1 つ(ADR-DOE-CLUSTER-001 R1)。
;;;
;;; 形は静的に決まる物だけを受ける(doeff-linter が実行せずに読めるように — ADR-DOE-CLUSTER-001 R4b):
;;;   job の行 = (名の記号 (関数の記号 引数…) :鍵 値 …)。引数は系の引数の記号か literal(文字列・数・keyword・True/False/None と、
;;;   それを入れた list と dict)。:needs は文字列の集合の literal、:readiness は文字列の鍵と数の dict、:environ は
;;;   文字列の鍵と文字列の値の dict、:update は "recreate" か "handoff"。外れれば展開の時の SyntaxError。
;;;   :replicas は job の望む台数(0 = 取り下げ・1 = 動かす)の整数の literal で、**必ず書く** — coordinator の Service は job 1 つに 1 つで、
;;;   台数は job の性質。宣言し直しはこの値を書く(宣言の道具の引数で台数を持たない — 持つと、取り下げてあった Service へ台数を書かずに
;;;   宣言し直した時に job が起きない取り違えが起きる)。
;;;   :reads / :writes は job が外の置き場(記録の service など)から読む・書く物の集合で、任意の欄 — 文字列の literal の集合で、綴りは
;;;   <置き場>:<名>(`:` がちょうど 1 つ・両側が空でない)。置き場の名の集合は使い手の repo の語彙なので doeff は持たない。静的な記述の
;;;   job の dict にだけ載り、job の値(doeff-cluster の Job)と宣言の行には載らない(実行時に読む物が無い)。使い手の系の job に欄を
;;;   求める・置き場の名を照らすのは使い手の側の doeff-linter の規則(#3495・#3496)。
;;; 値の意味(readiness の窓の形・environ の名の衝突など)は doeff-cluster の service_build.system-of が呼ばれた時に検める。
(import hy)
(import hy.models [Dict Expression Float Integer Keyword List Set String Symbol])
(import doeff-hy.declarations [needs-names])

(setv JOB-KEYS #(":needs" ":replicas" ":readiness" ":update" ":environ" ":reads" ":writes"))
(setv UPDATE-FORMS #("recreate" "handoff"))
;; job の望む台数として書ける値(coordinator の Service の replicas が受ける値と同じ — 0 = 取り下げ・1 = 動かす)。
(setv REPLICAS-VALUES #(0 1))
(setv CONSTANT-SYMBOLS #("True" "False" "None"))


(defn literal? [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  "Program の引数に置ける literal か(静的に値が決まる form だけを通すため)。"
  (cond
    (isinstance form #(String Integer Float Keyword)) True
    (isinstance form Symbol) (in (str form) CONSTANT-SYMBOLS)
    (isinstance form #(List Dict)) (all (gfor item form (literal? item)))
    True False))


(defn string-dict [form #^ str where #^ str key value-types #^ str value-word]  ; defk にできない: macro の展開の時に呼ぶ関数
  "文字列の鍵の dict の literal を検め、鍵と値の form の組の list を返す(:readiness・:environ の共通の読み)。"
  (when (not (isinstance form Dict))
    (raise (SyntaxError (.format "{}: {} は文字列の鍵の dict の literal: {}" where key (hy.repr form)))))
  (setv pairs (list (zip (cut form None None 2) (cut form 1 None 2))))
  (for [#(k v) pairs]
    (when (not (isinstance k String))
      (raise (SyntaxError (.format "{}: {} の鍵は文字列の literal: {}" where key (hy.repr k)))))
    (when (not (isinstance v value-types))
      (raise (SyntaxError (.format "{}: {} の値は{}の literal: {}" where key value-word (hy.repr v))))))
  pairs)


(defn record-names [form #^ str where #^ str key]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:reads` / `:writes` の値の form を展開の時に検め、外の置き場の名の整列した list を返す — linter が実行せずに綴りで読めるように、
   形は文字列の literal の集合だけ(空の集合 = 読み書きしないと宣言した)、綴りは <置き場>:<名>(`:` がちょうど 1 つ・両側が空でない)。
   置き場の名が何かは照らさない(使い手の repo の語彙 — 頭注)。"
  (when (not (isinstance form Set))
    (raise (SyntaxError (.format "{}: {} は置き場の名の文字列の集合 #{{\"<置き場>:<名>\" …}}: {}" where key (hy.repr form)))))
  (for [item form]
    (when (not (isinstance item String))
      (raise (SyntaxError (.format "{}: {} の要素は文字列の literal: {}" where key (hy.repr item)))))
    (setv parts (.split (str item) ":"))
    (when (not (and (= (len parts) 2) (all parts)))
      (raise (SyntaxError (.format "{}: {} の綴りは <置き場>:<名>(`:` がちょうど 1 つ・両側が空でない): {}" where key (hy.repr item))))))
  (sorted (sfor item form (str item))))


(defn static-value [form]  ; defk にできない: macro の展開の時に呼ぶ関数
  "literal の form → 静的な記述(__doeff_system__)に載せる Python の値。"
  (cond
    (isinstance form String) (str form)
    (isinstance form Integer) (int form)
    (isinstance form Float) (float form)
    True (hy.repr form)))


(defn read-job [row #^ list params #^ str system]  ; defk にできない: macro の展開の時に呼ぶ関数
  "job の行 1 つを検め、#(名 Program の form 鍵 → 値の form の dict 静的な記述) を返す。"
  (when (not (and (isinstance row Expression) (>= (len row) 2) (isinstance (get row 0) Symbol)))
    (raise (SyntaxError (.format "defsystem {}: job の行は (名 (関数 引数…) :鍵 値 …): {}" system (hy.repr row)))))
  (setv name (str (get row 0))
        where (.format "defsystem {} の job {}" system name)
        program (get row 1)
        options (list (cut row 2 None)))
  (when (not (and (isinstance program Expression) (>= (len program) 1) (isinstance (get program 0) Symbol)))
    (raise (SyntaxError (.format "{}: Program は (関数の記号 引数…) の呼び出しで書く: {}" where (hy.repr program)))))
  (for [arg (cut program 1 None)]
    (when (not (or (and (isinstance arg Symbol) (in (str arg) params)) (literal? arg)))
      (raise (SyntaxError (.format "{}: Program の引数は系の引数 {} か literal: {}" where (or params "(無し)") (hy.repr arg))))))
  (when (% (len options) 2)
    (raise (SyntaxError (.format "{}: :鍵 値 の組が揃っていない: {}" where (hy.repr row)))))
  (setv values {} static {"name" name "function" (str (get program 0))})
  (for [#(k v) (zip (cut options None None 2) (cut options 1 None 2))]
    (setv key (str k))
    (when (not (and (isinstance k Keyword) (in key JOB-KEYS)))
      (raise (SyntaxError (.format "{}: 鍵 {} は受けない — 受ける鍵は {}" where (hy.repr k) (.join " " JOB-KEYS)))))
    ;; 2 回目の鍵は静的な記述で見る(どの鍵も記述に載り、:reads / :writes は job の値に載らない)。
    (when (in (cut key 1 None) static)
      (raise (SyntaxError (.format "{}: 鍵 {} が 2 回ある" where key))))
    (match key
      (| ":reads" ":writes")
        (setv (get static (cut key 1 None)) (record-names v where key))
      ":needs"
        (do (setv names (needs-names v where))
            (setv (get values key) `(frozenset [~@(lfor n names (String n))])
                  (get static "needs") names))
      ":replicas"
        (do (when (not (and (isinstance v Integer) (in (int v) REPLICAS-VALUES)))
              (raise (SyntaxError (.format "{}: :replicas は {} のどれかの整数(0 = 取り下げ・1 = 動かす): {}"
                                           where (.join " / " (gfor r REPLICAS-VALUES (str r))) (hy.repr v)))))
            (setv (get values key) v (get static "replicas") (int v)))
      ":update"
        (do (when (not (and (isinstance v String) (in (str v) UPDATE-FORMS)))
              (raise (SyntaxError (.format "{}: :update は {} のどれか: {}" where (.join " / " UPDATE-FORMS) (hy.repr v)))))
            (setv (get values key) v (get static "update") (str v)))
      ":readiness"
        (do (setv pairs (string-dict v where key #(Integer Float) "数"))
            (setv (get values key) v (get static "readiness") (dfor #(a b) pairs (str a) (static-value b))))
      _
        (do (setv pairs (string-dict v where key String "文字列"))
            (setv (get values key) v
                  (get static (cut key 1 None)) (dfor #(a b) pairs (str a) (str b))))))
  (when (not-in ":replicas" values)
    (raise (SyntaxError (.format "{}: :replicas が無い — job の望む台数(0 = 取り下げ・1 = 動かす)を必ず書く" where))))
  #(name program values static))


(defn call-shape-form [program]  ; defk にできない: macro の展開の時に呼ぶ関数
  "Program の呼び出し (関数 引数… :鍵 値 …) → 実行時に CallShape(関数・位置の引数・名の引数の値)を作る form。
   defk の呼び出しの結果からは引数を読めないので、宣言の表示(describe)と土台の置き方の検めのために形を残す。"
  (setv positional [] named [] rest (list (cut program 1 None)))
  (while rest
    (setv head (.pop rest 0))
    (if (and (isinstance head Keyword) rest)
        (.extend named [(String (hy.mangle (cut (str head) 1 None))) (.pop rest 0)])
        (.append positional head)))
  ;; CallShape は defrecord(構成子は名の引数だけを受ける)。
  `(doeff_cluster.shared.intent.service_model.CallShape :function ~(get program 0) :args [~@positional] :kwargs {~@named}))


(defn param-parts [param #^ str system]  ; defk にできない: macro の展開の時に呼ぶ関数
  "系の引数 1 つ → #(名の記号 型の記号か None)。引数は素の記号か、型の注記つき #^ T foundation(読み取り器の (annotate foundation T))。
   型は module の最上位の型の名(記号・点つきの記号)だけを受ける — 静的な記述に型の module:qualname を残し、汎用の模擬の検が型から
   模擬の土台を引くため。"
  (cond
    (isinstance param Symbol) #(param None)
    (and (isinstance param Expression) (= (len param) 3) (= (str (get param 0)) "annotate") (isinstance (get param 1) Symbol))
      (do (when (not (isinstance (get param 2) Symbol))
            (raise (SyntaxError (.format "defsystem {}: 引数 {} の型は module の最上位の型の名(記号): {}"
                                         system (get param 1) (hy.repr (get param 2))))))
          #((get param 1) (get param 2)))
    True (raise (SyntaxError (.format "defsystem {}: 引数は記号か #^ 型 記号([foundation] か [#^ T foundation] の形): {}"
                                      system (hy.repr param))))))


(defn defsystem-form [name params body]  ; defk にできない: macro の展開の時に呼ぶ関数
  "defsystem の展開: 土台を受けて doeff_cluster.shared.entry.service_build.system-of を呼ぶ関数と、静的な記述 __doeff_system__・
   __doeff_tags__(役 entry)を置く form を作るため。引数に型の注記が在れば、記述の param_types(名 → 型の module:qualname)に残す。"
  (setv system (str name))
  (when (not (isinstance params List))
    (raise (SyntaxError (.format "defsystem {}: 引数は list([foundation] か [#^ T foundation] の形): {}" system (hy.repr params)))))
  (setv parts (lfor p params (param-parts p system))
        param-names (lfor #(p _) parts (str p))
        typed (lfor #(p t) parts :if (is-not t None) #((str p) t))
        rows (list body)
        doc None)
  (when (and rows (isinstance (get rows 0) String))
    (setv doc (get rows 0) rows (cut rows 1 None)))
  (when (not rows)
    (raise (SyntaxError (.format "defsystem {}: job の行が 1 つも無い" system))))
  (setv jobs (lfor row rows (read-job row param-names system))
        names (lfor j jobs (get j 0)))
  (for [n names]
    (when (> (.count names n) 1)
      (raise (SyntaxError (.format "defsystem {}: job の名 {} が 2 回ある" system n)))))
  (setv job-forms
        (lfor #(job-name program values _) jobs
              `(doeff_cluster.shared.entry.service_build.job
                 ~(String job-name) ~program
                 :call ~(call-shape-form program)
                 ~@(sum (lfor #(k v) (.items values) [(Keyword (hy.mangle (cut k 1 None))) v]) []))))
  (setv static {"name" system "params" param-names "jobs" (lfor j jobs (get j 3))})
  ;; 静的な記述は setattr 1 回で置く(param_types も同じ式の中で足す)。置いた後に関数の属性 __doeff_system__ を読み直す文を
  ;; 展開に出さない — 関数の型は欄を持たず、型検査の展開で reportFunctionMemberAccess になり書き手に直せない(agora-redesign #2291)。
  ;; setattr の記帳は doeff-hy-check が型検査の展開から外す(static_check._bookkeeping_statement)。
  (setv description
        (if typed
            `(| ~(hy.models.as-model static)
                {"param_types" {~@(sum (lfor #(n t) typed [(String n) `(+ (. ~t __module__) ":" (. ~t __qualname__))]) [])}})
            (hy.models.as-model static)))
  ;; 系の関数の答えの型は System(service_build.system-of の答え)— 注記を展開に書く。型検査が系の値を System と読み、
  ;; 書き手に直せない「公開の関数に答えの型が無い」の所見を出さないため(agora-redesign #3366 — 道具がこの展開に委ねる)。
  `(do
     (import doeff_cluster.shared.intent.service_model)
     (import doeff_cluster.shared.entry.service_build)
     (import doeff_hy.declarations)
     (defn #^ doeff_cluster.shared.intent.service_model.System ~name [~@params]
       ~@(if (is doc None) [] [doc])
       (doeff_cluster.shared.entry.service_build.system-of ~(String system) #(~@job-forms)))
     (setattr ~name "__doeff_system__" ~description)
     (setattr ~name "__doeff_tags__" (doeff_hy.declarations.DefinitionTags :context ~(String system) :role "entry"))))
