;;; 持ち主ごとの作業場所の volume の effect(型は doeff_cluster._shared.intent.workspace_volume_model)の本番の答え手 — その job を回す
;;; worker の身元(ServiceAccount の token)で自区画の API server の Longhorn の物(Volume・VolumeAttachment)を読み書きし、mount と umount は
;;; 同じ Pod の mount の係(sidecar workspace-mount)へ頼む。決まりは deploy/k8s/workspace-volumes/README.md。
;;;
;;;   WORKSPACE-SITE          本番の場所と待つ秒(Longhorn の namespace・値の ConfigMap・係の頼みの dir — zeus の node.yaml の emptyDir と
;;;                           同じ path・mountinfo・上限の秒)
;;;   volume-create-text      volume の名・級の parameters・大きさ → Longhorn の Volume の作成の本文の JSON の字(級の値の写し — README の表)
;;;   created-volume          CreateWorkspaceVolume: 値の ConfigMap と級(StorageClass)を読み、その名の完了した backup が在れば一番新しい物
;;;                           から戻し(spec.fromBackup)、無ければ空で Volume を POST する(201 = 作った・409 = 在った)。在る volume が戻す
;;;                           途中なら戻し終わるまで、消す途中なら消え終わるまで待つ。POST の前に札の disk の残りを数え(room-held)、
;;;                           受けられなければ作らずに置き場の不足 WorkspaceRoomShort で断る
;;;   attached-volume         AttachWorkspaceVolume: VolumeAttachment に ticket を置き、Volume に使った刻の印を書き、ticket が満たされ volume が
;;;                           この node に付くまで待つ。Longhorn が replica を置けないと答えた中身の無い volume は、待たずに消して断る
;;;                           (dropped-unplaced — 戻しの待ちも同じ)
;;;   mounted-volume          MountWorkspaceVolume: 付ける先の dir を作り、係へ mount を頼む(既に付いていれば頼まない・中身の在る dir には
;;;                           付けない — 隠さない)
;;;   unmounted-volume        UmountWorkspaceVolume: 係へ umount を頼む(何も付いていなければ頼まない)
;;;   detached-volume         DetachWorkspaceVolume: mount が残っていないかを確かめ、ticket を外し、volume がこの node から外れるまで待ち、
;;;                           使い終わりの刻の印を書く
;;;   cooled-volume           CoolWorkspaceVolume: 外れている volume の Snapshot と Backup を作って完了を待ち、使う ticket が無い事を確かめて、
;;;                           読んだ版の上でだけ Volume を消す
;;;   purged-volume           PurgeWorkspaceVolume: volume の無い名の BackupVolume を、最後の backup から keepDays を過ぎていれば消す
;;;   idle-cooling            CoolIdleWorkspaceVolumes: 外れている volume・札の disk の残り(room-slots)を読み、冷やす順に冷やし、その後に
;;;                           この機体に付いている volume の空きを 1 つずつ読んで広げる(mounted-growth)
;;;   grown-volume            付いている volume 1 つの file system の空きが値の growBelowPercent を割っていれば、名目の予約の増えを数えて
;;;                           (room-held)Volume の spec.size を倍(上限 maxSizeBytes)に書き、engine が広がるのを待ち(waited-engine)、
;;;                           係へ grow(resize2fs)を頼む — 付けたまま。名目を書いた後に止まった前の広げは、file system だけを広げ直す
;;;   workspace-room          ReadWorkspaceRoom: 札の disk の残りの本数(room-slots — Longhorn の置く線 2 つ: 過剰割り当ての % の名目の
;;;                           予約の線と、最小の空きの % の実の空きの線)と値の spareVolumes を読む
;;;   place-read              ReadWorkspacePlace: mount の表・付ける先の dir・持ち主の volume と backup を読み、付ける先の様子を答える
;;;   workspace-volumes-api   10 の effect の本番の答え手
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
;;; 残る volume の detach・付ける先に別の物・API server に届かない・断られた・token が読めない・答えの形が違う・backup は在るが完了した物が
;;; 無い名の作成(空で作らない)・上限の秒の内に戻らない・使っている volume と戻す途中の volume を冷やす・snapshot か backup が失敗した・冷やす
;;; 途中に使い始めた(消さない)・volume の在る名と最後の backup から keepDays の内の名の backup を消す。置き場の不足は型を分ける
;;; (WorkspaceRoomShort — WorkspaceVolumeRefused の子): 作る前の数えで札の disk が受けられない・付ける時か戻す時に Longhorn が replica を
;;; 置けない(中身の無い volume は消してから断る)。fresh = 持ち主が中身を持たないか(呼び手が空の場所に落としてよいかを決める)。
;;; 作る前の数えと作りの間の競り(同じ disk に別の持ち主の作りが並ぶ・Pod の入れ替えで 2 つの世代の worker が並ぶ)は数えでは除けない —
;;; 負けた側は付ける時に Longhorn の「置けない」を読み、中身の無い volume を消して同じ断りで答える(faulted の volume を残さない)。
;;;
;;; 冷やす途中の attach(使う側が勝つ): attach は ticket を置いてから Volume に刻の印を書き、冷やす側は Volume を読み(版 R)、次に
;;; VolumeAttachment を読んで使う ticket が無い事を確かめ、版 R の上でだけ消す(DELETE の preconditions)。消せたなら印はまだ書かれていない
;;; = ticket は VolumeAttachment を読んだ後に置かれたので、attach の印の書きが 404 になり、attach は「消された」と断る(使い手は作る(戻す)
;;; から出し直す)。印が先なら、冷やす側の版 R が古いか ticket が見えるので消さない。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord defwire])
(import dataclasses [dataclass])  ; defrecord・defwire の展開が使う
(import datetime [datetime])
(import json)
(import posixpath)
(import urllib.parse [quote :as url-quote])
(import doeff_hy._wire [Malformed parse-json parse-json-as])
(import doeff_core_effects [DiskUsage FileFailed ListDirectory MakeDirectory PathKind PathStat ReadDiskUsage ReadText RemoveTree RenamePath
                            StatPath WriteText])
(import doeff_core_effects [HttpFailed HttpRequest HttpResponse])
(import doeff_time [Delay GetMonotonic GetTime])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CoolIdleWorkspaceVolumes CoolingSweep
                                                           CoolWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume PurgeWorkspaceVolume ReadWorkspacePlace
                                                           ReadWorkspaceRoom UmountWorkspaceVolume WorkspaceOutcome WorkspacePlace
                                                           WorkspaceRoom WorkspaceRoomShort WorkspaceVolumeRefused WorkspaceVolumeSite])
(import doeff_cluster._shared.intent.supplied_model [WorkerFactName])
(import doeff_cluster._shared.core.workspace_volume_rules [AttachStep AttachView BackupView DetachStep DiskRoom MountEntry
                                                         NAME-PREFIX USED-AT-KEY WarmVolume attach-step cooling-name
                                                         cooling-order detach-step free-slots mount-entries mounted-at
                                                         newest-backup owner-refusal ticket-id TICKET-TYPE using-tickets
                                                         volume-mount-points volume-name volume-of-entry])
(import doeff_cluster._shared.protocol.supplied_values [KUBE-API REFUSAL-BODY-LIMIT REQUEST-TIMEOUT-SECONDS SERVICE-ACCOUNT-TOKEN-FILE
                                                      kube-headers service-account-text status-field text-field worker-fact])

(val MODULE-TAGS {:context "doeff-cluster" :role "protocol"})

