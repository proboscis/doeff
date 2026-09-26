;;; service が「準備できた」を報告する effect(process の生存とは別の、業務の条件つきの readiness)。
;;;
;;; 業務コード(service の Program)は拍ごとに ReportReady を出す。何をもって準備できたとするかは service が決める
;;; (書き手なら「書き先へ書けた・または書く必要が無いと判断した拍を終えた」)。coordinator は Service の宣言の
;;; readiness {"windowSeconds": n} を見て、同じ担い手・同じ版からの ready が直近 n 秒以内にある時だけ Ready とする。
;;; 報告が途絶えれば(拍が止まった・落ちた)window を過ぎて NotReady になる。Rollout はこの Ready を見て旧を止める。
;;;
;;; handler は 2 つ(readiness_handlers.hy): readiness-http = coordinator へ送る・readiness-memory = テストの記録。
;;; 宣言の readiness の形の検め(readiness-refusal)と入れ替えの期限(handoff-timeout-ms)もここに置く(宣言の側と coordinator の側が使う)。
(require doeff-hy.macros [val])
(import dataclasses [dataclass])
(import doeff [EffectBase])


(setv ROLE-ACTIVE "active" ROLE-STANDBY "standby")

;; --- 宣言の readiness の形 ---
;; {"windowSeconds" n "handoffTimeoutSeconds" m?}。windowSeconds = 直近 n 秒以内の「準備できた」だけを Ready と数える。
;; handoffTimeoutSeconds = 入れ替え(update = handoff)の新の世代が動き出してから Ready になるまで待つ上限(既定 300 — Rollout の
;; readyTimeoutSeconds の既定と同じ)。越えたら coordinator が入れ替えを諦め(新を止めて旧を残す — handoff_policy)、Service の status に
;; 理由を出す。期限は handoff の Service だけが持つ(recreate の宣言に書けば断る — 効かない欄を黙って受けない)。
(val HANDOFF-TIMEOUT-SECONDS 300)
(val READINESS-KEYS #("windowSeconds" "handoffTimeoutSeconds"))


(defn #^ bool positive-number [value]  ; defk にできない: 宣言の検め(module の読み込みの時と coordinator の純粋な判断)が呼ぶ
  "JSON の正の数か(bool は数に数えない)。"
  (and (isinstance value #(int float)) (not (isinstance value bool)) (> value 0)))


(defn #^ (| str None) readiness-refusal [readiness #^ str update]  ; defk にできない: 宣言の検め(module の読み込みの時と coordinator の純粋な判断)が呼ぶ
  "宣言の readiness(None か dict)と入れ替えの形 update → 読めなければ理由の文、読めれば None。service の宣言(service_model.service)と
   coordinator の宣言の読み(cluster_policy.job-from-json)の 2 つの入口が同じ規則で検める(定義点はここ 1 つ)。"
  (cond
    (is readiness None) None
    (not (isinstance readiness dict)) (.format "readiness は dict: {!r}" readiness)
    (not (positive-number (.get readiness "windowSeconds")))
      (.format "readiness は windowSeconds(正の数)を持つ dict: {!r}" readiness)
    (any (gfor k readiness (not-in k READINESS-KEYS)))
      (.format "readiness の知らない欄: {}(書ける欄 = {})" (sorted (gfor k readiness :if (not-in k READINESS-KEYS) (str k)))
               (list READINESS-KEYS))
    (and (in "handoffTimeoutSeconds" readiness) (not (positive-number (get readiness "handoffTimeoutSeconds"))))
      (.format "readiness の handoffTimeoutSeconds は正の数: {!r}" (get readiness "handoffTimeoutSeconds"))
    (and (in "handoffTimeoutSeconds" readiness) (!= update "handoff"))
      (.format "handoffTimeoutSeconds は update = handoff の Service だけが持つ(いまの update = {!r})" update)
    True None))


(defn #^ int handoff-timeout-ms [readiness]  ; defk にできない: coordinator の純粋な判断(Program の外)が呼ぶ
  "宣言の readiness(None か検めを通った dict)→ 入れ替えの新の世代が Ready になるまで待つ上限(ms)。書かなければ既定。"
  (int (* 1000 (.get (or readiness {}) "handoffTimeoutSeconds" HANDOFF-TIMEOUT-SECONDS))))


(defclass [(dataclass :frozen True)] ReportReady [EffectBase]
  "結果は None。ready = 準備できた(真)/できていない(偽)。reason = 人が読む理由(短く)。報告が届かなくても業務は止めない。
   role(2026-09-24)= active(本当に仕事をしている)か standby(名前付きの lease を他が持つ間、書きを捨てて拍を回している待機)。
   coordinator は standby の Ready も Service の Ready に数える(入れ替えで旧を止める合図)が、書き手の計器
   doeff_worker_service_ready_replicas は active の Ready だけを数える(alert の材料)。"
  (#^ bool ready)
  (setv #^ str reason "")
  (setv #^ str role ROLE-ACTIVE))
