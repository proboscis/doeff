# 設計検証の本文 — DOEFF002 の初期化の口・数える範囲と、doeff-linter の基点の鍵

依頼 lt-JAMKZ0NP8ZTQ52AHNGW67SGKPP(class investigate)・card ki-9da0a5c71947。

## この文書の目的

読み手が次の 4 つを、この文書と同じ dir の証拠だけで確かめられるようにする。

1. 日次の lint の DOEFF002 の 2 行が、本物の所見か linter の誤検出だったか。
2. 盲検の前に何を主張し、盲検 A・B の失敗例でどの主張が崩れたか。
3. 崩れた主張を、どの境界・検査の直しで塞いだか。
4. 直した後に、正常例が通り、失敗例が狙った理由で拒まれる事。

## 完了の範囲

実装完了まで。直しは 2 便とも doeff の main に入っている。

| 便 | 中身 | 着地の sha |
|---|---|---|
| L2099 | DOEFF002 が `__set_name__` を初期化と見る・説明文を実装に合わせる・基点の鍵を替える・鍵の替えで判定に入った DOEFF004 を直す・`_absent` を `_mut_absent` に | 4ccab04aca929df800e6d33d36b8e31c8f87fcea(linter の commit は 7b3a9b190) |
| L2102 | 盲検の失敗例を塞ぐ(下の「直し」)。DOEFF002 が `async def` の method と入れ子の本体の全部を数える・日次が基点の鍵と木の linter の鍵の違いを赤にする | 4b254ec2f7463a4c78645ea4aff0b976865e0e02(linter の commit は 99fb5f922) |

- 基準の版(直す前の main)= 61f0600093debe4c04c6550985bc4b26a3b6e4dc。
- 盲検の前の設計と主張は design-before-blind.md(sha256 1c778e48c2f4a97beb466162a2c4ee2250dbc0ed2210ae68ed49e0c6c4fea812)。この本文は、その主張を書き換えずに、盲検の結果と直しを足す。
  - 作業の branch wt/doeff002-design-check では、盲検を起こす前の commit 805c6ea4b(10-11T18:51Z)に入れた。着地の rebase で sha は替わるので、同一性は sha256 で照らす。

## 固定した要件

- R1(card の受入 c51f88e5f): 2026-10-12 00:00 JST より後の doeff の日次の lint で、次の 2 つに戻る。
  - program_effects.py の DOEFF002 が基点 1 件
  - macro_use.py の DOEFF002 が 0 件
- R2: 誤検出なら linter を直して戻す。基点に足して通さない。
- R3: 直しが、日次の lint に新しい赤の行を足さない。

## 調べの答え

| 所見 | 判定 | 処置 |
|---|---|---|
| macro_use.py `self.missed = True`(a5d666e74) | 本物(`facts()` の中で代入) | 54f1cdfa3 が `_mut_missed` に改名済み |
| program_effects.py `self._held = f"_{name}"`(646a3e986) | 誤検出。descriptor の `__set_name__` は、owner の class を作る時に Python が 1 度だけ呼ぶ初期化の口(PEP 487) | M1 が `__set_name__` を初期化と見る(L2099) |
| program_effects.py `self._absent = _absent_receiver()`(e3a391c71・日次の後) | 本物(遅れて作る値) | `_mut_absent` に改名(L2099) |

- 規則 DOEFF002 の説明文「list・dict・set」は、実装(誕生から値の型を見ない)と食い違っていた。依頼の見立てが外れた元はこの説明文。

## 責務(module)

