# agentd conformance suite — 契約(C0-1 台本設計)

> **裁定追補(2026-07-06、ACP plan U1 / C 段階 plan 裁定台帳 8)**: 本文書の
> 「Rust = oracle」は破棄された。Rust 実装は正しさの基準であったことは一度も
> なく(schema 検証は無裁可 subset の fail-open)、以後は「凍結された旧実装
> (rollback 可用性のためだけに保存)」と読み替える。**S20 以降、result-contract
> 検証の正解定義は JSON Schema 仕様**(検証器 = jsonschema 参照実装の輸入、
> 仕様適合は upstream の公式 JSON-Schema-Test-Suite CI から継承)。suite の
> canonical gate は CONFORMANCE_AGENTD_BIN = Hy session host。旧実装挙動を
> expected に固定する parity ピンは(歴史ピンとしても)置かない。

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
| `{"await_monitor_ack": {"timeout_s": N}}` | **monitor が現在のフレームを消費するまで保持**: wire `session.get` を poll し、session 行が存在し `awaiting_response` latch がクリアされる(= active marker を観測、main.rs:3629)まで進まない。ハザード 4 の決定的同期 |
| `{"report_result": {"payload": {...}} }` / `{"report_result": "schema_invalid"}` | report-result-mcp 経由で報告。応答(accepted / -32002 / -32003)を journal へ |
| `{"sleep_s": N}` | 経過待ち(stall 系は knob で数秒に短縮) |
| `{"scroll": N}` | N 行の改行で古い行を tail 窓から掃き出す(TUI 再描画の等価物。tail-30 の active marker 退役など) |
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
| F-dialog-codex-update / F-dialog-bypass / F-dialog-fullscreen / F-dialog-managed | R9 fast-path 対象ダイアログ(S18 で Rust detector と verbatim 一致まで確定) | 決定的 dismisser の発火確認 |
| F-dialog-trust | claude workspace-trust gate(2026-07-07 実物 frame verbatim。旧 F-trust-dialog を置換 — 旧文言は現行 CLI と乖離・未使用だった) | 5 つ目の R9 dialog(Rust oracle 非在)。dismiss = Enter 単発(既定選択が trust 側)。未 handle だと launch 永久 hang する実障害の再発防止 |
| F-dialog-unknown | R9 のどの detector にも合致しない架空の startup dialog(idle/active marker も無し) | launch fail-closed の再発防止: R9 外 dialog に prompt を送出して silent hang する旧縮退の禁止(2026-07-07 契約修正) |

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
| S4 | schema-invalid 報告 → 拒否(agent 可視面 = MCP tool error `isError:true`+schema 文言。**-32002 は daemon wire のみ** — ハザード 5)・`result_payload_json` 非永続 → solicitation 後に valid 報告 → done | 002 R3 / 0035 R4 | — | P | M2 |
| S5 | F-menu-codex で turn-end 到達 → judge が solicitation より**先**(journal で受領順を確認)→ unblock keys 受領 → 続行 | 002 R5/R6 | — | P | M2 |
| S6 | F-frozen + stall T 超過 → bounded judge(3)→ failed・reason `interactive-prompt-blocked:` 接頭・cause InteractivePromptBlocked false | 002 R5/R7 | (a) | P | M2 |
| S6b | judge 無効(空文字)変種: stall 点 = 即 typed failure / turn-end 点 = solicitation へ degrade。judge error(不在パス)変種: stall 点 = typed failure | 002 R7 | — | P | M2 |
| S7 | F-failed → failed・cause 写像どおり(`authentication failed`→RunnerUnavailable false / `timeout`→TimedOut true / その他→RunFailed false) | taxonomy 凍結 | (a) | P | M2 |
| S8a | F-api-limit 単独 → status `blocked_api`(**非終端が正** — active_statuses に含まれ、await_result は -32000 timeout。level-triggered: pane が変われば回復し得る) | main.rs:1918/2912 | — | P | M2 |
| S8b | failure マーカー + api-limit 文言の複合フレーム → failed 時の output 写像で cause **RateLimited retryable=true** が wire に載る(last_validation_error 無しの failed のみ output 写像が走る — main.rs:3895-3905) | ACP ADR 0042 下流 | (a) | P | M2 |
| S9 | 帯域外 tmux kill: result 報告済→done(result-first)/ 未報告→exited・cause Lost retryable=true。ACP 側 200 discriminator は ACP EntityReadsSpec 所管(重複させない) | main.rs:3922-3951 | (c) | P | M2 |
| S10 | 報告済 payload が agentd 再起動後も await_result で読める + 終端後の再報告 = already_reported:true / 未報告終端後の報告 = -32003(**両者とも daemon wire `session.report_result` でのみ観測可** — MCP relay 面では潰れる、ハザード 5) | COALESCE 規律(main.rs:2339) | (d)(e) | P | M2 |
| S11 | agent_type=codex・CODEX_HOME 無し → **tmux 呼び出し前に** launch Err(tmp root に tmux 痕跡ゼロ・session 行無し・shim 未実行)。claude・CLAUDE_CONFIG_DIR 無し → warning のみ(DOE-003 R3 staged)。**caveat**: pre-launch trust writer は CODEX_HOME/CLAUDE_CONFIG_DIR を **daemon プロセス env** から fallback 参照(main.rs:1500/1553)するため、harness は `extra_env` で daemon にスクラッチ home を渡す(未指定だと実 `~/.codex`/`~/.claude` を汚す) | DOE-003 R1/R3 | (g) | P | M1 |
| S12 | claude launch(M1)→ `<CLAUDE_CONFIG_DIR>/.claude.json` に canonicalized work_dir の `hasTrustDialogAccepted=true` が temp+rename で書かれる。pre-seed 済なら fake は即 REPL 描画 | 42fb28fa 傷跡 | — | P | M1 |
| S13 | claude launch(M1)→ fake が受領した argv に `--settings {"disableAllHooks":true}` と `--mcp-config`(doeff_result stdio)を確認。codex launch → `-c mcp_servers.doeff_result.command=` を確認 | 49b3549b 傷跡 / 0035 配線 | — | P | M1 |
| S14 | 解決済み実効 identity(CODEX_HOME / CLAUDE_CONFIG_DIR)が session 行に永続化される — **oracle expected-red**(agent_sessions に identity 列なし、走査で確認済) | DOE-004 契約拡張 | (b) | **X** | M1 |
| S15 | solicitation 1 回目と 2 回目の間で agentd 再起動 → `result_solicitations_used` が生存し合計 2 で終端(awaiting_response latch は再起動でクリアされる仕様と両立) | 002 law counters-durable | (e) | P | M2 |
| S16 | 2 session 並走・片方を異常系フレームに → 他方が golden path を完走。tick は panic/error を捕捉して継続(run_worker_tick)。per-session 隔離の粒度は oracle では tick 単位 — Hy 実装は session 単位隔離を満たすこと(観測可能な assert は「他方の完走」で共通) | DOE-004 R3 | (f) | P | M2 |
| S17 | in-process result endpoint ↔ host endpoint の意味論 parity(per-kind ゲートとの継ぎ目) | ACP plan 補遺 | — | X(C1 後) | — |
| S18 | R9 fast-path: 5 ダイアログの dismissal keys を journal で受領確認(codex update→Down×2+Enter / bypass→Down,Enter / fullscreen→Down,Enter=Not now / trust→Enter(既定 Yes,I trust — pre-seed S12 と同じ意図) / managed→Enter)。**観測物理で契約修正**: codex-update/bypass/fullscreen/trust は `wait_for_repl_idle` のみ(launch 経路 = M1)で発火し M2 では到達不能。managed のみ monitor loop でも発火(main.rs:3604)なので M2 で mid-session 検証。tty は canonical+ICRNL(Enter=`\r`→`\n`、Down+Enter は 1 行で到達 = `\x1b[B` を待つ)。managed の bare Enter は内容で判別不能なので `observed_active_at` set(managed 分岐でしか立たない)を主 assert に。trust の bare Enter は順序で証明(dialog 表示中に `\n` が着く → その後にしか prompt paste が match しない)。**trust は Rust oracle 非在**(2026-07-07 のカバレッジ欠落修正 — 実物 frame で detect_dialog=None を実証してから追加)。**fail-closed(2026-07-07 契約修正。2026-07-17 に oracle main.rs も同一契約を採用し乖離解消)**: R9 外の未知 dialog で repl-idle 予算(`DOEFF_AGENTD_REPL_IDLE_MAX_WAIT_SECS`、既定 120s)が尽きたら、旧 oracle の「構わず paste」ではなく typed error で launch を fail させる — prompt 未配送、登録済みの booting 行は terminal failed(timed_out)へ遷移(2026-07-17 契約改訂、S22 参照 — 行未永続ではなくライフサイクル)、mux session は terminal-first で掃除(FAILED 永続化 → cleanup。成功は cleaned_at、画面 tail をエラーに同梱)。silent hang の構造的禁止 | 002 R9 | — | P | M1(update/bypass/fullscreen/trust/unknown)/ M2(managed) |
| S19 | launch-timeout watchdog: F-frozen のまま startup 完了マーカーを出さない → `DOEFF_AGENTD_LAUNCH_TIMEOUT_SECS` 超過で failed・TimedOut true / zombie(`{"exit":0}` → idle shell)→ exited・Lost true / stale-observation → exited・Lost true。**stale-obs の black-box 形状**: tmux session は生かしたまま(2 枚目の window を足す)監視対象 pane を帯域外 kill → 以後 tick は `tmux_capture` で abort し `last_observed_at` が凍結、stale 分岐(tmux probe より前)が `DOEFF_AGENTD_STALE_OBSERVATION_SECS` 超過で reap。3 knob は全て env-only なので `extra_env` 経由 | main.rs:3485-3587 | — | P | M2 |

