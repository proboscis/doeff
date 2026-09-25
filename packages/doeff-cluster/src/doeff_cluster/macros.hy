;;; service の宣言の macro。業務の側は (require doeff-cluster.macros [defservice defsystem]) で使う。
;;;
;;;   (defservice turn-placer {:env "myapp.envs:board_env"
;;;                            :requires {:kind "k3s"}
;;;                            :config {:interval 1.0}}
;;;     [interval]
;;;     {:pre [(: interval float)] :post [(: % int)]}
;;;     (<- ...) ...)
;;;
;;;   名前付きの常駐 job を 1 つ宣言する。本体は defk と同じ書き味(contract の map があれば defk、無ければ @do の関数)。
;;;   展開すると、Program を作る関数 `<name>-program` と、宣言の行 `<name>`(ServiceDef)ができ、service の表に登録される。
;;;   宣言の durable な形は「関数の参照(import path)+ commit」で、cloudpickle の blob は宣言に置かない。
;;;
;;;   (defsystem lab [turn-placer turn-runner])
;;;
;;;   系を構成する service の組を名前で束ねる。そこから (a) テスト用の単一の main の Program(service_model.system-main)と
;;;   (b) coordinator に渡す宣言(service_model.system-declaration)を導く。
;;;
;;; 書き方は doeff-hy の macros.hy(defk / defhandler)に合わせる: 検査は macro の展開時に SyntaxError で名指しする。

(import hy)
(import hy.models)


(defn _pairs [d]
  "hy.models.Dict(鍵と値が交互の列)を (鍵 値) の組の列にする。"
  (list (zip (cut d None None 2) (cut d 1 None 2))))


(defn _string-key [k]
  "keyword の鍵は文字列にする(:kind → \"kind\")。文字列の鍵はそのまま。"
  (cond
    (isinstance k hy.models.Keyword) (hy.models.String (. k name))
    (isinstance k hy.models.String) k
    True (raise (SyntaxError (+ "defservice: 鍵は keyword か文字列で書く: " (repr k))))))


(defn _string-keyed [d]
  "keyword の鍵の Dict を、実行時に文字列の鍵の dict になる形へ写す(入れ子は写さない)。"
  (when (not (isinstance d hy.models.Dict))
    (raise (SyntaxError (+ "defservice: :requires と :config は {…} で書く: " (repr d)))))
  (hy.models.Dict (lfor pair (_pairs d) item [(_string-key (get pair 0)) (get pair 1)] item)))


(defmacro defservice [name meta params #* body]
  "名前付きの常駐 job(service)を 1 つ宣言する。meta = {:env \"import path\" :requires {…} :config {…} :readiness {…}?
   :update \"handoff\"? :base-from {…}?}。
   :readiness {\"windowSeconds\" n} = 本体が ReportReady で報告する「準備できた」が直近 n 秒以内にある時だけ Ready(Rollout が見る)。
   :update \"handoff\" = 版や設定が変わった時、新の process を旧と並べて起こし、新が Ready と数えられてから旧を止める(名前付きの lease で
   書きを 1 つに絞り、lease を待つ間も待機の拍で Ready を報告する service だけが使う)。既定は \"recreate\"(旧を止めてから新)。
   :base-from {\"kind\" \"Deployment\" \"namespace\" … \"name\" … \"container\" …} = 業務コードの版(土台の commit)をその Deployment の
   image の版へ追わせる(coordinator の base_follow_policy)。"
  (when (not (isinstance meta hy.models.Dict))
    (raise (SyntaxError (.format "defservice {}: 2 つ目は {{:env … :requires … :config …}} の map" name))))
  (setv opts (dfor pair (_pairs meta) (. (get pair 0) name) (get pair 1)))
  (for [key opts]
    (when (not-in key ["env" "requires" "config" "readiness" "update" "base-from"])
      (raise (SyntaxError (.format "defservice {}: 知らない項目 :{}(使えるのは :env :requires :config :readiness :update :base-from)"
                                   name key)))))
  (setv env (.get opts "env"))
  (when (is env None)
    (raise (SyntaxError (.format "defservice {}: :env(実行先で組む handler の組の import path)が要る" name))))
  (setv requires (_string-keyed (.get opts "requires" (hy.models.Dict [])))
        config (_string-keyed (.get opts "config" (hy.models.Dict [])))
        readiness (if (in "readiness" opts) (_string-keyed (get opts "readiness")) None)
        update (.get opts "update" "recreate")
        base-from (if (in "base-from" opts) (_string-keyed (get opts "base-from")) None)
        program-name (hy.models.Symbol (+ (str name) "-program"))
        has-contracts (and body (isinstance (get body 0) hy.models.Dict)))
  (setv fn-form
    (if has-contracts
        `(defk ~program-name ~params ~@body)
        `(defn [_doeff_do] ~program-name ~params ~@body)))
  `(do
     (import doeff.do [do :as _doeff_do])
     (require doeff-hy.macros [defk <-])
     (import doeff_cluster.service_model [ServiceDef register-service])
     ~fn-form
     (setv ~name
       (register-service
         (ServiceDef ~(str name)
                     (+ __name__ ":" ~(hy.mangle (str program-name)))
                     ~env
                     (tuple (sorted (.items ~requires)))
                     (tuple (sorted (.items ~config)))
                     ~program-name
                     ~readiness
                     ~update
                     ~base-from)))))


(defmacro defsystem [name services]
  "系を構成する service の組を名前で束ねる。services = [service の宣言の名 …]。"
  (when (not (isinstance services hy.models.List))
    (raise (SyntaxError (.format "defsystem {}: service の組は [a b c] で書く" name))))
  `(do
     (import doeff_cluster.service_model [System])
     (setv ~name (System ~(str name) (tuple [~@services])))))
