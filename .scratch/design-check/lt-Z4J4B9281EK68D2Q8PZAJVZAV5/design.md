# 設計検証 — 素の commit の hook が stage した file に semgrep の規則を当てる(L2051)

- 依頼: lt-Z4J4B9281EK68D2Q8PZAJVZAV5(class investigate・card ki-2ff6e513ee7f / ki-8c0bb496bfab)
- 対象の版: doeff `15e6fc9c4ef0d9de254adc7b70f5395d69a5de31`(L2051 の着地・origin/main の先端)
- 完了の範囲: **実装完了まで**(実装は L2051 で main に入った)。Pod へ効くのは、L2051 の後に準備した worker の Pod から(下の限界 L1)。
- 著者: 会話 c-GPQPMMK7CZKZE1Z0WVXRKDYS92・claude-opus-5-5・effort high(自己申告)

## 1. 固定した要件

- R1 汎用の package(doeff-cluster・doeff-records・doeff-claude-code・doeff-codex など)の註・docstring・文書に使い手の系の名
  (agora・agora-controllers・acp・kanban)を足す commit を、**commit の時に**止める(semgrep の規則
  `doeff-packages-have-no-application-vocabulary` ほか `.semgrep.yaml` の規則)。直す前は、agora の担当の Pod の commit が通り、
  翌日の日次の `test_semgrep_findings_match_baseline_ratchet` で初めて赤になっていた(10-11 の 5 回)。
- R2 着地の門(マージの列)は衝突の印の走査だけ(operator 2026-10-02 の裁定)なので、止める所は著述機の commit の hook。
- R3 Pod は pre-commit の枠組みを据え付けず、素の hook(`scripts/git-hooks/pre-commit` の写し)を入れる。
- R4 作業木に `.venv` が無くても走る(Pod の作業木・make sync をしていない作業木)。
- R5 stage した断面にだけ当てる。既存の所見(基点)で塞がない — 新しい所見だけを止める。

## 2. 責務(module)

| id | 実体 | 責務 | 持つ知識 | 隠す知識 |
| --- | --- | --- | --- | --- |
| plain-hook | `scripts/git-hooks/pre-commit` | commit の時の入口。作り直し中は判定しない・(1) semgrep の項 → (2) 台帳の突合 の順に撃ち、赤なら止める | 撃つ順・止め方・古い断面を塞がない判断 | どの file にどの規則を当てるか・道具の版 |
| hook-items | `.pre-commit-config.yaml` の semgrep の 4 項(`semgrep`・`semgrep-hy`・`semgrep-reading-plane`・`semgrep-application-vocabulary`、どれも別名 `semgrep-rules`) | どの file 種にどの semgrep の設定を当てるかの**唯一の定義点** | files / types / exclude の選別・Python は基点と比べる | 機体に枠組みが据わっているか |
| locked-entry | `scripts/precommit_locked.py`・`scripts/semgrep_locked.py` | uv.lock の版の pre-commit / semgrep を uv の道具の置き場から呼ぶ | 版の読み(uv.lock) | 作業木の .venv・探し道の道具 |
| rules | `.semgrep.yaml` | 何が違反か | 規則の pattern と paths | どこから撃たれるか |
| baseline | `scripts/hook_finding_baseline.py`・`scripts/hook_finding_baseline/*.json` | Python の既存の所見と比べ、増えた所見だけを止める | 規則 × file の基点 | — |
| pod-installer | agora `deploy/agent-worker/worker-prepare.sh` の 5. | Pod の準備の時に doeff の clone へ hook の写しを入れる | いつ・どこへ写すか | hook の中身・規則 |
| wiring-tests | `tests/test_lint_entrypoint_wiring.py` の `test_plain_hook_*` | 素の hook の振る舞いと別名の配線を検で固定する | 検の repo の組み立て(偽の uv.lock・偽の基点) | — |

