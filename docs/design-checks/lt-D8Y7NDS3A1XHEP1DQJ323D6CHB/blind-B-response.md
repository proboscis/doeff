## 結論

失敗例を 1 つ作り、本物の入口で確かめました。coordinator の入口 `main.hy`(M-entry)に「Pod の名乗りの補い」を足す変更です。提示された検査のうち実行できたものはすべて通りました。同じ設定のまま、契約上は送ってはいけない coordinator が、本番の job の名 `coordinator/agent-worker` で行を送ることも実測しました。

## 1. 機能要求と差分

**機能要求(ありそうな形)**: 「agora の ConfigMap coordinator-env の送り先(09e8fd517)と、doeff の宣言の POD_NAME・POD_NAMESPACE(dc3385b9a)の配備の順に関係なく、版上げの 1 回目から coordinator の行を送りたい。宣言が名乗りを書き忘れても、k8s が container に置く HOSTNAME(= Pod の名)と、coordinator の宣言の namespace `agent-worker` で補う」。

**差分**(`main.hy` だけを変える。line_stamp.py・宣言・test は変えない):

```diff
-(import doeff_cluster.worker.entry.line_stamp [COORDINATOR-LOG-ENV-NAMES coordinator-log-plan process-lines])
+(import doeff_cluster.worker.entry.line_stamp [COORDINATOR-LOG-ENV-NAMES POD-NAME-VAR POD-NAMESPACE-VAR coordinator-log-plan process-lines])
+(val POD-NAME-FALLBACK-ENV "HOSTNAME")
+(val POD-NAMESPACE-FALLBACK "agent-worker")
 ...
 (defk log-environment []
-  (<- found (get tuple #(EnvEntry ...)) (ReadEnvironment COORDINATOR-LOG-ENV-NAMES))
-  (dfor e found :if e.value e.name e.value))
+  (<- found (get tuple #(EnvEntry ...)) (ReadEnvironment (+ COORDINATOR-LOG-ENV-NAMES #(POD-NAME-FALLBACK-ENV))))
+  (val read (dfor e found :if e.value e.name e.value))
+  (val fallback {POD-NAME-VAR (.get read POD-NAME-FALLBACK-ENV "") POD-NAMESPACE-VAR POD-NAMESPACE-FALLBACK})
+  (dfor name COORDINATOR-LOG-ENV-NAMES
+        :setv value (or (.get read name) (.get fallback name ""))
+        :if value
+        name value))
```

註と docstring を含む全文は `/tmp/tmp.ieSXwYDbSs/coordinator-identity-fallback.patch` にあります。

## 2. 本来の所有者と、破られる契約

- **名乗りの出どころ**: 本来は M-decl が持ちます(downward API で POD_NAME・POD_NAMESPACE を置く)。この差分では M-entry が「HOSTNAME = Pod の名」という慣習と、namespace の値 `agent-worker`(M-decl の metadata.namespace の写し)を持ちます。同じ知識が 2 か所に分かれます。
- **名乗りが欠けた時の判断**: 本来は M-stamp の `coordinator_log_plan` が持ちます。公開契約は「送り先があり POD_NAME・POD_NAMESPACE が欠ければ JobLogRefused(欠けた名を挙げる)」です。この差分では M-entry が先に欠けを埋めるので、本番の経路では JobLogRefused に届かなくなります。送るかどうかの判断が M-entry へ移ります。
- **M-entry の契約違反**: 「どの名を読むかは M-stamp の組に任せる。送り先の値・形は知らない」に反します。組の外の HOSTNAME を読み、値も持ちます。
- **S-dist の主張の破れ**: 主張は「名乗りが欠ければ共有の名で混ぜずに送らない」です。lab の宣言が POD_NAMESPACE を書き忘れると、本番の job の名 `coordinator/agent-worker` で送ってしまいます。さらに、断りの 1 行(「名乗りの環境変数が無い」)も出なくなるので、宣言の欠けに気づく手がかりが消えます。

## 3. 各検査がこの差分を拒まない理由

