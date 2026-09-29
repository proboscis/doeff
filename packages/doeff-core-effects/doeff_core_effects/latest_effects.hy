;;; 最新の値の effect — 1 つの run が置いた値の最新を、別の run が書き手を待たずに読む(agora-redesign #1440・ADR-DOE-CORE-EFFECTS-004)。
;;; 業務の語を持たない土台の語彙。
;;;
;;;   PublishLatest  value を置く。鍵は値の型ちょうど(type(value))— 同じ型の前の値を置き換える。答え = None。
;;;   ReadLatest     kind の型の最新の値を読む。まだ置いていなければ None。子の型の値は親の型では読めない(鍵は型ちょうど)。
;;;
;;; 置く値は変わらない値(frozen の record など)にする — 読み手は参照をそのまま受け取るので、置いた後に中身を書き換えると読み手に見える。
;;;
;;; 答え手: process-latest-handler(process_latest.hy — 本物。process に 1 つの置き場を名前で共有し、別の run・別の thread が同じ値を読む)と
;;; memory-latest-handler(memory_latest.hy — 1 つの run の中の状態だけ)。2 つは同じ契約のテストを通す(tests/test_latest_contract.hy)。
(require doeff-hy.macros [defeffect])


(defeffect PublishLatest
  "value を、その型の最新の値として置く(頭の註)。"
  {:fields [(: value object)]
   :answer None
   :tags {:context "latest" :role "foundation"}})


(defeffect ReadLatest
  "kind の型の最新の値を読む(まだ無ければ None — 頭の註)。"
  {:fields [(: kind type)]
   :answer (| object None)
   :tags {:context "latest" :role "foundation"}})
