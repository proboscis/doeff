;; headless の codex の adapter(handlers/headless_codex.hy)が、層 2 の利用枠切れの事実(UsageLimitHit)を公開型 AgentAccountLimit へ
;; 写す検(card acp:kanban-issue:ki-a59d80e0afa9)。録った行の筋(stub)と fake の筋は test_headless_codex_adapter.hy が撃つ — ここは
;; 録りに無い窓の長さを、写しの関数へ直に渡す。
;;
;; 守る事: agora は account_limit の在る失敗を口座の利用枠切れと読み、resets_at の刻までその口座を外す(claude と同じ道)。adapter は
;; 層 2 の事実の刻と文をそのまま運び、窓の長さを claude と同じ綴りの窓の名にするだけ(判定・閾値・codex の誤りの種類の綴りは層 2)。
(require doeff-hy.macros [deftest <- val])
(val MODULE-TAGS {:context "headless-codex-adapter-test" :role "program"})
(import doeff_agents [AgentAccountLimit])
(import doeff_agents._handlers.headless_codex [account-limit-of])
;; 層 2 の事実の型は組み立ての部品から読む(この検も doeff_codex を import しない)。
(import doeff_agents._handlers.headless_compose [CodexUsageLimitHit])

(val TEXT "You’ve hit your usage limit.")
(val FIVE-HOURS-AT 1893456000)
(val SEVEN-DAYS-AT 1893888000)


(deftest test-the-window-length-becomes-the-claude-window-name
  ;; 5 時間(300 分)= five_hour・7 日(10080 分)= seven_day。刻と文はそのまま。
  (<- five (account-limit-of (CodexUsageLimitHit :minutes 300 :resets-at FIVE-HOURS-AT :text TEXT)))
  (assert (= five (AgentAccountLimit :window "five_hour" :resets-at FIVE-HOURS-AT :text TEXT)) five)
  (<- seven (account-limit-of (CodexUsageLimitHit :minutes 10080 :resets-at SEVEN-DAYS-AT :text TEXT)))
  (assert (= seven (AgentAccountLimit :window "seven_day" :resets-at SEVEN-DAYS-AT :text TEXT)) seven)
  ;; 名の表に無い長さの窓: 刻は運び、窓の名は None(綴りを発明しない)。
  (<- daily (account-limit-of (CodexUsageLimitHit :minutes 1440 :resets-at FIVE-HOURS-AT :text TEXT)))
  (assert (= daily (AgentAccountLimit :window None :resets-at FIVE-HOURS-AT :text TEXT)) daily))


(deftest test-an-unstated-return-stays-unstated-and-no-fact-is-no-limit
  ;; 層 2 が窓も刻も名乗らない利用枠切れ: 窓も刻も None(文は運ぶ)。利用枠切れの事実が無い終わりは account_limit None。
  (<- unstated (account-limit-of (CodexUsageLimitHit :minutes None :resets-at None :text TEXT)))
  (assert (= unstated (AgentAccountLimit :window None :resets-at None :text TEXT)) unstated)
  (<- absent (account-limit-of None))
  (assert (is absent None) absent))
