;;; テストの偽の coordinator(fake の盤 board_fake.hy・MemoryCoordinator の線 coordinator_contract_handlers.hy の coordinator-over-http)が、
;;; 名前付きの lease の空きの待ち GET /watch?lease=<名> に答える 1 か所(2026-10-07 の決定 B — SemaphoreSession は時間で問い直さず、claim を
;;; 断られたらこの待ちだけで空きを待つ)。本物の coordinator は待ちの係(watch_policy の lease の watcher)が、返した時・期限が切れた時に
;;; 答える。偽物は同じ 2 つの起き方を、待ちの呼び鈴(lease への書きで鳴らす)と期限の刻(lease_rules の lease-full-until)で作る。
;;;
;;;   lease-freed        満ちていれば、最初に空く刻か、その lease への書きで鳴る呼び鈴まで待つ。空いていればすぐ戻る
;;;   lease-waiters-rung lease への書きの後に、その名前を待つ呼び鈴を全部鳴らす(返した・取った・延ばした — 起きた待ち手は claim し直し、
;;;                      まだ満ちていれば待ち直す)
;;;   LEASE-FREED-REPLY  待ちの返事の本文(本物と同じ形 — lease-wait-answer が changed を読む)
;;; waiters = 名前 → 待っている呼び鈴の列(偽物の handler の session の dict)。
(require doeff-hy.macros [defk <- val])
(val MODULE-TAGS {:context "doeff-cluster-test" :role "test"})
(import doeff_core_effects.scheduler [CreatePromise CompletePromise Promise])
(import doeff_cluster.shared.core.promise_wait [promise-or-timeout])

(val LEASE-FREED-REPLY {"revision" 0 "changed" True})


(defk lease-freed [waiters name full-until now]
  {:pre [(: waiters dict) (: name str) (: full-until (| int None)) (: now int)] :post [(: % None)]
   :tags {:context "doeff-cluster-test" :role "program"}}
  "lease name の空きの待ちに答える前に、空くまで待つため(full-until = 最初に空く刻・None = 今空いている)。"
  (when (is-not full-until None)
    (<- bell Promise (CreatePromise))
    (setv (get waiters name) (+ (.get waiters name #()) #(bell)))
    (<- _rung (promise-or-timeout bell.future (/ (max 0 (- full-until now)) 1000))))
  None)


(defk lease-waiters-rung [waiters name]
  {:pre [(: waiters dict) (: name str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "lease name への書きの後に、その名前を待っている呼び鈴を全部鳴らすため(待ち手は claim し直す)。"
  (for [bell (.pop waiters name #())]
    (<- (CompletePromise bell True)))
  None)
