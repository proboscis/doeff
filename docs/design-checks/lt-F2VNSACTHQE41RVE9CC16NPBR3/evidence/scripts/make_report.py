"""design-check.json を作る。使い方: uv run --no-project python make_report.py <記録の dir>"""

import hashlib
import json
import pathlib
import sys

D = pathlib.Path(sys.argv[1])


def ref(rel: str) -> dict[str, str]:
    return {"path": rel, "sha256": hashlib.sha256((D / rel).read_bytes()).hexdigest()}


def check(cid, scenarios, control, expectation, observation, evidence):
    return {"id": cid, "scenarios": scenarios, "control": control, "expectation": expectation,
            "observation": observation, "evidence": ref(evidence), "status": "passed"}


V = "evidence/validate/{}-on-e61d80c39.txt".format
S = "evidence/scenarios/{}-on-e61d80c39.txt".format
ALL = ["M-loop", "M-expect", "M-runner", "M-make", "M-round", "M-checks", "M-route"]


def unchanged(changed):
    return [m for m in ALL if m not in changed]


modules = [
    {"id": "M-loop", "responsibility": "Makefile の target test-packages(検の対象 — この変更では変えない)",
     "owns": "母集団の選び(packages/*/tests に検の file・PACKAGE_EXTRA_TEST_ROOTS)・赤の後も続ける・test-packages failed: の名指し・PACKAGE_PYTEST_DURATIONS",
     "hides": "session の起こし方(PACKAGE_UV_RUN に委ねる)・根の argv での綴り", "interface": "make の変数 PACKAGE_UV_RUN・PACKAGE_PYTEST_DURATIONS・PACKAGE_EXTRA_TEST_ROOTS・rc・要約の行",
     "effects": "session ごとの process の起動", "lifetime": "make の 1 回", "invariants": "1 package の赤で止まらず全部を訪ね、赤が在れば 0 以外で終わり名指す。全部緑なら rc 0"},
    {"id": "M-expect", "responsibility": "期待の導出(_expected_packages・_package_extra_test_roots・_DAILY_DURATIONS_FLAGS・_stage_run・_named_failed_packages)",
     "owns": "期待の package の集合を file system から(Makefile の実走からは取らない)・外の根は Makefile に聞く・秒の flag・要約の行の読み方",
     "hides": "なし(関数の層)", "interface": "関数", "effects": "file system を読む・make を 1 回(外の根を聞く)", "lifetime": "検ごと",
     "invariants": "期待を検の対象の実走から取らない"},
    {"id": "M-runner", "responsibility": "偽の runner _FAKE_RUNNER(sh)",
     "owns": "赤の dir に物理の path で当たる引数を受けた時だけ rc 1・返す rc と cwd と引数を 1 行(タブ区切り)で記録",
     "hides": "path の解き方(cd -- と pwd -P・CDPATH を外す)・記録の形", "interface": "env DOEFF_FAKE_RUNNER_RECORD・DOEFF_FAKE_RUNNER_FAIL・rc",
     "effects": "記録の file へ追記(1 呼び 1 回の printf)", "lifetime": "session の 1 回", "invariants": "Python を起こさない・POSIX の sh の builtin だけ"},
    {"id": "M-make", "responsibility": "_run_test_packages・_MakeRound・_RunnerCall",
     "owns": "本物の Makefile を -f で名指し -C <木> で回す・runner と記録を tmp に置く・記録を _RunnerCall(rc, cwd, argv) に読む・引数の dir を物理の path に解く口 dirs()・120 秒で打ち切る",
     "hides": "記録の形と path の解き方(検は _RunnerCall と dirs() だけを使う)", "interface": "_run_test_packages(repo, tmp, fail, *overrides) -> _MakeRound",
     "effects": "make を doeff の subprocess_handler で 1 回・tmp に file を書く", "lifetime": "呼びごと", "invariants": "検は記録の file を直に読まない"},
    {"id": "M-round", "responsibility": "fixture packages_round と _PackagesRound",
     "owns": "この repo の一巡を module で 1 回・模擬の結末は「期待の集合の名前順で最初の package だけが赤」の 1 つ",
     "hides": "なし", "interface": "_PackagesRound(expected, first, made)", "effects": "M-make を 1 回", "lifetime": "module(scope=module)",
     "invariants": "別の模擬の結末を相乗りさせない(訪ねの検が 0 以外の session を数えて守る)"},
    {"id": "M-checks", "responsibility": "検 3 本(visit・flags・green)の判定",
     "owns": "visit: 0 以外を返したのが最初の package の 1 つだけ・訪ねた集合 = 期待・外の根・rc≠0・名指し = [first] / flags: 全 session に秒の flag / green: 模型の木の 3 根を 1 度ずつ・rc 0・名指し無し",
     "hides": "なし", "interface": "pytest の検", "effects": "記録を読む(green は M-make を 1 回)", "lifetime": "検ごと",
     "invariants": "訪ねた根は dirs() で解いた dir で比べ、M-loop の綴りに依らない"},
    {"id": "M-route", "responsibility": "検の実行経路(root の pyproject の contract-tests prefix \"\"・日次の段 root)",
     "owns": "make test-changed がどの変更でもこの file を走らせる事・日次の段 root の pytest", "hides": "なし", "interface": "設定",
     "effects": "なし(設定)", "lifetime": "設定", "invariants": "この file は変更ごとと日次に走る"},
]

