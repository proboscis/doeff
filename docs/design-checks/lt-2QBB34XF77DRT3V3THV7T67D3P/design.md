# 設計検証 — doeff-linter が機体の検の枠を取る(依頼 lt-2QBB34XF77DRT3V3THV7T67D3P・card ki-ce35bf38508d)

## この文書の目的

読み終えた人が、次の 4 つを言えるようにする。

1. doeff-linter がどこで・どう機体の検の枠を取り、どの知識を誰が持つか。
2. 将来の変更(機体ごとの本数・置き場・取り方・新しい mode・入れ子・包みの死・呼び手の pin の上げ)が、どこまでで収まるか。
3. その主張を、何が機械で守っているか(検の名・実行経路・限界)。
4. 盲検 A・B が見つけた 2 つの失敗例が何で、どう直し、直した後に何を観測したか。

完了の範囲: **実装完了まで**。下の表の変更は全部 main に着地済みで、検は着地の版か、着地の版と同じ code の候補で走らせた。

## 1. 全体の形

```
会話の Bash / poe lint / git の commit-msg の hook / land_lint_gate / 日次の段 / code-quality
        │  doeff-linter <重い mode の引数>
        ▼
doeff-linter(pid P)── main.rs: --list-rules の後、仕事の前に check_slot::enter(weight(Mode))
        │  重い mode・印 DOEFF_LINTER_CHECK_SLOT が無い・$HOME/dotfiles/agent/tests/machine_check_slots.py が在る
        │  → `uv python find` の python へ exec(pid P のまま)
        ▼
python machine_check_slots.py run --label "doeff-linter <mode> <作業 dir>" -- <自分> <同じ引数>(pid P)
        │  正本: 祖先の列で入れ子なら取らない / 枠を取る(埋まっていれば保持者を名乗って待つ・上限で 75)
        │  子を Popen(印 DOEFF_LINTER_CHECK_SLOT=1・PR_SET_PDEATHSIG = 包みが死んだら子も死ぬ)
        ▼
doeff-linter(子)── 印が在るので enter は何もしない → lint → 終われば包みが枠を解く
        └ --commit-hook の子の linter(editor-json × 2)は印を継ぐので正本を通らない
```

| repo | 着地 | 変えた物 |
|---|---|---|
| dotfiles | L5630 f333bd2f | 正本 `run` の包みの死で命令も死ぬ(die_with_wrapper)・名乗りに会話の id(command_label) |
| doeff | L2149 86ecd134 | `packages/doeff-linter/src/check_slot.rs`・main.rs の enter の呼び・tests/check_slot.rs |
| agora-controllers | L6327 4e2d1721 | doeff-min-commit = 86ecd134・日次の段 doeff-lint-whole を `run` で包む |
| dotfiles | L5640 f8a49209 | 盲検 A の直し: 日次の段 doeff-linter-agentcli と commit の hook が linter の 75 を未実行と読む |
| doeff | L2153 a3c8aa86 | 盲検 A・B の直し: 75 を README と --help の公開の終了コードに書く・検 2 つを強める |

## 2. 責務

| id | 実体 | 持つ知識・判断 | 隠す知識 | 公開 |
|---|---|---|---|---|
| M-canon | dotfiles agent/tests/machine_check_slots.py + .json | 枠の値・取り方・入れ子の判定・`run` の寿命・上限の終了コード EXPIRED_RC | lock の file の名と中身・ポーリング・祖先の判定・内部の印 AI_CHECK_SLOT_HOLDER | `run [--label] -- <命令>`(終了は命令のまま・上限で EXPIRED_RC = 75)・`holders` |
| M-gate | doeff src/check_slot.rs | どの mode が重いか・正本の在処・再入の印・python の選び・exec で渡す事 | 枠の値と取り方(持たない) | `weight(Mode) -> Weight`・`enter(Weight)` |
| M-cli | doeff src/main.rs・README | 引数の読み・enter を呼ぶ位置・**公開の終了コード(0・1・2・75)** | 枠の事 | CLI |
| M-callers | 終了コードで判じる呼び手(dotfiles の日次の段・commit の hook・agora-controllers の land_lint_gate)と、判じない呼び手(poe lint・会話の直) | 何を lint するか・自分の上限・**linter の公開の終了コードの読み方** | 枠の値・取り方・入れ子 | — |

盲検の前の主張(design-pre-blind.md・sha256 ca5e4481020a77449f66e6337f7e604566bc0ca893a827244cd856255c146bf2)からの変更は太字の 2 か所 — 盲検 A を受けて、終了 75 を M-cli の公開の契約に置き、終了コードで判じる呼び手はその読み方を持つ、と改めた(4 節)。

## 3. シナリオと、事前の予測と実測

