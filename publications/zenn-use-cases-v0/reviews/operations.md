# operations記事の実装照合

- 担当: `review_operations`
- 確認日: 2026-09-16
- 対象: `doeff-operations.md`、専用例`examples/operations.py`
- 参照基準: `REVIEW-INSTRUCTIONS.md`、`doeff-patterns`、`doeff-runtime`
- 状態: 記事・例・画像仕様を修正済み。画像の実生成は親担当へ委譲。

## 修正した説明とコード

1. **Gitの対象branchをそろえた。** 旧例は`GitPush(branch="docs-update")`だけを指定し、commitとPR作成は現在のbranchを対象にしていた。`GitPush`はcheckoutではないため対象不一致になり得る。新例はpushとPR作成のbranch指定を省略し、現在のbranchを使う。テストでpushの記録とPRのbranch一致を確認した。
2. **差分とcommitの対象をそろえた。** `GitDiff(staged=True)`と`GitCommit(all=False)`を使う。記事に「作業用branchで、必要な変更だけstage済み」という入力条件を明記した。`GitPull`は変更を加える前の別の`@do`へ分けた。テストハンドラが実際にstageやbranchを操作しないことも明記した。
3. **文章だけだった合成にコードを追加した。** `propose_and_notify`が`yield propose_change(...)`、`yield signing_key()`、`yield Notify(...)`を順に実行する。PR URLへHMAC署名を作り、通知へURLと署名を渡す。差分なしでは秘密情報の取得も通知も依頼しない。
4. **戻り値の差を明示した。** Secretのメモリ版とCloud版は`bytes`、環境変数版は`str`。`signing_key`は取得結果を`bytes`へそろえる。`Acknowledge`も共通の待機保証ではなく、testingでは設定次第で`True`、console/logでは`False`になる。
5. **log通知の翻訳先までコードで示した。** `log_handler`は`Tell`を発行するため、外側のWriterとStateで収集する。`notify_progress()`を変更せず、3件のログの順序と`False`の確認結果を検証した。
6. **本番構成を明示した。** Git、console通知、Cloud Secret Managerに加え、Cloudのクライアント解決が必要とするReader・Try・Writer・Stateを取り付ける。クライアントを利用者が作る例にはせず、Cloudハンドラの内部で遅延解決する構成とした。構成の定義だけを評価し、本番Programは実行していない。
7. **全コード行に日本語コメントを付けた。** 記事の9つのPythonブロック、専用例、画像用コードの各実質行について、目的と期待する結果・動作を記述した。閉括弧だけの行は直前の説明に含めた。

## 実装に残る不一致を隠さない

記事を実際に合成して、次の3件を再現した。runtimeやpackagesは変更していない。

| 箇所 | 現行実装 | 確認した例外 |
| --- | --- | --- |
| 通知のtesting/console/logが未対応effectを受ける場合 | 引数なしの`Pass()` | `TypeError: Pass.__new__() missing 2 required positional arguments: 'effect' and 'k'` |
| `env_var_handler`で秘密情報が見つからない場合 | 削除済みの`Delegate()` | `RuntimeError: Delegate was removed: use 'yield effect' to re-perform in handler body` |
| Secretの`in_memory_handlers`が未対応effectを受ける場合 | 削除済みの`Delegate()` | 同上 |

通知側は、記事で明示した`notifications_only`の`@do dispatch`が通知の3種類だけを既存ハンドラへ渡し、それ以外は`Pass(effect, k)`で外側へ渡す。これは既存パッケージにそのまま任意の合成能力があるという主張ではなく、現行APIを使って記事側で選別する例である。

Secretのメモリ版は最も外側へ置き、Gitと通知を内側で解釈する。環境変数版は値が見つかる経路だけを検証した。旧記事の「環境変数にない秘密情報は自動でSecret Managerへ委譲する」という説明は削除した。製品側で委譲実装の修正が必要なことを親担当へ通知済み。

