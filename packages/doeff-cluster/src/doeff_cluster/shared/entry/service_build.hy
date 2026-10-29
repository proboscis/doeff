;;; 系(System)の宣言の値を作る構成子と、coordinator へ渡す宣言の行の組み立て(ADR-DOE-CLUSTER-001 の段 3 — #2540 で intent から分けた)。
;;;
;;; defsystem の展開が呼ぶのは job と system-of(この module)。宣言の行は system-declaration が作る(規則は
;;; doeff_cluster.shared.intent.service_model の頭の註)。CLI の入口(declare)は resolve・resolve-value で引数の `module:attr` を解く。
;;; 型は doeff_cluster.shared.intent.service_model、identity と検めの判断は doeff_cluster.shared.core.service_rules。
(require doeff-hy.macros [deff val])
(val MODULE-TAGS {:context "doeff-cluster" :role "main"})
(import collections.abc [Callable])
(import dataclasses [replace])
(import importlib)
(import doeff [DoExpr Program run])
(import doeff_cluster.shared.core.capabilities [capabilities-of])
(import doeff_cluster.shared.core.service_rules [identity-of describe-identity environ-overlay-refusal])
(import doeff_cluster.shared.core.job_rules [content-refusal])
(import doeff_cluster.shared.core.readiness_rules [readiness-refusal readiness-environ-refusal readiness-from-environ])
(import doeff_cluster.shared.protocol.program_codec [encode-program])
(import doeff_cluster.shared.core.remote_rules [program-sha])
(import doeff_cluster.shared.intent.runtime_env_model [RuntimeEnv EnvVar])
(import doeff_cluster.shared.core.runtime_env_rules [runtime-env->json])
(import doeff_cluster.shared.intent.service_model [UPDATE-FORMS REPLICAS-VALUES CallShape Job System Declaration])
(import doeff_cluster.shared.intent.detached_model [GroupLimit])
(import doeff_cluster.shared.core.detached_rules [group-limit-refusal group-limit-spec])


(deff job [#^ str name program * #^ CallShape call needs #^ int replicas #^ (| dict None) [readiness None] #^ str [update "recreate"]  ; defk にできない: macro の展開 — defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
           #^ (| dict None) [environ None]]
  {:pre [(: name str) (: program (| Program int str list dict None)) (: call CallShape) (: needs (| frozenset set list tuple None)) (: replicas int) (: readiness (| dict None)) (: update str) (: environ (| dict None))]
   :post [(: % Job)] :tags {:context "doeff-cluster" :role "main" :reads "env"}}
  "job 1 つを検めて作る(defsystem の展開が呼ぶ)。replicas = 望む台数(必ず渡す — 0 = 取り下げ・1 = 動かす)。旧い引数(:env・:config・
   :env-config・:requires)はこの関数に無いので TypeError。readiness の欄を :environ の名で書いた時(readiness_model の註)は、宣言の
   :environ の既定の値で読んだ形を検める(job の readiness は名のまま持ち、system-declaration が上書きを重ねた値で読み直す)。"
  (when (not (isinstance program DoExpr))
    (raise (TypeError (.format "job {}: Program の値(defk の関数を呼んだ結果)を渡す: {!r}" name program))))
  (when (not-in replicas REPLICAS-VALUES)
    (raise (ValueError (.format "job {} の :replicas は {} のどれか(0 = 取り下げ・1 = 動かす): {!r}" name REPLICAS-VALUES replicas))))
  (when (not-in update UPDATE-FORMS)
    (raise (ValueError (.format "job {} の :update は {} のどれか: {!r}" name UPDATE-FORMS update))))
  (setv caps (capabilities-of (if (is needs None) [] needs) (.format "job {} の :needs" name)))
  (when (not caps)
    (raise (ValueError (.format "job {} の :needs が空 — 要る能力の名を 1 つ以上書く(ADR-DOE-CLUSTER-001 R4b)" name))))
  (when (and (is-not environ None) (not (isinstance environ dict)))
    (raise (TypeError (.format "job {} の :environ は文字列の鍵と値の dict: {!r}" name environ))))
  (setv environ-given (if (is environ None) {} environ))
  (setv env-vars (tuple (gfor k (sorted environ-given) (EnvVar :name k :value (get environ-given k)))))
  ;; readiness は :environ の値の型を EnvVar が検めた後に読む(名で書いた欄は environ の文字列を数として読む)。
  (setv refusal (or (readiness-environ-refusal readiness environ-given)
                    (readiness-refusal (readiness-from-environ readiness environ-given) update)))
  (when (is-not refusal None)
    (raise (ValueError (.format "job {} の :readiness: {}" name refusal))))
  (identity-of call (.format "job {}" name))
  (Job :name name :program program :call call :needs (frozenset caps) :replicas replicas :readiness readiness :update update
       :environ env-vars))


(deff system-of [#^ str name #^ tuple jobs * #^ tuple [task-limits #()]]  ; defk にできない: defsystem の展開(module の読み込みの時の値)が呼ぶ構成子
  {:pre [(: name str) (: jobs tuple) (: task-limits tuple)] :post [(: % System)] :tags {:context "doeff-cluster" :role "main"}}
  "job の組と組の同時数の上限を系にする(job の名の重なり・組の名の重なり・受けられない上限は断る — 上限の規則は coordinator の
   受け口と同じ group-limit-refusal)。名の無い組の上限には宣言する系の名 name を付ける(GroupLimit の system — coordinator は別の系が
   宣言した組の書き直しを断る)。既に名の付いた上限(作った系の値を繋ぐ時 — 宣言の道具が 1 度に宣言する系を名「a+b」の 1 つの系に
   繋ぐ)は作った系の名を保つ(繋いだ名で付け直すと、同じ系を別の組み合わせで宣言するたびに持ち主が揺れて 409 になる)。"
  (setv names (lfor j jobs j.name))
  (for [n names]
    (when (> (.count names n) 1)
      (raise (ValueError (.format "系 {} に job {} が 2 つある" name n)))))
  (for [l task-limits]
    (when (not (isinstance l GroupLimit))
      (raise (TypeError (.format "系 {} の :task-limits は GroupLimit の tuple: {!r}" name l)))))
  (setv named (tuple (gfor l task-limits (if l.system l (replace l :system name)))))
  (for [l named]
    (setv refusal (group-limit-refusal l.group (group-limit-spec l)))
    (when (is-not refusal None)
      (raise (ValueError (.format "系 {} の :task-limits: {}" name refusal)))))
  (setv groups (lfor l named l.group))
  (for [g groups]
    (when (> (.count groups g) 1)
      (raise (ValueError (.format "系 {} の :task-limits に組 {} が 2 つある" name g)))))
  (System :name name :jobs jobs :task-limits named))


(deff system-declaration [#^ System system #^ str revision #^ (| RuntimeEnv None) [runtime-env None] #^ (| dict None) [environ None] * #^ dict versions
                          #^ (| dict None) [contents None]]  ; defk にできない: declare の CLI が呼ぶ
  {:pre [(: system System) (: revision str) (: versions dict) (: runtime-env (| RuntimeEnv None)) (: environ (| dict None)) (: contents (| dict None))]
   :post [(: % Declaration)] :tags {:context "doeff-cluster" :role "main" :spells "json"}}
  "系 → coordinator へ渡す宣言(改訂 1 の A・F・G)。job ごとに Program を詰めて sha を鍵に programs へ、行は sha と identity・
   versions・describe・environ と、job の望む台数 replicas を持つ(宣言し直しは書く Service の台数を job の値にする — #3487)。versions = 送り手の版の識別(キーワード専用の必須 — 呼び手が io の層の process_versions.process-versions で綴って渡す —
   宣言の組み立ては process を読まない・#1630)。runtime-env の env-vars と :environ で同じ名が在れば断る(子の環境変数の足し口を 1 つにする)。
   environ = job ごとの environ の上書き(配る先ごとの値 — 口の URL・下限の刻など。宣言の :environ に重ね、規則は environ-overlay-refusal。
   spec-hash に入るので、上書きを変えると入れ替わる)。行の readiness は、:environ の名で書いた欄を上書きを重ねた後の値の数に置き換えた形
   (readiness_model の註 — coordinator が受ける readiness はいつも数。読めない値・形の破れは job を名指して断る)。
   contents = job ごとの中身の鍵(job の名 → 鍵の文字列 — card ki-81bf84ac6590)。送り手が「その job の process が読む code の全部(実行環境の
   どの repo の、どの file の中身か)」から計った鍵で、在る job の指紋は版と repo の commit の代わりに鍵を読む — 読む code の変わらない版で
   宣言し直しても、worker は process を起こし直さず root も用意しない。鍵が読む code を覆わないと、変わった code が黙って当たらない
   (覆う範囲は送り手の約束)。None・載らない job = 今どおり版と commit で判じる。系に無い job の名と綴りの誤り(content-refusal)は断る。"
  (setv overlay (or environ {})
        keys (or contents {})
        refusal (environ-overlay-refusal system overlay))
  (when (is-not refusal None)
    (raise (ValueError refusal)))
  (setv strangers (sorted (gfor name keys :if (not (any (gfor j system.jobs (= j.name name)))) name)))
  (when strangers
    (raise (ValueError (.format "contents の job {} は系 {} に無い" strangers system.name))))
  (for [#(name key) (.items keys)]
    (setv problem (content-refusal key))
    (when (is-not problem None)
      (raise (ValueError (.format "job {} の {}" name problem)))))
  (setv env-json (if (is runtime-env None) None (run (runtime-env->json runtime-env)))
        declared-vars (if (is runtime-env None) #() (sfor v runtime-env.env-vars v.name))
        rows [] programs {})
  (for [j system.jobs]
    (setv clash (sorted (gfor v j.environ :if (in v.name declared-vars) v.name)))
    (when clash
      (raise (ValueError (.format "job {} の :environ {} は実行環境の env-vars と同じ名 — 子の環境変数はどちらか 1 つで宣言する"
                                  j.name clash))))
    (setv environ (| (dfor v j.environ v.name v.value) (.get overlay j.name {}))
          readiness-problem (or (readiness-environ-refusal j.readiness environ)
                                (readiness-refusal (readiness-from-environ j.readiness environ) j.update)))
    (when (is-not readiness-problem None)
      (raise (ValueError (.format "job {} の :readiness(environ の上書きを重ねた後): {}" j.name readiness-problem))))
    (setv blob (encode-program j.program)
          sha (program-sha blob)
          identity (identity-of j.call (.format "job {}" j.name)))
    (setv (get programs sha) blob)
    (.append rows
             (| {"name" j.name
                 "revision" revision
                 "needs" (sorted j.needs)
                 "replicas" j.replicas
                 "run" {"kind" "service" "program" sha "identity" identity "versions" versions
                        "describe" (describe-identity identity)}
                 "environ" environ}
                (if j.readiness {"readiness" (readiness-from-environ j.readiness environ)} {})
                (if (= j.update "recreate") {} {"update" j.update})
                (if (in j.name keys) {"content" (get keys j.name)} {})
                (if (is env-json None) {} {"runtimeEnv" env-json}))))
  (Declaration :rows rows :programs programs :limits system.task-limits))


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
