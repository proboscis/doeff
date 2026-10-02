;;; service が「準備できた」を報告する effect(process の生存とは別の、業務の条件つきの readiness)。
;;;
;;; 業務コード(service の Program)は拍ごとに ReportReady を出す。何をもって準備できたとするかは service が決める
;;; (書き手なら「書き先へ書けた・または書く必要が無いと判断した拍を終えた」)。coordinator は Service の宣言の
;;; readiness {"windowSeconds": n} を見て、同じ担い手・同じ版からの ready が直近 n 秒以内にある時だけ Ready とする。
;;; 報告が途絶えれば(拍が止まった・落ちた)window を過ぎて NotReady になる。Rollout はこの Ready を見て旧を止める。
;;;
;;; handler は 2 つ(readiness_handlers.hy): readiness-http = coordinator へ送る・readiness-memory = テストの記録。
;;; 宣言の readiness の形の検め(readiness-refusal)・入れ替えの期限(handoff-timeout-ms)は doeff_cluster.shared.core.readiness_rules
;;; (宣言の側と coordinator の側が使う)・報告の揃え(reported-readiness)は doeff_cluster.shared.core.readiness_report。ここは型と定数だけ。
(require doeff-hy.macros [val])
(require doeff-hy.record [defrecord])
(val MODULE-TAGS {:context "doeff-cluster" :role "intent"})
(import dataclasses [dataclass])
(import doeff [EffectBase])
(import typing [ClassVar])
(import doeff_cluster.shared.intent.record_spec [RecordSpec RecordMode Unexecuted])


(setv ROLE-ACTIVE "active" ROLE-STANDBY "standby")

;; --- 宣言の readiness の形 ---
;; {"windowSeconds" n "handoffTimeoutSeconds" m?}。windowSeconds = 直近 n 秒以内の「準備できた」だけを Ready と数える。
;; handoffTimeoutSeconds = 入れ替え(update = handoff)の新の世代が動き出してから Ready になるまで待つ上限(既定 300 — Rollout の
;; readyTimeoutSeconds の既定と同じ)。越えたら coordinator が入れ替えを諦め(新を止めて旧を残す — handoff_policy)、Service の status に
;; 理由を出す。期限は handoff の Service だけが持つ(recreate の宣言に書けば断る — 効かない欄を黙って受けない)。
(val HANDOFF-TIMEOUT-SECONDS 300)
(val READINESS-KEYS #("windowSeconds" "handoffTimeoutSeconds"))


;; 報告の reason を coordinator が残す長さ(字)。
(val REASON-KEPT-CHARS 300)
;; 報告の本文の 1 つの欄の素の値(JSON の値)。
(val JsonField (| dict list str int float bool None))


(defrecord ReportedReadiness
  "報告の ready・reason・role を coordinator が残す形に揃えた値(shared/core/readiness_report.reported-readiness の答え — #2756 の前は
   同じ 3 欄の dict): ready = 準備できたか・reason = 理由の先頭 REASON-KEPT-CHARS 字・role = ROLE-ACTIVE か ROLE-STANDBY。coordinator の
   準備の報告(coordinator/intent/cluster_model.ReadinessReport の 3 欄)と fake(readiness-memory の記録)が同じ値を持つ。"
  (#^ bool ready)
  (#^ str reason)
  (#^ str role))


(defclass [(dataclass :frozen True)] ReportReady [EffectBase]
  "結果は None。ready = 準備できた(真)/できていない(偽)。reason = 人が読む理由(短く)。報告が届かなくても業務は止めない。
   role(2026-09-24)= active(本当に仕事をしている)か standby(名前付きの lease を他が持つ間、書きを捨てて拍を回している待機)。
   coordinator は standby の Ready も Service の Ready に数える(入れ替えで旧を止める合図)が、書き手の計器
   doeff_worker_service_ready_replicas は active の Ready だけを数える(alert の材料)。"
  (setv #^ (get ClassVar RecordSpec) __record-spec__ (RecordSpec :mode RecordMode.OUTPUT :unexecuted Unexecuted.NOTHING))
  (#^ bool ready)
  (setv #^ str reason "")
  (setv #^ str role ROLE-ACTIVE))
