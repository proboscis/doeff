;;; 置き場の手入れ — 保持の期限を過ぎた行と出来事の回収、古い変更の列の刈り取り(記録の service の中で回す Program)。
;;;
;;; どちらも公開 effect ではない(業務の Program は撃たない)。記録の service の composition root が手入れの係を 1 つ立て、
;;; maintenance-loop を回す。handler の組(memory・PostgreSQL)はどれもこの 2 つに答える。
;;;
;;;   SweepExpired   期限を過ぎた行を消して変更の列に RowRemoved を積み、期限を過ぎた出来事を捨てる。読み書きの前にも同じ回収が
;;;                  走るが、誰も触らない置き場でも行が残り続けないように係が撃つ
;;;   PruneChanges   keep-seconds より古い変更を変更の列から消し、floor(これより前の位置は Reset)を上げる。変更の列が際限なく
;;;                  伸びないようにする。floor より前の位置で WatchChanges を頼んだ読み手は Reset を受けて一覧から読み直す
;;;                  (keep-seconds は読み手の遅れの許容 — これより遅れた読み手だけが読み直す)
(require doeff-hy.macros [defk <-])
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import doeff_time [Delay])
(import doeff_records.values [Unreachable])


(defclass [(dataclass :frozen True)] SweepExpired [EffectBase]
  "保持の期限を過ぎた行と出来事を回収する。答え = Swept | Unreachable。")


(defclass [(dataclass :frozen True)] PruneChanges [EffectBase]
  "keep-seconds 秒より古い変更を変更の列から消す。答え = Pruned | Unreachable。"
  (#^ float keep-seconds)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.keep-seconds bool) (not (isinstance self.keep-seconds #(int float))) (< self.keep-seconds 0))
      (raise (ValueError (.format "PruneChanges.keep_seconds は 0 以上の秒: {!r}" self.keep-seconds))))))


(defclass [(dataclass :frozen True)] Swept []
  "回収の結果: rows = 消した行の数(変更の列に RowRemoved を積んだ数)。"
  (#^ int rows))


(defclass [(dataclass :frozen True)] Pruned []
  "刈り取りの結果: floor = 刈った後の floor(この位置以上の WatchChanges は続けられる)/ removed = 消した変更の数。"
  (#^ int floor)
  (#^ int removed))


(defclass [(dataclass :frozen True)] MaintenanceReport []
  "手入れ 1 回の結果(係の記録と計器のため)。置き場に届かなかった手入れは Unreachable(次の回で撃ち直す)。"
  (#^ (| Swept Unreachable) swept)
  (#^ (| Pruned Unreachable) pruned))


(defk maintenance-tick [keep-seconds]
  {:pre [(: keep-seconds (| int float))] :post [(: % MaintenanceReport)]}
  "手入れを 1 回行う: 期限切れを回収してから、古い変更を刈る(回収が積んだ RowRemoved は新しいので刈られない)。"
  (<- swept (SweepExpired))
  (<- pruned (PruneChanges keep-seconds))
  (MaintenanceReport swept pruned))


(defk maintenance-loop [interval-seconds keep-seconds ticks]
  {:pre [(: interval-seconds (| int float)) (: keep-seconds (| int float)) (: ticks (| int None))]
   :post [(: % list)]}
  "記録の service の手入れの係の本体: interval-seconds ごとに maintenance-tick を撃つ。ticks = 回数(None = 止めるまで)。
   答え = 手入れの結果の列(ticks が None の時は返らない)。"
  (setv reports [] count 0)
  (while (or (is ticks None) (< count ticks))
    (<- report (maintenance-tick keep-seconds))
    ;; 止めるまで回す係は結果を溜めない(溜めると際限なく伸びる)。
    (when (is-not ticks None) (.append reports report))
    (+= count 1)
    (when (or (is ticks None) (< count ticks))
      (<- (Delay interval-seconds))))
  reports)
