静的検査を通るのに責務を破る実装を 1 つ作りました。差分は /tmp の写しにだけ当て、検査 1〜4 は実際に走らせて全部通ることを確かめました。検査 5 は未実行です。

## 1. 機能要求と差分

**要求**: 各 package の `test_generated_stubs.py` は、.pyi が今の .hy と一致するかを `stale_in` で確かめています。この展開は .hy を 1 本ずつ順に処理していて遅い(doeff-cluster には道具が作った .pyi が 81 本あります。本数は数えましたが、速さは測っていません)。doeff-hy-check がすでに持っている「展開の子の process の pool」で並べて速くしたい、という要求です。`doeff-hy-stub --check` も同じ関数を通ります。

**差分**(`/tmp/ppslot-failcase-a/doeff`・基準は 7e092becc):
```python
# static_check.py(project_closure の後に足す)
def project_each(root, roots, sources, limit: int = EXPANDER_LIMIT) -> dict[Path, Projection | CompileFailure]:
    with _Expanders(limit) as expanders:
        return expanders.expanded(root, roots, sources)

# static_stub.py
def stale_stubs(root, roots, sources):
    owned = [s for s in sources if generated(s.with_suffix(".pyi"))]
    projected = project_each(root, roots, owned)          # limit を渡さない
    return tuple(i for s in owned if (i := _stale(s, projected[s])) is not None)
```
あわせて `stub_of` から `stub_from(projected, source)` を切り出し、`_stale` はその引数を受ける形にしました。既存の `project_closure(..., limit: int = EXPANDER_LIMIT)` と同じ書き方なので、普通の実装者が書きそうな形です。

## 2. 本来の持ち主と、破る契約

- **1 枠の中で並べてよい process の数の持ち主**は、宣言(`machine_check_slots.json`)とその公開の口 `process_width` です。doeff はそれを `SlotHeld.width` で受け取り、`expander_limit(width)` で使うだけ、という分担です。
- 差分では、同じ展開の仕組み(`_Expanders`)の子の数を、宣言のある機体でも doeff の定数 `EXPANDER_LIMIT = 4` が決めます。判断が宣言から doeff の定数へ移ります。破るのは次の 4 つです。
  - R1: doeff に 2 つ目の持ち主を残さない。
  - D2: 定数は既定であって値の持ち主ではない。
  - I1: 1 枠の process 木の並走は宣言の数以下。
  - S1: 宣言を替えればコードを変えずに従う。
- 具体的な場面: pytest の受付が取った 1 枠の中で、6 package の一致の検が展開の子 4 本を起こします(宣言は 2)。

## 3. 各検査が拒否しない理由と、実行結果

1. **M-opacity-guard**: `opened_uses` が見るのは `check_slot.py` の source だけで、差分はこの file を触りません。名の走査は、正本が持つ名の組 9 語と照らすだけで、差分の名はどれも組にありません。→ 実行して **3 passed**。
2. **M-slot-scene**: `take_slot` と main の経路は変えていません。→ 実行して **16 passed・skip 0**(幅の口を確かめる 2 本も走りました)。
3. **幅の使い手**: 配線の検が見るのは「main が `project_closure` へ渡す上限」だけで、新しい呼び手は見ません。→ 実行して **15 passed**。
4. **変更時の品質検査**: → 実行して rc 0、`code-quality: passed (scope=changed・型検査は未実行(fast の段))`。
   - static_stub.py は検査範囲(include)に入っていて解析されましたが、所見はありません。
   - static_stub から static_check への依存は、契約で許された範囲です。
   - static_check.py は include に無いという「登録が必要」の名乗りが出ますが、fast の段では止めません。試作でも同じ名乗りになるはずの file です。
5. **正本の検**: **未実行**です。差分は dotfiles を触らないので、この検の入力は変わりません。

## 4. 検査通過と違反を確かめる手順と結果

- **準備**: 共有の木を `git clone --shared` で /tmp へ写し、7e092becc を detached で出して差分を当て、`uv sync --frozen` しました。HOME 用の dir の下に `dotfiles` を dotfiles の木への symlink として置き、検査 1〜4 を指定どおりの命令で走らせました(結果は 3 節のとおり)。
- **違反の観測**: `sh /tmp/ppslot-failcase-a/probe/run_probe.sh <写しの根>` で走らせます。
  - 写しの home に、宣言(枠 1 本・processesPerSlot は 2 と 1 の 2 通り)と、.hy 8 本 + 道具が作った .pyi を置きます。
  - 正本の `run` で包んだ中(祖先が枠を持つ形)と、自分で枠を取る形の 2 通りで、次を出します: 受けた width、`stale_in` が pool に頼んだ子の数、走っている間の子孫の数の最大。

| 版 | processesPerSlot | width / main が使う数 | stale_in の pool | 子孫の最大 |
|---|---|---|---|---|
| 差分あり | 2 | 2 / 2 | [4] | 6 |
| 差分あり | 1 | 1 / 1 | [4] | 6 |
| 基準 | 2・1 | 同じ | [] | 0 |

- 2 つの撃ち方で結果は同じでした。
- 期待(契約どおりなら): pool は width 以下。実際は宣言と無関係に 4 です。

## 5. 未確認の前提・不足

- **測っていない物**:
  - 実物の pytest の中で、doeff-cluster の一致の検を撃った時の様子。正本の `run` の包みで代わりに確かめました。
  - 速さの改善。要求の動機は推測です。
  - 子孫 6 の内訳。forkserver・resource_tracker・子 4 と見ていますが、推測です。
  - 検査 5。
- **範囲の解釈**: 設計は doeff-hy-check の並走を対象に書かれていて、doeff-hy-stub は名指していません。「範囲外」と読まれる余地が文言の上で残ります。それでも I1 は「1 枠の process 木」全体の約束であり、同じ仕組みと同じ定数を使っています。
- **既知の限界との関係**: 試作の検の docstring は「`process_width` の答えを使わずに幅を自前で決める形」を限界として挙げています。この例はその一種ですが、違う点があります。誰も幅を決めず、共有の仕組みの既定値を黙って受け継いでいて、場所も強制方法の表が挙げない入口です。

共有の 2 つの木は変えていません(`git status` は空)。写しと probe は `/tmp/ppslot-failcase-a/` にあります(差分あり = `doeff`、基準 = `doeff-orig`、観測の script = `probe/`)。

WAIT: work — 呼び手の次の指示
