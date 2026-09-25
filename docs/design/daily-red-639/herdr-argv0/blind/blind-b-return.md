# 盲検 B の返答(未加工)

起動: Agent tool・subagent_type=general-purpose・model=opus(要求 claude-opus-5-5・effort xhigh は起動口に effort の欄が無く指定できず — 観測できたモデルは未確認)
agentId: a3e46403be3507d2e・起動 2026-09-25T23:0x Z 頃(JST 09-26 08:0x)・所要 550655 ms・tool_uses 52・機体 proboscis-mbp(personal)
fallback の理由: 第 1 候補 gpt-6-astra(effort low)を `CODEX_HOME=~/.codex/profiles/personal codex exec -m gpt-6-astra -c model_reasoning_effort=low -s read-only`
で起動した(session id 01a0dac0-33c9-7e00-b0dc-9a69a3a797ce・2026-09-25T22:47:04Z)が、`401 Unauthorized: Incorrect API key provided` で実行に失敗した(log = evidence/blind-codex-run-b.log)。
入力: /tmp/blind-herdr-argv0/prompt-b.md(sha256 dfa2ba8bbb5e77a03476344ce2599de1056262031422ec4441d14e2b5579fa8d = 手順書の B の依頼文 + blind-input.md の全文)。読み取り対象 = /tmp/blind-herdr-argv0/src(doeff 72da0ff1 の写し)と /tmp/blind-herdr-argv0/herdr-0.9.1。
文脈: 親の会話を fork・resume せず新しい文脈で起動。A の返答は見せていない(A・B は同じ 1 つの返事の中で同時に起動)。

---

1 つ目の反例を具体化しました。herdr の前面の job から「shell ではない process」を選ぶ処理を M2 に入れる修正です。提示された検査は通る見込みですが、idle shell の語彙と zombie 判定の一部が M5(policy)から M2(substrate)へ移ります。検査は 1 つも実行していません。以下で「通る」と書いたものはすべてコードを読んだうえでの推測です。

## 1. 機能要求と最小差分

**機能要求(現実的な不具合報告の形)**
呼び手が起動 command を上書きし、`bash ./run-agent.sh` のような wrapper で agent を起動する場合を考えます(`launch.hy` は上書きされた command をそのまま送ります)。wrapper の中身は `claude …; ./report.sh $?` のように claude の後にも処理が続くので、bash は claude を子として起動し、自分は終わらずに残ります。このため前面の process group は `[bash(wrapper), claude]` になります。

herdr の Linux 実装はこの一覧を pid の昇順に並べます(`platform/linux.rs:479` の `members.sort_unstable_by_key(|member| member.pid)`)。その結果、先頭は wrapper の bash です。M2 が `"bash"` を返し、M5 は生きている session を `exited`(vanished: "tmux pane returned to idle shell: bash")で終わらせてしまいます。そこで「herdr の答えは job の全 process を持っているのだから、shell ではない process を返せば直る」という修正要求が出ます。

**提案コード**(`substrate_herdr.hy`。M2 の本体だけを置き換え、M3・M4・M5 は触りません)

