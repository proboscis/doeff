## 結論

設計どおりに書いた最小の実装で、予定の検査 K1〜K5 と既存の 2 検査はどれも赤になりません。それでも Q1・Q4・S2 は破れます。原因は sessionhost(host 役の process)が launch 時の `session_env` を自分の sqlite の行に保存し、続きのターンでそれを使い回すことです。これにより、記録サービスの宛先の写しが agentd の再起動をまたいで残ります。

---

### 1. 機能要求と差分

**要求**: Q1/Q2 そのものです。32,768 byte を超える `ai tell` を、どのターンからも送れるようにします。

**差分**: 設計の C1〜C3 と「内部で自由」の範囲にそのまま従った、ふつうの実装です。全文(131 行)は `/tmp/ki-e930-blindB/counterexample.diff` にあります。要点は次のとおりです。

- **effects.py**
  - 集合を 1 か所で定義:`RUNNER_SEAT_ENV_NAMES = frozenset({CONVERSATION_ID_ENV, SEAT_OPENER_ENV, RECORD_URL_ENV})`
  - `record_enabled: bool` を `record_url: str | None` に置き換え、`record_enabled` は property に変更(食い違う 2 欄を作らない)
- **runtime.py**: `record_url=record_sink`(`record-sink-of` を通った値をそのまま渡す)
- **join.hy**: 門 (c) を `(in name RUNNER-SEAT-ENV-NAMES)` に変更
- **judgment.hy**
  - `charter-with-record-env` を新しく追加
  - `incarnation-charter-of` の中で `charter-with-conversation-env` の直後に呼ぶ(seat_env より後なので後勝ち)
- **agentd.hy**: 呼び出しに `settings.record-url` を足す

host 側(`headless.hy` / `launch.hy` / `policy.hy`)は 1 行も変えません。

### 2. 本来の持ち主と、破られる契約

**本来の持ち主**
- 宛先の値は宣言 file の `[record].url` が持ちます。
- `join.record-sink-of` を通った値を、agentd の process が起動時に読みます(P4)。
- 「このターンの席がどの宛先を使うか」は、agentd の charter の組み立てが毎回決めるはずです。

**実際に起きること**(基準コードで確認)
- `headless.hy` 319〜321 行で、launch は `session_env` から OAuth 札だけを落として、残りを行の `launch_overlay` に永続化します。`RECORD_SERVICE_URL` も入ります。
- `continue-headless-process`(448・467〜469 行)は、`overlay.session_env ∪ turn-env` から env を組み直して process を起こします。
- agentd の送信側が運ぶ `turn-session-env-of` は OAuth 札だけで、`turn-charter-of` も記憶まわりの欄だけです。
- claude の headless はターンが終わるたびに process が降ります。このため 2 ターン目以降の普段のターンはすべてこの続きの経路を通り、`next-arm-for-job` は「idle ∧ 同じ家 ∧ 同じ effort ∧ 入れ替え中でない」なら `send` を選びます。

**移ってしまうもの**
- 宛先の値が、sessionhost の永続ストアに席向けの写しとして残ります。
- 「このターンの席が使う宛先」を決める役が、agentd から host の行の再生に移ります。
- この写しの寿命は agentd の process ではなく、session の行に結びつきます。
- `headless.hy` 自身の docstring が「sessionhost の sqlite へ写すと第 2 の正本が腐る」と戒めている形そのものです。

**破られる契約**
- Q1: 「その agentd 自身が追記に使う宛先と byte 同一」
- Q4: 「席向けの写しを作らせない」
- S2: 「agentd と席が同時に新しい値を使う」
- P4 の前提: 値の寿命が agentd の再起動をまたぐ

C1 は字面上は満たしています。C1 の範囲が `incarnation-charter-of` の経路だけで、`send` を除いているためです。ずれは C1 と Q1(「すべてのターン」)の間にあります。

### 3. 各検査が拒否しない理由

