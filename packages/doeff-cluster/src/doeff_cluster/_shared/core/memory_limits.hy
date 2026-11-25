;;; 機体の memory の見張りの閾(MemoryLimits)と DANGER の線(danger-line-mib)の唯一の定義元。
;;; worker の見張り(worker/core/memory_rules)と coordinator の memory の床(coordinator/core/measured_room)、
;;; site の区画の宣言の test が同じ 1 か所から danger-available を読むため、共有の core に置く(数の写しを 2 か所に置かない)。
(require doeff-hy.macros [deff val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "judgment"})
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import math [ceil])

;; byte の単位(log の値は MiB)。
(val MIB (* 1024 1024))


(defrecord MemoryLimits
  "見張りの閾(空き / 総量 — kubelet の下の機体は追い出しまでの空き / 線の大きさ・それ以外は MemAvailable / MemTotal。見張りが
   worker/core/eviction_rules の line-memory で NodeMemory にして渡す): danger-available = 空き / 総量 がこれ未満なら DANGER・danger-pressured-available と danger-full-avg10 =
   空き / 総量 がこれ未満で、memory の PSI の full avg10(%)がこれ以上でも DANGER(reclaim で全 process が止まり始めた)・
   tight-available と tight-full-avg10 = どちらかを越えたら TIGHT・min-victim-bytes = これより小さい単位は止めない(食った物が job の外の
   時に、小さい job を次々に止めない)・steady-ratio = 常駐の job の今の memory が平常の大きさのこの倍以上の時だけ、その job の単位を
   止める候補にする(memory_rules の頭の註)。既定の値の根拠: node-a の 3 回は空き 1.5〜2.5 GiB / 28.3 GiB(5〜9%)・node-h は 7 GB / 125 GiB(5.6%)
   で止まり、ふだんの最低は node-a 6.2 GiB(22%)・node-h 52 GiB(41%)。swarm-share = 群れの止めを掛ける job の木の、機体の全量に
   占める割合の最低(既定 0.25 — node-h の 10-23 の CLI を並べる job の木は約 64 GB / 128 GB)。"
  {:check [(< 0 danger-available danger-pressured-available 1) (< 0 danger-available tight-available 1) (> danger-full-avg10 0)
           (> tight-full-avg10 0) (>= min-victim-bytes 0) (> steady-ratio 1) (< 0 swarm-share 1)]}
  (setv #^ float danger-available 0.10)
  (setv #^ float danger-pressured-available 0.20)
  (setv #^ float danger-full-avg10 25.0)
  (setv #^ float tight-available 0.20)
  (setv #^ float tight-full-avg10 10.0)
  (setv #^ int min-victim-bytes (* 512 MIB))
  (setv #^ float steady-ratio 1.5)
  (setv #^ float swarm-share 0.25))


(deff danger-line-mib [#^ int total-mib #^ (| MemoryLimits None) [limits None]]  ; cannot be a defk: coordinator の床(measured_room)と site の区画の宣言の test(Program の外)が同じ 1 か所から読む
  {:pre [(: total-mib int) (>= total-mib 0) (: limits (| MemoryLimits None))] :post [(: % int) (>= % 0)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "見張りの DANGER の線(MiB — 切り上げ): 線の大きさ total-mib × danger-available(limits が None の時は既定の MemoryLimits)。
   空きがこの値以上なら空き / total-mib ≥ danger-available で DANGER に入らず、この値未満なら DANGER に入る。
   danger-available の持ち主はこの module の MemoryLimits の 1 か所だけで、worker・coordinator・site の宣言の test はここから読む。"
  (setv active (if (is limits None) (MemoryLimits) limits))
  (ceil (* total-mib active.danger-available)))


(deff compartments-ceiling-mib [#^ int total-mib #^ (| MemoryLimits None) [limits None]]  ; cannot be a defk: site の区画の宣言の test と sim の区画の宣言(Program の外)が同じ 1 か所から読む
  {:pre [(: total-mib int) (>= total-mib 0) (: limits (| MemoryLimits None))] :post [(: % int) (>= % 0)]
   :tags {:context "doeff-cluster" :role "judgment"}}
  "区画の和(outside を除く)の上限(MiB): 線の大きさ total-mib − 見張りの DANGER の線(danger-line-mib)。
   和がこの値以下なら空きが total-mib × danger-available 以上に残り、見張りの DANGER に入らない。"
  (- total-mib (danger-line-mib total-mib limits)))
