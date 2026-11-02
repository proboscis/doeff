;;; 持ち主ごとの作業場所の volume の effect(型は doeff_cluster._shared.intent.workspace_volume_model)の本番の答え手 — その job を回す
;;; worker の身元(ServiceAccount の token)で自区画の API server の Longhorn の物(Volume・VolumeAttachment)を読み書きし、mount と umount は
;;; 同じ Pod の mount の係(sidecar workspace-mount)へ頼む。決まりは deploy/k8s/workspace-volumes/README.md。
;;;
;;;   WORKSPACE-SITE          本番の場所と待つ秒(Longhorn の namespace・値の ConfigMap・係の頼みの dir — zeus の node.yaml の emptyDir と
;;;                           同じ path・mountinfo・上限の秒)
;;;   volume-create-text      volume の名・級の parameters・大きさ → Longhorn の Volume の作成の本文の JSON の字(級の値の写し — README の表)
;;;   created-volume          CreateWorkspaceVolume: 値の ConfigMap と級(StorageClass)を読み、Volume を POST(201 = 作った・409 = 在った)
;;;   attached-volume         AttachWorkspaceVolume: VolumeAttachment に ticket を置き、ticket が満たされ volume がこの node に付くまで待つ
;;;   mounted-volume          MountWorkspaceVolume: 付ける先の dir を作り、係へ mount を頼む(既に付いていれば頼まない)
;;;   unmounted-volume        UmountWorkspaceVolume: 係へ umount を頼む(何も付いていなければ頼まない)
;;;   detached-volume         DetachWorkspaceVolume: mount が残っていないかを確かめ、ticket を外し、volume がこの node から外れるまで待つ
;;;   workspace-volumes-api   5 つの effect の本番の答え手
;;;
;;; 仕組み: supplied_values・kube_services と同じく、doeff の汎用の effect(token の file の ReadText・HttpRequest・file の effect・doeff-time の
;;; Delay と GetMonotonic・機体の事実の ReadEnvironment)で問う。外側に、宛先が API server の要求を区画の CA で検める接続
;;; (doeff_cluster._foundation.in_cluster_api の in-cluster-api-routed)と file・時計・環境の答え手を要る(job の本番の土台)。
;;; 機体の node は機体の事実 NODE_NAME(worker が子の process へ渡す名)。
;;;
;;; 係への頼み(README の節「mount の係」): 頼みの dir に `<鍵>.tmp` を書いて `<鍵>.req` へ名を替え、`<鍵>.out`(1 行目 = 0 か 1・2 行目から
;;; 訳)を待って読み、消す。鍵 = <命令>-<volume の名>-<時計の μ 秒>。係の無い worker(頼みの dir が無い)は頼む前に断る。
;;;
;;; 断る物(WorkspaceVolumeRefused — 持ち主の id・volume の名・何をしようとしたか・理由): 名の条件を外れる id(作る前に)・無い volume の
;;; attach・別の node に ticket の在る volume の attach と detach・上限の秒の内に付かない・外れない・係が答えない・係が断った・mount の
;;; 残る volume の detach・付ける先に別の物・API server に届かない・断られた・token が読めない・答えの形が違う。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])  ; defrecord・defwire の展開が使う
(import json)
(import posixpath)
(import urllib.parse [quote :as url-quote])
(import doeff_hy._wire [Malformed parse-json parse-json-as])
(import doeff_core_effects [FileFailed MakeDirectory PathKind PathStat ReadText RemoveTree RenamePath StatPath WriteText])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_time [Delay GetMonotonic])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume UmountWorkspaceVolume WorkspaceOutcome
                                                           WorkspaceVolumeRefused WorkspaceVolumeSite])
(import doeff_cluster._shared.intent.supplied_model [WorkerFactName])
(import doeff_cluster._shared.core.workspace_volume_rules [AttachStep AttachView DetachStep MountEntry attach-step detach-step
                                                         mount-entries mounted-at owner-refusal ticket-id TICKET-TYPE
                                                         volume-mount-points volume-name volume-of-entry])
