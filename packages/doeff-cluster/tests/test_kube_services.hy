;;; job が自分へ届く網の宛先(k8s の Service)を宣言どおりに在らせる effect EnsureKubeService(doeff_cluster.shared.intent.kube_service_model)
;;; の検 — 本番の答え手(doeff_cluster.shared.protocol.kube_services の kube-services-api)と模擬の答え手(doeff_cluster.sim.k8s_services の
;;; held-kube-services)が同じ筋書きに同じ結末で答え、同じ Service の一覧を残す事を確かめる。
;;;
;;;   綴り              宣言 → server-side apply の本文(宣言が持つ欄だけ・LOAD-BALANCER は node の口を開けない・class は書いた時だけ)
;;;   宣言の検め        空の selector・口の無い Service・同じ名の口・CLUSTER-IP の class は作る時に断る(失敗ケース)
;;;   契約              初めて当てた Service は CREATED・在る Service は APPLIED(同じ宣言でも)・一覧は namespace と名ごとに最後の宣言
;;;   本番の要求の形    worker の身元の Bearer の token で、API server の Service の path へ PATCH(server-side apply・持ち主の名・force)
;;;   断る(失敗ケース)  権限の無い namespace(403)・名乗れない token(401)・読めない token の file・届かない API server は、namespace と名・
;;;                     理由を挙げて断る。断りの文に token は載らない
;;; 外の世界は、この file の API server の相手役(server-side apply を受ける)と memory の file。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import collections.abc [Callable])
(import doeff [with_handlers])
(import doeff_hy.frozen [FrozenMap])
(import doeff_core_effects [MemoryFile MemoryFiles])
(import doeff_core_effects [state])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [memory-file-handler])
(import doeff_cluster.shared.intent.kube_service_model [EnsureKubeService KubeService KubeServiceOutcome KubeServicePort KubeServiceRefused
                                                        KubeServiceType])
(import doeff_cluster.shared.protocol.kube_services [APPLY-CONTENT-TYPE FIELD-MANAGER kube-services-api service-apply-text service-path])
(import doeff_cluster.shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-TOKEN-FILE])
(import doeff_cluster.sim.k8s_services [KubeServiceLog held-kube-services])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val TOKEN "worker-identity-token")
(val WORKER-FILES (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content (.encode (+ TOKEN "\n") "utf-8"))) :dirs #()))
(val ON-NODE (FrozenMap {"app" "job-worker" "example.dev/node" "n1"}))
;; cluster の中の宛先と、load balancer の実装が外へ出す宛先(class・label・annotation つき)。
(val INSIDE (KubeService :namespace "apps" :name "ledger" :type KubeServiceType.CLUSTER-IP :selector ON-NODE
                         :ports #((KubeServicePort :name "http" :port 8621 :target-port 8621))
                         :labels (FrozenMap {"app" "ledger"})))
(val OUTSIDE (KubeService :namespace "apps" :name "ledger-lb" :type KubeServiceType.LOAD-BALANCER :selector ON-NODE
                          :ports #((KubeServicePort :name "http" :port 80 :target-port 8622))
                          :labels (FrozenMap {"app" "ledger" "lb.example.dev/class-config" "entry"})
                          :annotations (FrozenMap {"lb.example.dev/hostname" "ledger"})
                          :load-balancer-class "lb.example.dev"))
;; 同じ Service(namespace と名)の宣言を変えた物(口を足した)。
(val INSIDE-WIDER (KubeService :namespace "apps" :name "ledger" :type KubeServiceType.CLUSTER-IP :selector ON-NODE
                               :ports #((KubeServicePort :name "http" :port 8621 :target-port 8621)
                                        (KubeServicePort :name "metrics" :port 9100 :target-port 9100))
                               :labels (FrozenMap {"app" "ledger"})))
