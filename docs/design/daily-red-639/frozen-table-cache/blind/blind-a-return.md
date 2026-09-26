## 反例: 再試行の可否を表(M1)だけで変えても、agentd を通る手番の結末は変わらない

### 1. 変更要求とその理由

S1 に設計者が挙げた例のうち「context_exhausted を retryable=true にする」を使います。

- 理由: ACP 側から「文脈の枯渇で終わった手番は、圧縮してから走らせ直せば通る」という要求が出る場面です。冷えた再開の前に `/compact` を走らせる経路は既に `HeadlessRunOnce` として在るので、同じ封筒でも圧縮の後なら通る、という論拠は現実に立ちます。
- 新しい語を足さないので、行を書く側の変更は要りません。変わるのは再試行の可否だけです。

### 2. 元の主張・前提と、予想に反して変わるもの

- 元の主張(S1): 変わるのは M1(表)・M2(README)・M3(写し)・M4(ADR)の 4 か所で、共同の不変条件である。不適用の節では「doeff 側が持つのは語の表だけ」とも書いている。
- 前提は守っています。語は wire の snake_case のままで、M3 は dict の完全一致で比べます。変更の例は設計者自身が挙げたものなので、契約の適用範囲内です。
- 実際には、agentd の ACP 側の腕が手番の結末を組む `packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy` の `job-outcome-of`(6300 行付近)が、category の名前だけで結末を分けています。
  - host_drained は `{agentd-stopped, host-drained}` と条件 HostDrained になる。
  - rate_limited は `provider-limit-condition-of` で ProviderLimit になる。
  - それ以外の、done でない終端はすべて `{failed, SessionFailed}` になる。
- 同じ dict の中に `retryable` の欄が在るのに、doeff の中でこれを読む箇所は1つもありません。書き出すのは store の直列化(`store.hy` 297 行)だけで、読み手は下流だけです。
- したがって、要求を実際に効かせるには `acp/judgment.hy` と `acp/effects.py` の変更が要り、内容によっては ACP の契約(scheduling.json)の語の追加も要ります。これらは M1〜M8 のどれにも入っていません。
- 実績でも裏付けがあります。S1 型の実例である host_drained の追加(f271ae39)は 14 file を変えました。その中には `acp/effects.py`・`acp/judgment.hy`・`headless_protocol.py`・`headless.hy`・`host.hy`・`acp/runtime.py`・`acp/entry.py` が入っており、逆に M2 と M3 は直し漏れでした(これが依頼書 P の原因です)。

### 3. 何がどこへ漏れているか(区別)

| 区分 | 該当 |
|---|---|
| 共同の不変条件(意図どおり) | M1〜M4 に加えて、表の値を固定した別の検。`sessionhost_policy_deftests.hy:903`(context_exhausted の行の retryable が False)、ADR-009・ADR-011 の deftest、`test_sessionhost_headless.py:652-653`。どれも変更すれば赤になるので健全だが、「4 か所」という数より多い |
| 公開契約の拡張(正当になりうる) | ACP に別の結末を伝えるなら、`acp/effects.py` の語と ACP の scheduling.json に語を足す。agentd と ACP の間の契約の拡張で、それ自体は欠陥ではない。ただし設計の責務表にこの対応を持つ module が載っていない |
| 知識の漏洩(反例の核) | 「どの終端の手番を走らせ直すか」という M1 の知識を、`job-outcome-of` が category 名の分岐として持ち直している。しかも同じ入力に載っている `retryable` を読まない。その結果、agentd の経路では M1 は判断の唯一の実装点ではなく、M1 の列は doeff の中では書かれるだけで読まれない |
| 強制の穴 | M3 は M1 とその写しを比べるだけ。ADR-012 の針は host_drained という 1 語の綴りの一致と、写す箇所が1つであることしか見ない。「retryable=true の語は `job-outcome-of` で失敗にならない」「`job-outcome-of` が retryable を読む」を検める検が無いので、M1 だけを変えると全部の検が緑のまま黙って効かない |

