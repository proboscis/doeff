# agentd conformance suite — 契約(C0-1 台本設計)

Status: **CONTRACT FIXED(2026-07-05)— physics は Rust agentd
`packages/doeff-agentd/src/main.rs`(branch adr0035-byte-faithful-transport
tip)の実走査で確定**。ADR-DOE-AGENTS-004 R4(conformance 先行・Rust =
oracle)の実体。上位 plan: agent-control-plane
`docs/acp-2026-07-05-agentd-hy-session-host-plan.md`(C0-1 / C0-2)。
先例: ACP `sdk/python/integration/mini_conformance.py`(black-box・
依存清潔性 = import グラフ検査・PASS/FAIL 集計)、および本 repo の
`packages/doeff-agents/tests/agentd_result_retry_e2e_support.py` /
`test_agentd_byte_faithful_transport_e2e.py`(fake-agent 駆動 e2e —
本 suite はこのパターンを吸収・拡張する)。

## 目的

Rust agentd の hardening(cargo ~93 tests + 2026-07-05 の trust/hooks
傷跡)を**挙動として**結晶化し、Hy 再実装(DOE-005 / plan C2-C3)の
parity 到達を black-box で判定する交代ゲートの前半。実モデル・実クォータは
一切使わない。

## 2 ゲートの区別(ACP session-host plan より、混同禁止)

- **per-kind ゲート**(将来の新 CLI 追加時): 直接束縛・in-process。
  host 義務は検査しない(できない)。
- **交代ゲート**(本 suite): **転送束縛 = agentd の公開 socket 越し**。
  寿命の外部性(再起動生存・呼び手死後の継続)はここでしか目撃できない。
  driver の依存は AgentdClient 相当の wire client + stdlib のみ
  (mini_conformance と同じ import グラフ検査を suite 自身に適用)。
  例外: 「wire に現れない義務」(payload 永続化・カウンタ耐久)の assert
  に限り SQLite の**読み**を許す(書きは禁止)。

## 構成要素

1. **conformance-agent** — 台本駆動の偽 CLI(Python stdlib)。2 つの
   起動モードを持つ:
   - **M1: PATH-shadowing モード** — `claude` / `codex` という名前で
     テスト専用 PATH ディレクトリに置き、`session_env["PATH"]` で pane の
     shell に解決させる。command override を使わないので **agentd の実
     launch 経路全体**(resolve_launch_command → build_*_argv →
     ResultChannel 配線 → wait_for_repl_idle → prompt paste)が走る。
     起動時に自分の argv / env を journal へ記録 → driver が
     `--settings {"disableAllHooks":true}`(claude)や
     `-c mcp_servers.doeff_result.command=...`(codex)の実在を assert。
   - **M2: command-override モード** — `command=` 明示で起動(既存
     e2e 支援と同型)。monitor 経路のシナリオ用。result 報告は
     `$DOEFF_AGENTD_BIN report-result-mcp --session $DOEFF_RESULT_SESSION_ID
     --socket $DOEFF_AGENTD_SOCKET` を直接叩く(main.rs:1306 の
     mcp_command_args と同物理)。
2. **scripted judge** — `--prompt-judge-cmd` / env
   `DOEFF_AGENTD_PROMPT_JUDGE_CMD` に差す決定的スクリプト。stdin の
   pane capture を台本キーで引き、strict JSON `{blocked, keys, reason}`
   を返す(keys は whitelist 内のみ: 単一英数 or
   Up/Down/.../Enter/Escape 等、main.rs:3247)。judge-unavailable 系は
   (i) 空文字 = judge 無効化(stall 点 = 即 typed failure / turn-end 点
   = solicitation へ degrade)と (ii) 実在しないコマンド = judge error
   の 2 変種を区別してテストする。
