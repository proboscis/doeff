# 次にやること — card 化できる形の 13 件

2026-09-18 の設計の会話で合意した物。**card 1 枚 = ここの 1 節**の想定で、題・なぜ・受入・依存を
そのまま写せるように書いてある(盤の namespace `agora-redesign`)。

⚠ この file を書いた席は agora の管理外(素の terminal・印 `AGORA_CONVERSATION_ID` を持たない)なので、
card は作れなかった。agora の会話から起票する。

順序の骨: **7 → 5 → 6**(controller の切り出し)/ **1 → 2 → 3 ↔ 4**(器の切り出し)は並行できる。
8・9・10・11・12・13 は独立。

---

## 1. doeff-agents の起こす口に型を置く

**なぜ**: 起こす口の契約が `SessionLaunch(params: JSONObject)`(説明文に「params = charter そのもの」)で、
荷物に型が無い。だから境界ごとに欄を手で数え直す名簿が直列に 5 つでき、載せ忘れは**無音で既定値に落ちる**。
実測 2026-09-18: 稼働中の claude 席 43〜49 本のうち閾値の旗を持つ物が 0 本・1 手番の平均の文脈 550k・
日に 16,976u。

**受入**
- [ ] `AgentSpec`(器の仕様ちょうど)を doeff-agents 側に宣言し、launch / resume の effect の荷物にする
- [ ] 本文・添付・受入の schema・借りた札を `AgentSpec` に**入れない**(その手番の値として別に運ぶ)
- [ ] socket の復号は 1 点。以後は型のまま持ち回る
- [ ] 5 つの名簿(wire の受理形・蘇生の params・行の overlay・続きの手番・ACP 側の写し)が消える
- [ ] 応急処置(`policy.LAUNCH-FLAG-KEYS` / `carry-launch-flags`)を削除する
- [ ] 欄を足す時の編集点が 1 つで、構築し忘れが検査で赤になる

**依存**: なし(いちばん先に撃てる)

---

## 2. 薄い Python client を切り出す

**なぜ**: `agentd_client.py` は 1,475 行あり、`adapters.base` / `io_root` / `io_effects` / `effects` /
`monitor` に絡んでいて**外の repo から依存できない**。だから glue が中に居るしかなかった。
Haskell 側は `doeff-agent-haskell`(別 repo・1 module・型つき `LaunchRequest`)として既に在る。

**受入**
- [ ] wire の綴りと型だけを知る薄い client(doeff-agents の内側を知らない)
- [ ] `doeff-agent-haskell` と**同じ形**(型つき・単体で依存できる)
- [ ] 外の repo から普通に依存宣言できることを実際に確かめる

**依存**: 1(型が先)

---

## 3. sessionhost を別 repo にする

**なぜ**: 純度を機械で検められるようにする唯一の手段。同じ package の中には依存の宣言が無いので、
「相手の領域を知らない」を検めようがない。実測: sessionhost の外向きの依存は `doeff` / `doeff-hy` /
`doeff-time` の 3 つだけで、monorepo に絡んでいない。

**受入**
- [ ] `doeff` / `doeff-hy` / `doeff-time` を外から引ける形にする
- [ ] 検の機構(`doeff-adr` の plugin・品質検査・走行の受付の門)・`.agents/` の宣言・land queue
- [ ] 配備(`doeff-sessionhost` の uv tool の元)
- [ ] **実行の入口が `acp.entry:main` でなくなる**

**依存**: 4(入口を glue が握っているので同時か glue が先)

---

## 4. glue(`sessionhost/acp/` 15,753 行)を agora 側へ出す

**なぜ**: ACP の行を見て器を起こす node の controller が、器の package の中に住み、実行の入口にもなり、
ACP の文書(charter)を素通ししている。核は 13,711 行で、glue の方が大きい。

**受入**
- [ ] 棚卸し — 15,753 行のうち ACP の runtime の作り直し(informer・backoff・health・身元)と、
      この node 固有の判断を分ける。前者は捨てて `acp_client.runtime` を使う
- [ ] 残りを agora 側の controller として置く(handler が 2 の client を使う)
- [ ] charter → `AgentSpec` の**翻訳**を入れる(素通しをやめる)
- [ ] doeff-agents に ACP の語が 1 つも残らない

**依存**: 1・2

---

## 5. 配置(Scheduling)を `agora-scheduling` へ

**なぜ**: 2026-08-17 の裁定「`src/Acp` へ運転政策を足さない」が、いま**実行時にしか成立していない**
(`installedApps` は空・daemon は Scheduling を呼ばない)。library と binary には方策が同居している。
7,517 行。

**受入**
- [ ] Haskell のまま別 repo(`agora-scheduling`)。`Acp.Sdk.WireClient` で wire を話す out-of-process の controller
- [ ] `acp-engine` の library から消える(`layer-boundary-baseline.json` の ratchet で確かめる)
- [ ] 出す時に 9(選定の重複)を解く

**依存**: 7

---

## 6. 郵便(Messaging)を `agora-messaging` へ

**なぜ**: 同上。13,301 行。⚠ **配達の判断だけでなく依頼の一生**(ask / accept / send-back / withdraw・
class・親子)を含む — B(仕事)は独立の context として作られず、郵便に畳まれている
(法 `request_is_a_message_projection` 2026-09-14)。配置より慎重に。

**受入**
- [ ] Haskell のまま別 repo(`agora-messaging`)
- [ ] `agora-controllers/controllers/messaging`(受付の据え付け)との関係を決める
- [ ] `acp-engine` の library から消える

