"""架空の対局で、入力の記録・再構成・イベント待機をオフラインで確かめる。"""

import json  # 入力履歴をJSONへ保存し、同じデータを読み戻す。
from collections import deque  # 再生済みの入力を取り除き、欠落と余剰を検出する。
from dataclasses import asdict, dataclass  # 入力を不変の値にし、履歴には辞書として残す。
from datetime import datetime, timezone  # 仮想時計の開始点をUTCで固定する。
from pathlib import Path  # 検証用JSONの保存先を組み立てる。
from tempfile import TemporaryDirectory  # 検証後に保存ファイルを自動で片付ける。

from doeff_core_effects import Get, Put, state  # 対局ごとのHPを読み書きする。
from doeff_core_effects.scheduler import Spawn, Wait, scheduled  # 入力と対局を並行に進める。
from doeff_events import Publish, WaitForEvent, event_handler  # カード入力の発行と待機を扱う。
from doeff_time import Delay, GetTime, sim_time_handler  # 実時間を待たず、経過秒数を確認する。

from doeff import Effect, Pass, Resume, do, handler, run  # 操作・解釈・再開と検証時の実行を使う。


@dataclass(frozen=True)  # 一度出したカード入力を途中で書き換えない。
class CardPlayed:  # 画面やスクリプトから届く入力の値を表す。
    damage: int  # この入力によるダメージを持つ。検証では3・4・5を使う。


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


def match_program(inputs):  # 対局用ハンドラを取り付けたProgramを組み立てる。
    return state(initial={"hp": 9})(handler(rules)(inputs(battle())))  # 対局ごとに別のHPを持つ。


@do  # 二つの独立した対局を合成する。
def two_matches():  # 同じルールへ別々の入力列を渡す。
    first = yield Spawn(match_program(scripted_player((2, 3, 4))))  # 3ターンで終わる対局を開始する。
    second = yield Spawn(match_program(scripted_player((5, 5))))  # 2ターンで終わる対局を開始する。
    return (yield Wait(first)), (yield Wait(second))  # 両方の結果を開始した順に受け取る。


def verify() -> None:  # 実行を許す検証境界。外部サービスへ接続しない。
    events = state(initial={"enemy_hp": 10})(event_match())  # イベント例専用のHPを用意する。
    events = event_handler()(events)  # 入力の発行と待機をメモリ上でつなぐ。
    assert run(scheduled(sim_time_handler()(events))) == ("勝利", 0, 3.0)  # 仮想3秒で勝利する。
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
    mismatched = [dict(entry) for entry in journal]  # 正常な履歴を壊さず、不一致の検証用に複製する。
    mismatched[0] = {**mismatched[0], "input": {"turn": 99}}  # 最初のターンだけ99へ変える。
    for invalid in (journal[:-1], [*journal, journal[0]], mismatched):  # 欠落・余剰・不一致を試す。
        try:  # これは検証用。再生が明示的に拒否することを確認する。
            run(match_program(replay_inputs(invalid)))  # 不完全または異なる履歴で対局を再構成する。
        except ValueError:  # 履歴の不整合を検出した、期待どおりの失敗を確認する。
            continue  # 次の不整合ケースも確認する。
        raise AssertionError("不正な履歴を再生できてしまいました")  # 拒否されなければ検証を失敗させる。
    both = run(  # 仮想時間とスケジューラで二つの対局を最後まで進める。
        scheduled(  # 並行する対局とイベントの待機を進める。
            sim_time_handler(start_time=datetime(2026, 1, 1, tzinfo=timezone.utc))(two_matches())  # 2対局に同じ仮想時計を渡す。
        )  # どちらの対局にも同じ仮想時計を提供する。
    )
    assert both == ({"turns": 3, "hp": 0}, {"turns": 2, "hp": 0})  # 対局のHPが混ざっていない。


if __name__ == "__main__":  # import時には検証を実行せず、直接起動したときだけ確認する。
    verify()  # イベント・記録再生・不正履歴の拒否・独立した2対局を検証する。
    print("ゲーム進行・記録再生・履歴の整合性・独立した2対局: OK")  # 検証成功を端末へ表示する。
