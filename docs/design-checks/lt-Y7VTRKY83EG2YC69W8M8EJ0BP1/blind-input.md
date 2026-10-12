# 盲検の入力 — 設計と主張 — doeff-hy の Hy の上限(依頼 lt-Y7VTRKY83EG2YC69W8M8EJ0BP1・card ki-0ff168f4eaf5)

## 完了の範囲
- 実装完了まで。実装は doeff main e71995df(L2157・基底 74873132f)に着地済み。
- 盲検の前の主張は design-pre-blind.md の commit で固定した。

## 固定した要件(card ki-0ff168f4eaf5 の受入)
- c18579d29: 上限を置く案と置かない案を、doeff・agora-controllers・merge-queue の解決と版上げの手順への影響で比べ、card に書く。
- cf9c6c9b8: 比べから案を 1 つ選び、選んだ案を main へマージするか、置かない訳を card に書く。
- 背景: doeff-hy の宣言は `hy>=1.2.0` で上限が無く、他の repo が自分の lock で Hy を上げる道は doeff の検(ADR-DOE-HY-008 R4 — doeff の uv.lock の版で hy.* の名を照合)に入らなかった。

## 選んだ設計
doeff-hy の Hy の依存の上限を、doeff の uv.lock の hy の版ちょうど(`<=<版>`)にする。今は `hy>=1.2.0,<=1.3.1`。上限と uv.lock の食い違いを検 R6 が赤にする。

## 責務(module)
| id | 責務 | 持つ知識 | 隠す知識 | 公開の形 | effect | 寿命 | 守る不変条件 |
|---|---|---|---|---|---|---|---|
| M-cap | doeff-hy が環境に入れてよい Hy の範囲を宣言する(packages/doeff-hy/pyproject.toml の dependencies の hy の行) | Hy の上限と下限 | なし(宣言は公開の metadata) | PEP 508 の requirement。uv の解決が読む | なし — 静的な metadata(他の repo の uv の解決にだけ効く) | doeff の commit ごと | 上限 = M-lock の版ちょうど。上限は doeff-hy の 1 か所だけ |
| M-lock | doeff が検で確かめる Hy の版を 1 つに決める(doeff の uv.lock の hy の行) | 確かめる Hy の版 | なし | uv.lock の `[[package]] name = "hy"` の version と source | なし | doeff の commit ごと | registry(PyPI)から入れる版が 1 行だけ(R3・locked-hy-version) |
| M-check | 上限と lock・hy.* の名の照合を判じる(docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy の R4・R6) | 判定の規則(R4: doeff が import する hy.* の名が lock の版に在る・R6: 上限 = lock の版) | 走査の絞り込み・使い捨ての環境の作り方 | deftest 2 本(test-adr-doe-hy-008-imports-exist-in-pypi-hy・test-adr-doe-hy-008-doeff-hy-caps-hy-at-the-locked-version)と判定の defk(hy-cap-violations ほか) | file の読み・R4 だけ uv の使い捨ての環境の子 process | 検の 1 回の実行 | 上限・lock・名の 3 つが同じ版を指す |
| M-select | 変えた path から登記の前に走らせる検を選ぶ(root の pyproject.toml の [[tool.doeff.contract-tests]] と scripts/run_changed_tests.py) | path の接頭辞 → 検の対応 | 選び方の実装 | `make test-changed` | 子 process で pytest | 登記の前の 1 回 | uv.lock か packages/doeff-hy/pyproject.toml を変えた便は R6 を選ぶ。uv.lock を変えた便は R4 も選ぶ |
| M-consumer | 他の repo が自分の環境の Hy を決める(agora-controllers・custody・merge-queue の deploy/ledger/runtime・code-quality・agent-commons・dotfiles agentcli の pyproject.toml と uv.lock) | 自分の Hy の下限・固定・lock の版、doeff の版(doeff-min-commit・git の rev) | doeff の検の中身 | 自分の uv.lock | なし | 各 repo の commit ごと | lock の Hy は M-cap の範囲の内(uv の解決が守る) |

## 将来の変更と主張(盲検の前に固定)
| id | 軸 | 変更 | 主張 | 予想する波及 | 変える module | 変えない module・契約 | 前提 |
|---|---|---|---|---|---|---|---|
| S1 | hardware | 消費者(agora-controllers)が Python の新しい版へ移るため、doeff の lock より新しい Hy を入れたい | 消費者だけでは入れられず、uv が doeff-hy の上限を名指して解決を止める。入れる道は doeff の 1 commit(M-lock と M-cap)→ 消費者の doeff の版と lock の更新 | doeff: M-lock・M-cap の 1 commit。消費者: M-consumer(doeff の版・lock) | M-lock・M-cap・M-consumer | M-check・M-select の規則は変えない | 消費者は doeff-hy を path か git で引く(index から引かない) |
| S2 | storage | doeff-hy を package の index(PyPI・社内の index)で配る | (除外)今の配り方は git と path だけで、index に置く予定は無い | — | — | — | — |
| S3 | effects | Hy の修正版(1.3.2)が importer など Hy の内部の振る舞いを直す | 修正版は doeff が lock と上限を上げる commit まで、どの消費者にも入らない。doeff が上げる便は M-lock と M-cap を同じ commit で変え、M-select が R4 と R6 を選ぶ。上限だけ・lock だけを上げた便は R6 が赤 | M-lock・M-cap の 1 commit | M-lock・M-cap | M-check・M-select・M-consumer | doeff の検(R4・全部の検)が新しい版で緑になる事を、上げる便が確かめる |
| S4 | distribution | 兄弟の path で doeff を引く消費者(agora-controllers・custody)が doeff-min-commit を上限の入った版より後へ上げる | 消費者の変更は doeff-min-commit と uv.lock の doeff-hy の requires-dist の 1 行だけで、Hy の版は変わらない。lock を更新しない便は `uv lock --check` が赤 | M-consumer(pyproject の 1 行・uv.lock の 1 行) | M-consumer | doeff の module 全部・消費者の code | 消費者の lock の Hy が上限の内(今は 1.3.1 と 1.3.0) |
| S5 | concurrency | 2 つの便が並んで着地する(片方が Hy の lock を変え、もう片方が doeff-hy の依存の宣言を変える) | 合わせた木で上限と lock が食い違えば、R6 がその木で赤になる(判定は木 1 つの中身だけを読む) | なし(検出だけ) | なし | M-check | 着地の門は衝突の印だけを見るので、合わせた木の検は日次の全体の回か次の便の test-changed で走る |
| S6 | simulation | 決定的な模擬の環境で走らせる | (除外)上限は解決の時の静的な metadata で、実行の時の振る舞い・時刻・順序を持たない | — | — | — | — |