scenarios = [
    {"id": "S-hw", "axis": "hardware", "applicability": "applicable",
     "change": "Mac で走らせる(BSD の userland・/bin/sh は bash の POSIX mode・tmp は /var → /private/var の symlink の下・realpath -m 無し)",
     "claim": "機体に依るのは M-runner だけで、POSIX の sh の builtin だけを使う。比べるのは物理の path なので、symlink の下の tmp や repo でも同じ dir を同じと数える",
     "expected_scope": "なし", "actual_scope": "なし — dash と bash --posix で、symlink の下の cwd から綴り 3 通りで赤の dir を rc 1・他は rc 0・記録の cwd は物理の path。pwd(論理)の変種は赤を見落とす",
     "changed_modules": [], "unchanged_modules": ALL, "reason": "予測どおり(物理の path で比べる)",
     "revision": "不要。Mac の実機では走らせていない(design.md §11)"},
    {"id": "S-st", "axis": "storage", "applicability": "applicable",
     "change": "記録の形を変える(引数にタブや改行が入る・記録を file でなく fd へ出す)",
     "claim": "記録の形を知るのは書き手 M-runner と読み手 M-make だけで、検は _MakeRound.calls しか見ない",
     "expected_scope": "M-runner・M-make",
     "actual_scope": "区切りを \\037 に替える変更は M-runner と M-make だけで 3 passed。runner だけ替えると読み手が ValueError で 3 本とも赤。fd へ出す形は RunProcess に fd を渡す欄が無く依存先の拡張が要る(盲検 A)。e61d80c39 で rc の欄を足した時は _MakeRound.calls の型が tuple から _RunnerCall に変わった(公開の口の変更)",
     "changed_modules": ["M-runner", "M-make"], "unchanged_modules": unchanged(["M-runner", "M-make"]),
     "reason": "区切り(中の形)は予測どおり。欄を足すのは M-make の公開の口の意図した変更。fd は依存先の契約の拡張で、漏洩ではない",
     "revision": "e61d80c39 で calls を型 _RunnerCall にし、path の解きを dirs() の 1 か所にした(盲検 A の直しと共に)"},
    {"id": "S-eff", "axis": "effects", "applicability": "applicable",
     "change": "Makefile の loop が runner の起こし方を変える(package の dir へ cd して tests を渡す・引数を足す・母集団の loop を足す・要約の行の書き方を変える)",
     "claim": "M-loop の変更に M-runner・M-make・M-round を合わせる必要は無く、退行(package を飛ばす・赤で止める・赤を飲む・外の根を飛ばす・秒の flag を落とす・緑で rc≠0)は M-checks のどれかが赤にする。cd してから相対で渡す形も runner が自分の cwd から解くので同じ package と数える",
     "expected_scope": "M-loop だけ",
     "actual_scope": "c854bccaf: 根を tests/ で渡す形(盲検 A)で緑の検だけが偽の赤 — M-checks も変える必要が在った。e61d80c39: 同じ形が M-loop だけで 3 passed。退行 6 種はどれも M-checks のどれかが赤。要約の行の文言は M-expect が読む公開の口",
     "changed_modules": ["M-loop"], "unchanged_modules": unchanged(["M-loop"]),
     "reason": "盲検 A: 緑の検が M-loop の根の綴りを文字列で知っていた(隠すはずの知識の漏洩)。要約の行の文言を変えるのは公開の契約の変更で予測の対象外と分ける",
     "revision": "e61d80c39: 引数の解きを _RunnerCall.dirs() 1 つにし、緑の検は解いた dir の並びで比べる。G1(c854bccaf): 全部緑の rc 0 を模型の木で見る検を足した"},
    {"id": "S-conc", "axis": "concurrency", "applicability": "applicable",
     "change": "(a) root の pytest を xdist で並べ、3 本が別の worker に載る (b) Makefile の loop が package を並べて走らせ、runner の呼びが重なる",
     "claim": "(a) fixture は worker ごとに 1 回で正しさは変わらず、費用は共有の検を受けた worker の数(≤ 2)まで (b) runner は 1 呼び 1 行を printf 1 回で O_APPEND の file に足すので行が混ざらず、検は順に依らない",
     "expected_scope": "(a) なし (b) M-loop だけ",
     "actual_scope": "(a) -n 3 で 3 passed (b) 並べて pid ごとに wait する Makefile で 3 passed(M-loop だけ)。素の wait で赤を落とすと訪ねの検が rc 0 で赤",
     "changed_modules": ["M-loop"], "unchanged_modules": unchanged(["M-loop"]), "reason": "予測どおり", "revision": "不要 — 盲検は S-conc の失敗例を出さなかった"},
    {"id": "S-sim", "axis": "simulation", "applicability": "applicable",
     "change": "新しい検が別の模擬の結末を要る(rc 5「集めた検 0 件」の package・赤 2 つ・時間切れ)",
     "claim": "模擬の結末は M-runner が env から決める。新しい結末は M-runner(と env の名 1 つ)と、それを使う新しい検が _run_test_packages で自分の一巡を回す形で足し、共有の一巡と既存の検は変えない",
     "expected_scope": "M-runner・新しい検",
     "actual_scope": "c854bccaf: 新しい検が共有の一巡に相乗りする形(盲検 B)が検を通り、赤だけの日に rc 0 の書き損じを隠した。e61d80c39: 相乗りは訪ねの検が赤。主張どおり自分の模型の木の一巡で足す形は M-loop(機能の本体)・M-runner・M-make(env の配線と引数 1 つ)・M-checks(新しい検)で 4 passed、M-round と既存の検は不変。同じ機能の書き損じ(赤だけの日に rc 0)は訪ねの検が赤",
     "changed_modules": ["M-loop", "M-runner", "M-make", "M-checks"], "unchanged_modules": unchanged(["M-loop", "M-runner", "M-make", "M-checks"]),
     "reason": "M-make の変更は env の配線と口の引数 1 つで意図した拡張(盲検 A の指摘 — 予測が狭すぎた)。盲検 B: 共有の状態(模擬の結末)に 2 人目の書き手が入ると M-checks の判定の前提が黙って崩れた — 強制が無かった",
     "revision": "e61d80c39: runner が rc を記録し、訪ねの検が「0 以外を返したのは最初の package の 1 つだけ」を確かめる(外れたら『自分の一巡で回す』の文で赤)"},
    {"id": "X-dist", "axis": "distribution", "applicability": "excluded",
     "reason": "検は 1 機体の 1 つの pytest の process の中で自分の checkout(REPO_ROOT = __file__ から)を読み、機体をまたぐ状態を持たない。日次の段は自分の作業木で走る"},
]

