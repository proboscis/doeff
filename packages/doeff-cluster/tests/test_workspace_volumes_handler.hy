;;; 作業場所の volume の effect(doeff_cluster._shared.intent.workspace_volume_model)の検 — 本番の答え手(doeff_cluster._shared.protocol.
;;; workspace_volumes の workspace-volumes-api)を偽の API server(Longhorn の Volume・VolumeAttachment・値の ConfigMap・級)と偽の mount の係
;;; (頼みの dir の file を読んで答える)の上で回し、模擬の答え手(doeff_cluster._sim.workspace_volumes の held-workspace-volumes)と同じ
;;; 筋書きに同じ結末で答え、同じ volume の様子を残す事を確かめる。
;;;
;;;   通し              作る → attach → mount → umount → detach を 2 度ずつ(2 度目は ALREADY)。最後の様子は外れて file system を持つ
;;;   失敗ケース        名の条件を外れる id(API server にも係にも問わない)・無い volume の attach・別の node の volume の attach と detach
;;;                     (ticket を書き換えない)・attach していない volume の mount・付ける先に別の volume・別の path に付いた volume の mount・
;;;                     使っている process の在る umount・mount の残る detach(ticket を外さない)・届かない API server
;;;   本番だけ          ticket が満たされるまで待つ・満たされなければ上限の秒で断る(Longhorn の訳つき)・答えない係は上限の秒で断り、頼みを
;;;                     引く・級の値の写し(本文)・写せない級は断る・読んだ版の上にだけ書く(409 で読み直す)・外れる途中は待つ・
;;;                     読めない token は断り、断りの文に token が載らない・要求は worker の身元で出す
;;;   冷やす・戻す・消す 冷やして戻す通し(本番と模擬)・使っている volume は冷やさない・完了した backup の無い名は空で作らない・消すのは
;;;                     keepDays の後・まとめて冷やす順と空きの数(本番と模擬)。本番だけ: snapshot と backup の失敗で消さない・読んだ版が
;;;                     古い削除は読み直す・札の違う disk を数えない
;;;   付ける先の様子    付ける先の様子の読み(MOUNTED・READY・OUTSIDE・CLASH — 本番と模擬)・中身の在る dir には付けない(隠さない)
;;;   置き場の数え      札の disk の残りが 0 なら作成も戻しも POST せずに置き場の不足で断る(fresh か否か — 本番と模擬)・残りの読み・
;;;                     実の空きの線で断る・付ける時に Longhorn が置けないと答えた空の volume は待たずに消す・replica の置かれた volume は
;;;                     消さない・置けない戻しは消して backup を残す・戻す volume は backup の大きさで作る
;;;   広げる            (本番だけ — 模擬の世界は file system の空きを持たない)冷やす回が、付いた volume の空きが線を割れば名目を倍に
;;;                     書き、engine を待って係に file system を広げさせる・空きの在る物と上限の物は広げない・置き場が足りなければ
;;;                     書かない・前の広げの続きは倍にしない・engine が広がらなければ係に頼まずに断る
;;;   並べて走らせる    (Spawn と模擬の時計)戻す途中に同じ持ち主の次の頼み(メッセージ)が来たら、先の戻しに相乗りして戻し終わりを待つ
;;;                     (戻すのは 1 度)・冷やす途中に使い始めたら冷やす側が退き volume が残る・消す途中に使い始めたら消え終わりを待って
;;;                     戻す・ticket を置いた直後に消された attach は作り直しを頼む断りを返す
;;; 外の世界は、この file の Longhorn の相手役と mount の係の相手役・memory の file・模擬の時計。級の parameters と値の ConfigMap の中身は
;;; 宣言の file(deploy/k8s/workspace-volumes の storageclass.yaml と workspace-volume.json)から読む。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass replace])  ; defrecord の展開が名指す・相手役の様子の書き換え
(import datetime [datetime timezone])
(import json)
(import pathlib [Path])
(import urllib.parse [parse-qs urlsplit])
(import yaml)
(import doeff [with_handlers])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [state])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [DiskUsage FileFailed PathKind PathStat ReadDiskUsage ReadText RemoveTree RenamePath StatPath WriteText])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_core_effects [Spawn Task Gather])
(import doeff_time [Delay GetTime SimClock sim-time-handler])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CoolIdleWorkspaceVolumes CoolingSweep
                                                           CoolWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume PurgeWorkspaceVolume ReadWorkspacePlace
                                                           ReadWorkspaceRoom UmountWorkspaceVolume WorkspaceOutcome WorkspacePlace
                                                           WorkspaceRoom WorkspaceRoomShort WorkspaceVolumeRefused WorkspaceVolumeSite])
(import doeff_cluster._shared.core.workspace_volume_rules [USED-AT-KEY ticket-id volume-name])
(import doeff_cluster._shared.protocol.workspace_volumes [workspace-volumes-api])
(import doeff_cluster._shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-TOKEN-FILE])
(import doeff_cluster._sim.workspace_volumes [HeldBackup HeldVolume WorkspaceVolumeWorld held-volume held-workspace-volumes put-volume])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val DECLARED (/ (. (Path __file__) parent parent) "deploy" "k8s" "workspace-volumes"))
(val SETTINGS-TEXT (.read-text (/ DECLARED "workspace-volume.json") :encoding "utf-8"))
(val PARAMETERS (dict (get (yaml.safe-load (.read-text (/ DECLARED "storageclass.yaml") :encoding "utf-8")) "parameters")))
(val TOKEN "worker-identity-token")
(val NODE "zeus")
(val OWNER "c-EXAMPLEOWNERID000000000001")
(val NAME "ws-c-exampleownerid000000000001")
(val OTHER "c-EXAMPLEOTHERID00000000002")
(val PLACE "/work/agent-workspaces/a-k973")
(val PLACE-2 "/work/agent-workspaces/a-k973-2")
;; 検の場所: 本番と同じ path・短い秒(待つ筋書きが模擬の時計で速く終わる)。
(val SITE (WorkspaceVolumeSite :longhorn-namespace "longhorn-system" :settings-namespace "agent-worker" :settings-name "workspace-volume"
                               :mount-dir "/run/workspace-mount" :mountinfo "/proc/self/mountinfo"
                               :attach-seconds 5.0 :detach-seconds 5.0 :mount-seconds 5.0 :backup-seconds 5.0 :restore-seconds 5.0
                               :poll-seconds 0.5))
