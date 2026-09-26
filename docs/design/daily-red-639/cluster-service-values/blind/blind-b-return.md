<!-- 盲検 B の返答(未加工)。起動: Claude Code の Agent tool・subagent_type general-purpose・model opus(claude-opus-5-5)・新しい文脈(fork でも resume でもない)。
     A とは別の文脈で同時に起動し、互いの返答は見ていない。effort は起動口が受け付けない(指定できない)。機体 = agentd-pool-1(pod)。
     agentId a0e71599a91048088・所要 1170.2 秒・subagent_tokens 193754・tool_uses 57。
     codex(gpt-6-astra・low)は起動口 cx が「この機体は宿名を宣言していない」で断った(evidence/blind-launcher-cx-refused.log)。
     ツールの出力が各行に付けた 2 字の字下げだけを外した。 -->

## 結論

M5(service の本体の検査)は「宣言と本体が同じ module にある」ことを前提にしていますが、この前提を守らせる仕組みはどこにもありません。宣言を別の module に置くだけで、本体に直の file I/O があっても、提示された検査はすべて通りました。本体と宣言の中身を変えず、宣言の置き場所だけを本体の module へ移すと、同じ検査が赤になります。これも実際に走らせて確かめました。

## 1. 機能の要求と差分

**要求**: 会話の手番がすべて done になったら要約を `digest/<会話>` に置く service「digest-keeper」を新しく作り、k3s の controller として出す。起こし直した後も前回の続きから進めたい。配備の値(env・requires・設定)は宣言用の module に集め、業務の module は `doeff_cluster.service_model` に依らない形にする(lab の `agora.hy` が System を別の module で束ねているのと同じ向き)。

agora-controllers に新しいファイルを足すだけです(`controllers/digest/__init__.py` は空)。

`controllers/digest/keeper.hy`(本体だけで、宣言は書かない)
```hy
(defk digest-keeper-program [interval cycles cursor-file]
  {:pre [(: interval (| int float)) (: cycles (| int None)) (: cursor-file str)] :post [(: % int)]}
  (setv cursor (Path cursor-file))
  (setv done (if (.exists cursor) (set (json.loads (.read-text cursor :encoding "utf-8"))) (set)))
  (setv made 0 cycle 0)
  (while (or (is cycles None) (< cycle cycles))
    (<- rows dict (ReadShared "turn/"))
    (for [conv (sorted (sfor key rows (get (.split key "/") 1)))]
      (setv turns (lfor #(k v) (sorted (.items rows)) :if (.startswith k (+ "turn/" conv "/")) v))
      (when (and (not-in conv done) (all (gfor row turns (= (.get row "state") "done"))))
        (<- ok bool (WriteShared (+ "digest/" conv) {"conversation" conv "turns" (len turns)
                                  "digest" (.join "," (lfor row turns (.get row "result" "")))} :expect None))
        (when ok (+= made 1))
        (.add done conv)))
    (.write-text cursor (json.dumps (sorted done)) :encoding "utf-8")
    (+= cycle 1)
    (<- (Delay interval)))
  made)
```

`controllers/digest/cluster.hy`(宣言と System)
```hy
(import doeff_cluster.service_model [service System])
(import controllers.digest.keeper [digest-keeper-program])
(setv digest-keeper (service "digest-keeper" digest-keeper-program
                             :env "controllers.digest.envs:digest_env" :requires {"role" "controller"}
                             :config {"interval" 5.0 "cycles" None
                                      "cursor-file" "/var/lib/agora/digest-keeper.cursor.json"}))
(setv digest-system (System "digest" #(digest-keeper)))
```

ほかに `controllers/digest/envs.hy` を置きました。`shared-http` と実時計を返すだけの env です。全文は `/tmp/blind-cluster-b/trees/ac-cx/controllers/digest/` にあります。

## 2. 本来の持ち主と、破られる契約

