---
title: "書く・見つける・検査する — doeffの開発支援"
emoji: "🔎"
type: "tech"
topics: ["python", "doeff", "設計"]
published: false
---

小さな処理を組み合わせる設計では、「使える処理を見つける」「対応するハンドラを忘れない」「設計の約束を検査する」ことも大切です。doeffの開発支援パッケージは、この3つを手伝います。

たとえば、操作と対応ハンドラを宣言すると、対応漏れを検出できます。以下は、このあとに載せる完全な例の抜粋です。

```python
assert_domain_covered(document_domain)  # ReadTitleの対応ハンドラがあるので通る。
program = read_title("見出し\n本文")  # 見出しを求めるProgramを作る。
assert run(handler(title_handler)(program)) == "見出し"  # 実際の戻り値も確認する。
```

![宣言と実行の両方を検査する](/images/zenn-use-cases-v0/generated/tooling-concept.png)

操作とハンドラの対応宣言を検査し、具体的な入力への結果は別の実行テストで確認します。

## 領域を宣言し、ハンドラの指定漏れを検出する

`doeff-domain`では、領域を`Domain`という値で宣言します。ここでは文書の先頭行を求める`ReadTitle`と、その操作を扱う`title_handler`を対応させます。

`@handles`は検査用の宣言です。実行時のハンドラ設置は`handler(title_handler)(program)`が担当します。宣言だけでハンドラが自動的に設置されるわけではありません。

```python
from dataclasses import dataclass  # 依頼の引数を変更不能な値としてまとめる。

from doeff_domain import (  # 領域の宣言・対応検査・所属検査を使う。
    Domain,  # 操作と対応ハンドラを組にした宣言の型。
    DomainCoverageError,  # 対応ハンドラが足りないときの例外。
    assert_domain_covered,  # 宣言した操作が対応ハンドラで網羅されるか検査する。
    assert_no_orphan_effects,  # 指定モジュールの操作に所属先があるか検査する。
    handles,  # ハンドラが扱う操作を明示するためのデコレータ。
    register_domain,  # 所属検査で使う登録表へ領域を追加する。
)

from doeff import (  # 依頼・合成・実行を使う。
    Effect,  # 独自の依頼ReadTitleの基底型を使う。
    EffectGenerator,  # yieldする計算の最終結果を型で示す。
    Pass,  # 対象外の依頼を外側へ渡す。
    Resume,  # 求められた値で続きkを再開する。
    do,  # 生成器を合成可能なProgramにする。
    handler,  # ハンドラをProgramへ設置する。
    run,  # テストでProgramをVMに渡して結果を得る。
)


@dataclass(frozen=True)  # 依頼を受け取った後で文書が書き換わらないようにする。
class ReadTitle(Effect):  # 文書の先頭行を求める操作を表す。
    document: str  # 先頭行と本文を含む文字列をハンドラへ渡す。


@handles(ReadTitle)  # このハンドラがReadTitleを扱うことを検査器へ伝える。
@do  # ハンドラの処理もProgramとして継続操作をyieldする。
def title_handler(effect, k):  # 依頼と、その結果を待つ続きkを受け取る。
    if isinstance(effect, ReadTitle):  # 見出しの依頼だけをここで解釈する。
        return (yield Resume(k, effect.document.splitlines()[0]))  # 先頭行で呼び出し元を再開する。
    return (yield Pass(effect, k))  # ほかの操作は外側のハンドラへ渡す。


@do  # 見出し取得を別の処理からyieldできるProgramにする。
def read_title(document: str) -> EffectGenerator[str]:  # 文字列を受け取り見出しを返す。 # doeff: kleisli
    return (yield ReadTitle(document))  # ハンドラが返した先頭行をそのまま結果にする。


document_domain = Domain(  # 登録表を触らず、領域の宣言を値として作る。
    name="article-document",  # 領域を識別する名前を付ける。
    title="記事の文書操作",  # 人が読む表示名を付ける。
    effects=(ReadTitle,),  # この領域が導入する操作を1つ指定する。
    handlers=(title_handler,),  # ReadTitleを扱うハンドラを対応させる。
)


def verify() -> None:  # 通る宣言・落ちる宣言・実際の結果をオフラインで確認する。
    assert_domain_covered(document_domain)  # ReadTitleの対応宣言があるので例外なく通る。
    program = read_title("見出し\n本文")  # 入力を固定した見出し取得のProgramを作る。
    assert run(handler(title_handler)(program)) == "見出し"  # 設置したハンドラが先頭行を返す。
    incomplete = Domain(  # 同じ操作から対応ハンドラだけを省いた反例を作る。
        name="article-incomplete",  # 正常例と区別できる名前を付ける。
        title="ハンドラの指定漏れ",  # 何が欠けた例なのかを示す。
        effects=(ReadTitle,),  # ReadTitleへの対応が必要であることは変えない。
        handlers=(),  # 対応するハンドラを宣言しない。
    )
    try:  # 宣言の不備が例外になることをテストする。
        assert_domain_covered(incomplete)  # ReadTitleが未対応なのでDomainCoverageErrorになる。
    except DomainCoverageError as error:  # 想定した検査エラーだけを受け取る。
        if "ReadTitle" not in str(error):  # 指定漏れの操作名が診断に含まれない場合を調べる。
            raise AssertionError("診断に操作名がありません") from error  # 説明不足の診断も検査失敗にする。
    else:  # 例外が出なければ検査の期待に反する。
        raise AssertionError("対応するハンドラがないのに検査が通りました")  # 誤った成功を拒否する。

verify()  # 正常な宣言・対応漏れ・具体的な戻り値をまとめて確認する。
```

