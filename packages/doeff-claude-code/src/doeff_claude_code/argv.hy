;;; claude の print mode の起動の argv と、transcript の置き場の綴り — 純関数だけ(この package でここ 1 か所)。
;;;
;;; 移した元: doeff-agents の impls/headless_argv.hy(build-claude-headless・cold-compaction-argv・argv-with-settings-env)と
;;; impls/claude_code.hy(build-claude-argv の旗の並び・transcript の置き場の綴り)。doeff-agents の側は #604 で付け替えるまで残る。
;;;
;;; prompt は argv に載せない(stdin の user の行 — dialogue.hy)。例外は冷えた続きの前の 1 回きりの process だけ。
(require doeff-hy.macros [defk val])
(import collections.abc [Mapping])
(import hashlib)
(import json)
(import os)
(import re)
(import doeff_hy.frozen [FrozenMap thaw-json])
(import doeff_claude_code.values [ClaudeSessionSpec ClaudeHome BypassAll AskHost DenyUnlisted McpSse McpStdio
                                  AutocompactAuto AutocompactTokens FreshSession ResumeSession ForkSession])

(setv STREAM-FLAGS ["-p" "--input-format" "stream-json" "--output-format" "stream-json" "--verbose"
                    "--include-partial-messages"])

;; 手番の外で CLI に仕事をさせない handler の物理: 手番の間に置いた仕事(background の subagent・Bash の run_in_background・
;; Monitor)の完了の合図で、CLI は host の頼みなしに次の手番を起こし model を動かす(#517 の事故の形)。会話の process を手番を
;; またいで生かす形(#3672)では、その手番は誰の物でもない出力になる。背景の仕事を消す環境変数(--settings の env)は Bash の
;; run_in_background の引数だけを消し、Monitor は残す(2.1.289 の実測)ので、Monitor は起こす引数でツールから外す
;; (--disallowedTools は --allowedTools と settings の許可より先に効く)。それでも手番の外で出力した process は handler が降ろす
;; (dialogue.hy の on-record)。
(setv PER-TURN-SETTINGS-ENV (FrozenMap {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" "1"}))
(setv OUTSIDE-TURN-TOOLS #("Monitor"))
(setv DISABLE-ALL-HOOKS "disableAllHooks")


(defn #^ str mangle-cwd [#^ str canonical-cwd]
  "transcript の置き場の dir 名: 実体の path(realpath)の英数字以外を - にした綴り(CLI の規則)。"
  (re.sub "[^A-Za-z0-9]" "-" canonical-cwd))

(defn #^ str transcript-dir [#^ str config-dir #^ str canonical-cwd]
  (.format "{}/projects/{}" config-dir (mangle-cwd canonical-cwd)))

(defn #^ str transcript-path [#^ str config-dir #^ str canonical-cwd #^ str session-id]
  (.format "{}/{}.jsonl" (transcript-dir config-dir canonical-cwd) session-id))

(defn #^ (get dict #(str str)) process-env [#^ ClaudeHome home]
  "起こす process の env: 家の env ちょうど + CLAUDE_CONFIG_DIR(os.environ は足さない)。"
  (| (dict home.env) {"CLAUDE_CONFIG_DIR" home.config-dir}))


(defn #^ list permission-flags [policy]
  (cond
    (isinstance policy BypassAll) ["--dangerously-skip-permissions"]
    (isinstance policy AskHost)
      (+ (if (= policy.mode "default") [] ["--permission-mode" policy.mode])
         ["--permission-prompt-tool" "stdio"])
    (isinstance policy DenyUnlisted)
      (+ ["--permission-prompts" "none"]
         (if policy.allowed-tools ["--allowedTools" (.join "," policy.allowed-tools)] []))
    True (raise (TypeError (.format "許可の方策が閉語彙の外: {!r}" policy)))))

(defn #^ FrozenMap merged-settings [#^ FrozenMap declared #^ FrozenMap env]
  "宣言の settings に env を合流した新しい settings(env の鍵は env の値で上書き — handler の物理が勝つ)。"
  (setv declared-env (.get declared "env"))
  (setv merged-env (FrozenMap (| (if (isinstance declared-env Mapping) (dict declared-env) {}) (dict env))))
  (FrozenMap (| (dict declared) {"env" merged-env})))

(defn #^ list settings-flags [#^ FrozenMap settings]
  "--settings の旗(argv へ JSON を書く境界 — 凍らせた値はここで JSON の形へ戻す)。"
  (if settings ["--settings" (json.dumps (thaw-json settings) :separators #("," ":") :sort-keys True)] []))

(defn #^ list autocompact-flags [window]
  (cond
    (is window None) []
    (isinstance window AutocompactAuto) ["--autocompact" "auto"]
    (isinstance window AutocompactTokens) ["--autocompact" (str window.tokens)]
    True (raise (TypeError (.format "圧縮の閾値が閉語彙の外: {!r}" window)))))

(defn #^ dict mcp-entry [server]
  (if (isinstance server McpSse)
      {"type" "sse" "url" server.url}
      {"type" "stdio" "command" server.command "args" (list server.args) "env" (dict server.env)}))

(defn #^ list mcp-flags [#^ FrozenMap servers]
  "MCP の旗: 手番の MCP は宣言の 1 点が正 — 宣言した server だけを渡し、どちらの場合も --strict-mcp-config で家(configDir)の MCP の
   設定と claude.ai の connector を継がせない。宣言が無い時も付ける: 付けないと子の CLI は家の MCP と connector を読んでから init を
   出し、起動が約 0.6〜0.8 秒遅れる(2026-09-26 の実測)。"
  (if servers
      ["--mcp-config" (json.dumps {"mcpServers" (dfor #(name server) (.items servers) name (mcp-entry server))}
                                  :separators #("," ":") :sort-keys True)
       "--strict-mcp-config"]
      ["--strict-mcp-config"]))

(defn #^ list base-flags [#^ ClaudeSessionSpec spec #^ FrozenMap settings]
  "起動の基礎の旗(許可・settings・effort・model・圧縮・MCP・system prompt の追記)。"
  (+ (permission-flags spec.permission)
     (settings-flags settings)
     (if spec.effort ["--effort" spec.effort] [])
     (if spec.model ["--model" spec.model] [])
     (autocompact-flags spec.autocompact)
     (mcp-flags spec.mcp-servers)
     (if spec.system-prompt-append ["--append-system-prompt" spec.system-prompt-append] [])))

(defn #^ list origin-flags [origin]
  (cond
    (isinstance origin FreshSession) ["--session-id" origin.session-id]
    (isinstance origin ResumeSession) ["--resume" origin.session-id]
    (isinstance origin ForkSession) ["--resume" origin.parent-session-id "--fork-session"]
    True (raise (TypeError (.format "会話の始まり方が閉語彙の外: {!r}" origin)))))

(defn #^ (get list str) launch-flags [#^ (get tuple #(str ...)) command #^ ClaudeSessionSpec spec]
  "会話の process の argv の、会話の始まり方を除いた部分: command(実行ファイルと前置きの引数)+ stream-json の旗 + 基礎の旗 +
   手番の外の仕事を置く道具を外す旗。"
  (+ (list command) STREAM-FLAGS
     (base-flags spec (merged-settings spec.settings PER-TURN-SETTINGS-ENV))
     ["--disallowedTools" (.join "," OUTSIDE-TURN-TOOLS)]))

(defn #^ list launch-argv [#^ tuple command #^ ClaudeSessionSpec spec origin]
  "会話の process の argv: launch-flags + 会話の始まり方。"
  (+ (launch-flags command spec) (origin-flags origin)))

(defk launch-key [#^ (get tuple #(str ...)) command #^ ClaudeSessionSpec spec]
  {:pre [(: command tuple) (: spec ClaudeSessionSpec)] :post [(: % str)] :tags {:context "claude-code" :role "judgment"}}
  "起こした時の条件の鍵: 生きた process を次の手番に使い回してよいかを決める 1 点(#3672)。argv(会話の始まり方を除く — 実行ファイル・
   model・effort・settings・MCP・許可・圧縮・system prompt の追記)・cwd の実体・起こす env(家の置き場と資格を含む)の sha256 の 16 進。
   資格の値は指紋の中にだけ入る(鍵から値は戻らない)。会話の始まり方を除くのは、同じ会話の続き(ResumeSession)だけが使い回すので、
   どの手番の続きかは会話の id が決めるため。指紋の材料の写像は JSON の境界(sha256 へ渡す綴り)。"
  (val material {"argv" (launch-flags command spec)
                 "cwd" (os.path.realpath spec.cwd)
                 "env" (sorted (.items (process-env spec.home)))})
  (.hexdigest (hashlib.sha256 (.encode (json.dumps material :separators #("," ":") :sort-keys True :ensure-ascii False)
                                       "utf-8"))))

(defn #^ list cold-resume-argv [#^ tuple command #^ ClaudeSessionSpec spec #^ str session-id]
  "降りた会話を --resume で起こす前の 1 回きりの print mode の argv(spec.cold-resume-prompt)。
   settings から disableAllHooks を落とす: 1 回きりの命令が plugin の slash command の時、全 hook の無効化は plugin の hook まで
   殺し、命令が組込みの振る舞いに落ちる(doeff-agents 2026-09-22 01:4x の実測)。"
  (setv settings (FrozenMap (gfor #(key value) (.items (merged-settings spec.settings PER-TURN-SETTINGS-ENV))
                                 :if (!= key DISABLE-ALL-HOOKS) #(key value))))
  (+ (list command) (base-flags spec settings) ["-p" spec.cold-resume-prompt "--resume" session-id]))
