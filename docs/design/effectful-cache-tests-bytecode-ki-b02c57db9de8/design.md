# @effectful のキャッシュの検査 2 本が日次の全体検証で必ず赤になる件 — 設計

- 依頼: `lt-64Y1D2GNNSNFQ1YBY6QPW4ZY50`(class investigate・依頼者 `c-D6AFCPB1VRMNTVN9ECSZMCAS3T`)
- card: `acp:kanban-issue:ki-b02c57db9de8`(盤 agora-redesign・p2)
- 著者: 会話 `c-ZSX5HW937JVHSG80KE6JPCKC1W`・model claude-opus-5-5・effort 既定(起動口に指定の欄が無い)
- 基準 commit: doeff `5750826265f65e4e6727d6e00bebf76b97cb28b3`(2026-09-24 の origin/main)
- 完了の範囲: **設計まで**。実装は別の依頼(class dev)で行う。下の試作はこの dir の
  `evidence/FINAL-tracked.patch` に在り、設計用の worktree で最小実験に使っただけで本線には入っていない。

§1〜§9 は盲検の反例を受け取る前に固定した本文。盲検を受けた修正は §10 に足し、§1〜§9 は書き換えない
(同じ本文の写し = `design-before-blind.md`、hash は §10 に記す)。

## 1. 固定した要件(card の「望む状態」)

1. 検査 2 本が、自分の前提(bytecode を書ける)を検査の中で立てる。どの環境でも同じ答えになる。
2. `sys.dont_write_bytecode` が真の時にキャッシュを書かないこと自体を、別の 1 本で守る。
3. dotfiles `agentcli/src/agentcli/land.py` の `_hygiene_env` が渡す `PYTHONDONTWRITEBYTECODE=1` は変えない。
4. 受入: 着地の翌日 03:30 JST の doeff の日次で 2 つの名前が失敗名から消える。対照として
   `PYTHONDONTWRITEBYTECODE=1` を付けた手元の実行で、直す前は 2 本赤・直した後は緑を示す。
5. (依頼書から)同じ前提を黙って置いている検査を doeff で 1 度だけ走査し、在れば同じ実装の依頼に入れる。

## 2. 実測 — 仕組みの再現

依頼者の説明どおりだった。捨てる仮説は無い。

| 実行 | 結果 | 記録 |
| --- | --- | --- |
| 基準版・`PYTHONDONTWRITEBYTECODE=1` | 2 本赤(333 行・351 行の `is_file()`) | `evidence/repro-before-env1.log`・`evidence/E3-base-env1.log` |
| 基準版・env 無し | 2 本緑 | `evidence/E3b-base-envunset.log` |
| 基準版・`-n 4`・env=1 | 2 本赤 | `evidence/E7-base-xdist-env1.log` |

- `doeff/_effectful_rewrite.py` の `_write_cache` は `sys.dont_write_bytecode` が真なら書かない(379 行)。正しい振る舞い。
- land の `_hygiene_env`(dotfiles origin/master 380〜408 行)が機構の子へ足す env は `PYTHONDONTWRITEBYTECODE=1` だけ
  (ほかは席の同一性を運ぶ env を落とすだけ)。日次の走行は `.agents/land-queue.toml` の `[gate].full`
  = `uv run --no-sync pytest -q -m 'not e2e'`(pyproject の testpaths)。

## 3. 走査の結果(同じ前提を置いている検査)

範囲: `.venv`・`target` を除く repo 全体の py / hy / hyk / hyp / rs / toml / yaml / sh / Makefile。
語: `__pycache__`・`.pyc`・`cache_tag`・`dont_write_bytecode`・`PYTHONDONTWRITEBYTECODE`・`py_compile`・`compileall`・
`cache_from_source`・`bytecode`・`marshal`・`cache_path(`・`_write_cache`・`SourceFileLoader`・`MAGIC_NUMBER`・`get_code`・
`__cached__`・`pycache_prefix` ほか。