- **状態の持ち主**: 「どこまで要約したか」という状態は、本来 `ReadShared`/`WriteShared` を通して共有の保存に置くものです(S-STORE)。この差分では、それが本体と「走った機体のディスク」へ移ります。M4 の契約「file を直に触らない・型のある effect だけ」を破っています。
- **置き場を選ぶ知識**: 置き場の選択は env の handler が持つはずです。それが本体の引数 `cursor-file` と宣言の config に移り、M2 がそのまま coordinator 向けの JSON に写します。
- **effect の持ち主**: handler の組では止められない実 I/O が本体に残ります。S-SIM の主張「handler だけを差し替えて回す」と S-EFF・S-STORE の主張「本体が直に I/O を書けば、どの dir でも M5 が赤にする」、ADR の law `service-body-is-io-free-anywhere` が成り立たなくなります。
- **根本の原因**: 「どの関数が service の本体か」という知識は、M1・M2 の実行時の値が持っています。M5 はそれを字面から組み直しており、その際に「宣言は本体と同じ module にある」と仮定しています。`service` はどの module の最上位の関数でも受け付けるので、この仮定を強制する者がいません。設計の「限界」の列にあるのは「語の一覧の外」と「本体が呼ぶ別の関数の中」だけで、今回の本体は自分で直接 `(.read-text` と `(.write-text` を書いているため、どちらにも当たりません。
  - 推測: 以前の `defservice` のような 1 つの form なら構造上同じ場所に並んでいたはずですが、旧 macro は読んでいません。

## 3. 提示された各検査が拒否しない理由(すべて実際に走らせた結果)

| 検査 | 結果 | 通る理由 |
| --- | --- | --- |
| doeff の `test_service_declaration.hy` | 終了コード 0、6 passed | doeff の fixture だけを検めており、agora-controllers を読みません。今回の宣言自体も M1 を正しく通ります(実測: `resolve` の結果が宣言した関数そのもの=True) |
| `test_adr_doe_hy_005_macros_live_only_in_doeff_hy` | 終了コード 0、1 passed | doeff の中の `defmacro` しか見ません。差分に `defmacro` は無く、doeff も変えていません |
| M5 の ADR ファイル | 終了コード 0、9 passed(変更前も 9 passed) | 下に理由を 2 つ書きます |
| `test_turns.hy` | 終了コード 0、6 passed | `lab` の System しか使わず、新しい System は触れません |
| `scripts/check_typed_values.hy --root <写し>` | 終了コード 0、「1243 件 / 282 file」「ok」(変更前と同じ) | 公開面に dict や tuple の型注記がありません |

M5 の ADR が通る理由は次の 2 つです。

- **semgrep の 4 規則**: 対象パスは `controllers/worker/lab/**` と `controllers/worker/services/**` だけで、`controllers/digest/` は含まれません。
- **`service-body-report`**:
  - `(service\s+"` を含むファイルしか読まないので、本体の `keeper.hy` は読み飛ばされます。
  - `cluster.hy` は読まれますが、同じ module に `defk digest-keeper-program` が無いので本体は 0 本です。
  - 空の検査を見分ける確認は repo 全体で `bodies > 0` を見るだけなので、他の 89 本で満たされます。
  - 実測値は bodies=89(変更前と同じ)、violations=() でした。

## 4. 検査の通過と責務違反を同じ設定で確かめる手順

準備として、agora-controllers を `/tmp/blind-cluster-b/trees/` の下に 3 つ写しました(`ac-base` / `ac-cx` / `ac-control`)。各写しの `.git` ファイル(元の checkout の gitdir を指している)は、元へ書き込まないよう `.git-pointer-disabled` に改名しています。命令は依頼文のものに、次の変更だけを加えました。

- `R` を写し先に置き換えた
- `uv ... run` に `--no-sync` を付けた
- `PYTHONPYCACHEPREFIX` を `/tmp/blind-cluster-b/pyc` に向けた

**手順**

