# doeff-system-one

判定の effect。「この state について、この問いにどう答えるか」を較正された判定器(TypeSafe の
System One = Jev、または同じ契約を持つ別の実装)に出し、確率つきの答えを受け取る。

provider を知らない層で、doeff-llm と doeff-openai の関係と同じ分け方をとる。

| package | 役目 |
|---|---|
| `doeff-system-one`(この package) | effect の契約(`Judge`)・問いと答えの型・テスト用の scripted handler |
| `doeff-jev` | TypeSafe(直の API と Vercel AI Gateway)へ `Judge` を写す handler・宛先と鍵の解決・答えの cache |
| 自前の判定器の package(将来) | 自前の GPU で動く Jev 互換の判定器の handler(通信の形が同じなら doeff-jev の handler に宛先を渡すだけ) |

## 使い方

```python
from doeff import do, run, with_handlers
from doeff_system_one import Judge, choice, noul, score

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
    # 確信度が床の下なら「判定できない」— 「条件を満たさない」と読まない。
    return vague.confidence is not None and vague.confidence >= 0.6 and vague.value >= 0.7
```

handler は組み立て点(composition root)が選ぶ。テストでは `scripted_judge_handler` を積む。

## 問いの 3 種類

| 種類 | 答え | 使いどころ |
|---|---|---|
| `choice(instructions, options)` | `value` = 選ばれた option・`probabilities`・`confidence` | 決まった候補から 1 つ選ぶ |
| `noul(instructions)` | `value` = はいの確率(0〜1)・`confidence` = 0.5 からの距離 × 2 | 条件が成り立つか |
| `score(instructions, levels)` | `value` = 段階の期待値・`confidence` | 程度を測る |

## 答えの読み方

公開 API は effect と handler と、問いと答えの型だけ(ADR-DOE-API-001 R1)。答えを読む純粋な関数(`answered`・`chosen`・
`crosses`・`calibration_ok`・`clip`)と state の目安の字数 `STATE_CHARS_ADVISED` は、この package の内側(`_judgment`)に
置き、入口から出さない。呼び手は次の読み方を自分の code で書く。

- 確信度(`confidence`)が無いか床の下の答えは「判定できない」であって「条件を満たさない」ではない。
- choice は、確信度が床の上で、値が「どれでもない」の option でない時だけ採る。
- 使う前に判定器を測る: 違反する例が越え、守る例が越えず、**両方が答えを出せている**こと。
- state に載せる文は 2,000 字ほどまでに切る。2,000 字を超えると確信度が落ちる(実測 2026-09-19)。
