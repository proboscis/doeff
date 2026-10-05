;;; 退きの知らせ(#3672)の本番の路の検の job: 本番の答え手 pipe-retirement-notices を土台に並べ、待ち始めに印 Listening を、AwaitRetirement
;;; の答えを受けるたびに知らせの型の名を、引数の file へ 1 行ずつ足す(受けた知らせを after にして次を待つ — 止めの合図の既定の動きで終わる)。
;;;
;;; 引数: <知らせの file>
(require doeff-hy.macros [defk deff <- val var])
(import sys)
(import doeff [run with-handlers])
(import doeff_core_effects.handlers [state])
(import doeff_core_effects.scheduler [scheduled])
(import doeff_core_effects.file_effects [AppendText file-done])
(import doeff_core_effects.os_file [os-file-handler])
(import doeff_cluster.worker.intent.worker_model [Retired HandoffAbandoned])
(import doeff_cluster.worker.intent.retirement_model [AwaitRetirement])
(import doeff_cluster.worker.entry.retirement_notices [pipe-retirement-notices])


(defk listen [path]
  {:pre [(: path str)] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "program"}}
  "退きの知らせを待ち、受けるたびに path へ知らせの型の名を 1 行足す(止められるまで)— 本番の路で届いた知らせを検が file で読むため。
   最初に印 Listening を足す(検が job の起動を待ってから worker の知らせを出せる)。"
  (<- (file-done (AppendText path "Listening\n")))
  (var after None)
  (while True
    (<- told (| Retired HandoffAbandoned) (AwaitRetirement :after after))
    (<- (file-done (AppendText path (+ (. (type told) __name__) "\n"))))
    (:= after told))
  None)


(deff main []  ; defk にできない: 子の process の入口(`__main__` が素の関数として呼ぶ)
  {:pre [] :post [(: % None)] :tags {:context "doeff-cluster-test" :role "main"}}
  "検の job の本体: 本番の答え手と file の答え手を並べ、scheduler の下で知らせを待ち続けるため。"
  (run (scheduled (with-handlers [(state) os-file-handler pipe-retirement-notices] (listen (get sys.argv 1)))))
  None)


(when (= __name__ "__main__")
  (main))
