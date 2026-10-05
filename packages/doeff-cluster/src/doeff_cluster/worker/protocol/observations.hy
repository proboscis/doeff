;;; worker の言い換えどうしの問い(handlers.hy の I/O を worker/protocol の言い換えに分けた時に足した効果 — #2464〜#2467)。業務の判断
;;; (worker/core)は出さない: 世界の観測のまとめ(worker/protocol/world の local-host)と状態の file(worker/protocol/status_file)が、
;;; 子 process・入口の検め・コードの木・実行環境の root の言い換えへ問う。intent の層から移した(#2031 — 言い換えの handler が intent を
;;; 出さない・DOEFF130)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] ObserveProcesses [EffectBase]
  "worker が起こした job の子 process の観測(ProcessView の tuple — 終わりを観測した子は exit-code を持つ)。ObserveWorld の答え手
   (local-host)が、子 process の言い換え(worker/protocol/process_host)へ問う(#2464)。")


(defclass [(dataclass :frozen True)] ObserveCode [EffectBase]
  "版ごとのコードの木の観測(CodeView の tuple — 準備中・失敗・完成品)。世界の観測のまとめ(local-host)が、コードの木の言い換え
   (worker/protocol/code_store)へ問う。問われた拍に、終わった準備を片づける(#2466)。")


(defclass [(dataclass :frozen True)] CodeTimings [EffectBase]
  "版ごとの木の準備にかかった秒(版 → 秒の写像)。状態の file の答え手(worker/protocol/status_file)が、コードの木の言い換えへ問う(#2466)。")


(defclass [(dataclass :frozen True)] ObserveEnvs [EffectBase]
  "実行環境の root の観測(CodeView の tuple — 準備中・失敗・完成品)。世界の観測のまとめ(local-host)が、root の言い換え
   (worker/protocol/env_store)へ問う。問われた拍に、終わった準備を片づけ、待っている準備を起こす(#2467)。")


(defclass [(dataclass :frozen True)] ObserveEnvDisk [EffectBase]
  "実行環境の root の置き場の disk の観測(EnvDisk)。世界の観測のまとめ(local-host)が、root の言い換えへ問う(#2467)。")


(defclass [(dataclass :frozen True)] ObserveProbes [EffectBase]
  "入口の検めの観測(ProbeView の tuple — 待ち・走っている・答えの出た検め)。世界の観測のまとめ(local-host)が、検めの言い換え
   (worker/protocol/probes)へ問う。問われた拍に、待っている束を起こし・終わった束と時間切れの束を片づける(#2465)。")


(defclass [(dataclass :frozen True)] ObserveWarmChildren [EffectBase]
  "root ごとの待ちの子の観測(WarmChildView の tuple — 起こし中・準備済み・終わった・止め中)。世界の観測のまとめ(local-host)が、
   待ちの子の言い換え(worker/protocol/warm_host)へ問う(#3646)。")
