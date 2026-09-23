---
title: "カードを出すまで待つ。そのまま書けるゲーム進行 — doeffでゲームエンジンを作る"
emoji: "🃏"
type: "tech"
topics: ["python", "doeff", "ゲーム開発"]
published: false
---

カードを選ぶ。効果を解決する。相手の行動を待つ。次のターンへ進む。

この順序を、入力待ちまで含めてPythonのループで書けます。次は、このあと動かす戦闘処理の抜粋です。

```python
from doeff import do  # 下の関数を、yieldで合成できるProgramへ変える。

@do  # イベント待ちを含むループを、合成可能なProgramにする。
def event_battle():  # 入力が届くたびに敵のHPを減らす。
    hp = yield Get("enemy_hp")  # 初期HPの10を状態ハンドラから読む。
    while hp > 0:  # 敵のHPが残る間だけ次のカードを受け付ける。
        card = yield WaitForEvent(CardPlayed)  # CardPlayedが届くまで、この計算を待機させる。
        if card.damage <= 0:  # 不正なカードで進行が止まらなくなることを防ぐ。
            raise ValueError("ダメージは正の値にしてください")  # 入力の誤りを呼び出し側へ伝える。
        hp = max(0, hp - card.damage)  # HPを10→7→3→0と減らし、負数にはしない。
        yield Put("enemy_hp", hp)  # 更新後のHPをこの対局の状態へ保存する。
    return "勝利"  # 最後のカードでHPが0になると、待機ループを終了する。
```

**「カードが出されるまで待つ」を、ゲーム進行の途中に書く。** 入力を受け取る場所と、入力をどこから供給するかを分けられます。待っている間には、スケジューラが別のタスクを進めます。

![ゲームの進行を保ち、入力の解釈を取り替える](/images/zenn-use-cases-v0/generated/games-concept.png)

ゲームは入力をyieldし、ハンドラが入力値を返します。固定入力と保存履歴を交換しても、ターンを進めるコードは共通です。

## イベントを待つループを、仮想時間で動かす

最初は、3枚のカードを1秒間隔で出す例です。ここからのコードは、上の`event_battle`と同じファイルへ順に置けます。[実行できる完全な例](examples/game_replay.py)にも各行の目的と期待結果を載せています。追加パッケージは`doeff-events`と`doeff-time`です。

```python
import json  # 入力履歴をJSONへ保存し、同じデータを読み戻す。
from collections import deque  # 再生済みの入力を取り除き、欠落と余剰を検出する。
from dataclasses import asdict, dataclass  # 入力を不変の値にし、履歴には辞書として残す。
from datetime import datetime, timezone  # 仮想時計の開始点をUTCで固定する。
from pathlib import Path  # 検証用JSONの保存先を組み立てる。
from tempfile import TemporaryDirectory  # 検証後に保存ファイルを自動で片付ける。

from doeff import Effect, Pass, Resume, do, handler, run  # 操作・解釈・再開と検証時の実行を使う。
from doeff_core_effects import Get, Put, state  # 対局ごとのHPを読み書きする。
from doeff_core_effects.scheduler import Spawn, Wait, scheduled  # 入力と対局を並行に進める。
from doeff_events import Publish, WaitForEvent, event_handler  # カード入力の発行と待機を扱う。
from doeff_time import Delay, GetTime, sim_time_handler  # 実時間を待たず、経過秒数を確認する。

@dataclass(frozen=True)  # 一度出したカード入力を途中で書き換えない。
class CardPlayed:  # 画面やスクリプトから届く入力の値を表す。
    damage: int  # この入力によるダメージを持つ。検証では3・4・5を使う。

@do  # 入力の供給もProgramにして、時間の解釈を外側に委ねる。
def publish_cards():  # 人間の操作の代わりに、固定したカード列を供給する。
    for damage in (3, 4, 5):  # 3枚で初期HP10を削り切る入力を選ぶ。
        yield Delay(1)  # 次の購読登録を待ち、仮想時計で1秒後にカードを出す。
        yield Publish(CardPlayed(damage))  # その時点で待っている対局に入力を渡す。


@do  # 入力と戦闘を一つのProgramへ合成する。
def event_match():  # 結果・残りHP・仮想経過秒数を検証できる形で返す。
    started = yield GetTime()  # 仮想時計の開始時刻を読む。
    player = yield Spawn(publish_cards())  # 入力を供給する別タスクを開始する。
    result = yield event_battle()  # 戦闘のProgramを合成し、「勝利」を受け取る。
    yield Wait(player)  # 3枚目の発行を含め、入力タスクの終了まで確認する。
    hp = yield Get("enemy_hp")  # 戦闘が更新した最終HPの0を読む。
    finished = yield GetTime()  # 3回のDelayで3秒進んだ仮想時刻を読む。
    return result, hp, (finished - started).total_seconds()  # 期待値は("勝利", 0, 3.0)。
```

