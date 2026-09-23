---
title: "エフェクトは何を切り出すのか — 副作用からドメインAPIまで"
emoji: "🧩"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

「エフェクトは副作用を扱うもの」と理解すると、HTTPや状態の更新が思い浮かびます。それも有力な設計です。でも、エフェクトとして切り出す境界は、もっと自由に選べます。

ゲームなら「ダメージを与える」。文書処理なら「指定した版のページを読む」。**処理が要求する操作を、どの言葉で表現したいか**が出発点です。

```python
@do  # ゲームの手順を、実行前のProgramとして組み立てる。
def attack():  # ダメージ後のHPを返す、ひとつのゲーム操作。
    remaining = yield DealDamage(3)  # 3点のダメージを依頼し、更新後のHPを受け取る。
    return remaining  # 例えば開始HPが10なら、ハンドラから受け取った7を返す。
```

後で定義する`DealDamage`を使った抜粋です。HPの保存先や表示の更新方法を、`attack`では決めていません。ゲームのルールが要求する操作を名前にしています。

![切り出した操作を、別のエフェクトで実現する](/images/zenn-use-cases-v0/generated/boundaries-concept.png)

ドメインの操作を、HTTP・状態・Memoなどの操作へ翻訳できます。何を切り出すかと、どう実現するかを別々に設計します。

## 副作用で切るか、ドメインで切るか

| 境界の選び方 | この記事のエフェクト | 外側へ置きたい判断 |
| --- | --- | --- |
| 副作用や計算上の関心事 | `Get`、`Put`、`HttpRequest` | 状態をどこに持つか、どう通信するか |
| ドメインの操作 | `DealDamage`、`ReadPage` | ダメージや、版付きのページ取得をどう実現するか |

前者なら、純粋な計算と副作用を分ける関数型プログラミングのスタイルに使えます。後者なら、ドメインAPIを定義し、その実現方法を分離する設計に使えます。

両方を重ねても構いません。ドメインのハンドラが、より細かいエフェクトへ翻訳し、さらに外側のハンドラが状態の更新や通信を担当する形です。

## ドメインの操作を、状態の操作へ翻訳する

次は、`DealDamage`を`Get`と`Put`で実現する例です。

```python
from doeff import Effect, Pass, Resume, do, handler, run  # 依頼・委譲・再開と検証用runを使う。
from doeff_core_effects import Get, Put, state  # 状態を読む依頼、書く依頼、その担当を使う。

class DealDamage(Effect):  # 「ダメージ後のHPを返す」というドメインの操作を定める。
    def __init__(self, amount: int):  # 依頼するダメージ量を受け取る。
        super().__init__()  # doeffが扱うエフェクトとして初期化する。
        if amount <= 0:  # この操作では、0以下のダメージを許可しない。
            raise ValueError("ダメージは正の整数で指定してください")  # 不正な依頼を早く検出する。
        self.amount = amount  # ハンドラが読む、依頼のデータとして保持する。

@do  # ゲームの手順をProgramとして合成できるようにする。
def attack():  # 状態の保存先を知らず、更新後のHPを返す。
    remaining = yield DealDamage(3)  # 操作の解釈をハンドラへ任せ、更新後のHPを受け取る。
    return remaining  # ハンドラの戻したHPを、呼び出し元へ返す。

@handler  # ダメージの解釈を、Programに取り付けられる形にする。
@do  # 状態を読む・書く部分も、外側へyieldする。
def damage_handler(effect, k):  # 依頼と、結果を待つ処理の続きを受け取る。
    if not isinstance(effect, DealDamage):  # ダメージ以外の依頼には介入しない。
        return (yield Pass(effect, k))  # 担当する別のハンドラへ通す。
    hp = yield Get("hp")  # 状態ハンドラから現在のHPを受け取る。
    remaining = max(0, hp - effect.amount)  # 純粋な計算で、0を下回らない残りHPを求める。
    yield Put("hp", remaining)  # 状態ハンドラに更新を依頼し、完了を待つ。
    return (yield Resume(k, remaining))  # 更新後のHPでattackのyieldを再開する。

program = state(initial={"hp": 10})(damage_handler(attack()))  # 翻訳の外側へ状態の担当を付ける。
assert run(program) == 7  # 3点のダメージ後に、attackが7を返すことを検証する。
assert run(state(initial={"hp": 2})(damage_handler(attack()))) == 0  # HPは負の値にならない。
```

