## 結論

素の hook(`scripts/git-hooks/pre-commit`)に「semgrep の項が当たる file を stage していなければ pre-commit を起こさない」という前段の判定を足す実装は、提示された `test_plain_hook_*` の 6 件をすべて通りました(実測)。その一方で、`.pre-commit-config.yaml` の semgrep の項だけが持つはずの「どの file に semgrep を当てるか」の知識を写して持ち、Pod の hook だけが違反を黙って通します。この取りこぼしは、今の設定のままでも(違反 1)、設計が「素の hook は変えなくてよい」と言う将来の変更の後でも(違反 2)、実際に起きました。

## 1. 機能要求と差分

**要求(現実的な形)**: 素の hook の (1) は、semgrep の項が当たる file を 1 つも stage していない commit(docs/adr・Makefile・uv.lock だけ、など)でも毎回動く。uv で python を解き、道具の置き場から pre-commit を起こし、未 stage の変更を stash して戻す。Pod の担当は小さな commit を多く積むので、当たらない commit では pre-commit を起こさないでほしい。

**差分**(写しの中で当てた物。`/tmp/dc-semgrep-K8QL/hook.patch`):

```diff
 RULES="$TOP/scripts/precommit_locked.py"
-if [ -f "$RULES" ]; then
+# semgrep の項が当たる file の形(Python と Hy・読む面の TypeScript・汎用の 4 package の中)。この形の file を 1 つも stage して
+# いない commit(docs/adr・Makefile・uv.lock だけなど)では pre-commit を起こさない — uv の python の解き・道具の置き場からの
+# 起動・未 stage の変更の stash と戻しを、当たらない commit で毎回払わない(Pod の担当は小さな commit を多く積む)。
+SEMGREP_FILES='\.(py|hy|hyp|hyk)$|^ide-plugins/vscode/doeff-runner/src/read/.*\.ts$|^packages/doeff-(cluster|records|claude-code|codex)/'
+if [ -f "$RULES" ] && git diff --cached --name-only --diff-filter=ACMR | grep -Eq "$SEMGREP_FILES"; then
   (cd "$TOP" && uv run --no-project --python 3.12 python "$RULES" run semgrep-rules) || {
```

正規表現は、今の 4 項の `files`・`types` を見て写した物です。別名の呼び出し `run semgrep-rules` は残してあります。

## 2. 本来の持ち主と、侵す契約

- **知識の移動**: 「どの file 種に semgrep の項を当てるか」は hook-items(`.pre-commit-config.yaml` の 4 項)が唯一の定義点で、plain-hook が隠すはずの知識です(設計の表 2)。差分の後は、plain-hook が `SEMGREP_FILES` という 2 つ目の定義を持ちます。「この commit に semgrep は要らない」という判断も、plain-hook が先にしてしまいます。
- **stage の読み手が 2 人になる**: plain-hook が自分で `git diff --cached` を読みます。pre-commit とは別の読み方なので、file 名の扱いが食い違います。
- **契約「hook-items → plain-hook は `pre-commit run semgrep-rules`」**: 呼び出しの文字は残りますが、呼ぶかどうかが写しの知識で決まります。選ぶ所が 1 か所という契約の中身が崩れます。
- **将来の変更についての主張が崩れる**:
  - S-distribution-items(「項に別名を書けば両方の機体に届く・plain-hook は不変」)
  - S-distribution-rules(「Pod に入った hook の写しを替えなくてよい」)。規則を広げる時に項も変える場合が当たります。
- **Pod の写し(pod-installer)の版の問題**: worker-prepare.sh の 5. は、Pod の準備の時に hook を 1 度だけ写します(抜粋で確認)。そのため写した知識は「Pod を準備した時の main の版」で止まります。将来の変更で `SEMGREP_FILES` を一緒に直しても、準備済みの Pod には届きません(差分の形と抜粋からの推論。Pod では試していません)。
- **見え方**: 黙って通します。枠組みを据えた機体は止め、素の hook の機体(Pod)だけが通すので、機体の種類で結論が割れます。

## 3. 提示された各検査が拒まない理由

