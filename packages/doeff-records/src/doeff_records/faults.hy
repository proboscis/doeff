;;; 検の口(公開 effect ではない)— 置き場の出来事を筋書きから起こす。handler の組はどれもこれに答える。
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] AdvanceStoreEpoch [EffectBase]
  "置き場が作り直された(版 epoch が 1 進み、それまでの変更の列を忘れる)ことを起こす。答え = 新しい epoch(int)。
   前の epoch の位置で WatchChanges / ListRows を頼むと Reset が返る。")