| id | 軸 | 変更 | 事前の予測(design-pre-blind.md) | 実測 | 判定 |
|---|---|---|---|---|---|
| S1 | hardware | 機体ごとに K を変える | json の値だけ | K = 1 の宣言の写しで、linter を変えずに待ち・名乗り・空いた拍の走りを観測。値を linter に写す変異は値の写しの検が赤 | 予測どおり |
| S2 | storage | 置き場の dir を替える | json の dir だけ | 置き場 = 一時の dir の宣言で同上。dir を写す変異は赤 | 予測どおり |
| S3 | effects | 取り方を変える | M-canon だけ | 取り方の違う正本の版 2 つ(c97aefc38 = 包みの死の扱いが無い版・f8a49209)で、linter を変えずに同じ検 6 本緑。取り方(`slot-` の組み方)を写す変異と、入れ子の判定(内部の印)を写す変異(盲検 B)は赤 | 予測どおり。ただし盲検 B は、直す前の検が入れ子の判定の写しを捕まえない事を示した(4 節 B) |
| S4 | effects | 新しい重い mode | M-cli の引数と M-gate の Mode / weight | 重い mode を軽い側へ倒す変異は、結合の検 6 本は通り、単体の検 repo_scans_take_a_slot_named_by_their_mode が赤 | 予測どおり(捕まえるのは単体の検) |
| S5 | concurrency | 検の中の検 | 2 つ目を取らず止まらない | K = 1 で `--commit-hook` の親が唯一の枠を取り、子の linter 2 回は正本を通らず(通ったのは 1 回・印 1)、1 秒で終了 0。pytest 相当の包みの中の命令は待たない(dotfiles の検)。印を継いだだけの linter は内側と読まれず待つ | 予測どおり |
| S8 | effects | 呼び手の時間切れで包みを SIGKILL | linter も死ぬ | 包みを SIGKILL すると命令が死ぬ。die_with_wrapper を外す変異では「包みが死んだ後も命令が走り続けている」で赤 | 予測どおり |
| S9 | effects | linter を使う repo が pin を門の入った版へ上げる(盲検 A) | (盲検の前の主張では M-callers は枠を知らず変わらない) | 終了コードで判じる呼び手が 75 を赤と読んだ(日次の段 Linted(75)・段の終了 75 / commit の hook は終了 1) | **予測外れ** — 4 節 A で直した |
| S6 | distribution | 機体をまたぐ上限 | 対象外 | — | 枠は 1 台の CPU を分ける OS の資源。機体をまたぐ上限は coordinator の task の枠(PER-HOST)が持つ |
| S7 | simulation | 決定的な模擬 | 対象外 | — | 枠は flock・/proc・exec で、doeff の effect を使わない。agora_sim は linter の process を起こさない |

## 4. 盲検の失敗例と直し

盲検の起動の記録は blind-meta.md、未加工の返答は blind-A-response.md・blind-B-response.md。

### A — 終了 75 が公開の契約に書かれず、終了コードで判じる呼び手が赤と読む(成立・実測で再現)

- 何が起きたか: 重い mode の linter は自分の process を正本の `run` へ exec で渡すので、待ちの上限の終了 75 がそのまま linter の終了コードになる。README と --help の終了コードには 75 が無く、dotfiles の日次の段 doeff-linter-agentcli は 75 を `Linted(75)`(赤・段の終了 75)、commit の hook は「新しい当たり」(終了 1)と読んだ。どちらも自分の契約では未実行 = 97。
- 漏れの区別: 75 の意味は「待ちの上限で lint をしなかった = 未実行(撃ち直せる)」で、枠の値・取り方・入れ子の判定は含まない。設計の判断として、これを linter の **公開の契約の拡張**(新しい終了コード)と扱い、M-cli に書く。終了コードで判じる呼び手が新しい終了コードの読み方を持つのは契約の拡張の当然の帰結で、隠す知識の漏洩ではない。ただし盲検の前の主張はこの拡張と呼び手の変更を予測していなかった(S9 は予測外れとして残す)。
- 別の直し方と、取らなかった訳: (1) 正本が呼び手の未実行の終了コード(97)を出す — 正本が呼び手ごとの語彙を持つことになり、S3 の前提も変わる。(2) M-gate が exec をやめて 75 を訳す — pid が変わり、呼び手の SIGKILL が linter に届かなくなる(S8)。
- 直し: doeff a3c8aa86 で README と --help の EXIT CODES に 75 = 未実行を書いた。dotfiles f8a49209 で、日次の段は終了が正本の EXPIRED_RC(値は正本から読む)なら Unexecuted(local-timeout — pytest の枠の上限切れと同じ帰属)、commit の hook は 75 の分岐を当たりの分岐より前に置いて 97 で止める(shell は正本を import できないので綴りで持ち、正本の EXPIRED_RC との一致を検が読む)。
- 再検証: 門を持つ本物の linter(86ecd134 の断面)で枠 K = 1・上限 2 秒の HOME から撃つと、直す前の段(f333bd2f)は `Linted(returncode=75)`・段の終了 75、直した後(f8a49209)は `Unexecuted(kind='local-timeout')`・`check-unexecuted:` の行・段の終了 97(evidence/repro-A-…)。dotfiles の検 3 本は緑で、直しを外すと 2 本が赤(正常例は緑のまま)。agora-controllers の land_lint_gate は閉じた語彙(0・1・3・4 以外は動かなかった物)で 75 を「測れなかった(2)」と読む — 未実行の側に倒れるので判定は正しい。理由の文は「linter が動かなかった(終わりの code 75)」で、linter の公開の終了コードの表で引けるので、直していない。

