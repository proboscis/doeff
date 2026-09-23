---
title: "同じdoeffを、Hyでもっと組み立てやすく書く"
emoji: "🌿"
type: "tech"
topics: ["python", "doeff", "hy"]
published: false
---

`@do`と`yield`で処理をつなぎ、その操作をハンドラで解釈する。`doeff-hy`は、この構造をHyのマクロでも書けるようにします。HyはPython上で動くLispです。Python側のエフェクトやハンドラを共有し、同じdoeffのランタイムで実行します。

## Pythonと同じ計算を、Hyで書く

まずPythonなら、必要な値を`yield`で受け取ります。

```python
from doeff import do, run  # Programを作るdoと、結果を確認するrunを読み込む。
from doeff_core_effects import Ask, reader  # 値の問い合わせと、辞書で答えるハンドラを使う。

@do  # greetの呼び出しで、後から実行するProgramを返す。
def greet(name: str):  # 挨拶する相手を通常の引数で受け取る。
    prefix = yield Ask("prefix")  # ハンドラから「こんにちは、」を受け取る。
    return prefix + name  # 接頭辞と名前を結合した文字列を返す。

assert run(reader(env={"prefix": "こんにちは、"})(greet("太郎"))) == "こんにちは、太郎"  # ハンドラを取り付けて結果を確認する。
```

Hyの`defk`は、同じく`Program`を返す関数を定義します。`<-`で結果を受け取り、`:pre`と`:post`で入力と結果の契約を記述します。

```hy
(require doeff-hy.macros [defk <-]) ; Programを作る構文と、結果を受け取る構文を読み込む。
(import doeff [run]) ; 例の結果をその場で検証するために読み込む。
(import doeff-core-effects [Ask reader]) ; Python例と同じ問い合わせとハンドラを使う。

(defk greet [name] ; 名前から挨拶を作るProgramを返す。
  {:pre [(: name str)] :post [(: % str)]} ; 実行時に入力nameと結果%が文字列であることを検査する。
  (<- prefix (Ask "prefix")) ; ハンドラから「こんにちは、」を受け取りprefixに束縛する。
  (+ prefix name)) ; 接頭辞に名前を続けた文字列を返す。

(assert (= (run ((reader :env {"prefix" "こんにちは、"}) (greet "太郎"))) "こんにちは、太郎")) ; 同じハンドラでPython例と同じ結果を確認する。
```

契約の記述は`defk`に必須です。各引数と返り値の型を記述し、実行時には`isinstance`を使う検査へ展開されます。契約を実行するタイミングも、`Program`を実行するときです。

![PythonもHyも、同じハンドラで実行する](/images/zenn-use-cases-v0/generated/hy-concept.png)

PythonのyieldもHyの<-も、Askの結果を受け取って挨拶を作ります。記述した計算は同じランタイムとハンドラを使えます。

## 式の中で、エフェクトの結果を使う

`!`を使うと、式の途中で結果を受け取れます。`defk`が式を展開するので、`!`自体を別の関数としてインポートする必要はありません。

```hy
(require doeff-hy.macros [defk]) ; defkが本体の!も展開する。
(import doeff [run]) ; 価格と送料を合計した結果を確認する。
(import doeff-core-effects [Ask reader]) ; 値を問い合わせ、辞書から返すハンドラを用意する。

(defk total [] ; 引数なしで合計を計算するProgramを返す。
  {:pre [] :post [(: % int)]} ; 引数の検査は空とし、結果が整数であることを検査する。
  (+ (! (Ask "price")) (! (Ask "shipping")))) ; 価格1000、送料200をこの順に受け取り、1200を返す。

(assert (= (run ((reader :env {"price" 1000 "shipping" 200}) (total))) 1200)) ; ハンドラの返した2つの値が合計されると確認する。
```

現在の実装は`(! 式)`を**その位置の`yield`へ展開**します。価格を受け取ってから送料を受け取り、足し算へ進みます。自動的に並列実行する記法ではありません。

この「その位置」が大事です。条件分岐で選ばれなかった式は実行されません。

