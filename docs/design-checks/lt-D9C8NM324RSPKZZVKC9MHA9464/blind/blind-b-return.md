## 失敗例: 環境変数を読む macro の使い手を、環境ごとの置き場の entry で使い回す(読み込みの口 L に入れた変更)

この変更は提示された検査を通ります。それでも、macro の file が変わった展開を今に合うと判じます(B1 違反)。木には何も当てていません。

### 1. 機能の要求と差分

**要求**: worker の Pod や新しい作業木では、Hy の code を共有の置き場から引きます。ところが環境変数を読む macro の使い手は、記録の行が印 `ENVIRONMENT_READ` なので照らしがいつも古いと判じます。そのため Pod・作業木ごとに compile し直しています。環境変数の値が同じ process どうしでは、置き場の entry を使い回したい、という要求です。

**差分**: `loader_hooks.py` だけを変えます(全体は `/tmp/blind-B/loader_hooks.diff`)。
- `_to_shared_store`: 印の行を持つ code は、通常の鍵に今の `os.environ` 全部の指紋を足した entry へ書きます。
- `_from_shared_store`: 通常の entry が使えない時に、その環境の entry を引きます。照らしは次の一文で行います。

```python
if record is None or not record_is_current_here(_apart_from_environment(record)):
    return None
# _apart_from_environment = 記録から digest == records.ENVIRONMENT_READ の行を除いた dataclasses.replace(...)
```

鍵の指紋が環境変数を覆うので、残りの行を照らせば足りる、という考えで書いた変更です。

### 2. 本来の持ち主と、破られる契約

- **C(照らし)の判断が L へ移ります。** 契約は「記録か今の digest のどちらかが印なら古い」です。L は印の行を消した記録を作って C の口に通し、C を迂回しています。
  - probe で確かめると、同じ記録をそのまま C に渡した答えは `False` でした。それでも L はその code を使います。
  - L 自身の不変条件「照らしが偽なら compile し直す」にも反します。
- **W・R の知識(印の意味)を L が読み替えています。**
  - W では、印は閉包の digest **全体** の代わりです(`_remembered` の `return ClosureDigest(ENVIRONMENT_READ, modules)`)。macro の code・補助の関数・辿った値もこの digest に含まれます。
  - L はこれを「環境変数にだけ依る」という旗だと読み、digest の代わりに環境の指紋を使います。何を digest に入れるかは W の責務ですが、その判断が L に移っています。
  - R の形と `RECORD_TAG` は変えていません。
- **process の状態の読みが L に入ります。** L が `os.environ` 全体を読むようになります。
- **主張との食い違い**
  - S2(process ごとの展開し直しをなくす変更は W の `_bound` だけ)に反し、L で実現しています。
  - S5(別の環境で作った展開を引かない)と B1(macro を定義した file の中身が違えば古い)を破ります。

### 3. 提示された各検査がこの変更を拒まない理由

| 検査 | 結果 | 理由 |
|---|---|---|
| 環境変数の形の検 | **実測**: 12 passed(元の版も 12 passed) | 理由は下の 3 点(code を読んだ推測で、計装はしていない) |
| 照らしの他の検(9 file) | **実測**: 18 + 75 + 42 = 135 passed(3 回に分けて実行) | 9 file のうち test_macro_use_record.py 以外の 8 file を grep し、環境変数を読む macro が無い事を確かめた。印の無い記録の鍵と書き方は変えていない |
| ruff | 変更後 2 件(I001 10 行・E402 37 行)、元の file 3 件(同じ 2 件 + PLR0911)。どちらも exit 1 | 新しい指摘は無い。戻りの数が減り PLR0911 は消える。元から在る指摘を着地の関門がどう扱うかは不明 |
| 変更範囲の品質検査・pyright | 走らせていない | 結果は主張しない。足した import は、関数の中の標準 library と、同じ package の既に import 済みの module だけ |

環境変数の形の検が通る理由(推測):
- `_scope_tree` は同じ根・同じ `PYTHONPYCACHEPREFIX` で子の process を 2 つ走らせます。2 つ目の子は作業木の .pyc の timestamp が合うので、loader_hooks.py の 484 行で置き場を引く前に戻ります。変えた枝を通りません。
- 検が替えるのは環境変数だけで、macro の file は替えません。環境変数を替えれば指紋も変わるので、環境の entry には当たりません。
- `test_a_changed_environment_value_is_stale_in_a_second_process` と `_row_check` の単体の検は、C を直に呼びます(L を通りません)。

### 4. 最小の再現

**入力**:
- `macros.hy` = `(import os) (defmacro direct [] (os.environ.get "BLINDB_MODE" "plain"))`
- `u_direct.hy` = `(require blindb_env.macros [direct]) (setv value (direct))`
- 変更 = `direct` の本体を `(.upper …)` で包む

**手順**:
1. 写しを作る: `cp -r <木>/packages/doeff-hy/src/doeff_hy_bytecode_guard /tmp/blind-B/overlay/` のあと、`python3 -I /tmp/blind-B/tools/apply_diff.py /tmp/blind-B/overlay/doeff_hy_bytecode_guard/loader_hooks.py`
2. 検を走らせる: 提示の命令に `PYTHONPATH=/tmp/blind-B/overlay`(と pycache・置き場の指定)を足して実行します。
3. probe を走らせる: 木を cwd にして `PYTHONPYCACHEPREFIX=/tmp/blind-B/pycache DOEFF_HY_CODE_STORE=/tmp/blind-B/store uv run --frozen python /tmp/blind-B/probe_store.py`

**実測の結果**(P4 以外は、子の process の環境変数が全く同じ):

| 手順 | 元の版 | 失敗例 |
|---|---|---|
| P2 別の作業木で macro を変えた | `PLAIN`(compile し直す) | **`plain`**(使い手を compile しない) |
| P3 変えていない作業木の写し | `plain` | `plain`(compile 0 — 要求の利得) |
| P4 `BLINDB_MODE=fancy` | `FANCY` | `FANCY` |
| Q2 同じ作業木で macro をその場で書き換えた(.pyc を書かない設定・名指しの置き場) | `PLAIN` | **`plain`** |

### 5. 確かめていない前提と、足りない情報

- 木を書き換える代わりに、`PYTHONPATH` の写しで差分を当てました。
  - 同じ設定の `python -c` で写しの loader_hooks が起動時に読まれる事と、probe の子の process が写しを使った事は確かめました。
  - 検の子の process 1 つずつまでは確かめていません。
- 元の版で流したのは環境変数の形の検だけです。9 file の検を元の版では流していません。
- 実際に起きる頻度は、作る側と使う側で環境変数の全部が同じかどうかで変わります。Pod の HOSTNAME や shell の PWD などの違いは確かめていません。Q2 の形はこの前提に依りません。
- 今の本物の macro(doeff_hy など)が印を出すかどうかは確かめていません(要件 3 の通りなら出さず、今は変更が働かないはずです)。
- 指定の外で `code_store.py` を読みました(置き場の鍵の作り方を知るため)。
- `/tmp/blind-B` は別の作業(`co`・`co-probe`)と共用です。
  - probe は最初に `/tmp/blind-B/probe` を消して作り直します。初回の前に同じ名の dir が無かった事は確かめていません。
  - 目についた範囲の他の probe(`probe_prod.py`・`make_v1.py`・`lt0bc9-probe.hy`)は、この dir を参照していませんでした。
