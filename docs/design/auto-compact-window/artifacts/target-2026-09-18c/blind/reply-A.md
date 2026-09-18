調査は完了しています。以下が反例の報告本文（全文）です。共有ソースは変更せず、pytest は 1 本も走らせず（読み取りと、モジュールを import しただけの純関数評価のみ）、他会話への連絡もしていません。

---

# 1. 現実的な変更要求とその理由

**要求（CC1 段 2 そのもの）**: 計画席（lead）に 800,000、実装席（worker）に 200,000 の閾値を配る。

理由は入力が自分で立てている通りです。R4 が要件として在り、段 2 として計画済みで、動機（1 手番平均 550k・600k 超が費用の 59%）から見て lead と worker を同じ値で運ぶ必然性がない。lead は設計の全体を抱えるので広く、worker は触る file の周辺だけなので狭くて足ります。前提 (a)(b)(c) を 1 つも変えない、最も素直な次の一歩です。

# 2. 覆る主張・前提と、予想に反して変わる責務

## 2-1. 実測: 宣言は argv 導出点に届かない（launch の腕でも）

公開契約 `charter-object` は「launch / rehydrate の腕は charter を丸ごと params にする（素通し）」「resume の腕は `judgment.resume-params-of` の名簿が写す」と書いています。**前者は偽**です。

agentd は器を socket 越しの RPC で動かします（合成の根 = `/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:596` が `SessionRpc` を刺す／`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/acp/handlers.py:1036-1073`）。その受け口 `session.launch` は、wire の params を**閉じた 23 欄の名簿から作り直します**（`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/host.hy:928-976` の `build-launch-program-params`）。`auto_compact_window` はその名簿に無い。

```
auto_compact_window in program params? False
keys: ['agent_type', 'allow_metered_billing', 'backend_kind', 'binding', 'command',
       'context_file', 'effort', 'events_root', 'expected_result', 'launch_attribution',
       'lifecycle', 'max_running', 'mcp_servers', 'model', 'prompt',
       'repl_idle_max_wait_seconds', 'session_env', 'session_id', 'session_name',
       'skip_trust_setup', 'socket_path', 'work_dir', 'workspace_seed']
```

つまり今日の本番では、どの腕を通っても argv に載る値は **code の定数 400,000 ちょうど**です。R3（agentd は charter の値を読み、値の方策を code に置かない）が、実効では逆になっています。CC2 の「値の調整は宣言側だけ・code の差分 0 行」も、この状態のままでは成り立ちません。

## 2-2. 名簿は 1 つではなく、直列に 4 つある

`resume-params-of` は「resume の腕の名簿」として正しく直っています（`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy:2320-2343`）。しかしその下流に、同じ形の**無宣言の名簿がさらに 3 つ**あります。

| 順 | 場所 | 欄を持つか |
| --- | --- | --- |
| 1 | `acp/judgment.hy:2334-2340` `resume-params-of` | 有り（今回追加） |
| 2 | `sessionhost/host.hy:928-976`（launch）／`host.hy:1347-1367`（resume の `program-params`） | 無し |
| 3 | `sessionhost/launch.hy:1276-1325` `resume-session` の `launch-params` | 無し |
| 4 | `sessionhost/headless.hy:287-290`（行に残す `launch-overlay`）／`headless.hy:350-357`（`continue-headless-process`） | 無し |

`grep -c auto_compact_window` は `launch.hy` / `headless.hy` / `host.hy` のいずれも 0 です。

## 2-3. 責務表に無い「4 つ目の起こす腕」

責務表の `agentd-arms` は launch / resume / rehydrate の 3 つですが、実際にはもう 1 つ CLI process を起こす路があります。温かい session への `session.send` で process が降りていた時、`--resume` で**起こし直してから**本文を書く路です（`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/headless.hy:405-418` → `continue-headless-process` `headless.hy:336-374`）。ここは行の `launch_overlay` から `agent_type / work_dir / model / effort / mcp_servers / expected_result` だけを取り出して argv を組み直します。

