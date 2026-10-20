---
title: "公開APIの型の穴をテストで塞ぎ、黙って開かせない"
emoji: "🕳️"
type: "tech"
topics: ["python", "pyright", "mypy", "テスト", "doeff"]
published: false
---

型付きのPythonライブラリを出しても、使う人のpyrightでは型がUnknownになることがあります。私たちのライブラリdoeffでは、PyPIの0.6.0で、公開の名381個のうち84個の型が分かりませんでした。直した0.7.0では406個のうち5個になり、5個とも訳をテストの表に書いてあります。

この記事では、型の穴をpyrightのverifytypesで数え、穴の名と訳の表をpytestのテストにします。表に無い穴が開いても、表の穴が塞がっても、テストは赤になります。型の誤りを誤りと言えるかは、使う人と同じ入れ方の環境で、別のテストに確かめさせます。

doeffは、処理の中身と動かし方を分けて書けるPythonのライブラリです。連載が初めての方は[1本目の入門](https://zenn.dev/proboscis/articles/doeff-introduction)から、コードは[GitHub](https://github.com/proboscis/doeff)から読めます。

## まず短いコード

次のコードは、doeffを使う人が書く形です。`task_id`も`value`も、型は`int`のはずです。

```python
# app.py — doeffを使う人のコード。pyrightのstrictで型を確かめる。
from doeff import Some  # 値が在る事を表す型。
from doeff_core_effects.scheduler import Task  # 並べて走らせた処理の控え。


def task_number(task: Task[int]) -> int:  # 控えから、処理の番号を返す。
    return task.task_id  # 番号はintのはず。


value = Some(1).value  # 中身の1を取り出す。intのはず。
```

これを、doeffの2つの版で、pyrightの一番厳しい強さ(strict)にかけます。`uv run --with`は、名指した版を一時の環境に入れて命令を走らせます。

```bash
echo '{"typeCheckingMode": "strict"}' > pyrightconfig.json  # 一番厳しい強さにする。
run="uv run --no-project --python 3.12 --with pyright==1.1.414"  # 一時の環境で走らせる。
$run --with doeff==0.6.0 pyright app.py  # 4 errors(task_idとvalueがUnknown)
$run --with doeff==0.7.0 pyright app.py  # 0 errors
$run --with doeff==0.6.0 pyright --verifytypes doeff  # 381個のうち84個・78%
$run --with doeff==0.7.0 pyright --verifytypes doeff  # 406個のうち5個・98.8%
```

- 0.6.0では、2行とも型がUnknownになり、誤りが4つ出ます。使う人のコードに誤りはありません。
- 穴はライブラリの側にありました。`Task`の`__init__`の引数にも、`Some.value`の戻りにも、型の注記が無かったのです。
- `--verifytypes`は、公開の名ごとに、引数・戻り・属性の型がすべて分かるかを数えます。
- 数えた結果の割合が、type completeness scoreです。この記事では、点と呼びます。

数は、2026年10月18日に、Python 3.12.14とpyright 1.1.414で測りました。範囲はPyPIの`doeff`の2つの版で、それぞれ新しい環境に入れて測りました。

## 全体の図

![型の穴を、2つのテストで見張る](/images/zenn-use-cases-v0/generated/types-concept.png)
*上の表のテストは、型が分かるかを数えます。下の型の見本は、使う人と同じ入れ方で、誤りを誤りと言うかを見ます。*

上は、テストの一式に入れたpytestです。CIでも、変更のたびに走ります。verifytypesが返す「型の分からない公開の名」を、訳を書いた表と比べます。下は、CIの別の確かめです。使う人と同じ入れ方の環境で、型の見本のファイルをpyrightとmypyにかけます。

2つは見る物が違います。上は、型が分かるかどうかだけを数えます。注記が誤っていても、型が分かれば数は上がります。下は、誤った書き方を型の道具が誤りと言うかを見ます。

## 穴は、1つの注記の抜けから広がる

0.6.0の84個の内訳を、次の図にまとめました。一番大きいのは、doeffが頭から公開し直していた27個でした。中身は、別の部品`doeff_core_effects`の依頼と、処理を並べる道具の控え(`Task`など)です。

![0.6.0の84個の穴と、その扱い](/images/zenn-use-cases-v0/generated/types-flow.png)
*84個のうち82個は、注記を付けるか型の道具から隠して塞ぎました。0.7.0の5個は、残した2個と、後から足した3個です。*

84個のうち67個は、2026年10月17日の1つのcommitで塞ぎました。そのcommitの本文の測り(Python 3.12)では、381個のうち69個が2個になりました。点は81.9%から99.5%です。0.6.0との差の15個は、同じ日に先に入れた`Some`と`Maybe`の直しの分です。0.7.0の98.8%との差は、後で足した3個の名です。

verifytypesは厳しめに数えます。classの中の1つのメソッドに戻りの型が無いだけで、classごと「一部分からない」になります。そのclassを引数に取る関数も、同じく数えられます。

典型の3つを、小さなライブラリで再現しました。直す前は、公開の名8個のうち5個の型が分からず、点は37.5%です。

```python
# tinylib/__init__.py — 型の穴の典型3つと、その直し方(直した後の形)。
from typing import TYPE_CHECKING, Any, NoReturn  # 注記に使う型と、型の道具が読む時だけ真になる印。


class Task:  # 並べて走らせた処理の控え。
    def __init__(self, task_id: int) -> None:  # 引数に型を書く。属性task_idの型もintになる。
        self.task_id = task_id  # 処理の番号を持つ。

    def __repr__(self) -> str:  # 1つ目:-> strが無いと、Taskも、Taskを受けるwaitも分からない。
        return f"Task({self.task_id})"  # 表示用の文字列を返す。


def wait(task: Task) -> int:  # Taskを受ける関数。Taskが分かれば、この関数も分かる。
    return task.task_id  # 番号を返す。


def make_handler(name: str) -> Any:  # 値を作る関数。戻りの型が広い。
    return name  # 例では名前をそのまま返す。


writer: Any = make_handler("writer")  # 2つ目:変数にも注記を書く。無いと道具ごとに推す型が違いうる。

if not TYPE_CHECKING:  # 3つ目:実行時だけに置く。廃止した名を、型の道具から隠す。

    class _Removed:  # 廃止した名の置き物。使うと替わりの名を言って落ちる。
        def __init__(self, name: str) -> None:  # 廃止した名を受ける。
            self._name = name  # 落ちる時の文に使う。

        def __call__(self, *args: object) -> NoReturn:  # 呼ばれたら必ず落ちる。
            raise RuntimeError(f"{self._name} was removed")  # 廃止を知らせる。

    KleisliProgram = _Removed("KleisliProgram")  # 古いimportは実行時には通る。
```

直した後は、公開の名7個の型がすべて分かり、点は100%です。それぞれの直し方の訳は次の通りです。

- 1つ目は、`__repr__`の`-> str`です。doeffでは、依頼のclass9個の`__repr__`に足しました。これで`Tell`や`slog`が返す依頼の型も分かりました。
- 2つ目は、変数の注記です。注記が無いと、verifytypesは道具によって推す型が違いうると数えます。
- 3つ目は、廃止した名17個です。注記を付けて見せると、使えば必ず落ちる名を、型の検査が通してしまいます。
- そこで、型の道具からは隠しました。使う人のimportの行が、型の誤りになります。実行時の案内の文は変えていません。

`writer: Any`のように`Any`を書いても、型が分かる名として数えられます。doeffでは`Any`でなく、handlerを表す型を書きました。点だけを追うと、`Any`で数を上げられてしまう点に気をつけます。

## 穴の名と訳を、テストの表にする

塞げない穴もあります。doeffでは、処理の部品をまとめた`DoExpr`と`ProgramBase`を残しました。型の引数を書くと、`isinstance`に渡せなくなるためです。そこで、残す穴の名と訳を表にして、テストで比べます。

```python
# tests/test_public_types.py — 型の分からない公開の名を、表に書いた名だけに保つ。
import json  # pyrightの答えのJSONを読む。
import subprocess  # pyrightを子のプロセスで走らせる。
import sys  # 今のPython(ライブラリを入れた環境)で走らせる。
from pathlib import Path  # 作業のdirを受ける。

NODE = "isinstance()に渡すため、型の引数を書かない"  # 残す訳(1)。
DEMO = "READMEの最初の例を、注記なしのまま印字して走らせる"  # 残す訳(2)。
KNOWN_GAPS = {  # 型が分からないまま残す公開の名と、その訳。
    "doeff.DoExpr": NODE,  # 処理の部品の型をまとめた別名。
    "doeff.ProgramBase": NODE,  # 同じ形の古い名。
    "doeff.cli.demo.digest": DEMO,  # python -m doeff demo の例の関数。
    "doeff.cli.demo.fake_world": DEMO,  # 同じ例の、偽の世界のhandler。
    "doeff.cli.demo.live_world": DEMO,  # 同じ例の、本物の世界のhandler。
}


def unknown_names(package: str, workdir: Path) -> set[str]:  # 型の分からない公開の名を集める。
    (workdir / "pyrightconfig.json").write_text("{}")  # リポジトリの設定を読ませず、入れた物を見る。
    command = [sys.executable, "-m", "pyright", "--verifytypes", package, "--outputjson"]  # 結果をJSONで受ける。
    output = subprocess.run(command, cwd=workdir, capture_output=True, text=True).stdout  # 穴があると終了コードは1。
    symbols = json.loads(output)["typeCompleteness"]["symbols"]  # 名ごとの判定の一覧。
    return {s["name"] for s in symbols if s["isExported"] and not s["isTypeKnown"]}  # 公開の名で、型の分からない物。


def test_no_new_gap_opens(tmp_path: Path) -> None:  # 表に無い穴が開いたら赤にする。
    assert unknown_names("doeff", tmp_path) - KNOWN_GAPS.keys() == set()  # 注記を付けるか、訳を書いて表に足す。


def test_closed_gaps_leave_the_table(tmp_path: Path) -> None:  # 表の穴が塞がったら赤にする。
    assert KNOWN_GAPS.keys() - unknown_names("doeff", tmp_path) == set()  # 表から外して、守る範囲を広げる。
```

テストは2つの向きで赤になります。

- 1つ目は、表に無い穴が開いた時です。注記を付けるか、訳を書いて表に足すまで通りません。
- 2つ目は、表の穴が塞がった時です。表から外すまで通りません。外すと、その名も守りに入ります。
- `pyrightconfig.json`を空にした一時のdirで走らせます。リポジトリの設定がsourceを直に読ませると、入れた物と違う物を数えるためです。

この表は、作った翌日に働きました。2026年10月17日の16時27分(UTC)に、試しの命令`python -m doeff demo`が入りました。この命令は、READMEの最初の例を自分で印字して走らせます。例は注記なしで書く決まりなので、新しい名3個の型が分かりませんでした。

mainのテストは、表に無い3個の名を挙げて赤になりました。同じ日の22時30分に、訳を書いて表に足し、緑に戻しました。この3個が、0.7.0の5個のうちの3個です。穴は開きましたが、黙っては開きませんでした。

## 使う人と同じ入れ方で、型の道具に誤りを言わせる

verifytypesは、型が分かるかしか見ません。そこで、型の見本のファイルを25個置きました。各ファイルは、使う人の書き方を真似て、わざと誤った行を混ぜます。誤りの出るべき行には、行の末に印を書きます。

```python
# tests/type_probes/p10_scheduler.py(抜き書き)— 誤りの出る行に印を書いた、型の見本。
from collections.abc import Generator  # 処理の関数の戻りの型を書く。
from typing import Any, assert_type  # 型が違えば、その行が型の誤りになる。

from doeff import do, run  # 処理を書いて、走らせる。
from doeff_core_effects.scheduler import Spawn, Task, Wait, scheduled  # 処理を並べて走らせる依頼。


@do  # 生成器の関数を、後で走らせる処理にする。
def worker(n: int) -> Generator[Any, Any, int]:  # intを返す処理。
    return n  # 受けた数をそのまま返す。
    yield  # 生成器の関数にするための行(走らない)。


@do  # 同じく、処理にする。
def main() -> Generator[Any, Any, int]:  # 子の処理を起こして、答えを待つ。
    task = yield from Spawn(worker(1))  # 子の処理を起こす。
    assert_type(task, Task[int])  # 控えの型はTask[int]。
    one = yield from Wait(task)  # 子の答えを待つ。
    assert_type(one, int)  # 答えの型はint。
    text: str = yield from Wait(task)  # expect-error (intをstrの変数に入れる)
    return one  # 子の答えを返す。


assert_type(run(scheduled(main())), int)  # 走らせた答えの型もint。
```

- `# expect-error`の行は、pyrightとmypyの両方が、厳しい設定で誤りを出すべき行です。
- 印の無い行は、両方とも誤りを出さないはずの行です。誤りが出たら、その行と文を挙げて落ちます。
- まだ直していない穴には`# gap-6 want-clean`のように、穴の番号と望む結末を書きます。
- 穴が塞がると、道具は「印を替えて守りに入れる」と言って落ちます。テストの表と同じ、2つの向きです。

この確かめは、開発の環境では走らせません。新しい環境に、doeffの核の4個だけを入れて走らせます。核とは、`pip install doeff`で入る4個です。開発の環境はsourceを直に読むので、使う人の入れ方で戻った穴に気づけないためです。

CIでは、Python 3.11と3.14で走ります。2026年10月18日に、手元のPython 3.12で走らせた結果です。

```text
$ uv run --no-project python tools/verify_type_probes.py --python 3.12
型の道具: pyright 1.1.411・mypy 2.4.0(uv.lock の版)
== Python 3.12: 新しい venv に doeff を extra 無しで入れる
    ok  入った物がちょうど核の 4 個で、doeff の 3 個はこの checkout から組んだ wheel
    ok  probe の印が読める
    ok  型の probe(pyright strict・mypy --strict) — file 25 個・印の行 32・誤り 73
        開いている抜け(印の通り): 抜け 6 が 2 行
    ok  doeff の README の例(pyright standard・mypy 既定) — file 7 個・印の行 0・誤り 0
    ok  doeff-vm の README の例(pyright standard・mypy 既定) — file 1 個・印の行 0・誤り 0
全部通った
```

印の行は32、型の道具が出した誤りは73でした。1つの印の行に、2つの道具が誤りを出すので、数は印より多くなります。READMEの例も、写した人の道具が赤を出さない事を確かめます。

## できない事と気をつける事

- verifytypesの点は、注記が正しいかを見ません。`Any`を書いても点は上がります。
- verifytypesは、道具の版で数が変わることがあります。CIでは、型の道具の版をlockのファイルで固定しています。
- `doeff_core_effects`の点は92.3%です。核に無いaiohttpなどを使う部品の穴が、まだ残っています。
- 型の見本の確かめは、Linuxでだけ走らせています。macOSとWindowsで型の道具を走らせたことはありません。

1つ失敗もありました。2026年10月17日の18時42分(UTC)に、別の変更が、入れ方の道具の引数を増やしました。型の見本の道具は古い引数で呼んでいたので、見本を1つも見ずに落ちました。

その後の約13時間、mainへの17回のpushで、このCIは毎回赤でした。直したのは翌日、この記事のコードを走らせて気づいた時です。呼び方の食い違いを捕まえるテストも足しました。守りのテストも、赤を誰かが読まなければ守りになりません。

## まとめ

- 型付きのライブラリの穴は、使う人のstrictで初めて見えます。`pyright --verifytypes`で、公開の名ごとに数えられます。
- 1つの注記の抜けは、それを受け取る関数や依頼に広がります。`__repr__`と変数の注記を見落とさないようにします。
- 廃止した名の置き物は、型の道具から隠します。使う人のimportの行で、早く誤りにできます。
- 残す穴は、名と訳の表にしてテストで比べます。穴が開いても塞がっても、テストを赤にします。
- 型が正しいかは、型の見本と印で確かめます。使う人と同じ入れ方の環境で走らせます。

doeffでは、この2つのテストで、0.6.0の84個を0.7.0の5個にしました。5個とも、テストの表に訳があります。

## 参考

- [連載の1本目「処理を書き換えずに、テストと本番で動かし方を替えるdoeffとは」](https://zenn.dev/proboscis/articles/doeff-introduction)
- ADRを書いて終わりにするのをやめる(公開予定):決めた約束をpytestで守る、別の例です。
- ライブラリのcommitの68%を、Claude Codeと書いた1年(公開予定):型の見本を含む、4つの網の全体です。
- [pyrightの文書「Verifying Type Completeness」](https://github.com/microsoft/pyright/blob/main/docs/typed-libraries.md#verifying-type-completeness)
- [PEP 561 — 型の情報を持つパッケージ(py.typed)](https://peps.python.org/pep-0561/)
- [表のテストの全体 tests/test_public_name_types.py](https://github.com/proboscis/doeff/blob/main/tests/test_public_name_types.py)
- [型の見本25個 tests/type_probes/](https://github.com/proboscis/doeff/tree/main/tests/type_probes)
- [型の見本の道具 tools/verify_type_probes.py](https://github.com/proboscis/doeff/blob/main/tools/verify_type_probes.py)と[CIの設定](https://github.com/proboscis/doeff/blob/main/.github/workflows/type-probes.yml)
- 前の回:バックテストと本番で処理を2つ書くのをやめる(公開予定)
- 次の回:async/awaitがあるのに、なぜyieldで書くのか(公開予定)