[完全な例](examples/domain_check.py)では、宣言を登録せず`Domain`値を直接検査します。この用途に登録表の初期化やテスト用の`isolated_registry()`は要りません。

「どの領域にも属さない操作」まで検出したい場合は、導入する領域を一度登録し、検査するモジュールを指定します。次のコードは、先ほどの定義がある同じPythonモジュールで実行します。

```python
register_domain(document_domain)  # ReadTitleの所属先を、このプロセスで1回登録する。
assert_no_orphan_effects([ReadTitle.__module__])  # 定義元モジュールの操作に所属漏れがないので通る。
```

登録はプロセス内で管理されます。同じ名前や同じエフェクトの重複登録はエラーです。実際のプロジェクトでは、宣言モジュールの読み込み時など、登録箇所を一つにします。

これらは**宣言上の対応と所属を調べる検査**です。`@handles(ReadTitle)`を付けても、ハンドラの実装が正しいかまでは証明できません。具体的な入力への結果は、上の`assert run(...)`のように確認します。

## 処理を見つける — doeff-indexer

`doeff-indexer`はPythonソースから`@do`、`Program`の注釈、専用のコメントなどを読み、定義の場所や型の情報を索引化します。処理を実行して結果を集める道具ではありません。

先ほどの`read_title`の定義行には`# doeff: kleisli`を付けました。現行の`find-kleisli`はこの明示的な印を要求します。また`--type-arg str`は、**唯一の必須引数が受け取る型**を絞り込みます。戻り値の型を探す指定ではありません。

```bash
doeff-indexer index --root publications/zenn-use-cases-v0/examples --pretty # 例の定義と型をJSONで出す。
doeff-indexer find-kleisli --root publications/zenn-use-cases-v0/examples --type-arg str # strを受ける印付き関数を探す。
```

これらはCLIをインストール済みの環境向けです。この記事ではRustのCLI実装と照合しており、バイナリによる検索結果までは確認していません。

## 依存する操作を調べる — doeff-effect-analyzer

`seda`は`doeff-effect-analyzer`のCLI名です。PythonシンボルやHyソースを静的に調べ、操作への依存を表示します。解析器は開発途中で、任意の動的なPythonコードを完全に解析する保証はありません。

```bash
PYTHONPATH=publications/zenn-use-cases-v0/examples seda analyze domain_check.read_title # 見出し取得の依存をJSONで調べる。
seda hy publications/zenn-use-cases-v0/examples/hy_composition.hy --format tree # Hyの計算と依存を木として表示する。
```

CLIの引数と対象の定義を実装に照合しました。ここでもバイナリによる解析結果は未確認です。

## コードの規則を検査する — doeff-linter

`doeff-linter`はPythonコードの品質とdoeff固有の規則を検査します。次のコマンドは教材のファイルを入力にし、設定した規則への指摘をJSONで出す使い方です。「この教材が全規則に違反しない」という結果を表してはいません。

```bash
doeff-linter publications/zenn-use-cases-v0/examples/domain_check.py --output-format json --no-log # 指摘をJSONで出し、ログファイルを作らない。
```

CLIの引数は実装と照合済みで、バイナリによるlint実行は未確認です。

## 設計判断に、実行できる検査を添える — doeff-adr

「空白だけの見出しを受理しない」という判断を、理由、規則、正常例、反例のテストとして一緒に持ちます。以下は記事用の独立した教材です。

