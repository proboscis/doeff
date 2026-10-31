;;; まねた受付の箱の形の検 — 模擬の環境の入口の検(_sim/test_entries_on_sim)から分けた。_sim の module は区画を隠す間、古い path の層で
;;; 公開の名に数えられたので、検の関数と InboxQueue の import を公開の外(tests/)へ置いた(ADR-DOE-API-001 R1・R5)。
(require doeff-hy.macros [deftest val])
(import doeff_cluster._shared.protocol.inbox [InboxQueue])
(import doeff_cluster._foundation.coordinator_inbox [StopState])
(import doeff_cluster._record_store.entry.handler_sets [ScriptedRecordInbox])


(deftest test-the-scripted-record-inbox-keeps-the-inbox-shape
  ;; まねた受付の箱は本番の http-requests が読む受付の箱の形(InboxQueue — runtime-checkable なので欄も見る)を保つ: 形に口が
  ;; 足された時(96be48c3c の reached)に、まねた箱が置き去りになれば、入口を回す検より先にここで落ちる。
  (val inbox (ScriptedRecordInbox [] (StopState)))
  (assert (isinstance inbox InboxQueue) (vars inbox)))
