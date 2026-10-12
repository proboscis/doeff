# 設計検証の結果 — doeff-hy の Hy の上限(依頼 lt-Y7VTRKY83EG2YC69W8M8EJ0BP1・card ki-0ff168f4eaf5)

## この文書の目的
読み手が、doeff-hy の Hy の上限の設計について次の 3 つを確かめられるようにする。
- 盲検の前の主張(design-pre-blind.md・sha256 560b8311…)のうち、どれが実測で保たれ、どれが盲検 A・B の失敗例で破れたか。
- 破れた所をどう直したか、または誰へ渡したか。
- 主張を強制する検が、通常の変更の経路のどこから呼ばれるか。

## 全体の構成(今)
```
packages/doeff-hy/pyproject.toml ──(R6 で一致を縛る)── uv.lock の hy 1.3.1
  "hy>=1.2.0,<=1.3.1"  (M-cap)                          (M-lock)
        │ uv の解決が読む                                     │ R4 が照合の版に読む
        ▼                                                     ▼
各消費者の uv.lock の Hy ≤ 1.3.1 (M-consumer)      docs/adr/defadr_doeff_hy_008_hy_is_not_forked.hy (M-check)
                                                    ▲ make test-changed が選ぶ(契約の表 — M-select)
                                                    │ 接頭辞 uv.lock → R4・R6 / packages/doeff-hy/pyproject.toml → R6
```

## 完了の範囲
- 実装完了まで。
  - 上限の実装: doeff main e71995df(L2157・基底 74873132f)。
  - 盲検 B を受けた R6 の直し: commit d10bb940e。この設計検証の記録と同じ branch(wt/hyub-design-check)で main へ入れる。
- この設計検証は上限の着地の後に行った(ターンの終わりの検査が報告 JSON を求めたため)。盲検の前の主張は f35286376 で固定した。
- 記録の途中で main に R7(fe951a108・card ki-2fa4c02604b1 — import でない Hy の内部への依りの照合)が入り、同じ冊が衝突した。両方を残して載せ直し、載せ直した木で R6(実物の木・marker・doeff-domain への写し)と反例の検・ADR の契約の検を走らせ直した(evidence/rebased-real.log.txt・evidence/rebased-B-marker-B-domain.log.txt・evidence/rebased-judgments-contract.log.txt)。

## 主張ごとの結果
| 主張 | 盲検の前の予想 | 実測 | 判定 |
|---|---|---|---|
| S1 消費者だけでは doeff の lock より新しい Hy を入れられない | uv が doeff-hy の上限を名指して止まる | 上限の行が条件なしなら主張どおり(evidence/S1-S4-consumer-resolution.log.txt の S1-N)。盲検 B: 上限の行に `; python_version < '3.15'` を付けると、直す前の R6 は通し(evidence/before-fix-B-marker-B-domain-A-lower.log.txt)、3.15 の環境では上限が消える | B で破れた → 直した(R6 が marker を赤にする)。直した後は marker の木が赤(evidence/after-fix-real-B-marker-B-domain.log.txt) |
| S3 Hy を上げる便は M-lock と M-cap の 1 commit | 上限だけ・lock だけを上げた便は R6 が赤 | 上限だけ(<=1.3.2)・小版の上限(<1.4)・lock だけ(1.3.0)は赤、両方を揃えた木は緑(evidence/after-fix-S3-S5-mutants.log.txt) | 上限の側は保たれた。下限の側は盲検 A で破れた(下の節) |
| 上限は doeff-hy の 1 か所(M-cap の不変条件) | (盲検の前は判定なし) | 盲検 B: doeff-domain に同じ上限を写しても直す前の R6 は通した | B で破れた → 直した(R6 が doeff-hy の外の上限を赤にする)。直した後は写しの木が赤 |
| S4 兄弟の path の消費者は doeff-min-commit と uv.lock の 1 行だけ | Hy の版は変わらない。lock を更新しない便は `uv lock --check` が赤 | 主張どおり。差分は requires-dist の 1 行、Hy は 1.3.1 のまま、更新しない便は rc=1 | 保たれた |
| S5 並んだ 2 便の合わせ目の食い違いは R6 が赤 | 判定は木 1 つの中身だけを読む | lock だけを下げた合わせ目は赤、揃えた合わせ目は緑 | 保たれた(検が走るのは日次か次の便の test-changed — 着地の門は検を走らせない) |

## 盲検 A の失敗例 — 下限の持ち主が無い(範囲の外へ渡した)
- **失敗例**: Hy の修正版が上流の欠陥(`hy.compat.rewriting_unparse` の再帰)を直し、doeff が Hy を上げて当て物 `doeff_hy/ast_unparse.py` を外す。そのとき下限 `>=1.2.0` が残ると、doeff の版を上げた消費者は古い Hy(1.3.0・1.3.1)の上で当て物の無い doeff-hy を動かし、止まる。
- **実測**:
  - 当て物が無いと、Hy 1.3.0 と 1.3.1 のどちらでも `inspect.signature(..., annotation_format=Format.STRING)` が 20 秒で終わらない(rc=124)。当て物があれば 0.001 秒で返る(evidence/A-hang-without-and-with-patch.log.txt・sha256 90c853b7…・script evidence/A-hang.py.txt)。
  - 直す前の R6 は下限 `hy>=0.1,<=1.3.1` を通す(evidence/before-fix-B-marker-B-domain-A-lower.log.txt の案 lower-0.1)。
