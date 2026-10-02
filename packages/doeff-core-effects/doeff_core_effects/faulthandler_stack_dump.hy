;;; 全 thread の stack を書く見張りの本物の答え手 faulthandler-stack-dump-handler(agora-redesign #2748)— ArmStackDump・DisarmStackDump・
;;; ReadStackDumps に、CPython の faulthandler の見張りと 1 つの run の中の台帳で答える。
;;;
;;; 何のためか: 共有の実行の流れが止まると、止めた呼び出しは止まりが明けた後には stack に残らない。止まりの最中に全 thread の stack を
;;; process の log(sys.stderr)へ書いておけば、後から名指せる。faulthandler.dump_traceback_later は期限を C の thread で数え、期限を
;;; 過ぎたらその thread が書くので、止まった thread が GIL や free-threading の全 thread の停止を持ったままでも書ける(Python の thread の
;;; 見張りは自分も止まる)。repeat なしなので、張った見張り 1 つにつき書くのは 1 度だけ。
;;;
;;; 約束: faulthandler の見張りは process に 1 つ(張り直すと前の見張りは消える)— この答え手を 1 つの process に 2 つ入れない。書き先は
;;; 張った時の sys.stderr。期限の判じ(ReadStackDumps の答え)は memory の答え手と同じ数え方(stack_dump_effects.hy の stack-dumps-at)で、
;;; 呼び手の刻(time.monotonic と同じ物差し)で数える。faulthandler に渡す残りの秒は at + seconds - いまの time.monotonic(過ぎていれば
;;; 最小の秒 — すぐ書く)。外側に state の handler が要る。
(require doeff-hy.macros [defhandler <- val var])
(import faulthandler)
(import sys)
(import time)
(import doeff_core_effects.stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps StackDumpLedger stack-dumps-at])

;; faulthandler が受ける最小の残りの秒(0 以下は受けない)。
(val MINIMUM-SECONDS 0.001)


(defhandler faulthandler-stack-dump-handler
  "見張りの効果に、faulthandler の見張りで本当に書いて答える(頭の註)。"
  (session var ledger (StackDumpLedger :deadline None :written #()))
  (ArmStackDump [at seconds]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (val deadline (+ at seconds))
    (faulthandler.dump-traceback-later (max MINIMUM-SECONDS (- deadline (time.monotonic))) :repeat False :file sys.stderr :exit False)
    (:= ledger (StackDumpLedger :deadline deadline :written settled.written))
    (resume None))
  (DisarmStackDump [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (faulthandler.cancel-dump-traceback-later)
    (:= ledger (StackDumpLedger :deadline None :written settled.written))
    (resume None))
  (ReadStackDumps [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger settled)
    (resume settled.written)))
