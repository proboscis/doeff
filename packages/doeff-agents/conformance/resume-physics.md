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

## cross-binding resume の追加プローブ(ADR-DOE-AGENTS-006 改訂 Phase 0)

測定日 2026-08-11。claude 2.1.226(model haiku)、codex-cli 0.145.0-alpha.30
(`codex exec` / `codex exec resume`)。ACP 枠切れ failover の「別アカウントでの
同一会話 resume」(決裁 decision-acp-ratelimit-attempt-loss-2026-08-11)に向けた
transcript transplant の物理校正。

- **(a) transcript 不在の `claude --resume <uuid>` は loud に失敗する**:
  rc=1、stderr `No conversation found with session ID: <uuid>`。silent な新規
  会話への縮退はなく、transcript も projects dir も生成されない。
  → 偽 CLI(conformance_agent.py)の旧挙動「不在でも inherited=\"\" のまま
  silent 継続」は実物と乖離 — 本改訂で loud exit(rc=1・同文言)に整合させた。
- **(b) 別 CLAUDE_CONFIG_DIR へ symlink した transcript の `--resume` は文脈を
  保つ**: 所有 profile A(cryptic)で `--session-id <uuid>` 起動し codeword を
  記憶させ、**別アカウントの実 profile B**(cryptic-1 — oauth account が A と
  別)の `projects/<同 mangled key>/` へ transcript を symlink して
  `CLAUDE_CONFIG_DIR=B claude --resume <uuid>` → codeword を正答
  (AZURE-KOMOREBI-77)。resume 後も symlink は symlink のまま残り、追記は
  解決先(A の実体)へ届く(A の transcript が伸びるのを実測)。cc
  share_resume_session が毎日実働させている物理の機械記録化。
  - 補足: claude の credential は CLAUDE_CONFIG_DIR ごとに macOS Keychain の
    `Claude Code-credentials-<hash>` に分離される — transcript transplant は
    auth を運ばない(auth は binding の責務、transcript は会話の責務)。
- **(c) codex の cross-home resume は sessions 配下の link で可能**:
  home B に rollout link が無い `codex exec resume <uuid>` は rc=1 の loud
  エラー `Error: thread/resume: thread/resume failed: no rollout found for
  thread id <uuid> (code -32600)`(silent 新規化なし)。source rollout を
  `<B>/sessions/<同 Y/M/D>/<同 filename>` へ symlink すると resume が成立し
  文脈を保つ(EMBER-KITSUNE-9 正答)。追記は symlink 解決先(A の実体)へ
  届き、link は link のまま。conversation_json の rollout_path(絶対 path)を
  codex 自身が辿ることは**ない** — resume の rollout 発見は常に自 home の
  `sessions/` 走査であり、cross-home は link の敷設が必須。
  - 補足: 実測は同一 auth.json(symlink)で実施 — rollout 発見は home 単位で
    account 非依存(rollout に account は埋まらない)。

### transplant 物理の含意(sessionhost 実装が従う凍結事実)

- claude: source = `<source CLAUDE_CONFIG_DIR>/projects/<mangle(realpath
  work_dir)>/<conversation.session_id>.jsonl` → target = `<binding
  config_dir>/projects/<同 key>/<同 uuid>.jsonl` の symlink。周辺 artifact
  (`projects/<key>/sessions-index.json`・`session-env/<sid>`・
  `file-history/<sid>`)も同型 link(あれば張る・無ければ skip —
  dotfiles agentcli share.py の 4 対と同型。所有 profile は source 行の
  effective_identity で既知なので registry 走査は持ち込まない)。
- codex: source = conversation_json の rollout_path(絶対)→ target =
  `<binding の sessions root>/<rollout_path の sessions/ 以下同相対 path>` の
  symlink。sessions root は native 形 = `<codex_home>/sessions`、二軸形 =
  `<profile_dir>/sessions`(home view の sessions は bundle 側実体への link —
  substrate compose-home-view)。
- source 不在(実体でも解決可能な symlink でもない)は typed reject
  `transcript_not_discoverable` — 実 CLI の loud 失敗(a)(c1)を launch 前に
  前倒しする(壊れた宿りを作らない)。
