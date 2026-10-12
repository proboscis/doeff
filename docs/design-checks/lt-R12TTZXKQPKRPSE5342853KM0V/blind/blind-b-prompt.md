# 盲検 B への入力(固定の依頼文 + 材料)

以下は静的検査を通過する責務違反の失敗例探しの依頼です。設計の承認判定は求めていません。

あなたは静的検査を通過する責務違反の失敗例を生成してください。
合理的な機能追加・修正として実装されそうで、提示した検査をそのまま実行しても通るのに、
知識・判断・状態・effectsの所有者や公開契約を侵す実装を1つ具体化してください。
一般的な設計レビューや承認判定は求めていません。

検査の無効化、baselineの改変、対象除外、as any、ignoreやlint抑制、根拠のないcast、
動的importによる回避は使わないでください。小さい既存例に対する抜け道だけでなく、
通常の実装者が機能を実現しようとして書きそうなコードで示してください。

返答には次を含めてください。
1. 現実的な機能要求と、提案する具体的なコードまたは最小差分。
2. 本来の責務所有者と、違反する契約。何の判断・知識がどこへ移るか。
3. 提示された各検査がそのコードを拒否しない理由。未提示の検査は推測しないこと。
4. 同じ設定で検査通過と責務違反の両方を確かめる最小の入力・手順・期待結果。
5. 未確認の前提、不足する情報。推測と実測を明確に区別すること。

共有ソースを変更せず、全数検査や他会話への連絡を行わず、日本語で返してください。
実行していない検査を通ったと断定しないでください。失敗例が見つからない場合は探索した範囲と
不足を述べ、合格・安全とは結論しないでください。

---

# 材料(盲検 A・B に同じ物を渡す)

対象 repo: doeff。対象の変更: commit 3682f28c8b06bbd18f125619a85f35d88f337e21(doeff main に着地済み)。
読める所: この材料の本文(下に実コードを載せた)。足りない時だけ、読み取りだけで `git -C ~/repos/doeff show 3682f28c8:<path>` や `git -C ~/repos/doeff show 3682f28c8^:<path>`(変更前)を使ってよい(~/repos/doeff が無い・版が無い時は、その不足を返答に書く — 推測で補わない)。
file を書かない・git を動かさない・pytest の全体は走らせない。試す file が要るなら /tmp/blind-<A か B>/ の下にだけ作る。

## 要件(固定)


| id | 内容 |
|---|---|
| R1 | doeff main の EXCLUDED に dir の行 `docs/design/` が無いか、残すなら行の理由が「make test-changed の選びが記録の模型を検として走らせる事」と「R8 の抜け道」への答えを書いている |
| R2 | docs/design/seat-home-common-instructions-AJ8C0B/model/ の test_*.py 4 本が、.py.txt への改名か日次の母集団の根への登録のどちらかに決まり、tests/test_daily_test_population.py が緑 |
| R3 | 記録の中に検の名の file を置いても日次が赤にならない一方、本物の検を記録の dir にだけ置いても緑にならない(ADR-DOE-ENFORCE-001 R8 の役目を弱めない) |

## 責務(modules)


| id | 責務 | 所有する知識 | 隠す知識 | 公開契約 |
|---|---|---|---|---|
| M-name | 「検の file」の名の規則 | root の ini の python_files・doeff_hy_test_files・doeff-adr の既定の pattern | 各 pattern の読み方 | `_test_file_patterns(repo)`・`_is_test_file(rel, patterns)`。scripts/run_changed_tests.py も同じ ini の値から同じ判定をする |
| M-pop | 検の file が日次の母集団の根の下か、理由つきの除外の行の下に在る事の確認(R8) | 母集団の根・EXCLUDED の表(path か dir の接頭辞 → 理由) | 木の歩き方 | `_assert_every_test_file_belongs_to_a_daily_population`・`test_exclusion_table_has_no_stale_rows` |
| M-select | 変えた file から走らせる検を選ぶ(make test-changed) | 変えた検・逆依存の選び | linter の逆依存の問い | scripts/run_changed_tests.py。**名の規則だけを読み EXCLUDED を読まない** |
| M-records | 設計の記録(docs/design/<記録>/・docs/design-checks/<記録>/): その時点の証拠 | 記録の中の模型・測りの script・写し | — | 検の名の file は集め手の pattern に当たらない名(`.py.txt`)で置く。着地の後は書き換えない |
| M-seal | hash で封をした記録の file を linter の規則から外す名指し | evidence/SHA256SUMS.txt の path と hash・architecture.hy の :files | — | tests/test_sealed_records_are_named.py と packages/doeff-linter/tests/rule_population.rs |

