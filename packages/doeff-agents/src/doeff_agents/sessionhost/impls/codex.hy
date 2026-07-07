;;; codex per-kind defhandler(ADR-DOE-AGENTS-004 R2、C2)。
;;;
;;; protocol 物理の単一の家: oracle = packages/doeff-agentd/src/main.rs
;;; build_codex_argv / trust_codex_workspace / session_launch の
;;; auth-profile gate。conformance 凍結: S11(CODEX_HOME 明示必須 —
;;; tmux 呼び出し前に typed fail)/ S13(-c mcp_servers."doeff_result"
;;; .command/.args 配線・prompt は argv に載らない)。
;;;
;;; substrate-clean 領域: 生 IO 禁止(defsemgrep 執行)。

(require doeff-hy.macros [defk deff <- defhandler])

(import doeff_agents.sessionhost.effects [
  BuildLaunch
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  WireResultChannel
  fs-canonical-path
  fs-compose-home-view
  fs-read-text
  fs-write-text-atomic
  fs-make-dirs
  env-get
  tmux-send-keys])
(import doeff_agents.sessionhost.impls.channel [
  REPORT-RESULT-MCP-SERVER
  result-channel-spec])
(import doeff_agents.sessionhost.impls.markers [classify-output])


;; ---------------------------------------------------------------------------
;; TOML quoting(oracle toml_quoted_key / toml_quoted_string — 常に quote)
;; ---------------------------------------------------------------------------

(deff toml-quoted [value]
  {:pre [(: value str)]
   :post [(: % str)]}
  "TOML 文字列 quote(backslash → \\\\、\" → \\\")。key も value も同物理。"
  (setv escaped (.replace (.replace value "\\" "\\\\") "\"" "\\\""))
  f"\"{escaped}\"")


;; ---------------------------------------------------------------------------
;; argv 物理(oracle build_codex_argv — S13 で oracle green 済みの凍結配線)
;; ---------------------------------------------------------------------------

(deff build-codex-argv [params]
  {:pre [(: params dict)]
   :post [(: % list)]}
  "codex の起動 argv。凍結物理:
   - `codex --yolo` 接頭
   - effort は `-c model_reasoning_effort=\"...\"`(TOML 文字列)
   - caller mcp_servers は `-c mcp_servers.\"name\".url=\"...\"`
   - result channel は `-c mcp_servers.\"doeff_result\".command=` +
     `.args=[...]`(TOML 配列)— caller server → channel → model の順
   - prompt は決して argv に載せない(単発実行になり monitor が
     validate/再促serする前に process が死ぬ)"
  (setv args ["codex" "--yolo"])
  (setv effort (.get params "effort"))
  (when effort
    (.extend args ["-c" f"model_reasoning_effort={(toml-quoted effort)}"]))
  (for [[name url] (.items (.get params "mcp_servers" {}))]
    (.extend args
             ["-c" f"mcp_servers.{(toml-quoted name)}.url={(toml-quoted url)}"]))
  (setv channel (.get params "result_channel"))
  (when channel
    (setv server-key (toml-quoted REPORT-RESULT-MCP-SERVER))
    (.extend args
             ["-c" (+ f"mcp_servers.{server-key}.command="
                      (toml-quoted (get channel "command")))])
    (setv arg-items (.join "," (lfor a (get channel "args") (toml-quoted a))))
    (.extend args ["-c" f"mcp_servers.{server-key}.args=[{arg-items}]"]))
  (setv model (.get params "model"))
  (when model
    (.extend args ["--model" model]))
  args)


;; ---------------------------------------------------------------------------
;; auth-profile gate + trust 物理(oracle session_launch gate /
;; trust_codex_workspace — S11)
;; ---------------------------------------------------------------------------

(deff codex-auth-gate [params]
  {:pre [(: params dict)]
   :post [(: % "None — gate は raise するか通すだけ")]}
  "ADR-DOE-AGENTS-003: agent の auth profile は per-project の決定で、既定は
   無い。auth profile の明示(typed binding か launch command への
   CODEX_HOME= 埋め込み — R7: session_env は非 auth overlay で運搬手段では
   ない)が無い codex launch は tmux に触る前に typed fail(暗黙の ~/.codex
   fallback が共有マシンで個人アカウントの週次クォータを焼いた 2026-07-04 の
   実障害)。"
  (setv binding (.get params "binding"))
  (setv command (or (.get params "command") ""))
  (when (and (is binding None)
             (not-in "CODEX_HOME=" command))
    (raise (RuntimeError
             (+ "session.launch: no agent auth profile for a codex session — "
                "declare the typed `binding` (kind \"codex\", codex_home) or "
                "embed CODEX_HOME= in the explicit launch command. There is NO "
                "default: the implicit ~/.codex fallback selects whatever "
                "account lives there. Declare the auth profile per "
                "project/namespace (ADR-DOE-AGENTS-003 / -004 R7)."))))
  None)


