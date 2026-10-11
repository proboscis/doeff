;; 口座の利用枠切れの事実(UsageLimitHit)の検 — 層 2 が、ターンの中で届いた枠の知らせ(RateLimits)と失敗の終わり(誤りの種類
;; usageLimitExceeded)から、口座が待つ窓の長さ・戻りの刻・codex の文を作る(card ki-a59d80e0afa9)。
;;
;; 守る事: 上の層は利用枠切れを UsageLimitHit だけで読み、codex の誤りの種類の綴り・使った割合の閾値・枠の知らせの重ね方を知らない
;; (層 2 の替え玉 fake も同じ事実で利用枠切れのターンを出せる)。刻は codex が名乗った尽きた窓の戻りの刻だけで、名乗られなければ
;; None(刻を発明しない — 早すぎる刻は口座を早く戻し、また断られる)。窓は長さ(分)で名乗る — 窓の名は上の層の語。
(require doeff-hy.macros [val])
(val MODULE-TAGS {:context "codex-test" :role "foundation"})
(require doeff-hy.macros [deftest defk <- var])
(import json)
(import dataclasses [fields])
(import pathlib [Path])
(import typing [get-args])
(import doeff_codex.lines [classify-line CodexLine LimitRecord RateLimits RateLimitWindow TurnEnded TurnStatus UnknownValue
                           UsageLimitHit])
(import doeff_codex.limits [merged-limits usage-limit-hit-of])
(import doeff_codex.handler [SessionRuntime absorb])
(import doeff_codex.values [CodexSessionSpec CodexHome])
(import doeff_codex.rpc [ApprovalPolicy SandboxMode])
(import doeff_hy.json_value [OpaqueJson])

(val TEXT "You’ve hit your usage limit.")
(val FIVE-HOURS-AT 1893456000)
(val SEVEN-DAYS-AT 1893888000)
;; codex が誤りの種類 usageLimitExceeded で失敗に終えたターンの終わり(行の分類の答え — 利用枠切れの事実はまだ載らない)。
(val LIMITED (TurnEnded :thread-id "t" :turn-id "u" :status TurnStatus.FAILED :error-message TEXT :error-kind "usageLimitExceeded"))
;; 窓(使った割合・長さ〔分〕・戻りの刻): SPENT = 尽きた / PART = 尽きていない。
(val FIVE-HOURS-SPENT (RateLimitWindow :used-percent 100 :minutes 300 :resets-at FIVE-HOURS-AT))
(val FIVE-HOURS-PART (RateLimitWindow :used-percent 80 :minutes 300 :resets-at FIVE-HOURS-AT))
(val FIVE-HOURS-SPENT-UNSTATED (RateLimitWindow :used-percent 100 :minutes 300 :resets-at None))
(val SEVEN-DAYS-SPENT (RateLimitWindow :used-percent 100 :minutes 10080 :resets-at SEVEN-DAYS-AT))
(val SEVEN-DAYS-PART (RateLimitWindow :used-percent 37 :minutes 10080 :resets-at SEVEN-DAYS-AT))
(val ONE-DAY-SPENT (RateLimitWindow :used-percent 100 :minutes 1440 :resets-at FIVE-HOURS-AT))
(val SRC (/ (. (Path __file__) (resolve) parent parent) "src" "doeff_codex"))


