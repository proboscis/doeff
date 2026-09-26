;;; headless backend の起動 argv(agora-redesign #37・段 2 lane 2d)— print mode の唯一の家。
;;;
;;; sessionhost の tui の adapter は claude を print / one-shot mode で起こしてはならない
;;; (semgrep doeff-agents-no-claude-print-mode: 1 手番で process が死に、monitor が result を
;;; validate / 再促できない)。headless backend はその例外を **この file 1 つ**に閉じる:
;;; 手番の終わり(stream-json の result の行)と続き(--resume)を substrate(headless_process /
;;; headless_protocol)が持つので、one-shot でも session は会話の資源として温かく続く。
;;; semgrep の rule はこの file と headless の substrate / program / 検だけを除外する
;;; (ADR-DOE-AGENTS-012 R11・law print-mode-has-one-home-the-headless-backend)。
;;;
;;; 基礎の旗(--dangerously-skip-permissions / --settings / effort / model / mcp-config)は
;;; tui の argv builder(impls/claude_code.hy build-claude-argv・impls/codex.hy
;;; build-codex-argv)と共有し、並行実装を作らない(protocol-physics-has-one-home)。
;;; prompt は argv に載せない(stdin の作法 = headless_protocol の Dialogue.turn)。
;;;
;;; substrate-clean 領域: 生 IO 禁止(defsemgrep 執行)。ここは純粋な組み立てだけ。

(require doeff-hy.macros [defk defhandler <-])

(import json)
(import json)

(import doeff_agents.sessionhost.headless_effects [BuildHeadlessLaunch])

(import doeff_agents.sessionhost.headless_protocol [
  ClaudeDialogue
  CodexDialogue
  CodexPlan])
(import doeff_agents.sessionhost.impls.claude_code [build-claude-argv])
(import doeff_agents.sessionhost.impls.codex [build-codex-argv])
(import doeff_agents.sessionhost.drivers [DRIVER-EXECUTABLE])


;; claude の print mode の旗(--verbose の後ろに --include-partial-messages: dotfiles
;; agentcli/headless.py _claude_build_cmd と同じ並び — 本文の途中(delta)を実況に含める。
;; この旗が無い stream は content block が完成した時にしか assistant を出さない)。
;; --input-format stream-json(段 8 lane 4x・agora-redesign #56): stdin を user の行で読む温かい
;; process — result の後も生きて次の行を次の手番にし、手番の途中の行は CLI が次の tool の
;; 境界で走っている手番に注入する(割り込みの本文・実測 2026-09-13 conformance/interrupt-physics.md)。
(setv CLAUDE-HEADLESS-FLAGS
      ["-p" "--input-format" "stream-json" "--output-format" "stream-json" "--verbose" "--include-partial-messages"])
(setv CODEX-APP-SERVER-ARGS ["app-server" "--listen" "stdio://"])

;; 冷えた再開の前の圧縮(fast-jev-compaction plugin・2026-09-22): 続きの手番の process は
;; 最初の model 呼び出しの前に plugin が圧縮できない(engine は起動時と prompt の送信時の
;; hook からの圧縮を断る)。⇒ 起動側が先に `-p "/compact fast-jev-if-cold" --resume <sid>` を
;; 1 回走らせ、plugin が自分の状態(TTL・profile・model・機体)で温冷を決める。温ければ何も
;; 起きず(model の呼び出し 0 回)、冷えていれば古い tool の結果だけを消す(要約文は書かない)。
(setv CLAUDE-COLD-COMPACTION-PROMPT "/compact fast-jev-if-cold")

;; 手番の中で生まれた仕事を手番の外へ持ち越させない(2026-09-23・card ki-266beb90dddf / ki-36a5b0e70f04 の計画の会話の実弾):
;; 1 手番 1 process(R48・law claude-turn-end-is-process-end)の器は result の行で process を降ろす。ところが CLI は
;; Agent tool の subagent を既定で background に回し(run_in_background)、model は「盲検 A・B の返答待ち」の
;; WAIT: work で手番を終える — 降ろした process と一緒に subagent が死に、完了の合図で起きる続きの手番は誰も起こさない
;; (依頼は開いたまま担い手は静止する)。⇒ headless の claude は background の仕事そのものを持てない形で起こす:
;; CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1 で Agent は同期(返答が同じ手番の tool の結果に返る)・Bash の
;; run_in_background と Monitor は出ない(実測 2026-09-23: 旗なし = "Async agent launched" の後に result、
;; 旗あり = subagent の報告が tool の結果に返ってから result)。運ぶ口は --settings の env(flagSettings・1 つの
;; --settings に合流 — build-claude-argv の不変量)。
(setv CLAUDE-HEADLESS-SETTINGS-ENV {"CLAUDE_CODE_DISABLE_BACKGROUND_TASKS" "1"})


(defk argv-with-settings-env [argv env]
  {:pre [(: argv list) (: env dict)]
   :post [(: % list)]}
  "argv の 1 つの --settings の env へ env を合流した新しい argv(無ければ --settings を足す・純関数)。
   既に在る env の鍵は env の値で上書きする(この宣言が headless の物理 — 席の settings は hook だけを持つ R13)。"
  (setv out (list argv))
  (if (in "--settings" out)
      (do
        (setv index (+ (.index out "--settings") 1))
        (setv settings (json.loads (get out index)))
        (setv merged (dict (or (.get settings "env") {})))
        (.update merged env)
        (setv (get settings "env") merged)
        (setv (get out index) (json.dumps settings :separators #("," ":"))))
      (.extend out ["--settings" (json.dumps {"env" (dict env)} :separators #("," ":"))]))
  out)
(import doeff_agents.sessionhost.impls.fast_jev [fast-jev-compaction-enabled])


(defk build-claude-headless [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "claude の headless の起動: {argv, dialogue}。
   - argv = `claude` + print mode の旗 + tui と同じ基礎の旗(build-claude-argv の並び —
     --dangerously-skip-permissions / --settings / --effort / --model / --mcp-config)
   - 会話の最初の手番は `--session-id <sid>`(build-claude-argv が conversation から
     付ける — 登記時鋳造の id が初手番から会話の id)、続きの手番(resume_mode =
     \"resume\")は `--resume <sid>`
   - dialogue = ClaudeDialogue(1 手番 1 process・prompt は stdin の user の行・result で手番の
     終わり = 対話の終わり〔器が EOF で降ろす・段 12 lane 12e #517〕・途中の行は割り込みの本文・
     次の手番は --resume の新しい process)"
  (setv base-params (dict params))
  (setv resume-mode (.get params "resume_mode"))
  (setv conversation (.get params "conversation"))
  (when (= resume-mode "resume")
    ;; --session-id は fresh だけ(build-claude-argv は resume_mode が在ると付けない)
    (.pop base-params "conversation" None))
  (<- base (build-claude-argv base-params))
  (setv argv (+ [(get base 0)] (list CLAUDE-HEADLESS-FLAGS) (list (cut base 1 None))))
  (when (.get params "cache_maintenance" False)
    ;; 専用pingがStop/WAIT hookで通常業務へ転化しない。その他のsettings・model・toolsは保存する。
    ;; 通常turnのsession_hooks契約を緩めず、この明示された専用経路だけで合成する。
    (if (in "--settings" argv)
      (do
        (setv index (+ (.index argv "--settings") 1))
        (setv settings (json.loads (get argv index)))
        (setv (get settings "disableAllHooks") True)
        (setv (get argv index) (json.dumps settings :separators #("," ":"))))
      (.extend argv ["--settings" (json.dumps {"disableAllHooks" True})]))
    (.extend argv ["--max-turns" "1"]))
  (<- argv (argv-with-settings-env argv CLAUDE-HEADLESS-SETTINGS-ENV))
  (setv built {"argv" argv "dialogue" (ClaudeDialogue)})
  (when (and (= resume-mode "resume") (isinstance conversation dict))
    (setv conv-id (.get conversation "session_id"))
    (when (isinstance conv-id str)
      (.extend argv ["--resume" conv-id])
      ;; 冷えた再開の前の圧縮の argv: 同じ基礎の旗 + print mode の prompt 1 つ + 同じ --resume。
      ;; stream-json の旗は載せない(stdin の対話ではなく 1 回きり)。
      ;; ⚠ --settings の disableAllHooks は載せない(実測 2026-09-22 01:4x): disableAllHooks は plugin の hook
      ;;   まで殺し、`/compact` が組込みの要約(model 1 回・会話全体・3.5 分)に落ちる。この 1 回きりの process は
      ;;   session_hooks = inherit と同じ形で起きる(config-dir の所有者の hook 層は spawn env の
      ;;   AGENT_SESSION_CLASS で self-gate する契約 — 手番と同じ実効 env で起こす)。
      (setv (get built "cold_compaction_argv") (! (cold-compaction-argv base conv-id)))))
  built)


(defk cold-compaction-argv [base conv-id]
  {:pre [(: base list) (> (len base) 0) (: conv-id str)]
   :post [(: % list)]}
  "続きの手番の基礎の argv(build-claude-argv の並び)から、再開前の圧縮の 1 回きりの argv を組む(純関数)。
   --settings から disableAllHooks を落とす(他の欄 — 自動記憶の置き場など — は保ち、空になれば旗ごと消す)。
   末尾に print mode の prompt と --resume。"
  (setv args (list base))
  (setv out [])
  (setv i 0)
  (while (< i (len args))
    (setv arg (get args i))
    (if (and (= arg "--settings") (< (+ i 1) (len args)))
        (do
          (setv settings (try (json.loads (get args (+ i 1))) (except [Exception] {})))
          (when (not (isinstance settings dict)) (setv settings {}))
          (.pop settings "disableAllHooks" None)
          (when settings
            (.extend out ["--settings" (json.dumps settings :separators #("," ":"))]))
          (setv i (+ i 2)))
        (do
          (.append out arg)
          (setv i (+ i 1)))))
  (+ out ["-p" CLAUDE-COLD-COMPACTION-PROMPT "--resume" conv-id]))


(defk codex-root-config-args [params]
  {:pre [(: params dict)]
   :post [(: % list)]}
  "build-codex-argv の `-c key=value` の対だけ(effort・caller mcp・result channel)。
   root の旗は subcommand(app-server)の前に置く。--model は thread の params で運ぶ
   (app-server は model を thread/start の欄で受ける)ので argv から外す。tui の接頭
   `--yolo` もここで落ちる(`-c` の対だけを拾う)— headless の方策は params(下)。"
  (setv base (build-codex-argv params))
  (setv out [])
  (setv index 0)
  (while (< index (len base))
    (setv arg (get base index))
    (cond
      (= arg "-c")
      (do (.extend out ["-c" (get base (+ index 1))])
          (+= index 2))
      (= arg "--model")
      (+= index 2)
      True
      (+= index 1)))
  out)


(defk build-codex-headless [params]
  {:pre [(: params dict)]
   :post [(: % dict)]}
  "codex の headless の起動: {argv, dialogue}。
   - argv = `codex <-c …> app-server --listen stdio://`。**全面許可の旗(--yolo / --sandbox /
     -a / --full-auto)は argv に載せない**: app-server では承認と sandbox の方策は thread /
     turn の params が正本(headless_protocol.THREAD_FULL_ACCESS / TURN_FULL_ACCESS — dotfiles
     codex_shim.full_access_app_server と同じ綴り)で、PATH の codex が dotfiles の router shim
     の機体では旗が政策違反として exit 2 で拒まれる(2026-09-12 の本番の実弾・agora-redesign
     #37 lane 2d-2)。shim が足す正規形 --dangerously-bypass-approvals-and-sandbox も載せない
     (shim 自身の不変量)。`-c` の対(effort / mcp / result channel)は tui と共有のまま。
   - dialogue = CodexDialogue(温かい process・initialize → thread/start | thread/resume →
     turn/start・turn/completed で終わり・turn/interrupt で割り込み)。続きの手番
     (resume_mode = \"resume\")は conversation.session_id を thread の id として resume。"
  ;; argv[0] = 種類の実行ファイルの名の唯一の定義(drivers.DRIVER-EXECUTABLE・ADR-DOE-AGENTS-012 R61)。
  (setv argv (+ [(get DRIVER-EXECUTABLE "codex")] (! (codex-root-config-args params)) (list CODEX-APP-SERVER-ARGS)))
  (setv conversation (.get params "conversation"))
  (setv resume-id
        (if (and (= (.get params "resume_mode") "resume") (isinstance conversation dict))
            (.get conversation "session_id")
            None))
  (setv plan (CodexPlan :cwd (str (.get params "work_dir" ""))
                        :model (.get params "model")
                        :effort (.get params "effort")
                        :resume-thread-id (if (isinstance resume-id str) resume-id None)))
  {"argv" argv "dialogue" (CodexDialogue plan)})


;; ---------------------------------------------------------------------------
;; defhandler(kind 別の dispatch — claude_code.hy / codex.hy は import 元なので、
;; 循環を避けてここに置く。設置は host.hy run-hosted の backend=headless の枝)
;; ---------------------------------------------------------------------------

(defk build-headless [agent-type params]
  {:pre [(: agent-type str) (: params dict)]
   :post [(: % dict)]}
  "kind → headless の起動(閉語彙 claude | codex — 他は loud に断る)。"
  (cond
    (= agent-type "claude") (! (build-claude-headless params))
    (= agent-type "codex") (! (build-codex-headless params))
    True (raise (RuntimeError
                  f"headless backend has no launch physics for agent_type {agent-type !r}"))))


(defhandler headless-argv-impl []
  (BuildHeadlessLaunch [agent-type params]
    (<- built (build-headless agent-type params))
    (resume built)))
