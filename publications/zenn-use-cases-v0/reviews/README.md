# 記事別の精査と修正（2026-09-16）

ユーザーが指摘した基準に従い、記事ごとに独立した担当エージェントを割り当てる。3担当ずつ実行する。

## 必須確認

1. 合成する処理は`@do`で書き、呼び出しを`yield`でつなぐ。非同期ループ全体を`async def`へ逃がし、ひとつの`Await`に隠さない。
2. `Await`はSDKなど実際のawaitableとの接点で使用し、公開APIのimport・引数・戻り値・ハンドラ設置を実装と照合する。
3. すべての紹介機能にコードを置く。モックの成功と、実サービスで確認した範囲を区別する。
4. プロバイダ差、未実装機能、継続や状態の契約を誇張しない。
5. 図はimagegenによる白背景の平面的なダイアグラムと短いコード。コードと矢印を記事に対応させる。

## 担当・確認状態

| 記事 | 担当エージェント | 内容の精査 | 画像差し替え |
| --- | --- | --- | --- |
| llm | review_llm | 修正・検証済み | 更新・目視確認済み |
| vm | review_vm | 修正・検証済み | 更新・目視確認済み |
| handlers | review_handlers | 修正・検証済み | 更新・目視確認済み |
| main | review_main | 修正・検証済み | 更新・目視確認済み |
| agents | review_agents | 修正・検証済み | 更新・目視確認済み |
| image | review_image | 修正・検証済み | 更新・目視確認済み |
| games | review_games | 修正・検証済み | 更新・目視確認済み |
| events | review_events | 修正・検証済み | 更新・目視確認済み |
| traverse | review_traverse | 修正・検証済み | 更新・目視確認済み |
| time | review_time | 修正・検証済み | 更新・目視確認済み |
| color | review_color | 修正・検証済み | 更新・目視確認済み |
| di | review_di | 修正・検証済み | 更新・目視確認済み |
| composition | review_composition | 修正・検証済み | 更新・目視確認済み |
| memo | review_memo | 修正・検証済み | 更新・目視確認済み |
| replay | review_replay | 修正・検証済み | 更新・目視確認済み |
| durable | review_durable | 修正・検証済み | 更新・目視確認済み |
| observe | review_observe | 修正・検証済み | 更新・目視確認済み |
| operations | review_operations | 修正・検証済み | 更新・目視確認済み |
| remote | review_remote | 修正・検証済み | 更新・目視確認済み |
| tooling | review_tooling | 修正・検証済み | 更新・目視確認済み |
| boundaries | review_boundaries | 修正・検証済み | 更新・目視確認済み |
| hy | review_hy | 修正・検証済み | 更新・目視確認済み |
| coroutines | review_coroutines | 修正・検証済み | 更新・目視確認済み |
