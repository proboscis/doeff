;;; 系(System)の宣言の値と、coordinator へ渡す宣言の行(ADR-DOE-CLUSTER-001 の段 3)。
;;;
;;; job(常駐の service)は Program の値 1 つ(defk の関数を呼んだ結果 — R1)と、置き場所と入れ替えの約束(needs・readiness・update・
;;; environ)を持つ。handler は Program の中(defk の本体の with-handlers)で作り、宣言には入れない(R3b)。系は doeff-hy の defsystem で
;;; 書き、土台を引数に受けて System の値を返す。土台 = 本体の Program を受け、自分の handler(と scheduler・時計)の下で走らせて答えを
;;; 返す module の最上位の defk(計画 10.1 — job は自分で scheduled を包まない。本番の土台は scheduler を含み、sim の土台は含まない):
;;;
;;;   (defk production-foundation [body]
;;;     (<- answer (scheduled (with-handlers [(state) (environ-reader) host-reader (sync-time-handler) …] body)))
;;;     answer)
;;;   (defk tally-program [foundation step]
;;;     (<- total (foundation (tally-body step)))
;;;     total)
;;;   (defsystem lab [foundation]
;;;     "見本の系"
;;;     (tally (tally-program foundation 2) :needs #{"net"} :environ {"TALLY_BASE" "1"}))
;;;
;;; 手元で系を回すのは local.sim-cluster(sim の土台で作った同じ系の値を、本物の coordinator と worker の上で走らせる)。
;;;
;;; defsystem の展開が呼ぶのは job と system-of(この module)。宣言の行は system-declaration が作る:
;;;   - Program は encode-program で詰め、中身の sha256 を鍵に置き場(coordinator の /programs/<sha>)へ別に送る。行は sha だけを持つ
;;;     (改訂 1 の F)。
;;;   - 行の identity = 呼んだ関数の module:qualname・引数の正規 JSON(関数は {"ref": "module:qualname"})。spec-hash は identity・
;;;     revision・versions・environ から作り、詰めた文字列は比べない(改訂 1 の A — cloudpickle の出力は同じ Program でも揺れる)。
;;;   - describe = identity から作る表示の 1 行(coordinator は業務の code を持たず Program を解けないので、表示は宣言が運ぶ)。
;;; 旧い宣言(:env・:config・:env-config・:requires・関数の参照 + 設定)は受け付けない(operator 2026-09-27)。
(require doeff-hy.macros [defk deff <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import dataclasses)
(import typing [ClassVar Protocol runtime-checkable])
(import importlib)
(import sys)
(import json)
(import doeff [DoExpr Program run])
(import doeff_cluster.shared.core.capabilities [capabilities-of])
(import .readiness_model [readiness-refusal])
(import .remote_model [encode-program program-sha])
(import .runtime_env_model [RuntimeEnv EnvVar runtime-env->json])

(val UPDATE-FORMS #("recreate" "handoff"))


(defrecord CallShape
  "Program を作った呼び出しの形(defsystem の展開が残す — defk の呼び出しの結果からは引数を読めないため)。
   function = 呼んだ関数・args = 位置の引数の値・kwargs = 名の引数の値(Hy の名 → 値)。identity と describe の材料。"
  (#^ Callable function)
  (#^ list args)
  (#^ dict kwargs))


(defrecord Job
  "系の job 1 つ(常駐の service)。program = Program の値(R1)・call = それを作った呼び出しの形・needs = 要る能力の名(空でない
   frozenset — R4b)・readiness = {\"windowSeconds\" n …} か None・update = recreate | handoff・environ = 子の環境変数(EnvVar の
   tuple — 名の順)。"
  (#^ str name)
  (#^ object program)
  (#^ CallShape call)
  (#^ frozenset needs)
  (#^ (| dict None) readiness)
  (#^ str update)
  (#^ tuple environ))


(defrecord System
  "系 = job の組(defsystem の関数が返す値)。name = 系の名・jobs = Job の tuple(名は重ならない)。"
  (#^ str name)
  (#^ tuple jobs))


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


(defclass [runtime-checkable] RecordArgument [Protocol]
  "系の引数に渡せる record(defrecord・dataclass の値)の印 — 欄の宣言 __dataclass_fields__ を持つ値。"
  (setv #^ (get ClassVar dict) __dataclass_fields__ {}))


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


(deff job [#^ str name program * #^ CallShape call needs #^ (| dict None) [readiness None] #^ str [update "recreate"]
           #^ (| dict None) [environ None]]  ; defk にできない: defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
  {:pre [(: name str) (: program (| Program int str list dict None)) (: call CallShape) (: needs (| frozenset set list tuple None)) (: readiness (| dict None)) (: update str) (: environ (| dict None))]
   :post [(: % Job)] :tags {:context "doeff-cluster" :role "entry"}}
  "job 1 つを検めて作る(defsystem の展開が呼ぶ)。旧い引数(:env・:config・:env-config・:requires)はこの関数に無いので TypeError。"
  (when (not (isinstance program DoExpr))
    (raise (TypeError (.format "job {}: Program の値(defk の関数を呼んだ結果)を渡す: {!r}" name program))))
  (when (not-in update UPDATE-FORMS)
    (raise (ValueError (.format "job {} の :update は {} のどれか: {!r}" name UPDATE-FORMS update))))
  (setv refusal (readiness-refusal readiness update))
  (when (is-not refusal None)
    (raise (ValueError (.format "job {} の :readiness: {}" name refusal))))
  (setv caps (capabilities-of (if (is needs None) [] needs) (.format "job {} の :needs" name)))
  (when (not caps)
    (raise (ValueError (.format "job {} の :needs が空 — 要る能力の名を 1 つ以上書く(ADR-DOE-CLUSTER-001 R4b)" name))))
  (when (and (is-not environ None) (not (isinstance environ dict)))
    (raise (TypeError (.format "job {} の :environ は文字列の鍵と値の dict: {!r}" name environ))))
  (setv environ-given (if (is environ None) {} environ))
  (setv env-vars (tuple (gfor k (sorted environ-given) (EnvVar :name k :value (get environ-given k)))))
  (identity-of call (.format "job {}" name))
  (Job :name name :program program :call call :needs (frozenset caps) :readiness readiness :update update :environ env-vars))


(deff system-of [#^ str name #^ tuple jobs]  ; defk にできない: defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
  {:pre [(: name str) (: jobs tuple)] :post [(: % System)] :tags {:context "doeff-cluster" :role "entry"}}
  "job の組を系にする(名の重なりは断る)。"
  (setv names (lfor j jobs j.name))
  (for [n names]
    (when (> (.count names n) 1)
      (raise (ValueError (.format "系 {} に job {} が 2 つある" name n)))))
  (System :name name :jobs jobs))


(deff job-named [#^ System system #^ str name]  ; defk にできない: 宣言の道具と sim の宿が呼ぶ純粋な読み
  {:pre [(: system System) (: name str)] :post [(: % (| Job None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "系の中の名 name の job(無ければ None)。"
  (next (gfor j system.jobs :if (= j.name name) j) None))


(defk foundation-needs-refusal [system foundation]
  {:pre [(: system System) (: foundation Callable)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "judgment"}}
  "土台の関数の頭の :needs(__doeff_needs__)が系の各 job の :needs の一部かを検め、外れた job と足りない能力を並べた理由の文を
   返すため(外れが無ければ None)— declare が宣言の前に断る(計画 9 節の P・ADR-DOE-CLUSTER-001 R4b。doeff-linter の照合が来るまでは
   宣言の時点で)。土台が :needs を名乗らなければ検めない。土台が中に並べる handler の :needs は集めない(集めるには handler を作る =
   実行が要る)— 土台の頭に手で書く決まりで、頭が中の handler の :needs を漏らしていても linter の照合までは見つからない。"
  ;; 見るのは foundation と、各 job の呼び出しの形(CallShape)の引数に在る関数の全部 — 系が土台を 2 つ以上受ける時(家族ごとの口の
  ;; 違う土台)も、宣言の道具が土台ごとに検めを手で写さずに済む(構成のレビュー 2026-09-28 の D)。
  (var short [])
  (for [j system.jobs]
    (<- found list (callables-in [#* j.call.args #* (.values j.call.kwargs)]))
    (val carried (lfor f (+ [foundation] found) :if (is-not (getattr f "__doeff_needs__" None) None) f))
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
  "呼び出しの引数の値の中の関数(list と dict の中も)を並べるため — 土台の :needs の検めが、系の引数に渡した土台の全部を見る。"
  (var found [])
  (for [v values]
    (match v
      (list) (do (<- inner list (callables-in v)) (:= found (+ found inner)))
      (tuple) (do (<- inner list (callables-in (list v))) (:= found (+ found inner)))
      (dict) (do (<- inner list (callables-in (list (.values v)))) (:= found (+ found inner)))
      _ (when (callable v) (:= found (+ found [v])))))
  found)


(defrecord Declaration
  "system-declaration の答え: rows = coordinator へ渡す宣言の行(Service ごと)・programs = 行が参照する詰めた Program(sha → 文字列)。
   declare は programs を /programs へ置いてから rows を書く。"
  (#^ list rows)
  (#^ dict programs))


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


(deff system-declaration [#^ System system #^ str revision #^ (| RuntimeEnv None) [runtime-env None] #^ (| dict None) [environ None] * #^ dict versions]  ; defk にできない: declare の CLI が呼ぶ
  {:pre [(: system System) (: revision str) (: versions dict) (: runtime-env (| RuntimeEnv None)) (: environ (| dict None))] :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "entry"}}
  "系 → coordinator へ渡す宣言(改訂 1 の A・F・G)。job ごとに Program を詰めて sha を鍵に programs へ、行は sha と identity・
   versions・describe・environ を持つ。versions = 送り手の版の識別(キーワード専用の必須 — 呼び手が io の層の process_versions.current-versions で読んで渡す —
   宣言の組み立ては process を読まない・#1630)。runtime-env の env-vars と :environ で同じ名が在れば断る(子の環境変数の足し口を 1 つにする)。
   environ = job ごとの environ の上書き(配る先ごとの値 — 口の URL・下限の刻など。宣言の :environ に重ね、規則は environ-overlay-refusal。
   spec-hash に入るので、上書きを変えると入れ替わる)。"
  (setv overlay (or environ {})
        refusal (environ-overlay-refusal system overlay))
  (when (is-not refusal None)
    (raise (ValueError refusal)))
  (setv env-json (if (is runtime-env None) None (run (runtime-env->json runtime-env)))
        declared-vars (if (is runtime-env None) #() (sfor v runtime-env.env-vars v.name))
        rows [] programs {})
  (for [j system.jobs]
    (setv clash (sorted (gfor v j.environ :if (in v.name declared-vars) v.name)))
    (when clash
      (raise (ValueError (.format "job {} の :environ {} は実行環境の env-vars と同じ名 — 子の環境変数はどちらか 1 つで宣言する"
                                  j.name clash))))
    (setv blob (encode-program j.program)
          sha (program-sha blob)
          identity (identity-of j.call (.format "job {}" j.name)))
    (setv (get programs sha) blob)
    (.append rows
             (| {"name" j.name
                 "revision" revision
                 "needs" (sorted j.needs)
                 "run" {"kind" "service" "program" sha "identity" identity "versions" versions
                        "describe" (describe-identity identity)}
                 "environ" (| (dfor v j.environ v.name v.value) (.get overlay j.name {}))}
                (if j.readiness {"readiness" (dict j.readiness)} {})
                (if (= j.update "recreate") {} {"update" j.update})
                (if (is env-json None) {} {"runtimeEnv" env-json}))))
  (Declaration :rows rows :programs programs))


(deff resolve-value [#^ str path]  ; defk にできない: CLI の入口(declare)が引数の文字列を解く
  {:pre [(: path str)] :post [(: % (| Callable System))] :tags {:context "doeff-cluster" :role "entry"}}
  "`module:attr` の import path を値に解くため(名を解く口はここ 1 つ — 契約の dynamic_imports で名指す・#1692)。答えは系の関数・
   土台の関数か、旧い形の系の値(呼び手の declare が形を見て、理由つきで断る)。"
  (when (not-in ":" path)
    (raise (ValueError (+ "関数の参照は module:attr の形で書く: " path))))
  (setv #(module attr) (.split path ":" 1))
  (getattr (importlib.import-module module) attr))


(deff resolve [#^ str path]  ; defk にできない: CLI の入口(declare)が引数の文字列を解く
  {:pre [(: path str)] :post [(: % Callable)] :tags {:context "doeff-cluster" :role "entry"}}
  "`module:attr` の import path を関数に解く(declare の系の関数と土台の関数)。旧い形の系の値(System)は関数ではないので、
   名指しで断る(declare はその前に理由つきで断るので、ここへ来るのは呼び手の誤り)。"
  (setv value (resolve-value path))
  (when (isinstance value System)
    (raise (TypeError (+ "関数ではなく旧い形の系の値を指している: " path))))
  value)