```python
events = state(initial={"enemy_hp": 10})(event_match())  # この戦闘専用に初期HP10を持たせる。
events = event_handler()(events)  # カードの発行と待機をつなぐハンドラを取り付ける。
events = sim_time_handler()(events)  # Delayを実時間の待機ではなく仮想時計の前進として扱う。
assert run(scheduled(events)) == ("勝利", 0, 3.0)  # 検証：仮想3秒、HP0で勝利する。
```

入力の供給も戦闘も`@do`で書き、`yield event_battle()`で合成しています。ゲームループ全体を別の`async def`へ移す必要はありません。

ここで使う`doeff-events`のハンドラは、**その時点で待っている購読者へ配信する、メモリ上の仕組み**です。未受信のカードを自動で蓄積するキューではありません。この例では毎回`Delay(1)`を挟み、戦闘側の次の待ち受け登録が先に進みます。実際のゲームで連続入力を受ける場合は、対局ID・ターン・入力の順番の照合や、受信確認などを設計します。

## ゲーム固有の操作をeffectにする

次は「カードを引く」「行動を選ぶ」「ダメージを与える」を、それぞれ操作として分けます。以下の`DrawCard`・`ChooseAction`・`DealDamage`は**この記事で定義するドメインAPI**です。組み込みのゲーム用APIではありません。

```python
@dataclass(frozen=True)  # 操作の引数を固定し、記録と照合できる値にする。
class DrawCard(Effect):  # カードの決め方を、ルールから分離した操作として表す。
    turn: int  # 0始まりのターンを持ち、同じカードでも発生位置を区別する。


@dataclass(frozen=True)  # 行動を要求した時点の引数を保持する。
class ChooseAction(Effect):  # 人間・AI・保存履歴へ行動選択を依頼する境界を作る。
    turn: int  # どのターンの選択なのかを履歴と照合する。
    card: int  # 今回引いたカードのダメージを選択側へ渡す。


@dataclass(frozen=True)  # ダメージ要求を不変の値として渡す。
class DealDamage(Effect):  # HPの更新規則を、入力の供給から分ける。
    amount: int  # 選んだカードのダメージ量をルールハンドラへ渡す。


@do  # 入力待ちを含むゲーム進行を、同じyieldでつなぐ。
def battle():  # HP9の敵へカードを出し、倒すまでターンを繰り返す。
    turn = 0  # 記録照合に使うターン番号を0から始める。
    while (yield Get("hp")) > 0:  # この対局のHPが0になるまで続ける。
        card = yield DrawCard(turn)  # 記録時は2・3・4、再生時は保存値を受け取る。
        action = yield ChooseAction(turn, card)  # 入力ハンドラから"play"を受け取る。
        if action != "play":  # この最小ルールで未定義の行動を見逃さない。
            raise ValueError("この例で選べる行動はplayだけです")  # 不正な入力を拒否する。
        yield DealDamage(card)  # ルールハンドラへHPの更新を依頼する。
        turn += 1  # 次の入力を次ターンの操作として識別する。
    return {"turns": turn, "hp": (yield Get("hp"))}  # 2・3・4なら3ターン、残りHP0。
```

このゲームの行動は`play`だけです。まず固定入力で3ターン進め、次に同じ入力を履歴から返します。人間の画面操作やAIの判断へ接続する場合も、`ChooseAction`の結果を返すハンドラを別に実装する設計になります。この記事の実行例にGUIやAIの接続は含めていません。