## 実装根拠

- `packages/doeff-git/src/doeff_git/effects/local.py`: `GitDiff.staged`、`GitCommit.all`。
- `packages/doeff-git/src/doeff_git/effects/remote.py`: push/pullのbranchは任意指定。
- `packages/doeff-git/src/doeff_git/handlers/production.py`: commit時の`git add -A`の条件、現在のbranchの解決、git/ghの呼び出し。
- `packages/doeff-git/src/doeff_git/handlers/testing.py`: 操作の記録形式、固定応答、PRHandle生成。
- `packages/doeff-notify/src/doeff_notify/handlers/testing.py`: 通知ID・thread ID、auto_acknowledge、未対応effectの`Pass()`。
- `packages/doeff-notify/src/doeff_notify/handlers/log.py`: `Tell`への翻訳と確認結果`False`。
- `packages/doeff-notify/src/doeff_notify/handlers/stdout.py`: 標準出力と確認結果`False`。
- `packages/doeff-secret/src/doeff_secret/testing.py`: CRUD、bytes化、未対応effectの`Delegate()`。
- `packages/doeff-secret/src/doeff_secret/handlers.py`: 名前正規化、文字列の戻り値、未検出時の`Delegate()`。
- `packages/doeff-google-secret-manager/src/doeff_google_secret_manager/handlers/production.py`: クライアント解決、bytesの戻り値、CRUD。
- `packages/doeff-google-secret-manager/src/doeff_google_secret_manager/client.py`: 遅延クライアント構築、Ask/Try/Get/Put/Tellの利用、ADCによる認証。
- `doeff/__init__.py`: `Delegate`は削除済みAPI。
- `doeff/program.py`: `handler`による取り付け、現行VMの`Pass`再export。
- `packages/doeff-core-effects/doeff_core_effects/handlers.py`: WriterとStateの組み合わせ、`writer_log()`。

## 検証

次の専用例を、全workspace packageのローカルsourceを`PYTHONPATH`へ追加した環境で実行した。

```sh
PYTHONPATH=.:packages/doeff-core-effects:packages/doeff-git/src:packages/doeff-notify/src:packages/doeff-secret/src .venv/bin/python publications/zenn-use-cases-v0/examples/operations.py
uv run ruff check publications/zenn-use-cases-v0/examples/operations.py
```

- 専用例: 成功。pullの件数、commitの`all=False`、push/PR branch一致、PR作成とmergeの段階分離、差分なし、通知ID、CRUD、Git/Secret/通知の合成、HMAC署名、str→bytes、通知のTell翻訳を確認した。
- 記事: 正規表現で9つのPythonブロックを取り出し、同じ名前空間へ順番に`compile`/`exec`して成功した。本番構成のブロックはProgramの構築までで、`run`しないことを確認した。
- 不一致の再現: 通知へGetSecret、空の環境変数ハンドラへGetSecret、SecretのメモリハンドラへNotifyを渡し、それぞれ上表の例外になることを確認した。
- 行コメント: Pythonの`tokenize`で記事・専用例の各実質行にコメントがあることを確認し、内容を目視した。
- Ruff: 成功。

実git/gh、PR作成・merge、クラウドSDKの通信、ADC認証、実秘密情報の読み取り、外部通知、エージェント起動は実行していない。検証は固定・メモリのハンドラと構成定義までに限定した。

## 親担当への引き継ぎ

- `operations-visuals.json`を正本として、画像2枚の生成と共通captions/manifestを更新する。記事のaltと本文のcaptionは一致済み。
- package/feature一覧に、通知ハンドラとSecretハンドラの現行の委譲制約を反映する。通知配送先はconsole/log/testingであり、Slack実装が内蔵されているとは書かない。
- `examples/external_workflows.py`にはこの担当記事のGit/通知/Secretの関数は存在せず、今回の変更との同期修正は不要。共有ファイルは編集していない。
