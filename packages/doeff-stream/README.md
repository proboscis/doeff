# doeff-stream

入力の source を 1 つの列に merge し、純な step で fold する runtime です。

```
host = fold(step, 初期の状態, merge(subscribe(...), timers()))
```

- step は純な関数 `step(state, input) -> Next(state, effects)` です。effect は値で、runtime が実行します。
- runtime は `Run(key, program)` を 1 つずつ自分の task で走らせ、その task の終わりを必ず 1 つの入力として step に戻します。
  値で終われば `Answered(key, value)`、例外で終われば `Raised(key, error)`、step の `CancelRun(key)` で止まれば `Cancelled(key)`。
- fold は effect の答えを待ちません。待つのは次の入力だけです。

中身は 4 つだけです。map・window・backpressure のような operator の群は持ちません。

| 部品 | 口 | 中身 |
|---|---|---|
| Source | `subscribe(*types)`・`timers()` | 外の task が bus に出す出来事と、step が置いた timer の期限(`TimerFired`)。effect の答えと task の終わりは runtime 自身の source で、どの merge にも必ず入る |
| merge | `merge(*sources) -> Inputs` | source を 1 つの入力の列にする。`Inputs.event_types` が fold の待つ型の全部 |
| fold | `fold(step, initial, inputs)` | 入力を 1 つずつ step に渡し、答えの effect を順に実行する。`Finish(value)` で `value` を答える |
| test の harness | `replay(step, initial, inputs) -> Replay` | VM を使わずに入力の列を step に与え、状態(`states`・`final`)と effect(`effects`)の列を返す |

## step が返す effect

| effect | 振る舞い |
|---|---|
| `Run(key, program)` | `program`(Program か effect)を新しい task で走らせる。終わりは `key` を持つ入力 1 つで戻る。走っている key の `Run` は断る |
| `CancelRun(key)` | `key` の task を取り消す。終わりはやはり 1 つの入力で戻る(取り消しが効けば `Cancelled`、先に終わっていればその終わり)。走っていない key は何もしない |
| `Finish(value)` | fold を終える。答えの最後に置く。走っている task を取り消し、全部が終わるのを待ってから `value` を答える(その終わりは step に渡さない) |

step が例外を上げた時、または fold の task が取り消された時は、走っている task を取り消してから例外を先へ通します。

## 終わりが必ず入力になる訳

`Run` 1 つに task を 2 つ立てます。program を走らせる task と、それを `Wait` で見張る task です。見張りは `Try` で終わりを
受けて、値・例外・取り消しのどれでも `Publish` を 1 度します。`CancelRun` が取り消すのは program の task だけで、見張りは
取り消しません。だから、始まる前に取り消された task の終わりも入力になります。program の task が `TaskCancelledError` で
終わった時は、step がその key を取り消していれば `Cancelled`、自分で上げた物(取り消された別の task の待ち)なら `Raised` です。

見張りの `Publish` は fold の購読者の列に積まれるので、step が別の入力を処理している間に終わった task も落ちません。同じ bus の
別の fold が走らせた task の終わりは、その fold の物と見分けて読み捨てます。

## 組み立て

fold は handler を被せません。呼び手が、`inputs.event_types` を購読する bus の handler(`subscribed_event_handler`)と、
`timers()` を使う時は timer の handler をその内側に置き、`scheduled` の下で走らせます。

```python
from doeff_core_effects import scheduled
from doeff_events import EventBus, subscribed_event_handler, timer_handler
from doeff_stream import Answered, Finish, Next, Run, fold, merge, subscribe, timers

def step(state, event):
    match event:
        case Poke():
            return Next(state, (Run("fetch", fetch()),))
        case Answered(key="fetch", value=value):
            return Next(value, (Finish(value),))
        case _:
            return Next(state)

inputs = merge(subscribe(Poke), timers())
host = subscribed_event_handler(EventBus(), "host", inputs.event_types)(
    timer_handler()(fold(step, None, inputs))
)
result = run(scheduled(sim_time_handler(start_time=T0)(host)))
```

## test の harness

```python
from doeff_stream import Answered, Finish, Run, replay

trace = replay(step, None, [Poke(), Answered("fetch", 3)])
assert [type(effect) for effect in trace.effects] == [Run, Finish]
assert trace.effects[-1] == Finish(3)
assert trace.final == 3
```

`replay` は fold と同じ検めで step の答えを確かめ、runtime が渡さない入力(走っていない key の終わり・`Finish` の後の入力)を断ります。
