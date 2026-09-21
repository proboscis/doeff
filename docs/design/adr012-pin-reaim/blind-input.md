# 材料 1: 提案中の設計

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

# 材料 2: 事前に固定した主張

# 事前の主張(盲検 A・B を起こす**前**に固定)

基準 commit: `abd03fd2a58dbad29bcb21f435539451315db523`
固定時刻: 2026-09-22(JST)・機体 CA-20038667

## 前提(この主張が成り立つ条件)

1. 針は **ソースを静的に読む**道具であり、実行時の意味を証明しない。
   「名前は在るが中身が別物」は本設計の強制方法では捕まえられない — 挙動の反例の検が担う。
2. `acp/` 配下の判断は Hy と Python が混在し、同じ判断が言語をまたいで移動しうる。
3. 冊(`defadr_..._012_...hy`)の針は冊の中で完結し、外部の lint には依存しない。
4. 将来のすべての変更を予測できるとは主張しない。主張するのは
   「**下の 6 軸の現実的な変更で、法が満たされたまま針だけが赤くなることがない**」に限る。

## 変更シナリオ(6 軸)と主張

| 軸 | 適用 | 変える要求 | 主張(何が変わり、何が変わらないか) | 予想する波及範囲 |
|---|---|---|---|---|
| **effects** | applicable | `session_env` を運ぶ **5 つ目の口**が生える(例: 新しい RPC の腕) | 変わる = 実装の新しい腕 + 冊の**名簿 1 行**。変わらない = 判断 `policy.session-env-admission-error` 本体、他 4 つの呼び手、針の本体 | 実装 1 file + 名簿 1 行 |
| **storage** | applicable | charter に運ぶ欄がもう 1 つ増える(`d8472e1a` と同じ形の名簿の畳み直し) | 変わる = `resume-params-of` の名簿の綴り。変わらない = 針(読む点を form 名で絞るので字下げ・括弧の形に依らない) | 実装 1 file のみ・**冊は 0 行** |
| **concurrency** | applicable | loop を再び別の層へ移す(`2bcc4a40` と同じ形。例: agentd と host の process 分割 `20c50b4e` の続き) | 変わる = `settings.draining` を書く点の**在処**。変わらない = 針(file を名指さず `acp/` 配下から 1 点を探すので) | 実装 1〜2 file・**冊は 0 行** |
| **distribution** | applicable | 手番の文の合成に欄が増える(`b288669d` と同じ形。例: 郵便の見出しに優先度を足す) | 変わる = `mail-turn-text-of` の引数。変わらない = 針(呼び先の名と役で撃ち、総数・位置を見ない) | 実装 2 file・**冊は 0 行** |
| **hardware** | applicable | backend の種類が増える(headless / tui に 3 つ目) | 変わる = judgment の畳みの判断。変わらない = 針(`agentd.hy` が backend の語を持たないことを撃つ禁止語の針は語の集合で、種類が増えても形は同じ) | 実装 1 file・冊は禁止語の名簿 1 行 |
| **simulation** | applicable | 偽の器(`fake.py` / `World` / `FakeSessions`)の構成が変わる | 変わる = 挙動の反例の組み立て。変わらない = 構造の針(ソースだけを読む) | test 1 file・冊は挙動節のみ |

**6 軸すべてを applicable とする。** 不適用の軸は無い — この冊は agentd/sessionhost の
全域を撃つ針の集合なので、どの軸の変更も針に当たりうる。

## 局所化できると主張する理由

再照準後の針が読むのは 4 種だけ(頂点の form の名・呼び先の名と引数の役・語の在否・名前の集合)。
このどれも、**法が変わらない限り変わらない**量である:

- 頂点の form の名 = 法が「判断の座」と呼んでいるもの。改名は法の改訂を伴う。
- 引数の**役の在否** = 法が「何が渡る」と書いているもの。役が消えるのは法の違反。
- 語の在/不在 = 法が禁じている綴り。
- 名前の集合 = 法が「並行実装しない」と言っている読み手の一覧。

