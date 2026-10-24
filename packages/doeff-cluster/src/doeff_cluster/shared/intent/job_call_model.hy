;;; job から別の job の API へ要求を 1 つ渡す effect(job 向けの effect)の型 — 使い手の repo の業務の code は CallJob だけを出し、相手へ届ける
;;; 道(HTTP の要求と宛先)も、模擬で相手へ直に渡す道も知らない。届け方を決めるのは handler だけ(cluster では HTTP の要求で相手の Service へ・
;;; 模擬では相手の job の答えの 1 点へ直に)— 業務の code が HttpRequest と宛先の環境変数を持つと、模擬は HTTP の相手を差し替えるしかなく、
;;; 差し替えた相手が本物の job の答えとずれる。
;;;
;;;   JobField        名と値の組 1 つ(要求の query・見出しと答えの見出し)
;;;   JobRequest      API へ渡す要求 1 つ — method・path(API の根の後ろ・%-encode する前の綴り)・query・headers・body・patience = 答えを待つ上限の秒・
;;;                   follow-redirects = 相手の転送(3xx の Location)を送る handler が辿るか(既定 = 辿らない。公開の網の口の読みのように、改名した口の
;;;                   旧い名へ 301 で新しい口を答える相手を読む時だけ辿る — 直に渡す handler の相手は辿った後の答えを答える)
;;;   JobAnswer       API の答え 1 つ — status・content-type・body・headers
;;;   JobUnreachable  答えが届かなかった(宛先が無い・接続できない・待ちの上限を越えた)— reason = 人と log が読む理由の 1 行
;;;   CallJob         API api(使い手の repo が名付けた API の名)へ要求 request を 1 つ渡す(答え = JobAnswer か JobUnreachable)
;;;
;;; handler は 2 つ(doeff_cluster.shared.protocol.job_call_handlers): job-call-sender = 宛先(API の根の URL を持つ環境変数)の口へ HTTP の要求 1 本で
;;; 送る(本番)・job-call-handover = 相手の job の答えの 1 点へ直に渡す(模擬)。2 つが同じ要求に同じ答えを返す事は契約の検
;;; tests/test_job_call_contract.hy が見る。記録では答えを記録の値で返す(read — 相手の job の答えは外の世界)。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import typing [ClassVar])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode])


(defrecord JobField
  "名と値の組 1 つ(要求の query・見出しと答えの見出し)。"
  {:tags {:context "doeff-cluster" :role "intent"} :check [(.strip name)]}
  (#^ str name)
  (#^ str value))


(defrecord JobRequest
  "API へ渡す要求 1 つ(頭の註): method・path(API の根の後ろ・`/` で始まる %-encode する前の綴り)・query・headers・body(None = 本文なし)・
   patience = 答えを待つ上限の秒・follow-redirects = 送る handler が相手の転送を辿るか(既定 False)。宛先は持たない。"
  {:tags {:context "doeff-cluster" :role "intent"} :check [(.strip method) (.startswith path "/") (> patience 0)]}
  (#^ str method)
  (#^ str path)
  (setv #^ (get tuple #(JobField ...)) query #()
        #^ (get tuple #(JobField ...)) headers #()
        #^ (| bytes None) body None
        #^ float patience 30.0
        #^ bool follow-redirects False))


(defrecord JobAnswer
  "API の答え 1 つ: status・content-type・body・headers(答えの見出し — 名は答え手の綴りのまま)。"
  {:tags {:context "doeff-cluster" :role "intent"}}
  (#^ int status)
  (#^ str content-type)
  (#^ bytes body)
  (setv #^ (get tuple #(JobField ...)) headers #()))


(defrecord JobUnreachable
  "答えが届かなかった(頭の註): reason = 理由の 1 行。"
  {:tags {:context "doeff-cluster" :role "intent"} :check [(.strip reason)]}
  (#^ str reason))


(defclass [(dataclass :frozen True)] CallJob [(get EffectBase (| JobAnswer JobUnreachable))]
  "API api へ要求 request を 1 つ渡す(頭の註)。答え = JobAnswer(相手が答えた — status は相手の決めたまま)か JobUnreachable(届かなかった)。"
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.READ))
  (#^ str api)
  (#^ JobRequest request))
