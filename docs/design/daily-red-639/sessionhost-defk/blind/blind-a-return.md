# 盲検 A の返答(未加工)

起動: Agent tool・subagent_type=general-purpose・model=opus(要求 claude-opus-5-5・effort xhigh は起動口に effort の欄が無く指定できず — 観測できたモデルは未確認)
agentId: a15d8ef679f1a673c・起動 2026-09-25T20:51:07Z 頃・所要 634003 ms・tool_uses 42
fallback の理由: 第 1 候補 gpt-6-astra(low)は codex の実行ファイルがこの pod に無い(`codex --version` → command not found)
入力: /tmp/blind639/input.md(sha256 e3e5a0697f4f5d3a8f2120bf659ea1ac70ca168afaaae59eb7f95776fc4eed30 = blind-input.md と同一)
文脈: 親の会話を fork・resume せず新しい文脈で起動。B の返答は見せていない(A・B は同じ 1 つの返事の中で同時に起動)。

---

## 結論

主張 S3「transaction を使う操作を足しても、変わるのは M2 の本体 1 つと M3 の入口 1 つ」に対する反例が見つかりました。前提は変えていません。新しい本体が既存の公開 helper を 1 つ使うだけで、M1 の transaction が本体の途中で終わります。原因は `cache_host_store.py:106` の `conn.commit()` です。

## 1. 変更要求と、それが必要になる理由

**要求:** 専用操作(cache ping)を送る前の記録を、lease を持っている host だけが書けるようにする。この記録は state = RUNNING への遷移で、`cache_host.hy:133` のコメントでは「送信前のdurable fence」と呼ばれています。具体的には、lease の持ち主の確認と記録の書き込みを 1 つの BEGIN IMMEDIATE の中で行います。lease を使って書き手を締め出す、標準的な強化です。

**理由:**
- R10(c) は「別 socket・同一 DB の誤構成 = 生きた二重 host」が起こりうると認めています。ただし検出は heartbeat(約 3 秒ごと)に頼っています。検出されるまでは、lease を持たない host も記録を書き、同じ会話へ ping を送れます。
- `cache_host.hy:120` は、この排他の目的を「同じ履歴への同時書込みだけを防ぐ」と書いています。今の排他は 2 つだけで、どちらも別の process には効きません。
  - `host.hy:1884` の `session_mutation_lock`(1 process の中だけの RLock)
  - actor による直列化(接続は 1 本)
- 別の接続に対して「確認してから書く」を原子的にできるのは BEGIN IMMEDIATE だけです。したがって M1 を使うのが自然な実装です。

## 2. 関係する主張・前提と、予想に反して変わるもの

**関係する主張と前提:**
- S3 の主張: M2 に本体 1 つ、M3 に入口 1 つ。M1 と M4 は変わらない。
- S1 の前提: 本体は「transaction の中で走る」ことだけに依存する。
- R15(a): 明示の transaction は M1 の 1 点だけが張る。
- M2 が隠す知識: transaction の開き方。

**前提は守っています。** 新しい本体は M2 に置き、M3 の入口から actor の thread の中で `run` します。本体が呼ぶのは store に既にある公開 helper `cache_receipt_put` だけです。

**予想に反して変わるもの:** `packages/doeff-agents/src/doeff_agents/sessionhost/cache_host_store.py` です。このモジュールの責務は専用操作の記録の読み書きで、lease とも transaction とも別の責務です。

## 3. 漏れている知識と、変更が波及する経路

**漏れている知識:** 「transaction をいつ終えるか」は M1 だけが持つはずの判断です。ところが `cache_receipt_put` は書いた後に自分で commit します。
- 今の本番では害がありません。actor の接続は `isolation_level=None` で、この関数は transaction の外でしか呼ばれないので、commit は何もしません。
- transaction の中で使われた瞬間に効き始める、潜在的な漏れです。

**M1 の判断も同時に崩れます。** M1 は失敗時に `in_transaction` が偽なら「SQLite が自分で巻き戻した」とみなし、ROLLBACK を省きます。しかし helper が commit した場合も `in_transaction` は偽になり、両者を区別できません。その結果、途中まで確定した書き込みが黙って残ります。

**実装の道筋は 3 つありますが、どれも M2・M3 の外へ波及します。**
- **既存の helper を使う:** `cache_host_store.py` を直す必要があります。「書いたら確定する」という、この関数の契約そのものを変えることになります。
- **本体から `HostCacheWrite` などの effect を出す:** この effect を解釈する handler は `store.hy:1435` の `sqlite-session-store [actor]` しかなく、中で `.submit actor` を呼びます。actor の thread の中から呼ぶと、自分自身の完了を待って止まります(実測)。避けるには、接続に直結した 2 つ目の handler を足し、M3 が「今は actor の thread の中で transaction が開いている」と知ったうえで handler を選ぶ必要があります。thread と transaction の知識が handler の選択へ漏れます。
- **SQL を本体に直接書く:** `cache_receipt_put` の状態遷移の規則を M2 へ写すことになります(RUNNING から REQUESTED へ戻さない、完了後は変えない、process の識別は変えない)。専用操作の知識が lease の判定に漏れ、規則が 2 か所に分かれます。これはコードを読んだうえでの推測です。

**欠陥ではない変更との区別:**
- M2 の本体と M3 の入口の追加は、主張どおりの意図した拡張です。
- `HostCacheWrite` の RUNNING の枝から新しい入口を呼び、owner-pid を `sqlite-session-store` と `run-hosted` へ渡すのは配線の変更です。
- `cache_host_store.py` の変更だけは、隠すべき知識が漏れていることによる波及です。これが反例の中身です。

