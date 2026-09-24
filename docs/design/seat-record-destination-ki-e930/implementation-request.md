# 実装依頼書: 席の env に記録サービスの宛先を agentd 自身が置く(card ki-e930b8506201)

- card: `acp:kanban-issue:ki-e930b8506201`(盤 agora-redesign)・計画の依頼: `lt-R6BA55GFR7NZG4P7F6F9A0XXDG`
- 設計: `docs/design/seat-record-destination-ki-e930/design.md`(この file と同じ dir)。反例と修正は `counterexamples.md`。
- 基準 commit: doeff `4da6ca4a3dd83bdf8bc57e5ecf799365721167be` 以降の main。
- 範囲: doeff の実装・試験・ADR の改め → main へ着地 → pool と会社 Mac へ配備 → 実機の受入。dotfiles の `tell.py` と
  ACP の宣言(`agentd-pool-join.yaml`)・Mac の宣言(`acp-single-mac.toml`)は**変えない**。

## 1. 問題(実測)

agentd が起こす手番の env に `RECORD_SERVICE_URL` が無く、32,768 byte を超える本文の `ai tell` がどの手番からも
「記録の service の宛先が宣言されていない(env RECORD_SERVICE_URL)」で断られる。agentd 自身は宣言 file の `[record].url` で
宛先を知っていて、自分の追記に使っている。pool pod で 2026-09-24 に再現し、`[record].url` と同じ値を明示すると
34,077 byte の本文が通った(郵便 `lt-SAGPVFCCQ31D6D2W5746B1STXA` delivered)= 欠けているのは値だけで、認証の問題は無い
(`evidence/pod-measurement.log`)。

## 2. 作るもの(公開契約)

- **C1**: `incarnation-charter-of` が組む charter(launch / resume / rehydrate / rebuild)の `session_env` に
  `RECORD_SERVICE_URL` = 参加の門 `join.record-sink-of` を通った値(agentd 自身が `RecordHttp` に渡す値と同じ)を置く。
  記録が無効な settings(試験の対照だけ)では置かない。
- **C2**: 走行者が持つ名の集合 `{AGORA_CONVERSATION_ID, AGORA_SEAT_OPENER, RECORD_SERVICE_URL}` を `effects` に 1 か所で宣言する。
  `join.seat-env-of` の門 (c) はこの集合を読む(`[agentd].seat_env` に 1 つでも在れば参加を断る・理由つき)。
  charter を組む側も同じ集合の名だけを書く。
- **C3**: 順序は seat_env → 走行者の名(後に勝つ)。
- **C5**: 機体が席へ渡す env(seat_env の対 + 走行者の宛先。会話の身元と手番の札は含まない)を **1 つの関数**で組み、
  charter はその組を書き、同じ組の指紋(sha256 などの不透明な値)を `session-attribution-of` が帰属(`launch_attribution` の
  agentd の欄・名 `nodeEnv`)に刻む。`next-arm-for-job` は温かい session の帰属の指紋が今の agentd の指紋と**違う・無い**時に
  send を選ばず、effort が違う時と同じ resume(候補を片付け、同じ session id で起こし直す・cache は保つ)を選ぶ。
  指紋の材料は判断の層に運ぶ settings から作る(`next-arm-for-job` の呼び手 `agentd.hy` 2004 行付近で渡す)。

内部で自由: `charter-with-conversation-env` を広げるか新しい関数を足すか / `AgentdSettings` の欄の持ち方
(ただし「`record_enabled` が真なのに URL が無い」状態を作れない形 — 例: URL の欄 1 つから `record_enabled` を導く。
`record_enabled=` を組む試験は 6 file・9 行)。

### なぜ C5 が要るか(盲検の反例・再現済み)

claude の headless は手番ごとに process が降り、2 手番目からは host の `continue-headless-process` が
**行に保存した生まれた時の `session_env`** に手番の env(札だけ)を重ねて起こす(`headless.hy` 319〜321・448〜469 行)。
C1 だけだと、`[record].url` を変えて agentd だけを起こし直した後(Mac は host と agentd が別の launchd unit)、既存の
温かい session は古い宛先のまま走る(`counterexamples/B_witness_continue.log`)。C5 はこれを起こし方の判断の 1 点で解く
(試作で確かめた: `evidence/proto_arm_on_node_env_change.{diff,py,log}` — 試作は参考。そのまま写さなくてよい)。

## 3. してはいけないこと

- 継承の名簿(`policy.SPAWN-INHERITED-ENV-KEYS` / `headless-spawn-env`)に名を足さない(R30 (4))。
- host(`headless.hy` / `launch.hy` / `policy.hy` の `TURN-*` と `overlay-without-turn-auth`)に走行者の名を教えない。
- 送りの手番の env(`judgment.turn-session-env-of`)に走行者の名を足さない(C5 で send を選ばないことで解く — 第 2 の書き手を作らない)。
- 宣言 file(`seat_env`)に `RECORD_SERVICE_URL` の写しを書かない。dotfiles `tell.py` を変えない。
- 名 `RECORD_SERVICE_URL` を `effects.py` の定義行の外で文字列として綴らない。

## 4. 検査(TDD の順: 失敗する試験を先に commit → 実装)

