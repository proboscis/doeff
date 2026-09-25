;;; claude の print mode の起動の argv と、transcript の置き場の綴り — 純関数だけ(この package でここ 1 か所)。
;;;
;;; 移した元: doeff-agents の impls/headless_argv.hy(build-claude-headless・cold-compaction-argv・argv-with-settings-env)と
;;; impls/claude_code.hy(build-claude-argv の旗の並び・transcript の置き場の綴り)。doeff-agents の側は #604 で付け替えるまで残る。
;;;
;;; prompt は argv に載せない(stdin の user の行 — dialogue.hy)。例外は冷えた続きの前の 1 回きりの process だけ。
(import json)
(import re)
(import doeff_claude_code.values [ClaudeSessionSpec ClaudeHome BypassAll AskHost DenyUnlisted McpSse McpStdio
                                  AutocompactAuto AutocompactTokens FreshSession ResumeSession ForkSession])

(setv STREAM-FLAGS ["-p" "--input-format" "stream-json" "--output-format" "stream-json" "--verbose"
                    "--include-partial-messages"])

;; 手番ごとに process を降ろす handler の物理: CLI に手番の外へ持ち越す仕事(background の subagent・Bash の
;; run_in_background・Monitor)を持たせない。持たせると、降ろした process と一緒にその仕事が死に、完了の合図で起きる
;; 続きの手番は誰も起こさない(doeff-agents 2026-09-23 の実測)。運ぶ口は --settings の env。
(setv PER-TURN-SETTINGS-ENV {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" "1"})
(setv DISABLE-ALL-HOOKS "disableAllHooks")


(defn #^ str mangle-cwd [#^ str canonical-cwd]
  "transcript の置き場の dir 名: 実体の path(realpath)の英数字以外を - にした綴り(CLI の規則)。"
  (re.sub "[^A-Za-z0-9]" "-" canonical-cwd))

(defn #^ str transcript-dir [#^ str config-dir #^ str canonical-cwd]
  (.format "{}/projects/{}" config-dir (mangle-cwd canonical-cwd)))

(defn #^ str transcript-path [#^ str config-dir #^ str canonical-cwd #^ str session-id]
  (.format "{}/{}.jsonl" (transcript-dir config-dir canonical-cwd) session-id))

(defn #^ dict process-env [#^ ClaudeHome home]
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

(defn #^ dict merged-settings [#^ dict declared #^ dict env]
  "宣言の settings に env を合流した新しい dict(env の鍵は env の値で上書き — handler の物理が勝つ)。"
  (setv merged (dict declared))
  (setv declared-env (.get merged "env"))
  (setv (get merged "env") (| (if (isinstance declared-env dict) (dict declared-env) {}) env))
  merged)

(defn #^ list settings-flags [#^ dict settings]
  (if settings ["--settings" (json.dumps settings :separators #("," ":") :sort-keys True)] []))

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

(defn #^ list mcp-flags [#^ dict servers]
  (if servers
      ["--mcp-config" (json.dumps {"mcpServers" (dfor #(name server) (.items servers) name (mcp-entry server))}
                                  :separators #("," ":") :sort-keys True)
       "--strict-mcp-config"]
      []))

(defn #^ list base-flags [#^ ClaudeSessionSpec spec #^ dict settings]
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

(defn #^ list launch-argv [#^ tuple command #^ ClaudeSessionSpec spec origin]
  "手番の process の argv: command(実行ファイルと前置きの引数)+ stream-json の旗 + 基礎の旗 + 会話の始まり方。"
  (+ (list command) STREAM-FLAGS
     (base-flags spec (merged-settings spec.settings PER-TURN-SETTINGS-ENV))
     (origin-flags origin)))

(defn #^ list cold-resume-argv [#^ tuple command #^ ClaudeSessionSpec spec #^ str session-id]
  "降りた会話を --resume で起こす前の 1 回きりの print mode の argv(spec.cold-resume-prompt)。
   settings から disableAllHooks を落とす: 1 回きりの命令が plugin の slash command の時、全 hook の無効化は plugin の hook まで
   殺し、命令が組込みの振る舞いに落ちる(doeff-agents 2026-09-22 01:4x の実測)。"
  (setv settings (dfor #(key value) (.items (merged-settings spec.settings PER-TURN-SETTINGS-ENV))
                       :if (!= key DISABLE-ALL-HOOKS) key value))
  (+ (list command) (base-flags spec settings) ["-p" spec.cold-resume-prompt "--resume" session-id]))
