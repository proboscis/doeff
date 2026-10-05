;;; headless の handler の組み立ての部品(composition root が使う)— agora-redesign #604。
;;;
;;; 呼び手(agora など)が doeff_claude_code を import せずに組めるように、doeff-claude-code の handler(層 2)と
;;; headless の adapter(handlers/headless.hy・層 3)の対をここで作る。adapter の module は層 2 の handler も CLI の実行ファイルも
;;; 知らない — 知るのはこの組み立ての module だけ。
;;; どちらの組も外側に doeff-time の時間の handler(本番 = sync-time-handler・模擬 = sim-time-handler)と scheduler を要る。
;;; 本番の組は加えて slog の答え手を要る(層 2 の本番の handler が CLI の起動の計時の行を slog で出す — agora-redesign #3605)。
;;; 並びは with_handlers の順(先頭が外側): 層 2 の handler → adapter(Program に近い側)。
(import collections.abc [Callable Mapping])
(import doeff_hy.frozen [FrozenMap])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
(import doeff_agents.handlers.headless [HeadlessClaudeConfig HeadlessState headless-claude-handler])


(defn #^ list headless-claude-handlers [#^ str config-dir #^ (get FrozenMap str) env
                                        [settings None] [cold-resume-prompt None] [command #("claude")]]
  "本番の組: doeff-claude-code の本番の handler(手番ごとに CLI を起こす)+ headless の adapter。
   config-dir / env = claude の家(資格は root が custody から借りて env に置く — 凍らせた写像。dict を渡しても作る時に凍らせる)/
   settings = CLI の settings の JSON(FrozenMap か None)。行の時刻は壁の時計で刻む。"
  (setv config (HeadlessClaudeConfig (ClaudeHome config-dir env)
                                     :settings (if (is settings None) (FrozenMap) settings)
                                     :cold-resume-prompt cold-resume-prompt))
  [(claude-code-handler (ClaudeCodeHost (tuple command) (clock-of (sync-time-handler))))
   (headless-claude-handler config (HeadlessState))])

(defn #^ list fake-headless-claude-handlers [responder [config-dir "fake-claude-home"] [world None]
                                             * #^ (get Mapping #(str str)) env #^ (get Mapping #(str object)) settings]
  "模擬の組: doeff-claude-code の fake の handler + headless の adapter(process も API も使わない)。
   responder = (入力の本文 それまでの入力の tuple) → FakeReply(返事の本文・道具の秒数・許可の問いの要否)。
   world = 呼び手が持つ fake の世界(FakeClaudeWorld — 検の口で家の中身を触る・同じ家の上で process を作り直す〔world.restarted〕
   模擬のため)。responder と world はちょうど 1 つ。
   env / settings = 本番の組(headless-claude-handlers)と同じ意味の家の env(文字列 → 文字列の写像)と CLI の settings(JSON の写像)。
   どちらも必ず渡す(既定の値は無い — 宣言する物の無い呼び手は空の写像を明示で渡す・agora-redesign #3387)。上の層の模擬が、本番と
   同じ手順で決めた env と settings を起動の宣言(層 2 へ渡る ClaudeSessionSpec の home.env と settings)に載せて観測するため
   (agora-redesign #3327)。fake の層 2 はどちらも読まない。どちらも宣言を作る時に凍らせる(ClaudeHome・HeadlessClaudeConfig)。"
  (when (= (is responder None) (is world None))
    (raise (ValueError "fake-headless-claude-handlers は responder と world のちょうど 1 つを受ける")))
  [(fake-claude-code-handler (if (is world None) (FakeClaudeWorld responder) world))
   (headless-claude-handler (HeadlessClaudeConfig (ClaudeHome config-dir env) :settings settings)
                            (HeadlessState))])
