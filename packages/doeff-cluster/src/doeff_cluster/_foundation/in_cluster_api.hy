;;; k8s の Pod の中から自分の区画の API server を呼ぶ土台(job が要る値を参照で受ける部品 doeff_cluster.shared.protocol.supplied_values の
;;; 要求を、区画の CA で検める接続へ回す)。
;;;
;;;   SERVICE-ACCOUNT-CA-PATH     k8s が Pod に必ず置く ServiceAccount の dir の区画の CA の file
;;;   CLUSTER-API                 Pod の中から見た自区画の API server
;;;   in-cluster-api-connected    本体を、区画の CA で API server を検める HTTP の client(環境の proxy を読まない)の下で走らせる土台の口
;;;   cluster-ca-answer           要求 1 つに、上の口の client で答える(CA の file が無い worker では要求を出さずに断る)
;;;   in-cluster-api-routed       宛先が自区画の API server の HttpRequest だけに cluster-ca-answer で答える答え手。ほかの宛先の要求は外側へ流す
;;;   ClusterCaMissing            API server 宛ての要求を、区画の CA の file が無い worker で止めた(要求を出していない)
;;;
;;; 宛先での振り分け: job は値を worker の身元で API server から読み、同じ本体が外の系(git の hosting など)へも HTTPS を出す。本体を丸ごと
;;; in-cluster-api-connected で包むと外の系の要求まで区画の CA で検めて落ちるので、使い手の process の外側が in-cluster-api-routed を HTTP の
;;; 答え手のすぐ内側に 1 つ並べ、宛先(scheme・host・port)が API server の要求だけを区画の CA で検める。ほかの要求は外側の既定の CA の client が
;;; 答える。CA の file が無い worker(cluster の Pod でない — 機体の上の worker・手元の CLI)では、API server 宛ての要求を外側へ流さず、CA の
;;; file の path を挙げて断る(既定の CA で検め直す道を作らない)。port の読めない URL は API server でないとして外側へ流す(外側の client が
;;; 今までどおり断る)。
;;; API server 宛ての要求は、要求ごとに区画の CA の client を作って閉じる(接続を持ち越さない — 量は job の起動の時の数本)。
(require doeff-hy.macros [defhandler defk <- val])
(import os)
(import ssl)
(import urllib.parse [SplitResult urlsplit])
(import doeff [Program EffectBase with-handlers])
(import doeff_core_effects.http_effects [HttpFailed HttpFailureKind HttpRequest])
(import doeff_core_effects.http_handlers [http-production-handler http-client-factory])

(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})

;; Pod の中の ServiceAccount の CA(k8s が必ず置く path)。
(val SERVICE-ACCOUNT-CA-PATH "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt")
;; Pod の中から見た自区画の API server(k8s が置く名)。値を読む部品が要求を出す宛先 doeff_cluster.shared.protocol.supplied_values の KUBE-API と
;; 同じ字(層 foundation は protocol を import できないので 2 か所に在る — 同じ字である事は tests/test_supplied_values.hy が縛る)。
(val CLUSTER-API "https://kubernetes.default.svc")
;; API server 宛ての要求を、CA の file が無い worker で止めた時の文(宛先と CA の file の path を挙げる)。
(val CA-MISSING "宛先 {} は自区画の API server — 区画の CA の file {} が無い(この worker は ServiceAccount の dir を持たない — cluster の Pod でない)ので要求を出していない")


(defk in-cluster-api-connected [ca-path body]
  {:pre [(: ca-path str) (: body (| Program EffectBase))] :post [(: % "body の答え")] :tags {:context "doeff-cluster" :role "foundation"}}
  "本体を、区画の CA(ca-path の file)で API server を検める HTTP の client の下で走らせるため(頭の註)。CA の file は呼ぶたびに読む。"
  (val cluster-ca (ssl.create-default-context :cafile ca-path))
  (<- answer (with-handlers [(http-production-handler :client-factory (fn [] (http-client-factory :verify cluster-ca :trust-env False)))]
                            body))
  answer)


