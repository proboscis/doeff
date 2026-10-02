;;; 全 thread の stack を書く見張りの memory の答え手 memory-stack-dump-handler(agora-redesign #2748)— ArmStackDump・DisarmStackDump・
;;; ReadStackDumps に、1 つの run の中の台帳(session の値)で答える。
;;;
;;; 何のためか: 模擬と検では、本物(faulthandler_stack_dump.hy)と同じ約束で「止まった時だけ・止まり 1 回に 1 度」書くかを確かめたいが、
;;; 実時間の見張りの thread にも process の log にも触れたくない。違うのは書くかどうかだけで、期限の数え方(stack_dump_effects.hy の
;;; stack-dumps-at)は本物と同じ。外側に state の handler が要る。
(require doeff-hy.macros [defhandler <- var val])
(val MODULE-TAGS {:context "stack-dump" :role "foundation"})
(import doeff_core_effects.stack_dump_effects [ArmStackDump DisarmStackDump ReadStackDumps StackDumpLedger stack-dumps-at])


(defhandler memory-stack-dump-handler
  "見張りの効果に、書かずに台帳だけで答える(頭の註)。"
  (session var ledger (StackDumpLedger :deadline None :written #()))
  (ArmStackDump [at seconds]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger (StackDumpLedger :deadline (+ at seconds) :written settled.written))
    (resume None))
  (DisarmStackDump [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger (StackDumpLedger :deadline None :written settled.written))
    (resume None))
  (ReadStackDumps [at]
    (<- settled StackDumpLedger (stack-dumps-at ledger at))
    (:= ledger settled)
    (resume settled.written)))
