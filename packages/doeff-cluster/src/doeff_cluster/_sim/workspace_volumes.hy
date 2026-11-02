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
(require doeff-hy.macros [defhandler defk val var])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import dataclasses [replace])
(import posixpath)
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume UmountWorkspaceVolume WorkspaceOutcome
                                                           WorkspaceVolumeRefused])
(import doeff_cluster._shared.core.workspace_volume_rules [owner-refusal volume-name])


(defrecord HeldVolume
  "模擬の世界の volume 1 つ: name = volume の名・node = attach された node(None = 外れている)・mounted-at = 付いている path(None = 付いて
   いない)・formatted = file system を持つか(初めて mount した時に作る)。"
  (#^ str name)
  (setv #^ (| str None) node None)
  (setv #^ (| str None) mounted-at None)
  (setv #^ bool formatted False))


(defclass WorkspaceVolumeWorld []
  "模擬の答え手 held-workspace-volumes の世界と、volume の様子の入れ物 — 検と模擬の世界が、走らせた後に読むため。node = 答え手の機体の
   node・volumes = volume の様子(初めて作った順 — 書き換えるたびに組を作り直す)・busy = 使っている process の在る path(umount を断る)・
   down = API server に届かない世界か。"
  (defn #^ None __init__ [self #^ str node #^ tuple [volumes #()] #^ frozenset [busy (frozenset)] #^ bool [down False]]
    ;; defk にできない: class の作り手(Python が呼ぶ口)
    "入れ物を作るため(volumes = 前から在る volume)。"
    (setv self.node node
          #^ (get tuple #(HeldVolume ...)) self.volumes volumes
          self.busy busy
          self.down down)))


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


(defk sim-created [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "CreateWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val name (! (sim-name world owner "作成" True)))
  (if (is-not (! (held-volume world name)) None)
      WorkspaceOutcome.ALREADY
      (do (! (put-volume world (HeldVolume :name name)))
          WorkspaceOutcome.DONE)))


(defk sim-attached [world owner]
  {:pre [(: world WorkspaceVolumeWorld) (: owner str)] :post [(: % WorkspaceOutcome)] :tags {:context "doeff-cluster" :role "foundation"}}
  "AttachWorkspaceVolume を世界 world に当てるため(頭の註の契約)。"
  (val doing "attach")
  (val name (! (sim-name world owner doing True)))
  (val volume (! (held-volume world name)))
  (cond
    (is volume None) (raise (! (sim-refused owner doing "volume が無い(先に作る)")))
    (= volume.node world.node) WorkspaceOutcome.ALREADY
    (is-not volume.node None)
    (raise (! (sim-refused owner doing (.format "node {} への ticket が在る(別の機体で使っているかもしれない — 外さない)" volume.node))))
    True (do (! (put-volume world (replace volume :node world.node)))
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
  (cond
    (or (is volume None) (is volume.node None)) WorkspaceOutcome.ALREADY
    (!= volume.node world.node)
    (raise (! (sim-refused owner doing (.format "ticket が別の node {} に在る(その機体で使っているかもしれない — 外さない)" volume.node))))
    True (do (! (put-volume world (replace volume :node None)))
             WorkspaceOutcome.DONE)))


;; 引数に残す理由: 世界は呼び手が作って走らせた後に読む記録の置き場(検と模擬の世界ごとに別の入れ物)。
(defhandler held-workspace-volumes [#^ WorkspaceVolumeWorld world]
  {:tags {:context "doeff-cluster" :role "foundation"}}
  ;; 模擬: 5 つの effect を世界に当てる(頭の註の契約)。
  (CreateWorkspaceVolume [owner]
    (val outcome (! (sim-created world owner)))
    (resume outcome))
  (AttachWorkspaceVolume [owner]
    (val outcome (! (sim-attached world owner)))
    (resume outcome))
  (MountWorkspaceVolume [owner path]
    (val outcome (! (sim-mounted world owner path)))
    (resume outcome))
  (UmountWorkspaceVolume [owner path]
    (val outcome (! (sim-unmounted world owner path)))
    (resume outcome))
  (DetachWorkspaceVolume [owner]
    (val outcome (! (sim-detached world owner)))
    (resume outcome)))