## 変更後の設計


1. 検の名の file を記録に置く時は `<名>.py.txt` にする(M-records が名で M-name の外へ出る)。M-pop の dir の行は置かない。
2. 改名できない file(閉じた記録が path と sha256 で名指す)だけを、1 file ごとの path の行で EXCLUDED に載せる(dir の行は置かない — 新しい検は隠せない)。
3. M-select は変えない。記録の中の file が名の規則に当たらないので、選びの入力に入らない(穴 A を、選びではなく**置く名**で閉じる)。
4. 赤の文と表の頭の注釈が、置き方(`.py.txt`)を案内する。

## 将来の変更と主張(前提つき)


6 軸を扱う。

| id | 軸 | 適用 | 変更 | 主張(盲検の前) | 変える責務 | 変えない責務 |
|---|---|---|---|---|---|---|
| S1 | storage | 適用 | 新しい設計の記録が model/ や evidence/ に `test_*.py` の模型や再現の写しを置く | 母集団の検が赤になり、赤の文が .py.txt を案内する。直すのは記録の file の名だけで、M-pop・M-select・M-seal は変えない | M-records | M-name・M-pop・M-select・M-seal |
| S2 | storage | 適用 | 閉じた記録が path と sha256 で名指す `*_test.py` の script が新しい記録に入る(改名できない) | EXCLUDED に 1 file の path の行を 1 つ足す。M-select・M-seal・M-name は変えない。その行の隣に置いた新しい検は赤のまま | M-pop(表の 1 行) | M-name・M-select・M-seal |
| S3 | effects | 適用 | 本物の回帰の検を記録の dir にだけ置く(`docs/design/<記録>/` の下の `test_*.py`) | 母集団の検が赤になる(dir の行が無いので隠れない)。.py.txt に替えると赤は消えるが、その file は走らない — 走る検にしたいなら母集団の根へ置く | なし(赤が出る) | 全部 |
| S4 | concurrency | 適用 | 2 つの会話が同時に別々の記録を足す | .py.txt の置き方なら共有の file を触らない。S2 の行だけが M-pop の表で衝突し得る(稀) | M-records(各自の dir) | M-pop |
| S5 | hardware | 不適用 | — | 名の規則の検は file の名と中身を読むだけで、機体の資源に依らない | — | — |
| S6 | distribution | 不適用 | — | 日次は atlas の Pod の git の作業木で直に走り(R10)、make test-changed は手元の作業木で走る。どちらも同じ ini と同じ名の規則を読む | — | — |
| S7 | simulation | 不適用 | — | 検は file の名・中身・make の出力を読む決定的な検。時刻・乱数を持たない | — | — |

前提: 「検の file」の定義は root の ini が 1 点で持つ。記録の中の file は、読み手が名の規則だけで走らせる物(M-select・日次の pytest)から外れていれば足りる。
未知の将来の分類・新しい種類の記録の dir の追加は予測しない(新しい検の名の規則や集め手の追加は M-name の変更になる)。

## 予測する変更範囲


3682f28c8 の変更は 10 file: 4 本の改名・封の表(SHA256SUMS.txt)・architecture.hy・RECOVERY.md・rule_population.rs・test_daily_test_population.py・
test_sealed_records_are_named.py(下限 4 → 3)。主張: これは「改名の連鎖(封の名指しを持つ 3 責務 — M-seal の 3 か所)」と
「M-pop の表と検」で、M-name・M-select は変わらない。

