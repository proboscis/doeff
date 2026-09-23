;;; 直接束縛 deftest: per-kind defhandler(claude-code / codex)の protocol 物理検証
;;; (DOE-004 C2)。
;;;
;;; oracle parity の対象(conformance README の凍結物理):
;;;   - S13: argv 配線(claude --settings disableAllHooks は既定のみ —
;;;     session_hooks=inherit で外れる。+ --mcp-config stdio +
;;;     --strict-mcp-config / codex -c mcp_servers."doeff_result".command/.args。
;;;     prompt は argv に載らない・print mode 不使用)
;;;   - S12: claude trust pre-seed(canonicalized work_dir key・temp+rename)
;;;   - S11: codex CODEX_HOME ゲート(tmux 効果ゼロで typed fail)
;;;   - F-* marker 分類 + R9 dialog 検出(dismiss keys は impl 所有)
;;;
;;; fake substrate(dict-backed fs / 台本 env / 記録 tmux)で impl handler を
;;; 直接束縛する。生 IO ゼロ。oracle: agentd-rust-final:src/main.rs
;;; build_claude_argv / build_codex_argv / trust_*_workspace /
;;; output_has_* / dismiss_*。

(require doeff-hy.macros [deftest defk deff <- defhandler])

(import json)
(import pytest)
(import doeff [EffectBase run])

(import doeff_agents.sessionhost.effects [
  PaneObservation
  BuildLaunch
  FsComposeHomeView
  PreLaunchSetup
  ClassifyPane
  DeliverMessage
  WireResultChannel
  TmuxNewSession
  TmuxSendKeys
  FsCanonicalPath
  FsReadText
  FsRemoveFile
  FsWriteTextAtomic
  FsMakeDirs
  EnvGet
  build-launch
  build-resume
  pre-launch-setup
  classify-pane
  deliver-message
  wire-result-channel])
(import doeff_agents.sessionhost.impls.claude_code [claude-code-impl autocompact-arg-pair-ok])
(import doeff_agents.sessionhost.impls.codex [codex-impl])
(import doeff_agents.sessionhost.impls.markers [is-api-limit-refusal api-limit-scope-of api-limit-resets-at
                                                  API-LIMIT-SCOPE-ACCOUNT API-LIMIT-SCOPE-MODEL API-LIMIT-SCOPE-UNKNOWN
                                                  API-LIMIT-SCOPE-TABLE API-LIMIT-SCOPE-OTHERWISE])
;; 器 → 制御面の一周(card acp:kanban-issue:ki-5d4849d22a4e): 器の側の 1 点が当てた cause を、制御面の 1 点が条件に組む。
(import doeff_agents.sessionhost.headless [headless-turn-limit-cause])
(import doeff_agents.sessionhost.headless_protocol [Verdict])
(import doeff_agents.sessionhost.store [terminal-cause-to-dict])
(import doeff_agents.sessionhost.acp.judgment [provider-limit-condition-of])
(import doeff_agents.sessionhost.acp.effects [PROVIDER-LIMIT-SCOPES PROVIDER-LIMIT-REASONS])


;; ---------------------------------------------------------------------------
;; fake substrate world(impls は substrate effect しか yield できない —
;; ここで受けて記録する。tmux-calls の空を assert することが S11 の
;; 「tmux 呼び出し前に fail」の直接束縛版)
;; ---------------------------------------------------------------------------

(defclass ImplWorld []
  (defn __init__ [self]
    (setv self.fs {})              ;; path -> text
    (setv self.dirs [])            ;; FsMakeDirs の記録
    (setv self.atomic-writes [])   ;; [(path, tmp-suffix)]
    (setv self.removed [])         ;; FsRemoveFile の記録(card ki-6b5c4b270ca0)
    (setv self.env {})             ;; EnvGet 台本(process env fallback)
    (setv self.canonical {})       ;; path -> canonical path 台本
    (setv self.tmux-calls [])      ;; あらゆる tmux effect の記録
    (setv self.composed [])        ;; FsComposeHomeView の記録(二軸形の家)
    (setv self.sent-keys [])))     ;; TmuxSendKeys の記録


(defhandler fake-impl-substrate [world]
  (FsCanonicalPath [path]
    (resume (.get world.canonical path path)))
  (FsReadText [path]
    (resume (.get world.fs path)))
  (FsWriteTextAtomic [path text tmp-suffix]
    (.append world.atomic-writes #(path tmp-suffix))
    (setv (get world.fs path) text)
    (resume None))
  (FsRemoveFile [path]
    ;; card acp:kanban-issue:ki-6b5c4b270ca0: 名指した 1 file を落とす(不在は成功)。
    (.append world.removed path)
    (resume (is-not (.pop world.fs path None) None)))
  (FsMakeDirs [path]
    (.append world.dirs path)
    (resume None))
  (FsComposeHomeView [auth-file profile-dir view-root]
    ;; 二軸形の家の合成(#15)。実体は substrate の compose-home-view —
    ;; ここは決定的な view の path を返すだけ(従量課金の便 lane A の metered codex が
    ;; 通る経路)。
    (.append world.composed #(auth-file profile-dir view-root))
    (resume f"{view-root}/composed-view"))
  (EnvGet [name]
    (resume (.get world.env name)))
  (TmuxNewSession [session-name work-dir env]
    (.append world.tmux-calls #("new-session" session-name))
    (resume "%99"))
  (TmuxSendKeys [pane-id text literal submit]
    (.append world.tmux-calls #("send-keys" pane-id))
    (.append world.sent-keys #(pane-id text literal submit))
    (resume None)))


(defn base-params [#** overrides]
  (setv params {"session_id" "s1"
                "session_name" "doeff-s1"
                "agent_type" "codex"
                "work_dir" "/work/dir"
                "lifecycle" "run_to_completion"
                "session_env" {}
                "prompt" "do the task"
                "command" None
                "expected_result" {"type" "object"}
                "model" None
                "effort" None
                "mcp_servers" {}
                "result_channel" None
                "socket_path" "/tmp/agentd.sock"
                "skip_trust_setup" False})
  (.update params overrides)
  params)


(defn channel-spec []
  {"command" "/opt/doeff-sessionhost"
   "args" ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]})


(defk perform [op]
  {:pre [(: op EffectBase)] :post [(: % "effect の handler 解釈結果")]}
  "bare effect を 1 回 yield する最小 program(handler は program を包むため)。"
  (<- result op)
  result)

(defk run-claude [world op]
  {:pre [(: world ImplWorld) (: op EffectBase)]
   :post [(: % "impl handler 実行結果")]}
  (<- result ((fake-impl-substrate world)
              ((claude-code-impl "/opt/doeff-sessionhost") (perform op))))
  result)

(defk run-codex [world op]
  {:pre [(: world ImplWorld) (: op EffectBase)]
   :post [(: % "impl handler 実行結果")]}
  (<- result ((fake-impl-substrate world)
              ((codex-impl "/opt/doeff-sessionhost") (perform op))))
  result)


;; ---------------------------------------------------------------------------
;; BuildLaunch — S13 argv 配線 parity(oracle build_claude_argv / build_codex_argv)
;; ---------------------------------------------------------------------------

(deftest test-claude-argv-golden-wiring
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :model "claude-fable-5"
                            :effort "high"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  ;; 凍結接頭(49b3549b 傷跡: hooks 無効化は agent contract の一部)
  (assert (= (cut argv 0 4)
             ["claude" "--dangerously-skip-permissions"
              "--settings" "{\"disableAllHooks\":true}"]))
  ;; effort → model の順(oracle 順序)
  (assert (= (.index argv "--effort") 4))
  (assert (= (get argv 5) "high"))
  (assert (= (get argv (+ (.index argv "--model") 1)) "claude-fable-5"))
  ;; doeff_result stdio server が --mcp-config JSON に載る + strict
  (setv mcp-json (get argv (+ (.index argv "--mcp-config") 1)))
  (setv mcp (json.loads mcp-json))
  (setv server (get mcp "mcpServers" "doeff_result"))
  (assert (= (get server "type") "stdio"))
  (assert (= (get server "command") "/opt/doeff-sessionhost"))
  (assert (= (get server "args")
             ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]))
  (assert (in "--strict-mcp-config" argv))
  ;; prompt は argv に載らない・print mode 不使用(launch invariant)
  (assert (not-in "do the task" argv))
  (assert (not-in "-p" argv))
  (assert (not-in "--print" argv)))


(deftest test-claude-argv-always-declares-the-compaction-threshold
  ;; 会話の圧縮の閾値(設計記録 docs/design/auto-compact-window・operator 指示 2026-09-18)。根 = 起こす argv が閾値を
  ;; 何も名乗らず、claude が自分の窓(1M)いっぱいまで畳まずに伸びていた。実測 2026-09-18:
  ;; 1 手番の平均の文脈 550k・最大 967k・600k 超の手番が費用の 59%。
  ;; ⇒ effort(名乗らない席は旗を出さない)と**逆に**、閾値は常に載せる。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :model "claude-fable-5-1"
                            :effort "xhigh"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (in "--autocompact" argv) argv)
  ;; 会話が名乗らない拍は床(400k)。
  (assert (= (get argv (+ (.index argv "--autocompact") 1)) "400000") argv)
  ;; 位置 = model の後ろ・mcp の前(凍結接頭と `--effort` の位置を動かさない)。
  (assert (= (cut argv 0 4)
             ["claude" "--dangerously-skip-permissions"
              "--settings" "{\"disableAllHooks\":true}"]))
  (assert (= (.index argv "--effort") 4) argv)
  (assert (< (.index argv "--model") (.index argv "--autocompact")) argv)
  (assert (< (.index argv "--autocompact") (.index argv "--mcp-config")) argv))


(deftest test-claude-argv-carries-the-threshold-the-conversation-declared
  ;; 役ごとの値(lead は広く・worker は狭く)は**会話の宣言**が運ぶ。agentd は
  ;; charter.auto_compact_window を読むだけで、役の表を code に持たない。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :model "claude-fable-5-1"
                            :auto_compact_window 200000
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (= (get argv (+ (.index argv "--autocompact") 1)) "200000") argv))


(defn bad-threshold-params [bad]
  ;; 幅の外・読めない値を宣言した席の params(反例の入力を 1 点で作る)。
  (base-params :agent_type "claude"
               :model "claude-fable-5-1"
               :auto_compact_window bad
               :result_channel (channel-spec)))


(defn assert-degraded-to-auto [argv bad]
  (setv value (get argv (+ (.index argv "--autocompact") 1)))
  (assert (= value "auto") #(bad value)))


(deftest test-claude-argv-never-carries-a-threshold-that-kills-the-turn
  ;; 反例(claude 2.1.274 実測): 幅(100k〜1M)の外・読めない値を argv に載せると
  ;; argv 解釈の段で死に、stream-json の行を 1 つも吐かない ── 手番が丸ごと消える。
  ;; ⇒ 縮退の向きは常に `auto`(CLI 自身の窓に合わせた調整)で、縮退したことは
  ;; argv 自身が名乗る(`ps` に `--autocompact auto` と出る = 外から読める)。
  ;; ⚠ 反例は 1 つずつ束縛する(手番を起こす効果は for の本体では走らない)。
  (<- low (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params 50000))))
  (assert-degraded-to-auto low 50000)
  (<- high (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params 2000000))))
  (assert-degraded-to-auto high 2000000)
  (<- word (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params "nope"))))
  (assert-degraded-to-auto word "nope")
  (<- flag (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params True))))
  (assert-degraded-to-auto flag True)
  (<- shape (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params {}))))
  (assert-degraded-to-auto shape {})
  (<- negative (run-claude (ImplWorld) (build-launch "claude" (bad-threshold-params -1))))
  (assert-degraded-to-auto negative -1))