## 強制方法
| 守る責務 | 強制方法 | 実装箇所 | 実行経路 |
|---|---|---|---|
| 上限 = lock の版(M-cap と M-lock の一致) | 契約の検(deftest)。宣言と lock を tomllib・packaging で読み、`<=<lock の版>` が specifier に在り lock の版を通す事を判じる | docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy の hy-cap-violations・doeff-hy-requirements・test-adr-doe-hy-008-doeff-hy-caps-hy-at-the-locked-version。反例は test-adr-doe-hy-008-judgments-reject-the-counterexamples | root の pyproject.toml の契約の表: 接頭辞 uv.lock と packages/doeff-hy/pyproject.toml の行 → `make test-changed`。日次の全体の回(docs/adr は testpaths) |
| 消費者の Hy ≤ doeff が確かめた版 | uv の解決(依存の metadata の requirement) | packages/doeff-hy/pyproject.toml の `hy>=1.2.0,<=1.3.1` | 各消費者の `uv lock`・`uv sync`。agora-controllers の日次の uv-lock(`uv lock --check`) |
| 上限の在りかは 1 か所 | 判定が doeff-hy の宣言だけを読む(HY-CAP-DECLARATION) | 同上 | 同上 |
| lock の Hy の名の照合(R4) | 契約の検・使い捨ての環境で import | test-adr-doe-hy-008-imports-exist-in-pypi-hy | 契約の表: 接頭辞 uv.lock |

## 実コードと検査(読み取り対象・版 = doeff の木の HEAD。e71995df を祖先に持つ)
作業木: `/work/toolset/worktrees/doeff-wt-hyub-dc`(doeff の repo)。読むだけにする。

| 物 | path | 何が在るか |
|---|---|---|
| 上限の宣言(M-cap) | packages/doeff-hy/pyproject.toml の `[project] dependencies` | `"hy>=1.2.0,<=1.3.1"` |
| lock(M-lock) | uv.lock の `[[package]] name = "hy"` と、doeff-hy の `[package.metadata] requires-dist` | hy 1.3.1(registry)・`{ name = "hy", specifier = ">=1.2.0,<=1.3.1" }` |
| 判定と検(M-check) | docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy | defk `hy-cap-violations`・`doeff-hy-requirements`・`locked-hy-version`・`scan-hy-imports`・`missing-on-pypi-hy`、defadr の :decision R1〜R6・:laws・:enforcement の deftest 5 本 |
| 選び(M-select) | pyproject.toml(root)の `[[tool.doeff.contract-tests]]`(prefix "uv.lock" と "packages/doeff-hy/pyproject.toml" の行)・scripts/run_changed_tests.py | `make test-changed` が変えた path の接頭辞で検を選ぶ |
| 他の doeff の package の Hy の宣言 | packages/*/pyproject.toml | doeff-adr・doeff-claude-code・doeff-cluster・doeff-codex・doeff-conductor・doeff-docker・doeff-domain が hy を宣言(上限なし) |

## 消費者(M-consumer)の今(各 repo の origin/main・2026-10-12)
| repo | doeff の引き方 | Hy の宣言 | lock の Hy |
|---|---|---|---|
| agora-controllers | 兄弟の path(`../doeff`・editable)・`[tool.agora-controllers] doeff-min-commit` | 根 `hy>=1.3`・controllers/* `hy>=1.0` | 1.3.1 |
| custody | 兄弟の path | custody-scripted `hy>=1.3` | 1.3.1 |
| merge-queue(deploy/ledger/runtime) | git の rev | 根 `hy==1.3.0` | 1.3.0 |
| code-quality | git の rev | `hy==1.3.0` | 1.3.0 |
| agent-commons・agora-controllers/agent-env/cli | git の rev | `hy>=1.2.0` | 1.3.1 |
| dotfiles agentcli | git の rev | `hy>=1.2.0` | — |
agora-controllers の日次の全体検証は処理ステージ uv-lock(`uv lock --check`・doeff は doeff-min-commit ちょうど)を持つ。着地の門(merge queue)は git の衝突の印だけを見て、検を走らせない。

## 検の実行の仕方(読み取りだけで足りなければ)
- 判定の検: `cd /work/toolset/worktrees/doeff-wt-hyub-dc && uv run --frozen pytest 'docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy::test_adr_doe_hy_008_doeff_hy_caps_hy_at_the_locked_version' -q`(機体の検の枠が埋まっていると最初に待つ)
- 共有の作業木を書き換えない。試すなら `mktemp -d` の下へ写して試す。
