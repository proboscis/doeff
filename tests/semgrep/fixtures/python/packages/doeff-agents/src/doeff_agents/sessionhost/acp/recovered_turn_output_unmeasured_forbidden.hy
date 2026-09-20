;;; semgrep hit fixture: doeff-agents-recovered-turns-do-not-claim-unread-output
;;;                    / doeff-agents-turn-output-judgment-reads-the-materials-coverage
;;; (card acp:kanban-issue:ki-ef537db05f7f — 再起動の後に行から拾い直した手番は、材料の
;;;  読み始めが手番の始まりではなく拾い直した拍の file の大きさなので、再起動の前に書かれた
;;;  出力を読めない。直す前の形: recover-job が start-offset-of の覆い〔(get start 2)〕を
;;;  運ばず、手番の終わりの判断も材料の覆いを知らないまま「出力 0 件 = 出さなかった」と
;;;  結論していた。答え終えた手番が failed / TurnProducedNothing になり、ACP の配達が
;;;  同じ郵便でもう一度手番を作る = 二重回答)。

(defk recover-job [settings state row now-ms]
  (<- plan LaunchPlan (launch-plan-of row))
  (<- recovered-arm str (recovered-arm-of plan view row.resource-id))
  (<- start tuple (start-offset-of view recovered-arm))
  ;; BAD: 拾い直した材料がこの手番を覆うかを運ばない(覆っている前提で組む)
  (<- job InFlightJob
      (in-flight-job-of row plan view settings.node-name row.created-at-ms
                        row.created-at-ms (get start 1) lease #()))
  job)


(defk settle-record [settings state job view source path outcome step now-ms]
  ;; BAD: 材料の覆いを読まずに「出力 0 件」を判じている
  (<- nothing (| dict None) (turn-output-condition-of
                              step source path batch
                              (if (isinstance view SessionView) view.turn-error None)
                              True))
  nothing)