**依存**: 7・5(配置を先に出して形を確かめる)

---

## 7. `Acp.Sdk.WireClient` を公開の package にする

**なぜ**: 5 と 6 の前提。2 系統の controller(Haskell と Hy)が同じ wire を話す。
ADR 0013 O5/O6 が「Haskell の controller を engine の外で走らせる」ために用意した物で、
client と apiserver が同じ型と同じ Aeson の instance を共有するので書きは byte 一致。

**受入**
- [ ] 公開の package として外の repo から依存できる
- [ ] 置き場は ACP の repo のままでよい(client は提供する側の物・相手の語彙を知らないので汚さない)

**依存**: なし

---

## 8. 会話の `status.agent` から器の語を出す

**なぜ**: 原則 11(operator 指示 2026-09-11 逐語 "a conversation is an abstraction for agora system,
**not actual agent session management backend**")に反している。`status.agent` は 9 欄あり、
`model` / `workDir` / `effort` / `compactAt` は器の語。段ごとに 1 欄ずつ積まれてきた
(9o-1 → 10e → 10f → 10n → 10r → 11m)。

**受入**
- [ ] **10 個目(`autoCompactWindow`)を足さない**
- [ ] agora の判断が読む欄(`model` / `profile` / `workDir`)だけを宣言に残す
- [ ] 誰も読まない欄は器の spec の中へ(会話の schema が変わらなくなる)
- [ ] 判定の規則を書く — 「その欄を読む agora の判断が在るか」

**依存**: 1(器の spec に持ち主が要る)

---

## 9. profile の選定の重複を 1 点へ

**なぜ**: 同じ判断が 2 か所にある — ACP の `Decide.credentialFor`(Haskell)と dotfiles の
`pick_pool_profile`(Python)。`limit_switch` は既に規律的(第 2 の名簿も第 2 の選定も作らない・
予算の係の `ProfileExhausted` を読む)なので、残る重複はこの 1 点。

**受入**
- [ ] 判断は配置の controller の 1 点
- [ ] 機体側(dotfiles)は**目と手だけ** — 自分の器 → 会話 → 行を読む → 違えば切り替える
- [ ] 印を持たない席(素の terminal)は対象外(系の外)

**依存**: 5

---

## 10. `ai` は会話を作る(pane を作らない・印を注ぐ)

**なぜ**: `ai` は会話を作るべきなのに、herdr の pane を起こし、`AGORA_CONVERSATION_ID` を**注いでいない**
(`launch.py` が子へ注ぐ `AGORA_*` は `AGORA_WAIT_CONTRACT` だけ)。help も「EVERY `ai` session lives in
**herdr**」のままで、2026-09-05 の「herdr は使わない」より前の文言。

**受入**
- [ ] `ai` が起こす席は会話として登記され、印が注がれる
- [ ] herdr の pane を作らない
- [ ] help の文言を直す

**依存**: なし

---

## 11. agora 固有の指示を CLAUDE.md から出して agora が注入する

**なぜ**: agora / ACP / herdr 固有の指示は CLAUDE.md / AGENTS.md に置かず、**agora が注入する**
(operator 指示 2026-09-18)。CLAUDE.md 自身の冒頭も「Only always-on rules that must bind EVERY session
belong here」と言っている。いまは 553 行のうち約 250 行が agora 固有で、**系の外の席にも全部載っている**。

**受入**
- [ ] agora が注ぐ(約 250 行): Convqueue First 143 / Turn-End Wait 25 / Voice Messages 17 /
      Coupling-Core 15 / Operator Feedback 11 / 押す物の実射 7 / セッションの実態確認 5 / Decisions 33
- [ ] repo の CLAUDE.md へ(約 105 行): Testing 48 / Worktree Placement 36 / コード品質 19 / Upstream Rules
- [ ] 残す(約 200 行): Meta / Language / AskUserQuestion / Engineering Principles / File Safety /
      Company Profile API Calls / Visualization / User Profile / Model and Reasoning Effort
- [ ] 注入の口 = `--append-system-prompt-file`(per-launch の注入は `AGORA_WAIT_CONTRACT` で前例あり)

**依存**: 10(印が注がれる形と同じ経路)

---

## 12. 2026-09-11 の設計を更新する

**なぜ**: 実物とずれている。

**受入**
- [ ] **B(仕事 / Work)は作られなかった** — 依頼 = 郵便の投影(法 2026-09-14)。9 context は実質 8
- [ ] E(配置)と D(郵便)の置き場を改訂(engine の中の Haskell → 別 repo の controller)
- [ ] 成果物の境界(repo)について設計が何も言っていない点を補う — 今日の混ざりは全部そこから出た

**依存**: 5・6

---

## 13. 盤の `by` を principal にする

**なぜ**: いま盤に書く時の名乗りの文法が `conversation:<id>` 1 種類だけで、**controller すら会話 id を
渡されて会話のふりをしている**(`controllers/kanban/source/main.hy`「`ai kanban` と同じ掟」)。
掟の筋(card の著者は返事を返せる相手であるべき)は正しいが、表現が「会話でなければならない」と狭い。
card は依頼ではない(card → 依頼 は `convert` という別の動詞)ので、依頼の掟をそのまま掛けるのは広すぎる。

**受入**
- [ ] `by` が principal を受ける(会話 / controller / operator)
- [ ] 返事の規則が「著者が受け取れるならそこへ、受け取れないなら card を持つ会話へ」
- [ ] controller が会話 id を渡されなくても盤に書ける(印の意味が濁らなくなる)

**依存**: なし