(deftest test-the-account-waits-for-the-exhausted-window-that-resets-last
  ;; 尽きた窓が 1 つ: その窓の長さと戻りの刻。
  (<- one (usage-limit-hit-of LIMITED #((RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-PART))))
  (assert (= one (UsageLimitHit :minutes 300 :resets-at FIVE-HOURS-AT :text TEXT)) one)
  ;; 尽きた窓が 2 つ: 口座はどちらも戻るまで使えないので、遅く戻る方。別の枠(limit-id)の窓も同じに数える。
  (<- both (usage-limit-hit-of LIMITED #((RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary None)
                                        (RateLimits :limit-id "codex_other" :primary None :secondary SEVEN-DAYS-SPENT))))
  (assert (= both (UsageLimitHit :minutes 10080 :resets-at SEVEN-DAYS-AT :text TEXT)) both)
  ;; 名の無い長さの窓も長さのまま運ぶ(窓の名は上の層が決める)。
  (<- daily (usage-limit-hit-of LIMITED #((RateLimits :limit-id "codex" :primary ONE-DAY-SPENT :secondary None))))
  (assert (= daily (UsageLimitHit :minutes 1440 :resets-at FIVE-HOURS-AT :text TEXT)) daily))


(deftest test-an-unstated-return-stays-unstated
  ;; 利用枠切れでも、戻りの刻を決める窓が分からなければ長さも刻も None(文は運ぶ): 知らせが無い・尽きた窓が無い(使った割合が 100 に
  ;; 届かない)・尽きた窓の 1 つが戻りの刻を名乗らない。
  (val unstated (UsageLimitHit :minutes None :resets-at None :text TEXT))
  (for [limits [#()
                #((RateLimits :limit-id "codex" :primary FIVE-HOURS-PART :secondary SEVEN-DAYS-PART))
                #((RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT-UNSTATED :secondary SEVEN-DAYS-SPENT))]]
    (<- hit (usage-limit-hit-of LIMITED limits))
    (assert (= hit unstated) #(limits hit))))


(deftest test-other-failures-are-not-usage-limits
  ;; 利用枠切れでない誤り(上流の 429 の繰り返しの尽き・版で増えた種類・種類なし)は、尽きた窓を知っていても利用枠切れにしない。
  (val exhausted #((RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary None)))
  (for [kind ["responseTooManyFailedAttempts" (UnknownValue :value (OpaqueJson.of "usageLimitSoon")) None]]
    (<- hit (usage-limit-hit-of (TurnEnded :thread-id "t" :turn-id "u" :status TurnStatus.FAILED :error-message TEXT :error-kind kind)
                                exhausted))
    (assert (is hit None) #(kind hit))))


(deftest test-a-quiet-update-keeps-the-windows-already-named
  ;; 同じ枠の後の知らせが窓を名乗らなくても、前に名乗った窓を消さない(codex 0.162.1 の schema の註: 名乗らない欄は前に見た値を消さない)。
  ;; 名乗った窓は新しい値に替わる。別の枠の知らせは別に覚え、前の枠の窓を消さない。
  (val named (RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-PART))
  (<- first (merged-limits #() named))
  (assert (= first #(named)) first)
  (<- kept (merged-limits first (RateLimits :limit-id "codex" :primary None :secondary None)))
  (assert (= kept #(named)) kept)
  (<- moved (merged-limits first (RateLimits :limit-id "codex" :primary None :secondary SEVEN-DAYS-SPENT)))
  (assert (= moved #((RateLimits :limit-id "codex" :primary FIVE-HOURS-SPENT :secondary SEVEN-DAYS-SPENT))) moved)
  (val other (RateLimits :limit-id "codex_other" :primary None :secondary SEVEN-DAYS-PART))
  (<- both (merged-limits first other))
  (assert (= both #(named other)) both))


(deftest test-a-rate-limit-update-read-right-after-the-turn-start-answer-joins-that-turn
  ;; 失敗ケース(設計検証 lt-4FZFGSA5AJ7DGD7BZ4656WY6HS の再現): 読みの thread は turn/start の答えの直後の行を、答えを待つ側が
  ;; 走っているターンを立てる前に読み得る。ターンを名乗らない枠の知らせがその隙に届いても、答えが名乗ったターンに積まれ、
  ;; そのターンの失敗の終わりが利用枠切れの事実を運ぶ(読みの thread と同じ順で absorb を呼ぶ — 答えを待つ側は何もしない)。
  (val runtime (SessionRuntime (CodexSessionSpec :home (CodexHome :env {}) :cwd "/tmp" :approval-policy ApprovalPolicy.NEVER
                                                 :sandbox SandboxMode.READ-ONLY)))
  (for [line [{"id" 3 "result" {"turn" {"id" "u1" "status" "inProgress"}}}
              {"method" "account/rateLimits/updated"
               "params" {"rateLimits" {"limitId" "codex" "secondary" None
                                       "primary" {"usedPercent" 100 "windowDurationMins" 300 "resetsAt" FIVE-HOURS-AT}}}}
              {"method" "turn/completed"
               "params" {"threadId" "t1" "turn" {"id" "u1" "status" "failed"
                                                 "error" {"message" TEXT "codexErrorInfo" "usageLimitExceeded"}}}}]]
    (<- record (classify-line (json.dumps line)))
    (<- (absorb runtime record)))
  (assert (= runtime.outside #()) runtime.outside)
  (val end (. (get runtime.turns "u1") end))
  (assert (= end.usage-limit (UsageLimitHit :minutes 300 :resets-at FIVE-HOURS-AT :text TEXT)) end))


(deftest test-a-rate-limit-update-read-right-after-turn-started-joins-that-turn
  ;; 失敗ケース(依頼 lt-0BC9J56TE8QA3VH1FZNJD3F4FS・設計検証 lt-H32P81572MAR6ANS1EAQJRXAAQ の盲検 B): 続きの会話で、前のターン u0 が
  ;; 終わって走っているターンの印がまだ u0 を指す間に、読みの thread が turn/started(u1)を読み、続けて枠の知らせを読む(turn/start の
  ;; 答えはまだ読んでいない)。枠の知らせは u1 に積まれ(終わった u0 にも outside にも落ちない)、u1 の失敗の終わりが利用枠切れの事実を
  ;; 運ぶ(読みの thread と同じ順で absorb を呼ぶ — 答えを待つ側は何もしない)。
  (val runtime (SessionRuntime (CodexSessionSpec :home (CodexHome :env {}) :cwd "/tmp" :approval-policy ApprovalPolicy.NEVER
                                                 :sandbox SandboxMode.READ-ONLY)))
  (for [line [{"id" 3 "result" {"turn" {"id" "u0" "status" "inProgress"}}}
              {"method" "turn/started" "params" {"threadId" "t1" "turn" {"id" "u0" "status" "inProgress"}}}
              {"method" "turn/completed" "params" {"threadId" "t1" "turn" {"id" "u0" "status" "completed"}}}
              {"method" "turn/started" "params" {"threadId" "t1" "turn" {"id" "u1" "status" "inProgress"}}}
              {"method" "account/rateLimits/updated"
               "params" {"rateLimits" {"limitId" "codex" "secondary" None
                                       "primary" {"usedPercent" 100 "windowDurationMins" 300 "resetsAt" FIVE-HOURS-AT}}}}
              {"method" "error"
               "params" {"error" {"message" TEXT "codexErrorInfo" "usageLimitExceeded"} "willRetry" False "threadId" "t1"
                         "turnId" "u1"}}
              {"method" "turn/completed"
               "params" {"threadId" "t1" "turn" {"id" "u1" "status" "failed"
                                                 "error" {"message" TEXT "codexErrorInfo" "usageLimitExceeded"}}}}]]
    (<- record (classify-line (json.dumps line)))
    (<- (absorb runtime record)))
  (assert (= runtime.outside #()) runtime.outside)
  (assert (= (. (get runtime.turns "u0") limits) #()) (. (get runtime.turns "u0") limits))
  (val end (. (get runtime.turns "u1") end))
  (assert (= end.usage-limit (UsageLimitHit :minutes 300 :resets-at FIVE-HOURS-AT :text TEXT)) end))


(defk limited-turn-lines [#^ str turn-id #^ int request-id #^ tuple told]
  {:pre [(: turn-id str) (: request-id int) (: told tuple)] :post [(: % tuple)] :tags {:context "codex-test" :role "foundation"}}
  "続きの会話の利用枠切れの 1 ターンを、読みの thread が読む順の行(JSON の文字)にするため: turn/start の答え → turn/started → told
   (ターンの中で届く枠の知らせの行 — 空なら知らせは無い)→ 誤り → 失敗の終わり。"
  (val failed {"message" TEXT "codexErrorInfo" "usageLimitExceeded"})
  (+ #((json.dumps {"id" request-id "result" {"turn" {"id" turn-id "status" "inProgress"}}})
       (json.dumps {"method" "turn/started" "params" {"threadId" "t1" "turn" {"id" turn-id "status" "inProgress"}}}))
     told
     #((json.dumps {"method" "error" "params" {"error" failed "willRetry" False "threadId" "t1" "turnId" turn-id}})
       (json.dumps {"method" "turn/completed" "params" {"threadId" "t1" "turn" {"id" turn-id "status" "failed" "error" failed}}}))))


(deftest test-a-usage-limit-turn-reads-only-the-notices-of-its-own-turn
  ;; 失敗ケース(依頼 lt-0BC9J56TE8QA3VH1FZNJD3F4FS の設計検証・盲検 B): 口座の枠の知らせはターンの記録ごとに重ね、ターンをまたいで
  ;; 持ち越さない。前のターン u0 が尽きた窓(5 時間・戻り FIVE-HOURS-AT)を名乗って利用枠切れで終わった後、続きのターン u1 も利用枠切れで
  ;; 終わる。u1 の中で窓を名乗る知らせが無い(知らせが来ない・来ても窓を名乗らない)なら、u1 の事実は窓も刻も None — u0 の古い刻を
  ;; u1 の事実として運ばない(会話の一生で枠を重ねて終わりの事実を作る実装は、ほかの検を全部通したままこれを破った)。
  (val spent (json.dumps {"method" "account/rateLimits/updated"
                          "params" {"rateLimits" {"limitId" "codex"
                                                  "primary" {"usedPercent" 100 "windowDurationMins" 300 "resetsAt" FIVE-HOURS-AT}
                                                  "secondary" {"usedPercent" 37 "windowDurationMins" 10080
                                                               "resetsAt" SEVEN-DAYS-AT}}}}))
  (val quiet (json.dumps {"method" "account/rateLimits/updated"
                          "params" {"rateLimits" {"limitId" "codex" "primary" None "secondary" None}}}))
  (for [later [#() #(quiet)]]
    (val runtime (SessionRuntime (CodexSessionSpec :home (CodexHome :env {}) :cwd "/tmp" :approval-policy ApprovalPolicy.NEVER
                                                   :sandbox SandboxMode.READ-ONLY)))
    (<- first (limited-turn-lines "u0" 3 #(spent)))
    (<- second (limited-turn-lines "u1" 4 later))
    (for [raw (+ first second)]
      (<- record (classify-line raw))
      (<- (absorb runtime record)))
    (val before (. (get runtime.turns "u0") end usage-limit))
    (assert (= before (UsageLimitHit :minutes 300 :resets-at FIVE-HOURS-AT :text TEXT)) before)
    (val hit (. (get runtime.turns "u1") end usage-limit))
    (assert (= hit (UsageLimitHit :minutes None :resets-at None :text TEXT)) #(later hit))))


(deftest test-every-record-that-carries-limit-windows-is-a-limit-record
  ;; 失敗ケース(依頼 lt-0BC9J56TE8QA3VH1FZNJD3F4FS の設計検証・盲検 A): どの記録が利用枠切れの事実の材料(口座の枠の窓)を運ぶかは、
  ;; 記録の型を持つ lines が 1 か所(LimitRecord)で名乗り、handler はその集まりでターンの枠へ重ねる(handler は記録の class を列挙しない
  ;; — TurnRecord と同じ形)。codex の版上げで窓を運ぶ記録(例: 枠の断面をまとめた通知)を足して LimitRecord に入れ忘れると、handler は
  ;; その記録をただの出来事として積み、ターンの枠に重ねない — 利用枠切れの戻りの刻が黙って None になる。この検が赤にする。
  ;; 窓を運ぶ = 欄の型の式のどこか(和・tuple の中も辿る)に RateLimitWindow か RateLimits が在る。
  (var carrying #())
  (var pending #())
  (for [record (get-args CodexLine)]
    (:= pending (tuple (gfor field (fields record) field.type)))
    (while pending
      (when (in (get pending 0) #(RateLimitWindow RateLimits))
        (:= carrying (+ carrying #(record)))
        (:= pending #()))
      (when pending
        (:= pending (+ (cut pending 1 None) (get-args (get pending 0)))))))
  (val declared (frozenset (or (get-args LimitRecord) #(LimitRecord))))
  (assert (= (frozenset carrying) declared)
          (.format "窓を運ぶのに LimitRecord に無い: {} / LimitRecord に在るのに窓を運ばない: {}"
                   (sorted (gfor kind (- (frozenset carrying) declared) kind.__name__))
                   (sorted (gfor kind (- declared (frozenset carrying)) kind.__name__)))))


(deftest test-the-codex-layer-names-windows-by-their-length
  ;; 層 2 は窓を長さ(分)で名乗り、上の層の窓の名(claude の CLI の綴り five_hour・seven_day)を持たない — 名の表は doeff-agents の
  ;; adapter の 1 か所(層 2 の fake が名で筋書きを受けると、名の表の逆が層 2 に 2 つ目の定義として入る — 設計検証の盲検 B)。
  (val spoken (lfor path (sorted (.rglob SRC "*")) :if (in path.suffix #(".hy" ".py" ".pyi"))
                    word #("five_hour" "seven_day") :if (in word (.read-text path :encoding "utf-8"))
                    #((str (.relative-to path SRC)) word)))
  (assert (= spoken []) spoken))
