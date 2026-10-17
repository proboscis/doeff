## 盲検 B の結果: 提示された検査を全部通り、所有を侵す実装

失敗例を 1 つ作りました。写した root に、写し元の root の .pyc も hardlink で置く実装です。共有の source は変えず、写し(`/tmp/tmp.P4ndKzifhN/repo`)に差分を当てて実測しました。提示された検査は全部通り、保存先の「使った印」が付かなくなることで、使っている entry を掃除が消すことも確かめました。

### 1. 機能要求と差分

**要求**: 「写した root では、変わらない source の .pyc も前の root から hardlink で持ち越し、root ごとの .pyc の書きと照らしを減らす」。
- 動機は S2 の場面です。保存先が root と別の volume だと `link_pyc` が断り、焼く道具が root ごとに .pyc を全部書きます。写し元の root は同じ volume に在るので、そこからなら hardlink で置けます。
- 焼く道具の頭の註には「木に既に在る .pyc(同じ commit の木を hardlink で写した root)が今の source と macro に合う物は、pool へ渡さずに残す(reused)」とあり、この実装を誘います。

**素直な置き場は検査が断ります(実測)**: `copied-tree` の中で .pyc を写すと、`test_env_tree_placement.hy` が 1 failed / 4 passed になります。検査が写し元に置いた `__pycache__/keep.cpython-314.pyc` が写った木に残るためです。そこで実装者は「木の形は展開と同じ」という約束を守り、.pyc の写しを別の要求に出します。差分は次の 4 file です。

- `env_prepare_model.hy` / `.pyi`: effect を 1 つ足す。
  ```hy
  (defclass [(dataclass :frozen True)] ShareBytecode [EffectBase]
    (#^ str source) (#^ str dest))
  ```
- `env_prepare.hy` の `stage-trees`: `MaterializeTree` の直後に足す。
  ```hy
  (when (= placed.how TREE-COPY)
    (<- shared int (ShareBytecode source.dir (.format "{}/{}" request.root repo.name)))
    (<- (PrepareNote (.format "repo {} の木に写し元 {} の .pyc を {} 個 hardlink で置いた" repo.name source.dir shared))))
  ```
- `env_translation.hy`: `defk shared-bytecode` と handler の節を足す。中身は次のとおりです。
  - `WalkTree` で写し元と新しい木を読む。
  - 写し元の `__pycache__/*.pyc`(`.venv` の下は除く)のうち、同じ dir に同じ名の source(`.py .hy .hyk .hyp`)が新しい木に在る物を選ぶ。
  - 選んだ物を `MakeDirectory` と `LinkFile` で置き、置いた数を答える。

全部の差分は `cd /tmp/tmp.P4ndKzifhN && diff -u env_translation.hy.orig repo/packages/doeff-cluster/src/doeff_cluster/worker/protocol/env_translation.hy` で見られます(他の 3 file も同じ形で `.orig` と比べられます)。

### 2. 本来の所有者と、侵す契約

- **R3・R6・I4(分け合う先は保存先だけ)**: 前の root から .pyc を引き継ぐ 2 本目の経路ができます。どの .pyc を持ち越すかは、写し元の選び(source-key)と木の置き方の翻訳が決めることになります。本来この判断は bytecode の準備(焼く道具と保存先)が持ちます。
- **保存先の状態(使った印)**: 持ち越した .pyc は焼く道具では reused か、計画にも入りません。このため `.code` の entry も `.pyc` の blob も時刻が進みません。掃除(R5)は時刻だけで選ぶので、全部の root が使っている entry を 7 日で消します。
- **bytecode の置き方の知識が漏れる**: `__pycache__/<名>.<tag>.pyc` と source の対応、source の拡張子が env_translation に入ります。
- **隠れた方針に依る判断**: 「焼く道具が照らすから安全」と判断していますが、`code_plan.compile-plan` は「.pyc が既に在る Python の source は計画に入れない」(引き継ぎの時代の前提)という方針を持っています。Python の file では照らしが起きないので、変わった file の古い .pyc が、完成の印の付いた root に残ります。
- **R6 の主張が崩れる**: 「同じ code の .pyc は root をまたいで 1 つの file」が、掃除の後に崩れます。

### 3. 提示された各検査が拒否しない理由(全部、差分を当てた写しで実行)

