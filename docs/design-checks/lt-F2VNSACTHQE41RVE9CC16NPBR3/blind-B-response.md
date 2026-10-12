# 失敗例: 収集 0 件(rc 5)の結末を共有の一巡に相乗りさせる

見つかった失敗例は 1 つです。一巡を共有しているテストに新しい模擬の結末を相乗りさせると、提示の 3 本と ruff は通るのに、「赤だけの日に rc 0 になる退行」が見逃されるようになります。私は何も実行していません。下の「通る」「赤になる」はすべて、コードを読んだうえでの推測です。

## 1. 機能要求と差分

**要求(現実的なもの)**: pytest が収集 0 件(rc 5)で終わった package の session を、テストの赤と分けて `test-packages collected nothing:` の行で名指す(段は赤のまま)。
- 動機: Makefile の find による前選び(`test_*.py`・`test_*.hy`)は、集め手の pattern の写しです。両者がずれると、集め手の無い `test_*.hy` しか持たない package が rc 5 で終わり(#2591 の形)、今は「テストの失敗」と記帳されます。
- 実装者は S-sim のとおり偽の runner に env を 1 つ足します。ただし新しいテストは自分の一巡を回さず、既存の module scope の fixture `packages_round` に相乗りさせます。理由は、fixture の docstring が「検ごとに回すと…2 回回す」と共有を勧めていること、R3(2 秒)と `make test-changed` の 60 秒の枠、そしてコードが少なく済むことです。

**差分の要点**

Makefile(`/tmp/doeff-blind-c854bccaf/Makefile` の 208・218・225・227 行):
```diff
-	@failed=""; \
+	@failed=""; empty=""; \
-			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$(basename $$dir)"; \
+			$(PACKAGE_UV_RUN) pytest "$${dir}tests" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \
+			if [ $$rc -eq 5 ]; then empty="$$empty $$(basename $$dir)"; elif [ $$rc -ne 0 ]; then failed="$$failed $$(basename $$dir)"; fi; \
-		$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS) || failed="$$failed $$root"; \
+		$(PACKAGE_UV_RUN) pytest "$$root" -m "not e2e" $(PACKAGE_PYTEST_DURATIONS); rc=$$?; \
+		if [ $$rc -eq 5 ]; then empty="$$empty $$root"; elif [ $$rc -ne 0 ]; then failed="$$failed $$root"; fi; \
-	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; exit 1; fi
+	if [ -n "$$empty" ]; then echo ""; echo "test-packages collected nothing:$$empty"; fi; \
+	if [ -n "$$failed" ]; then echo ""; echo "test-packages failed:$$failed"; fi; \
+	[ -z "$$failed$$empty" ]
```

`/tmp/doeff-blind-c854bccaf/tests/test_daily_test_population.py`:
```diff
 # _FAKE_RUNNER(sh)
 fail=$(cd -- "$DOEFF_FAKE_RUNNER_FAIL" 2>/dev/null && pwd -P)
+empty=$(cd -- "$DOEFF_FAKE_RUNNER_EMPTY" 2>/dev/null && pwd -P)
 ...
     if [ -n "$fail" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$fail" ]; then rc=1; fi
+    if [ -n "$empty" ] && [ -d "$arg" ] && [ "$(cd -- "$arg" && pwd -P)" = "$empty" ]; then rc=5; fi

-def _run_test_packages(repo: Path, tmp: Path, fail: Path, *overrides: str) -> _MakeRound:
+def _run_test_packages(
+    repo: Path, tmp: Path, fail: Path, *overrides: str, empty: Path | None = None
+) -> _MakeRound:
 ...
                     EnvEntry(name="DOEFF_FAKE_RUNNER_FAIL", value=str(fail)),
+                    EnvEntry(name="DOEFF_FAKE_RUNNER_EMPTY", value=str(empty or tmp / "no-such-tests")),

 class _PackagesRound:
     expected: list[str]
     first: str
+    last: str
     made: _MakeRound

 def packages_round(...):
-    first = expected[0]
+    first, last = expected[0], expected[-1]
     made = _run_test_packages(
         REPO_ROOT, tmp_path_factory.mktemp("packages-round"),
         REPO_ROOT / "packages" / first / "tests",
+        empty=REPO_ROOT / "packages" / last / "tests",
     )
-    return _PackagesRound(expected=expected, first=first, made=made)
+    return _PackagesRound(expected=expected, first=first, last=last, made=made)

+def _named_empty_packages(output: str) -> list[str] | None:
+    summary = [line for line in output.splitlines() if line.startswith("test-packages collected nothing:")]
+    return summary[-1].split(":", 1)[1].split() if summary else None
+
+def test_make_test_packages_names_an_empty_session_apart_from_reds(packages_round: _PackagesRound) -> None:
+    """収集 0 件(rc 5)の session は赤と分けて名指し、段は 0 以外で終わる。"""
+    last, output = packages_round.last, packages_round.made.output
+    assert _named_empty_packages(output) == [last], f"… — R8\n{output[-2000:]}"
+    assert last not in (_named_failed_packages(output) or []), f"… — R8\n{output[-2000:]}"
+    assert packages_round.made.exit_code != 0, f"… — R8\n{output[-2000:]}"
```

検査の無効化・除外・抑制・cast・動的 import は使っていません。

## 2. 本来の持ち主と、侵す契約

- **状態(M-round)**: 共有の一巡の結末の組は「名前順で最初の package だけが赤」(P3・`_PackagesRound` の docstring)でした。差分の後は、別のテストの都合で rc 5 の package(`doeff-vm-core`)が加わります。共有の状態に、2 人目の持ち主がシナリオを書き込んだことになります。
- **判断(M-checks の visit の検)**: `assert packages_round.made.exit_code != 0` は「赤が 1 つ在れば rc≠0」(R8・赤を飲まない)を判定していました。差分の後は rc 5 の session だけでも rc≠0 になるため、この assert が何を証明するかを fixture(M-round)が決めることになります。テストの本文は 1 字も変わらないまま、判定の意味だけが変わります。
- **公開契約(S-sim の主張)**: 「新しい結末は M-runner と新しいテストが `_run_test_packages` で自分の一巡を回す形で足し、共有の一巡と既存のテストは変えない」に違反します。
- **R5 と R4**: 失われる性質は「赤だけで rc≠0」です。その結果、S-eff が「M-checks のどれかが赤にする」と主張する退行(赤を飲む)が見逃されます。visit・flags の docstring の「赤は最初の package だけ」も偽になります。

## 3. 各検査が拒否しない理由(すべて読みによる推測)

- **test_make_test_packages_is_green_when_every_package_is_green**
  - `empty` を渡さないので、env は存在しない dir を指し、runner の `empty` は空になります。
  - 全部の session が rc 0 なので要約の行は出ず、最後の `[ -z "" ]` が 0 を返し、make の rc は 0 です。
  - 引数の文字列は変わらないので、visited の比較も一致します。
- **test_make_test_packages_visits_every_package_after_a_red**
  - runner は rc に関わらず全部の呼びを記録するので、reached は期待と一致し、sim の根も訪ねています。
  - 要約の行の判定 `[ -z " doeff-adr doeff-vm-core" ]` が 1 を返すので、make の rc は 2(≠0)です。
  - `_named_failed_packages` は `test-packages failed:` で始まる行しか読まないので、結果は `["doeff-adr"]` で一致します。
- **test_make_test_packages_lists_every_slow_test**
  - `.agents/land-queue.toml` は変えていないので、部分文字列の判定は真のままです。
  - Makefile は全部の session に `$(PACKAGE_PYTEST_DURATIONS)` を渡し続けます。
- **ruff check**
  - 新しい import も抑制もありません。可変長引数の後の keyword 専用引数は正しい構文です。
  - E501・PLR0913・PLR2004 は ignore に入っています。
  - 新しい文字列の全角の括弧・ダッシュは、既存の行と同じ種類の文字です。結果は今の file と同じになるはずですが、ruff は走らせておらず版も未確認です。
- **強制方法の表の各行**
  - frozen `_MakeRound`: 慣習であり、触れていません。
  - `scope="module"`: 一巡は module で 1 回のままです。むしろ相乗りを後押ししています。
  - sh の runner: `cd`・`pwd -P`・`[`・`printf` だけを使っています。
  - 実行経路: 同じ file で、新しいテストも `-k make_test_packages` に当たります。
- **拒否されない根本の理由**: 共有の一巡の結末の組(赤以外に 0 でない session が無いこと)を確かめる検査が、どこにもありません。

## 4. 確かめる手順(自分の worktree で。私は走らせていません)

1. c854bccaf に差分を当て、`uv run --no-sync pytest -q tests/test_daily_test_population.py -k make_test_packages` と `uv run --no-sync ruff check tests/test_daily_test_population.py` を走らせる。期待は 4 passed と ruff の指摘 0 件。
2. 変異を入れる。Makefile の最終行を `[ -z "$$empty" ]` に変える(赤だけの日に rc 0 になる)。手順 1 をもう一度走らせると、期待は 4 passed のまま。
   - 補足: 実装者が `exit 1` を 2 つ目の `if` だけに移す書き損じをしても同じ結果になり、機能の commit そのものに入り得ます。
3. 違反が表に出ることを、同じ helper の使い捨てのテストで確かめる。
   ```python
   def test_probe_red_alone(tmp_path):
       (tmp_path / "packages/a/tests").mkdir(parents=True)
       (tmp_path / "packages/a/tests/test_x.py").write_text("def test_x():\n    pass\n")
       made = _run_test_packages(tmp_path, tmp_path, tmp_path / "packages/a/tests")
       assert _named_failed_packages(made.output) == ["a"]
       assert made.exit_code != 0
   ```
   期待は、最後の assert で落ちること(赤を名指したのに rc 0)。
4. 対照として、c854bccaf のままで 227 行の `exit 1` だけを消す。期待は、visit の検が「rc が 0 — R8」で赤になること。共有の一巡を変えていなければ捕まる、という比較です。

## 5. 実測と推測の区別、不足

**実測(読みと、読み取り専用の木への find)**
- 入力の md を全部、テストの file を全部、Makefile の 1〜80 行と 206〜227 行、pyproject の ruff の設定を読みました。
- Makefile の find の条件を、そのまま shell で木に当てました。tests/ を持つ 36 package のうち `doeff-indexer` と `doeff-linter` が飛ばされ、34 が走ります。
- Python の `sorted` では、最初が `doeff-adr`、最後が `doeff-vm-core` です。

**推測**
- `_expected_packages` の結果が、この 34 と同じであること(関数そのものは走らせていません)。
- 手順 1〜4 の合否、make の rc が 2 になること、ruff の結果のすべて。
- runner の subshell が 1 session あたり最大 2 つ増えることが、R3(2 秒)に与える影響は測っていません。

**不足**
- 日次の道具が段の rc をどう使うかは読んでいません(rc を公開の口とする表に依っています)。
- `subprocess_handler` の `exit_code` の中身は読んでいません。
- 提示されていない検査(同じ file の他のテスト・ADR の deftest)は評価していません。

**本例とは別に気づいた 2 点(未検証)**
- green の検は引数を文字列で比べています(`arg.endswith(("tests","sim"))` と相対 path の一覧)。そのため、S-eff の「cd してから相対で渡す形も同じと数える」は、このテストには成り立ちません(偽の赤になる側の問題です)。
- flags の検は、段の形を `make test-packages PACKAGE_UV_RUN=` の部分文字列でしか見ていません。段の側で公開の変数を上書きされても(`PACKAGE_EXTRA_TEST_ROOTS=` など)、一巡は Makefile の既定で回るので気づけません。これは、検査を通る別の失敗例の候補です。

WAIT: work — 呼び出し元の次の依頼
