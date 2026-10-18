# doeff-claude-judge

doeff-system-one の `Judge` effect を **Claude の構造化の答え**(structured output)で判定する。doeff-jev(TypeSafe の Jev)と
同じ位置(provider 側)で、純粋な変換と、Judge を Claude Code の CLI の 1 回きりの print へ運ぶ handler を持つ。

| 部品 | 中身 |
|---|---|
| `wire.py` | `prepare(judge) -> ClaudeQuery`: Judge を Claude への 1 回の問いの部品(system の文・利用者の文・答えの JSON Schema の文)に写す。`parse(judge, answer, *, model, input_tokens, elapsed_ms) -> Verdict \| MalformedAnswer`: 構造化の答え(JSON の文か読んだ値)を Verdict に写す |
| `target.py` | `ClaudeJudgeTarget`(model・家の設定 dir・子の env・thinking の token 数・effort・資格を借りるか)・`JudgeCredential`(借りた access token と期限)・`NoJudgeCredential`・effect `BorrowJudgeCredential`(判定 1 回の資格を借りる — 使い手の貸し手が答える) |
| `handler.py` | `claude_judge_handler(target)`: Judge → 資格を借りる → doeff-claude-code の `ClaudePrintOnce` を 1 回出す → Verdict。process は起こさない(semgrep の規則 `doeff-claude-judge-starts-no-process`) |
| `wiring.py` | `claude_judge_stack(target, *, printer, lender, command, log_stderr)`(積みの 1 か所)・`run_judgment`・`production_printer(command)`(本番の print の実体)・`scripted_printer(answer)`(検の fake — CLI を起こさない)・`fixed_credential_lender(credential)` |

使い手は、この package の型(`ClaudeJudgeTarget`・`JudgeCredential`・`BorrowJudgeCredential`)と組み立ての道具だけを読み、
doeff-claude-code の型を名指さない。

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

## 判定 1 回の流れと失敗

1. `prepare` で問いの部品を作る(写せない Judge はここで `unsupported`)。
2. 宛先が借りる時(`borrow_credential=True`・既定)は `BorrowJudgeCredential(model=…)` を出す。答えが `NoJudgeCredential` か例外なら
   `no_credentials`。借りた資格は、その CLI の process だけの設定 dir(家の設定 dir の下)の `.credentials.json` に置かれ、process が
   降りたら dir ごと消える(doeff-claude-code)。refresh token は運ばない。
3. `ClaudePrintOnce` を 1 回出す。待ちの上限は Judge の `timeout_seconds`(CLI の起動と終わりの 4〜6 秒を含む — Jev の既定の 4 秒では
   足りないので、呼び手が判定ごとに決める)。口座の利用枠が尽きた print(`PrintLimited`)は `JudgeUsageLimited`(kind `usage_limit`・
   `window` = 尽きた枠の種類・`resets_at` = 戻る刻の epoch 秒 — 名乗らなければ None。呼び手は `JudgeError` のまま受けられる)、CLI が
   API の誤りを名乗れば `http`(`status` = その HTTP status)、答えずに降りた・待ちの上限を越えた・print の handler の誤りは `transport`。
4. 構造化の答えを `parse` で Verdict に写す(形が違えば `malformed`)。Verdict の `model` = CLI が名乗った model・`input_tokens` =
   CLI が名乗った読みの token の和(cache の作り・読みを含む)・`elapsed_ms` = CLI の process の秒。

記録は `slog("claude_judge", …)` の 1 行(model・成否・誤りの種類・token・ms — 資格と本文は載せない)。

## 使い方

```python
from doeff_claude_judge import ClaudeJudgeTarget, JudgeCredential, fixed_credential_lender, run_judgment
from doeff_system_one import Judge, noul

from doeff import do

target = ClaudeJudgeTarget(model="claude-sonnet-5-5", config_dir="/var/lib/judge/claude",
                           env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/var/lib/judge"})
judge = Judge(state={"document": text}, questions={"plain": noul("Can a first-time reader decide from the document?")},
              timeout_seconds=60.0, cacheable=False)


@do
def flow():
    return (yield judge)


verdict = run_judgment(flow(), target=target, lender=fixed_credential_lender(JudgeCredential(access_token=token, expires_at=None)))
```

家の設定 dir は判定だけの空の dir を勧める(借りた資格の dir は、家の設定 dir の中身への link を持つ)。

## 検

```sh
uv run --no-sync pytest packages/doeff-claude-judge/tests -m "not e2e"
```

本物の CLI に 1 回出す検(印 e2e・slow — 本物の利用枠を使う)は、CLI の `.credentials.json` の path を env
`DOEFF_CLAUDE_JUDGE_E2E_CREDENTIALS` に置いた時だけ走る(access token と期限だけを読む)。本文は env `DOEFF_CLAUDE_JUDGE_E2E_DOCUMENT`
(無ければ検が組む 5 万字の見本)、model は `DOEFF_CLAUDE_JUDGE_E2E_MODEL`(既定 claude-sonnet-5-5)、thinking の token 数は
`DOEFF_CLAUDE_JUDGE_E2E_THINKING_TOKENS`。

```sh
DOEFF_CLAUDE_JUDGE_E2E_CREDENTIALS=$CLAUDE_CONFIG_DIR/.credentials.json \
  uv run --no-sync pytest packages/doeff-claude-judge/tests/test_real_cli.py -m e2e -s
```
