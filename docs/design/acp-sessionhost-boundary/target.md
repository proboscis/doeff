# ACP と sessionhost の境界 — 目標の設計

2026-09-18。この文書は**目標の形**を決める物で、現状の説明は
`docs/design/auto-compact-window/artifacts/situation-2026-09-18/report.html` にある。
出自は、会話の圧縮の閾値が起動の引数に届かない欠陥を追ううちに、境界そのものが
問題だと分かったこと。

## 規則(operator が定めた・この設計の制約)

1. **doeff-agents は ACP を知らない。** 型も語彙も handler も持たない。
2. **sessionhost が依存してよいのは doeff-agents だけ。**
3. **ACP は sessionhost を直接使わない。** ACP は自分の interface を持ち、その外へ出ない。
4. **adapter が「ACP の interface → doeff の effect」を変換する。** 両方の語彙を知ってよい唯一の場所。

## 一言でいうと

**ACP 側はこの形が既に完成している。欠けているのは、引く側の node にいる相手方だけ。**
その相手方が doeff-agents の中に住み、ACP の文書を素通ししているので、規則 1 と 3 と 4 が破れている。

---

## 1. 目標の構造

<!--FIG1-->

### 3 つの成果物と責務

| 成果物 | 持つもの | 知ってよいこと | 知ってはいけないこと |
| --- | --- | --- | --- |
| **doeff-agents** | sessionhost(汎用の runner)・公開の口(型・effect の宣言・socket の client) | process の起こし方・見張り方 | ACP のすべて |
| **adapter(node の controller)** | ACP の行を見る・翻訳する・doeff の effect を yield する | **両方** | — |
| **ACP** | 制御面・kinds・Agent の effect と handler 群 | 自分の interface | sessionhost の内側 |

### 依存の向き

```
ACP  ←  adapter  →  doeff-agents  →  sessionhost
```

adapter だけが両方を向く。doeff-agents から ACP への矢印は**存在しない**。

---

## 2. 1 手番が流れる道(引く側)

<!--FIG2-->

押す側(ACP が socket を直接呼ぶ backend)も同じ口に着く。**どちらの backend でも、
socket を渡るのは doeff-agents の型を直列化した物**で、ACP の文書ではない。

---

## 3. 契約

### doeff-agents が公開する物

| | 中身 |
| --- | --- |
| **型** | `AgentSpec` — 器の仕様ちょうど(agent の種類・model・effort・作業場・mcp・畳む閾値・backend・lifecycle) |
| **effect の宣言** | launch / resume / send。荷物は `AgentSpec`(**opaque な JSON ではない**) |
| **既定の handler** | socket を話す物(いまの `SessionRpc` / `AgentdClient` 相当) |
| **添付の型** | 荷物の一部なので公開側が持つ |

不変条件:

- `AgentSpec` に**依頼の材料**(本文・添付・受入の schema)を入れない。その手番の値として別に運ぶ。
- `AgentSpec` に**資格**(借りた札)を入れない。手番ごとの値として別に運ぶ。
- socket を渡るのは `AgentSpec` を直列化した物。**復号は 1 点**で、以後は型のまま持ち回る。
- 欄を足す時に編集するのは型 1 か所。**構築し忘れは検査で赤になる**(無音で既定値に落ちない)。

### adapter がする物

| | 中身 |
| --- | --- |
| **見る** | `acp_client.runtime` の informer で自分に結ばれた行を追う(自前の watch を書かない) |
| **翻訳** | ACP の spec → `AgentSpec`。**ここが adapter を管から adapter にする** |
| **yield** | doeff の effect を出す。純粋な判断 |
| **書き戻す** | 結末と観測を ACP の行へ |

不変条件:

- ACP の文書(charter)を**そのまま下流へ流さない**。必ず型に訳す。
- ACP の runtime(informer・backoff・health・身元・条件の読み書き)を作り直さない。
- 判断は純粋にし、I/O は handler へ。偽の handler で socket も process も無しに検が書ける。

### ACP が持つ物

いまのまま。`Agent/Effect.hs` の effect と、handler 3 つ(押す / 行を書く / 偽)。

---

## 4. なぜこの形か — 半分は既に在る

推測ではなく、現物がそうなっている。

