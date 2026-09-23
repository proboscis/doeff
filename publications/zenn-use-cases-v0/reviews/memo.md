# memo 記事の実装照合レビュー

- 担当: `review_memo`（記事別レビューエージェント）
- 対象: `doeff-memo.md`、`examples/memo_policy.py`
- 確認日: 2026-09-16
- 実装版: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 使用基準: `REVIEW-INSTRUCTIONS.md`、doeff-patterns、doeff-runtime
- 状態: 記事・専用例・図の仕様の修正とオフライン検証を完了。画像生成・共通台帳の更新は親担当へ引き継ぎ。

## 指摘と修正

1. **説明だけだった機能を実行可能な例へ追加した。** `MemoExists`の補充なし、L3からの取得とL2/L1への補充、L1でのヒット、全対象層への`MemoPut`と`MemoDelete`、全層不在時の`KeyError`をそれぞれ確認する。最終戻り値だけでなく各保存先を直接観測する。
2. **コスト経路の確認を強めた。** `CHEAP`・`EXPENSIVE`・`IRREPRODUCIBLE`を別キーで保存し、L1には全3件、安価層には1件、高価層には高価・再現困難の2件が入ることを検査した。コストは保存キーの一部にはならないため、同じキーに異なる意味の値と異なるコストを混ぜないと説明した。
3. **誤った削除コストの影響をコードで示した。** 高価な結果を既定の`CHEAP`で削除するとL1からは消えるが、高価層には残る。その後の`MemoGet(EXPENSIVE)`でL1へ復活する。正しいコストで削除すると両層から消えるところまで比較する。誤った削除は明示した比較用の検証であり、推奨の呼び出し方ではない。
4. **TTLの説明を実装に合わせて明確化した。** 標準`memo_handler`は`MemoPut`のコストを経路判定に使うが、保存先には`put(key, value)`を呼ぶ。`ttl`・`lifecycle`・`metadata`は渡さない。「全バックエンド一律ではない」という従前の弱い説明を改め、標準の層で`ttl=0`でも値を取得できることを検証した。保持方針を実行したい場合は、それを解釈するハンドラの設計が必要。
5. **遅延生成を現行実装に根拠付けて紹介した。** `memo_handler`は保存先を返すProgramも受け取り、対象コストの依頼が初めて届いた時に一度だけ解決する。安価な依頼しかない場合は高価層が生成されないことを確認した。
6. **保存方法と実取得を分けた。** HTTP例は`make_memo_rewriter(HttpRequest)`・Memoハンドラ・HTTPハンドラを別々に設置する構成とした。`client_factory`を利用側に要求しない。外部HTTPは実行せず、固定HTTP担当と拒否HTTP担当による実行検証は修正済みの記録・再生記事へリンクした。
7. **各行に目的と期待する動作を説明した。** 本文は11個のPythonブロック、実質122行。専用例は実質129行。図のコードは合計6行。空行、docstring、閉括弧のみの行を除き、tokenizeでコメントの存在を確認し、内容を読んで意味を確認した。
8. **呼び出し方を揃えた。** 合成する処理はすべて`@do`。保存先が公開する`Await`はそのまま`yield`する。アプリケーションのループを`async def`へ移して`Await`で囲む構成は使っていない。`run`を呼ぶ通常の関数は、検証専用の実行境界のみ。

## 実装根拠

| 確認内容 | 根拠 |
| --- | --- |
| コストの一致判定、`EXPENSIVE`は`IRREPRODUCIBLE`も扱う | `packages/doeff-core-effects/doeff_core_effects/memo_handlers.py:118` `_matches_cost` |
| Memoハンドラの公開API | 同ファイル `:134` `memo_handler` |
| 存在確認→取得、ミス時の元の依頼への委譲、保存 | 同ファイル `:178` `make_memo_rewriter` |
| 文字列キーにコストを加えない | 同ファイル `_storage_key` |
| 保存先の遅延解決、インスタンス単位で一度だけ | `packages/doeff-core-effects/doeff_core_effects/_memo_handlers_impl.hy:34` `_ensure-store`、`:148` `memo-handler` |
| 全層不在ならExistsはFalse、GetはKeyError | 同ファイル `:50` `_outer-exists`、`:60` `_outer-get` |
| 書き込み・削除の外側への伝播 | 同ファイル `:73` `_broadcast-put`、`:84` `_broadcast-delete` |
| Existsでは補充しない | 同ファイル `:100` `MemoExistsEffect` |
| Getのヒット終端、ミス時の問い合わせと手前への保存 | 同ファイル `:110` `MemoGetEffect` |
| PutとDeleteの戻り値はNone、TTL等は保存先へ渡さない | 同ファイル `:129` `MemoPutEffect`、`:138` `MemoDeleteEffect` |
| 方針の定義と既定値 | `memo_policy.py`、`memo_effects.py` |
| 保存先のAwaitとメモリ保持、SQLiteでのpickle保存 | `storage/memory.py`、`storage/sqlite.py` |

## 検証

以下を実行した。

```sh
.venv/bin/python publications/zenn-use-cases-v0/examples/memo_policy.py # 階層・コスト・削除・遅延・TTL・SQLiteを検証する。
.venv/bin/ruff check publications/zenn-use-cases-v0/examples/memo_policy.py # 専用例の静的検査を行う。
.venv/bin/python -m pytest tests/effects/test_memo_delete.py tests/effects/test_memo_lazy_storage.py tests/effects/test_memo_no_terminal.py tests/effects/test_memo_rewriter_compute_unhandled.py -q # 関連する既存13テストを実行する。
```

- 専用例は全assertに成功。
- 本文からPythonフェンス11個を取り出し、同じ名前空間で掲載順に`compile`・`exec`して全assertに成功。
- Ruffは成功。
- 関連する既存テストは **13 passed**。ADRの対象を絞った実行による未収集警告が1件出る。全スイートや全ADRの成功は主張しない。
- 不在の`MemoGet`は意図した`KeyError`を出し、`pytest.raises`で捕捉する。doeffが期待した失敗のトレースを表示するが、検証失敗ではない。
- 本文実行後に検証用の名前空間を破棄してGCを実行した。これによりSQLiteインスタンスをPython終了まで保持しない。製品実装は変更していない。
- SQLiteの確認は同一プロセスで新しい保存先インスタンス・新しい`run`を作る範囲。プロセス終了後の再開やクラッシュ耐性はこの例では検証していない。
- HTTPはハンドラの構成を作るだけで、実行していない。実通信・LLM・エージェント・有料API・Redis・MinIOは未実行。

## 画像の引き継ぎ

正本は `memo-visuals.json`。記事のaltとキャプションを一致させた。

- 概念図: handlerを付ける3行とL3/L2/L1の入れ子を対応させる。問い合わせは本体に近いL1から外側へ進む。
- 処理図: L3だけに42を用意し、L1/L2のミスを経て読み出し、L2・L1の順に補充して本体へ返す。
- いずれも白背景の平面的ダイアグラム。3D・写実の要素は使わない。図のコードは全行に日本語コメントを付けた。

## 変更範囲

この記事・専用例・この報告書・図仕様だけを変更した。runtime、共通のREADME/TODO/manifest/coverage、他記事、Git履歴は変更していない。