対して、今回赤くした 5 つの量(字下げ・引数の総数・file の在処・呼び手の総数・括弧の形)は
**法が 1 文字も言及していない**。⇒ 針をこの 4 種に限れば、法と針の赤が一致する。

## 維持すると主張する契約(壊してはならないもの)

1. 5 本の針が現に捕まえていた違反は、再照準後も**全部捕まる**(各項の変異で実証する)。
2. 冊の他の針(約 405 本)と挙動の検には**触れない** — 本 card は 5 か所 + ratchet 1 本のみ。
3. law の `:statement` は 1 文字も変えない(法は現在のコードで満たされているため、改訂は不要)。

## 予想する実測との差(事前)

差は出ないと予想する。出るとすれば以下のどれかで、その場合は境界か針を直して再検証する:
- `call-args-of` / `readers-of` が Hy の複数行に折れた呼びを拾えない(helper の限界)
- 名簿の突合が「同名の別 form」で誤検知する

# 材料 3: 冊の構造 helper(針が使える読み口)の実体
```hy
(defn #^ list code-lines [#^ Path path]
  "註釈(;; / #)と空行を除いた code 行の列。"
  (setv out [])
  (for [line (.splitlines (.read-text path :encoding "utf-8"))]
    (setv stripped (.lstrip line))
    (when (and stripped
               (not (.startswith stripped ";"))
               (not (.startswith stripped "#")))
      (.append out line)))
  out)


;; ---------------------------------------------------------------------------
;; 針の照準の部品(構造で撃つ口)と、集合の宣言(名簿)
;;
;; 実弾 2026-09-17〜09-19(日次の全体検証がこの冊で赤 7 本): 7 本ともルール本文 = law の
;; statement は現在のコードで満たされたままで、落ちたのは針が焼き付けた**ソースの字面**
;; (行の literal・出現回数・語の有無)だけが正当な変更で動いたため。⇒ 針は「数」ではなく
;; **名前の集合**を、「行の字面」ではなく**呼び先と引数の役**を撃つ。集合の宣言(名簿)は
;; この冊の 1 か所に置き、針はそこを読む — 針の中に第 2 の名簿を書かない。
;; ---------------------------------------------------------------------------

(setv TOP-FORM-RE
      (re.compile r"^\((?:defk|deff|defn|defmacro|defclass)\s+(?:#\^\s*\S+\s+)?([^\s\[\(\]]+)"))


(defn #^ list bare-code-lines [#^ Path path]
  "禁止語の針が読む行(bare-code-lines)を、**走査が生きている証拠**つきで返す。
   proofs のどれか 1 つでも見つからなければ赤 — 『絞り込みが空になって静かに緑』を作らない。
   実弾 2026-09-19: 同じ日次の別の族で、rg の type 名の綴り違い(rs)が exit 2 + 空の stdout を
   返し、13 本の検査が**何も走査せずに緑**だった。`not-in` の形の針は空の走査で必ず通るので、
   その形を使う針は走査が生きている証拠を自分で持つ。"
  (setv lines (bare-code-lines path))
  (for [proof proofs]
    (assert (any (gfor line lines (in proof line)))
            f"禁止語の針の走査が生きていない — 証拠 {proof} が {path.name} の code 行に 1 つも無い"))
  lines)


(defn #^ str collapsed-code [#^ Path path]
  "code 行を 1 本の文へ均した綴り(空白の連なりは 1 つ)。**行の折れ方に依らない**針のため。
   反例: :launch-overlay の overlay が carry-launch-flags に包まれて 2 行に折れた拍に、
   1 行の文字列一致で撃っていた針が外れた(a0f475fb・2026-09-18)。"
  (re.sub r"\s+" " " (.join " " (lfor line (code-lines path) (.strip line)))))


(defn #^ dict readers-of [#^ list paths #^ str word]
  "語 word を**読む点**を『それを囲む頂点の form の名 → その行の列』で返す。
   頂点の外の行(import の一覧の項)は読みではないので数えない。
   数ではなく名前の集合で撃つための材料 — 名簿と突き合わせれば、読み手が増えた便は
   『読み手 X を足した — 名簿へ宣言せよ』という読める赤になる。"
  (setv found {})
  (for [path paths]
    (setv block None)
    (for [line (code-lines path)]
      (when (.startswith line "(")
        (setv hit (.search TOP-FORM-RE line))
        (setv block (if (is hit None) None (.group hit 1))))
      (when (and (is-not block None) (in word line))
        (.setdefault found block [])
        (.append (get found block) line))))
  found)


(defn #^ list io-failure-edges-of [#^ Path path]
  "『I/O の失敗を切り離す縁』(except IO-FAILURES)を、その縁が自分で名乗る log の語で並べる
   (`agentd: <縁の名> failed …`)。f-string の欄は {} に均す。数ではなく名前で撃つための材料。"
  (setv lines (code-lines path))
  (setv edges [])
  (for [[i line] (enumerate lines)]
    (when (in "(except [e IO-FAILURES]" line)
      (setv named None)
      (for [ahead (cut lines (+ i 1) (+ i 6))]
        (setv hit (re.search r"agentd: (.+?) failed" ahead))
        (when (and (is-not hit None) (is named None))
          (setv named (re.sub r"\{[^{}]*\}" "{}" (.group hit 1)))))
      (assert (is-not named None)
              f"I/O の縁が log で自分を名乗っていない(名で撃てない縁を足さない): {path.name} の {(+ i 1)} 行目")
      (.append edges named)))
  edges)


(defn #^ list call-args-of [#^ list lines #^ str name]
  "code 行の列から `(name …)` の呼びを見つけ、頂点の引数の綴りの列を呼びごとに返す。
   呼び先と**引数の役**で撃つための材料 — 行の折れ方・空白・局所変数の名に依らない。
   反例: ローカル変数の改名(job-status → fresh-status・6401d1d5 2026-09-17)で、呼びの 1 行を
(defn #^ list call-args-of [#^ list lines #^ str name]
  "code 行の列から `(name …)` の呼びを見つけ、頂点の引数の綴りの列を呼びごとに返す。
   呼び先と**引数の役**で撃つための材料 — 行の折れ方・空白・局所変数の名に依らない。
   反例: ローカル変数の改名(job-status → fresh-status・6401d1d5 2026-09-17)で、呼びの 1 行を
   字面で pin していた針が 2 本落ちた。渡っている cause は 1 度も欠けていなかった。"
  (setv text (.join " " (lfor line lines (.strip line))))
  (setv out [])
  (setv start 0)
  (while True
    (setv at (.find text f"({name} " start))
    (when (< at 0) (break))
    (setv i (+ at 1 (len name)))
    (setv depth 1)
    (setv args [])
    (setv token [])
    (setv in-string False)
    (while (and (< i (len text)) (> depth 0))
      (setv ch (get text i))
      (cond
        in-string
          (do (when (and (= ch "\\") (< (+ i 1) (len text)))
                (.append token ch)
                (setv i (+ i 1))
                (setv ch (get text i)))
              (when (= ch "\"") (setv in-string False))
              (.append token ch))
        (= ch "\"") (do (setv in-string True) (.append token ch))
        (in ch "([{") (do (setv depth (+ depth 1)) (.append token ch))
        (in ch ")]}") (do (setv depth (- depth 1)) (when (> depth 0) (.append token ch)))
        (and (= depth 1) (= ch " ")) (do (when token (.append args (.join "" token))) (setv token []))
        True (.append token ch))
      (setv i (+ i 1)))
    (when token (.append args (.join "" token)))
    (.append out args)
    (setv start (+ at 1)))
  out)

```