(val SIZE (get (json.loads SETTINGS-TEXT) "sizeBytes"))
(val MAX (get (json.loads SETTINGS-TEXT) "maxSizeBytes"))
(val TARGET (get PARAMETERS "backupTargetName"))
(val DAY 86400.0)
(val LONGHORN-HEAD "/apis/longhorn.io/v1beta2/namespaces/longhorn-system")
(val VOLUMES-PATH (+ LONGHORN-HEAD "/volumes"))
(val ATTACHMENTS-PATH (+ LONGHORN-HEAD "/volumeattachments"))
(val SNAPSHOTS-PATH (+ LONGHORN-HEAD "/snapshots"))
(val BACKUPS-PATH (+ LONGHORN-HEAD "/backups"))
(val BUNDLES-PATH (+ LONGHORN-HEAD "/backupvolumes"))
(val NODES-PATH (+ LONGHORN-HEAD "/nodes"))
(val OVER-PROVISIONING-PATH (+ LONGHORN-HEAD "/settings/storage-over-provisioning-percentage"))
(val MINIMAL-AVAILABLE-PATH (+ LONGHORN-HEAD "/settings/storage-minimal-available-percentage"))
(val REPLICAS-PATH (+ LONGHORN-HEAD "/replicas"))
(val ENGINES-PATH (+ LONGHORN-HEAD "/engines"))
;; BackupVolume の名の尻(Longhorn は <volume の名>-<hash> にする — 相手役は固定の hash)。
(val BUNDLE-TAIL "-5a2925a0")
(val SETTINGS-PATH "/api/v1/namespaces/agent-worker/configmaps/workspace-volume")
(val CLASS-PATH "/apis/storage.k8s.io/v1/storageclasses/longhorn-workspace")
(val WORKER-FILES (MemoryFiles :files #((MemoryFile :path SERVICE-ACCOUNT-TOKEN-FILE :content (.encode (+ TOKEN "\n") "utf-8")))
                               :dirs #("/run/workspace-mount" "/work/agent-workspaces")))


(defrecord SeenRequest
  "Longhorn の相手役が受けた要求 1 つ: method・path(API server の頭を外した物)・content-type・authorization・body = 本文の字。"
  (#^ str method)
  (#^ str path)
  (#^ (| str None) content-type)
  (#^ (| str None) authorization)
  (#^ str body))


(defrecord Step
  "筋書きの一手: verb = create・attach・mount・umount・detach・cool・purge・sweep・room・place・wait・owner = 持ち主の id(sweep と wait は使わない)・
   path = 付ける先(mount と umount)・seconds = 待つ秒(wait)。"
  (#^ str verb)
  (setv #^ str owner "")
  (setv #^ (| str None) path None)
  (setv #^ float seconds 0.0))


(defrecord Pending
  "相手役の Longhorn の途中の仕事 1 つ: volume = volume の名・left = 終わるまでの読みの残り。"
  (#^ str volume)
  (#^ int left))


(defrecord ApiRan
  "本番の答え手の走り 1 つ: outcomes = 結末の組・left = 終わりの memory の file。"
  (#^ tuple outcomes)
  (#^ MemoryFiles left))


(defclass LonghornWorld []
  "Longhorn の相手役と mount の係の相手役の世界: held = volume と backup の様子(模擬の答え手と同じ入れ物)・parameters = 級の parameters・
   settle = ticket を置いた・外した後に Longhorn が付け終わる・外し終わるまでの読みの数・stuck = 付け終わらない・races = 先に 409 で断る
   書きの数・silent = 係が答えない・restore-settle と snapshot-settle と backup-settle と dying-settle = 戻し・snapshot・backup・消しが
   終わるまでの読みの数・snapshot-error と backup-error = 失敗する・stale-deletes = 版が古いと断る削除の数・vanish-after-ticket =
   ticket を置いた直後に volume が消される回数(冷やす側の削除が間に入った)・extra-disks = 札の違う disk の組(数えない disk)・
   available = 札 workspace の disk の実の空き(byte — None = 空の disk で storageMaximum と同じ)・minimal = 設定 storage-minimal-available-
   percentage・unplaceable = 次に replica の置き先を決める時(空の volume を初めて付ける・backup から戻す)に、Longhorn が置けないと答える
   回数(作る前の数えと付けの間に置き場が尽きた世界)・backup-size = backup が覚える volume の名目の大きさ(None = 値の大きさ)・
   engine-settle = 名目の大きさを書いた後に engine が広がるまでの読みの数・engine-stuck = engine が広がらない・sizes = volume の名目の
   大きさ(名 → byte — 無ければ値の大きさ)・fs-sizes = 付いた volume の file system の総量(名 → byte — 無ければ値の大きさ)・used =
   file system の使用(名 → byte — 無ければ 0)。名目の予約の線は過剰割り当て 100% で、札 workspace の disk の storageMaximum は
   capacity 本ぶん(模擬の世界の残り capacity − volume の数と揃う)・storageScheduled は名目の大きさの和。走らせた後に読む物: pending = 付け外しの途中の読みの残り・leaving = 外す途中の volume の元の node・version = VolumeAttachment の
   版・revision = Volume の版・restoring と sources = 戻す途中の読みの残りと戻す元・dying = 消す途中の読みの残り・snapshots と backups =
   途中の snapshot と backup・unplaced = replica をまだどこにも置いていない volume の名・failing = Longhorn が置けないと答えている volume の
   名・engine-left = 名目の大きさを書いた後に engine が広がるまでの読みの残り・created = 作成の本文・seen = 受けた要求・asked = 係が
   受けた頼み。"
  (defn #^ None __init__ [self #^ WorkspaceVolumeWorld held #^ dict [parameters None] #^ int [settle 0] #^ bool [stuck False]
                          #^ int [races 0] #^ bool [silent False] #^ int [restore-settle 0] #^ int [snapshot-settle 0]
                          #^ int [backup-settle 0] #^ int [dying-settle 0] #^ bool [snapshot-error False] #^ bool [backup-error False]
                          #^ int [stale-deletes 0] #^ int [vanish-after-ticket 0] #^ tuple [extra-disks #()]
                          #^ (| int None) [available None] #^ int [minimal 25] #^ int [unplaceable 0] #^ (| int None) [backup-size None]
                          #^ int [engine-settle 0] #^ bool [engine-stuck False] #^ (| dict None) [sizes None] #^ (| dict None) [fs-sizes None]
                          #^ (| dict None) [used None]]
    ;; defk にできない: class の作り手(Python が呼ぶ口)
    "相手役の世界を作るため。"
    (setv self.held held
          self.parameters (if (is parameters None) PARAMETERS parameters)
          self.settle settle self.stuck stuck self.races races self.silent silent
          self.restore-settle restore-settle self.snapshot-settle snapshot-settle self.backup-settle backup-settle
          self.dying-settle dying-settle self.snapshot-error snapshot-error self.backup-error backup-error
          self.stale-deletes stale-deletes self.vanish-after-ticket vanish-after-ticket self.extra-disks extra-disks
          self.available available self.minimal minimal self.unplaceable unplaceable
          self.backup-size (if (is backup-size None) SIZE backup-size)
          self.engine-settle engine-settle self.engine-stuck engine-stuck
          self.sizes (dict (or sizes {})) self.fs-sizes (dict (or fs-sizes {})) self.used (dict (or used {})) self.engine-left {}
          self.pending {} self.leaving {} self.version 1 self.revision 1
          self.restoring {} self.sources {} self.dying {} self.snapshots {} self.backups {}
          self.unplaced (set) self.failing (set)
          #^ (get tuple #(str ...)) self.created #()
          #^ (get tuple #(SeenRequest ...)) self.seen #()
          #^ (get tuple #(str ...)) self.asked #())))


(defk api-answer [status payload]
  {:pre [(: status int) (: payload dict)] :post [(: % HttpResponse)]}
  "相手役の答え(status と JSON の値)を HTTP の応答にするため。"
  (val text (json.dumps payload))
  (HttpResponse status {} (.encode text "utf-8") text "" 0.0))


(defk iso-of [epoch]
  {:pre [(: epoch float)] :post [(: % str)]}
  "epoch 秒を k8s の刻の字(ISO 8601 の UTC・尻の Z)にするため。"
  (+ (.isoformat (.replace (datetime.fromtimestamp epoch timezone.utc) :tzinfo None)) "Z"))


(defk changed [world volume]
  {:pre [(: world LonghornWorld) (: volume HeldVolume)] :post [(: % None)]}
  "相手役の Longhorn が volume の様子を書き換え、Volume の版を進めるため。"
  (<- (put-volume world.held volume))
  (setv world.revision (+ world.revision 1))
  None)


(defk helper-tickets [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % dict)]}
  "volume name の途中の snapshot と backup のために Longhorn が置く ticket(id → ticket)を得るため。"
  (| (dfor #(snap work) (.items world.snapshots) :if (and (= work.volume name) (> work.left 0))
           (+ "snapshot-controller-" snap) {"id" (+ "snapshot-controller-" snap) "type" "snapshot-controller" "nodeID" NODE})
     (dfor #(backup work) (.items world.backups) :if (and (= work.volume name) (> work.left 0))
           (+ "backup-controller-" backup) {"id" (+ "backup-controller-" backup) "type" "backup-controller" "nodeID" NODE})))


(defk volume-payload [world volume]
  {:pre [(: world LonghornWorld) (: volume HeldVolume)] :post [(: % dict)]}
  "Volume 1 つの GET の本文を、世界の様子から綴るため(付け外しの途中は attaching・detaching・戻す途中は attached で restoreRequired)。"
  (val name volume.name)
  (val waiting (or world.stuck (> (.get world.pending name 0) 0)))
  (val restoring (> (.get world.restoring name 0) 0))
  (val helping (bool (! (helper-tickets world name))))
  (val status (cond
                restoring {"state" "attached" "currentNodeID" NODE}
                (and (in name world.leaving) waiting) {"state" "detaching" "currentNodeID" (get world.leaving name)}
                (is-not volume.node None) {"state" (if waiting "attaching" "attached") "currentNodeID" volume.node}
                helping {"state" "attached" "currentNodeID" NODE}
                True {"state" "detached" "currentNodeID" ""}))
  (val restore (if (in name world.sources) {"restoreRequired" restoring "restoreInitiated" True} {}))
  ;; Longhorn v1.13 は置き先を決められない volume に condition Scheduled False(reason ReplicaSchedulingFailure)を書く。
  (val placing (if (in name world.failing)
                   {"type" "Scheduled" "status" "False" "reason" "ReplicaSchedulingFailure"
                    "message" "precheck new replica failed: insufficient storage"}
                   {"type" "Scheduled" "status" "True" "reason" "" "message" ""}))
  {"metadata" (| {"name" name "resourceVersion" (str world.revision) "creationTimestamp" (! (iso-of 0.0))
                  "annotations" {USED-AT-KEY (str (int volume.used-at))}}
                 (if (in name world.dying) {"deletionTimestamp" (! (iso-of 0.0))} {}))
   "spec" (| {"backupTargetName" TARGET "size" (str (.get world.sizes name SIZE))}
             (if (in name world.sources) {"fromBackup" (get world.sources name)} {}))
   "status" (| status restore {"conditions" [placing]})})


(defk volume-answer [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % HttpResponse)]}
  "Volume の GET に、世界の様子から答えるため(読むたびに戻しと消しの途中の残りを 1 つ減らし、終われば戻した・消えた様子にする)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val payload (! (volume-payload world volume)))
  ;; 置き先の無い戻しは進まない(replica が無い)。
  (when (and (in name world.restoring) (not-in name world.failing))
    (val restore-left (max 0 (- (get world.restoring name) 1)))
    (setv (get world.restoring name) restore-left)
    (when (= restore-left 0)
      (del (get world.restoring name))
      (setv world.revision (+ world.revision 1))))
  (when (in name world.dying)
    (val dying-left (max 0 (- (get world.dying name) 1)))
    (setv (get world.dying name) dying-left)
    (when (= dying-left 0)
      (del (get world.dying name))
      (<- (forgotten world name))))
  (! (api-answer 200 payload)))


(defk forgotten [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % None)]}
  "相手役の Longhorn が volume name を消し終えた様子にするため(volume と、その replica の置き先と戻しの覚え)。"
  (setv world.held.volumes (tuple (gfor one world.held.volumes :if (!= one.name name) one)))
  (.discard world.unplaced name)
  (.discard world.failing name)
  (.pop world.restoring name None)
  (.pop world.sources name None)
  None)


(defk placement-decided [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % None)]}
  "replica をまだ置いていない volume name の置き先を、相手役の Longhorn が決める時(初めて付ける・戻す)の様子にするため(unplaceable の
   回数だけ置けないと答え、ほかは置く)。"
  (when (in name world.unplaced)
    (if (> world.unplaceable 0)
        (do (setv world.unplaceable (- world.unplaceable 1))
            (.add world.failing name))
        (.discard world.unplaced name)))
  None)


(defk attachment-answer [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % HttpResponse)]}
  "VolumeAttachment の GET に、世界の様子から答えるため(読むたびに付け外しの途中の残りを 1 つ減らす)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val waiting (or world.stuck (> (.get world.pending name 0) 0) (> (.get world.restoring name 0) 0) (in name world.failing)))
  (val id (! (ticket-id name)))
  (val tickets (| (if (is volume.node None) {} {id {"id" id "type" "longhorn-api" "nodeID" volume.node}}) (! (helper-tickets world name))))
  (val statuses (if (is volume.node None)
                    {}
                    {id {"id" id "satisfied" (not waiting)
                         "conditions" [{"type" "Satisfied" "status" (if waiting "False" "True")
                                        "message" (if waiting "waiting for the engine of the volume" "")}]}}))
  (val left (max 0 (- (.get world.pending name 0) 1)))
  (setv (get world.pending name) left)
  (when (and (= left 0) (in name world.leaving))
    (del (get world.leaving name)))
  (! (api-answer 200 {"metadata" {"name" name "resourceVersion" (str world.version)}
                      "spec" {"attachmentTickets" tickets "volume" name}
                      "status" {"attachmentTicketStatuses" statuses}})))


(defk attachment-patched [world name body]
  {:pre [(: world LonghornWorld) (: name str) (: body str)] :post [(: % HttpResponse)]}
  "VolumeAttachment の merge patch に、版を照らして答えるため(この答え手の ticket だけを書ける)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val patch (json.loads body))
  (when (or (!= (get patch "metadata" "resourceVersion") (str world.version)) (> world.races 0))
    (setv world.races (max 0 (- world.races 1)) world.version (+ world.version 1))
    (return (! (api-answer 409 {"kind" "Status" "reason" "Conflict"}))))
  (val tickets (get patch "spec" "attachmentTickets"))
  (assert (= (list tickets) [(! (ticket-id name))]) tickets)
  (val ticket (get tickets (! (ticket-id name))))
  (if (is ticket None)
      (do (setv (get world.leaving name) volume.node)
          (<- (changed world (replace volume :node None))))
      (do (assert (= (get ticket "type") "longhorn-api") ticket)
          (<- (placement-decided world name))
          (<- (changed world (replace volume :node (get ticket "nodeID"))))))
  (setv (get world.pending name) world.settle world.version (+ world.version 1))
  ;; 冷やす側の削除が、ticket を置いた後・印を書く前に入った世界(頭の註の窓)。
  (when (and (> world.vanish-after-ticket 0) (is-not ticket None))
    (setv world.vanish-after-ticket (- world.vanish-after-ticket 1)
          world.held.volumes (tuple (gfor one world.held.volumes :if (!= one.name name) one))))
  (! (api-answer 200 {})))


(defk volume-patched [world name body]
  {:pre [(: world LonghornWorld) (: name str) (: body str)] :post [(: % HttpResponse)]}
  "Volume の merge patch(使った刻の印の annotation か、名目の大きさ spec.size — 付いている volume だけ広げられる)に答えるため。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val patch (json.loads body))
  (when (= (list patch) ["spec"])
    (assert (= (list (get patch "spec")) ["size"]) patch)
    (assert (is-not volume.node None) volume)
    (setv (get world.sizes name) (int (get patch "spec" "size"))
          (get world.engine-left name) world.engine-settle)
    (<- (changed world volume))
    (return (! (api-answer 200 {}))))
  (assert (= (list patch) ["metadata"]) patch)
  (val mark (get patch "metadata" "annotations" USED-AT-KEY))
  (<- (changed world (replace volume :used-at (float mark))))
  (! (api-answer 200 {})))


(defk volume-deleted [world name body]
  {:pre [(: world LonghornWorld) (: name str) (: body str)] :post [(: % HttpResponse)]}
  "Volume の DELETE に、preconditions の版を照らして答えるため(消す途中の読みの数だけ deletionTimestamp を見せてから消える)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val version (get (json.loads body) "preconditions" "resourceVersion"))
  (when (or (!= version (str world.revision)) (> world.stale-deletes 0))
    (setv world.stale-deletes (max 0 (- world.stale-deletes 1)) world.revision (+ world.revision 1))
    (return (! (api-answer 409 {"kind" "Status" "reason" "Conflict"}))))
  (if (> world.dying-settle 0)
      (setv (get world.dying name) world.dying-settle)
      (<- (forgotten world name)))
  (! (api-answer 200 {"kind" "Status" "status" "Success"})))


(defk backup-payload [backup state size]
  {:pre [(: backup HeldBackup) (: state str) (: size int)] :post [(: % dict)]}
  "Backup 1 つの GET の本文を綴るため(size = backup が覚える volume の名目の大きさ)。"
  {"metadata" {"name" backup.name "labels" {"backup-volume" backup.volume "backup-target" TARGET}}
   "status" {"state" state "backupCreatedAt" (if (= state "Completed") (! (iso-of backup.created-at)) "")
             "url" (if (= state "Completed") (.format "s3://longhorn-workspace@us-east-1/?backup={}&volume={}" backup.name backup.volume) "")
             "volumeSize" (str size)}})


(defk selected-volume [path]
  {:pre [(: path str)] :post [(: % (| str None))]}
  "一覧の path の labelSelector の backup-volume の値を得るため(backup-target が級の先でなければ None — 何も選ばない)。"
  (val query (parse-qs (. (urlsplit path) query)))
  (val pairs (dict (gfor part (.split (get (.get query "labelSelector" [""]) 0) ",") :if (in "=" part) (.split part "=" 1))))
  (if (= (.get pairs "backup-target") TARGET) (.get pairs "backup-volume") None))


(defk snapshot-answer [world snap]
  {:pre [(: world LonghornWorld) (: snap str)] :post [(: % HttpResponse)]}
  "Snapshot の GET に答えるため(読むたびに残りを 1 つ減らし、0 で readyToUse か error)。"
  (when (not-in snap world.snapshots)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val work (get world.snapshots snap))
  (setv (get world.snapshots snap) (replace work :left (max 0 (- work.left 1))))
  (! (api-answer 200 {"metadata" {"name" snap}
                      "status" (if world.snapshot-error
                                   {"readyToUse" False "error" "failed to create snapshot: engine is not running"}
                                   {"readyToUse" (= work.left 0) "error" ""})})))


(defk backup-answer [world backup]
  {:pre [(: world LonghornWorld) (: backup str)] :post [(: % HttpResponse)]}
  "Backup の GET に答えるため(読むたびに残りを 1 つ減らし、0 で Completed — 世界の backup に足す — か Error)。"
  (val done (lfor one world.held.backups :if (= one.name backup) one))
  (when done
    (return (! (api-answer 200 (! (backup-payload (get done 0) "Completed" world.backup-size))))))
  (when (not-in backup world.backups)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val work (get world.backups backup))
  (<- now datetime (GetTime))
  (val held (HeldBackup :volume work.volume :name backup :created-at (.timestamp now)))
  (cond
    world.backup-error (! (api-answer 200 (| (! (backup-payload held "Error" world.backup-size))
                                             {"status" {"state" "Error" "error" "failed to upload: connection reset by peer"}})))
    (> work.left 1) (do (setv (get world.backups backup) (replace work :left (- work.left 1)))
                        (! (api-answer 200 (! (backup-payload held "InProgress" world.backup-size)))))
    True (do (del (get world.backups backup))
             (setv world.held.backups (+ world.held.backups #(held)))
             (! (api-answer 200 (! (backup-payload held "Completed" world.backup-size)))))))


(defk volumes-listed [world]
  {:pre [(: world LonghornWorld)] :post [(: % HttpResponse)]}
  "Volume の一覧に答えるため(読みの残りは減らさない)。"
  (var items [])
  (for [volume world.held.volumes]
    (:= items (+ items [(! (volume-payload world volume))])))
  (! (api-answer 200 {"items" items})))


(defk volume-created [world body]
  {:pre [(: world LonghornWorld) (: body str)] :post [(: % HttpResponse)]}
  "Volume の POST に答えるため(在れば 409。spec.fromBackup が在れば、戻しの読みの数だけ戻す途中を見せる)。"
  (val created (json.loads body))
  (val name (get created "metadata" "name"))
  (val source (.get (get created "spec") "fromBackup" ""))
  (setv world.created (+ world.created #(body)))
  (<- volume (held-volume world.held name))
  (<- now datetime (GetTime))
  (when (is-not volume None)
    (return (! (api-answer 409 {"kind" "Status" "reason" "AlreadyExists"}))))
  (setv (get world.sizes name) (int (get created "spec" "size")))
  ;; Longhorn v1.13 は外れている volume の replica を作るだけで置かない — 置き先は初めて付ける時か、戻す時(付けて戻す)に決める。
  (.add world.unplaced name)
  (when source
    (setv (get world.sources name) source)
    (<- (placement-decided world name))
    (when (or (> world.restore-settle 0) (in name world.failing))
      (setv (get world.restoring name) (max 1 world.restore-settle))))
  (<- (changed world (HeldVolume :name name :formatted (bool source) :used-at (.timestamp now))))
  (! (api-answer 201 {"metadata" {"name" name}})))


(defk replicas-listed [world path]
  {:pre [(: world LonghornWorld) (: path str)] :post [(: % HttpResponse)]}
  "Replica の一覧(labelSelector longhornvolume で選ぶ)に答えるため(volume 1 つに replica 1 つ — 置いていなければ nodeID は空)。"
  (val query (parse-qs (. (urlsplit path) query)))
  (val chosen (get (.split (get (.get query "labelSelector" ["="]) 0) "=" 1) 1))
  (<- volume (held-volume world.held chosen))
  (! (api-answer 200 {"items" (if (is volume None)
                                  []
                                  [{"metadata" {"name" (+ chosen "-r-0")}
                                    "spec" {"volumeName" chosen "nodeID" (if (in chosen world.unplaced) "" NODE)}}])})))


(defk device-size [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % int)]}
  "volume name の engine が今見せる block device の大きさを得るため(名目の大きさを書いた後、読みの残りが尽きるまでと engine-stuck の
   世界では、前の大きさ = file system の総量のまま)。"
  (if (or world.engine-stuck (> (.get world.engine-left name 0) 0))
      (.get world.fs-sizes name SIZE)
      (.get world.sizes name SIZE)))


(defk engines-listed [world path]
  {:pre [(: world LonghornWorld) (: path str)] :post [(: % HttpResponse)]}
  "Engine の一覧(labelSelector longhornvolume で選ぶ)に答えるため(volume 1 つに engine 1 つ — 名目の大きさを書いた後は、読みの残りが
   尽きるまで前の大きさを見せる。engine-stuck の世界は広がらない)。"
  (val query (parse-qs (. (urlsplit path) query)))
  (val chosen (get (.split (get (.get query "labelSelector" ["="]) 0) "=" 1) 1))
  (<- volume (held-volume world.held chosen))
  (val size (! (device-size world chosen)))
  (val left (.get world.engine-left chosen 0))
  (when (> left 0)
    (setv (get world.engine-left chosen) (- left 1)))
  (! (api-answer 200 {"items" (if (is volume None)
                                  []
                                  [{"metadata" {"name" (+ chosen "-e-0")} "status" {"currentSize" (str size)}}])})))


(defk snapshot-made [world body]
  {:pre [(: world LonghornWorld) (: body str)] :post [(: % HttpResponse)]}
  "Snapshot の POST に答えるため(snapshot-settle の読みの後に使えるようになる)。"
  (val made (json.loads body))
  (assert (get made "spec" "createSnapshot") made)
  (setv (get world.snapshots (get made "metadata" "name")) (Pending :volume (get made "spec" "volume") :left world.snapshot-settle))
  (! (api-answer 201 made)))


(defk backup-made [world body]
  {:pre [(: world LonghornWorld) (: body str)] :post [(: % HttpResponse)]}
  "Backup の POST に答えるため(label の backup の先と、作った snapshot の名を確かめる)。"
  (val made (json.loads body))
  (val labels (get made "metadata" "labels"))
  (assert (= (get labels "backup-target") TARGET) made)
  (assert (in (get made "spec" "snapshotName") world.snapshots) made)
  (setv (get world.backups (get made "metadata" "name")) (Pending :volume (get labels "backup-volume") :left world.backup-settle))
  (! (api-answer 201 made)))


(defk backups-listed [world path]
  {:pre [(: world LonghornWorld) (: path str)] :post [(: % HttpResponse)]}
  "Backup の一覧(labelSelector で選ぶ)に答えるため(終わった物と途中の物)。"
  (val chosen (! (selected-volume path)))
  (var items [])
  (for [one world.held.backups :if (= one.volume chosen)]
    (:= items (+ items [(! (backup-payload one one.state world.backup-size))])))
  (for [#(name work) (.items world.backups) :if (= work.volume chosen)]
    (:= items (+ items [(! (backup-payload (HeldBackup :volume work.volume :name name :created-at 0.0) "InProgress" world.backup-size))])))
  (! (api-answer 200 {"items" items})))


(defk backup-dropped [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % HttpResponse)]}
  "Backup の DELETE に答えるため。"
  (setv world.held.backups (tuple (gfor one world.held.backups :if (!= one.name name) one)))
  (! (api-answer 200 {})))


(defk bundles-listed [world path]
  {:pre [(: world LonghornWorld) (: path str)] :post [(: % HttpResponse)]}
  "BackupVolume の一覧(labelSelector で選ぶ)に答えるため(backup の在る volume に束 1 つ)。"
  (val chosen (! (selected-volume path)))
  (val held (any (gfor one world.held.backups (= one.volume chosen))))
  (! (api-answer 200 {"items" (if held [{"metadata" {"name" (+ chosen BUNDLE-TAIL)} "spec" {"volumeName" chosen}}] [])})))


(defk bundle-dropped [world bundle]
  {:pre [(: world LonghornWorld) (: bundle str)] :post [(: % HttpResponse)]}
  "BackupVolume の DELETE に答えるため(束の backup を全部消す)。"
  (assert (.endswith bundle BUNDLE-TAIL) bundle)
  (val volume (cut bundle 0 (- (len bundle) (len BUNDLE-TAIL))))
  (setv world.held.backups (tuple (gfor one world.held.backups :if (!= one.volume volume) one)))
  (! (api-answer 200 {})))


(defk nodes-payload [world]
  {:pre [(: world LonghornWorld)] :post [(: % dict)]}
  "Longhorn の Node の一覧の本文を綴るため(zeus に札 workspace の disk 1 つ — 過剰割り当て 100% の名目で capacity 本を受ける大きさ・実の
   空き available — と extra-disks)。"
  (val disk {"tags" ["ssd" "workspace"] "allowScheduling" True "storageReserved" 0})
  (val maximum (* world.held.capacity SIZE))
  (val room {"storageMaximum" maximum "storageScheduled" (sum (gfor volume world.held.volumes (.get world.sizes volume.name SIZE)))
             "storageAvailable" (if (is world.available None) maximum world.available)
             "conditions" [{"type" "Ready" "status" "True"} {"type" "Schedulable" "status" "True"}]})
  (val extra (dfor #(index tags) (enumerate world.extra-disks) (+ "extra-" (str index)) tags))
  {"items" [{"metadata" {"name" NODE}
             "spec" {"disks" (| {"workspace-disk" disk}
                                (dfor #(key tags) (.items extra) key {"tags" (list tags) "allowScheduling" True "storageReserved" 0}))}
             "status" {"diskStatus" (| {"workspace-disk" room}
                                       (dfor key extra key {"storageMaximum" (* 100 SIZE) "storageScheduled" 0 "storageAvailable" (* 100 SIZE)
                                                            "conditions" [{"type" "Schedulable" "status" "True"}]}))}}]})


(defk longhorn-answer [world request]
  {:pre [(: world LonghornWorld) (: request SeenRequest)] :post [(: % HttpResponse)]}
  "API server の要求 1 つに、身元 → 宛先の順で答えるため。"
  (val path request.path)
  (cond
    (!= request.authorization (+ "Bearer " TOKEN)) (! (api-answer 401 {"kind" "Status" "reason" "Unauthorized"}))
    (and (= request.method "GET") (= path SETTINGS-PATH)) (! (api-answer 200 {"data" {"workspace-volume.json" SETTINGS-TEXT}}))
    (and (= request.method "GET") (= path CLASS-PATH)) (! (api-answer 200 {"parameters" world.parameters}))
    (and (= request.method "POST") (= path VOLUMES-PATH))
    (do (<- answer HttpResponse (volume-created world request.body)) answer)
    (and (= request.method "GET") (= path VOLUMES-PATH)) (! (volumes-listed world))
    (and (= request.method "GET") (.startswith path (+ VOLUMES-PATH "/")))
    (do (<- answer HttpResponse (volume-answer world (cut path (+ (len VOLUMES-PATH) 1) None))) answer)
    (and (= request.method "PATCH") (.startswith path (+ VOLUMES-PATH "/")))
    (do (assert (= request.content-type "application/merge-patch+json") request)
        (<- answer HttpResponse (volume-patched world (cut path (+ (len VOLUMES-PATH) 1) None) request.body))
        answer)
    (and (= request.method "DELETE") (.startswith path (+ VOLUMES-PATH "/")))
    (do (<- answer HttpResponse (volume-deleted world (cut path (+ (len VOLUMES-PATH) 1) None) request.body)) answer)
    (and (= request.method "POST") (= path SNAPSHOTS-PATH)) (! (snapshot-made world request.body))
    (and (= request.method "GET") (.startswith path (+ SNAPSHOTS-PATH "/")))
    (do (<- answer HttpResponse (snapshot-answer world (cut path (+ (len SNAPSHOTS-PATH) 1) None))) answer)
    (and (= request.method "POST") (= path BACKUPS-PATH)) (! (backup-made world request.body))
    (and (= request.method "GET") (.startswith path (+ BACKUPS-PATH "?"))) (! (backups-listed world path))
    (and (= request.method "GET") (.startswith path (+ BACKUPS-PATH "/")))
    (do (<- answer HttpResponse (backup-answer world (cut path (+ (len BACKUPS-PATH) 1) None))) answer)
    (and (= request.method "DELETE") (.startswith path (+ BACKUPS-PATH "/"))) (! (backup-dropped world (cut path (+ (len BACKUPS-PATH) 1) None)))
    (and (= request.method "GET") (.startswith path (+ BUNDLES-PATH "?"))) (! (bundles-listed world path))
    (and (= request.method "DELETE") (.startswith path (+ BUNDLES-PATH "/"))) (! (bundle-dropped world (cut path (+ (len BUNDLES-PATH) 1) None)))
    (and (= request.method "GET") (= path NODES-PATH)) (! (api-answer 200 (! (nodes-payload world))))
    (and (= request.method "GET") (= path OVER-PROVISIONING-PATH)) (! (api-answer 200 {"metadata" {"name" "x"} "value" "100"}))
    (and (= request.method "GET") (= path MINIMAL-AVAILABLE-PATH)) (! (api-answer 200 {"metadata" {"name" "x"} "value" (str world.minimal)}))
    (and (= request.method "GET") (.startswith path (+ REPLICAS-PATH "?"))) (! (replicas-listed world path))
    (and (= request.method "GET") (.startswith path (+ ENGINES-PATH "?"))) (! (engines-listed world path))
    (and (= request.method "GET") (.startswith path (+ ATTACHMENTS-PATH "/")))
    (do (<- answer HttpResponse (attachment-answer world (cut path (+ (len ATTACHMENTS-PATH) 1) None))) answer)
    (and (= request.method "PATCH") (.startswith path (+ ATTACHMENTS-PATH "/")))
    (do (assert (= request.content-type "application/merge-patch+json") request)
        (<- answer HttpResponse (attachment-patched world (cut path (+ (len ATTACHMENTS-PATH) 1) None) request.body))
        answer)
    True (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))


(defhandler longhorn-peer [#^ LonghornWorld world]
  ;; 引数に残す理由: 相手役の世界(何を持ち、どう答えるか)は筋書きごとの外の世界の形で、走らせた後に読む入れ物でもある。
  ;; API server 宛ての HTTP の要求だけに答える(届かない世界は接続の失敗)。
  (HttpRequest [method url headers body failures-as-values]
    :when (and (isinstance url str) (.startswith url KUBE-API))
    (if world.held.down
        (if failures-as-values
            (resume (HttpFailed :url url :detail "ConnectError: [Errno 111] Connection refused" :kind HttpFailureKind.CONNECT-FAILED))
            (raise (ConnectionError "[Errno 111] Connection refused")))
        (do (val seen (SeenRequest :method method :path (cut url (len KUBE-API) None)
                                   :content-type (.get (or headers {}) "content-type")
                                   :authorization (.get (or headers {}) "authorization")
                                   :body (cond (isinstance body bytes) (.decode body "utf-8") (is body None) "" True (str body))))
            (setv world.seen (+ world.seen #(seen)))
            (<- answer HttpResponse (longhorn-answer world seen))
            (resume answer)))))


(defk mountinfo-of [world]
  {:pre [(: world LonghornWorld)] :post [(: % str)]}
  "答え手の process の mountinfo を、世界の付いている volume から綴るため(係が host の /dev/longhorn/<名> の device で付けた形 — 空白は
   8 進の escape)。"
  (val base #("1 0 0:902 / / rw,relatime - overlay overlay rw" "2 1 8:17 /agent-worker /work rw,noatime master:790 - ext4 /dev/sdb1 rw"))
  (val lines (lfor #(index volume) (enumerate world.held.volumes) :if (is-not volume.mounted-at None)
                   (.format "{} 2 8:{} / {} rw,relatime master:9 - ext4 /host/dev/longhorn/{} rw" (+ 100 index) (* 16 index)
                            (.replace volume.mounted-at " " "\\040") volume.name)))
  (+ (.join "\n" (+ (list base) lines)) "\n"))


(defk keeper-run [world line]
  {:pre [(: world LonghornWorld) (: line str)] :post [(: % str)]}
  "係の頼み 1 行 line を世界に当て、答えの file の中身(1 行目が 0 か 1)を得るため(係の script と同じ順で断る)。"
  (val parts (.split line " "))
  (val verb (get parts 0))
  (val name (get parts 1))
  (val target (get parts 2))
  (<- volume (held-volume world.held name))
  (val there (lfor one world.held.volumes :if (= one.mounted-at target) one))
  (<- stat (| PathStat FileFailed) (StatPath target))
  (cond
    (= verb "mount")
    (cond
      (or (isinstance stat FileFailed) (!= stat.kind PathKind.DIRECTORY))
      (+ "1\nworkspace-mount: 付ける先 " target " が在る dir でない(dir は頼み手が作る)")
      (or (is volume None) (!= volume.node world.held.node))
      (+ "1\nworkspace-mount: /host/dev/longhorn/" name " が無い — volume " name " がこの node に attach されていない")
      (and there (= (. (get there 0) name) name)) (+ "0\nworkspace-mount: " target " には既に " name " が付いている")
      there (+ "1\nworkspace-mount: " target " には別の物が付いている")
      (is-not volume.mounted-at None) (+ "1\nworkspace-mount: " name " は既に " volume.mounted-at " に付いている")
      True (do (<- (put-volume world.held (replace volume :mounted-at target :formatted True)))
               (+ "0\nworkspace-mount: " name " を " target " に付けた")))
    (= verb "umount")
    (cond
      (not there) (+ "0\nworkspace-mount: " target " には何も付いていない")
      (!= (. (get there 0) name) name) (+ "1\nworkspace-mount: " target " に付いているのは " name " でない")
      (in target world.held.busy) (+ "1\nworkspace-mount: " target " を外せなかった(使っている process が在るかもしれない)")
      True (do (<- (put-volume world.held (replace volume :mounted-at None)))
               (+ "0\nworkspace-mount: " name " を " target " から外した")))
    (= verb "grow")
    (cond
      (not there) (+ "1\nworkspace-mount: " target " に何も付いていない")
      (!= (. (get there 0) name) name) (+ "1\nworkspace-mount: " target " に付いているのは " name " でない")
      True (do (val device (! (device-size world name)))
               (setv (get world.fs-sizes name) device)
               (+ "0\nworkspace-mount: " name " の file system を device の大きさ " (str device) " byte まで広げた")))
    True (+ "1\nworkspace-mount: 知らない命令 '" verb "'")))


(defhandler keeper-peer [#^ LonghornWorld world]
  ;; 引数に残す理由: 係の相手役は Longhorn の相手役と同じ世界(attach された volume だけを付けられる)を読み書きする。
  ;; 係の相手役: 頼みの dir の `<鍵>.req` へ名を替える書きを受けて頼みを読み、答えの file を置き、頼みを消す(係と同じ — silent の世界は
  ;; 頼みを置いたまま答えない)。答え手の process の mountinfo の読みと、付いた volume の file system の空きの読みにも答える。
  (ReadText [path]
    :when (= path SITE.mountinfo)
    (<- text str (mountinfo-of world))
    (resume text))
  (ReadDiskUsage [path]
    :when (any (gfor one world.held.volumes (= one.mounted-at path)))
    (val volume (get (lfor one world.held.volumes :if (= one.mounted-at path) one) 0))
    (val total (.get world.fs-sizes volume.name SIZE))
    (resume (DiskUsage :total total :free (max 0 (- total (.get world.used volume.name 0))))))
  (RenamePath [source target]
    :when (and (isinstance target str) (.startswith target (+ SITE.mount-dir "/")) (.endswith target ".req"))
    (<- moved (| FileFailed None) (RenamePath source target))
    (if (or world.silent (isinstance moved FileFailed))
        (resume moved)
        (do (<- line str (ReadText target))
            (setv world.asked (+ world.asked #((.strip line))))
            (<- answer str (keeper-run world (.strip line)))
            (<- (WriteText (+ (cut target 0 -4) ".out") answer))
            (<- (RemoveTree target))
            (resume moved)))))


(defhandler node-environ [#^ str node]
  ;; 引数に残す理由: 答え手の機体の node は筋書きごとの外の世界の形。
  ;; 機体の事実の読み(ReadEnvironment)に NODE_NAME だけを答える(本番は worker が子の process へ渡す環境変数)。
  (ReadEnvironment [names]
    (resume (tuple (gfor name names :if (= name "NODE_NAME") (EnvEntry :name name :value node))))))


(defk sweep-text [sweep]
  {:pre [(: sweep CoolingSweep)] :post [(: % str)]}
  "まとめて冷やす答え sweep を、結末の組に並べる字にするため(failed は断りの文の頭の volume の名だけ・広げた物が在れば grown を足す)。"
  (+ (.format "sweep slots={} cooled={} failed={}" sweep.slots (.join "," sweep.cooled)
              (.join "," (gfor line sweep.failed (get (.split line ":" 1) 0))))
     (if sweep.grown (+ " grown=" (.join "," sweep.grown)) "")))


(defk step-outcome [step]
  {:pre [(: step Step)] :post [(: % str)]}
  "筋書きの一手 step を effect で出し、結末の字(done・already・restored・sweep …・waited)を得るため(断りは呼び手が拾う)。"
  (match step.verb
    "wait" (do (<- (Delay step.seconds)) (return "waited"))
    "sweep" (do (<- sweep CoolingSweep (CoolIdleWorkspaceVolumes)) (return (! (sweep-text sweep))))
    "room" (do (<- room WorkspaceRoom (ReadWorkspaceRoom)) (return (.format "room slots={} spare={}" room.slots room.spare)))
    "place" (do (<- place WorkspacePlace (ReadWorkspacePlace step.owner step.path)) (return (+ "place " place.value))))
  (val effect (match step.verb
                "create" (CreateWorkspaceVolume step.owner)
                "attach" (AttachWorkspaceVolume step.owner)
                "mount" (MountWorkspaceVolume step.owner step.path)
                "umount" (UmountWorkspaceVolume step.owner step.path)
                "detach" (DetachWorkspaceVolume step.owner)
                "cool" (CoolWorkspaceVolume step.owner)
                "purge" (PurgeWorkspaceVolume step.owner)))
  (<- outcome WorkspaceOutcome effect)
  outcome.value)


(defk run-steps [steps]
  {:pre [(: steps tuple)] :post [(: % tuple)]}
  "筋書き steps を順に effect で出し、結末の組(done・already・restored・refused: <断りの文>・置き場の不足は short-fresh: か
   short-held: <断りの文> ほか)を得るため(断られても次の手へ進む)。"
  (var outcomes #())
  (for [step steps]
    (try
      (<- outcome str (step-outcome step))
      (:= outcomes (+ outcomes #(outcome)))
      (except [error WorkspaceRoomShort]
        (:= outcomes (+ outcomes #((+ (if error.fresh "short-fresh: " "short-held: ") (str error))))))
      (except [error WorkspaceVolumeRefused]
        (:= outcomes (+ outcomes #((+ "refused: " (str error))))))))
  outcomes)


(defk finished-run [steps]
  {:pre [(: steps tuple)] :post [(: % ApiRan)]}
  "筋書き steps を当て、結末の組と終わりの memory の file を得るため。"
  (<- outcomes tuple (run-steps steps))
  (<- left MemoryFiles (ReadMemoryFiles))
  (ApiRan :outcomes outcomes :left left))


(defk clock-from [start]
  {:pre [(: start float)] :post [(: % SimClock)]}
  "模擬の時計を、epoch 秒 start から始めるため。"
  (SimClock :current-time (datetime.fromtimestamp start timezone.utc)))


(defk on-api [world steps [files WORKER-FILES] [start 0.0]]
  {:pre [(: world LonghornWorld) (: steps tuple) (: files MemoryFiles) (: start float)] :post [(: % ApiRan)]}
  "本番の答え手で、相手役の世界 world に筋書き steps を(模擬の時計の刻 start から)当てた結末の組と、終わりの memory の file を得るため。"
  (<- ran ApiRan (with_handlers [(state) (memory-file-handler files) (sim-time-handler :clock (! (clock-from start))) (node-environ NODE)
                                 (longhorn-peer world) (keeper-peer world) (workspace-volumes-api SITE)]
                   (finished-run steps)))
  ran)


(defk on-sim [world steps [start 0.0] [files WORKER-FILES]]
  {:pre [(: world WorkspaceVolumeWorld) (: steps tuple) (: start float) (: files MemoryFiles)] :post [(: % tuple)]}
  "模擬の答え手で、世界 world に筋書き steps を(模擬の時計の刻 start から・付ける先の dir の中身は memory の file files)当てた結末の組を
   得るため。"
  (<- outcomes tuple (with_handlers [(state) (memory-file-handler files) (sim-time-handler :clock (! (clock-from start)))
                                     (held-workspace-volumes world)]
                       (run-steps steps)))
  outcomes)


(defk kinds [outcomes]
  {:pre [(: outcomes tuple)] :post [(: % tuple)]}
  "結末の組 outcomes から、断りの文を落とした種類の組(done・already・refused・short-fresh・short-held)を得るため。"
  (tuple (gfor outcome outcomes (if (in ": " outcome) (get (.split outcome ": " 1) 0) outcome))))


(defrecord BothRan
  "同じ筋書きを 2 つの答え手に当てた走り: real と held = 本番と模擬の結末の組・api = 本番の相手役・sim = 模擬の世界・left = 本番の終わりの
   memory の file。"
  (#^ tuple real)
  (#^ tuple held)
  (#^ LonghornWorld api)
  (#^ WorkspaceVolumeWorld sim)
  (#^ MemoryFiles left))


(defk both [make steps [start 0.0] [files WORKER-FILES]]
  {:pre [(: make Callable) (: steps tuple) (: start float) (: files MemoryFiles)] :post [(: % BothRan)]}
  "世界を作る関数 make で本番用と模擬用の世界を別に作り、筋書き steps を(模擬の時計の刻 start から・memory の file files の上で)両方の
   答え手に当てるため。"
  (val api (LonghornWorld (make)))
  (val sim (make))
  (<- on-real ApiRan (on-api api steps files start))
  (<- on-held tuple (on-sim sim steps start files))
  (BothRan :real on-real.outcomes :held on-held :api api :sim sim :left on-real.left))


;; volume の無い zeus の世界を作る関数(筋書きごとに新しい世界を作る — 本番用と模擬用で別の入れ物)。
(val fresh (fn [] (WorkspaceVolumeWorld NODE)))


(val CYCLE #((Step :verb "create" :owner OWNER) (Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER) (Step :verb "attach" :owner OWNER)
             (Step :verb "mount" :owner OWNER :path PLACE) (Step :verb "mount" :owner OWNER :path PLACE) (Step :verb "umount" :owner OWNER :path PLACE) (Step :verb "umount" :owner OWNER :path PLACE)
             (Step :verb "detach" :owner OWNER) (Step :verb "detach" :owner OWNER)))


(deftest test-a-volume-goes-through-its-whole-life-on-both-handlers
  (<- ran BothRan (both fresh CYCLE))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim) (val left ran.left)
  (val wanted #("done" "already" "done" "already" "done" "already" "done" "already" "done" "already"))
  (assert (= real wanted) real)
  (assert (= held wanted) held)
  (val after (HeldVolume :name NAME :node None :mounted-at None :formatted True))
  (assert (= api.held.volumes #(after)) api.held.volumes)
  (assert (= sim.volumes #(after)) sim.volumes)
  ;; 係が受けた頼みは mount と umount の 1 つずつ(2 度目は mountinfo で既にそうだと読み、頼まない)。頼みと答えの file は残らない。
  (assert (= api.asked #((+ "mount " NAME " " PLACE) (+ "umount " NAME " " PLACE))) api.asked)
  (val stray (lfor file left.files :if (.startswith file.path (+ SITE.mount-dir "/")) file.path))
  (assert (= stray []) stray)
  ;; 付ける先の dir は答え手が作り、外した後も残す。
  (assert (in PLACE left.dirs) left.dirs))


(deftest test-an-id-outside-the-name-rule-touches-nothing
  (for [bad #("c_1" "c-1/../x" (* "a" 38))]
    (<- ran BothRan (both fresh #((Step :verb "create" :owner bad) (Step :verb "attach" :owner bad) (Step :verb "mount" :owner bad :path PLACE) (Step :verb "detach" :owner bad))))
    (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim) (val left ran.left)
    (assert (= (! (kinds real)) #("refused" "refused" "refused" "refused")) real)
    (assert (= (! (kinds held)) #("refused" "refused" "refused" "refused")) held)
    (assert (= api.seen #()) api.seen)
    (assert (= api.asked #()) api.asked)
    (assert (= sim.volumes #()) sim.volumes)))



(deftest test-failure-cases-are-refused-the-same-on-both-handlers
  (val elsewhere (HeldVolume :name NAME :node "atlas" :formatted True))
  (val attached (HeldVolume :name NAME :node NODE :formatted True))
  (val other-name (! (volume-name OTHER)))
  (val cases #(;; 無い volume の attach。
               #(#() #((Step :verb "attach" :owner OWNER)) "volume が無い")
               ;; 別の node に付いた volume の attach と detach(ticket を書き換えない)。
               #(#(elsewhere) #((Step :verb "attach" :owner OWNER)) "node atlas への ticket が在る")
               #(#(elsewhere) #((Step :verb "detach" :owner OWNER)) "別の node atlas")
               ;; attach していない volume の mount。
               #(#((HeldVolume :name NAME)) #((Step :verb "mount" :owner OWNER :path PLACE)) "attach されていない")
               ;; 付ける先に別の volume。
               #(#(attached (HeldVolume :name other-name :node NODE :mounted-at PLACE :formatted True))
                 #((Step :verb "mount" :owner OWNER :path PLACE)) "別の物")
               ;; 別の path に付いた volume を、もう 1 つの path に mount。
               #(#((HeldVolume :name NAME :node NODE :mounted-at PLACE-2 :formatted True))
                 #((Step :verb "mount" :owner OWNER :path PLACE)) "既に /work/agent-workspaces/a-k973-2 に付いている")
               ;; 付ける先に付いているのは別の volume の umount。
               #(#((HeldVolume :name other-name :node NODE :mounted-at PLACE :formatted True))
                 #((Step :verb "umount" :owner OWNER :path PLACE)) "でない")
               ;; mount の残る volume の detach(ticket を外さない)。
               #(#((HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))
                 #((Step :verb "detach" :owner OWNER)) "まだ /work/agent-workspaces/a-k973 に mount されている")
               ;; 絶対の正規の path でない付ける先。
               #(#(attached) #((Step :verb "mount" :owner OWNER :path "/work/agent-workspaces/../x")) "絶対の正規の path でない")
               #(#(attached) #((Step :verb "mount" :owner OWNER :path "work/x")) "絶対の正規の path でない")))
  (for [#(volumes steps reason) cases]
    (val make (fn [] (WorkspaceVolumeWorld NODE :volumes volumes)))
    (<- ran BothRan (both make steps))
    (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim) (val left ran.left)
    (assert (and (= (len real) 1) (.startswith (get real 0) "refused: ") (in reason (get real 0))) #(steps real))
    (assert (and (= (len held) 1) (.startswith (get held 0) "refused: ") (in reason (get held 0))) #(steps held))
    ;; 断った後の様子は前と同じ(本番は ticket の書きも係への頼みもしない — mount の係が断る筋書きは頼む)。
    (assert (= api.held.volumes (. (make) volumes)) #(steps api.held.volumes))
    (assert (= sim.volumes (. (make) volumes)) #(steps sim.volumes))
    (assert (not (any (gfor seen api.seen (= seen.method "PATCH")))) #(steps api.seen))))


(deftest test-a-busy-place-is-not-unmounted
  (val mounted (HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))
  (val make (fn [] (WorkspaceVolumeWorld NODE :volumes #(mounted) :busy (frozenset #(PLACE)))))
  (<- ran BothRan (both make #((Step :verb "umount" :owner OWNER :path PLACE) (Step :verb "detach" :owner OWNER))))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim) (val left ran.left)
  (assert (= (! (kinds real)) #("refused" "refused")) real)
  (assert (= (! (kinds held)) #("refused" "refused")) held)
  (assert (in "使っている process" (get real 0)) real)
  (assert (= api.held.volumes #(mounted)) api.held.volumes)
  (assert (= sim.volumes #(mounted)) sim.volumes))


(deftest test-an-unreachable-api-server-is-refused
  (val make (fn [] (WorkspaceVolumeWorld NODE :down True)))
  (<- ran BothRan (both make #((Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER))))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim) (val left ran.left)
  (assert (= (! (kinds real)) #("refused" "refused")) real)
  (assert (= (! (kinds held)) #("refused" "refused")) held)
  (assert (in "API server に届かない" (get real 0)) real)
  (assert (= sim.volumes #()) sim.volumes))


(deftest test-attach-waits-for-longhorn-to-satisfy-the-ticket
  (val api (LonghornWorld (fresh) :settle 3))
  (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER) (Step :verb "mount" :owner OWNER :path PLACE))))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (= outcomes #("done" "done" "done")) outcomes)
  (val reads (lfor seen api.seen :if (and (= seen.method "GET") (.startswith seen.path ATTACHMENTS-PATH)) seen))
  (assert (>= (len reads) 4) (len reads)))


(deftest test-a-ticket-longhorn-never-satisfies-is-refused-with-its-reason
  (val api (LonghornWorld (fresh) :stuck True))
  (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER))))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (= (get outcomes 0) "done") outcomes)
  (assert (and (.startswith (get outcomes 1) "refused: ") (in "5.0 秒の内に node zeus に付かない" (get outcomes 1))
               (in "waiting for the engine of the volume" (get outcomes 1)))
          outcomes))


(deftest test-a-silent-keeper-is-refused-and-its-request-withdrawn
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #((HeldVolume :name NAME :node NODE))) :silent True))
  (<- ran ApiRan (on-api api #((Step :verb "mount" :owner OWNER :path PLACE))))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (and (.startswith (get outcomes 0) "refused: ") (in "秒の内に答えない" (get outcomes 0))) outcomes)
  (val stray (lfor file left.files :if (.startswith file.path (+ SITE.mount-dir "/")) file.path))
  (assert (= stray []) stray))


(deftest test-a-worker-without-a-keeper-is-refused-before-asking
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #((HeldVolume :name NAME :node NODE)))))
  (val files (MemoryFiles :files WORKER-FILES.files :dirs #("/work/agent-workspaces")))
  (<- ran ApiRan (on-api api #((Step :verb "mount" :owner OWNER :path PLACE)) files))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (and (.startswith (get outcomes 0) "refused: ") (in "mount の係が無い worker" (get outcomes 0))) outcomes)
  (assert (= api.asked #()) api.asked))


(deftest test-the-volume-is-spelled-from-the-class-and-the-settings
  (val api (LonghornWorld (fresh)))
  (<- (on-api api #((Step :verb "create" :owner OWNER))))
  (assert (= (len api.created) 1) api.created)
  (assert (= (json.loads (get api.created 0))
             {"apiVersion" "longhorn.io/v1beta2" "kind" "Volume"
              "metadata" {"name" NAME "namespace" "longhorn-system"
                          "labels" {"recurring-job-group.longhorn.io/workspace-backup" "enabled"
                                    "recurring-job-group.longhorn.io/default" "enabled"}}
              "spec" {"size" (str (get (json.loads SETTINGS-TEXT) "sizeBytes")) "frontend" "blockdev" "accessMode" "rwo"
                      "numberOfReplicas" 1 "staleReplicaTimeout" 2880 "dataLocality" "best-effort" "backupTargetName" "garage-workspace"
                      "diskSelector" ["workspace"]}})
          (get api.created 0)))


(deftest test-a-class-the-handler-cannot-copy-is-refused
  (for [#(parameters reason) #(#((| PARAMETERS {"migratable" "true"}) "写し方が無い: migratable")
                               #((| PARAMETERS {"fsType" "xfs"}) "ext4 でない")
                               #((| PARAMETERS {"numberOfReplicas" "one"}) "数でない")
                               #((| PARAMETERS {"recurringJobSelector" "[{\"name\": 1}]"}) "形が違う"))]
    (val api (LonghornWorld (fresh) :parameters parameters))
    (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER))))
    (val outcomes ran.outcomes) (val left ran.left)
    (assert (and (.startswith (get outcomes 0) "refused: ") (in reason (get outcomes 0))) #(parameters outcomes))
    (assert (= api.created #()) api.created)))


(deftest test-a-ticket-write-is-applied-only-on-the-version-it-read
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #((HeldVolume :name NAME))) :races 2))
  (<- ran ApiRan (on-api api #((Step :verb "attach" :owner OWNER))))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (= outcomes #("done")) outcomes)
  (val writes (lfor seen api.seen :if (and (= seen.method "PATCH") (.startswith seen.path ATTACHMENTS-PATH))
                    (get (json.loads seen.body) "metadata" "resourceVersion")))
  (assert (= (len writes) 3) writes)
  (assert (= (len (set writes)) 3) writes))


(deftest test-detach-waits-until-the-volume-leaves-the-node
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #((HeldVolume :name NAME :node NODE :formatted True))) :settle 2))
  (<- ran ApiRan (on-api api #((Step :verb "detach" :owner OWNER) (Step :verb "detach" :owner OWNER))))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (= outcomes #("done" "already")) outcomes)
  (val reads (lfor seen api.seen :if (and (= seen.method "GET") (.startswith seen.path VOLUMES-PATH)) seen))
  (assert (>= (len reads) 4) (len reads)))


(deftest test-an-unreadable-token-is-refused-without-the-token
  (val api (LonghornWorld (fresh)))
  (val files (MemoryFiles :files #() :dirs WORKER-FILES.dirs))
  (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER)) files))
  (val outcomes ran.outcomes) (val left ran.left)
  (assert (and (.startswith (get outcomes 0) "refused: ") (in "token の file が読めない" (get outcomes 0))) outcomes)
  (assert (not-in TOKEN (get outcomes 0)) outcomes))


(deftest test-requests-carry-the-worker-identity
  (val api (LonghornWorld (fresh)))
  (<- (on-api api CYCLE))
  (assert api.seen)
  (assert (all (gfor seen api.seen (= seen.authorization (+ "Bearer " TOKEN)))) api.seen)
  (val methods (sorted (set (gfor seen api.seen seen.method))))
  (assert (= methods ["GET" "PATCH" "POST"]) methods))


;; --- 冷やす・戻す・消す ---------------------------------------------------------------------------------------------

;; 刻 0 に冷やした backup 1 つ(冷えた持ち主の世界の元)。
(val COLD-BACKUP (HeldBackup :volume NAME :name (+ NAME "-cool-0") :created-at 0.0))
;; 外れていて file system を持つ volume(温かい持ち主)。
(val WARM (HeldVolume :name NAME :formatted True))
;; 次の頼み(メッセージ)が来た時に上に載る系が出す手: 作る(戻す)→ attach → mount。
(val START #((Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER) (Step :verb "mount" :owner OWNER :path PLACE)))


(deftest test-a-cooled-volume-comes-back-from-its-backup-on-both-handlers
  (val steps (+ CYCLE #((Step :verb "cool" :owner OWNER) (Step :verb "cool" :owner OWNER)) START #((Step :verb "create" :owner OWNER))))
  (<- ran BothRan (both fresh steps))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim)
  (val wanted (+ #("done" "already" "done" "already" "done" "already" "done" "already" "done" "already")
                 #("done" "already" "restored" "done" "done" "already")))
  (assert (= real wanted) real)
  (assert (= held wanted) held)
  ;; 戻した volume は file system を持ち(mkfs しない)、backup は「消す」まで残る。
  (val after (HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))
  (assert (= api.held.volumes #(after)) api.held.volumes)
  (assert (= sim.volumes #(after)) sim.volumes)
  (assert (= api.held.backups #(COLD-BACKUP)) api.held.backups)
  (assert (= sim.backups #(COLD-BACKUP)) sim.backups)
  ;; 本番: 2 度目の作成は backup の url から戻す本文。
  (assert (= (len api.created) 2) api.created)
  (assert (= (get (json.loads (get api.created 1)) "spec" "fromBackup")
             (.format "s3://longhorn-workspace@us-east-1/?backup={}&volume={}" COLD-BACKUP.name NAME))
          (get api.created 1)))


(deftest test-a-volume-in-use-is-not-cooled-on-both-handlers
  (for [volume #((HeldVolume :name NAME :node NODE :formatted True) (HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))]
    (val make (fn [] (WorkspaceVolumeWorld NODE :volumes #(volume))))
    (<- ran BothRan (both make #((Step :verb "cool" :owner OWNER))))
    (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim)
    (assert (and (.startswith (get real 0) "refused: ") (in "使っている(ticket longhorn-api-" (get real 0))) real)
    (assert (and (.startswith (get held 0) "refused: ") (in "使っている(ticket longhorn-api-" (get held 0))) held)
    (assert (= api.held.volumes #(volume)) api.held.volumes)
    (assert (= sim.volumes #(volume)) sim.volumes)
    (assert (= #(api.held.backups sim.backups) #(#() #())) #(api.held.backups sim.backups))
    (assert (not (any (gfor seen api.seen (= seen.method "POST")))) api.seen)))


(deftest test-a-cold-name-without-a-finished-backup-is-not-made-empty-on-both-handlers
  (val failed (HeldBackup :volume NAME :name (+ NAME "-cool-0") :created-at 0.0 :state "Error"))
  (val make (fn [] (WorkspaceVolumeWorld NODE :backups #(failed))))
  (<- ran BothRan (both make #((Step :verb "create" :owner OWNER))))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim)
  (assert (and (.startswith (get real 0) "refused: ") (in "完了した物が無い(空で作らない)" (get real 0))) real)
  (assert (and (.startswith (get held 0) "refused: ") (in "完了した物が無い(空で作らない)" (get held 0))) held)
  (assert (= #(api.held.volumes sim.volumes) #(#() #())) #(api.held.volumes sim.volumes))
  (assert (= api.created #()) api.created))


(deftest test-backups-are-purged-only-after-keep-days-on-both-handlers
  (val make (fn [] (WorkspaceVolumeWorld NODE :backups #(COLD-BACKUP))))
  (val steps #((Step :verb "purge" :owner OWNER) (Step :verb "wait" :seconds (* 5 DAY)) (Step :verb "purge" :owner OWNER)
               (Step :verb "purge" :owner OWNER)))
  (<- ran BothRan (both make steps (* 3 DAY)))
  (val real ran.real) (val held ran.held) (val api ran.api) (val sim ran.sim)
  (assert (= (! (kinds real)) #("refused" "waited" "done" "already")) real)
  (assert (= (! (kinds held)) #("refused" "waited" "done" "already")) held)
  (assert (in "最後の backup から 3.0 日(keepDays 7 日を過ぎるまで消さない)" (get real 0)) real)
  (assert (= #(api.held.backups sim.backups) #(#() #())) #(api.held.backups sim.backups))
  ;; 本番は束(BackupVolume)を消す。
  (val drops (lfor seen api.seen :if (= seen.method "DELETE") seen.path))
  (assert (= drops [(+ BUNDLES-PATH "/" NAME BUNDLE-TAIL)]) drops)
  ;; 温かい volume の在る名は、日数に関わらず消さない。
  (val warm (fn [] (WorkspaceVolumeWorld NODE :volumes #(WARM) :backups #(COLD-BACKUP))))
  (<- kept BothRan (both warm #((Step :verb "purge" :owner OWNER)) (* 30 DAY)))
  (assert (in "Longhorn の volume が在る" (get kept.real 0)) kept.real)
  (assert (in "Longhorn の volume が在る" (get kept.held 0)) kept.held)
  (assert (= kept.api.held.backups #(COLD-BACKUP)) kept.api.held.backups))


;; まとめて冷やす検の世界を作る関数(筋書きごとに新しい入れ物): 刻 10 時間に、使い終わりが 0・7・9・9.5 時間の外れた volume 4 つと、
;; 付いている volume 1 つ(名目で capacity 本を受ける disk)。
(val idle-world (fn [capacity]
                  (WorkspaceVolumeWorld NODE :capacity capacity
                                        :volumes #((HeldVolume :name "ws-c-a1" :formatted True :used-at 0.0)
                                                   (HeldVolume :name "ws-c-a2" :formatted True :used-at (* 7 3600.0))
                                                   (HeldVolume :name "ws-c-a3" :formatted True :used-at (* 9 3600.0))
                                                   (HeldVolume :name "ws-c-a4" :formatted True :used-at (* 9.5 3600.0))
                                                   (HeldVolume :name "ws-c-a5" :node NODE :formatted True :used-at 0.0)))))


(deftest test-the-idle-sweep-cools-the-stale-and-then-the-oldest-on-both-handlers
  ;; 値の spareVolumes は 4。空き 7(12 − 5)なら 2 時間を過ぎた a1・a2 だけ — 付いている a5 は古くても冷やさない。
  ;; 空き 1(6 − 5)では 1 + 2 < 4 なので、2 時間の内の a3 を古い順に 1 本足す。空き 0(5 − 5)では外れた物を全部(0 + 4 = 4)。
  (for [#(capacity wanted left) #(#(12 "sweep slots=7 cooled=ws-c-a1,ws-c-a2 failed=" #("ws-c-a3" "ws-c-a4" "ws-c-a5"))
                                  #(6 "sweep slots=1 cooled=ws-c-a1,ws-c-a2,ws-c-a3 failed=" #("ws-c-a4" "ws-c-a5"))
                                  #(5 "sweep slots=0 cooled=ws-c-a1,ws-c-a2,ws-c-a3,ws-c-a4 failed=" #("ws-c-a5")))]
    (<- ran BothRan (both (fn [] (idle-world capacity)) #((Step :verb "sweep")) (* 10 3600.0)))
    (assert (= ran.real #(wanted)) ran.real)
    (assert (= ran.held #(wanted)) ran.held)
    (assert (= (tuple (gfor one ran.api.held.volumes one.name)) left) ran.api.held.volumes)
    (assert (= (tuple (gfor one ran.sim.volumes one.name)) left) ran.sim.volumes)
    (assert (= (len ran.api.held.backups) (- 5 (len left))) ran.api.held.backups)))


(deftest test-the-idle-sweep-counts-only-disks-with-the-class-tag
  (val api (LonghornWorld (idle-world 12) :extra-disks #(#("hot" "ssd") #("ssd"))))
  (<- ran ApiRan (on-api api #((Step :verb "sweep")) WORKER-FILES (* 10 3600.0)))
  (assert (= ran.outcomes #("sweep slots=7 cooled=ws-c-a1,ws-c-a2 failed=")) ran.outcomes))


(deftest test-a-failed-snapshot-or-backup-keeps-the-volume
  (for [#(flag reason) #(#("snapshot" "snapshot ws-c-exampleownerid000000000001-cool-0 が失敗した: failed to create snapshot")
                         #("backup" "backup ws-c-exampleownerid000000000001-cool-0 が失敗した: failed to upload"))]
    (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM)) :snapshot-error (= flag "snapshot") :backup-error (= flag "backup")))
    (<- ran ApiRan (on-api api #((Step :verb "cool" :owner OWNER))))
    (assert (and (.startswith (get ran.outcomes 0) "refused: ") (in reason (get ran.outcomes 0))) ran.outcomes)
    (assert (= api.held.volumes #(WARM)) api.held.volumes)
    (assert (= api.held.backups #()) api.held.backups)
    (assert (not (any (gfor seen api.seen (= seen.method "DELETE")))) api.seen)))


(deftest test-a-delete-on-a-stale-version-is-read-again
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM)) :stale-deletes 2))
  (<- ran ApiRan (on-api api #((Step :verb "cool" :owner OWNER))))
  (assert (= ran.outcomes #("done")) ran.outcomes)
  (val versions (lfor seen api.seen :if (= seen.method "DELETE") (get (json.loads seen.body) "preconditions" "resourceVersion")))
  (assert (= (len versions) 3) versions)
  (assert (= (len (set versions)) 3) versions)
  (assert (= api.held.volumes #()) api.held.volumes)
  (assert (= api.held.backups #(COLD-BACKUP)) api.held.backups))


;; --- 付ける先の様子(volume の外の中身を隠さない)--------------------------------------------------------------------

;; 付ける先 PLACE に、volume の外で書かれた file 1 つが在る worker の file(本番の機体の dir に書いた作業場所の形)。
(val FILLED-FILES (MemoryFiles :files (+ WORKER-FILES.files #((MemoryFile :path (+ PLACE "/notes.md") :content b"written outside a volume\n")))
                               :dirs (+ WORKER-FILES.dirs #(PLACE))))
;; 付ける先 PLACE が空の dir の worker の file(外した後に残る付ける先の dir)。
(val EMPTY-DIR-FILES (MemoryFiles :files WORKER-FILES.files :dirs (+ WORKER-FILES.dirs #(PLACE))))


(deftest test-the-place-is-read-the-same-on-both-handlers
  (val mounted (HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))
  (val other (HeldVolume :name (! (volume-name OTHER)) :node NODE :mounted-at PLACE :formatted True))
  (val place (Step :verb "place" :owner OWNER :path PLACE))
  (val cases #(;; 何も無い・空の dir — 付けてよい。
               #(#() #() WORKER-FILES "place ready")
               #(#() #() EMPTY-DIR-FILES "place ready")
               ;; 中身の在る dir で、持ち主は volume も backup も持たない — 持ち主の作業場所は volume の外。
               #(#() #() FILLED-FILES "place outside")
               ;; 中身の在る dir で、持ち主が温かい volume か backup を持つ — 付けても付けなくても一方が見えなくなる。
               #(#(WARM) #() FILLED-FILES "place clash")
               #(#() #(COLD-BACKUP) FILLED-FILES "place clash")
               ;; 付いている物。
               #(#(mounted) #() WORKER-FILES "place mounted")
               #(#(other) #() WORKER-FILES "place clash")))
  (for [#(volumes backups files wanted) cases]
    (<- ran BothRan (both (fn [] (WorkspaceVolumeWorld NODE :volumes volumes :backups backups)) #(place) 0.0 files))
    (assert (= ran.real #(wanted)) #(wanted ran.real))
    (assert (= ran.held #(wanted)) #(wanted ran.held))
    ;; 読むだけ — 書きの要求も係への頼みも無い。
    (assert (all (gfor seen ran.api.seen (= seen.method "GET"))) ran.api.seen)
    (assert (= ran.api.asked #()) ran.api.asked)))


(deftest test-a-mount-never-hides-what-the-dir-holds-on-both-handlers
  ;; 付ける先の dir に volume の外で書かれた中身が在る。mount は付けずに断る(本番は係へ頼まない)— 付けると中身が隠れる。
  (val attached (HeldVolume :name NAME :node NODE :formatted True))
  (<- ran BothRan (both (fn [] (WorkspaceVolumeWorld NODE :volumes #(attached))) #((Step :verb "mount" :owner OWNER :path PLACE)) 0.0
                        FILLED-FILES))
  (for [outcomes #(ran.real ran.held)]
    (assert (and (= (! (kinds outcomes)) #("refused")) (in "に中身が在る(volume の外に書かれた物 — 付けると隠すので付けない)" (get outcomes 0)))
            outcomes))
  (assert (= ran.api.held.volumes #(attached)) ran.api.held.volumes)
  (assert (= ran.sim.volumes #(attached)) ran.sim.volumes)
  (assert (= ran.api.asked #()) ran.api.asked)
  ;; 空の dir(外した後に残る付ける先)には付ける。
  (<- empty BothRan (both (fn [] (WorkspaceVolumeWorld NODE :volumes #(attached))) #((Step :verb "mount" :owner OWNER :path PLACE)) 0.0
                          EMPTY-DIR-FILES))
  (assert (= #(empty.real empty.held) #(#("done") #("done"))) #(empty.real empty.held)))


;; --- 置き場の数え(作る前に数える・置けない volume を残さない)----------------------------------------------------------

(deftest test-a-full-disk-is-refused-before-the-volume-is-posted-on-both-handlers
  ;; 札の disk が受けられる残りが 0(capacity 2 に温かい volume 2 本)。新しい持ち主の作成も冷えた持ち主の戻しも、POST せずに置き場の
  ;; 不足で断る(新しい持ち主 = fresh・冷えた持ち主 = 中身を持つ)。残りの読みは 0 本と値の spareVolumes 4 を答える。
  (val cold-other (HeldBackup :volume (! (volume-name OTHER)) :name (+ (! (volume-name OTHER)) "-cool-0") :created-at 0.0))
  (val full (fn [] (WorkspaceVolumeWorld NODE :capacity 2 :backups #(cold-other)
                                         :volumes #((HeldVolume :name "ws-c-w1" :formatted True) (HeldVolume :name "ws-c-w2" :formatted True)))))
  (<- ran BothRan (both full #((Step :verb "room") (Step :verb "create" :owner OWNER) (Step :verb "create" :owner OTHER))))
  (for [outcomes #(ran.real ran.held)]
    (assert (= (! (kinds outcomes)) #("room slots=0 spare=4" "short-fresh" "short-held")) outcomes)
    (assert (in "もう受けられない" (get outcomes 1)) outcomes))
  (assert (= ran.api.created #()) ran.api.created)
  (assert (not (any (gfor seen ran.api.seen (= seen.method "POST")))) ran.api.seen)
  (assert (= (len ran.api.held.volumes) 2) ran.api.held.volumes)
  (assert (= (len ran.sim.volumes) 2) ran.sim.volumes)
  ;; 1 本空けば(capacity 3)作る。
  (<- room BothRan (both (fn [] (WorkspaceVolumeWorld NODE :capacity 3 :volumes #((HeldVolume :name "ws-c-w1" :formatted True))))
                         #((Step :verb "room") (Step :verb "create" :owner OWNER) (Step :verb "room") (Step :verb "create" :owner OTHER))))
  (assert (= room.real #("room slots=2 spare=4" "done" "room slots=1 spare=4" "done")) room.real)
  (assert (= room.held room.real) room.held))


(deftest test-the-real-space-line-refuses-a-disk-near-its-minimal-available
  ;; 名目の予約の線は十分(capacity 20)だが、実の空きが最小の空き 25% の線の上に名目 1 本ぶんしか無い世界。1 本目は作り、2 本目は
  ;; POST せずに断る(Longhorn は空きが線を越える disk にだけ置く — 置けない Volume を作らない)。
  (val maximum (* 20 SIZE))
  (val roomy (LonghornWorld (fresh) :available (+ (// maximum 4) SIZE)))
  (<- ran ApiRan (on-api roomy #((Step :verb "room") (Step :verb "create" :owner OWNER))))
  (assert (= ran.outcomes #("room slots=1 spare=4" "done")) ran.outcomes)
  (val tight (LonghornWorld (fresh) :available (+ (// maximum 4) (// SIZE 2))))
  (<- again ApiRan (on-api tight #((Step :verb "room") (Step :verb "create" :owner OTHER))))
  (assert (= (! (kinds again.outcomes)) #("room slots=0 spare=4" "short-fresh")) again.outcomes)
  (assert (= tight.created #()) tight.created))


(deftest test-a-fresh-volume-longhorn-cannot-place-is-deleted-at-attach-without-waiting
  ;; 作る前の数えは通ったが、付ける時に Longhorn が replica を置けないと答える(数えと付けの間に別の作りが残りを使った)。attach は
  ;; 上限の秒(5 秒)まで待たず、replica の無い volume を消して置き場の不足(fresh)で断る — faulted の volume を残さない。
  (val api (LonghornWorld (fresh) :unplaceable 1))
  (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER) (Step :verb "attach" :owner OWNER))))
  (assert (= (! (kinds ran.outcomes)) #("done" "short-fresh")) ran.outcomes)
  (assert (in "Longhorn が replica を置けない(ReplicaSchedulingFailure precheck new replica failed: insufficient storage) — 中身の無い volume を消した"
              (get ran.outcomes 1))
          ran.outcomes)
  (assert (= api.held.volumes #()) api.held.volumes)
  (assert (= api.failing (set)) api.failing)
  ;; 待ちは読み直しの数回(上限の秒 5 / 0.5 秒 = 10 回の手前)で終わる。
  (val reads (lfor seen api.seen :if (and (= seen.method "GET") (.startswith seen.path ATTACHMENTS-PATH)) seen))
  (assert (<= (len reads) 2) (len reads))
  ;; 次の頼みは、置き場が戻っていれば空で作り直して付く。
  (<- next ApiRan (on-api api START))
  (assert (= next.outcomes #("done" "done" "done")) next.outcomes))


(deftest test-a-warm-volume-whose-replica-is-placed-is-not-deleted
  ;; replica の置かれた(中身を持ちうる)volume は、Longhorn が置けないと答えても消さない — 付くのを上限の秒まで待って断る。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM))))
  (.add api.failing NAME)
  (<- ran ApiRan (on-api api #((Step :verb "attach" :owner OWNER))))
  (assert (and (= (! (kinds ran.outcomes)) #("refused")) (in "5.0 秒の内に node zeus に付かない" (get ran.outcomes 0))) ran.outcomes)
  (assert (= (tuple (gfor one api.held.volumes one.name)) #(NAME)) api.held.volumes)
  (assert (not (any (gfor seen api.seen (= seen.method "DELETE")))) api.seen))


(deftest test-a-restore-longhorn-cannot-place-is-deleted-and-the-backup-kept
  ;; 冷えた持ち主の戻しで Longhorn が replica を置けない。戻しの待ちは上限の秒まで待たず、replica の無い volume を消して置き場の不足
  ;; (中身を持つ — fresh でない)で断る。backup は残り、置き場が戻れば次の作成が戻す。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :backups #(COLD-BACKUP)) :unplaceable 1))
  (<- ran ApiRan (on-api api (+ #((Step :verb "create" :owner OWNER)) START)))
  (assert (= (! (kinds ran.outcomes)) #("short-held" "restored" "done" "done")) ran.outcomes)
  (assert (= api.held.backups #(COLD-BACKUP)) api.held.backups)
  (assert (= (len api.created) 2) api.created))


(deftest test-a-restored-volume-is-made-as-large-as-its-backup
  ;; backup を取った時の volume が値の大きさより大きい(使う間に広げた)。戻す volume は backup の大きさで作る(Longhorn は小さい volume
  ;; への戻しを断る)。値より小さい backup は値の大きさで作る。
  (for [#(kept wanted) #(#((* 3 SIZE) (* 3 SIZE)) #((// SIZE 2) SIZE))]
    (val api (LonghornWorld (WorkspaceVolumeWorld NODE :backups #(COLD-BACKUP)) :backup-size kept))
    (<- ran ApiRan (on-api api #((Step :verb "create" :owner OWNER))))
    (assert (= ran.outcomes #("restored")) ran.outcomes)
    (assert (= (get (json.loads (get api.created 0)) "spec" "size") (str wanted)) api.created)))


;; --- 広げる(付いた volume の空きが線を割れば、付けたまま広げる)------------------------------------------------------------

;; この機体に付いて mount された持ち主の volume(冷やす回が空きを読む物)。
(val MOUNTED (HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True))
;; 値の大きさ(10 GiB)の 1 割。
(val TENTH (// SIZE 10))


(defk size-writes [api]
  {:pre [(: api LonghornWorld)] :post [(: % list)]}
  "相手役が受けた、Volume の名目の大きさ(spec.size)の書きの値を、受けた順に得るため。"
  (lfor seen api.seen :if (and (= seen.method "PATCH") (.startswith seen.path VOLUMES-PATH) (in "spec" (json.loads seen.body)))
        (get (json.loads seen.body) "spec" "size")))


(deftest test-a-mounted-volume-short-of-space-is-grown-online-by-the-sweep
  ;; 付いた volume の file system の空きが値の growBelowPercent(50%)を割った(10 GiB のうち 6 GiB を使った)。冷やす回は名目を倍(上限の
  ;; 値 maxSizeBytes 16 GiB)に書き、engine が広がる(読み 3 回)のを待ってから、係に file system を広げさせる。付けたまま — mount も
  ;; ticket も触らない。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(MOUNTED)) :engine-settle 3 :used {NAME (* 6 TENTH)}))
  (<- ran ApiRan (on-api api #((Step :verb "sweep"))))
  (assert (.endswith (get ran.outcomes 0) (+ " failed= grown=" NAME)) ran.outcomes)
  (assert (= (! (size-writes api)) [(str MAX)]) api.seen)
  (assert (= api.asked #((.format "grow {} {}" NAME PLACE))) api.asked)
  (assert (= (get api.fs-sizes NAME) MAX) api.fs-sizes)
  (assert (= api.held.volumes #(MOUNTED)) api.held.volumes)
  (assert (not (any (gfor seen api.seen (.startswith seen.path ATTACHMENTS-PATH)))) api.seen))


(deftest test-a-volume-with-room-or-at-the-largest-size-is-not-grown
  ;; 空きが線の上(10 GiB のうち 4 GiB を使った)の volume と、名目が上限の maxSizeBytes に居る volume(ほぼ満ちていても)は広げない
  ;; (名目の書きも係への頼みも無い)。
  (for [#(sizes used) #(#({} {NAME (* 4 TENTH)}) #({NAME MAX} {NAME (- MAX 1024)}))]
    (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(MOUNTED)) :sizes sizes :fs-sizes sizes :used used))
    (<- ran ApiRan (on-api api #((Step :verb "sweep"))))
    (assert (.endswith (get ran.outcomes 0) " failed=") ran.outcomes)
    (assert (= (! (size-writes api)) []) api.seen)
    (assert (= api.asked #()) api.asked)))


(deftest test-a-grow-the-disk-cannot-hold-is-refused-without-writing-the-size
  ;; 札の disk の名目の残りが、広げる分(6 GiB)を受けられない(capacity 1 — 付いた 1 本で満ちた)。名目を書かずに、訳を残して次へ進む。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(MOUNTED) :capacity 1) :used {NAME (* 9 TENTH)}))
  (<- ran ApiRan (on-api api #((Step :verb "sweep"))))
  (assert (= ran.outcomes #((+ "sweep slots=0 cooled= failed=" NAME))) ran.outcomes)
  (assert (= (! (size-writes api)) []) api.seen)
  (assert (= api.asked #()) api.asked))


(deftest test-a-file-system-left-behind-its-volume-is-grown-without-doubling-again
  ;; 前の広げが名目を書いた後、file system を広げる前に止まった(名目 16 GiB・file system 10 GiB)。次の回は名目を書き直さず(倍に
  ;; しない)、係に file system だけを広げさせる。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(MOUNTED)) :sizes {NAME MAX} :used {NAME (* 6 TENTH)}))
  (<- ran ApiRan (on-api api #((Step :verb "sweep"))))
  (assert (.endswith (get ran.outcomes 0) (+ " failed= grown=" NAME)) ran.outcomes)
  (assert (= (! (size-writes api)) []) api.seen)
  (assert (= api.asked #((.format "grow {} {}" NAME PLACE))) api.asked)
  (assert (= (get api.fs-sizes NAME) MAX) api.fs-sizes))


(deftest test-an-engine-that-does-not-grow-is-refused-before-the-file-system
  ;; Longhorn が名目の書きを受けたが、engine が広がらない。上限の秒(検の attach-seconds 5 秒)で断り、係には頼まない(名目は書いた
  ;; まま — 次の回が file system だけを広げ直す)。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(MOUNTED)) :engine-stuck True :used {NAME (* 6 TENTH)}))
  (<- ran ApiRan (on-api api #((Step :verb "sweep"))))
  (assert (.endswith (get ran.outcomes 0) (+ " failed=" NAME)) ran.outcomes)
  (assert (= (! (size-writes api)) [(str MAX)]) api.seen)
  (assert (= api.asked #()) api.asked))


;; --- 並べて走らせる(戻す・冷やす途中に次の頼みが来る)------------------------------------------------------------------

(defk later [seconds steps]
  {:pre [(: seconds float) (: steps tuple)] :post [(: % tuple)]}
  "seconds 秒待ってから筋書き steps を当てた結末の組を得るため(並べて走らせる 2 つ目の頼み — 途中に来たメッセージ)。"
  (<- (Delay seconds))
  (<- outcomes tuple (run-steps steps))
  outcomes)


(defk side-by-side [first second seconds]
  {:pre [(: first tuple) (: second tuple) (: seconds float)] :post [(: % tuple)]}
  "筋書き first と、seconds 秒後に始める筋書き second を並べて走らせ、2 つの結末の組を得るため。"
  (<- one Task (Spawn (run-steps first)))
  (<- two Task (Spawn (later seconds second)))
  (<- done list (Gather one two))
  (tuple done))


(defk together-on-api [world first second seconds]
  {:pre [(: world LonghornWorld) (: first tuple) (: second tuple) (: seconds float)] :post [(: % tuple)]}
  "本番の答え手で、相手役の世界 world に 2 つの筋書きを並べて当てた結末の組の組を得るため。"
  (<- done tuple (with_handlers [(state) (memory-file-handler WORKER-FILES) (sim-time-handler :clock (! (clock-from 0.0)))
                                 (node-environ NODE) (longhorn-peer world) (keeper-peer world) (workspace-volumes-api SITE)]
                   (side-by-side first second seconds)))
  done)


(deftest test-a-message-during-the-restore-joins-it
  ;; 冷えた持ち主に頼みが来て戻す途中(読み 8 回 ≒ 4 秒)に、同じ持ち主の次の頼みが 1 秒後に来る。次の頼みの作成は先の戻しの終わりを
  ;; 待って ALREADY で返り(volume の作成は 1 度 = 戻すのは 1 度)、attach と mount が続く。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :backups #(COLD-BACKUP)) :restore-settle 8))
  (<- done tuple (together-on-api api #((Step :verb "create" :owner OWNER)) START 1.0))
  (assert (= done #(#("restored") #("already" "done" "done"))) done)
  (assert (= (len api.created) 1) api.created)
  (assert (= api.restoring {}) api.restoring)
  (val after (get api.held.volumes 0))
  (assert (= api.held.volumes #((HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True :used-at after.used-at))) api.held.volumes)
  ;; attach の印は、次の頼みが来た(1 秒)後・戻し終わりの後の刻。
  (assert (> after.used-at 1.0) after)
  ;; ticket は戻し終わった後に 1 度だけ置かれた(戻す途中の volume に付けに行かない)。
  (val tickets (lfor seen api.seen :if (and (= seen.method "PATCH") (.startswith seen.path ATTACHMENTS-PATH)) seen))
  (assert (= (len tickets) 1) tickets))


(deftest test-a-message-during-the-cooling-keeps-the-volume
  ;; 温かい volume の backup の途中(読み 8 回)に頼みが来る。使う側が勝つ: 作成は ALREADY・attach と mount が通り、冷やす側は volume を
  ;; 消さずに退く(backup は残る)。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM)) :backup-settle 8))
  (<- done tuple (together-on-api api #((Step :verb "cool" :owner OWNER)) START 1.0))
  (val cooling (get done 0))
  (val using (get done 1))
  (assert (and (= (len cooling) 1) (in "冷やす途中に使い始めた(ticket longhorn-api-" (get cooling 0))
               (in "volume を消さない(backup ws-c-exampleownerid000000000001-cool-0 は残す)" (get cooling 0)))
          cooling)
  (assert (= using #("already" "done" "done")) using)
  (assert (= api.held.volumes #((HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True :used-at 1.0))) api.held.volumes)
  (assert (= (tuple (gfor one api.held.backups one.name)) #((+ NAME "-cool-0"))) api.held.backups)
  (assert (not (any (gfor seen api.seen (= seen.method "DELETE")))) api.seen))


(deftest test-a-message-while-the-volume-is-being-deleted-waits-and-restores
  ;; 冷やす側が消し始めた後(消え終わるまで読み 4 回)に頼みが来る。作成は消え終わりを待ってから backup から戻す。冷やす側も DONE。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM)) :dying-settle 4))
  (<- done tuple (together-on-api api #((Step :verb "cool" :owner OWNER)) START 0.5))
  (assert (= done #(#("done") #("restored" "done" "done"))) done)
  (assert (= api.held.volumes #((HeldVolume :name NAME :node NODE :mounted-at PLACE :formatted True :used-at (. (get api.held.volumes 0) used-at))))
          api.held.volumes)
  (assert (= (len api.created) 1) api.created)
  (assert (in "fromBackup" (get (json.loads (get api.created 0)) "spec")) api.created))


(deftest test-an-attach-whose-volume-was-deleted-under-it-asks-to-start-over
  ;; 冷やす側の削除が、attach の ticket の書きと印の書きの間に入った(頭の註の窓)。attach は印の書きの 404 で消されたと知り、作る(戻す)
  ;; から出し直すよう断る。出し直すと backup から戻って使える。
  (val api (LonghornWorld (WorkspaceVolumeWorld NODE :volumes #(WARM) :backups #(COLD-BACKUP)) :vanish-after-ticket 1))
  (<- ran ApiRan (on-api api (+ #((Step :verb "attach" :owner OWNER)) START)))
  (assert (= (! (kinds ran.outcomes)) #("refused" "restored" "done" "done")) ran.outcomes)
  (assert (in "ticket を置いた後に volume が消された(冷やされた — 作る(戻す)から出し直す)" (get ran.outcomes 0)) ran.outcomes))
