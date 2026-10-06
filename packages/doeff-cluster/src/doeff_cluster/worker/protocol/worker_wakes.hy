;;; worker の周の間の待ちを起こす物を集める効果(WorkerWakes)の一番外の答え手(#3871 の単位 4)。状態を持つ handler(送り手の口・実行環境・
;;; process の host)は、この効果を外へ出し直した答えに自分の分を足して返す。一番外はこの no-wakes が空の組を返す — 組み立ての根は
;;; これを状態を持つ handler より外に置く。
(require doeff-hy.macros [defhandler val])
(val MODULE-TAGS {:context "worker" :role "protocol"})
(import doeff_cluster.shared.intent.due_model [DueNever])
(import doeff_cluster.worker.intent.worker_model [WakeSet WorkerWakes])


(defhandler no-wakes
  ;; 引数なし: 一番外の答えはいつも空の組(期限なし・呼び鈴なし・待つ子なし)。
  (WorkerWakes []
    (resume (WakeSet :due (DueNever) :bells #() :exits #()))))