- 同じ前提(書かれることを黙って期待する)で落ちる検査: **既知の 2 本だけ**。
- 別の問題を 1 つ見つけた: `packages/doeff-hy/tests/test_source_positions.py:73,78` と
  `packages/doeff-hy/tests/test_none_type_contract.py:57,62` の module 単位の fixture が、import の間だけ
  `sys.dont_write_bytecode = True` にしたあと、元の値に戻さず **無条件に `False`** を入れる。
  同じ process で後ろに走る検査は、env が 1 でも書く状態になる。
  実測: 基準版・env=1 で `packages/doeff-hy/tests/test_source_positions.py tests/test_effectful.py` を
  1 本の走行にすると、赤のはずの 2 本が **緑になる**(34 passed — `evidence/E11a-base-hy-then-effectful-env1.log`)。
  この 2 file は testpaths の外なので日次の走行には入らないが、開発機で並べて走らせると失敗が隠れ、
  被験の木へ `__pycache__` を書かないという land の守りも外れる。

## 4. 設計

### 4.1 決定

| # | 決定 | 満たす要件 |
| --- | --- | --- |
| D1 | 検査 2 本は、本文の先頭で `monkeypatch.setattr(sys, "dont_write_bytecode", False)` を置いて前提を自分で立てる | 要件 1 |
| D2 | 新しい 1 本 `test_no_cache_is_written_while_bytecode_writing_is_off`: 自分で `True` を立て、import が同じ値を返すこと・キャッシュの file も `__pycache__` も作られないこと・2 回目の import も compile し直すことを確かめる | 要件 2 |
| D3 | root の `conftest.py` に session 単位の autouse fixture `_bytecode_writing_off` を置く。収集の後から終わりまで `sys.dont_write_bytecode = True` と `PYTHONDONTWRITEBYTECODE=1` を立て、終わりに戻す。終わりの時点で値が `True` に残っていなければ走行を赤にする。固定が効いていることを `tests/test_bytecode_writing_off.py` の 2 本で守る | 再発の防止(開発機の答え = 日次の答え) |
| D4 | doeff-hy の fixture 2 つは、入る前の値を保存して `finally` でその値へ戻す | 要件 5(走査で見つけた同族) |
| — | land.py は変えない | 要件 3 |

**選んだ理由(1 行)**: 答えが環境で割れた根は「検査が前提を黙って置いたこと」と「開発機の走行と日次の走行で
この設定が違い、書き忘れが作者の機体で見えないこと」の 2 つなので、前者を各検査の中で(D1・D2)、後者を
走行の側で日次と同じ値に揃えて(D3)直す。

### 4.2 退けた案

| 案 | 退けた理由 |
| --- | --- |
| land.py で `PYTHONDONTWRITEBYTECODE` を外す | 被験の木を汚さない守りを外す(要件 3 に反する) |
| `_write_cache` が `sys.dont_write_bytecode` を無視する | 標準の importer と違う振る舞いになり、読み取り専用の木や land の守りに反する(要件 2 に反する) |
| 前提を `tmp_package` fixture に入れる | 前提の要らない検査(import error・effectful の無い module)にも黙って効き、書かない側の 1 本が fixture の値を上書きする形になる。前提は各検査の本文に在るほうが読める |
| 固定(D3)を関数単位にする | module・session 単位の fixture が固定の外に出る。実測で、module 単位の fixture が書くことを前提にした検査は、関数単位の固定だと開発機で緑・日次の設定で赤になる(`evidence/F-ctl-module-fixture-function-pin-envunset.log` 緑・`...-env1.log` 赤)。session 単位なら開発機でも赤(`evidence/F-neg-module-fixture-session-pin-envunset.log`) |
| 固定を収集の前(`pytest_configure`)から立てる | 開発機でも収集と pytest の assertion の書き換えが bytecode を書かなくなり、編集した module を毎回 compile し直す。収集の中で書くことを前提にする検査は無い(走査の結果)ので、収集は固定の外に置く(§8 の限界) |
| `sys.dont_write_bytecode` への直接の代入を semgrep で禁じる | semgrep の検査範囲(`make lint-semgrep` と `tests/test_semgrep_gate.py` = `doeff/ packages/`)が `tests/` に届かず、範囲を広げると 273 本の規則すべてが `tests/` を読むことになる。代わりに D3 の終わりの確かめで、値を戻さない fixture を振る舞いで赤にする |