| 検査 | 理由 | 根拠 |
|---|---|---|
| K1 | 1 つの settings の中で launch→continue を回すため、行の値と settings の値がいつも一致する。settings を変えて再起動し、host はそのまま残す、という形を検べない | 仕様の文面からの推測(未実装) |
| K2 | 門 (c) が集合を読むので `RECORD_SERVICE_URL=` は ValueError になる | 読解 |
| K3 | 見るのは組んだ charter だけ。差は `{CONV, OPENER, RECORD}` で集合と等しい。host の行と続きの経路は見ない | 読解 |
| K4 | `charter-with-record-env` が seat_env より後に上書きする | 読解 |
| K5 | 名前は定数経由でしか書いていない | /tmp で仕様の regex を当てて実測:差分後の 4 file で 0 件。陽性の見本では 1 件当たり、rule が生きていることも確認 |
| 既存の継承の検査 | host に直接 launch する test で、charter を通らない。差分は host 側に触れない | 読解・未実行 |
| 既存の semgrep `…seat-facing-env` | 差分は 3 語を綴らない | /tmp で実測:差分後の 5 file で 0 件、陽性の見本では当たる |

### 4. 最小の確認手順

1. 共有していない作業樹に差分を当て、K1〜K5 と既存の 2 検査を実行します。**期待: すべて緑**(未実行)。
2. 同じ K1 の fixture を使い、途中で settings だけを差し替えます。
   - settings A(`record_url=URL1`)で launch する → 行の `launch_overlay.session_env.RECORD_SERVICE_URL == URL1`(Q4 の写し)
   - settings B(`URL2`、agentd だけ再起動、host は残す)で `send` する。turn env は `turn-session-env-of` の結果 → host の `headless-send-program` → HeadlessSpawn の env
   - **期待(Q1/S2)**: URL2。**実際**: URL1。
3. 実測の範囲: host 側は差分で変わらないので、基準コードのまま `/tmp/ki-e930-blindB/witness_continue.py` を実行しました。既存の `ContinueWorld` / `run-continue` を流用しています。結果(`run.log`):
   ```
   turn_env from agentd B: {}
   spawned RECORD_SERVICE_URL: http://record-1.example:8874
   byte-identical to agentd B: False
   ```
   agentd 側が charter に URL1 を置く部分は差分の読解によるもので、差分そのものは実行していません。
4. 実機で起こる場面(未実行): Mac の宣言では agentd(`com.masui.acp-agentd`)と host(`com.masui.acp-sessionhost`)が別の unit です。宣言の注記どおり agentd だけを再起動すると、動き続けている claude の会話は古い URL へ本文を置きます。agentd は新しい URL から読むので、本文を失うか、送信が失敗します。

### 5. 未確認の前提と、足りない情報

- **K1 の組み方しだい**: K1 が continue 用の行を空の overlay で作る場合、この最小の差分は K1 で赤になります。その場合、実装者は送信側にも名前を運ばせるはずで、反例は「charter の外にもう 1 か所書き手ができ、K3 から見えない」という別の形に変わります。
- 差分は /tmp の写しに当てて semgrep を回しただけで、コンパイルも test も通していません。`record_enabled=` / `:record-enabled` を組み立てている test は 8 か所あり、書き換えが要ります。
- ADR の中にある構造の検査は提示された検査ではないので、評価していません。`charter-with-conversation-env` の呼び出し行は、意図してそのまま残しています。
- `next-arm-for-job` が `send` を選ぶことと、`sessions-to-retire` が TTL で決まる(ターンのたびに延びる)ことは、読解だけです。
- 未確認の点:
  - pool の pod で agentd だけが再起動される場面があるか
  - 移転後に古い記録サービスへまだ届くか(これで失敗の形が変わる)
  - 席の settings file の `env` ブロックで `RECORD_SERVICE_URL` を上書きできるか(Claude Code の優先順位を確かめていない)

書き込みは `/tmp/ki-e930-blindB/` の中だけで、共有の checkout には書いていません。
