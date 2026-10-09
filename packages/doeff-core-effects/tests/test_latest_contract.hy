;;; 最新の値の契約テスト — 同じ effect(PublishLatest・ReadLatest)に答える本物(process-latest-handler)と fake(memory-latest-handler)が、
;;; 同じ deftest を通る(agora-redesign #1440・#1107 の決め)。解釈器の組み立ては latest_contract_handlers.hy。
;;;
;;; 見る性質:
;;;   * まだ置いていない型を読むと None
;;;   * 置いた値をそのまま読める
;;;   * 同じ型を置き直すと、読めるのは最後の値
;;;   * 鍵は値の型ちょうど — 別の型の値は互いに上書きしない。子の型の値は親の型では読めない
;;;   * 変わるまでの待ち(AwaitLatest)は、既に別の値なら待たず・別の task が置いた値で起き・取り消されても次の待ちを壊さない
;;; 本物だけの性質(別の run・別の thread との共有)は test_process_latest.hy。
(require doeff-hy.macros [defk deftest <- val])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest AwaitLatest])
(import doeff_core_effects.scheduler [Spawn Wait Cancel CreatePromise CompletePromise])


(defrecord Progress
  "検の値 1: 同期の進み。"
  (#^ str waiting))


(defrecord Faults
  "検の値 2: 破れの数。"
  (#^ int total))


(defclass [(dataclass :frozen True)] LoudFaults [Faults]
  "Faults の子の型(鍵が型ちょうどであることを見るため)。")


(deftest test-a-type-never-published-reads-none
  {:interpreters ["process-latest" "memory-latest"]}
  (<- value (ReadLatest Progress))
  (assert (is value None) (.format "置いていない型の読みが {!r}" value)))


(deftest test-a-published-value-is-read-back
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "記録の表の一覧")))
  (<- value (ReadLatest Progress))
  (assert (= value (Progress :waiting "記録の表の一覧")) (.format "置いた値の読みが {!r}" value)))


(deftest test-the-last-publish-wins
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "一覧")))
  (<- (PublishLatest (Progress :waiting "畳んだ")))
  (<- value (ReadLatest Progress))
  (assert (= value (Progress :waiting "畳んだ")) (.format "置き直した後の読みが {!r}" value)))


(deftest test-the-key-is-the-exact-type
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "一覧")))
  (<- (PublishLatest (Faults :total 2)))
  (<- (PublishLatest (LoudFaults :total 7)))
  (<- progress (ReadLatest Progress))
  (<- faults (ReadLatest Faults))
  (<- loud (ReadLatest LoudFaults))
  (assert (= progress (Progress :waiting "一覧")) (.format "Faults を置いた後の Progress が {!r}" progress))
  (assert (= faults (Faults :total 2)) (.format "子の型 LoudFaults を置いた後の Faults が {!r}(子の型で上書きしない)" faults))
  (assert (= loud (LoudFaults :total 7)) (.format "LoudFaults の読みが {!r}" loud)))


;; --- 変わるまでの待ち(AwaitLatest — agora-redesign #4296)------------------------------------------------------------------
;; 書き手の run が置いた断面を、別の run が一定の間隔では確かめ直さずに、置かれた時に読む。どちらの答え手も同じ契約:
;;   * 今の値が既に seen と別なら待たずに答える
;;   * 待っている間に別の task が置いた値で起きて、その値を答える
;;   * 取り消された待ちの後に置いても、次の待ちは置いた値で起きる(取り消しが保存先を壊さない)

(defk waiting-for [seen]
  {:pre [(: seen (| Progress None))] :post [(: % (| Progress None))] :tags {:context "latest-test" :role "program"}}
  "Progress の最新の値が seen と別の物になるまで待ち、その値を答える(別の task で走らせる Program)。"
  (<- value (AwaitLatest Progress seen))
  value)


(defk let-others-run []
  {:pre [] :post [(: % None)] :tags {:context "latest-test" :role "program"}}
  "先に spawn した task が呼び鈴の待ちに入るまで回すため(後に spawn した task が終わるのを待つ — 時計を使わない)。"
  (<- promise (CreatePromise))
  (<- helper (Spawn (CompletePromise promise None)))
  (<- (Wait helper))
  None)


(deftest test-await-answers-at-once-when-the-value-already-differs
  {:interpreters ["process-latest" "memory-latest"]}
  (<- (PublishLatest (Progress :waiting "一覧")))
  (<- value (AwaitLatest Progress None))
  (assert (= value (Progress :waiting "一覧")) (.format "既に置かれた値の待ちの答えが {!r}" value)))


(deftest test-await-wakes-on-a-publish-from-another-task
  {:interpreters ["process-latest" "memory-latest"]}
  (val first (Progress :waiting "一覧"))
  (<- (PublishLatest first))
  (<- waiter (Spawn (waiting-for first)))
  (<- (let-others-run))
  (<- (PublishLatest (Progress :waiting "まとめた")))
  (<- value (Wait waiter))
  (assert (= value (Progress :waiting "まとめた")) (.format "置き直した後の待ちの答えが {!r}" value)))


(deftest test-a-cancelled-await-does-not-break-the-next-one
  {:interpreters ["process-latest" "memory-latest"]}
  (val first (Progress :waiting "一覧"))
  (<- (PublishLatest first))
  (<- cancelled (Spawn (waiting-for first)))
  (<- (let-others-run))
  (<- (Cancel cancelled))
  (val second (Progress :waiting "まとめた"))
  (<- (PublishLatest second))
  (<- waiter (Spawn (waiting-for second)))
  (<- (let-others-run))
  (<- (PublishLatest (Progress :waiting "閉じた")))
  (<- value (Wait waiter))
  (assert (= value (Progress :waiting "閉じた")) (.format "取り消しの後の待ちの答えが {!r}" value)))
