# 設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4 — root の作り直しで変わらない file を書き直さない(card ki-d51b7baa38bc)

## この文書の目的

読み手が、(1) 何を直して main に入れたか、(2) どの責務がどの知識を持ち、将来の変更がどこに収まると主張したか、(3) 盲検 A・B が
見つけた崩れ方と、それを直した物・直した後に確かめた事、(4) 残る限界、を辿れるようにする。

## 1. 全体の構成(今 ⇒ 目標)

```
今(doeff 5ab25a28f の worker・Mac)                          目標(doeff 9fe8c330b + 盲検への直し F1・F2)
--------------------------------------------------           ---------------------------------------------------------
env_prepare.stage-trees                                      env_prepare.stage-trees(変えない)
  └ MaterializeTree ─▶ env_translation.copied-tree             └ MaterializeTree ─▶ env_translation.copied-tree
        └ RunProcess "cp -al --parents -t"                           └ LinkFile(os.link)× 変わらない普通の file
          (BSD の cp が断る → 全部を git archive で展開)              (symlink・submodule は git archive で置き直す)
env_prepare.stage-bytecode                                   env_prepare.stage-bytecode(変えない)
  └ CompileTrees ─▶ bytecode_pool.bake-one                     └ CompileTrees ─▶ bytecode_pool.bake-one
        └ write-pyc(root ごとに新しい file)                          └ code_store.entry_pyc で bytes を組み
                                                                      code_store.link_pyc で保存先の .pyc の file へ hardlink
                                                                      (置けなければ write-pyc)
code_store(.code・.imports・.binding・.facts)                code_store(+ .pyc の blob — 名 = 中身の sha256・0444)
ADR-DOE-BUILD-002 R1〜R5b・law 3 本                          + R6(.pyc を保存先の file への hardlink で置く)
                                                             + R3b と law only-the-bytecode-tool-places-pyc
```

Mac の root 1 本が新しく書いた私有の byte(10-17 19:53 の root・Mac の会話 c-SFBYQW1RACV4YRDGN4V1Q2R1KH の測り): 木 301 MB・木の .pyc 100.6 MB・
venv の .pyc 25.8 MB・他 1.6 MB = 429 MB。直した後の検 test_h(Mac と同じく GNU の書き方を断る cp を PATH の頭に置く)で、1 commit の差の root の
新しい実体の byte は 28,672(今の形 548,864 の 5.2%)。

## 2. 完了の範囲

- 実装完了まで: doeff L2515(869980ffc・9fe8c330b — main)。盲検への直し F1・F2 は、この記録と同じ branch wt/ki-d51b-design-check の commit「doeff-hy code_store に .pyc の bytes の組み方…」(記録と一緒に land する — main の sha は card ki-d51b7baa38bc に書く)。
- Mac の worker への据え付け(coordinator の版上げ L2517・dotfiles の plist L5869・Mac の据え直し)はこの設計の外の配備の段。

## 3. 責務

design-pre-blind.md の表のまま(tree-placement・copy-source-choice・code-store・bake-pool・store-sweep・import-hook)。盲検の後に変えたのは
code-store の公開の口だけ: `entry_pyc(head, entry) -> bytes` と `link_entry_pyc(store, head, entry, target)` を足し、「分け合える .pyc の bytes の
組み方」(頭の後ろに entry の中身を marshal し直さずに並べる)を code-store が持つ形にした。bake-pool は entry_pyc を呼ぶだけ。

## 4. 不変条件

I1〜I6 は design-pre-blind.md のまま。I4(前の木から引き継がない)の強制に R3b を足した(5 節)。

## 5. 事前の主張と、実測の波及

| id | 軸 | 事前の主張(要約) | 実測 | 修正 |
|---|---|---|---|---|
| S1 | hardware | 外の cp を起こさないので module を変えずに別の userland で写す | GNU の書き方を断る cp を PATH の頭に置いても copy(test-a-tree-is-copied-where-cp-refuses-gnu-options・test_h)。変わらない symlink は symlink のまま | 不要 |
| S2 | storage | 別の file system は link_pyc が理由を返し書きに戻る。clone へ替えるなら link_pyc の中だけ | 置けない時は例外にせず理由を返す(test_a_target_that_cannot_be_linked)。2 つの木が 1 つの file を分け合う | 不要(盲検 A の所見で S2 の「clone なら test_h は新しい inode を数えるので赤の見込み」は推測 — 未実行。限界に記す) |
| S3 | effects | LinkFile が断られたら tree-placement の中で展開に戻す | 写し元の file が欠けると dest を空にして展開し、理由が file を名指す | 不要 |
| S4 | distribution | 不適用(保存先と root は機体ごと) | — | — |
| S5 | concurrency | 名 = 中身の sha256 と一時の file + replace で、同時に置いても正しい | 8 process × 50 木で blob 1 つ・nlink 401・一時の file の残り 0。片方の置き直しは他を変えない(s5-concurrent.log・test_g) | 不要 |
| S6 | simulation | memory の LinkFile で写しの判断を確かめられる | commit だけ変えた準備は app も lib も copy・元の鍵の違う root は写さない(ev-sim.log) | 不要 |
| SA | storage(盲検 A) | (主張 = code-store が「並びと名の作り方・hardlink と書き戻しの判断」を隠し、他の書き手は link_pyc を呼ぶだけで済む) | 崩れた: 分け合える bytes の組み方が bake-pool にだけ在り、import の時の .pyc を寄せる 2 つ目の書き手は組み方を写すしかない。素直に link_pyc を呼ぶと root ごとに別の blob(2 module × 2 root で 4)。marshal し直すと bytes が変わる(blind-a-repro-before.log) | F1: 組み方を code_store の entry_pyc・link_entry_pyc へ移した。2 つ目の書き手は公開の口だけで焼く道具と同じ file を分け合う(test_a_second_writer_shares_the_bake_tools_file_through_the_entry_api) |
| SB | effects(盲検 B) | (主張 = I4 は law the-prepare-tool-does-not-carry-from-a-previous-tree が守る) | 崩れた: 木を置く翻訳に別の effect ShareBytecode を足して写し元の .pyc を hardlink する実装が、全部の検を通った。保存先の使った印が進まず、8 日後の掃除で保存先の .pyc 24 → 0・次の root の新しい byte 188,416・変わった .py の古い .pyc が完成した root に残った(blind-b-repro-before.log) | F2: R3b と law only-the-bytecode-tool-places-pyc。doeff-cluster の source で .pyc の置き場の知識を綴ってよいのは焼く道具の module と模擬の世界だけ。B の木では env_translation.hy を名指して断り、今の木は違反 0(blind-b-law-after.log) |

