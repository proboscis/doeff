## 失敗例: 業務の語の規則に、5 つ目の汎用 package を足す

失敗例を 1 つ見つけ、`/tmp` の使い捨ての repo で再現しました。崩れるのは主張 S-distribution-rules の「`.semgrep.yaml` に規則を足す・変える → 変わるのは rules だけ」です。同じ主張の「plain-hook と pod-installer は変えなくてよい(Pod の hook の写しを替えなくてよい)」の側は、この例でも成り立ちます。

### 1. 変更要求と、それが必要になる理由
- **要求**: 規則 `doeff-packages-have-no-application-vocabulary` の対象に 5 つ目の汎用 package を足す。例は `doeff-core-effects` です。
- **理由**: R1 は対象を「doeff-cluster・doeff-records・doeff-claude-code・doeff-codex **など**」と書いています。規則の message にも「元は 3 package」とあります。実際に 2026-10-10 に doeff-codex が足され、3 つから 4 つになりました(git の履歴で実測)。汎用の package を作るたびに繰り返し起きる変更で、極端な規模でも無関係な要求でもありません。

### 2. 元の主張・前提と、予想に反して変わるもの
- **前提**: S-distribution-rules の「規則を変える」です。`paths.include` に 1 行足すのはこれそのものなので、前提の内側(契約の適用範囲内)の変更です。
- **主張**:
  - 規則の変更は作業木の版で効く。
  - 予想される波及は rules だけ。
  - 責務の分担は、rules が「pattern と paths」、hook-items が「file 種の選別(files / types / exclude)」。
- **実際に起きること**: rules だけを変えると、新しい package の Python と Hy 以外の file(README.md・toml・sh・pyi など)はどの項にも渡りません。素の hook はそのまま通します(exit 0)。止めるには、hook-items の `semgrep-application-vocabulary` の `files: '^packages/doeff-(cluster|records|claude-code|codex)/'` も変える必要があります。つまり変更は **rules と hook-items** の 2 か所に及びます。
- **変わらないもの**: plain-hook・pod-installer・locked-entry・baseline は変わりません。c3 では hook の写しを替えずに止まりました。
- **強制方法も気づかない**:
  - `test_plain_hook_runs_every_semgrep_hook_of_the_config` は別名の有無しか見ないので緑のままです(実測)。
  - `test_plain_hook_stops_staged_application_vocabulary` は既存の 4 package の path を固定で使っています。
  - 残る後ろ盾は日次の ratchet だけです。翌日に初めて赤になる形で、R1 が直そうとした失敗と同じです。

### 3. どの知識がどこへ漏れているか
- **知識**: 「どの package が汎用か(業務の語の規則が当たる範囲)」です。これが rules の `paths.include` と、hook-items の `files` の regex(と `name` の文字列)の 2 か所に書かれています。
  - hook-items の本来の知識は file 種(types・拡張子)の選別です。ところがこの項の regex は、1 つの規則の paths をそのまま写した物です。
  - 規則の message は「語の一覧の定義元はこの規則 1 つ」と書いていますが、package の一覧は 2 か所にあります。
- **契約・配線・不変条件との区別**:
  - 公開契約の拡張ではありません。規則の id・別名 `semgrep-rules`・plain-hook と locked-entry の契約・項の数は変わらず、新しい項も別名も足しません。
  - 配線の変更でもありません。plain-hook・pod-installer・別名の配線は変わりません。
  - 共同の不変条件(「Python と Hy 以外に当たる規則の paths ⊆ それを渡す項の files」)として読むこともできます。ただし §2〜§4 はこれを宣言しておらず(§3 は「rules だけ」と予想)、§4 の検も守っていません。宣言も強制も無いので、**実質は rules の知識が hook-items へ漏れている**と判断しました。これは推測を含む判断です。
- **実例(実測)**: 2026-10-10 の 3→4 の拡張は 2 つの commit に分かれていました。
  - `3c7c9b87c`(13:54): `.semgrep.yaml` の paths と message だけを変更。
  - `81084e7d3`(14:00): `.pre-commit-config.yaml` の `name` と `files` を変更。同じ commit で `tests/test_package_file_names_have_no_application_vocabulary.py` も変わっています。この file は読める範囲の外なので、確かめたのは変更の統計だけで、中身は読んでいません。

### 4. 最小の入力・再現手順・観測すべき結果
script は `/tmp/dc-vocab-bes750/bin/exp.sh` です。
1. 空の git repo に、15e6fc9c4 の `scripts/git-hooks/pre-commit`・`scripts/precommit_locked.py`・`scripts/semgrep_locked.py`・`scripts/hook_finding_baseline.py`・`.pre-commit-config.yaml` を写す。
2. `.semgrep.yaml` は業務の語の規則だけ(312〜335 行)を写し、`- "/packages/doeff-core-effects/**"` を 1 行足す。これが将来の変更です。
3. `uv.lock` は semgrep 1.169.0 と pre-commit 4.6.2 の 2 項だけにし、基点は空にする。base を commit する。
4. `packages/<pkg>/README.md` に「宣言の欄(agora の kanban の行)」を書いて stage し、根で `sh scripts/git-hooks/pre-commit` を撃つ。
5. 比べるため、日次と同じ形 `semgrep --config .semgrep.yaml packages/ --error --json` も撃つ。