| S20 | result-contract 検証 = JSON Schema 仕様(U1 復元契約): items 違反 payload は report 時 reject → solicitation → in-session fix(ACP steward 実障害の形そのまま)/ meta-schema 違反 schema は session.launch で fail-closed 拒否 | doeff#482 / U1 裁定 | — | P(hy gate のみ — 旧実装は fail-open で基準外) | M2 |

| S21 | resume / fork(ADR-DOE-AGENTS-006): 偽 CLI に transcript 契約(--session-id 受理・rollout 書き・`--resume`/`codex resume|fork` での文脈再開)を追加し、kill → `session.resume` の文脈保持 / fork の系譜と独立性 / identity-unknown の typed 失敗 / 並行 incarnation reject / 世代整合(旧 incarnation の遅延 report が新行を汚さない)を daemon+socket ゲートで検証 | ADR-006 R6 | — | P(hy gate のみ — Rust oracle は resume/fork 非対応で基準外)— `conformance/test_s21_resume_fork.py`(実 tmux、claude/codex 両レーン parametrize + identity-unknown)。実 CLI 物理(codex resume/fork の受理形・claude --fork-session の transcript 意味論)は `conformance/resume-physics.md` の Phase 0 プローブ(2026-07-13 実測)で校正済み | M1(claude/codex resume/fork)/ M2(identity-unknown) |
| S22 | 登録は ready gate より前(issue agentd-session-registration-after-ready-gate、2026-07-17): `session.launch` が repl-idle 待ちでブロックしている間に booting 行が観測可能(SQLite 帯域外読み — wire に現れない義務の観測例外。handshake < 数秒の回復)。予算切れ後は同じ行が terminal failed(timed_out)へ遷移し booting 残置ゼロ、mux session は terminal-first で掃除(FAILED 永続化 → cleanup、成功は cleaned_at — #542 レビュー由来の順序)。awaiting latch は登録時点から武装(prompt 配送 launch のみ — `await_monitor_ack` の同期点保存) | issue agentd-session-registration-after-ready-gate | — | P(hy gate。oracle main.rs 側は cargo test が同一契約をピン) | M1(unknown dialog で ready 不達) |

| S23 | koine session surface v0: `session.adopt` の順序義務 — 実在確認 → 成功時のみ登記。missing target は typed error(error_code `adopt_target_not_found`・文字列 — koine 由来の新契約は数値表へ足さない)で**行を残さない**。成功は adopted=1・lifecycle 既定 interactive・不透明 id。冪等(同一 substrate.ref の非終端行は既存行を返す)。`session.list` の adopted filter(対話席一覧の主 filter)込み | koine semantics-v0 operations / ADR-DOE-AGENTS-007 R1/R2 | — | **P(hy gate のみ — Rust oracle は koine 非対応で基準外。前例 = S20/S21)** | 帯域外 mux session + wire |
| S24 | adopted id は不透明: sessionhost 採番(uuid4)・呼び手の名 / substrate.ref を埋め込まない・`session.get(id)` で往復する | koine semantics-v0 resource 表 | — | P(hy gate のみ) | wire |
| S25 | turn 打刻の単一 writer 面: descriptor {pane_id 第一鍵, agent_name 第二鍵} を sessionhost が adopt 済み非終端行へ解決。turn_open → holder='agent'・wait NULL / turn_close → holder=wait.who(無ければ 'work')・wait は **opaque 保存**(再 parse しない)。未 adopt 打刻 = 正直 no-op(`{"adopted":false}` の ok 応答)+ `daemon.status` counters(turn_stamp_unadopted / turn_stamp_resolved、in-memory)。行を作らない | koine turn-stamp-path / ADR-007 R5 | — | P(hy gate のみ) | wire + SQLite 読み |
| S26 | **interactive 不刈り(koine 安全条項 1 — 本 stage の中核)**: lifecycle=interactive 行は launch timeout / stale observation / zombie reaper / mux 消滅の 4 条件下でも**非終端のまま**(finished_at / terminal_cause 無し)。last_observed_at の前進を「monitor は生きて評価した上で刈らなかった」witness として assert(dead monitor の空緑を防ぐ)。TDD red 実測 2026-07-21: 実装前は 4 経路すべてが interactive 行を terminal 化した。boot watchdog 経路(mid-launch daemon 死)は M2 から到達不能のため宣言済み gap(免除 arm は booting arm より前に置かれ構造的に守られる) | koine 安全条項 1 / ADR-007 R3 | — | P(hy gate のみ) | M2(lifecycle=interactive) |
| S27 | 鏡原則(koine 安全条項 3): adopt 席の pane を帯域外 kill → 行は exited 化も削除もされず、`session.get` / `session.list` の wire に導出 field `substrate_present: false` / `substrate_checked_at`(毎読み probe — 保存しない)が載る。adopted filter の一覧にも残る | koine 安全条項 3 / ADR-007 R4 | — | P(hy gate のみ) | wire + 帯域外 kill |

koine 系 wire 導出 field(S23-S27 で凍結): `session.get` / `session.list` /
`session.adopt` の応答に `stalled`(= turn_holder=='agent' かつ
now-turn_since > `DOEFF_AGENTD_TURN_STALL_SECS`(既定 1800)。close 済み =
WAIT 待ちは経過によらず false・signal only)、免除(adopted または
interactive)かつ非終端の行に `substrate_present` / `substrate_checked_at`。
store には保存されない(level-triggered 読み出し導出)。

X 項目を P として数えて「oracle green」を主張することは禁止。
**C0-2 の完了 = 全 P green on Rust + 全 X の expected-red 記録**。

### C0-1 残りシナリオの実装状況(2026-07-05・全 P green on Rust)

S1-S5/S7-S10/S15 に続き、以下を Rust oracle に対して green 実装済み
(X の S14 は expected-red として記録)。テストファイルは
`test_<sid>_*.py`。

| # | 状況 | テスト | 実装上の要点 / 観測物理 |
|---|---|---|---|
| S6 | ✅ green | test_s6_stall_judge.py | judge blocked → 1 tick 1 unblock、`prompt_unblock_attempts` が 3 に達して exhausted failed。judge inconclusive(空 verdict 表)変種も同じ bound(`session_prompt_judge_inconclusive`×3)。stall 台本は S5 と同じ盲窓同期(`await_monitor_ack`)+ ハザード 3 の >100 行 scroll |
| S6b | ✅ green | test_s6b_judge_unavailable.py | judge 無効("")= stall 点で attempt 0 のまま即 typed failure(`no prompt judge configured`)。judge 不在パス = attempt 1 消費して `prompt judge failed`。turn-end 点の solicitation degrade は M2 バッチ全体(既定 judge 無効)が既に witness |
| S11 | ✅ green | test_s11_auth_profile_gate.py | 上行の caveat 参照(daemon-env trust fallback) |
| S12 | ✅ green | test_s12_claude_trust_preseed.py | `os.path.realpath(work_dir)` で canonical 化した project key に `hasTrustDialogAccepted=true`、temp+rename の残骸不在も確認 |
| S13 | ✅ green | test_s13_argv_wiring.py | journaled argv から claude の `--settings {"disableAllHooks":true}`/`--mcp-config`(doeff_result stdio)/`--strict-mcp-config`、codex の `-c mcp_servers."doeff_result".command=`/`.args=[...]` を厳密一致。claude 変種は M1 golden path 完走も兼ねる |
| S14 | 🟥 expected-red(X) | test_s14_identity_persistence_expected_red.py | `agent_sessions` に identity 列無し + 実効 CODEX_HOME が行の全値に非出現。Hy gate で positive assert に差し替え |
| S16 | ✅ green | test_s16_concurrency_isolation.py | 1 daemon 2 session、B(F-failed)先行 → A(golden path)完走を両方 wire で確認 |
| S18 | ✅ green | test_s18_dialog_fastpaths.py | 上の S18 行参照(M1/M2 分割・canonical tty・observed_active_at 主 assert) |
| S19 | ✅ green | test_s19_watchdogs.py | 上の S19 行参照(3 reaper・env-only knob・stale の black-box 形状) |

**harness 拡張(このバッチ)**: `AgentdHarness.extra_env`(daemon プロセス
env: env-only watchdog knob + trust fallback home)と `Scenario.launch_m1`
(PATH-shadowing。session_env の PATH prepend だけでは pane の login zsh が
PATH を再構築して実 codex/claude を解決してしまう実測があるため、
scenario 専用 `ZDOTDIR` の rc で shim dir を再 prepend して決定化)。

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
| unblock budget(3) | `--prompt-unblock-attempts` / `DOEFF_AGENTD_PROMPT_UNBLOCK_ATTEMPTS`(main.rs:646 — 実装時走査で実在確認。S6 は既定 3 のまま検証) | ✓ |
| stale-observation 閾値(300s) | `DOEFF_AGENTD_STALE_OBSERVATION_SECS`(S19 用に oracle へ追加した env-only knob、`effective_stale_observation_threshold_seconds` — 既定 300s・意味論不変。launch timeout と同じく flag 無しの env 専用なので harness は `extra_env` で daemon プロセスに渡す) | ✓(追加済) |
| wait_for_repl_idle 上限(120s) | 定数 — fake は即 idle を描画するので実害なし | 不要 |

oracle への変更は「意味論を変えない設定追加」のみ許す(挙動変更禁止)。

## Non-goals

- 実モデル実行(7/7 前は特に禁止 — conformance-agent で代替。既存の
  `agentd_real_agent_result_retry_e2e_support.py` 系は本 suite に含めない)
- Rust agentd の挙動変更(上記 knob 追加を除く)
- per-kind ゲートの実装(C1 以降)

## 発見済みハザード(suite 設計に焼き込み済み)

1. **デフォルト judge は実 claude**: `DEFAULT_PROMPT_JUDGE_CMD = claude -p
   --settings '{"disableAllHooks":true}' --model haiku`(main.rs:150)が
   turn-end 判定点で solicitation より先に最大 3 回走る(:3722)。suite の
   non-goal(実モデル禁止)に直撃するため、**harness は既定で
   `--prompt-judge-cmd ""`(無効)を渡す**。judge シナリオ(S5/S6)だけが
   scripted judge を明示配線する。Hy 実装の conformance 実行時も同じ既定を
   維持すること。
2. **terminal cause の output 写像は「reason 無し failed」限定**:
   last_validation_error が立つ経路(solicitation 超過・stall)は明示
   カテゴリが先に書かれ、output 写像(RateLimited/TimedOut/…)は
   走らない(first-write-wins、main.rs:3895-3905)。S7/S8b のフレームは
   これを前提に設計されている。
3. **turn-end には stable tail が要る**: フレーム描画後に出力を続けると
   turn-end に到達しない。idle glyph は capture 100 行内に残留するので、
   stall 系(S6)は 100 行超の scroll で idle glyph を掃き出してから
   凍結フレームを出す。
4. **launch の盲窓(blind window)**: `session.launch` は prompt の
   paste + Enter + confirm ループ(main.rs:1794-1830、confirm 再送で
   最大 ~5s)を**同期的に終えた後**に初めて session 行を upsert する。
   monitor は行の無い session を観測できないため、この窓の中で描画して
   退役させたフレームは**存在しなかったのと同じ**。特に
   `awaiting_response` latch は active marker の観測でしか
   クリアされない(main.rs:3629)ので、active フレームを盲窓内で
   scroll してしまうと latch が永久に残り turn-end・judge・solicitation
   がすべて死ぬ(S5 で実測)。sleep での回避は confirm 再送回数に依存する
   race — フレーム退役の前に必ず `await_monitor_ack` を挟む。
   付随物理: 台本 agent は tty echo を切らないので paste された prompt が
   `› ` 行上に残留し、confirm_literal_prompt_submitted が「未送信」と
   誤検知して Enter を 3 回再送する(= 盲窓が ~3s 伸びる + 余分な `\r` が
   tty バッファに溜まる)。await_keys はこの余分な Enter に耐える設計を
   保つこと。
5. **MCP relay は数値エラーコードと already_reported を潰す**(S4/S10
   worker 発見): `report-result-mcp` relay(main.rs:863/908)は
   `RpcResponse.error_code` を破棄し(main.rs:213)、agent 可視面を
   `{"content":[{"text":...}],"isError":bool}` に平坦化する。**-32002 /
   -32003 / `already_reported:true` は daemon 制御 socket の
   `session.report_result` 応答にのみ現れる**。契約 assert は面ごとに:
   agent 面 = `isError` + 文言、wire 面 = driver が `AgentdClient.request`
   で直接話す。Hy 実装は**両面とも**再現すること(relay が数値コードを
   透過し始めたら parity break)。
6. **daemon は SIGTERM で DB lease を解放しない**(S10 worker 発見):
   lease TTL 10s(main.rs:21)内の再 serve は「lease is active」で即死
   (main.rs:1092)。harness.restart() が TTL 越え再試行を吸収済み。
   再起動耐久系(S10/S15)を書くときは restart() を使うこと。

## 実装メモ(C0-1 残り = sonnet worker へ委譲する範囲)

- 土台は `agentd_result_retry_e2e_support.py` の流儀を踏襲: cargo build
  → `doeff-agentd --db <tmp> --socket <tmp> --monitor-interval-ms 100
  --max-running 2 serve` → AgentdClient で駆動 → journal + wire +
  (許可範囲の)SQLite 読みで assert。
- 禁止 env(`ANTHROPIC_API_KEY*`)は session_env に入れない(hard reject)。
- フレームは描画後静止(stable 条件)。paste 確認の再 Enter
  (confirm_literal_prompt_submitted)が来ても壊れない await_keys にする。
- シナリオごとに完全独立の tmp root(db/socket/homes/PATH dir)。