;; 権限の外の namespace の宣言。
(val ELSEWHERE (KubeService :namespace "vault" :name "ledger" :type KubeServiceType.CLUSTER-IP :selector ON-NODE
                            :ports #((KubeServicePort :name "http" :port 8621 :target-port 8621))))


(defrecord SeenRequest
  "API server の相手役が受けた要求 1 つ: method・url・params = query・content-type・authorization・body = 本文の字。"
  (#^ str method)
  (#^ str url)
  (#^ dict params)
  (#^ (| str None) content-type)
  (#^ (| str None) authorization)
  (#^ str body))


(defrecord AppliedObject
  "API server の相手役が持つ Service 1 つ: path = API server の上の path・body = 最後に当てた本文の字。"
  (#^ str path)
  (#^ str body))


(defclass ApiServerWorld []
  "API server の相手役の世界と、受けた物の入れ物: token = 認める身元の token・namespaces = 身元が Service を書ける namespace・down = 届かない
   世界か・objects = 持つ Service(初めて作った順)・seen = 受けた要求(受けた順)。受けるたびに組を作り直す。"
  (defn #^ None __init__ [self #^ str token #^ tuple namespaces #^ bool [down False]]  ; defk にできない: class の作り手(Python が呼ぶ口)
    "空の相手役を作るため。"
    (setv self.token token
          self.namespaces namespaces
          self.down down
          #^ (get tuple #(AppliedObject ...)) self.objects #()
          #^ (get tuple #(SeenRequest ...)) self.seen #())))


(defk api-answer [status text url]
  {:pre [(: status int) (: text str) (: url str)] :post [(: % HttpResponse)]}
  "相手役の答え(status と本文の字)を HTTP の応答にするため。"
  (HttpResponse status {} (.encode text "utf-8") text url 0.0))


(defk applied-answer [world request]
  {:pre [(: world ApiServerWorld) (: request SeenRequest)] :post [(: % HttpResponse)]}
  "Service の server-side apply 1 つに、API server と同じ順(身元 → 権限 → 作るか置き換えるか)で答えるため。"
  (val path (cut request.url (len KUBE-API) None))
  (val namespace (get (.split path "/") 4))
  (cond
    (!= request.authorization (+ "Bearer " TOKEN))
    (! (api-answer 401 "{\"kind\":\"Status\",\"reason\":\"Unauthorized\",\"code\":401}" request.url))
    (not-in namespace world.namespaces)
    (! (api-answer 403 (+ "{\"kind\":\"Status\",\"reason\":\"Forbidden\",\"message\":\"services is forbidden in the namespace " namespace "\"}")
                   request.url))
    True
    (do (val existed (any (gfor held world.objects (= held.path path))))
        (val applied (AppliedObject :path path :body request.body))
        (setv world.objects (if existed
                                (tuple (gfor held world.objects (if (= held.path path) applied held)))
                                (+ world.objects #(applied))))
        (! (api-answer (if existed 200 201) request.body request.url)))))


(defhandler api-server-peer [#^ ApiServerWorld world]
  ;; 引数に残す理由: 相手役の世界(何を許し、何を持つか)は筋書きごとの外の世界の形で、走らせた後に読む入れ物でもある。
  ;; API server の Service の PATCH だけに答え、ほかの HTTP の要求は外側へ通す。
  (HttpRequest [method url headers params body failures-as-values]
    :when (and (= method "PATCH") (isinstance url str) (.startswith url (+ KUBE-API "/api/v1/namespaces/")) (in "/services/" url))
    (when world.down
      (if failures-as-values
          (resume (HttpFailed :url url :detail "ConnectError: [Errno 111] Connection refused" :kind HttpFailureKind.CONNECT-FAILED))
          (raise (ConnectionError "[Errno 111] Connection refused"))))
    (val seen (SeenRequest :method method :url url :params (dict (or params {}))
                           :content-type (.get (or headers {}) "content-type") :authorization (.get (or headers {}) "authorization")
                           :body (if (isinstance body bytes) (.decode body "utf-8") (str body))))
    (setv world.seen (+ world.seen #(seen)))
    (resume (! (applied-answer world seen)))))


(defk ensured-all [services]
  {:pre [(: services tuple)] :post [(: % tuple)]}
  "宣言 services を順に当て、結末の組を得るため。"
  (var outcomes #())
  (for [service services]
    (<- outcome KubeServiceOutcome (EnsureKubeService service))
    (:= outcomes (+ outcomes #(outcome))))
  outcomes)


(defk on-api [world services]
  {:pre [(: world ApiServerWorld) (: services tuple)] :post [(: % tuple)]}
  "本番の答え手で、API server の相手役 world に宣言 services を順に当てた結末の組を得るため。"
  (<- outcomes tuple (with_handlers [(state) (api-server-peer world) (memory-file-handler WORKER-FILES) kube-services-api]
                       (ensured-all services)))
  outcomes)


(defk on-sim [log services]
  {:pre [(: log KubeServiceLog) (: services tuple)] :post [(: % tuple)]}
  "模擬の答え手で、入れ物 log に宣言 services を順に当てた結末の組を得るため。"
  (<- outcomes tuple (with_handlers [(held-kube-services log)] (ensured-all services)))
  outcomes)


(defk refusal-on-api [world files service]
  {:pre [(: world ApiServerWorld) (: files MemoryFiles) (: service KubeService)] :post [(: % (| str None))]}
  "worker の file が files・API server が world の世界で宣言 service を当てた時の断りの文を得るため(断らなければ None)。"
  (try
    (<- (with_handlers [(state) (api-server-peer world) (memory-file-handler files) kube-services-api] (EnsureKubeService service)))
    None
    (except [error KubeServiceRefused]
      (str error))))


(defk refused-record? [build]
  {:pre [(: build Callable)] :post [(: % bool)]}
  "宣言を作る関数 build が、作る時に断られるかを判じるため。"
  (try
    (build)
    False
    (except [ValueError]
      True)))


(deftest test-a-declaration-is-spelled-as-the-fields-it-holds
  (<- inside str (service-apply-text INSIDE))
  (assert (= (json.loads inside)
             {"apiVersion" "v1" "kind" "Service"
              "metadata" {"name" "ledger" "namespace" "apps" "labels" {"app" "ledger"}}
              "spec" {"type" "ClusterIP" "selector" {"app" "job-worker" "example.dev/node" "n1"}
                      "ports" [{"name" "http" "port" 8621 "targetPort" 8621 "protocol" "TCP"}]}})
          inside)
  ;; LOAD-BALANCER は class と annotation を書き、node の口を開けない。
  (<- outside str (service-apply-text OUTSIDE))
  (val spec (get (json.loads outside) "spec"))
  (assert (= #((get spec "type") (get spec "loadBalancerClass") (get spec "allocateLoadBalancerNodePorts"))
             #("LoadBalancer" "lb.example.dev" False))
          spec)
  (assert (= (get (json.loads outside) "metadata" "annotations") {"lb.example.dev/hostname" "ledger"}) outside)
  ;; CLUSTER-IP は LoadBalancer の欄を書かない・空の annotation は欄ごと省く。
  (assert (not-in "loadBalancerClass" inside) inside)
  (assert (not-in "allocateLoadBalancerNodePorts" inside) inside)
  (assert (not-in "annotations" inside) inside)
  (<- path str (service-path OUTSIDE))
  (assert (= path "/api/v1/namespaces/apps/services/ledger-lb") path))


(deftest test-a-declaration-that-cannot-be-a-service-is-refused-when-made
  (val port (KubeServicePort :name "http" :port 8621 :target-port 8621))
  ;; 失敗ケース: 空の selector(namespace の全部の Pod を選ぶ)・口の無い Service・同じ名の口・CLUSTER-IP の class・範囲の外の口。
  (for [build #((fn [] (KubeService :namespace "apps" :name "x" :type KubeServiceType.CLUSTER-IP :selector (FrozenMap {}) :ports #(port)))
                (fn [] (KubeService :namespace "apps" :name "x" :type KubeServiceType.CLUSTER-IP :selector ON-NODE :ports #()))
                (fn [] (KubeService :namespace "apps" :name "x" :type KubeServiceType.CLUSTER-IP :selector ON-NODE :ports #(port port)))
                (fn [] (KubeService :namespace "apps" :name "x" :type KubeServiceType.CLUSTER-IP :selector ON-NODE :ports #(port)
                                    :load-balancer-class "lb.example.dev"))
                (fn [] (KubeServicePort :name "http" :port 0 :target-port 8621)))]
    (<- refused bool (refused-record? build))
    (assert refused build))
  ;; 判定が常に断る物でないこと。
  (<- fine bool (refused-record? (fn [] INSIDE)))
  (assert (not fine)))


(deftest test-both-handlers-create-then-apply-and-keep-the-last-declaration
  (val story #(INSIDE INSIDE OUTSIDE INSIDE-WIDER))
  (val expected #(KubeServiceOutcome.CREATED KubeServiceOutcome.APPLIED KubeServiceOutcome.CREATED KubeServiceOutcome.APPLIED))
  (val world (ApiServerWorld TOKEN #("apps")))
  (<- api-outcomes tuple (on-api world story))
  (val log (KubeServiceLog))
  (<- sim-outcomes tuple (on-sim log story))
  (assert (= api-outcomes expected) api-outcomes)
  (assert (= sim-outcomes expected) sim-outcomes)
  ;; 模擬の一覧は namespace と名ごとに最後の宣言(初めて作った順)。
  (assert (= log.services #(INSIDE-WIDER OUTSIDE)) log.services)
  ;; 本番の相手役が持つ物は、模擬の一覧を同じ綴りにした物と同じ。
  (var expected-objects #())
  (for [service log.services]
    (<- path str (service-path service))
    (<- text str (service-apply-text service))
    (:= expected-objects (+ expected-objects #((AppliedObject :path path :body text)))))
  (assert (= world.objects expected-objects) world.objects))


(deftest test-the-production-handler-applies-with-the-worker-identity
  (val world (ApiServerWorld TOKEN #("apps")))
  (<- (on-api world #(OUTSIDE)))
  (assert (= (len world.seen) 1) world.seen)
  (val seen (get world.seen 0))
  (<- body str (service-apply-text OUTSIDE))
  (assert (= #(seen.method seen.url seen.params seen.content-type seen.authorization seen.body)
             #("PATCH" (+ KUBE-API "/api/v1/namespaces/apps/services/ledger-lb") {"fieldManager" FIELD-MANAGER "force" "true"}
               APPLY-CONTENT-TYPE (+ "Bearer " TOKEN) body))
          seen))


(deftest test-a-service-that-cannot-be-applied-is-refused-with-the-place-and-the-reason
  ;; 失敗ケース 1: 身元が Service を書けない namespace は 403。
  (<- forbidden (| str None) (refusal-on-api (ApiServerWorld TOKEN #("apps")) WORKER-FILES ELSEWHERE))
  (assert (and (is-not forbidden None) (in "vault/ledger" forbidden) (in "403" forbidden)) forbidden)
  ;; 失敗ケース 2: API server が認めない token は 401 — 断りの文に token は載らない。
  (val stale (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content b"expired-token\n")) :dirs #()))
  (<- unauthorized (| str None) (refusal-on-api (ApiServerWorld TOKEN #("apps")) stale INSIDE))
  (assert (and (is-not unauthorized None) (in "apps/ledger" unauthorized) (in "401" unauthorized) (not-in "expired-token" unauthorized))
          unauthorized)
  ;; 失敗ケース 3: token の file が無い worker。
  (<- no-token (| str None) (refusal-on-api (ApiServerWorld TOKEN #("apps")) (MemoryFiles :files #() :dirs #()) INSIDE))
  (assert (and (is-not no-token None) (in "apps/ledger" no-token) (in "token" no-token)) no-token)
  ;; 失敗ケース 4: 届かない API server(本番と模擬の両方)。
  (<- down (| str None) (refusal-on-api (ApiServerWorld TOKEN #("apps") :down True) WORKER-FILES INSIDE))
  (assert (and (is-not down None) (in "apps/ledger" down) (in "届かない" down)) down)
  (var sim-down None)
  (try
    (<- (on-sim (KubeServiceLog :down True) #(INSIDE)))
    (except [error KubeServiceRefused]
      (:= sim-down (str error))))
  (assert (and (is-not sim-down None) (in "apps/ledger" sim-down) (in "届かない" sim-down)) sim-down)
  ;; 判定が常に断る物でないこと: 揃っていれば断らない。
  (<- fine (| str None) (refusal-on-api (ApiServerWorld TOKEN #("apps")) WORKER-FILES INSIDE))
  (assert (is fine None) fine))
