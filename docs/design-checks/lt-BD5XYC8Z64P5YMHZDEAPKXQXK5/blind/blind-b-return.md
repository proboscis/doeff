## 盲検 b の報告: 検査を通るのに責務を侵す実装(sequential に進捗の callback を足す)

先に結論です。提示された 4 つの検査をそのまま走らせると全部通ります。それでも「どの Traverse の答えが平らにされるか」(M1 の判断)と flatten の継ぎ方(M2 の知識)が、M3 の handler の中に写し直されます。その結果、正しい program で進捗の数が答えの件の数と食い違うことを実際に走らせて確かめました。実験は `/tmp/blind-b/` の下だけで行い、写しは変えていません。

### 1. 機能要求と差分

**要求:** ex08(`packages/doeff-traverse/examples/ex08_tqdm_progress.hy`)の tqdm の handler には 2 つの不満がありました。
- Pass で f を包む作りなので、件の失敗と skip を数えられない。
- From を重ねると、外の件ごとに bar が立つ。

そこで `sequential()` 自身に `on_progress(label, done, total)` を持たせ、for/do 1 つにつき 1 本の進捗にし、From を重ねた時は組の数で数える、という要求です。

**差分:** 全体は `/tmp/blind-b/proposal-handlers.diff` と `/tmp/blind-b/proposal-handlers-pyi.diff` にあり、当てた物は `/tmp/blind-b/overlay/doeff_traverse/` にあります。要の部分は次のとおりです。

```python
def sequential(on_progress=None):
    ...
    running = []   # 走っている for/do の進捗。空でない間に来た Traverse は「内側の From」と読む
    ...
        if isinstance(effect, Traverse):
            items_iter = [...]                       # total のため件を先に並べる
            if running:                              # 内側の From: 外の件 1 つが n 件に割れる
                progress = running[-1]; progress.total += len(items_iter) - 1
            else:
                progress = _Progress(effect.label, len(items_iter))
            running.append(progress)
            ... 失敗・skip・前段で落ちた件 → progress.step(on_progress)
            ... 成功: if not isinstance(value, Collection): progress.step(on_progress)  # Collection は flatten で割れる
            running.pop()
```

`.pyi` には `def sequential(on_progress: Callable[[str | None, int, int], None] | None = None) -> ProgramHandler` を足しました。

### 2. 本来の持ち主と、侵す契約

- **M1 の判断が M3 へ移る。** 「この Traverse は内側の From で、答えが外の件に継がれる」は、M1 が展開の時に構文から決めています(`_binds-from?`・in-step)。M5 の Traverse は f・items・label しか運ばないので、M3 はこれを動的な文脈から推し直すことになります(同じ handler に、外の件の program の中から戻ってくる)。この推しの元は、M3 が隠すべき「件の program の組み立て(install_self を置き直すこと)」です。
- **M2 の知識が M3 に写る。** 写るのは flatten の継ぎ方そのものです。有効な外の件は n 件に割れ、空なら 0 件になり、落ちた外の件は 1 件のまま残ります。
- **状態が M3 に置かれる。** 重なりの状態 `running` が handler の値の寿命で残り、for/do を越えます。
- **破れる主張と契約:**
  - S1 の「重ねの知識は M1 と M2 に閉じていて、handler は重ねを知らない」が破れます。
  - S2 の「変わらない: M3」が破れます(D2)。
  - 新しい公開の約束「答えの件で数える」を M3 だけでは守れません(D1)。

### 3. 各検査が拒否しない理由

- **実行の検:** `sequential()` を callback なしで使い、見るのは答えだけです(valid_values・番号・失敗・(stage, event)・errors・f が @do か)。差分は答えを変えず、for/do の本体の中に for/do を置く形も、進捗も読みません。足した数え方の処理は callback が無くても走ります。
- **型の検:** pyright が見るのは `.pyi` の引数の型と、展開した `.hy` です。どの Traverse が平らにされるかも進捗の数も、型には現れません。
- **Collection の型:** 差分は `flatten` を呼ばず、`isinstance(value, Collection)` で実行時の class として読むだけです。
- **実行時の確かめ(TypeError):** flatten に Collection でない有効な値が渡った時だけ起きます。D1 は From が 1 つなので flatten が呼ばれず、重ねた From では値が Collection なので起きません。

### 4. 確かめた手順と結果(実測)

命令はすべて写しの dir から実行しました。

