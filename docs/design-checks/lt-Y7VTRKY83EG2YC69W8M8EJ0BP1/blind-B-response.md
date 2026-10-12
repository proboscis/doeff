## 結論

検査を通るのに責務を侵す実装を 1 つ見つけ、両方を一時 dir の写しで実測しました。中身は「doeff-hy の Hy の上限の行に、Python の版の条件(marker)を 1 つ付ける」1 行の変更です。共有の作業木は書き換えていません(作業後の `git status` は 0 行)。

## 1. 機能の要求と差分

**要求(現実的な形):** Python 3.15 が出たので、agora-controllers(今は `requires-python >=3.14`)は 3.15 へ移りたい。doeff の検は 3.14t(`.python-version` = 3.14.3t)でしか走らず、3.15 で確かめた Hy はまだ無い。そこで「3.15 へ移る repo を doeff-hy の上限で止めないでほしい」と頼まれる。

**`packages/doeff-hy/pyproject.toml` の差分**
```diff
     # Hy を上げる便は、この上限と uv.lock を同じ commit で上げる(ADR-DOE-HY-008 R6・card ki-0ff168f4eaf5)。
-    "hy>=1.2.0,<=1.3.1",
+    # 上限は doeff の検が走る Python(.python-version = 3.14)の範囲だけに掛ける — doeff は 3.15 で Hy を確かめていないので、
+    # 3.15 へ移る repo(agora-controllers)の Hy は、その repo の宣言と lock が決める(doeff-hy を使う repo はどれも自分で hy を宣言している)。
+    "hy>=1.2.0,<=1.3.1; python_version < '3.15'",
```

**`uv.lock` の差分**(写しで `uv lock` を実行して得た物・実測)
- doeff-hy の dependencies と requires-dist の 2 行だけに `marker = "python_full_version < '3.15'"` が付きます。
- `[[package]] name = "hy"` は 1.3.1・registry の 1 行のままです。

## 2. 本来の持ち主と、侵す契約

**本来の持ち主**
- 「環境に入れてよい Hy の範囲」は M-cap が持ちます。不変条件は「上限 = M-lock の版ちょうど」で、doeff-hy が入る全ての環境に効く前提です。
- 「確かめた版」は M-lock が、判定は M-check(R4・R6)が持ちます。
- M-consumer の不変条件は「lock の Hy は M-cap の範囲の内(uv の解決が守る)」です。