| 在る物 | 場所 |
| --- | --- |
| Agent の effect(型つき) | `Acp/App/Agent/Effect.hs`(`effectful >= 2.5`) |
| handler 3 つ | `Agent/Handler/{Agentd,AgentJob,Mock}.hs` |
| **押す側の翻訳** | `sessionSpecToRequest :: AgentSessionSpec -> LaunchRequest` |
| **doeff の型(Haskell)** | `doeff-agent-haskell` の `LaunchRequest`(ACP が依存している) |
| **controller の runtime(Hy)** | `clients/hy/acp_client/runtime/`(controller・effects・handlers_wire・handlers_fake・informer・backoff・health・identity) |
| **controller の SDK(Python)** | `sdk/python/acp_controller`(「どの process・どの言語でも同じ engine に対して動く」) |

**押す側は型を組んでいる。引く側だけが素通ししている。** 理由は配置にある —
押す側は別 process なので型に落とすしかなく、引く側は同じ process に同居しているので
その痛みが誰にも発生しなかった。

---

## 5. 現状との差 — 何がどこへ動くか

<!--FIG3-->

| 規則 | 今 | 目標 |
| --- | --- | --- |
| 1. doeff-agents は ACP を知らない | **破** `doeff_agents/sessionhost/acp/` 15,753 行・実行の入口が `acp.entry:main` | acp/ は外へ。入口は doeff-agents 自身の物 |
| 2. sessionhost は doeff-agents にだけ依存 | **満** 核から acp への import は 0 件 | そのまま |
| 3. ACP は直接使わない | **破** socket を渡るのが charter そのもの | 渡るのは `AgentSpec` の直列化 |
| 4. adapter が変換する | **破** `SessionLaunch(params: JSONObject)` = 素通し | 翻訳が入る |

数字:核 13,711 行 / `acp/` 15,753 行(うち `judgment.hy` が 5,144)。

---

## 6. 移す順序

依存があるので順番が決まっている。

1. **doeff-agents が口を公開する** — `AgentSpec` の型、launch / resume の effect の宣言、socket の client、添付の型。
   ⚠ これが無いと、acp/ を外へ出しても ACP の語彙が doeff-agents に残る。
2. **引く側の翻訳を入れる** — charter → `AgentSpec`。ここで 5 つの名簿が消える(下の第 7 節)。
3. **棚卸し** — `acp/` の 15,753 行のうち、ACP の runtime の作り直し(informer・backoff・health・身元)と、
   この node 固有の判断を分ける。前者は捨てて `acp_client.runtime` を使う。
4. **移す** — 残りを ACP の repo の controller として置く(`clients/hy/acp_client` の上)。
   別 repo にするのは、版を切る周期を ACP と分けたい時だけ。
5. **入口を戻す** — `doeff-sessionhost` の実行の入口を doeff-agents 自身の物にする。
6. **配置** — node に何を入れるかが 1 つから 2 つになる。uv tool と launchd の据え付けを直す。

段 1 と 2 は doeff-agents の中だけで完結し、いまの欠陥(閾値が届かない)の根治でもある。
段 3 以降は ACP 側の仕事。

---

## 7. いまの欠陥がこの設計のどこに収まるか

会話の圧縮の閾値が起動の引数に届かなかったのは、**段 1 と 2 が無いこと**の症状だった。

- 口が「charter を素通し」なので荷物に型が無い
- 型が無いので、境界ごとに欄を手で数え直す(名簿が直列に 5 つ)
- 数え直しなので、載せ忘れは**無音**で既定値に落ちる

段 1 と 2 を入れると、名簿はすべて消える(復号 1 点 + 行が型をそのまま持つ)。
いま branch に着地している応急処置(`carry-launch-flags`)は、その時に削除する。

---

## 8. 未決と、まだ数えていない物

- **`acp/` の内訳を数えていない。** ACP の runtime の作り直しがどれだけを占めるかは棚卸し待ち。
  見立てでは相当量だが、断言しない。
- **置き場の最終決定**(ACP の repo の中 か 別 repo か)。決め手は版を切る周期。
- **`AgentSpec` の欄の確定** — 器の仕様の境目。`lifecycle` / `binding` / `skip_trust_setup` が
  どちら側かは、欄ごとに決める。
- **押す側と引く側で型を共有するか。** Haskell の `LaunchRequest` と Hy / Python 側の型が
  同じ形である必要がある。契約の正本をどこに置くかは未決。
- **配備の形** — node に 2 つの成果物を入れる運用に変わる。
