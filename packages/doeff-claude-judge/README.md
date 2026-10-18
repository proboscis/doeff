# doeff-claude-judge

doeff-system-one の `Judge` effect を **Claude の構造化の答え**(structured output)で判定するための写し。
doeff-jev(TypeSafe の Jev)と同じ位置(provider 側)で、今は通信をしない純粋な変換だけを持つ。

| 部品 | 中身 |
|---|---|
| `wire.py` | `prepare(judge) -> ClaudeQuery`: Judge を Claude への 1 回の問いの部品(system の文・利用者の文・答えの JSON Schema の文)に写す。`parse(judge, answer, *, model, input_tokens, elapsed_ms) -> Verdict \| MalformedAnswer`: 構造化の答え(JSON の文か読んだ値)を Verdict に写す |

運ぶ handler(Messages API の `output_config.format` か、`claude -p` の `--system-prompt`・標準入力・`--json-schema`)は
この package にまだ無い。どちらで運んでも、この変換は同じ。

## 綴り

- 利用者の文: state を名の札で囲んだ本文と、名つきの問いの並び。state が札の名にできる鍵の表なら欄ごとに
  `<欄の名>…</欄の名>`(例 `{"document": 本文}` → `<document>…</document>`)、それ以外は `<state>…</state>`。
  文字列はそのまま、他は JSON。
- 答えの Schema: 問いの名ごとの欄(全部が要る・余りを許さない)。
  - noul → `number`(はいの確率)。Verdict の答えの value = その確率。
  - choice → 候補の id を鍵にした `object`(候補ごとの確率・全部の候補が要る)。Verdict の答えの probabilities =
    その表、value = 確率の最も高い候補の id、confidence = その確率。
  - score の問いと画像(assets)は写さない(`JudgeError` の `unsupported`)。
- Schema は Messages API の構造化の答えが受ける部分だけを使う(`minimum` / `maximum` と配列の長さの制約は受けない)。
  範囲 0〜1 は説明の文で伝え、`parse` が確かめる。choice を候補の数と同じ長さの配列にせず id の object にするのも同じ訳。

## 形の違う答え

答えが JSON でない・object でない・欄が欠ける・余る・数でない(真偽値も数と見ない)・有限でない・0〜1 の外の答えは、
Verdict を作らずに `MalformedAnswer(detail, question)` で返す。運ぶ handler は `as_error()` で doeff-jev と同じ
`JudgeError("malformed", …)` にして Judge の呼び手へ投げる。

確率は答えの値のまま運ぶ(丸めない・足して 1 に直さない)。床と比べるのは呼び手で、判定器の値そのもの。

## 使い方

```python
from doeff_claude_judge import parse, prepare
from doeff_system_one import Judge, noul

judge = Judge(state={"document": text}, questions={"plain": noul("Can a first-time reader decide from the document?")})
query = prepare(judge)          # query.system / query.user / query.schema_json を運ぶ handler が送る
outcome = parse(judge, structured_output, model="claude-sonnet-5-5")
```
