;;; 最新の値の本物の答え手 process-latest-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— PublishLatest・ReadLatest に、process に
;;; 1 つの置き場で答える。
;;;
;;; 何のためか: 動き続ける process では、処理ループの run が断面(破れの記録・同期の進み・標本の ring …)を置き、別の run(probe の HTTP)が
;;; 書き手を待たずに読む。session の値は 1 つの run の中にしか無いので、この module が「名前 → 置き場(型 → 最新の値)」を process に 1 つ
;;; 持ち、同じ名前で入れた答え手どうし(別の run・別の thread)が同じ置き場を読み書きする。class は作らない(ADR-DOE-HY-007 R3・R4)。
;;;
;;; 書きは置き場の dict の 1 つの鍵の差し替え 1 回、読みは 1 つの鍵の参照 1 回で、どちらも lock を取らない(dict の 1 回の読み書きは
;;; free-threaded の Python でも割れない)。置き場を作る時だけ lock を取る。置き場は process の終わりまで残る。
;;; thread に触るのはこの module だけ(memory の答え手 memory_latest.hy は触らない)。
(require doeff-hy.macros [defhandler defk val])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import threading)
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])


;; process に 1 つ: 名前 → 置き場(型 → 最新の値)と、置き場を作る時の lock。
(val BOARDS {})
(val BOARDS-LOCK (threading.Lock))


(defk latest-board [name]
  {:pre [(: name str)] :post [(: % dict)] :tags {:context "latest" :role "foundation"}}
  "答え手を入れる時に、name の置き場(型 → 最新の値)を引くため(無ければ作る — 別の run と同じ置き場を共有する)。"
  (with [BOARDS-LOCK]
    (when (not-in name BOARDS)
      (setv (get BOARDS name) {}))
    (get BOARDS name)))


(defhandler process-latest-handler [#^ str name]
  "PublishLatest・ReadLatest に、process に 1 つの置き場 name で答える(頭の註)。"
  ;; 引数に残す理由: name は別の run と同じ置き場を共有する鍵 — 入れる所ごとに決まる値で、Ask では区別できない。
  (session val board (! (latest-board name)))
  (PublishLatest [value]
    (setv (get board (type value)) value)
    (resume None))
  (ReadLatest [kind]
    (resume (.get board kind))))