## ルールと入力を別のハンドラへ任せる

ダメージの計算は`rules`、カードと行動の供給は`scripted_player`が担当します。`rules`は状態の読み書きをさらに外側へ依頼します。**ハンドラ自身もeffectを使って、ほかのハンドラと合成できます。**

```python
@do  # ルールの解釈自体も、状態操作を含むProgramにする。
def rules(effect, k):  # kは、この操作の結果を待っているゲームの続き。
    if isinstance(effect, DealDamage):  # HPの更新規則だけをこのハンドラで扱う。
        if effect.amount <= 0:  # 不正なダメージは状態を変える前に拒否する。
            raise ValueError("ダメージは正の値が必要です")  # ゲームを誤った状態で続けない。
        hp = yield Get("hp")  # 外側の状態ハンドラから現在のHPを読む。
        remaining = max(0, hp - effect.amount)  # HPは9→7→4→0と変化する。
        yield Put("hp", remaining)  # この対局のHPだけを更新する。
        return (yield Resume(k, remaining))  # 残りHPを返して、ゲームの続きを再開する。
    return (yield Pass(effect, k))  # 入力や時間など、担当外の操作は外側へ渡す。


def scripted_player(cards: tuple[int, ...]):  # 固定のカード列を返すハンドラを組み立てる。
    @do  # 入力を返す前のDelayも、通常のeffectとして処理する。
    def interpret(effect, k):  # 固定入力の解釈を担当する。
        if isinstance(effect, DrawCard):  # 指定ターンのカードを求められた場合。
            return (yield Resume(k, cards[effect.turn]))  # 0・1・2ターンへ2・3・4を返す。
        if isinstance(effect, ChooseAction):  # カードを出すかどうかの選択を求められた場合。
            yield Delay(1)  # 仮想時間で1秒考えた後に行動を確定する。
            return (yield Resume(k, "play"))  # この例の唯一の行動でゲームを再開する。
        return (yield Pass(effect, k))  # ダメージや状態操作は担当ハンドラへ渡す。

    return handler(interpret)  # Programに取り付ける関数を返す。ここではまだ実行しない。
```

`scripted_player`はハンドラを組み立てる通常の関数です。内部の`interpret`が、実行時に呼ばれる`@do`の計算です。組み立てる段階と、入力を待って値を返す段階を分けています。

`Resume(k, value)`は待っていたゲームの続きを、その値で再開します。担当外の操作には`Pass(effect, k)`を使います。詳しくは[ハンドラの合成](doeff-handlers.md)で扱います。

## 入力を取得するハンドラへ、記録だけを重ねる

記録側に、カードの選び方や画面のクライアントを渡す必要はありません。入力を解釈するハンドラを外側へ置き、記録側は同じeffectをそこへ渡します。

```python
def record_inputs(journal: list[dict]):  # 入力取得とは別に、記録するハンドラを組み立てる。
    @do  # 入力の取得結果を待ってから記録できるようにする。
    def interpret(effect, k):  # カードと行動の依頼だけを記録する。
        if isinstance(effect, (DrawCard, ChooseAction)):  # 再現に必要な入力操作を選ぶ。
            value = yield effect  # 外側の入力ハンドラへ同じ依頼を渡し、結果を受け取る。
            entry = {  # 操作名・引数・結果を、再生時に照合できる1件の値へまとめる。
                "operation": type(effect).__name__,  # DrawCardかChooseActionかを残す。
                "input": asdict(effect),  # ターン番号と、選択時にはカードの値も残す。
                "result": value,  # 入力ハンドラから受け取ったカードや行動を残す。
            }
            journal.append(entry)  # ゲームを再開する前に、今回の入力を履歴へ追加する。
            return (yield Resume(k, value))  # 実際に得た入力をゲームへ返す。
        return (yield Pass(effect, k))  # 決定的なダメージ計算や状態操作は記録しない。

    return handler(interpret)  # 入力ハンドラとは独立して取り付けられる関数を返す。
```