### B — 正本の内部の印を見て門を飛ばす近道が、直す前の検を全部通る(成立・実測で再現)

- 何が起きたか: 「pytest の中の linter は python の起動を払う」のを嫌い、正本が子へ継ぐ内部の印 `AI_CHECK_SLOT_HOLDER` を見て門を飛ばす近道を check_slot.rs に足すと、直す前の検は印 env の無い環境で全部通る。保持者が退いた後に印だけを継いだ子の linter は、K = 1 が埋まっている間に枠の外で lint した(盲検 B の実測 0.8 秒・待ち無し)。
- 漏れ: 入れ子の判定(祖先の列 → 読めない時だけ印 → その枠が今も lock されている時だけ内側)は M-canon の隠す知識。近道は印の名と「印が在れば内側」という判断を M-gate へ写す。
- 直し(doeff a3c8aa86・検を強めた):
  - `a_stale_holder_marker_does_not_let_the_linter_skip_a_full_slot_and_the_expiry_exits_75` — 正本の印を継いだだけ(指す pid は保持者でない)の linter も、埋まった枠を待ち、上限で lint をせずに終了 75。
  - `the_linter_keeps_no_copy_of_the_slot_values` — 正本の `HOLDER_ENV` の名(正本の source から読む — この検に名を写さない)も linter の code に無い事。
- 再検証: B の差分を当てた版で、印 env の無い環境では既存の 4 本が通り(B の主張どおり)、新しい 2 本が狙いどおりの理由で赤(値の写しの検は `AI_CHECK_SLOT_HOLDER` を名指し、印の検は lint が走った事を名指す)。直した版は 6 本緑。枠の包みの中(印 env の在る環境)では、加えて R1 の検と道の検も赤になる。

## 5. 強制の方法

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| 値は宣言 1 つ(R2・S1・S2) | 検: linter の src の全 .rs の code(`//` の後を除く)に本物の宣言の dir の値・宣言の file の名・`waitLimitSeconds`・`slot-`・正本の内部の印の名が無い。check_slot.rs の code に宣言の slots と waitLimitSeconds の数と同じ綴りの語が無い | doeff tests/check_slot.rs::the_linter_keeps_no_copy_of_the_slot_values | doeff の cargo test(日次の rust の段 make test-rust・手元) | 別の綴りの写し(数の分解・別名の env)は見ない。それは下の振る舞いの検が補う |
| 枠を越えた linter は待つ(R1・S1) | 検: 正本の写し・K = 1 の HOME で、保持者の間は待って名乗り、空いた拍に枠を取って lint し、終われば解く | tests/check_slot.rs::a_linter_beyond_the_slots_waits_names_the_holder_and_runs_when_one_frees | 同上 | 正本の無い機体ではこの検は赤(未実行を合格にしない) |
| 入れ子の判定は正本だけ(S3・S5) | 検: 印を継いだだけの linter も埋まった枠を待ち、上限で 75 | tests/check_slot.rs::a_stale_holder_marker_… | 同上 | 「印の指す pid が実際に枠を持つ」入れ子は正本の検(dotfiles)が見る |
| 重い mode だけが正本を 1 度・同じ引数で通る(S4・S5) | 型(Weight の enum・Mode の struct)+ 単体 4 本 + 結合 3 本 | src/check_slot.rs の tests・tests/check_slot.rs | 同上 | main.rs が enter より前に返す新しい mode を足すと門を通らない(呼ぶ位置は型で縛っていない — 単体の検の Mode の組み合わせに無い mode は捕まえない) |
| 包みの死で命令も死ぬ(S8) | 検(直しを外すと赤) | dotfiles agent/tests/test_machine_check_slots.py::test_a_command_dies_with_its_killed_wrapper | dotfiles の pytest | Linux だけ(prctl) |
| 終了 75 は未実行(S9) | 公開の契約(README・--help)+ 呼び手の検 | doeff README / main.rs・dotfiles agent/tests/test_doeff_linter_slot_expiry.py | doeff の README・dotfiles の pytest | 新しく終了コードで判じる呼び手が 75 の読み方を持つかは、その呼び手の検に任せる(静的に強制しない) |
| 段の上限の前に枠(日次) | 宣言(段の命令の頭)と註 | agora-controllers .agents/land-queue.toml の doeff-lint-whole | 日次 | 新しい「上限つきの呼び手」が段を包むかは慣習 |