この路は例外的ではありません。claude は割り込みの SIGINT で降りる（`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/headless_protocol.py:723-726` の註）ので、設計された割り込み（段 10 lane 10n）のたびに次の手番はここを通ります。host の再起動後（段 10 lane 10h）も同じです。

R5 を「launch / resume / rehydrate の 3 つ」と字義通りに読むなら、この路は契約の適用範囲外です。しかしそれ自体が問題で、**契約が母集団を数え落としている**ことになります。

## 2-4. 前提 (c) は実測と食い違う

「会話が宣言できる欄の閉語彙は現状 model / effort / workDir」は、ACP の現物と合いません。`/Users/s22625/repos/agent-control-plane/src/Acp/App/Scheduling/Inputs.hs:744-780` の `ConversationAgent` は model / profile / workDir / effort / **compactAt** / interruptEscalationSeconds / fallback / declaredAt / generation を持ち、契約 `/Users/s22625/repos/agent-control-plane/docs/contracts/agora-kinds.json:183` に `compactAt`（0〜100 の整数）が宣言済みです。

`compactAt` は**同じ「会話の圧縮の閾値」を別の単位（文脈使用率の %）で持つ、既存の別系統**です（`acp/judgment.hy:881-912` の `compact-at-of` / `compaction-due`、`acp/judgment.hy:955-1004` の `next-arm-for-job` が `compact` を受けて rehydrate へ倒す）。新しい `auto_compact_window` は同じ会話の同じ文脈に同時に効きます。

## 2-5. 会話の宣言として配るなら、さらに 6 点が知識を要求する

役ごとの値を配る経路は、前提 (b)（方策の行は `current` の 1 つ・既定 charter は全会話で共通）を守る限り**会話の宣言に乗せる**しかありません。すると `Plan.charterFor`（`/Users/s22625/repos/agent-control-plane/src/Acp/App/Messaging/Plan.hs:146-180`）に欄を通すだけでは済まず、agentd 側の以下が「この欄は process の旗か・変えたら器を作り直すか・温かい送りでは効かないと名乗るか」を知る必要があります。

- `acp/effects.py:329-341` の `AgentSetting` / `AGENT_SETTINGS` / `AGENT_SETTINGS_RESTART_ON` / `AGENT_CAPABILITIES` / `CHARTER_SETTING_KEYS`
- `acp/judgment.hy:688-702` `capabilities-of`（node が名乗る能力の表）
- `acp/judgment.hy:752-782` `ignored-settings-of`（黙って落とさないための条件 `AgentSettingIgnored`）
- `acp/judgment.hy:672-686` `session-affinity-key-of`（器を使い回す鍵）
- `acp/judgment.hy:955-1004` `next-arm-for-job`（effort と同じく「旗が違えば `--resume` で起こし直す」腕が要る）
- `acp/judgment.hy:791-800` `session-attribution-of`（起こした時の値を帰属に刻む点。読み口は `judgment.hy:828-835` の `session-effort-of` に相当）

`effort` が既にこの 6 点を全部通っているのが、必要な工数の実測値です。CC1 の「route-dispatcher と acp-charter だけ」は、この 6 点と 2-2 の名簿 3 つを数えていません。

# 3. どの知識が漏れ、何が契約拡張か

## 漏れているのは「幅」でも「綴り」でもなく、**寿命**

`autocompact-derivation` は幅・床・縮退の向き・受ける型をよく隠しています。隠せていないのは「**この値は process を起こす瞬間にしか効かない = 起動の旗である**」という寿命の知識です。この 1 事実を知らないと正しく書けない判断点が、2-2 の名簿 4 つと 2-5 の 6 点に散っています。

