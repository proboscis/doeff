# ADR-012 の針を「ソースの字面」から「構造」へ再照準する

- 基準 commit: `abd03fd2a58dbad29bcb21f435539451315db523` (origin/main 2026-09-22 08:19 JST)
- 赤くなった断面: `68f075d2708664d07905ad1f509f3b1489845c66` (日次の全体検証 2026-09-22 03:30 JST)
- 対象: `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の検査 4 本
- 範囲: **設計まで**。実装は別の依頼として起票する。

## 1. 測った根(実測・推測なし)

日次が赤くした 4 本は、いずれも **law の :statement が現在のコードで満たされたまま**で、
落ちたのは針が凍結した**ソースの字面**だけである。4 本は 1 つの commit には畳めない
(4 件の別々の正当な変更が、それぞれ 1 つずつ別の字面を動かした)が、**1 つの構造的な原因**に畳める。

| # | 検査 | 針が凍結していた字面 | 現在の綴り | 原因 commit | law は満たされているか |
|---|------|--------------------|-----------|------------|--------------------|
| a | headless-first-turn-carries-the-mail | `(<- text str (mail-turn-text-of input-id row.spec body))` (judgment.hy) | 第 4 引数 `row.status` が増えた | `b288669d` | **満たされている** — 合成点は `mail-turn-text-of` の 1 点のまま |
| b | 同上 | `(<- text str (mail-turn-text-of message-id message.spec body))` (agentd.hy) | 第 4 引数 `message.status` が増えた | `b288669d` | **満たされている** — 同上 |
| c | turn-credential-rides-the-turn | `(headless-send-program sid message awaiting session-env attachments)` | 引数 `turn-charter` が間に増えた | `a6b16d63` | **満たされている** — `session-env` は今も送りの腕へ渡る |
| d | turn-credential-rides-the-turn | 関所の呼び手が **3 か所ちょうど** | 4 か所(`session.cache-ping` の口が増えた) | `2bcc4a40` | **満たされている** — 4 つ目の口は判断 `session-env-admission-error` を**再利用**しており、並行実装していない |
| e | attachment-spelling-lives-in-the-dialogue | `"             MESSAGE-ATTACHMENTS-KEY]]"` (字下げ 13 + `]]` まで含む行の一致) | 名簿が `[...]` から `(+ #(...) CHARTER-CARRIED-KEYS)` に畳まれ、字下げと閉じ括弧が変わった | `d8472e1a` | **満たされている** — `MESSAGE-ATTACHMENTS-KEY` は今も resume の名簿に在る |
| f | stop-drains-declared-nodes-before-closing | `replace(settings, draining=True) if draining else settings` (runtime.py) | loop が Hy 側へ移り `(replace settings :draining control.draining)` (worker_loop.hy) | `2bcc4a40` | **満たされている** — drain の合図は今も `settings.draining` に落ちる |

実測(基準 abd03fd2):
- 関所の呼び手 4 か所 = host.hy:1609(cache-ping)・host.hy:1641(session.send)・launch.hy:694(session.launch)・join.hy:821(join.seat_env)
- `mail-turn-text-of` の呼び 2 か所 = judgment.hy:3246・agentd.hy:3502(どちらも 4 引数)
- `headless-send-program` の呼び 1 か所 = host.hy:1684(6 引数)
- resume の名簿 = judgment.hy:2932 に `MESSAGE-ATTACHMENTS-KEY`

f は基準 abd03fd2 では**緑**。ただし緑にしたのは `20c50b4e` で、**新しい字面に一致させ直した**
(`replace(settings, draining=True) …` → `(replace settings :draining control.draining)`)だけであり、
針の形は字面のままなので同じ壊れ方が再発する。⇒ 本設計は f も再照準の対象に含める。

## 2. 構造的な原因(なぜ再発するか)

この冊は針 **410 本**(長い literal の `(in "…" line)` 244 + `.startswith line "…"` 166)が
ソースの字面を凍結しており、構造の helper(`readers-of` / `call-args-of` / `collapsed-code` /
`live-bare-lines`)の使用は **16 か所**しかない(基準 abd03fd2 で実測)。

冊自身が 2026-09-17〜19 の実弾(同じ機序で 7 本が赤)を受けて方針を書いている(同 file 205〜215 行):

> 7 本ともルール本文 = law の statement は現在のコードで満たされたままで、落ちたのは針が焼き付けた
> **ソースの字面**(行の literal・出現回数・語の有無)だけが正当な変更で動いたため。⇒ 針は「数」ではなく
> **名前の集合**を、「行の字面」ではなく**呼び先と引数の役**を撃つ。

つまり**正しい形は冊自身が既に宣言している**。今回赤くなった 5 か所は、その方針が書かれた時に
一緒に直されなかった残りである。⇒ 本設計は新しい方針を発明しない。**冊の既存の方針を、
残っていた 5 か所へ適用する**。

## 3. 責務の分離(この設計が守る境界)

| 責務 | 所有するもの | 隠すもの(他から見えてはならない) |
|---|---|---|
| **law**(`:statement`) | 何が真であるべきか | 真の実現方法・綴り |
| **針**(`deftest`) | その真偽を現在のコードで**測る方法** | — |
| **実装**(`sessionhost/**`) | 真を実現すること | **綴り**(引数の並び・字下げ・file の在処・呼び手の総数) |

侵食の形: 針が「綴り」を読むと、実装が自由に持つべき綴りが針の所有物になる。
すると実装の正当な refactor が law と無関係な赤を生み、日次の全体検証が信用を失う
(「また字面か」で赤を読み飛ばす習慣がつくと、本物の退行を見逃す)。

## 4. 公開契約 — 針が読んでよいもの・読んではいけないもの

**読んでよい(構造)**
1. 頂点の form の**名前**(`defk` / `deff` / `defn` / `def` / `class` の名)と、その**個数**
   (= 「判断の座は 1 点」を撃つ正当な形)
2. 呼び先の**名前**と、引数の**役の在否**(位置・総数ではなく「`session-env` を渡しているか」)
3. 語の**在/不在**(禁止語の針。走査が生きている証拠つき = `live-bare-lines`)
4. **名前の集合**と宣言した名簿の突合(= 「読み手が増えた便は名簿へ宣言せよ」という読める赤)

**読んではいけない(字面)**
- 行の字下げ・改行位置・閉じ括弧の形
- 呼びの**引数の個数**と**位置**
- `acp/` の中での file の**在処**(同じ判断が別 file へ移っただけで赤にしない)
- 呼び手の**総数**(数ではなく名前の集合で撃つ)

## 5. 5 か所の再照準(設計)

各項は「何を守るか(= 針が現に捕まえ続けねばならない違反)」を先に固定し、それを
字面に依らずに撃つ形を与える。**主張を弱めない**ことが条件なので、各項に
「この形でも捕まること」を示す変異(mutation)を添える。

### (a)(b) `mail-turn-text-of` の 2 つの呼び — 合成点は 1 つ
- 守るもの: 手番の文を組むのは judgment の `mail-turn-text-of` **1 点**で、
  入力の腕(judgment)と注入の腕(agentd)が**どちらもそれを呼ぶ**。
- 再照準: 各 file で `call-args-of` を使い、`mail-turn-text-of` の呼びが**ちょうど 1 つ**、
  かつその引数に**郵便の id と spec と本文の役が在る**ことを撃つ。引数の総数は見ない。
- 変異で赤になること: agentd.hy の呼びを文字列連結へ置き換える → 呼び 0 で赤。

### (c) `headless-send-program` — 手番ごとの env が送りの腕へ渡る
- 守るもの: 実弾 #92 の形(行に残った誕生の札で手番が走る)を防ぐため、
  送りの腕に**その手番の** `session-env` が渡ること。
- 再照準: `call-args-of host-lines "headless-send-program"` の呼びがちょうど 1 つで、
  引数の集合に `session-env` と `attachments` が**含まれる**ことを撃つ。並びと総数は見ない。
- 変異で赤: 呼びから `session-env` を落とす → 赤。

### (d) 関所の呼び手 — 数ではなく**名前の集合**
- 守るもの: 運ぶ口が増えても判断 `session-env-admission-error` を**並行実装しない**
  (R30 (3) / R51 (1))。
- 再照準: `readers-of` で「関所を呼ぶ頂点の form の**名前の集合**」を採り、
  冊の名簿の節に宣言した集合と**等しい**ことを撃つ。差が出たら
  「読み手 X を足した — 名簿へ宣言せよ」「読み手 Y が消えた」と**読める**赤にする。
- 変異で赤: 5 つ目の呼び手を足す → 名簿に無い名で赤。**判断を写した**(policy を呼ばず
  同じ拒否を自前で書いた)場合も、呼び手が減るので赤。
- 注: これは「4 つ目の口が生えたら育てる」と冊が既に書いている針。数を 3→4 に直すだけでは
  同じ更新が次の口でまた要る。名簿の形にすると、育てる操作が**宣言 1 行**になり、
  赤が何をせよと言っているかが読める。

### (e) resume の名簿に添付が在る
- 守るもの: 実弾 2026-09-15 09:5x(resume の腕だけ画像が黙って落ちる)。
  `resume-params-of` が写す名簿に `MESSAGE-ATTACHMENTS-KEY` が在ること。
- 再照準: `readers-of` を `resume-params-of` の頂点に絞り、その form の中で
  `MESSAGE-ATTACHMENTS-KEY` が読まれていることを撃つ。字下げ・括弧・名簿の綴り方は見ない。
- 変異で赤: 名簿から `MESSAGE-ATTACHMENTS-KEY` を外す → 赤。

### (f) drain の合図が `settings.draining` に落ちる
- 守るもの: loop が drain の合図を `settings.draining` に写すこと(写さないと
  `declared-capacity-of` が排水中に 0 を返さず、排水が効かない)。
- 再照準: **file を名指さず** `acp/` の配下から「`settings` に `draining` を書く点」を
  1 つだけ見つけ、それが loop の control を読んでいることを撃つ。
  `runtime.py` / `worker_loop.hy` のどちらに在っても通る。
- 変異で赤: `(replace settings :draining control.draining)` の `control.draining` を
  `False` に固定する → 書く点はあるが control を読まないので赤。

## 6. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 合成点・関所・送りの腕・名簿・drain の 5 つの不変条件 | 冊の既存の構造 helper(`readers-of` / `call-args-of` / `collapsed-code`)で撃つ deftest | `docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy` の当該 5 か所 | 日次の全体検証(`ai land verify`)と、変更時の焦点走行 | 「名前は在るが意味が違う」は静的に表せない。挙動の反例の検(`packages/doeff-agents/tests/test_sessionhost_acp.py` 等)が補う |
| 名簿の宣言が針の中に散らないこと | 名簿は冊の名簿の節(1 か所)に置き、針はそこを読む | 同 file の名簿の節 | 同上 | 名簿の節自体の肥大は人手の管理 |
| **新しい字面の針が増えないこと**(再発の上限) | 冊を読み、長い literal を凍結する針の本数が基準値を超えたら赤にする ratchet | 新設の deftest 1 本 | 同上 | 既存 410 本は残る(別 card の射程)。ratchet は**増加**だけを止める |

## 7. この card の射程と、射程外として名指すもの

- **射程内**: 上の 5 か所の再照準 + ratchet 1 本。
- **射程外(別 card を推奨)**: 残る約 405 本の字面の針の一括再照準。
  本設計はそれを**やらない**と明示する(1 card で 410 本を触ると、法と無関係な差分が
  巨大になり、退行の検分が不可能になる)。ratchet が増加を止めるので、
  残りは触る便ごとに漸減する。

---

# 8. 設計の改訂(測定と盲検の結果・事前の主張は §claims.md のまま残す)

事前の主張(claims.md)は**書き換えない**。以下は反例と実測を受けた改訂の記録である。

## 改訂 1 — (d) 名簿の鍵は「囲む form の名」ではなく「呼びが名乗る動詞」(自分の実測で発見)

当初案は `readers-of` で「関所を呼ぶ頂点の form の名の集合」を採る形だった。実測すると:

```
dispatch-method: (session-env-admission-error session-env method)        ← cache ping の口
                 (session-env-admission-error session-env "session.send") ← 送りの口
admit-launch:    (session-env-admission-error session-env "session.launch")
seat-env-of:     (session-env-admission-error (dict pairs) "join.seat_env")
```

host.hy の**2 つの口が同じ `dispatch-method` の中に在る**ので、form の名で数えると 4 口が 3 名に
畳まれ、**同じ form に 3 つ目の口が生えても気づけない**。⇒ 鍵は `call-args-of` で採った
**呼びの第 2 引数(動詞)**にした。実証: 5 つ目の口(`"session.rehydrate"`)を launch.hy へ足すと
名簿の差として赤になり、何を宣言せよかが読める(M3b)。

## 改訂 2 — (e) は構造では足りない。**挙動**で撃つ(盲検 A の反例・実行で確認)

盲検 A の反例: `resume-params-of` の欄の一覧を module の定数(`RESUME-ATTACHMENT-KEYS`)へ
括り出す整理をすると、**添付は継がれたまま** `readers-of` が `resume-params-of` を読み手として
見つけられず針が赤になる。これは仮説ではなく実測で確認した(下記 checks の `ce-A`)。

この整理は空想ではない — 同じ file の `CHARTER-CARRIED-KEYS` が現にその形で、`d8472e1a` は
まさに「欄を 1 つ足す操作が名簿を 4 枚触らせる形を畳む」ために導入した。次に欄が増える便で
添付も同じ形に括られる可能性は高い。

⇒ (e) は `resume-params-of` を**実際に走らせ**、添付が params に乗るかを測る形にした。
継承は名簿の綴りではなく**答え**なので、答えを測る。改訂後: A の反例で緑(継承は保たれている)・
添付を名簿から外す変異で赤(M4)。

**claims.md の storage 軸「冊は 0 行」は、当初案では偽だった** — 改訂後に真になった。

## 改訂 3 — (a)(b) に「呼んだ後で作り直さない」を足す(盲検 B の反例・実行で確認)

盲検 B の反例: `mail-turn-text-of` を呼んだ**後で**返り値に見出しを足す実装
(`(when (= (.get message.spec "priority") "urgent") (setv text (+ "【優先度: 緊急】\n" text)))`)は、
呼びも引数の役も無傷なので「呼び先と役」の針を通る。

⚠ 重要な区別: **この違反は旧い字面の針でも通る**(実行で確認した — 旧針を綴り合わせだけ直した
版に B の反例を当てると緑)。つまりこれは本設計が持ち込んだ後退ではなく、**元から在った穴**である。
条件つき(priority = urgent)なので、既存の挙動の検(通常の郵便 "hello" を流す)でも当たらない。

⇒ (a)(b) に構造の針を 1 つ足した: 合成の呼びで束ねた名が、**その頂点の form の中で setv され直したら赤**。
実証: B の反例で赤(読める文言で違反行を名指す)・現行のコードでは誤検知 0(judgment.hy / agentd.hy とも 0 件)。

## 改訂 4 — 字面の針の ratchet は**置かない**(設計判断・自己の分析)

§6 の表に「長い literal を凍結する針の本数が基準値を超えたら赤にする ratchet」を挙げていたが、
**取り下げる**。理由: それ自体が「数で撃つ針」であり、この冊が明文で禁じている形
(「針は『数』ではなく**名前の集合**を」)と矛盾する。正当な針の追加のたびに基準値の更新を
強いる騒音源になり、今回直している病をメタな層で再生産する。

⇒ 残る約 405 本の字面の針は**別 card の射程**として名指す(本 card では触らない)。
本 card が置くのは「壊れた針は綴りを合わせ直すのではなく**構造へ再照準する**」という実例 5 件で、
方針そのものは冊の 205〜215 行に既に在る。

## 9. 実測と事前予測の差

| 事前の予測(claims.md) | 実測 | 差の原因 |
|---|---|---|
| storage 軸: 実装 1 file・冊は 0 行 | **当初案では偽**(盲検 A) → 改訂 2 の後に真 | 構造の針が「名簿がどこに書かれているか」に依存していた。挙動へ移して解消 |
| 「5 本の針が現に捕まえていた違反は再照準後も全部捕まる」 | **真**(M1〜M5 の 5 変異すべて赤) | — |
| concurrency / distribution / effects / hardware / simulation 軸 | 予測どおり(冊は 0 行 or 名簿 1 行) | — |
| 「差は出ないと予想する」 | **偽** — 2 件出た(改訂 2・改訂 3) | 予測が甘かった。どちらも盲検が見つけ、実行で確認して境界を直した |

## 10. 残る限界(静的に表せないこと)

1. 「名前は在るが意味が違う」— 例えば `mail-turn-text-of` の中身が壊れる退行は、構造の針では
   捕まらない。挙動の反例の検(`packages/doeff-agents/tests/test_sessionhost_acp.py` 等)が担う。
2. 改訂 3 の針が撃つのは `setv` による**再束縛**ちょうど。別名の変数へ写してから加工する形
   (`(setv shown (+ "…" text))`)は通る。これは静的には追い切れない(データフロー解析が要る)ため
   **限界として残す**。
3. 残る約 405 本の字面の針は本 card では触らない(§8 改訂 4)。