- **判定**: 知識の漏洩。「doeff-hy が正しく動く最古の Hy」は doeff-hy の当て物の中身だけで決まるのに、持ち主の module も検も無い。正しく扱うには M-cap の下限・M-consumer・M-check が変わる。
- **ここで直さない訳**:
  - 検で確かめられる下限は「lock の版ちょうど」だけ。しかし code-quality は Hy の投影のために hy 1.3.0 ちょうどを要求し(code-quality の pyproject.toml の註・quality/hy_projection.py)、dotfiles agentcli と merge-queue もそれに合わせて 1.3.0 を固定している。
  - 下限を lock の版にすると、この 3 つが doeff の版を上げた時に解けなくなる。
  - 下限の決め方は、依頼者が範囲の外として切った card ki-96ca6c49fffd(doeff-hy の Hy の下限と 1.3.0 が検に入らず、doeff-domain には上限が効かない)で決める。この節の実測は、その card へ発言として渡した。
- **A のほかの指摘**:
  - R4 が当て物の頼る内部の名(`rewriting_unparse`・`_could_be_hy_src`)を数えない事、上流が名を保って直すと `install()` が上流の直しを上書きし続ける事。
  - どちらも上限の設計の外で、既存の card ki-2fa4c02604b1(import でない Hy の内部への依り)の範囲に入る。この設計検証では再現していない。

## 盲検 B の失敗例 — 検を通る上限の抜け道(直した)
| 抜け道 | 直す前 | 直し(d10bb940e) | 直した後 |
|---|---|---|---|
| 上限の行に条件(marker)を付ける | R6 passed | hy-cap-violations が marker の付いた行を赤にする | R6 failed「hy の上限の行に条件(marker)が付いている」 |
| doeff-domain に上限を写す | R6 passed | hy-caps-outside-doeff-hy・hy-declarations-outside-doeff-hy が root と packages/*/ の pyproject.toml(doeff-hy を除く)の、上から縛る hy の宣言を赤にする | R6 failed「hy の上限は packages/doeff-hy/pyproject.toml の 1 か所だけに置く」 |
| doeff_hy の code に「確かめた版」の定数を写す | (B が候補として挙げただけ・未実測) | 直さない — 版の写しの一般の問題(card ki-df4cc0b38cda が検の側の写しを消した) | — |

## 強制方法(盲検の後)
| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 上限 = lock の版・条件なし(M-cap と M-lock) | 契約の検(deftest) | defadr の hy-cap-violations・doeff-hy-requirements・R6 の deftest・反例の deftest | 契約の表: 接頭辞 uv.lock・packages/doeff-hy/pyproject.toml → `make test-changed`(evidence/M-select-contract-table.log.txt)。日次の全体の回 | 着地の門は検を走らせない |
| 上限は 1 か所 | 契約の検 | hy-caps-outside-doeff-hy・hy-declarations-outside-doeff-hy(同じ R6 の deftest) | 同上 | doeff-hy の外の pyproject.toml だけを変えた便では R6 が選ばれない(契約の表の行は doeff-hy と uv.lock だけ)— 日次の全体の回で赤になる |
| 消費者の Hy ≤ 確かめた版 | uv の解決 | packages/doeff-hy/pyproject.toml | 各消費者の uv lock・agora-controllers の日次の uv-lock | 消費者の側の uv の設定(override-dependencies など)は doeff の検の外 |
| 下限 | なし | — | — | 持ち主が無い — card ki-96ca6c49fffd |

## 計測の注
- evidence/before-fix-B-marker-B-domain-A-lower.log.txt と evidence/after-fix-real-B-marker-B-domain.log.txt の `rc=` の行は、pytest でなく後ろの grep の終了コードを拾っている(script を直す前の版)。判定はそれぞれの案の pytest の要約行(`1 passed` / `1 failed`)で読む。
- 直した後の script(evidence/repro.sh.txt)は pipefail で pytest の終了コードを出す。evidence/after-fix-S3-S5-mutants.log.txt の `rc=` は pytest の物。

## 範囲の外・未確認
- 下限と 1.3.0 の消費者: card ki-96ca6c49fffd へ渡した。
- 当て物の内部の名と `install()` の検出: card ki-2fa4c02604b1 の範囲。
- 消費者の解決は最小の消費者(doeff-hy を path で引く)で測った。本物の agora-controllers の木での `uv lock` は走らせていない(custody・agent-commons を ssh の git で引くため)。