`attack`はゲームの操作だけを使い、`damage_handler`が状態の操作へ翻訳します。`k`は、結果を受け取ったあとの続きです。`Resume`で残りHPを渡すと、`attack`の`remaining`に値が入り、処理が進みます。

この例の状態はメモリ内にあります。ハンドラを差し替えるときにも、「更新を終えてからダメージ後のHPを返す」という操作の意味を維持します。どのハンドラを内側に置くか、翻訳された操作がどこへ届くかは、[ハンドラの合成](doeff-handlers.md)で詳しく扱います。

## 文書の取得・保存・並行処理を組み合わせる

文書の索引を作る例へ進みます。最初に「はじめに・遊び方」、次に「遊び方・おわりに」を読みます。同じ版の「遊び方」は一度だけ取得し、各索引のページは並行に読みます。

境界は`ReadPage(page_id, revision)`です。この操作の結果を「指定した版の本文文字列」と定めます。[完全な実行例](examples/document_pipeline.py)では、本文の取得方法、Memoの保存先、時計を別々に選んで検証しています。以下のコードはその抜粋です。

```python
from dataclasses import dataclass  # ページの識別情報を持つ、変更しない依頼データを作る。
from doeff_core_effects.scheduler import Gather, Spawn  # 開始したTaskを並行に進め、結果を集める。
from doeff_time import GetTime  # 選んだ時計から、処理の開始・終了時刻を受け取る。

@dataclass(frozen=True)  # ページIDと版を、依頼後に変更できないようにする。
class ReadPage(Effect):  # HTTPなどの取得手段から独立した、文書の操作。
    page_id: str  # 例ではintro・rules・endingのいずれかを指定する。
    revision: str  # 同じページでも版が違えば、別の本文として扱う。

@do  # 本文の取得と見出し抽出を、小さなProgramへまとめる。
def read_title(page_id: str, revision: str):  # 指定した版のページから見出しを返す。
    page = yield ReadPage(page_id, revision)  # 取り付けたハンドラから本文文字列を受け取る。
    return page.splitlines()[0]  # 本文の先頭行を、見出しとして返す。

@do  # 子Programを使い、複数ページから索引を作る。
def make_index(page_ids: tuple[str, ...]):  # 入力されたページ順の見出しを返す。
    tasks = ()  # 開始済みTaskを、入力されたページ順に保持する。
    for page_id in page_ids:  # 各読み取りを開始し、まだ結果を待たず次へ進む。
        task = yield Spawn(read_title(page_id, "edition-1"))  # 読み取りを開始してTaskを受け取る。
        tasks = (*tasks, task)  # 新しいTaskを末尾へ加え、索引の順序を保つ。
    titles = yield Gather(*tasks)  # 開始済みTaskを待ち、入力順の見出しリストを受け取る。
    return tuple(titles)  # 例えば「はじめに・遊び方」の組を返す。

@do  # 2つの索引作成と計時を、同じyieldの規約でつなぐ。
def workflow():  # 2つの索引と、選んだ時計での経過秒数を返す。
    start = yield GetTime()  # 取得前の時刻を受け取る。
    first = yield make_index(("intro", "rules"))  # 2ページを並行に読み、最初の索引を得る。
    second = yield make_index(("rules", "ending"))  # 同じ版のrulesと、新しいendingを読む。
    end = yield GetTime()  # 2つの索引ができた時刻を受け取る。
    return first, second, (end - start).total_seconds()  # 索引の値と経過秒数を返す。
```