1. 変更前(`ac-base`)で M5 の ADR と `test_turns` を走らせる: 9 passed / 6 passed。
2. 差分を足した木(`ac-cx`)で同じ命令を走らせる: 9 passed(47.4 秒)/ 6 passed。
3. 同じ木・同じ環境で `hy /tmp/blind-cluster-b/demo_violation.hy <ac-cx> /tmp/blind-cluster-b/run` を走らせる(終了コード 0)。handler は仮想の時計とメモリの盤だけです。
   - M2 が出す宣言: `"factory": "controllers.digest.keeper:digest_keeper_program"`、config に `cursor-file` が載る。
   - 1 回目(新しい盤): 答えは `[2]`、盤に `digest/c1` と `digest/c2`。盤には印の行が無く、ディスクのファイルに `["c1", "c2"]` が残る。
   - 2 回目(入力が同じ新しい盤、設定も同じ): 答えは `[0]`、盤の digest は `[]`。handler が握っていない状態によって結果が変わっています。
   - 3 回目(宣言の設定のまま、handler だけ fake): `FileNotFoundError: '/var/lib/agora/digest-keeper.cursor.json'`。handler を差し替えるだけでは回せません。`/var/lib/agora` は存在しないことを先に確かめており、何も作られていません。
4. 対照(`ac-control`): 本体は同じまま、`service` の呼び出しだけを `keeper.hy` に移して M5 の ADR を走らせる。
   - 終了コード 1、`1 failed, 8 passed`
   - `AssertionError: service の本体の違反: (('controllers/digest/keeper.hy', 'digest-keeper-program', '(.read-text '), ('controllers/digest/keeper.hy', 'digest-keeper-program', '(.write-text '))`

違いは宣言を置いた module だけです。

## 5. 未確認の前提と、足りない情報

**実測はしていないこと**

- 本物の coordinator と worker では走らせていません。「別の worker へ移ると印が失われる」は、ADR 自身の事実の記述と `job_entry.run-service` の読みからの推測です。手元の in-process の実走で示せたのは「状態が handler の外にある」ところまでです。
- 写しでは git 無しで semgrep を走らせました。変更前も 9 passed で同じでしたが、対象の選び方が git ありの場合と完全に同じかは確かめていません。今回のディレクトリは設定の include の外なので、影響は無いと推測しています。
- 依頼の範囲外として、次は走らせていません: `.semgrep.yaml` の `worker-` 以外の規則、agora_sim の ADR、`test_effect_codec_coverage.hy`、全数の検査。これらが今回の差分を断るかどうかは分かりません。
- 「宣言を別の module に分けるのは普通の実装者の選択か」は判断です。根拠として、`lab/agora.hy` の分け方と下の既存例を挙げます。

**あわせて実測したこと**(`measure_m5.hy`・`measure_shapes.hy`)

- 変更前の repo に、M5 が本体を読んでいない宣言がすでに 4 本あります。`controllers/agora_sim/coordinator.hy` が補助関数 `sim-service` を通して `(service name program …)` と書いているためで、このファイルは `SERVICE-DECLARATION-RE` に当たりません。import すると `coordinator_pod_program` など 4 つの ServiceDef ができます。
- 本体に `(open …)` を持つ同じ見本を、宣言の書き方だけ変えて試しました。「名前を f-string で組む(シャードの族)」「名前を定数で渡す」「既定値のために `defn` で包む」の 3 形とも、読めた本体は `[]` でした。同じ module に文字列の名で宣言した対照では `(open ` に当たります。
- grep で見た関連の事実: 本番の `budget_writer-program` は、env しか使わない設定(`token-file`・`lease-ttl`・`margin-ms`)を本体の引数と契約に持っています。config のキーは本体の引数に無いと失敗する契約なので、env の知識が本体の公開の契約に入り込みます。S-STORE の「本体は変えない」とずれますが、これは別の反例候補で、実走では確かめていません。

**元の checkout が変わっていないことの確認**: 実行の前に目印のファイルを作り、実行の後に 2 つの checkout で目印より新しいファイルを探しました(`.git` の下は除く)。結果は 0 件で、`git status` も空でした。

ファイルはすべて `/tmp/blind-cluster-b/` にあります。
- `trees/ac-cx`(反例)
- `trees/ac-control`(対照)
- `trees/ac-base`(変更前)
- `demo_violation.hy`、`measure_m5.hy`、`measure_shapes.hy`
- `run/node-a/cursor.json`(1 回目の実走で残った印のファイル)