| ID | 責務 | 持つ物 | 隠す物 | 口 |
|---|---|---|---|---|
| M1 | DOEFF002 の規則 | 初期化の method の集合(`is_init_method`)・数える範囲(class の `def` / `async def` の method と、その入れ子の本体の全部・代入の左辺の組)・名の決まり | 値の型を問わない事・入れ子の本体の一覧(`utils::nested_bodies` に委ねる) | `LintRule::check(&RuleContext) -> Vec<Violation>` |
| M2 | 規則の説明文 | rule_info.rs・report.rs の文 | — | 人と道具への説明 |
| M3 | 基点の比べ | 基点の数・数えた linter の鍵(`version`)・日次(--strict)の鍵の照らし | binary の探し方(置き場・land-arm) | `hook_finding_baseline.py check / lower / init` |
| M4 | tools の層の宣言 | 母集団から外す名指しの module と理由 | — | defarchitecture の `:modules`・`:exempt` |
| M5 | 閉じの解析の reader | 遅れて作る `:absent` の受け手(`_mut_absent`) | — | `_Reader.absent_receiver()` |
| M6 | 子の process を起こす検 | 子へ渡す環境 | — | pytest の `monkeypatch.setenv`(子は受け継ぐ) |

## 盲検の結果

起動の記録は blind/blind-meta.md、返答は加工せず blind/blind-a-return.md・blind/blind-b-return.md に置いた。

### 盲検 A — 鍵の替えの時機を誰も持たない

- 主張の崩れ:
  - S1 の予想「M3 は version の 1 行、M4〜M6 の code は変わらない」は、書かれていない前提(基点の鍵 = HEAD の linter の鍵)の上でしか成り立たない。
  - S2 の主張「鍵の合わない状態では測れないと名乗る」は、基点の鍵の binary が在る時は成り立たない。
- 漏れた知識:
  - 「木の linter の意味と、関門が測る意味が同じか」の判断に持ち主がいない。
  - linter を変えた便が基点を数え直さないと、その代価が次に鍵を替える便へ溜まる。
  - 実績: 10-03〜10-11 に linter の入力を変えた commit 28 本・基点を書いた commit 0 本。L2099 は DOEFF002 の直しなのに、DOEFF004 の所見を M4・M6 で直した。
- 再現(evidence/a/A-repro-at-7b3a9b190.txt):
  - M1 を変えて鍵を替えていない 7b3a9b190 の木で、日次と同じ段を回した。
  - HEAD の鍵は 5c59502e…、基点の鍵は bee713059f…。
  - 段は「測れない」と名乗らず、`__set_name__` を初期化と見ない 780ef711a の linter で測った(program_effects.py は基点 1 → 今 3)。
- 成立: 成立。

### 盲検 B — DOEFF002 が数えない形

- 主張の崩れ:
  - S1 の主張「M1 は後から代入する状態を型に依らず `_mut` の名に強いる」は、前提の形(`self.<名> = …`)のままで崩れる。
  - 崩れる場所: `async def` の method・try の else と finally・match の case・for の else・組の左辺・method の中の関数。
- 現実的な例: OpenRouter の client に、作った後に開く接続の pool `_http` を `async def` の中で足す差分。検査を全部通ったまま、名に `_mut` の無い状態ができる。
- 再現:
  - evidence/b/B-repro-at-4ccab04ac.txt — 9 つの形のうち、所見は `plain` の 1 つだけ。
  - evidence/b/B-red-before-fix.txt — 失敗ケースの検の赤。
- 成立: 成立。
- 範囲の外とした所: B が挙げた「接続の寿命を設定の値の object が持つ事」の是非は、M1〜M6 の外の設計の問いで、B 自身も推測と書いた。ここでは扱わない。

### 盲検 A の副次の候補(主の例ではない)

| 候補 | 判断 |
|---|---|
| DOEFF119(Hy の規則)が別の初期化の集合を持つ | S1 の前提(Python の代入)の外。doeff の pyproject では無効 |
| DOEFF003 が名の決まりを写して持つ | 名の決まりを締める変更は主張していない。S1 の範囲の外 |
| `__setstate__` を初期化の集合に足す | 将来の変更の候補。直しの後は、M1・M2 の文・M3 の鍵の替えに収まる(下の「予測と実測」) |

