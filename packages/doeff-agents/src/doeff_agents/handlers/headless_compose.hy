;;; headless の handler の組み立ての部品(composition root が使う)— agora-redesign #604。
;;;
;;; 呼び手(agora など)が doeff_claude_code を import せずに組めるように、doeff-claude-code の handler(層 2)と
;;; headless の adapter(handlers/headless.hy・層 3)の対をここで作る。adapter の module は層 2 の handler も CLI の実行ファイルも
;;; 知らない — 知るのはこの組み立ての module だけ。
;;; どちらの組も外側に doeff-time の時間の handler(本番 = sync-time-handler・模擬 = sim-time-handler)と scheduler を要る。
;;; 並びは with_handlers の順(先頭が外側): 層 2 の handler → adapter(Program に近い側)。
(import collections.abc [Callable])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import doeff_agents.handlers.headless [HeadlessClaudeConfig HeadlessState headless-claude-handler])


(defn #^ list headless-claude-handlers [#^ str config-dir #^ dict env
                                        [settings None] [cold-resume-prompt None] [command #("claude")]]
  "本番の組: doeff-claude-code の本番の handler(手番ごとに CLI を起こす)+ headless の adapter。
   config-dir / env = claude の家(資格は root が custody から借りて env に置く)。行の時刻は壁の時計で刻む。"
  (setv config (HeadlessClaudeConfig (ClaudeHome config-dir (dict env)) :settings (dict (or settings {}))
                                     :cold-resume-prompt cold-resume-prompt))
  [(claude-code-handler (ClaudeCodeHost (tuple command) (clock-of (sync-time-handler))))
   (headless-claude-handler config (HeadlessState))])

(defn #^ list fake-headless-claude-handlers [#^ Callable responder [config-dir "fake-claude-home"]]
  "模擬の組: doeff-claude-code の fake の handler + headless の adapter(process も API も使わない)。
   responder = (入力の本文 それまでの入力の tuple) → FakeReply(返事の本文・道具の秒数・許可の問いの要否)。"
  [(fake-claude-code-handler (FakeClaudeWorld responder))
   (headless-claude-handler (HeadlessClaudeConfig (ClaudeHome config-dir {})) (HeadlessState))])
