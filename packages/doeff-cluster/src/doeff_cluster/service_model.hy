;;; 系(System)の宣言の値と、coordinator へ渡す宣言の行(ADR-DOE-CLUSTER-001 の段 3)。
;;;
;;; job(常駐の service)は Program の値 1 つ(defk の関数を呼んだ結果 — R1)と、置き場所と入れ替えの約束(needs・readiness・update・
;;; environ)を持つ。handler は Program の中(defk の本体の with-handlers)で作り、宣言には入れない(R3b)。系は doeff-hy の defsystem で
;;; 書き、土台(handler の組を返す module の最上位の関数)を引数に受けて System の値を返す:
;;;
;;;   (defsystem lab [foundation]
;;;     "見本の系"
;;;     (tally (tally-program foundation 2) :needs #{"net"} :environ {"TALLY_BASE" "1"}))
;;;
;;; defsystem の展開が呼ぶのは job と system-of(この module)。宣言の行は system-declaration が作る:
;;;   - Program は encode-program で詰め、中身の sha256 を鍵に置き場(coordinator の /programs/<sha>)へ別に送る。行は sha だけを持つ
;;;     (改訂 1 の F)。
;;;   - 行の identity = 呼んだ関数の module:qualname・引数の正規 JSON(関数は {"ref": "module:qualname"})。spec-hash は identity・
;;;     revision・versions・environ から作り、詰めた文字列は比べない(改訂 1 の A — cloudpickle の出力は同じ Program でも揺れる)。
;;;   - describe = identity から作る表示の 1 行(coordinator は業務の code を持たず Program を解けないので、表示は宣言が運ぶ)。
;;; 旧い宣言(:env・:config・:env-config・:requires・関数の参照 + 設定)は受け付けない(operator 2026-09-27)。
(require doeff-hy.macros [deff val])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import hashlib)
(import importlib)
(import json)
(import doeff [DoExpr run])
(import .cluster_model [capabilities-of])
(import .readiness_model [readiness-refusal])
(import .remote_model [encode-program current-versions])
(import .runtime_env_model [RuntimeEnv EnvVar runtime-env->json])

(val UPDATE-FORMS #("recreate" "handoff"))


(defrecord CallShape
  "Program を作った呼び出しの形(defsystem の展開が残す — defk の呼び出しの結果からは引数を読めないため)。
   function = 呼んだ関数・args = 位置の引数の値・kwargs = 名の引数の値(Hy の名 → 値)。identity と describe の材料。"
  (#^ object function)
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
  (when (is-not (getattr (importlib.import-module module) qualname None) function)
    (raise (TypeError (.format "{}: {}:{} を import しても同じ関数に届かない" where module qualname))))
  (+ module ":" qualname))


(deff canonical-argument [value #^ str where]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: value (| Callable str int float bool list dict None)) (: where str)] :post [(: % (| dict str int float bool list None))]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "Program の引数 1 つ → identity に載せる正規の値。関数は {\"ref\": \"module:qualname\"}、JSON にできる値はそのまま(dict は鍵を整列)。
   それ以外(object・handler の値)は断る — identity が引数の値を表せないと、宣言し直すたびの入れ替えの要否を決められない。"
  (cond
    (callable value) {"ref" (function-reference value where)}
    (isinstance value dict) (dfor k (sorted value) k (canonical-argument (get value k) where))
    (isinstance value list) (lfor v value (canonical-argument v where))
    True value))


(deff identity-of [#^ CallShape call #^ str where]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: call CallShape) (: where str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "judgment"}}
  "呼び出しの形 → identity(関数の参照・位置の引数・名の引数の正規 JSON)。spec-hash の材料(改訂 1 の A)。"
  {"function" (function-reference call.function where)
   "args" (lfor a call.args (canonical-argument a where))
   "kwargs" (dfor k (sorted call.kwargs) k (canonical-argument (get call.kwargs k) where))})


(deff describe-identity [#^ dict identity]  ; defk にできない: 宣言の値を作る時に呼ぶ純粋な判断
  {:pre [(: identity dict)] :post [(: % str)] :tags {:context "doeff-cluster" :role "judgment"}}
  "identity → 表示の 1 行 `module:qualname(引数, 名=値)`(関数の引数は参照の名で)。"
  (defn show [v] (if (and (isinstance v dict) (= (list v) ["ref"])) (get v "ref") (json.dumps v :ensure-ascii False :sort-keys True)))
  (.format "{}({})" (get identity "function")
           (.join ", " (+ (lfor a (get identity "args") (show a))
                          (lfor #(k v) (.items (get identity "kwargs")) (.format "{}={}" k (show v)))))))


(deff job [#^ str name program * #^ CallShape call needs #^ (| dict None) [readiness None] #^ str [update "recreate"]
           #^ (| dict None) [environ None]]  ; defk にできない: defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
  {:pre [(: name str) (: program (| DoExpr int str list dict None)) (: call CallShape) (: needs (| frozenset set list tuple None)) (: update str)]
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
  (setv env-vars (tuple (gfor k (sorted (or environ {})) (EnvVar :name k :value (get environ k)))))
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


(defrecord Declaration
  "system-declaration の答え: rows = coordinator へ渡す宣言の行(Service ごと)・programs = 行が参照する詰めた Program(sha → 文字列)。
   declare は programs を /programs へ置いてから rows を書く。"
  (#^ list rows)
  (#^ dict programs))


(deff system-declaration [#^ System system #^ str revision #^ (| RuntimeEnv None) [runtime-env None]]  ; defk にできない: declare の CLI が呼ぶ
  {:pre [(: system System) (: revision str)] :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "entry"}}
  "系 → coordinator へ渡す宣言(改訂 1 の A・F・G)。job ごとに Program を詰めて sha を鍵に programs へ、行は sha と identity・
   versions・describe・environ を持つ。runtime-env の env-vars と :environ で同じ名が在れば断る(子の環境変数の足し口を 1 つにする)。"
  (setv versions (current-versions)
        env-json (if (is runtime-env None) None (run (runtime-env->json runtime-env)))
        declared-vars (if (is runtime-env None) #() (sfor v runtime-env.env-vars v.name))
        rows [] programs {})
  (for [j system.jobs]
    (setv clash (sorted (gfor v j.environ :if (in v.name declared-vars) v.name)))
    (when clash
      (raise (ValueError (.format "job {} の :environ {} は実行環境の env-vars と同じ名 — 子の環境変数はどちらか 1 つで宣言する"
                                  j.name clash))))
    (setv blob (encode-program j.program)
          sha (.hexdigest (hashlib.sha256 (.encode blob "ascii")))
          identity (identity-of j.call (.format "job {}" j.name)))
    (setv (get programs sha) blob)
    (.append rows
             (| {"name" j.name
                 "revision" revision
                 "needs" (sorted j.needs)
                 "run" {"kind" "service" "program" sha "identity" identity "versions" versions
                        "describe" (describe-identity identity)}
                 "environ" (dfor v j.environ v.name v.value)}
                (if j.readiness {"readiness" (dict j.readiness)} {})
                (if (= j.update "recreate") {} {"update" j.update})
                (if (is env-json None) {} {"runtimeEnv" env-json}))))
  (Declaration :rows rows :programs programs))


(deff resolve [#^ str path]  ; defk にできない: CLI の入口(declare)が引数の文字列を解く
  {:pre [(: path str)] :post [(: % Callable)] :tags {:context "doeff-cluster" :role "entry"}}
  "`module:attr` の import path を関数に解く(declare の系の関数と土台の関数)。"
  (when (not-in ":" path)
    (raise (ValueError (+ "関数の参照は module:attr の形で書く: " path))))
  (setv #(module attr) (.split path ":" 1))
  (getattr (importlib.import-module module) attr))
