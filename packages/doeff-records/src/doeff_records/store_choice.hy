;;; 記録の service の置き場の選び — 表の用意の作り手(prepare-of)と /readyz の問い(readiness)の組(agora-redesign #1608)。
;;; 本体の設定 records-serving(main.hy)がこの値を引数で受け、RecordsServing の prepare と readiness を作る。置き場ごとの値は置き場の module が
;;; 持つ: PostgreSQL = main.hy の PG-STORE・memory = memory.hy の memory-store-choice。以前は records-serving が PostgreSQL に固定していて、
;;; 使い手が dataclasses.replace で上書きしていた(#1604 の過渡の差し替え口)。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import collections.abc [Callable])


(defrecord StoreChoice
  "記録の置き場の選び: prepare-of = (schema prefix host) → 表を用意して「書き手の名 → 記録の handler」の関数を返す Program を作る関数
   (入口の用意の task が 1 度だけ撃つ)・readiness = () → 置き場に届けば True の Program(/readyz が撃つ・None = 用意が済めば ready)。"
  (#^ Callable prepare-of)
  (setv #^ (| Callable None) readiness None))
