;;; kube_model の effect の handler。kube-api = Pod の中から k8s の API へ(ServiceAccount の token)・kube-memory = テストの dict・
;;; kube-unavailable = 資格の無い所(手元の coordinator)で全部 KubeUnavailable を返す。HTTP の client はこの module の中に閉じる。
(require doeff-hy.macros [defhandler])
(import json)
(import pathlib [Path])
(import httpx)
(import .kube_model [ReadDeployment ScaleDeployment AnnotateDeployment KubeUnavailable])

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
   "annotations" (or (.get meta "annotations") {})
   ;; pod template の container の名 → image(本番の配備の流れが apply で決めた版 — Service の土台の追随が読む)。
   "images" (dfor c (or (.get (.get (.get spec "template" {}) "spec" {}) "containers") [])
                  (.get c "name" "") (.get c "image" ""))})


(defclass KubeClient []
  "k8s の API の client。token は要求ごとに file から読む(projected token は期限で入れ替わる)。"
  (defn __init__ [self [base API-URL] [sa-dir SA-DIR] [timeout 5.0] [transport None]]
    (setv self.base base self.sa-dir (Path sa-dir)
          self.client (httpx.Client :timeout timeout :verify (str (/ self.sa-dir "ca.crt")) :trust-env False
                                    #** (if transport {"transport" transport} {}))))

  (defn [staticmethod] #^ bool available [[sa-dir SA-DIR]]
    (.exists (/ (Path sa-dir) "token")))

  (defn #^ dict headers [self [content-type None]]
    (setv token (.strip (.read-text (/ self.sa-dir "token") :encoding "utf-8")))
    (| {"Authorization" (+ "Bearer " token) "Accept" "application/json"}
       (if content-type {"Content-Type" content-type} {})))

  (defn #^ str path [self #^ str namespace #^ str name [sub ""]]
    (.format "{}/apis/apps/v1/namespaces/{}/deployments/{}{}" self.base namespace name sub))

  (defn call [self #^ str method #^ str url #** kwargs]
    (try
      (setv response (.request self.client method url #** kwargs))
      (except [error httpx.HTTPError]
        (raise (KubeUnavailable (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))))
    (when (>= response.status-code 300)
      (raise (KubeUnavailable (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))))
    (.json response))

  (defn #^ dict read [self #^ str namespace #^ str name]
    (deployment-view (.call self "GET" (.path self namespace name) :headers (.headers self))))

  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run]
    (setv body (.call self "PATCH" (.path self namespace name "/scale")
                      :headers (.headers self "application/merge-patch+json")
                      :params (if dry-run {"dryRun" "All"} {})
                      :content (.encode (json.dumps {"spec" {"replicas" replicas}}) "utf-8")))
    (.get (.get body "spec" {}) "replicas" replicas))

  (defn annotate [self #^ str namespace #^ str name #^ dict annotations]
    (.call self "PATCH" (.path self namespace name)
           :headers (.headers self "application/merge-patch+json")
           :content (.encode (json.dumps {"metadata" {"annotations" annotations}}) "utf-8"))
    None))


(defhandler kube-api [#^ KubeClient client]
  (ReadDeployment [namespace name] (resume (.read client namespace name)))
  (ScaleDeployment [namespace name replicas dry-run] (resume (.scale client namespace name replicas dry-run)))
  (AnnotateDeployment [namespace name annotations] (resume (.annotate client namespace name annotations))))


(defhandler kube-unavailable [#^ str reason]
  (ReadDeployment [namespace name] (raise (KubeUnavailable reason)))
  (ScaleDeployment [namespace name replicas dry-run] (raise (KubeUnavailable reason)))
  (AnnotateDeployment [namespace name annotations] (raise (KubeUnavailable reason))))


(defclass KubeMemory []
  "テストの k8s。deployments = 「ns/名」→ 観測の dict(specReplicas・readyReplicas・annotations …)。
   scale は宣言の台数だけを変える(Pod が立つ・消えるのはテストが .settle で進める)。calls = 受けた書きの記録。
   down = 真の間は全部 KubeUnavailable(API の途絶)。"
  (defn __init__ [self #^ dict deployments]
    (setv self.deployments deployments self.calls [] self.down False))

  (defn #^ dict row [self #^ str namespace #^ str name]
    (when self.down (raise (KubeUnavailable "テストの k8s が止まっている")))
    (setv key (+ namespace "/" name))
    (when (not-in key self.deployments) (raise (KubeUnavailable (+ "無い Deployment: " key))))
    (get self.deployments key))

  (defn settle [self #^ str key [ready None]]
    "Pod が宣言の台数に揃った(ready を渡せばその数だけ準備できた)とする。"
    (setv row (get self.deployments key) n (get row "specReplicas"))
    (.update row {"replicas" n "readyReplicas" (if (is ready None) n ready) "availableReplicas" n "updatedReplicas" n})))


(defhandler kube-memory [#^ KubeMemory kube]
  (ReadDeployment [namespace name]
    (resume (| {"replicas" 0 "readyReplicas" 0 "availableReplicas" 0 "updatedReplicas" 0
                "generation" 1 "observedGeneration" 1 "annotations" {} "images" {}}
               (.row kube namespace name))))
  (ScaleDeployment [namespace name replicas dry-run]
    (setv row (.row kube namespace name))
    (.append kube.calls {"op" "scale" "key" (+ namespace "/" name) "replicas" replicas "dryRun" dry-run})
    (when (not dry-run) (setv (get row "specReplicas") replicas))
    (resume replicas))
  (AnnotateDeployment [namespace name annotations]
    (setv row (.row kube namespace name))
    (.append kube.calls {"op" "annotate" "key" (+ namespace "/" name) "annotations" annotations})
    (setv merged (| (.get row "annotations" {}) annotations))
    (setv (get row "annotations") (dfor #(k v) (.items merged) :if (is-not v None) k v))
    (resume None)))
