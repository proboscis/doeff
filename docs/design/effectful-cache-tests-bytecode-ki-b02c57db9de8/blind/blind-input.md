# 盲検の入力 — @effectful のキャッシュの検査と、検査の走行の「bytecode を書かない」設定

対象版: doeff `5750826265f65e4e6727d6e00bebf76b97cb28b3` に、下の「提案の差分」を当てた木。
読める場所(読むだけ・変更しない): `/home/kento/.worktrees/doeff-wt-ki-b02c57db9de8-design/`
(差分は当ててある。`git diff` と `tests/test_bytecode_writing_off.py`(未追跡の新しい file)が提案)。
設計の記録の dir(`docs/design/effectful-cache-tests-bytecode-ki-b02c57db9de8/`)は読まないこと。

## 背景(事実)

- doeff の `@effectful` は import の時に関数を書き換え、書き換えた bytecode を
  `__pycache__/<stem>.<cache_tag>-doeff-effectful-<REWRITE_VERSION>.pyc` に保存して再利用する
  (`doeff/_effectful_rewrite.py` の `cache_path` / `_read_cache` / `_write_cache` / `EffectfulLoader.get_code`)。
  `_write_cache` は `sys.dont_write_bytecode` が真なら書かない。書けない木(OSError)でも黙って書かない。
- 着地と日次の全体検証を走らせる道具(別 repo dotfiles の `agentcli/src/agentcli/land.py` の `_hygiene_env`)は、
  検査の子 process すべてに `PYTHONDONTWRITEBYTECODE=1` を渡す(被験の木に `__pycache__` を書かせないため)。
  この道具は変えない。
- 日次の全体検証の命令: `make sync && uv run --no-sync pytest -q -m 'not e2e'`(`pyproject.toml` の testpaths =
  `tests`・`docs/adr`・`packages/doeff-adr/tests`・`packages/doeff-domain/tests`・
  `packages/doeff-effect-analyzer/tests/python`・`packages/doeff-time/tests`・`packages/doeff-vm/tests`・
  `packages/doeff-vm-core/tests`)。遠隔の機体(zeus)で走り、不達ならこの機体で同じ命令を走らせる。
- 開発者は普段 env を付けずに `uv run pytest ...` を走らせる。

## 要件

1. `tests/test_effectful.py` のキャッシュの検査 2 本(`test_cache_is_named_by_the_rewrite_version_and_reused`・
   `test_a_new_rewrite_version_rebuilds_the_cache`)が、自分の前提(bytecode を書ける)を検査の中で立て、
   どの環境でも同じ答えになる。
2. `sys.dont_write_bytecode` が真の時にキャッシュを書かないことを、別の 1 本で守る。
3. land の `PYTHONDONTWRITEBYTECODE=1` は変えない。
4. 同じ種類の問題(検査の答えが走行環境の bytecode の設定に依る)が再び入らないようにする。

## 提案(決定)

- D1: 2 本は本文の先頭で `monkeypatch.setattr(sys, "dont_write_bytecode", False)` を置く。
- D2: 新しい 1 本 `test_no_cache_is_written_while_bytecode_writing_is_off`(`True` を自分で立て、キャッシュの file と
  `__pycache__` が作られないこと・2 回目の import も compile し直すことを確かめる)。
- D3: root `conftest.py` に session 単位の autouse fixture `_bytecode_writing_off`。収集の後から走行の終わりまで
  `sys.dont_write_bytecode = True` と `PYTHONDONTWRITEBYTECODE=1` を立てて、終わりに戻す。終わりの時点で値が
  `True` でなければ走行を赤にする。`tests/test_bytecode_writing_off.py` の 2 本が、検査の本文と子 process で
  値が立っていることを確かめる。
- D4: `packages/doeff-hy/tests/test_source_positions.py` と `test_none_type_contract.py` の module fixture は、
  `sys.dont_write_bytecode` を入る前の値へ戻す(以前は無条件に `False` を入れていた)。

