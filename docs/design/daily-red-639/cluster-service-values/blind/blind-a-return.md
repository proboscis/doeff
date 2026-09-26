<!-- 盲検 A の返答(未加工)。起動: Claude Code の Agent tool・subagent_type general-purpose・model opus(claude-opus-5-5)・新しい文脈(fork でも resume でもない)。
     effort は起動口が受け付けない(指定できない)。機体 = agentd-pool-1(pod)。agentId a5483ee884667f40f・所要 503.7 秒・subagent_tokens 136918・tool_uses 33。
     codex(gpt-6-astra・low)は起動口 cx が「この機体は宿名を宣言していない」で断った(evidence/blind-launcher-cx-refused.log)。
     ツールの出力が各行に付けた 2 字の字下げだけを外した。 -->

## 反例: 実行先だけが持つ記録の設定 `record` を、service の宣言の `:config` に書く

**要旨(実測)**: `run.config` の `record` 欄は「業務の引数ではなく組み立て側の設定」です。この知識を持つのは実行先の `job_entry.run-service` と `replay_main` だけで、1 process の投影 `service_model.service-program` と宣言の値 `service` は持っていません。そのため同じ宣言が、1 process の main と cluster の片方でしか動きません。宣言の 1 行の変更で済むと予想された変更が、M2(と、正しく直すなら M1・M3)の変更を必要とします。

### 1. 変更要求と、それが必要になる理由

- **要求**: 「effect の記録を service の宣言の `:config` に書いて常時有効にし、宣言を正本にする」。
- **理由(実測で裏付け)**: 記録は README の手順で、動いている定義の `run.config.record` を直に `PUT` して付けています(agora-controllers `controllers/worker/README.md` 404〜417 行)。ところが `declare --apply` は `run` を宣言から作り直すので、記録が黙って消えます(同 README 125・417 行に ⚠ が書かれています)。
  - `declare.spec-for-update` に「いま `run.config` = `{"n":1,"record":{…}}`、宣言は `record` なし」を渡すと、PUT の `run.config` は `{"n": 1}` になりました(実測)。
  - この事故を無くすには、宣言そのものに `record` を書くのが自然です。

### 2. 関係する元の主張・前提と、予想に反して変わるもの

- **主張 S-DIST**: 「同じ宣言のまま cluster へ出す」。予想では変わるのは M4 だけで、M1・M2・M3 は変わりません。
- **主張 S-SIM**: 「同じ宣言の値を `system-main` で回す。宣言と本体は変わらない」。
- **M2 の不変条件**: 「config = 宣言の config に上書きを重ねた物。鍵が本体の引数に無ければ失敗する」。
- **M1 の docstring**(`service_model.hy:76`): 「program は設定の鍵を引数に受ける」。
- **前提は守っています**: 本体は module の最上位の `defk`、鍵は文字列、値は JSON にできます。`record` は固定版の M3 に既にある機能で、新しい前提は置いていません。doeff-cluster の README も「`run.config` に `record` 欄を足す」と案内しており、`run.config` は M2 が `:config` から作る物です。つまりこの例は契約の範囲内です。
- **実際に変わる物**:
  - M2 の `service-program`(`service_model.hy:109-112`)。いまは config の鍵を全部そのまま引数に渡しています。
  - 正しく直すなら、さらに M1(`record` を `config` から分けた欄にする)、M3(`job_entry.hy:79`、`replay_main.hy:34`)、coordinator の `cluster_policy.spec-of-declaration`(`--config` へまとめて渡す箇所)も変わります。
- **逆向き**: 本体の引数に `record` という名を使うと、1 process では動き、cluster では毎回落ちます。M4 の引数名が、M3 が隠している知識に縛られています。

### 3. どの知識がどこへ漏れているか(契約拡張との区別)

- **隠すはずの知識の漏洩**: M3 の知識である「記録の層」の設定が、M1 が本体の引数と定めた `config` の名前空間に同居しています。その結果、次の箇所がこの予約を知る必要があります。
  - M2(外す処理を足す必要がある)
  - M1(`record` を拒むか分けるか)
  - M4(引数に `record` という名を使えない)
  - 運用者(`declare --config` に書き足す必要がある)
- **共同の不変条件(宣言も検査もされていない)**: 「(a) 1 process と (b) 実行先で、同じ config から同じ引数を作る」。これは `(factory #** (dfor … (hy.mangle k) v))` という同じ処理の 3 つの写しで保たれています(`service-program`・`run-service`・`replay_main`)。`record` を外す処理は、そのうち 2 つにしかありません。
- **契約拡張との区別**: M1 に `:record` 欄を足して M2〜M3 へ別の欄で運ぶのは、正当な公開契約の拡張です。複数の file が変わることは欠陥ではありません。欠陥は、いまの契約のまま、予想で M4 だけとされた変更が M2 の変更を要すること、そして (a) と (b) の食い違いに、どのテストも入口の検め(probe)も気づかないことです。

