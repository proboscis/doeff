# 実装依頼書 — @effectful のキャッシュの検査を走行環境から切り離し、検査の走行の bytecode の設定を 1 つに揃える

- 親の依頼(計画段): `lt-64Y1D2GNNSNFQ1YBY6QPW4ZY50`(class investigate)。依頼者として検収するのは計画段の会話
  `c-ZSX5HW937JVHSG80KE6JPCKC1W`。
- card: `acp:kanban-issue:ki-b02c57db9de8`(盤 agora-redesign・p2)。card は増やさず、記録はこの card のスレッドへ書く。
- 設計本文: 同じ dir の `design.md`(**§10 を先に読むこと** — 盲検の反例を受けた修正 R1〜R3 が §4 の決定を改めている)。
- 基準 commit: doeff `5750826265f65e4e6727d6e00bebf76b97cb28b3`。
- 触る repo: **doeff だけ**。dotfiles の `land.py` は触らない。
- 試作の差分(そのまま出発点に使ってよい): `evidence/REV-tracked.patch`
  (sha256 `e8a67b50b455b0018e2bf77d9fa9c0ccdbe3f9d5e422c63a2a99e88a17356fe7`・基準 commit に `git apply` で当たる)。

## 1. 結合核の突き合わせの結論

- 名簿: dotfiles origin/master `docs/coupling-core-watchlist.md` の `coupling-core-paths`(119 pattern)と
  `coupling-core-fleet-paths`(1 pattern = `.agents/land-queue.toml`)。doeff は repo 固有の名簿を持たない。
- 触る 5 path(`conftest.py`・`tests/test_effectful.py`・`tests/test_bytecode_settings_pinned.py`(新規)・
  `packages/doeff-hy/tests/test_source_positions.py`・`packages/doeff-hy/tests/test_none_type_contract.py`)と、
  この dir(`docs/design/effectful-cache-tests-bytecode-ki-b02c57db9de8/`)を突き合わせて **当たり 0 件**
  (`evidence/coupling_core_match.log`)。
- ⇒ 結合核に触る変更の扱い(上位の model と人で、法・反例・テスト・実装をまとめて出す)は掛からない。
  触る path が増えたら突き合わせをやり直すこと。

## 2. 確定した決定

| # | 決定 | 戻し方 |
| --- | --- | --- |
| D1 | `test_cache_is_named_by_the_rewrite_version_and_reused` と `test_a_new_rewrite_version_rebuilds_the_cache` は、本文の先頭で `monkeypatch.setattr(sys, "dont_write_bytecode", False)` を置く | 行を消す |
| R1 | キャッシュの検査は置き場を `rewrite.cache_path(str(source))` に聞く。置き場と名前の契約(`package / "__pycache__" / f"mod.{cache_tag}-doeff-effectful-{REWRITE_VERSION}.pyc"`)を確かめるのは `test_cache_is_named_by_the_rewrite_version_and_reused` の 1 か所だけ。版での作り直しの 1 本は `"next-version" in rebuilt.name` と `rebuilt.is_file()` だけを見る | 検査を元の形へ |
| D2 + R3 | 新しい 1 本 `test_no_cache_is_written_while_bytecode_writing_is_off`: 対照として flag を偽にして同じ置き場へ書かれることを先に示し、消してから flag を真にして 2 回 import し、キャッシュが無いこと・一時 file が残らないこと・2 回とも compile したことを確かめる | 検査を消す |
| D3 + R2 | root `conftest.py` に session 単位の autouse fixture `_bytecode_settings_pinned`: `pytest.MonkeyPatch.context()` で `sys.dont_write_bytecode = True`・`sys.pycache_prefix = None`・`PYTHONDONTWRITEBYTECODE=1`・`PYTHONPYCACHEPREFIX` を消す。終わりに 2 つの値が `(True, None)` でなければ走行を赤にする(assert)。守る検査 `tests/test_bytecode_settings_pinned.py` の 2 本(本文・子 process) | fixture と検査 file を消す |
| D4 | doeff-hy の module fixture 2 つは、入る前の `sys.dont_write_bytecode` を保存し、`finally` でその値へ戻す(無条件の `False` をやめる) | 元の 1 行へ |
| — | `doeff/_effectful_rewrite.py` は変えない(書かない判断は正しい)。land.py も変えない | — |
| — | `sys.dont_write_bytecode` への直接の代入を禁じる semgrep 規則は足さない(semgrep の範囲が `tests/` に届かないため。`design.md` §4.2) | — |

選んだ理由(1 行): 答えが環境で割れた根は「検査が前提を黙って置いたこと」と「開発機と日次で bytecode の設定が違い、
書き忘れが作者の機体で見えないこと」の 2 つなので、前者は各検査の中で、後者は検査の走行の側で Python の bytecode の
設定 2 つを揃えて直す。