| 検査 | 結果 | 拒否しない理由 |
|---|---|---|
| ADR の law one-bytecode-store-entry | PASSED | 型は `".code"` と `".imports"` の綴りだけ。差分が綴るのは `".pyc"` と `"__pycache__"` |
| ADR の law the-prepare-tool-does-not-carry | PASSED | 読むのは code_prepare.hy と bytecode_pool.hy の `"--from"`・`"--changed"` だけ。持ち越しは env_prepare と env_translation に在る |
| ADR の残り(worker-defaults・judgments・adr_contract) | PASSED | 文字列の在る無しと、純な判断の反例だけを見る |
| test_env_tree_placement.hy | 5 passed | `MaterializeTree` だけを出す。`ShareBytecode` は別の effect なので通らない |
| test_env_prepare.hy(模擬) | 33 passed | 模擬の世界は木に .pyc を持たない(env_world の註)。保存先も印の file だけで、使った印を模さない |
| test_code_prepare_content_store.py `-k "test_f or test_g or test_h"` | 3 passed | `MaterializeTree` と焼く道具を直に使い、`ShareBytecode` を出さない |
| test_code_store_pyc_links.py・test_code_store_entries.py | 17 passed | code_store は変えていない |
| test_env_store.hy | 5 passed | 掃除は変えていない。違反は「印が付かない」側に在る |

- test_env_tree_placement と test_env_store は 1 回にまとめて走らせ、10 passed でした(5 + 5)。
- ADR の file は丸ごと 1 回走らせ、5 passed でした。
- env の検査では、pytest の plugin(`origin_probe`)で、差分を当てた module(写しの path・`ShareBytecode` 在り)が読まれたことを確かめました。

### 4. 最小の再現と実測

作業木の中から、次の命令で走らせます。

```
C=/tmp/tmp.P4ndKzifhN/repo
PYTHONPATH=$C/packages/doeff-cluster/src:$C/packages/doeff-hy/src uv run --no-sync pytest -q --rootdir=$C <上の検の file>
PYTHONPATH=$C/packages/doeff-cluster/src uv run --no-sync python /tmp/tmp.P4ndKzifhN/scripts/demo_carry.py {control|share} <新しい dir> $C/packages/doeff-cluster/src/doeff_cluster/worker/entry/code_prepare.hy
uv run --no-sync python /tmp/tmp.P4ndKzifhN/scripts/check_stale.py /tmp/tmp.P4ndKzifhN/demo
```

筋書きは本物の git・tar・os.link・焼く道具・`sweep-leftovers` を使い、次の順で進めます。
1. root-1 を展開して焼く。
2. 保存先の時刻を 8 日前に戻す。
3. root-2 を root-1 から写して焼く。
4. 掃除を 1 回走らせる。
5. 写し元の無い root-3 を焼く(lock を上げて source-key が変わった最初の root と同じ形)。

| 観測 | control(今の main) | share(この差分) |
|---|---|---|
| root-2 の焼き | stored 25・rebuilt 1 | ShareBytecode が 26 個置き、stored 0・rebuilt 0・reused 3(.py の 23 個は計画の外) |
| 掃除の後の保存先 | `.pyc` 24・`.code` 145 | `.pyc` 0・`.code` 122(root-2 が使う entry が消えた) |
| root-3 の焼き | stored 26・Hy の compile 0 | rebuilt 23・Hy の compile 2 |
| root-3 の .pyc のうち、root-2 と同じ実体の物 | 26 / 26 | 0 / 26 |
| root-3 の .pyc の新しい byte | 0 | 188,416 |
| root-2 の古い .pyc(source の hash が違う) | 無し | `bulk/__pycache__/m0.cpython-314.pyc`(v2 で変えた file)。完成の印は在る |

### 5. 未確認の前提と、推測・実測の区別

- **時間は近似です**: 7 日続く「持ち越しだけの準備」は、保存先の時刻を 8 日前に戻して近似しました。実際の日数は回していません。
- **機体の違い**: 測ったのはこの Pod の Linux の file system で、APFS では測っていません。
- **動機は仮定です**: Mac の保存先が別の volume か、照らしを省くとどれだけ速くなるかは測っていません。
- **本番の頻度は未測定**: lock の更新などで写し元の無い root がどのくらい起きるかは測っていません。lock の更新で source-key が変わることは、code と模擬の検査から読んだだけです。
- **古い .pyc の影響は推測**: 子の job が bytecode を書かないなら、毎回の起動で m0 を compile し直します。これは検査の註から推した事で、本番では確かめていません。
- **模擬の中の様子は推測**: 模擬の検査で `ShareBytecode` が置いた数が 0 だったのは、env_world の註から推した事で、直接は観ていません。
- **未提示の検査**(linter・semgrep・型検査・.pyi の照合など)は走らせていません。通るかどうかも推測しません。
- **この例の外で気付いた事(どちらも未実測・推測)**:
  - 保存先の .pyc を 0444 にしても、uid 0 で走る書き手は断られません。worker の Pod の実行 user は確かめていません。
  - loader_hooks の `_to_shared_store` は `add_code` を data なしで呼びます。同じ指紋の entry が marshal し直した bytes で上書きされ、R6 の「同じ bytes」が揺らぐ可能性があります。

作業木は変えていません(`git status` は空)。写し・script・観測の結果は `/tmp/tmp.P4ndKzifhN`(124 MB)に残してあり、要らなければ消せます。

status: done — 盲検 B の失敗例を 1 つ作り、検査の通過と違反を実測した
WAIT: work — 依頼元の次の指示