(defn threshold-of [argv]
  (get argv (+ (.index argv "--autocompact") 1)))


(deftest test-the-threshold-on-the-argv-is-always-inside-the-band
  ;; 盲検の反例 B(2026-09-18)。「Sonnet / Haiku は窓が 200k しかないので、モデルの窓の
  ;; 40% で頭打ちにしてほしい」という**もっともらしい**要求を素直に実装すると、
  ;; 200000 × 0.4 = 80000 が argv に載る ── 幅(100k〜1M)の下なので、その席の手番は
  ;; argv 解釈の段で死に、stream-json を 1 行も吐かない。反例は関門の関数に 1 文字も触らず、
  ;; 単体の検(model が Fable 固定で天井 = 床)も semgrep も素通しした。
  ;; ⇒ 不変量は「導出点の戻り」ではなく「**argv に出る値**」に掛ける。
  ;;    検も model を振って、出た値そのものを見る。
  (setv world (ImplWorld))
  (<- fable (run-claude world (build-launch "claude"
              (base-params :agent_type "claude" :model "claude-fable-5-1"
                           :result_channel (channel-spec)))))
  (assert (autocompact-arg-pair-ok ["--autocompact" (threshold-of fable)]) fable)
  (<- sonnet (run-claude (ImplWorld) (build-launch "claude"
               (base-params :agent_type "claude" :model "claude-sonnet-5"
                            :auto_compact_window 200000
                            :result_channel (channel-spec)))))
  (assert (autocompact-arg-pair-ok ["--autocompact" (threshold-of sonnet)]) sonnet)
  (<- haiku (run-claude (ImplWorld) (build-launch "claude"
              (base-params :agent_type "claude" :model "claude-haiku-4-5-20251001"
                           :result_channel (channel-spec)))))
  (assert (autocompact-arg-pair-ok ["--autocompact" (threshold-of haiku)]) haiku)
  (<- unknown (run-claude (ImplWorld) (build-launch "claude"
                (base-params :agent_type "claude" :model "some-unknown-model"
                             :auto_compact_window 150000
                             :result_channel (channel-spec)))))
  (assert (autocompact-arg-pair-ok ["--autocompact" (threshold-of unknown)]) unknown)
  ;; 出口の関門そのもの: 幅の外の値は「並びとして不正」と判じる。
  (assert (not (autocompact-arg-pair-ok ["--autocompact" "80000"])))
  (assert (not (autocompact-arg-pair-ok ["--autocompact" "2000000"])))
  (assert (autocompact-arg-pair-ok ["--autocompact" "auto"])))


(deftest test-claude-resume-argv-declares-the-threshold-too
  ;; 起こす腕は launch だけではない — 蘇生の手番で閾値が落ちると、**長く続いている
  ;; 会話ほど**窓の上限任せに戻る(いちばん太い席から先に漏れる形)。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :model "claude-fable-5-1"
                            :auto_compact_window 200000
                            :resume_mode "resume"
                            :conversation {"session_id" "c-old"}
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-resume "claude" params)))
  (assert (= (get argv (+ (.index argv "--autocompact") 1)) "200000") argv)
  (assert (= (cut argv -2 None) ["--resume" "c-old"]) argv))


(deftest test-claude-argv-session-hooks-inherit
  ;; session_hooks=inherit(daemon env knob DOEFF_AGENTD_SESSION_HOOKS —
  ;; launch.hy が params へ写す)では --settings {"disableAllHooks":true} を
  ;; 出さない: 全 hook 無効化は安全側 hook まで切る(2026-08-18 ACP 起動会話で
  ;; 発火 0 の実測 — route-c03fe34745)。他の凍結物理は既定と同一。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :session_hooks "inherit"
                            :effort "high"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (= (cut argv 0 2) ["claude" "--dangerously-skip-permissions"]))
  (assert (not-in "--settings" argv))
  (assert (in "--strict-mcp-config" argv))
  (assert (not-in "-p" argv)))


(deftest test-claude-argv-carries-the-conversation-memory-home
  ;; V4(ADR-DOE-AGENTS-006 R11): charter が運ぶ置き場は `--settings` の**1 つの JSON**に合流する。
  ;; --settings を 2 回出すと後勝ちで片方が黙って消えるので、hook の無効化と同居させる。
  ;; 綴りは `autoMemoryDirectory` — 実射 2026-09-20 で `memoryDir`(CLI 内部の property 名)は
  ;; 黙って無視され、既定の家の置き場が作られた。
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude" :memory_dir "/state/agent-memory/c-01ARZ"))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (= (.count argv "--settings") 1) argv)
  (setv settings (json.loads (get argv (+ (.index argv "--settings") 1))))
  (assert (= settings {"disableAllHooks" True "autoMemoryDirectory" "/state/agent-memory/c-01ARZ"})
          settings)
  ;; 置き場だけを宣言した手番(hook は継ぐ)でも --settings は 1 つで、置き場は落ちない。
  (setv inherit (base-params :agent_type "claude" :session_hooks "inherit"
                             :memory_dir "/state/agent-memory/c-01ARZ"))
  (<- inherit-argv (run-claude world (build-launch "claude" inherit)))
  (assert (= (.count inherit-argv "--settings") 1) inherit-argv)
  (assert (= (json.loads (get inherit-argv (+ (.index inherit-argv "--settings") 1)))
             {"autoMemoryDirectory" "/state/agent-memory/c-01ARZ"})
          inherit-argv))