```hy
;; 設計判断を、正常例と反例を持つ実行可能な検査へつなぐ教材。
(require doeff-adr.macros [defadr rule law deftest]) ; 判断・規則・法則・テストの構文を読み込む。
(import doeff-adr.macros [fact counterexample]) ; 問題の事実と、許さない反例を値として記述する。
(import doeff [do :as _doeff-do]) ; deftestが生成するProgramで使うデコレータを用意する。

(defn title-valid [title] ; 文字列が空白以外の文字を含むかを判定する純粋関数。
  (> (len (.strip title)) 0)) ; "遊び方"はTrue、空白だけの文字列はFalseになる。

(defadr ADR-ARTICLE-TITLE ; 記事用の設計判断を、このモジュールの値として定義する。
  :title "空の見出しを受け付けない" ; 何を守る判断なのかを表示名にする。
  :status "accepted" ; 採用済みとして、実行可能な検査があることを契約検査に求める。
  :scope ["example_document.py"] ; この教材で想定する適用先を記録する。
  :problem [(fact "空の見出しでは文書を識別できない")] ; 判断が必要な理由を記録する。
  :decision [(rule R1 "空白だけの見出しも拒否する")] ; 空文字だけでなく空白も拒否する規則を選ぶ。
  :laws [(law nonempty-title ; 守る条件を、判断から参照できる法則として置く。
    :statement "受理する見出しは空白を除いて1文字以上" ; 正常な入力の条件を記述する。
    :counterexamples [(counterexample "空白だけの文字列を受理する")])] ; 禁止する具体例を記録する。
  :enforcement [(deftest test-title-contract ; 正常例と反例を実行するテスト関数を生成する。
    (assert (title-valid "遊び方")) ; 文字のある見出しを受理することを確認する。
    (assert (not (title-valid "   "))))]) ; 空白だけの見出しを拒否することを確認する。
```

`defadr`は判断の構造を検査するテストを、`deftest`は見出しの挙動を検査するテストを生成します。後者をどのハンドラで実行するかは、利用するプロジェクト側の責務です。

[この例](examples/adr_example.hy)は既定のpytest収集名ではないため、ここでは生成された関数を明示的に呼びます。Hyをimportできる環境で、リポジトリのルートから実行します。

```bash
PYTHONPATH=publications/zenn-use-cases-v0/examples uv run python - <<'PYTHON' # 教材を読み込んで2種類の検査を行う。
import hy  # HyモジュールをPythonからimportできるようにする。
import adr_example as example  # ADRの値と、生成されたテスト関数を読み込む。
from doeff import run  # 外部I/Oを含まない教材のProgramをテストで実行する。
example.test_title_contract(lambda program, **kwargs: run(program))  # "遊び方"を受理し、空白を拒否する。
example.test_ADR_ARTICLE_TITLE_adr_contract()  # 採用済みADRに必要な記述と検査があることを確認する。
PYTHON
```

実際のプロジェクトでは`doeff-adr`のpytestプラグインを使い、`defadr_*.hy`などの対象名、または`doeff_adr_hy_files`設定で収集できます。`deftest`を含む場合は、プロジェクトで`doeff_interpreter`フィクスチャを用意します。プラグインがアプリの実行環境を自動的に選ぶことはありません。

## 静的な反例にも検査を置く

実行時の判定とは別に、`Title("")`という呼び出しを検出する小さな静的検査です。`bad`が検出され、`good`が通る両側を持ちます。変数が実行時にどんな文字列を持つかまでは、このパターンでは分かりません。

```hy
;; 記事用の静的検査であり、リポジトリの実運用規則へは登録しない。
(require doeff-adr.macros [defsemgrep]) ; badとgoodを実際に検査するテストを生成する。
(defsemgrep no-empty-title ; 記事用の検査名を付ける。
  :languages ["python"] ; Pythonの構文木としてパターンを照合する。
  :message "空の見出しでは文書を識別できません" ; 検出したときに示す理由を指定する。
  :pattern "Title(\"\")" ; Titleへ空文字リテラルを渡す呼び出しを検出する。
  :bad ["Title(\"\")"] ; 反例が少なくとも1件検出されることを検査する。
  :good ["Title(\"遊び方\")"]) ; 有効な見出しの例が検出されないことを検査する。
```

[静的検査の例](examples/static_check.hy)が生成するテストを、Semgrepがインストール済みの環境で次のように実行します。

```bash
PYTHONPATH=publications/zenn-use-cases-v0/examples uv run python - <<'PYTHON' # 教材のbad/goodを静的検査へ渡す。
import hy  # Hyファイルのimportを有効にする。
import static_check as example  # 検査定義と生成テストを読み込む。
example.test_no_empty_title_defsemgrep()  # 空文字の呼び出しだけが検出されることを確認する。
PYTHON
```

## Hyで計算・ハンドラ・テストを組み立てる

