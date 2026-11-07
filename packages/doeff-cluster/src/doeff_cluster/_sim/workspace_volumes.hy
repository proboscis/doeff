;;; 持ち主ごとの作業場所の volume の effect(型は doeff_cluster._shared.intent.workspace_volume_model)の模擬の答え手(模擬の環境)。
;;; 本番の答え手(doeff_cluster._shared.entry.workspace_volume_api の workspace-volumes-api — Longhorn の物と mount の係)と同じ結末で答え、
;;; volume の様子を入れ物に残す(検と模擬の世界が、走らせた後に読む)。
;;;
;;; 契約(本番と同じ — tests/test_workspace_volumes_handler.hy が両方の答え手で同じ筋書きを回す):
;;;   * 名の条件を外れる持ち主の id は、何もせずに断る。
;;;   * 作る: 無ければ作って DONE・在れば ALREADY。届かない API server は区画の基盤の止まり(WorkspaceClusterUnreachable)で断る。
;;;   * attach: 無い volume・別の node に付いている volume は断る。この node に付いていれば ALREADY・外れていれば付けて DONE。
;;;   * mount: 付ける先が絶対の正規の path でない・付ける先に別の volume・付ける先の dir に中身が在る(隠さない — 外側の file の答え手で
;;;     読む)・この node に attach されていない・別の path に付いている時は断る。
;;;     付ける先に同じ volume が付いていれば ALREADY・ほかは付けて DONE(空の volume は ext4 を作った事にする)。
;;;   * umount: 付ける先に何も無ければ ALREADY・別の volume なら断る・使っている process が在れば断る・ほかは外して DONE。
;;;   * detach: まだ mount されていれば断る・別の node に付いていれば断る・無い volume と外れた volume は ALREADY・ほかは外して DONE。
;;;   * 作る(戻す): volume が無く、その名の完了した backup が在れば戻して RESTORED(file system を持つ)。backup は在るが完了した物が無ければ断る。
;;;   * 置き場: 作る(戻す)前に、札の disk の線ごとの残りの byte(名目 = capacity × size-bytes − volume の名目の和・実 = 同じ大きさ −
;;;     volume の実の使用の和 − 最小の空き minimal%)が入る大きさ(戻しは backup の大きさ)を覆わなければ、外れた volume を本番と同じ
;;;     冷やす順(cooling-order — 温かく置く時間を待たない)で冷やしてから作る。冷やしても覆えなければ作らずに WorkspaceRoomShort で断る
;;;     (fresh = 戻す backup が無い)。残りの読み(ReadWorkspaceRoom)は同じ残りと spare-bytes を答える。
;;;   * 使った刻: attach(付けた時)と detach(外した時)が volume の used-at に今の刻を書く。
;;;   * 冷やす: 無い volume は ALREADY・付いている volume は断る・ほかは backup(<名>-cool-<刻>)を足して volume を消し DONE。
;;;   * 消す: volume が在れば断る・backup が無ければ ALREADY・最後の backup から keep-days の内なら断る・ほかは backup を消して DONE。
;;;   * まとめて冷やす: 外れている volume を cooling-order(線ごとの残りの byte・需要 = spare-bytes)の順に冷やす。
;;;   * 付ける先の読み: 付ける先に持ち主の volume が付いていれば MOUNTED・別の volume なら CLASH。何も付いていなければ、dir が無いか空なら
;;;     READY・中身が在れば、持ち主の volume か backup が在る時 CLASH・無い時 OUTSIDE。
;;;   * 付いた volume の読み: 世界の機体の node に付いた volume を、名の順に持ち主の id(小文字)と使った刻で答える。
;;; 模擬の世界の時間は doeff-time の GetTime(外側に時計の答え手が要る)。付ける先の dir の中身は外側の file の答え手(本番と同じ effect)。
(require doeff-hy.macros [defhandler defk <- val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import dataclasses [replace])
(import datetime [datetime])
(import posixpath)
(import doeff_time [GetTime])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachedWorkspaceVolume AttachWorkspaceVolume CoolIdleWorkspaceVolumes CoolingSweep
                                                           CoolWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume PurgeWorkspaceVolume ReadAttachedWorkspaceVolumes ReadWorkspacePlace
                                                           ReadWorkspaceRoom UmountWorkspaceVolume WorkspaceOutcome WorkspacePlace
                                                           WorkspaceClusterUnreachable WorkspaceRoom WorkspaceRoomShort WorkspaceVolumeRefused])
