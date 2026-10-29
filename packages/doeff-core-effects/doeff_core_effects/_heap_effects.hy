;;; heap の凍結の effect — 起動の読み込みを終えた拍に 1 度、いま生きている object を以後の GC の走査から外す(agora-redesign #1440・
;;; ADR-DOE-CORE-EFFECTS-004)。業務の語を持たない土台の語彙。
;;;
;;;   CollectAndFreeze  回収してから凍らせる。答え = 凍らせた object の数(int)。
;;;
;;; 何のためか: 起動の後ほとんど動かない大きな cache(数万行の object)を持つ process では、世代 2 の回収が allocation の数で起き、そのたびに
;;; 全部を走査して処理ループの 1 回を伸ばす(agora の画面の実測: 回収なし 5 ms / あり 32 ms)。program は GC を知らず、境目で凍結を求めるだけ。
;;;
;;; 計器(meter_effects.hy)と module を分けた理由: 計器は観測を積んで読むだけで process の振る舞いを変えない。凍結は process の GC の状態を
;;; 変える操作で、計器を持たない program も使う。一緒に置くと、計器を差し替える模擬が GC の操作まで一緒に差し替える形になる。
;;;
;;; 答え手: gc-freeze-handler(gc_freeze.hy — 本物)と scripted-freeze-handler(scripted_freeze.hy — GC に触れず決めた数を答える)。
(require doeff-hy.macros [defeffect])


(defeffect CollectAndFreeze
  "回収してから凍らせる(頭の註)。答え = 凍らせた object の数。"
  {:fields []
   :answer int
   :tags {:context "heap" :role "foundation"}})