実際の変更箇所と事前予測の差: L2515 の実装は予測どおり tree-placement(env_translation)・code-store・bake-pool(と模擬の世界・ADR)に収まった。
盲検の後の直し F1 は code-store の公開の口を足し bake-pool の呼びを替えた(意図した公開契約の拡張 — 組み方の知識の移動)。F2 は ADR(強制)だけ。

## 6. 強制の方法

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| R1 並びは code_store だけ | law one-bytecode-store-entry | docs/adr/defadr_doeff_build_002_one_bytecode_store.hy | pytest の ADR の deftest・commit の hook(enforcement 台帳) | `.pyc` の blob の綴りは型に入らない(`.pyc` は __pycache__ の名にも使う) |
| R3 道具は前の木から引き継がない | law the-prepare-tool-does-not-carry-from-a-previous-tree | 同 | 同 | 引数の形だけを見る |
| R3b .pyc を root に置くのは焼く道具だけ | law only-the-bytecode-tool-places-pyc(`"__pycache__"`・`".pyc"`・pyc-path・cache_from_source の綴りを、許した file の外で断る) | 同・enforcement-ledger.json | 同 | 綴りの検。焼く道具の module の別の関数(code_plan の pyc-source など)を import して使う形は見ない。doeff-cluster の外(agora-controllers など)は見ない |
| 組み方は code_store だけ(F1) | 公開の口 entry_pyc・link_entry_pyc と検 test_a_second_writer… | code_store.py・packages/doeff-hy/tests/test_code_store_pyc_links.py | pytest | 呼び手が自分で bytes を組む事は静的に断れない(分け合いが減るだけで中身は正しい) |
| I1 書き換えない | 保存先の .pyc を 0444・検 test_g・test_the_stores_file_is_read_only… | code_store.py・検 | pytest | uid 0 の書き手は 0444 を越える(worker は uid 1000・Mac は利用者の uid で走る) |
| I2 木 = 展開 | test_env_tree_placement(本物の git・tar・os.link) | packages/doeff-cluster/tests/test_env_tree_placement.hy | pytest | — |
| 新しい byte ≤ 1/10 | test_h | packages/doeff-cluster/tests/test_code_prepare_content_store.py | pytest | venv の .pyc(uv sync)は範囲の外 |

## 7. 盲検 A・B

- 起こし方と入力の hash: blind-meta.md。返答: blind-a-reply.md・blind-b-reply.md(加工なし)。
- A の再現: evidence/blind-a-repro-before.log(A の script evidence/blind-a-files/import_time_pyc.py・remarshal.py を設計者が新しい dir で走らせた — naive の書き手で blob 4・規則を写すと 1・marshal し直すと bytes が違う)。直した後: evidence/ev-code-store.log の test_a_second_writer_shares_the_bake_tools_file_through_the_entry_api。
- B の再現: evidence/blind-b-repro-before.log(B の script evidence/blind-b-files/demo_carry.py・check_stale.py を、control = 9fe8c330b の作業木・share = B の差分 evidence/blind-b-files/share-bytecode.diff を当てた写しで走らせた)。直した後: evidence/blind-b-law-after.log(evidence/law_on_tree.py が ADR の判断の関数を、今の木 = 違反 0・B の 3 file を重ねた木 = env_translation.hy を名指して rc 1)。B の検の通過の主張(R3 の law・木の形・模擬・焼く道具・保存先・掃除の検)は B の報告の実測で、設計者は B の差分に対して新しい law が断る事を確かめた。
- 途中の誤り(数えない): 1 回目は B の木に pytest を rootdir を替えて走らせ、conftest が読まれず fixture が無いで落ちた。これは law の断りではないので数えず、判断の関数を直に呼ぶ script に替えた。

## 8. 残る限界(未解決ではなく、範囲の外か補う検の在る物)

- venv の .pyc(root 1 本 約 26 MB)は uv sync が root ごとに書き、root の path が中身に入るので分け合えない。
- R3b は綴りの検で、許した module の関数を import して .pyc を置く形は見ない(盲検 B と同じ形の実装はこの綴りを持つ)。
- import の口(loader_hooks._to_shared_store)は add_code を data なしで呼ぶ。同じ指紋の entry を marshal し直した bytes で書き直すと、次の焼きで
  .pyc の blob が 1 つ増える(中身は正しい)。
- root の大きさの数え(env_upkeep)は hardlink を重ねて数える。Mac では root どうしの分け合いが増え、掃除が実際より大きく見積もる(早めに消す側)。
- S2 の clone への切り替えは試していない(推測)。
