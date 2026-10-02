;;; k8s の API の client(Pod の中から ServiceAccount の token で叩く汎用の I/O)。HTTP の client はこの module の中に閉じる。
;;; coordinator の kube_model の effect に答える handler は coordinator/protocol/kube.hy — この module は intent の型を読まない
;;; (層 foundation が読めるのは foundation だけ)ので、届かない・断られた時に投げる例外の型は組み立てる側(entry)が :fail で渡す。
;;; Deployment の読みは API の JSON の本文を返すだけ — 観測の欄の読みは答え手の側(coordinator/protocol/kube.hy の deployment-view・
;;; agora-redesign #2764)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import json)
(import pathlib [Path])
(import typing [Callable])
(import httpx)


(setv SA-DIR "/var/run/secrets/kubernetes.io/serviceaccount")
(setv API-URL "https://kubernetes.default.svc")


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

  ;; 答えは k8s の API の JSON の本文(object の dict)。要求の形は意味の引数(本文の型・dry-run・本文)で受け、HTTP の headers と
  ;; query の写像は httpx へ渡すこの 1 点で組む(写像を運ぶ型の欄を持たない — #2722)。
  (defn #^ dict call [self #^ str method #^ str url #^ (| str None) [content-type None] #^ bool [dry-run False]
                      #^ (| bytes None) [content None]]
    (try
      (setv response (.request self.client method url :headers (.headers self content-type)
                               :params (if dry-run {"dryRun" "All"} {}) :content content))
      (except [error httpx.HTTPError]
        (raise (self.fail (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))))
    (when (>= response.status-code 300)
      (raise (self.fail (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))))
    (.json response))

  (defn #^ dict node-labels [self #^ str node]
    "Node の metadata.labels(能力の導出の材料)。"
    (or (get (get (.call self "GET" (.format "{}/api/v1/nodes/{}" self.base node)) "metadata") "labels") {}))

  (defn #^ dict read [self #^ str namespace #^ str name]
    "Deployment の object(API の JSON の本文のまま)。"
    (.call self "GET" (.path self namespace name)))

  (defn #^ int scale [self #^ str namespace #^ str name #^ int replicas #^ bool dry-run]
    (setv body (.call self "PATCH" (.path self namespace name "/scale")
                      :content-type "application/merge-patch+json"
                      :dry-run dry-run
                      :content (.encode (json.dumps {"spec" {"replicas" replicas}}) "utf-8")))
    (.get (.get body "spec" {}) "replicas" replicas))

  (defn #^ None annotate [self #^ str namespace #^ str name #^ dict annotations]
    (.call self "PATCH" (.path self namespace name)
           :content-type "application/merge-patch+json"
           :content (.encode (json.dumps {"metadata" {"annotations" annotations}}) "utf-8"))
    None))