- **`test_plain_hook_runs_every_semgrep_hook_of_the_config`**: 確かめるのは 3 つだけです。entry に「semgrep」を含む項の集合と、別名 `semgrep-rules` を持つ項の集合が一致すること。そして hook の文に `run semgrep-rules` があること。差分はどちらも崩さないので、通りました(実測。将来の変更の後も通過)。
- **`test_plain_hook_stops_staged_application_vocabulary`**: 3 つの入力(`packages/doeff-cluster/…/example.hy`・`packages/doeff-codex/tests/test_example.py`・`packages/doeff-records/README.md`)はどれも ASCII の名前で、4 package の中にあります。写した正規表現に当たるので pre-commit が撃たれ、赤のままです(実測で通過)。
- **`test_plain_hook_passes_staged_file_without_findings`**: 正規表現に当たり、所見が無いので 0 です(実測で通過)。
- **`semgrep_locked.py`(版は uv.lock)**: 差分は道具の呼び方を変えません。
- **日次の `test_semgrep_findings_match_baseline_ratchet`**: 走らせていません(指示どおり)。コードを読む限り、`doeff/` と `packages/` だけを走査して所見の数を比べます。差分が触るのは `scripts/git-hooks/pre-commit` だけなので、新しい所見は生まれないと推測します。違反が main に入った翌日に赤になるかどうかは確かめていません。これは R1 が無くしたかった「翌日に初めて赤」の形そのものです。

## 4. 最小の再現(同じ設定で、通過と違反の両方)

**準備**: `git clone --no-hardlinks <作業木> /tmp/dc-semgrep-K8QL/doeff`(15e6fc9c4)。原本の hook を `orig/pre-commit` に写し、上の差分を当てます。

**検査の通過**: 写しの根で次を実行します。

```
PATH=~/repos/doeff/.venv/bin:$PATH uv run --no-project --python ~/repos/doeff/.venv/bin/python python -m pytest tests/test_lint_entrypoint_wiring.py -k plain_hook -q
```

期待は 6 passed、**実測も 6 passed** でした(差分だけの時も、将来の変更を足した時も)。

**違反の観察**: `/tmp/dc-semgrep-K8QL/driver.py` を使いました。検の helper(`_plain_hook_repository`・`_environment`・`_run_child`)をそのまま呼ぶので、偽の uv.lock・空の基点・業務の語の規則 1 つ・本物の semgrep 1.169.0 という設定は検と同じです。下の表は実測の値です。

| 入力 | 差分の後の素の hook | 原本の素の hook | 枠組み `pre-commit run semgrep-rules` | `.git/hooks` に写した差分の後の hook で `git commit` | 原本の写しで `git commit` |
|---|---|---|---|---|---|
| 違反 1(今の設定のまま)`packages/doeff-records/説明.md` に「宣言の欄(agora の kanban の行)」 | 0・規則名なし | 1・規則名あり | 1・規則名あり | 0・commit が入った(HEAD は 2 本) | 1・入らない(1 本) |
| 違反 2(将来の変更の後)`packages/doeff-agents/README.md` に同じ文 | 0・規則名なし | 1・規則名あり | 1・規則名あり | 0・commit が入った(2 本) | 1・入らない(1 本) |

- **違反 1 の原因**(実測): `git diff --cached --name-only` は日本語の名前を `"packages/doeff-records/\350\252\254\346\230\216.md"` と引用符つきで出します(core.quotePath は既定のまま)。先頭の `"` のせいで `^packages/` に当たりません。
- **違反 2 の将来の変更**(`/tmp/dc-semgrep-K8QL/future.patch`): 文書の手順どおりに行います。規則 `doeff-packages-have-no-application-vocabulary` の `paths.include` に `/packages/doeff-agents/**` を足し、別名つきの項 `semgrep-application-vocabulary` の `files` に `agents` を足します。hook は設計の主張どおり変えません。

## 5. 未確認の前提と、足りない情報