公開の契約:
- plain-hook: git の pre-commit hook の契約(引数なし・cwd は作業木の根・exit 0 = 通す・非 0 = 止める)。
- hook-items → plain-hook: `pre-commit run semgrep-rules`(別名 1 つ)。素の hook は項の名を並べない。
- locked-entry: `uv run --no-project --python 3.12 python scripts/precommit_locked.py <pre-commit の引数…>` → pre-commit の終了値をそのまま返す。

## 3. 将来の変更と主張(盲検の前に固定)

| id | 軸 | 変更 | 主張 | 予想の波及 |
| --- | --- | --- | --- | --- |
| S-hardware | hardware | 別の CPU・OS の機体で hook を走らせる | **対象外**: hook は機器を使わない。機体の違いは uv の python の解き(`--python 3.12`・既存の宣言 ki-a28a482132ab)に閉じ、この変更は触らない。Linux/x86_64 の Pod の外は試せない | — |
| S-storage-venv | storage | 作業木に `.venv` が無い・uv の道具の置き場が初めて | hook は作業木の .venv を読まず、uv.lock の版を uv の置き場から呼ぶ。置き場の違いは locked-entry に閉じる | locked-entry だけ(plain-hook・hook-items は不変) |
| S-storage-index | storage | stage した断面と作業木の中身が違う(一部だけ stage) | 当てるのは stage した断面だけ。作業木の未 stage の変更は判定に入らず、hook の後も残る | 変更なし(pre-commit の stash の契約に依る) |
| S-effects-tool | effects | 道具を走らせられない(uv が無い・取り寄せに失敗) | 走らせられない時は commit を止め、名指す(黙って通さない) | 変更なし(plain-hook の `|| exit 1`) |
| S-distribution-rules | distribution | `.semgrep.yaml` に規則を足す・規則を変える | 規則の変更は作業木の版で効き、各 Pod に入った hook の写しを替えなくてよい | rules だけ(plain-hook・pod-installer は不変) |
| S-distribution-items | distribution | 新しい file 種へ semgrep の項を足す | 項に別名 `semgrep-rules` を書けば、枠組みの機体と素の hook の機体の両方に届く。書き忘れは wiring-tests が赤にする | hook-items だけ(plain-hook は不変) |
| S-concurrency | concurrency | 同じ Pod の同じ clone の別の作業木で、2 つの会話が同時に commit する | hook は commit ごとの process で、共有する可変の状態は uv の置き場(uv が鍵を持つ)だけ。結論はそれぞれの stage で決まる | 変更なし |
| S-simulation | simulation | 道具を偽に差し替えた検の repo で hook を決定的に検める | 偽の uv.lock・偽の基点・本物の semgrep で、止める例と通す例を検で再現できる | wiring-tests だけ |

## 4. 強制方法

