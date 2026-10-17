---
title: "設計の記録をテストとして走らせる — doeff-adrでADRに規則と反例を持たせる"
emoji: "📐"
type: "tech"
topics: ["python", "doeff", "テスト", "設計", "semgrep"]
published: false
---

設計で決めた約束を、ADRという文書に残すことがあります。ところが数か月たつと、コードが約束から外れていることがよくあります。

たとえば「業務のコードは、今の時刻を直に読まない」と決めたとします。テストで時刻を固定できるようにするためです。それでも、誰かが`datetime.now()`を1行足します。レビューで見落とすと、テストはその日の日付で通ってしまいます。

文書は、読まれた時にしか働きません。人も、AIのコーディングエージェントも、文書を読み落とします。この記事では、ADRそのものをpytestで走るファイルにする方法を紹介します。使うのは、Pythonのライブラリdoeffの部品doeff-adrです。

## まず短いコード

次のADRは、決めた訳・規則・反例と、規則を守らせる検査を1つのファイルに持ちます。pytestで走らせると、3つのテストになります。

```hy
;; docs/adr/defadr_billing_clock.hy — 設計の記録を、そのままpytestで走るファイルにする。
(require doeff-adr.macros [defadr rule law deftest defsemgrep]) ; ADR・規則・性質・2種類の検査の構文を使う。
(require doeff-hy.macros [val]) ; 一度だけ決める名前を作る。
(import doeff-adr.macros [fact counterexample]) ; 問題の事実と、許さない反例を値として書く。
(import datetime [datetime timezone]) ; 期限と、固定する時刻を作る。
(import doeff-time [sim-time-handler]) ; 開始時刻を決めた仮想の時計のハンドラ。
(import billing [is-overdue]) ; 守られる側の業務のコード。

(val today (datetime 2026 10 17 :tzinfo timezone.utc)) ; テストの「今日」を固定する。

(defadr ADR-BILLING-001 ; ADRの番号。この名でテストの名前が決まる。
  :title "業務のコードは時刻を直に読まない" ; 何を決めたか。
  :status "accepted" ; 採用済み。採用済みのADRは、検査を1つ以上持つ必要がある。
  :problem [(fact "datetime.now()を直に読むと、テストの結果が走らせた日で変わる")] ; 決めた訳。
  :decision [(rule R1 "今の時刻はGetTimeの依頼で頼み、外側の時計のハンドラが答える")] ; 決めた規則。
  :laws [(law same-clock-same-answer ; 規則が守られた時に成り立つ性質。
           :statement "同じ時刻を答える時計の下では、同じ期限に同じ答えを返す" ; 性質の文。
           :counterexamples [(counterexample "業務の関数の中でdatetime.now()を呼ぶ")])] ; 許さない例。
  :enforcement ; ここから下が、pytestで走る検査。
  [(deftest test-overdue-follows-the-clock ; 1つ目:時計を固定して、答えが決まる事を試す。
     (<- late ((sim-time-handler :start-time today) ; 今日を10月17日に固定した時計を付ける。
               (is-overdue (datetime 2026 10 1 :tzinfo timezone.utc)))) ; 期限10月1日は過ぎている。
     (<- early ((sim-time-handler :start-time today) ; 同じ時計をもう一度付ける。
                (is-overdue (datetime 2026 10 31 :tzinfo timezone.utc)))) ; 期限10月31日はまだ先。
     (assert (and late (not early)))) ; 走らせた日によらず、TrueとFalseになる。
   (defsemgrep no-direct-now ; 2つ目:Semgrepの規則が、例どおりに検出するかを試す。
     "billing-no-direct-now" ; .semgrep.yamlにある規則の名。
     [{"relative-path" "billing.py" ; 検出すべき例:業務のファイルでOSの時計を読む。
       "source" "from datetime import datetime\nnow = datetime.now()\n"}]
     [{"relative-path" "billing.py" ; 検出しない例:時刻をGetTimeの依頼で頼む。
       "source" "def f():\n    now = yield GetTime()\n    return now\n"}])])
```

コードはHyで書きます。HyはPythonの上で動く、Lispの形の言語です。`(f x :key v)`はPythonの`f(x, key=v)`にあたり、`;`から行末は注釈です。Hyのファイルは、Pythonのモジュールと同じようにimportできます。

- `defadr`が、1件のADRです。`:problem`に訳、`:decision`に規則R1を書きます。
- `:laws`には、規則が守られた時に成り立つ性質と、許さない反例を書きます。
- `:enforcement`の中が、pytestで走る検査です。
- `deftest`は、時計を固定して期限の判定を試します。
- `deftest`の中の`(<- 名 式)`は、Pythonの`名 = yield 式`にあたります。式の答えを名に入れます。
- `defsemgrep`は、静的検査の規則が悪い例を検出し、良い例を検出しない事を試します。