`value = yield effect`では、いま処理している記録ハンドラの外側へ入力を依頼します。返った値を記録してから、ゲームへ返します。実際に入力を取得する責務と、その結果を保存する責務が独立しています。

## 再生では、保存済みの入力を返す

再生ハンドラは入力を取得しません。次に要求された操作名と引数を履歴と照合し、保存した結果を返します。履歴の欠落や余りもエラーにします。

```python
def replay_inputs(journal: list[dict]):  # 完了履歴から入力を返すハンドラを組み立てる。
    entries = deque(journal)  # 元の履歴を壊さず、再生する順番を持つ。

    @do  # ゲームからの入力依頼を、記録した結果へ置き換える。
    def interpret(effect, k):  # 外部入力やDelayを発生させずに再生する。
        if isinstance(effect, (DrawCard, ChooseAction)):  # 記録対象と同じ操作だけを解釈する。
            if not entries:  # 次の入力が必要なのに履歴が終わっていないかを確認する。
                raise ValueError("入力履歴が途中で終わっています")  # 不完全な履歴を拒否する。
            entry = entries.popleft()  # 次の1件だけを使い、同一入力も発生順で区別する。
            if entry["operation"] != type(effect).__name__ or entry["input"] != asdict(effect):  # 依頼と照合する。
                raise ValueError("記録と今回の操作が一致しません")  # 操作名と引数の不一致を拒否する。
            return (yield Resume(k, entry["result"]))  # 保存された2・3・4や"play"を返す。
        return (yield Pass(effect, k))  # HPの計算は記録時と同じルールへ委ねる。

    def install(program):  # 再生ハンドラと、履歴を使い切ったことの確認を組み立てる。
        @do  # 実行後の確認もProgramへつなぎ、ここでrunは呼ばない。
        def checked_replay():  # ゲーム完了後に余分な入力が残っていないか確かめる。
            result = yield handler(interpret)(program)  # 履歴から入力を返して対局を完了させる。
            if entries:  # ゲームが早く終わるなど、履歴の末尾が未使用になっていないか調べる。
                raise ValueError("未使用の入力履歴が残っています")  # 一致しない履歴を成功扱いしない。
            return result  # 履歴を使い切った場合だけ、最終状態を返す。

        return checked_replay()  # 検査を含むProgramを返し、実行は呼び出し側へ委ねる。

    return install  # 保存履歴を取り付ける関数を返す。対局ごとに新しく作る。
```

`checked_replay`も`@do`で、`yield handler(interpret)(program)`から結果を受け取ったあと、使い残した入力がないことを確認します。計算の途中で`run()`を呼んで、別の実行環境へ逃がしていません。

## ハンドラを組み合わせ、保存前後の結果を比べる

対局ごとにHP9の状態を作ります。次の`match_program`も実行関数ではなく、ハンドラ付きのProgramを組み立てる関数です。

```python
def match_program(inputs):  # 対局用ハンドラを取り付けたProgramを組み立てる。
    return state(initial={"hp": 9})(handler(rules)(inputs(battle())))  # 対局ごとに別のHPを持つ。
```

次は実行確認用のコードです。入力を記録した対局と、JSONから読み直して再構成した対局で、結果が一致することを確かめます。

```python
journal: list[dict] = []  # 完了した対局の6件の入力を記録する領域を用意する。
recording = record_inputs(journal)(battle())  # ゲームに最も近い位置で入力の依頼を捕まえる。
recording = scripted_player((2, 3, 4))(recording)  # 記録ハンドラが再依頼した入力を供給する。
recording = state(initial={"hp": 9})(handler(rules)(recording))  # 同じルールと初期HPを付ける。
original = run(scheduled(sim_time_handler()(recording)))  # 入力の待機を仮想時間で進める。
assert original == {"turns": 3, "hp": 0}  # 2+3+4のダメージで3ターン後にHP0になる。
assert len(journal) == 6  # 各ターンのDrawCardとChooseActionを各1件記録している。
with TemporaryDirectory() as directory:  # 保存と読み戻しを、検証用の領域で行う。
    path = Path(directory) / "match.json"  # 既存の対局データへ書き込まない保存先を選ぶ。
    payload = {"rules": "example-v1", "initial_hp": 9, "inputs": journal}  # 前提も一緒に残す。
    path.write_text(json.dumps(payload), encoding="utf-8")  # 完了後の履歴をJSONで保存する。
    saved = json.loads(path.read_text(encoding="utf-8"))  # 保存したデータから再構成を始める。
    assert saved["rules"] == "example-v1"  # 再生するルールの版が一致していることを確かめる。
    assert saved["initial_hp"] == 9  # 今回の初期HPが保存時と一致することを確かめる。
    restored = run(match_program(replay_inputs(saved["inputs"])))  # 入力・時間の実装なしで再生する。
    assert restored == original  # 保存前と同じターン数とHPを得る。
```