(deftest test-claude-argv-without-a-memory-home-is-byte-identical-to-today
  ;; V5: 置き場を宣言しない手番の argv は 1 byte も変わらない(凍結配線の pin を壊さない)。
  ;; 空・空白だけの欄も「宣言していない」— 設定に空の path を書かない。
  (setv world (ImplWorld))
  (<- baseline (run-claude world (build-launch "claude" (base-params :agent_type "claude"))))
  (assert (= (cut baseline 0 4)
             ["claude" "--dangerously-skip-permissions"
              "--settings" "{\"disableAllHooks\":true}"])
          baseline)
  (for [blank [None "" "   "]]
    (<- argv (run-claude world (build-launch "claude" (base-params :agent_type "claude"
                                                                   :memory_dir blank))))
    (assert (= argv baseline) #(blank argv)))
  ;; resume の argv も同じ 1 点(build-claude-argv)を通るので、同じ保証が効く。
  (<- resume-argv (run-claude world (build-resume "claude"
                                                   (base-params :agent_type "claude"
                                                                :resume_mode "resume"
                                                                :conversation {"session_id" "conv-1"}
                                                                :memory_dir "/state/agent-memory/c-01ARZ"))))
  (assert (= (.count resume-argv "--settings") 1) resume-argv)
  (assert (in "autoMemoryDirectory"
              (get resume-argv (+ (.index resume-argv "--settings") 1)))
          resume-argv))


(deftest test-claude-argv-merges-the-declared-seat-settings
  ;; card acp:kanban-issue:ki-7b52bb76aa6e(ADR-DOE-AGENTS-004 R13): 機体の参加の宣言が名指した席の settings
  ;; (launch / headless が起動の拍ごとに読んで params claude_settings に載せる — 中身は dotfiles の proxy 登録 1 枚)は、
  ;; 記憶の置き場と**同じ 1 つの** `--settings` に合流する。--settings を 2 回出すと後勝ちで片方が黙って消える。
  (setv world (ImplWorld))
  (setv seat-hooks {"hooks" {"PreToolUse" [{"matcher" "*"
                                          "hooks" [{"type" "command"
                                                    "command" "python3 ~/dotfiles/claude-hooks/hook-proxy.py PreToolUse"
                                                    "timeout" 120}]}]}})
  (setv params (base-params :agent_type "claude"
                            :session_hooks "inherit"
                            :memory_dir "/state/agent-memory/c-01ARZ"
                            :claude_settings seat-hooks
                            :effort "high"
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (= (cut argv 0 2) ["claude" "--dangerously-skip-permissions"]))
  (assert (= (.count argv "--settings") 1) argv)
  (setv settings (json.loads (get argv (+ (.index argv "--settings") 1))))
  (assert (= settings {"autoMemoryDirectory" "/state/agent-memory/c-01ARZ"
                       "hooks" (get seat-hooks "hooks")})
          settings)
  ;; disableAllHooks は inherit の手番に現れない(宣言した hook が生きる)。
  (assert (not-in "disableAllHooks" settings) settings)
  ;; 記憶の置き場の無い手番でも合流点は同じ 1 つ(席の settings だけの --settings)。
  (setv bare (base-params :agent_type "claude" :session_hooks "inherit" :claude_settings seat-hooks))
  (<- bare-argv (run-claude world (build-launch "claude" bare)))
  (assert (= (.count bare-argv "--settings") 1) bare-argv)
  (assert (= (json.loads (get bare-argv (+ (.index bare-argv "--settings") 1))) seat-hooks) bare-argv)
  ;; 蘇生の argv も同じ 1 点(build-claude-argv)を通る — headless_argv も同じ点を借りる。
  (<- resume-argv (run-claude world (build-resume "claude"
                                                   (base-params :agent_type "claude"
                                                                :session_hooks "inherit"
                                                                :resume_mode "resume"
                                                                :conversation {"session_id" "conv-1"}
                                                                :claude_settings seat-hooks))))
  (assert (= (.count resume-argv "--settings") 1) resume-argv)
  (assert (in "hook-proxy.py" (get resume-argv (+ (.index resume-argv "--settings") 1))) resume-argv))


(deftest test-claude-argv-refuses-seat-settings-that-collide-with-doeff-keys
  ;; R13: 鍵の衝突は fail-loud — 黙って後勝ちにすると「hook を配ったつもりで disableAllHooks が残る」か
  ;; 「記憶の置き場が消える」のどちらかが無音で起きる。参加の門 (c)(d) が断るはずの形が起動の拍に現れても、
  ;; argv の合流点は自分で断る(門の写しではなく、合流点の自衛)。
  (setv world (ImplWorld))
  (for [[label params] [
         ;; (c) doeff が置く鍵を宣言が持つ — inherit で置き場が無くても autoMemoryDirectory は doeff の鍵
         ["autoMemoryDirectory" (base-params :agent_type "claude" :session_hooks "inherit"
                                             :claude_settings {"autoMemoryDirectory" "/elsewhere"})]
         ["disableAllHooks" (base-params :agent_type "claude" :session_hooks "inherit"
                                         :claude_settings {"disableAllHooks" False "hooks" {}})]
         ;; 置き場と同じ鍵 — 後勝ちで置き場が消える形
         ["memory-dir-collision" (base-params :agent_type "claude" :session_hooks "inherit"
                                              :memory_dir "/state/agent-memory/c-01ARZ"
                                              :claude_settings {"autoMemoryDirectory" "/x"})]
         ;; (d) disabled の手番に席の settings — disableAllHooks が勝って宣言した hook が黙って死ぬ
         ["disabled-with-hooks" (base-params :agent_type "claude" :session_hooks "disabled"
                                             :claude_settings {"hooks" {}})]
         ;; object でない宣言
         ["not-an-object" (base-params :agent_type "claude" :session_hooks "inherit"
                                       :claude_settings ["hooks"])]]]
    (setv raised None)
    (try
      (<- _ (run-claude world (build-launch "claude" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) label)
    (assert (in "claude_settings" (str raised)) #(label (str raised)))))


(deftest test-claude-argv-without-declared-seat-settings-is-byte-identical
  ;; R13: 宣言の無い機体(欄なし・None・空の {})の argv は今日と 1 byte も変わらない — 既定と inherit の両方で。
  (setv world (ImplWorld))
  (for [hooks ["disabled" "inherit"]]
    (<- baseline (run-claude world (build-launch "claude" (base-params :agent_type "claude"
                                                                       :session_hooks hooks
                                                                       :memory_dir "/state/agent-memory/c-01ARZ"))))
    (for [blank [None {}]]
      (<- argv (run-claude world (build-launch "claude" (base-params :agent_type "claude"
                                                                     :session_hooks hooks
                                                                     :memory_dir "/state/agent-memory/c-01ARZ"
                                                                     :claude_settings blank))))
      (assert (= argv baseline) #(hooks blank argv)))))


(deftest test-claude-argv-caller-sse-servers
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude"
                            :mcp_servers {"tools" "http://127.0.0.1:9/sse"}
                            :result_channel (channel-spec)))
  (<- argv (run-claude world (build-launch "claude" params)))
  (setv mcp (json.loads (get argv (+ (.index argv "--mcp-config") 1))))
  (assert (= (get mcp "mcpServers" "tools")
             {"type" "sse" "url" "http://127.0.0.1:9/sse"}))
  (assert (in "doeff_result" (get mcp "mcpServers"))))


(deftest test-claude-argv-no-mcp-config-without-channel
  (setv world (ImplWorld))
  (setv params (base-params :agent_type "claude" :result_channel None
                            :expected_result None))
  (<- argv (run-claude world (build-launch "claude" params)))
  (assert (not-in "--mcp-config" argv))
  (assert (not-in "--strict-mcp-config" argv))
  ;; effort / model 無指定はフラグ自体を出さない
  (assert (not-in "--effort" argv))
  (assert (not-in "--model" argv)))


(deftest test-codex-argv-golden-wiring
  (setv world (ImplWorld))
  (setv params (base-params :model "gpt-5"
                            :effort "high"
                            :mcp_servers {"tools" "http://127.0.0.1:9/sse"}
                            :result_channel (channel-spec)))
  (<- argv (run-codex world (build-launch "codex" params)))
  (assert (= (cut argv 0 2) ["codex" "--yolo"]))
  ;; effort は TOML 文字列(oracle toml_quoted_string)
  (assert (in "model_reasoning_effort=\"high\"" argv))
  ;; caller server は url、channel は command + args(TOML 配列、key は常に quote)
  (assert (in "mcp_servers.\"tools\".url=\"http://127.0.0.1:9/sse\"" argv))
  (assert (in "mcp_servers.\"doeff_result\".command=\"/opt/doeff-sessionhost\"" argv))
  (assert (in (+ "mcp_servers.\"doeff_result\".args="
                 "[\"report-result-mcp\",\"--session\",\"s1\",\"--socket\",\"/tmp/agentd.sock\"]")
              argv))
  ;; -c が各値の直前に居る
  (setv effort-idx (.index argv "model_reasoning_effort=\"high\""))
  (assert (= (get argv (- effort-idx 1)) "-c"))
  ;; model は channel 配線の後(oracle 順序)
  (assert (= (get argv (+ (.index argv "--model") 1)) "gpt-5"))
  (assert (> (.index argv "--model")
             (.index argv "mcp_servers.\"doeff_result\".command=\"/opt/doeff-sessionhost\"")))
  ;; prompt は argv に載らない
  (assert (not-in "do the task" argv)))


(deftest test-codex-argv-minimal
  (setv world (ImplWorld))
  (setv params (base-params :result_channel None :expected_result None))
  (<- argv (run-codex world (build-launch "codex" params)))
  (assert (= argv ["codex" "--yolo"])))


;; ---------------------------------------------------------------------------
;; WireResultChannel — mcp_command_args 同物理(main.rs:1319)
;; ---------------------------------------------------------------------------

(deftest test-wire-result-channel-spec
  (setv world (ImplWorld))
  (<- spec (run-codex world (wire-result-channel "codex" "s1" "/tmp/agentd.sock")))
  (assert (= (get spec "command") "/opt/doeff-sessionhost"))
  (assert (= (get spec "args")
             ["report-result-mcp" "--session" "s1" "--socket" "/tmp/agentd.sock"]))
  (setv world2 (ImplWorld))
  (<- spec2 (run-claude world2 (wire-result-channel "claude" "s1" "/tmp/agentd.sock")))
  (assert (= spec spec2)))


;; ---------------------------------------------------------------------------
;; PreLaunchSetup codex — S11 ゲート + config.toml trust 物理
;; ---------------------------------------------------------------------------

(deftest test-codex-prelaunch-rejects-missing-codex-home
  (setv world (ImplWorld))
  ;; process env に CODEX_HOME が居ても、binding / command の明示が無ければ
  ;; 拒否(R7: ゲートは typed binding と command 埋め込みのみを見る — 暗黙の
  ;; ~/.codex fallback が個人アカウントを焼いた実障害)
  (setv (get world.env "CODEX_HOME") "/env/home")
  (setv params (base-params))
  (setv raised None)
  (try
    (<- _ (run-codex world (pre-launch-setup "codex" params)))
    (except [e RuntimeError]
      (setv raised e)))
  (assert (is-not raised None))
  (assert (in "no agent auth profile" (str raised)))
  (assert (in "CODEX_HOME" (str raised)))
  ;; tmux 効果ゼロ = 「tmux 呼び出し前に fail」の直接束縛版
  (assert (= world.tmux-calls []))
  ;; fs にも触っていない
  (assert (= world.atomic-writes []))
  (assert (= world.fs {})))


(deftest test-codex-prelaunch-accepts-command-embedded-home
  (setv world (ImplWorld))
  (setv params (base-params
                 :command "CODEX_HOME=/x/codex codex --yolo"))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  ;; command 埋め込みはゲート通過(trust 書き先は解決不能なので env fallback、
  ;; ここでは env 台本も無し → trust 書き込みはスキップされない:
  ;; oracle は daemon env fallback で ~/.codex に書くが、直接束縛では
  ;; EnvGet(None) → 書き先無しの typed skip とし、identity は None を返す)
  (assert (= world.tmux-calls [])))


(deftest test-codex-prelaunch-trust-creates-config
  (setv world (ImplWorld))
  (setv params (base-params :binding {"kind" "codex" "codex_home" "/x/codex"}))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  ;; 実効 identity が返る(S14 の Hy positive 化の布石)
  (assert (= (get identity "CODEX_HOME") "/x/codex"))
  (assert (in "/x/codex" world.dirs))
  (setv written (get world.fs "/x/codex/config.toml"))
  (assert (in "[projects.\"/work/dir\"]" written))
  (assert (in "trust_level = \"trusted\"" written)))


(deftest test-codex-prelaunch-trust-idempotent-replace
  (setv world (ImplWorld))
  (setv (get world.fs "/x/codex/config.toml")
        "[projects.\"/work/dir\"]\ntrust_level = \"untrusted\"\n[other]\nkey = 1\n")
  (setv params (base-params :binding {"kind" "codex" "codex_home" "/x/codex"}))
  (<- _ (run-codex world (pre-launch-setup "codex" params)))
  (setv written (get world.fs "/x/codex/config.toml"))
  ;; 既存 header 内の trust_level を差し替え(重複 append しない)
  (assert (= (.count written "trust_level") 1))
  (assert (in "trust_level = \"trusted\"" written))
  (assert (in "[other]" written)))


;; ---------------------------------------------------------------------------
;; PreLaunchSetup claude — S12 trust pre-seed(canonical key・temp+rename)
;; ---------------------------------------------------------------------------

(deftest test-claude-prelaunch-preseeds-trust
  (setv world (ImplWorld))
  (setv (get world.canonical "/work/dir") "/private/work/dir")
  (setv params (base-params :agent_type "claude"
                            :binding {"kind" "claude-code"
                                      "config_dir" "/x/claude"}))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (in "/x/claude" world.dirs))
  ;; canonicalized work_dir が project key(S12: /tmp → /private/tmp)
  (setv state (json.loads (get world.fs "/x/claude/.claude.json")))
  (setv project (get state "projects" "/private/work/dir"))
  (assert (= (get project "hasTrustDialogAccepted") True))
  (assert (= (get project "hasCompletedProjectOnboarding") True))
  ;; temp+rename(oracle: .agentd-tmp suffix — 残骸不在は substrate 契約)
  (assert (= world.atomic-writes [#("/x/claude/.claude.json" ".agentd-tmp")])))


(deftest test-claude-prelaunch-merges-existing-state
  (setv world (ImplWorld))
  (setv (get world.fs "/x/claude/.claude.json")
        (json.dumps {"projects" {"/old" {"hasTrustDialogAccepted" True}}
                     "userID" "u1"}))
  (setv params (base-params :agent_type "claude"
                            :binding {"kind" "claude-code"
                                      "config_dir" "/x/claude"}))
  (<- _ (run-claude world (pre-launch-setup "claude" params)))
  (setv state (json.loads (get world.fs "/x/claude/.claude.json")))
  ;; 既存 state は保持されつつ新 project が追記される
  (assert (= (get state "userID") "u1"))
  (assert (in "/old" (get state "projects")))
  (assert (in "/work/dir" (get state "projects"))))


(deftest test-claude-prelaunch-env-fallback-no-raise
  (setv world (ImplWorld))
  ;; CLAUDE_CONFIG_DIR 無し = warning のみ(DOE-003 R3 staged)。
  ;; process env fallback → HOME/.claude 既定(oracle home_dir().join(.claude))
  (setv (get world.env "HOME") "/home/u")
  (setv params (base-params :agent_type "claude"))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/home/u/.claude"))
  (assert (in "explicit" (get identity "warnings" 0))))


;; ---------------------------------------------------------------------------
;; PreLaunchSetup の課金の階級(従量課金の便 lane A・ADR-DOE-AGENTS-004 R9 改訂):
;; 家の中の従量課金の宣言と binding の kind の一致を起動前に検める
;; ---------------------------------------------------------------------------

(deftest test-claude-metered-pre-launch-requires-declared-credential-in-settings
  ;; kind `claude-code-metered` は「この家は従量課金」という宣言を型で運ぶので、
  ;; 家の中身が本当にそう宣言しているかを起動前に検める。受けるのは公式文書の
  ;; 2 形だけ: settings.json の apiKeyHelper か、Vertex の env の対
  ;; (CLAUDE_CODE_USE_VERTEX=1 + ANTHROPIC_VERTEX_PROJECT_ID)。
  ;; 断りは trust の書きより前(fs への書き込みゼロ・tmux 効果ゼロ)。
  ;; **鍵の値は読まない**: 判定は policy の純関数が宣言の名だけを返す。
  (setv metered-binding {"kind" "claude-code-metered" "config_dir" "/x/claude-metered"})

  ;; (1) apiKeyHelper で通る + 課金の階級の印が identity に載る
  (setv world (ImplWorld))
  (setv (get world.fs "/x/claude-metered/settings.json")
        (json.dumps {"apiKeyHelper" "cat /secrets/anthropic-key"}))
  (setv params (base-params :agent_type "claude" :binding metered-binding))
  (<- identity (run-claude world (pre-launch-setup "claude" params)))
  (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude-metered"))
  (assert (= (get identity "billing") "metered"))
  ;; 鍵を読む道具の綴り(apiKeyHelper の値)は identity に載らない
  (assert (not-in "secrets/anthropic-key" (json.dumps identity)))
  ;; trust の pre-seed は従来どおり同じ本体を通る(並行実装を作っていない pin)
  (assert (in "/x/claude-metered/.claude.json" world.fs))

  ;; (2) Vertex の env の対でも通る(鍵の無い従量課金 — 課金は GCP)
  (setv world2 (ImplWorld))
  (setv (get world2.fs "/x/claude-metered/settings.json")
        (json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"
                            "ANTHROPIC_VERTEX_PROJECT_ID" "proj-x"}}))
  (<- identity2 (run-claude world2 (pre-launch-setup "claude" params)))
  (assert (= (get identity2 "billing") "metered"))

  ;; (3) 宣言が無い / 家が無い / 壊れている / 受けない形 → typed reject・副作用ゼロ
  (for [settings [None
                  "{not json"
                  (json.dumps {})
                  (json.dumps {"env" {"ANTHROPIC_API_KEY" "sk-x"}})
                  (json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"}})
                  (json.dumps {"apiKeyHelper" "   "})]]
    (setv w (ImplWorld))
    (when (is-not settings None)
      (setv (get w.fs "/x/claude-metered/settings.json") settings))
    (setv raised None)
    (try
      (<- _ (run-claude w (pre-launch-setup "claude" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {settings !r}")
    (assert (in "claude-code-metered" (str raised)))
    (assert (in "apiKeyHelper" (str raised)))
    ;; trust の書きより前に断っている
    (assert (= w.atomic-writes []))
    (assert (not-in "/x/claude-metered/.claude.json" w.fs))
    (assert (= w.tmux-calls [])))

  ;; (4) 定額の kind に metered の枝が漏れない: 宣言の無い定額の家は今日どおり通り、
  ;; 課金の階級の印も付かない(宣言が **在る** 定額の家の締め直しは lane B の
  ;; test-claude-pre-launch-rejects-metered-declaration-in-subscription-home が持つ)。
  (setv plain (ImplWorld))
  (setv (get plain.fs "/x/claude/settings.json") (json.dumps {"model" "opus"}))
  (<- plain-identity
      (run-claude plain (pre-launch-setup
                          "claude"
                          (base-params :agent_type "claude"
                                       :binding {"kind" "claude-code"
                                                 "config_dir" "/x/claude"}))))
  (assert (= (get plain-identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (not-in "billing" plain-identity)))


(deftest test-codex-metered-pre-launch-requires-api-key-field-in-auth-file
  ;; kind `codex-metered` の受理形は制御面の二軸宣言 {auth_file, profile_dir}。
  ;; host は宣言から家の view を合成し、その auth.json が **非空の**
  ;; OPENAI_API_KEY を持つことだけを検める(値は読まない)。
  ;; ⚠ 欄の有無では判じない: codex の CLI は定額(ChatGPT)の login でも
  ;; auth.json にこの欄を null で書き出す(実測 2026-09-15 — 運用主の家 3 つとも
  ;; 欄は在る・中身は空)。欄の有無で判じると定額の家を全部「従量課金」と誤る。
  (setv metered-binding {"kind" "codex-metered"
                         "auth_file" "/auths/metered.json"
                         "profile_dir" "/profiles/metered"})
  (setv params (base-params :binding metered-binding))
  (setv view "/state/doeff/agent-homes/composed-view")

  ;; (1) 非空の鍵の欄で通る + 印と二軸の宣言が identity に載る(resume 用)
  (setv world (ImplWorld))
  (setv (get world.env "XDG_STATE_HOME") "/state")
  (setv (get world.fs f"{view}/auth.json")
        (json.dumps {"OPENAI_API_KEY" "sk-test-not-a-real-key" "auth_mode" "apikey"}))
  (<- identity (run-codex world (pre-launch-setup "codex" params)))
  (assert (= (get identity "CODEX_HOME") view))
  (assert (= (get identity "billing") "metered"))
  (assert (= (get identity "codex_auth_file") "/auths/metered.json"))
  (assert (= (get identity "codex_profile_dir") "/profiles/metered"))
  ;; 鍵の値は identity に載らない
  (assert (not-in "sk-test-not-a-real-key" (json.dumps identity)))
  ;; 合成は宣言二軸 + 解決した view root で 1 回
  (assert (= world.composed [#("/auths/metered.json" "/profiles/metered"
                               "/state/doeff/agent-homes")]))
  ;; trust は従来どおり同じ本体が view の config.toml へ書く
  (assert (in "trust_level = \"trusted\"" (get world.fs f"{view}/config.toml")))

  ;; (2) 欄が空 / null / 家が無い / 壊れている → typed reject・trust の書きゼロ
  (for [auth [None
              "{not json"
              (json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"
                           "tokens" {"access_token" "t"}})
              (json.dumps {"OPENAI_API_KEY" ""})
              (json.dumps {"auth_mode" "chatgpt"})]]
    (setv w (ImplWorld))
    (setv (get w.env "XDG_STATE_HOME") "/state")
    (when (is-not auth None)
      (setv (get w.fs f"{view}/auth.json") auth))
    (setv raised None)
    (try
      (<- _ (run-codex w (pre-launch-setup "codex" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {auth !r}")
    (assert (in "codex-metered" (str raised)))
    (assert (in "codex login --with-api-key" (str raised)))
    (assert (= w.atomic-writes []))
    (assert (= w.tmux-calls [])))

  ;; (3) 定額の kind に metered の枝が漏れない: 定額の login の家(欄は在るが空)は
  ;; 今日どおり通り、課金の階級の印も付かない(非空の欄を持つ定額の家の締め直しは
  ;; lane B の test-codex-pre-launch-rejects-api-key-in-subscription-auth-file が持つ)。
  (setv plain (ImplWorld))
  (setv (get plain.fs "/x/codex/auth.json")
        (json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"}))
  (<- plain-identity
      (run-codex plain (pre-launch-setup
                         "codex"
                         (base-params :binding {"kind" "codex"
                                                "codex_home" "/x/codex"}))))
  (assert (= (get plain-identity "CODEX_HOME") "/x/codex"))
  (assert (not-in "billing" plain-identity)))


(deftest test-claude-pre-launch-rejects-metered-declaration-in-subscription-home
  ;; lane B の締め直し(従量課金の便・ADR-DOE-AGENTS-003 R4 改訂): 定額の kind の家に従量課金の
  ;; 宣言があれば拒否する。旧形は env の名しか見ないので、家の中に鍵を入れれば黙って
  ;; 通る道が開いていた — 課金の階級が型に現れず、監査点も消える形。
  ;; 直し方 2 択(kind を *-metered にする / 家から宣言を外す)を文言が名指す。
  ;; account(どの人の login か)は今も検めない(R4 の不変部分)。
  (setv subscription {"kind" "claude-code" "config_dir" "/x/claude"})
  (setv params (base-params :agent_type "claude" :binding subscription))
  (for [[settings declared]
        [#((json.dumps {"apiKeyHelper" "cat /secrets/key"}) "apiKeyHelper")
         #((json.dumps {"env" {"CLAUDE_CODE_USE_VERTEX" "1"}})
           "env.CLAUDE_CODE_USE_VERTEX")
         #((json.dumps {"env" {"ANTHROPIC_API_KEY" "sk-x"}}) "env.ANTHROPIC_API_KEY")
         #((json.dumps {"env" {"ANTHROPIC_AUTH_TOKEN" "t"}}) "env.ANTHROPIC_AUTH_TOKEN")]]
    (setv world (ImplWorld))
    (setv (get world.fs "/x/claude/settings.json") settings)
    (setv raised None)
    (try
      (<- _ (run-claude world (pre-launch-setup "claude" params)))
      (except [e RuntimeError] (setv raised e)))
    (assert (is-not raised None) f"expected reject for {settings}")
    (setv message (str raised))
    (assert (in "is a subscription kind" message) message)
    (assert (in declared message) message)
    (assert (in "claude-code-metered" message) message)
    (assert (in "remove the metered declaration" message) message)
    ;; 断りは trust の書きより前(副作用ゼロ)
    (assert (= world.atomic-writes []))
    (assert (not-in "/x/claude/.claude.json" world.fs))
    (assert (= world.tmux-calls []))
    ;; 鍵の値そのものは文言に出ない(名だけ)
    (assert (not-in "secrets/key" message))
    (assert (not-in "sk-x" message)))

  ;; 宣言の無い家・不在の家は今日どおり通る(段階強制を巻き戻さない)
  (for [settings [None (json.dumps {}) (json.dumps {"model" "opus"})]]
    (setv ok-world (ImplWorld))
    (when (is-not settings None)
      (setv (get ok-world.fs "/x/claude/settings.json") settings))
    (<- identity (run-claude ok-world (pre-launch-setup "claude" params)))
    (assert (= (get identity "CLAUDE_CONFIG_DIR") "/x/claude"))
    (assert (not-in "billing" identity))
    (assert (in "/x/claude/.claude.json" ok-world.fs)))

  ;; 破損した settings.json は **通す**(今日と同じ — 破損は claude 自身が loud に
  ;; 落ちる)。ただし検められなかったことを warning に 1 行残す。
  (setv broken (ImplWorld))
  (setv (get broken.fs "/x/claude/settings.json") "{not json")
  (<- broken-identity (run-claude broken (pre-launch-setup "claude" params)))
  (assert (= (get broken-identity "CLAUDE_CONFIG_DIR") "/x/claude"))
  (assert (in "/x/claude/.claude.json" broken.fs))
  (assert (any (gfor w (get broken-identity "warnings") (in "not a JSON object" w)))
          (str (get broken-identity "warnings"))))


(deftest test-codex-pre-launch-rejects-api-key-in-subscription-auth-file
  ;; lane B の codex 面: 定額の kind の家(native 形 {codex_home} も二軸形も同じ
  ;; 1 点の読み)に **非空の** OPENAI_API_KEY が在れば拒否する。
  ;; ⚠ 欄が null / 空の家は通る — codex の CLI は定額(ChatGPT)の login でも欄を
  ;; null で書き出す(実測 2026-09-15: 運用主の家 3 つとも欄は在る・中身は空)。
  ;; 欄の有無で判じると運用主の codex の起動を 100% 断る。
  (setv native {"kind" "codex" "codex_home" "/x/codex"})
  (setv two-axis {"kind" "codex" "auth_file" "/auths/sub.json"
                  "profile_dir" "/profiles/sub"})
  (setv view "/state/doeff/agent-homes/composed-view")

  ;; (1) native 形: 非空の鍵の欄 → 拒否(trust の書きより前)
  (setv world (ImplWorld))
  (setv (get world.fs "/x/codex/auth.json")
        (json.dumps {"OPENAI_API_KEY" "sk-x" "auth_mode" "apikey"}))
  (setv raised None)
  (try
    (<- _ (run-codex world (pre-launch-setup "codex" (base-params :binding native))))
    (except [e RuntimeError] (setv raised e)))
  (assert (is-not raised None))
  (setv message (str raised))
  (assert (in "is a subscription kind" message) message)
  (assert (in "codex-metered" message) message)
  (assert (in "OPENAI_API_KEY" message) message)
  (assert (not-in "sk-x" message) message)
  (assert (= world.atomic-writes []))
  (assert (= world.tmux-calls []))

  ;; (2) 二軸形も同じ 1 点で検まる(合成 view の auth.json を読む)
  (setv world2 (ImplWorld))
  (setv (get world2.env "XDG_STATE_HOME") "/state")
  (setv (get world2.fs f"{view}/auth.json") (json.dumps {"OPENAI_API_KEY" "sk-y"}))
  (setv raised2 None)
  (try
    (<- _ (run-codex world2 (pre-launch-setup "codex" (base-params :binding two-axis))))
    (except [e RuntimeError] (setv raised2 e)))
  (assert (is-not raised2 None))
  (assert (in "is a subscription kind" (str raised2)))
  (assert (= world2.atomic-writes []))

  ;; (3) 定額の login の家(欄は在るが null / 空 / 欄なし)は今日どおり通る —
  ;; 運用主の配備が止まらないことの pin
  (for [auth [(json.dumps {"OPENAI_API_KEY" None "auth_mode" "chatgpt"
                           "last_refresh" "2026-09-15T00:00:00Z"
                           "tokens" {"access_token" "t"}})
              (json.dumps {"OPENAI_API_KEY" ""})
              (json.dumps {"OPENAI_API_KEY" "   "})
              (json.dumps {"auth_mode" "chatgpt"})
              None]]
    (setv ok-world (ImplWorld))
    (when (is-not auth None)
      (setv (get ok-world.fs "/x/codex/auth.json") auth))
    (<- identity (run-codex ok-world (pre-launch-setup "codex" (base-params :binding native))))
    (assert (= (get identity "CODEX_HOME") "/x/codex"))
    (assert (not-in "billing" identity))
    ;; trust は従来どおり書かれる
    (assert (in "trust_level = \"trusted\"" (get ok-world.fs "/x/codex/config.toml"))))

  ;; (4) 破損した auth.json は通す(今日と同じ — 破損は codex 自身が loud に落ちる)
  (setv broken (ImplWorld))
  (setv (get broken.fs "/x/codex/auth.json") "{not json")
  (<- broken-identity (run-codex broken (pre-launch-setup "codex" (base-params :binding native))))
  (assert (= (get broken-identity "CODEX_HOME") "/x/codex")))


;; ---------------------------------------------------------------------------
;; ClassifyPane — F-* marker + R9 dialog(dismiss keys は impl 所有)
;; ---------------------------------------------------------------------------

(defk classify-codex [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  (setv world (ImplWorld))
  (<- obs (run-codex world (classify-pane "codex" output)))
  obs)

(defk classify-claude [output]
  {:pre [(: output str)] :post [(: % PaneObservation)]}
  (setv world (ImplWorld))
  (<- obs (run-claude world (classify-pane "claude" output)))
  obs)


(deftest test-classify-codex-idle-and-active
  (<- idle (classify-codex "some output\n› "))
  (assert idle.has-idle-prompt)
  (assert (not idle.has-active-marker))
  (assert idle.startup-finished)
  (<- active (classify-codex "working (12s • esc to interrupt)"))
  (assert active.has-active-marker)
  ;; MCP boot 中の spinner は active ではない(16h-stuck 実障害)
  (<- booting (classify-codex "Starting MCP servers (1/5) (esc to interrupt)"))
  (assert (not booting.has-active-marker))
  (assert (not booting.startup-finished)))


(deftest test-classify-claude-spinner-physics
  ;; 最終 ❯ の上の非空行に `… (` = live spinner(oracle
  ;; output_has_live_claude_spinner_marker)
  (<- active (classify-claude "✢ Swooping… (37s · thinking…)\n\n❯"))
  (assert active.has-active-marker)
  ;; ❯ の上が普通の出力なら active ではない
  (<- idle (classify-claude "⏺ done reading\n\n❯"))
  (assert (not idle.has-active-marker))
  (assert idle.has-idle-prompt)
  (assert idle.has-turn-activity))


(deftest test-classify-claude-spinner-crosses-composer-border
  ;; issue #573(2026-07-29 実 incident の pane snapshot 現物): 現行 claude TUI
  ;; は入力欄を全幅罫線で囲む — `❯` 直上の非空行は罫線になり、live spinner 行は
  ;; その 1 つ上に居る。罫線で探索が止まると active-marker が常に false になり、
  ;; turn-end 判定が stability guard 単独に退化 → spinner の秒刻みと monitor
  ;; 間隔の aliasing で走行中 turn へ solicitation・中断キーが飛ぶ。
  (setv bordered-working
        (+ "s/adr/defadr_0066_merge_lane_land_queue.hy\n"
           "\n"
           "✦ Cerebrating… (1m 34s · ↓ 2.2k tokens · thought for 39s)\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle) · gh auth login for PR status ·\n"))
  (<- active (classify-claude bordered-working))
  (assert active.has-active-marker)
  ;; 枠線付きの idle pane — 罫線 skip が本文から spinner を捏造しないこと
  (setv bordered-idle
        (+ "⏺ done. result written.\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"))
  (<- idle (classify-claude bordered-idle))
  (assert (not idle.has-active-marker))
  (assert idle.has-idle-prompt)
  ;; 歴史 spinner(罫線の上の直近非空行が本文)は live ではない
  (setv bordered-history
        (+ "✢ Swooping… (1s · thinking)\n"
           "wrote invalid result\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"))
  (<- hist (classify-claude bordered-history))
  (assert (not hist.has-active-marker)))


(deftest test-classify-claude-working-pane-carries-both-marker-facts
  ;; ACP issue 55b1bd の前提の逐語固定(agentd.sqlite 直読 2026-08-12・
  ;; session agent_inv_wi_5f3fa0e22242b74d_a1 の死亡時 output_snippet 現物)。
  ;; この席は 31 分 5 秒・91.4k トークンを走らせている最中に「入力待ちが
  ;; 1800 秒続いた」として終端された。現物が示す事実は 2 つ同時である:
  ;;   - waiting marker は True(permission-mode フッターの逐語 3 語が常設)
  ;;   - active marker も True(罫線を跨いだ live spinner + esc to interrupt)
  ;; marker 検出はどちらも正しい。誤りは『waiting だけを見て状態を決める』
  ;; 分類の側にあった(policy 側 deftest
  ;; test-working-pane-with-waiting-footer-stays-running が連言を固定する)。
  (setv working-31min
        (+ "✶ Whatchamacalliting… (31m 5s · ↓ 91.4k tokens)\n"
           "\n"
           (* "─" 80) "\n"
           "❯ \n"
           (* "─" 80) "\n"
           "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for age…\n"))
  (<- obs (classify-claude working-31min))
  (assert obs.has-waiting-marker)
  (assert obs.has-active-marker))


(deftest test-classify-claude-queued-messages-fact
  ;; issue #573(incident event 1997731 現物): mid-turn に配送された message は
  ;; composer に積まれ、入力行が `❯ Press up to edit queued messages` になる。
  ;; 未消費 queue = turn 走行中の明白な busy 証拠 — PaneObservation が事実として
  ;; 運び、中断キー送出の安全壁が参照する。
  (setv queued
        (+ (* "─" 80) "\n"
           "❯ Press up to edit queued messages\n"
           (* "─" 80) "\n"))
  (<- obs (classify-claude queued))
  (assert obs.has-queued-messages)
  ;; 素の idle pane に queue 事実は立たない
  (<- idle (classify-claude "some scroll\n❯ \n"))
  (assert (not idle.has-queued-messages))
  ;; 履歴に同文言が写っても最終 prompt 行に居なければ事実にしない
  (<- hist (classify-claude
             "⏺ echoed: Press up to edit queued messages\n\n❯ \n"))
  (assert (not hist.has-queued-messages)))


(deftest test-classify-failure-api-waiting-windows
  ;; failure は tail 10 行窓
  (<- f (classify-codex "fatal error: kaboom"))
  (assert f.has-failure-marker)
  ;; api-limit は tail 30 行窓
  (<- a (classify-codex "rate limit exceeded"))
  (assert a.has-api-limit-marker)
  ;; waiting は raw 一致
  (<- w (classify-claude "Type your message"))
  (assert w.has-waiting-marker))


(deftest test-api-limit-marker-current-claude-tui-exhausted-wordings
  ;; issue #557 二次補強: 現行 claude TUI の exhausted 側文言は marker 表に
  ;; 無かった(2026-07-20 実測)。exhausted 側だけを追補する。
  (for [frame ["Claude usage limit reached. Your limit will reset at 6pm (Asia/Tokyo)."
               "You've reached your usage limit · resets Jul 26 at 6am"
               "You've hit your usage limit. Upgrade to increase your limits."
               "5-hour limit reached ∙ resets 3am"
               "Weekly limit reached · resets Jul 29 at 9am"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}"))
  ;; approaching 側(まだ動ける)は blocked_api を立ててはならない —
  ;; issue #557 実測 footer 文言(88% 到達)は marker 非対象
  (<- approaching (classify-claude
                    "You've used 88% of your Fable 5 limit · resets Jul 26 at 6am"))
  (assert (not approaching.has-api-limit-marker)))


(deftest test-api-limit-marker-adr0049-r9-spend-limit-wordings
  ;; ACP ADR 0049 R9(2026-07-26 実 incident): Fable 月次枠 0% の実物文言
  ;; 「You've hit your monthly spend limit. /model to switch models.」は
  ;; 既存の "you've hit your limit" に部分一致しない("monthly spend" が
  ;; 挟まる)。credits 枯渇(out of usage credits)も同系の実物文言で
  ;; 語彙に無かった。両方とも exhausted 側 = blocked_api が正。
  (for [frame ["You've hit your monthly spend limit. /model to switch models."
               "You're out of usage credits · buy more credits or upgrade your plan"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}")))


(deftest test-api-limit-scope-is-the-account-unless-the-text-names-a-model
  ;; 2026-09-23(operator の規則 2026-09-17「profile が費用の上限で止まったら、種類を問わず口座が枯れた 1 事実」):
  ;; 実物の文 = 2026-09-23 08:00〜15:00 JST の agent-job の ProviderLimit の 8 件(3 種)と、過去の実物の文。
  ;; 2026-09-24(card acp:kanban-issue:ki-5d4849d22a4e): 分類の 1 点は api-limit-scope-of(#(範囲 理由))。
  ;; group の上限 $N の族は下の検(unknown)へ移した — ここは account と model の 2 つの答え。
  (for [said ["You've hit your session limit · resets 6:20pm (Asia/Tokyo)"
              "You've hit your weekly limit · resets Sep 27 at 7pm (Asia/Tokyo)"
              "You've hit your individual spend limit · ask your admin to raise it"
              "You've hit your monthly spend limit. /model to switch models."
              "You've hit your usage limit. Upgrade to increase your limits."
              "You're out of usage credits · buy more credits or upgrade your plan"
              ;; 族の表に無い言い回し(429 の構造だけで限度と判じた断り)も口座全体 — 範囲を名乗らない文は今日どおり
              "Usage is paused for this workspace · ask your admin"
              ""]]
    (assert (= (api-limit-scope-of said) #("account" "rate-limited")) said))
  (for [said ["You've reached your Fable 5 limit. /model to switch models."
              "You've reached your Opus 4.5 weekly limit. /model to switch models."
              "You’ve reached your Fable limit."
              "You've reached your Sonnet limit"]]
    (assert (= (api-limit-scope-of said) #("model" "rate-limited")) said)))


(deftest test-api-limit-scope-of-the-org-cap-family-is-unknown-because-the-text-names-no-window
  ;; card acp:kanban-issue:ki-5d4849d22a4e(2026-09-24): 「Your group's usage limit is set to $0 · ask your admin for a
  ;; higher limit」はどの窓が枯れたかを言わない(口座の 5 時間の窓が枯れた拍にも、Fable の週の窓だけが枯れた拍にも
  ;; 同じ文)。器は口座全体と決めず unknown と名乗る(範囲の推定は窓を持つ予算の係の 1 点)。理由は rate-limited のまま —
  ;; 範囲と理由は別の軸。実物の文(2026-09-17 p10174 ×18・2026-09-23 ×6・2026-09-24 00:13 JST p10173)と主語・金額の変種。
  (for [said ["Your group's usage limit is set to $0 · ask your admin for a higher limit"
              "Your organization’s usage limit is set to $25 · ask your admin for a higher limit"
              "YOUR GROUP'S USAGE LIMIT IS SET TO $0"]]
    (assert (= (api-limit-scope-of said) #("unknown" "rate-limited")) said))
  ;; 陰性対照: 金額の無い設定の説明は族の外(口座全体の既定へ落ちる — 限度かどうかは is-api-limit-refusal が別に判じる)
  (assert (= (api-limit-scope-of "Your usage limit is set to the plan default. See /usage.") #("account" "rate-limited")))
  ;; 表は上から順: model の族を名乗る文は、同じ文に組織の上限の述部が在っても model(文が model を名乗った事実が先)
  (assert (= (api-limit-scope-of "You've reached your Fable 5 limit. Your group's usage limit is set to $0.")
             #("model" "rate-limited"))))


(deftest test-the-containers-limit-words-stay-inside-the-condition-vocabulary
  ;; 器の表(impls/markers.hy)が返す範囲と理由の語は、制御面が条件へ写す閉語彙(acp/effects.py の
  ;; PROVIDER_LIMIT_SCOPES / PROVIDER_LIMIT_REASONS — 契約 ACP scheduling.json の写しの座)の中に在る。表に行を足して
  ;; 語を増やしたのに閉語彙を足し忘れると、制御面は欄の無い cause と同じ既定(account / rate-limited)へ読み替える —
  ;; その食い違いをここで赤にする(2 つの家の語は同じ綴り・同じ集合)。
  (for [[scope reason] (+ (lfor [_matches scope reason] API-LIMIT-SCOPE-TABLE #(scope reason)) [API-LIMIT-SCOPE-OTHERWISE])]
    (assert (in scope PROVIDER-LIMIT-SCOPES) scope)
    (assert (in reason PROVIDER-LIMIT-REASONS) reason))
  (assert (= (set #(API-LIMIT-SCOPE-ACCOUNT API-LIMIT-SCOPE-MODEL API-LIMIT-SCOPE-UNKNOWN)) (set PROVIDER-LIMIT-SCOPES))
          PROVIDER-LIMIT-SCOPES))


(deftest test-the-containers-limit-cause-becomes-a-condition-that-names-scope-reason-and-model
  ;; card acp:kanban-issue:ki-5d4849d22a4e(2026-09-24): 器の側の 1 点(headless.headless-turn-limit-cause)が文に表を当てて
  ;; cause の欄(limit_scope / limit_reason / limit_resets_at_ms)に載せ、永続の JSON(store.terminal-cause-to-dict)を
  ;; 制御面の 1 点(acp/judgment.provider-limit-condition-of)が agent-job の条件 ProviderLimit に組む —— 一周の記録の欄の形。
  ;; 文は 2026-09-23 16:3x JST の p10184 の実物 3 種(Fable の手番の $0・Opus の手番の session limit・Fable の限度)。
  (setv observed-at "2026-09-23T07:33:00+00:00") ;; 16:33 JST
  (setv at 1790148780000)                          ;; 同じ拍の agentd の時計(epoch ms)
  (defn condition-of [said model]
    (setv cause (run (headless-turn-limit-cause (Verdict "turn-ended" :ok False :detail said :api-error-status 429)
                                                observed-at)))
    (assert (is-not cause None) said)
    (run (provider-limit-condition-of (terminal-cause-to-dict cause) model "p10184" 2 at)))
  ;; group の上限 $0 → 範囲 unknown(器は決めない)・理由 rate-limited・model の欄 = 走らせた model(予算の係が推定に使う)
  (setv org-cap "Your group's usage limit is set to $0 · ask your admin for a higher limit")
  (assert (= (condition-of org-cap "claude-fable-5-1")
             {"type" "ProviderLimit" "status" "True" "reason" "rate-limited" "message" org-cap
              "scope" "unknown" "model" "claude-fable-5-1"
              "profile" "p10184" "attempt" 2 "at" at}))
  ;; session limit → 口座全体・model の欄を書かない・文が名乗る戻りの時刻(19:10 JST = 10:10Z)を resetsAt に
  (setv session "You've hit your session limit · resets 7:10pm (Asia/Tokyo)")
  (assert (= (condition-of session "claude-opus-5-5")
             {"type" "ProviderLimit" "status" "True" "reason" "rate-limited" "message" session
              "scope" "account" "resetsAt" 1790158200000
              "profile" "p10184" "attempt" 2 "at" at}))
  ;; Fable の限度 → その model だけの枯れ・model の欄 = 走らせた model
  (setv fable "You've reached your Fable 5 limit")
  (assert (= (condition-of fable "claude-fable-5-1")
             {"type" "ProviderLimit" "status" "True" "reason" "rate-limited" "message" fable
              "scope" "model" "model" "claude-fable-5-1"
              "profile" "p10184" "attempt" 2 "at" at}))
  ;; 器の cause の欄の形(永続の JSON)— 範囲と理由は別の欄
  (setv persisted (terminal-cause-to-dict
                    (run (headless-turn-limit-cause (Verdict "turn-ended" :ok False :detail org-cap :api-error-status 429)
                                                    observed-at))))
  (assert (= #((get persisted "category") (get persisted "limit_scope") (get persisted "limit_reason"))
             #("rate_limited" "unknown" "rate-limited"))
          persisted)
  (assert (not-in "limit_resets_at_ms" persisted) persisted))


(deftest test-api-limit-resets-at-reads-the-named-instant-in-its-zone
  (setv jst-15 1790143200000) ;; 2026-09-23 15:00 JST
  ;; 日付の無い形 = 断りの後で最初のその時刻。
  (assert (= (run (api-limit-resets-at "You've hit your session limit · resets 6:20pm (Asia/Tokyo)" jst-15))
             (+ jst-15 (* 200 60 1000))))
  (assert (= (run (api-limit-resets-at "You've hit your session limit · resets 2:50am (Asia/Tokyo)" jst-15))
             (+ jst-15 (* (+ (* 11 60) 50) 60 1000))))
  ;; 日付の在る形(年は名乗らない)。
  (assert (= (run (api-limit-resets-at "You've hit your weekly limit · resets Sep 27 at 7pm (Asia/Tokyo)" jst-15))
             (+ jst-15 (* (+ (* 4 24) 4) 3600 1000))))
  ;; 年の変わり目: 12 月末の断りの「resets Jan 2」は翌年。
  (setv dec-30 1830092400000) ;; 2027-12-30 00:00 JST
  (assert (= (run (api-limit-resets-at "resets Jan 2 at 9am (Asia/Tokyo)" dec-30)) (+ dec-30 (* (+ (* 3 24) 9) 3600 1000))))
  ;; 時間帯の無い形・戻りを名乗らない文・未知の時間帯は読まない。
  (for [said ["You've reached your usage limit · resets Jul 26 at 6am"
              "Your group's usage limit is set to $0 · ask your admin for a higher limit"
              "resets 6pm (Mars/Olympus)"
              "resets 13pm (Asia/Tokyo)"]]
    (assert (is (run (api-limit-resets-at said jst-15)) None) said)))


(deftest test-api-limit-marker-possessive-family-bounded-match
  ;; ACP ADR 0049 R9 改訂(2026-08-07): 所有格〜limit 間へ provider 可変語
  ;; (AI 名・プラン名・期間名)が挟まる exhausted 告知の族。逐語列挙は
  ;; 2026-07-20 / 07-26 / 08-06 と 3 度同型で破れた(8/6 実 incident:
  ;; 「You've reached your Fable 5 limit. /model to switch models.」が
  ;; 16 逐語のどれにも部分一致せず、22 件が run_failed/retryable=false で
  ;; 捨てられた)— 語を足すのではなく、有界可変挿入(空白区切り 0〜4 語・
  ;; 各語は英数 + 内部ピリオドのみ = 文境界を越えない)を許す族照合で
  ;; 根治する。
  (for [frame [;; 2026-08-06 実 incident verbatim(22 件を落とした形)
               "You've reached your Fable 5 limit. /model to switch models."
               ;; 2026-07-26 実 incident verbatim(monthly spend 形)も同じ族
               "You've hit your monthly spend limit. /model to switch models."
               ;; 2026-07-20 実測(usage limit 形)も同じ族
               "You've reached your usage limit · resets Jul 26 at 6am"
               "You've hit your usage limit. Upgrade to increase your limits."
               ;; 挿入 0 語(旧 "you've hit your limit" 逐語の被覆)
               "You've hit your limit. Upgrade to continue."
               ;; 族の一般形: 未知の provider 語でも有界内なら陽性
               ;;(バージョン番号の内部ピリオドも語として許す)
               "You've reached your Opus 4.5 weekly limit. /model to switch models."]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}")))


(deftest test-api-limit-marker-family-negative-controls
  ;; 陰性対照: 族照合は無限定の緩い照合(誤検知で健全席を上限扱い =
  ;; 走行中の席を blocked_api に落とす)であってはならない側の壁。
  (for [frame [;; approaching 側(まだ動ける)— 動詞 used は族外のまま
               ;;(issue #557 の対象外規律を族照合後も維持)
               "You've used 88% of your Fable 5 limit · resets Jul 26 at 6am"
               ;; 所有格と limit が文境界を越えて共起しても族外
               ;;(挿入語の内部ピリオド許容は文末ピリオドを含まない)
               "You've reached your destination. Set a new rate limit in settings."
               ;; 上限と無関係の通常出力
               "⏺ done reading the file\n\n❯"
               "All tests passed. 26 passed in 27.94s"]]
    (<- obs (classify-claude frame))
    (assert (not obs.has-api-limit-marker)
            f"unexpected api-limit marker: {frame !r}")))


(deftest test-api-limit-marker-org-cap-family
  ;; agora-redesign #513(2026-09-17 実 incident): 組織の側の上限「Your group's usage limit is
  ;; set to $0 · ask your admin for a higher limit」は所有格族の外の述部で、会社の口座 p10174 の
  ;; 18 手番が普通の終わりとして流れた(族の表が破れた 4 度目)。固定するのは述部と金額の先頭だけ。
  (for [frame ["Your group's usage limit is set to $0 · ask your admin for a higher limit"
               "Your organization’s usage limit is set to $25 · ask your admin for a higher limit"]]
    (<- obs (classify-claude frame))
    (assert obs.has-api-limit-marker f"expected api-limit marker: {frame !r}"))
  ;; 陰性対照: 設定の説明(金額なし)は上限の断りではない
  (<- prose (classify-claude "Your usage limit is set to the plan default. See /usage."))
  (assert (not prose.has-api-limit-marker)))


(deftest test-is-api-limit-refusal-structure-first-then-wording
  ;; agora-redesign #513: CLI が構造で名乗る status が在れば 429 ちょうどが限度(文は読まない)。
  ;; 実測 2026-09-17 の 4 種の文は全部 429 —— 未知の言い回しでも 429 なら限度。
  (setv unknown-wording (is-api-limit-refusal "Usage is paused for this workspace · ask your admin" 429))
  (assert unknown-wording)
  (setv group-cap (is-api-limit-refusal "Your group's usage limit is set to $0 · ask your admin for a higher limit" 429))
  (assert group-cap)
  ;; 429 でない status を名乗る断りは、文に limit の語が在っても限度ではない
  ;; (403 組織の剥奪・401 失効 —— 別の族・別の手当て)
  (setv revoked (is-api-limit-refusal "Your organization has disabled Claude subscription access · usage limit reached" 403))
  (assert (not revoked))
  (setv expired (is-api-limit-refusal "Failed to authenticate. API Error: 401 OAuth access token has been revoked." 401))
  (assert (not expired))
  ;; status を名乗らない面(pane の画面・旧い CLI・codex)は文の族の表に落ちる
  (setv worded (is-api-limit-refusal "You've reached your Fable limit. /model to switch models." None))
  (assert worded)
  (setv plain (is-api-limit-refusal "error_max_turns" None))
  (assert (not plain)))


(deftest test-classify-codex-update-dialog-down-steps
  (setv frame-sel1 (+ "✨ Update available!\n"
                      "› 1. Update now (runs npm install)\n"
                      "  2. Skip\n"
                      "  3. Skip until next version\n"
                      "Press enter to continue"))
  (<- obs1 (classify-codex frame-sel1))
  (assert (= obs1.dialog "codex-update"))
  (assert (= (list obs1.dialog-dismiss-keys) ["Down" "Down" "Enter"]))
  (assert (not obs1.startup-finished))
  ;; 0.142.x は "2. Skip" が初期選択(oracle: selected-option から Down 数を導出)
  (setv frame-sel2 (.replace frame-sel1 "› 1. Update now" "  1. Update now"))
  (setv frame-sel2 (.replace frame-sel2 "  2. Skip\n" "› 2. Skip\n"))
  (<- obs2 (classify-codex frame-sel2))
  (assert (= (list obs2.dialog-dismiss-keys) ["Down" "Enter"])))


(deftest test-classify-claude-dialogs
  (setv bypass (+ "Bypass Permissions mode\n"
                  "❯ 1. No, exit\n  2. Yes, I accept\n"
                  "Enter to confirm"))
  (<- b (classify-claude bypass))
  (assert (= b.dialog "bypass"))
  (assert (= (list b.dialog-dismiss-keys) ["Down" "Enter"]))
  (assert (not b.startup-finished))
  (setv fullscreen (+ "Try the new fullscreen renderer?\n"
                      "❯ 1. Yes, try it\n  2. Not now\n"
                      "Enter to confirm"))
  (<- fs (classify-claude fullscreen))
  (assert (= fs.dialog "fullscreen"))
  (assert (= (list fs.dialog-dismiss-keys) ["Down" "Enter"]))
  (setv managed "Managed settings require approval\nSettings requiring approval:\n  - statusLine")
  (<- m (classify-claude managed))
  (assert (= m.dialog "managed"))
  (assert (= (list m.dialog-dismiss-keys) ["Enter"])))


(deftest test-classify-claude-trust-dialog
  ;; 実物 frame(herdr demo-claude-2 で 2026-07-07 逐語採取。workspace path
  ;; 行のみ可変なので一般化)。claude CLI が未 trust の cwd で起動すると出す
  ;; startup gate — R9 で dismiss しないと wait-for-repl-idle が永久に idle を
  ;; 見ず、120s 上限縮退 → launch が prompt を dialog に送出して死ぬ実障害。
  ;; 長文の質問文は pane 幅次第で reflow されるため marker には使わない
  ;; (幾何学物理: 折返しは部分文字列一致を殺す)。
  (setv trust (+ "\n"
                 " Accessing workspace:\n"
                 "\n"
                 " /home/user\n"
                 "\n"
                 " Quick safety check: Is this a project you created or one you trust? (Like your own code,\n"
                 " a well-known open source project, or work from your team). If not, take a moment to\n"
                 " review what's in this folder first.\n"
                 "\n"
                 " Claude Code'll be able to read, edit, and execute files here.\n"
                 "\n"
                 " Security guide\n"
                 "\n"
                 " ❯ 1. Yes, I trust this folder\n"
                 "   2. No, exit\n"
                 "\n"
                 " Enter to confirm · Esc to cancel\n"))
  (<- t (classify-claude trust))
  ;; 既定選択が option 1(trust 側)なので dismiss は Enter 単発。doeff が
  ;; 制御する work_dir を信頼する = pre-seed(hasTrustDialogAccepted=True)と
  ;; 同じポリシー(bypass は既定 No,exit だから Down,Enter — trust は違う)
  (assert (= t.dialog "trust"))
  (assert (= (list t.dialog-dismiss-keys) ["Enter"]))
  ;; 選択行は行頭スペース付き ` ❯` — idle prompt と誤認しないこと
  (assert (not t.has-idle-prompt))
  ;; trust dialog は stuck-in-startup — launch watchdog の解除信号ではない
  (assert (not t.startup-finished)))


(deftest test-classify-unsubmitted-paste
  (setv frame "❯ [Pasted text +40 lines]")
  (<- obs (classify-claude frame))
  (assert obs.has-unsubmitted-paste)
  (<- clean (classify-claude "❯"))
  (assert (not clean.has-unsubmitted-paste)))


(deftest test-classify-unsubmitted-attachment-chip
  ;; issue #568(ADR-DOE-AGENTS-010 R1): [Image #N] 添付チップは prompt 行の
  ;; 外(直下の行・行頭空白)に描かれる — 空の ❯ prompt + チップ行
  ;; (2026-07-28 実 wedge の pane 形)を unsubmitted と判定する。検知は
  ;; composer 領域(最終 prompt 行とそれ以降)を見る。
  (<- wedged (classify-claude (+ "❯\n"
                                 "  [Image #150]\n"
                                 "\n"
                                 "  ⏵⏵ bypass permissions on (shift+tab to cycle)")))
  (assert wedged.has-unsubmitted-paste)
  ;; paste チップ・queued ヒントも prompt 行の直下に落ちる形がある
  (<- pasted (classify-claude "❯\n  [Pasted text #1 +12 lines]"))
  (assert pasted.has-unsubmitted-paste)
  (<- queued (classify-claude "❯\n  Press up to edit queued messages"))
  (assert queued.has-unsubmitted-paste)
  ;; 送信済み履歴のチップ(最終 prompt 行より上)は unsubmitted ではない
  (<- historical (classify-claude (+ "❯ [Image #3]\n"
                                     "⏺ Read file\n"
                                     "❯")))
  (assert (not historical.has-unsubmitted-paste)))


;; ---------------------------------------------------------------------------
;; DeliverMessage — live REPL paste + submit(盲窓物理は substrate 所有)
;; ---------------------------------------------------------------------------

(deftest test-deliver-message-yields-literal-submit
  (setv world (ImplWorld))
  (<- _ (run-codex world (deliver-message "%1" "hello agent")))
  (assert (= world.sent-keys [#("%1" "hello agent" True True)])))
