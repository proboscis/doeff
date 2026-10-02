;;; 全 thread の stack を log に書く見張りの effect — 共有の実行の流れ(scheduler・Await の橋の event loop)が止まった時に、止めた呼び出しを
;;; 名指す証拠を止まりの最中に残すため(agora-redesign #2748・#2734)。業務の語を持たない土台の語彙。
;;;
;;;   ArmStackDump     at + seconds までに張り直されなければ、process の全 thread の stack を 1 度だけ書く見張りを張る。張り直すと前の見張りは
;;;                    消える(process に見張りは 1 つ)。at = 張った刻(単調時計の秒 — time.monotonic と同じ物差し)。答え = None。
;;;   DisarmStackDump  張った見張りを外す(at = 外した刻)。答え = None。
;;;   ReadStackDumps   見張りが書いた刻(期限の刻)の列を、古い順に読む(at = 読む刻 — 読む刻に期限を過ぎていた見張りも書いた物に数える)。
;;;
;;; 刻を effect に載せる理由: 期限を過ぎたかの判じを、本物と memory の答え手が同じ物差し(呼び手の時計)で同じ数え方 stack-dumps-at で
;;; する。memory の答え手は時計を持たない(仮想の時計の下の呼び手が刻を渡す)。
;;;
;;; 答え手: faulthandler-stack-dump-handler(faulthandler_stack_dump.hy — 本物。期限は CPython の faulthandler の C の thread が数え、期限を
;;; 過ぎたらその thread が sys.stderr に書く — 止まった thread が GIL や free-threading の全 thread の停止を持ったままでも書ける)と
;;; memory-stack-dump-handler(memory_stack_dump.hy — 書かずに数えるだけ)。2 つは同じ契約のテストを通す(tests/test_stack_dump_contract.hy)。
(require doeff-hy.macros [defeffect defk])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])  ; defrecord の展開が名指す


(defeffect ArmStackDump
  "at + seconds までに張り直されなければ全 thread の stack を 1 度書く見張りを張る(頭の註)。"
  {:fields [(: at float) (: seconds float)]
   :answer None
   :tags {:context "stack-dump" :role "foundation"}})


(defeffect DisarmStackDump
  "張った見張りを外す(頭の註)。"
  {:fields [(: at float)]
   :answer None
   :tags {:context "stack-dump" :role "foundation"}})


(defeffect ReadStackDumps
  "見張りが書いた刻の列を古い順に読む(頭の註)。"
  {:fields [(: at float)]
   :answer (get tuple #(float ...))
   :tags {:context "stack-dump" :role "foundation"}})


(defrecord StackDumpLedger
  "見張りの台帳: deadline = 張っている見張りの期限の刻(張っていなければ None)・written = 書いた刻の列(古い順)。"
  {:tags {:context "stack-dump" :role "foundation"}}
  (#^ (| float None) deadline)
  (#^ (get tuple #(float ...)) written))


(defk stack-dumps-at [ledger at]
  {:pre [(: ledger StackDumpLedger) (: at float)] :post [(: % StackDumpLedger)] :tags {:context "stack-dump" :role "foundation"}}
  "刻 at の時点の台帳を出すため — 期限を過ぎた見張りは期限の刻に 1 度書いて消えている(本物と memory の答え手が共有する数え方)。"
  (if (and (is-not ledger.deadline None) (> at ledger.deadline))
      (StackDumpLedger :deadline None :written (+ ledger.written #(ledger.deadline)))
      ledger))