各名簿は「起動の旗になる欄の全集合」を暗黙に知っている前提で書かれた**閉じた写し**です。旗を 1 つ足すと 4〜5 箇所が同時に古くなる。これが隠すはずの知識の漏洩の正体で、`--effort` の位置を凍結したことや幅の定数を 1 点にしたことでは緩和されません。

## R7 の観測性がこの漏れに効かない

R7 は「誤りは縮退し、縮退したことが外から読める」ことを求め、実装はそれを「argv 自身が名乗る（`ps` に `--autocompact auto`）」で満たしています。しかし**欄が落ちた時の縮退先は `auto` ではなく床 400,000** です。`ps` にも `backend_ref.argv` にも `--autocompact 400000` と出て、それが宣言由来か床由来かを区別する手段がありません。条件も log も出ません。lead が 800,000 を宣言しても、無音で正常な argv に見えます。

## 検が捕まえられない理由

`/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/tests/sessionhost_acp_rehydrate_deftests.hy:1129-1143` の検は名前が `test-every-arm-that-wakes-the-seat-carries-the-compaction-threshold` ですが、検めているのは 2-2 の 1 段目（`resume-params-of`）だけです。そして deftest が使う偽の器 `/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/src/doeff_agents/sessionhost/acp/fake.py:551-560` は params を `dict(params)` で丸ごと控え、JSON にできるかだけを見ます — **本物の RPC が持つ名簿を模していない**。偽の器のほうが本物より寛容なので、落ちる場所をまたいで緑になります。適合検査 `/Users/s22625/.worktrees/doeff-wt-autocompact-window/packages/doeff-agents/conformance/test_s13_argv_wiring.py` は `autocompact` を 1 度も言及しません。

## 契約拡張との区別（欠陥ではない部分）

以下は**意図した公開契約の拡張**で、欠陥ではありません。

- ACP の会話の宣言語彙に欄を足す（`agora-kinds.json` / `messaging.json` / `Intent.hs` / `Inputs.hs` / `charterFor`）
- wire の `LaunchParams` に欄を足す（`host.hy` の受理形）
- file が複数変わること自体

欠陥は次の 3 点です。(1) 拡張点が 5 つあるのにどこにも宣言されていない。(2) 1 つ漏らすと無音で床へ落ちる（R7 が効かない）。(3) 検の器が本物より寛容なので、漏れが緑で通る。

## 共同の不変条件（宣言されていない）

床 400,000 は註のとおり「1M の窓に対して 40%」という仮定で選ばれています。この値は `compactAt` が比べる文脈使用率の軌跡を変えます — CLI が 400k で畳むと、使用率は `compactAt`（%）の高い値に到達しなくなる可能性があります。2 つの閾値は所有者が別（`compactAt` は agentd の judgment、`auto_compact_window` は claude の impl）で、単位も別（% と token）で、関係を宣言した点が 1 つもありません。

# 4. 最小の再現手順と観測すべき結果

いずれも読み取りのみ。`~/repos/doeff/.venv` の python を worktree の source に向けています。

**再現 A — 宣言は launch の RPC 境界で落ちる（実測・2-1 の出力）**

```
WT=/Users/s22625/.worktrees/doeff-wt-autocompact-window
PYTHONPATH="$WT/packages/doeff-agents/src:$WT/packages/doeff-hy/src:$WT" \
  ~/repos/doeff/.venv/bin/python -c "
import hy
from doeff_agents.sessionhost import host as H
cfg = H.HostConfig(db_path='/tmp/x.db', socket_path='/tmp/x.sock', tmux_bin='tmux',
                   monitor_interval_seconds=1, max_running=4, allow_metered_billing=False,
                   result_solicitation_limit=1, prompt_stall_seconds=1, prompt_unblock_limit=1,
                   prompt_judge_cmd=None, backend='headless', herdr_socket=None,
                   headless_events_root='/tmp/ev', exit_when_orphaned=False)
charter = {'session_id':'s1','session_name':'n1','agent_type':'claude','work_dir':'/tmp',
           'model':'claude-fable-5-1','effort':'xhigh','auto_compact_window':800000}
print('auto_compact_window in program params?',
      'auto_compact_window' in H.build_launch_program_params(charter, cfg))"
```

