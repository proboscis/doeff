# resume / fork の実 CLI 物理(ADR-DOE-AGENTS-006 R6 Phase 0 プローブ)

conformance の偽 CLI(`conformance_agent.py` の会話契約)と sessionhost の
argv / 発見物理を**実物の claude / codex CLI で校正**した実測記録。
herdr-physics.md と同じ役割 — 契約 README は tmux-oracle のまま、ここは
「偽物が真似ている本物」の測定値を凍結する。

測定日 2026-07-13。claude Claude Code(model haiku-4-5)、
codex 0.144.1(`codex exec` 非対話 / 対話 `codex fork`)。

## claude

- **identity は launch 時に鋳造できる**: `--session-id <uuid>` を渡すと、その
  UUID がそのまま transcript ファイル名になる。transcript の家は
  `<CLAUDE_CONFIG_DIR>/projects/<mangled realpath cwd>/<uuid>.jsonl`
  (mangle = 非英数字→`-`、cwd は realpath 済み)。
  → sessionhost は R1 でこれを使う(host が UUID 鋳造 → `--session-id` 注入 →
  boot 前に stored fact)。**発見に頼らない**唯一の kind。
  - 実測: `--session-id 5ef346d2-…` → `projects/-private-…-work/5ef346d2-….jsonl` が出現。
- **resume は文脈を保つ**: `claude --resume <uuid> "<prompt>"` は同じ transcript
  を継続し、前ターンで覚えた語(AZURE-FALCON-42)を答えた。
- **fork は新 UUID を鋳造し、親の文脈を継ぐ**: `claude --resume <uuid>
  --fork-session "<prompt>"` は**親と別の**新 UUID の transcript を作り
  (`ad21aca5-…` ≠ 親 `5ef346d2-…`)、かつ親の文脈(同じ語)を答えた。
  → 新 identity は事前に分からない = **fork は事後発見**(monitor arm)。
- argv 物理の含意: `build-claude-argv` の `--session-id` 注入(fresh)と
  `build-claude-resume-argv` の `--resume`(+ fork は `--fork-session`)は
  実 CLI のフラグ名・意味と一致。prompt は argv に載る(print mode の実測)
  が、sessionhost は live REPL 配送なので載せない — フラグ集合のみ校正対象。

## codex

- **identity は CLI 側が鋳造する(事前指定不可)**: 起動すると
  `<CODEX_HOME>/sessions/<Y>/<M>/<D>/rollout-<ts>-<uuid>.jsonl` が出現し、
  先頭行が `{"type":"session_meta","payload":{"id":"<uuid>","cwd":"<abs>", …}}`。
  `session_id` と `id` は同値で両方入る。cwd は絶対パス(実測は realpath 済み
  `/private/tmp/…`)。
  → sessionhost は R1 でこれを**事後発見**する(rollout 先頭行の cwd-match、
  `payload.id` を identity に採る)。実装の発見 arm と一致。
  - 実測 meta 先頭: `{"payload":{"session_id":"019f5a57-…","id":"019f5a57-…","cwd":"/private/…/work","originator":"codex_exec", …}}`
- **resume は文脈を保つ**: `codex … resume <uuid> "<prompt>"` は既存 rollout を
  継続し、前セッションの語(EMBER-WOLF-7)を答えた。非対話は
  `codex exec resume <uuid>`、対話は `codex resume <uuid>`。sessionhost は
  live REPL(対話)なので `codex … resume <uuid>` を使う。
- **fork は新 UUID を鋳造する**: `codex --yolo fork <uuid>` は起動しても
  即座には rollout を書かず、**最初のターンで**親と別の新 UUID の rollout を
  作る(`019f5a59-…` ≠ 親 `019f5a57-…`、同 cwd)。
  → fork の新 identity も事前不明 = 事後発見。**ただし rollout はターン発生
  まで書かれない**ため、発見 arm が数 cycle 空振りしてから捕獲するのは正常
  (level-triggered 設計とちょうど噛み合う)。
- argv 物理の含意: `build-codex-resume-argv` が base argv 末尾に
  `resume <id>` / `fork <id>` を付ける形は、root options を subcommand の前に
  置く実 CLI の usage(`codex [OPTIONS] <COMMAND> [ARGS]`)と一致
  (`codex --yolo fork <id>` が実際に起動した)。

## 偽 CLI(conformance_agent.py)が真似ている点と割り切り

- 真似る: claude の `--session-id` 受理 + `projects/<mangled>/…jsonl` 生成、
  `--resume`/`--fork-session` の文脈継承と fork 新 id 鋳造;codex の rollout
  `session_meta{id,cwd}` 生成、`resume`/`fork` サブコマンドの継承と新 id 鋳造。
  → これで daemon の identity 捕獲(claude 即時 / codex・fork 事後発見)が
  実物と同じ経路で走る。
- 割り切り(実害なし): 偽物は fork の rollout を**起動時に即書く**
  (本物 codex は初ターンまで遅延)。sessionhost の発見 arm は level-triggered
  で「まだ無い」を許容するので、即時/遅延どちらでも同じく正しく捕獲する —
  タイミングの差は契約に影響しない。偽物は語彙の再生成(会話内容)は模さない;
  S21 が検証するのは identity の連続性と系譜であって、モデルの応答内容ではない。

## S21 テストへの反映

`test_s21_resume_fork.py` は上記物理の偽 CLI 版を daemon+socket ゲートで走らせ、
kill→resume の文脈保持 / fork の新会話・系譜・独立性 / identity-unknown reject /
one-live-incarnation reject / 世代整合を検証する(claude・codex 両レーン M1)。
