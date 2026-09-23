Opus 5.5(自己申告)

# 反例: `@effectful` のキャッシュを `sys.pycache_prefix` に従わせる修正

提案の検査は開発機の環境でも日次の環境でもすべて通ります。一方で、キャッシュの検査 2 本の答えが走行環境の別の bytecode 設定 `PYTHONPYCACHEPREFIX` で変わります。要件 4 で防ぐはずの種類の問題が、固定の仕組みをすり抜けて戻ってきます。

## 1. 機能要求と差分

**要求(想定)**: デプロイ先のコンテナはソースの木が読み取り専用です。`PYTHONPYCACHEPREFIX=/var/cache/pycache` を設定しているので、標準の `.pyc` はそこへ書かれます。ところが `@effectful` の module だけは `__pycache__` を作れません。`_write_cache` は OSError を黙って捨てるので、起動のたびに書き換えと compile をやり直しています。`_write_cache` のコメントにも「標準の importer と同じ」とあるので、標準と同じく `sys.pycache_prefix` に従ってほしい、という要求です。

**差分**(複写先 `/tmp/ki-b02c57db9de8-blind-B/tree` だけに当てました。元の木との `diff -u` の抜粋):
```diff
 def cache_path(source_path: str) -> Path:
     ...
     name = f"{source.stem}.{tag}-doeff-effectful-{REWRITE_VERSION}.pyc"
-    return source.parent / "__pycache__" / name
+    if sys.implementation.cache_tag is None:
+        return source.parent / "__pycache__" / name
+    # Beside the standard .pyc ... or the source's mirror under sys.pycache_prefix
+    return Path(importlib.util.cache_from_source(source_path)).with_name(name)
 ...
-        path.parent.mkdir(exist_ok=True)
+        path.parent.mkdir(parents=True, exist_ok=True)
```
あわせて、docstring に 1 行を足しました。`tests/test_effectful.py` には新しい 1 本 `test_the_cache_follows_the_pycache_prefix` を足しました。この検査は設計の公開の形どおり、`monkeypatch` で flag を `False` にし、`sys.pycache_prefix` を一時 dir に設定したうえで、`rewrite.cache_path(...)` が prefix の下に在ることを確かめます。既存の検査 2 本(D1 で直した 2 本)には手を入れていません。

## 2. 本来の所有者と、侵される契約

- **キャッシュの場所を決める知識が動く**: 置き場所はもともと effectful-cache(`doeff/_effectful_rewrite.py`)が、source の path・`cache_tag`・版から一通りに決めていました。変更後は、process 全体の状態 `sys.pycache_prefix` も一緒に決めます。この値は環境変数か `-X` で決まり、機体ごとに違います。つまり決定の一部が走行環境へ移ります。公開の形とされた「上の名前」(source の隣の `__pycache__/…`)も、この状態しだいで変わるようになります。
- **cache-tests の不変条件が崩れる**: 不変条件は「答えが走行環境の flag・env に依らない」です。2 本はキャッシュの場所を、公開の `cache_path` を呼ばずに `package / "__pycache__" / f"mod.{tag}-doeff-effectful-{V}.pyc"` と字面で書き写しています。そのため「prefix が設定されていない」という前提に黙って依存します。この前提は、検査の中でも宣言されず(D1 が立てるのは flag だけ)、どこからも固定されていません。
- **suite-pin の責務と要件 4 が満たされない**: suite-pin の責務は「走行中の flag と env を日次と同じ値にする」ことです。しかし固定しているのは `sys.dont_write_bytecode` と `PYTHONDONTWRITEBYTECODE` だけです。走行の終わりの assert も flag しか見ません。
- **S1 の主張が崩れる**: 「前提の書き忘れは開発機でも赤になる」が成り立ちません。prefix の無い開発機でも日次でも緑で、prefix の在る機体でだけ赤になります。
- **S2 の予想範囲が検査で強制されない**: S2 は名前の付け方が変わると cache-tests も変わると予想しています。ところが今回は cache-tests を変えないまま、すべての検査が通ります。

## 3. 各検査がこの変更を止めない理由(実測)

- **pytest**(触った 2 file):
  - 開発機の環境(`env -u PYTHONDONTWRITEBYTECODE -u PYTHONPYCACHEPREFIX`)で `34 passed, 1 warning in 4.51s`、exit=0。
  - 日次の環境(`PYTHONDONTWRITEBYTECODE=1`)で `34 passed, 1 warning in 4.47s`、exit=0。
  - 通る理由: どちらの環境にも prefix が無いので、`cache_from_source` は従来と同じ `__pycache__` を返します。
  - testpaths の中で `doeff-effectful` の名前か `cache_path` を参照するのは `tests/test_effectful.py` だけです(grep で実測。`persistent_cache_path` は無関係)。