![1件のADRから、3つのテストと1つの走査が出る](/images/zenn-use-cases-v0/generated/adr-concept.png)
*ADRのファイルをpytestが集め、3つのテストにします。コード全体への走査は、同じ名前の規則を`semgrep scan`で当てます。*

## ADRと、それが崩れる訳

ADR(Architecture Decision Record)は、設計の判断を1件ずつ残す文書です。何を決めたか、なぜ決めたか、何を選ばなかったかを書きます。多くはMarkdownで書き、リポジトリの`docs/adr/`などに置きます。

文書だけのADRには、次の弱さがあります。

- 規則を破るコードを書いても、何も落ちません。
- 守られているかは、人が読んで確かめるしかありません。
- 規則を守らせる手段が無くても、採用済みとして残ります。

doeff-adrは、ADRをHyのファイルにして、pytestから実行します。文書だけのADRとの違いは次のとおりです。

| ADRの項目 | 文書だけのADR | doeff-adrのADR |
| --- | --- | --- |
| 決めた規則 | 文章で書く | `rule`で番号を付けて書く |
| 守る性質と反例 | 文章で書く | `law`と`counterexample`の値にする |
| 規則を守らせる手段 | レビューに頼る | `deftest`と`defsemgrep`のテスト |
| 採用済みの印 | 状態の欄に書く | 検査が1つも無いと、テストが落ちる |

## 守られる側のコード

ADRが守るのは、請求の期限を判定する次の関数です。今の時刻は、自分では読みません。

```python
from datetime import datetime  # 期限の型に使う(ここでは時刻を読まない)。

from doeff import EffectGenerator, do  # 戻り値の型と、Programにする印を使う。
from doeff_time import GetTime  # 「今の日時が欲しい」という依頼。答えるのは外側の時計のハンドラ。


@do  # 呼んだだけでは動かないProgramにする。
def is_overdue(due: datetime) -> EffectGenerator[bool]:  # 期限を過ぎたかをboolで返す。
    now = yield GetTime()  # 時刻はハンドラに頼む。datetime.now()は呼ばない(ADRのR1)。
    return now > due  # 期限より後ならTrue。
```

- `@do`を付けた関数は、呼んでもすぐには動かず、手順を表すProgramを返します。
- `GetTime`は「今の日時が欲しい」という依頼です。doeffでは、このような依頼をエフェクトと呼びます。
- 依頼を受けて答える部品を、ハンドラと呼びます。
- `yield GetTime()`で処理が止まります。外側のハンドラが日時を渡すと、続きが動きます。
- 本番ではOSの時計のハンドラ、テストでは仮想の時計のハンドラを付けます。
- 時計のハンドラは、doeffの部品doeff-timeが持ちます。
- そのため、このプロジェクトのコードにはOSの時計を読む行がありません。

仕組みは、3本目の[20秒待つ処理を、20秒待たずにテストする](doeff-time.md)で詳しく説明しています。

## 1つ目の検査:時計を固定して答えを試す

ADRの`deftest`は、仮想の時計`sim-time-handler`を付けて`is-overdue`を2回動かします。

- 時計は、今日を2026年10月17日に固定します。
- 期限が10月1日なら過ぎているのでTrue、10月31日ならまだなのでFalseです。
- どの日に走らせても、同じ答えになります。

`deftest`が作る処理をどう動かすかは、プロジェクトが決めます。そのため、`doeff_interpreter`というfixtureを`conftest.py`に置きます。

```python
import pytest  # fixtureを定義する。
from doeff import run  # Programを最後まで動かす。
from doeff_core_effects.scheduler import scheduled  # 時計のハンドラが要る、タスクの管理を付ける。


@pytest.fixture  # deftestの関数が受け取るfixtureにする。
def doeff_interpreter():  # 名前はdoeff-adrが決めている。
    def run_program(program, *, env=None):  # deftestはProgramを渡す。envはここでは使わない。
        return run(scheduled(program))  # 時計はADRの中で付ける。ここはタスクの管理だけを付ける。

    return run_program  # 動かす関数を返す。
```

doeff-adrは、実行の仕方やハンドラを自分では選びません。プロジェクトごとに、使う時計や外部の接続が違うからです。

ここに落とし穴があります。`is_overdue`の中で、`datetime.now(due.tzinfo)`を直に読むように書き換えたとします。それでもこのテストは、10月31日までは通ります。今日が、10月1日より後で10月31日より前だからです。実行のテストだけでは、規則の破れを見逃すことがあります。そこで、コードの形を見る2つ目の検査を置きます。

## 2つ目の検査:Semgrepの規則を例で試す

