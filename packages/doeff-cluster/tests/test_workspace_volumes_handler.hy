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
;;; 外の世界は、この file の Longhorn の相手役と mount の係の相手役・memory の file・模擬の時計。級の parameters と値の ConfigMap の中身は
;;; 宣言の file(deploy/k8s/workspace-volumes の storageclass.yaml と workspace-volume.json)から読む。
(require doeff-hy.macros [deftest defk defhandler <- val var])
(require doeff-hy.record [defrecord])
(import collections.abc [Callable])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import json)
(import pathlib [Path])
(import yaml)
(import doeff [with_handlers])
(import doeff_core_effects [MemoryFile MemoryFiles ReadMemoryFiles])
(import doeff_core_effects [state])
(import doeff_core_effects [memory-file-handler])
(import doeff_core_effects [FileFailed PathKind PathStat ReadText RemoveTree RenamePath StatPath WriteText])
(import doeff_core_effects [HttpFailed HttpFailureKind HttpRequest HttpResponse])
(import doeff_core_effects [EnvEntry ReadEnvironment])
(import doeff_time [SimClock sim-time-handler])
(import doeff_cluster._shared.intent.workspace_volume_model [AttachWorkspaceVolume CreateWorkspaceVolume DetachWorkspaceVolume
                                                           MountWorkspaceVolume UmountWorkspaceVolume WorkspaceOutcome
                                                           WorkspaceVolumeRefused WorkspaceVolumeSite])
(import doeff_cluster._shared.core.workspace_volume_rules [ticket-id volume-name])
(import doeff_cluster._shared.protocol.workspace_volumes [workspace-volumes-api])
(import doeff_cluster._shared.protocol.supplied_values [KUBE-API SERVICE-ACCOUNT-TOKEN-FILE])
(import doeff_cluster._sim.workspace_volumes [HeldVolume WorkspaceVolumeWorld held-volume held-workspace-volumes put-volume])

(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})

(val DECLARED (/ (. (Path __file__) parent parent) "deploy" "k8s" "workspace-volumes"))
(val SETTINGS-TEXT (.read-text (/ DECLARED "workspace-volume.json") :encoding "utf-8"))
(val PARAMETERS (dict (get (yaml.safe-load (.read-text (/ DECLARED "storageclass.yaml") :encoding "utf-8")) "parameters")))
(val TOKEN "worker-identity-token")
(val NODE "zeus")
(val OWNER "[id]")
(val NAME "ws-c-k973zx4fcyma7pz498c0464b7g")
(val OTHER "c-OTHERZX4FCYMA7PZ498C0464B")
(val PLACE "/work/agent-workspaces/a-k973")
(val PLACE-2 "/work/agent-workspaces/a-k973-2")
;; 検の場所: 本番と同じ path・短い秒(待つ筋書きが模擬の時計で速く終わる)。
(val SITE (WorkspaceVolumeSite :longhorn-namespace "longhorn-system" :settings-namespace "agent-worker" :settings-name "workspace-volume"
                               :mount-dir "/run/workspace-mount" :mountinfo "/proc/self/mountinfo"
                               :attach-seconds 5.0 :detach-seconds 5.0 :mount-seconds 5.0 :poll-seconds 0.5))
