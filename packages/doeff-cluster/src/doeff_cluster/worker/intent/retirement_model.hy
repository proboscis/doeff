;;; job の process が「入れ替え(handoff)で退く事になった」を出来事で知る効果(#3672)。
;;;
;;;   (<- told (AwaitRetirement))                    ; 退く知らせが来るまで待つ(答え = Retired)
;;;   (<- again (AwaitRetirement :after told))       ; 次の知らせまで待つ(退きが取り消されたら HandoffAbandoned)
;;;
;;; 事実の持ち主は worker: 入れ替えを宣言した service の spec が変わると、worker は新のコードが揃い新の入口の検めが通った拍で旧を名から外し
;;; (RetireJob)、次の拍で新を同じ名で起こし、coordinator が新を Ready と数えた後に旧を止める(SIGTERM)。worker は名から外す時に旧の
;;; process へ「退く」(Retired)を知らせる — 新の起動・新の Ready・旧の SIGTERM のどれよりも前。coordinator が期限で入れ替えを諦めると
;;; (新が Ready にならない)旧は止められずに動き続けるので、worker は同じ process へ「退きを取り消した」(HandoffAbandoned)を知らせる。
;;; 宣言が変わって諦めが解ければ、もう一度「退く」を知らせる。訳の語は worker の止めの訳 StopReason の値(worker_model の Retired・
;;; HandoffAbandoned)をそのまま使う(別の語の型を作らない)。
;;;
;;; 答えは今の知らせが after と違う時に返る(1 回の効果で 1 つの知らせ — 次の知らせは、受けた値を after に渡した次の待ちで受ける)。
;;; after = None(既定)は「まだ何も受けていない」: 退く知らせが来るまで待つ。入れ替えの無い止め(宣言から外れた・spec が変わった
;;; recreate・worker の停止・途絶)と落ちでは何も知らせない — 止めは止めの合図(doeff_core_effects の AwaitStop)で知る。
;;;
;;; handler:
;;;   pipe-retirement-notices(worker/entry/retirement_notices.hy)… 本番: worker の子 process の中で、shim が渡した知らせの pipe(宿の契約
;;;                                   HOST-CONTRACT の notice-env が fd の番号を運ぶ)の行を読む thread が待ちを起こす(間隔で読み直さない)。
;;;   sim-cluster(sim/local.hy)        … 模擬: 偽の宿の RetireJob・NoticeJob が世界の受け手へ知らせを立て、待ちを起こす。
(require doeff-hy.macros [defeffect val])
(val MODULE-TAGS {:context "worker" :role "intent"})
(import dataclasses [dataclass])
(import doeff_cluster.worker.intent.worker_model [Retired HandoffAbandoned])


;; 退きの知らせの閉じた和: Retired = 退く(新が Ready と数えられたら止められる)・HandoffAbandoned = 退きを取り消した(入れ替えの諦め —
;; 旧は動き続ける)。
(val Retirement (| Retired HandoffAbandoned))


(defeffect AwaitRetirement
  "この process の job の退きの知らせが after と違う値になるまで待つ(頭の註)。after = 前に受けた知らせ(None = まだ何も受けていない)。
   答え = 今の知らせ(Retired か HandoffAbandoned)。入れ替えの無い止めと落ちでは答えない。"
  {:fields [(: after (| Retired HandoffAbandoned None) None)]
   :answer (| Retired HandoffAbandoned)
   :tags {:context "worker" :role "intent"}})
