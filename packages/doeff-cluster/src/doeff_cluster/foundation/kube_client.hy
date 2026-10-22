;;; k8s の API の client(Pod の中から ServiceAccount の token で叩く汎用の I/O)。HTTP の client はこの module の中に閉じる。
;;; coordinator の kube_model の effect に答える handler は coordinator/protocol/kube.hy — この module は intent の型を読まない
;;; (層 foundation が読めるのは foundation だけ)ので、届かない・断られた時に投げる例外の型は組み立てる側(entry)が :fail で渡す。
;;; Deployment と Node は API の JSON の本文を中を読まずに運ぶ値(OpaqueJson)で渡すだけ — 観測の欄の読みは答え手の側
;;; (coordinator/protocol/kube.hy の deployment-view・node-labels-view・#2764・#2807)。
;;; Deployment も Node も時間で読みに行かず、1 つずつ list の後の watch で見張る(follow — #3868・follow-node — #4070): daemon の thread が
;;; list で今を伝え、その一覧の版から watch の stream で変化の出来事を受けて伝える。stream が普通に終われば覚えた版から受け直し、版が
;;; 古すぎれば(410)list し直し、届かない・断られた時は理由を伝えて retry-seconds の後に list し直す(見張り — 2 つの種類で同じ)。
;;; 思わぬ例外でも見張りの thread は抜けない: 理由を伝え、traceback を stderr に出し、retry-seconds の後に list し直す。
;;; 接続の pool は thread の間で分けない: 見張り 1 つごとに自分の httpx.Client を持ち、台数と annotation の書き(本体の thread)は設定の
;;; client を使う(card ki-3ba062c02beb — 1 つの pool を 2 つ以上の thread が使うと、free-threaded の Python では httpcore の
;;; has_expired が pool の錠の外で書き換わる接続の期限を読み、float と None を比べる TypeError で見張りの thread が止まった・
;;; 本番 2026-10-09 12:40:48 JST)。
;;;
;;; 形(#2943 — 以前は class KubeClient と class ObjectWatch が httpx.Client・thread・停止の Event を持っていた): 欄だけの record と、作る関数・
;;; 動かす関数(defk)に分けた。
;;;   KubeSettings  client の設定(届かない時の例外の型・API の URL・ServiceAccount の dir・秒の設定)と本体の thread の接続の pool
;;;   KubeClient    kube-api(coordinator/protocol/kube.hy)が呼ぶ 4 つの口(follow・follow-node・scale・annotate — 形は同じ module の KubeCalls)
;;;                 と、その設定。口は作る関数 kube-client が設定を握らせた関数で、呼ぶと下の defk を新しい VM で走らせる(handler の本体と見張りの
;;;                 thread は、効果を出さずに HTTP を呼ぶだけ)
;;;   WatchPlace    見張り 1 つ(Deployment か Node)の持ち物: 相手・伝え先の関数・止める合図・今受けている stream の箱・自分の接続の pool
;;; thread の入口は daemon の thread の target(外の library の callback)だけで、中身は defk の watch-run。
(require doeff-hy.macros [defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import socket)
(import ssl)
(import sys)
(import threading)
(import traceback)
(import typing [Callable])
(import httpx)
(import doeff_hy.json_value [OpaqueJson])
(import doeff_core_effects.offloaded_call [run-detached])


(setv SA-DIR "/var/run/secrets/kubernetes.io/serviceaccount")
(setv API-URL "https://kubernetes.default.svc")
;; watch の stream の読みの打ち切りを、server が timeoutSeconds で閉じる刻より後ろへずらす秒(client が先に切らない)。
(setv WATCH-READ-MARGIN-SECONDS 30)


(defrecord KubeSettings
  "k8s の API の client の設定。fail = 届かない・2xx でない時に、理由の文を渡して投げる例外の型(coordinator の entry が kube_model の
   KubeUnavailable を渡す)・base = API server の URL・sa-dir = ServiceAccount の dir(token は要求ごとに file から読む — projected token は
   期限で入れ替わる。ca.crt は接続を作る時に読む)・timeout = 要求の秒・transport = 検が渡す偽の API(None = Pod の中の本物の API へ、TCP の
   keepalive を付けて繋ぐ — keepalive-options)・retry-seconds = 見張りが届かない・断られた後に list し直すまでの秒・watch-seconds = watch の
   要求の timeoutSeconds(server がこの秒で stream を閉じ、見張りは覚えた版から受け直す)・client = 本体の thread の要求(台数と annotation の書き)の
   接続の pool — 見張りは new-pool で自分の pool を作る(頭の註)。"
  (#^ (get Callable #([str] Exception)) fail)
  (#^ str base)
  (#^ str sa-dir)
  (#^ float timeout)
  (#^ (| httpx.BaseTransport None) transport)
  (#^ float retry-seconds)
  (#^ int watch-seconds)
  (#^ httpx.Client client))


(defrecord WatchNext
  "見張りの watch 1 回の後の次の要求: version = 続きを受ける版(None = list し直す)・pause = 次の要求までの秒(0 = すぐ)。"
  (#^ (| str None) version)
  (#^ float pause))


(defrecord WatchPlace
  "見張り 1 つ(Deployment か Node)の持ち物 — follow・follow-node が作り、daemon の thread で watch-run を回す(#3868・#4070)。
   list-url = その種類の一覧の URL・name = 見張る object の名(list と watch を名の fieldSelector で絞る)・kind = 理由の文で種類を名指す
   語(Deployment・Node)・key = 理由の文で相手を名指す綴り(Deployment は「ns/名」・Node は名)・on-body / on-error = 伝え先(thread の
   中から呼ぶ)・stopped = 止める合図・held = 今受けている watch の stream を入れる 1 つの箱(止める時に閉じて、読みの途中の thread を
   抜けさせる)・http = この見張りだけが使う接続の pool(頭の註。thread の終わりに閉じる)。"
  (#^ KubeSettings settings)
  (#^ str list-url)
  (#^ str name)
  (#^ str kind)
  (#^ str key)
  (#^ (get Callable #([OpaqueJson] None)) on-body)
  (#^ (get Callable #([str] None)) on-error)
  (#^ threading.Event stopped)
  (#^ list held)
  (#^ httpx.Client http))


(defrecord KubeClient
  "kube-api が呼ぶ k8s の client の 4 つの口(形は coordinator/protocol/kube.hy の KubeCalls)と、その設定。
   follow = Deployment 1 つを list の後の watch で見張る daemon の thread を始め、止める関数を返す(on-body = Deployment の object・on-error = 無い・
   消された・届かない・断られた・読めない理由 — どちらも thread の中から呼ぶ。止めた後はどちらも呼ばない)・follow-node = Node 1 つを同じ形で見張る
   (worker の置かれた node の label を、変化の出来事で伝える)・scale = 台数を変えて宣言の台数を返す・annotate = annotation を足す。"
  (#^ KubeSettings settings)
  (#^ (get Callable #([str str (get Callable #([OpaqueJson] None)) (get Callable #([str] None))] (get Callable #([] None)))) follow)
  (#^ (get Callable #([str (get Callable #([OpaqueJson] None)) (get Callable #([str] None))] (get Callable #([] None)))) follow-node)
  (#^ (get Callable #([str str int bool] int)) scale)
  (#^ (get Callable #([str str dict] None)) annotate))


(defk keepalive-options []
  {:pre [] :post [(: % tuple)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本物の API への接続の TCP の keepalive の設定(15 秒黙れば 5 秒ごとに 3 回確かめる — 約 30 秒)。繋がったまま黙った相手(FIN も RST
   も来ない — API server の node の障害・conntrack の失効)の watch の stream を、読みの打ち切り(watch-seconds + 余白)より早く切れた
   接続として見つけ、見張りが理由を伝えるため(#3868)。値は Linux の socket の名(coordinator は Pod の中だけで本物の API へ繋ぐ)。"
  #(#(socket.SOL_SOCKET socket.SO_KEEPALIVE 1) #(socket.IPPROTO_TCP socket.TCP_KEEPIDLE 15)
    #(socket.IPPROTO_TCP socket.TCP_KEEPINTVL 5) #(socket.IPPROTO_TCP socket.TCP_KEEPCNT 3)))


(defk kube-available [[sa-dir SA-DIR]]
  {:pre [(: sa-dir str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Pod の中で動いているか(ServiceAccount の token の file が在るか)を知るため — 無い手元の coordinator は k8s の無い答え手を使う。"
  (.exists (/ (Path sa-dir) "token")))


(defk new-pool [sa-dir timeout transport]
  {:pre [(: sa-dir str) (: timeout float) (: transport (| httpx.BaseTransport None))] :post [(: % httpx.Client)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "自分の接続の pool を持つ新しい client を作るため(本体の thread と見張り 1 つごとに 1 つ — 頭の註)。検が偽の API を渡していれば
   その transport を通す(偽の API は接続の pool を持たない)。"
  (httpx.Client :timeout timeout :trust-env False
                :transport (if (is transport None)
                               (httpx.HTTPTransport
                                 :verify (ssl.create-default-context :cafile (str (/ (Path sa-dir) "ca.crt")))
                                 :socket-options (list (! (keepalive-options))))
                               transport)))


(defk kube-headers [settings content-type]
  {:pre [(: settings KubeSettings) (: content-type (| str None))] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "要求の header を作るため(token は要求ごとに file から読む)。"
  (val token (.strip (.read-text (/ (Path settings.sa-dir) "token") :encoding "utf-8")))
  (| {"Authorization" (+ "Bearer " token) "Accept" "application/json"}
     (if content-type {"Content-Type" content-type} {})))


(defk deployments-url [settings namespace]
  {:pre [(: settings KubeSettings) (: namespace str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "名前空間の Deployment の一覧の URL(見張りの list と watch が使う)。"
  (.format "{}/apis/apps/v1/namespaces/{}/deployments" settings.base namespace))


(defk nodes-url [settings]
  {:pre [(: settings KubeSettings)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Node の一覧の URL(見張りの list と watch が使う — Node は名前空間を持たない)。"
  (.format "{}/api/v1/nodes" settings.base))


(defk deployment-url [settings namespace name sub]
  {:pre [(: settings KubeSettings) (: namespace str) (: name str) (: sub str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Deployment 1 つ(と scale などの subresource sub — 無ければ空)の URL(台数と annotation の書きが使う)。"
  (<- list-url str (deployments-url settings namespace))
  (.format "{}/{}{}" list-url name sub))


;; 答えは k8s の API の JSON の本文(object の dict)。要求の形は意味の引数(本文の型・dry-run・本文)で受け、HTTP の headers と
;; query の写像は httpx へ渡すこの 1 点で組む(写像を運ぶ型の欄を持たない — #2722)。
(defk kube-call [settings method url content-type dry-run content]
  {:pre [(: settings KubeSettings) (: method str) (: url str) (: content-type (| str None)) (: dry-run bool) (: content (| bytes None))]
   :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "本体の thread の要求 1 つを送り、k8s の API の JSON の本文を返すため。届かない・2xx でない時は設定の fail の例外を投げる。"
  (<- headers dict (kube-headers settings content-type))
  (try
    (setv response (.request settings.client method url :headers headers :params (if dry-run {"dryRun" "All"} {}) :content content))
    (except [error httpx.HTTPError]
      (raise (settings.fail (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))))
  (when (>= response.status-code 300)
    (raise (settings.fail (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300)))))
  (.json response))


(defk scale-patch [replicas]
  {:pre [(: replicas int)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "scale の subresource への merge patch の本文(JSON の境界の綴りの 1 点)。"
  {"spec" {"replicas" replicas}})


(defk annotate-patch [annotations]
  {:pre [(: annotations dict)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "Deployment への merge patch の本文(annotation を足す — JSON の境界の綴りの 1 点)。"
  {"metadata" {"annotations" annotations}})


(defk scale-deployment [settings namespace name replicas dry-run]
  {:pre [(: settings KubeSettings) (: namespace str) (: name str) (: replicas int) (: dry-run bool)] :post [(: % int)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "Deployment の宣言の台数を replicas にして(dry-run なら試すだけ)、k8s が答えた宣言の台数を返すため。"
  (<- url str (deployment-url settings namespace name "/scale"))
  (<- patch dict (scale-patch replicas))
  (<- body dict (kube-call settings "PATCH" url "application/merge-patch+json" dry-run (.encode (json.dumps patch) "utf-8")))
  (val spec (.get body "spec"))
  (if (isinstance spec dict) (.get spec "replicas" replicas) replicas))


(defk annotate-deployment [settings namespace name annotations]
  {:pre [(: settings KubeSettings) (: namespace str) (: name str) (: annotations dict)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "Deployment の annotation に annotations を足す(値が null の鍵は消す — merge patch)ため。"
  (<- url str (deployment-url settings namespace name ""))
  (<- patch dict (annotate-patch annotations))
  (<- body dict (kube-call settings "PATCH" url "application/merge-patch+json" False (.encode (json.dumps patch) "utf-8")))
  None)


;; --- 見張り(list の後の watch — #3868・#4070)---------------------------------------------------------------------------

(defk watch-place [settings list-url name kind key on-body on-error]
  {:pre [(: settings KubeSettings) (: list-url str) (: name str) (: kind str) (: key str)
         (: on-body (get Callable #([OpaqueJson] None))) (: on-error (get Callable #([str] None)))]
   :post [(: % WatchPlace)] :tags {:context "doeff-cluster" :role "foundation"}}
  "見張り 1 つの持ち物を作るため(自分の接続の pool と止める合図を新しく持つ)。"
  (<- http httpx.Client (new-pool settings.sa-dir settings.timeout settings.transport))
  (WatchPlace :settings settings :list-url list-url :name name :kind kind :key key :on-body on-body :on-error on-error
              :stopped (threading.Event) :held [None] :http http))


(defk stop-watch [watching]
  {:pre [(: watching WatchPlace)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "止める合図を立て、今の stream を閉じるため(止めた後は on-body も on-error も呼ばない)。"
  (.set watching.stopped)
  (val response (get watching.held 0))
  (when (is-not response None)
    (.close response))
  None)


(defk tell-body [watching body]
  {:pre [(: watching WatchPlace) (: body dict)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "止められていなければ、見張る object(Deployment か Node)を伝えるため。"
  (when (not (.is-set watching.stopped))
    (watching.on-body (OpaqueJson.of body)))
  None)


(defk tell-error [watching reason]
  {:pre [(: watching WatchPlace) (: reason str)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "止められていなければ、伝えられない理由を伝えるため。"
  (when (not (.is-set watching.stopped))
    (watching.on-error reason))
  None)


(defk version-of [body]
  {:pre [(: body (| dict list str int float bool None))] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "k8s の object か一覧の metadata.resourceVersion(無い・形が違えば None)。"
  (val meta (if (isinstance body dict) (.get body "metadata") None))
  (val version (if (isinstance meta dict) (.get meta "resourceVersion") None))
  (if (isinstance version str) version None))


(defk watch-params [watching]
  {:pre [(: watching WatchPlace)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "list と watch を見張りの相手の object 1 つに絞る query(名の fieldSelector)。"
  (dict :fieldSelector (+ "metadata.name=" watching.name)))


(defk watch-query [watching version]
  {:pre [(: watching WatchPlace) (: version str)] :post [(: % dict)] :tags {:context "doeff-cluster" :role "foundation"}}
  "watch の要求の query(名の fieldSelector に、続きを受ける版・しおりの許し・server が stream を閉じる秒を足す)。"
  (<- narrowed dict (watch-params watching))
  (| narrowed {"watch" "true" "resourceVersion" version "allowWatchBookmarks" "true"
               "timeoutSeconds" (str watching.settings.watch-seconds)}))


(defk list-once [watching]
  {:pre [(: watching WatchPlace)] :post [(: % (| str None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "list で object の今(在れば object・無ければ「無い」理由)を伝え、watch を始める一覧の版を返すため。届かない・断られた・読めない
   時は理由を伝えて None(呼び手が retry-seconds の後に list し直す)。"
  (<- headers dict (kube-headers watching.settings None))
  (<- params dict (watch-params watching))
  (try
    (setv response (.get watching.http watching.list-url :headers headers :params params))
    (except [error httpx.HTTPError]
      (<- (tell-error watching (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))
      (return None)))
  (when (>= response.status-code 300)
    (<- (tell-error watching (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300))))
    (return None))
  (try
    (setv listed (.json response))
    (except [error ValueError]
      (<- (tell-error watching (.format "k8s の {} の一覧が JSON でない: {}" watching.kind error)))
      (return None)))
  (<- version (| str None) (version-of listed))
  (val items (if (isinstance listed dict) (.get listed "items") None))
  (when (or (is version None) (not (isinstance items list)))
    (<- (tell-error watching (.format "k8s の {} の一覧の形が違う(metadata.resourceVersion・items)" watching.kind)))
    (return None))
  (if (and items (isinstance (get items 0) dict))
      (<- (tell-body watching (get items 0)))
      (<- (tell-error watching (.format "無い {}: {}" watching.kind watching.key))))
  version)


(defk watch-once [watching version]
  {:pre [(: watching WatchPlace) (: version str)] :post [(: % WatchNext)] :tags {:context "doeff-cluster" :role "foundation"}}
  "version から watch の stream を受け、出来事を伝えるため。答え = WatchNext(次に watch する版か None・次の要求までの秒):
   stream が普通に終わった・止められた = 覚えた版と 0・版が古すぎる(ERROR の 410)= None と 0(すぐ list し直す)・それ以外の ERROR と
   届かない・断られた・読めない = 理由を伝えて None と retry-seconds。"
  (<- headers dict (kube-headers watching.settings None))
  (<- params dict (watch-query watching version))
  (val retry (WatchNext :version None :pause watching.settings.retry-seconds))
  (try
    (with [response (.stream watching.http "GET" watching.list-url :headers headers
                             :params params
                             :timeout (httpx.Timeout watching.settings.timeout
                                                     :read (+ watching.settings.watch-seconds WATCH-READ-MARGIN-SECONDS)))]
      (setv (get watching.held 0) response)
      (when (.is-set watching.stopped)
        (return (WatchNext :version version :pause 0.0)))
      (when (>= response.status-code 300)
        (.read response)
        (<- (tell-error watching (.format "k8s の API が {} を返した: {}" response.status-code (cut response.text 0 300))))
        (return retry))
      (var current version)
      (for [line (.iter-lines response)]
        (when (.is-set watching.stopped)
          (return (WatchNext :version current :pause 0.0)))
        (when (.strip line)
          (val event (json.loads line))
          (match event
            {"type" (| "ADDED" "MODIFIED") "object" body} :if (isinstance body dict)
              (do (<- seen (| str None) (version-of body))
                  (:= current (or seen current))
                  (<- (tell-body watching body)))
            {"type" "DELETED" "object" body}
              (do (<- seen (| str None) (version-of body))
                  (:= current (or seen current))
                  (<- (tell-error watching (.format "{} が消された: {}" watching.kind watching.key))))
            {"type" "BOOKMARK" "object" body}
              (do (<- seen (| str None) (version-of body))
                  (:= current (or seen current)))
            {"type" "ERROR" "object" {"code" 410}}
              (return (WatchNext :version None :pause 0.0))
            {"type" "ERROR" "object" {"message" message}}
              (do (<- (tell-error watching (str message)))
                  (return retry))
            _
              (do (<- (tell-error watching (.format "k8s の watch の出来事の形が違う: {}" (cut line 0 300))))
                  (return retry)))))
      (WatchNext :version current :pause 0.0))
    (except [error httpx.HTTPError]
      (<- (tell-error watching (.format "k8s の API に届かない: {}: {}" (. (type error) __name__) error)))
      retry)
    (except [error ValueError]
      (<- (tell-error watching (.format "k8s の watch の出来事が JSON でない: {}" error)))
      retry)
    (finally
      (setv (get watching.held 0) None))))


(defk tell-unexpected [watching error]
  {:pre [(: watching WatchPlace) (: error Exception)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "見張りの thread の中で上がった思わぬ例外を、止められていなければ理由として伝え、traceback を stderr に 1 つ出すため(黙って
   止まらない — 頭の註。理由は観測の表で「読めない」になり、Rollout はその理由を名指して Unknown と扱う)。"
  (when (not (.is-set watching.stopped))
    (print (.format "coordinator: k8s の {} {} の見張りで思わぬ例外 — 理由を伝え、{} 秒の後に list し直します"
                    watching.kind watching.key watching.settings.retry-seconds)
           :file sys.stderr :flush True)
    (traceback.print-exception error :file sys.stderr)
    (<- (tell-error watching (.format "k8s の {} の見張りで思わぬ例外: {}: {}" watching.kind (. (type error) __name__) error))))
  None)


(defk watch-run [watching]
  {:pre [(: watching WatchPlace)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "止められるまで list と watch を繰り返すため(daemon の thread の本体)。思わぬ例外でも抜けず、理由を伝えて retry-seconds の後に list し
   直す(tell-unexpected)。抜けるのは止められた時だけで、その時に自分の client を閉じる。"
  (var version None)
  (try
    (while (not (.is-set watching.stopped))
      (try
        (if (is version None)
            (do (<- listed (| str None) (list-once watching))
                (:= version listed)
                (when (is version None)
                  (.wait watching.stopped watching.settings.retry-seconds)))
            (do (<- upcoming WatchNext (watch-once watching version))
                (:= version upcoming.version)
                (when (> upcoming.pause 0)
                  (.wait watching.stopped upcoming.pause))))
        (except [error Exception]
          (<- (tell-unexpected watching error))
          (:= version None)
          (.wait watching.stopped watching.settings.retry-seconds))))
    (finally
      (.close watching.http)))
  None)


(defk start-watch [watching]
  {:pre [(: watching WatchPlace)] :post [(: % (get Callable #([] None)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "見張り 1 つの watch-run を daemon の thread で回し始め、止める関数を返すため(follow と follow-node の共通の始め)。"
  ;; thread の target と止める関数は外の library が呼ぶ素の関数 — 中身は defk を新しい VM で走らせる。
  (.start (threading.Thread :target (fn [] (run-detached (watch-run watching))) :name "kube-watch" :daemon True))
  (fn [] (run-detached (stop-watch watching))))


(defk follow-deployment [settings namespace name on-body on-error]
  {:pre [(: settings KubeSettings) (: namespace str) (: name str) (: on-body (get Callable #([OpaqueJson] None)))
         (: on-error (get Callable #([str] None)))]
   :post [(: % (get Callable #([] None)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "Deployment 1 つを list の後の watch で見張る daemon の thread(名 kube-watch)を始め、止める関数を返すため(時間で読みに行かず、
   変化の出来事で伝える — #3868)。on-body = Deployment の object(list の今と、ADDED・MODIFIED の出来事)・on-error = 無い・消された・
   届かない・断られた・読めない理由。どちらも thread の中から呼ぶ。止めた後はどちらも呼ばない。"
  (<- url str (deployments-url settings namespace))
  (<- watching WatchPlace (watch-place settings url name "Deployment" (+ namespace "/" name) on-body on-error))
  (<- stop (get Callable #([] None)) (start-watch watching))
  stop)


(defk follow-node-object [settings name on-body on-error]
  {:pre [(: settings KubeSettings) (: name str) (: on-body (get Callable #([OpaqueJson] None))) (: on-error (get Callable #([str] None)))]
   :post [(: % (get Callable #([] None)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "Node 1 つを list の後の watch で見張る daemon の thread(名 kube-watch)を始め、止める関数を返すため(worker の置かれた node の label を
   時間で読みに行かず、変化の出来事で伝える — #4070)。on-body = Node の object・on-error = 無い・消された・届かない・断られた・読めない
   理由(follow-deployment と同じ)。"
  (<- url str (nodes-url settings))
  (<- watching WatchPlace (watch-place settings url name "Node" name on-body on-error))
  (<- stop (get Callable #([] None)) (start-watch watching))
  stop)


(defk kube-client [fail [base API-URL] [sa-dir SA-DIR] [timeout 5.0] [transport None] [retry-seconds 10.0] [watch-seconds 60]]
  {:pre [(: fail (get Callable #([str] Exception))) (: base str) (: sa-dir str) (: timeout float) (: transport (| httpx.BaseTransport None))
         (: retry-seconds float) (: watch-seconds int)]
   :post [(: % KubeClient)] :tags {:context "doeff-cluster" :role "foundation"}}
  "k8s の API の client を作るため。fail = 届かない・2xx でない時に、理由の文を渡して投げる例外の型。transport = 検が渡す偽の API
   (None = Pod の中の本物の API へ、TCP の keepalive を付けて繋ぐ)。本体の thread の要求の接続の pool はここで 1 つ作る(見張りは自分の pool を作る)。"
  (<- pool httpx.Client (new-pool sa-dir timeout transport))
  (val settings (KubeSettings :fail fail :base base :sa-dir sa-dir :timeout timeout :transport transport
                              :retry-seconds retry-seconds :watch-seconds watch-seconds :client pool))
  ;; 4 つの口は外の handler と thread が呼ぶ素の関数 — 中身は defk を新しい VM で走らせる。
  (KubeClient :settings settings
              :follow (fn [namespace name on-body on-error] (run-detached (follow-deployment settings namespace name on-body on-error)))
              :follow-node (fn [name on-body on-error] (run-detached (follow-node-object settings name on-body on-error)))
              :scale (fn [namespace name replicas dry-run] (run-detached (scale-deployment settings namespace name replicas dry-run)))
              :annotate (fn [namespace name annotations] (run-detached (annotate-deployment settings namespace name annotations)))))