```hy
 (import doeff_agents.sessionhost.substrate [ ... unsubmitted-paste-input?])
+;; 前面の job から shell でない process を選ぶ。一覧は zombie 判定と同じものを使う
+;; (一覧を 2 か所に書かない — substrate.hy も policy の定数を import している)。
+(import doeff_agents.sessionhost.policy [IDLE-SHELL-COMMANDS])

 (defk herdr-foreground-command [result]
   {:pre [(: result dict)]
    :post [(: % (| str None))]}
-  "pane.process_info の答え → 前面の job の先頭の process の command 名 …"
+  "pane.process_info の答え → 前面の job のうち idle shell(policy の
+   IDLE-SHELL-COMMANDS)でない最初の process の command 名。全部が shell なら先頭。
+   wrapper の shell と agent が同じ前面の job に並ぶと、Linux の herdr は pid の
+   昇順なので先頭が wrapper になり、生きた session が idle shell に見えるため。 …(以下は既存の文)"
-  (setv proc (match result ... first ...))
-  (setv argv0 (.get proc "argv0")) ... (match #(argv0 from-argv) ...))
+  (match result
+    {"process_info" {"foreground_processes" [(dict) #* _rest]}} None
+    {"process_info" {"foreground_processes" []}} (return None)
+    {"process_info" (dict) :as info} :if (not-in "foreground_processes" info)
+    (return None)
+    _ (raise (HerdrContractError
+               f"herdr pane.process_info answer is outside the API contract: {result !r}")))
+  (setv commands [])
+  (for [proc (get (get result "process_info") "foreground_processes")]
+    (when (not (and (isinstance proc dict)
+                    (isinstance (.get proc "name") str)
+                    (isinstance (.get proc "argv0") #(str NoneType))
+                    (or (is (.get proc "argv") None)
+                        (and (isinstance (.get proc "argv") list)
+                             (all (gfor part (.get proc "argv") (isinstance part str)))))))
+      (raise (HerdrContractError
+               f"herdr pane.process_info process is outside the API contract: {proc !r}")))
+    (setv argv0 (.get proc "argv0"))
+    (setv argv (.get proc "argv"))
+    (setv from-argv (when argv
+                      (.removeprefix (. (pathlib.PurePosixPath (get argv 0)) name) "-")))
+    (.append commands (match #(argv0 from-argv)
+                        #((str) :as named _) :if named named
+                        #(_ (str) :as derived) :if derived derived
+                        _ (get proc "name"))))
+  (match (lfor command commands :if (not-in command IDLE-SHELL-COMMANDS) command)
+    [chosen #* _rest] chosen
+    _ (get commands 0)))
```

実装者が足しそうな回帰テスト(`sessionhost_substrate_herdr_deftests.hy` の M2 の表のテストへ追加):

```hy
  (<- wrapped (herdr-foreground-command
                (answer [{"pid" 20 "name" "bash" "argv" ["bash" "./run-agent.sh"]}
                         {"pid" 21 "name" "2.1.201" "argv0" "claude"}])))
  (assert (= wrapped "claude"))
```

## 2. 本来の責務所有者と、違反する契約

- **本来の持ち主は M5(`policy.hy` の zombie reaper)です。** 次の 3 点を M5 が持っています。
  - idle shell の語彙(`policy.hy:915`)。
  - 「前面が idle shell に戻ったか」の判断(`policy.hy:1479-1495`)。
  - agent が起動する helper の扱い。`policy.hy:912-914` のコメントは「shell を列挙する — agent が fork する短命の helper を誤分類しないため、agent 側の blacklist にはしない」と書いています。

  effect の docstring(`effects.hy:637-640`)も「zombie 判定(idle shell 列挙)は policy 所有」と明記しています。
- **移るもの**
  - 知識: idle shell の語彙が M2 でも使われるようになり、substrate が policy の判定語彙に依存します。
  - 判断: 「shell と shell 以外が並ぶ job は idle ではない」という zombie 判定の一部が、herdr の経路だけで M2 の中で行われます。M5 が受け取るのは加工後の名前なのに、M5 はそれを前面の command だと思って判断します。
- **破られる公開契約**
  - M2 の公開の形は「前面の job の先頭の process の command 名」です(`conformance/herdr-physics.md` も「`foreground_processes[0]` の command 名を使う」と書いています)。これが黙って「shell でない最初の process」に変わります。
  - effect `TmuxPaneCurrentCommand` の意味(「pane の foreground command 名」)が herdr の経路でだけ変わります。M4 と M6 が同じ effect に同じ意味で答える前提、つまり M5 が「substrate の種類を隠す」前提が崩れます。
- **破られる将来の変更についての主張**
  - S1: M2 の出力が herdr の契約だけでなく policy の定数でも変わります。M5 の語彙変更(たとえば herdr 自身は shell として扱う `nu`・`pwsh` を足す)が M2 の選び方を変えてしまうので、「M2 の 1 定義だけ」「M3・M4・M5 は変わらない」が成り立ちません。
  - S3: 答えを dict で M2 に渡した結果が policy の状態に依存します。さらに M5 の偽の handler を使うテストは、本番の herdr で実際に行われる「job の構成を見た判断」を表現できなくなります。