(import doeff_core_effects [FileFailed ListDirectory PathKind PathStat StatPath])
(import doeff_cluster._shared.core.workspace_volume_rules [NAME-PREFIX CoolingPlan RoomBytes WarmVolume cooling-name cooling-order
                                                         owner-refusal room-covers? ticket-id volume-name])


(defrecord HeldVolume
  "模擬の世界の volume 1 つ: name = volume の名・node = attach された node(None = 外れている)・mounted-at = 付いている path(None = 付いて
   いない)・formatted = file system を持つか(初めて mount した時に作る・backup から戻した volume は持つ)・used-at = 使った刻(epoch 秒 —
   作った時・attach と detach が書く)・size = 名目の大きさ(byte — 0 = 世界の size-bytes)・actual = 実の使用(byte)。"
  (#^ str name)
  (setv #^ (| str None) node None)
  (setv #^ (| str None) mounted-at None)
  (setv #^ bool formatted False)
  (setv #^ float used-at 0.0)
  (setv #^ int size 0)
  (setv #^ int actual 0))


(defrecord HeldBackup
  "模擬の世界の backup 1 つ: volume = volume の名・name = backup の名・created-at = 作った刻(epoch 秒)・state = 様子(Completed・
   InProgress・Error)・size と actual = 取った時の volume の名目の大きさと実の使用(byte — 0 の size = 世界の size-bytes。戻す volume は
   この大きさで作る)。"
  (#^ str volume)
  (#^ str name)
  (#^ float created-at)
  (setv #^ str state "Completed")
  (setv #^ int size 0)
  (setv #^ int actual 0))


(defclass WorkspaceVolumeWorld []
  "模擬の答え手 held-workspace-volumes の世界と、volume の様子の入れ物 — 検と模擬の世界が、走らせた後に読むため。node = 答え手の機体の
   node・volumes = volume の様子(初めて作った順 — 書き換えるたびに組を作り直す)・backups = backup(作った順)・busy = 使っている process の
   在る path(umount を断る)・down = API server に届かない世界か・capacity = 札の disk の大きさ(作る時の大きさ size-bytes の volume の
   本数で数える — 名目の予約の線は過剰割り当て 100% の capacity × size-bytes byte)・minimal = 実の空きの線の最小の空きの %・warm-hours・
   spare-bytes・keep-days・size-bytes = 値の ConfigMap の warmHours・spareBytes・keepDays・sizeBytes。"
  (defn #^ None __init__ [self #^ str node #^ (get tuple #(HeldVolume ...)) [volumes #()] #^ frozenset [busy (frozenset)]
                          #^ bool [down False] #^ (get tuple #(HeldBackup ...)) [backups #()] #^ int [capacity 20] #^ int [minimal 0] #^ int [warm-hours 2]
                          #^ int [spare-bytes 17179869184] #^ int [keep-days 7] #^ int [size-bytes 2147483648]]
    ;; defk にできない: class の作り手(Python が呼ぶ口)
    "入れ物を作るため(volumes と backups = 前から在る物)。"
    (setv self.node node
          #^ (get tuple #(HeldVolume ...)) self.volumes volumes
          #^ (get tuple #(HeldBackup ...)) self.backups backups
          self.busy busy
          self.down down
          self.capacity capacity
          self.minimal minimal
          self.warm-hours warm-hours
          self.spare-bytes spare-bytes
          self.keep-days keep-days
          self.size-bytes size-bytes)))


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
    (raise (WorkspaceClusterUnreachable (str (! (sim-refused owner doing "API server に届かない: 模擬の世界が down"))))))
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
  (val source (if done (get done -1) None))
  (when (and (not there) (or done (not backups)))
    (<- (sim-room-made world owner doing (max world.size-bytes (if (is source None) 0 (or source.size world.size-bytes))) (not done))))
  (cond
    there WorkspaceOutcome.ALREADY
    done (do (! (put-volume world (HeldVolume :name name :formatted True :used-at now :size source.size :actual source.actual)))
             WorkspaceOutcome.RESTORED)
    backups (raise (! (sim-refused owner doing (+ "backup は在るが完了した物が無い(空で作らない): "
                                                  (.join "・" (gfor one backups (+ one.name " " one.state)))))))
    True (do (! (put-volume world (HeldVolume :name name :used-at now)))
             WorkspaceOutcome.DONE)))


(defk sim-room-bytes [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % RoomBytes)] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world の札の disk の線ごとの残りの byte を得るため(頭の註の契約 — 本番の room-bytes の数えを、過剰割り当て 100% の 1 枚の disk に
   当てた物)。"
  (val maximum (* world.capacity world.size-bytes))
  (RoomBytes :nominal (- maximum (sum (gfor one world.volumes (or one.size world.size-bytes))))
             :actual (- maximum (sum (gfor one world.volumes one.actual)) (// (* maximum world.minimal) 100))))


(defk sim-warm [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % (get tuple #(WarmVolume ...)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "世界 world の外れている volume を、冷やす順の判断に渡す形にするため(名目の大きさと実の使用を添える)。"
  (tuple (gfor one world.volumes :if (is one.node None)
               (WarmVolume :name one.name :used-at one.used-at :size (or one.size world.size-bytes) :actual one.actual))))


(defk sim-room-made [world owner doing demand fresh]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: doing str) (: demand int) (> demand 0) (: fresh bool)] :post [(: % None)]
   :tags {:context "doeff-cluster" :role "foundation"}}
  "作る前に、世界 world の札の disk の残りが需要 demand を両方の線で覆う事を確かめるため。覆わなければ外れた volume を本番と同じ冷やす順
   (温かく置く時間を待たない)で冷やし、冷やしても覆えなければ置き場の不足で断る(頭の註の契約)。"
  (val room (! (sim-room-bytes world)))
  (when (! (room-covers? room demand))
    (return None))
  (val short (.format "札の disk が名目 {} byte の volume をもう受けられない(模擬の世界の capacity — 名目の予約 {} byte・実の空き {} byte)"
                      demand room.nominal room.actual))
  (<- now float (sim-now))
  (val plan (! (cooling-order (! (sim-warm world)) now (float "inf") room demand)))
  (when (not plan.covered)
    (raise (WorkspaceRoomShort (str (! (sim-refused owner doing (+ short " — 外れた volume を全部冷やしても足りない")))) fresh)))
  (for [name plan.names]
    (<- (sim-cooled world (cut name (len NAME-PREFIX) None))))
  (when (not (! (room-covers? (! (sim-room-bytes world)) demand)))
    (raise (WorkspaceRoomShort (str (! (sim-refused owner doing (+ short " — 冷やした後も足りない")))) fresh)))
  None)


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


(defk sim-filled [target]
  {:pre [(: target str)] :post [(: % bool)] :tags {:context "doeff-cluster" :role "foundation"}}
  "付ける先 target に中身が在るか(dir の中に何か在る・dir でない物が在る)を、外側の file の答え手で読むため(本番と同じ読み — 無い path・
   空の dir・読めない path は False)。"
  (<- held (| PathStat FileFailed) (StatPath target))
  (when (isinstance held FileFailed)
    (return False))
  (match held.kind
    PathKind.MISSING False
    PathKind.DIRECTORY (do (<- listed (| tuple FileFailed) (ListDirectory target))
                           (and (not (isinstance listed FileFailed)) (bool listed)))
    _ True))


(defk sim-place [world owner path]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: path str)] :post [(: % WorkspacePlace)] :tags {:context "doeff-cluster" :role "foundation"}}
  "ReadWorkspacePlace を世界 world に当てるため(頭の註の契約)。"
  (val doing "付ける先の読み")
  (val name (! (sim-name world owner doing True)))
  (val target (! (sim-target owner doing path)))
  (val there (lfor one world.volumes :if (= one.mounted-at target) one))
  (when there
    (return (if (= (. (get there 0) name) name) WorkspacePlace.MOUNTED WorkspacePlace.CLASH)))
  (<- filled bool (sim-filled target))
  (cond
    (not filled) WorkspacePlace.READY
    (or (is-not (! (held-volume world name)) None) (! (held-backups world name))) WorkspacePlace.CLASH
    True WorkspacePlace.OUTSIDE))


(defk sim-mounted [world owner path]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str) (: path str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "MountWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "mount")
  (val name (! (sim-name world owner doing False)))
  (val target (! (sim-target owner doing path)))
  (val there (lfor one world.volumes :if (= one.mounted-at target) one))
  (val volume (! (held-volume world name)))
  (<- filled bool (sim-filled target))
  (val covered (and (not there) filled))
  (cond
    (and there (= (. (get there 0) name) name)) WorkspaceOutcome.ALREADY
    there (raise (! (sim-refused owner doing (.format "{} には別の物({})が付いている" target (. (get there 0) name)))))
    covered (raise (! (sim-refused owner doing (+ target " に中身が在る(volume の外に書かれた物 — 付けると隠すので付けない)"))))
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
    (raise (WorkspaceClusterUnreachable (str (! (sim-refused owner doing "API server に届かない: 模擬の世界が down"))))))
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
    True (do (setv world.backups (+ world.backups #((HeldBackup :volume name :name (! (cooling-name name (int now))) :created-at now
                                                                :size volume.size :actual volume.actual)))
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
    (raise (WorkspaceClusterUnreachable "持ち主 全部 の volume を 冷やす順の読み できない — API server に届かない: 模擬の世界が down")))
  (val room (! (sim-room-bytes world)))
  (<- now float (sim-now))
  (val plan (! (cooling-order (! (sim-warm world)) now (* world.warm-hours 3600.0) room world.spare-bytes)))
  (var cooled #())
  (for [name plan.names]
    (<- outcome WorkspaceOutcome (sim-cooled world (cut name (len NAME-PREFIX) None)))
    (when (= outcome WorkspaceOutcome.DONE)
      (:= cooled (+ cooled #(name)))))
  (CoolingSweep :nominal room.nominal :actual room.actual :cooled cooled :failed #() :covered plan.covered))


(defk sim-room [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % WorkspaceRoom)] :tags {:context "doeff-cluster" :role "foundation"}}
  "ReadWorkspaceRoom を世界 world に当てるため(頭の註の契約)。"
  (when world.down
    (raise (WorkspaceClusterUnreachable "持ち主 全部 の volume を 置き場の読み できない — API server に届かない: 模擬の世界が down")))
  (val room (! (sim-room-bytes world)))
  (WorkspaceRoom :nominal room.nominal :actual room.actual :spare world.spare-bytes))


(defk sim-attached-volumes [world]
  {:pre [(: world WorkspaceVolumeWorld)] :post [(: % (get tuple #(AttachedWorkspaceVolume ...)))] :tags {:context "doeff-cluster" :role "foundation"}}
  "ReadAttachedWorkspaceVolumes を世界 world に当てるため(頭の註の契約 — 世界の機体の node に付いた volume を名の順に)。"
  (when world.down
    (raise (WorkspaceClusterUnreachable "持ち主 全部 の volume を 付いた volume の読み できない — API server に届かない: 模擬の世界が落ちている")))
  (tuple (gfor one (sorted world.volumes :key (fn [one] one.name)) :if (= one.node world.node)
               (AttachedWorkspaceVolume :owner (cut one.name (len NAME-PREFIX) None) :used-at one.used-at))))


;; 引数に残す理由: 世界は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-workspace-volumes [#^ WorkspaceVolumeWorld world]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 11 の effect を世界に当てる(頭の註の契約)。
  (CreateWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-created world owner))
    (resume outcome))
  (AttachWorkspaceVolume [owner]
    (<- outcome WorkspaceOutcome (sim-attached world owner))
    (resume outcome))
  (MountWorkspaceVolume [owner path]
    (<- outcome WorkspaceOutcome (sim-mounted world owner path))
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
    (resume room))
  (ReadWorkspacePlace [owner path]
    (<- place WorkspacePlace (sim-place world owner path))
    (resume place))
  (ReadAttachedWorkspaceVolumes []
    (<- attached tuple (sim-attached-volumes world))
    (resume attached)))
