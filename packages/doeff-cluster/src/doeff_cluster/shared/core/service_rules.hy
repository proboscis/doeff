;;; 系(System)の宣言の値を検めて読む判断(service_model.hy の型の上の純粋な判断 — #2540 で intent から分けた)。
;;;
;;; 呼び出しの形(CallShape)→ identity(関数の参照・引数の正規 JSON)と表示の 1 行・系の中の job の引き・土台の :needs の検め・
;;; job ごとの environ の上書きの検め。型(CallShape・Job・System・RecordArgument)は doeff_cluster.shared.intent.service_model、
;;; 値を作る構成子と宣言の行の組み立て(job・system-of・system-declaration・resolve)は doeff_cluster.shared.entry.service_build。
(require doeff-hy.macros [defk deff <- val var])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import collections.abc [Callable])
(import dataclasses)
(import sys)
(import json)
(import doeff_cluster.shared.intent.service_model [CallShape Job System RecordArgument])


(deff function-reference [#^ Callable function #^ str where]  ; defk にできない: 宣言の値を作る時(module の読み込み・declare)に呼ぶ純粋な判断
  {:pre [(: function Callable) (: where str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "module の最上位の関数 → `module:qualname`。入れ子の関数・lambda・handler の値は実行先で import しても引けない(または値として
   詰めることになる)ので断る — 土台を最上位の関数で渡す決まり(計画 6 節)の検め。"
  (setv module (getattr function "__module__" None) qualname (getattr function "__qualname__" None))
  ;; handler の値を先に見る(defhandler の値の qualname は入れ子の関数の形なので、後に見ると「最上位に置く」の理由で断ってしまう)。
  (when (hasattr function "__doeff_handler_data__")
    (raise (TypeError (.format "{}: {}.{} は handler の値 — handler は Program の本体の中で関数を呼んで作る" where module qualname))))
  (when (or (not (isinstance module str)) (not (isinstance qualname str)) (in "." qualname) (in "<" qualname))
    (raise (TypeError (.format "{}: 関数 {!r} は module の最上位に置く(module:qualname で引けない)" where function))))
  (when (is-not (getattr (.get sys.modules module) qualname None) function)
    (raise (TypeError (.format "{}: {}:{} を import しても同じ関数に届かない" where module qualname))))
  (+ module ":" qualname))



(deff canonical-record [value #^ str where]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: value RecordArgument) (: where str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "系の引数の record 1 つ → identity の正規の値 {\"record\": \"module:qualname\", \"fields\": {欄: 正規の値}}。土台を欄の名と型を持つ 1 つの
   値で渡せるようにするため(汎用の模擬のテストが土台の型から模擬の土台を組める)。型は関数と同じく module の最上位に
   在ること(実行先で import して引く)・凍った record であること(宣言の後に欄が変わると identity が値を表さない)を検める。欄は名で整列し、
   値は canonical-argument と同じ規則で正規化する — 同じ record は同じ綴りになる。"
  (setv kind (type value))
  (when (not (. (getattr kind "__dataclass_params__") frozen))
    (raise (TypeError (.format "{}: record {}.{} は凍っていない — 系の引数の record は frozen(defrecord)にする"
                               where kind.__module__ kind.__qualname__))))
  {"record" (function-reference kind where)
   "fields" (dfor name (sorted (gfor field (dataclasses.fields value) field.name))
                  name (canonical-argument (getattr value name) where))})


(deff canonical-argument [value #^ str where]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: value (| Callable str int float bool list tuple dict RecordArgument None)) (: where str)]
   :post [(: % (| dict str int float bool list None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "Program の引数 1 つ → identity に載せる正規の値。関数は {\"ref\": \"module:qualname\"}、record は canonical-record、JSON にできる値は
   そのまま(dict は鍵を整列・tuple は list)。それ以外(object・handler の値)は断る — identity が引数の値を表せないと、宣言し直すたびの
   入れ替えの要否を決められない。"
  (cond
    (isinstance value RecordArgument) (canonical-record value where)
    (callable value) {"ref" (function-reference value where)}
    (isinstance value dict) (dfor k (sorted value) k (canonical-argument (get value k) where))
    (isinstance value #(list tuple)) (lfor v value (canonical-argument v where))
    True value))


(deff identity-of [#^ CallShape call #^ str where]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: call CallShape) (: where str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "呼び出しの形 → identity(関数の参照・位置の引数・名の引数の正規 JSON)。spec-hash の材料(改訂 1 の A)。"
  {"function" (function-reference call.function where)
   "args" (lfor a call.args (canonical-argument a where))
   "kwargs" (dfor k (sorted call.kwargs) k (canonical-argument (get call.kwargs k) where))})


(deff describe-identity [#^ dict identity]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: identity dict)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "identity → 表示の 1 行 `module:qualname(引数, 名=値)`(関数の引数は参照の名で・record は `module:型(欄=値, …)`)。"
  (defn #^ str show [#^ object v]
    (match v
      {"ref" ref} :if (and (isinstance v dict) (= (len v) 1)) ref
      {"record" kind "fields" fields} :if (and (isinstance v dict) (= (len v) 2) (isinstance fields dict))
      (.format "{}({})" kind (.join ", " (lfor #(k f) (.items fields) (.format "{}={}" k (show f)))))
      _ (json.dumps v :ensure-ascii False :sort-keys True)))
  (.format "{}({})" (get identity "function")
           (.join ", " (+ (lfor a (get identity "args") (show a))
                          (lfor #(k v) (.items (get identity "kwargs")) (.format "{}={}" k (show v)))))))



(deff job-named [#^ System system #^ str name]  ; defk にできない: 宣言の道具と sim の宿が呼ぶ純粋な読み
  {:pre [(: system System) (: name str)] :post [(: % (| Job None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "系の中の名 name の job(無ければ None)。"
  (next (gfor j system.jobs :if (= j.name name) j) None))


(defk foundation-needs-refusal [system foundation]
  {:pre [(: system System) (: foundation (| Callable RecordArgument))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "土台の関数の頭の :needs(__doeff_needs__)が系の各 job の :needs の一部かを検め、外れた job と足りない能力を並べた理由の文を
   返すため(外れが無ければ None)。foundation = 系に渡した土台 — 土台の関数か、土台の関数を欄に持つ record(系の引数の型の値 — 型から
   本番の土台を引く宣言の道具が渡す・#3030。record は欄の中の関数を全部見る)。declare が宣言の前に断る(計画 9 節の P・ADR-DOE-CLUSTER-001 R4b。doeff-linter の照合が来るまでは
   宣言の時点で)。土台が :needs を名乗らなければ検めない。土台が中に並べる handler の :needs は集めない(集めるには handler を作る =
   実行が要る)— 土台の頭に手で書く決まりで、頭が中の handler の :needs を漏らしていても linter の照合までは見つからない。"
  ;; 見るのは foundation と、各 job の呼び出しの形(CallShape)の引数に在る関数の全部 — 系が土台を 2 つ以上受ける時(家族ごとの口の
  ;; 違う土台)も、宣言の道具が土台ごとに検めを手で写さずに済む(構成のレビュー 2026-09-28 の D)。
  (var short [])
  (<- own list (callables-in [foundation]))
  (for [j system.jobs]
    (<- found list (callables-in [#* j.call.args #* (.values j.call.kwargs)]))
    (val carried (lfor f (+ own found) :if (is-not (getattr f "__doeff_needs__" None) None) f))
    (for [f carried]
      ;; 欄は宣言の道具が関数に付ける印なので getattr で読む(関数の型は欄を持たない — #1690)
      (val missing (- (frozenset (getattr f "__doeff_needs__")) j.needs))
      (when missing
        (.append short (.format "{} の土台 {}:{}(足りない {})" j.name (getattr f "__module__" "?") (getattr f "__qualname__" "?")
                                (sorted missing))))))
  (match short
    [] None
    _ (.format "土台の :needs が job の :needs に含まれない: {} — 土台の要る能力を job の :needs に書く(土台の :needs ⊆ job の :needs)"
               (.join "・" (sorted (set short))))))


(defk callables-in [values]
  {:pre [(: values list)] :post [(: % list)] :tags {:context "doeff-cluster" :role "judgment"}}
  "呼び出しの引数の値の中の関数(list と dict と土台の record の欄の中も)を並べるため — 土台の :needs の検めが、系の引数に渡した土台の
   全部を見る(土台を record 1 つで渡す系でも、欄の土台を見落とさない)。"
  (var found [])
  (for [v values]
    (match v
      (list) (do (<- inner list (callables-in v)) (:= found (+ found inner)))
      (tuple) (do (<- inner list (callables-in (list v))) (:= found (+ found inner)))
      (dict) (do (<- inner list (callables-in (list (.values v)))) (:= found (+ found inner)))
      (RecordArgument) (do (<- inner list (callables-in (lfor field (dataclasses.fields v) (getattr v field.name))))
                           (:= found (+ found inner)))
      _ (when (callable v) (:= found (+ found [v])))))
  found)


(deff environ-overlay-refusal [#^ System system #^ dict environ]  ; defk にできない: 宣言の値を作る時(declare の CLI・sim-cluster の宣言)に呼ぶ純粋な判断
  {:pre [(: system System) (: environ dict)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "job ごとの environ の上書き(job 名 → {名: 文字列})を検め、断る理由の文を返す(無ければ None)。系に無い job・宣言の :environ に
   無い名・文字列でない値は断る — 上書きは宣言に書いた名の値だけを変え、黙って名を足さない(本番の declare と sim-cluster が同じ規則)。"
  (setv declared (dfor j system.jobs j.name (sfor v j.environ v.name)))
  (for [#(name given) (.items environ)]
    (when (not-in name declared)
      (return (.format "environ の上書きの job {!r} は系 {} に無い(在るのは {})" name system.name (sorted declared))))
    (when (not (isinstance given dict))
      (return (.format "environ の上書き {!r} は名 → 文字列の dict: {!r}" name given)))
    (setv unknown (sorted (gfor k given :if (not-in k (get declared name)) k)))
    (when unknown
      (return (.format "job {} の environ の上書き {} は宣言の :environ に無い名 — 宣言に書いた名だけを上書きする" name unknown)))
    (setv wrong (sorted (gfor #(k v) (.items given) :if (not (isinstance v str)) k)))
    (when wrong
      (return (.format "job {} の environ の上書き {} の値は文字列(本番の環境変数と同じ型)" name wrong))))
  None)
