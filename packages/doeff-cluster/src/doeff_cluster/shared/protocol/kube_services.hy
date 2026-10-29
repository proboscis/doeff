;;; job が自分へ届く網の宛先(k8s の Service)を宣言どおりに在らせる effect EnsureKubeService(型は
;;; doeff_cluster.shared.intent.kube_service_model)の本番の答え手 — その job を回す worker の身元(ServiceAccount の token)で、自区画の
;;; API server へ server-side apply を 1 本出す。
;;;
;;;   FIELD-MANAGER        server-side apply の持ち主の名(この答え手が当てた欄を持つ — 他の持ち主が足した欄には触らない)
;;;   service-path         Service 1 つの、API server の上の path
;;;   service-apply-text   宣言 → server-side apply の本文の JSON の字(宣言が持つ欄だけ — 模擬の答え手と検も同じ綴りで照らす)
;;;   applied-service      宣言 1 つを当て、結末(作った・当て直した)を得る。当てられなければ namespace と名・理由を挙げて断る
;;;   kube-services-api    EnsureKubeService の本番の答え手
;;;
;;; 仕組み: ConfigMap・Secret の読み(supplied_values)と同じく、doeff の汎用の effect(token の file の ReadText と HttpRequest)で問う。
;;; 答え手の外側に、宛先が API server の要求を区画の CA で検める接続(doeff_cluster.foundation.in_cluster_api の in-cluster-api-routed)と
;;; file の答え手を並べる(使い手の process の外側 — job の本番の土台)。
;;; 結末は API server の答えの status で読む: 201 = 作った・200 = 在った物に当て直した。ほかの status・届かない・token が読めない時は断る
;;; (断りの文に載せるのは namespace と名・status・API server の断りの本文の先頭まで)。当て直しは force(持ち主の替わる欄も、この宣言の値に
;;; 揃える — 宣言を YAML から移す時に、前の持ち主の欄を引き取る)。
(require doeff-hy.macros [defhandler defk <- val])
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_core_effects [FileFailed])
(import doeff_core_effects.http_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_cluster.shared.intent.kube_service_model [EnsureKubeService KubeService KubeServiceOutcome KubeServiceRefused KubeServiceType])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API REFUSAL-BODY-LIMIT REQUEST-TIMEOUT-SECONDS SERVICE-ACCOUNT-TOKEN-FILE
                                                      kube-headers service-account-text status-field text-field])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})

(val FIELD-MANAGER "doeff-cluster")
;; server-side apply の本文の型(JSON は YAML の部分集合なので JSON の字で送る)。
(val APPLY-CONTENT-TYPE "application/apply-patch+yaml")


(defk service-path [service]
  {:pre [(: service KubeService)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言 service の Service の、API server の上の path を組むため。"
  (+ "/api/v1/namespaces/" (url-quote service.namespace :safe "") "/services/" (url-quote service.name :safe "")))


(defk service-apply-text [service]
  {:pre [(: service KubeService)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "宣言 service を server-side apply の本文の JSON の字へ綴るため(JSON の境界の 1 点 — 欄は k8s の Service と同じ名。宣言が持つ欄だけを書く:
   空の labels・annotations は欄ごと省き、load-balancer-class は書いた時だけ・LOAD-BALANCER は node の口を開けない — 頭の註は
   kube_service_model.hy)。口の protocol は TCP と書く(server-side apply は口の列を port と protocol の組で見分ける)。"
  (val metadata (| {"name" service.name "namespace" service.namespace}
                   (if service.labels {"labels" (dict service.labels)} {})
                   (if service.annotations {"annotations" (dict service.annotations)} {})))
  (val spec (| {"type" service.type.value
                "selector" (dict service.selector)
                "ports" (lfor port service.ports {"name" port.name "port" port.port "targetPort" port.target-port "protocol" "TCP"})}
               (if (is service.load-balancer-class None) {} {"loadBalancerClass" service.load-balancer-class})
               (if (= service.type KubeServiceType.LOAD-BALANCER) {"allocateLoadBalancerNodePorts" False} {})))
  (json.dumps {"apiVersion" "v1" "kind" "Service" "metadata" metadata "spec" spec} :ensure-ascii False :sort-keys True))


(defk refused [service reason]
  {:pre [(: service KubeService) (: reason str)] :post [(: % KubeServiceRefused)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言 service を当てられなかった断りを、namespace と名・理由を挙げて作るため。"
  (KubeServiceRefused (+ "Service " service.namespace "/" service.name " を当てられない — " reason)))


(defk outcome-of-answer [service answer]
  {:pre [(: service KubeService) (: answer (| HttpResponse HttpFailed))] :post [(: % KubeServiceOutcome)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "API server の答えから結末を読むため(201 = 作った・200 = 当て直した)。届かない・ほかの status は理由を挙げて断る。"
  (when (isinstance answer HttpFailed)
    (raise (! (refused service (+ "API server に届かない: " (! (text-field answer.detail)))))))
  (val status (! (status-field answer.status)))
  (match status
    201 KubeServiceOutcome.CREATED
    200 KubeServiceOutcome.APPLIED
    _ (raise (! (refused service (+ (str status) " " (cut (! (text-field answer.text)) 0 REFUSAL-BODY-LIMIT)))))))


(defk applied-service [service]
  {:pre [(: service KubeService)] :post [(: % KubeServiceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "宣言 service を、その job を回す worker の身元(ServiceAccount の token — 回るので当てるたびに読む)で自区画の API server へ当て、結末を
   得るため(頭の註 — 当てられなければ namespace と名・理由を挙げて断る・再試行しない)。"
  (<- token (| str FileFailed) (service-account-text SERVICE-ACCOUNT-TOKEN-FILE))
  (when (isinstance token FileFailed)
    (raise (! (refused service (+ "ServiceAccount の token の file が読めない: " token.detail)))))
  (<- headers (get dict #(str str)) (kube-headers token APPLY-CONTENT-TYPE))
  (<- path str (service-path service))
  (<- body str (service-apply-text service))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest "PATCH" (+ KUBE-API path) :headers headers :params {"fieldManager" FIELD-MANAGER "force" "true"} :body body
                   :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
  (<- outcome KubeServiceOutcome (outcome-of-answer service answer))
  outcome)


(defhandler kube-services-api
  ;; 本番: 宣言を worker の身元で自区画の API server へ当てる(頭の註)。外側に file の答え手と、API server 宛ての要求を区画の CA で検める
  ;; HTTP の答え手を要る。
  (EnsureKubeService [service]
    (<- outcome KubeServiceOutcome (applied-service service))
    (resume outcome)))
