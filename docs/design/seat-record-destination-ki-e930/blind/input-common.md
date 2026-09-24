# 盲検 A・B に渡す共通の入力

基準: doeff `4da6ca4a3dd83bdf8bc57e5ecf799365721167be`。読める checkout = `/home/kento/.worktrees/doeff-wt-ki-e930-plan`
(dotfiles の読み手は `/home/kento/dotfiles/agentcli/src/agentcli/tell.py`)。**どちらも読むだけ**(編集・commit・全数の試験・他の会話への連絡をしない)。

## 背景

agora の会話の手番は、各機体の agentd(doeff `packages/doeff-agents/src/doeff_agents/sessionhost/acp/`)が起こす
claude / codex の process で走る。手番の中の道具 `ai tell`(dotfiles `agentcli/src/agentcli/tell.py`)は、本文が
32,768 byte を超えるメッセージの本文を記録サービスに置き、見出しだけを ACP に投函する。記録サービスの宛先は
env `RECORD_SERVICE_URL` から読む(`tell.py` の `record_appender`)。agentd 自身は宣言 file の `[record].url` から
同じ宛先を知っていて自分の追記に使うが、手番の env には届いていないので、上限を超える本文はどの手番からも送れない。

## 固定した要件

- Q1. agentd が起こすすべての手番の process の env に `RECORD_SERVICE_URL` が在り、値はその agentd 自身が追記に使う宛先
  (参加の門 `join.record-sink-of` を通った値)と byte 同一。
- Q2. 32,768 byte を超える本文の `ai tell` が会社 Mac と pool pod の両方の手番から通る。
- Q3. 上限内のメッセージの振る舞いは変えない。`tell.py` は変えない。
- Q4. 宛先の値の置き場は各機体の宣言 file の `[record].url` の 1 か所のまま。席向けの写しを作らせない。
- Q5. 席は agentd の env を継がない(継承の名簿 `policy.SPAWN-INHERITED-ENV-KEYS` は 1 語も開けない)。

## 提案する設計

agentd は、自分が追記に使う記録サービスの宛先を、起こす手番の env に自分で置く。会話の身元
(`AGORA_CONVERSATION_ID` / `AGORA_SEAT_OPENER`)と同じ「走行者が持つ名」として扱う。

### 責務

| module id | 持つ知識・判断 | 変わるか |
| --- | --- | --- |
| `host-declaration` | 宣言 file の `[record].url`(Mac = dotfiles `cron_management/acp-single-mac.toml`・pool = 別 repo の ConfigMap) | 変わらない |
| `join-gate` | `join.hy`: 宣言の解釈と参加の門(`record-sink-of`・`seat-env-of` の門 (a)(b)(c)) | 門 (c) の名の集合を「走行者が持つ名」の集合へ差し替える |
| `runner-env-names` | `effects.py`: 走行者が持つ名の集合(新設の 1 定義点)と各名の綴り | 新設 |
| `agentd-settings` | `effects.AgentdSettings` と `runtime.settings_from_env`: 参加の門を通った記録の宛先を判断の層へ運ぶ | 欄を 1 つ運ぶ(`record_enabled` と食い違える 2 欄にしない) |
| `charter-assembly` | `judgment.incarnation-charter-of` と `charter-with-*`: 席の env を組む順序 | 走行者の名に記録の宛先を足す |
| `spawn-policy` | `policy.SPAWN-INHERITED-ENV-KEYS` / `headless-spawn-env`: 継承の名簿 | 変わらない |
| `seat-tool` | dotfiles `tell.py`: `RECORD_SERVICE_URL` を読んで本文を置く | 変わらない |
| `record-service` | 記録サービス: 追記の受理と認証 | 変わらない |

### 公開契約

- C1: agentd が起こす手番(launch / resume / rehydrate / rebuild — `incarnation-charter-of` が組むすべて)の
  `charter.session_env` に `RECORD_SERVICE_URL` = `record-sink-of` を通った値が在る。記録が無効な設定(試験の対照だけ)では置かない。
- C2: 走行者が持つ名の集合 = `{AGORA_CONVERSATION_ID, AGORA_SEAT_OPENER, RECORD_SERVICE_URL}` を `effects` の 1 か所で宣言する。
  `join.seat-env-of` の門 (c) はこの集合を読み、宣言に 1 つでも在れば参加を断る。charter を組む側も同じ集合の名だけを書く。
- C3: 順序は seat_env → 走行者の名。走行者の名が後に勝つ。
- C4: 継承の名簿・`tell.py`・上限内のメッセージの経路・記録サービスは変えない。

