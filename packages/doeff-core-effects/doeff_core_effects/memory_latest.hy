;;; 最新の値の memory の答え手 memory-latest-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— PublishLatest・ReadLatest に、
;;; 1 つの run の中の状態(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(process_latest.hy)と同じ契約で最新の値の受け渡しを確かめたいが、process に 1 つの置き場や thread に
;;; 触れたくない。違うのは置き場だけ(process に 1 つ → この run の中)。別の run とは共有しない。外側に state の handler が要る。
(require doeff-hy.macros [defhandler var val])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])


(defhandler memory-latest-handler
  "PublishLatest・ReadLatest に、この run の中の置き場(型 → 最新の値)で答える(頭の註)。"
  (session var board {})
  (PublishLatest [value]
    (:= board (| board {(type value) value}))
    (resume None))
  (ReadLatest [kind]
    (resume (.get board kind))))