## 責務

| id | 持つもの | 公開の形 | 不変条件 |
| --- | --- | --- | --- |
| effectful-cache(`doeff/_effectful_rewrite.py`・変えない) | 書く / 書かないの判断・名前の付け方・file の形 | `cache_path(source_path)` と上の名前 | flag が真なら書かない・壊れた cache は捨てて compile |
| cache-tests(`tests/test_effectful.py` のキャッシュの 3 本と `tmp_package`) | 各検査の前提(flag の値) | 検査名 3 つ | 答えが走行環境の flag・env に依らない |
| suite-pin(`conftest.py` の `_bytecode_writing_off`・`tests/test_bytecode_writing_off.py`) | 走行中の flag と env を日次と同じ値にする | 検査は前提を変えたい時に monkeypatch で上書きする | 開発機と日次でこの値が同じ・終わりに値が戻っている |
| hy-probe-fixtures(doeff-hy の fixture 2 つ) | probe の import の間だけ flag を真にする | fixture `mod` | fixture の後の flag = 前の flag |
| land-env(dotfiles `land.py`・外部・変えない) | 子の env に `PYTHONDONTWRITEBYTECODE=1` | 子の env | 被験の木に `__pycache__` を書かせない |

## 変更シナリオと主張

| id | 軸 | 変更 | 主張 | 予想する範囲 |
| --- | --- | --- | --- | --- |
| S1 | distribution | 日次の走行場所が変わる(遠隔 ↔ fallback ↔ 開発機)。場所ごとに env の有無が違う | キャッシュの 3 本と固定の 2 本の答えは env に依らず同じ。前提の書き忘れは開発機でも赤になる | 変わる module なし |
| S2 | storage | キャッシュの名前の付け方を変える | 変わるのは effectful-cache と cache-tests の 2 本だけ | effectful-cache・cache-tests |
| S3 | concurrency | 日次を xdist(`-n N`)で走らせる | 変更なし | なし |
| S4 | effects | キャッシュを書かない条件を 1 つ足す、または今の条件を落とす | 変わるのは effectful-cache と cache-tests の 1 本。前提を立てた 2 本・固定は変わらない。条件を落とせば書かない側の 1 本が赤 | effectful-cache・cache-tests |
| S5 | hardware | 適用しない(機体の違いは env を通してしか入らない。検査は `sys.implementation.cache_tag` を読む) | — | — |
| S6 | simulation | 適用しない(キャッシュは import の副産物で program の意味に入らない) | — | — |

## 検査の実体と範囲

- pytest: 上の testpaths。root `conftest.py` が全 testpaths に効く(rootdir = repo root)。
- ruff check / ruff format(`make lint-ruff`)・pyright(`make lint-pyright`)。
- semgrep: `.semgrep.yaml` を `doeff/ packages/` にかける(`make lint-semgrep`・`tests/test_semgrep_gate.py`)。
  `tests/` と root の `conftest.py` は semgrep の範囲の外。
- 全体の走行は日次だけ。手元では触った file に絞った走行だけをする。

## 実験の手段(使ってよい)

- 手元の走行: `/tmp/ki-b02c57db9de8/run.sh` は使わないこと(記録を上書きする)。代わりに、上の木を
  `/tmp/ki-b02c57db9de8-blind-<A または B>/tree` へ複写(`cp -r`)し、その中で
  `PYTHONPATH=<複写>:<複写>/packages/doeff-core-effects /home/kento/repos/doeff/.venv/bin/python -m pytest <file> -q -p no:randomly`
  を撃てる(env の有無は `PYTHONDONTWRITEBYTECODE=1` / `env -u PYTHONDONTWRITEBYTECODE` で切り替える)。
- 複写の外(元の木・`/home/kento/repos/doeff`・ほかの dir)を変更しない。全体の走行(testpaths 全部)はしない。
