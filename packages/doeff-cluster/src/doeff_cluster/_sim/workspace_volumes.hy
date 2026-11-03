;;; 持ち主ごとの作業場所の volume の effect(型は doeff_cluster._shared.intent.workspace_volume_model)の模擬の答え手(模擬の環境)。
;;; 本番の答え手(doeff_cluster._shared.protocol.workspace_volumes の workspace-volumes-api — Longhorn の物と mount の係)と同じ結末で答え、
;;; volume の様子を入れ物に残す(検と模擬の世界が、走らせた後に読む)。
;;;
;;; 契約(本番と同じ — tests/test_workspace_volumes_handler.hy が両方の答え手で同じ筋書きを回す):
;;;   * 名の条件を外れる持ち主の id は、何もせずに断る。
;;;   * 作る: 無ければ作って DONE・在れば ALREADY。届かない API server は断る。
;;;   * attach: 無い volume・別の node に付いている volume は断る。この node に付いていれば ALREADY・外れていれば付けて DONE。
;;;   * mount: 付ける先が絶対の正規の path でない・付ける先に別の volume・この node に attach されていない・別の path に付いている時は断る。
;;;     付ける先に同じ volume が付いていれば ALREADY・ほかは付けて DONE(空の volume は ext4 を作った事にする)。
;;;   * umount: 付ける先に何も無ければ ALREADY・別の volume なら断る・使っている process が在れば断る・ほかは外して DONE。
;;;   * detach: まだ mount されていれば断る・別の node に付いていれば断る・無い volume と外れた volume は ALREADY・ほかは外して DONE。
;;;   * 作る(戻す): volume が無く、その名の完了した backup が在れば戻して RESTORED(file system を持つ)。backup は在るが完了した物が無ければ断る。
;;;   * 置き場: 作る(戻す)前に、札の disk の残り(capacity − volume の数)が無ければ作らずに WorkspaceRoomShort で断る(fresh = 戻す
;;;     backup が無い)。残りの読み(ReadWorkspaceRoom)は同じ残りと spare-volumes を答える。
;;;   * 使った刻: attach(付けた時)と detach(外した時)が volume の used-at に今の刻を書く。
;;;   * 冷やす: 無い volume は ALREADY・付いている volume は断る・ほかは backup(<名>-cool-<刻>)を足して volume を消し DONE。
;;;   * 消す: volume が在れば断る・backup が無ければ ALREADY・最後の backup から keep-days の内なら断る・ほかは backup を消して DONE。
;;;   * まとめて冷やす: 外れている volume を cooling-order(空き = capacity − volume の数)の順に冷やす。
;;; 模擬の世界の時間は doeff-time の GetTime(外側に時計の答え手が要る)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import dataclasses [replace])
(import datetime [datetime])
(import posixpath)
(import doeff_time [GetTime])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CoolIdleWorkspaceVolumes CoolingSweep
                                                           CoolWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume PurgeWorkspaceVolume ReadWorkspaceRoom
                                                           UmountWorkspaceVolume WorkspaceOutcome WorkspaceRoom WorkspaceRoomShort
                                                           WorkspaceVolumeRefused])
(import doeff_cluster._shared.core.workspace_volume_rules [NAME-PREFIX WarmVolume cooling-name cooling-order owner-refusal ticket-id
                                                         volume-name])


