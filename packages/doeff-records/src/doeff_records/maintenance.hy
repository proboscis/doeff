;;; 置き場の手入れ — 保持の期限を過ぎた行と出来事の回収、古い変更の列の刈り取り(記録の service の中で回す Program)。
;;;
;;; どちらも公開 effect ではない(業務の Program は撃たない)。記録の service の composition root が手入れの係を 1 つ立て、
;;; maintenance-loop を回す。handler の組(memory・PostgreSQL)はどれもこの 2 つに答える。
;;;
;;;   SweepExpired   期限を過ぎた行を消して変更の列に RowRemoved を積み、期限を過ぎた出来事を捨てる。回収はこの effect の時だけ
;;;                  走る — 読みは回収せず期限を自分で見て(#3561)、書き(PutRow・PutRows・AppendEvent)は自分が触る行と出来事だけを
;;;                  片付ける(#3605 の D)。触られない期限を過ぎた行の RowRemoved は、係が撃つこの回収が積む
;;;   PruneChanges   keep-seconds より古い変更を変更の列から消し、floor(これより前の位置は Reset)を上げる。変更の列が際限なく
;;;                  伸びないようにする。floor より前の位置で WatchChanges を頼んだ読み手は Reset を受けて一覧から読み直す
;;;                  (keep-seconds は読み手の遅れの許容 — これより遅れた読み手だけが読み直す)。upto-seq を持てば、消すのはその番号まで
;;;                  (送りの答え — 下)
;;;
;;; 消す前に送る(agora-controllers の card ki-c32e43651f2e・親 ki-e26367fd8fcb — 2026-10-11 11:21 に記録の Postgres の disk が 100% に
;;; なった。古い変更を消さずに階層つきの保管へ送ってから外す): 手入れの計画が送りの関数 archive(keep-seconds → Program)を持てば、
;;; 刈りの前にそれを走らせ、答え ArchivedThrough の番号までだけを刈る(送りが Unreachable の回は刈らない)。送りは書きの錠の外で走る
;;; (送り先の I/O で書き手を止めない)。送り先とその効果は使い手の composition が決める(この package は送り先を知らない)。
(require doeff-hy.macros [defk <- val var])
(val MODULE-TAGS {:context "records" :role "program"})
(import dataclasses [dataclass])
(import collections.abc [Callable])
(import doeff [EffectBase])
(import doeff_time [Delay])
(import doeff_records.values [Unreachable])


(defclass [(dataclass :frozen True)] SweepExpired [EffectBase]
  "保持の期限を過ぎた行と出来事を回収する。答え = Swept | Unreachable。")


(defclass [(dataclass :frozen True)] PruneChanges [EffectBase]
  "keep-seconds 秒より古い変更を変更の列から消す。upto-seq = 消してよい番号の上限(送って確かめた番号 — None = 上限なし・送りを持たない
   計画)。答え = Pruned | Unreachable。"
  (#^ float keep-seconds)
  (setv #^ (| int None) upto-seq None)
  (defn #^ None __post_init__ [self]
    (when (or (isinstance self.keep-seconds bool) (not (isinstance self.keep-seconds #(int float))) (< self.keep-seconds 0))
      (raise (ValueError (.format "PruneChanges.keep_seconds は 0 以上の秒: {!r}" self.keep-seconds))))
    (when (and (is-not self.upto-seq None) (or (isinstance self.upto-seq bool) (not (isinstance self.upto-seq int)) (< self.upto-seq 0)))
      (raise (ValueError (.format "PruneChanges.upto_seq は 0 以上の番号か None: {!r}" self.upto-seq))))))


(defclass [(dataclass :frozen True)] ArchivedThrough []
  "送りの答え: seq = 送り先に在ると確かめた変更の番号の上限(この番号までは刈ってよい — 0 = 1 つも確かめていない)。"
  (#^ int seq))


(defclass [(dataclass :frozen True)] Swept []
  "回収の結果: rows = 消した行の数(変更の列に RowRemoved を積んだ数)。"
  (#^ int rows))


(defclass [(dataclass :frozen True)] Pruned []
  "刈り取りの結果: floor = 刈った後の floor(この位置以上の WatchChanges は続けられる)/ removed = 消した変更の数。"
  (#^ int floor)
  (#^ int removed))


(defclass [(dataclass :frozen True)] MaintenanceReport []
  "手入れ 1 回の結果(係の記録と計器のため)。置き場に届かなかった手入れは Unreachable(次の回で撃ち直す)。archived = 送りの答え
   (送りを持たない計画は None・送り先に届かなかった回は Unreachable で、その回は刈らない — pruned も同じ Unreachable)。"
  (#^ (| Swept Unreachable) swept)
  (#^ (| Pruned Unreachable) pruned)
  (setv #^ (| ArchivedThrough Unreachable None) archived None))


(defk prune-after-archive [keep-seconds archive]
  {:pre [(: keep-seconds (| int float)) (: archive (| Callable None))] :post [(: % MaintenanceReport)]}
  "古い変更を、送りの関数 archive が在れば送って確かめた番号までだけ刈るため(頭の註の「消す前に送る」)。答えの swept は呼び手が埋める。"
  (when (is archive None)
    (<- pruned (PruneChanges keep-seconds))
    (return (MaintenanceReport (Swept 0) pruned)))
  (<- archived (archive keep-seconds))
  (match archived
    (ArchivedThrough :seq seq) (do (<- bounded (PruneChanges keep-seconds :upto-seq seq))
                                   (MaintenanceReport (Swept 0) bounded archived))
    (Unreachable) (MaintenanceReport (Swept 0) archived archived)
    _ (raise (TypeError (.format "手入れの送りの答えが ArchivedThrough でも Unreachable でもない: {!r}" archived)))))


(defk maintenance-tick [keep-seconds archive]
  {:pre [(: keep-seconds (| int float)) (: archive (| Callable None))] :post [(: % MaintenanceReport)]}
  "手入れを 1 回行う: 期限切れを回収してから、古い変更を(送りが在れば送って確かめた所まで)刈る(回収が積んだ RowRemoved は新しいので
   刈られない)。"
  (<- swept (SweepExpired))
  (<- report (prune-after-archive keep-seconds archive))
  (MaintenanceReport swept report.pruned report.archived))


(defk maintenance-loop [interval-seconds keep-seconds archive ticks]
  {:pre [(: interval-seconds (| int float)) (: keep-seconds (| int float)) (: archive (| Callable None)) (: ticks (| int None))]
   :post [(: % list)]}
  "記録の service の手入れの係の本体: interval-seconds ごとに maintenance-tick を撃つ。archive = 送りの関数(keep-seconds → 答えが
   ArchivedThrough | Unreachable の Program・None = 送らない)。ticks = 回数(None = 止めるまで)。答え = 手入れの結果の列(ticks が
   None の時は返らない)。"
  (setv reports [] count 0)
  (while (or (is ticks None) (< count ticks))
    (<- report (maintenance-tick keep-seconds archive))
    ;; 止めるまで回す係は結果を溜めない(溜めると際限なく伸びる)。
    (when (is-not ticks None) (.append reports report))
    (+= count 1)
    (when (or (is ticks None) (< count ticks))
      (<- (Delay interval-seconds))))
  reports)
