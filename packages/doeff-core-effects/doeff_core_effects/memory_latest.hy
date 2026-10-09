;;; 最新の値の memory の答え手 memory-latest-handler(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)— PublishLatest・ReadLatest・AwaitLatest に、
;;; 1 つの run の中の状態(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(process_latest.hy)と同じ契約で最新の値の受け渡しを確かめたいが、process に 1 つの置き場や thread に
;;; 触れたくない。違うのは置き場だけ(process に 1 つ → この run の中)。別の run とは共有しない。外側に state の handler が要る。
;;;
;;; 変わるまでの待ち(AwaitLatest — agora-redesign #4296)は本物と同じ形: 待つ側は呼び鈴(CreateExternalPromise の約束 — 同じ run の task が
;;; 完了してもよい)を session の「型 → 掛かっている呼び鈴の組」に掛け、park の形(Wait の PRIORITY_IDLE — 約束が開いている間も仮想の時計が
;;; 進む)で待つ。PublishLatest が値を差し替えた後に、その型の呼び鈴を全部外して鳴らす。呼び手が待ちの task を Cancel した時は、約束の取り消しの
;;; callback が呼び鈴を外す — callback は effect を出せないので、呼び鈴の組は中身を書き換える dict に持ち、待ちに入る時にその dict を session に
;;; 置く(task を回す effect より前に置く — 2 つの待ちが別々の dict を置き合わない。以後に読むと同じ dict を引く・1 つの run の中だけ)。
;;; 節の中の session の値は節に入った時に 1 度読まれ、待ちの後にもう一度読めないので、鳴らす側は置いた値で呼び鈴を完了し、待つ側はその値を答える
;;; (同じ object が置き直された時は seen のままなので、また掛けて待つ)。
(require doeff-hy.macros [defhandler deff var val <-])
(val MODULE-TAGS {:context "latest" :role "foundation"})
(import functools [partial])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects.scheduler [CreateExternalPromise ExternalPromise Wait PRIORITY_IDLE])


(deff drop-bell [bells kind bell]  ; defk にできない: 呼び鈴の約束の取り消しの callback(ExternalPromise.on-cancel)が scheduler の中で素の関数として呼ぶ
  {:pre [(: bells dict) (: kind type) (: bell ExternalPromise)] :post [(: % None)] :tags {:context "latest" :role "foundation"}}
  "待ち終えた・取り消された待ちの呼び鈴 bell を型 kind の組から外すため(鳴って外された後なら何もしない — 呼び鈴は object そのもので比べる)。"
  (setv left (tuple (gfor other (.get bells kind #()) :if (is-not other bell) other)))
  (if left
      (setv (get bells kind) left)
      (.pop bells kind None))
  None)


(defhandler memory-latest-handler
  "PublishLatest・ReadLatest・AwaitLatest に、この run の中の保存先(型 → 最新の値)で答える(頭の註)。"
  (session var board {})
  ;; 型 → 掛かっている呼び鈴の組(頭の註の変わるまでの待ち — 中身を書き換える dict。初めて待つ時に session に置く)。
  (session var bells {})
  (PublishLatest [value]
    (:= board (| board {(type value) value}))
    (for [bell (.pop bells (type value) #())]
      (.complete bell value))
    (resume None))
  (ReadLatest [kind]
    (resume (.get board kind)))
  (AwaitLatest [kind seen]
    (var current (.get board kind))
    (when (is current seen)
      (val held bells)
      (:= bells held)
      (while (is current seen)
        (<- bell ExternalPromise (CreateExternalPromise))
        (setv (get held kind) (+ (.get held kind #()) #(bell)))
        (.on-cancel bell (partial drop-bell held kind bell))
        (<- rung (Wait bell.future :priority PRIORITY_IDLE))
        (drop-bell held kind bell)
        (:= current rung)))
    (resume current)))
