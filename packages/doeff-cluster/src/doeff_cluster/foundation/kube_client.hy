;;; k8s の API の client(Pod の中から ServiceAccount の token で叩く汎用の I/O)。HTTP の client はこの module の中に閉じる。
;;; coordinator の kube_model の effect に答える handler は coordinator/protocol/kube.hy — この module は intent の型を読まない
;;; (層 foundation が読めるのは foundation だけ)ので、届かない・断られた時に投げる例外の型は組み立てる側(entry)が :fail で渡す。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import json)
(import pathlib [Path])
(import typing [Callable TypedDict Unpack])
(import httpx)


(defclass KubeRequestOptions [TypedDict :total False]
  "k8s の API への要求で httpx の request にそのまま渡す欄(使う物だけ)。"
  (#^ dict headers)
  (#^ dict params)
  (#^ bytes content))

(setv SA-DIR "/var/run/secrets/kubernetes.io/serviceaccount")
(setv API-URL "https://kubernetes.default.svc")


(defn #^ dict deployment-view [#^ dict body]
  "Deployment の object → 観測の dict(Rollout が見る欄だけ)。"
  (setv spec (.get body "spec" {}) status (.get body "status" {}) meta (.get body "metadata" {}))
  {"specReplicas" (.get spec "replicas" 1)
   "replicas" (.get status "replicas" 0)
   "readyReplicas" (.get status "readyReplicas" 0)
   "availableReplicas" (.get status "availableReplicas" 0)
   "updatedReplicas" (.get status "updatedReplicas" 0)
   "generation" (.get meta "generation" 0)
   "observedGeneration" (.get status "observedGeneration" 0)
   "annotations" (or (.get meta "annotations") {})})


(defclass KubeClient []
  "k8s の API の client。token は要求ごとに file から読む(projected token は期限で入れ替わる)。
   fail = 届かない・2xx でない時に、理由の文を渡して投げる例外の型(coordinator の entry が kube_model の KubeUnavailable を渡す)。"
  (defn #^ None __init__ [self #^ (get Callable #(#(str) Exception)) fail #^ str [base API-URL] #^ str [sa-dir SA-DIR]
                          #^ float [timeout 5.0] #^ (| httpx.BaseTransport None) [transport None]]
    (setv self.fail fail self.base base self.sa-dir (Path sa-dir)
          self.client (httpx.Client :timeout timeout :verify (str (/ self.sa-dir "ca.crt")) :trust-env False
                                    :transport transport)))

  (defn [staticmethod] #^ bool available [#^ str [sa-dir SA-DIR]]
    (.exists (/ (Path sa-dir) "token")))

  (defn #^ dict headers [self #^ (| str None) [content-type None]]
    (setv token (.strip (.read-text (/ self.sa-dir "token") :encoding "utf-8")))
    (| {"Authorization" (+ "Bearer " token) "Accept" "application/json"}
       (if content-type {"Content-Type" content-type} {})))

  (defn #^ str path [self #^ str namespace #^ str name #^ str [sub ""]]
    (.format "{}/apis/apps/v1/namespaces/{}/deployments/{}{}" self.base namespace name sub))

  ;; 答えは k8s の API の JSON の本文(object の dict)。
  (defn #^ dict call [self #^ str method #^ str url #^ (get Unpack KubeRequestOptions) #** kwargs]
    (try
      (setv response (.request self.client method url #** kwargs))
      (except [error httpx.HTTPError]
        (raise (self.fail (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))))
    (when (>= response.status-code 300)
      (raise (self.fail (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))))
    (.json response))

  (defn #^ dict node-labels [self #^ str node]
    "Node の metadata.labels(能力の導出の材料)。"
    (or (get (get (.call self "GET" (.format "{}/api/v1/nodes/{}" self.base node) :headers (.headers self)) "metadata") "labels") {}))

  (defn #^ dict read [self #^ str namespace #^ str name]
    (deployment-view (.call self "GET" (.path self namespace name) :headers (.headers self))))

  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run]
    (setv body (.call self "PATCH" (.path self namespace name "/scale")
                      :headers (.headers self "application/merge-patch+json")
                      :params (if dry-run {"dryRun" "All"} {})
                      :content (.encode (json.dumps {"spec" {"replicas" replicas}}) "utf-8")))
    (.get (.get body "spec" {}) "replicas" replicas))

  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations]
    (.call self "PATCH" (.path self namespace name)
           :headers (.headers self "application/merge-patch+json")
           :content (.encode (json.dumps {"metadata" {"annotations" annotations}}) "utf-8"))
    None))
