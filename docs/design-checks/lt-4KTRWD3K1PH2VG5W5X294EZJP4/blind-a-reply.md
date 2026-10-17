## 失敗例: job が import の時に書く .pyc も、保存先の 1 つの file に寄せる

崩れる例を 1 件見つけました。共有の公開の口 `link_pyc` を契約どおり呼ぶだけの素直な実装では、例外も誤った中身も出ないまま、root ごとに別の blob ができます。正しく分け合うには、焼く道具(bake-pool)が持つ「.pyc の bytes の組み方」を、import の口(import-hook)へ写すか、保存先(code-store)へ移す必要があります。

### 1. 変更要求と、それが要る理由
- **要求:** root の job が import の時に書く .pyc も、前の root と同じ source なら書き直さず、保存先の 1 つの file への hardlink で置く。対象は、焼く範囲の外の module(宣言の bytecode-entries の閉包の外・後から import する module)です。
- **理由:**
  - job の子は .pyc を書きます。`launch.hy` の CHILD-ENV-ALLOWED は PYTHON* を継がず、bytecode を止める設定もありません。
  - `env_upkeep.hy` の kept-bytes の註に、zeus での測りとして「完成の後に増えるのは job の import が書く .pyc だけ・root の 0.03〜2.5%」とあります。
  - root は 1 日 30 本作り直すので、この .pyc も root ごとに新しい block になり、APFS の snapshot が 24 時間持ちます。今回の要件と同じ動機の次の一歩です。
  - 受入の test_h は子を `PYTHONDONTWRITEBYTECODE=1` で起こすので、この分は測りに入っていません。
- **前提との関係:**
  - root の鍵・完成の印・掃除の規則・I1(書くのは置き換えだけ)・R1・R3 はどれも変えません。import-hook は設計の責務表に在る責務です。
  - したがって、契約の範囲の中で要件の対象を広げる変更です。
  - ただし、元の要件の「変わらない file」に import の時の .pyc が入るかは、設計文書から読めません。入るなら、今の実装の欠けとして別件になります。

### 2. 関係する主張と、予想に反して変わる物
- **設計の主張:**
  - code-store は「並びと名の作り方・hardlink と書き戻しの判断」を隠し、公開の口は `link_pyc(store pyc target)` です。
  - bake-pool は保存先の並びを code-store に任せます。
  - この表から予想すると、import-hook が `link_pyc` を呼ぶだけ(公開の口を使う事と配線)で済むはずです。
- **実際に変わる物:**
  - **import-hook**(`loader_hooks.py`): Hy の `_stored_code`・`_recompile` と、Python の module を CPython が `set_data` で書く道。
  - **code-store**: 組み方を引き受けるなら、新しい口が要ります。
  - **bake-pool**(`bytecode_pool.hy`): 組み方を code-store へ移すなら、呼び方を替えます。
  - **.pyc の頭を組む 3 か所**: `python_bytecode.checked-hash-pyc`(doeff-core-effects)・`records.pyc_bytes`(doeff-hy)・bytecode_pool の `PYC-HEAD-BYTES` と `(+ head found.data)`。
  - **ADR の R6 と R2c**: R2c は「import の口は読んだ .pyc と同じ方式で書く」で、.pyc が無い時は timestamp の頭になり、焼く道具の checked hash の頭と合いません。

### 3. どの知識がどこへ漏れているか
- **分け合いが効く条件:** 同じ code の .pyc が、root をまたいで同じ bytes である事です。
  - code-store が持つのは「bytes の sha256 で名を付ける」事だけです。
  - 同じ bytes を作る規則は bake-pool の `bake-one` と ADR R6 の文に在ります。規則の中身は、頭 16 byte = checked hash・entry の中身をそのまま並べて marshal し直さない・焼いた最初の bytes を entry にする、の 3 つです。
  - つまり、code-store が隠すはずの「分け合いの判断」の半分が、呼び手の 1 つ(bake-pool)に在ります。
- **import-hook の今のやり方は、この規則と逆です:** `current_code` で code を引き、`_imp._fix_co_filename` で root の path に直してから、`records.pyc_bytes` で marshal し直し、頭は timestamp にします。
- **結果:** `link_pyc` を呼ぶだけの実装では要件を満たせません。しかも blob が保存先に 7 日残るので、今より disk を長く持ちます。直し方は 2 つで、どちらも変更が広がります。
  - 規則を import-hook へ写す: 規則が 2 か所になり、片方を替えると、どの検も赤にならずに分け合いが切れます。
  - 規則を code-store へ移す: 3 つの package が変わります。
- **区別:**
  - import-hook から `link_pyc` を呼ぶ事は、公開の契約を使うだけで欠陥ではありません。
  - `set_data` を包む事は配線の変更です。
  - I1 は共同の不変条件ですが、import-hook は既に置き換えで書くので守れます。
  - 漏れているのは「分け合える bytes の組み方」だけです。
  - ADR の law one-bytecode-store-entry は `.code`・`.imports` の綴りしか見ないので、この 2 つ目の組み方を検出しません(実測)。

