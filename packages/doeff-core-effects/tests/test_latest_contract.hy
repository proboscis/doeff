;;; 最新の値の契約テスト — 同じ effect(PublishLatest・ReadLatest)に答える本物(process-latest-handler)と fake(memory-latest-handler)が、
;;; 同じ deftest を通る(agora-redesign #1440・#1107 の決め)。解釈器の組み立ては latest_contract_handlers.hy。
;;;
;;; 見る性質:
;;;   * まだ置いていない型を読むと None
;;;   * 置いた値をそのまま読める
;;;   * 同じ型を置き直すと、読めるのは最後の値
;;;   * 鍵は値の型ちょうど — 別の型の値は互いに上書きしない。子の型の値は親の型では読めない
;;; 本物だけの性質(別の run・別の thread との共有)は test_process_latest.hy。
(require doeff-hy.macros [deftest <-])
(require doeff-hy.record [defrecord])
(import dataclasses [dataclass])
(import doeff_core_effects.latest_effects [PublishLatest ReadLatest])


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