# 材料 4: 現在の針(再照準の対象 5 か所)
```hy
       (.put-row world.acp (turn-row "t-lost" "conv-l" "m-l" (- world.local.now-ms 300)))
       (.tick world 1000)
       (setv sid (sid-of world "t-lost"))
       (setv world.state (initial-state))
       (.kill-backend world.sessions sid)
       (.tick world 1000)
       (setv lost (status-of (get world.acp.rows "acp-system:agent-job:t-lost")))
       (assert (= (get lost "phase") PHASE-ENDED))
       (assert (= (last-condition-type lost) "SessionLost"))
       (assert (= (get (status-of (get world.acp.rows "default:turn-record:t-lost")) "state") "ended"))
       (assert (= world.sessions.cleanups []) "session は host に任せる(R25)")
       (.put-row world.acp (message-row "m-l2" "second"))
       (.put-row world.acp (turn-row "t-next" "conv-l" "m-l2" world.local.now-ms))
       (.tick world 1000)
       (assert (= world.sessions.cleanups [sid]) "手番の途中で死んだ候補は片付けて resume(R25)")
       (assert (= (len world.sessions.resumes) 1))
       (assert (= (get (status-of (get world.acp.rows "acp-system:agent-job:t-next")) "phase") PHASE-RUNNING))
       (assert (any (gfor line world.local.logs (in "mid-turn with a dead backend process" line))) "片付けの理由は観測の語で(R25)")
       ;; 純関数: 観測の無い眺めは生きていると読む・死は明示の False だけ(片付けた後の行は終端なので running に戻して読む)。
       (setv view (replace (get world.sessions.views sid) :status "running" :turn-ended-at-ms None))
       (assert (= (run (job-step-of (replace view :backend-alive None) 0 True False)) "observe"))
       (assert (= (run (job-step-of (replace view :backend-alive False) 0 True False)) "session-lost"))
       ;; card acp:kanban-issue:ki-2bd49c68b042: 降りた process がこの手番の結果を器の記録へ出していたなら、
       ;; それは失われた session ではなく終わった手番(結果を持つ)— 材料が名乗る事実を live-backend より先に読む。
       (assert (= (run (job-step-of (replace view :backend-alive False :lifecycle "multi_turn") 0 True True))
                  "turn-end"))
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_dead_backend_of_a_running_job_ends_it_with_session_lost_and_the_next_turn_resumes"
                   "test_live_backend_of_a_recovered_job_is_observed_not_lost"
                   "test_backend_liveness_is_read_from_the_observation_not_the_status_word"]]
         (assert (in (+ "def " name "(") tests) f"R25 の反例の検が無い: {name}"))
       (setv host-tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests" "test_sessionhost_headless.py") :encoding "utf-8"))
       (for [name ["test_recovery_verdict_is_the_one_decision"
  ;; --- headless-first-turn の 2 か所 ---
       (assert (not-in "message" world.acp.lists))
       (setv metric (get (lfor m world.local.metrics :if (= (get m "metric") "agent-job-to-send") m) -1))
       (assert (= (get metric "createdAtMs") 1437))
       (assert (= (get metric "ms") (- 2500 1437))))
     (deftest test-adr-doe-agents-012-mail-delivery-is-evidenced-by-the-row
       ;; R50 の針(構造): 欄の綴りは effects.py の 1 点・claim の宣言は running-status-of の 1 点・
       ;; 行への書きは record-inputs-delivered の 1 点(足す腕だけ・消す腕は無い)・本文と id の対応は
       ;; send-parcels-of の 1 点。反例(挙動)の検は packages/doeff-agents/tests/test_sessionhost_acp.py に在る。
       (setv effects-lines (code-lines (/ ACP-DIR "effects.py")))
       (assert (= (len (lfor line effects-lines :if (.startswith line "JOB_INPUTS_DELIVERED_KEY") line)) 1)
               "欄の綴りの座は effects.py の 1 点(R50)")
       (setv judgment-lines (code-lines (/ ACP-DIR "judgment.hy")))
       (for [name ["inputs-delivered-status-of" "mail-input-ids-of" "send-parcels-of"]]
         (assert (= (len (lfor line judgment-lines :if (.startswith line (+ "(defk " name " ")) line)) 1)
                 f"判断は judgment の 1 点(R50): {name}"))
       ;; claim の status は欄を宣言する(送りの着地を待たない)
       (assert (= (len (lfor line judgment-lines :if (in "(setv (get next JOB-INPUTS-DELIVERED-KEY) [])" line) line)) 1)
               "受けた拍に空で宣言する(R50)")
       (setv agentd-lines (code-lines (/ ACP-DIR "agentd.hy")))
       (assert (= (len (lfor line agentd-lines :if (.startswith line "(defk record-inputs-delivered ") line)) 1)
               "行への書きは 1 点(R50)")
       (assert (= (len (lfor line agentd-lines :if (in "(record-inputs-delivered row.key job-id" line) line)) 1)
  ;; --- turn-credential の 2 か所 ---
       ;; 段 12(card acp:kanban-issue:ki-d13566f4d5eb・決定 案 A): charter の要求の門は走行係の 1 点で、種類の分岐より前に立つ。
       (assert (any (gfor line effects-lines (.startswith line "CHARTER_PLACE_KEY: str = \"place\"")))
               "charter の置き場の欄の綴りは effects の 1 点(card ki-d13566f4d5eb)")
       (assert (any (gfor line effects-lines (in "CONDITION_PLACE_MISMATCH: ConditionType = \"PlaceMismatch\"" line)))
               "要求の食い違いの条件の名は effects の 1 点(card ki-d13566f4d5eb)")
       (assert (any (gfor line effects-lines (.startswith line "AgentdPlace = Literal[\"company\", \"personal\", \"cluster\"]")))
               "置き場の閉語彙に cluster が無い(card ki-d13566f4d5eb の供給の 1 語)")
       (assert (any (gfor line judgment-lines (in "(and (bool places) (is-not place None) (not-in place places))" line)))
               "要求の突合が『charter.place が集合に無い』の形でない(card ki-d13566f4d5eb)")
       (assert (= (len (lfor line agentd-lines :if (in "(place-mismatch settings.places charter-place)" line) line)) 1)
               "charter の要求の突合は claim の腕で 1 度(card ki-d13566f4d5eb)")
       ;; 門が種類の分岐より**前**に在る(verify / summarize が素通りしない)— 行の順で撃つ。
       (setv claim-at (.index agentd-lines "(defk claim-job [settings state rows row previously-deferred now-ms]"))
       (setv gate-at (next (gfor [i line] (enumerate agentd-lines)
                                 :if (and (> i claim-at) (in "(place-mismatch settings.places charter-place)" line)) i)))
       (setv kind-at (next (gfor [i line] (enumerate agentd-lines)
                                 :if (and (> i claim-at) (in "(<- kind str (job-kind-of row))" line)) i)))
       (assert (< gate-at kind-at)
               "要求の門が種類の分岐より後に在る — verify / summarize が門を素通りする(card ki-d13566f4d5eb)")
       (assert (= (len (lfor line agentd-lines :if (in "(turn-session-env-of lease)" line) line)) 1)
               "手番ごとの env を組む点は 1 つ(R30)")
       (for [line (live-bare-lines (/ ACP-DIR "agentd.hy") ["(defk claim-job " "(turn-session-env-of lease)"])]
         (assert (not-in "CLAUDE_CODE_OAUTH_TOKEN" line)
                 f"agentd.hy は札の env の名を直に持たない(R30): {line}"))
       (setv policy-lines (code-lines (/ SESSIONHOST-DIR "policy.hy")))
       (for [name ["session-env-admission-error" "overlay-without-turn-auth" "inheritable-spawn-env" "spawn-env-inherited?"]]
         (assert (= (len (lfor line policy-lines
  ;; --- attachment の 1 か所 ---
       (for [name ["join.hy" "judgment.hy" "agentd.hy" "effects.py" "runtime.py"]]
         (for [line (code-lines (/ ACP-DIR name))]
           (for [word ["\"ACP_BASE\"" "\"AGORA_BRAIN_URL\"" "\"HERDR_HUD_STATE_BACKEND\""]]
             (assert (not-in word line) f"doeff は席向けの env の綴りを持たない(R51 (4)): {name} {line}"))))
       ;; 反例(挙動)の検が在ること。
       (setv tests (.read-text (/ (. (Path __file__) parent parent parent) "packages" "doeff-agents" "tests"
                                  "test_sessionhost_acp.py") :encoding "utf-8"))
       (for [name ["test_join_seat_env_parses_declared_lines"
                   "test_join_refuses_credential_shaped_seat_env"
                   "test_join_refuses_seat_env_that_names_conversation_identity"
                   "test_charter_seat_env_cannot_override_conversation_identity"
                   "test_join_without_seat_env_still_joins"
                   "test_join_refuses_seat_env_that_names_a_binding_owned_home"
```