観測: `False`。lead の宣言 800,000 は argv 導出点に届く前に消える。

**再現 B — 届いた場合でも、温かい送りの続きの手番だけ床に戻る（実測）**

```
[launch    ] ... --model claude-fable-5-1 --autocompact 800000 --session-id c-0001
[warm send ] ... --model claude-fable-5-1 --autocompact 400000 --resume  c-0001
```

`continue-headless-process`（`headless.hy:350-357`）が組む params をそのまま `build-claude-resume-argv` に渡した結果です。2-2 の名簿 2・3 を直しても、名簿 4（行の `launch_overlay`）を直さなければこの形が残ります。

**再現 C — 静的**

```
grep -c auto_compact_window \
  $WT/packages/doeff-agents/src/doeff_agents/sessionhost/{launch,headless,host}.hy
# → 0 / 0 / 0
grep -c autocompact $WT/packages/doeff-agents/conformance/test_s13_argv_wiring.py
# → 0
```

**本番で観測すべきもの**: 会話の行に `auto_compact_window` を宣言した状態で、`session.get` の `backend_ref.argv`、または当該 node の `ps` の `--autocompact` の値。宣言した値ではなく `400000` が出れば再現。宣言を落とした条件（`AgentSettingIgnored` 等）はどこにも出ません。

**最小の修理の形（参考）**: 名簿を 4 つ直すのは対症です。根は「起動の旗の集合が 5 箇所に暗黙に写っている」ことなので、旗になる欄の集合を 1 点で宣言し、wire・`launch_overlay`・続きの手番の params がその 1 点から導かれる形が根治側です。

# 5. 未確認の前提と不足

- **実測と読みの区別**: 2-1 と 4 の再現 A / B は実行した結果です。2-2 の名簿 2・3 が欄を落とすことは source の読みと grep（dict リテラルで作り直し・catch-all の merge 無し）による結論で、RPC を通した実射はしていません。
- **CLI の実物を起こしていません**。綴り `--autocompact`・幅 100k〜1M・幅の外で手番が死ぬこと（R2）は入力の記述をそのまま採りました。claude 2.1.274 を自分で起こして確かめていません。
- **推測**: 床 400,000 を窓 200k の model（sonnet 等）に渡した時の挙動（実質無効か・受理されるか）は未確認です。床の註は 1M の窓を仮定しており、model は既に会話が宣言できる欄なので、この組み合わせは今日でも起こり得ます。
- **推測**: 割り込み後に claude の process が降りるため次の送りが `continue-headless-process` を通る、という頻度の主張は `headless_protocol.py:723-726` の註と `headless_process.py:261` の導出からの読みで、本番の発生率は測っていません。
- **本番の行を読んでいません**。`compactAt` を実際に宣言している会話が在るか、`auto_compact_window` を宣言した会話が在るかは ACP の本番の行で確認していません（2-5 の共同の不変条件が現に発火しているかは未確認）。
- **段 2 の起票側（dotfiles の route 設定）は未実装**なので、役の語がどこに乗るかは未確定です。本稿は前提 (b) を守って「会話の宣言に乗せる」案で論じました。方策の行を役ごとに増やす案なら 2-5 の一部は回避できますが、それは前提 (b) を変えるので**契約の適用範囲外**です。
- 適合検査 `test_s13_argv_wiring.py` を走らせていません（実際に agentd を起こす検で、焦点走の範囲と時間の判断から見送りました）。

**結論**: 合格・安全とは言えません。CC1 は現状のまま実装すると、宣言した値が本番の argv に 1 度も届かないか、届いても腕によって黙って床に戻ります。