## 5. 責務

| id | 何を持つか | 隠すもの | 公開の形 | 副作用 | 寿命 | 守る不変条件 |
| --- | --- | --- | --- | --- | --- | --- |
| effectful-cache(`doeff/_effectful_rewrite.py` の `cache_path` / `_read_cache` / `_write_cache` / `EffectfulLoader.get_code`)— **変えない** | 書き換えた bytecode の保存と再利用・書く / 書かないの判断・名前の付け方 | file の中身の形(header・marshal) | `cache_path(source_path)`・名前 `__pycache__/<stem>.<cache_tag>-doeff-effectful-<REWRITE_VERSION>.pyc` | `__pycache__` の読み書き。書けない木では黙って書かない | import 1 回 | `sys.dont_write_bytecode` が真なら書かない・壊れた cache は捨てて compile |
| cache-tests(`tests/test_effectful.py` のキャッシュの 3 本と `tmp_package`) | 名前・再利用・版での作り直し・書かない振る舞いの確認。各検査の前提(flag の値) | なし | 検査名 3 つ | `tmp_path` の下だけに書く。flag は monkeypatch で変えて戻す | 検査 1 本 | 答えが走行環境の flag・env に依らない |
| suite-pin(root `conftest.py` の `_bytecode_writing_off` と `tests/test_bytecode_writing_off.py`) | 走行中の flag と env を日次と同じ値にすること | なし | 検査が前提を変えたい時は monkeypatch で上書きする(戻しは pytest) | process の flag と `os.environ` を session の間だけ変えて戻す | pytest の session(xdist では worker ごと) | 開発機の走行と日次の走行でこの設定の値が同じ・終わりに値が戻っている |
| hy-probe-fixtures(doeff-hy の module fixture 2 つ) | `.hy` の probe を bytecode を書かずに import する | なし | fixture `mod` | import の間だけ flag を真にして元へ戻す | module | fixture の後の flag = 前の flag |
| land-env(dotfiles `land.py` の `_hygiene_env`)— **外部・変えない** | 機構の子の env に `PYTHONDONTWRITEBYTECODE=1` | なし | 子の env | なし(値を作るだけ) | 走行 1 回 | 被験の木に `__pycache__` を書かせない |

## 6. 変更シナリオと事前の主張(盲検の前に固定)

| id | 軸 | 変更 | 主張 | 予想する範囲(変わる / 変わらない) |
| --- | --- | --- | --- | --- |
| S1 | distribution | 日次の走行場所が変わる(zeus の遠隔実行 ↔ この機体への fallback ↔ 開発機)。場所ごとに `PYTHONDONTWRITEBYTECODE` の有無が違う | キャッシュの 3 本と固定の 2 本の答えは走行場所の env に依らず同じ。前提の書き忘れは開発機でも赤になる | 変わる: なし / 変わらない: 全部 |
| S2 | storage | キャッシュの名前の付け方を変える(例: `-doeff-effectful-` の綴り) | 変わるのは effectful-cache の `cache_path` と、名前を公開の形として確かめる cache-tests の 2 本。書かない側の 1 本(`rewrite.cache_path` を読む)・固定・land-env は変わらない | 変わる: effectful-cache・cache-tests(2 本) / 変わらない: suite-pin・hy-probe-fixtures・land-env |
| S3 | concurrency | 日次を xdist の並列(`-n N`)で走らせる | 変更なし。固定は worker の process ごとに立ち、各検査は自分の `tmp_path` だけを使う | 変わる: なし / 変わらない: 全部 |
| S4 | effects | キャッシュを書かない条件を 1 つ足す、または今の条件を落とす | 変わるのは effectful-cache と、その条件を確かめる cache-tests の 1 本。前提を立てた 2 本・固定・land-env は変わらない。今の条件を落とす変更は書かない側の 1 本が赤で止める | 変わる: effectful-cache・cache-tests / 変わらない: suite-pin・hy-probe-fixtures・land-env |
| S5 | hardware | (適用しない) | 書く / 書かないの判断は process の flag と木の書き込みの可否だけで決まり、機体の違いは env(S1)を通してしか入らない。Python の版の違いは `cache_tag` に出るが、検査は `sys.implementation.cache_tag` を読んで名前を組む | — |
| S6 | simulation | (適用しない) | キャッシュは import の副産物で、doeff の program の意味(effect・handler・scheduler)に入らない。決定的シミュレーションの対象になる状態を持たない | — |

