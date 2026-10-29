;;; worker の Pod の job の label の付け外し(card ki-a66bb71f4152 — 判断は worker/core/pod_label_rules)。答え手 pod-job-labels は worker の
;;; 拍の宣言の読み ReadDesired を外側(coordinator への口 coordinator_link)へ渡し、その答えの宣言がこの worker に置いた常駐の job を
;;; 自分の Pod の label に写してから、宣言をそのまま返す。拍に乗る(新しい loop も watch も起こさない)— 揃えるのは宣言の job が変わった
;;; 拍と RESYNC-EVERY-MS ごと。
;;;   自分の Pod: 名前空間の Pod を置かれた node(spec.nodeName)で絞って list し、metadata.uid が Pod の uid(downward API の POD_UID)と
;;;     同じ物。patch にも uid を載せる(API server は uid の違う patch を「field is immutable」で断る — 同じ名の別の Pod へ書かない)。
;;;   身元: ServiceAccount の token(回るので要求のたびに読む)。名前空間は同じ dir の namespace の file。API server は区画の CA で
;;;     検める(入口が in-cluster-api-routed を HTTP の答え手のすぐ内側に並べる)。
;;;   失敗しても job を止めない: token が読めない・届かない・断られた・形の違う答え・思わぬ例外は、訳の変わり目だけを log に 1 行出し、
;;;     RETRY-FIRST-MS から倍にして待ってから試し直す。宣言は ReadDesired の答えのまま返す。
;;;   世代: 同じ Pod に同じ名の 2 つの世代が並ぶ間(keeper の版の入れ替え — deploy/boot.sh)、退いた世代(返事の superseded)は label に
;;;     触らない。新しい世代の返事は、前の世代がまだ動かしている job も載せる(印 elder-runs の job・handoff の job)ので、新しい世代だけが
;;;     書けば Pod の label は Pod の中で動く job を覆う。1 度退いた世代は、途絶で絞った宣言(superseded を持たない)の拍にも書かない。
;;;   宣言が読めない拍(DesiredUnreadable)は揃えない(今の label のまま)。途絶で絞った宣言(cut-off)は絞った job に揃える(止めた job の
;;;     label を外す)。
;;; I/O は汎用の効果(file の ReadText・HttpRequest・slog)で出す。本物の答え手は入口が並べる offloaded-file-handler と
;;; in-cluster-api-routed + http-production-handler、検は偽の API server(httpx の MockTransport)。
(require doeff-hy.macros [defhandler defk <- val])
(require doeff-hy.record [defrecord defwire])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])  ; defrecord・defwire の展開が名指す
(import json)
(import urllib.parse [quote :as url-quote])
(import doeff_hy._wire [Malformed parse-json])
(import doeff_core_effects [slog])
(import doeff_core_effects [FileFailed ReadText])
(import doeff_core_effects.http_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_cluster.shared.core.clock [now-epoch-ms])
(import doeff_cluster.worker.core.pod_label_rules [JobLabels LabelChange PodLabel job-labels-wanted job-labels-patch labels-sync-due
                                                   retry-after-ms labels-line])
(import doeff_cluster.worker.intent.worker_model [DesiredJobs DesiredUnreadable ReadDesired])

;; API server の断りの本文を訳に載せる長さと、要求 1 本の時間切れ(秒 — 拍が API server の答えを待つ上限)。
(val REFUSAL-BODY-LIMIT 300)
(val REQUEST-TIMEOUT-SECONDS 5.0)