```hy
(require doeff-hy.macros [defk]) ; 条件式の中にある!も、その位置で展開する。
(import doeff [run]) ; ハンドラなしで完了できることを確認する。
(import doeff-core-effects [Ask]) ; 実行されなかったことを確認する問い合わせを用意する。

(defk optional-price [enabled] ; 値の問い合わせを条件分岐する小さなProgramを返す。
  {:pre [(: enabled bool)] :post [(: % int)]} ; 条件はbool、結果は整数と検査する。
  (if enabled (! (Ask "price")) 0)) ; FalseならAskを実行せず、その場で0を返す。

(assert (= (run (optional-price False)) 0)) ; Askハンドラを付けなくても、未選択の分岐は実行されず成功する。
```

## ハンドラ・子Program・テストをつなぐ

`defhandler`で操作の解釈を定義し、`do!`で式をひとつの`Program`にまとめられます。子Programの呼び出しも`<-`で結果を受け取ります。

```hy
(require doeff-hy.macros [defk <- do! defhandler deftest]) ; ハンドラ・計算・テストを記述する構文を読み込む。
(import doeff [run]) ; この例の最終結果を確認する。
(import doeff-core-effects [Ask]) ; 接頭辞をハンドラへ問い合わせる。

(defhandler greeting-source [] ; 呼ぶとProgramを包む関数を返す、引数なしのハンドラ工場。
  (Ask [key] ; Askのkeyを受け取り、対応する値を選ぶ。
    (resume (get {"prefix" "こんにちは、"} key)))) ; prefixには挨拶を返して処理を再開し、未登録キーはKeyErrorにする。

(defk greet [name] ; 挨拶を作る子Programを返す。
  {:pre [(: name str)] :post [(: % str)]} ; 入力と結果を文字列として検査する。
  (+ (! (Ask "prefix")) name)) ; 問い合わせの結果と名前を結合する。

(setv greeting ; 固定した宛先のProgramを、実行せず値として保存する。
  (do! ; 後から実行できる計算のまとまりを作る。
    {:post [(: % str)]} ; このまとまりの結果が文字列であることを検査する。
    (<- text (greet "読者のみなさん")) ; 子Programの挨拶を受け取って次の式へ進む。
    (+ text "！"))) ; 感嘆符付きの挨拶を返す。

(deftest test-greeting ; doeff_interpreterを受け取るpytest用テストを生成する。
  (<- text greeting) ; テスト用の実行環境で挨拶Programの結果を受け取る。
  (assert (= text "こんにちは、読者のみなさん！"))) ; 接頭辞・宛先・感嘆符をまとめて検査する。

(assert (= (run ((greeting-source) greeting)) "こんにちは、読者のみなさん！")) ; ハンドラを取り付けたProgramを実行して結果を確認する。
```

ここで`greeting-source`に書いた`[]`は、引数なしのハンドラ工場を定義します。`(greeting-source)`でハンドラを作り、それに`greeting`を渡して取り付けます。

`deftest`はハンドラの選択をテスト実行側に委ねます。[完全な例](examples/hy_composition.hy)をPythonから読み込める場所に置き、次を`test_hy_example.py`として実行すると、生成されたテストをpytestが収集します。

```python
import hy  # .hyファイルをPythonから読み込めるようにする。
import pytest  # 実行環境を渡すfixtureを定義する。
import hy_composition as example  # 完全な例のProgram・ハンドラ・生成済みテストを読み込む。
from doeff import run  # テストの境界で、ハンドラ付きProgramを実行する。

@pytest.fixture  # 同名の引数を持つテストへ、この実行関数を渡す。
def doeff_interpreter():  # deftestが要求する実行環境を用意する。
    def execute(program):  # テスト本体のProgramを受け取る、pytest側の実行境界。
        return run(example.greeting_source()(program))  # 挨拶ハンドラを付け、テスト内のassertまで実行する。
    return execute  # Programを実行する関数をfixtureの値にする。

test_greeting = example.test_greeting  # Hyが生成したtest_関数を、このPythonモジュールで収集可能にする。
```

これはテストの実行境界なので`run`を使います。計算を合成する側は、先ほどの`do!`内のように`<-`を使います。