## 7. 強制の方法

| 守る責務 | 強制の方法と選んだ理由 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 検査の答えが走行環境の flag に依らない(cache-tests) | suite-pin: 走行中の値を日次と同じにするので、前提を書き忘れた検査は作者の機体で赤になる。検査ごとの静的な規則では fixture・helper・subprocess の形を拾い切れないので、振る舞いで揃える | `conftest.py` の `_bytecode_writing_off`・`tests/test_bytecode_writing_off.py` | 開発機の pytest 全般・日次の `pytest -q -m 'not e2e'`(testpaths) | 収集の中の書き込みは固定の外。root 以外を rootdir にした走行(package 単独)は外。固定を消しても日次は env で緑のままで、固定を守る 2 本は開発機でだけ効く |
| flag が真なら書かない(effectful-cache) | 新しい検査 1 本。条件を消すミュータントで赤になることを確かめた | `tests/test_effectful.py::test_no_cache_is_written_while_bytecode_writing_is_off` | 同上 | 読み取り専用の木で書かない振る舞いは対象外(今の検査にも無い) |
| flag を変えたら戻す(全検査) | suite-pin の終わりの確かめ(値が `True` でなければ走行を赤)。doeff-hy の 2 つは保存と戻し | `conftest.py`・doeff-hy の 2 file | 同上 | 赤は最後の検査の teardown の error として出るので、どの fixture が戻さなかったかは名指ししない。途中で戻さず終わりまでに戻す形は捕まえない |
| land の守りは変えない(land-env) | 触らない | — | — | — |

型の検査・import の向き・小さい interface の検討: 変えるのは検査と検査の枠だけで、公開 API・型・import の向きに
触れない(型で表す契約が増えない)。万能の service や責務の集中は生まれない。

## 8. 最小実験(設計段で実行したもの)

走行の口: `evidence/run.sh <label> <1|unset> <pytest の引数>`(設計用の worktree・基準 57508262・
`PYTHONPATH` を worktree に向け、main checkout の venv の python で pytest を撃つ)。各 log の頭に
基準版・env・差分の hash・命令を書く。最終の差分 = `evidence/FINAL-tracked.patch`
(sha256 `506c6e5e8cd15860283e4eb72920e97797c033815d3fb92ec0a38068fd9aefbe`)。

| 記録 | 条件 | 結果 |
| --- | --- | --- |
| `F-pos-all-env1.log` / `F-pos-all-envunset.log` | 最終の差分・`tests/test_effectful.py tests/test_bytecode_writing_off.py` | どちらも 33 passed |
| `F-pos-xdist-env1.log` / `F-pos-xdist-envunset.log` | 同上・`-n 4` | どちらも 33 passed |
| `F-neg-pin-without-P1-envunset.log` | 固定あり・D1 なし・env 無し(開発機) | 2 本赤 — 前提の書き忘れが開発機で見える |
| `F-neg-module-fixture-session-pin-envunset.log` | 書くことを前提にした module fixture の probe・session 固定・env 無し | 赤 |
| `F-ctl-module-fixture-function-pin-envunset.log` / `...-env1.log` | 同じ probe・関数単位の固定 | env 無しで緑・env=1 で赤(関数単位だと割れる) |
| `F-neg-mutant-M1-env1.log` | `_write_cache` の flag の判断を消すミュータント(`M1-write-cache-ignores-flag.patch`) | 新しい 1 本だけ赤・2 本は緑 |
| `F-scope-mutant-M2-env1.log` | 名前の綴りを変えるミュータント(`M2-cache-name-changes.patch`) | 名前を確かめる 2 本だけ赤・31 passed |
| `F-neg-leak-detected-env1.log` | D4 を外した(doeff-hy が値を戻さない)走行 | 終わりの確かめが teardown の error で赤 |
| `F-pos-hy-effectful-env1.log` | 最終の差分・doeff-hy 2 file + 上の 2 file | 41 passed |
| `E11b-P3only-hy-then-effectful-env1.log` | D4 だけ当てた走行・env=1 | 2 本赤(隠れていた赤が見える) |
| `F-lint.log` | ruff check・ruff format・pyright・semgrep(変えた 5 file) | ruff check 0・semgrep 0・pyright は基準版と同じ既存の 3 件(`pyright-base.log`)・format の差分は基準版から在る行だけ |
| `coupling_core_match.log` | 触る 5 path と結合核の名簿(dotfiles origin/master) | 当たり 0 |

