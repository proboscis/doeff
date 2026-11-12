;;; machine の用途の区画の宣言の型(ADR-DOE-CLUSTER-005 R1・R2)。machine 1 台の memory と CPU を用途ごとの区画に分け、区画ごとに
;;; cgroup の層 1 つで限る。宣言は coordinator の資源 MachineCompartments の行(machine ごとに 1 行 — 名 = 宣言する site が選ぶ machine
;;; の名)で、site の宣言の手順だけが書く。doeff は形だけを持ち、値は site が持つ。
;;;
;;; 1 つの事実は 1 か所: 区画の値はこの行だけが持ち、worker の行(WorkerInfo)・task の行・枠の file の行・WORKER_CGROUP_LAYOUT に欄を
;;; 足さない。worker が受け取る区画(HeldCompartment)は行からの写し(heartbeat の返事の compartments — 新しい事実ではない)。
;;; 形の検めと綴りは doeff_cluster._shared.core.compartment_rules(coordinator の資源の口・保存の読み直し・worker の返事の読みが同じ規則を読む)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass field])


;; 区画の用途(閉じた集合): tasks = coordinator が task を置く区画・services = 常駐の job を置く区画・worker = worker の本体と init の層・
;; prepare = 版の準備の層・hosted = 上に載る系が走らせる process のために worker が持つ層(1 つの層に 1 区画)・outside = OS・kubelet・
;; doeff の走らせない process と Pod(worker も層も名指さない)。
(val COMPARTMENT-USES (frozenset #("tasks" "services" "worker" "prepare" "hosted" "outside")))
;; いつも守る用途: memoryLimitMiB を memoryMiB より大きくでき、層に memory.min = memoryMiB も掛ける(守る区画)。
(val PROTECTED-USES (frozenset #("services" "worker")))
;; memoryLimitMiB を書ける用途: いつも守る用途と hosted。hosted は止めてはいけない仕事(会話の CLI・マージの列の門の命令 — MUST-NOT-WAIT)の
;; 層に限って、memoryLimitMiB > memoryMiB を書いた時だけ守る区画になる(memory.min = memoryMiB・上限は必ず memoryLimitMiB — R3 の改め
;; 2026-11-12)。書かない hosted は上限 memoryMiB だけ。
(val GUARDABLE-USES (frozenset #("services" "worker" "hosted")))
;; worker 1 つが高々 1 つ持つ用途(置き方の和の区画)。
(val SINGLE-USES (frozenset #("tasks" "services")))


(defrecord Compartment
  "区画 1 つ(資源 MachineCompartments の spec の compartments の 1 行): use = 用途(COMPARTMENT-USES)・worker = 区画を持つ doeff の worker の
   名・layer = その worker の cgroup の層の名(outside は worker も layer も空)・memory-mib = 区画を計画し守る量・memory-limit-mib = 使ってよい
   最大(GUARDABLE-USES の外は memory-mib と同じ・守る区画は memory-mib 以上・守る hosted は memory-mib より大きい)・cpu-millis = CPU の宣言(1000 = 1 core)。"
  {:tags {:context "doeff-cluster" :role "intent"}
   :check [(>= memory-mib 0) (>= memory-limit-mib memory-mib) (>= cpu-millis 0)]}
  (#^ str use)
  (#^ str worker)
  (#^ str layer)
  (#^ int memory-mib)
  (#^ int memory-limit-mib)
  (#^ int cpu-millis))


(defrecord MachineCompartments
  "machine 1 台の区画の宣言(資源 MachineCompartments の行 1 つ): machine = 行の名(in-cluster の machine は k8s の node の名・native の
   machine は host の名)・compartments = 区画の列(書いた順を保つ)。"
  {:tags {:context "doeff-cluster" :role "intent"}}
  (#^ str machine)
  (#^ (get tuple #(Compartment ...)) compartments))


(defrecord HeldCompartment
  "worker 1 つが持つ区画(heartbeat の返事の compartments の 1 行 — MachineCompartments の行からの写し・R2): use・layer・memory-mib・
   memory-limit-mib・cpu-millis は Compartment と同じ意味。worker は層 layer に memory.max = memory-limit-mib を、守る区画(PROTECTED-USES と、
   memory-limit-mib > memory-mib の hosted — compartment_rules.guarded?)は memory.min = memory-mib も書く(R3)。"
  {:tags {:context "doeff-cluster" :role "intent"}
   :check [(>= memory-mib 0) (>= memory-limit-mib memory-mib) (>= cpu-millis 0)]}
  (#^ str use)
  (#^ str layer)
  (#^ int memory-mib)
  (#^ int memory-limit-mib)
  (#^ int cpu-millis))