### 4. 最小の再現手順と観測結果(すべて実測)

作った file は `/tmp/blind-cluster-a/cx/recsvc.hy`(`ledger`: 本体 `[n]` で `:config {"n" 1 "record" {…}}`、`tally`: 本体 `[record n]` で `:config {"record" True "n" 1}`)と `drive.hy`・`drive_declare.hy` です。

| 手順 | 命令(要点) | 終了コード | 結果 |
|---|---|---|---|
| 基準 | 依頼文の 4 命令 | 0・0・0・0 | 6 passed / 1 passed / `test_turns` 6 passed / M5 9 passed |
| (b) の JSON と (a) の実行 | `cd cx; uv --project D run --no-sync hy drive.hy` | 0(例外は driver 内で捕捉) | `ledger` の JSON の `run.config` に `record` が入る。`system-main` は `TypeError: ledger_program() got an unexpected keyword argument 'record'`。`tally` の `system-main` は `[101]` |
| (b) の実行先 | `hy -m doeff_cluster.job_entry service --factory recsvc:ledger_program --env recsvc:plain_env --config '{"n":1,"record":{…}}'` | 0 | 記録係が起動し「が終わった: 2」 |
| (b) の逆向き | 同じ命令で `tally`、`--config '{"n":1,"record":true}'` | 1 | `TypeError: tally_program() missing 1 required positional argument: 'record'` |
| 入口の検め | `job_entry probe --factory recsvc:tally_program …` | 0 | 「読み込めた」— 起動前には気づかない |
| 実アプリで確認 | `/tmp/blind-cluster-a/ac` に写した木で、`turns.hy` の `turn-runner` の `:config` に `"record" {…}` を 1 つ足し、`test_turns.hy` を実行 | 1 | 4 failed / 2 passed |
| 変更の理由 | `drive_declare.hy`(`spec-for-update`) | 0 | PUT の `run.config` = `{"n": 1}`(記録が消える) |

`test_turns.hy` の 4 本の内訳は次のとおりです。

- 3 本(`test_whole_system…`・`test_failed_agent_task…`・`test_resent_request…`)は `system-main` の中で `unexpected keyword argument 'record'` になりました。これが欠陥です。
- 残る 1 本(`test_declaration_carries…`)は、JSON の config を完全一致で比べているための失敗です。テスト側の直しで済む想定内の失敗で、欠陥ではありません。

`test_turns.hy` の実行には `-p no:cacheprovider` を付けました(写した木に cache を書かないため)。2 つの checkout は固定版のまま、`git status` も空です(確認済み)。

### 5. 未確認の前提と不足する情報(推測と実測の区別)

- **推測**: 実際の cluster では `tally` 型の本体が「2・4・8…60 秒」の間隔で起動と失敗を繰り返すはずです。根拠は README の記述で、coordinator と worker を使った実走はしていません。
- **推測**: 運用者が本当に `record` を宣言へ移したがっているか、また sim / 1 process の側でも記録したいかは、agora-controllers README の ⚠ からの推定です。issue などの一次資料は確認していません。
- **実測で分かった細かい点**: `service-program` は `Expand` を返すだけで、その時点では失敗しません。TypeError は Program を走らせた時に出ます。M2・S-SIM の「Program を組む時に失敗する」は正確には「走らせた時」です。黙って既定値になることはありません。
- **不足**: 設計者が `record` をどの責務の持ち物と見ていたかの記録は、読んでよい範囲の外なので確認していません。全数のテスト、他の呼び手(`agora_sim/world.hy` 等)への影響の実走もしていません。`world.hy` は doeff の `service-program` を使っているので同じ挙動になると推測しています。
- **別の候補(本題の外。実測済み)**: M5 の本体の特定方法(`(service "名" <symbol>)` と同じ module の `defk` を対応させる)は、M1 の契約が許す「別 module の関数を名指す宣言」を見落とします。
  - 再現: `/tmp/blind-cluster-a/m5root` に「本体(`open` あり)を `controllers/x/bodies.hy` に置き、`controllers/x/decl.hy` で import して宣言する」例と、普通の service を 1 本置きました。
  - 結果: `service-body-report` は `ServiceBodyReport(bodies=1, violations=())` を返し、緑になりました(`drive_m5.hy`、終了コード 0)。
  - このため S-EFF・S-STORE の「どの dir でも M5 が赤にする」は、宣言と本体を別 module に分けると成り立ちません。
  - 原因と見られるのは、macro が暗黙に持っていた「本体と宣言を 1 つの form に置く」という責務です。この責務は置き換えの表に載っていません。