| case | stage した file | hook-items | 素の hook | 日次と同じ形 |
|---|---|---|---|---|
| c1 対照 | doeff-records/README.md | 変えない | **exit 1**・規則の名が出る | exit 1 |
| c2 rules だけ変更 | doeff-core-effects/README.md | 変えない | **exit 0**・`semgrep 業務の語(…)...(no files to check)Skipped` | exit 1・所見あり |
| c3 rules と hook-items | 同じ | regex に core-effects を足す | **exit 1**・規則の名が出る | exit 1 |

観測すべき点は c2 です。素の hook が `Skipped` で通る一方、同じ file に日次と同じ形の走査は所見を出します。

### 5. 未確認の前提・不足する情報
- **実測したもの**:
  - c1〜c3 の終了値と出力。
  - c2 の木で別名の検と同じ判定を再現し、緑になったこと。
  - 2 つの commit の差分。
- **推測・未確認のもの**:
  - `doeff-core-effects` は、検の import `doeff_core_effects` から推した名です。packages/ の下に在るか、汎用と判断され得るかは確かめていません。regex に無い名なら、どの package でも同じ結果になるはずです。
  - 本物の uv.lock は読める範囲の外なので読んでいません。版は docstring にある 1.169.0 と、cache に在った 4.6.2 を使いました。項の選別は版に依らないと推測しています。
  - 規則は 1 つに絞り、台帳の突合(`check_enforcement_ledger.py`)は写していません。README の stage は docs/adr にも .semgrep.yaml にも触れないので、本物の hook でも台帳の突合は撃たれないと推測しています。
  - 枠組みを据えた機体・Pod の上・本物の日次の test(基点の数)は試していません。試したのは README.md だけで、pyproject.toml・.sh・.pyi も同じく抜けると推測しています。
  - 同じ dir の `design.md` は読める範囲の外なので読んでおらず、そこに不変条件が宣言されているかは分かりません。2 つの commit が別々に main に入ったかどうかも確かめていません。
- **本題の外の観察(どれも試していない推測)**:
  - **Rust と generic の規則**: languages が `[rust]` の規則が 84 件あります。また generic の規則で paths が `/packages/**` の物(例 `doeff-agents-no-claude-print-mode`)もあります。4 package の外の Python・Hy 以外の file はどの項にも渡らないので、これらは commit の時には当たらないと見ています。
  - **台帳の引き金の重複**: 素の hook は台帳の引き金 `docs/adr .semgrep.yaml` を、項 `enforcement-ledger` の `files` とは別に自分で持っています。`.semgrep.yaml` を分割すると Pod の hook の写しの入れ替えが要ります。ただしこれは規則の置き場所という前提を変える変更なので、契約の適用範囲外です。
  - **Hy・TS・文書の項に基点が無い**: これらの項は `--error` で、基点と比べません。既存の所見がある規則を足すと、R5 に反して既存の所見で塞ぐ恐れがあります。

### 実行したコマンドと終了値
共有の木では `git log` と `git show` で読んだだけです。add・commit・checkout などはしていません。

1. `sha256sum blind-input.md; wc -l …` → 0
2. `cat scripts/git-hooks/pre-commit .pre-commit-config.yaml agora-worker-prepare-excerpt.sh` → 0
3. `git log -1; wc -l <読める file>; ls scripts/hook_finding_baseline/ .scratch/…/` → 0
4. `grep -n "def \|plain_hook" tests/test_lint_entrypoint_wiring.py` → 0
5. `grep … .semgrep.yaml`(規則の在り処と languages の数え) → 0
6. `which uv semgrep pre-commit git; uv --version; uv cache dir; ls <cache>` → 0(which は semgrep と pre-commit を見つけず)
7. `ls <uv cache>/wheels-v6/pypi | grep semgrep|pre-commit` → 0
8. `ls … | sed | sort -u`(cache の版の一覧) → 0
9. `git log -S'doeff-codex' / -S'semgrep-application-vocabulary' -- .semgrep.yaml .pre-commit-config.yaml` → 0
10. `git show --stat 3c7c9b87c 81084e7d3 -- …` → 0
11. `git show --format= 81084e7d3 / 3c7c9b87c -- …`(差分) → 0
12. `mktemp -d; uv tool run --python 3.12 --from semgrep==1.169.0 semgrep --version; uv tool run … pre-commit==4.6.2 pre-commit --version` → 0 / 0
13. `sh bin/exp.sh c1-control packages/doeff-records/README.md no` → 0(内側: 素の hook 1・日次と同じ形 1)
14. `sh bin/exp.sh c2-rules-only … no; sh bin/exp.sh c3-rules-and-items … yes` → 0(内側: c2 は hook 0・日次 1、c3 は hook 1・日次 1)
15. `uv run --no-project --python 3.12 --with pyyaml python -I -c <別名の検の判定>`(c2 の木で) → 0

WAIT: work — 呼び手の会話の次の依頼
