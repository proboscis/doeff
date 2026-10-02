;;; k8s の Deployment を読む・台数を変える effect(Rollout の reconciler が旧 / 新の片方として本番の Deployment を扱う口)。
;;;
;;; 触るのは台数(scale の subresource)だけ。kubectl ではなく k8s の API を handler(coordinator/protocol/kube.hy・client は foundation/kube_client.hy)経由で叩く。
;;; 権限は coordinator の ServiceAccount に、対象の Deployment の get と scale だけを許す Role で与える(deploy/cluster.yaml)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "coordinator" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_hy.table [TableWrite])
(import doeff_cluster.coordinator.intent.cluster_model [DeploymentSeen DeploymentUnreadable NodeLabelsSeen NodeLabelsUnreadable])


(defclass KubeUnavailable [Exception]
  "k8s の API に届かない・権限が無い・資格が無い。Rollout はこの観測を Unknown として扱い、何もしない(次の拍で試し直す)。")


(defclass [(dataclass :frozen True)] ScaleDeployment [EffectBase]
  "Deployment の宣言の台数を replicas にする。dry-run = k8s の server 側の dryRun(権限と形を検めるが保存しない)。
   結果は書いた(dry-run なら書いたはずの)台数。失敗は KubeUnavailable。"
  (#^ str namespace)
  (#^ str name)
  (#^ int replicas)
  (setv #^ bool dry-run False))


(defclass [(dataclass :frozen True)] AnnotateDeployment [EffectBase]
  "Deployment に annotation を置く(値 None は外す)。scale の権限だけでは通らない(deployments の patch が要る)ので、
   Rollout の spec の markDeployment が真の時だけ出す。結果は None。"
  (#^ str namespace)
  (#^ str name)
  (#^ dict annotations))


(defclass [(dataclass :frozen True)] StartKubeReads [EffectBase]
  "Rollout の相手の Deployment(「ns/名」の tuple)と worker の置かれた node(名の tuple)の読みを、調停ループの外で始める(#2807 —
   読みが詰まっても調停ループが heartbeat に答え続けるため)。読みが既に走っていれば何もしない。started-ms = 始めた時刻(ループの now —
   読んだ観測の時刻にもなる)。結果は始めたか(bool)。読みの答えは CollectKubeReads で受け取る。
   Deployment の観測は cluster_model.DeploymentReading(宣言の台数・status の台数・generation・observedGeneration・annotations — k8s の JSON を
   型へ解くのは答え手 coordinator/protocol/kube の 1 点・#2728)。node の label は worker の置かれた node から能力を導くため(ADR-DOE-CLUSTER-001
   R4b・改訂 1 の I)— 権限は coordinator の ServiceAccount に nodes の get を与える ClusterRole(配備する側の manifest)。"
  (#^ (get tuple #(str ...)) deployments)
  (#^ (get tuple #(str ...)) nodes)
  (#^ int started-ms))


(defclass [(dataclass :frozen True)] CollectKubeReads [EffectBase]
  "始めた読みの答えを、待たずに受け取る(#2807)。結果は KubeReadsIdle(走っている読みが無い)・KubeReadsRunning(まだ)・
   KubeReadsDone(終わった — 観測の表への書き)のどれか。読みが now-ms - started-ms >= name-after-ms まで終わらなければ、その初回の
   KubeReadsRunning だけ overdue が真(調停ループが名指しの 1 行を出す — 1 つの読みにつき 1 度)。"
  (#^ int now-ms)
  (#^ int name-after-ms))


(defrecord KubeReadsIdle
  "CollectKubeReads の答え: 走っている読みが無い。")


(defrecord KubeReadsRunning
  "CollectKubeReads の答え: 読みがまだ終わっていない。started-ms = 始めた時刻・overdue = 名指す秒を超えた初回か。"
  (#^ int started-ms)
  (#^ bool overdue))


(defrecord KubeReadsDone
  "CollectKubeReads の答え: 読みが終わった。deployments = 「ns/名」→ DeploymentSeen / DeploymentUnreadable の書き(doeff_hy.table の
   TableWrite の tuple)・nodes = node の名 → NodeLabelsSeen / NodeLabelsUnreadable の書き。観測の時刻 at は読みを始めた時刻
   (古さを長めに数える — 15 秒より古い観測は Rollout の判断で Unknown)。"
  (#^ (get tuple #((get TableWrite (| DeploymentSeen DeploymentUnreadable)) ...)) deployments)
  (#^ (get tuple #((get TableWrite (| NodeLabelsSeen NodeLabelsUnreadable)) ...)) nodes))