| id | 検査 | 置き場の目安 |
| --- | --- | --- |
| K1' | 記録の宛先を持つ settings で launch / resume / continue の 3 経路を fake の器で回し、**器が起こした process の env** に byte 同一の値。記録が無効なら無い。**手番の間で settings を差し替える**(A で launch → 行 → B の agentd が次の手番を起こす)と腕は resume で、起こした process の env は B の値。差し替えないなら send | `packages/doeff-agents/tests/sessionhost_charter_reaches_the_seat_deftests.hy` の隣 |
| K2 | `seat-env-of` が `RECORD_SERVICE_URL=…` を含む宣言を ValueError で断る | `packages/doeff-agents/tests/test_sessionhost_acp.py` の seat-env-of の検の隣 |
| K3 | 契約: 門 (c) の集合 == charter が書く走行者の名 ∧ 指紋の材料 == charter が書く組(同じ関数の出力)∧ 送りの手番の env(`turn-session-env-of`)に走行者の名が 1 つも無い | 同上 |
| K4 | join を迂回して seat_env に `RECORD_SERVICE_URL=http://bogus` を直接渡しても charter の値は走行者の値 | K1' の隣 |
| K5 | semgrep `doeff-agents-runner-owned-seat-env-spelled-once`: `["'](RECORD_SERVICE_URL\|AGORA_CONVERSATION_ID\|AGORA_SEAT_OPENER)["']` を `packages/doeff-agents/src/**` で禁じ、`effects.py` だけ除く(基準 commit で当たりは effects.py の 3 行だけ — 実測)。bad / clean の例つき | `.semgrep.yaml` |
| K6 | `next-arm-for-job`(純粋): 指紋が同じ → send / 違う → resume + 候補を片付ける / 指紋が無い → resume | next-arm-for-job の既存の検の隣 |
| K7 | join が席の settings file(`claude_settings_file`)を検める同じ点で、`env` ブロックが走行者の名を含めば断る(今日の `seat-settings.json` の鍵は `hooks` だけ — 実測) | `join.hy` の `claude-settings-file-of` の検の隣 |

ADR: `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` に新しい rule(R63 の見込み)を足し、R51 (1)(c) と R30 (4) の文を改める。
R51 (4)(doeff は席向けの名を綴らない)との区別を条文に書く — `RECORD_SERVICE_URL` は名も値も doeff が持つ(名 = `effects.RECORD_URL_ENV`・
値 = doeff の宣言 schema の `[record].url`)ので第 2 の既定は生まれない。`law` / `deftest` / `defsemgrep` を足したら
`docs/adr/enforcement-ledger.json` を**同じ commit** で更新する(ADR-DOE-ENFORCE-001 R5)。

走らせる検査は触った file の focused なものだけ(1 分以内)。全体の試験は撃たない(日次の役目)。
`.rs` は触らないので `make sync` は要らない。

## 5. 配備と実機の受入

1. main へ着地(personal の口座の会話から)。
2. **pool**: 新しい doeff を焼いた pool の image を建て、agent-control-plane の `deploy/acp-control/acpcluster.yaml` の pin を上げ、
   排水つきで roll する(pool の doeff は image に焼かれる — pod の realign は image の `doeff-revision` へ HEAD を合わせる)。
   `agentd-pool-join.yaml` は触らない。
3. **会社 Mac**: Mac の agentd を新しい doeff で起こし直す(dotfiles `cron_management/ACP-SINGLE-MAC.md` と `acp_single_mac.hy` の
   `--install-from` の経路)。宣言 file は触らない。host を起こし直す必要は無い(C5 により、配備前に生まれた温かい session は
   帰属に指紋が無いので次の手番で 1 度だけ resume になる)。
4. 受入(両方の機体で・配備の後に起こした手番で):
   - (a) 手番の中で `printenv RECORD_SERVICE_URL` の値と、その機体の宣言 file の `[record].url` が byte 同一
     (pool = `/etc/agentd/agentd.toml`・Mac = `~/.local/state/doeff/acp-agentd/agentd.toml`)。
   - (b) 33 KB を超える本文の `ai tell --kind note`(env を明示しない)が「本文は記録の service に置いて見出し(bodyRef)で投函した」で通り、
     `ai tell status <id>` が delivered、宛先の会話の手番の入力に本文が現れる。
   - (c) 上限内のメッセージは今日どおり(bodyRef が付かず ACP の行に本文が載る)。
   - (d) node の行の `spec.agentd.revision` が着地の commit を含む。
   - 会社 Mac の手番で測るには、会社 Mac に結ばれた会話の手番が要る。自分の会話が Mac に結ばれない時は、Mac に結ばれる会話へ
     測定を頼むか、その旨を報告に書く(黙って pool だけで受入を済ませない)。

## 6. 報告

`ai reply <この依頼の郵便 id> --kind report` で、次を書く:
着地の commit・配備(image の tag と pin の commit・Mac の agentd の版)・K1'〜K7 の試験名と結果・受入 (a)〜(d) の実測
(値・郵便 id・`ai tell status` の出力)・この依頼書からの逸脱(あれば「逸脱」の節で、何をなぜ替えたか)。
card のスレッドにも同じ要約を `ai kanban say acp:kanban-issue:ki-e930b8506201 --text-file <file>` で残す。