(import doeff_cluster._shared.protocol.supplied_values [KUBE-API REFUSAL-BODY-LIMIT REQUEST-TIMEOUT-SECONDS SERVICE-ACCOUNT-TOKEN-FILE
                                                      kube-headers service-account-text status-field text-field worker-fact])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})

;; 本番の場所と待つ秒。mount-dir は zeus の node.yaml の頼みの dir(emptyDir の workspace-mount の mountPath)と同じ path — 検が縛る。
;; 待つ秒: attach は replica の engine が起きるまで(測りでは数秒)、係は空の 10 GiB の volume の mkfs.ext4 を含む。
(val WORKSPACE-SITE (WorkspaceVolumeSite :longhorn-namespace "longhorn-system"
                                         :settings-namespace "agent-worker"
                                         :settings-name "workspace-volume"
                                         :mount-dir "/run/workspace-mount"
                                         :mountinfo "/proc/self/mountinfo"
                                         :attach-seconds 180.0
                                         :detach-seconds 180.0
                                         :mount-seconds 180.0
                                         :poll-seconds 0.5))
;; Longhorn の物の API の頭(longhorn.io/v1beta2 — cluster の Longhorn v1.13 の版)。
(val LONGHORN-API "/apis/longhorn.io/v1beta2")
;; 値の ConfigMap の鍵(kustomization の configMapGenerator が file の名をそのまま鍵にする)。
(val SETTINGS-KEY "workspace-volume.json")
(val JSON-TYPE "application/json")
(val MERGE-PATCH-TYPE "application/merge-patch+json")
;; 級の parameters のうち、volume の欄へ写す物(README の写し方の表)。ほかの parameter は黙って捨てずに断る(級を替えた時に写しを足す)。
(val CLASS-PARAMETERS (frozenset #("numberOfReplicas" "diskSelector" "dataLocality" "staleReplicaTimeout" "backupTargetName"
                                   "recurringJobSelector" "fsType")))
;; 係が作れる file system(mkfs.ext4 だけ)。
(val CLASS-FS-TYPE "ext4")