- **root の `_bytecode_writing_off` と `tests/test_bytecode_writing_off.py`**: `sys.pycache_prefix` と `PYTHONPYCACHEPREFIX` は、固定も確認もしていません(コードを読んで確認)。prefix を設定した走行でも、この 2 本は緑でした。終わりの assert も発火していません。
- **ruff**: `ruff check doeff/_effectful_rewrite.py tests/test_effectful.py` は `All checks passed!`、exit=0。`ruff format --check` は同じ 2 file で `2 files already formatted`、exit=0(ruff 0.15.21)。
- **pyright**: `pyright doeff/_effectful_rewrite.py` は `0 errors, 0 warnings, 0 informations`、exit=0。`cache_path` の型は `str -> Path` のまま変わっていません。
- **semgrep**: 着地前の semgrep 検査と同じ config と flag で、変更した 1 file だけを走らせました(`--project-root <複写> --config .semgrep.yaml doeff/_effectful_rewrite.py --error --quiet --json`)。git を避けるため `--no-git-ignore` だけ足しています。結果は exit=0、`results 0 errors 0`。`.semgrep.yaml` に `pycache`・`cache_from_source`・`dont_write`・`importlib` を扱う規則は 0 件です(grep で実測)。`tests/` は検査範囲の外です。

## 4. 検査通過と契約違反を両方確かめる手順

1. `cp -r /home/kento/.worktrees/doeff-wt-ki-b02c57db9de8-design /tmp/ki-b02c57db9de8-blind-B/tree` を実行する(exit=0)。
2. 変更前(提案だけの木)に、`bash /tmp/ki-b02c57db9de8-blind-B/run3.sh base tests/test_effectful.py tests/test_bytecode_writing_off.py -k "cache or bytecode"` を実行する。`run3.sh` は blind-input の手順の命令を 3 つの環境で順に実行するだけのものです。
   - 開発機の環境: `5 passed, 28 deselected`、exit=0
   - 日次の環境: `5 passed, 28 deselected`、exit=0
   - prefix あり: `5 passed, 28 deselected`、exit=0
   - つまり提案だけの木なら、prefix があっても緑です。
3. 1 節の差分を当てて同じ命令を実行する(`-rf` 付き)。
   - 開発機の環境: `6 passed`、exit=0
   - 日次の環境: `6 passed`、exit=0
   - prefix あり: `2 failed, 4 passed`、exit=1。失敗は `test_cache_is_named_by_the_rewrite_version_and_reused` と `test_a_new_rewrite_version_rebuilds_the_cache`。
4. 日次の環境に prefix を足した場合(`PYTHONDONTWRITEBYTECODE=1 PYTHONPYCACHEPREFIX=…`): `2 failed, 4 passed`、exit=1。失敗箇所は `test_effectful.py:337` と `:356` の `assert … .is_file()` です。キャッシュ自体は prefix の下へ正しく書かれていました(`…/pycache-cx/tmp/pytest-of-kento/…/mod.cpython-314-doeff-effectful-d641f33ad76c0d22.pyc` を find で確認)。
5. 触った 2 file の全体を prefix ありで走らせた結果: `2 failed, 32 passed`、exit=1。

## 5. 実測と推測の区別、未確認の前提

- **blind-input の外で読んだこと(読むだけ)**: dotfiles の `land.py` の `_hygiene_env` は、親の env から `CLAUDE*`・`AGENTDECK*`・`CODEX_HOME`・`HERDR_PANE_ID` を落とした残りをすべて子へ渡し、`PYTHONDONTWRITEBYTECODE=1` を足します。したがって親の env に `PYTHONPYCACHEPREFIX` があれば、着地の検査にも日次にもそのまま渡る、というのは推測です。land 自体は動かしていません。
- **未確認**: zeus・fallback 先の機体・各開発者の shell に `PYTHONPYCACHEPREFIX` があるかは分かりません。この機体には無いことを `env | grep ^PYTHON` で実測しました。どこにも無ければ、この違反は検査に現れないまま残ります。
- **未実行**:
  - 日次の全体走行と `tests/test_semgrep_gate.py`。変更した file の semgrep 検出は 0 件なので、全体の件数も変わらないはずだというのは推測です。
  - `make lint-ruff`・`make lint-pyright`・`make lint-semgrep` の全範囲。走らせたのは変更した file だけです。
  - `make lint` に含まれる `lint-doeff`・`lint-packages`。提示された検査に入っていないので、通るかどうかは分かりません。
- **走行の前提**: Python 3.14.3 の free-threading build で、`cache_tag` は `cpython-314`。
- **解釈と構成**: 要求の文面は私が作ったものです。「上の名前」がディレクトリの位置まで含むかどうかは、私の解釈です。
- **採らなかった候補(未検証の手がかり)**: 固定は収集の後から始まります。実際に元の木には `tests/effectful_cases/__pycache__/programs.cpython-314-doeff-effectful-*.pyc` が在り、収集中に `@effectful` のキャッシュが木へ書かれていることが見えています。収集の時点の状態に依存する検査も、同じ種類の抜け道になり得ます。反例としては具体化していません。

複写先で作った file はすべて `/tmp/ki-b02c57db9de8-blind-B/` の下です(`tree/`・`run3.sh`・`*.log`・`semgrep.json`・`pycache-*`)。例外として、pytest は一時 dir を通常どおり `/tmp/pytest-of-kento/` に作ります。元の worktree・`/home/kento/repos/doeff` は変えておらず、git の操作もしていません。