`Spawn`には子Programを渡し、結果として`Task`を受け取ります。`Gather`へ渡すのは、その開始済みのTaskです。現在のAPIでは、Programのリストを直接`Gather`へ渡す呼び出し方にはなりません。

### ドメインの操作を、HTTPの依頼へ翻訳する

```python
from doeff_core_effects import HttpRequest, HttpResponse  # HTTPの公開の依頼型と応答型を使う。

@handler  # 文書の依頼をHTTPへ翻訳する担当を、Programに取り付けられる形にする。
@do  # HTTPへの依頼も、処理本体と同じyieldで行う。
def pages_over_http(effect, k):  # ReadPageだけを、本文取得の手順へ翻訳する。
    if not isinstance(effect, ReadPage):  # 別種の依頼には介入しない。
        return (yield Pass(effect, k))  # 元の依頼と続きを、別の担当へ渡す。
    url = f"https://example.invalid/{effect.revision}/{effect.page_id}"  # 版とIDから取得先を決める。
    response = yield HttpRequest("GET", url)  # 外側のHTTP担当からHttpResponseを受け取る。
    response.raise_for_status()  # HTTPエラーなら、本文を成功値として返さない。
    return (yield Resume(k, response.text))  # 本文文字列でReadPageを待つ処理を再開する。
```

翻訳する担当は、HTTPクライアントを作りません。`HttpRequest`を依頼するところで、もう一度境界を切ります。HTTP担当を本番・テストへ交換しても、`read_title`も`pages_over_http`も同じままです。

### テストでは、HTTPのハンドラを交換する

このテスト用ハンドラは、固定した本文を返します。`Delay`で取得時間を表すため、時計の差し替えも確認できます。

```python
from doeff_time import Delay  # 取得待機を、時計に解釈してもらう依頼として表す。

def fixed_http(pages, calls, seconds):  # 固定本文・到達記録・取得時間を持つ、テスト用担当を作る。
    @handler  # 本番HTTPと同じ位置へ取り付けられる形にする。
    @do  # 待機と結果返却を、DelayとResumeで行う。
    def interpret(effect, k):  # HttpRequestを直接受け取るので、クライアントは不要。
        if not isinstance(effect, HttpRequest):  # HTTP以外の依頼には干渉しない。
            return (yield Pass(effect, k))  # Memoや時間の依頼を外側へ通す。
        if effect.method != "GET":  # このテストはGETの固定データだけを提供する。
            raise ValueError(effect.method)  # 未対応の操作を成功させない。
        text = pages[effect.url]  # 未定義URLはKeyErrorとし、ネットワークへ接続しない。
        calls.append(effect.url)  # HTTP担当まで届いた依頼だけを数える。
        yield Delay(seconds)  # 選んだ時計で、指定した取得時間を待つ。
        response = HttpResponse(200, {}, text.encode(), text, effect.url, seconds)  # 成功応答を作る。
        return (yield Resume(k, response))  # HTTP応答を戻し、文書取得の翻訳処理を再開する。
    return interpret  # 呼び出し元は、この担当をProgramへ取り付けられる。

@handler  # 保存済みデータだけで動くことを確かめる、もうひとつのHTTP担当。
@do  # 未保存のHTTP依頼を、Programの失敗として伝える。
def reject_http(effect, k):  # HTTPが呼ばれないはずの再実行に取り付ける。
    if not isinstance(effect, HttpRequest):  # HTTP以外の処理は引き続き使える。
        return (yield Pass(effect, k))  # 保存値の読み取りや時計の処理を妨げない。
    raise LookupError(effect.url)  # 未保存のURLを、実際の通信へ流さず拒否する。
```

### メモ化する単位と保存先を、取得から分ける

次の構成では、`ReadPage`の結果をメモ化します。`ReadPage`を受けた`make_memo_rewriter`が、保存済みかを調べ、未保存なら外側へ取得を委ね、返ってきた本文を保存します。

