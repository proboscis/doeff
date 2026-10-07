;;; k8s の API の client(Pod の中から ServiceAccount の token で叩く汎用の I/O)。HTTP の client はこの module の中に閉じる。
;;; coordinator の kube_model の effect に答える handler は coordinator/protocol/kube.hy — この module は intent の型を読まない
;;; (層 foundation が読めるのは foundation だけ)ので、届かない・断られた時に投げる例外の型は組み立てる側(entry)が :fail で渡す。
;;; Deployment と Node は API の JSON の本文を中を読まずに運ぶ値(OpaqueJson)で渡すだけ — 観測の欄の読みは答え手の側
;;; (coordinator/protocol/kube.hy の deployment-view・#2764・#2807)。
;;; Deployment は時間で読みに行かず、follow で 1 つずつ list の後の watch で見張る(#3868): daemon の thread が list で今を伝え、その一覧の
;;; 版から watch の stream で変化の出来事を受けて伝える。stream が普通に終われば覚えた版から受け直し、版が古すぎれば(410)list し直し、
;;; 届かない・断られた時は理由を伝えて retry-seconds の後に list し直す。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import threading)
(import typing [Callable])
(import httpx)
(import doeff_hy.json_value [OpaqueJson])


(setv SA-DIR "/var/run/secrets/kubernetes.io/serviceaccount")
(setv API-URL "https://kubernetes.default.svc")
;; watch の stream の読みの打ち切りを、server が timeoutSeconds で閉じる刻より後ろへずらす秒(client が先に切らない)。
(setv WATCH-READ-MARGIN-SECONDS 30)


(defclass KubeClient []
  "k8s の API の client。token は要求ごとに file から読む(projected token は期限で入れ替わる)。
   fail = 届かない・2xx でない時に、理由の文を渡して投げる例外の型(coordinator の entry が kube_model の KubeUnavailable を渡す)。
   retry-seconds = 見張りが届かない・断られた後に list し直すまでの秒・watch-seconds = watch の要求の timeoutSeconds。"
  (defn #^ None __init__ [self #^ (get Callable #(#(str) Exception)) fail #^ str [base API-URL] #^ str [sa-dir SA-DIR]
                          #^ float [timeout 5.0] #^ (| httpx.BaseTransport None) [transport None]
                          #^ float [retry-seconds 10.0] #^ int [watch-seconds 300]]
    (setv self.fail fail self.base base self.sa-dir (Path sa-dir) self.timeout timeout
          self.retry-seconds retry-seconds self.watch-seconds watch-seconds
          self.client (httpx.Client :timeout timeout :verify (str (/ self.sa-dir "ca.crt")) :trust-env False
                                    :transport transport)))

  (defn [staticmethod] #^ bool available [#^ str [sa-dir SA-DIR]]
    (.exists (/ (Path sa-dir) "token")))

  (defn #^ dict headers [self #^ (| str None) [content-type None]]
    (setv token (.strip (.read-text (/ self.sa-dir "token") :encoding "utf-8")))
    (| {"Authorization" (+ "Bearer " token) "Accept" "application/json"}
       (if content-type {"Content-Type" content-type} {})))

  (defn #^ str deployments-url [self #^ str namespace]
    "名前空間の Deployment の一覧の URL(見張りの list と watch が使う)。"
    (.format "{}/apis/apps/v1/namespaces/{}/deployments" self.base namespace))

  (defn #^ str path [self #^ str namespace #^ str name #^ str [sub ""]]
    "Deployment 1 つ(と scale などの subresource)の URL(台数と annotation の書きが使う)。"
    (.format "{}/{}{}" (.deployments-url self namespace) name sub))

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

  (defn #^ OpaqueJson node-labels [self #^ str node]
    "Node の metadata.labels(能力の導出の材料)を、中を読まずに運ぶ JSON の値で返す(解くのは coordinator/protocol/kube — #2807)。"
    (OpaqueJson.of (or (get (get (.call self "GET" (.format "{}/api/v1/nodes/{}" self.base node)) "metadata") "labels") {})))

  (defn #^ (get Callable #([] None)) follow [self #^ str namespace #^ str name #^ (get Callable #([OpaqueJson] None)) on-body
                                             #^ (get Callable #([str] None)) on-error]
    "Deployment 1 つを list の後の watch で見張る daemon の thread(名 kube-watch)を始め、止める関数を返すため(時間で読みに行かず、
     変化の出来事で伝える — #3868)。on-body = Deployment の object(list の今と、ADDED・MODIFIED の出来事)・on-error = 無い・消された・
     届かない・断られた・読めない理由。どちらも thread の中から呼ぶ。止めた後はどちらも呼ばない。"
    (setv watching (DeploymentWatch self namespace name on-body on-error))
    (.start (threading.Thread :target watching.run :name "kube-watch" :daemon True))
    watching.stop)

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
    None)

  (defn #^ (get Callable #([] bool)) in-background [self #^ (get Callable #([] None)) work #^ (get Callable #([] None)) then]
    "node の label の読み(coordinator/protocol/kube の KubeReadBatch)を調停ループの外の daemon の thread で読み、終わったかを答える
     関数を返すため(同期の client が scheduler の thread を塞がない — #2807)。work が返った後に終わった印を立て、その後に then を呼ぶ
     (呼び手は then で受付の箱を起こす — 印より前に起こすと、起きたループが「まだ」と読んで眠り直す・#3868)。"
    (setv done (threading.Event))
    (.start (threading.Thread :target (fn [] (work) (.set done) (then)) :name "kube-reads" :daemon True))
    done.is-set))


(defrecord WatchNext
  "見張りの watch 1 回の後の次の要求: version = 続きを受ける版(None = list し直す)・pause = 次の要求までの秒(0 = すぐ)。"
  (#^ (| str None) version)
  (#^ float pause))


(defclass DeploymentWatch []
  "Deployment 1 つの見張り(KubeClient.follow が作り、daemon の thread で run を回す — #3868)。stopped = 止める合図・response = 今
   受けている watch の stream(止める時に閉じて、読みの途中の thread を抜けさせる)。"
  (defn #^ None __init__ [self #^ KubeClient client #^ str namespace #^ str name #^ (get Callable #([OpaqueJson] None)) on-body
                          #^ (get Callable #([str] None)) on-error]
    (setv self.client client self.namespace namespace self.name name self.on-body on-body self.on-error on-error
          self.stopped (threading.Event) self.response None)
    None)

  (defn #^ None stop [self]
    "止める合図を立て、今の stream を閉じるため(止めた後は on-body も on-error も呼ばない)。"
    (.set self.stopped)
    (setv response self.response)
    (when (is-not response None)
      (.close response))
    None)

  (defn #^ str key [self]
    "理由の文で見張りの相手を名指すため(「ns/名」)。"
    (+ self.namespace "/" self.name))

  (defn #^ None tell-body [self #^ dict body]
    "止められていなければ、Deployment の object を伝えるため。"
    (when (not (.is-set self.stopped))
      (self.on-body (OpaqueJson.of body)))
    None)

  (defn #^ None tell-error [self #^ str reason]
    "止められていなければ、伝えられない理由を伝えるため。"
    (when (not (.is-set self.stopped))
      (self.on-error reason))
    None)

  (defn [staticmethod] #^ (| str None) version-of [#^ object body]
    "k8s の object か一覧の metadata.resourceVersion(無い・形が違えば None)。"
    (setv meta (if (isinstance body dict) (.get body "metadata") None))
    (setv version (if (isinstance meta dict) (.get meta "resourceVersion") None))
    (if (isinstance version str) version None))

  (defn #^ dict params [self]
    "list と watch を見張りの相手の Deployment 1 つに絞る query(名の fieldSelector)。"
    (dict :fieldSelector (+ "metadata.name=" self.name)))

  (defn #^ (| str None) list-once [self]
    "list で Deployment の今(在れば object・無ければ「無い」理由)を伝え、watch を始める一覧の版を返すため。届かない・断られた・読めない
     時は理由を伝えて None(呼び手が retry-seconds の後に list し直す)。"
    (try
      (setv response (.get self.client.client (.deployments-url self.client self.namespace) :headers (.headers self.client)
                           :params (.params self)))
      (except [error httpx.HTTPError]
        (.tell-error self (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error))
        (return None)))
    (when (>= response.status-code 300)
      (.tell-error self (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))
      (return None))
    (try
      (setv listed (.json response))
      (except [error ValueError]
        (.tell-error self (.format "k8s の Deployment の一覧が JSON でない: {}" error))
        (return None)))
    (setv version (DeploymentWatch.version-of listed)
          items (if (isinstance listed dict) (.get listed "items") None))
    (when (or (is version None) (not (isinstance items list)))
      (.tell-error self "k8s の Deployment の一覧の形が違う(metadata.resourceVersion・items)")
      (return None))
    (if (and items (isinstance (get items 0) dict))
        (.tell-body self (get items 0))
        (.tell-error self (+ "無い Deployment: " (.key self))))
    version)

  (defn #^ WatchNext watch-once [self #^ str version]
    "version から watch の stream を受け、出来事を伝えるため。答え = WatchNext(次に watch する版か None・次の要求までの秒):
     stream が普通に終わった・止められた = 覚えた版と 0・版が古すぎる(ERROR の 410)= None と 0(すぐ list し直す)・それ以外の ERROR と
     届かない・断られた・読めない = 理由を伝えて None と retry-seconds。"
    (setv params (| (.params self) {"watch" "true" "resourceVersion" version "allowWatchBookmarks" "true"
                                    "timeoutSeconds" (str self.client.watch-seconds)})
          retry (WatchNext :version None :pause self.client.retry-seconds))
    (try
      (with [response (.stream self.client.client "GET" (.deployments-url self.client self.namespace) :headers (.headers self.client)
                               :params params
                               :timeout (httpx.Timeout self.client.timeout
                                                       :read (+ self.client.watch-seconds WATCH-READ-MARGIN-SECONDS)))]
        (setv self.response response)
        (when (.is-set self.stopped)
          (return (WatchNext :version version :pause 0.0)))
        (when (>= response.status-code 300)
          (.read response)
          (.tell-error self (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))
          (return retry))
        (setv current version)
        (for [line (.iter-lines response)]
          (when (.is-set self.stopped)
            (return (WatchNext :version current :pause 0.0)))
          (when (.strip line)
            (setv event (json.loads line))
            (match event
              {"type" (| "ADDED" "MODIFIED") "object" body} :if (isinstance body dict)
                (do (setv current (or (DeploymentWatch.version-of body) current))
                    (.tell-body self body))
              {"type" "DELETED" "object" body}
                (do (setv current (or (DeploymentWatch.version-of body) current))
                    (.tell-error self (+ "Deployment が消された: " (.key self))))
              {"type" "BOOKMARK" "object" body}
                (setv current (or (DeploymentWatch.version-of body) current))
              {"type" "ERROR" "object" {"code" 410}}
                (return (WatchNext :version None :pause 0.0))
              {"type" "ERROR" "object" {"message" message}}
                (do (.tell-error self (str message))
                    (return retry))
              _
                (do (.tell-error self (.format "k8s の watch の出来事の形が違う: {}" (cut line 0 300)))
                    (return retry)))))
        (WatchNext :version current :pause 0.0))
      (except [error httpx.HTTPError]
        (.tell-error self (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error))
        retry)
      (except [error ValueError]
        (.tell-error self (.format "k8s の watch の出来事が JSON でない: {}" error))
        retry)
      (finally
        (setv self.response None))))

  (defn #^ None run [self]
    "止められるまで list と watch を繰り返す(daemon の thread の本体)。"
    (setv version None)
    (while (not (.is-set self.stopped))
      (if (is version None)
          (do (setv version (.list-once self))
              (when (is version None)
                (.wait self.stopped self.client.retry-seconds)))
          (do (setv next (.watch-once self version)
                    version next.version)
              (when (> next.pause 0)
                (.wait self.stopped next.pause)))))
    None))
