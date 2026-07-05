;;; Executable ADR: agent 実行 = effects+handlers、agentd = Hy session host.

(require doeff-adr.macros [defadr defsemgrep rule law])
(import doeff-adr.macros [fact interpretation counterexample])


(defadr ADR-DOE-AGENTS-004
  :title "agent 実行は effect 語彙 + kind 別 defhandler に分解し、agentd は『寿命の外部性』だけを提供する Hy 製 session host(session を resource とするミニ control plane)へ再実装する — conformance 先行で Rust を oracle に交代"
  :status "proposed"
  :scope ["packages/doeff-agents"
          "packages/doeff-agentd(現 Rust = oracle)"
          "docs/adr/defadr_doeff_agents_004_effects_session_host.hy"]
  :problem
    [(fact
       "agentd は god daemon 化し(argv 組立・trust・monitor・solicitation・judge・taxonomy・DB・socket)、protocol 物理が Rust builder と Python adapter に二重実装されて claude 側だけ腐った(trust ダイアログ永久ハング・hooks 継承死は Rust 側のみ修正が届いた)。"
       :evidence "doeff 42fb28fa / 49b3549b(2026-07-05 live 傷跡)")
     (fact
       "ACP は doeff-agents でなく agentd の暗黙 wire に結合しており、新 CLI(opencode/kimi/AWS 系)追加の経路が『Rust の god daemon を編集する』しかない。"
       :evidence "ACP src/Acp/App/Agentd*.hs; ユーザー討議 2026-07-05 深夜")]
  :context
    [(interpretation
       "agentd の存在理由は効果の解釈ではなく寿命の外部性(セッションが呼び手より長生きする・複数 user 間で真実が一つ)。意味論は doeff-native であり、daemon とは handler stack を寿命の外側でホストする場所にすぎない(ユーザー裁定)。多重化点は host の内側 = kubelet/CRI 同型で、ACP は multi-backend を持たない。")
     (interpretation
       "実装言語は Hy + doeff(ユーザー裁定)。monitor は常駐 continuation ではなく、session 行(store-as-truth)から毎 cycle 再導出する level-triggered reconciler program — agentd は ACP と同じ存在論のミニ control plane になり、新しい理論を要さない。")]
  :decision
    [(rule R1 "effect 語彙が interface: BuildLaunch / PreLaunchSetup / ClassifyPane / DeliverMessage / WireResultChannel(+substrate: SessionStore / Tmux / Fs / Env / Clock / Proc)。共有 policy program(monitor・bounded solicitation・judge・taxonomy・result 検証)は 1 本で、impl は書かない。substrate の Fs / Env は C2 で追加 — per-kind trust 物理(S11/S12: claude .claude.json temp+rename・codex config.toml・process env fallback)は kind 所有だが、impls/ は substrate-clean で生 IO を持てないため、FS / env 読みも effect 境界を通る。")
     (rule R2 "kind 追加 = defhandler モジュール 1 個 + kind スキーマ + conformance green。同一モジュールが直接束縛(呼び手 process 内)と host 束縛(RPC 転送)の両方で動く — impl は substrate-clean(生 IO 禁止、substrate effect のみ yield)。")
     (rule R3 "agentd(Hy)は外部性の 4 点だけを所有する: socket・単一 writer actor(SQLite session 行)・毎 cycle の reconciler 起動・lease。RPC method は program に写像され、handler stack が解釈する。continuation は永続化しない — 真実は行のみ。")
     (rule R4 "conformance 先行: Rust agentd を oracle に black-box 契約 suite(mini_conformance 前例)+ 台本駆動の conformance-agent(偽 CLI、実クォータ非消費)を先に整備し、Hy 実装は parity 到達で交代。cargo 93 tests + 2026-07-05 の trust/hooks 傷跡を挙動として結晶化してから Rust を退役する。")
     (rule R5 "host は kinds.list で {kind, apiVersion, スキーマ} を広告し、ACP は宣言時に照合して未知 kind/version を fail-loud 拒否する。wire は有限の versioned 語彙に限る(任意 effect のリモート転送は禁止)。")
     (rule R6 "デプロイは frozen 環境から(pin 済み専用 env)。dev venv / target-debug 依存の自己参照(子守りが子守られる開発環境に依存する)を禁止する。")]
  :laws
    [(law protocol-physics-has-one-home
       :statement "protocol_physics(kind) => single_defhandler_module never_duplicated_across_languages"
       :counterexamples
         [(counterexample "Rust builder と Python adapter に同じ CLI の物理を二重実装し、片方だけ修正する")])
     (law daemon-owns-only-exteriority
       :statement "session_host => socket + writer_actor + reconciler_schedule + lease; semantics_lives_in_programs_and_handlers"
       :counterexamples
         [(counterexample "solicitation 回数や taxonomy を host 固有コードに焼き付ける")])
     (law truth-is-rows-not-continuations
       :statement "monitor => level_triggered_rederivation_from_session_rows; continuations_never_persisted"
       :counterexamples
         [(counterexample "中断した program の continuation を直列化して再起動時に復元する")])
     (law conformance-before-cutover
       :statement "rust_retirement => hy_impl_passes_oracle_conformance including_trust_and_hooks_scars"
       :facts
         [(fact
            "C0-2 交代ゲート前半は達成済み: conformance suite 31/31 green on Rust oracle(全 P green・S14 は X として expected-red 記録)+ cargo test -p doeff-agentd 94 passed / 0 failed。"
            :evidence "packages/doeff-agents/conformance @ doeff 0b67cd5c(2026-07-05): pytest 31/31・cargo 94/0")]
       :counterexamples
         [(counterexample "conformance 無しで Hy 版に切り替え、solicitation/turn-end の hardening が退行する")])]
  :enforcement
    ;; C1(effect 語彙 + policy program)と同一チェンジセットで substrate-clean
    ;; を実 enforcement 化。conformance suite ゲートは C0-2 で green 済み
    ;; (conformance-before-cutover law の :facts 参照)。
    [(defsemgrep no-new-agent-physics-in-rust-agentd
       :languages ["generic"]
       :pattern "fn build_kimi_argv"
       :message "新 CLI の protocol 物理を Rust agentd に足すのは ADR-DOE-AGENTS-004 R2 違反。kind 追加は doeff-agents の defhandler モジュール + conformance。"
       :bad ["fn build_kimi_argv(params: &LaunchParams) -> Vec<String> {"]
       :good ["; doeff-agents/impls/kimi.hy に defhandler を書く"])
     ;; R2 substrate-clean: impls/(per-kind defhandler 置き場)は substrate
     ;; effect(SessionStore / Tmux / Fs / Env / Clock / Proc)を yield する
     ;; のみ — 生 IO(subprocess / sqlite3 / open / os.system)を直接呼ぶことを
     ;; 禁止する。glob は C1 で `packages/doeff-agents/impls/` を先行予約し、
     ;; C2 実装時に import 可能なパッケージ内
     ;; `packages/doeff-agents/src/doeff_agents/sessionhost/impls/` へ具体化
     ;; (doeff-agents は hatchling src-layout — src 外のモジュールは wheel に
     ;; 入らず import 不能)。installed rule は .semgrep.yaml の
     ;; doeff-agents-substrate-clean-impls。
     (defsemgrep substrate-clean
       "doeff-agents-substrate-clean-impls"
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/kimi.hy"
         "source" ";; per-kind impl が subprocess を直接叩く違反\n(import subprocess)\n(defn launch [argv] (subprocess.run argv))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/opencode.hy"
         "source" ";; per-kind impl が sqlite3 を直接読む違反\n(import sqlite3 [connect])\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/awskind.hy"
         "source" ";; per-kind impl がファイル IO を直接行う違反\n(defn read-home [path] (with [f (open path)] (.read f)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/geminikind.hy"
         "source" ";; per-kind impl が shell を直接叩く違反\n(defn kick [cmd] (os.system cmd))\n"}]
       [{"relative-path" "packages/doeff-agents/src/doeff_agents/sessionhost/impls/cleankind.hy"
         "source" ";; substrate-clean な per-kind impl: substrate effect を yield するのみ\n(defhandler cleankind-handler\n  (ClassifyPane [agent-type output]\n    (resume (classify-frame output)))\n  (DeliverMessage [pane-id text]\n    (<- _ (TmuxSendKeys :pane-id pane-id :text text :literal True :submit True))\n    (resume None)))\n"}
        {"relative-path" "packages/doeff-agents/src/doeff_agents/session_store_sub.py"
         "source" "# substrate handler 側(impls/ の外)は生 IO を持ってよい\nimport sqlite3\nimport subprocess\n"}])]
  :plans ["../agent-control-plane 側 master plan: docs/acp-2026-07-05-agentd-hy-session-host-plan.md"])