## 検査の実体(B 向け: 提示する検査はこれだけ。ほかを推測しない)

- 日次の母集団の検: tests/test_daily_test_population.py(下に全文の抜粋)。make test-changed は repo 全体の契約の組としてこの検をどの差分でも走らせ、日次の root 段も走らせる。
- 封の検: tests/test_sealed_records_are_named.py(architecture.hy の :files が封の表に在り hash が一致する事)・packages/doeff-linter/tests/rule_population.rs。
- 選び: scripts/run_changed_tests.py の `collector_patterns`・`is_test_file`(下)。EXCLUDED は読まない。

### 変更の差分(3682f28c8・tests/test_daily_test_population.py)
```diff
diff --git a/tests/test_daily_test_population.py b/tests/test_daily_test_population.py
index 85277385d..466f3f2f1 100644
--- a/tests/test_daily_test_population.py
+++ b/tests/test_daily_test_population.py
@@ -103,15 +103,26 @@ _NOT_WALKED = (
 
 # どの母集団にも属さない test 名の file の除外の表(path か、`/` で終わる dir の接頭辞 → 理由)。
 # 行を足す時は理由を書く。該当する file が 1 本も無い行は古い行として赤になる。
-# 設計検証の記録(docs/design-checks/)は行で外さない — 記録の中の検の写しは集め手の pattern に当たらない名
+# 設計の記録(docs/design/・docs/design-checks/)は dir の行で外さない — 記録の中の検の写し・模型は集め手の pattern に当たらない名
 # (`<名>.py.txt`)で置く。行で外すと、make test-changed の選び(名の規則だけを読む)が写しを検として走らせ、本物の
-# 検を記録の中にだけ置いても緑になる(設計検証 docs/design-checks/lt-9R40Q1PM496VA4HVD1FMXVYW8Z の盲検 A・B)。
+# 検を記録の中にだけ置いても緑になる(設計検証 docs/design-checks/lt-9R40Q1PM496VA4HVD1FMXVYW8Z の盲検 A・B — docs/design/ の
+# 行も同じ形で、seat-home の模型 4 本は 2026-10-12 に .py.txt へ替えて外した・card ki-d153ab595504)。
+# 記録の中の行は、hash で封をした閉じた記録が名指して改名できない file だけを、1 file ごとの path で書く(dir の行と違い、
+# 新しい検を隠せない)。残る穴は make test-changed の選び 1 つ: その file を変えると、選びが検として集める(module の先頭が走り、
+# 0 本集まって「未測」になる — 赤にはならない)。
 EXCLUDED: dict[str, str] = {
     "packages/doeff-agents/conformance/": (
         "tmux / herdr の実 pane を使う黒箱の交代ゲート(実モデルは使わない)。日次の宿で tmux が"
         "使えるかが未測定で、母集団へ入れるかは card acp:kanban-issue:ki-2832a04913cc が決める"
     ),
-    "docs/design/": "設計の検証の模型と実験(その時点の記録で、日次の母集団ではない)",
+    "docs/design/worker-pod-cpu-share/artifacts/2026-10-12/evidence/scripts/fork_test.py": (
+        "測りの script(pytest の検ではなく、module の先頭で sched_setattr と fork を走らせる)。閉じた記録 "
+        "docs/design/worker-pod-cpu-share/records/assessment-1bff46b3e6e1.json が path と sha256 で名指すので改名できない"
+    ),
+    "docs/design/worker-pod-cpu-share/artifacts/2026-10-12/evidence/scripts/reset_test.py": (
+        "fork_test.py と対の測りの script(pytest の検ではなく、module の先頭で sched_setattr と chrt を走らせる)。"
+        "同じ閉じた記録の本文が名で指すので改名しない"
+    ),
     "ide-plugins/pycharm/test_program_detection.py": (
         "PyCharm plugin の検出の検体(テスト関数を持たない)"
     ),
@@ -223,7 +234,7 @@ def _assert_every_test_file_belongs_to_a_daily_population(repo: Path, roots: lis
         f"日次のどの母集団にも除外の表にも無い検の file が {len(orphans)} 本: {orphans}"
         " — 母集団の根の下へ置くか(package の tests/ の外の根は Makefile の PACKAGE_EXTRA_TEST_ROOTS へ足す)、"
         "tests/test_daily_test_population.py の EXCLUDED に理由つきで載せる(呼び手の無い木の緑は日次に見えない)。"
-        "設計検証の記録(docs/design-checks/)の検の写しは、集め手の pattern に当たらない名(`<名>.py.txt`)に替える"
+        "設計の記録(docs/design/・docs/design-checks/)の中の検の写し・模型は、集め手の pattern に当たらない名(`<名>.py.txt`)に替える"
         "— ADR-DOE-ENFORCE-001 R8"
     )
 
@@ -571,6 +582,38 @@ def test_test_named_copy_in_a_design_check_record_is_red_until_renamed(tmp_path:
     _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
 
 
+def test_test_named_file_in_a_design_record_is_red_until_renamed(tmp_path: Path) -> None:
+    """失敗ケース: docs/design/ の記録の中に検の名の file(`test_*.py` の模型・`*_test.py` の測りの script)を置くと赤、集め手の
+    pattern に当たらない名(`.py.txt`)に替えると緑。除外の表に名指しの行がある file の隣に置いた新しい検も赤(card ki-d153ab595504)。
+
+    docs/design/ を dir の行で外していた形では、seat-home の模型 4 本(`def test_*` を持たず `t_*` を __main__ から回す script)が
+    make test-changed の選びで検として集められ(0 本集まって「未測」)、本物の検を記録の中にだけ置いても日次が緑だった
+    (docs/design-checks/ の盲検 A・B と同じ 2 つの穴)。
+    """
+    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
+    (tmp_path / "tests").mkdir()
+    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
+    named = next(Path(row) for row in EXCLUDED if row.startswith("docs/design/") and not row.endswith("/"))
+    candidates = (
+        Path("docs/design/lt-MODEL/model/test_chain.py"),
+        Path("docs/design/lt-MODEL/evidence/scripts/fork_test.py"),
+        named.with_name("hidden_test.py"),
+    )
+    for path in (named, *candidates):
+        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
+        (tmp_path / path).write_text("def test_it():\n    pass\n", encoding="utf-8")
+
+    with pytest.raises(AssertionError) as red:
+        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
+    message = str(red.value)
+    missing = [path.as_posix() for path in candidates if path.as_posix() not in message]
+    assert not missing, f"母集団の外の検として名指されていない: {missing} — R8"
+    assert named.as_posix() not in message, f"除外の表が名指す {named} まで赤になっている"
+    for path in candidates:
+        (tmp_path / path).rename(tmp_path / f"{path}.txt")
+    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
+
+
 def test_every_test_named_file_is_collected_by_some_collector() -> None:
     """test_*.py・test_*.hy の file はどれも、どれかの集め手の pattern に当たる(agora-redesign #2591)。"""
     _assert_every_test_named_source_is_collected(REPO_ROOT)
```