再生時は`scripted_player`も`sim_time_handler`も不要です。保存された`DrawCard`と`ChooseAction`の結果をそのまま返すので、`Delay`が発生しません。ルールとHPの更新は、記録時と同じ計算を通ります。

![入力を記録し、同じルールで対局を再構成する](/images/zenn-use-cases-v0/generated/games-flow.png)

記録時は入力ハンドラから得た6件の結果を保存します。再生時はJSONの履歴から入力を返し、同じ3ターン・HP0を再構成します。

## 完了履歴の再構成と、クラッシュからの再開

この例で実装した保存は、**対局が完了してからJSONへ書き、その入力列で最初から再実行すること**です。待機中のPythonジェネレータや、メモリ上の`state`を保存しているわけではありません。

ターン番号があるので、同じカードが2回出ても別の入力として扱います。永続実行へ進めるなら、対局ID・ルールの版・初期状態・受理した入力を、再開に必要な契約として保存します。受理した入力が失われない書き込み、再実行で重複させたくない外部処理、乱数の結果の扱いも必要です。

このコードは各入力の直後に耐障害性のある保存を行わず、強制終了からの復旧も検証していません。永続メモの具体的なコードと、複数プロセスで確認した範囲は[永続実行の記事](doeff-durable.md)へ分けています。

## 別の対局は、新しい計算と状態から始める

同じルールへ異なる入力列を渡し、二つの対局を並行に動かします。

```python
@do  # 二つの独立した対局を合成する。
def two_matches():  # 同じルールへ別々の入力列を渡す。
    first = yield Spawn(match_program(scripted_player((2, 3, 4))))  # 3ターンで終わる対局を開始する。
    second = yield Spawn(match_program(scripted_player((5, 5))))  # 2ターンで終わる対局を開始する。
    return (yield Wait(first)), (yield Wait(second))  # 両方の結果を開始した順に受け取る。
```

```python
both = run(scheduled(sim_time_handler()(two_matches())))  # 検証：二つの対局を仮想時間で進める。
assert both == ({"turns": 3, "hp": 0}, {"turns": 2, "hp": 0})  # 別々のHP9をそれぞれ削り切る。
```

`match_program`を呼ぶたびに新しい状態ハンドラを作るので、対局のHPが混ざりません。継続のコピーではなく、初期状態から別のProgramを作っています。

カードゲームやターン制ゲームでは、ルールの順序と入力待ちを一緒に読めることが効きます。描画や演出との接続を別のハンドラへ置けば、同じルールを固定入力でも保存履歴でも動かせます。

## 確認した範囲

完全な例で、イベント待機と仮想3秒の経過、6件の入力の記録、JSON保存・読み戻し、同じ最終状態の再構成を確認しています。さらに、履歴の欠落・余剰・引数不一致の拒否と、独立した2対局の並行実行も確認しています。外部サービス・GUI・実AIには接続していません。

## 関連記事・実装

- [ハンドラの合成](doeff-handlers.md)
- [時間とスケジューリング](doeff-time.md)
- [イベントの発行と待機](doeff-events.md)
- [逐次・並行と失敗時の扱い](doeff-traverse.md)
- [永続実行の入口](doeff-durable.md)
- [イベントハンドラの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-events/src/doeff_events/handlers/memory.py)

[メイン記事へ戻る](doeff-main.md)