Semgrepは、コードを構文の形で探す静的検査の道具です。規則はYAMLで書き、CIでコード全体に当てます。

```yaml
# 業務のコードがOSの時計を直に読む呼び出しを探す(ADR-BILLING-001のR1)。
rules:
  - id: billing-no-direct-now  # ADRのdefsemgrepが、この名で規則を引く。
    languages: [python]  # Pythonの構文として照合する。
    severity: ERROR  # 見つかったら失敗として扱う。
    message: 今の時刻はGetTimeの依頼で頼みます(ADR-BILLING-001のR1)  # 見つけた時に出す訳。
    pattern-either:  # どれか1つに当たれば見つける。
      - pattern: datetime.datetime.now(...)  # from datetime import datetime の形も同じ物と見る。
      - pattern: time.time()  # UNIXの秒を直に読む呼び出し。
```

ADRの`defsemgrep`は、この規則へ検出すべき例と検出しない例を渡します。テストは2つの例を一時的なディレクトリに書き、Semgrepを当てます。検出すべき例を見逃すか、検出しない例で検出すると、テストが落ちます。規則そのものが壊れていない事も、これで確かめられます。

4つの検査は、確かめる事が少しずつ違います。

| 検査 | 確かめる事 | 走る時 |
| --- | --- | --- |
| `semgrep scan` | コードに、規則に当たる行が無い | CIとコミットの前 |
| `defsemgrep`のテスト | 規則が、例どおりに検出する | pytest |
| `deftest`のテスト | 時計を固定した時の答え | pytest |
| ADRの形のテスト | 採用済みのADRが、検査を持つ | pytest |

ADRの形のテストは、`defadr`が自動で作ります。名前は`test_ADR_BILLING_001_adr_contract`です。

## 走らせる

doeff本体とdoeff-hyは、PyPIの0.6.0を入れます。doeff-adrとdoeff-timeはPyPIに無いので、GitHubから入れます。どちらも、自分のプロジェクトの`pyproject.toml`に書きます。

```bash
mkdir -p billing-example/docs/adr && cd billing-example  # 自分のプロジェクトを作り、上の4つの枠を置く。
cat > pyproject.toml <<'EOF'  # 依存と、pytestにADRを集めさせる設定を書く。
[project]
name = "billing-example"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "doeff-hy==0.6.0",  # PyPIの0.6.0。doeff本体も同じ版が入る。
    "doeff-time @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-time",
    "doeff-adr @ git+https://github.com/proboscis/doeff@9adc4f8#subdirectory=packages/doeff-adr",
    "semgrep",  # defsemgrepのテストとsemgrep scanが使う。
]

[tool.uv]
no-sources = true  # doeff本体をGitHubから組み立てず、PyPIの版を使う。

[tool.pytest.ini_options]
testpaths = ["docs/adr"]  # pytestにADRのディレクトリを見させる。
EOF
uv run pytest -q  # 依存を入れ、ADRの3つのテストを走らせる。「3 passed」と出る。
uv run semgrep scan --config .semgrep.yaml --error .  # 規則をコードに当てる。0件で終了コード0。
uv run doeff-adr verify-wiring  # 全てのADRがpytestに集められたかを確かめる。
```

`no-sources = true`を書かないと、doeff本体もGitHubから入り、Rustのビルドが要ります。

4つの枠は、次の名前で置きます。ADRは`docs/adr/defadr_billing_clock.hy`、業務のコードは`billing.py`です。Semgrepの規則は`.semgrep.yaml`、fixtureは`conftest.py`です。

`testpaths`に`"docs/adr"`を入れると、pytestがADRのディレクトリを見ます。名前が`defadr_*.hy`のファイルを、doeff-adrのpytestのプラグインがテストとして読みます。

最後の`verify-wiring`は、全てのADRのファイルがpytestに集められたかを確かめます。`testpaths`から`docs/adr`が抜けると、ADRのテストは1つも走りません。それでもpytestは成功するので、この確かめをCIに置きます。

## 決まりを破ると、どこで止まるか

決まりを3通りに破り、どこで止まるかを試しました。2026年10月17日に、上の手順でPyPIの0.6.0を入れて走らせた出力です。

```text
# 1. billing.pyの「now = yield GetTime()」を「now = datetime.now(due.tzinfo)」に替えた
$ uv run semgrep scan --config .semgrep.yaml --error .
    billing.py
   ❯❯❱ billing-no-direct-now
          ❰❰ Blocking ❱❱
          今の時刻はGetTimeの依頼で頼みます(ADR-BILLING-001のR1)
            9┆ now = datetime.now(due.tzinfo)
(終了コード 1。同じ変更でも、pytestの3つのテストは通った)

# 2. ADRから:enforcementを消した
$ uv run pytest -q
E           AssertionError: ADR-BILLING-001: accepted ADR must have executable enforcement
FAILED docs/adr/defadr_billing_clock.hy::test_ADR_BILLING_001_adr_contract - ...
(終了コード 1)

# 3. pyproject.tomlのtestpathsを["tests"]にした(ADRは集められない)
$ uv run doeff-adr verify-wiring
ERROR: doeff-adr wiring verification failed: executable ADR files exist but were not collected:
  - docs/adr/defadr_billing_clock.hy
(終了コード 4。同じ設定でも、普段のpytestはtestsのテストだけを走らせて通った)
```