(defrecord PodLabelSettings
  "答え手 pod-job-labels の設定(入口が起動の時に 1 度だけ作る): api = 自区画の API server の URL・token-file = ServiceAccount の token の
   file・namespace-file = 同じ dir の名前空間の file・node = この worker の Pod が置かれた node の名(list の絞り)・pod-uid = この Pod の
   metadata.uid(downward API の POD_UID — 自分の Pod を見分ける)。"
  (#^ str api)
  (#^ str token-file)
  (#^ str namespace-file)
  (#^ str node)
  (#^ str pod-uid))


(defclass PodLabelCell []
  "答え手 pod-job-labels が拍から拍へ持ち越す値の入れ物(入口が作って渡し、検は同じ入れ物を外から見る)。superseded = この世代が退いた
   (1 度真になれば戻らない)・synced-wanted / synced-ms = 最後に揃えた時の求める label と刻(None = まだ揃えていない)・retry-at-ms = 失敗の
   後に次に試す刻(None = 失敗していない)・failures = 続けて失敗した数・failure = 最後の失敗の訳(\"\" = 揃った)・told-failure = log に
   出した最後の訳・told-unlabelled = log に出した label に成らない job の名の組。"
  (defn #^ None __init__ [self]
    (setv self.superseded False self.synced-wanted None self.synced-ms None self.retry-at-ms None self.failures 0 self.failure ""
          self.told-failure "" self.told-unlabelled #())))


(defwire PodMetaWire
  "Pod の list の答えの行の metadata のうち読む欄: name・uid・labels(k8s は空の欄を省くので、無ければ None)。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str name)
  (#^ str uid)
  (setv #^ (| (get dict #(str str)) None) labels None))


(defwire PodWire
  "Pod の list の答えの行のうち読む欄: metadata。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ PodMetaWire metadata))


(defwire PodListWire
  "Pod の list(GET /api/v1/namespaces/<名前空間>/pods)の答えの本文のうち読む欄: items = Pod の行の列(k8s は空の列を省きうる)。"
  {:tags {:context "worker" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(PodWire ...)) None) items None))


(defrecord OwnPod
  "list から見つけたこの worker の Pod: name = Pod の名(patch の宛先)・labels = 今の label(全部 — 鍵の順)。"
  (#^ str name)
  (#^ (get tuple #(PodLabel ...)) labels))


(defk refusal-of [answer]
  {:pre [(: answer (| HttpResponse HttpFailed))] :post [(: % (| str None))] :tags {:context "worker" :role "protocol"}}
  "API server の答えが 2xx でなければその訳(届かない・status と本文の先頭)を返すため。2xx なら None。"
  (match answer
    (HttpFailed) (+ "API server に届かない: " answer.detail)
    _ (if (<= 200 answer.status 299)
          None
          (.format "API server が {} で断った: {}" answer.status (cut answer.text 0 REFUSAL-BODY-LIMIT)))))


(defk own-pod-of [text uid]
  {:pre [(: text str) (: uid str)] :post [(: % (| OwnPod str))] :tags {:context "worker" :role "protocol" :reads "json"}}
  "Pod の list の答えの本文から、metadata.uid が uid の Pod を見つけるため。本文の形が違う・在らない時は訳の文。"
  (<- parsed (| PodListWire Malformed) (parse-json PodListWire text))
  (when (isinstance parsed Malformed)
    (return "Pod の list の答えの本文の形が違う"))
  (val found (lfor pod (or parsed.items #()) :if (= pod.metadata.uid uid) pod.metadata))
  (if found
      (OwnPod :name (. (get found 0) name)
              :labels (tuple (gfor #(key value) (sorted (.items (or (. (get found 0) labels) {}))) (PodLabel :key key :value value))))
      (.format "この Pod(uid {})が list に無い" uid)))


(defk patch-body [uid changes]
  {:pre [(: uid str) (: changes (get tuple #(LabelChange ...)))] :post [(: % bytes)] :tags {:context "worker" :role "protocol" :spells "json"}}
  "Pod の label の付け外しを、API server の merge patch の本文に綴るため(uid を載せる — uid の違う Pod へは API server が断る。外す
   label は null)。"
  (.encode (json.dumps {"metadata" {"uid" uid "labels" (dfor change changes change.key change.value)}} :ensure-ascii False) "utf-8"))


(defk account-text [path what]
  {:pre [(: path str) (: what str)] :post [(: % str)] :tags {:context "worker" :role "protocol"}}
  "ServiceAccount の dir の file 1 つ(what = 訳に載せる名)の中身を、前後の空白を落として読むため。読めない・空なら ValueError で訳を
   挙げる(呼び手 labels-synced が訳にする)。"
  (<- read (ReadText path))
  (when (isinstance read FileFailed)
    (raise (ValueError (.format "ServiceAccount の {} の file が読めない: {}" what read.detail))))
  (val text (.strip read))
  (when (not text)
    (raise (ValueError (.format "ServiceAccount の {} の file が空" what))))
  text)


(defk labels-synced [settings wanted]
  {:pre [(: settings PodLabelSettings) (: wanted frozenset)] :post [(: % (| tuple str))] :tags {:context "worker" :role "protocol" :spells "http"}}
  "自分の Pod の job の label を wanted(鍵の組)に揃えるため: Pod を list で見つけ、今の label との差の merge patch を当てる(差が無ければ
   当てない)。答え = 当てた付け外しの列(LabelChange — 空 = 揃っていた)か、揃えられなかった訳の文。思わぬ例外も訳にする(job を止めない)。"
  (try
    (<- token str (account-text settings.token-file "token"))
    (<- namespace str (account-text settings.namespace-file "namespace"))
    (val pods-url (+ settings.api "/api/v1/namespaces/" (url-quote namespace :safe "") "/pods"))
    (val headers {"authorization" (+ "Bearer " token) "accept" "application/json"})
    (<- listed (| HttpResponse HttpFailed)
        (HttpRequest "GET" pods-url :headers headers :params {"fieldSelector" (+ "spec.nodeName=" settings.node)}
                     :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
    (<- list-refused (| str None) (refusal-of listed))
    (when (is-not list-refused None)
      (return list-refused))
    (<- pod (| OwnPod str) (own-pod-of listed.text settings.pod-uid))
    (when (isinstance pod str)
      (return pod))
    (<- changes (get tuple #(LabelChange ...)) (job-labels-patch pod.labels wanted))
    (when (not changes)
      (return changes))
    (<- body bytes (patch-body settings.pod-uid changes))
    (<- patched (| HttpResponse HttpFailed)
        (HttpRequest "PATCH" (+ pods-url "/" (url-quote pod.name :safe "")) :body body
                     :headers (| headers {"content-type" "application/merge-patch+json"})
                     :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
    (<- patch-refused (| str None) (refusal-of patched))
    (if (is patch-refused None) changes patch-refused)
    (except [error Exception]
      (repr error))))


(defk outcome-noted [cell wanted outcome now-ms]
  {:pre [(: cell PodLabelCell) (: wanted frozenset) (: outcome (| tuple str)) (: now-ms int)] :post [(: % None)]}
  "揃えの答え outcome を入れ物に書き、label を付け外しした時・失敗の訳が変わった時・戻った時に log へ 1 行出すため。失敗の後は
   retry-after-ms だけ待つ刻を置く。"
  (if (isinstance outcome tuple)
      (do (setv cell.synced-wanted wanted cell.synced-ms now-ms cell.retry-at-ms None cell.failures 0 cell.failure "")
          (when outcome
            (<- line str (labels-line outcome))
            (<- (slog line :level "info"))))
      (do (setv cell.failures (+ cell.failures 1) cell.failure outcome)
          (<- wait int (retry-after-ms cell.failures))
          (setv cell.retry-at-ms (+ now-ms wait))))
  (when (!= cell.failure cell.told-failure)
    (setv cell.told-failure cell.failure)
    (<- (slog (if cell.failure
                  (+ "worker: Pod の job の label を揃えられない(job は止めない — 間を空けて試し直す): " cell.failure)
                  "worker: Pod の job の label を揃えられるようになりました")
              :level (if cell.failure "warning" "info"))))
  None)


(defk labels-noted [settings cell desired]
  {:pre [(: settings PodLabelSettings) (: cell PodLabelCell) (: desired (| DesiredJobs DesiredUnreadable))] :post [(: % None)]}
  "拍の宣言 desired の常駐の job を、揃える拍(labels-sync-due)なら自分の Pod の label に写すため(頭の註)。退いた世代と、宣言が読めない
   拍は揃えない。label の名に成らない job の名は、組が変わった時に log へ 1 行出す。"
  (when (and (isinstance desired DesiredJobs) desired.superseded)
    (setv cell.superseded True))
  (when (and (isinstance desired DesiredJobs) (not cell.superseded))
    (<- labels JobLabels (job-labels-wanted desired.jobs))
    (when (!= labels.unlabelled cell.told-unlabelled)
      (setv cell.told-unlabelled labels.unlabelled)
      (when labels.unlabelled
        (<- (slog (+ "worker: label の名に成らない job の名は Pod の label にしない: " (.join "・" labels.unlabelled)) :level "warning"))))
    (<- now-ms int (now-epoch-ms))
    (<- due bool (labels-sync-due labels.wanted cell.synced-wanted cell.synced-ms cell.retry-at-ms now-ms))
    (when due
      (<- outcome (| tuple str) (labels-synced settings labels.wanted))
      (<- (outcome-noted cell labels.wanted outcome now-ms))))
  None)


(defhandler pod-job-labels [#^ PodLabelSettings settings #^ PodLabelCell cell]
  "worker の拍の宣言の読み ReadDesired を外側の答え手へ渡し、答えの宣言の常駐の job を自分の Pod の label に写してから、答えをそのまま
   返す(頭の註)。"
  {:tags {:context "worker" :role "protocol"}}
  ;; 引数に残す理由: 設定は起動の時に 1 度だけ決まる Pod の事実・入れ物は拍から拍へ持ち越す値(組み立てが作り、検が外から見る)。
  (ReadDesired [env-report stopping]
    (<- desired (| DesiredJobs DesiredUnreadable) effect)
    (<- (labels-noted settings cell desired))
    (resume desired)))