`doeff-hy`は、Pythonと同じ実行環境で使えるLisp系言語Hy向けのマクロです。`defk`で計算、`defhandler`で解釈、`do!`で処理のまとまり、`deftest`で結果の検査を表せます。

```hy
(require doeff-hy.macros [defk <- do! defhandler deftest]) ; 計算・合成・ハンドラ・テストの構文を使う。
(import doeff [do :as _doeff-do run]) ; 生成コード用のdoと、テスト用のrunを用意する。
(import doeff-core-effects [Ask]) ; あいさつの接頭辞をハンドラへ求める操作を読み込む。

(defhandler greeting-source [] ; Askを解釈するハンドラを、呼び出して設置できる形で作る。
  (Ask [key] ; 問い合わせたキーを受け取る。
    (resume (get {"prefix" "こんにちは、"} key)))) ; prefixなら接頭辞を返して続きを再開する。

(defk greet [name] ; 名前を受け取り、あいさつを返す計算にする。
  {:pre [(: name str)] :post [(: % str)]} ; 入力と最終結果が文字列であることを確認する。
  (+ (! (Ask "prefix")) name)) ; 接頭辞をyieldで受け取り、名前と連結する。

(setv greeting ; あいさつに句読点を足すProgramを変数に保持する。
  (do! ; 2段階の処理を一つのProgramへまとめる。
    {:post [(: % str)]} ; 全体の結果も文字列であることを確認する。
    (<- text (greet "読者のみなさん")) ; 子の計算から接頭辞付きの名前を受け取る。
    (+ text "！"))) ; "こんにちは、読者のみなさん！"を返す。

(deftest test-greeting ; 実行環境を外から受け取るテスト関数を生成する。
  (<- text greeting) ; あいさつのProgramを実行して文字列を受け取る。
  (assert (= text "こんにちは、読者のみなさん！"))) ; 最終的なあいさつを確認する。

(test-greeting (fn [program #** kwargs] (run ((greeting-source) program)))) ; 接頭辞ハンドラを選んでテストを実行する。
```

Hyは`@do`を使うための必須条件ではありません。マクロの展開や型・値の契約を含む詳しい説明は[Hyの記事](doeff-hy.md)、`doeff-traverse`による内包表記や集約は[コレクションの記事](doeff-traverse.md)へ進めます。

## 実行基盤と検証用のパッケージ

`doeff-vm`はPythonとの接続、`doeff-vm-core`はRustのVM本体を担います。通常は公開された`run`から利用します。先ほどの例でも、次の1行でRust VMへ処理が渡ります。

```python
assert run(handler(title_handler)(read_title("見出し\n本文"))) == "見出し"  # VMが依頼をハンドラへ渡し、結果で再開する。
```

Pythonの関数本体までRustに変換されるわけではありません。継続をどこで切り、どう再開するかは[VMの解説](doeff-vm.md)で扱います。

`doeff-test-target`は静的解析器などのテスト入力を集めたパッケージです。業務機能として組み込むものではなく、たとえば次の定義を解析対象として扱えます。

```bash
seda analyze doeff_test_target.core.alpha.alpha # テスト入力にあるAsk("alpha")への依存を解析対象にする。
```

このコマンドは解析器の呼び出し例で、実行結果は未確認です。旧`doeff-agentd`には、現行の起動APIを紹介できる実装がありません。エージェントのセッション管理は[doeff-agentsの記事](doeff-agents.md)を参照してください。

## 道具をつなげる

![宣言の検査と実行テストを組み合わせる](/images/zenn-use-cases-v0/generated/tooling-flow.png)

対応宣言は通る例と落ちる例を検査し、処理を実行するテストでは具体的な戻り値を確認します。

最初から全部を導入する必要はありません。処理を探すときは索引、規則を守るときはlintやADR、領域の操作を整理するときはドメインの検査、と必要なものを選べます。

## 実装を読む

- [Hyマクロ](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-hy/README.md)
- [索引とfind-kleisliの実装](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-indexer/src/indexer.rs)
- [解析CLI](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-effect-analyzer/src/main.rs)
- [リンターCLI](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-linter/src/main.rs)
- [ADRのpytest収集](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-adr/src/doeff_adr/pytest_plugin.py)
- [領域と対応ハンドラの検査](https://github.com/proboscis/doeff/blob/d4705914e39740aee98a9f57a4535c463d9479cc/packages/doeff-domain/src/doeff_domain/checks.py)

この草稿は上記の開発版を参照しています。領域の検査、Hyの計算、ADRの生成テスト、Semgrepのbad/goodはオフラインで実行確認し、未実行のCLIは本文で区別しています。

[メイン記事へ戻る](doeff-main.md)