(defclass ClusterCaMissing [FileNotFoundError]
  "API server 宛ての要求を、区画の CA の file が無い worker で、失敗を値で受けない呼び手へ止めた(要求を出していない)— 宛先と CA の file の path を名指す。"
  (defn #^ None __init__ [self #^ str url #^ str ca-path]  ; defk にできない: 例外の型を作る時に Python が呼ぶ口(__init__)
    (.__init__ (super) (.format CA-MISSING url ca-path))
    (setv self.url url
          self.ca-path ca-path)))


(defk effective-port [parts]
  {:pre [(: parts SplitResult)] :post [(: % (| int None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "URL の口の port を、書き省いた時は scheme の既定の port として得るため(https://h と https://h:443 を同じ口と判じる)。"
  (or parts.port (match parts.scheme "https" 443 "http" 80 _ None)))


(defk readable-port? [parts]
  {:pre [(: parts SplitResult)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "URL の口の port が読めるか(数で、範囲の内か)を判じるため — 読めない URL は API server でないとして外側の client へ流す(頭の註)。"
  (try
    parts.port
    True
    (except [ValueError]
      False)))


(defk same-origin? [url api]
  {:pre [(: url str) (: api str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "要求の宛先 url が API server api と同じ口(scheme・host・port)かを判じるため — 似た名の host(<api の host>.example.com)・http の綴り・
   別の port・port の読めない URL は API server でない(scheme と host の大文字・小文字は区別しない — urlsplit が小文字にする)。"
  (val target (urlsplit url))
  (val base (urlsplit api))
  (<- readable bool (readable-port? target))
  (when (not readable)
    (return False))
  (<- target-port (| int None) (effective-port target))
  (<- base-port (| int None) (effective-port base))
  (and (= target.scheme base.scheme) (= target.hostname base.hostname) (= target-port base-port)))


(defk cluster-ca-answer [ca-path request]
  {:pre [(: ca-path str) (: request HttpRequest)] :post [(: % "request の答え(HttpResponse か HttpFailed)")]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "API server 宛ての要求 request に、区画の CA(ca-path の file)で検める client で答えるため。CA の file が無い worker では要求を出さず、
   失敗を値で受ける要求には HttpFailed(繋がらなかった)・そうでなければ ClusterCaMissing で断る(外側の既定の CA の client へ流さない)。"
  (when (not (os.path.isfile ca-path))
    (if request.failures-as-values
        (return (HttpFailed :url request.url :detail (.format CA-MISSING request.url ca-path) :kind HttpFailureKind.CONNECT-FAILED))
        (raise (ClusterCaMissing request.url ca-path))))
  (<- answer (in-cluster-api-connected ca-path request))
  answer)


(defk routed-answer [api ca-path request]
  {:pre [(: api str) (: ca-path str) (: request HttpRequest)] :post [(: % "request の答え(HttpResponse か HttpFailed)")]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "要求 request の宛先が API server api なら区画の CA(ca-path の file)で検める client で答え、そうでなければ外側の答え手(既定の CA の
   client)へそのまま渡し直して、その答えを得るため(頭の註の「宛先での振り分け」)。"
  (<- api-bound bool (same-origin? request.url api))
  (when (not api-bound)
    (<- delegated request)
    (return delegated))
  (<- answer (cluster-ca-answer ca-path request))
  answer)


(defhandler in-cluster-api-routed [#^ str api #^ str ca-path]
  "宛先が自区画の API server(api)の HttpRequest だけに、区画の CA(ca-path の file)で検める client で答える(頭の註の「宛先での振り分け」)。
   ほかの宛先の要求は、外側の答え手(既定の CA の client)へそのまま渡し直す。"
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 引数に残す理由: 宛先と CA の file は組み立てごとに違う外の世界の形(本番 = 自区画の API server と k8s が Pod に置く CA の file・検 =
  ;; 在らない file と閉じた口)で、Ask で運ぶ設定ではない。
  (HttpRequest []
    (<- answer (routed-answer api ca-path effect))
    (resume answer)))
