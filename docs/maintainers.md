# maintainer の設備(着地・検証)

この文書は doeff の**maintainer 専用**の設備を集めたもの。doeff を依存として使う人・
手元で開発する人には要らない。通常の導入と開発は root `README.md` の
`## Installation` / `## Development`(`make sync` と `uv run pytest`)で足りる。

## main への着地(2026-08-09〜)

公開(origin main への push)は land queue の窓が行う — 変更は branch を push して `ai land request` で列へ(直 push は guard が block)。

upstream 規約(検証済みでしか main を進めない)は 2026-08-09 から窓の battery が機械強制する。

窓を回す機体は `.agents/land-queue.toml` の `policy.runner` が決める — 2026-09-14 から `"pod"`(k3s の頭脳の pod の land-api・実行者 `land-runner@agora-1`・押す鍵 = Secret `land-key-doeff` の deploy key)。登記した Mac は「この repo の窓は pod が回す」と名乗って見送る。Mac の窓へ戻すには runner の行を消す(agora-redesign #71 便 2d)。

## `.agents/land-queue.toml` は maintainer の設備

同 file は着地の窓の宣言で、maintainer の検証設備を持つ。`gate.full`(日次検証便
`ai land verify` と full_paths 便だけが使う全量検査の逐語)・`gate.timeout_s`・
focus mode の方策・`UV_CACHE_DIR` の lane 分離・実行面(zeus / k3s)の指定・
`[test-admission]`(広範囲テストの走行の受付)がここに集まる。**doeff を使う・
doeff を手元で開発するのに、この file を読む必要はない。**

## 追跡対象の衛生(雛形と fixture)

`.gitignore` は実行時の状態(DB / SQLite / agent home / log)と生成物を除外する。
`scripts/check-repo-hygiene.sh` が、除外の対象に当たる file を誤って追跡していないか
検査する。

`.env.*` や `*.jsonl` に当たる file を**意図して追跡したい**時(設定の雛形、テストの
fixture)は、`.gitignore` を緩めるのではなく、同 script の allowlist(`case` 節)へ
path を足し、**それを使う README かテストを同じ行のコメントに書く**。除外の規則は
repo 全体の既定のまま保ち、例外は 1 箇所で名前と用途つきで宣言する。

2026-09-15 時点で、この allowlist に載せるべき file は 1 件も無い。

`python/` の規則は **repo 根の一時作業 dir だけ**を指す(`/python/`)。package の中の
`python/`(例 `packages/doeff-indexer/python/`)は正規のソースなので対象外。
