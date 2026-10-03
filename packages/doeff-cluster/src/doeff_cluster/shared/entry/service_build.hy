;;; 系(System)の宣言の値を作る構成子と、coordinator へ渡す宣言の行の組み立て(ADR-DOE-CLUSTER-001 の段 3 — #2540 で intent から分けた)。
;;;
;;; defsystem の展開が呼ぶのは job と system-of(この module)。宣言の行は system-declaration が作る(規則は
;;; doeff_cluster.shared.intent.service_model の頭の註)。CLI の入口(declare)は resolve・resolve-value で引数の `module:attr` を解く。
;;; 型は doeff_cluster.shared.intent.service_model、identity と検めの判断は doeff_cluster.shared.core.service_rules。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import collections.abc [Callable])
(import importlib)
(import doeff [DoExpr Program run])
(import doeff_cluster.shared.core.capabilities [capabilities-of])
(import doeff_cluster.shared.core.service_rules [identity-of describe-identity environ-overlay-refusal])
(import doeff_cluster.shared.core.readiness_rules [readiness-refusal])
(import doeff_cluster.shared.protocol.program_codec [encode-program])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvVar])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.intent.service_model [UPDATE-FORMS CallShape Job System Declaration])


(deff job [#^ str name program * #^ CallShape call needs #^ (| dict None) [readiness None] #^ str [update "recreate"]  ; defk にできない: macro の展開 — defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
           #^ (| dict None) [environ None]]
  {:pre [(: name str) (: program (| Program int str list dict None)) (: call CallShape) (: needs (| frozenset set list tuple None)) (: readiness (| dict None)) (: update str) (: environ (| dict None))]
   :post [(: % Job)] :tags {:context "doeff-cluster" :role "main" :reads "env"}}
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
  {:pre [(: name str) (: jobs tuple)] :post [(: % System)] :tags {:context "doeff-cluster" :role "main"}}
  "job の組を系にする(名の重なりは断る)。"
  (setv names (lfor j jobs j.name))
  (for [n names]
    (when (> (.count names n) 1)
      (raise (ValueError (.format "系 {} に job {} が 2 つある" name n)))))
  (System :name name :jobs jobs))


(deff system-declaration [#^ System system #^ str revision #^ (| RuntimeEnv None) [runtime-env None] #^ (| dict None) [environ None] * #^ dict versions]  ; defk にできない: declare の CLI が呼ぶ
  {:pre [(: system System) (: revision str) (: versions dict) (: runtime-env (| RuntimeEnv None)) (: environ (| dict None))] :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "系 → coordinator へ渡す宣言(改訂 1 の A・F・G)。job ごとに Program を詰めて sha を鍵に programs へ、行は sha と identity・
   versions・describe・environ を持つ。versions = 送り手の版の識別(キーワード専用の必須 — 呼び手が io の層の process_versions.process-versions で綴って渡す —
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
  {:pre [(: path str)] :post [(: % (| Callable System))] :tags {:context "doeff-cluster" :role "main"}}
  "`module:attr` の import path を値に解くため(名を解く口はここ 1 つ — 契約の dynamic_imports で名指す・#1692)。答えは系の関数・
   土台の関数か、旧い形の系の値(呼び手の declare が形を見て、理由つきで断る)。"
  (when (not-in ":" path)
    (raise (ValueError (+ "関数の参照は module:attr の形で書く: " path))))
  (setv #(module attr) (.split path ":" 1))
  (getattr (importlib.import-module module) attr))


(deff resolve [#^ str path]  ; defk にできない: CLI の入口(declare)が引数の文字列を解く
  {:pre [(: path str)] :post [(: % Callable)] :tags {:context "doeff-cluster" :role "main"}}
  "`module:attr` の import path を関数に解く(declare の系の関数と土台の関数)。旧い形の系の値(System)は関数ではないので、
   名指しで断る(declare はその前に理由つきで断るので、ここへ来るのは呼び手の誤り)。"
  (setv value (resolve-value path))
  (when (isinstance value System)
    (raise (TypeError (+ "関数ではなく旧い形の系の値を指している: " path))))
  value)