# 材料 5: 現在の実装(針が読む点)
```
-- host.hy 関所の呼び 2 か所 / 送りの腕:
1609:    (setv env-error (session-env-admission-error session-env method))
1641:    (setv send-env-error (session-env-admission-error session-env "session.send"))
1684:                  (headless-send-program sid message awaiting session-env turn-charter attachments)
-- launch.hy / join.hy 関所:
../../../packages/doeff-agents/src/doeff_agents/sessionhost/launch.hy:694:  (setv env-error (session-env-admission-error session-env "session.launch"))
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/join.hy:821:  (setv admission (session-env-admission-error (dict pairs) "join.seat_env"))
-- mail-turn-text-of:
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy:3200:(defk mail-turn-text-of [message-id spec body [status None]]
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy:3246:          (<- text str (mail-turn-text-of input-id row.spec body row.status))
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy:3502:            (<- text str (mail-turn-text-of message-id message.spec body message.status))
-- resume の名簿:
  ;; ⚠ ここは charter の欄を**名簿で**写す(素通しではない)。名簿は 2 段: 古くからの欄は名前で、
  ;; 席へ運ぶ欄(下)は定義点 1 つから写す。
  ;; 実弾 2026-09-15 09:5x(operator): 段 10 lane 10o の attachments を名簿に入れ忘れたので、
  ;; **腕が resume の手番だけ**画像が黙って落ちていた(誤りも条件も出ないまま model が画像を見ない)。
  ;; 起こす腕は launch / resume / rehydrate の 3 つ — 検が launch しか通っていなかったのが見落としの根。
  (for [key (+ #("prompt" "model" "effort" "mcp_servers" "session_env" "binding"
                 "expected_result" "context_file" "launch_attribution"
                 MESSAGE-ATTACHMENTS-KEY)
               ;; 席へ運ぶ欄(閾値・記憶の置き場・記憶の本文)は**名前をここで数えない** —
               ;; 定義点 1 つ(CHARTER-CARRIED-KEYS)から写す。名前で足した便が族のもう片方を
               ;; 落としたまま通ったのが card acp:kanban-issue:ki-a40292ed30d9 の壊れ方。
               CHARTER-CARRIED-KEYS)]
    (when (in key charter)
      (setv (get params key) (get charter key))))
  params)

-- drain:
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/worker_loop.hy:20:  (ReadLoopControl [] (resume (LoopControl (ports.stopping) (ports.draining))))
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/worker_loop.hy:44:      (<- state AgentdState (agentd-tick (replace settings :draining control.draining) state))
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:534:    在否だけを読む level-triggered の合図で、拍ごとに読み直す(SIGTERM の排水と同じ ``settings.draining`` を立てる —
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:593:    """``LoopPorts.draining`` の 1 点(card acp:kanban-issue:ki-567f2dd6140f §3.1d)。
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:602:    どちらも同じ ``settings.draining`` に落ちるので、capacity の**判断**は
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:608:    def draining() -> bool:
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:629:    return draining
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:663:    (level-triggered)。停止の腕が立てる ``drain`` の合図と**同じ** ``settings.draining`` に落ちるので、
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/runtime.py:780:            f"agentd: stop ({reason}) — draining {running} running job(s): no new claims, capacity 0, "
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/loop_model.py:13:    draining: bool
../../../packages/doeff-agents/src/doeff_agents/sessionhost/acp/loop_model.py:19:    draining: Callable[[], bool]
```

# 材料 6: 検査の走らせ方と検出範囲
- 走行: `.venv/bin/python -m pytest docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k "<名>"`(冊 1 file・54 deftest)
- 日次の全体検証 `ai land verify` が 1 日 1 回この冊を含む全数を走らせる
- 針はソースを静的に読むだけ。挙動の反例は packages/doeff-agents/tests/test_sessionhost_acp.py 等が別に持つ
- 基準 commit: abd03fd2a58dbad29bcb21f435539451315db523
