;;; 検の口(公開 effect ではない)— 置き場の出来事を筋書きから起こす。handler の組はどれもこれに答える。
(import dataclasses [dataclass])
(import doeff [EffectBase])


(defclass [(dataclass :frozen True)] AdvanceStoreEpoch [EffectBase]
  "置き場が作り直された(版 epoch が 1 進み、それまでの変更の列を忘れる)ことを起こす。答え = 新しい epoch(int)。
   前の epoch の位置で WatchChanges / ListRows を頼むと Reset が返る。")


(defclass [(dataclass :frozen True)] SetStoreOutage [EffectBase]
  "置き場に届かない状態を起こす・戻すため(記録の service の不達・一部の表の断りの筋書き — 検と模擬の土台の故障の口)。
   detail = 届かない理由の文(None = 戻す)/ names = 届かない表と追記の列の名(None = 全部)。届かない間、名に当たる公開 effect
   (ReadRow・ListRows・PutRow・PutRows・WatchChanges・AppendEvent・ReadEvents)は Unreachable(detail) を答え、置き場を変えない —
   本番の記録の口(http_client)が service に届かない時に返す答えと同じ。答え = None。memory の置き場が答える(PostgreSQL の置き場は
   本物の不達が起きるので答えない)。"
  (#^ (| str None) detail)
  (setv #^ (| frozenset None) names None))