### tests/test_daily_test_population.py の関数(3682f28c8 の版)
```python
_PYTEST_PYTHON_FILES = ("test_*.py", "*_test.py")
# 「検のつもりの名」の file — どれかの集め手に集められなければ、日次で 1 本も走らない(agora-redesign #2591)。
_TEST_NAMED_SOURCES = ("test_*.py", "test_*.hy")


def _test_file_patterns(repo: Path) -> tuple[str, ...]:
    """「何が検の file か」の定義の 1 点 — pytest が集める 3 つの経路の名の規則。どれも集め手そのものが読む値。

    - Python の検: root の ini の python_files(既定は pytest の既定)。
    - Hy の検の file: root の ini の doeff_hy_test_files(doeff-adr の plugin の集め手が同じ値を読む・deftest の
      file も上から順に実行する script も集める — agora-redesign #2591)。
    - executable ADR: doeff-adr の plugin の既定の pattern と root の ini の doeff_adr_hy_files。

    `.py` だけを数えていた時は、母集団の外の `.hy` の検が日次で走らなくても赤にならなかった
    (#2542 の packages/doeff-cluster/src/doeff_cluster/sim/test_entries_on_sim.hy・agora-redesign #2577)。
    Hy の条件を検の側に `test_*.hy` と写していた時は、集める conftest の無い dir の test_*.hy(packages/doeff-hy/tests
    の 6 本)を「検の file」と数えながら、どの集め手も集めていなかった(agora-redesign #2591)。
    """
    ini = _root_ini(repo)
    return (
        *ini.get("python_files", _PYTEST_PYTHON_FILES),
        *ini.get("doeff_hy_test_files", ()),
        *ADR_FILE_PATTERNS,
        *ini.get("doeff_adr_hy_files", ()),
    )


def _is_test_file(rel: str, patterns: tuple[str, ...]) -> bool:
    """pytest の照合と同じ: `/` を含まない pattern は file の名に、含む pattern は repo の根からの path に当てる。"""
    name = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel if "/" in p else name, p) for p in patterns)


# 歩かない dir = pytest の既定の norecursedirs + build の出力(target・__pycache__)。
# 日次の検査の木には make sync の出力(.venv・target)が在り、2026-10-10 までの遠隔の木(remote_check の写し — git の名簿の
# file だけを送る)には .git も無かったので、git ではなく file system を歩く(2026-10-10 から段は日次の task の git の作業木で直に走る
# — ADR-DOE-ENFORCE-001 R10)。
# 2026-09-25 実測: この規則で歩いた test 名の file の集合は、素の worktree(450 本)でも
# build 済みの checkout(449 本)でも `git ls-files --cached --others --exclude-standard` と一致した。
_NOT_WALKED = (
    "*.egg",
    "*.egg-info",
    ".*",
    "_darcs",
    "build",
    "CVS",
    "dist",
    "node_modules",
    "venv",
    "{arch}",
    "target",
    "__pycache__",
)

# どの母集団にも属さない test 名の file の除外の表(path か、`/` で終わる dir の接頭辞 → 理由)。
# 行を足す時は理由を書く。該当する file が 1 本も無い行は古い行として赤になる。
# 設計の記録(docs/design/・docs/design-checks/)は dir の行で外さない — 記録の中の検の写し・模型は集め手の pattern に当たらない名
# (`<名>.py.txt`)で置く。行で外すと、make test-changed の選び(名の規則だけを読む)が写しを検として走らせ、本物の
# 検を記録の中にだけ置いても緑になる(設計検証 docs/design-checks/lt-9R40Q1PM496VA4HVD1FMXVYW8Z の盲検 A・B — docs/design/ の
# 行も同じ形で、seat-home の模型 4 本は 2026-10-12 に .py.txt へ替えて外した・card ki-d153ab595504)。
# 記録の中の行は、hash で封をした閉じた記録が名指して改名できない file だけを、1 file ごとの path で書く(dir の行と違い、
# 新しい検を隠せない)。残る穴は make test-changed の選び 1 つ: その file を変えると、選びが検として集める(module の先頭が走り、
# 0 本集まって「未測」になる — 赤にはならない)。
EXCLUDED: dict[str, str] = {
    "packages/doeff-agents/conformance/": (
        "tmux / herdr の実 pane を使う黒箱の交代ゲート(実モデルは使わない)。日次の宿で tmux が"
        "使えるかが未測定で、母集団へ入れるかは card acp:kanban-issue:ki-2832a04913cc が決める"
    ),
    "docs/design/worker-pod-cpu-share/artifacts/2026-10-12/evidence/scripts/fork_test.py": (
        "測りの script(pytest の検ではなく、module の先頭で sched_setattr と fork を走らせる)。閉じた記録 "
        "docs/design/worker-pod-cpu-share/records/assessment-1bff46b3e6e1.json が path と sha256 で名指すので改名できない"
    ),
    "docs/design/worker-pod-cpu-share/artifacts/2026-10-12/evidence/scripts/reset_test.py": (
        "fork_test.py と対の測りの script(pytest の検ではなく、module の先頭で sched_setattr と chrt を走らせる)。"
        "同じ閉じた記録の本文が名で指すので改名しない"
    ),
    "ide-plugins/pycharm/test_program_detection.py": (
        "PyCharm plugin の検出の検体(テスト関数を持たない)"
    ),
    "packages/doeff-agentic/examples/": "例示の script(__main__ で走らせる)",
    "packages/doeff-test-target/src/doeff_test_target/effects/test_effects.py": (
        "fixture の module 名(テストではない)"
    ),
    "tools/test_python_versions.py": "Python の版を回す道具の script",
}


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


def _is_excluded(path: str, row: str) -> bool:
    return path == row or (row.endswith("/") and path.startswith(row))


def _assert_every_test_file_belongs_to_a_daily_population(repo: Path, roots: list[str]) -> None:
    orphans = [
        path
        for path in _test_named_files(repo)
        if not any(_is_under(path, root) for root in roots)
        and not any(_is_excluded(path, row) for row in EXCLUDED)
    ]
    assert not orphans, (
        f"日次のどの母集団にも除外の表にも無い検の file が {len(orphans)} 本: {orphans}"
        " — 母集団の根の下へ置くか(package の tests/ の外の根は Makefile の PACKAGE_EXTRA_TEST_ROOTS へ足す)、"
        "tests/test_daily_test_population.py の EXCLUDED に理由つきで載せる(呼び手の無い木の緑は日次に見えない)。"
        "設計の記録(docs/design/・docs/design-checks/)の中の検の写し・模型は、集め手の pattern に当たらない名(`<名>.py.txt`)に替える"
        "— ADR-DOE-ENFORCE-001 R8"
    )


def test_every_test_file_belongs_to_a_daily_population() -> None:
    """検の file(.py・.hy)は日次の母集団の根(root の testpaths・package の tests・Makefile の
    PACKAGE_EXTRA_TEST_ROOTS)の下か、除外の表に在る。"""
    _assert_every_test_file_belongs_to_a_daily_population(REPO_ROOT, _population_roots(REPO_ROOT))


def test_test_named_copy_in_a_design_check_record_is_red_until_renamed(tmp_path: Path) -> None:
    """失敗ケース: 設計検証の記録の中に検の名の写し(test_*.py)を置くと赤、集め手の pattern に当たらない名
    (`.py.txt`)に替えると緑(設計検証 docs/design-checks/lt-9R40Q1PM496VA4HVD1FMXVYW8Z)。

    docs/design-checks/ を除外の行で外した形(77e0eb3f9)では、写しは make test-changed の選びで検として走り(名に `.` を
    含む写しは集める時に落ちて赤 1)、本物の検を記録の中にだけ置いても緑だった(盲検 A・B)。
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    copy = tmp_path / "docs" / "design-checks" / "lt-MODEL" / "evidence" / "scripts" / "test_scope.revision.py"
    copy.parent.mkdir(parents=True)
    copy.write_text("def test_scope():\n    pass\n", encoding="utf-8")

    with pytest.raises(AssertionError, match=re.escape(copy.relative_to(tmp_path).as_posix())):
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    copy.rename(copy.with_name(f"{copy.name}.txt"))
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))


def test_test_named_file_in_a_design_record_is_red_until_renamed(tmp_path: Path) -> None:
    """失敗ケース: docs/design/ の記録の中に検の名の file(`test_*.py` の模型・`*_test.py` の測りの script)を置くと赤、集め手の
    pattern に当たらない名(`.py.txt`)に替えると緑。除外の表に名指しの行がある file の隣に置いた新しい検も赤(card ki-d153ab595504)。

    docs/design/ を dir の行で外していた形では、seat-home の模型 4 本(`def test_*` を持たず `t_*` を __main__ から回す script)が
    make test-changed の選びで検として集められ(0 本集まって「未測」)、本物の検を記録の中にだけ置いても日次が緑だった
    (docs/design-checks/ の盲検 A・B と同じ 2 つの穴)。
    """
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    named = next(Path(row) for row in EXCLUDED if row.startswith("docs/design/") and not row.endswith("/"))
    candidates = (
        Path("docs/design/lt-MODEL/model/test_chain.py"),
        Path("docs/design/lt-MODEL/evidence/scripts/fork_test.py"),
        named.with_name("hidden_test.py"),
    )
    for path in (named, *candidates):
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text("def test_it():\n    pass\n", encoding="utf-8")

    with pytest.raises(AssertionError) as red:
        _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))
    message = str(red.value)
    missing = [path.as_posix() for path in candidates if path.as_posix() not in message]
    assert not missing, f"母集団の外の検として名指されていない: {missing} — R8"
    assert named.as_posix() not in message, f"除外の表が名指す {named} まで赤になっている"
    for path in candidates:
        (tmp_path / path).rename(tmp_path / f"{path}.txt")
    _assert_every_test_file_belongs_to_a_daily_population(tmp_path, _population_roots(tmp_path))


def test_exclusion_table_has_no_stale_rows() -> None:
    """除外の表の各行は、今の木の test 名の file を 1 本以上覆う。"""
    files = _test_named_files(REPO_ROOT)
    stale = [row for row in EXCLUDED if not any(_is_excluded(path, row) for path in files)]
    assert not stale, (
        f"除外の表に該当する file が 1 本も無い古い行: {stale} — 消す — ADR-DOE-ENFORCE-001 R8"
    )


```

