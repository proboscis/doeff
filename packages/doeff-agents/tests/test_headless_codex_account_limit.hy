;; headless の codex の adapter(handlers/headless_codex.hy)が、codex の利用枠切れのターンの終わりを AgentAccountLimit にする判定の検
;; (card acp:kanban-issue:ki-a59d80e0afa9)。録った行の筋(stub)は test_headless_codex_adapter.hy が撃つ — ここは録りに無い窓の組を、
;; 判定の関数へ層 2 の記録の型で直に渡す。
;;
;; 守る事: agora は account_limit の在る失敗を口座の利用枠切れと読み、resets_at の刻までその口座を外す(claude と同じ道)。刻は codex が
;; 名乗った尽きた窓の戻りの刻だけで、名乗られなければ None(刻を発明しない — 発明すると口座を早く戻し、また断られる)。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "headless-codex-adapter-test" :role "program"})
(import doeff_agents.effects [AgentAccountLimit])
(import doeff_agents.handlers.headless_codex [account-limit-of merged-limits])
(import doeff_codex.lines [TurnEnded TurnStatus RateLimits RateLimitWindow UnknownValue])
(import doeff_hy.json_value [OpaqueJson])

(val TEXT "You’ve hit your usage limit.")
(val FIVE-HOURS-AT 1893456000)
(val SEVEN-DAYS-AT 1893888000)
;; codex が誤りの種類 usageLimitExceeded で失敗に終えたターンの終わり。
(val LIMITED (TurnEnded :thread-id "t" :turn-id "u" :status TurnStatus.FAILED :error-message TEXT :error-kind "usageLimitExceeded"))
;; 窓(使った割合・長さ〔分〕・戻りの刻): SPENT = 尽きた / PART = 尽きていない。
(val FIVE-HOURS-SPENT (RateLimitWindow :used-percent 100 :minutes 300 :resets-at FIVE-HOURS-AT))
(val FIVE-HOURS-PART (RateLimitWindow :used-percent 80 :minutes 300 :resets-at FIVE-HOURS-AT))
(val FIVE-HOURS-SPENT-UNSTATED (RateLimitWindow :used-percent 100 :minutes 300 :resets-at None))
(val SEVEN-DAYS-SPENT (RateLimitWindow :used-percent 100 :minutes 10080 :resets-at SEVEN-DAYS-AT))
(val SEVEN-DAYS-PART (RateLimitWindow :used-percent 37 :minutes 10080 :resets-at SEVEN-DAYS-AT))
(val ONE-DAY-SPENT (RateLimitWindow :used-percent 100 :minutes 1440 :resets-at FIVE-HOURS-AT))


(deftest test-the-account-waits-for-the-exhausted-window-that-resets-last
  ;; 尽きた窓が 1 つ: その窓の名(claude と同じ綴り)と戻りの刻。
  (<- one (account-limit-of LIMITED (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-PART)))
  (assert (= one (AgentAccountLimit :window "five_hour" :resets-at FIVE-HOURS-AT :text TEXT)) one)
  ;; 尽きた窓が 2 つ: 口座はどちらも戻るまで使えないので、遅く戻る方。
  (<- both (account-limit-of LIMITED (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-SPENT)))
  (assert (= both (AgentAccountLimit :window "seven_day" :resets-at SEVEN-DAYS-AT :text TEXT)) both)
  ;; 名の表に無い長さの窓: 刻は運び、窓の名は None(綴りを発明しない)。
  (<- daily (account-limit-of LIMITED (RateLimits :limit-id "codex" :primary ONE-DAY-SPENT :secondary None)))
  (assert (= daily (AgentAccountLimit :window None :resets-at FIVE-HOURS-AT :text TEXT)) daily))


(deftest test-an-unstated-return-stays-unstated
  ;; 利用枠切れでも、戻りの刻を決める窓が分からなければ刻も窓の名も None(文は運ぶ): 知らせが無い・尽きた窓が無い(使った割合が 100 に
  ;; 届かない)・尽きた窓の 1 つが戻りの刻を名乗らない。
  (val unstated (AgentAccountLimit :window None :resets-at None :text TEXT))
  (for [limits [None
                (RateLimits :limit-id "codex" :primary FIVE-HOURS-PART :secondary SEVEN-DAYS-PART)
                (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT-UNSTATED :secondary SEVEN-DAYS-SPENT)]]
    (<- limit (account-limit-of LIMITED limits))
    (assert (= limit unstated) #(limits limit))))


(deftest test-other-failures-are-not-account-limits
  ;; 利用枠切れでない誤り(上流の 429 の繰り返しの尽き・版で増えた種類・種類なし)は、尽きた窓を知っていても口座の利用枠切れにしない。
  (val exhausted (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary None))
  (for [kind ["responseTooManyFailedAttempts" (UnknownValue :value (OpaqueJson.of "usageLimitSoon")) None]]
    (<- limit (account-limit-of (TurnEnded :thread-id "t" :turn-id "u" :status TurnStatus.FAILED :error-message TEXT :error-kind kind)
                                exhausted))
    (assert (is limit None) #(kind limit))))


(deftest test-a-quiet-update-keeps-the-windows-already-named
  ;; 同じ枠の後の知らせが窓を名乗らなくても、前に名乗った窓を消さない(codex 0.162.1 の schema の註: 名乗らない欄は前に見た値を消さない)。
  ;; 名乗った窓は新しい値に替わる。別の枠の知らせは前の枠の窓を引き継がない。
  (val named (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-PART))
  (<- kept (merged-limits named (RateLimits :limit-id "codex" :primary None :secondary None)))
  (assert (= kept named) kept)
  (<- moved (merged-limits named (RateLimits :limit-id "codex" :primary None :secondary SEVEN-DAYS-SPENT)))
  (assert (= moved (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-SPENT)) moved)
  (val other (RateLimits :limit-id "codex_other" :primary None :secondary SEVEN-DAYS-PART))
  (<- replaced (merged-limits named other))
  (assert (= replaced other) replaced)
  (<- first (merged-limits None named))
  (assert (= first named) first))
