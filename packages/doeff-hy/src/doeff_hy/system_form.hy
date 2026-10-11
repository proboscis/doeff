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
;;;   系の頭(説明の文の後・job の行の前)に `:task-limits {"<組>" {"scope" "worker" "limit" 6} …}` を置ける(2026-10-12・card
;;;   ki-b0e8028c8c8d)— 系が宣言する組の同時数の上限(doeff-cluster の GroupLimit)。組の名は「/」を含まない文字列の literal、scope は
;;;   "worker" か "cluster"、limit は 1 以上の整数の literal、"perPart" True は組を名乗る task が区分(part)ごとに数える組。task は組の名
;;;   だけを名乗り、値はここが持つ。静的な記述の task_limits に載る。
;;; 値の意味(readiness の窓の形・environ の名の衝突など)は doeff-cluster の service_build.system-of が呼ばれた時に検める。
(import hy)
(import itertools [takewhile])
(import hy.models [Dict Expression Float Integer Keyword List Set String Symbol])
(import doeff-hy.declarations [needs-names])
;; 展開が import する doeff-cluster の module の綴り(型検査のための展開の掃除が重ねを畳む綴りと 1 つの定義元 — static_view.py)。
(import doeff-hy.static-view [DEFSYSTEM-MODEL-MODULE DEFSYSTEM-BUILD-MODULE DEFSYSTEM-IMPORTS])