### 4. 最小の再現(実測)

`/tmp/p-blind-639/retryable_flip_probe.py` を用意しました。source は書き換えず、表の反転を process の中で dict を書き換えて模します。通る路は本番と同じ `make-cause` → `terminal-cause-to-dict` → wire の snapshot → `session_view_of` → `job-outcome-of` です。

```
cd /Users/kento/.worktrees/doeff-wt-639-P-blind
PYTHONDONTWRITEBYTECODE=1 uv run --no-sync python /tmp/p-blind-639/retryable_flip_probe.py
```

結果(`/tmp/p-blind-639/retryable_flip_probe.log`):

- context_exhausted: retryable が False から True に変わっても、結末は `{failed, SessionFailed}` のまま。「job outcome changed: False」。
- run_failed と cancelled も同じで、結末は変わらない。
- host_drained: retryable を True から False にしても `{agentd-stopped, host-drained}`・HostDrained のまま。

主張どおりなら、表を反転した後は ACP が走らせ直せる結末に変わるはずです。実際には、4 語とも結末が 1 bit も変わりませんでした。

現状の確認として、許された焦点の検 3 file(policy・cache_maintenance・headless_events)を走らせ、99 passed でした(`/tmp/p-blind-focal.log`)。

### 5. 未確認の前提・不足する情報(推測と実測の区別)

- 推測: ACP の engine が agentd を通らない経路で、session の snapshot の `retryable` を今も読むかどうか。M1 の註には Observed.hs `failureKindForCause` を読むとありますが、ACP の repo は読む範囲の外で確かめていません。読んでいるなら、表の反転は古い経路にだけ効き、agentd の経路には効かないので、2 つの経路の意味が分かれます。
- 推測: context_exhausted の行は pane(tmux)の経路が書きます。headless の経路が書くのは run_failed・vanished・rate_limited・host_drained・cancelled などです。agentd を tmux の backend で動かす機体が今あるかは未確認です。ただ、run_failed でも結末が変わらないことは実測したので、反例の構造は語の選び方に依りません。
- 推測: ACP が SessionFailed の条件の文(`session failed: context_exhausted (...)`)を解いて語を拾っているかは未確認です。拾っているなら、ACP 側にも同じ知識が漏れていることになります。

### 補足: 探索した他の軸

- S2(置き場): 主張の範囲では反例は見つかりませんでした。ただし locator の綴り(`.events.jsonl` と `.cache-`)は M5 の知識とされていますが、次の箇所にも同じ綴りが直接書かれています。
  - `cache_host.hy:108` の `(+ events-path ".cache-" suffix)`
  - `headless.hy:147`
  - `acp/fake.py:937`
  - c98fd834 以後の M8 が固定で書く locator。`MemoryEventStore.append` は綴りの外の名を ValueError で断るので、M8 はこの綴りに依存します(コードを読んだだけで、走らせてはいません)。

  綴りを変える変更は S2 の前提(綴りは替えない)の外なので、別の候補として挙げるにとどめます。
- S3(仮想の時計): `cache-host-probe` の路では、登記簿の時計は読まれません。`HostCacheStopProcess` も、record の process が None なので呼ばれません。コードを読んだ範囲では主張どおりです。

関係する file:
- `/Users/kento/.worktrees/doeff-wt-639-P-blind/packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy`
- `/Users/kento/.worktrees/doeff-wt-639-P-blind/packages/doeff-agents/src/doeff_agents/sessionhost/acp/effects.py`
- `/Users/kento/.worktrees/doeff-wt-639-P-blind/packages/doeff-agents/src/doeff_agents/sessionhost/policy.hy`
- `/Users/kento/.worktrees/doeff-wt-639-P-blind/packages/doeff-agents/src/doeff_agents/sessionhost/store.hy`
- `/Users/kento/.worktrees/doeff-wt-639-P-blind/docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy`