![決まりを破る3つの変更と、止まる所](/images/zenn-use-cases-v0/generated/adr-flow.png)
*破り方ごとに、止める検査が違います。1つ目はpytestでは通るので、コードの形を見る走査が要ります。*

- 業務のコードに`datetime.now()`を足すと、`semgrep scan`がその行を指して止まります。
- `:enforcement`を消すと、採用済みなのに検査が無いとして、ADRの形のテストが落ちます。
- `testpaths`から外すと、`verify-wiring`が集められていないファイルを名指します。

1つ目の変更では、pytestの3つのテストが通りました。実行のテストと形の検査は、互いの見逃しを埋めます。

## doeffのリポジトリでの使い方

doeff自身も、設計の判断をこの形で書いています。2026年10月17日に、doeffのmain(commit `9adc4f8`)で数えた値です。

| 項目 | 数 |
| --- | --- |
| `docs/adr/`のADRのファイル | 43件 |
| 性質(`law`) | 189個 |
| `deftest`の検査 | 115個 |
| `defsemgrep`の検査 | 51個 |
| `.semgrep.yaml`の規則 | 282個 |

数は、doeffの台帳`docs/adr/enforcement-ledger.json`から読みました。この台帳は、ADRとSemgrepの規則から作る生成物です。検査が減ると、台帳の差分にその名前が残ります。最初のADRのファイルは、2026年7月1日に入りました。

ADRの規則が破れると、テストの失敗として出ます。失敗の出力は、人にもAIのエージェントにも同じ形で届きます。どのADRのどの規則かも、テストの名前と出力に出ます。

## できない事と気をつける事

- `law`の文と反例は記録です。文そのものは実行されません。守らせるのは`deftest`と`defsemgrep`です。
- Semgrepは構文の形を見ます。文字列から関数を組み立てる呼び方などは見つけられません。
- `semgrep`のコマンドが無い環境では、`defsemgrep`のテストは飛ばされず、失敗します。
- Semgrepの起動には時間がかかります。2026年10月17日に手元で測ると、`defsemgrep`のテスト1つに約10秒かかりました。
- `deftest`を使うには、プロジェクトに`doeff_interpreter`のfixtureが要ります。
- ADRはHyで書きます。プロジェクトの本体はPythonのままで構いません。

## まとめ

- ADRに規則・反例・検査を一緒に書くと、規則の破れがテストの失敗になります。
- 実行のテストは答えを、Semgrepはコードの形を確かめます。片方だけでは見逃しがあります。
- `verify-wiring`で、ADRがテストから漏れていない事も確かめます。

最初は1件のADRと、1つの`deftest`から始められます。

連載はこの10本目で一区切りです。1本目の目次から、ほかの悩みに合う回を選べます。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月17日に、上の手順でPyPIのdoeff 0.6.0を入れて確かめました。Pythonは3.12と3.14で、Hy 1.3.1・Semgrep 1.181.0です。GitHubにあるdoeffのmain(commit `9adc4f8`)の環境でも、同じく動きました。

- 完全な例:[ADR](examples/adr_as_test/docs/adr/adr_billing_clock.hy)・[業務のコード](examples/adr_as_test/billing.py)・[conftest.py](examples/adr_as_test/conftest.py)・[Semgrepの規則](examples/adr_as_test/.semgrep.yaml)・[pyproject.toml](examples/adr_as_test/pyproject.toml)
- 完全な例は、doeffのリポジトリの中に置いています。そのためADRのファイル名を`adr_billing_clock.hy`にし、`pyproject.toml`で集めます。
- [doeff-adrのREADME](https://github.com/proboscis/doeff/blob/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-adr/README.md)
- 時計のハンドラ:[20秒待つ処理を、20秒待たずにテストする](doeff-time.md)
- Hyの書き方:[同じdoeffを、Hyでもっと組み立てやすく書く](doeff-hy.md)
- 索引やリンターなどの道具:[書く・見つける・検査する](doeff-tooling.md)
- AIのエージェントに規則を見せる仕組み:[AIに要る時だけ規則を見せるhook](doeff-hooks.md)
- 前の回:[async/awaitがあるのに、なぜyieldか](doeff-coroutines.md)
