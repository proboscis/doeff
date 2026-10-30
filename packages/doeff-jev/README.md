# doeff-jev

doeff-system-one の `Judge` effect を **TypeSafe の Jev** で判定する handler。doeff-llm に対する
doeff-openai と同じ位置(provider 側)。

入口 `doeff_jev` が見せるのは、handler と handler の工場と設定の型だけ(doeff の ADR-DOE-API-001 R1):
`judge_stack`・`jev_handler`・`jev_memo_handler`・`journal_handler`・`durable_cache` と `JevTarget`。下の module は内側
(名に `_` を付けた所)に在り、使い手は読まない。

| 部品(内側) | 中身 |
|---|---|
| `_target.py` | 宛先(URL・model・通信の形・API キー)の解き方 1 点。純粋な `resolve_target(env, read_text)` と、宛先を渡されない `judge_stack` が呼ぶ `target_from_process_environment()` |
| `_wire.py` | Judge → HTTP の request / HTTP の答え → Verdict の写し(純粋)。TypeSafe 直と Vercel AI Gateway の 2 形式 |
| `_handlers/production.py` | `jev_handler(target)`: Judge を受けて `HttpRequest` を yield する(HTTP を自分では呼ばない)。`jev_memo_handler(target)`: cacheable な Judge の答えを CacheGet / CachePut で包む |
| `_handlers/journal.py` | `slog("jev_judge", …)` を 1 行 JSON で file に書く記録の出口 |
| `_wiring.py` | handler の積み方 1 か所(`judge_stack`)と、process をまたぐ cache の実体(`durable_cache`) |

## 宛先の切り替え

```
JEV_BASE_URL=http://<host>:8646/v1/systemone JEV_MODEL=<model>  # 自前で動かす Jev 互換の判定器へ
JEV_WIRE=gateway                                                  # Vercel 経由(既存の呼び手の形)
```

TypeSafe と Vercel 以外の宛先ではキーが無くても送る。TypeSafe のキーは `TYPESAFE_API_KEY` →
`~/.config/jev/api_key`、Vercel は `AI_GATEWAY_API_KEY` → `~/jev_key`。

## 使い方(組み立て点)

program は effect `Judge` を出すだけで、組み立て点が `judge_stack` の積みを被せて doeff の `run` で回す。

```python
from doeff import do, run, with_handlers
from doeff_core_effects import scheduled
from doeff_system_one import Judge, Question
from doeff_jev import durable_cache, judge_stack

@do
def is_japanese():
    return (yield Judge(state="この文章は日本語か。",
                        questions={"ja": Question(kind="noul", instructions="この文章は日本語で書かれているか。")}))

stack = judge_stack(            # 宛先を渡さなければ、この process の環境と home の file から解く(上の「宛先の切り替え」)
    cache=durable_cache(),      # 省くと process 内だけの cache
    caller="my-hook",
)
verdict = run(scheduled(with_handlers(stack, is_japanese())))
```

宛先を決めて渡す時は `judge_stack(JevTarget(base_url, model, wire, api_key, source), …)`。
テストでは `http=` に HttpRequest を台本で答える handler を渡す(`tests/test_handler.py`)。

## 記録

`~/.local/state/doeff-jev/journal.log` に 1 判定 1 行(宛先・model・呼び手 `caller`・問いの key の列 `keys`・問いの数・
トークン・所要 ms・成否・中継の名乗り `proxy`・手元の cache の当たり `cached`)。Jev と自前の判定器の比較はこの記録で測る。
journal を積まない組み立て点では、同じ行が外の slog の出口(stderr など)へ抜ける。

## 呼び手の名と中継の名乗り

`caller`(`judge_stack` / `jev_handler` の引数)は、記録の行に載るほか、要求の見出し `x-jev-caller` で宛先へ送る
(形は `<部品>/<出来事か規則>` — 例 `rulebook-advisor/Stop`・`doeff-linter/DOEFF205`・`messaging/classification`)。
本文には入れない(中継 jev-proxy の答えの鍵を変えないため)。中継が答えの見出し `x-jev-proxy` で名乗った答えの出所
(hit / miss / coalesced / refreshed)は `Verdict.proxy` に、手元の cache が答えた判定は `Verdict.cached = True` に印す
(`input_tokens`・`elapsed_ms` は問うた時の値のまま — 費用を数える時は cached を除く)。
