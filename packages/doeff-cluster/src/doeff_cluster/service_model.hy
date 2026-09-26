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
;;;                              :env "myapp.envs:board_env" :requires {"kind" "k3s"} :config {"interval" 1.0}))
;;;   (setv lab (System "lab" #(turn-placer)))
(require doeff-hy.macros [defk <-])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import importlib)
(import inspect)
(import json)
(import hy)
(import doeff [Program])
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
  (setv #^ (| dict None) readiness None)    ; {"windowSeconds": n}(ReportReady で Ready を報告する service だけ)
  (setv #^ str update "recreate")          ; 入れ替えの形 recreate | handoff(worker_model.JobSpec の handoff)
  (setv #^ (| dict None) base-from None))  ; 土台の commit を追う Deployment(base_follow_policy)


(defclass [(dataclass :frozen True)] System []
  "系を構成する service の組。"
  (#^ str name)
  (#^ tuple services))


(setv UPDATE-FORMS #("recreate" "handoff"))

;; run.config のうち本体の引数ではなく、実行先の組み立て側が読む欄(record = effect の記録係の設定・job_entry の recording-layer)。
;; 本体へは渡さない — 1 process の main・実行先・再生の 3 つとも、program-arguments を通して同じ引数を作る。
(setv RECORD-KEY "record")
(setv ASSEMBLY-KEYS #(RECORD-KEY))


(defn #^ dict program-arguments [#^ dict config]  ; defk にできない: 宣言の時点の検め(check-program-arguments)も使う
  "run.config から本体の keyword 引数を作る唯一の場所。組み立て側の欄(ASSEMBLY-KEYS)を外し、JSON の鍵(kebab / snake)を
   Hy の引数名へ mangle する。1 process の main(service-program)・実行先(job_entry service)・再生(replay_main)が使う。"
  (dfor #(k v) (.items config) :if (not-in k ASSEMBLY-KEYS) (hy.mangle k) v))


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


(defn #^ None check-program-arguments [#^ str name #^ Callable program #^ dict config]  ; defk にできない: 宣言の値は module の読み込みの時に組む
  "宣言の設定が本体の引数と合うかを宣言の時点で検める(食い違いは実行先で走らせた時に初めて落ち、coordinator が起こし直し続ける)。
   設定の鍵(組み立て側の欄を除く)は本体の引数にある・既定値の無い引数は設定にある・本体は組み立て側の欄の名を引数に取らない。"
  (setv parameters (.values (. (inspect.signature program) parameters))
        reserved (sfor k ASSEMBLY-KEYS (hy.mangle k))
        given (program-arguments config))
  (setv takes-any (any (gfor p parameters (= p.kind inspect.Parameter.VAR_KEYWORD)))
        named (lfor p parameters
                    :if (in p.kind #(inspect.Parameter.POSITIONAL_OR_KEYWORD inspect.Parameter.KEYWORD_ONLY))
                    p))
  (setv problems
        (+ (lfor p named :if (in p.name reserved)
                 (.format "引数 {} は組み立て側の欄の名で、どの実行の道でも本体へ渡らない" p.name))
           (if takes-any
               []
               (lfor k (sorted given) :if (not-in k (sfor p named p.name))
                     (.format "設定の鍵 {} は本体の引数に無い" k)))
           (lfor p named :if (and (is p.default inspect.Parameter.empty) (not-in p.name given) (not-in p.name reserved))
                 (.format "引数 {} が設定に無い" p.name))))
  (when problems
    (raise (TypeError (.format "service {} の設定が本体 {} の引数と合わない: {}" name program.__qualname__ (.join "・" problems))))))


(defn #^ ServiceDef service [#^ str name #^ Callable program *  ; defk にできない: 宣言の値は module の読み込みの時に組む
                             #^ str env
                             #^ (| dict None) [requires None]
                             #^ (| dict None) [config None]
                             #^ (| dict None) [readiness None]
                             #^ str [update "recreate"]
                             #^ (| dict None) [base-from None]]
  "名前付きの常駐 job(service)を 1 つ宣言する。
   program = Program を作る module の最上位の関数(設定の鍵を引数に受ける — 食い違いはここで TypeError)。env = 実行先で組む handler の組を返す関数の import path。
   requires = 置き場の条件・config = 設定(どちらも文字列の鍵の dict)。
   readiness {\"windowSeconds\" n} = 本体が ReportReady で報告する「準備できた」が直近 n 秒以内にある時だけ Ready(Rollout が見る)。
   update \"handoff\" = 版や設定が変わった時、新の process を旧と並べて起こし、新が Ready と数えられてから旧を止める(名前付きの lease で
   書きを 1 つに絞り、lease を待つ間も待機の拍で Ready を報告する service だけが使う)。既定は \"recreate\"(旧を止めてから新)。
   base-from {\"kind\" \"Deployment\" \"namespace\" … \"name\" … \"container\" …} = 業務コードの版(土台の commit)をその Deployment の
   image の版へ追わせる(coordinator の base_follow_policy)。"
  (when (not-in update UPDATE-FORMS)
    (raise (ValueError (.format "service {} の :update は {} のどれか: {!r}" name UPDATE-FORMS update))))
  (setv reference (program-reference program)
        settings (string-keyed name "config" (or config {})))
  (check-program-arguments name program settings)
  (ServiceDef name
              reference
              env
              (tuple (sorted (.items (string-keyed name "requires" (or requires {})))))
              (tuple (sorted (.items settings)))
              program
              (if (is readiness None) None (string-keyed name "readiness" readiness))
              update
              (if (is base-from None) None (string-keyed name "base-from" base-from))))


(defn resolve [#^ str path]
  "`module:attr` の import path を object に解く。"
  (when (not-in ":" path)
    (raise (ValueError (+ "関数の参照は module:attr の形で書く: " path))))
  (setv #(module attr) (.split path ":" 1))
  (getattr (importlib.import-module module) attr))


(defn #^ dict config-of [#^ ServiceDef service [overrides None]]
  "宣言の設定に上書き(テストで周期を有限にする等)を重ねた dict。"
  (| (dict service.config) (or overrides {})))


(defn #^ Program service-program [#^ ServiceDef service [overrides None]]
  "設定を引数として Program を作る(program-arguments — 実行先と同じ引数)。"
  (setv factory (or service.program-factory (resolve service.factory)))
  (factory #** (program-arguments (config-of service overrides))))


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


(defn #^ list system-declaration [#^ System system #^ str revision [overrides None]]
  "(b) coordinator に渡す宣言。job の名前 = service の名前。config は JSON の object。"
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
       (if service.base-from {"baseFrom" (dict service.base-from)} {}))))
