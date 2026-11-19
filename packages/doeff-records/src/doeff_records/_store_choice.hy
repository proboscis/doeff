;;; 記録の service の置き場の選び — 表の用意の作り手(prepare-of)と /readyz の問い(readiness)の組。
;;; 本体の設定 records-serving(_main.hy)がこの値を引数で受け、RecordsServing の prepare と readiness を作る。置き場ごとの値は置き場の module が
;;; 持つ: PostgreSQL = _main.hy の PG-STORE・memory = _memory.hy の memory-store-choice。以前は records-serving が PostgreSQL に固定していて、
;;; 使い手が dataclasses.replace で上書きしていた(#1604 の過渡の差し替え口)。
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す
(import collections.abc [Callable])
(import doeff [Program])


(defrecord StorePressure
  "置き場の詰まりの読み(/readyz の答えに載せる): lock-waiters = 置き場の錠を待っている接続の本数・
   idle-in-transaction-max-seconds = transaction を開いたまま止まっている接続のうち最も長い秒(無ければ 0.0)。"
  (#^ int lock-waiters)
  (#^ float idle-in-transaction-max-seconds))


(defrecord PressureUnread
  "置き場には届いたが、詰まりの読みが答えなかった(reason = 理由の 1 行)。読めなかった数を 0 と名乗らない。"
  (#^ str reason))


(defrecord StoreChoice
  "記録の置き場の選び: prepare-of = (schema prefix host numbering) → 表を用意して「書き手の名 → 記録の handler」の関数を返す Program を作る
   関数(入口の用意の task が 1 度だけ撃つ。numbering = 設定の番号の形 — RecordsSettings の註・空 = 置き場のまま)・readiness = () → 置き場に届けば True の Program(/readyz が撃つ・None = 用意が済めば ready)・
   pressure = () → StorePressure | PressureUnread の Program(/readyz が届いた後に撃つ・None = 詰まりの無い置き場 — memory は 0 と答える)・
   permit-database = GET /metrics が接続の許可の負荷を読む SQL の database の名(None = 接続の許可を持たない置き場 — memory)。"
  (#^ (get Callable #(... (get Program #(object object)))) prepare-of)
  (setv #^ (| (get Callable #(... (get Program #(bool object)))) None) readiness None)
  (setv #^ (| (get Callable #(... (get Program #((| StorePressure PressureUnread) object)))) None) pressure None)
  (setv #^ (| str None) permit-database None))
