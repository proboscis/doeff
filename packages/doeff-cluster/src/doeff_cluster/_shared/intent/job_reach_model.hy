;;; job の API の口へ届くかそのものを読む effect(届くかの検査 — job 向けの effect の兄弟)の型。CallJob(job_call_model.hy)は相手の答えを
;;; 読む effect で、届かない時は訳の 1 行しか持たず、繋ぐまでの上限も、宛先の名を引かずに番地で届く道も持たない。届くかを見る使い手
;;; (相手ごとに、名で引く道と番地の道の 2 本を出して、届かない種類で見る側の故障と相手の故障を分ける見張り)は、答えの中身でなく届いた事実を読み、
;;; 届かない種類を訳の字で分けない。使い手の業務の code は ReachJob だけを出し、HTTP の要求と宛先を知らない。
;;;
;;;   JobWay         届く道の閉じた語: BY-NAME = 宛先の名を引いて繋ぐ(cluster の Service の名)・BY-ADDRESS = 名を引かずに番地へ繋ぐ
;;;                  (Service の番地 — 名前引きの故障の外の道)
;;;   ReachFailure   届かない種類の閉じた語: NOT-CONNECTED = 繋がらなかった(名前引きの失敗と、名前引きの待ちを含む繋ぎの時間切れも
;;;                  ここ)・TIMED-OUT = 繋いだ後に答えの待ちの上限を越えた・OTHER = ほか(繋いだ後に切れた・話し方の誤り)
;;;   JobReached     相手が答えた — status は相手の決めたまま(2xx かは判じない)
;;;   JobNotReached  答えが来なかった — failure = 届かない種類・reason = 人と log が読む理由の 1 句(判じに使わない)
;;;   JobNoWay       その道の宛先が無い(宛先の表に道が無い・宛先の値が空)— 要求を出していない。reason = 理由の 1 句
;;;   ReachJob       API api(使い手の repo が名付けた名)の口へ、道 way で要求 request を 1 つ出し、届いたかを答える。connect-patience = 繋ぐまでの
;;;                  上限の秒(None = 要求の patience のまま — 番地の道のように、繋ぐのが短い道は短くする)。やり直さない
;;;
;;; handler は 2 つ(doeff_cluster._shared.protocol.job_reach_handlers): job-reach-sender = 道ごとの宛先(URL を持つ環境変数)の口へ HTTP の要求
;;; 1 本で出す(本番)・job-reach-handover = 相手の job の答えの 1 点(CallJob の相手 Counterpart と同じ)へ直に渡す(模擬 — 道を問わない)。
;;; 2 つが同じ要求に同じ答えを返す事は契約の検 tests/test_job_reach_contract.hy が見る。記録では答えを記録の値で返す(read)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defenum defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])  ; defrecord の展開が使う
(import enum [StrEnum])  ; defenum の展開が使う
(import doeff [EffectBase])
(import typing [ClassVar])
(import doeff_cluster._shared.intent.record_spec [RecordSpec RecordMode])
(import doeff_cluster._shared.intent.job_call_model [JobRequest])


(defenum JobWay BY-NAME BY-ADDRESS)


(defenum ReachFailure NOT-CONNECTED TIMED-OUT OTHER)


(defrecord JobReached
  "相手が答えた(頭の註): status = 答えの status(相手の決めたまま)。"
  {:tags {:context "doeff-cluster" :role "intent"}}
  (#^ int status))


(defrecord JobNotReached
  "答えが来なかった(頭の註): failure = 届かない種類・reason = 理由の 1 句。"
  {:tags {:context "doeff-cluster" :role "intent"} :check [(.strip reason)]}
  (#^ ReachFailure failure)
  (#^ str reason))


(defrecord JobNoWay
  "その道の宛先が無いので要求を出していない(頭の註): reason = 理由の 1 句。"
  {:tags {:context "doeff-cluster" :role "intent"} :check [(.strip reason)]}
  (#^ str reason))


(defclass [(dataclass :frozen True)] ReachJob [(get EffectBase (| JobReached JobNotReached JobNoWay))]
  "API api の口へ道 way で要求 request を 1 つ出し、届いたかを答える(頭の註)。connect-patience = 繋ぐまでの上限の秒か None。"
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str api)
  (#^ JobRequest request)
  (#^ JobWay way)
  (#^ (| float None) connect-patience))