- **正しい置き場所(参考)**: 判断は M5 に残すべきです。観測側が job の構成を渡し、両方の substrate が同じ形で答える形にすると、M2 は herdr の契約だけを知っていれば済みます。

## 3. 提示された各検査が拒否しない理由(すべて読みからの推測・未実行)

- **pytest `test_herdr_foreground_command_follows_the_process_info_contract`**(`sessionhost_substrate_herdr_deftests.hy:275-330`): 既存の表はすべて process が 1 つか、空か、鍵の省略です。process が 1 つなら `commands` は 1 要素なので、出力は変わりません(claude→"claude"、zeus→"zsh"、`-bash`→"bash"、argv0 が null→"zsh"、comm だけ→"codex"、`["-"]`→"zsh"、空・省略→None)。契約の外の 8 通りも同じく `HerdrContractError` になり、文言に "pane.process_info" が入ります。`[["zsh"]]` と `process_info` が None の場合は最初の `match` が、name 欠落・argv0 が数値・argv が文字列・argv の要素が数値の場合はループ内の検査が拒否します。process が 2 つ以上の場合は 1 件も試されていません。
- **pytest `test_herdr_pane_current_command_reads_answers_without_argv0`**: M3 は変更しません。zeus の答えは zsh だけなので "zsh"、鍵の省略は None、`pane_not_found` は None、`invalid_request` は送出されたまま、request の列も同じです。
- **`test_herdr_lifecycle_smoke` ほか**: 実 herdr が無ければ skip されます。実 herdr がある場合も、素の shell pane は最終的に zsh だけになり、5 秒待つループで吸収されると推測します。
- **`sessionhost_policy_deftests.hy`**: `TmuxPaneCurrentCommand` を偽の handler(`world.pane-commands`)で答えるので、M2 の変更は届きません。
- **semgrep**: この file に効く generic 規則のパターンと差分を照らし合わせました。
  - `doeff-agents-herdr-label-holders-must-not-be-indexed` の正規表現は `holders` で終わる名前の添字だけを見ます。差分の `(get commands 0)`・`(get argv 0)` は当たりません。
  - `…-identity-not-agent-name`(`"agent.get"`)は差分に該当する文字列がありません。
  - `…-agent-argv-head-names-the-driver-executable` は `"claude"`/`"codex"` を引用符付きで argv の先頭に置く形だけを見ます。本体の差分にはありません。
  - `doeff-agents-no-claude-print-mode` は同じ行の `claude … -p` を見ますが、該当しません。
  - defhandler の位置、legacy の defk の引数、`(! …)`、`make-cause "lost"`、env の綴り等の規則も該当しません。
  - policy からの import、shell 語彙の二重利用、process の選び方を見る規則はありません(`substrate.hy:61` が既に policy の定数を import しているので、形としても前例どおりです)。
  - `tests/semgrep/test_vm_failfast_semgrep_rules.py` は検体(fixture)にだけ規則を当てるテストで、差分の影響を受けません。
- **defk の台帳**(`defadr_doeff_hy_004_defk_only.hy`): 差分は `(deff ` の文字列を足さず、関数は defk のままです。読み取り対象の写しで `(deff\s` を数えると 13 で、台帳の 13 と一致しました(これは grep で実際に数えた値)。差分の後も 13 なので、「増えた」「台帳の削り忘れ」のどちらの判定も通ります。
- **`:pre`/`:post`**: 入力は dict、戻り値は `commands` の要素(str)か None です。

## 4. 検査通過と責務違反を同じ設定で確かめる手順

1. 共有 checkout ではなく隔離した写しに、上の差分と追加テストを当てる。
2. 検査を走らせる(期待はすべて緑。未実行なので推測):
   - `uv run pytest packages/doeff-agents/tests/test_sessionhost_substrate_herdr.py -k "process_info_contract or without_argv0"`
   - `uv run pytest packages/doeff-agents/tests/test_sessionhost_policy.py -k zombie`
   - `uv run pytest docs/adr/defadr_doeff_hy_004_defk_only.hy -k deff_ratchet`
   - `uv run semgrep --metrics=off --disable-version-check --config .semgrep.yaml --error packages/doeff-agents/src/doeff_agents/sessionhost/substrate_herdr.hy packages/doeff-agents/tests/sessionhost_substrate_herdr_deftests.hy`