### 4. 最小の例・再現手順・観測(実測)
環境は Linux、作業木の venv の CPython 3.14t、doeff 9fe8c330b です。

**手順:**
1. `mktemp -d` の下に `rootA/repo/lazy/` を作り、`__init__.py`・`plain.py`(`VALUE = 41`)・`thing.hy`(`(setv value (+ 1 41))`)を置く。`rootB` には A の file を `os.link` で写す(tree-placement と同じく、inode も mtime も同じ)。
2. 各 root で、job と同じく bytecode を書く設定にし、`DOEFF_HY_CODE_STORE=<dir>/store` で `import lazy.plain, lazy.thing` を走らせる。
3. 両方の root の `__pycache__` の .pyc を比べる。
4. 素直な拡張を模擬する: 3 の bytes をそのまま、worker の版の `code_store.link_pyc` へ渡す。
5. 焼く道具の規則を模擬する: `checked_hash_pyc(entry.code, src)[:16] + current_entry(...).data` を `link_pyc` へ渡す。
6. `marshal.dumps(entry.code)` と `entry.data` を比べる。
7. 4 と 5 の行を ADR の `spells-store-layout` に渡す。

**観測:**

| 手順 | 結果 |
|---|---|
| 3 | plain・thing とも A と B で bytes が違う。頭 16 byte は同じで、違いは co_filename(rootA/… と rootB/…) |
| 4 | 保存先の .pyc の blob が 4 つ(2 module × 2 root。分け合えていれば 2) |
| 5 | thing は 2 つの root で blob 1 つ |
| 6 | False。co_filename を直さなくても、marshal し直すだけで bytes が変わる(rootA の .pyc の後ろの bytes と entry の中身は同じ = True) |
| 7 | 4・5 ともに False(law は検出しない) |
| 参考 | 焼く道具の側の test_f・test_g は通った(2 passed・106 秒) |

再現の script は `/tmp/tmp.oLrFeLzA1W/scripts/import_time_pyc.py` と `remarshal.py` です。引数はその dir と作業木の path です。

### 5. 未確認の前提と、足りない情報
- 本番の宣言が bytecode-entries を持つか(閉包の外の module がどれだけ在るか)は確かめていません。agora-controllers の宣言は読んでいません。
- Mac の root で import の時に書かれる .pyc の量は測っていません。0.03〜2.5% は zeus についての註の数字で、私の測りではありません。この要求の費用対効果は、この量で決まります。
- job の子が本番で .pyc を書く事は、許可表と kept-bytes の註からの推論です。動いている worker では観測していません。
- 測ったのは Linux の file system です。APFS と snapshot の効果は測っていません。co_filename と marshal し直しの性質は CPython の物で、file system に依らないと推測しています。
- marshal し直しの差は 3.14t(free-threaded)で観測しました。通常の build で同じかは確かめていません。
- 手順 5 では、macro の照らしを常に真にして通しました(entry は 1 つ)。B の import が保存先から引いたか compile したかは区別していません(どちらでも B の path が入ります)。
- test_env_tree_placement・test_env_prepare・test_env_store・保存先の検・ADR の law の検は走らせていません。

**崩れなかった所と、推測だけの所見:**
- S1・S3 は、コードを読んだ範囲では主張どおりです。
- S2(clone に替える)は、変更が `link_pyc` の中で閉じそうです。ただし test_h は新しい inode の block を数えるので、clone では実際の書きが無くても赤になる見込みです(推測・APFS では未実行)。
- root の掃除の見積もり(`env_upkeep.hy` の roots-bytes・sweep-choice)は、hardlink を重ねて数える前提に立っているのに、設計の責務表に入っていません。Mac で空きが最低を割ると、消しても空かない root を候補が尽きるまで消すかもしれません(推測・未実測)。

**関係する file:**
- `/home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check/packages/doeff-hy/src/doeff_hy_bytecode_guard/loader_hooks.py`(`_stored_code`・`_recompile`)
- `/home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check/packages/doeff-cluster/src/doeff_cluster/foundation/bytecode_pool.hy`(`bake-one`・`PYC-HEAD-BYTES`)
- `/home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check/packages/doeff-hy/src/doeff_hy_bytecode_guard/code_store.py`(`link_pyc`・`current_entry`)
- `/home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check/packages/doeff-cluster/src/doeff_cluster/worker/core/launch.hy`
- `/home/ubuntu/.worktrees/doeff-wt-ki-d51b-design-check/docs/adr/defadr_doeff_build_002_one_bytecode_store.hy`(R6・R2c)

status: done — import の時の .pyc を寄せる変更で崩れる例を 1 件、実測つきで返した
WAIT: work — 呼び手の次の依頼
