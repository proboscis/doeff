# 盲検の入力 — 「手番の文を組む座は 1 点」の静的検査(R16 の針)

対象版: doeff `origin/main` = `09309e77ecd24d3f555b012000e910782b734fa0`。
提案 = 下の「提案の差分」を当てた状態(冊 1 file のみの変更)。

読み取り専用で読める実物(**変更しないでください**):
- 冊(検査の在り処): `/Users/s22625/.worktrees/doeff-wt-adr012-sendback/docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy`
  (この複製には提案が既に当たっています。⚠ 別の検証が同じ file を書き換えながら走っているので、
   読んだ瞬間に一時的な変異が混ざることがあります。判断は下に転記した本文を正本にしてください)
- 実コード: 同じ複製の `packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy` と
  `.../acp/agentd.hy`

## 1. 系の文脈(何をする系か)

`agentd` は機体ごとの常駐で、制御面(ACP)の行を読んで agent の session を起こし、
会話へ届いた郵便(Message)を agent の手番へ渡す。郵便が手番へ渡る形は 2 通り在る:

- 1 手番目に畳む(headless の器): `session.launch` の prompt に郵便の文を畳んで起こす。
- 走っている手番へ注入する: `session.interject` で、走っている session へ文を差し込む。

どちらの路でも「agent が読む郵便の文」は同じ 1 つの綴りでなければならない(見出し 1 行 +
本文)。見出しには郵便の id・種類(kind)・class・差出人・親・時刻が載る。

## 2. 責務(この設計が守ると主張するもの)

| 責務 | 所有者 |
| --- | --- |
| 手番へ渡す郵便の文を**組む**(見出し + 本文の合成) | `judgment.hy` の `mail-turn-text-of` の 1 点 |
| 組んだ文を**運ぶ**(畳む・注入する) | `judgment.message-bodies-of`(畳み)と `agentd.deliver-interrupts-of`(注入) |
| 実 I/O(session への launch / interject) | `handlers.py`(この検査の対象外) |

隠す知識 = 見出しの綴り・欄の順・欄の在否の判断。運ぶ側はそれを 1 つも知らない。

## 3. 公開契約

- `judgment.mail-turn-text-of [message-id spec body [status None]] -> str`(純粋な判断)
- 法 R16(冊の `law headless-first-turn-carries-the-mail` の一部・逐語):
  「the prompt of session.launch / session.resume = first-turn-prompt-of(…)」
  「手番の文を組む座は judgment.mail-turn-text-of の 1 点」
- `SessionInterject :session-id :text :ref :attachments` — 走っている手番への注入の要求(型つき)

## 4. 実コードの現状(2 か所ちょうど)

`judgment.hy` の `message-bodies-of`(畳みの側):

```hy
    (if (and (is-not row None) (isinstance body str))
        (do
          (<- text str (mail-turn-text-of input-id row.spec body row.status))
          (.append bodies text)
          (.append attachments (.get carried input-id #())))
        (.append missing input-id)))
```

`agentd.hy` の `deliver-interrupts-of`(注入の側):

```hy
            (<- text str (mail-turn-text-of message-id message.spec body message.status))
            (<- outcome (| Interjected SessionRefused)
                (SessionInterject :session-id job.session-id :text text :ref message-id
                                  :attachments carried))
```

## 5. 提案の差分(検査の実体・この file の全文が下の path に在ります)

`sendback-1/prototype.diff`(sha256 `f97ff57357e0de5baad886bb681974ccbbc636ffecaa7dea2494badc8a12edf2`)
= `/Users/s22625/.worktrees/doeff-wt-adr012-design/docs/design/adr012-pin-reaim/sendback-1/prototype.diff`

骨は 3 つ:

1. **form で読む口**(冊の中の部品): `hy.read-many` で対象の file を Hy 自身の reader に
   読ませ、`(mail-turn-text-of …)` の呼びごとに #(頂点の form の名・呼びを包む form の頭の綴り・
   呼びの返りを束ねた名・その名の**読み**の列)を返す。読みの綴りは `(f …)` → `"f"`・
   `(.m recv …)` → `"recv.m"`・属性 → `"."`・列 `[…]` → `"(列)"`。
2. **名簿**(冊の唯一の宣言点):
   ```hy
   (setv MAIL-TURN-TEXT-CONSUMERS
         {"judgment.hy" {"bodies.append" "…同じ bodies へ、順のまま積む"}
          "agentd.hy" {"SessionInterject" "…組み替えずそのまま渡す"}})
   ```
3. **針**(2 file それぞれに対して):
   - 呼びは 1 つ(form 上でも 1 つ)
   - 呼びを包む form の頭が `<-` でなければ赤
   - 束ねた名の読みの集合が名簿の鍵の集合と**一致**しなければ赤
     (名簿の外の読み手が読んだら赤・名簿の読み手が読まなくなったら赤)
   - 禁止の綴り(`setv` / `setx` / `let` 等)を針の中に**列挙しない**

## 6. 検査の実行(そのまま撃てる 1 行)

```
cd /Users/s22625/.worktrees/doeff-wt-adr012-sendback
PYTHONPATH="$(cat /tmp/sb-pp.txt)" /Users/s22625/repos/doeff/.venv/bin/python -m pytest \
  docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy -k first_turn_carries --no-header -q
```

1 本・約 30 秒。検出範囲 = 上の 2 file の**同じ頂点の form の中**。
この冊の他の 58 本と、全数・全体の走行は**撃たないでください**(1 日 1 回の制限が在ります)。
⚠ 実験は自分の複製を作って行い、上の複製と `~/repos/doeff` は書き換えないでください。

## 7. 設計者の主張(反証の対象)と前提

- 主張 1: `mail-turn-text-of` が返した文を**同じ頂点の form の中で**組み替える実装は、
  書き方の綴りに依らず赤になる(行の折れ方・空白・局所変数の名・どの構文で束ね直すかに依らない)。
- 主張 2: 文の読み手を足す/替える変更は、名簿への 1 行の宣言で緑になり、宣言しなければ赤になる。
- 主張 3: 意味を変えない整理(折り直し・束ねた名の改名・欄の順の入れ替え・呼びに引数を足す)は
  緑のまま。
- 前提: 覆うのは**同じ頂点の form の中**ちょうど。別の頂点の form へ渡った後の組み替え、
  `handlers.py` の実 I/O の中の組み替え、`mail-turn-text-of` を呼ばずに文を作る形は、
  この針は捕まえない(前者 2 つは検出範囲の外・後者は「呼びは 1 つ」と法の挙動の反例が別に押さえる)。

## 8. 現実的に来る変更の候補(材料・これに縛られないでください)

- 見出しに欄を足す(受入の期限・優先度・添付の数)。
- 1 手番に複数の郵便を畳む時の区切りを変える。
- 音声の会話向けに文を平易化する / 長すぎる本文を切る。
- codex の器(app-server)向けに文の形を変える。
- 注入の時だけ「割り込みです」の 1 行を足す。