## コレクション処理も、ハンドラを選んで実行する

`for/do`の`From`で項目を取り出し、`When`で条件を表します。以下では`[1 -1 3]`から正の値を選び、2倍してから`Reduce`で合計します。

```hy
(require doeff-hy.macros [defk <- for/do]) ; 関数・結果の結合・コレクション処理の構文を読み込む。
(import doeff [do :as _doeff-do run]) ; for/doが作る子Program用のdoと、検証用のrunを用意する。
(import doeff-traverse [Traverse :as _doeff_traverse_Traverse ; Fromが生成するTraverseの参照名を用意する。
                       Skip :as _doeff_traverse_Skip ; Whenが偽のときに生成するSkipの参照名を用意する。
                       Reduce]) ; 有効な項目を畳み込む依頼を読み込む。
(import doeff-traverse.handlers [sequential]) ; ここでは項目を順番に解釈するハンドラを選ぶ。

(defk add [total value] ; 累積値と次の値を足すProgramを返す。
  {:pre [(: total int) (: value int)] :post [(: % int)]} ; 引数2個と結果が整数であることを検査する。
  (+ total value)) ; 0と2なら2、2と6なら8を返す。

(defk eligible-total [values] ; 正の値を2倍して合計するProgramを返す。
  {:pre [(: values list)] :post [(: % int)]} ; 入力はリスト、結果は整数と検査する。
  (<- selected ; 各項目の値と履歴を保持するCollectionを受け取る。
    (for/do ; 項目ごとの処理をTraverseへ展開する。
      (<- value (From values)) ; ハンドラが入力の各値をこの後の処理へ渡す。
      (When (> value 0)) ; -1をSkipにし、後続の2倍処理には進ませない。
      (* value 2))) ; 有効な項目の値を2と6にする。
  (<- total (Reduce add 0 selected)) ; 除外した項目を飛ばし、0→2→8と畳み込む。
  total) ; 計算済みの整数8を返す。

(assert (= (run ((sequential) (eligible-total [1 -1 3]))) 8)) ; 順次ハンドラで、正の値だけの倍数合計を確認する。
(assert (= (run ((sequential) (eligible-total [-3 0]))) 0)) ; 全件除外なら初期値0が返ると確認する。
```

`From`と`When`は、この位置ではマクロが認識する構文です。それぞれを関数として呼び出したり、別にインポートしたりする必要はありません。一方、展開先の`Traverse`と`Skip`には、上の別名付きインポートが必要です。

`selected`は単なるリストではなく、項目の履歴も持つ`Collection`です。`When`が除外した項目は`skipped`として残り、`Reduce`は有効な値だけを処理します。順次実行・並行実行や失敗の扱いは、[コレクション処理の記事](doeff-traverse.md)でハンドラを比較しています。

## 処理の流れ

![式に書いた順序で、値を受け取る](/images/zenn-use-cases-v0/generated/hy-flow.png)

!はその位置のyieldへ展開されます。価格1000を受け取り、送料200を受け取ってから、合計1200を返します。

## まず操作の意味を決め、それから書き味を選ぶ

[エフェクトの境界](doeff-boundaries.md)で説明したように、操作の意味と解釈を分けることが設計の中心です。Hyを使うと、その計算を式やマクロで組み立てられます。

`!`や`defhandler`を使っても、ハンドラの順序や一度だけ再開できる継続という実行上の規則は同じです。[ハンドラの合成](doeff-handlers.md)や[Rust VMの実行](doeff-vm.md)にも、そのままつながります。

---

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考資料・検証版

- [doeff-hyのマクロ実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-hy/src/doeff_hy/macros.hy)
- [ハンドラのマクロ実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-hy/src/doeff_hy/handle.hy)
- [Traverseのハンドラ実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-traverse/doeff_traverse/handlers.py)

掲載したPython・Hyの全ブロックと完全な例を、この開発checkoutでオフライン実行しています。契約違反の検出、条件分岐で未選択のAskが実行されないこと、価格と送料の問い合わせ順序、除外した項目が集計されないこと、pytestによるdeftestの収集を確認しています。