## 直し(L2102)

### B を塞ぐ: M1 が数える範囲

- class の本体の `def` と `async def` の両方の method を歩く。
- 入れ子の本体は `utils::nested_bodies` の 1 か所の一覧で辿る。M1 が自前に持っていた欠けた一覧を捨てた。
  - method の中の class には降りない。その method の self は別の object のため。
- 代入の左辺は tuple・list・starred の中まで見る。
- 失敗ケースの検:
  - `test_assignments_in_async_methods_and_every_nested_body_are_counted`(直す前は `{"_j"}` だけで赤)
  - `test_a_nested_class_assigns_to_its_own_instance`
  - `test_assignments_in_init_bodies_stay_initialization`
- 判定に入った本物の所見 2 行を改名した。
  - test_agentd_client.py `_requests_seen` → `_mut_requests_seen`
  - test_edit_image.py `calls` → `mut_calls`

### A を塞ぐ: M3 が鍵の違いを持つ

- `hook_finding_baseline.py check --strict`(日次)は、木の HEAD の linter の組み立ての入力の鍵が基点の鍵と違えば赤にする。
  - 赤の文は両方の鍵と `--linter <binary> lower doeff-linter` の手順を名指す。semgrep の uv.lock の版の検と同じ形。
- hook(--strict なし)は止めない。他人の linter の変更で、その後の全部の commit を止めないため。
- git の無い木は HEAD の鍵が無いので照らさない。
- 失敗ケースの検: `test_strict_names_a_linter_changed_past_the_baseline_and_turns_it_red`(直す前は rc 0 で赤)。

## 予測と実測

| シナリオ | 盲検の前の予測 | 実測 | 差の訳 |
|---|---|---|---|
| S1 | M1(と検・M2 の文)・M3 は version の 1 行・M4〜M6 は変わらない | L2099: M1・M2・M3 の version に加え、M4(層 release)と M6(検 4 file)が変わった。M5 の改名は S1 の波及ではなく、日次の後に入った別の本物の所見 | 知識の漏洩(盲検 A)。鍵の替えが 28 本分遅れていて、DOEFF004 の意味の変更の代価が DOEFF002 の便に乗った |
| S1(直しの後) | 同上 | L2102: M1・M3(code の照らしと version の 1 行)・DOEFF002 自身の新しい所見の持ち主 2 file。鍵の替えで判定に入った他の規則の行は 0(lower の名指しは元からの 5 組と、DOEFF002 の 2 行だけ) | 予測どおり。M3 の code の変更は、盲検 A を塞ぐ直しそのもの |
| S2 | M3 の data 1 行と、判定に入る所見の持ち主 | 予測どおり。ただし「鍵の合わない状態では測れないと名乗る」は、binary の在る時に崩れた | 照らしの欠け。L2102 の --strict の照らしで塞いだ |
| S3 | M6 か M4 の 1 項目 | M6 の 4 file と、M4 の層 1 つ | 予測どおり |

- 予想の範囲を後から広げてはいない。S1 の崩れは崩れとして残し、直した境界で再検証した。

## 強制方法(直しの後)

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| M1: 初期化の外の代入は型と形に依らず `_mut` | DOEFF002(severity error) | doeff002_mutable_attribute_naming.rs<br>検 8 本 | commit の hook と日次 lint の段(基点の比べ)<br>cargo の検は日次の rust の段 | `setattr`・`self.__dict__[…]`・`self` 以外の名・`for self.x in`・`with … as self.x` は見ない<br>継承元の framework の属性への書きは見分けない(card ki-71204291060d) |
| M1 の入れ子の一覧 | `utils::nested_bodies`(網羅の match・`_ =>` なし) | utils/mod.rs | 同上 | 新しい文の種類は、その match が compile の時に名指す |
| M2 = M1 | 人の読み | rule_info.rs・report.rs | なし | 機械では照らさない |
| M3: 基点は下がる向き・鍵の一致 | `check --strict` の鍵の照らし<br>`lower` は上げない | scripts/hook_finding_baseline.py<br>検 tests/test_hook_finding_baseline.py | 日次 lint の段 | hook では照らさない(意図)<br>git の無い木は照らさない |
| M4: 名指しの module だけを外す | defarchitecture の `:modules`・`:exempt`(理由が要る) | tools/architecture.hy<br>tests/rule_population.rs | 日次 lint の段・rust の段 | 外す理由の妥当さは人が判じる |
| M6: 検は os.environ を参照しない | DOEFF004 | linter | 同上 | `subprocess` の `env=None` の受け継ぎ自体は見ない |