(defwire SettingsWire
  "値の ConfigMap の workspace-volume.json のうち、作る時に読む欄: storageClass = 級の名・sizeBytes = 大きさ(byte)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str storage-class)
  (#^ int size-bytes))


(defwire ConfigMapWire
  "ConfigMap の GET の答えのうち読む欄: data(鍵 → 字。k8s は空の欄を省くので、無ければ None)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str str)) None) data None))


(defwire StorageClassWire
  "StorageClass の GET の答えのうち読む欄: parameters(名 → 字)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str str)) None) parameters None))


(defwire RecurringSelectorWire
  "級の parameter recurringJobSelector(JSON の列)の 1 つ: name = RecurringJob か群の名・isGroup = 群か。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :reject}
  (#^ str name)
  (setv #^ bool is-group False))


(defwire VolumeStatusWire
  "Longhorn の Volume の status のうち読む欄: state(creating・detached・attaching・attached・detaching ほか)・currentNodeID。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names {state "state" current-node-id "currentNodeID"} :unknown :ignore}
  (setv #^ str state "")
  (setv #^ str current-node-id ""))


(defwire VolumeWire
  "Longhorn の Volume の GET の答えのうち読む欄: status(作った直後は無い)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| VolumeStatusWire None) status None))


(defwire TicketWire
  "VolumeAttachment の spec の ticket 1 つのうち読む欄: nodeID(付ける node)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names {node-id "nodeID"} :unknown :ignore}
  (setv #^ str node-id ""))


(defwire TicketConditionWire
  "VolumeAttachment の ticket の status の condition 1 つのうち読む欄: type・message。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str type "")
  (setv #^ str message ""))


(defwire TicketStatusWire
  "VolumeAttachment の ticket の status 1 つのうち読む欄: satisfied・conditions。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ bool satisfied False)
  (setv #^ (| (get tuple #(TicketConditionWire ...)) None) conditions None))


(defwire AttachmentMetaWire
  "VolumeAttachment の metadata のうち読む欄: resourceVersion(ticket の書きを、読んだ版の上にだけ当てる)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str resource-version))


(defwire AttachmentSpecWire
  "VolumeAttachment の spec のうち読む欄: attachmentTickets(ticket の id → ticket)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str TicketWire)) None) attachment-tickets None))


(defwire AttachmentStatusWire
  "VolumeAttachment の status のうち読む欄: attachmentTicketStatuses(ticket の id → status)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str TicketStatusWire)) None) attachment-ticket-statuses None))


(defwire AttachmentWire
  "Longhorn の VolumeAttachment(volume と同じ名)の GET の答えのうち読む欄: metadata・spec・status。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ AttachmentMetaWire metadata)
  (setv #^ (| AttachmentSpecWire None) spec None)
  (setv #^ (| AttachmentStatusWire None) status None))


(defrecord AttachRead
  "volume 1 つの attach の読み: view = 判断に渡す様子・version = VolumeAttachment の resourceVersion(無ければ空)。"
  (#^ AttachView view)
  (#^ str version))


(defrecord KeeperAnswer
  "mount の係の答え 1 つ: code = 0(した・既にそうだった)か 1(断った・失敗した)・notes = 2 行目からの訳と命令の出力。"
  (#^ int code)
  (#^ str notes))


(defk refused [owner doing reason]
  {:pre [(: owner str) (: doing str) (: reason str)] :post [(: % WorkspaceVolumeRefused)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume を doing(何をしようとしたか)できなかった断りを、volume の名(名に成る id の時)と理由を挙げて作るため。"
  (<- refusal (| str None) (owner-refusal owner))
  (val place (if (is refusal None) (+ "volume " (! (volume-name owner))) "volume"))
  (WorkspaceVolumeRefused (.format "持ち主 {} の {} を {} できない — {}" owner place doing reason)))


(defk checked-name [owner doing]
  {:pre [(: owner str) (: doing str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主の id owner の volume の名を得るため。名の条件を外れる id は、何もせずに理由を挙げて断る(縮めた名や重なった名を作らない)。"
  (<- refusal (| str None) (owner-refusal owner))
  (when (is-not refusal None)
    (raise (! (refused owner doing refusal))))
  (! (volume-name owner)))


(defk kube-answer [owner doing method path body content-type]
  {:pre [(: owner str) (: doing str) (: method str) (: path str) (: body (| str None)) (: content-type str)] :post [(: % HttpResponse)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "API server の path へ method の要求を 1 本出し、答えを得るため(身元は worker の ServiceAccount の token — 回るので要求のたびに読む)。
   token が読めない・API server に届かない時は理由を挙げて断る(status は呼び手が読む)。"
  (<- token (| str FileFailed) (service-account-text SERVICE-ACCOUNT-TOKEN-FILE))
  (when (isinstance token FileFailed)
    (raise (! (refused owner doing (+ "ServiceAccount の token の file が読めない: " token.detail)))))
  (<- headers (get dict #(str str)) (kube-headers token content-type))
  (<- answer (| HttpResponse HttpFailed)
      (HttpRequest method (+ KUBE-API path) :headers headers :body body
                   :timeout-seconds REQUEST-TIMEOUT-SECONDS :max-retries 0 :failures-as-values True))
  (when (isinstance answer HttpFailed)
    (raise (! (refused owner doing (+ "API server に届かない: " (! (text-field answer.detail)))))))
  answer)


(defk status-refusal [owner doing what answer]
  {:pre [(: owner str) (: doing str) (: what str) (: answer HttpResponse)] :post [(: % WorkspaceVolumeRefused)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "API server が what(何の要求か)を思わぬ status で答えた断りを、status と本文の先頭を挙げて作るため。"
  (! (refused owner doing (.format "{} に API server が {} で答えた: {}" what (! (status-field answer.status))
                                   (cut (! (text-field answer.text)) 0 REFUSAL-BODY-LIMIT)))))


(defk malformed-refusal [owner doing what]
  {:pre [(: owner str) (: doing str) (: what str)] :post [(: % WorkspaceVolumeRefused)] :tags {:context "doeff-cluster" :role "protocol"}}
  "API server の what の答えの本文の形が違った断りを作るため。"
  (! (refused owner doing (+ what " の答えの本文の形が違う"))))


(defk volume-path [site name]
  {:pre [(: site WorkspaceVolumeSite) (: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "volume の名 name の Longhorn の Volume の、API server の上の path を組むため。"
  (+ LONGHORN-API "/namespaces/" (url-quote site.longhorn-namespace :safe "") "/volumes/" (url-quote name :safe "")))


(defk attachment-path [site name]
  {:pre [(: site WorkspaceVolumeSite) (: name str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "volume の名 name の Longhorn の VolumeAttachment(volume と同じ名)の、API server の上の path を組むため。"
  (+ LONGHORN-API "/namespaces/" (url-quote site.longhorn-namespace :safe "") "/volumeattachments/" (url-quote name :safe "")))


(defk workspace-settings [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % SettingsWire)] :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "値の ConfigMap(site の settings-namespace と settings-name)の workspace-volume.json を読むため(読めない・無い・形が違えば断る)。"
  (val doing "作成")
  (val what (+ "ConfigMap " site.settings-namespace "/" site.settings-name))
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ "/api/v1/namespaces/" (url-quote site.settings-namespace :safe "")
                                                             "/configmaps/" (url-quote site.settings-name :safe ""))
                                       None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing (+ what " の GET") answer))))
  (<- held (| ConfigMapWire Malformed) (parse-json ConfigMapWire (! (text-field answer.text))))
  (when (or (isinstance held Malformed) (not-in SETTINGS-KEY (or held.data {})))
    (raise (! (malformed-refusal owner doing (+ what " の鍵 " SETTINGS-KEY)))))
  (<- settings (| SettingsWire Malformed) (parse-json SettingsWire (get held.data SETTINGS-KEY)))
  (when (isinstance settings Malformed)
    (raise (! (malformed-refusal owner doing (+ what " の " SETTINGS-KEY)))))
  settings)


(defk class-parameters [owner class-name]
  {:pre [(: owner str) (: class-name str)] :post [(: % (get dict #(str str)))] :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "級 class-name(StorageClass)の parameters を読むため(読めない・無い・形が違えば断る)。"
  (val doing "作成")
  (val what (+ "StorageClass " class-name))
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ "/apis/storage.k8s.io/v1/storageclasses/" (url-quote class-name :safe ""))
                                       None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing (+ what " の GET") answer))))
  (<- held (| StorageClassWire Malformed) (parse-json StorageClassWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing what))))
  (dict (or held.parameters {})))


(defk volume-create-text [site name parameters size-bytes]
  {:pre [(: site WorkspaceVolumeSite) (: name str) (: parameters (get dict #(str str))) (: size-bytes int) (> size-bytes 0)]
   :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json" :reads "json"}}
  "volume の名 name の Longhorn の Volume の作成の本文を、級の parameters と大きさ size-bytes から JSON の字へ綴るため(JSON の境界の 1 点 —
   README の写し方の表: replica の数・置く disk の札・locality・stale の分・backup の先は spec の同じ名の欄へ、recurringJobSelector の群は
   label recurring-job-group.longhorn.io/<群> へ、RecurringJob 1 つは label recurring-job.longhorn.io/<名> へ写す。frontend は blockdev・
   access mode は rwo)。写し方の無い parameter・ext4 でない fsType・数でない replica の数と stale の分・形の違う selector は ValueError
   で断る(呼び手が断りの文にする)。"
  (val unknown (sorted (- (set parameters) CLASS-PARAMETERS)))
  (when unknown
    (raise (ValueError (+ "級の parameter に写し方が無い: " (.join "," unknown)))))
  (when (!= (.get parameters "fsType" CLASS-FS-TYPE) CLASS-FS-TYPE)
    (raise (ValueError (+ "級の fsType が " CLASS-FS-TYPE " でない(mount の係は " CLASS-FS-TYPE " だけを作る): " (get parameters "fsType")))))
  (var selectors #())
  (when (in "recurringJobSelector" parameters)
    (<- parsed (| (get tuple #(RecurringSelectorWire ...)) Malformed)
        (parse-json-as (get tuple #(RecurringSelectorWire ...)) (get parameters "recurringJobSelector")))
    (when (isinstance parsed Malformed)
      (raise (ValueError (+ "級の recurringJobSelector の形が違う: " (get parameters "recurringJobSelector")))))
    (:= selectors parsed))
  (val labels (dfor selector selectors
                    (+ (if selector.is-group "recurring-job-group.longhorn.io/" "recurring-job.longhorn.io/") selector.name) "enabled"))
  (var counts {})
  (for [key #("numberOfReplicas" "staleReplicaTimeout")]
    (when (in key parameters)
      (when (not (.isdigit (get parameters key)))
        (raise (ValueError (+ "級の " key " が数でない: " (get parameters key)))))
      (:= counts (| counts {key (int (get parameters key))}))))
  (val texts (dfor key #("dataLocality" "backupTargetName") :if (in key parameters) key (get parameters key)))
  (val disks (if (in "diskSelector" parameters)
                 {"diskSelector" (lfor tag (.split (get parameters "diskSelector") ",") :if (.strip tag) (.strip tag))}
                 {}))
  (json.dumps {"apiVersion" "longhorn.io/v1beta2" "kind" "Volume"
               "metadata" (| {"name" name "namespace" site.longhorn-namespace} (if labels {"labels" labels} {}))
               "spec" (| {"size" (str size-bytes) "frontend" "blockdev" "accessMode" "rwo"} counts texts disks)}
              :ensure-ascii False :sort-keys True))


(defk created-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume が無ければ、値の ConfigMap の級と大きさで作るため(201 = DONE・409 = 在った ALREADY — 中身に触らない。同じ名の
   作成が重なっても、API server が 1 つだけを作る)。"
  (val doing "作成")
  (<- name str (checked-name owner doing))
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (var body "")
  (try
    (<- text str (volume-create-text site name parameters settings.size-bytes))
    (:= body text)
    (except [error ValueError]
      (raise (! (refused owner doing (+ "級 " settings.storage-class " を写せない: " (str error)))))))
  (<- answer HttpResponse (kube-answer owner doing "POST" (+ LONGHORN-API "/namespaces/" (url-quote site.longhorn-namespace :safe "")
                                                              "/volumes")
                                       body JSON-TYPE))
  (match (! (status-field answer.status))
    201 WorkspaceOutcome.DONE
    409 WorkspaceOutcome.ALREADY
    _ (raise (! (status-refusal owner doing "Volume の作成" answer)))))


(defk attach-read [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % (| AttachRead None))]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の attach の様子を、Volume と VolumeAttachment の GET で読むため(volume が無ければ None。VolumeAttachment がまだ無ければ
   attachment False の様子)。"
  (<- volume-answer HttpResponse (kube-answer owner doing "GET" (! (volume-path site name)) None JSON-TYPE))
  (match (! (status-field volume-answer.status))
    404 (return None)
    200 None
    _ (raise (! (status-refusal owner doing "Volume の GET" volume-answer))))
  (<- volume (| VolumeWire Malformed) (parse-json VolumeWire (! (text-field volume-answer.text))))
  (when (isinstance volume Malformed)
    (raise (! (malformed-refusal owner doing "Volume の GET"))))
  (val status (or volume.status (VolumeStatusWire)))
  (<- answer HttpResponse (kube-answer owner doing "GET" (! (attachment-path site name)) None JSON-TYPE))
  (match (! (status-field answer.status))
    404 (return (AttachRead :view (AttachView :attachment False :ticket-node None :satisfied False :message "" :state status.state
                                              :current-node status.current-node-id :other-tickets #())
                            :version ""))
    200 None
    _ (raise (! (status-refusal owner doing "VolumeAttachment の GET" answer))))
  (<- held (| AttachmentWire Malformed) (parse-json AttachmentWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "VolumeAttachment の GET"))))
  (val own (! (ticket-id name)))
  (val tickets (or (and held.spec held.spec.attachment-tickets) {}))
  (val statuses (or (and held.status held.status.attachment-ticket-statuses) {}))
  (val ticket (.get tickets own))
  (val ticket-status (.get statuses own))
  (val messages (if (is ticket-status None)
                    #()
                    (tuple (gfor condition (or ticket-status.conditions #()) :if condition.message condition.message))))
  (AttachRead :view (AttachView :attachment True
                                :ticket-node (if (is ticket None) None ticket.node-id)
                                :satisfied (and (is-not ticket-status None) ticket-status.satisfied)
                                :message (.join "; " messages)
                                :state status.state
                                :current-node status.current-node-id
                                :other-tickets (tuple (sorted (gfor key tickets :if (!= key own) key))))
              :version held.metadata.resource-version))


(defk ticket-put-text [name node version]
  {:pre [(: name str) (: node str) (: version str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "volume name の VolumeAttachment に、node へ付ける ticket を足す merge patch の本文を綴るため(読んだ版 version の上にだけ当てる —
   その間に別の書きが入れば API server が 409 で断り、呼び手が読み直す)。"
  (val id (! (ticket-id name)))
  (json.dumps {"metadata" {"resourceVersion" version}
               "spec" {"attachmentTickets" {id {"id" id "type" TICKET-TYPE "nodeID" node "parameters" {"disableFrontend" "false"}
                                               "generation" 0}}}}
              :ensure-ascii False :sort-keys True))


(defk ticket-removal-text [name version]
  {:pre [(: name str) (: version str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "volume name の VolumeAttachment から、この答え手の ticket を外す merge patch の本文を綴るため(null = 鍵を外す・読んだ版の上にだけ当てる)。"
  (json.dumps {"metadata" {"resourceVersion" version} "spec" {"attachmentTickets" {(! (ticket-id name)) None}}}
              :ensure-ascii False :sort-keys True))


(defk patched-attachment [site owner doing name body]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: body str)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "VolumeAttachment に merge patch の本文 body を当てるため(当たった = True・読んだ後に別の書きが入った 409 = False — 呼び手が読み直す)。"
  (<- answer HttpResponse (kube-answer owner doing "PATCH" (! (attachment-path site name)) body MERGE-PATCH-TYPE))
  (match (! (status-field answer.status))
    200 True
    409 False
    _ (raise (! (status-refusal owner doing "VolumeAttachment の ticket の書き" answer)))))


(defk attached-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume をこの機体の node に attach し、ticket が満たされ volume がその node に付くまで待つため(ticket を置いた = DONE・
   既に付いていた = ALREADY。無い volume・別の node に ticket の在る volume・上限の秒の内に付かない時は断る)。"
  (val doing "attach")
  (<- name str (checked-name owner doing))
  (<- node str (worker-fact WorkerFactName.NODE_NAME))
  (<- started float (GetMonotonic))
  (var put False)
  (while True
    (<- read (| AttachRead None) (attach-read site owner doing name))
    (when (is read None)
      (raise (! (refused owner doing "volume が無い(先に作る)"))))
    (<- step AttachStep (attach-step read.view node))
    (match step
      AttachStep.READY (return (if put WorkspaceOutcome.DONE WorkspaceOutcome.ALREADY))
      AttachStep.ELSEWHERE (raise (! (refused owner doing (.format "node {} への ticket が在る(別の機体で使っているかもしれない — 外さない)"
                                                                     read.view.ticket-node))))
      AttachStep.PUT (do (<- placed bool (patched-attachment site owner doing name (! (ticket-put-text name node read.version))))
                         (when placed (:= put True)))
      AttachStep.WAIT (do (<- now float (GetMonotonic))
                          (when (> (- now started) site.attach-seconds)
                            (raise (! (refused owner doing (.format "{} 秒の内に node {} に付かない(volume {}・node {}・ticket {})"
                                                                    site.attach-seconds node (or read.view.state "?")
                                                                    (or read.view.current-node "?")
                                                                    (or read.view.message "訳なし"))))))
                          (<- (Delay site.poll-seconds)))))
  WorkspaceOutcome.DONE)


(defk mount-table [site owner doing]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str)] :post [(: % (get tuple #(MountEntry ...)))]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "答え手の process から見た mount の表を読むため(読めなければ断る)。"
  (<- text (| str FileFailed) (ReadText site.mountinfo))
  (when (isinstance text FileFailed)
    (raise (! (refused owner doing (+ "mount の表 " site.mountinfo " が読めない: " text.detail)))))
  (! (mount-entries (! (text-field text)))))


(defk detached-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume の、この機体の node への attach を外し、外れるまで待つため(ticket を外した = DONE・無かった = ALREADY)。
   まだ mount されている volume は外さない(外すと file system が書きの途中で切れる — 先に umount)。別の node に ticket の在る volume・
   上限の秒の内に外れない時は断る。"
  (val doing "detach")
  (<- name str (checked-name owner doing))
  (<- node str (worker-fact WorkerFactName.NODE_NAME))
  (<- entries tuple (mount-table site owner doing))
  (<- points tuple (volume-mount-points entries name))
  (when points
    (raise (! (refused owner doing (+ "まだ " (.join "・" points) " に mount されている(先に umount)")))))
  (<- started float (GetMonotonic))
  (var removed False)
  (while True
    (<- read (| AttachRead None) (attach-read site owner doing name))
    (when (is read None)
      (return (if removed WorkspaceOutcome.DONE WorkspaceOutcome.ALREADY)))
    (<- step DetachStep (detach-step read.view node))
    (match step
      DetachStep.GONE (return (if removed WorkspaceOutcome.DONE WorkspaceOutcome.ALREADY))
      DetachStep.ELSEWHERE (raise (! (refused owner doing (.format "ticket が別の node {} に在る(その機体で使っているかもしれない — 外さない)"
                                                                     read.view.ticket-node))))
      DetachStep.REMOVE (do (<- dropped bool (patched-attachment site owner doing name (! (ticket-removal-text name read.version))))
                            (when dropped (:= removed True)))
      DetachStep.WAIT (do (<- now float (GetMonotonic))
                          (when (> (- now started) site.detach-seconds)
                            (raise (! (refused owner doing (.format "{} 秒の内に node {} から外れない(volume {})"
                                                                    site.detach-seconds node (or read.view.state "?"))))))
                          (<- (Delay site.poll-seconds)))))
  WorkspaceOutcome.DONE)


(defk keeper-answer-of [text]
  {:pre [(: text str)] :post [(: % (| KeeperAnswer None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "係の答えの file の字 text を読むため(1 行目が 0 か 1 でなければ None)。"
  (val lines (.splitlines text))
  (if (and lines (in (.strip (get lines 0)) #("0" "1")))
      (KeeperAnswer :code (int (.strip (get lines 0))) :notes (.strip (.join "\n" (cut lines 1 None))))
      None))


(defk keeper-answer [site owner doing verb name path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: verb str) (: name str) (: path str)] :post [(: % KeeperAnswer)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "mount の係へ頼み `<verb> <name> <path>` を 1 つ出し、答えを得るため(頭の註の頼み方 — 係の無い worker・上限の秒の内に答えない・
   答えの形が違う時は断る。答えの file は読んだら消す)。"
  (<- held (| PathStat FileFailed) (StatPath site.mount-dir))
  (when (or (isinstance held FileFailed) (!= held.kind PathKind.DIRECTORY))
    (raise (! (refused owner doing (+ "mount の係が無い worker(頼みの dir " site.mount-dir " が無い)")))))
  (<- started float (GetMonotonic))
  (val key (.format "{}-{}-{}" verb name (int (* started 1000000))))
  (val base (posixpath.join site.mount-dir key))
  (<- written (| FileFailed None) (WriteText (+ base ".tmp") (.format "{} {} {}\n" verb name path)))
  (when (isinstance written FileFailed)
    (raise (! (refused owner doing (+ "係への頼みを書けない: " written.detail)))))
  (<- renamed (| FileFailed None) (RenamePath (+ base ".tmp") (+ base ".req")))
  (when (isinstance renamed FileFailed)
    (raise (! (refused owner doing (+ "係への頼みを置けない: " renamed.detail)))))
  (while True
    (<- text (| str FileFailed) (ReadText (+ base ".out")))
    (if (isinstance text FileFailed)
        (do (<- now float (GetMonotonic))
            (when (> (- now started) site.mount-seconds)
              (<- (RemoveTree (+ base ".req")))
              (raise (! (refused owner doing (.format "mount の係が {} 秒の内に答えない(頼み {}.req)" site.mount-seconds key)))))
            (<- (Delay site.poll-seconds)))
        (do (<- (RemoveTree (+ base ".out")))
            (<- answer (| KeeperAnswer None) (keeper-answer-of (! (text-field text))))
            (when (is answer None)
              (raise (! (refused owner doing (+ "mount の係の答えの形が違う: " (cut text 0 REFUSAL-BODY-LIMIT))))))
            (return answer))))
  (KeeperAnswer :code 1 :notes ""))


(defk checked-path [owner doing path]
  {:pre [(: owner str) (: doing str) (: path str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "付ける先 path が絶対の正規の path(. や .. や重ねた / や尻の / を含まない)かを確かめるため(違えば断る — 作業の根の下かは係が確かめる)。"
  (when (or (not (.startswith path "/")) (!= (posixpath.normpath path) path) (= path "/"))
    (raise (! (refused owner doing (+ "付ける先 '" path "' が絶対の正規の path でない")))))
  path)


(defk mounted-volume [site owner path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume を path に mount するため(付けた = DONE・既に付いていた = ALREADY)。付ける先の dir は作り、外した後も残す。
   付ける先に別の物が付いている・係が断った時は断る(attach していない volume は係が device の無い事で断る)。"
  (val doing "mount")
  (<- name str (checked-name owner doing))
  (<- target str (checked-path owner doing path))
  (<- entries tuple (mount-table site owner doing))
  (<- at (| MountEntry None) (mounted-at entries target))
  (when (is-not at None)
    (<- found (| str None) (volume-of-entry at))
    (if (= found name)
        (return WorkspaceOutcome.ALREADY)
        (raise (! (refused owner doing (.format "{} には別の物({})が付いている" target at.source))))))
  (<- made (| FileFailed None) (MakeDirectory target))
  (when (isinstance made FileFailed)
    (raise (! (refused owner doing (+ "付ける先の dir を作れない: " made.detail)))))
  (<- answer KeeperAnswer (keeper-answer site owner doing "mount" name target))
  (when (!= answer.code 0)
    (raise (! (refused owner doing (+ "mount の係が断った: " answer.notes)))))
  WorkspaceOutcome.DONE)


(defk unmounted-volume [site owner path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "path に付いた持ち主 owner の volume を外すため(外した = DONE・何も付いていなかった = ALREADY)。別の物が付いている・係が断った
   (使っている process が在る)時は断る。付ける先の dir は残す。"
  (val doing "umount")
  (<- name str (checked-name owner doing))
  (<- target str (checked-path owner doing path))
  (<- entries tuple (mount-table site owner doing))
  (<- at (| MountEntry None) (mounted-at entries target))
  (when (is at None)
    (return WorkspaceOutcome.ALREADY))
  (<- found (| str None) (volume-of-entry at))
  (when (!= found name)
    (raise (! (refused owner doing (.format "{} に付いているのは volume {} でない({})" target name at.source)))))
  (<- answer KeeperAnswer (keeper-answer site owner doing "umount" name target))
  (when (!= answer.code 0)
    (raise (! (refused owner doing (+ "mount の係が断った: " answer.notes)))))
  WorkspaceOutcome.DONE)


(defhandler workspace-volumes-api [#^ WorkspaceVolumeSite site]
  ;; 引数に残す理由: 場所と待つ秒は本番(WORKSPACE-SITE)と検で違う(検は memory の file の上の頼みの dir と短い秒)。
  ;; 本番: Longhorn の物を worker の身元で読み書きし、mount は係へ頼む(頭の註)。外側に file・時計・環境の答え手と、API server 宛ての
  ;; 要求を区画の CA で検める HTTP の答え手を要る。
  (CreateWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (created-volume site owner))
    (resume outcome))
  (AttachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (attached-volume site owner))
    (resume outcome))
  (MountWorkspaceVolume [owner path]
    (<- outcome WorkspaceOutcome (mounted-volume site owner path))
    (resume outcome))
  (UmountWorkspaceVolume [owner path]
    (<- outcome WorkspaceOutcome (unmounted-volume site owner path))
    (resume outcome))
  (DetachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (detached-volume site owner))
    (resume outcome)))