**何がどこへ移るか**
- Python 3.15 以上の環境では、Hy の版を決める判断が doeff(M-lock と R4)から各消費者の宣言へ移ります。移り先は、agora-controllers の根 `hy>=1.3`・controllers/* `hy>=1.0`・custody `hy>=1.3`・agent-commons `hy>=1.2.0` などです。
- R4 は「3.14 × lock の版」でしか測りません。そのため、3.15 で入る Hy はどの doeff の検も通りません。

**破れる主張**
- S1「消費者だけでは入れられない」は、3.15 では偽になります。
- S3「修正版は、doeff が上げる commit までどの消費者にも入らない」も、3.15 では偽になります。
- R6 の law は字面のまま成り立ちますが、M-cap の不変条件は「3.15 未満だけ」に縮みます。

**副作用**
- 「doeff の検が走る Python の版」という `.python-version` の知識が、`'3.15'` の写しとして M-cap に入ります。
- 提示された検の中に、この写しと `.python-version` を結ぶ物はありません。

## 3. 提示された検がこれを拒まない理由

| 検 | 拒まない理由 | 写しでの結果 |
|---|---|---|
| R6(`hy-cap-violations`) | 見るのは「hy の行が 1 つ」「`<=1.3.1` が specifier に在る」「specifier が 1.3.1 を通す」の 3 つだけで、`requirement.marker` を読まない。law の statement も「the single hy requirement r」の specifier だけを量る | 緑 |
| R4 | 照合の版は uv.lock の hy の行(1.3.1 のまま)、Python は検を走らせる interpreter(3.14)。宣言の marker は入力に無い | 緑 |
| R3・R5・反例の検 | フォークの URL と例外の台帳だけを見る。判定の code は変えていない | 緑 |
| M-select(契約の表 + `run_changed_tests.py`) | 変えた path(doeff-hy の pyproject と uv.lock)から、test_hy_stubs・R4・R6・test_daily_test_population・test_contract_table_follows_packages の 5 本を選ぶ。R4・R6 は選ばれて走るが、上のとおり緑。表と package は変えていないので、表の読みも止まらない(gaps は `()`) | 選びは script の関数で計算(実測) |
| 逆依存の検 | この環境の PATH に doeff-linter が無いので、「逆依存を測れなかった」と名指すだけで赤にしない | code を読んだ推測 |

対照として、次の 2 つでは R6 が赤になることも実測しました。
- 上限を外した `hy>=1.2.0` は「上限が uv.lock の版ちょうどでない」で赤。
- 正直に 2 行へ分けた形(`<=1.3.1; <3.15` と `>=1.2.0; >=3.15`)は「hy の行が 1 つでない」で赤。

つまり、1 行に marker を付けた形だけがすり抜けます。

## 4. 確かめる最小の手順と結果

準備した物は、すべて `/tmp/hycap-yItaGD` の下に残してあります。

1. **写しを作る:** `git ls-files -z | xargs -0 cp --parents -t /tmp/hycap-yItaGD/repo/` で写し、1 の差分を当てて `uv lock` を実行します。
2. **検を走らせる:** 次の形で、1 本ずつ流します。
   ```
   cd /work/toolset/worktrees/doeff-wt-hyub-dc && uv run --frozen bash -c 'cd /tmp/hycap-yItaGD/repo && pytest "docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy::<名>" -q -m "not e2e"'
   ```
   - 実測の結果: R6 は 1 passed、R4 は 1 passed(32.9 秒)、R3・R5・反例の検もそれぞれ passed。
   - test_hy_stubs.py は 3 passed、test_contract_table_follows_packages.py は 14 passed。
3. **選びを確かめる:** `uv run --no-project python -I /tmp/hycap-yItaGD/scripts/select.py /tmp/hycap-yItaGD/repo packages/doeff-hy/pyproject.toml uv.lock` で、上の 5 本が選ばれます(実測)。
4. **違反を確かめる(`exp/` の下):**
   - 偽の hy 1.3.1 と 1.3.2 の wheel を作りました(`scripts/fake_wheel.py`)。
   - 宣言の行だけを写した stub の doeff-hy を path で引く消費者を置きました。消費者の宣言は `requires-python >=3.14`・`hy>=1.3; python_version<'3.15'`・`hy>=1.3.2; python_version>='3.15'` です。
   - そこで `uv lock --no-index --find-links ../wheels --no-python-downloads` を実行しました。

   | doeff-hy の宣言 | 結果(実測) |
   |---|---|
   | 元の行 | 解けず、uv が `doeff-hy==0.1.0 depends on hy>=1.2.0,<=1.3.1` と名指して止まる(S1 の主張どおり) |
   | marker を付けた行 | 解けて、`python_full_version < '3.15'` では hy 1.3.1、`python_full_version >= '3.15'` では hy 1.3.2 を固定する |

   - 補足として `uv pip compile --python-version 3.15` でも試しました。元の行は hy==1.3.1、marker の行は hy==1.3.2 になりました(実測)。

## 5. 確かめていない前提・足りない情報

**違反の側**
- Hy 1.3.2 は架空の版です。今日の PyPI の最新は 1.3.1 でした(`uv pip compile` で実測)。Hy 1.3.1 が本当に 3.15 で動かないかは、3.15 の interpreter が無いので測っていません。
- 消費者を `requires-python >=3.15` にした `uv lock` は、interpreter が無いので失敗しました(実測)。代わりに `uv pip compile --python-version 3.15` で測っています。「3.15 以上だけの lock では marker の行が消える」は推測です。
- stub は hy の行だけを持ちます。本物の doeff-hy の他の依存が hy を縛らないことは pyproject を読んで確かめましたが、agora-controllers の本物の木での解決は走らせていません。

**検の側**
- `make test-changed` そのものは走らせていません。git の操作をしない決まりなので、差分を持つ作業木を作れないためです。選びは script の関数に、変えた path を手で渡して計算しました。
- 選ばれた tests/test_daily_test_population.py(13 本)は走らせていません。
- 決まりに反して、test_hy_stubs.py と test_contract_table_follows_packages.py は file ごと走らせました(どちらも契約の表が file で名指す検です)。
- agora-controllers の日次の `uv lock --check` も走らせていません。

**判断の側**
- 実装者が赤になる 2 行の形ではなく、通る 1 行の形を書くかどうかは判断であって、実測ではありません。

**探索したが確かめていない別の候補**
- doeff-domain に同じ上限を写す形: R6 は doeff-hy しか読まないので、上限が 2 か所になっても拒まない見込みです。
- doeff_hy の code に「確かめた版」の定数を写す形: こちらも R6 は見ません。

WAIT: work — 依頼元の次の指示
