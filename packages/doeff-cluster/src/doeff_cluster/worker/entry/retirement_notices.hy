;;; 退きの知らせ(worker/intent/retirement_model の AwaitRetirement — #3672)の本番の答え手 pipe-retirement-notices。worker の子 process の
;;; 中で、job の土台の組に並べる(宿の契約 HOST-CONTRACT の 4 つ目 — 入口 job_entry は handler を足さない・R2)。
;;;
;;; 路: worker が入れ替えで job を名から外す(RetireJob)・入れ替えの諦めとその解け(NoticeJob)の時に、shim の標準入力へ 1 行
;;; (worker/protocol/process_host の retirement-line)を書く → shim がその行を job の知らせの pipe へ中継する(worker/entry/shim の
;;; --notice-env)→ ここの読みの thread(foundation/notice_pipe)が行を受けて待ちを起こす。間隔で読み直さない。
;;; 語を知らせの型に読むのは process_host の retirement-of-word(綴りは止めの訳の語 stop-reason-word の 1 か所)。知らない語は名指しの
;;; ValueError が待ち手に上がる。worker の子でない process(環境変数 HOST-CONTRACT.notice-env が無い)では答えない(退く事が起きない)。
;;;
;;; 並び: session val を使うので外側に状態の handler(doeff_core_effects.handlers の state)、待ちは外部の Promise なので外側に scheduler が
;;; 要る。sim の偽の宿は同じ効果に世界の受け手で答える(sim/local.hy の process-notices)。
(require doeff-hy.macros [defhandler <- val var])
(val MODULE-TAGS {:context "worker" :role "main"})
(import doeff_cluster.foundation.host_contract [HOST-CONTRACT])
(import doeff_cluster.foundation.notice_pipe [open-notice-box next-notice])
(import doeff_cluster.worker.intent.worker_model [Retired HandoffAbandoned])
(import doeff_cluster.worker.intent.retirement_model [AwaitRetirement])
(import doeff_cluster.worker.protocol.process_host [stop-reason-word retirement-of-word])


(defhandler pipe-retirement-notices
  {:needs #{} :tags {:context "worker" :role "main"}}
  ;; 本番の宿の答え(頭の註)。読みの thread は process に 1 本(最初の問いで立てる — session で 1 回)。
  (session val box (! (open-notice-box HOST-CONTRACT.notice-env)))
  (AwaitRetirement [after]
    ;; 前に受けた知らせを pipe の語に綴り直し、それと違う語が来るまで待つ。
    (var seen None)
    (when (is-not after None)
      (<- spelled str (stop-reason-word after))
      (:= seen spelled))
    (<- word str (next-notice box seen))
    (<- notice (| Retired HandoffAbandoned) (retirement-of-word word))
    (resume notice)))