## 6. 実装の証拠と、通常の経路への接続

- doeff の検 tests/check_slot.rs と src/check_slot.rs の単体は `cargo test` に入り、日次の全体検証の rust の段(make test-rust)で走る。手元: a3c8aa86 で結合 6 本・単体 4 本緑(evidence/P-base-check_slot-on-a3c8aa86.log.txt)。86ecd134 の候補で doeff-linter の検の全体 897 本緑・赤 0(evidence/P-full-…)。
- dotfiles の検は dotfiles の pytest に入る。f333bd2f の候補で枠の検 21 本緑、f8a49209 の候補で 75 の読みの検 3 本緑・hook を読む既存の検 11 本緑。
- 実地: 門を持つ断面の linter を本物の HOME で会話の直の形で撃つと、保持者の一覧に `doeff-linter lint agora-controllers-wt-ki-ce35 (c-H600A0RRJ4BXT8053GRH15X3WP)` が載った(evidence/P-live-…)。
- agora-controllers の pin の上げ: pin ちょうどの doeff の組で記録の検 2 file 23 本緑・poe test-changed 選び 6 本 / 撃ち終えた 5 本 / 赤 0 / 予算で止めた 1 本(controllers/custody_body/tests/test_custody_contract.hy -k test_c_ — 日次で測る)。テスト一式は回していない。

## 7. 証拠の一覧(evidence/)

| file | 何の証拠か |
|---|---|
| P-base-check_slot-on-a3c8aa86.log.txt | 直した版の結合 6 本・単体 4 本 |
| mutant-S1S2-copied-dir.{diff,log}.txt | 置き場の値の写し → 値の写しの検が赤 |
| mutant-S3-copied-acquisition.{diff,log}.txt | 取り方の写し → 値の写しの検が赤 |
| mutant-S4-heavy-mode-as-light.{diff,log}.txt | 重い mode を軽い側へ → 単体の検が赤(結合は通る) |
| P-S3-canon-c97aefc38.log.txt・P-S3-canon-f8a49209.log.txt | 取り方の違う正本の版 2 つで、linter を変えずに 6 本緑 |
| mutant-B-holder-env-shortcut.diff.txt・mutant-B-without-holder-env.log.txt・mutant-B-inside-a-held-slot.log.txt | 盲検 B の差分 → 印 env の無い環境で新しい 2 本が赤(既存 4 本は通る)・包みの中では 4 本が赤 |
| mutant-R1-gate-returns-early-on-86ecd134-candidate.log.txt | 門を外した版 → R1 の検と道の検が赤(86ecd134 の候補・HOME が在れば enter がすぐ戻る変異) |
| repro-A-stage-before-f333bd2f-after-f8a49209.log.txt・repro-A.sh.txt | 盲検 A の再現(直す前 Linted(75)・段の終了 75 / 直した後 Unexecuted・段の終了 97) |
| P-S9-dotfiles-expiry-tests.log.txt・N-S9-dotfiles-expiry-tests-fix-reverted.log.txt・S9-dotfiles-fix.patch.txt | dotfiles の 75 の読みの検 3 本緑 / 直しを外すと 2 本赤 |
| P-S5-S8-dotfiles-canon.log.txt | 正本の入れ子の検 3 本と包みの死の検が緑 |
| mutant-S8-no-pdeathsig.{diff,log}.txt | die_with_wrapper を外す → 包みの死の検が赤 |
| P-S5-nested-commit-hook-K1.log.txt・S5-nested-commit-hook.sh.txt | K = 1 で --commit-hook の親だけが正本を 1 度通り、子は通らず 1 秒で終了 0 |
| P-full-doeff-linter-suite-897-on-86ecd134-candidate.log.txt | doeff-linter の検の全体 897 本緑 |
| P-live-direct-path-holders.log.txt・live-direct.sh.txt | 本物の枠の保持者の一覧に会話の直の linter が載った |
| P-pin-86ecd134-records-tests.log.txt・P-agora-controllers-test-changed.log.txt | agora-controllers の pin の上げの確かめ |
| mutants.sh.txt | 変異と正本の版の実験の script |

## 8. 型や hook が証明しない事

報告の JSON の構造の検査は、盲検の実施・model の実際の出自・この本文の意味の正しさを確かめない。盲検の model は Agent tool の引数の値(opus・xhigh)で、実際の model と effort は観測していない(blind-meta.md)。受入の条の意味の判断は依頼者(card の受け入れ)に渡す。