3. **driver** — シナリオごとに専用 tmp root(db/socket/tmp CODEX_HOME/
   CLAUDE_CONFIG_DIR)で `doeff-agentd serve` を起動し、launch → 観測 →
   assert を wire 越しに行う。高速化 knob(§knobs)で実時間実行。

## conformance-agent 台本形式

台本 = JSON 配列、パスは env `CONFORMANCE_SCRIPT`。journal 追記先は
`CONFORMANCE_JOURNAL`(受領 argv/env/キー/report 応答を JSONL で記録)。

| step | 意味 |
|---|---|
| `{"render": "<frame-id or literal>"}` | pane にフレームを描画。frame-id は下の凍結フレーム語彙(F-*)を参照 |
| `{"await_keys": {"expect": "<substr>", "timeout_s": N}}` | tty 入力を待ち journal へ記録(prompt paste・solicitation・unblock keys の受領確認) |
| `{"report_result": {"payload": {...}} }` / `{"report_result": "schema_invalid"}` | report-result-mcp 経由で報告。応答(accepted / -32002 / -32003)を journal へ |
| `{"sleep_s": N}` | 経過待ち(stall 系は knob で数秒に短縮) |
| `{"record_env": ["CODEX_HOME", "CLAUDE_CONFIG_DIR", ...]}` | 起動時 env を journal へ |
| `{"exit": code}` | 終了(zombie/exited 系) |

### 凍結フレーム語彙(F-*)— Rust monitor の実マーカーに束ねる

monitor 分類は lowercase tail の**部分文字列一致**(main.rs:2775-3229)。
conformance-agent のフレームはこの実マーカーを再現する。**この表は
checklist (a) の凍結対象の一部**(marker→分類は impl 所有になるが、
分類→category→retryable は契約所有):

| frame | 内容(verbatim 断片) | monitor の読み |
|---|---|---|
| F-idle-codex | 行頭 `› ` | idle prompt(turn-end 候補) |
| F-idle-claude | 行頭 `❯`(U+276F) | idle prompt |
| F-active-codex | `working (` or `esc to interrupt` | active(`starting mcp servers` 中は無効) |
| F-active-claude | 最終 `❯` の上の行に `… (` | live spinner = active |
| F-turn-activity-claude | `⏺` / `⎿` | startup 完了(active ではない) |
| F-failed | `fatal error` 等(tail 10 行) | status failed |
| F-api-limit | `rate limit exceeded` / `quota exceeded` 等(tail 30 行) | status blocked_api |
| F-waiting | `Type your message` 等(raw 一致) | status blocked |
| F-menu-codex | `› 1. Switch…`(idle glyph でメニュー描画) | idle に偽装したメニュー(R6 の核心) |
| F-frozen | idle でも active でもない任意画面(pager/login 風) | stall watchdog 対象 |
| F-trust-dialog | claude trust ダイアログ風フレーム | pre-seed 無しの hang 再現用 |
| F-dialog-codex-update / F-dialog-bypass / F-dialog-fullscreen / F-dialog-managed | R9 fast-path 対象の 4 ダイアログ | 決定的 dismisser の発火確認 |

turn-end 判定は「idle prompt AND not active AND **500 字 tail が前回
snapshot と一致(stable)**」(main.rs:2832, 2932)なので、フレームは
描画後に静止させること(継続出力すると turn-end に到達しない)。

## カバレッジ行列(確定版)

タグ: **P** = parity(Rust oracle green 必須 = C0-2 ゲート)/
**X** = extension(oracle expected-red を明記、Hy 実装のみ gate)。

