;;; service(名前付きで個別に管理する常駐 job)の宣言の型と、宣言の組(System)から導く 2 つの形。
;;;
;;;   (a) system-main        = 全 service の Program を Spawn して Gather する 1 つの Program(テスト用の main。handler は差し替える)
;;;   (b) system-declaration = coordinator に渡す宣言(service ごとに 名前・関数の参照・env・requires・commit・設定)
;;;
;;; service どうしは戻り値でやり取りしない。共有の状態を読み書きする effect を通してだけつながる。
;;; 宣言の durable な形は「関数の参照(module:attr)+ commit」。実行先はその commit のコードを準備してから参照を解く。
;;;
;;; 宣言は値と関数で書く(macro は置かない — ADR-DOE-HY-005 R5):
;;;
;;;   (defk turn-placer-program [interval]
;;;     {:pre [(: interval float)] :post [(: % int)]}
;;;     …)
;;;   (setv turn-placer (service "turn-placer" turn-placer-program
;;;                              :env "myapp.envs:board_env" :requires {"kind" "k3s"} :config {"interval" 1.0}
;;;                              :env-config {"token-file" "/etc/myapp/token"}))
;;;   (setv lab (System "lab" #(turn-placer)))
;;;
;;; 設定は持ち主で 2 つに分けて書く: :config = 本体の引数(鍵と引数が 1 対 1)・:env-config = env だけが読む設定(置き場・資格の file・
;;; lease の時間等 — 本体の公開の契約に入れない)。coordinator へは 2 つを重ねた平たい run.config が渡り、実行先は本体へ本体の引数の名の
;;; 設定だけを、env へは全体を渡す。
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import importlib)
(import inspect)
(import json)
(import hy)
(import doeff [Program run])
(import .runtime_env_model [RuntimeEnv runtime-env->json])
(import .readiness_model [readiness-refusal])
(import doeff_core_effects.scheduler [Spawn Gather Task])


(defclass [(dataclass :frozen True)] ServiceDef []
  "service 1 本の宣言。requires と config は (鍵 値) の組を鍵の順に並べた tuple(比較と JSON 化のため)。
   program-factory は宣言した module の中で持つ関数そのもの(テストと同じ process の main が直接使う)。"
  (#^ str name)
  (#^ str factory)
  (#^ str env)
  (#^ tuple requires)
  (#^ tuple config)
  (setv #^ object program-factory None)
  (setv #^ (| dict None) readiness None)    ; {"windowSeconds": n "handoffTimeoutSeconds": m?}(ReportReady で Ready を報告する service だけ)
  (setv #^ str update "recreate")          ; 入れ替えの形 recreate | handoff(worker_model.JobSpec の handoff)
  (setv #^ (| dict None) base-from None)   ; 土台の commit を追う Deployment(base_follow_policy)
  (setv #^ tuple env-config #()))           ; env だけが読む設定(鍵の順の (鍵 値) の組)。本体の引数にしない


(defclass [(dataclass :frozen True)] System []
  "系を構成する service の組。"
  (#^ str name)
  (#^ tuple services))


(setv UPDATE-FORMS #("recreate" "handoff"))

;; run.config のうち本体の引数ではなく、実行先の組み立て側が読む欄(record = effect の記録係の設定・job_entry の recording-layer)。
;; 本体へは渡さない — 1 process の main・実行先・再生の 3 つとも、program-arguments を通して同じ引数を作る。
(setv RECORD-KEY "record")
(setv ASSEMBLY-KEYS #(RECORD-KEY))


(defn #^ (| frozenset None) argument-names [#^ Callable program]  ; defk にできない: 宣言の値は module の読み込みの時に組む
  "本体が名で受ける引数の名(Hy の引数名 = mangle 済み)。**kwargs を受ける本体は None(どの鍵も受ける)。"
  (setv parameters (list (.values (. (inspect.signature program) parameters))))
  (if (any (gfor p parameters (= p.kind inspect.Parameter.VAR_KEYWORD)))
      None
      (frozenset (gfor p parameters
                       :if (in p.kind #(inspect.Parameter.POSITIONAL_OR_KEYWORD inspect.Parameter.KEYWORD_ONLY))
                       p.name))))


(defn #^ dict program-arguments [#^ Callable program #^ dict config]  ; defk にできない: 宣言の時点の検め(check-program-arguments)も使う
  "run.config から本体の keyword 引数を作る唯一の場所。本体の引数の名の設定だけを渡し(env だけが読む設定と組み立て側の欄
   ASSEMBLY-KEYS は渡さない)、JSON の鍵(kebab / snake)を Hy の引数名へ mangle する。1 process の main(service-program)・
   実行先(job_entry service)・再生(replay_main)が使う。"
  (setv names (argument-names program))
  (dfor #(k v) (.items config)
        :if (and (not-in k ASSEMBLY-KEYS) (or (is names None) (in (hy.mangle k) names)))
        (hy.mangle k) v))


(defn #^ list settings-left-to-env [#^ Callable program #^ dict config]  ; defk にできない: 実行先の入口(job_entry)が呼ぶ
  "本体へ渡さない設定の鍵(組み立て側の欄を除く — env だけが読む設定)。実行先が起動の時に印字する(手で書き換えた設定の綴りの
   違いを見つける手がかり — 宣言した設定は宣言の時点で検めてある)。"
  (setv given (program-arguments program config))
  (sorted (gfor k config :if (and (not-in k ASSEMBLY-KEYS) (not-in (hy.mangle k) given)) k)))


(defn #^ str program-reference [#^ Callable program]  ; defk にできない: 宣言の値は module の読み込みの時に組む
  "Program を作る関数の参照 `module:attr`。module の最上位の名でない関数(入れ子の関数・lambda)は実行先で import しても
   引けないので、宣言の時点で断る。"
  (setv qualname program.__qualname__)
  (when (or (in "." qualname) (in "<" qualname))
    (raise (ValueError (+ "service の Program を作る関数は module の最上位に置く(module:attr で引けない): "
                          program.__module__ "." qualname))))
  (+ program.__module__ ":" qualname))


(defn #^ dict string-keyed [#^ str name #^ str option #^ dict mapping]  ; defk にできない: 宣言の値は module の読み込みの時に組む
  "鍵が全部文字列の dict だけを通す(宣言は JSON で coordinator へ渡る)。"
  (for [key mapping]
    (when (not (isinstance key str))
      (raise (TypeError (.format "service {} の :{} の鍵は文字列で書く: {!r}" name option key)))))
  mapping)


(defn #^ None check-program-arguments [#^ str name #^ Callable program #^ dict config #^ dict env-config]  ; defk にできない: 宣言の値は module の読み込みの時に組む
  "宣言の設定の持ち主が本体の引数と合うかを宣言の時点で検める(食い違いは実行先で走らせた時に初めて落ち、coordinator が起こし直し
   続ける)。:config の鍵(組み立て側の欄を除く)は本体の引数にある・既定値の無い引数は :config にある・:env-config の鍵は本体の
   引数の名でも :config の鍵でも組み立て側の欄でもない・本体は組み立て側の欄の名を引数に取らない。"
  (setv names (argument-names program)
        reserved (sfor k ASSEMBLY-KEYS (hy.mangle k))
        given (sfor k config :if (not-in k ASSEMBLY-KEYS) (hy.mangle k))
        required (lfor p (.values (. (inspect.signature program) parameters))
                       :if (and (in p.kind #(inspect.Parameter.POSITIONAL_OR_KEYWORD inspect.Parameter.KEYWORD_ONLY))
                                (is p.default inspect.Parameter.empty))
                       p.name))
  (setv problems
        (+ (lfor n (sorted (or names #())) :if (in n reserved)
                 (.format "引数 {} は組み立て側の欄の名で、どの実行の道でも本体へ渡らない" n))
           (if (is names None)
               []
               (lfor k (sorted config) :if (and (not-in k ASSEMBLY-KEYS) (not-in (hy.mangle k) names))
                     (.format "設定の鍵 {} は本体の引数に無い(env だけが読む設定なら :env-config に書く)" k)))
           (lfor n required :if (and (not-in n given) (not-in n reserved))
                 (.format "引数 {} が :config に無い" n))
           (lfor k (sorted env-config)
                 :if (or (in k ASSEMBLY-KEYS) (in k config) (and (is-not names None) (in (hy.mangle k) names)))
                 (cond (in k ASSEMBLY-KEYS) (.format "env の設定 {} は組み立て側の欄の名" k)
                       (in k config) (.format "鍵 {} を :config と :env-config の両方に書いている" k)
                       True (.format "env の設定 {} は本体の引数の名(本体の設定なら :config に書く)" k)))))
  (when problems
    (raise (TypeError (.format "service {} の設定が本体 {} の引数と合わない: {}" name program.__qualname__ (.join "・" problems))))))


(defn #^ ServiceDef service [#^ str name #^ Callable program *  ; defk にできない: 宣言の値は module の読み込みの時に組む
                             #^ str env
                             #^ (| dict None) [requires None]
                             #^ (| dict None) [config None]
                             #^ (| dict None) [env-config None]
                             #^ (| dict None) [readiness None]
                             #^ str [update "recreate"]
                             #^ (| dict None) [base-from None]]
  "名前付きの常駐 job(service)を 1 つ宣言する。
   program = Program を作る module の最上位の関数(設定の鍵を引数に受ける — 食い違いはここで TypeError)。env = 実行先で組む handler の組を返す関数の import path。
   requires = 置き場の条件・config = 本体の引数の設定・env-config = env だけが読む設定(どれも文字列の鍵の dict)。
   readiness {\"windowSeconds\" n} = 本体が ReportReady で報告する「準備できた」が直近 n 秒以内にある時だけ Ready(Rollout が見る)。
   update \"handoff\" = 版や設定が変わった時、新の process を旧と並べて起こし、新が Ready と数えられてから旧を止める(名前付きの lease で
   書きを 1 つに絞り、lease を待つ間も待機の拍で Ready を報告する service だけが使う)。既定は \"recreate\"(旧を止めてから新)。
   readiness の handoffTimeoutSeconds(handoff の service だけ・既定 300)= 新が Ready になるまで待つ上限。越えたら coordinator が
   入れ替えを諦める(新を止めて旧を残し、Service の status に理由を出す — 宣言が変わるまで)。
   base-from {\"kind\" \"Deployment\" \"namespace\" … \"name\" … \"container\" …} = 業務コードの版(土台の commit)をその Deployment の
   image の版へ追わせる(coordinator の base_follow_policy)。"
  (when (not-in update UPDATE-FORMS)
    (raise (ValueError (.format "service {} の :update は {} のどれか: {!r}" name UPDATE-FORMS update))))
  (setv refusal (readiness-refusal readiness update))
  (when (is-not refusal None)
    (raise (ValueError (.format "service {} の :readiness: {}" name refusal))))
  (setv reference (program-reference program)
        settings (string-keyed name "config" (or config {}))
        env-settings (string-keyed name "env-config" (or env-config {})))
  (check-program-arguments name program settings env-settings)
  (ServiceDef name
              reference
              env
              (tuple (sorted (.items (string-keyed name "requires" (or requires {})))))
              (tuple (sorted (.items settings)))
              program
              (if (is readiness None) None (string-keyed name "readiness" readiness))
              update
              (if (is base-from None) None (string-keyed name "base-from" base-from))
              :env-config (tuple (sorted (.items env-settings)))))


(defn resolve [#^ str path]
  "`module:attr` の import path を object に解く。"
  (when (not-in ":" path)
    (raise (ValueError (+ "関数の参照は module:attr の形で書く: " path))))
  (setv #(module attr) (.split path ":" 1))
  (getattr (importlib.import-module module) attr))


(defn #^ dict config-of [#^ ServiceDef service [overrides None]]
  "coordinator へ渡る平たい設定: 本体の引数の設定と env だけが読む設定に、上書き(テストで周期を有限にする等)を重ねた dict。
   上書きは宣言した鍵と組み立て側の欄だけを変える — 宣言に無い鍵は TypeError(綴りの違う鍵を黙って足さない)。"
  (setv declared (| (dict service.config) (dict service.env-config))
        unknown (sorted (gfor k (or overrides {}) :if (and (not-in k declared) (not-in k ASSEMBLY-KEYS)) k)))
  (when unknown
    (raise (TypeError (.format "service {} の上書きに宣言に無い設定の鍵がある: {}" service.name (.join "・" unknown)))))
  (| declared (or overrides {})))


(defn #^ Program service-program [#^ ServiceDef service [overrides None]]
  "設定を引数として Program を作る(program-arguments — 実行先と同じ引数)。"
  (setv factory (or service.program-factory (resolve service.factory)))
  (factory #** (program-arguments factory (config-of service overrides))))


(defn #^ (| ServiceDef None) service-named [#^ System system #^ str name]
  (for [service system.services]
    (when (= service.name name) (return service)))
  None)


(defk system-main-program [system overrides]
  {:pre [(: system System) (: overrides dict)] :post [(: % list)]}
  ;; (a) テスト用の main: 全 service を Spawn して Gather する。結果 = 各 service の Program の戻り値の list。
  ;; overrides = {service 名: {設定の鍵: 値}}。
  (setv tasks [])
  (for [service system.services]
    (<- task Task (Spawn (service-program service (.get overrides service.name))))
    (.append tasks task))
  (<- results list (Gather #* tasks))
  results)


(defn #^ Program system-main [#^ System system [overrides None]]
  "system-main-program の入口(上書きは省ける)。defk は既定値の引数を持てないので、省略の解決をここに置く。"
  (system-main-program system (or overrides {})))


(defn #^ list system-declaration [#^ System system #^ str revision [overrides None] #^ (| RuntimeEnv None) [runtime-env None]]
  "(b) coordinator に渡す宣言。job の名前 = service の名前。config は JSON の object。
   runtime-env = 実行環境の宣言(在れば全 service を env の root で起こす — worker は image の venv ではなく宣言の repo の commit と
   uv の lock から準備した root の venv を使う・2026-09-26)。env を持つ宣言は image の版を追う base-from を持てない(commit が 2 つに
   なる — 持つ service があれば ValueError)。"
  (when (is-not runtime-env None)
    (setv following (lfor service system.services :if service.base-from service.name))
    (when following
      (raise (ValueError (.format "runtime-env を持つ宣言は base-from の service を含めない(commit は宣言の repos が決める): {}"
                                  (.join "・" following))))))
  (setv env-json (if (is runtime-env None) None (run (runtime-env->json runtime-env))))
  (lfor service system.services
    (| {"name" service.name
        "revision" revision
        "requires" (dict service.requires)
        "run" {"kind" "service"
               "factory" service.factory
               "env" service.env
               "config" (config-of service (.get (or overrides {}) service.name))}}
       (if service.readiness {"readiness" (dict service.readiness)} {})
       (if (= service.update "recreate") {} {"update" service.update})
       (if service.base-from {"baseFrom" (dict service.base-from)} {})
       (if (is env-json None) {} {"runtimeEnv" env-json}))))