;; 本番の場所と待つ秒。mount-dir は zeus の node.yaml の頼みの dir(emptyDir の workspace-mount の mountPath)と同じ path — 検が縛る。
;; 待つ秒: attach は replica の engine が起きるまで(測りでは数秒)、係は空の 10 GiB の volume の mkfs.ext4 を含む。backup と戻しは中身の
;; 量で伸びる(2026-11-03 の zeus の測り: 中身 50 MB で backup 27 秒・戻し 21 秒)— 10 GiB を書き切った volume でも収まる上限にする。
(val WORKSPACE-SITE (WorkspaceVolumeSite :longhorn-namespace "longhorn-system"
                                         :settings-namespace "agent-worker"
                                         :settings-name "workspace-volume"
                                         :mount-dir "/run/workspace-mount"
                                         :mountinfo "/proc/self/mountinfo"
                                         :attach-seconds 180.0
                                         :detach-seconds 180.0
                                         :mount-seconds 180.0
                                         :backup-seconds 1800.0
                                         :restore-seconds 1800.0
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
  "値の ConfigMap の workspace-volume.json のうち読む欄: storageClass = 級の名・sizeBytes = 作る時の大きさ(byte)・warmHours = 使い終わりから
   温かく置く時間・spareVolumes = 札の disk に空けておく volume の本数・keepDays = 最後の backup から backup を消せるまでの日数・
   growBelowPercent = 付いている volume の file system の空きがこの % を割ったら広げる(0 = 広げない)・maxSizeBytes = 広げる上限(byte —
   0 = 広げない)。広げる 2 つの欄は無い ConfigMap でも読める(無ければ広げない)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str storage-class)
  (#^ int size-bytes)
  (#^ int warm-hours)
  (#^ int spare-volumes)
  (#^ int keep-days)
  (setv #^ int grow-below-percent 0)
  (setv #^ int max-size-bytes 0))


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


(defwire VolumeConditionWire
  "Longhorn の Volume の condition 1 つのうち読む欄: type・status(字の True か False)・reason・message。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str type "")
  (setv #^ str status "")
  (setv #^ str reason "")
  (setv #^ str message ""))


(defwire VolumeStatusWire
  "Longhorn の Volume の status のうち読む欄: state(creating・detached・attaching・attached・detaching ほか)・currentNodeID・restoreRequired と
   restoreInitiated(backup から戻す volume の、戻しが要る・始まった)・conditions(Scheduled = replica を置けたか)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"}
   :names {state "state" current-node-id "currentNodeID" restore-required "restoreRequired" restore-initiated "restoreInitiated"
           conditions "conditions"}
   :unknown :ignore}
  (setv #^ str state "")
  (setv #^ str current-node-id "")
  (setv #^ bool restore-required False)
  (setv #^ bool restore-initiated False)
  (setv #^ (| (get tuple #(VolumeConditionWire ...)) None) conditions None))


(defwire VolumeMetaWire
  "Longhorn の Volume の metadata のうち読む欄: name・resourceVersion(消す時の preconditions)・annotations(使った刻の印)・
   creationTimestamp・deletionTimestamp(消す途中なら在る)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str name "")
  (setv #^ str resource-version "")
  (setv #^ (| (get dict #(str str)) None) annotations None)
  (setv #^ str creation-timestamp "")
  (setv #^ (| str None) deletion-timestamp None))


(defwire VolumeSpecWire
  "Longhorn の Volume の spec のうち読む欄: fromBackup(戻す元 — 空なら空で作った volume)・backupTargetName(backup の先)・size(名目の
   大きさ — byte の数の字)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str from-backup "")
  (setv #^ str backup-target-name "")
  (setv #^ str size ""))


(defwire EngineStatusWire
  "Longhorn の Engine の status のうち読む欄: currentSize(engine が今見せている block device の大きさ — byte の数の字)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str current-size ""))


(defwire EngineWire
  "Longhorn の Engine の一覧の 1 つのうち読む欄: status。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| EngineStatusWire None) status None))


(defwire EngineListWire
  "Longhorn の Engine の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(EngineWire ...)) None) items None))


(defwire VolumeWire
  "Longhorn の Volume の GET の答えのうち読む欄: metadata・spec・status(作った直後は無い)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| VolumeMetaWire None) metadata None)
  (setv #^ (| VolumeSpecWire None) spec None)
  (setv #^ (| VolumeStatusWire None) status None))


(defwire VolumeListWire
  "Longhorn の Volume の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(VolumeWire ...)) None) items None))


(defwire ReplicaSpecWire
  "Longhorn の Replica の spec のうち読む欄: nodeID(置いた node — 空 = まだ置いていない)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names {node-id "nodeID"} :unknown :ignore}
  (setv #^ str node-id ""))


(defwire ReplicaWire
  "Longhorn の Replica の一覧の 1 つのうち読む欄: spec。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| ReplicaSpecWire None) spec None))


(defwire ReplicaListWire
  "Longhorn の Replica の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(ReplicaWire ...)) None) items None))


(defwire NamedMetaWire
  "Longhorn の物の metadata のうち読む欄: name。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str name ""))


(defwire BackupStatusWire
  "Longhorn の Backup の status のうち読む欄: state・backupCreatedAt・url(戻す元)・error・volumeSize(backup を取った時の volume の名目の
   大きさ — byte の数の字)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str state "")
  (setv #^ str backup-created-at "")
  (setv #^ str url "")
  (setv #^ str error "")
  (setv #^ str volume-size ""))


(defwire BackupWire
  "Longhorn の Backup の GET の答えのうち読む欄: metadata・status(作った直後は無い)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| NamedMetaWire None) metadata None)
  (setv #^ (| BackupStatusWire None) status None))


(defwire BackupListWire
  "Longhorn の Backup の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(BackupWire ...)) None) items None))


(defwire NamedWire
  "Longhorn の物(BackupVolume ほか)の一覧の 1 つのうち読む欄: metadata。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| NamedMetaWire None) metadata None))


(defwire NamedListWire
  "Longhorn の物の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(NamedWire ...)) None) items None))


(defwire SnapshotStatusWire
  "Longhorn の Snapshot の status のうち読む欄: readyToUse・error。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ bool ready-to-use False)
  (setv #^ str error ""))


(defwire SnapshotWire
  "Longhorn の Snapshot の GET の答えのうち読む欄: status(作った直後は無い)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| SnapshotStatusWire None) status None))


(defwire DiskSpecWire
  "Longhorn の Node の spec の disk 1 つのうち読む欄: tags・allowScheduling・storageReserved(byte)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(str ...)) None) tags None)
  (setv #^ bool allow-scheduling False)
  (setv #^ int storage-reserved 0))


(defwire DiskConditionWire
  "Longhorn の Node の disk の condition 1 つのうち読む欄: type・status(字の True か False)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ str type "")
  (setv #^ str status ""))


(defwire DiskStatusWire
  "Longhorn の Node の status の disk 1 つのうち読む欄: storageMaximum・storageScheduled・storageAvailable(byte)・conditions。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ int storage-maximum 0)
  (setv #^ int storage-scheduled 0)
  (setv #^ int storage-available 0)
  (setv #^ (| (get tuple #(DiskConditionWire ...)) None) conditions None))


(defwire NodeSpecWire
  "Longhorn の Node の spec のうち読む欄: disks(disk の名 → disk)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str DiskSpecWire)) None) disks None))


(defwire NodeStatusWire
  "Longhorn の Node の status のうち読む欄: diskStatus(disk の名 → 様子)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get dict #(str DiskStatusWire)) None) disk-status None))


(defwire NodeWire
  "Longhorn の Node 1 つのうち読む欄: spec・status。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| NodeSpecWire None) spec None)
  (setv #^ (| NodeStatusWire None) status None))


(defwire NodeListWire
  "Longhorn の Node の一覧の GET の答えのうち読む欄: items。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (setv #^ (| (get tuple #(NodeWire ...)) None) items None))


(defwire LonghornSettingWire
  "Longhorn の Setting 1 つの GET の答えのうち読む欄: value(字)。"
  {:tags {:context "doeff-cluster" :role "protocol" :reads "json"} :names :camel :unknown :ignore}
  (#^ str value))


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


(defrecord Placing
  "Volume の condition Scheduled の読み: failed = Longhorn が replica の置き先を見つけられないと答えている(status False・reason が
   …SchedulingFailure — 名目の予約の線か実の空きの線で置けない・札を持つ disk が無いなど)・note = その reason と message(人が読む訳)。
   Longhorn v1.13 は外れている volume の replica を作るだけで置かず(condition は True)、初めて付ける時か戻す時に置き先を決める
   (2026-11-03 の本番の試し — README の節「置く disk」)。"
  (#^ bool failed)
  (#^ str note))


(defrecord AttachRead
  "volume 1 つの attach の読み: view = 判断に渡す様子・version = VolumeAttachment の resourceVersion(無ければ空)・placing = Volume の
   condition Scheduled の読み。"
  (#^ AttachView view)
  (#^ str version)
  (#^ Placing placing))


(defrecord KeeperAnswer
  "mount の係の答え 1 つ: code = 0(した・既にそうだった)か 1(断った・失敗した)・notes = 2 行目からの訳と命令の出力。"
  (#^ int code)
  (#^ str notes))


(defrecord VolumeRead
  "Longhorn の Volume 1 つの読み: name・version = resourceVersion・state = status.state(読めなければ空)・restoring = backup から戻す途中
   (spec.fromBackup が在り、戻しがまだ始まっていないか終わっていない)・deleting = 消す途中・used-at = 使った刻の印(epoch 秒 — 無ければ
   作られた刻)・backup-target = spec.backupTargetName・placing = condition Scheduled の読み(Longhorn が replica を置けないと答えているか)・
   size = spec.size(名目の大きさ byte — 読めなければ 0)。"
  (#^ str name)
  (#^ str version)
  (#^ str state)
  (#^ bool restoring)
  (#^ bool deleting)
  (#^ float used-at)
  (#^ str backup-target)
  (#^ Placing placing)
  (setv #^ int size 0))


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


(defk volume-create-text [site name parameters size-bytes from-backup]
  {:pre [(: site WorkspaceVolumeSite) (: name str) (: parameters (get dict #(str str))) (: size-bytes int) (> size-bytes 0)
         (: from-backup str)]
   :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol" :spells "json" :reads "json"}}
  "volume の名 name の Longhorn の Volume の作成の本文を、級の parameters と大きさ size-bytes から JSON の字へ綴るため(JSON の境界の 1 点 —
   README の写し方の表: replica の数・置く disk の札・locality・stale の分・backup の先は spec の同じ名の欄へ、recurringJobSelector の群は
   label recurring-job-group.longhorn.io/<群> へ、RecurringJob 1 つは label recurring-job.longhorn.io/<名> へ写す。frontend は blockdev・
   access mode は rwo。from-backup が空でなければ spec.fromBackup に書き、Longhorn がその backup から中身を戻す)。写し方の無い
   parameter・ext4 でない fsType・数でない replica の数と stale の分・形の違う selector は ValueError で断る(呼び手が断りの文にする)。"
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
  (val restore (if from-backup {"fromBackup" from-backup} {}))
  (json.dumps {"apiVersion" "longhorn.io/v1beta2" "kind" "Volume"
               "metadata" (| {"name" name "namespace" site.longhorn-namespace} (if labels {"labels" labels} {}))
               "spec" (| {"size" (str size-bytes) "frontend" "blockdev" "accessMode" "rwo"} counts texts disks restore)}
              :ensure-ascii False :sort-keys True))


(defk longhorn-path [site kind]
  {:pre [(: site WorkspaceVolumeSite) (: kind str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Longhorn の物の種類 kind(volumes・snapshots・backups・backupvolumes・nodes・settings・replicas・engines の複数形)の、API server の上の
   一覧の path を組むため。"
  (+ LONGHORN-API "/namespaces/" (url-quote site.longhorn-namespace :safe "") "/" kind))


(defk epoch-of [text]
  {:pre [(: text str)] :post [(: % (| float None))] :tags {:context "doeff-cluster" :role "protocol"}}
  "k8s の刻の字 text(ISO 8601 の UTC — 尻の Z)を epoch 秒にするため(空・読めない字は None)。"
  (try
    (.timestamp (datetime.fromisoformat text))
    (except [ValueError] None)))


(defk placing-of [status]
  {:pre [(: status VolumeStatusWire)] :post [(: % Placing)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Volume の status から、Longhorn が replica を置けないと答えているかを読むため(置けない volume を attach と戻しの上限の秒まで待たない)。"
  (val found (next (gfor condition (or status.conditions #()) :if (= condition.type "Scheduled") condition) None))
  (if (is found None)
      (Placing :failed False :note "")
      (Placing :failed (and (= found.status "False") (.endswith found.reason "SchedulingFailure"))
               :note (.strip (.join " " #(found.reason found.message))))))


(defk volume-read-of [owner doing held]
  {:pre [(: owner str) (: doing str) (: held VolumeWire)] :post [(: % VolumeRead)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Volume の GET の答え held を、判断に使う読みにするため(頭の註の VolumeRead — 使った刻の印が無い・読めなければ作られた刻)。metadata の
   名・版・作られた刻が無い答えは形が違うと断る(API server は必ず埋める)。"
  (val meta (or held.metadata (VolumeMetaWire)))
  (val spec (or held.spec (VolumeSpecWire)))
  (val status (or held.status (VolumeStatusWire)))
  (val mark (if (is meta.annotations None) "" (.get meta.annotations USED-AT-KEY "")))
  (val marked (if (.isdigit mark) (float mark) None))
  (<- created (| float None) (epoch-of meta.creation-timestamp))
  (when (or (not meta.name) (not meta.resource-version) (is created None))
    (raise (! (malformed-refusal owner doing "Volume の metadata(名・版・作られた刻)"))))
  (<- placing Placing (placing-of status))
  (VolumeRead :name meta.name :version meta.resource-version :state status.state
              :restoring (and (bool spec.from-backup) (or (not status.restore-initiated) status.restore-required))
              :deleting (is-not meta.deletion-timestamp None)
              :used-at (if (is-not marked None) marked created)
              :backup-target spec.backup-target-name
              :placing placing
              :size (if (.isdigit spec.size) (int spec.size) 0)))


(defk volume-read [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % (| VolumeRead None))]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の Longhorn の Volume を読むため(無ければ None)。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (! (volume-path site name)) None JSON-TYPE))
  (match (! (status-field answer.status))
    404 (return None)
    200 None
    _ (raise (! (status-refusal owner doing "Volume の GET" answer))))
  (<- held (| VolumeWire Malformed) (parse-json VolumeWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Volume の GET"))))
  (<- read VolumeRead (volume-read-of owner doing held))
  read)


(defk waited-gone [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "消す途中の volume name が消え終わるまで待つため(detach-seconds の内に消えなければ断る — Longhorn は replica と engine を片付けてから
   Volume を外す)。消えた後に同じ名で作り直された(消す途中でない)volume が見えても、消え終わったと読む。"
  (<- started float (GetMonotonic))
  (while True
    (<- read (| VolumeRead None) (volume-read site owner doing name))
    (when (or (is read None) (not read.deleting))
      (return None))
    (<- now float (GetMonotonic))
    (when (> (- now started) site.detach-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に消えない(volume {}・消す途中 {})" site.detach-seconds (or read.state "?")
                                              read.deleting)))))
    (<- (Delay site.poll-seconds)))
  None)


(defk waited-restored [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "backup から戻す途中の volume name が戻し終わるまで待つため(restore-seconds の内に終わらない・途中で消えた時は断る)。Longhorn が replica を
   置けないと答え、replica がどこにも置かれていなければ、待たずに volume を消して置き場の不足で断る(中身は backup に残る — fresh False)。"
  (<- started float (GetMonotonic))
  (while True
    (<- read (| VolumeRead None) (volume-read site owner doing name))
    (when (is read None)
      (raise (! (refused owner doing "戻す途中に volume が消えた"))))
    (when (not read.restoring)
      (return None))
    (when read.placing.failed
      (<- (dropped-unplaced site owner doing name read.placing.note False)))
    (<- now float (GetMonotonic))
    (when (> (- now started) site.restore-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に戻し終わらない(volume {})" site.restore-seconds (or read.state "?"))))))
    (<- (Delay site.poll-seconds)))
  None)


(defk label-query [name target]
  {:pre [(: name str) (: target str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "volume name の backup の先 target の Backup と BackupVolume を選ぶ一覧の query の字を組むため(Longhorn が付ける label backup-volume と
   backup-target — README の節「名」)。"
  (+ "?labelSelector=" (url-quote (.format "backup-volume={},backup-target={}" name target) :safe "")))


(defk backup-view-of [item]
  {:pre [(: item BackupWire)] :post [(: % BackupView)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Backup の一覧の 1 つ item を、判断に渡す読みにするため(作った直後で status が無ければ、様子と刻と url は空)。"
  (val status (or item.status (BackupStatusWire)))
  (BackupView :name (. (or item.metadata (NamedMetaWire)) name) :state status.state :created-at status.backup-created-at :url status.url
              :volume-size (if (.isdigit status.volume-size) (int status.volume-size) 0)))


(defk backup-views [site owner doing name target]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: target str)]
   :post [(: % (get tuple #(BackupView ...)))] :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の backup の先 target の Backup を、名の順に読むため。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "backups")) (! (label-query name target))) None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "Backup の一覧" answer))))
  (<- held (| BackupListWire Malformed) (parse-json BackupListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Backup の一覧"))))
  (var views #())
  (for [item (or held.items #())]
    (<- view BackupView (backup-view-of item))
    (:= views (+ views #(view))))
  (tuple (sorted views :key (fn [view] view.name))))


(defk class-backup-target [owner doing parameters]
  {:pre [(: owner str) (: doing str) (: parameters (get dict #(str str)))] :post [(: % str)] :tags {:context "doeff-cluster" :role "protocol"}}
  "級の parameters の backup の先(backupTargetName)を得るため(無ければ断る — 冷やす・戻す・消すは backup の先の名で backup を選ぶ)。"
  (val target (.get parameters "backupTargetName" ""))
  (when (not target)
    (raise (! (refused owner doing "級に backupTargetName が無い(backup の先が決まらない)"))))
  target)


(defk room-short [owner doing reason fresh]
  {:pre [(: owner str) (: doing str) (: reason str) (: fresh bool)] :post [(: % WorkspaceRoomShort)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume を置く場所が足りずに doing できなかった断りを、ほかの断りと同じ綴りで作るため(fresh = 中身を持たない持ち主か)。"
  (<- refusal WorkspaceVolumeRefused (refused owner doing reason))
  (WorkspaceRoomShort (str refusal) fresh))


(defk class-disk-tags [parameters]
  {:pre [(: parameters (get dict #(str str)))] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "級の parameters の diskSelector(`,` で分けた札)を、置く disk の札の組にするため。"
  (tuple (lfor tag (.split (.get parameters "diskSelector" "") ",") :if (.strip tag) (.strip tag))))


(defk room-slots [site owner doing parameters size-bytes]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: parameters (get dict #(str str))) (: size-bytes int) (> size-bytes 0)]
   :post [(: % int) (>= % 0)] :tags {:context "doeff-cluster" :role "protocol"}}
  "級 parameters の札の disk が、名目の大きさ size-bytes の volume をあと何本受けられるかを、Longhorn の置く線 2 つ(設定の過剰割り当ての % と
   最小の空きの % — free-slots)で数えるため(読めなければ断る)。"
  (<- rooms tuple (disk-rooms site owner doing (! (class-disk-tags parameters))))
  (<- percentage int (longhorn-percentage site owner doing "storage-over-provisioning-percentage"))
  (<- minimal int (longhorn-percentage site owner doing "storage-minimal-available-percentage"))
  (! (free-slots rooms size-bytes percentage minimal)))


(defk room-held [site owner doing parameters size-bytes fresh]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: parameters (get dict #(str str))) (: size-bytes int) (> size-bytes 0)
         (: fresh bool)]
   :post [(: % None)] :tags {:context "doeff-cluster" :role "protocol"}}
  "Volume を POST する前に、札の disk が名目の大きさ size-bytes の volume を受けられる事を確かめるため。受けられなければ作らずに置き場の不足で
   断る(Longhorn は置けない Volume も受けて、付ける時に faulted にする — 作る側が先に数える)。"
  (<- slots int (room-slots site owner doing parameters size-bytes))
  (when (< slots 1)
    (raise (! (room-short owner doing (.format "札の disk が名目 {} byte の volume をもう受けられない(Longhorn の置く線 — 名目の予約か実の空き)"
                                               size-bytes)
                          fresh))))
  None)


(defk replicas-placed [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の replica が 1 つでもどこかの disk に置かれているか(中身を持ちうるか)を、Longhorn の Replica の一覧で確かめるため。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "replicas"))
                                                            "?labelSelector=" (url-quote (+ "longhornvolume=" name) :safe ""))
                                       None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "Replica の一覧" answer))))
  (<- held (| ReplicaListWire Malformed) (parse-json ReplicaListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Replica の一覧"))))
  (any (gfor item (or held.items #()) (and (is-not item.spec None) (bool item.spec.node-id)))))


(defk dropped-unplaced [site owner doing name note fresh]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: note str) (: fresh bool)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "Longhorn が replica を置けないと答えた volume name(訳 note)を、中身を持たない時(replica がどの disk にも置かれていない — 付いた事が
   無い)にその場で消して置き場の不足で断るため(faulted の volume を残さず、付くのを上限の秒まで待たない)。replica の置かれた volume は
   中身を持ちうるので消さずに戻る(呼び手が待ちを続ける)。消す前に読み直した版の上でだけ消す — 読んだ後に書きが入れば消さずに断る。"
  (<- placed bool (replicas-placed site owner doing name))
  (when placed
    (return None))
  (<- read (| VolumeRead None) (volume-read site owner doing name))
  (var deleted (is read None))
  (when (is-not read None)
    (<- gone bool (deleted-volume site owner doing name read.version))
    (:= deleted gone)
    (when gone
      (<- (waited-gone site owner doing name))))
  (raise (! (room-short owner doing (+ "Longhorn が replica を置けない(" (or note "訳なし") ")"
                                       (if deleted " — 中身の無い volume を消した" " — 読んだ後に書きが入ったので消さなかった"))
                        fresh))))


(defk created-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume が無ければ、値の ConfigMap の級と大きさで作るため。その名の完了した backup が在れば(冷えている)一番新しい物から
   戻し、戻し終わるまで待つ(RESTORED)。backup が無ければ空で作る(DONE)。在れば ALREADY — 中身に触らない。ただし戻す途中なら戻し
   終わるまで待ち(同じ持ち主の次の頼みが先の戻しに相乗りする)、消す途中(冷やす手順の終わり)なら消え終わってから戻す。同じ名の作成が
   重なっても API server が 1 つだけを作り(409)、負けた側は勝った側の戻しを待つ。backup は在るが完了した物が無い名は、空で作らずに断る。
   作る前に札の disk の残りを数え(room-held)、受けられなければ POST せずに置き場の不足 WorkspaceRoomShort で断る。戻す volume は
   backup を取った時の大きさ(と値の大きさの大きい方)で作る — Longhorn は backup より小さい volume への戻しを断る。"
  (val doing "作成")
  (<- name str (checked-name owner doing))
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (<- target str (class-backup-target owner doing parameters))
  (while True
    (<- read (| VolumeRead None) (volume-read site owner doing name))
    (cond
      (and (is-not read None) read.deleting) (<- (waited-gone site owner doing name))
      (is-not read None) (do (when read.restoring
                               (<- (waited-restored site owner doing name)))
                             (return WorkspaceOutcome.ALREADY))
      True
      (do (<- backups tuple (backup-views site owner doing name target))
          (<- source (| BackupView None) (newest-backup backups))
          (when (and backups (is source None))
            (raise (! (refused owner doing (+ "backup は在るが完了した物が無い(空で作らない): " (.join "・" (gfor one backups (+ one.name " " (or one.state "?")))))))))
          (val size (if (is source None) settings.size-bytes (max settings.size-bytes source.volume-size)))
          (<- (room-held site owner doing parameters size (is source None)))
          (var body "")
          (try
            (<- text str (volume-create-text site name parameters size (if (is source None) "" source.url)))
            (:= body text)
            (except [error ValueError]
              (raise (! (refused owner doing (+ "級 " settings.storage-class " を写せない: " (str error)))))))
          (<- answer HttpResponse (kube-answer owner doing "POST" (! (longhorn-path site "volumes")) body JSON-TYPE))
          (match (! (status-field answer.status))
            201 (if (is source None)
                    (return WorkspaceOutcome.DONE)
                    (do (<- (waited-restored site owner doing name))
                        (return WorkspaceOutcome.RESTORED)))
            409 None
            _ (raise (! (status-refusal owner doing "Volume の作成" answer)))))))
  WorkspaceOutcome.ALREADY)


(defk marked-volume [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "volume name の Volume に、使った刻の印(USED-AT-KEY = 今の epoch 秒)を書くため(書けた = True・volume が無い 404 = False)。"
  (<- now datetime (GetTime))
  (val body (json.dumps {"metadata" {"annotations" {USED-AT-KEY (str (int (.timestamp now)))}}} :ensure-ascii False :sort-keys True))
  (<- answer HttpResponse (kube-answer owner doing "PATCH" (! (volume-path site name)) body MERGE-PATCH-TYPE))
  (match (! (status-field answer.status))
    200 True
    404 False
    _ (raise (! (status-refusal owner doing "Volume の使った刻の印の書き" answer)))))


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
  (<- placing Placing (placing-of status))
  (<- answer HttpResponse (kube-answer owner doing "GET" (! (attachment-path site name)) None JSON-TYPE))
  (match (! (status-field answer.status))
    404 (return (AttachRead :view (AttachView :attachment False :ticket-node None :satisfied False :message "" :state status.state
                                              :current-node status.current-node-id :other-tickets #())
                            :version "" :placing placing))
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
              :version held.metadata.resource-version
              :placing placing))


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
   既に付いていた = ALREADY。無い volume・別の node に ticket の在る volume・上限の秒の内に付かない時は断る)。ticket を置いたら Volume に
   使った刻の印を書く — 冷やす途中の volume を冷やす側に消させない(頭の註)。印を書く前に消されていたら、作る(戻す)から出し直すよう断る。
   付くのを待つ間に Longhorn が replica を置けないと答え、replica がどこにも置かれていなければ(初めて付ける空の volume — 作る前の数えと
   付けの間に置き場が尽きた)、上限の秒まで待たずに volume を消して置き場の不足で断る(fresh True)。"
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
                         (when placed
                           (:= put True)
                           (<- marked bool (marked-volume site owner doing name))
                           (when (not marked)
                             (raise (! (refused owner doing "ticket を置いた後に volume が消された(冷やされた — 作る(戻す)から出し直す)"))))))
      AttachStep.WAIT (do (when read.placing.failed
                            (<- (dropped-unplaced site owner doing name read.placing.note True)))
                          (<- now float (GetMonotonic))
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
   上限の秒の内に外れない時は断る。外したら Volume に使い終わりの刻の印を書く(冷やす順が読む — volume が無ければ書かない)。"
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
      DetachStep.GONE (if removed
                          (do (<- (marked-volume site owner doing name))
                              (return WorkspaceOutcome.DONE))
                          (return WorkspaceOutcome.ALREADY))
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


(defk place-filled [owner doing target]
  {:pre [(: owner str) (: doing str) (: target str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "何も付いていない付ける先 target に中身が在るか(dir の中に何か在る・dir でない物が在る)を読むため(無い path と空の dir は False。
   読めなければ断る)。"
  (<- held (| PathStat FileFailed) (StatPath target))
  (when (isinstance held FileFailed)
    (raise (! (refused owner doing (+ "付ける先 " target " が読めない: " held.detail)))))
  (match held.kind
    PathKind.MISSING False
    PathKind.DIRECTORY (do (<- listed (| tuple FileFailed) (ListDirectory target))
                           (when (isinstance listed FileFailed)
                             (raise (! (refused owner doing (+ "付ける先 " target " の中が読めない: " listed.detail)))))
                           (bool listed))
    _ True))


(defk owner-held [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner(volume の名 name)が中身を持ちうるか — Longhorn の volume か、その名の backup が在るか — を読むため。"
  (<- read (| VolumeRead None) (volume-read site owner doing name))
  (when (is-not read None)
    (return True))
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (<- target str (class-backup-target owner doing parameters))
  (<- backups tuple (backup-views site owner doing name target))
  (bool backups))


(defk place-read [site owner path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: path str)] :post [(: % WorkspacePlace)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume を付ける先 path の様子を読むため(ReadWorkspacePlace の答え — WorkspacePlace の頭の註): 一番上に付いている物が
   持ち主の volume なら MOUNTED・別の物なら CLASH。何も付いていなければ、dir が無いか空なら READY、中身が在れば持ち主が volume か backup を
   持つかで CLASH か OUTSIDE。何も変えない。"
  (val doing "付ける先の読み")
  (<- name str (checked-name owner doing))
  (<- target str (checked-path owner doing path))
  (<- entries tuple (mount-table site owner doing))
  (<- at (| MountEntry None) (mounted-at entries target))
  (when (is-not at None)
    (<- found (| str None) (volume-of-entry at))
    (return (if (= found name) WorkspacePlace.MOUNTED WorkspacePlace.CLASH)))
  (<- filled bool (place-filled owner doing target))
  (when (not filled)
    (return WorkspacePlace.READY))
  (<- held bool (owner-held site owner doing name))
  (if held WorkspacePlace.CLASH WorkspacePlace.OUTSIDE))


(defk mounted-volume [site owner path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "持ち主 owner の volume を path に mount するため(付けた = DONE・既に付いていた = ALREADY)。付ける先の dir は作り、外した後も残す。
   付ける先に別の物が付いている・中身の在る dir(volume の外に書かれた物 — 付けると隠す)・係が断った時は断る(attach していない volume は
   係が device の無い事で断る)。"
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
  (<- filled bool (place-filled owner doing target))
  (when filled
    (raise (! (refused owner doing (+ target " に中身が在る(volume の外に書かれた物 — 付けると隠すので付けない)")))))
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


(defk created-object [site owner doing kind body]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: kind str) (: body str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "Longhorn の物の種類 kind の一覧へ、本文 body の物を 1 つ作るため(201 だけを受ける — 同じ名が在る 409 も断る)。"
  (<- answer HttpResponse (kube-answer owner doing "POST" (! (longhorn-path site kind)) body JSON-TYPE))
  (when (!= (! (status-field answer.status)) 201)
    (raise (! (status-refusal owner doing (+ kind " の作成") answer))))
  None)


(defk snapshot-taken [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "作った Snapshot name が使えるようになるまで待つため(外れている volume は Longhorn が snapshot の ticket で付けて取る)。Longhorn は付け
   終わるまで error に「engine が動いていない — 付くのを待つ」を書くので、error は attach-seconds の間続いた時だけ失敗と読む。backup-seconds
   の内に終わらない時も断る(最後の error を挙げる)。"
  (<- started float (GetMonotonic))
  (var erring-since None)
  (var last-error "")
  (while True
    (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "snapshots")) "/" (url-quote name :safe "")) None JSON-TYPE))
    (<- now float (GetMonotonic))
    (match (! (status-field answer.status))
      200 (do (<- held (| SnapshotWire Malformed) (parse-json SnapshotWire (! (text-field answer.text))))
              (when (isinstance held Malformed)
                (raise (! (malformed-refusal owner doing "Snapshot の GET"))))
              (val status (or held.status (SnapshotStatusWire)))
              (when status.ready-to-use
                (return None))
              (if status.error
                  (do (:= last-error status.error)
                      (when (is erring-since None)
                        (:= erring-since now))
                      (when (> (- now erring-since) site.attach-seconds)
                        (raise (! (refused owner doing (.format "snapshot {} が失敗した: {}({} 秒続いた)" name status.error
                                                                site.attach-seconds))))))
                  (:= erring-since None)))
      404 None
      _ (raise (! (status-refusal owner doing "Snapshot の GET" answer))))
    (when (> (- now started) site.backup-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に snapshot {} が取れない(最後の error: {})" site.backup-seconds name
                                              (or last-error "無し"))))))
    (<- (Delay site.poll-seconds)))
  None)


(defk backup-finished [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "作った Backup name が完了するまで待つため(様子 Error・backup-seconds の内に終わらない時は断る — 完了を確かめずに volume を消さない)。"
  (<- started float (GetMonotonic))
  (while True
    (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "backups")) "/" (url-quote name :safe "")) None JSON-TYPE))
    (match (! (status-field answer.status))
      200 (do (<- held (| BackupWire Malformed) (parse-json BackupWire (! (text-field answer.text))))
              (when (isinstance held Malformed)
                (raise (! (malformed-refusal owner doing "Backup の GET"))))
              (val status (or held.status (BackupStatusWire)))
              (match status.state
                "Completed" (return None)
                "Error" (raise (! (refused owner doing (+ "backup " name " が失敗した: " (or status.error "訳なし")))))
                _ None))
      404 None
      _ (raise (! (status-refusal owner doing "Backup の GET" answer))))
    (<- now float (GetMonotonic))
    (when (> (- now started) site.backup-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に backup {} が終わらない" site.backup-seconds name)))))
    (<- (Delay site.poll-seconds)))
  None)


(defk deleted-volume [site owner doing name version]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: version str)] :post [(: % bool)]
   :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "volume name の Volume を、読んだ版 version の上でだけ消すため(DELETE の preconditions — 消した・既に無い = True・読んだ後に書きが
   入った 409 = False — 呼び手が読み直す)。"
  (val body (json.dumps {"apiVersion" "v1" "kind" "DeleteOptions" "preconditions" {"resourceVersion" version}} :sort-keys True))
  (<- answer HttpResponse (kube-answer owner doing "DELETE" (! (volume-path site name)) body JSON-TYPE))
  (match (! (status-field answer.status))
    200 True
    202 True
    404 True
    409 False
    _ (raise (! (status-refusal owner doing "Volume の削除" answer)))))


(defk tickets-of [name view]
  {:pre [(: name str) (: view AttachView)] :post [(: % (get tuple #(str ...)))] :tags {:context "doeff-cluster" :role "protocol"}}
  "volume name の attach の様子 view から、使っている印の ticket の id を得るため(この答え手の ticket と、冷やす手順の外のほかの ticket)。"
  (+ (if (is view.ticket-node None) #() #((! (ticket-id name)))) (! (using-tickets view.other-tickets))))


(defk engine-size [site owner doing name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str)] :post [(: % int) (>= % 0)]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の engine が今見せている block device の大きさ(byte)を、Longhorn の Engine の一覧で読むため(engine が無い・大きさが
   読めなければ 0)。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "engines"))
                                                            "?labelSelector=" (url-quote (+ "longhornvolume=" name) :safe ""))
                                       None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "Engine の一覧" answer))))
  (<- held (| EngineListWire Malformed) (parse-json EngineListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Engine の一覧"))))
  (max (gfor item (or held.items #())
             :if (and (is-not item.status None) (.isdigit item.status.current-size))
             (int item.status.current-size))
       :default 0))


(defk waited-engine [site owner doing name wanted]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: wanted int) (> wanted 0)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "名目の大きさを wanted に替えた volume name の engine が、その大きさの block device を見せるまで待つため(Longhorn は付いている volume を
   online で広げる — 2026-11-03 の本番の試しで 1 GiB → 2 GiB が約 10 秒。attach-seconds の内に広がらなければ断る)。"
  (<- started float (GetMonotonic))
  (while True
    (<- size int (engine-size site owner doing name))
    (when (>= size wanted)
      (return None))
    (<- now float (GetMonotonic))
    (when (> (- now started) site.attach-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に Longhorn が {} byte に広げない(engine は {} byte)" site.attach-seconds wanted size)))))
    (<- (Delay site.poll-seconds)))
  None)


(defk grown-volume [site owner path]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "path に付いた持ち主 owner の volume を、file system の空きが値の growBelowPercent を割っていれば、名目の大きさを倍(上限 maxSizeBytes)に
   広げるため(広げた = DONE・要らない・上限に居る・広げない値 = ALREADY)。名目の予約が増える分を札の disk が受けられなければ広げずに
   置き場の不足で断る(fresh False)。Volume の spec.size を書き、engine が広がるのを待ち、mount の係に file system を device の大きさまで
   広げさせる(resize2fs — 付けたまま)。前の広げが file system の前で止まっていた(名目が file system の 1.5 倍以上)なら、名目は替えずに
   file system だけを広げる(2 度倍にしない)。付いていない volume は断る。"
  (val doing "広げる")
  (<- name str (checked-name owner doing))
  (<- target str (checked-path owner doing path))
  (<- settings SettingsWire (workspace-settings site owner))
  (when (or (<= settings.grow-below-percent 0) (<= settings.max-size-bytes 0))
    (return WorkspaceOutcome.ALREADY))
  (<- entries tuple (mount-table site owner doing))
  (<- at (| MountEntry None) (mounted-at entries target))
  (when (is at None)
    (raise (! (refused owner doing (+ target " に何も付いていない")))))
  (<- found (| str None) (volume-of-entry at))
  (when (!= found name)
    (raise (! (refused owner doing (.format "{} に付いているのは volume {} でない({})" target name at.source)))))
  (<- usage (| DiskUsage FileFailed) (ReadDiskUsage target))
  (when (isinstance usage FileFailed)
    (raise (! (refused owner doing (+ target " の file system の空きが読めない: " usage.detail)))))
  (when (>= (* usage.free 100) (* usage.total settings.grow-below-percent))
    (return WorkspaceOutcome.ALREADY))
  (<- read (| VolumeRead None) (volume-read site owner doing name))
  (when (or (is read None) (<= read.size 0))
    (raise (! (refused owner doing "volume の名目の大きさが読めない"))))
  (val behind (>= (* read.size 2) (* usage.total 3)))
  (val wanted (if behind read.size (min settings.max-size-bytes (* 2 read.size))))
  (when (and (not behind) (<= wanted read.size))
    (return WorkspaceOutcome.ALREADY))
  (when (not behind)
    (<- parameters dict (class-parameters owner settings.storage-class))
    (<- (room-held site owner doing parameters (- wanted read.size) False))
    (val body (json.dumps {"spec" {"size" (str wanted)}} :ensure-ascii False :sort-keys True))
    (<- answer HttpResponse (kube-answer owner doing "PATCH" (! (volume-path site name)) body MERGE-PATCH-TYPE))
    (when (!= (! (status-field answer.status)) 200)
      (raise (! (status-refusal owner doing "Volume の大きさの書き" answer)))))
  (<- (waited-engine site owner doing name wanted))
  (<- grown KeeperAnswer (keeper-answer site owner doing "grow" name target))
  (when (!= grown.code 0)
    (raise (! (refused owner doing (+ "mount の係が file system を広げられなかった: " grown.notes)))))
  WorkspaceOutcome.DONE)


(defrecord Growth
  "付いている volume の空きを読んで広げた結末: grown = 広げた volume の名・failed = 広げられなかった volume の「<名>: <訳>」。"
  (#^ (get tuple #(str ...)) grown)
  (#^ (get tuple #(str ...)) failed))


(defk mounted-growth [site]
  {:pre [(: site WorkspaceVolumeSite)] :post [(: % Growth)] :tags {:context "doeff-cluster" :role "protocol"}}
  "この機体に付いている作業場所の volume を mount の表から読み、file system の空きが値の growBelowPercent を割った物を 1 つずつ広げるため
   (冷やす回 — 上に載る系が数分ごとに出す CoolIdleWorkspaceVolumes の program の回 — が呼ぶ。file system の空きの減りは知らせを
   出さないので、付けている間は冷やす回ごとに読む。広げられなかった物は訳を残して次へ進む)。"
  (val owner "全部")
  (val doing "広げる物の読み")
  (<- entries tuple (mount-table site owner doing))
  (var grown #())
  (var failed #())
  (for [entry entries]
    (<- name (| str None) (volume-of-entry entry))
    (when (and (is-not name None) (.startswith name NAME-PREFIX))
      (try
        (<- outcome WorkspaceOutcome (grown-volume site (cut name (len NAME-PREFIX) None) entry.mount-point))
        (when (= outcome WorkspaceOutcome.DONE)
          (:= grown (+ grown #(name))))
        (except [error WorkspaceVolumeRefused]
          (:= failed (+ failed #((+ name ": " (str error)))))))))
  (Growth :grown grown :failed failed))


(defk cooled-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol" :spells "json"}}
  "外れている持ち主 owner の volume を冷やすため(README の節「寿命」): Snapshot と Backup(名 <名>-cool-<刻>・label backup-volume と
   backup-target)を作って完了を待ち、Volume を読み(版)、VolumeAttachment に使う ticket が無い事を確かめてから、その版の上でだけ消し、
   消え終わるまで待つ(DONE)。volume が無ければ ALREADY(冷えているか、まだ作っていない)。使っている(ticket が在る・付いている)・
   戻す途中・ほかの冷やす手順の途中の volume は触らずに断る。冷やす途中に使い始めたら、volume を消さずに断る(backup は残る)。"
  (val doing "冷やす")
  (<- name str (checked-name owner doing))
  (<- read (| VolumeRead None) (volume-read site owner doing name))
  (cond
    (is read None) (return WorkspaceOutcome.ALREADY)
    read.deleting (do (<- (waited-gone site owner doing name))
                      (return WorkspaceOutcome.ALREADY))
    read.restoring (raise (! (refused owner doing "backup から戻す途中(使い始める volume は冷やさない)")))
    (not read.backup-target) (raise (! (refused owner doing "volume に backupTargetName が無い(backup の先が決まらない)"))))
  (<- seen (| AttachRead None) (attach-read site owner doing name))
  (when (is seen None)
    (return WorkspaceOutcome.ALREADY))
  (<- users tuple (tickets-of name seen.view))
  (when users
    (raise (! (refused owner doing (+ "使っている(ticket " (.join "・" users) ")")))))
  (when (or seen.view.other-tickets (!= seen.view.state "detached"))
    (raise (! (refused owner doing (.format "ほかの冷やす手順か付け外しの途中(volume {}・ticket {})" (or seen.view.state "?")
                                            (or (.join "・" seen.view.other-tickets) "無し"))))))
  (<- now datetime (GetTime))
  (val mark (! (cooling-name name (int (.timestamp now)))))
  (<- (created-object site owner doing "snapshots"
                      (json.dumps {"apiVersion" "longhorn.io/v1beta2" "kind" "Snapshot"
                                   "metadata" {"name" mark "namespace" site.longhorn-namespace}
                                   "spec" {"volume" name "createSnapshot" True}}
                                  :ensure-ascii False :sort-keys True)))
  (<- (snapshot-taken site owner doing mark))
  (<- (created-object site owner doing "backups"
                      (json.dumps {"apiVersion" "longhorn.io/v1beta2" "kind" "Backup"
                                   "metadata" {"name" mark "namespace" site.longhorn-namespace
                                               "labels" {"backup-volume" name "backup-target" read.backup-target}}
                                   "spec" {"snapshotName" mark "labels" {}}}
                                  :ensure-ascii False :sort-keys True)))
  (<- (backup-finished site owner doing mark))
  (<- started float (GetMonotonic))
  (while True
    (<- fresh (| VolumeRead None) (volume-read site owner doing name))
    (when (is fresh None)
      (return WorkspaceOutcome.DONE))
    (<- after (| AttachRead None) (attach-read site owner doing name))
    (when (is after None)
      (return WorkspaceOutcome.DONE))
    (<- late tuple (tickets-of name after.view))
    (when late
      (raise (! (refused owner doing (.format "冷やす途中に使い始めた(ticket {})— volume を消さない(backup {} は残す)" (.join "・" late) mark)))))
    (<- deleted bool (deleted-volume site owner doing name fresh.version))
    (when deleted
      (<- (waited-gone site owner doing name))
      (return WorkspaceOutcome.DONE))
    (<- now float (GetMonotonic))
    (when (> (- now started) site.detach-seconds)
      (raise (! (refused owner doing (.format "{} 秒の内に、読んだ版の上で volume を消せない(書きが続く)" site.detach-seconds)))))
    (<- (Delay site.poll-seconds)))
  WorkspaceOutcome.DONE)


(defk backup-volume-names [site owner doing name target]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: name str) (: target str)] :post [(: % (get tuple #(str ...)))]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "volume name の backup の先 target の BackupVolume(backup の束 — 名は <volume の名>-<hash>)の名を、名の順に読むため。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "backupvolumes")) (! (label-query name target)))
                                       None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "BackupVolume の一覧" answer))))
  (<- held (| NamedListWire Malformed) (parse-json NamedListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "BackupVolume の一覧"))))
  (tuple (sorted (gfor item (or held.items #()) :if (and item.metadata item.metadata.name) item.metadata.name))))


(defk deleted-object [site owner doing kind name]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: kind str) (: name str)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "protocol"}}
  "Longhorn の物の種類 kind の name を消すため(消した・既に無いを受ける)。"
  (<- answer HttpResponse (kube-answer owner doing "DELETE" (+ (! (longhorn-path site kind)) "/" (url-quote name :safe "")) None JSON-TYPE))
  (when (not-in (! (status-field answer.status)) #(200 202 404))
    (raise (! (status-refusal owner doing (+ kind " " name " の削除") answer))))
  None)


(defk purged-volume [site owner]
  {:pre [(: site WorkspaceVolumeSite) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "protocol"}}
  "冷えた持ち主 owner の backup を消すため(README の節「寿命」の「消す」— 上に載る系が「もう使わない」と決めた時): その名の BackupVolume を
   消す(Longhorn が束の backup を backup の先から消す。束が無く Backup だけが在れば Backup を 1 つずつ消す)— DONE。backup が無ければ
   ALREADY。Longhorn の volume が在る(温かい・使っている)・途中の backup が在る・最後の backup から値の keepDays を過ぎていない時は断る。"
  (val doing "消す")
  (<- name str (checked-name owner doing))
  (<- read (| VolumeRead None) (volume-read site owner doing name))
  (when (is-not read None)
    (raise (! (refused owner doing "Longhorn の volume が在る(温かいか使っている — 先に冷やす)"))))
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (<- target str (class-backup-target owner doing parameters))
  (<- backups tuple (backup-views site owner doing name target))
  (<- bundles tuple (backup-volume-names site owner doing name target))
  (when (and (not backups) (not bundles))
    (return WorkspaceOutcome.ALREADY))
  (val unfinished (lfor one backups :if (not-in one.state #("Completed" "Error")) one.name))
  (when unfinished
    (raise (! (refused owner doing (+ "途中の backup が在る: " (.join "・" unfinished))))))
  (<- now datetime (GetTime))
  (var stamps #())
  (for [one backups]
    (<- stamp (| float None) (epoch-of one.created-at))
    (when (is-not stamp None)
      (:= stamps (+ stamps #(stamp)))))
  (when stamps
    (val days (/ (- (.timestamp now) (max stamps)) 86400))
    (when (< days settings.keep-days)
      (raise (! (refused owner doing (.format "最後の backup から {:.1f} 日(keepDays {} 日を過ぎるまで消さない)" days settings.keep-days))))))
  (if bundles
      (for [bundle bundles]
        (<- (deleted-object site owner doing "backupvolumes" bundle)))
      (for [one backups]
        (<- (deleted-object site owner doing "backups" one.name))))
  WorkspaceOutcome.DONE)


(defk volume-reads [site owner doing]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str)] :post [(: % (get tuple #(VolumeRead ...)))]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "Longhorn の Volume のうち、名が作業場所の volume の頭(NAME-PREFIX)で始まる物を、名の順に読むため。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (! (longhorn-path site "volumes")) None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "Volume の一覧" answer))))
  (<- held (| VolumeListWire Malformed) (parse-json VolumeListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Volume の一覧"))))
  (var reads #())
  (for [item (or held.items #())]
    (when (and item.metadata (.startswith item.metadata.name NAME-PREFIX))
      (<- one VolumeRead (volume-read-of owner doing item))
      (:= reads (+ reads #(one)))))
  (tuple (sorted reads :key (fn [one] one.name))))


(defk disk-rooms [site owner doing tags]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: tags (get tuple #(str ...)))] :post [(: % (get tuple #(DiskRoom ...)))]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "Longhorn の Node の disk のうち、級の札 tags を全部持ち、置いてよく(allowScheduling)、Schedulable の condition が True の物の置き場の数
   を読むため(Longhorn が級の volume を置ける disk — 名の順)。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (! (longhorn-path site "nodes")) None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing "Longhorn の Node の一覧" answer))))
  (<- held (| NodeListWire Malformed) (parse-json NodeListWire (! (text-field answer.text))))
  (when (isinstance held Malformed)
    (raise (! (malformed-refusal owner doing "Longhorn の Node の一覧"))))
  (var rooms #())
  (for [node (or held.items #())]
    (val specs (or (and node.spec node.spec.disks) {}))
    (val statuses (or (and node.status node.status.disk-status) {}))
    (for [disk (sorted specs)]
      (val spec (get specs disk))
      (val status (.get statuses disk))
      (when (and spec.allow-scheduling (is-not status None) (<= (set tags) (set (or spec.tags #())))
                 (any (gfor condition (or status.conditions #()) (and (= condition.type "Schedulable") (= condition.status "True")))))
        (:= rooms (+ rooms #((DiskRoom :maximum status.storage-maximum :reserved spec.storage-reserved
                                       :scheduled status.storage-scheduled :available status.storage-available)))))))
  rooms)


(defk longhorn-percentage [site owner doing setting]
  {:pre [(: site WorkspaceVolumeSite) (: owner str) (: doing str) (: setting str)] :post [(: % int)]
   :tags {:context "doeff-cluster" :role "protocol" :reads "json"}}
  "Longhorn の % の設定 setting(storage-over-provisioning-percentage = disk に名目で置ける量の %・storage-minimal-available-percentage =
   置く disk に残す実の空きの %)を読むため(読めない・数でなければ断る)。"
  (<- answer HttpResponse (kube-answer owner doing "GET" (+ (! (longhorn-path site "settings")) "/" (url-quote setting :safe "")) None JSON-TYPE))
  (when (!= (! (status-field answer.status)) 200)
    (raise (! (status-refusal owner doing (+ "Longhorn の設定 " setting " の GET") answer))))
  (<- held (| LonghornSettingWire Malformed) (parse-json LonghornSettingWire (! (text-field answer.text))))
  (when (or (isinstance held Malformed) (not (.isdigit held.value)))
    (raise (! (malformed-refusal owner doing (+ "Longhorn の設定 " setting)))))
  (int held.value))


(defk workspace-room [site]
  {:pre [(: site WorkspaceVolumeSite)] :post [(: % WorkspaceRoom)] :tags {:context "doeff-cluster" :role "protocol"}}
  "札の disk が受けられる、値の大きさの volume の残りの本数と、値の spareVolumes を読むため(ReadWorkspaceRoom の答え — 読めなければ断る)。"
  (val owner "全部")
  (val doing "置き場の読み")
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (<- slots int (room-slots site owner doing parameters settings.size-bytes))
  (WorkspaceRoom :slots slots :spare settings.spare-volumes))


(defk idle-cooling [site]
  {:pre [(: site WorkspaceVolumeSite)] :post [(: % CoolingSweep)] :tags {:context "doeff-cluster" :role "protocol"}}
  "外れている作業場所の volume のうち、冷やす順(cooling-order — 使い終わりから warmHours を過ぎた物と、札の disk の空き(Longhorn の置く線
   2 つの内 — room-slots)が spareVolumes を割る分)の物を、古い順に 1 つずつ冷やし、その後に機体に付いている volume の空きを読んで広げる
   ため(mounted-growth — 冷やして空けた置き場から広げる。冷やせなかった・広げられなかった物は訳を残して次へ進む)。読めない時は断る。"
  (val owner "全部")
  (val doing "冷やす順の読み")
  (<- settings SettingsWire (workspace-settings site owner))
  (<- parameters dict (class-parameters owner settings.storage-class))
  (<- reads tuple (volume-reads site owner doing))
  (val warm (tuple (gfor one reads :if (and (= one.state "detached") (not one.restoring) (not one.deleting))
                         (WarmVolume :name one.name :used-at one.used-at))))
  (<- slots int (room-slots site owner doing parameters settings.size-bytes))
  (<- now datetime (GetTime))
  (<- order tuple (cooling-order warm (.timestamp now) (* settings.warm-hours 3600.0) slots settings.spare-volumes))
  (var cooled #())
  (var failed #())
  (for [name order]
    (try
      (<- outcome WorkspaceOutcome (cooled-volume site (cut name (len NAME-PREFIX) None)))
      (when (= outcome WorkspaceOutcome.DONE)
        (:= cooled (+ cooled #(name))))
      (except [error WorkspaceVolumeRefused]
        (:= failed (+ failed #((+ name ": " (str error))))))))
  (<- growth Growth (mounted-growth site))
  (CoolingSweep :slots slots :cooled cooled :failed (+ failed growth.failed) :grown growth.grown))


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
    (resume outcome))
  (CoolWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (cooled-volume site owner))
    (resume outcome))
  (PurgeWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (purged-volume site owner))
    (resume outcome))
  (CoolIdleWorkspaceVolumes []
    (<- sweep CoolingSweep (idle-cooling site))
    (resume sweep))
  (ReadWorkspaceRoom []
    (<- room WorkspaceRoom (workspace-room site))
    (resume room))
  (ReadWorkspacePlace [owner path]
    (<- place WorkspacePlace (place-read site owner path))
    (resume place)))
