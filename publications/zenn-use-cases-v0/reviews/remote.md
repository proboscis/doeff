# remote記事の実装レビュー

- 担当: 記事専任エージェント `review_remote`
- 日付: 2026-09-16
- 照合した版: `d4705914e39740aee98a9f57a4535c463d9479cc`
- 適用基準: `REVIEW-INSTRUCTIONS.md`、doeff-patterns、doeff-runtime。スキルの例より現行実装を優先。
- 編集対象: `doeff-remote.md`、`examples/container_program.py`、共有 `external_workflows.py` の `prepare_remote_files` / `run_in_remote_environment` / `run_in_local_container` の3関数のみ。

## 指摘と修正

### 上位ファクトリを動作可能な入口として紹介していた

`doeff_ml_nexus.interpreters` のimportが現環境では失敗する。Hyの`defk`が`:pre`契約を要求する一方、`make-remote-uv-interpreter` / `make-local-uv-interpreter` の定義には契約がない。

再現された例外は `HyMacroExpansionError`、内側の原因は `SyntaxError: defk make-remote-uv-interpreter: {:pre [...]} is required.`。

- 記事はこの不整合を明記した。
- 呼び出し形そのものは `interpreter = yield make_*_uv_interpreter(...)` → `yield interpreter(program)` が実装の契約どおり。例を消して機能を未説明にせず、定義だけの未動作例として別節に置いた。
- 共有ファイルの関数もimportを関数内に保ち、現版で実行できないことをコメントで示した。
- 動作確認済みの主例は、Dockerfile収集、直接の `DockerRun`、固定ハンドラ、serializerに変更した。
- runtime/packagesは変更していない。文書レビューの範囲を超えて製品不整合を修正していない。

### シリアライズの制限を曖昧にしていた

現環境では `Pure(42)` と `@do` の `calculate()` が、`default_serializer.dumps` / `loads` を往復し、復元後の `run` で42と6を返すことを実測した。以前の「VM次第でskipするテストがある」という説明だけでは、何を確認したかが伝わらなかった。

- 本文と専用例に往復のコードとassertを追加した。
- 未実行Programのシリアライズと、動作中のgenerator/継続の保存を区別した。
- `runner.hy`は復元した本体に裸の`run(program)`を呼ぶ。実行元のハンドラが自動移送されるという主張をせず、純粋な本体を例にした。
- serializerの成功を、Docker/SSH/GPUによる転送成功とは扱っていない。

### 各エフェクトの接続を文字列と依頼の段階まで確認するようにした

- Dockerfile6指示の順序と内容を検査。
- 本物の `docker_build_handler` を通し、`ShellRun`の引数と標準入力にDockerfileが渡ることを検査。固定ハンドラでプロセス起動を置換。
- 本物の `image_push_handler` を通し、`docker tag` → `docker push` の依頼順序と公開先タグの返却を検査。実公開なし。
- ローカルCPU、別ホストCPU、別ホストGPUの `DockerRun` についてhost/gpuの値を検査。本体は固定ハンドラ内で同一プロセス実行する。
- `uv_image` / `uv_gpu_image`を実行してDockerfileだけを収集し、GPU環境変数の有無とuv依存固定指示を確認。
- `Resolve` → `RsyncTo` → `WriteFile` の3依頼、除外対象、本文を固定ハンドラで検査。
- 合成関数は `@do` / `yield helper(...)` で統一。業務フローをasync helperへ逃がす例やclient_factoryはない。

## 実装根拠

- `packages/doeff-docker/src/doeff_docker/effects.hy`: kw-onlyの依頼型、DockerRunのhost/gpu/mounts/env_vars、ShellRunResult。
- `packages/doeff-docker/src/doeff_docker/handlers/dockerfile.hy`: Dockerfile指示→Tell→文字列収集。
- `packages/doeff-docker/src/doeff_docker/handlers/docker.hy`: DockerBuild→ShellRun、ImagePush→tag/push、返却タグ。
- `packages/doeff-core-effects/doeff_core_effects/handlers.py`: writerは外側のstateを必要とする。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/effects.hy`: Resolve/RsyncTo/WriteFileのフィールド。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/docker.hy`: uv_image/uv_gpu_imageとNVIDIA環境変数。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/interpreters.hy`: 2段階yieldの契約と不足しているdefk契約。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/context.hy`: 上位構成が依存検出・転送・依存パスの書き換えを呼ぶ実装。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/handlers/docker.hy`: Ask(serializer)、ファイル経由での移送、runner起動、結果の復元。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/runner.hy`: 復元した本体へのrunと結果の保存。
- `packages/doeff-ml-nexus/src/doeff_ml_nexus/serializer.hy`: cloudpickleへのdumps/loads委譲。

## 検証

### 成功

```sh
# 外部I/Oを固定ハンドラへ置き換え、依頼と実serializerの往復を検証する。
PYTHONPATH=packages/doeff-docker/src:packages/doeff-ml-nexus/src .venv/bin/python publications/zenn-use-cases-v0/examples/container_program.py
# 専用例の静的検査。成功時は All checks passed! を表示する。
.venv/bin/ruff check publications/zenn-use-cases-v0/examples/container_program.py --output-format concise
```

- 上記専用例は成功。上位ファクトリのimport失敗を迂回して成功扱いにすることはせず、実行対象から明示的に分けた。
- 本文のPythonコード8ブロックを同じ名前空間へ順番に `compile` / `exec` し、すべて成功。外部処理は関数の定義だけで、実行したのはassert付きのオフライン部分。上位ファクトリの関数は呼んでいない。
- 本文96実質行、専用例134実質行、共有の担当3関数21実質行に日本語コメントがあることをtokenizeで確認。空行・括弧だけの行・説明用docstringを除外。
- 図仕様のコード2本・計6行も構文と各行コメントを確認。

### 既存テストには失敗が残る

```sh
# 既存テストの現状を確認する。結果は11 passed / 7 failedであり、全成功とは報告しない。
PYTHONPATH=packages/doeff-docker/src:packages/doeff-ml-nexus/src .venv/bin/python -m pytest packages/doeff-docker/tests/test_effects.py packages/doeff-ml-nexus/tests/test_serializer.py packages/doeff-ml-nexus/tests/test_docker.py -q
```

7件はすべて、既存テストの `_run_with_handlers` がwriterの外側にstateを置いておらず、`Get('__doeff_writer_log__')`が未処理になる失敗。

- `TestCollectDockerfile::test_simple_dockerfile`
- `TestCollectDockerfile::test_with_env_and_workdir`
- `TestCollectDockerfile::test_conditional_instructions`
- `TestUvImage::test_basic_uv_image`
- `TestUvImage::test_gpu_image_has_nvidia_env`
- `TestUvImage::test_with_local_deps`
- `TestUvImage::test_rust_extension_detected`

記事のコレクタ例は `state()(writer(...))` を正しく設置し実行成功している。既存テスト/runtimeを変更して検査を緑にする作業は行っていない。

## 実行していない範囲

実Docker、実SSH、GPU処理、コンテナ内ランナー、レジストリへのpush、外部ホストへの転送はすべて未実行。有料APIや外部サービスにも接続していない。上位ファクトリはimport不整合があるため一連の構成処理を実行していない。

## 図の引き継ぎ

`remote-visuals.json`を正本に、記事側のaltとcaptionを更新した。図は平面的な依頼/ハンドラ図とシリアライズの流れ。どの往復を実行確認したかを注記し、未動作の上位ファクトリや実コンテナの成功を描かない。画像生成・共通captions/manifestの更新は親担当。
