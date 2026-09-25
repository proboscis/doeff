;;; service が「準備できた」を報告する effect(process の生存とは別の、業務の条件つきの readiness)。
;;;
;;; 業務コード(service の Program)は拍ごとに ReportReady を出す。何をもって準備できたとするかは service が決める
;;; (書き手なら「書き先へ書けた・または書く必要が無いと判断した拍を終えた」)。coordinator は Service の宣言の
;;; readiness {"windowSeconds": n} を見て、同じ担い手・同じ版からの ready が直近 n 秒以内にある時だけ Ready とする。
;;; 報告が途絶えれば(拍が止まった・落ちた)window を過ぎて NotReady になる。Rollout はこの Ready を見て旧を止める。
;;;
;;; handler は 2 つ(readiness_handlers.hy): readiness-http = coordinator へ送る・readiness-memory = テストの記録。
(import dataclasses [dataclass])
(import doeff [EffectBase])


(setv ROLE-ACTIVE "active" ROLE-STANDBY "standby")

(defclass [(dataclass :frozen True)] ReportReady [EffectBase]
  "結果は None。ready = 準備できた(真)/できていない(偽)。reason = 人が読む理由(短く)。報告が届かなくても業務は止めない。
   role(2026-09-24)= active(本当に仕事をしている)か standby(名前付きの lease を他が持つ間、書きを捨てて拍を回している待機)。
   coordinator は standby の Ready も Service の Ready に数える(入れ替えで旧を止める合図)が、書き手の計器
   doeff_worker_service_ready_replicas は active の Ready だけを数える(alert の材料)。"
  (#^ bool ready)
  (setv #^ str reason "")
  (setv #^ str role ROLE-ACTIVE))
