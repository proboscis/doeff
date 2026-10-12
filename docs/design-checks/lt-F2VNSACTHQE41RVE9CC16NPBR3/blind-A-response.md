S-eff の主張(Makefile の loop が runner の起こし方を変えても、変わるのは M-loop だけ)を破る失敗例を 1 つ見つけました。session の根を argv にどう書くかは M-loop の中の書き方ですが、M-checks の緑の検だけがそれを文字列のまま知っています。そのため、日次の性質を何も変えない M-loop の変更でも、緑の検が赤になって M-checks を直す必要が出ます。検は走らせておらず、すべて source を読んだ上での推論です。

### 1. 変更要求と、それが要る理由
**要求:** package ごとの検の根も Makefile の変数 1 つで定めます(例 `PACKAGE_TEST_ROOTS = $(wildcard packages/*/tests/)`)。それを PACKAGE_EXTRA_TEST_ROOTS と同じ 1 つの loop で回し、`print-package-test-roots` で外から読めるようにします。

**理由:**
- Makefile 自身が「母集団の根の定義はこの変数の 1 点」「検の側に 2 つ目の一覧を書かない(#2577)」と書いています。それなのに、package の根の形 `packages/<名>/tests` の写しが `/tmp/doeff-blind-c854bccaf/scripts/run_changed_tests.py` の `session_of`(621–632 行)に残っています。この要求は 1 点化を package の根にも広げる、自然な続きの仕事です。
- 2 つの loop の本体が重複しています。
- GNU make で dir だけを拾う普通の書き方 `$(wildcard packages/*/tests/)` は `packages/a/tests/` のように末尾に `/` を付けます。sh の `for t in packages/*/tests/` も同じです。この repo の `read_session_roots` も既に `root.rstrip("/")`(618 行)で末尾の `/` を受け入れており、make の出す根に `/` が付く事を想定しています。

### 2. 元の主張・前提と、予想に反して変わる所
**前提は守っています:**
- P1: session は全部 `$(PACKAGE_UV_RUN) pytest <根> …` で起こすままです。
- P2: 引数に足すのは `/` だけで、タブも改行も入りません。
- P3: fixture を変えないので、赤にする package は名前順で最初のままです。
- 変更は S-eff の軸「runner の起こし方」の中の、引数の書き方の変更です。
- M-loop の公開の口(make の変数・rc・要約の行)は変えません。

**予想に反して変わる所:** M-checks の緑の検 `test_make_test_packages_is_green_when_every_package_is_green` です(`/tmp/doeff-blind-c854bccaf/tests/test_daily_test_population.py` 371–376 行)。この検は argv の文字列のまま比べます。

```python
visited = sorted(arg for _cwd, argv in made.calls for arg in argv if arg.endswith(("tests", "sim")))
assert visited == ["extra/sim", "packages/a/tests", "packages/b/tests"]
```

同じ M-checks の中でも、訪ねの検(400・411 行)は `Path(cwd, arg).resolve()` で解いてから比べるので、変更の影響を受けません。予測範囲は「M-loop だけ」でしたが、実際は M-loop と M-checks が変わります。M-runner・M-make・M-round は変わりません。

### 3. どの知識がどこへ漏れるか(契約の拡張などとの区別)
- **漏れる知識:** 「session の根を argv にどう綴るか」です(repo の根からの相対・末尾の `/` なし・`./` なし・絶対 path でない)。これは M-loop の公開の口の表に無い、中の書き方です。
- **約束との食い違い:** S-eff の文と runner の註は「書き方に依らず、cwd から解いて同じ dir なら同じと数える」と約束しています。緑の検だけが、末尾の文字列と完全一致という別の規則で「訪ねた根」を出し直しています。同じ判断が M-checks の中に 2 つの規則で書かれ、片方が M-loop の書き方に結びついています。
- **区別:**
  - 公開の契約の拡張ではありません。M-loop の口も、緑の検が見る性質(rc 0・名指し無し)も変わりません。
  - 配線の変更ではありません。M-make も `_MakeRound` も変わりません。
  - 共同の不変条件でもありません。綴りを固定する要件は R1〜R5 にも M-checks の説明にも無く、訪ねの検と S-eff の文はむしろ綴りに依らない事を意図しています。
  - 例外として、`extra/sim` は PACKAGE_EXTRA_TEST_ROOTS の値をそのまま渡す公開の口なので、文字列で比べても正当です。
  - 以上から、隠すはずの知識の漏洩に分類します。

### 4. 最小の変更例・再現手順・観測すべき結果
最小の変更は Makefile 218 行の 1 文字です。要求どおりの全体の形にしても、argv に出るのは同じ `packages/<p>/tests/` です。共有の source ではなく、自分の作業木の写しで変えます。

```diff
-	$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" ...
+	$(PACKAGE_UV_RUN) pytest "$${dir}tests/" -m "not e2e" ...
```

**手順:**
1. 写しの Makefile の上の 1 行だけを変えます。
2. `uv run --no-sync pytest -q tests/test_daily_test_population.py -k "make_test_packages or failure_names"` を走らせます。
3. 比べるため、変更前の版でも同じ命令を走らせます。

**観測すべき結果(どれも推測です):**
- 緑の検だけが 374 行で赤になり、`模型の木の session を全部訪ねていない: ['extra/sim']` と出ます。rc を見る検(377 行)より前で止まります。
- 訪ねの検は緑のままです。resolve で末尾の `/` が落ち、runner の `cd && pwd -P` も同じ dir と見るので、最初の package も赤のまま名指されます。
- 秒の flag の検も緑のままです。
- 抜き出しに無かった `test_package_failure_names_are_repo_root_relative`(427 行)も緑のままです。pytest は失敗名を rootdir からの相対で作るので、`FAILED packages/a/tests/test_cli.py::test_fail` は変わりません。
- `./packages/<p>/tests` や `$(CURDIR)/packages/<p>/tests` の綴りでも、同じ赤が出ます。
- S-eff の文にある「package の dir へ cd して `tests` を渡す」形でも緑の検は赤になります。ただしこの形は失敗名の検も赤にする正当な退行(Makefile の註)なので、失敗例には使いませんでした。

### 5. 未確認の前提・不足する情報
**source を読んで確かめた事:**
- 緑の検の文字列での比べ方(371–376 行)と、訪ねの検の resolve(400・411 行)。
- runner の `cd && pwd -P`。
- `rstrip("/")`(run_changed_tests.py 618 行)と `session_of` の写し(621–632 行)。
- 抜き出しに無かった失敗名の検(427 行)が在る事。

**推測(走らせていない事):**
- 上の観測の全部。
- 末尾の `/` があっても pytest の node id と FAILED 行が変わらない事。
- `$(wildcard …/)` が末尾の `/` を残す事。

**不足:**
- package の根の 1 点化が実際に求められているか(card や issue の有無)は、読める範囲にありません。
- 実装者が `$(patsubst %/,%,…)` で `/` を落とせば、この赤は出ません。発生は書き方次第で、必ず起きるわけではありません。
- 緑の検の list の完全一致に「同じ根を 2 度回さない」性質を見る意図があるかは、記述が無く分かりません(訪ねの検は集合で比べるので、その性質を見られるのは緑の検だけです)。
- 全数検査はしていません。他のシナリオについて安全だとは結論しません。

### 予測の外れはあるが、漏洩とは分類しない観察
- **S-sim:** runner の env の名は M-make の `_run_test_packages`(311–314 行)が組み、`fail: Path` も 1 つだけです。rc 5 や赤 2 つの模擬を足すと M-make も変わり、予測の範囲より広くなります。分類は配線の変更、または M-make の公開の口の拡張です。親の環境へ書いて EXTEND で子へ流す抜け道は、#3012 の作法に反します(lint が tests/ でそれを止めるかは未確認)。
- **S-eff の「要約の行の書き方を変える」:** 行の形は M-loop の公開の口で、`_named_failed_packages` が読んでいます。形を変えれば M-expect も変わるので、これは公開の契約の変更です。予測の「M-loop だけ」はこの項目では言い過ぎですが、if の書き方だけを指すなら当たりません(文が曖昧です)。
- **S-st の「記録を fd へ出す」:** `RunProcess`(`/tmp/doeff-blind-c854bccaf/packages/doeff-core-effects/doeff_core_effects/process_effects.pyi`)には fd を子へ渡す欄がありません。M-runner と M-make の中だけでは済まず、依存先の契約の拡張が要ります。

WAIT: work — 呼び手の次の依頼