全体の走行はしていない(日次に任せる)。

## 9. 未確認事項(盲検の前)

- 日次の実走(zeus の遠隔実行)で env がそのまま子へ渡ること自体は、依頼者の実測(走行の記録
  `doeff-verify-20260924-033008.log`)に依る。この席では日次の記録を読んでいない。
- 固定(D3)を足した後の全体の走行の答えは、着地後の日次で確かめる(全体の走行は日次だけ)。
  根拠: 日次は既に全体を `PYTHONDONTWRITEBYTECODE=1` で走らせており、赤は 2 本 + semgrep の 1 本だけだった。
  固定は同じ値を収集の後から立てるだけなので、日次の答えは変わらない見込み。

## 10. 盲検の反例と設計の修正(2026-09-23 20:11〜20:50Z)

§1〜§9 は盲検の前の主張として変えない(同じ本文 = `design-before-blind.md`・sha256
`d00a5c4ff7e1c35a3ba2d14c2243f58e9982a8d1c5abca0f917328f6f7a36bc4`)。起動の記録 = `blind/blind-meta.md`
(gpt-6-astra は起動口が断り・Fable は利用枠の上限で途中終了・opus 段で A・B を完了。effort は指定できず未確認)。
入力 = `blind/blind-input.md`、返答(未加工)= `blind/blind-a-return.md`・`blind/blind-b-return.md`。

### 10.1 反例と再現

| id | 出所 | 反例 | 再現(設計者が撃ち直した記録) | 判定 |
| --- | --- | --- | --- | --- |
| A-1 / B-1 | 盲検 A・B(別々に同じ所へ到達) | キャッシュの置き場を標準の `sys.pycache_prefix` に従わせる(`cache_path` を `importlib.util.cache_from_source(...)` の隣へ)。2 本は置き場を `package / "__pycache__" / ...` と自分で組み立てているので、`PYTHONPYCACHEPREFIX` のある機体でだけ赤になり、開発機でも日次(どちらも prefix なし)でも緑のまま。固定(D3)は `dont_write_bytecode` しか揃えていない | `evidence/M3-cache-follows-pycache-prefix.patch` を当てて 4 通りの env で撃った(`checks/C07-...txt`): prefix あり = 2 failed(DWB の有無に依らず)・prefix なし = 33 passed | **成立**。S1(「答えは env に依らない」)と要件 4 が破れる。S2 の予想範囲(effectful-cache・cache-tests の 2 本)も外れた — 直すには固定も変える必要があった |
| A-2 | 盲検 A の副次の観測 | 置き場が書けない時(prefix の下で `mkdir` が OSError を黙って捨てる)、書かない側の 1 本は「判断して書かなかった」と「書けなかった」を区別できず、flag の判断を消したミュータントを見逃す | `M3b`(`parents=True` なし)+ `M1`(判断を消す)で撃った(`checks/C13-...txt`): prefix あり = 1 passed(見逃し)・prefix なし = 1 failed | **成立**。S4 の「条件を落とせば 1 本が赤」は、書き込み先に書けることを暗黙に前提していた |
| A-3 | 盲検 A の解釈 | 固定の側が「どの process 全体の設定をキャッシュが読むか」を写し持つので、キャッシュが読む設定を増やすと固定が黙って古くなる | A-1 と同じ実測 | 一部成立。固定が写すのを「キャッシュが読む設定」ではなく「Python が定める bytecode の設定 2 つ」にすれば、キャッシュの内側の知識は写さずに済む(10.2 の R2) |
| B-2 | 盲検 B の手がかり(具体化なし) | 固定は収集の後から始まるので、収集の中の書き込み(例: `tests/effectful_cases/__pycache__/programs.*-doeff-effectful-*.pyc`)に依る検査は同じ抜け道になりうる | 具体化されていない。走査(§3)で収集の中の書き込みに答えが依る検査は見つかっていない | 未成立(具体例なし)。§7 の限界として残す |

