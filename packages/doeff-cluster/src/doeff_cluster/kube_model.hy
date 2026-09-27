;;; k8s の Deployment を読む・台数を変える effect(Rollout の reconciler が旧 / 新の片方として本番の Deployment を扱う口)。
;;;
;;; 触るのは台数(scale の subresource)だけ。kubectl ではなく k8s の API を handler(kube_handlers.hy)経由で叩く。
;;; 権限は coordinator の ServiceAccount に、対象の Deployment の get と scale だけを許す Role で与える(deploy/cluster.yaml)。
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass KubeUnavailable [Exception]
  "k8s の API に届かない・権限が無い・資格が無い。Rollout はこの観測を Unknown として扱い、何もしない(次の拍で試し直す)。")


(defclass [(dataclass :frozen True)] ReadDeployment [EffectBase]
  "結果は dict: specReplicas(宣言の台数)・replicas / readyReplicas / availableReplicas / updatedReplicas(status)・
   generation・observedGeneration・annotations。読めなければ KubeUnavailable。"
  (#^ str namespace)
  (#^ str name))


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


(defclass [(dataclass :frozen True)] ReadNodeLabels [EffectBase]
  "k8s の Node の label を読む(worker の置かれた node から能力を導くため — ADR-DOE-CLUSTER-001 R4b・改訂 1 の I)。
   結果は dict(label の鍵 → 値)。読めなければ KubeUnavailable。権限は coordinator の ServiceAccount に nodes の get を与える
   ClusterRole(配備する側の manifest)。"
  (#^ str node))