| # | シナリオ | 検証する規則 | checklist | タグ | モード |
|---|---|---|---|---|---|
| S1 | golden path: launch → F-active → report_result(valid) → F-idle 静止 → done、await_result が byte-faithful payload を返す | 0035 / result-first(main.rs:3689) | (d) 前半 | P | M2 |
| S2 | turn-end・result 無し → solicitation 文言(`AGENTD RESULT CONTRACT: ...`)受領 → 報告 → done。solicitation 中 non-terminal を DB で確認 | 002 R1/R4 | — | P | M2 |
| S3 | solicitation budget(2)超過 → failed・reason `...after 2 solicitation(s)`・cause RunFailed retryable=false | 002 R2/R8 | (a) | P | M2 |
| S4 | schema-invalid 報告 → -32002 受領・`result_payload_json` 非永続 → solicitation 後に valid 報告 → done | 002 R3 / 0035 R4 | — | P | M2 |
| S5 | F-menu-codex で turn-end 到達 → judge が solicitation より**先**(journal で受領順を確認)→ unblock keys 受領 → 続行 | 002 R5/R6 | — | P | M2 |
| S6 | F-frozen + stall T 超過 → bounded judge(3)→ failed・reason `interactive-prompt-blocked:` 接頭・cause InteractivePromptBlocked false | 002 R5/R7 | (a) | P | M2 |
| S6b | judge 無効(空文字)変種: stall 点 = 即 typed failure / turn-end 点 = solicitation へ degrade。judge error(不在パス)変種: stall 点 = typed failure | 002 R7 | — | P | M2 |
| S7 | F-failed → failed・cause 写像どおり(`authentication failed`→RunnerUnavailable false / `timeout`→TimedOut true / その他→RunFailed false) | taxonomy 凍結 | (a) | P | M2 |
| S8 | F-api-limit → blocked_api → cause RateLimited **retryable=true** が wire(get/await)に載る | ACP ADR 0042 下流 | (a) | P | M2 |
| S9 | 帯域外 tmux kill: result 報告済→done(result-first)/ 未報告→exited・cause Lost retryable=true。ACP 側 200 discriminator は ACP EntityReadsSpec 所管(重複させない) | main.rs:3922-3951 | (c) | P | M2 |
| S10 | 報告済 payload が agentd 再起動後も await_result で読める + 終端後の再報告 = already_reported:true / 未報告終端後の報告 = -32003 | COALESCE 規律(main.rs:2339) | (d)(e) | P | M2 |
| S11 | agent_type=codex・CODEX_HOME 無し → **tmux 呼び出し前に** launch Err(tmp root に tmux 痕跡ゼロ)。claude・CLAUDE_CONFIG_DIR 無し → warning のみ(DOE-003 R3 staged) | DOE-003 R1/R3 | (g) | P | M1 |
| S12 | claude launch(M1)→ `<CLAUDE_CONFIG_DIR>/.claude.json` に canonicalized work_dir の `hasTrustDialogAccepted=true` が temp+rename で書かれる。pre-seed 済なら fake は即 REPL 描画 | 42fb28fa 傷跡 | — | P | M1 |
| S13 | claude launch(M1)→ fake が受領した argv に `--settings {"disableAllHooks":true}` と `--mcp-config`(doeff_result stdio)を確認。codex launch → `-c mcp_servers.doeff_result.command=` を確認 | 49b3549b 傷跡 / 0035 配線 | — | P | M1 |
| S14 | 解決済み実効 identity(CODEX_HOME / CLAUDE_CONFIG_DIR)が session 行に永続化される — **oracle expected-red**(agent_sessions に identity 列なし、走査で確認済) | DOE-004 契約拡張 | (b) | **X** | M1 |
| S15 | solicitation 1 回目と 2 回目の間で agentd 再起動 → `result_solicitations_used` が生存し合計 2 で終端(awaiting_response latch は再起動でクリアされる仕様と両立) | 002 law counters-durable | (e) | P | M2 |
| S16 | 2 session 並走・片方を異常系フレームに → 他方が golden path を完走。tick は panic/error を捕捉して継続(run_worker_tick)。per-session 隔離の粒度は oracle では tick 単位 — Hy 実装は session 単位隔離を満たすこと(観測可能な assert は「他方の完走」で共通) | DOE-004 R3 | (f) | P | M2 |
| S17 | in-process result endpoint ↔ host endpoint の意味論 parity(per-kind ゲートとの継ぎ目) | ACP plan 補遺 | — | X(C1 後) | — |
| S18 | R9 fast-path: 4 ダイアログフレーム各々に対し想定 dismissal keys を journal で受領確認(codex update→Down×N+Enter / bypass→Down,Enter / fullscreen→Not now / managed→Enter) | 002 R9 | — | P | M2 |
| S19 | launch-timeout watchdog: F-frozen のまま startup 完了マーカーを出さない → `DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS` 超過で failed・TimedOut true / stale-observation(300s)・zombie(idle shell)reaper → exited・Lost true | main.rs:3485-3587 | — | P | M2 |

