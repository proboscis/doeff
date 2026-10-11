## 盲検 B の失敗例：照らしの答えを process をまたいで覚える（loader_hooks）

見つかりました。性能の子 card の要求として自然に書かれそうな差分が、提示された検査を実際に撃っても全部通り、そのまま不変条件 B1 を破ります。古い展開が実際に実行されることまで実測で確かめました。

### 1. 機能要求と差分

**要求**：子 card ki-be596ac8276f（性能の条 cefc8ccca）として、「新しい process の照らし（`record_is_current_here`）が、何も変わっていなくても閉包を毎回辿り直している。前の process の答えを置き場に覚えて引け」。

**差分**：2 file・約 100 行。全文は `/tmp/blind-b-v1/scripts/verdict.diff` にあります。
- `code_store.py` に `VERDICT_SUFFIX = ".verdict"` を足す。種類の末尾の持ち主へ正しく足しています。
- `loader_hooks.record_is_current_here` を次のように包む。
```python
    entry = _verdict_entry(record)
    if entry is not None and _remembered_current(entry):
        return True
    current = macro_use.record_is_current(record, _hy_version(), file_sha256)
    if current and entry is not None:
        _remember_current(entry)
    return current
```
- 鍵（`_verdict_entry`）は次の物から作ります。
  - 印・Hy と Python の版
  - `os.environ` の全部
  - `records.record_fingerprint(record)`
  - 記録が名指す module（macros・values・files）の top の package の下にある .py と .hy の全 file の sha256。根は `importlib.util.find_spec(top)` で引き、`os.walk` で歩きます。
- 置き場の読み書きは code_store の `content_key`・`entry_path`・`read_entry`・`write_entry` だけを通します。並びの知識は漏らしていません。

### 2. 本来の持ち主と、侵す契約

- **侵す契約**：B1（`packages/doeff-hy/architecture.hy`）の「digest は process をまたいで覚えない。覚えてよいのは bound_source の純な読みだけ」と、決め「digest は生きた値に依るので file の状態だけでは信じられない」。答え（合う／古い）は digest を比べた結果なので、答えを覚えるのは digest を覚えるより強い覚えです。
- **移る判断**：「記録が今に合うか」は macro_use（`record_is_current`）の判断です。置き場に当たった時は、macro_use を呼ばずに loader_hooks の中で決まってしまいます。
- **移る知識**：「照らしが何に依るか」（何を digest に入れるか — macro_use が隠す知識）の 2 つ目の粗い定義が、loader_hooks の鍵（package の file と環境）に書かれます。表では loader_hooks は「digest の作り方」を隠される側です。
- **移る状態**：process をまたぐ新しい状態（`.verdict`）ができます。B1 が許すのは `.binding` だけです。
- **抜ける形**：素の値（int・str・素の組）は出どころの module を持たず、記録の `files` にも出ません。歩みが持ち主として覚えるのは関数・class・module だけだからです。そのため、macro と別の top の package から import した素の値は鍵に入りません。
- **波及**：`record_is_current_here` は共通の照らしの口です。
  - これを呼ぶ所：.pyc の読み・保存先の code（`_from_shared_store`）・`bytecode_is_current`・doeff-effect-analyzer・`doeff_hy.static_cache`。
  - 全部が古い答えを受け継ぎます。シナリオ S-dist の主張「別の module の値は毎 process 辿り直して古いと判じる」も崩れます。

### 3. 各検査が拒まない理由（実測）

