# 責務と置き場 — いまの姿と、分け方

2026-09-18。会話の圧縮の閾値が起動の引数に届かない欠陥を追ううちに、境界そのものが根だと
分かった。この文書は**責務がどこに住んでいるか**を実測で並べ、**どう分けるか**を示す。

目標の構造だけを読むなら `target.html`。現状の欠陥の説明は
`../auto-compact-window/artifacts/situation-2026-09-18/report.html`。

---

## 1. 登場する実体

| 実体 | 何か | 置き場 |
| --- | --- | --- |
| **sessionhost** | agent の process を起こす・見張る・止める汎用の runner | doeff-agents の中の module |
| **doeff-agents** | 器の package(effect と型と CLI ごとの adapter) | `doeff` monorepo の 1 package |
| **ACP** | 制御面の engine(kinds・行・watch・CAS・RBAC) | repo `agent-control-plane` |
| **agora** | 製品の面(protocol / HUD / daemon / relay) | repo `agora`(旧 herdr-hud) |
| **agora-controllers** | agora の運転の controller 群(Hy + doeff) | repo `agora-controllers` |
| custody | 資格の保管と貸与 | 別 repo・別 service |
| doeff-agent-haskell | 器の薄い client(型つき) | 別 repo |

---

## 2. いまの姿

<!--FIG1-->

**混ざっているのは 2 か所**で、どちらも同じ形をしている。

| | 中に居る物 | 大きさ |
| --- | --- | --- |
| doeff-agents | 汎用の runner + **ACP の glue**。しかも**実行の入口が glue 側**(`acp.entry:main`) | runner 13,711 行 / glue **15,753 行** |
| agent-control-plane | engine + **agora の配置の方策** + **押す側の adapter** | `App/Scheduling/` + `Agent/Handler/Agentd.hs` |

### 切れているのは実行時だけ

| 層 | doeff-agents の glue | ACP の配置の方策 |
| --- | --- | --- |
| 走る process | **同じ**(thread が自分自身に unix socket で話す) | 別(`acp daemon` と別 subcommand) |
| binary | 同じ(`doeff-sessionhost`) | 同じ(`acp`) |
| package / library | 同じ | 同じ(`acp-engine`) |
| repo | 同じ | 同じ |

論理的な境界(import の向き)は**どちらも綺麗**(核から glue への逆流は 0 件)。
それでも混ざる。**宣言されない境界は守られない。**

実例: この設計の議論のさなか、私は 1 時間のうちに ACP の語(`charter`)を汎用の runner の
註に 3 か所書いていた。何も止めなかった。

---

## 3. 責務と置き場

凡例 — **合** = 場所と責務が一致 / **ズレ** = 一致していない / **未決** = あるべき場所が未定。

### 器を走らせる

| 責務 | いまの場所 | | あるべき |
| --- | --- | --- | --- |
| process を起こす・見張る・止める | doeff-agents `sessionhost/` | 合 | 同じ |
| CLI ごとの argv の組み立て | doeff-agents `sessionhost/impls/` | 合 | 同じ |
| 起こす口の protocol(unix socket) | doeff-agents `sessionhost/host.hy` | 合 | 同じ |
| **起こす口の型** | **無い** — `SessionLaunch(params: JSONObject)`、説明文に「params = charter そのもの」 | **ズレ** | doeff-agents |
| 薄い client(Haskell) | `doeff-agent-haskell`(別 repo・1 module・型つき) | 合 | 同じ |
| **薄い client(Python)** | **無い** — `agentd_client.py` 1,475 行が内側に絡む | **ズレ** | 別成果物 |

### 制御面の基盤

| 責務 | いまの場所 | | あるべき |
| --- | --- | --- | --- |
| kinds / 行 / watch / CAS / RBAC | ACP `library acp-engine` | 合 | 同じ |
| kind の登録(契約から data だけで) | ACP `scripts/register_contract_kinds.hy` | 合 | 同じ |
| 領域の意味を持たないこと | `installedApps` は空・daemon は Scheduling を呼ばない | 合 | 同じ |
| 配達の判断(郵便を誰に届けるか) | ACP `App/Messaging/Decide.hs` | 合 | 同じ |

### agora の運転

| 責務 | いまの場所 | | あるべき |
| --- | --- | --- | --- |
| 口座の予算・枯渇の観測 → condition | `agora-controllers/controllers/budget` | 合 | 同じ |
| 盤 / 会話 / 画面 / 成果物 / 自動処理 / 分類 / 耐久 | agora-controllers の各 dir | 合 | 同じ |
| node の boot の事実 | `agora-controllers/controllers/nodeboot` | 合 | 同じ |
| 受付の会話の据え付け(判断は持たない) | `agora-controllers/controllers/messaging` | 合 | 同じ |
| **配置(どの profile・どの node で起こすか)** | **ACP `App/Scheduling/`** | **ズレ** | **未決** |
| **profile の選定** | **ACP `Decide.credentialFor`** と **dotfiles `pick_pool_profile`** の **2 つ** | **ズレ(重複)** | 1 つへ |
| headless の席の切り替え | ACP の配置 | ズレ | 同上 |
| **pane の席の切り替え** | **dotfiles `limit_switch.py`** | **ズレ** | **未決** |

### glue(両方の語彙を知る物)

