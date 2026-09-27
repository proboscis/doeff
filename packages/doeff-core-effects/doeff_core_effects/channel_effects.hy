;;; 汎用の列(channel)の effect(agora-redesign #802 便 4 の相乗り・最初の消費者 = agora の画面の処理ループの受け口 — socket の 1 通・起こし・
;;; 記録の表の変更・Spawn した task の答えを 1 本の列で届いた順に読む。btc-w1 の #811)。業務の語を持たない土台の語彙。
;;; 答え手は scheduler-channel-handler(scheduler_channel.hy)1 つだけ — I/O を持たず、同じ run の scheduler の promise で待つので、本番と模擬で
;;; 同じ物を使う(差し替える物が無い)。scheduled の下で使う。
;;;
;;;   CreateChannel   新しい空の列を作る。答え = Channel(列の握り)。
;;;   PutChannel      列の末尾に 1 つ積む。待たない(列に上限は無い)。答え = None。
;;;   TakeChannel     列の先頭を 1 つ取る。空なら積まれるまで、撃った task だけが待つ(他の task は回る)。届いた順(FIFO)。
;;;
;;; 積めるのは同じ run の task だけ(外の thread から積む口は無い — 外の thread の出来事は、その答え手が外から完了させる promise で待った
;;; task が PutChannel で積む)。
(require doeff-hy.record [defrecord])
(import collections [deque])
(import dataclasses [dataclass field])
(import doeff [EffectBase])


(defclass [(dataclass :eq False)] Channel []
  "列の握り(値ではなく資源 — 中身の items と待ち手の waiters は答え手が書き換える。同一性で比べる)。scheduler の thread の中だけで触る。"
  #^ deque items
  (setv items (field :default-factory deque))
  #^ list waiters
  (setv waiters (field :default-factory list)))


(defclass [(dataclass :frozen True)] CreateChannel [EffectBase]
  "新しい空の列を作る(頭の註)。答え = Channel。")


(defclass [(dataclass :frozen True)] PutChannel [EffectBase]
  "列の末尾に 1 つ積む(頭の註)。答え = None。"
  (#^ Channel channel)
  (#^ object item))


(defclass [(dataclass :frozen True)] TakeChannel [EffectBase]
  "列の先頭を 1 つ取る — 空なら積まれるまで待つ(頭の註)。答え = 積まれた値。"
  (#^ Channel channel))