### scripts/run_changed_tests.py の検の判定(3682f28c8 の版)
```python
def collector_patterns(repo: Path) -> TestFilePatterns:
    """pytest が集める 3 つの経路の名の規則(Python・Hy の検・executable ADR)。"""
    config = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    ini = config.get("tool", {}).get("pytest", {}).get("ini_options", {})
    parts = (
        _ini_strings(ini, "python_files", PYTEST_DEFAULT_PYTHON_FILES),
        _ini_strings(ini, "doeff_hy_test_files", ()),
        _adr_default_patterns(repo),
        _ini_strings(ini, "doeff_adr_hy_files", ()),
    )
    return TestFilePatterns(globs=tuple(glob for part in parts for glob in part.globs))


def is_test_file(path: str, patterns: TestFilePatterns) -> bool:
    """pytest の照合と同じ: `/` を含まない pattern は file の名に、含む pattern は repo の根からの path に当てる。"""
    name = path.rsplit("/", 1)[-1]
    return "fixtures" not in path.split("/") and any(
        fnmatch.fnmatchcase(path if "/" in p else name, p) for p in patterns.globs
    )


# ---------------------------------------------------------------------------
# 変えた file と選び
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ChangeSet:
    base: str
    files: tuple[str, ...]
```

### 記録の封(seat-home の model/architecture.hy・3682f28c8 の版)
```hy
(defarchitecture seat-home-model
  :root "."
  :layers [(layer sealed
             :summary "hash で封をした記録の file — 中身を evidence/SHA256SUMS.txt が控えるので書き換えない"
             :knows "封の表(evidence/SHA256SUMS.txt)"
             :does-not-know "doeff の Program・effect・handler"
             :files ["chain.py"]
             :exempt [(rule DOEFF007 "中身を evidence/SHA256SUMS.txt が控える記録の file で、書き換えると封が壊れる")])])
```