| 検査 | 結果 | 理由 |
|---|---|---|
| test_the_coordinator_names_itself_after_its_pod | **実測で pass** | `coordinator_log_plan` は変えておらず、この検は関数を直に呼ぶ。補いは関数の外で起きる |
| pyright の網羅 | 実行していない | 差分は Hy の file だけで、line_stamp.py は基の版と同じ |
| test_the_coordinator_ships_its_own_lines_from_two_pipes_until_it_stops / test_a_coordinator_without_the_destination_keeps_its_two_streams | **実測で pass** | どちらも env に 2 つの名を必ず渡すので、補いの道を通らない。送り先が無い場合は JobLogOff のまま |
| test_declarations_name_no_upper_system / test_no_worker_is_newer_than_the_coordinator | 実行していない | 中で `kubectl kustomize` を呼ぶので、作業の決まりに従い走らせていない。読むのは deploy/k8s・.semgrep.yaml・git の歴史で、差分はどれにも触れない(基の版の結果も測っていない) |
| agora の test_the_coordinator_env_configmap_… | 実行していない | agora の repo には差分が無い |
| code-quality の module_extensions | **fast の段を実測で passed**(写しで main.hy に絞った・findings 0) | 新しい import は line_stamp の `POD-NAME-VAR`・`POD-NAMESPACE-VAR` だけで、doeff-cluster-coordinator → doeff-cluster-line-stamp は許可されている |
| code-quality の full の段(型検査) | 結果なし | 1 度起動したが、10 分の上限で打ち切られた |

## 4. 確かめる手順と結果

写し `/tmp/tmp.ieSXwYDbSs` は `git archive HEAD` から作り、`uv sync --group dev` を済ませてあります。
- 検査: `uv run --group dev pytest packages/doeff-cluster/tests/test_job_log_ship.py -q -k coordinator`
- 確かめの検: 写しにだけ置いた `packages/doeff-cluster/tests/test_probe_coordinator_identity.py`(本物の入口を `coordinator_run` で起こし、検の中の受け手で受ける)。2 回目以降は速さのため `--no-sync` を付けました。

| 入力 | 基の版(実測) | 差分の後(実測) |
|---|---|---|
| 提示の 3 つの検 | 3 passed | 3 passed |
| lab の coordinator: OTLP と POD_NAME だけ(POD_NAMESPACE を書き忘れ) | records=0、stderr に「名乗りの環境変数が無い: POD_NAMESPACE」 | records=2、doeff.job=`coordinator/agent-worker`、断りの行なし |
| dc3385b9a より前の宣言: OTLP と HOSTNAME だけ | records=0、「POD_NAME,POD_NAMESPACE」を挙げて断る | records=2、`coordinator/agent-worker` で送る |

期待される結果は「提示の検はすべて緑のまま、確かめの検 2 本は差分の後だけ赤になる」で、実測もその通りでした。

## 5. 確かめていない前提と、足りない情報

- **「HOSTNAME = Pod の名」**: この会話の Pod(worker の Pod)の PID 1 の環境で、値が hostname と一致することは実測しました。coordinator の Pod では確かめていません。
- **「混ざる」**: 実測したのは、検の受け手で doeff.job が `coordinator/agent-worker` になるところまでです。ClickHouse の job_logs や `agora job-log` で実際に混ざるかは測っていません。また、lab の ConfigMap が同じ agora.tenant を置く場合に限ります(利用者 1 人の cluster なので、そうなる可能性は高いと推測しています)。
- **未実行の検査**: pyright、code-quality の full の段、宣言の検、agora の検は走らせていません。提示外の検査(semgrep の規則の全体など)の結果は推測しません。
- **別の穴(設定を読んだ推論で、実測ではない)**: code-quality の module doeff-cluster-coordinator は、paths に program.hy(M-core)と main.hy(M-entry)の両方を含みます。そのため module_extensions の許可は、M-core から line_stamp への import も許してしまいます。M-core の「行の送り先を知らない」は code-quality では守られていません。
- **作業の痕跡**:
  - 共有の作業木は変えていません。git status は、もともとあった未追跡の docs の dir だけです。
  - 写しの中では基を作るために使い捨ての `git init` と commit をしました。push はしていません。
  - 写し(646M)は消してかまいません。
  - design-pre-blind.md は読んでいません。

WAIT: work — 頼んだ側の次の依頼