## 検証(checks の対応)

| check | シナリオ | 種類 | 証拠 |
|---|---|---|---|
| C1 | S1 | 正常例 | evidence/s1/cargo-at-4b254ec2.txt — `__set_name__`・初期化の入れ子の本体・入れ子の class は所見にしない(8 passed)<br>rule_population 12 passed |
| C2 | S1 | 失敗例 | evidence/b/B-after-fix-at-4b254ec2.txt — 9 つの形が全部所見・盲検 B の client.py の差分(after・twin)が所見・before は 0 |
| C3 | S1 | 失敗例(直す前の再現) | evidence/b/B-repro-at-4ccab04ac.txt・evidence/b/B-red-before-fix.txt |
| C4 | S2 | 正常例 | evidence/s2/lint-stage-at-4b254ec2.txt — HEAD の鍵 = 基点の鍵・赤は元からの 5 組だけ・program_effects.py は `_tree` の 1 件・macro_use.py は 0 件 |
| C5 | S2 | 失敗例 | evidence/a/A-after-fix-at-4b254ec2.txt — 鍵の違う 7b3a9b190 の木に --strict が鍵を名指して rc 1・hook は鍵の違いで止めない |
| C6 | S2 | 失敗例(直す前の再現) | evidence/a/A-repro-at-7b3a9b190.txt・evidence/a/A-red-before-fix.txt |
| C7 | S2・S3 | 正常例 | evidence/s2/pytest-at-4b254ec2.txt — 基点の比べ・関門の道具・環境を置く検・改名した属性の検 60 passed(鍵の照らしの検と `lower` が上げない検を含む) |
| C8 | S3 | 正常例 | evidence/s3/env-at-4b254ec2.txt の正常例 — 検 4 file の DOEFF004 は 0 件 |
| C9 | S3 | 失敗例 | evidence/s3/env-at-4b254ec2.txt の失敗例 — 直す前の 4 file は `os.environ` の参照が 4 件・名指していない tools の module は 2 件・名指しの rust_wheel_release.py は 0 件 |

## 外したシナリオ

| ID | axis | 外した理由 |
|---|---|---|
| S4 | hardware | 静的な解析で、機体の資源に依らない |
| S5 | distribution | binary を機体ごとに組む・写す仕組み(linter_snapshot.py・land-arm)は変えていない。この変更はその仕組みを使うだけ |
| S6 | simulation | AST の読みは決定的で、時間と乱数を使わない |

## 残る事(未解決ではない — 受入と主張の外)

- 受入の条 c51f88e5f は、04:00 JST の日次の回の lint の結果で確かめる(この検証の後の、日次の仕事)。
- 日次の lint には、元から在る 5 組の赤が残る(card ki-7f77141635f0 の範囲)。
  - os_files.py・test_file_watch.py の DOEFF002
  - docs/design/analyzer-read-when-reached-…/measure の DOEFF004 2 file
  - test_static_check_named_paths.py の DOEFF006
- doeff/do.py の `# noqa: DOEFF002 - int counter, not a mutable container` 2 か所は、旧い説明文の読み違いから来た抑制に見える。手は付けていない。
