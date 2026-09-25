モデル名: GPT-6（詳細なモデル識別子は環境から提示されていません）。

**M5 の既存テストを通りながら、cache の所有範囲と寿命を侵す反例を確認しました。** 全検査の通過を示すものではありません。

1. **機能要求と具体的な変更**

要求は「Hy の再 compile を減らし、繰り返す focused 検査を速くする」です。`test_hy_loader_declared_by_importers.py` の `_fresh_import` を次のように変えます。

```diff
 import ast
+import os

 def _fresh_import(statement: str) -> subprocess.CompletedProcess[str]:
     ...
+    child_env = os.environ.copy()
+    child_env.pop("PYTHONDONTWRITEBYTECODE", None)
+    child_env["PYTHONPYCACHEPREFIX"] = str(SRC.parent / ".hy-import-cache")
     return subprocess.run(
-        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120, check=False
+        [sys.executable, "-c", probe], capture_output=True, text=True,
+        timeout=120, check=False, env=child_env
     )
```

package 内の固定ディレクトリに compile 結果を保存する、通常の高速化実装です。テストの assertion・母集団・設定は変更しません。

[正確な差分](/tmp/wt639-blind-B/tree/blind_b_experiment/candidate.diff)と候補ファイルを実験用ディレクトリに作成しました。既存ソースには適用していません。

2. **本来の所有者と違反する契約**

設計 §3 の M5 は、子 process の cache を **session ごとの私的な置き場**に所有し、木を汚さない契約です。走行全体の bytecode 方針は根の `conftest.py` が所有します。

候補は親の設定を保ちながら、子の保存先を checkout 内の固定パスにします。その結果、cache の寿命と共有範囲が「その session」から「同じ checkout を使う将来の走行すべて」へ移ります。session 終了後も状態が残り、破棄の責務も失われます。

これは単なる保存先の好みではなく、明示された私有性・寿命・木への書き込み禁止への違反です。

3. **提示された検査との関係**

| 検査 | この変更を拒否する根拠がない理由／確認限界 |
|---|---|
| M1：ADR-012 | 調べる対象は巡回処理と judgment の呼び出しです。子 Python の cache 保存先は対象にありません。ただし基準版には既知の文字列不一致があり、通過とは主張しません。 |
| M2：wiring | package 内の Hy import を確かめます。M5 の cache の寿命・配置を検査しません。未実行です。 |
| M3：前提の申告 | 提案された検査は未実装です。候補を拒否する実在の検査として扱えません。 |
| M4：母集団 | e2e の選別と agent の動作が対象で、cache の所有範囲を調べません。未実行です。 |
| M5：fresh interpreter | 実在する動的テストは子の終了コードを確認します。import 成功と、木への cache 書き込みは両立します。下記の 1 ケースで実測しました。提案された静的検査は未実装です。 |
| M6：期限・bytecode 固定 | 期限内に終了します。親の `sys.dont_write_bytecode` と `sys.pycache_prefix` は変えないため、既存の終了時 assertion も通ります。子の保存先はその assertion に含まれません。 |
| M7・M8：失敗名・未実行申告 | 読むのは出力された診断・申告です。ファイルシステム上の cache 所有範囲を検査する処理ではありません。実行はしていません。 |

root の `testpaths` に M5 の package テストは含まれず、日次では `make test-packages` が担当します。その経路の宣言は読みましたが、全体実行はしていません。

4. **最小手順と実測結果**

[再現スクリプト](/tmp/wt639-blind-B/tree/blind_b_experiment/reproduce.py)は、基準版と候補に対し、次の既存ケースだけを別々の pytest process で実行します。

```text
test_python_module_importing_hy_imports_in_a_fresh_interpreter[doeff_agents.adapters.base]
```

各呼び出しは指定された形式です。

```sh
uv run --no-sync pytest -q -p no:cacheprovider <対象ファイル>::<上記ケース>
```

候補は実験用の package 配置へコピーし、`src` は元のソースへの symlink としました。根の pytest 設定・`conftest.py` をそのまま読みます。

| 実行 | 終了コード | 固定 cache 内の `.pyc`：前 → 後 |
|---|---:|---:|
| 基準版 | 0 | 0 → 0 |
| 候補・初回 | 0 | 0 → 131 |
| 候補・別 session | 0 | 131 → 131 |

**検査通過と、session を越えて木に残る共有状態を同時に実測**しました。候補初回の出力は `1 passed, 1 warning` です。警告は focused 実行で ADR 全体を収集していない旨で、抑制していません。

5. **未確認事項と限界**

- 実測したのは M5 の上記 1 ケースです。19 importer 全件、M1〜M7 全体、日次全体の通過は未確認です。
- 永続 cache による速度改善量や競合障害は測っていません。反例の成立は、それらの発生を前提にしません。
- 将来実装される検査が保存先・session 間の分離まで検査するかは不明です。未実装の検査を通るとは断定できません。
- 固定設計の SHA-256 は提示値と一致しました。共有ソースの変更、他会話への連絡、本物の agent 呼び出しは行っていません。