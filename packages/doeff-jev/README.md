# doeff-jev

doeff-system-one の `Judge` effect を **TypeSafe の Jev** で判定する handler。doeff-llm に対する
doeff-openai と同じ位置(provider 側)。

| 部品 | 中身 |
|---|---|
| `target.py` | 宛先(URL・model・通信の形・API キー)の解き方 1 点。純粋な `resolve_target(env, read_text)` と、組み立て点だけが呼ぶ `target_from_process_environment()` |
| `wire.py` | Judge → HTTP の request / HTTP の答え → Verdict の写し(純粋)。TypeSafe 直と Vercel AI Gateway の 2 形式 |
| `handlers/production.py` | `jev_handler(target)`: Judge を受けて `HttpRequest` を yield する(HTTP を自分では呼ばない)。`jev_memo_handler(target)`: cacheable な Judge の答えを CacheGet / CachePut で包む |
| `handlers/journal.py` | `slog("jev_judge", …)` を 1 行 JSON で file に書く記録の出口 |
| `wiring.py` | handler の積み方 1 か所(`judge_stack`)と、CLI・hook の入口 `run_judgment` |

## 宛先の切り替え

```
JEV_BASE_URL=http://zeus:8646/v1/systemone JEV_MODEL=seimf-27b   # seimf(TypeSafe 互換)へ
JEV_WIRE=gateway                                                  # Vercel 経由(既存の呼び手の形)
```

TypeSafe と Vercel 以外の宛先ではキーが無くても送る。TypeSafe のキーは `TYPESAFE_API_KEY` →
`~/.config/jev/api_key`、Vercel は `AI_GATEWAY_API_KEY` → `~/jev_key`。

## 使い方(組み立て点)

```python
from doeff_system_one import judge, noul
from doeff_jev import run_judgment, target_from_process_environment, durable_cache

verdict = run_judgment(
    judge("この文章は日本語か。", {"ja": noul("この文章は日本語で書かれているか。")}),
    target=target_from_process_environment(),
    cache=durable_cache(),      # 省くと process 内だけの cache
    caller="my-hook",
)
```

テストでは `http=` に HttpRequest を台本で答える handler を渡す(`tests/test_handler.py`)。

## 記録

`~/.local/state/doeff-jev/journal.log` に 1 判定 1 行(宛先・model・問いの数・トークン・所要 ms・
成否・cache の当たり)。Jev と seimf の比較はこの記録で測る。
