;;; 最新の値の memory の答え手 memory-latest-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— PublishLatest・ReadLatest・AwaitLatest に、
;;; 1 つの run の中の状態(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(process_latest.hy)と同じ契約で最新の値の受け渡しを確かめたいが、process に 1 つの置き場や thread に
;;; 触れたくない。違うのは置き場だけ(process に 1 つ → この run の中)。別の run とは共有しない。外側に state の handler が要る。
;;;
;;; 保存先は本物と同じ形: 中身を書き換える dict 2 つ(型 → 最新の値・型 → 掛かっている呼び鈴の組)を 1 つの値 MemoryLatestStore にまとめ、
;;; session の値はその 1 つだけにする。run の中で最初に答える時に 1 度作って session に置き、以後の節は取り出して中身を書き換える(置き直さない)。
;;; だから置く・読む 1 回ごとの session の出し入れは 1 回(置く回数の多い使い手の検の歩数に乗る — 呼び鈴の組を 2 つ目の session の値に
;;; 持っていた形では置くたびに 3 回だった・test_memory_latest.hy)。
;;;
;;; 変わるまでの待ち(AwaitLatest — agora-redesign #4296)は本物と同じ形: 待つ側は呼び鈴(CreateExternalPromise の約束 — 同じ run の task が
;;; 完了してもよい)を呼び鈴の組に掛け、park の形(Wait の PRIORITY_IDLE — 約束が開いている間も仮想の時計が進む)で待つ。PublishLatest が値を
;;; 差し替えた後に、その型の呼び鈴を全部外して鳴らす。呼び手が待ちの task を Cancel した時は、約束の取り消しの callback(effect を出せない)が
;;; 呼び鈴の組から外す。鳴らす側は置いた値で呼び鈴を完了し、待つ側はその値を答える(同じ object が置き直された時は seen のままなので、
;;; また掛けて待つ)。
(require doeff-hy.macros [defhandler deff var val <-])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import functools [partial])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Wait PRIORITY_IDLE])


(defrecord MemoryLatestStore
  "1 つの run の中の保存先: values = 型 → 最新の値・bells = 型 → 掛かっている呼び鈴の組。どちらも節が中身を書き換える dict(頭の註)。"
  {:tags {:context "latest" :role "type"}}
  (#^ dict values)
  (#^ dict bells))


(deff drop-bell [bells kind bell]  ; defk にできない: 呼び鈴の約束の取り消しの callback(ExternalPromise.on-cancel)が scheduler の中で素の関数として呼ぶ
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "待ち終えた・取り消された待ちの呼び鈴 bell を型 kind の組から外すため(鳴って外された後なら何もしない — 呼び鈴は object そのもので比べる)。"
  (setv left (tuple (gfor other (.get bells kind #()) :if (is-not other bell) other)))
  (if left
      (setv (get bells kind) left)
      (.pop bells kind None))
  None)


(defhandler memory-latest-handler
  "PublishLatest・ReadLatest・AwaitLatest に、この run の中の保存先 MemoryLatestStore で答える(頭の註)。"
  (session val store (MemoryLatestStore :values {} :bells {}))
  (PublishLatest [value]
    (setv (get store.values (type value)) value)
    (for [bell (.pop store.bells (type value) #())]
      (.complete bell value))
    (resume None))
  (ReadLatest [kind]
    (resume (.get store.values kind)))
  (AwaitLatest [kind seen]
    (var current (.get store.values kind))
    (while (is current seen)
      (<- bell ExternalPromise (CreateExternalPromise))
      (setv (get store.bells kind) (+ (.get store.bells kind #()) #(bell)))
      (.on-cancel bell (partial drop-bell store.bells kind bell))
      (<- rung (Wait bell.future :priority PRIORITY_IDLE))
      (drop-bell store.bells kind bell)
      (:= current rung))
    (resume current)))