### 10.2 修正

| # | 修正 | 直す反例 |
| --- | --- | --- |
| R1 | キャッシュの検査は置き場を `rewrite.cache_path(...)`(置き場を決める持ち主)に聞く。置き場と名前の契約(`__pycache__/<stem>.<cache_tag>-doeff-effectful-<版>.pyc`)を確かめるのは `test_cache_is_named_by_the_rewrite_version_and_reused` の 1 か所だけにする。版での作り直しの 1 本は「新しい版の名前が付いていること」(`"next-version" in rebuilt.name`)だけを見る | A-1 / B-1(置き場の知識の写しを 1 か所へ) |
| R2 | 固定(D3)は Python の bytecode の設定 2 つを両方揃える: 書くか(`sys.dont_write_bytecode = True`・`PYTHONDONTWRITEBYTECODE=1`)と置き場(`sys.pycache_prefix = None`・`PYTHONPYCACHEPREFIX` を消す)。終わりの確かめも 2 つを見る。名前を `_bytecode_settings_pinned`、守る検査を `tests/test_bytecode_settings_pinned.py` に改めた | A-1 / B-1・A-3(機体の env でも将来の変更でも答えが割れない) |
| R3 | 書かない側の 1 本は、同じ置き場へ flag が偽なら書かれることを先に示してから(対照)、flag を真にして書かれないことを確かめる。一時 file の残りも見る | A-2 |

D1(2 本が前提を立てる)・D2(書かない側の 1 本)・D3(session 単位の固定と終わりの確かめ)・D4(doeff-hy の戻し)は
残る。試作の最終の差分 = `evidence/REV-tracked.patch`
(sha256 `e8a67b50b455b0018e2bf77d9fa9c0ccdbe3f9d5e422c63a2a99e88a17356fe7`)。

### 10.3 再検証(修正後の差分・`evidence/checks/` の束)

| 束 | 条件 | 結果 |
| --- | --- | --- |
| C01 | 修正後・DWB × prefix の 4 通り | どれも 33 passed |
| C02 | 基準版・DWB あり / なし | 2 failed / 2 passed(元の欠陥) |
| C03 | 修正後から rebuild の 1 本の前提行を消す・開発機の env | 1 failed(書き忘れが開発機で見える)。修正前の版での同じ確かめ(`F-neg-pin-without-P1`)も同梱 |
| C04 | module fixture が書くことを前提にする probe | session 固定・開発機 = 赤 / 関数単位の固定 = 開発機で緑・DWB=1 で赤(関数単位だと割れる) |
| C05 | 値を戻さない fixture | prefix を戻さない probe = 終わりの確かめが error / doeff-hy を戻さない版 = 守る検査が failed + 終わりの error。基準版で doeff-hy が赤を隠す実測(E11a)も同梱 |
| C06 | 修正後 + M3(置き場が prefix に従う)・4 通り | どれも 33 passed(置き場の変更が検査へ波及しない) |
| C07 | 修正前 + M3・4 通り | prefix あり 2 failed / なし 33 passed(反例の再現) |
| C08 | 修正後から R2 だけ外した版 + M3・4 通り、と M2(名前の綴りを変える) | prefix ありで赤は名前の検査 1 本 + 守る検査 2 本(固定が外れたのを捕まえる)。作り直しの 1 本と書かない側の 1 本は緑。M2 で赤は名前の検査 1 本だけ(修正前は 2 本) |
| C09 | 修正後・`-n 4`・4 通り | どれも 33 passed |
| C10 | 基準版・`-n 4`・DWB=1 | 2 failed |
| C11 | 修正後・doeff-hy 2 file + 2 file・4 通り | どれも 41 passed |
| C12 | 修正後 + M1(書かない判断を消す)・4 通り | どれも書かない側の 1 本だけ failed |
| C13 | 修正前 + M3b + M1 / 修正後 + M3b + M1 / R2 を外した版 + M3b(± M1) | 修正前は prefix ありで見逃し(1 passed)。修正後は prefix ありでも捕まえる(固定で prefix が消えるため)。R2 を外しても、対照の行(378 行 `assert cached.is_file()`)で「ここには書けない」と赤になり、見逃さない |
| C14 | ruff check・ruff format・pyright・semgrep(触る 5 file) | ruff check 0・semgrep 0・pyright は基準版と同じ既存の 3 件・format の差分は基準版から在る行だけ |

