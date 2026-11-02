;;; 持ち主(上に載る系が渡す id 1 つ — 例えば会話)ごとの作業場所の volume を、作る・機体の node に attach する・作業場所の path に mount
;;; する・外す effect の型(関数は持たない — 本番の答え手は doeff_cluster._shared.protocol.workspace_volumes の workspace-volumes-api・
;;; 模擬の答え手は doeff_cluster._sim.workspace_volumes の held-workspace-volumes)。決まり(級・名・寿命・mount の係)は
;;; deploy/k8s/workspace-volumes/README.md。
;;;
;;;   WorkspaceOutcome        effect の結末: DONE = 今した・ALREADY = 既にそうだった(何もしていない)
;;;   WorkspaceVolumeSite     本番の答え手が読む場所と待つ秒: Longhorn の namespace・値の ConfigMap・mount の係の頼みの dir・mountinfo の
;;;                           file・attach と detach と係の答えを待つ上限の秒・読み直す間の秒
;;;   WorkspaceVolumeRefused  できなかった(持ち主の id と volume の名・何をしようとしたか・理由を文に持つ — token は載せない)
;;;   CreateWorkspaceVolume   持ち主の volume が無ければ級の値で作る(在れば ALREADY — 中身に触らない)
;;;   AttachWorkspaceVolume   持ち主の volume を、答え手の機体の node に attach し、Longhorn が付け終わるまで待つ
;;;   MountWorkspaceVolume    attach した volume を path に mount する(path の dir は答え手が作る。空の volume には ext4 を作る)
;;;   UmountWorkspaceVolume   path に付いた持ち主の volume を外す(何も付いていなければ ALREADY。使っている process が在れば断る)
;;;   DetachWorkspaceVolume   持ち主の volume の attach を外し、外れるまで待つ(まだどこかに mount されていれば断る)
;;;
;;; 順: 作る → attach → mount → (使う)→ umount → detach。volume を消す effect は無い(冷やす・消すは寿命の決まりの別の仕事)。
;;; どの effect も何度出しても同じ様子に揃う(2 度目は ALREADY)。1 つの volume は同時に 1 つの node・1 つの path にだけ付く(RWO)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import typing [ClassVar])
(import doeff [EffectBase])
(import doeff_cluster._shared.intent.record_spec [RecordSpec RecordMode])


(defenum WorkspaceOutcome DONE ALREADY)


(defrecord WorkspaceVolumeSite
  "本番の答え手が読む場所と待つ秒(頭の註): longhorn-namespace = Longhorn の volume と VolumeAttachment の namespace・settings-namespace と
   settings-name = 値の ConfigMap(workspace-volume.json — 級の名と大きさ)・mount-dir = mount の係の頼みの dir(無い worker には係が
   無い)・mountinfo = 答え手の process から見た mount の表の file・attach-seconds と detach-seconds = Longhorn が付け終わる・外し終わる
   のを待つ上限・mount-seconds = 係の答えを待つ上限(空の volume の mkfs を含む)・poll-seconds = 読み直す間。"
  {:check [(> (len longhorn-namespace) 0) (> (len settings-namespace) 0) (> (len settings-name) 0)
           (.startswith mount-dir "/") (.startswith mountinfo "/")
           (> attach-seconds 0) (> detach-seconds 0) (> mount-seconds 0) (> poll-seconds 0)]}
  (#^ str longhorn-namespace)
  (#^ str settings-namespace)
  (#^ str settings-name)
  (#^ str mount-dir)
  (#^ str mountinfo)
  (#^ float attach-seconds)
  (#^ float detach-seconds)
  (#^ float mount-seconds)
  (#^ float poll-seconds))


(defclass WorkspaceVolumeRefused [RuntimeError]
  "作業場所の volume の操作ができなかった(持ち主の id と volume の名・何をしようとしたか・理由を文に持つ — token は載せない・既定の値で
   埋めない)。")


(defclass [(dataclass :frozen True)] CreateWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume が無ければ作る(頭の註)。"
  ;; 記録: 判断の外への報告(output — cluster の物を作る)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] AttachWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume を答え手の機体の node に attach する(頭の註)。"
  ;; 記録: 判断の外への報告(output — volume を node に付ける)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))


(defclass [(dataclass :frozen True)] MountWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume を path(作業の根の下の絶対 path)に mount する(頭の註)。"
  ;; 記録: 判断の外への報告(output — 機体の mount を足す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner)
  (#^ str path))


(defclass [(dataclass :frozen True)] UmountWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "path に付いた持ち主 owner の volume を外す(頭の註)。"
  ;; 記録: 判断の外への報告(output — 機体の mount を外す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner)
  (#^ str path))


(defclass [(dataclass :frozen True)] DetachWorkspaceVolume [(get EffectBase WorkspaceOutcome)]
  "持ち主 owner の volume の attach を外す(頭の註)。"
  ;; 記録: 判断の外への報告(output — volume を node から外す)。
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT))
  (#^ str owner))