(setv JOB-KEYS #(":needs" ":replicas" ":readiness" ":update" ":environ" ":reads" ":writes"))
;; 系の頭(説明の文の後・job の行の前)に置ける鍵。:task-limits = 系が宣言する組の同時数の上限(2026-10-12・card ki-b0e8028c8c8d)。
(setv SYSTEM-KEYS #(":task-limits"))
;; 組の上限 1 つの欄(doeff-cluster の資源 GroupLimit の spec と同じ綴り)と、範囲の名(doeff-cluster の TASK-LIMIT-SCOPES と同じ)。
(setv LIMIT-KEYS #("scope" "limit" "perPart"))
(setv LIMIT-SCOPES #("worker" "cluster"))
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


(defn read-task-limit [group spec #^ str where]  ; defk にできない: macro の展開の時に呼ぶ関数
  "`:task-limits` の組の上限 1 つ(組の名の form と上限の dict の form)を展開の時に検め、#(名 範囲 上限 区分ごとか) を返すため。"
  (setv name (str group))
  (when (or (not name) (in "/" name))
    (raise (SyntaxError (.format "{}: 組の名は「/」を含まない空でない文字列: {}" where (hy.repr group)))))
  (setv fields (dfor #(k v) (string-dict spec (+ where " の組 " name) "上限" #(String Integer Symbol) "文字列・整数・真偽")
                     (str k) v)
        unknown (sorted (gfor k fields :if (not-in k LIMIT-KEYS) k))
        scope (.get fields "scope")
        limit (.get fields "limit")
        per-part (.get fields "perPart" (Symbol "False")))
  (when unknown
    (raise (SyntaxError (.format "{} の組 {}: 受けない欄 {} — 受ける欄は {}" where name unknown (.join " " LIMIT-KEYS)))))
  (when (not (and (isinstance scope String) (in (str scope) LIMIT-SCOPES)))
    (raise (SyntaxError (.format "{} の組 {}: \"scope\" は {} のどれか: {}" where name (.join " / " LIMIT-SCOPES) (hy.repr scope)))))
  (when (not (and (isinstance limit Integer) (>= (int limit) 1)))
    (raise (SyntaxError (.format "{} の組 {}: \"limit\" は 1 以上の整数の literal: {}" where name (hy.repr limit)))))
  (when (not (and (isinstance per-part Symbol) (in (str per-part) #("True" "False"))))
    (raise (SyntaxError (.format "{} の組 {}: \"perPart\" は True か False: {}" where name (hy.repr per-part)))))
  #(name (str scope) (int limit) (= (str per-part) "True")))


(defn read-task-limits [form #^ str system]  ; defk にできない: macro の展開の時に呼ぶ関数
  "系の頭の `:task-limits` の値の form を展開の時に検め、#(組の上限の #(名 範囲 上限 区分ごとか) の list 静的な記述の dict) を返すため —
   linter が実行せずに読めるように、形は組の名(「/」を含まない空でない文字列の literal)→ {\"scope\" 範囲 \"limit\" 1 以上の整数
   \"perPart\" True|False?} の dict の literal だけ。同じ規則で doeff-cluster の service_build.system-of がもう 1 度検める。"
  (setv where (.format "defsystem {} の :task-limits" system)
        groups (lfor #(group spec) (string-dict form where ":task-limits" Dict "組の上限の dict") (read-task-limit group spec where))
        names (lfor g groups (get g 0))
        twice (sorted (sfor n names :if (> (.count names n) 1) n)))
  (when twice
    (raise (SyntaxError (.format "{}: 組 {} が 2 回ある" where twice))))
  #(groups (dfor #(name scope limit per-part) groups name {"scope" scope "limit" limit "perPart" per-part})))


(defn head-options [rows #^ str system]  ; defk にできない: macro の展開の時に呼ぶ関数
  "系の本体の form の列(説明の文を除いた物)から、頭の `:鍵 値` の組(job の行の前 — SYSTEM-KEYS)を読み、#(鍵の綴り → 値の form の
   dict 残りの job の行) を返すため。知らない鍵・値の無い鍵・2 回目の鍵は展開の時の SyntaxError。"
  (setv count (len (list (takewhile (fn [i] (isinstance (get rows i) Keyword)) (range 0 (len rows) 2))))
        keys (lfor i (range count) (str (get rows (* 2 i)))))
  (for [k keys]
    (when (not-in k SYSTEM-KEYS)
      (raise (SyntaxError (.format "defsystem {}: 系の頭の鍵 {} は受けない — 受ける鍵は {}" system k (.join " " SYSTEM-KEYS)))))
    (when (> (.count keys k) 1)
      (raise (SyntaxError (.format "defsystem {}: 系の頭の鍵 {} が 2 回ある" system k)))))
  (when (< (len rows) (* 2 count))
    (raise (SyntaxError (.format "defsystem {}: 系の頭の鍵 {} に値が無い" system (get keys -1)))))
  #((dfor i (range count) (get keys i) (get rows (+ (* 2 i) 1))) (list (cut rows (* 2 count) None))))


(defn dotted [#^ str spelling]  ; defk にできない: macro の展開の時に呼ぶ関数
  "展開が import する module と、そこから引く型と構成子を、1 つの綴りの定義元(static_view.py)から書くため — 点つきの綴りを
   Hy の読み取り器が source から作るのと同じ形 `(. a b c)` にする(点つきの Symbol は作れない)。位置は持たせない — 展開の常として
   defsystem の form の位置が入る(hy.read で読むと読んだ文字列の 1 行目の位置が残り、この式の赤が file の頭を指す)。"
  (Expression [(Symbol ".") #* (gfor part (.split spelling ".") (Symbol part))]))


(defn in-module [#^ str module #^ str name]  ; defk にできない: macro の展開の時に呼ぶ関数
  "module の名を引く式を作るため — `(import a.b.c)` が束縛するのは根の名だけなので、module の綴りを頭に持つ点つきの式で引く。"
  (dotted (+ module "." name)))


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
  `(~(in-module DEFSYSTEM-MODEL-MODULE "CallShape") :function ~(get program 0) :args [~@positional] :kwargs {~@named}))


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
  ;; 系の頭の鍵(説明の文の後・job の行の前 — SYSTEM-KEYS)。今は :task-limits だけ。
  (setv #(options rows) (head-options rows system)
        #(limits limits-static) (if (in ":task-limits" options) (read-task-limits (get options ":task-limits") system) #([] {})))
  (when (not rows)
    (raise (SyntaxError (.format "defsystem {}: job の行が 1 つも無い" system))))
  (setv jobs (lfor row rows (read-job row param-names system))
        names (lfor j jobs (get j 0)))
  (for [n names]
    (when (> (.count names n) 1)
      (raise (SyntaxError (.format "defsystem {}: job の名 {} が 2 回ある" system n)))))
  (setv job-forms
        (lfor #(job-name program values _) jobs
              `(~(in-module DEFSYSTEM-BUILD-MODULE "job")
                 ~(String job-name) ~program
                 :call ~(call-shape-form program)
                 ~@(sum (lfor #(k v) (.items values) [(Keyword (hy.mangle (cut k 1 None))) v]) []))))
  (setv static (| {"name" system "params" param-names "jobs" (lfor j jobs (get j 3))}
                  (if limits-static {"task_limits" limits-static} {})))
  ;; 組の上限は GroupLimit(doeff-cluster の service_model が引き渡す型)の tuple にして system-of へ渡す(無ければ鍵を書かない)。
  (setv limit-forms
        (lfor #(group scope limit per-part) limits
              `(~(in-module DEFSYSTEM-MODEL-MODULE "GroupLimit") :group ~(String group) :scope ~(String scope) :limit ~(Integer limit)
                 :per-part ~(Symbol (str per-part)))))
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
  ;; 系ごとに同じ import を出す — 1 つの module に系を 2 つ書いた時の重ねは、doeff-hy-check が型検査のための展開で 1 つに畳む
  ;; (static_check.without_bookkeeping・畳む綴りは DEFSYSTEM-IMPORTS)。
  `(do
     ~@(lfor module DEFSYSTEM-IMPORTS `(import ~(dotted module)))
     (import doeff_hy.declarations)
     (defn #^ ~(in-module DEFSYSTEM-MODEL-MODULE "System") ~name [~@params]
       ~@(if (is doc None) [] [doc])
       (~(in-module DEFSYSTEM-BUILD-MODULE "system-of") ~(String system) #(~@job-forms)
         ~@(if limit-forms [(Keyword "task-limits") `#(~@limit-forms)] [])))
     (setattr ~name "__doeff_system__" ~description)
     (setattr ~name "__doeff_tags__" (doeff_hy.declarations.DefinitionTags :context ~(String system) :role "entry"))))
