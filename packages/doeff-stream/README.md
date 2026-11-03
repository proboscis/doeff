# doeff-stream

入力の source を 1 つの列に merge し、純な step で fold する runtime です。

```
host = fold(step, 初期の状態, merge(subscribe(...), timers()))
```

- step は純な関数 `step(state, input) -> Next(state, effects)` か、その `Next` を答える Program を返す関数(別の defk を `<-` で呼ぶだけの defk)です。effect は値で、runtime が実行します。step の Program は effect を 1 つも出せません(節「step を Program で書く」)。
- runtime は `Run(key, program)` を 1 つずつ自分の task で走らせ、その task の終わりを必ず 1 つの入力として step に戻します。
  値で終われば `Answered(key, value)`、例外で終われば `Raised(key, error)`、step の `CancelRun(key)` で止まれば `Cancelled(key)`。
- fold は effect の答えを待ちません。待つのは次の入力だけです。

中身は 4 つだけです。map・window・backpressure のような operator の群は持ちません。

| 部品 | 口 | 中身 |
|---|---|---|
| Source | `subscribe(*types)`・`timers()` | 外の task が bus に出す出来事と、step が置いた timer の期限(`TimerFired`)。effect の答えと task の終わり、止めの合図(`StopArrived`)と合図の源の失敗(`SourceFailed`)は runtime 自身の source で、どの merge にも必ず入る |
| merge | `merge(*sources) -> Inputs` | source を 1 つの入力の列にする。`Inputs.event_types` が fold の待つ型の全部 |
| fold | `fold(step, initial, inputs)` | 入力を 1 つずつ step に渡し、答えの effect を順に実行する。`Finish(value)` で `value` を答える |
| test の harness | `replay(step, initial, inputs) -> Replay` | 入力の列を step に与え、状態(`states`・`final`)と effect(`effects`)の列を返す。素の関数の step は VM を使わない。Program を返す step は、fold と同じ評価(effect を全部断る handler)で入力 1 つごとに VM の上で走らせる |

## step が返す effect

| effect | 振る舞い |
|---|---|
| `Run(key, program)` | `program`(Program か effect)を新しい task で走らせる。終わりは `key` を持つ入力 1 つで戻る。走っている key の `Run` と、Program でも effect でもない `program` は断る |
| `CancelRun(key)` | `key` の task を取り消す。終わりはやはり 1 つの入力で戻る(取り消しが効けば `Cancelled`、先に終わっていればその終わり)。走っていない key は何もしない |
| `Finish(value)` | fold を終える。1 つの答えに 1 つまでで、どこに置いてもほかの effect の後に実行する。走っている task を取り消し、全部が終わるのを待ってから `value` を答える(その終わりは step に渡さない) |

step が例外を上げた時、または fold の task が取り消された時は、走っている task を取り消してから例外を先へ通します。

## 終わりが必ず入力になる訳

`Run` 1 つに task を 2 つ立てます。program を走らせる task と、それを `Wait` で見張る task です。見張りは try/except で終わりを
受けて、値・例外・取り消しのどれでも `Publish` を 1 度します。`CancelRun` が取り消すのは program の task だけで、見張りは
取り消しません。だから、始まる前に取り消された task の終わりも入力になります。program の task が `TaskCancelledError` で
終わった時は、step がその key を取り消していれば `Cancelled`、自分で上げた物(取り消された別の task の待ち)なら `Raised` です。

見張りの `Publish` は fold の購読者の列に積まれるので、step が別の入力を処理している間に終わった task も落ちません。同じ bus の
別の fold が走らせた task の終わりは、その fold の物と見分けて読み捨てます。

## step と run が守る約束

| 約束 | 訳 |
|---|---|
| 部品ごとに key を分ける(`("fetch", item)` のように、頭に部品の名を置いた tuple) | 走っている key の `Run` は断られ、run の終わりは key でしか振り分けられない |
| 同じ bus に fold を 2 つ置く時は、timer の tag も fold ごとに分ける | `TimerFired` は bus の全部の購読者に届き、run の終わりのようには fold を見分けない |
| step の Program は effect を出さない(`Ask`・時計・読み書き・`Wait`・`Spawn`・`WaitForEvent` のどれも) | fold が step の Program に全部の effect を断る handler を被せ、出した effect を `StepYieldedEffectError` で止める。外の世界は入力で読み、したい仕事は `Run` で頼む |
| run の program は `WaitForEvent`・`WaitForEvents` をしない | run は fold の bus の handler の下で走るので、その待ちは fold の入力を横取りする |
| run は取り消されたら終わる。外の世界を待つ run は自分の待ちに期限を付ける(`WaitWithin`) | fold は時計を持たず、`Finish` と例外の道は run が終わるまで待つ |
| 止めの合図(`StopArrived`)を受けたら、step が止めの順を決めて `Finish` を返す | fold は止めの合図で勝手に止まらない |

## 組み立て

fold が被せる handler は、step の Program の周りの 1 つ(effect を全部断る物)だけです。run には被せません。呼び手が、`inputs.event_types` を購読する bus の handler(`subscribed_event_handler`)と、
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

## step を Program で書く

step は `Next` の値の代わりに、`Next` を答える Program を返してよいです。defk で書いた reducer が、別の defk を `<-` で呼んで答えを組む形です。

```python
@do
def ordered(state, item):
    return Next(state | {item}, (Run(("fetch", item), fetch(item)),))

@do
def step(state, event):
    match event:
        case Order(item=item):
            return (yield ordered(state, item))
        case _:
            return Next(state)
```

- fold は step の答えが Program なら、全部の effect を断る handler の下で評価し、その値を `Next` として検めます。`replay` も同じ評価の道(`step_answer`)を通るので、同じ入力の列から同じ状態と effect の列が出ます。
- 断るのは列挙した effect ではなく全部です。step の Program が effect を出すと、継続を再開せずにその場で評価を終え、`StepYieldedEffectError`(`effect` = 出した effect・`step` = step の名)を上げます。step の中の `try` はこの断りを受けられません。fold では、ほかの step の例外と同じく走っている run を取り消してから先へ通します。
- step の答えは `Next` か、`Next` を答える Program のどちらかだけです。Program が `Next` 以外(tuple・別の Program)を答えるか、`effects` に `StepEffect` 以外の値(素の Program など)が混ざれば `TypeError` です。
- 素の関数の step は今までと同じに動き、`replay` も VM を使いません。

費用(2026-11-03・x86_64・Python 3.14・素の関数の step の呼び 1 回は約 0.5 µs): Program の step 1 回の評価は、合成 1 段で fold の中なら約 5 µs、`replay` では入力 1 つごとの `run` を含めて約 6 µs、別の Program を 10 段呼ぶと約 25 µs(1 段ごとに約 2 µs)。1 回の入力で多くの候補を判じる step で上乗せが効くなら、その判じを素の関数のまま step から呼べます。

## test の harness

```python
from doeff_stream import Answered, Finish, Run, replay

trace = replay(step, None, [Poke(), Answered("fetch", 3)])
assert [type(effect) for effect in trace.effects] == [Run, Finish]
assert trace.effects[-1] == Finish(3)
assert trace.final == 3
```

`replay` は fold と同じ検めで step の答えを確かめ、runtime が渡さない入力(走っていない key の終わり・`Finish` の後の入力)を断ります。
