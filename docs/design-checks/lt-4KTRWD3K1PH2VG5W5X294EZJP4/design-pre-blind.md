# 設計検証 lt-4KTRWD3K1PH2VG5W5X294EZJP4 — 盲検の前に保存した設計と主張

- 依頼: lt-4KTRWD3K1PH2VG5W5X294EZJP4(card ki-d51b7baa38bc)。Mac の worker の実行環境の root(job を走らせる環境)の作り直しが、
  前の root と変わらない file(repo の木と .pyc)を毎回全部書き、消した root の中身を APFS の local snapshot が 24 時間持つ。前の root と
  変わらない file を書き直さない。今の約束(root の鍵・完成の印・掃除の規則・hardlink の写しが写し元の file を書き換えない事)は保つ。
- 完了の範囲: 実装完了まで(doeff L2515 — main 869980ffc・9fe8c330b に入った)。Mac の worker への据え付けは別の段(coordinator・plist の版上げ)。
- 対象版: doeff 9fe8c330bdd86f6d2ff5c36ea643344d62b1ca94。

## 変えた事(実装)

1. 木の写し(worker/protocol/env_translation.hy の tree-delta・link-unchanged・copied-tree): 写し元の root の木の、変わらない普通の file を
   外の `cp -al --parents -t`(GNU の書き方 — macOS の BSD の cp が断った)でなく、doeff の汎用の file の effect LinkFile(os.link)で
   1 file ずつ hardlink にする。git ls-tree の mode 120000(symlink)・160000(submodule)は写さず mirror から置く。写せなければ CopyRefused
   で dest を空にして全部を展開に戻す(今までどおり)。模擬の世界(sim/env_world.hy)は cp の台本を外し、ls-tree の台本を mode つきの形にした。
2. .pyc(foundation/bytecode_pool.hy・doeff-hy の doeff_hy_bytecode_guard/code_store.py): 焼く道具は木の .pyc を、保存先(code_store)の
   中身の同じ file(種類 `.pyc`・名 = .pyc の中身の sha256・mode 0444)への hardlink で置く(`link_pyc` — 置けなければ今どおり書き、
   理由を返す)。保存先の code から置く .pyc は「頭 16 byte + entry の中身そのまま」(`current_entry` が `StoredCode(code, data)` を返す)、
   焼いた code の entry は焼いた .pyc の頭の後ろの bytes そのもの(`add_code(store, key, code, data)`)にして、同じ code の .pyc を root を
   またいで同じ bytes = 1 つの file にする。ADR-DOE-BUILD-002 に R6 を足した(前の木からは引き継がない R3 はそのまま)。

## 責務(module)

| id | 責務 | 持つ知識 | 隠す知識 | 公開の口 | effects |
|---|---|---|---|---|---|
| tree-placement | commit の木を root の下に置く(写し元が在れば変わらない file を hardlink・無ければ展開) | git の差の読み方・mode の扱い・hardlink の置き方・戻し方 | git の命令・LinkFile の使い方・symlink の扱い | effect MaterializeTree(mirror commit dest source)→ TreePlaced(how copied removed replaced reason) | RunProcess(git・tar)・LinkFile・MakeDirectory・RemoveTree・WalkTree |
| copy-source-choice | 写し元の root を選ぶ(env_prepare の copy-root・tree-source) | 元の鍵 source-key | なし(判断だけ) | KnownRoot の列 → TreeSource か None | なし(純な判断) |
| code-store | bytecode の保存先の並び・鍵・使った印・壊れた entry・(新)木の .pyc の置き方 | 並び <dir>/<頭 2>/<残り><種類>・code の鍵・.pyc の blob の名(sha256)・読み取り専用・一時の file からの置き換え | 並びと名の作り方・hardlink と書き戻しの判断 | code_key・current_code・current_entry→StoredCode・add_code(data)・link_pyc(store pyc target)→ 理由か None | file system を直に(標準 library だけ) |
| bake-pool | 焼く物 1 つずつ、保存先の code から置くか焼くかを決め .pyc を置く | bytecode-is-current での照らし・.pyc の頭の作り方 | 保存先の並び(code_store に任せる) | 標準入力の行 → 標準出力の行(failed・stored・reused・unstored) | file の読み・code_store の呼び・子 process の pool |
| store-sweep | 保存先の 7 日使われない file と dir を消す(worker/protocol/env_store の unused-store-names) | 時刻での選び | 種類を読まない | StatTree の答え → 消す名 | RemoveTree |
| import-hook | 子の import で .pyc を照らし、合わなければ保存先から引くか compile して書き直す(loader_hooks) | 照らし | — | Python の import | set_data(一時の file + replace) |