**今の検査はこれを検出しません。**
- R15 の法の文は「transaction を開くのは M1 だけ」としか言っておらず、閉じる側については何も言っていません。
- `.semgrep.yaml` には transaction についての規則がありません。
- `conn.commit()` は SQL の文字列ではなく method 呼び出しです。`executescript` も暗黙に COMMIT します。後から BEGIN / ROLLBACK / COMMIT の文字列を探す規則を足しても、どちらも検出できません。

## 4. 最小の変更例・再現手順・観測結果

変更例です。共有 source には入れていません。同じ形の本体を `/tmp/blind639-a/exp_bodies.hy` に置いて実験しました。

```hy
(defk db-fence-cache-operation-body [conn owner-pid record]   ; M2 に足す本体
  {:pre [(: conn sqlite3.Connection) (: owner-pid int) (: record HostCacheRecord)]
   :post [(: % NoneType)]}
  (setv current (db-read-lease conn))
  (when (or (is current None) (!= (get current "owner_pid") owner-pid))
    (raise (RuntimeError "not the lease holder")))
  (cache-receipt-put conn record)
  None)
;; M3 に足す入口: (run (db-immediate-transaction conn (db-fence-cache-operation-body conn owner-pid record)))
```

再現の手順です。作業樹の中で実行します。

```
cd /home/kento/.worktrees/doeff-wt-639-sessionhost-defk
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python /tmp/blind639-a/exp_control.py
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python /tmp/blind639-a/exp_run.py e4a e4b
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python /tmp/blind639-a/exp_e3lock.py
```

`exp_control.py` は、helper を書き込んだ前後で、別の接続(二重 host の役)が BEGIN IMMEDIATE を取れるかを timeout 0 で調べます。helper は元の `cache_receipt_put` と、`conn.commit()` の 1 行だけを抜いた写しで比べました。結果は次のとおりです(実測)。

| helper | 本体の後半で失敗させるか | 結果 | 書き込みの前 / 後の別接続 | 後に残った行 |
|---|---|---|---|---|
| 元の関数 | 失敗させない | `OperationalError: cannot commit - no transaction is active` | 待たされる / lock を取れた | op-f が残る |
| 元の関数 | RuntimeError で失敗 | RuntimeError | 待たされる / lock を取れた | op-f が残る(巻き戻らない) |
| commit を抜いた写し | 失敗させない | 正常に返る | 待たされる / 待たされる | op-f |
| commit を抜いた写し | RuntimeError で失敗 | RuntimeError | 待たされる / 待たされる | 残らない |

見るべき点は 3 つです。
- 成功したはずの操作が、失敗を報告する。
- 失敗した操作の書き込みが残る。
- 本体の途中で別の接続が書ける。つまり締め出しの原子性がない。

これらの違いは `cache_host_store.py:106` の 1 行に帰着します。`exp_run.py e4a e4b` でも同じ結果が出ました(lease の owner 222 と記録の両方が確定または残存)。

effect を出す道筋の実測(`exp_e3lock.py`)です。本体が `HostCacheActive` を出し、handler に `sqlite-session-store actor` を差しました。
- 3 秒経っても戻りませんでした。
- actor の接続は `in_transaction=True` のままでした。
- 外の接続は `database is locked` になりました。
- 書き込みの健康状態の失敗回数は 0 のままでした。
- 後続の `db-read-lease` も戻りませんでした。

## 5. 未確認の前提と不足する情報

**実測したこと:**
- 上の表と、effect を出す道筋の結果。
- 主張どおり成り立つことも確かめました。この 2 点は反例になりませんでした。
  - S2: 本体が handler のない `ClockNow` を出すと、UnhandledEffect が M1 の `<-` の位置に届きます。ROLLBACK され、lease の行は残りません(`exp_run.py e2`)。
  - S1 の前提: 同じ Program を 2 度 `<-` で実行すると、本体は 2 度走ります(`exp_run.py e5`)。
- commit を抜いた写しで、既存テスト `test_cache_receipt_survives_reopen_and_forbids_resend` と同じ手順が通りました(`exp_nocommit.py`)。本物のテストは走らせていません。

**推測にとどまること:**
- この締め出しの要求が実際に出るかどうか。R10(c)・`cache_host.hy:120`・`host.hy:1884` を読んだうえでの推測です。blue/green の入れ替えでは区画ごとに DB が分かれるので、2 つの host が同じ DB を開くのは誤構成の時だけです。
- 残った RUNNING の記録が、`expires_at` まで通常の送信を断ること。`cache-host-guard-normal-send` のコードを読んだうえでの推測で、実行はしていません。
- SQL を本体に直接書く道筋で規則が 2 か所に分かれること。
- 測定環境は Python 3.14.3(free-threading 版)と SQLite 3.50.4 だけです。他の版では測っていません。

**見ていない範囲:** `.scratch/`、全体のテスト、sessionhost 以外で store の接続を受け取る helper。

作業樹は変えていません。`git status` は開始時と同じく `?? .scratch/` だけです。venv を変えないよう `uv run` には `--no-sync` を付け、bytecode を書かないよう `PYTHONDONTWRITEBYTECODE=1` を設定して実行しました。実験ファイルはすべて `/tmp/blind639-a/` の下にあります。
