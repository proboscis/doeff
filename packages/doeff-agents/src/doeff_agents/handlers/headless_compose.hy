;;; headless の handler の組み立ての部品(composition root が使う)— agora-redesign #604。
;;;
;;; 呼び手(agora など)が doeff_claude_code を import せずに組めるように、doeff-claude-code の handler(層 2)と
;;; headless の adapter(handlers/headless.hy・層 3)の対をここで作る。adapter の module は層 2 の handler も CLI の実行ファイルも
;;; 知らない — 知るのはこの組み立ての module だけ。
;;; どちらの組も外側に doeff-time の時間の handler(本番 = sync-time-handler・模擬 = sim-time-handler)と scheduler を要る。
;;; 本番の組は加えて slog の答え手を要る(層 2 の本番の handler が CLI の起動の計時の行を slog で出す — agora-redesign #3605)。
;;; 並びは with_handlers の順(先頭が外側): 層 2 の handler → adapter(Program に近い側)。
(require doeff-hy.macros [defk])
(import collections.abc [Callable Mapping])
(import doeff_hy.frozen [FrozenMap])
(import doeff_time [sync-time-handler])
(import doeff_claude_code.values [ClaudeHome BypassAll PermissionPolicy])
(import doeff_claude_code.clock [clock-of])
(import doeff_claude_code.handler [ClaudeCodeHost claude-code-handler])
(import doeff_claude_code.fake [FakeClaudeWorld FakeReply fake-claude-code-handler])
;; 模擬の返事(FakeReply)の usage と last-call-usage の型。呼び手が doeff_claude_code を import せずに返事を組めるように、FakeReply と
;; 並べてここから読ませる(agora-redesign #3744)。
(import doeff_claude_code.lines [Usage])
(import doeff_agents.handlers.headless [HeadlessClaudeConfig HeadlessState headless-claude-handler])


(defn #^ list headless-claude-handlers [#^ str config-dir #^ (get FrozenMap str) env
                                        [settings None] [cold-resume-prompt None] [command #("claude")]
                                        * #^ int live-limit]
  "本番の組: doeff-claude-code の本番の handler(会話の CLI を手番をまたいで生かす)+ headless の adapter。
   config-dir / env = claude の家(資格は root が custody から借りて env に置く — 凍らせた写像。dict を渡しても作る時に凍らせる)/
   settings = CLI の settings の JSON(FrozenMap か None)/ live-limit = 同時に生かす CLI の本数の上限(呼び手の宣言から — 既定を
   持たない・#3672 の D2)。借りた資格で CLI を止める刻はこの関数が作る handler には設定せず、引き換えの答え
   (TurnCredential.usable-until)を何も足し引きせずにターンの宣言へ渡す(agora-redesign #3753 (c))。行の時刻は壁の時計で刻む。"
  (setv config (HeadlessClaudeConfig (ClaudeHome config-dir env)
                                     :settings (if (is settings None) (FrozenMap) settings)
                                     :cold-resume-prompt cold-resume-prompt))
  [(claude-code-handler (ClaudeCodeHost (tuple command) (clock-of (sync-time-handler)) live-limit))
   (headless-claude-handler config (HeadlessState))])

(defn #^ list fake-headless-claude-handlers [responder [config-dir "fake-claude-home"] [world None]
                                             * #^ (get Mapping #(str str)) env #^ (get Mapping #(str object)) settings
                                             #^ PermissionPolicy [permission (BypassAll)]]
  "模擬の組: doeff-claude-code の fake の handler + headless の adapter(process も API も使わない)。
   responder = (入力の本文 それまでの入力の tuple) → FakeReply(返事の本文・道具の秒数・許可の問いの要否)。
   world = 呼び手が持つ fake の世界(FakeClaudeWorld — 検の口で家の中身を触る・同じ家の上で process を作り直す〔world.restarted〕
   模擬のため)。responder と world はちょうど 1 つ。
   env / settings = 本番の組(headless-claude-handlers)と同じ意味の家の env(文字列 → 文字列の写像)と CLI の settings(JSON の写像)。
   どちらも必ず渡す(既定の値は無い — 宣言する物の無い呼び手は空の写像を明示で渡す・agora-redesign #3387)。上の層の模擬が、本番と
   同じ手順で決めた env と settings を起動の宣言(層 2 へ渡る ClaudeSessionSpec の home.env と settings)に載せて観測するため
   (agora-redesign #3327)。fake の層 2 はどちらも読まない。どちらも宣言を作る時に凍らせる(ClaudeHome・HeadlessClaudeConfig)。
   permission = 起動の宣言の許可の方策(既定 BypassAll — 本番の adapter と同じ形を上の層の模擬が渡して観測するため・#3753)。"
  (when (= (is responder None) (is world None))
    (raise (ValueError "fake-headless-claude-handlers は responder と world のちょうど 1 つを受ける")))
  [(fake-claude-code-handler (if (is world None) (FakeClaudeWorld responder) world))
   (headless-claude-handler (HeadlessClaudeConfig (ClaudeHome config-dir env) :settings settings :permission permission)
                            (HeadlessState))])


;; --- 層 2 と adapter を別々に作る入口(agora-redesign #3507)--------------------------------------------------------------------------
;; 呼び手が層 2(CLI の process の寿命)と adapter(agent の寿命)を別の段に置くため — 層 2 を土台の外側(本番 = process を起こす土台・
;; 模擬 = 外の世界の相手役)に置き、adapter だけを Program に近い側に置く。上の対の入口と同じ handler を同じ設定で作る(対の入口は変えない)。

(defk claude-process-layer [command live-limit]
  {:pre [(: command tuple) (all (gfor part command (isinstance part str))) (: live-limit int)]
   :post [(: % Callable)]}
  "層 2 の本番の handler だけ(会話の CLI を手番をまたいで生かす — headless-claude-handlers の対の 1 つ目と同じ物)を作るため。command =
   CLI の実行の引数の頭・live-limit = 同時に生かす CLI の本数の上限(呼び手の宣言から・#3672 の D2)。資格で CLI を止める刻はターンの
   宣言(ClaudeSessionSpec の credential-usable-until)が運ぶ(agora-redesign #3753 (c))。行の時刻は壁の時計で刻む。外側に doeff-time
   の時間の handler・scheduler・slog の答え手を要る。"
  (claude-code-handler (ClaudeCodeHost command (clock-of (sync-time-handler)) live-limit)))

(defk fake-claude-process-layer [responder world]
  {:pre [(: responder (| Callable None)) (: world (| FakeClaudeWorld None))] :post [(: % Callable)]}
  "層 2 の fake の handler だけ(process も API も使わない — fake-headless-claude-handlers の対の 1 つ目と同じ物)を作るため。
   responder / world の意味と「ちょうど 1 つ」は対の入口と同じ。"
  (when (= (is responder None) (is world None))
    (raise (ValueError "fake-claude-process-layer は responder と world のちょうど 1 つを受ける")))
  (fake-claude-code-handler (if (is world None) (FakeClaudeWorld responder) world)))

(defk claude-adapter [config-dir env settings cold-resume-prompt permission]
  {:pre [(: config-dir str) (: env Mapping) (: settings Mapping) (: cold-resume-prompt (| str None)) (: permission PermissionPolicy)]
   :post [(: % Callable)]}
  "headless の adapter だけ(doeff-agents の公開 effect を層 2 の effect へ写す — 対の入口の 2 つ目と同じ物。層 2 が本物でも fake でも同じ
   adapter)を作るため。config-dir / env = claude の家(凍らせる)・settings = CLI の settings の JSON の写像・cold-resume-prompt = 冷えた
   続きの前の 1 回きりの process に渡す文(None = 渡さない)・permission = 起動の宣言の許可の方策(HomeSettings = 設定 dir の settings.json の
   permissions に任せて許可の旗を付けない — #3753)。"
  (headless-claude-handler (HeadlessClaudeConfig (ClaudeHome config-dir env) :settings settings :cold-resume-prompt cold-resume-prompt
                                                 :permission permission)
                           (HeadlessState)))