3. 違反を確かめる(実 herdr は不要で、結果は決定的):
   - **入力 A(判断の移動)**: `foreground_processes = [{"pid" 20 "name" "bash" "argv" ["bash" "./run-agent.sh"]} {"pid" 21 "name" "2.1.201" "argv0" "claude"}]`
     - 期待: M2 は `"claude"` を返す。公開の形(先頭の process = `"bash"`)と食い違う。
     - 同じ状態で M5 に `"bash"` を答える偽の handler を使うと exited になる。つまり substrate によって zombie 判定が変わる。
   - **入力 B(語彙への依存)**: `[{"pid" 30 "name" "nu" "argv" ["nu"]} {"pid" 31 "name" "git" "argv" ["git" "status"]}]`
     - 期待: まず `"nu"` を返す。
     - 同じ process 内で `(.add doeff_agents.sessionhost.policy.IDLE-SHELL-COMMANDS "nu")` として M5 の語彙だけを変えると、同じ dict から `"git"` を返す。herdr の答えは変わっていないのに M2 の出力が変わる。確認後は `.discard` で戻す。
   - **静的な確認**: `grep -n "IDLE-SHELL-COMMANDS" packages/doeff-agents/src/doeff_agents/sessionhost/*.hy` を実行すると、`policy.hy` に加えて `substrate_herdr.hy`(import と M2)にも出る。

## 5. 未確認の前提・不足する情報

- **読んで確認したもの**
  - herdr の Linux 実装が pid の昇順に並べること(`linux.rs:479`)と、argv0 を埋めないこと。
  - 公開 schema の必須欄(pid・name/pane_id)。
  - `launch.hy` が上書きされた起動 command をそのまま送ること。
  - `substrate.hy` が policy を import していること。
  - policy が effects だけを import していて、import が循環しないこと。
  - deff の数が 13 であること。
- **推測(材料に無い、または未実行)**
  - すべての検査の通過。
  - Hy の `match` の書き方(`None` を本体にする腕、`[chosen #* _rest]`)がそのまま compile されること。既存コードの形に合わせましたが、compile はしていません。
  - 入れ子にした処理の例外の伝わり方は、ループを M2 の中に直接書いたので前提にしていません。
  - doeff-adr の pytest plugin が `.hy` を path 指定で集めること。
- **不足**
  - macOS の herdr の並び順(`proc_listpids` の順)は材料では決まりません。
  - `crate::detect::foreground_job` の実体が材料にありません。
  - tmux の `#{pane_current_command}` が group leader を返すかどうかも材料にありません。このため「tmux の経路なら刈られる」という substrate 間の食い違いは推測です。
  - wrapper で起動する運用が実際にあるか、どれくらいの頻度かは不明です。bash が claude を exec せずに子として残す挙動も、材料外の知識による推測です。
  - `scripts/check_enforcement_ledger.py` は写しに含まれていません(差分は `docs/adr` と `.semgrep.yaml` を触らないので、その hook は起動しないと推測します)。
- **判断しなかった検査**: 提示されていない ruff・pyright・doeff-linter と、ADR-004 の deftest。`defadr_doeff_agents_004_effects_session_host.hy` は `substrate_herdr` の別の関数を import していますが、結果は判断していません。
- **副作用**: 差分は 2 番目以降の process も契約どおりか検めるため、以前は無視していた契約の外の後続 process で `HerdrContractError` を出すようになります。
- **別の抜け道**: M3 が `HerdrContractError` を捕まえて None に落とす案(監視の周回を落とさないためという動機が付きやすい)も、M3 経由で契約の外の答えを試すテストが無いので通ると推測します。ただし具体化はしていません。
- 以上は反例が 1 つ見つかったという報告で、合格や安全を意味しません。