```python
from doeff_core_effects.handlers import await_handler, slog_discard_handler  # 保存の待機とログを処理。
from doeff_core_effects.memo_handlers import make_memo_rewriter  # 依頼へ再利用の方針を追加する。
from doeff_core_effects.scheduler import scheduled  # 並行処理と待機を進める。

def assemble(program, http, cache, clock):  # 構成関数として、各担当でProgramを包む。
    wrapped = make_memo_rewriter(  # 何を再利用するかを、HTTPと保存先から独立して決める。
        ReadPage,  # 版付きページの本文を、メモ化する単位にする。
        key_fn=lambda e: f"page:{e.revision}:{e.page_id}",  # この例のIDと版で保存キーを作る。
    )(program)  # 文書処理が出すReadPageへ、保存済み判定を挟む。
    wrapped = pages_over_http(wrapped)  # 保存ミスのReadPageをHttpRequestへ翻訳する。
    wrapped = http(wrapped)  # HTTPの取得方法を、本番・固定応答・拒否から選ぶ。
    wrapped = cache(wrapped)  # Memo系の依頼を、選んだ保存先に割り当てる。
    wrapped = clock(wrapped)  # DelayとGetTimeを、選んだ時計に割り当てる。
    wrapped = slog_discard_handler(wrapped)  # 保存や仮想時計の診断ログを受け取る。
    return scheduled(await_handler()(wrapped))  # 保存や実時計が出すAwaitと、並行処理を進める。
```

メモ化の単位をHTTP応答にしたければ、`HttpRequest`の境界で行う設計もできます。[HTTPとMemoの合成](doeff-replay.md)はその例です。ここでは**文書という意味で結果を再利用したい**ので、版とページIDをキーにします。HTTPのURLを変えても同じ本文と見なすのか、版が変わったら必ず取り直すのかは、ドメイン側の契約として決めます。

保存値がある間は取得担当へ到達しません。したがって、内容が変わったのに版を更新しないと、以前の本文を再利用します。「同じ版は同じ本文」という前提も、メモ化する操作の契約の一部です。

### 並行取得と、重複ページの再利用を確かめる

```python
from datetime import datetime, timezone  # 仮想時計をUTCの一定時刻から始める。
from doeff_core_effects.memo_handlers import in_memory_memo_handler  # Memoをメモリへ保存する。
from doeff_time import sim_time_handler  # 実時間を待たず、取得の予定へ時計を進める。

base = "https://example.invalid/edition-1"  # 固定応答だけで扱う、例示用URLを使う。
pages = {  # 各ページのHTTP応答本文を、テスト用データとして定める。
    f"{base}/intro": "はじめに\n本文",  # 最初の索引だけで使うページ。
    f"{base}/rules": "遊び方\n本文",  # 両方の索引で使う、再利用の対象ページ。
    f"{base}/ending": "おわりに\n本文",  # 2番目の索引で初めて使うページ。
}
calls = []  # HTTP担当まで届いたURLを記録する。
start = datetime(2026, 1, 1, tzinfo=timezone.utc)  # 仮想時計の開始点を固定する。
program = assemble(  # 文書処理へ、取得・保存・時計をそれぞれ取り付ける。
    workflow(), fixed_http(pages, calls, 2),  # 1回の取得に仮想時間で2秒かかる。
    in_memory_memo_handler(), sim_time_handler(start_time=start),  # メモリへ保存し、仮想時計を使う。
)
first, second, elapsed = run(program)  # 検証として実行し、2つの索引と経過秒数を得る。
assert first == ("はじめに", "遊び方")  # 最初の索引が、指定したページ順になる。
assert second == ("遊び方", "おわりに")  # 次の索引でも、同じrulesの見出しを使える。
assert sorted(calls) == sorted(pages)  # 延べ4ページを要求しても、実取得は3ページだけ。
assert elapsed == 4.0  # 並行な初回取得で2秒、新規endingで2秒だけ時計が進む。
```