X 項目を P として数えて「oracle green」を主張することは禁止。
**C0-2 の完了 = 全 P green on Rust + 全 X の expected-red 記録**。

## TerminalCause 凍結表(checklist (a) — 契約所有、走査値で凍結)

| 事象 | category | retryable |
|---|---|---|
| api-limit マーカー | RateLimited | **true** |
| tail に timeout/timed out/deadline | TimedOut | **true** |
| launch timeout / stale observation / zombie / tmux-gone | TimedOut / Lost / Lost / Lost | **true** |
| authentication failed | RunnerUnavailable | **false** |
| invalid json / protocol error | ProtocolError | **false** |
| solicitation 超過(turn-end 無結果) | RunFailed | **false** |
| interactive-prompt stall | InteractivePromptBlocked | **false** |
| その他 failed | RunFailed | **false** |

first-write-wins(set_terminal_cause_if_absent + DB COALESCE)も契約。
Hy 実装がこの表を変える場合は ADR 改訂が先(黙った変更は conformance red)。

## testability knobs(走査結果: ほぼ既存)

| knob | 既存手段 | 備考 |
|---|---|---|
| monitor tick | `--monitor-interval-ms`(既存 e2e は 100ms) | ✓ |
| stall T(180s) | `--prompt-stall-secs` / `DOEFF_AGENTD_PROMPT_STALL_SECS` | ✓ |
| solicitation budget(2) | `--result-solicitations` / env | ✓ |
| judge cmd | `--prompt-judge-cmd` / env(空 = 無効) | ✓ |
| launch timeout(60s) | `DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS` | ✓ |
| unblock budget(3) | config 定数 :136 — flag の有無を実装時に確認、無ければ knob 追加(意味論不変) | 要確認 |
| stale-observation 閾値(300s) | **定数 :3485 — knob 無し**。S19 用に env knob を oracle に追加(意味論不変・本 README に記録) | 追加要 |
| wait_for_repl_idle 上限(120s) | 定数 — fake は即 idle を描画するので実害なし | 不要 |

oracle への変更は「意味論を変えない設定追加」のみ許す(挙動変更禁止)。

## Non-goals

- 実モデル実行(7/7 前は特に禁止 — conformance-agent で代替。既存の
  `agentd_real_agent_result_retry_e2e_support.py` 系は本 suite に含めない)
- Rust agentd の挙動変更(上記 knob 追加を除く)
- per-kind ゲートの実装(C1 以降)

## 実装メモ(C0-1 残り = sonnet worker へ委譲する範囲)

- 土台は `agentd_result_retry_e2e_support.py` の流儀を踏襲: cargo build
  → `doeff-agentd --db <tmp> --socket <tmp> --monitor-interval-ms 100
  --max-running 2 serve` → AgentdClient で駆動 → journal + wire +
  (許可範囲の)SQLite 読みで assert。
- 禁止 env(`ANTHROPIC_API_KEY*`)は session_env に入れない(hard reject)。
- フレームは描画後静止(stable 条件)。paste 確認の再 Enter
  (confirm_literal_prompt_submitted)が来ても壊れない await_keys にする。
- シナリオごとに完全独立の tmp root(db/socket/homes/PATH dir)。
