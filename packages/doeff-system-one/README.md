# doeff-system-one

判定の effect。「この state について、この問いにどう答えるか」を較正された判定器(TypeSafe の
System One = Jev、または同じ契約を持つ別の実装)に出し、確率つきの答えを受け取る。

provider を知らない層で、doeff-llm と doeff-openai の関係と同じ分け方をとる。

| package | 役目 |
|---|---|
| `doeff-system-one`(この package) | effect の契約(`Judge`)・問いと答えの型・純粋な判断の道具・テスト用の scripted handler |
| `doeff-jev` | TypeSafe(直の API と Vercel AI Gateway)へ `Judge` を写す handler・宛先と鍵の解決・答えの cache |
| `doeff-seimf`(将来) | zeus の GPU で動く Jev 互換の判定器の handler(通信の形が同じなら doeff-jev の handler に宛先を渡すだけ) |

## 使い方

```python
from doeff import do, run, with_handlers
from doeff_system_one import Judge, choice, noul, score, answered

@do
def is_vague(prompt: str):
    verdict = yield Judge(
        state={"prompt": prompt},
        questions={
            "vague": noul("この依頼は曖昧で、着手前に計画が要るか。"),
            "kind": choice("この依頼の種類はどれか。", ["調査", "実装", "運用"]),
        },
    )
    vague = verdict.answer("vague")
    return answered(vague, floor=0.6) and vague.value >= 0.7
```

handler は組み立て点(composition root)が選ぶ。テストでは `scripted_judge_handler` を積む。

## 問いの 3 種類

| 種類 | 答え | 使いどころ |
|---|---|---|
| `choice(instructions, options)` | `value` = 選ばれた option・`probabilities`・`confidence` | 決まった候補から 1 つ選ぶ |
| `noul(instructions)` | `value` = はいの確率(0〜1)・`confidence` = 0.5 からの距離 × 2 | 条件が成り立つか |
| `score(instructions, levels)` | `value` = 段階の期待値・`confidence` | 程度を測る |

## 判断の道具(純粋)

- `answered(answer, floor)` — 確信度が床の上か。**床の下は「判定できない」であって「条件を満たさない」ではない。**
- `chosen(answer, floor, none_label)` — choice で、確信度が床の上かつ「どれでもない」でない時だけ値を返す。
- `calibration_ok(bad, good, crosses, floor)` — 使う前に判定器を測る。違反する例が越え、守る例が越えず、**両方が答えを出せている**こと。
- `clip(text, limit)` — state に載せる文を上限で切る。2,000 字を超えると確信度が落ちる(実測 2026-09-19)。