### 10.4 事前の予測との差

| シナリオ | 予測(§6) | 実測 | 差の原因 |
| --- | --- | --- | --- |
| S1 | 変わる module なし | 修正前: prefix のある機体で割れる(A-1)。修正後: 4 通りの env で同じ答え(C01・C06) | 固定が 2 つの設定の片方しか揃えていなかった(隠すはずの知識の漏れではなく、固定の責務の定義の不足)。R2 で直した |
| S2 | effectful-cache・cache-tests(2 本) | 修正前: 置き場の変更で固定も直す必要があった。修正後: 置き場の変更(M3)は検査へ波及しない(C06)。名前の変更(M2)は名前の検査 1 本だけ(C08) | cache-tests が置き場の知識を 2 か所に写していた(漏れ)。R1 で持ち主に聞く形へ直し、予測より狭くなった |
| S3 | 変化なし | 変化なし(C09・C10) | — |
| S4 | effectful-cache・cache-tests の 1 本。条件を落とせば 1 本が赤 | 修正前: 書けない置き場では見逃した(A-2)。修正後: どの env でも捕まえる(C12)。書けない置き場では対照の行で赤(C13) | 書かない側の 1 本が「書ける置き場」を暗黙に前提していた。R3 で検査の中の対照に直した |

### 10.5 強制の方法(修正後)

| 守る責務 | 強制の方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 検査の答えが走行環境の bytecode の設定に依らない | 固定(Python の bytecode の設定 2 つを session の間揃える)。前提を書き忘れた検査は作者の機体でも日次と同じ答え(赤)になる | root `conftest.py` の `_bytecode_settings_pinned` | 開発機の pytest 全般・日次の `pytest -q -m 'not e2e'` | 収集の中は固定の外(B-2)。root 以外を rootdir にした走行は外。Python 標準の外の設定(doeff 独自の env 等)を将来足すなら、その設定を読む検査が自分で立てる |
| 固定が効いている | `tests/test_bytecode_settings_pinned.py` の 2 本(本文と子 process) | 同左 | 同上 | 固定を消しても日次は env で DWB の側だけ緑になりうる(prefix の側は日次でも赤) |
| 設定を変えた検査は戻す | 固定の終わりの確かめ(2 つの値が戻っていなければ走行を赤) | `conftest.py` | 同上 | 赤は最後の検査の teardown の error として出て、どの fixture かは名指ししない |
| 置き場を決める知識は effectful-cache だけが持つ | キャッシュの検査は `rewrite.cache_path()` に聞く。置き場と名前の契約の確かめは 1 か所 | `tests/test_effectful.py` | 同上 | 静的な強制は無い(新しい検査が置き場を自分で組んでも止まらない)。固定で prefix が消えるので、そうした検査も環境で割れはしない |
| flag が真なら書かない | 書かない側の 1 本(対照つき) | 同上 | 同上 | 読み取り専用の木で書かない振る舞いは対象外 |

### 10.6 未確認事項(盲検の後)

- 日次の実走で env がそのまま子へ渡ることは依頼者の実測に依る(§9 と同じ)。
- 固定を足した後の全体の走行の答えは着地後の日次で確かめる。R2 で `PYTHONPYCACHEPREFIX` を消すのは、日次の宿に
  その env が無ければ何も変えない(この機体・盲検 A・B の機体とも無し — 実測)。日次の宿(zeus・pod)の env は未確認。
- 盲検の実 model と effort は自己申告だけ(`blind/blind-meta.md`)。