**(a) 提示の検査**
- 命令: `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/blind-b/overlay:/tmp/blind-b/plugins uv run --no-sync pytest -q -p no:cacheprovider -p blind_b_probe tests/test_for_do_runtime.py packages/doeff-hy/tests/test_static_check_for_do.py`
- 結果: `16 passed`。差分を当てる前も同じ 2 file で `16 passed` でした。
- 当てた物が読まれたかは記録用の plugin で確かめました: `handlers: /tmp/blind-b/overlay/doeff_traverse/handlers.py`・`sequential signature: (on_progress=None)`。
- 依頼の命令にない旗を 3 つ足しています。`-p no:cacheprovider` は写しに `.pytest_cache` を書かないため、`-p blind_b_probe` は読まれた module の path を記すだけ、`PYTHONDONTWRITEBYTECODE=1` は bytecode を書かないためです。

**(b) 型の検査**
- 命令: `uv run --no-sync doeff-hy-check --root /tmp/blind-b/hyroot --json --no-cache --strict /tmp/blind-b/hyroot/probe.hy`
- 中身: 重ねた From の defk・本体で for/do を `<-` で受ける defk・`(sequential :on-progress report)`。
- 結果: `[]`、exit 0。
- 対照に元の stub で同じ検体を走らせると、`reportCallIssue: No parameter named "on_progress"` などの赤が出ます。検査が stub を読んでいることは、これで確かめました。

**(c) 進捗の数**(`/tmp/blind-b/exp/progress_experiment.py`・`progress_experiment_2.py`)

書き手が確かめそうな形はすべて一致しました。

| 形 | 答えの件 | 最後の進捗 (label, done, total) |
|---|---|---|
| From 1 つ | 5 | (None, 5, 5) |
| From 2 つ | 4 | (None, 4, 4) |
| From 3 つ | 8 | (None, 8, 8) |
| 外の When と内の失敗 | 5 | ('outer', 5, 5) |
| 内の From が空 | 2 | (None, 2, 2) |

食い違う形は次の 3 つです。

| 例 | 形 | 答えの件 | 最後の進捗 |
|---|---|---|---|
| D1 | From 1 つの本体で `(<- sub (for/do (<- b (From [10 20 30] :label "posts")) …))`、本体の値は `(len (list sub))` | 2 | `('users', 8, 6)`(done が total を越える。"posts" の進捗は 1 度も出ない) |
| D1 | 同じ形で、本体の値が `sub`(Collection) | 2 | `('users', 6, 6)` |
| D2 | S2 の例「落ちた外の件を外す」を flatten だけに入れる(実験の中で一時的に差し替え) | 4 | `('outer', 5, 5)`。S2 の変更なのに M3 も直す必要が出る |
| D3 | 同じ handler の値を 2 度使い、1 度目が Exception でない BaseException で抜ける | 2 | 2 度目の進捗が `[('first', 1, 3), ('first', 2, 3)]`(1 度目の for/do の内側として数えられる) |

### 5. 確かめていない前提と、足りない情報

- **推測:** 本体から for/do を使う defk を呼ぶ形や、Reduce の f の中の Traverse も、D1 と同じ仕組みで誤って数えると見ていますが、走らせていません。
- **推測:** 同じ書き方を parallel に入れると、`running` を並列の件で共有して壊れる可能性がありますが、走らせていません。差分は sequential だけです。
- **前提:** D3 は handler の値を run を越えて使い回す使い方を前提にしています。現場でそう使うかは知りません。BaseException の例としては KeyboardInterrupt・asyncio.CancelledError が当たります。
- **未実行:** 提示されていない検査(ruff・semgrep・`packages/doeff-traverse/tests` の他の file など)は走らせていません。件を先に list にした事の影響もそれらでは確かめていません。
- **不明:** 型の検で、当てた stub が根の symlink と PYTHONPATH のどちらから読まれたかは分けて確かめていません。

### 実験の file
- `/tmp/blind-b/apply_patch.py`
- `/tmp/blind-b/proposal-handlers.diff`
- `/tmp/blind-b/proposal-handlers-pyi.diff`
- `/tmp/blind-b/overlay/doeff_traverse/`
- `/tmp/blind-b/exp/progress_experiment.py`
- `/tmp/blind-b/exp/progress_experiment_2.py`
- `/tmp/blind-b/hyroot/probe.hy`
- `/tmp/blind-b/hyroot-orig/probe.hy`
- `/tmp/blind-b/plugins/blind_b_probe.py`

WAIT: work — 依頼元(親の会話)の次の指示
