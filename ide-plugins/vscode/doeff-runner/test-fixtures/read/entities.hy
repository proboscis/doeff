;;; 実体の種類ごとの欄(agora-redesign #910 U4)のテストの見本 — defeffect・defhandler・defenum・型でない契約を持つ defk。
(require doeff-hy.macros [defk defhandler <-])
(require doeff-hy.record [defrecord defenum])
(import doeff-hy.effects [defeffect])


(defrecord Slot
  "置き場 1 つ。"
  (#^ str key)
  (#^ int size))


(defenum Tone LOUD QUIET)


(defeffect ReadSlot
  "置き場を鍵で 1 つ読む(答え = 頭の註)。"
  {:fields [(: key str)]
   :answer (| Slot None)
   :tags {:context "store" :role "intent"}})


(defhandler slot-store {:effects [ReadRow]
                        :tags {:context "store" :role "protocol"}}
  ;; 置き場の読みを行の読みで答える。
  (ReadSlot [key]
    (<- row (| Slot None) (ReadRow key))
    (resume row)))


(defk slot-size [key limit]
  {:pre [(: key str) (: limit int) (> limit 0)] :post [(: % int)]
   :effects [ReadSlot] :tags {:context "store" :role "program"}}
  "置き場の大きさを上限つきで返すため。"
  (<- slot (| Slot None) (ReadSlot key))
  (if (is slot None) 0 (min slot.size limit)))