## 守る不変条件

- I1 hardlink の先を書き換えない: 写した木の file・保存先の .pyc の file は、どの書き手もその場で開いて書かない(焼く道具の write-pyc・
  import の口の set_data・Python の import はどれも一時の file から置き換え)。保存先の .pyc は 0444。
- I2 できた木 = その commit を mirror から全部展開した木(symlink・実行の権限・名の空白や改行を含む)。
- I3 .pyc の blob の名 = その中身の sha256(名と中身が食い違わない)。
- I4 前の版の木から .pyc を引き継がない(ADR-DOE-BUILD-002 R3)。分け合う先は保存先だけ。
- I5 保存先の並びを綴るのは code_store.py だけ(R1)。
- I6 置けない時は書く(写しは展開に戻す・.pyc は中身を書く)— 準備は止めない。

## 変更シナリオと事前の主張

| id | 軸 | 変更 | 主張(変えるもの・変えないもの) |
|---|---|---|---|
| S1 | hardware | GNU の userland を持たない別の OS(BSD の cp・別の tar の版)の機体に worker を置く | 木の写しは外の cp を起こさない(LinkFile = os.link)ので、tree-placement も他の module も変えずに写す。変わるのは機体の土台だけ。 |
| S2 | storage | 保存先と root を別の file system に置く(例: 保存先を別の disk)・または APFS の clone で .pyc を置く形に替える | 別の file system は link_pyc が理由を返し bake-pool が書きに戻る(どの module も変えない)。clone に替えるなら code-store の link_pyc の中だけを変え、bake-pool・tree-placement・store-sweep は変えない。 |
| S3 | effects | LinkFile が途中で断られる(link の数の上限・写し元の file が消えた) | tree-placement の中で CopyRefused → dest を空にして展開に戻し理由を残す。copy-source-choice・env_prepare は変えない。 |
| S4 | distribution | 複数の機体で保存先を分け合う | 不適用: 保存先と root は機体ごと(boot.sh の $WORK_DIR/state)。機体をまたぐ hardlink は作れず、link_pyc は理由を返して書きに戻る。 |
| S5 | concurrency | 同じ機体の 2 つの worker の世代と、焼く道具の pool の複数の process が、同じ保存先の同じ .pyc を同時に置く・片方の root が .pyc を書き直す | 名が中身の sha256 で、置き方が一時の file + replace なので、どの module も変えずに正しい中身。片方の root の書き直しは他の root と保存先を変えない(I1)。 |
| S6 | simulation | 模擬の世界(memory の置き場)で写しを確かめる | memory-file-handler の LinkFile(中身の写し)で写しの判断と数を確かめられる。hardlink の実体の性質(I1)は本物の file system の検だけが確かめる。 |

## 強制の方法(実装済み)

| 守る責務 | 強制 | 実装箇所 | 実行経路 | 限界 |
|---|---|---|---|---|
| I5 並びは code_store だけ | ADR-DOE-BUILD-002 の law one-bytecode-store-entry(`.code`・`.imports` の綴りを入口の外で拒否) | docs/adr/defadr_doeff_build_002_one_bytecode_store.hy | pytest(ADR の deftest)・commit の hook | `.pyc` の blob の綴りは拒否の型に入っていない(`.pyc` は __pycache__ の名にも使うので綴りの型で分けられない) |
| I4 前の木から引き継がない | law the-prepare-tool-does-not-carry-from-a-previous-tree(--from・--changed の引数) | 同 | 同 | 引数以外の形の引き継ぎは見ない |
| I1 書き換えない | 保存先の .pyc は 0444(その場で開いて書く手は PermissionError)・検 test_g・test_code_store_pyc_links | code_store.py・packages/doeff-cluster/tests/test_code_prepare_content_store.py・packages/doeff-hy/tests/test_code_store_pyc_links.py | pytest | root の側の写した木の file(.py)は 0444 にしない(root を消す手のため) |
| I2 木 = 展開 | 検 test_env_tree_placement(本物の git・tar・os.link) | packages/doeff-cluster/tests/test_env_tree_placement.hy | pytest | — |
| S1 cp に依らない | 検 test-a-tree-is-copied-where-cp-refuses-gnu-options・test_h | 同上 | pytest | 他の外の命令(git・tar)の版の違いは見ない |
| 新しい byte | 検 test_h(1 commit の差の root の新しい実体の byte ≤ 1/10) | test_code_prepare_content_store.py | pytest | venv の .pyc(uv sync)は範囲の外 |
