;;; 口座の利用枠 — ターンの中で届いた枠の知らせ(RateLimits)の重ね方と、利用枠切れで終わったターンの事実(UsageLimitHit)の判定。
;;;
;;; 効果を持たない(純粋)。handler がターンの記録の上でこの 2 つを使い、ターンの終わり(TurnEnded)に利用枠切れの事実を載せる。上の層は
;;; 事実だけを読み、codex の誤りの種類の綴り・使った割合の閾値・知らせの重ね方を知らない(層 2 の fake も同じ事実で利用枠切れの終わりを
;;; 出せる)。実物の行は tests/recorded/codex-0.162.1/usage-limit(枠の知らせ → 誤り usageLimitExceeded → 失敗の終わり)。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex" :role "foundation"})
(require doeff-hy.macros [defk])
(import doeff_codex.lines [RateLimits TurnEnded UsageLimitHit USAGE-LIMIT-KIND])

;; 窓が尽きたと読む使った割合(百分率)。
(val SPENT-PERCENT 100)


(defk merged-limits [#^ tuple kept #^ RateLimits fresh]
  {:pre [(: kept tuple) (: fresh RateLimits)] :post [(: % tuple)] :tags {:context "codex" :role "judgment"}}
  "ターンの中で届いた枠の知らせを重ねるため(ターンの終わりに尽きた窓を読む材料)。kept = 枠の名(limit-id)ごとに 1 つの重ねた知らせの
   列(届いた順)。同じ枠の後の知らせが名乗らない窓は、前に名乗った窓を消さない(codex 0.162.1 の schema の註: 名乗らない欄は前に見た
   値を消さない)。名乗った窓は新しい値に替わる。別の枠の知らせは列の後ろに足す(前の枠の窓を消さない)。"
  (val before (next (gfor snapshot kept :if (= snapshot.limit-id fresh.limit-id) snapshot) None))
  (when (is before None)
    (return (+ kept #(fresh))))
  (val merged (RateLimits :limit-id fresh.limit-id
                          :primary (if (is fresh.primary None) before.primary fresh.primary)
                          :secondary (if (is fresh.secondary None) before.secondary fresh.secondary)))
  (tuple (gfor snapshot kept (if (is snapshot before) merged snapshot))))


(defk usage-limit-hit-of [#^ TurnEnded end #^ tuple limits]
  {:pre [(: end TurnEnded) (: limits tuple)] :post [(: % (| UsageLimitHit None))] :tags {:context "codex" :role "judgment"}}
  "上の層が口座を替えて戻りの刻まで待てるように、codex が口座の利用枠切れ(誤りの種類 USAGE-LIMIT-KIND)で終えたターンの事実を作るため
   (ほかの終わりは None)。limits = そのターンで重ねた枠の知らせ(merged-limits の答え)。text = codex の誤りの文。窓の長さと戻りの刻は、
   どの枠のでも使った割合 SPENT-PERCENT 以上の窓から読み、尽きた窓が 2 つ以上なら遅く戻る方(口座はどれも戻るまで使えない)。尽きた
   窓が無い・尽きた窓の 1 つでも戻りの刻を名乗らない・知らせが無ければ、長さも刻も None(刻を発明しない — 早すぎる刻は口座を早く
   戻し、また断られる)。"
  (when (!= end.error-kind USAGE-LIMIT-KIND)
    (return None))
  (val text (or end.error-message ""))
  (val spent (tuple (gfor snapshot limits
                          window #(snapshot.primary snapshot.secondary)
                          :if (and (is-not window None) (>= window.used-percent SPENT-PERCENT))
                          window)))
  (when (or (not spent) (any (gfor window spent (is window.resets-at None))))
    (return (UsageLimitHit :minutes None :resets-at None :text text)))
  (val last (max spent :key (fn [window] window.resets-at)))
  (UsageLimitHit :minutes last.minutes :resets-at last.resets-at :text text))