(defrecord HeldVolume
  "模擬の世界の volume 1 つ: name = volume の名・node = attach された node(None = 外れている)・mounted-at = 付いている path(None = 付いて
   いない)・formatted = file system を持つか(初めて mount した時に作る・backup から戻した volume は持つ)・used-at = 使った刻(epoch 秒 —
   作った時・attach と detach が書く)。"
  (#^ str name)
  (setv #^ (| str None) node None)
  (setv #^ (| str None) mounted-at None)
  (setv #^ bool formatted False)
  (setv #^ float used-at 0.0))


(defrecord HeldBackup
  "模擬の世界の backup 1 つ: volume = volume の名・name = backup の名・created-at = 作った刻(epoch 秒)・state = 様子(Completed・
   InProgress・Error)。"
  (#^ str volume)
  (#^ str name)
  (#^ float created-at)
  (setv #^ str state "Completed"))


(defclass WorkspaceVolumeWorld []
  "模擬の答え手 held-workspace-volumes の世界と、volume の様子の入れ物 — 検と模擬の世界が、走らせた後に読むため。node = 答え手の機体の
   node・volumes = volume の様子(初めて作った順 — 書き換えるたびに組を作り直す)・backups = backup(作った順)・busy = 使っている process の
   在る path(umount を断る)・down = API server に届かない世界か・capacity = 札の disk が名目で受ける volume の本数・warm-hours・
   spare-volumes・keep-days = 値の ConfigMap の同じ名の値。"
  (defn #^ None __init__ [self #^ str node #^ tuple [volumes #()] #^ frozenset [busy (frozenset)] #^ bool [down False]
                          #^ tuple [backups #()] #^ int [capacity 20] #^ int [warm-hours 2] #^ int [spare-volumes 4]
                          #^ int [keep-days 7]]
    ;; defk にできない: class の作り手(Python が呼ぶ口)
    "入れ物を作るため(volumes と backups = 前から在る物)。"
    (setv self.node node
          #^ (get tuple #(HeldVolume ...)) self.volumes volumes
          #^ (get tuple #(HeldBackup ...)) self.backups backups
          self.busy busy
          self.down down
          self.capacity capacity
          self.warm-hours warm-hours
          self.spare-volumes spare-volumes
          self.keep-days keep-days)))


(defk held-volume [world name]
  {:pre [(: world WorkspaceVolumeWorld) (: name str)] :post [(: % (| HeldVolume None))] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world の volume name の様子を得るため(無ければ None)。"
  (val found (lfor volume world.volumes :if (= volume.name name) volume))
  (if found (get found 0) None))


(defk put-volume [world volume]
  {:pre [(: world WorkspaceVolumeWorld) (: volume HeldVolume)] :post [(: % None)] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world に volume の様子を置くため(同じ名が在れば置き換え、無ければ末に足す)。"
  (setv world.volumes (if (any (gfor one world.volumes (= one.name volume.name)))
                          (tuple (gfor one world.volumes (if (= one.name volume.name) volume one)))
                          (+ world.volumes #(volume))))
  None)


(defk sim-refused [owner doing reason]
  {:pre [(: owner str) (: doing str) (: reason str)] :post [(: % WorkspaceVolumeRefused)] :tags {:context "doeff-cluster" :role "foundation"}}
  "持ち主 owner の volume を doing できなかった断りを、本番と同じ綴りで作るため。"
  (val place (if (is (! (owner-refusal owner)) None) (+ "volume " (! (volume-name owner))) "volume"))
  (WorkspaceVolumeRefused (.format "持ち主 {} の {} を {} できない — {}" owner place doing reason)))


(defk sim-name [world owner doing api]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: doing str) (: api bool)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "持ち主 owner の volume の名を得るため(名の条件を外れる id と、API server に問う操作 api で届かない世界は断る)。"
  (val refusal (! (owner-refusal owner)))
  (when (is-not refusal None)
    (raise (! (sim-refused owner doing refusal))))
  (when (and api world.down)
    (raise (! (sim-refused owner doing "API server に届かない: 模擬の世界が down"))))
  (! (volume-name owner)))


(defk sim-target [owner doing path]
  {:pre [(: owner str) (: doing str) (: path str)] :post [(: % str)] :tags {:context "doeff-cluster" :role "foundation"}}
  "付ける先 path が絶対の正規の path かを本番と同じに確かめるため。"
  (when (or (not (.startswith path "/")) (!= (posixpath.normpath path) path) (= path "/"))
    (raise (! (sim-refused owner doing (+ "付ける先 '" path "' が絶対の正規の path でない")))))
  path)


(defk held-backups [world name]
  {:pre [(: world WorkspaceVolumeWorld) (: name str)] :post [(: % (get tuple #(HeldBackup ...)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world の volume name の backup を、作った順に得るため。"
  (tuple (gfor backup world.backups :if (= backup.volume name) backup)))


(defk sim-now []
  {:pre [] :post [(: % float)] :tags {:context "doeff-cluster" :role "foundation"}}
  "模擬の世界の今の刻を epoch 秒で得るため(外側の時計の答え手が答える)。"
  (<- now datetime (GetTime))
  (.timestamp now))


(defk sim-created [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "CreateWorkspaceVolume を世界 world に当てるため(頭の註の契約 — 冷えた名は完了した backup から戻す)。"
  (val doing "作成")
  (val name (! (sim-name world owner doing True)))
  (<- now float (sim-now))
  (val backups (! (held-backups world name)))
  (val done (lfor backup backups :if (= backup.state "Completed") backup))
  (val there (is-not (! (held-volume world name)) None))
  (when (and (not there) (or done (not backups)) (< (! (sim-slots world)) 1))
    (raise (WorkspaceRoomShort (str (! (sim-refused owner doing "札の disk がもう受けられない(模擬の世界の capacity)"))) (not done))))
  (cond
    there WorkspaceOutcome.ALREADY
    done (do (! (put-volume world (HeldVolume :name name :formatted True :used-at now)))
             WorkspaceOutcome.RESTORED)
    backups (raise (! (sim-refused owner doing (+ "backup は在るが完了した物が無い(空で作らない): "
                                                  (.join "・" (gfor one backups (+ one.name " " one.state)))))))
    True (do (! (put-volume world (HeldVolume :name name :used-at now)))
             WorkspaceOutcome.DONE)))


(defk sim-slots [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % int) (>= % 0)] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world の札の disk が、あと何本の volume を受けられるかを得るため(capacity − volume の数・負は 0)。"
  (max 0 (- world.capacity (len world.volumes))))


(defk sim-attached [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "AttachWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "attach")
  (val name (! (sim-name world owner doing True)))
  (val volume (! (held-volume world name)))
  (<- now float (sim-now))
  (cond
    (is volume None) (raise (! (sim-refused owner doing "volume が無い(先に作る)")))
    (= volume.node world.node) WorkspaceOutcome.ALREADY
    (is-not volume.node None)
    (raise (! (sim-refused owner doing (.format "node {} への ticket が在る(別の機体で使っているかもしれない — 外さない)" volume.node))))
    True (do (! (put-volume world (replace volume :node world.node :used-at now)))
             WorkspaceOutcome.DONE)))


(defk sim-mounted [world owner path]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "MountWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "mount")
  (val name (! (sim-name world owner doing False)))
  (val target (! (sim-target owner doing path)))
  (val there (lfor one world.volumes :if (= one.mounted-at target) one))
  (val volume (! (held-volume world name)))
  (cond
    (and there (= (. (get there 0) name) name)) WorkspaceOutcome.ALREADY
    there (raise (! (sim-refused owner doing (.format "{} には別の物({})が付いている" target (. (get there 0) name)))))
    (or (is volume None) (!= volume.node world.node))
    (raise (! (sim-refused owner doing (+ "mount の係が断った: volume " name " がこの node に attach されていない"))))
    (is-not volume.mounted-at None)
    (raise (! (sim-refused owner doing (.format "mount の係が断った: {} は既に {} に付いている" name volume.mounted-at))))
    True (do (! (put-volume world (replace volume :mounted-at target :formatted True)))
             WorkspaceOutcome.DONE)))


(defk sim-unmounted [world owner path]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "UmountWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "umount")
  (val name (! (sim-name world owner doing False)))
  (val target (! (sim-target owner doing path)))
  (val there (lfor one world.volumes :if (= one.mounted-at target) one))
  (cond
    (not there) WorkspaceOutcome.ALREADY
    (!= (. (get there 0) name) name)
    (raise (! (sim-refused owner doing (.format "{} に付いているのは volume {} でない({})" target name (. (get there 0) name)))))
    (in target world.busy)
    (raise (! (sim-refused owner doing (+ "mount の係が断った: " target " を外せなかった(使っている process が在るかもしれない)"))))
    True (do (! (put-volume world (replace (get there 0) :mounted-at None)))
             WorkspaceOutcome.DONE)))


(defk sim-detached [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "DetachWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "detach")
  (val name (! (sim-name world owner doing False)))
  (val volume (! (held-volume world name)))
  (when (and (is-not volume None) (is-not volume.mounted-at None))
    (raise (! (sim-refused owner doing (+ "まだ " volume.mounted-at " に mount されている(先に umount)")))))
  (when world.down
    (raise (! (sim-refused owner doing "API server に届かない: 模擬の世界が down"))))
  (<- now float (sim-now))
  (cond
    (or (is volume None) (is volume.node None)) WorkspaceOutcome.ALREADY
    (!= volume.node world.node)
    (raise (! (sim-refused owner doing (.format "ticket が別の node {} に在る(その機体で使っているかもしれない — 外さない)" volume.node))))
    True (do (! (put-volume world (replace volume :node None :used-at now)))
             WorkspaceOutcome.DONE)))


(defk sim-cooled [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "CoolWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "冷やす")
  (val name (! (sim-name world owner doing True)))
  (val volume (! (held-volume world name)))
  (<- now float (sim-now))
  (cond
    (is volume None) WorkspaceOutcome.ALREADY
    (is-not volume.node None) (raise (! (sim-refused owner doing (+ "使っている(ticket " (! (ticket-id name)) ")"))))
    True (do (setv world.backups (+ world.backups #((HeldBackup :volume name :name (! (cooling-name name (int now))) :created-at now)))
                   world.volumes (tuple (gfor one world.volumes :if (!= one.name name) one)))
             WorkspaceOutcome.DONE)))


(defk sim-purged [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "PurgeWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "消す")
  (val name (! (sim-name world owner doing True)))
  (val backups (! (held-backups world name)))
  (<- now float (sim-now))
  (val days (if backups (/ (- now (max (gfor one backups one.created-at))) 86400) 0.0))
  (cond
    (is-not (! (held-volume world name)) None)
    (raise (! (sim-refused owner doing "Longhorn の volume が在る(温かいか使っている — 先に冷やす)")))
    (not backups) WorkspaceOutcome.ALREADY
    (< days world.keep-days)
    (raise (! (sim-refused owner doing (.format "最後の backup から {:.1f} 日(keepDays {} 日を過ぎるまで消さない)" days world.keep-days))))
    True (do (setv world.backups (tuple (gfor one world.backups :if (!= one.volume name) one)))
             WorkspaceOutcome.DONE)))


(defk sim-idle-cooled [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % CoolingSweep)] :tags {:context "doeff-cluster" :role "foundation"}}
  "CoolIdleWorkspaceVolumes を世界 world に当てるため(頭の註の契約)。"
  (when world.down
    (raise (WorkspaceVolumeRefused "持ち主 全部 の volume を 冷やす順の読み できない — API server に届かない: 模擬の世界が down")))
  (val slots (! (sim-slots world)))
  (val warm (tuple (gfor one world.volumes :if (is one.node None) (WarmVolume :name one.name :used-at one.used-at))))
  (<- now float (sim-now))
  (val order (! (cooling-order warm now (* world.warm-hours 3600.0) slots world.spare-volumes)))
  (var cooled #())
  (for [name order]
    (<- outcome WorkspaceOutcome (sim-cooled world (cut name (len NAME-PREFIX) None)))
    (when (= outcome WorkspaceOutcome.DONE)
      (:= cooled (+ cooled #(name)))))
  (CoolingSweep :slots slots :cooled cooled :failed #()))


(defk sim-room [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % WorkspaceRoom)] :tags {:context "doeff-cluster" :role "foundation"}}
  "ReadWorkspaceRoom を世界 world に当てるため(頭の註の契約)。"
  (when world.down
    (raise (WorkspaceVolumeRefused "持ち主 全部 の volume を 置き場の読み できない — API server に届かない: 模擬の世界が down")))
  (WorkspaceRoom :slots (! (sim-slots world)) :spare world.spare-volumes))


;; 引数に残す理由: 世界は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-workspace-volumes [#^ WorkspaceVolumeWorld world]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 9 つの effect を世界に当てる(頭の註の契約)。
  (CreateWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-created world owner))
    (resume outcome))
  (AttachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-attached world owner))
    (resume outcome))
  (MountWorkspaceVolume [owner path]
    (val outcome (! (sim-mounted world owner path)))
    (resume outcome))
  (UmountWorkspaceVolume [owner path]
    (val outcome (! (sim-unmounted world owner path)))
    (resume outcome))
  (DetachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-detached world owner))
    (resume outcome))
  (CoolWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-cooled world owner))
    (resume outcome))
  (PurgeWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-purged world owner))
    (resume outcome))
  (CoolIdleWorkspaceVolumes []
    (<- sweep CoolingSweep (sim-idle-cooled world))
    (resume sweep))
  (ReadWorkspaceRoom []
    (<- room WorkspaceRoom (sim-room world))
    (resume room)))
