;;; worker の言い換えどうしの問い(handlers.hy の I/O を worker/protocol の言い換えに分けた時に足した効果 — #2464〜#2467)。業務の判断
;;; (worker/core)は出さない: 世界の観測のまとめ(worker/protocol/world の local-host)と状態の file(worker/protocol/status_file)が、
;;; 子 process・入口の検め・コードの木・実行環境の root の言い換えへ問う。intent の層から移した(#2031 — 言い換えの handler が intent を
;;; 出さない・DOEFF130)。
;;;
;;; 拍の間の眠りの起こし方(#3834): 眠りの答え手(worker/protocol/tick_pauses)が眠る前に ArmWake で起こし方を問い、起きた後に DisarmWake で
;;; 外す。答え手 wake-host(worker/protocol/wake)が、各言い換えに子の終わりの見張りと時間の期限(XxxWake → HostWake)を、coordinator への口に
;;; 次の heartbeat の刻(LinkDue)を、止めの印に止めの合図の起こし(StopWake)を問うてまとめる。子の終わりは汎用の WatchExits(本物 = pidfd)
;;; が眠りの bell を満たして知らせる — 終わりを時間で起きて問わない。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_core_effects.process_effects [ExitTarget])
(import doeff_core_effects.scheduler [ExternalPromise])


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


;; --- 拍の間の眠りの起こし方(#3834 — 頭の註)----------------------------------------------------------------------------

(defrecord EveryTick
  "拍ごとに打つ印(LinkDue・HostWake・WorkerWake の due の値): 次の刻を前もって言えない口が在る — heartbeat を拍ごとに送る間・知らせの
   無い印を待つ間・期限を知らない宿。眠りは今までどおり tick-seconds(と呼び鈴)。")


(defrecord HostWake
  "言い換え 1 つの起こし方: targets = 終わりを見張る子(ExitTarget の tuple — 終わると眠りが起きる)・due-ms = 子の終わりを待たずに
   観測し直すべき最初の刻(epoch ms — 準備の停滞の期限・検めの時間切れ・shim の期限など)か EveryTick(拍ごとに読み直す物が在る)。
   None = 時間で観測し直す物が無い。"
  (#^ (get tuple #(ExitTarget ...)) targets)
  (setv #^ (| int EveryTick None) due-ms None))


(defrecord WorkerWake
  "拍の間の眠りの起こし方のまとめ(ArmWake の答え): due = 次に拍を打つべき最初の刻(epoch ms — 言い換えの期限と次の heartbeat の
   早い方)か EveryTick。子の終わりと止めの合図は、眠りの bell が満たされて知らせる。"
  (#^ (| int EveryTick) due))


(defclass [(dataclass :frozen True)] ProcessesWake [EffectBase]
  "job の子 process の起こし方(HostWake — 走っている子と待ちの子から分けた子の終わり)。起こし方のまとめ(wake-host)が子 process の
   言い換え(worker/protocol/process_host)へ問う。")


(defclass [(dataclass :frozen True)] CodeWake [EffectBase]
  "版の木の準備の起こし方(HostWake — 準備の子の終わり)。起こし方のまとめがコードの木の言い換え(worker/protocol/code_store)へ問う。")


(defclass [(dataclass :frozen True)] EnvsWake [EffectBase]
  "root の準備の起こし方(HostWake — 準備と prune の子の終わり・準備の停滞の期限)。起こし方のまとめが root の言い換え
   (worker/protocol/env_store)へ問う。")


(defclass [(dataclass :frozen True)] ProbesWake [EffectBase]
  "入口の検めの起こし方(HostWake — 束の子の終わり・時間切れと shim の期限)。起こし方のまとめが検めの言い換え(worker/protocol/probes)へ
   問う。")


(defclass [(dataclass :frozen True)] WarmWake [EffectBase]
  "待ちの子の起こし方(HostWake — 待ちの子の終わり・準備完了の印を待つ間の拍ごとの読み)。起こし方のまとめが待ちの子の言い換え
   (worker/protocol/warm_host)へ問う。")


(defclass [(dataclass :frozen True)] LinkDue [EffectBase]
  "coordinator への口が次に heartbeat を送る刻(epoch ms)か EveryTick(拍ごとに送る — 待ちの口を使えていない・前の heartbeat が
   届いていない・状態の報告がまだ送られていない)。起こし方のまとめが coordinator への口(worker/protocol/coordinator_link)へ問う。")


(defclass [(dataclass :frozen True)] StopWake [EffectBase]
  "止めの合図(SIGTERM・SIGINT)で眠りの bell を満たすよう止めの印に掛ける(bell = None で外す)。答え = None。合図が既に来ていれば
   その場で満たす。起こし方のまとめが止めの印(worker/protocol/stop の stop-flag)へ問う。"
  (#^ (| ExternalPromise None) bell))


(defclass [(dataclass :frozen True)] ArmWake [EffectBase]
  "拍の間の眠りの起こし方を整える(答え = WorkerWake)。子の終わりの見張りと止めの合図を眠りの bell に掛ける。眠りの答え手
   (worker/protocol/tick_pauses)が眠る前に出し、起こし方のまとめ(worker/protocol/wake の wake-host)が答える。"
  (#^ ExternalPromise bell))


(defclass [(dataclass :frozen True)] DisarmWake [EffectBase]
  "ArmWake で掛けた見張りを外す(起きた後 — 外した後は bell を満たさない)。答え = None。"
  (#^ ExternalPromise bell))