この例で再利用するのは、先の索引ですでに取得を終えた`rules`です。同一キーへの同時依頼を一件へまとめる保証までは、この例は扱いません。

### 保存先と時計を、それぞれ取り替える

SQLiteへ保存すれば、新しいProgramと新しいハンドラを作っても、同じ保存先から本文を読み直せます。取得担当を`reject_http`へ交換した状態で成功することが、HTTPへ依頼しなかった証拠になります。

```python
from pathlib import Path  # SQLiteの保存先を組み立てる。
from tempfile import TemporaryDirectory  # 検証で作る保存領域だけを後で片付ける。
from doeff_core_effects.memo_handlers import sqlite_memo_handler  # MemoをSQLiteへ保存する。

with TemporaryDirectory() as directory:  # 検証専用の保存領域を作る。
    database = Path(directory) / "pages.sqlite"  # 2回のrunで同じ保存先を使う。
    recorded = assemble(  # 初回は固定HTTP担当から取得して、本文を保存する。
        workflow(), fixed_http(pages, [], 2),  # 文書処理と取得方法は変更しない。
        sqlite_memo_handler(database), sim_time_handler(start_time=start),  # 保存先だけ変える。
    )
    assert run(recorded)[:2] == (first, second)  # SQLite保存でも、同じ索引ができる。
    replay = assemble(  # 新しいProgramとハンドラを作り、保存結果を再利用する。
        workflow(), reject_http,  # HTTPを呼ぶと必ず失敗する担当へ交換する。
        sqlite_memo_handler(database), sim_time_handler(start_time=start),  # 同じ保存先を開く。
    )
    assert run(replay) == (first, second, 0.0)  # 全ページが保存ヒットし、HTTP待機なしで成功する。
```

完全な実行例では、`rules`の版を`edition-2`へ変えると保存ミスになり、`reject_http`の`LookupError`が届くことも確認しています。

時計も独立に交換できます。次は同じ文書処理を、asyncioを使った実時間の時計で動かします。テストの待機は1回10ミリ秒です。

```python
from doeff_time import async_time_handler  # Delayをasyncioの実時間待機へ変換する。

real_calls = []  # 時計を変えても、取得回数が変わらないことを観測する。
real = assemble(  # 取得と保存はそのままに、時計の担当を交換する。
    workflow(), fixed_http(pages, real_calls, 0.01),  # 固定HTTP担当で、各取得を短く待つ。
    in_memory_memo_handler(), async_time_handler(),  # 新しいメモリ領域と実時間の時計を使う。
)
real_first, real_second, real_elapsed = run(real)  # 実際の短い待機を含めて実行する。
assert (real_first, real_second) == (first, second)  # 時計を変えても、文書処理の結果は同じ。
assert real_elapsed >= 0.02  # 2段階の取得に合計20ミリ秒以上を使う。厳密な所要時間は固定しない。
assert sorted(real_calls) == sorted(pages)  # 取得回数も変わらず、3ページ分だけになる。
```

本番のHTTPへ切り替える構成は次の形です。この関数は構成例で、記事の検証では呼び出していません。

```python
from doeff_core_effects.http_handlers import http_production_handler  # HTTP通信を担当する実装を使う。

def production_program(database):  # 業務の手順を変えず、外部接続の構成だけを組み立てる。
    return assemble(  # Programを包む構成関数なので、ここで処理をrunしない。
        workflow(), http_production_handler(),  # クライアント管理を本番HTTP担当へ任せる。
        sqlite_memo_handler(database), async_time_handler(),  # 保存先と時計は独立に選ぶ。
    )
```

この例のURLは`example.invalid`です。実サービスへ接続する際は、`pages_over_http`にそのドメインのURL規則を実装します。