内部で自由: `charter-with-conversation-env` を広げるか新しい関数を足すか / `AgentdSettings` の欄の持ち方
(ただし「record_enabled が真なのに URL が無い」状態を作れない形)。

### 変更シナリオと主張(盲検の前に固定)

前提: P1 席は agentd と同じネットワークの見え方を持つ。P2 記録サービスは席の札で追記を受ける(認証は doeff の外)。
P3 席の env は `charter.session_env` だけが運ぶ。P4 宛先は起動時に読む不変の値。

- S1 hardware(機体を足す): 触るのは宣言 file の `[record].url` 1 行だけ。他の module は変わらない。
- S2 storage(記録サービスの所在が変わる): 各機体の `[record].url` 1 行。agentd と席が同時に新しい値を使う。
- S3 effects(走行者が運ぶ宛先が 1 つ増える — 例 `AGORA_CUSTODY_URL`): 変わるのは `runner-env-names`・`agentd-settings`・
  `charter-assembly`。`join-gate` のコードは変わらない。既にその名を `seat_env` に写している宣言は、移行の便で宣言側を同時に直す。
- S4 distribution(席のネットワークの見え方が agentd と違う配置): P1 の外で C1 の適用範囲外。契約の拡張として
  `host-declaration`・`join-gate`・`agentd-settings` が変わり、`charter-assembly` は変わらない。
- S5 concurrency: 除外(起動時に読む不変の値・並行の書き手なし)。
- S6 simulation(決定的な試験): `charter-assembly` は純粋な関数で、settings を組むだけで全経路を検められる。

### 予定している検査(B が「通過」を論じる対象)

- K1 deftest(`packages/doeff-agents/tests/sessionhost_charter_reaches_the_seat_deftests.hy` の形): 記録の宛先を持つ settings で
  launch / resume / continue を fake の器で回し、器が起こした process の env に byte 同一の値が在る。記録が無効なら無い。
- K2 deftest: `seat-env-of` が `RECORD_SERVICE_URL=…` を含む宣言を ValueError で断る。
- K3 契約の検: 門 (c) の集合 == charter が書く走行者の名の集合(組んだ charter の session_env の名から seat_env の名を引いた集合と
  effects の集合が等しい)。
- K4 deftest: join を迂回して seat_env に `RECORD_SERVICE_URL=http://bogus` を直接渡しても charter の値は走行者の値。
- K5 semgrep `doeff-agents-runner-owned-seat-env-spelled-once`: `["'](RECORD_SERVICE_URL|AGORA_CONVERSATION_ID|AGORA_SEAT_OPENER)["']`
  を `packages/doeff-agents/src/**` から禁じ、`effects.py` だけ除く。
- 既存: `test_sessionhost_headless.py` の継承の検、semgrep `doeff-agents-does-not-spell-seat-facing-env`
  (`.semgrep.yaml` の同名の rule・`ACP_BASE|AGORA_BRAIN_URL|HERDR_HUD_STATE_BACKEND` を src から禁じる)。

## 読むべき実コード(基準 commit・上の checkout の相対 path)

- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy` — `charter-with-seat-env` / `charter-with-conversation-env` /
  `incarnation-charter-of`(2784 行付近〜)
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.hy` — `seat-env-of`(830 行付近)・`record-sink-of`・`join-plan-of`(1060〜1160 行付近)
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py` — `RECORD_URL_ENV`(1077)・`CUSTODY_URL_ENV`(1271)・
  `CONVERSATION_ID_ENV` / `SEAT_OPENER_ENV`(1289)・`class AgentdSettings`(1671〜・`record_enabled` 1857)
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py` — `settings_from_env`(200〜260 行付近)・`_record_sink_of_env`(542〜)・
  handler の組み立て(960〜990 行付近)
- `packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy` — `incarnation-charter-of` の呼び(1515 行付近)・
  `ListHostDrivers :env settings.seat-env`(825 行付近)
- `packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy` — `SPAWN-INHERITED-ENV-KEYS`・`seat-env-credential-shaped-offenders`・
  `session-env-admission-error`
- `packages/doeff-agents/tests/sessionhost_charter_reaches_the_seat_deftests.hy`・`packages/doeff-agents/tests/test_sessionhost_headless.py`
- `.semgrep.yaml` の `doeff-agents-does-not-spell-seat-facing-env`
- `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の R30・R51
- dotfiles `/home/kento/dotfiles/agentcli/src/agentcli/tell.py` の `record_appender`(2287 行付近)と `RECORD_SERVICE_URL_ENV`(436)
