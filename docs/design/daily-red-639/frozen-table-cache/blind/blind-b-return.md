## 結論

提示された検査(焦点の 3 file の pytest)をそのまま通りながら、cache ping の読み手(M7)と検査の世界(M8)に出来事の保存先の物理を持ち込む実装を 1 つ作りました。検査の通過と、保存先によって結果が変わる挙動は、どちらも実際に走らせて確かめています。元の作業樹は書き換えていません(`git status` は空、新しい file も無し)。

## 1. 機能要求と差分

**要求**: cache ping が失敗して `provider-error` や `cache-observation-missing` になっても、理由が語 1 つだけなので原因が分からない。子 process の stderr の末尾を失敗理由に足してほしい。

差分の全文は `/tmp/blind-p-cx/counterexample.diff` にあります。要点は次のとおりです。

```hy
;; cache_host.hy
(import .headless_events [HeadlessEventsSince STDERR-SUFFIX])
(defk cache-stderr-tail [record]
  {:pre [(: record HostCacheRecord)] :post [(: % str)]}
  "…診断だけ。出来事の読みは host の口(HeadlessEventsSince)だけで、置き場の file は開かない。"
  (<- chunk (HeadlessEventsSince (+ record.events-path STDERR-SUFFIX) 0))
  (setv lines (lfor line (.splitlines chunk.text) :if (.strip line) (.strip line)))
  (.join " | " (cut lines -3 None)))
;; cache-host-probe の失敗の枝: (<- tail str (cache-stderr-tail record))
;;   :reason (if tail f"{word}: {tail}" word)
```

あわせて `sessionhost_cache_maintenance_deftests.hy` に検査 `test-failed-ping-reason-carries-the-provider-stderr-tail` を 1 本足しました。memory の保存先は `since` で stderr を返さないため、package の `FileEventStore` を `headless-substrate` に組んでいます。これは要件 2 の「package が出す同じ契約の handler を組む」の文面には合っています。

実装者がこう書きやすい理由:
- 認められた effect だけを使い、file を直接開いていない。
- M5 が公開している定数を使っている。
- 開発機の Mac(file の保存先)では実際に動く。

## 2. 本来の持ち主と、破られる契約

- **stderr の保存場所(`locator + ".stderr"`)**: M5 の `FileEventStore` が隠しているはずの物理です。これが M7 の文字列の組み立てと、M8 の保存先の選び方へ移ります。
- **locator の綴り**: `key_of_locator` の 1 点が持つはずです。M7 は正規の 2 形の外の locator を作ります。memory と送り待ちの表はそれを `op='ping-err.stderr'`(存在しない流れ)として解き、file は生の path として開きます。つまり M5 の中で解釈が 2 つに割れています。
- **破られる契約**:
  - 法 `headless-events-are-read-through-the-host` の「どの handler も同じ契約に答える」「stderr は混ざらない」。
  - M7 の「保存先を知らない」。
  - S2 の主張「保存先を替えても M7 と M8 は変わらない」。保存先を替えると M7 の出力が変わり、`FileEventStore` を退役させると新しい検査が壊れます。
- **影響**: `HostCacheRecord.reason` は `cache_live.hy` の `PingFailed` を通って ACP の保守記録の理由に載ります。同じ失敗でも、機体の保存先によって記録の文面が変わります。成功・失敗の判定そのものは変わりません。

## 3. 各検査が拒否しない理由

| 検査 | 結果 | 通る理由 |
|---|---|---|
| M3 の固定表の完全一致(`test_sessionhost_policy.py`) | 実測で緑 | 表に触れていない |
| M4 の ADR の deftest | 走らせていない | source を読んだ限り、読むのは `host.hy`・`headless.hy`・`headless_protocol.py`・`policy.hy` の表・`test_sessionhost_headless.py` だけで、`cache_host.hy` は読まない |
| 法の検査のうち `test_sessionhost_headless_events.py` の 3 本 | 実測で緑 | agentd の RPC、保存先単体、file の物理だけを検め、M7 を走らせない。保存先の契約の検査は正規の locator しか使わない |
| 法の検査の残り 1 本(`test_sessionhost_headless.py`) | 走らせていない | 読んだ限り launch・`events_since`・`capture` だけで、cache ping は通らない |
| M8 の構成 | 機械の検査は無い | 既存の検査は成功の枝だけ通る。新しい検査は緑 |

## 4. 確かめる手順と結果(実測)

- 写し: `/tmp/blind-p-cx/wt`(rsync で写し、`.venv` は元の作業樹へ symlink)。`PYTHONPATH=/tmp/blind-p-cx/wt/packages/doeff-agents/src` を付けると写しの source が import されることを確かめました。
- 焦点の 3 file の pytest(指定のコマンド):
  - 差分を当てる前: 99 passed。
  - 差分を当てた後: 100 passed(cache 維持の file は新しい 1 本を含めて 8 passed)。
- 違反の確認: `/tmp/blind-p-cx/probe.py`。同じ入力(stdout に `result is_error:true`、stderr に 1 行)を 3 種類の保存先で `cache-host-probe` に通しました。

| 保存先 | 差分あり | 変更前(`/tmp/blind-p-cx/base/src`) |
|---|---|---|
| memory | `provider-error` | `provider-error` |
| file | `provider-error: API Error: 529 overloaded` | `provider-error` |
| 送り待ちの表 | `provider-error` | `provider-error` |

stderr の locator を `key_of_locator` に通すと `EventKey('resident', 'ping-err.stderr')` になりました。

## 5. 未確認の前提と足りない情報

- **走らせていないもの**: ADR の収集、`test_sessionhost_headless.py`、`make lint`(semgrep・pyright・doeff-linter)、Linux 機。これらが拒否するかどうかは判断していません。
- **source を読んだだけの推測**:
  - 本物の host では、子の stderr が `HeadlessProcess._read_stderr` から同じ locator の stderr の流れとして書かれ、file の保存先なら `<locator>.stderr` に落ちる。
  - Mac は環境変数 `DOEFF_AGENTD_EVENTS_OTLP_URL` が無いと file の保存先になる(`test_the_composition_root_swaps_in_…` の内容から読んだもの。host は起こしていません)。
- **不明**:
  - 実際の pod にその環境変数が配備されているか。
  - ACP 側が `reason` を自由な文として扱うか(doeff の中ではこの 3 語を読む箇所は見つかりませんでした)。
  - claude CLI が provider の失敗時に stderr へ何か書くか。

これは反例が 1 つ見つかったという報告で、他の違反が無いことや安全性を示すものではありません。
