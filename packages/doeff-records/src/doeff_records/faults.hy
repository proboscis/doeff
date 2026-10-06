;;; 検の口(公開 effect ではない)— 置き場の出来事を筋書きから起こす。AdvanceStoreEpoch は handler の組のどれもが答える。
;;; SetStoreOutage と AddStoreFault / ClearStoreFaults は memory の置き場だけが答える(PostgreSQL の置き場は本物の不達が起きる)。
(require doeff-hy.macros [defeffect])
(import collections.abc [Callable])
(import dataclasses [dataclass])
(import enum [Enum])
(import doeff [EffectBase])
(import doeff_records.values [Refused Unreachable])


(defclass [(dataclass :frozen True)] AdvanceStoreEpoch [EffectBase]
  "置き場が作り直された(版 epoch が 1 進み、それまでの変更の列を忘れる)ことを起こす。答え = 新しい epoch(int)。
   前の epoch の位置で WatchChanges / ListRows を頼むと Reset が返る。")


(defclass [(dataclass :frozen True)] SetStoreOutage [EffectBase]
  "置き場に届かない状態を起こす・戻すため(記録の service の不達・一部の表の断りの筋書き — 検と模擬の土台の故障の口)。
   detail = 届かない理由の文(None = 戻す)/ names = 届かない表と追記の列の名(None = 全部)。届かない間、名に当たる公開 effect
   (ReadRow・ListRows・PutRow・PutRows・WatchChanges・AppendEvent・ReadEvents・ReadStreamEnd・ReadEventByKey)と列の待ち WatchEvents は Unreachable(detail) を答え、置き場を変えない —
   本番の記録の口(http_client)が service に届かない時に返す答えと同じ。答え = None。memory の置き場が答える(PostgreSQL の置き場は
   本物の不達が起きるので答えない)。"
  (#^ (| str None) detail)
  (setv #^ (| frozenset None) names None))


;; --- 断りの故障 ----------------------------------------------------------------------------------
;; 置き場全体の不達(SetStoreOutage)より細かい故障: 名(表・列)と操作(読み / 書き)と絞り(effect の中身)で当たる effect だけに、
;; 決めた答え(Refused | Unreachable)を返させる。memory の置き場が各節の頭で当たる故障を探して答える(PostgreSQL の置き場は答えない)。
;; 使い手は業務の effect に答える偽の handler を書かず、この口で正典の置き場に断らせる。

(defclass StoreOperation [Enum]
  "故障が当たる操作の閉じた列挙: READ = ReadRow・ListRows・WatchChanges・WatchEvents・ReadEvents・ReadStreamEnd・ReadEventByKey / WRITE = PutRow・PutRows・AppendEvent。"
  (setv READ "read"
        WRITE "write"))


(defclass [(dataclass :frozen True)] StoreFault []
  "置き場の故障 1 つ。names = 当たる表と追記の列の名(effect が触る名のどれかが入っていれば当たる — PutRows は束の中の表のどれか 1 つで
   束ごと当たる)/ operation = 当たる操作 / answer = 当たった effect の答え(Refused | Unreachable)/ lands = 書きを置き場に着けてから
   答えを answer に差し替えるか(False = 置き場を変えずに answer を返す。読みは置き場を変えないのでどちらでも同じ)/ matching = effect を
   受けて当たるかを返す関数(None = 名と操作に当たる effect の全部)。"
  (#^ frozenset names)
  (#^ StoreOperation operation)
  (#^ (| Refused Unreachable) answer)
  (setv #^ bool lands False)
  (setv #^ (| Callable None) matching None)
  (defn #^ None __post-init__ [self]
    ;; 誤った値を黙って受けない(名の綴りを 1 つの str で渡すと文字の集合になる等)。
    (when (not (and (isinstance self.names frozenset) (all (gfor name self.names (isinstance name str)))))
      (raise (TypeError (.format "StoreFault.names は名の frozenset: {!r}" self.names))))
    (when (not (isinstance self.operation StoreOperation))
      (raise (TypeError (.format "StoreFault.operation は StoreOperation: {!r}" self.operation))))
    (when (not (isinstance self.answer #(Refused Unreachable)))
      (raise (TypeError (.format "StoreFault.answer は Refused か Unreachable: {!r}" self.answer))))
    (when (not (isinstance self.lands bool))
      (raise (TypeError (.format "StoreFault.lands は bool: {!r}" self.lands))))
    (when (not (or (is self.matching None) (callable self.matching)))
      (raise (TypeError (.format "StoreFault.matching は関数か None: {!r}" self.matching))))
    None))


(defeffect AddStoreFault
  "故障を 1 つ置く(置いた順に探し、当たった最初の 1 つが答える)。置き場全体の不達(SetStoreOutage)が先に答える。"
  {:fields [(: fault StoreFault)]
   :pre [(: fault StoreFault)]
   :answer None
   :tags {:context "records" :role "foundation"}})


(defeffect ClearStoreFaults
  "故障を外す: names = 外す故障の名(故障の names と 1 つでも重なる故障を外す)/ None = 全部。"
  {:fields [(: names (| frozenset None) None)]
   :pre [(: names (| frozenset None))]
   :answer None
   :tags {:context "records" :role "foundation"}})