(val VOLUMES-PATH "/apis/longhorn.io/v1beta2/namespaces/longhorn-system/volumes")
(val ATTACHMENTS-PATH "/apis/longhorn.io/v1beta2/namespaces/longhorn-system/volumeattachments")
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
  "筋書きの一手: verb = create・attach・mount・umount・detach・owner = 持ち主の id・path = 付ける先(mount と umount)。"
  (#^ str verb)
  (#^ str owner)
  (setv #^ (| str None) path None))


(defrecord ApiRan
  "本番の答え手の走り 1 つ: outcomes = 結末の組・left = 終わりの memory の file。"
  (#^ tuple outcomes)
  (#^ MemoryFiles left))


(defclass LonghornWorld []
  "Longhorn の相手役と mount の係の相手役の世界: held = volume の様子(模擬の答え手と同じ入れ物)・parameters = 級の parameters・settle =
   ticket を置いた・外した後に Longhorn が付け終わる・外し終わるまでの読みの数・stuck = 付け終わらない・races = 先に 409 で断る書きの数・
   silent = 係が答えない。走らせた後に読む物: pending = 付け外しの途中の読みの残り・leaving = 外す途中の volume の元の node・version =
   VolumeAttachment の版・created = 作成の本文・seen = 受けた要求・asked = 係が受けた頼み。"
  (defn #^ None __init__ [self #^ WorkspaceVolumeWorld held #^ dict [parameters None] #^ int [settle 0] #^ bool [stuck False]
                          #^ int [races 0] #^ bool [silent False]]
    ;; defk にできない: class の作り手(Python が呼ぶ口)
    "相手役の世界を作るため。"
    (setv self.held held
          self.parameters (if (is parameters None) PARAMETERS parameters)
          self.settle settle self.stuck stuck self.races races self.silent silent
          self.pending {} self.leaving {} self.version 1
          #^ (get tuple #(str ...)) self.created #()
          #^ (get tuple #(SeenRequest ...)) self.seen #()
          #^ (get tuple #(str ...)) self.asked #())))


(defk api-answer [status payload]
  {:pre [(: status int) (: payload dict)] :post [(: % HttpResponse)]}
  "相手役の答え(status と JSON の値)を HTTP の応答にするため。"
  (val text (json.dumps payload))
  (HttpResponse status {} (.encode text "utf-8") text "" 0.0))


(defk volume-answer [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % HttpResponse)]}
  "Volume の GET に、世界の様子から答えるため(付け外しの途中は attaching・detaching)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val waiting (or world.stuck (> (.get world.pending name 0) 0)))
  (val status (cond
                (and (in name world.leaving) waiting) {"state" "detaching" "currentNodeID" (get world.leaving name)}
                (is-not volume.node None) {"state" (if waiting "attaching" "attached") "currentNodeID" volume.node}
                True {"state" "detached" "currentNodeID" ""}))
  (! (api-answer 200 {"metadata" {"name" name} "status" status})))


(defk attachment-answer [world name]
  {:pre [(: world LonghornWorld) (: name str)] :post [(: % HttpResponse)]}
  "VolumeAttachment の GET に、世界の様子から答えるため(読むたびに付け外しの途中の残りを 1 つ減らす)。"
  (<- volume (held-volume world.held name))
  (when (is volume None)
    (return (! (api-answer 404 {"kind" "Status" "reason" "NotFound"}))))
  (val waiting (or world.stuck (> (.get world.pending name 0) 0)))
  (val id (! (ticket-id name)))
  (val tickets (if (is volume.node None) {} {id {"id" id "type" "longhorn-api" "nodeID" volume.node}}))
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
          (<- (put-volume world.held (HeldVolume :name name :node None :mounted-at volume.mounted-at :formatted volume.formatted))))
      (do (assert (= (get ticket "type") "longhorn-api") ticket)
          (<- (put-volume world.held (HeldVolume :name name :node (get ticket "nodeID") :mounted-at volume.mounted-at
                                                 :formatted volume.formatted)))))
  (setv (get world.pending name) world.settle world.version (+ world.version 1))
  (! (api-answer 200 {})))


(defk longhorn-answer [world request]
  {:pre [(: world LonghornWorld) (: request SeenRequest)] :post [(: % HttpResponse)]}
  "API server の要求 1 つに、身元 → 宛先の順で答えるため。"
  (val path request.path)
  (cond
    (!= request.authorization (+ "Bearer " TOKEN)) (! (api-answer 401 {"kind" "Status" "reason" "Unauthorized"}))
    (and (= request.method "GET") (= path SETTINGS-PATH)) (! (api-answer 200 {"data" {"workspace-volume.json" SETTINGS-TEXT}}))
    (and (= request.method "GET") (= path CLASS-PATH)) (! (api-answer 200 {"parameters" world.parameters}))
    (and (= request.method "POST") (= path VOLUMES-PATH))
    (do (val name (get (json.loads request.body) "metadata" "name"))
        (setv world.created (+ world.created #(request.body)))
        (<- volume (held-volume world.held name))
        (if (is-not volume None)
            (! (api-answer 409 {"kind" "Status" "reason" "AlreadyExists"}))
            (do (<- (put-volume world.held (HeldVolume :name name)))
                (! (api-answer 201 {"metadata" {"name" name}})))))
    (and (= request.method "GET") (.startswith path (+ VOLUMES-PATH "/")))
    (do (<- answer HttpResponse (volume-answer world (cut path (+ (len VOLUMES-PATH) 1) None))) answer)
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
      True (do (<- (put-volume world.held (HeldVolume :name name :node volume.node :mounted-at target :formatted True)))
               (+ "0\nworkspace-mount: " name " を " target " に付けた")))
    (= verb "umount")
    (cond
      (not there) (+ "0\nworkspace-mount: " target " には何も付いていない")
      (!= (. (get there 0) name) name) (+ "1\nworkspace-mount: " target " に付いているのは " name " でない")
      (in target world.held.busy) (+ "1\nworkspace-mount: " target " を外せなかった(使っている process が在るかもしれない)")
      True (do (<- (put-volume world.held (HeldVolume :name name :node volume.node :mounted-at None :formatted volume.formatted)))
               (+ "0\nworkspace-mount: " name " を " target " から外した")))
    True (+ "1\nworkspace-mount: 知らない命令 '" verb "'")))


(defhandler keeper-peer [#^ LonghornWorld world]
  ;; 引数に残す理由: 係の相手役は Longhorn の相手役と同じ世界(attach された volume だけを付けられる)を読み書きする。
  ;; 係の相手役: 頼みの dir の `<鍵>.req` へ名を替える書きを受けて頼みを読み、答えの file を置き、頼みを消す(係と同じ — silent の世界は
  ;; 頼みを置いたまま答えない)。答え手の process の mountinfo の読みにも答える。
  (ReadText [path]
    :when (= path SITE.mountinfo)
    (<- text str (mountinfo-of world))
    (resume text))
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


(defk run-steps [steps]
  {:pre [(: steps tuple)] :post [(: % tuple)]}
  "筋書き steps を順に effect で出し、結末の組(done・already・refused: <断りの文>)を得るため(断られても次の手へ進む)。"
  (var outcomes #())
  (for [step steps]
    (val effect (match step.verb
                  "create" (CreateWorkspaceVolume step.owner)
                  "attach" (AttachWorkspaceVolume step.owner)
                  "mount" (MountWorkspaceVolume step.owner step.path)
                  "umount" (UmountWorkspaceVolume step.owner step.path)
                  "detach" (DetachWorkspaceVolume step.owner)))
    (try
      (<- outcome WorkspaceOutcome effect)
      (:= outcomes (+ outcomes #(outcome.value)))
      (except [error WorkspaceVolumeRefused]
        (:= outcomes (+ outcomes #((+ "refused: " (str error))))))))
  outcomes)


(defk finished-run [steps]
  {:pre [(: steps tuple)] :post [(: % ApiRan)]}
  "筋書き steps を当て、結末の組と終わりの memory の file を得るため。"
  (<- outcomes tuple (run-steps steps))
  (<- left MemoryFiles (ReadMemoryFiles))
  (ApiRan :outcomes outcomes :left left))


(defk on-api [world steps [files WORKER-FILES]]
  {:pre [(: world LonghornWorld) (: steps tuple) (: files MemoryFiles)] :post [(: % ApiRan)]}
  "本番の答え手で、相手役の世界 world に筋書き steps を当てた結末の組と、終わりの memory の file を得るため。"
  (<- ran ApiRan (with_handlers [(state) (memory-file-handler files) (sim-time-handler :clock (SimClock)) (node-environ NODE)
                                 (longhorn-peer world) (keeper-peer world) (workspace-volumes-api SITE)]
                   (finished-run steps)))
  ran)


(defk on-sim [world steps]
  {:pre [(: world WorkspaceVolumeWorld) (: steps tuple)] :post [(: % tuple)]}
  "模擬の答え手で、世界 world に筋書き steps を当てた結末の組を得るため。"
  (<- outcomes tuple (with_handlers [(held-workspace-volumes world)] (run-steps steps)))
  outcomes)


(defk kinds [outcomes]
  {:pre [(: outcomes tuple)] :post [(: % tuple)]}
  "結末の組 outcomes から、断りの文を落とした種類の組(done・already・refused)を得るため。"
  (tuple (gfor outcome outcomes (if (.startswith outcome "refused: ") "refused" outcome))))


(defrecord BothRan
  "同じ筋書きを 2 つの答え手に当てた走り: real と held = 本番と模擬の結末の組・api = 本番の相手役・sim = 模擬の世界・left = 本番の終わりの
   memory の file。"
  (#^ tuple real)
  (#^ tuple held)
  (#^ LonghornWorld api)
  (#^ WorkspaceVolumeWorld sim)
  (#^ MemoryFiles left))


(defk both [make steps]
  {:pre [(: make Callable) (: steps tuple)] :post [(: % BothRan)]}
  "世界を作る関数 make で本番用と模擬用の世界を別に作り、筋書き steps を両方の答え手に当てるため。"
  (val api (LonghornWorld (make)))
  (val sim (make))
  (<- on-real ApiRan (on-api api steps))
  (<- on-held tuple (on-sim sim steps))
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
  (val writes (lfor seen api.seen :if (= seen.method "PATCH") (get (json.loads seen.body) "metadata" "resourceVersion")))
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
