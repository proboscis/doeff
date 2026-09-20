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

(import doeff_agents.sessionhost.effects [BuildHeadlessLaunch])

(import doeff_agents.sessionhost.headless_protocol [
  ClaudeDialogue
  CodexDialogue
  CodexPlan])
(import doeff_agents.sessionhost.impls.claude_code [build-claude-argv])
(import doeff_agents.sessionhost.impls.codex [build-codex-argv])


;; claude の print mode の旗(--verbose の後ろに --include-partial-messages: dotfiles
;; agentcli/headless.py _claude_build_cmd と同じ並び — 本文の途中(delta)を実況に含める。
;; この旗が無い stream は content block が完成した時にしか assistant を出さない)。
;; --input-format stream-json(段 8 lane 4x・agora-redesign #56): stdin を user の行で読む温かい
;; process — result の後も生きて次の行を次の手番にし、手番の途中の行は CLI が次の tool の
;; 境界で走っている手番に注入する(割り込みの本文・実測 2026-09-13 conformance/interrupt-physics.md)。
(setv CLAUDE-HEADLESS-FLAGS
      ["-p" "--input-format" "stream-json" "--output-format" "stream-json" "--verbose" "--include-partial-messages"])
(setv CODEX-APP-SERVER-ARGS ["app-server" "--listen" "stdio://"])


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
  (when (and (= resume-mode "resume") (isinstance conversation dict))
    (setv conv-id (.get conversation "session_id"))
    (when (isinstance conv-id str)
      (.extend argv ["--resume" conv-id])))
  {"argv" argv "dialogue" (ClaudeDialogue)})


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
  (setv argv (+ ["codex"] (! (codex-root-config-args params)) (list CODEX-APP-SERVER-ARGS)))
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