| 守る責務 | 強制方法 | 実装箇所 | 実行経路 | 限界 |
| --- | --- | --- | --- | --- |
| 規則の選別は hook-items の 1 か所 | semgrep を撃つ項はどれも別名 `semgrep-rules` を持つ・素の hook は `run semgrep-rules` を含む | `test_plain_hook_runs_every_semgrep_hook_of_the_config` | doeff の pytest(日次・変えた file の検) | 項の entry に `semgrep` の語が無い semgrep の撃ち方は数えない |
| 業務の語を足した stage を止める | 本物の semgrep で素の hook を撃つ検(Hy の註・Python の docstring・文書) | `test_plain_hook_stops_staged_application_vocabulary` | 同上 | 検の repo は規則を 1 つに絞る |
| 所見の無い stage は通す | 同じ検の正の例 | `test_plain_hook_passes_staged_file_without_findings` | 同上 | — |
| 道具の版は uv.lock | locked-entry だけが版を読む | `scripts/semgrep_locked.py`(既存 #2906) | hook の各項 | — |
| 全体の後ろ盾 | 日次の基点の ratchet | `tests/test_semgrep_gate.py::test_semgrep_findings_match_baseline_ratchet` | 日次の全体検証 | 翌日まで分からない |

## 5. 既知の限界(盲検の前から分かっている物)

- L1 Pod の hook は Pod の準備の時に本線の先端から写す。L2051 の前に準備した Pod の写しは古い(台帳だけを見る)。worker の Pod は値の変更で作り直さない(card ki-b52157bbc19d)。
- L2 `git commit --no-verify` は止めない(日次が後ろ盾)。
- L3 素の hook は doeff-linter を撃たない(この card の外)。

---

以下の 6.〜9. は盲検の後に足した。1.〜5. は盲検の前の版のまま(写し design-preblind.md・sha256 b7a26b147d378e49b0e43bcc19e8557e7cfd2aaf397a5cf244e7f7623d633e94)。

## 6. 盲検 A・B

- 起動の記録: blind/meta.md(第 1 候補 gpt-6-astra は codex の資格が無く起動せず、第 2 候補 opus・xhigh を Agent tool で起こした。実モデルは未観測)。
- 入力: blind-input.md(sha256 45cac21b…・1.〜4. と読める実体の一覧。5. の既知の限界は渡していない)。
- 返答(未加工): blind/A-reply.md・blind/B-reply.md。

| 盲検 | 失敗例 | 当たった主張 | 設計者の再現(修正前) | 判定 |
| --- | --- | --- | --- | --- |
| A | 業務の語の規則の `paths.include` に 5 つ目の package を足すと、項 `semgrep-application-vocabulary` の `files` の regex(4 package の名を写して持つ)も直さないと、素の hook がその package の文書を通す | S-distribution-rules の「波及は rules だけ」 | evidence/A-repro-before-fix.log: c2(規則だけ変える)で素の hook rc 0・日次と同じ走査 rc 1。c1(対照)と c3(項も直す)は rc 1 | **成立** — 知識の漏洩(どの package が汎用かが規則と項の 2 か所)。宣言した共同の不変条件ではない |
| B | 素の hook に「当たる file を stage していなければ pre-commit を起こさない」近道(stage の名の regex)を足すと、検 6 本を全部通りながら、名が ASCII の外の文書と将来の項の file を黙って通す | 「選別は hook-items の 1 か所・plain-hook は隠す」(2. の表)・S-distribution-items | evidence/B-repro-before-fix.log: 差分の後の hook で検 6 passed。`packages/doeff-records/説明.md` に業務の語 → 差分の後の hook rc 0・commit が入った / 元の hook rc 1 / 枠組み rc 1 | **成立** — 今の検が plain-hook の選別の禁止を表していない(実装は守っているが、検査が違反を拒まない) |

## 7. 修正と再検証(L2055 で main へ — ee7a4e8f4・bf6e78bc3)

| 失敗例 | 直し | 実装箇所 | 修正後の観測 |
| --- | --- | --- | --- |
| A | 項から package の名を外す: id `semgrep-other-files`・`files: '^(doeff|packages)/'`(日次の走査と同じ根)・Python と Hy を除く。どの package に当てるかは規則の `paths` の 1 か所。広げた範囲 707 file の既存の所見は 0 件(2026-10-11 実測)なので、既存の所見で塞がない(R5) | `.pre-commit-config.yaml` | evidence/A-repro-after-fix.log: c2 で素の hook rc 1(Failed・規則の名)。evidence/check-distribution-rules.log: 規則だけに package を足し、Pod と同じく `.git/hooks` へ写した hook で `git commit` → rc 1・HEAD は増えない |
| A の強制 | 検 `test_plain_hook_follows_the_rule_paths_for_documents`: 検の repo の規則にだけ package を足し(項は写したまま)、その package の README の業務の語を止める事・規則の外の package は通す事 | `tests/test_lint_entrypoint_wiring.py` | evidence/tests-before-item-fix.log: 直す前の項で package-added-to-the-rule が赤(1 failed / 9 passed)。evidence/tests-after-item-fix.log: 26 passed |
| B の強制 | 検 `test_plain_hook_leaves_file_selection_to_the_config`(どの項にも当たらない stage でも素の hook が pre-commit を起こし、別名の項の名がすべて出る)と、名が ASCII の外の文書の止め(`document-non-ascii-name`) | 同上 | evidence/B-patch-against-new-tests.log: B の差分を直しの後の木に当てると 3 failed(non-ascii・package-added・selection) |

事前の予測との差: S-distribution-rules は「rules だけ」と予測したが、修正前の実装では rules と hook-items が変わる必要があった(知識の漏洩)。予測の範囲を広げず、境界(項が package の名を持たない)と検を直して、修正後に「rules だけ」で止まる事を確かめた。S-distribution-items は予測どおり hook-items だけで届くが、plain-hook が選別を持たない事を拒む検が無かったので足した。

## 8. 場面ごとの検証(設計者が実行・本物の uv.lock の版の pre-commit と semgrep・本物の .semgrep.yaml)

script: checks.sh(直しの branch 7060adcb6 = 着地の bf6e78bc3 と同じ中身の写しを /tmp に作って撃つ)。

| 場面 | 正常例 | 失敗例 | 証拠 |
| --- | --- | --- | --- |
| storage-venv | `.venv` の無い写しで所見の無い Hy の註 → rc 0 | 業務の語の Hy の註 → rc 1・規則の名 | evidence/check-storage-venv.log |
| storage-index | 所見の無い行を stage・業務の語の行は未 stage → rc 0、hook の後も未 stage の行が残る | 業務の語を stage・作業木では消した → rc 1 | evidence/check-storage-index.log |
| effects-tool | 普段の PATH → rc 0 | PATH から uv を外す → `uv: not found`・rc 1・「走らせられなかった」の文 | evidence/check-effects-tool.log |
| distribution-rules | 規則に足した package の所見の無い README → commit rc 0・規則の外の package の業務の語 → rc 0 | 規則に足した package の業務の語 → commit rc 1・HEAD 不変 | evidence/check-distribution-rules.log・evidence/A-repro-after-fix.log |
| distribution-items | 直しの後の木で検 26 passed | B の近道 → 検 3 failed | evidence/tests-after-item-fix.log・evidence/B-patch-against-new-tests.log |
| concurrency | 同じ clone の 2 つの作業木(hook の写しは共有の 1 つ)で同時に commit・両方とも所見なし → rc 0 / rc 0 | 一方に業務の語 → rc 1(HEAD 不変)/ rc 0(HEAD +1) | evidence/check-concurrency.log |
| simulation | 偽の uv.lock・偽の基点・本物の semgrep の検の repo で、所見の無い stage・規則の外の package・どの項にも当たらない stage が通る | 業務の語(Hy の註・docstring・文書・名が ASCII の外の文書・規則に足した package)を止める | evidence/tests-after-item-fix.log・evidence/tests-before-item-fix.log |

変更箇所の品質検査: evidence/code-quality-changed.log(`code-quality --scope changed --base c4f1423f4`・passed・型検査は fast の段の外)。

## 9. 未確認・範囲外

- 実装の証拠の接続: 検は doeff の pytest(日次)から呼ばれる。素の hook は make hooks-install と Pod の準備(worker-prepare.sh の 5.)が入れる。L2051 の前に準備した Pod の写しは古く、規則を当てない(5. の L1)— その Pod の commit は日次の ratchet が後ろ盾(card ki-2ff6e513ee7f の条 c213707ec が日次 2 回の緑を数える)。hook の本体は L2055 で変えていないので、L2051 の後に準備した Pod の写しは L2055 の項の直しを作業木の版で受ける(evidence/check-distribution-rules.log と同じ形)。
- hardware の軸は対象外(3. の表)— Linux/x86_64 の外は試していない。
- 盲検 A の範囲外の観察(試していない推測として返った物): 素の hook の台帳の引き金(`docs/adr .semgrep.yaml`)が項 `enforcement-ledger` の `files` と 2 か所にある。これは L2051 の前からの台帳の機構(ADR-DOE-ENFORCE-001)で、この依頼の変更が足した物ではない。Rust の規則は、項の範囲を doeff/ と packages/ に広げたので、その中の .rs には commit の時に当たるようになった(広げた時点の所見 0 件)。
- `--no-verify` は止めない・素の hook は doeff-linter を撃たない(5. の L2・L3・この依頼の外)。