| 責務 | いまの場所 | | あるべき |
| --- | --- | --- | --- |
| 引く側:ACP の行を見て器を起こす | **doeff-agents `sessionhost/acp/`** | **ズレ** | agora 側 |
| 押す側:ACP の spec を器の型へ訳す | **ACP `Agent/Handler/Agentd.hs`** | **ズレ** | agora 側 |
| node の join / lease / 能力の名乗り | doeff-agents `sessionhost/acp/join.hy` | **ズレ** | agora 側 |
| 借りた札を手番の env に載せる | doeff-agents `sessionhost/acp/` | **ズレ** | agora 側 |

### 資格・製品の面・手元の道具

| 責務 | いまの場所 | |
| --- | --- | --- |
| 資格の保管と貸与 | `custody`(別 repo・別 service) | 合 |
| 利用枠の観測(会社機体でのみ測れる) | 会社機体の worker → cluster へ報告 | 合 |
| 観測の置き場 | ACP の `profile` の行 `status.windows` | 合 |
| protocol / HUD / daemon / relay | `agora` | 合 |
| `ai` の CLI(薄い皮)・利用枠の読み | dotfiles `agentcli` | 合 |

**ズレは 9 行。** うち 4 行が glue、2 行が「型 / client が無い」、2 行が方策の置き場、1 行が重複。

---

## 4. glue はどこに住むのか

「glue」と呼ばれる物は **2 種類**あり、置き場の答えが違う。

<!--FIG2-->

| | 何を知るか | 置き場 |
| --- | --- | --- |
| **client / SDK** | **提供する側の語彙だけ**。使う側を知らない | 提供する側でよい。**汚さない** |
| **adapter** | **両方の語彙**(`charter → AgentSpec`) | **どちらに置いても汚す** |

`doeff-agent-haskell` と `acp_client` は client であって glue ではない。ACP を 1 語も知らないから、
doeff 側に在っても純度を壊さない。

汚すのは adapter の方で、これを ACP に置けば ACP の純度が、runner に置けば runner の純度が壊れる。

**解 = adapter は「両方を必要とする理由を持つ側」に住む。** この系ではそれは **agora**。
ACP も doeff-agents も、相手が居なくても存在理由がある。両方が要るのは agora だけ。

判定は 1 つで足りる — **2 つ以上の領域の語を名指すか。** 名指すなら agora 側、名指さないなら提供する側の client。

⚠ ただし「判断が全部 agora へ出る」わけではない。`controllers/messaging` の README は
「配達の判断は ACP の 1 点で、**この repo に第 2 の判定点は置かない**」と明言している。
何が engine の判断で何が運転の方策かは、責務ごとに線を引く。

---

## 5. 目標の姿

<!--FIG3-->

依存は片方向で、**宣言に現れる**。

```
agora              →  agora-controllers
agora-controllers  →  acp の client + 契約
                   →  sessionhost の client(型つき)   ← adapter の handler だけ
ACP                →  (この集合の誰にも依存しない)
sessionhost        →  (同上)
```

`ACP → sessionhost` という辺は**無い**。controller が **handler を 2 つ**持ち、
それぞれの client を叩く。

---

## 6. なぜ repo を切るのか

整理のためではない。**純度を機械で検められるようにする唯一の手段**だから。

「この成果物は相手の領域を知らない」を検めるには、**依存の宣言が読める**必要がある。
同じ package の中では宣言が無いので、検めようがない。検められなければ drift は必ず戻る。

そしてもう 1 つ、切ると**欠けている物が強制的に生まれる**。

いま glue は `from doeff_agents.sessionhost...` で内側へ手を伸ばせる。repo が別になれば、
それができない。すると glue は**公開された client** を使うしかなくなり、その client は
内部構造を渡せないので**型**を持つしかなくなる。

表の「ズレ」9 行のうち 3 行(型が無い・Python の client が無い・glue が runner の中)は、
**repo を切ると同時に解ける**。気をつけて直すのではなく、構造上そうなるしかなくなる。

これは Haskell 側で既に起きたことでもある —— 別 repo だったから、client は薄くなり型を持った。

### 切るのに要る物

- `doeff` / `doeff-hy` / `doeff-time` を monorepo の外から引けること
  (実測: sessionhost の外向きの依存はこの 3 つだけ。monorepo に絡んでいない)
- 検の機構(`doeff-adr` の plugin・品質検査〔`code-quality` は既に別 repo〕・走行の受付の門)
- `.agents/` の宣言と land queue
- 配備(`doeff-sessionhost` の uv tool の元が repo になる)

### 順序の制約

**いまの実行の入口が `acp.entry:main`。** glue が入口を握っているので、
「sessionhost だけ先に切る」はできない。glue を先に出すか、切るのと同時に出すか。

---

## 7. 決まっていること / 未決

**決まっている**(法と実物が在る)

- engine は domain-free(`daemon-binary-must-be-domain-free` / `installedApps` は空)
- 配置の係は engine の外で判断する(`scheduling operator decides outside the engine`)
- `src/Acp` へ運転政策を足さない(2026-08-17 裁定・`layer-boundary-baseline.json` の ratchet が守る)
- agora の controller の住処は `agora-controllers`(10 個が既に居る・履歴ごと移した前例が 2 件)

**未決**

- 配置の方策は「運転政策」か。そうなら `src/Acp` を出る
- profile の選定を ACP(Haskell)と dotfiles(Python)のどちらへ寄せるか
- pane の席の切り替えの責任者
- sessionhost を切る repo の名前と、glue を出す順序