| 検査 | 結果 | 拒まない理由 |
|---|---|---|
| 照らしの検 4 file | base・mod とも test_macro_use_record **49 passed**、他の 3 file **24 passed** | 「process をまたぐ覚え」の条 1〜3 は、別の木の macro・**同じ package** の helper.hy・環境変数です。鍵はちょうどこの 3 つを覆います。解き直しが 0 回であることを見る検は、当たると歩み自体を省くので通ります。process の中の検は fixture が置き場を `off` にしているので、新しい道を通りません。 |
| code-quality の fast | **passed・rc 0** | 契約（`.agents/code-quality.d/dynamic-items-1459.json`）は `doeff_hy_bytecode_guard/**` 全体を 1 つの module（hy-bytecode-import）にしています。そのため loader_hooks から macro_use への越境は見えません。変えた 2 file は include の外で、「登録が必要」の注記だけが出て止まりません。`find_spec` は動的 import の規則の対象（`__import__`・`import_module`・`eval`・`exec`）に入っていません。 |
| commit の hook の semgrep | **rc 0**。固定版の semgrep を直に撃っても所見 0・誤り 0（2 file を走査） | 置き場の答えを覚えることを表す規則がありません。 |
| commit の hook の doeff-linter | **「測れない」で rc 0**（固定版の binary がこの機体に無い） | 補いとして同じ木から自分で組んで（debug build）hook と同じ引数で撃つと、error は 0 でした。mod では warning の DOEFF012 が 2 件増えますが、hook は error だけで止めます。`os.environ` を読む DOEFF004 はこの層では外されています。 |
| 日次の全体検証 | **撃っていない**（全数検査は禁止のため） | 参考として、変えた 2 file だけに pyright を撃ちました。誤りは base と同じ既存の 1 件（`_imp._fix_co_filename`）で、新しい誤りはありません。日次の結果の代わりにはなりません。 |

### 4. 最小の手順と期待・実測

**準備**
1. 固定の木を `git archive` で `/tmp/blind-b-v1/base` と `mod` に展開する。
2. mod に差分を当てる（`cd mod && patch -p1 < ../scripts/verdict.diff`）。
3. それぞれで `make sync` する。
4. `DOEFF_HY_CODE_STORE` と `PYTHONPYCACHEPREFIX` を自分の /tmp の dir に向ける。

**検査が通ることの確認**：上の pytest を両方の写しで撃つ。

**B1 の違反の確認**：`uv run --project <写し> python /tmp/blind-b-v1/scripts/b1_probe.py <空の dir>` を撃つ。最小の入力は次の 3 file です。
- `b1probe_a/macros.hy`：`(import b1probe_b.limits [LIMIT])`、`(defmacro limited [] LIMIT)`
- `b1probe_b/limits.hy`：`(setv LIMIT 3)`
- `b1probe_a/user.hy`：`(require b1probe_a.macros [limited …])`、`(setv value [(limited) …])`

記録の files は `["b1probe_a.macros"]` だけで、b1probe_b は名指されません。手順は「process 1 で記録」→「process 2 で変更なしの照らし（mod はここで答えを書く）」→「LIMIT を 3→30 に変える」→「process 3」です。

| 段 | B1 が求める答え | base | mod |
|---|---|---|---|
| A3：別 package の LIMIT 3→30 の後の照らし | false | false（3 回辿った） | **true（0 回辿った）** |
| B3：同じ変更の後の普通の import | value [30, 5] | [30, 5] | **[3, 5]**（古い展開が実行された） |
| C3（対照）：同じ package の STEP 5→50 | false | false | false |

変更なしの段（A2）は両方 true で、mod は辿った回数が 0 でした。性能の目的自体は果たしています。

### 5. 未確認の前提・足りない情報

- **今の木での実害（未確認）**：本番の macro に「別の top の package から素の値を import して返す」形が今あるかは調べ切れていません。defmacro を持つ .hy に絞った粗い grep では 0 件でした。今の時点では潜在の穴として扱うべきです。B1 の条そのものは形を問いません。
- **code-quality の撃ち方（指示との違い）**：写しに .git が無いので、提示の形ではなく snapshot の形（`--base-repo`・`--paths-file`）と自分の `--cache-dir` で撃ちました。規則が同じに当たるかは code を読んで推しただけです。
- **doeff-linter の版（未確認）**：自分で組んだ binary の組み立ての入力の鍵が、基点の鍵と同じかは確かめていません。
- **初回だけの ERROR（原因未調査）**：新しい写しで初めて撃った時だけ、test_bytecode_guard の 1 件（`test_a_hy_module_read_from_a_current_pyc_does_not_look_up_the_expansion_module`）が ERROR になりました。base でも mod でも同じで、2 回目は通りました。
- **他に鍵から抜ける形（推測・未実測）**：.so の拡張から来た素の値や、cwd などは鍵の外になるはずです。実測したのは別 package の素の値だけです。
- **盲検の範囲**：claims.md と evidence/ は読んでいません。

作った物はすべて `/tmp/blind-b-v1/` の下にあります（差分・探りの script・2 つの写し・linter と semgrep の出力）。linter を組んだ 2GB の dir は消しました。

WAIT: work — 呼び手（親の会話）の次の依頼