- **動機の数字**: pre-commit の起動と stash の待ちは測っていません。要求の形として置いた物です。
- **`packages/doeff-agents`**: 実在を確かめていません(6. の範囲外なので)。違反 2 では仮の名前として使いました。
- **検の走らせ方**: `uv run pytest` を写しの中で sync して走らせてはいません。共有の `~/repos/doeff/.venv` を読み取りだけで使いました。その中身は python 3.14t、doeff は /work/toolset/repos/doeff の 0ddffec2 で、15e6fc9c4 とは版が違います。
- **走らせていない検査**: 日次の ratchet。実際の木の根での素の hook と枠組みは、検と同じ組み立ての小さな repo でだけ走らせました。
- **Pod 側**: Pod の git に core.quotePath=false の設定があるかは知りません。設定があれば違反 1 は再現しないはずですが、違反 2 はそれに依りません。
- **作業木の間での hook の共有**: 同じ clone の別の作業木が `.git/hooks` の 1 つの写しを使う、という点は確かめていません。写しの中での `git worktree add` は環境の検査に止められました。
- **他の規則**: 検の repo は業務の語の規則 1 つに絞っているので、ほかの規則は試していません。`types: [python]` と拡張子の正規表現の違い(実行可能で shebang を持つ file)による取りこぼしも推測に留まります。
- **これで安全とは言えない事**: ここで示したのは失敗例 1 つです。検査がほかに抜け道を持たないことは確かめていません。

**私が守れなかった事**: Python の実行の一部は uv を通していません。共有の venv の python を直に呼んだ回が 3 回、写しの設定 2 file を書き換えるのに機体の `python3` を 1 回使いました。共有の file は変えていません(`git status` で確認)。uv の cache(`/work/state/uv-cache`)には書き込みが起きた可能性があります。

## 実際に実行したコマンドと終了の値

1. `sha256sum` と `wc -l` で blind-input.md を確かめる → 0
2. `cat` で plain hook・`.pre-commit-config.yaml`・`precommit_locked.py`・`semgrep_locked.py` を読む → 0
3. `grep -n plain_hook` で検の file を探し、`wc -l` → 0
4. `sed -n` で検の file の 1–60 行と 280–413 行を読む → 0
5. `sed -n` で検の 60–140 行を読み、`cat` で worker-prepare の抜粋を読む → 0
6. `cat` で `hook_finding_baseline.py` を読み、`ls` で基点の dir、`head` で semgrep.json → 0
7. `cat tests/test_semgrep_gate.py` → 0
8. `wc` と `grep` で `.semgrep.yaml` の規則を読む → 0
9. `git rev-parse HEAD`・`git status`・`which`・`uv --version`・`ls ~/repos/doeff/.venv`・`env|grep` → 0(最後の命令の値。`which` の一部は見つからず)
10. `PYTHONDONTWRITEBYTECODE=1 ~/repos/doeff/.venv/bin/python -c "import …"`、`semgrep --version`、`git -C ~/repos/doeff rev-parse HEAD` → 0
11. `uv python find 3.12` → 0、`mktemp -d`、`git clone -q --local …` → 128(別の device への hardlink で失敗)
12. `git clone -q --no-hardlinks … /tmp/dc-semgrep-K8QL/doeff` → 0
13. `cp` で原本の hook を写し、`grep` → 0
14. Edit tool で写しの hook に差分を当てる(命令ではない)
15. `git diff > hook.patch` と `sh -n scripts/git-hooks/pre-commit` → 0
16. venv の python を直に呼び `-m pytest … -k "plain_hook and runs_every" -p no:cacheprovider -q` → 0(1 passed)
17. venv の python を直に呼び `-m pytest … -k plain_hook -p no:cacheprovider -q` → 0(6 passed)
18. venv の python を直に呼び `driver.py … "packages/doeff-records/説明.md" …` → 0(表の違反 1)
19. 機体の `python3` で写しの `.semgrep.yaml` と `.pre-commit-config.yaml` を書き換え、`git diff` → 0
20. `uv run --no-project --python ~/repos/doeff/.venv/bin/python python -m pytest … -k plain_hook -p no:cacheprovider -q` → 0(6 passed)
21. `uv run --no-project --python … python driver.py … "packages/doeff-agents/README.md" …` → 0(表の違反 2)
22. 写しの中での `git worktree add` → 環境の hook に止められ、走っていません
23. `git status --short` で `~/repos/doeff` と共有の作業木、`ls /tmp/dc-semgrep-K8QL` → 0
24. `ls --time-style=full-iso ~/repos/doeff/.agora` と `date` → 0(`.agora/` は私が走らせる前からあった)
25. 写しの検の repo で `git diff --cached --name-only --diff-filter=ACMR` → 0、`git config --get core.quotePath` → 1(設定なし)