checks = [
    check("c-before-G1-4bb2", ["S-eff"], "negative", "4bb2e090c(一巡の共有)で、緑の時に rc≠0 になる変異 and-summary が検を通る(設計者の自己点検 G1 の成立)",
          "2 passed — 見逃し", "evidence/logs/G1-and-summary-on-4bb2e090c.txt"),
    check("c-before-G1-10ad", ["S-eff"], "negative", "変更の前 10addc647 の検は同じ変異を赤にしていた(R5 の対照)",
          "test_make_test_packages_lists_every_slow_test が「緑で終わらなかった」で赤", "evidence/logs/G1-and-summary-on-10addc647.txt"),
    check("c-before-A", ["S-eff"], "negative", "c854bccaf で盲検 A の差分(根を tests/ で渡す)が緑の検だけを偽の赤にする(失敗例の成立)",
          "緑の検が「session を全部訪ねていない: ['extra/sim']」で赤・他の 2 本は緑", "evidence/blind-A/A-trailing-slash-on-c854bccaf.txt"),
    check("c-before-B", ["S-sim"], "negative", "c854bccaf で盲検 B の相乗りの差分と「赤だけの日に rc 0」の変異が検を通る(失敗例の成立)",
          "手順 1・2 とも 4 passed・使い捨ての検で赤を名指したのに rc 0・対照(exit 1 を消す)は訪ねの検が赤", "evidence/blind-B/B-repro-on-c854bccaf.txt"),
    check("c-eff-pos-file", ["S-eff"], "positive", "e61d80c39 で触った file の検が全部緑", "15 passed(load 90)・ruff は手を付けていない行の既存の PT018 の 1 件",
          "evidence/focused-tests-on-e61d80c39.txt"),
    check("c-eff-pos-none", ["S-eff"], "positive", "e61d80c39 で変異なしの make の検 3 本が緑", "3 passed", V("none")),
    check("c-eff-pos-A", ["S-eff"], "positive", "e61d80c39 で盲検 A の差分(根を tests/ で渡す)が M-loop だけで緑", "3 passed", V("trailing-slash")),
    check("c-eff-neg-skip", ["S-eff"], "negative", "package を 1 つ飛ばすと訪ねの検が赤", "test_make_test_packages_visits_every_package_after_a_red が赤", V("skip-one-package")),
    check("c-eff-neg-stop", ["S-eff"], "negative", "最初の赤で止めると訪ねの検が赤", "訪ねの検が赤", V("stop-at-first-red")),
    check("c-eff-neg-swallow", ["S-eff"], "negative", "赤を飲むと訪ねの検が赤", "訪ねの検が赤", V("swallow-red")),
    check("c-eff-neg-roots", ["S-eff"], "negative", "外の根を飛ばすと赤", "緑の検と訪ねの検が赤", V("skip-extra-roots")),
    check("c-eff-neg-flags", ["S-eff"], "negative", "秒の flag を 1 つ落とすと秒の flag の検が赤", "test_make_test_packages_lists_every_slow_test が赤", V("drop-durations-min")),
    check("c-eff-neg-green", ["S-eff"], "negative", "緑の時に rc≠0 になる変異(G1)で緑の検が赤", "test_make_test_packages_is_green_when_every_package_is_green が赤", V("and-summary")),
    check("c-hw-pos", ["S-hw"], "positive", "dash と bash --posix で、symlink の下の cwd から綴り 3 通りで赤の dir に rc 1・他は rc 0・記録の cwd は物理の path",
          "両方の shell で packages/a/tests・packages/a/tests/・<tmp>/link/packages/a/tests が rc=1、packages/b/tests が rc=0、record_cwd=<tmp>/real", S("hw")),
    check("c-hw-neg", ["S-hw"], "negative", "引数の解きを論理の pwd にした runner は symlink の下の cwd で赤の dir を見落とす(物理の path で比べる事が主張の芯)",
          "両方の shell で論理 pwd の変種は packages/a/tests に rc=0", S("hw")),
    check("c-st-pos", ["S-st"], "positive", "記録の区切りを書き手と読み手の両方で替えると、検を変えずに緑", "3 passed", S("st-sep-both")),
    check("c-st-neg", ["S-st"], "negative", "書き手だけ替えて読み手が追わないと、黙って緑にならず赤", "3 本とも ValueError で赤", S("st-sep-runner-only")),
    check("c-conc-pos-par", ["S-conc"], "positive", "package の loop を並べて pid ごとに wait して赤を集める Makefile で、検を変えずに緑", "3 passed", S("conc-parallel-ok")),
    check("c-conc-pos-xdist", ["S-conc"], "positive", "xdist の 3 worker で緑", "3 passed", S("conc-xdist")),
    check("c-conc-neg", ["S-conc"], "negative", "並べて素の wait で赤を落とすと訪ねの検が赤", "訪ねの検が「rc が 0」で赤", S("conc-parallel-lost")),
    check("c-sim-pos", ["S-sim"], "positive", "rc 5 の結末を自分の模型の木の一巡で見る検として足すと、共有の一巡と既存の検を変えずに緑", "4 passed", S("sim-own-round")),
    check("c-sim-neg-bug", ["S-sim"], "negative", "同じ機能の書き損じ(赤だけの日に rc 0)を訪ねの検が赤にする", "訪ねの検が「rc が 0」で赤", S("sim-own-round-bug")),
    check("c-sim-neg-B", ["S-sim"], "negative", "e61d80c39 で盲検 B の相乗りの差分を訪ねの検が拒む",
          "手順 1・2 とも訪ねの検が「この一巡で 0 以外を返した session が最初の package doeff-adr の 1 つでない」で赤・手順 4 は「rc が 0」で赤",
          "evidence/blind-B/B-repro-on-e61d80c39.txt"),
]

report = {
    "version": 1,
    "task_id": "lt-F2VNSACTHQE41RVE9CC16NPBR3",
    "base_revision": "10addc6473923419ef6acdb18dcb9044b87c7602",
    "author": {"conversation_id": "c-RVHCXR3QG19HDMN7RP6A2M3TM1", "model": "claude-opus-5-5", "effort": "unspecified"},
    "requirements": "card acp:kanban-issue:ki-e8f54a86b0a8: R1 doeff の root の検 2 本(test_make_test_packages_visits_every_package_after_a_red・test_make_test_packages_lists_every_slow_test)は本物の make test-packages を偽の runner で回す一巡を 1 回だけ回し、記録を module の fixture で共に読む。R2 偽の runner は sh(path は cd と pwd -P で解く)。R3 2 本の合計が load 65〜70 で 2 秒以下(直す前と並べる)。R4 package を 1 つ飛ばす・秒の flag を落とす変異が赤。R5(設計者が足した)一巡の共有の前に検が見ていた性質を 1 つも失わない。",
    "design": ref("design.md"),
    "modules": modules,
    "scenarios": scenarios,
    "checks": checks,
    "unresolved": [],
}
(D / "design-check.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("checks", len(checks), "design", report["design"]["sha256"][:12])