`Await`は、HTTPや保存、実時計のハンドラが必要とする非同期の操作との接点です。文書の読み取りや索引作りの合成は`@do`のままです。補助関数を大きな`async def`へまとめて、全体を`Await`に渡す必要はありません。

## 結果だけでなく、続きをどう動かすか

ハンドラは、結果を即座に返すだけに限りません。前の例の`Wait`や`Gather`は、結果がまだない間は続きを保留し、準備ができたら再開します。タスクを開始する際には、優先度も指定できます。

```python
from doeff_core_effects.scheduler import PRIORITY_HIGH, Wait  # 優先度の指定と完了待ちを使う。

@do  # 優先度付きの開始と、結果の受け取りを同じProgramにする。
def urgent_title():  # 開始方法を変えても、ドメイン操作の結果は見出し文字列。
    task = yield Spawn(read_title("rules", "edition-1"), priority=PRIORITY_HIGH)  # 高優先度で開始。
    title = yield Wait(task)  # 取得完了まで保留し、結果の見出しで再開する。
    return title  # この例の本文なら「遊び方」を返す。
```

これは協調的な実行の優先度です。実行中のCPU処理を強制的に奪うOSのプリエンプションや、厳密な実時間の締め切り保証ではありません。[イベント待ち](doeff-events.md)では、入力の到着まで処理を保留する例を扱います。

`asyncio`も非同期I/O、タスク、同期、イベントループを扱います。時間だけを扱うわけではありません。[asyncioの公式ドキュメント](https://docs.python.org/3/library/asyncio.html)

doeffで注目するのは、**任意に定義した操作を、依頼のデータと継続としてハンドラへ渡せる**点です。時計、保存、ドメインAPIを同じ計算へ合成できます。[coroutineとの違い](doeff-coroutines.md)と、[Rust VMが続きの再開を管理する仕組み](doeff-vm.md)は、別の記事で掘り下げています。

なお、doeffの継続は一度だけ再開する方式です。同じ`k`を何度も再開して探索の分岐を増やす方式ではありません。独立した計算を繰り返す例は[Traverse](doeff-traverse.md)を参照してください。

## 境界の粒度も設計する

細かく切れば、個々の操作を観測・差し替えやすくなります。その分、操作の定義やハンドラ間の契約が増えます。大きくまとめれば、呼び出し側は簡潔になりますが、内側で直接行う操作には介入できません。

`remaining = max(0, hp - effect.amount)`の計算そのものは、通常のPythonです。`Get`と`Put`を境界にしたからといって、足し算までエフェクトにする必要はありません。同様に、`ReadPage`の内側で直接HTTPクライアントを呼べば、その通信にHTTPハンドラで介入できません。ここで`HttpRequest`へ翻訳したことにも意味があります。

判断の目安は「ここで実行方法を変えたいか」「ここを保存・再利用したいか」「この意味を呼び出し側に見せたいか」です。doeffはPython全体の副作用を自動検出したり、純粋性を強制したりする言語ではありません。

**切り出したい意味を操作として定義し、その意味を保ちながら実現方法を選ぶ。** エフェクトは、実行の道具であると同時に、設計の道具にもなります。この構造をより短く書く選択肢が、[Hyのマクロ](doeff-hy.md)です。

## 処理の流れ

![文書取得・Memo保存・HTTP・時計を別々に組む](/images/zenn-use-cases-v0/generated/boundaries-flow.png)

ReadPageの保存ヒットなら本文を戻し、保存ミスならHTTPへ翻訳して取得後に保存します。時計は取得待機を扱い、索引のProgramは共通です。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

- [完全な実行例](examples/document_pipeline.py): 固定HTTP・メモリ/SQLite保存・仮想/実時間の時計を、外部通信なしで検証しています。
- [エフェクトのメモ化と保存先の分離](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/memo_handlers.py)
- [スケジューラと優先度の実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-core-effects/doeff_core_effects/scheduler.py)

上のコードは、このリポジトリの開発checkoutで確認しています。実HTTPへの接続は実行していません。