## 3. 未確定事項

- 決めることは残っていない。comment の言い回しと、`conftest.py` の中で fixture を置く位置(試作は末尾)は実装者に任せる。
- ⚠ doeff の Verification Contract(`CLAUDE.md`)に従い、報告には下の受入条件 1 つずつに、撃った命令と記録
  (または検査名)を対応させた表を付ける。置き換え・弱めた所があれば「Verification deviations」として明記する。

## 4. 手順

1. 専用の置き場に worktree を作る(`~/.worktrees/doeff-wt-<slug>`・`origin/main` から)。
2. この dir(`docs/design/effectful-cache-tests-bytecode-ki-b02c57db9de8/`)は branch
   `design/ki-b02c57db9de8`(origin に push 済み)の commit に在る。その commit を実装の branch へ取り込む
   (設計の記録を実装と一緒に本線へ入れる)。
3. doeff の TDD の順で commit する:
   1. 失敗する検査を先に: `tests/test_bytecode_settings_pinned.py`(固定が無いので開発機で赤)と
      `tests/test_effectful.py` の変更(D1・R1・D2 + R3)。`PYTHONDONTWRITEBYTECODE=1` で撃つと、D1 を足す前の
      2 本が赤だったことも記録に残す。
   2. 実装: `conftest.py` の固定(D3 + R2)と doeff-hy の 2 file(D4)。
   試作の差分 `evidence/REV-tracked.patch` を当てれば 2 の状態になる(1 の commit は、その中から検査の file だけを先に入れる)。
4. 手元の検証(全体の走行はしない — 日次に任せる。下はすべて触った file に絞った走行):
   - 4 通りの env(`PYTHONDONTWRITEBYTECODE` あり / なし × `PYTHONPYCACHEPREFIX=<一時 dir>` あり / なし)で
     `uv run pytest tests/test_effectful.py tests/test_bytecode_settings_pinned.py packages/doeff-hy/tests/test_source_positions.py packages/doeff-hy/tests/test_none_type_contract.py -q`
   - ミュータント(当てて撃ち、`git apply -R` で戻して `git diff --stat -- doeff/` が空であることを確かめる):
     `evidence/M1-write-cache-ignores-flag.patch`・`evidence/M3-cache-follows-pycache-prefix.patch`
   - `ruff check`・`ruff format --check`・`semgrep --config .semgrep.yaml --error`・`pyright` を触った 5 file に。
     pyright の既存の 3 件(`evidence/pyright-base.log`)以外が出ないこと。format は基準版から在る差分だけ残ってよい。
5. land queue で着地させる(doeff の `.agents/land-queue.toml` の通常の経路)。本線に入った commit の sha を報告に書く。

## 5. 受入条件

| # | 条件 | 確かめ方 |
| --- | --- | --- |
| 1 | 対照: 基準 commit では `PYTHONDONTWRITEBYTECODE=1` で 2 本が赤、実装後は同じ命令で緑 | `PYTHONDONTWRITEBYTECODE=1 uv run pytest tests/test_effectful.py -q -k "cache or rebuild"` を両方の版で撃ち、要約行を貼る |
| 2 | 4 通りの env で、触った 4 file の走行がすべて緑 | 手順 4 の 1 つ目 |
| 3 | 書かない判断を消すミュータント(M1)で、4 通りの env のどれでも `test_no_cache_is_written_while_bytecode_writing_is_off` だけが赤 | 手順 4 の 2 つ目 |
| 4 | 置き場を prefix に従わせるミュータント(M3)で、4 通りの env のどれでも緑 | 同上 |
| 5 | 2 本のどちらかから D1 の行を消すと、env 無し(開発機)でもその検査が赤 | 一時的に行を消して撃ち、戻す |
| 6 | D4 を戻す(doeff-hy が値を戻さない)と、`packages/doeff-hy/tests/test_source_positions.py tests/test_bytecode_settings_pinned.py` の走行が赤(守る検査の failed と終わりの確かめの error) | 一時的に戻して撃ち、戻す |
| 7 | 触った 5 file の ruff check・semgrep が 0 件、pyright は既存の 3 件だけ | 手順 4 の 3 つ目 |
| 8 | `doeff/_effectful_rewrite.py` と dotfiles の `land.py` に差分が無い | `git diff --stat origin/main -- doeff/` |
| 9 | 本線に入り、着地の翌日 03:30 JST の doeff の日次で 2 つの名前が失敗名から消える | 依頼者(計画段の会話)が日次の記録で確かめる。実装者は本線の sha まで報告すればよい |

報告は、この依頼への `ai reply <依頼の郵便 id> --kind report` で出す。途中の経過は note でよい。