(deff upsert-codex-trust-toml [existing work-dir]
  {:pre [(: existing str) (: work-dir str)]
   :post [(: % str)]}
  "config.toml へ `[projects.\"<work_dir>\"] trust_level = \"trusted\"` を
   idempotent に書く(oracle trust_codex_workspace の行編集物理: 既存 header
   内の trust_level は差し替え・無ければ header 直下へ挿入・header 自体が
   無ければ末尾へ追記)。codex trust は work_dir を canonicalize しない
   (oracle 準拠 — claude と違う点)。"
  (setv quoted (toml-quoted work-dir))
  (setv header f"[projects.{quoted}]")
  (setv trust-line "trust_level = \"trusted\"")
  (setv lines (.splitlines existing))
  (setv header-index None)
  (for [[index line] (enumerate lines)]
    (when (= (.strip line) header)
      (setv header-index index)
      (break)))
  (if (is None header-index)
      (do
        (when (and lines (.strip (get lines -1)))
          (.append lines ""))
        (.append lines header)
        (.append lines trust-line))
      (do
        (setv end (+ header-index 1))
        (while (and (< end (len lines))
                    (not (.startswith (get lines end) "[")))
          (setv end (+ end 1)))
        (setv replaced False)
        (for [i (range (+ header-index 1) end)]
          (when (.startswith (.lstrip (get lines i)) "trust_level")
            (setv (get lines i) trust-line)
            (setv replaced True)
            (break)))
        (when (not replaced)
          (.insert lines (+ header-index 1) trust-line))))
  (setv output (.join "\n" lines))
  (if (.endswith output "\n") output (+ output "\n")))


(defk codex-view-root []
  {:pre []
   :post [(: % str)]}
  "二軸合成 view の置き場: $XDG_STATE_HOME/doeff/agent-homes(store DB と
   同じ解決系 — host.hy xdg-state-home と同写像。impl は substrate-clean
   なので env-get effect で解決する)。"
  (<- xdg (env-get "XDG_STATE_HOME"))
  (<- home (env-get "HOME"))
  (setv state-root
        (cond
          (and (is-not xdg None) (.strip xdg)) xdg
          (and (is-not home None) (.strip home)) f"{home}/.local/state"
          True (raise (RuntimeError
                        (+ "cannot resolve the agent-homes view root: "
                           "neither XDG_STATE_HOME nor HOME is set")))))
  f"{state-root}/doeff/agent-homes")


(defk codex-pre-launch [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "PreLaunchSetup の codex 実体: auth gate(S11、常時)→ trust 書き込み
   (skip_trust_setup で trust だけ飛ばす)。実効 CODEX_HOME は binding
   (R7: auth は typed 構成で運ぶ。v2 二軸形 {auth_file, profile_dir} は
   host が FsComposeHomeView で view を合成して native 形へ合流 — #15)
   → process env fallback(S11 caveat: oracle の trust writer は daemon env
   を fallback 参照する — command 埋め込みの escape hatch 用。binding が
   在れば fallback には決して到達しない)。解決不能(command 埋め込みで env
   にも無い)なら trust は typed skip(書き先が無い)で identity は None。"
  (codex-auth-gate params)
  (setv binding (.get params "binding"))
  (setv codex-home (when (is-not binding None) (.get binding "codex_home")))
  (when (and (is None codex-home) (is-not binding None))
    ;; admission(BINDING-KIND-SHAPES)通過済みの codex binding で codex_home
    ;; が無い = 二軸形。実在検証は FsComposeHomeView が単一の家(不在は
    ;; typed fail — 登録時検証の launch-time 移設、ACP 0040 R2 改訂)。
    (<- view-root (codex-view-root))
    (<- composed (fs-compose-home-view (get binding "auth_file")
                                       (get binding "profile_dir")
                                       view-root))
    (setv codex-home composed))
  (when (is None codex-home)
    (<- from-env (env-get "CODEX_HOME"))
    (setv codex-home from-env))
  (when (and (is-not codex-home None)
             (not (.get params "skip_trust_setup" False)))
    (<- _ (fs-make-dirs codex-home))
    ;; trust 書きは realpath へ。temp+rename(os.replace)は symlink を辿らず
    ;; 置換するため、config.toml が profile bundle への symlink のとき、旧形は
    ;; view 内の link を実ファイル化して registry と fork させていた(oracle の
    ;; plain write は symlink を貫通する — 「temp+rename は観測等価」は symlink
    ;; 先では偽。2026-07-08 #15 接地で発見した cutover 起源の地雷)。realpath に
    ;; 書けば trust は bundle に届き、view は symlink のまま保存される。
    (<- config-path (fs-canonical-path f"{codex-home}/config.toml"))
    (<- raw (fs-read-text config-path))
    (setv updated (upsert-codex-trust-toml (or raw "") (get params "work_dir")))
    (<- _ (fs-write-text-atomic config-path updated ".agentd-tmp")))
  {"CODEX_HOME" codex-home})


;; ---------------------------------------------------------------------------
;; per-kind defhandler(R2: 直接束縛と host 束縛の両方で同一モジュール)
;; ---------------------------------------------------------------------------

(defhandler codex-impl [result-command]
  (BuildLaunch [agent-type params]
    :when (= agent-type "codex")
    (resume (build-codex-argv params)))

  (PreLaunchSetup [agent-type params]
    :when (= agent-type "codex")
    (<- identity (codex-pre-launch params))
    (resume identity))

  (ClassifyPane [agent-type output]
    :when (= agent-type "codex")
    (resume (classify-output output)))

  (WireResultChannel [agent-type session-id socket-path]
    :when (= agent-type "codex")
    (resume (result-channel-spec result-command session-id socket-path)))

  (DeliverMessage [pane-id text]
    ;; live REPL への paste + submit(盲窓物理は substrate 所有)。
    (<- _ (tmux-send-keys pane-id text True True))
    (resume None)))
