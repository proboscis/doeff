import json, pathlib, subprocess, sys, time
W = pathlib.Path.home() / ".worktrees/doeff-wt-adr012-sendback"
A = W / "packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy"
BOOK = W / "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy"
PRISTINE = {A: A.read_text(encoding="utf-8"), BOOK: BOOK.read_text(encoding="utf-8")}

CALL = '            (<- text str (mail-turn-text-of message-id message.spec body message.status))\n'
INTER = ('                (SessionInterject :session-id job.session-id :text text :ref message-id\n'
         '                                  :attachments carried))\n')
ROSTER_AGENTD = '       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"}})'

def after_call(extra):
    return [(A, CALL, CALL + extra)]

CASES = [
    # (名, 期待, 差分の列, 何の形か)
    ("base", "green", [], "素の本線(試作を当てただけ)"),
    # --- 依頼者の差し戻しが名指した 3 形 + 元の 1 行 setv
    ("setv1", "red", after_call('            (setv text (+ "[urgent] " text))\n'),
     "1 行の setv(依頼者の盲検 B・旧い針が捕まえた唯一の形)"),
    ("rebind", "red", after_call('            (<- text str (pure (+ "[urgent] " text)))\n'),
     "<- で束ね直す(依頼者の指摘 1 — 旧い針は素通り)"),
    ("setv2", "red", after_call('            (setv text\n                  (+ "[urgent] " text))\n'),
     "setv を 2 行に折る(依頼者の指摘 2 — 旧い針は素通り)"),
    ("foldcall", "red", [(A, CALL,
       '            (<- text str\n                (mail-turn-text-of message-id message.spec body message.status))\n'
       '            (setv text (+ "[urgent] " text))\n')],
     "呼びの行を折る + 1 行の setv(依頼者の指摘 3 — 旧い針は bound が立たず黙る)"),
    # --- 当席が足した 3 形(差し戻しの返事で報せたもの)
    ("consume", "red", [(A, ':text text :ref message-id', ':text (+ "[urgent] " text) :ref message-id')],
     "送り先の引数でその場で組む"),
    ("let", "red", [(A, INTER,
       '                (let [text (+ "[urgent] " text)]\n'
       '                  (SessionInterject :session-id job.session-id :text text :ref message-id\n'
       '                                    :attachments carried)))\n')],
     "let で覆って組み替える"),
    ("wrap", "red", [(A, '(<- text str (mail-turn-text-of message-id message.spec body message.status))',
       '(<- text str (with-urgency (mail-turn-text-of message-id message.spec body message.status)))')],
     "呼びを別の form で包む(親が <- でなくなる)"),
    # --- 名簿の側
    ("alias", "red", after_call('            (setv other text)\n') +
       [(A, ':text text :ref message-id', ':text other :ref message-id')],
     "別の名へ写してから渡す"),
    ("dropuse", "red", [(A, ':text text :ref message-id', ':text body :ref message-id')],
     "名簿の読み手が読まなくなる(束ねた文が捨てられる)"),
    ("rosterempty", "red", [(BOOK, ROSTER_AGENTD, '       "agentd.hy" {}})')],
     "名簿から読み手の項を落とす(検査が噛む証拠の対・赤の側)"),
    ("newreader", "red", after_call('            (<- (LogLine :text text))\n'),
     "読み手を足して名簿へ宣言しない"),
    # --- 正当な整理(緑であるべき)
    ("renamebound", "green", [(A, '(<- text str (mail-turn-text-of message-id message.spec body message.status))',
       '(<- turn-text str (mail-turn-text-of message-id message.spec body message.status))'),
       (A, ':text text :ref message-id', ':text turn-text :ref message-id')],
     "束ねた名を改名する"),
    ("foldonly", "red-not-expected" if False else "green", [(A, CALL,
       '            (<- text str\n                (mail-turn-text-of message-id message.spec body\n'
       '                                   message.status))\n')],
     "呼びを 3 行に折るだけ(何も足さない)"),
    ("foldinter", "green", [(A, INTER,
       '                (SessionInterject\n                  :session-id job.session-id\n'
       '                  :ref message-id\n                  :text text\n'
       '                  :attachments carried))\n')],
     "送り先を折り直して欄の順を替える"),
    ("newreaderdeclared", "green", after_call('            (<- (LogLine :text text))\n') +
       [(BOOK, ROSTER_AGENTD,
         '       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"\n'
         '                    "LogLine" "配達した文をそのまま log に残す(2026-09-22 の反例の対)"}})')],
     "読み手を足して名簿へ 1 行宣言する(検査が噛む証拠の対・緑の側)"),
]

def apply(diffs):
    for path, old, new in diffs:
        s = path.read_text(encoding="utf-8")
        assert s.count(old) == 1, f"anchor hit {s.count(old)}: {old[:70]!r}"
        path.write_text(s.replace(old, new), encoding="utf-8")

def restore():
    for path, text in PRISTINE.items():
        path.write_text(text, encoding="utf-8")

PP = pathlib.Path("/tmp/sb-pp.txt").read_text().strip()
out = []
for name, expect, diffs, what in CASES:
    restore()
    apply(diffs)
    t0 = time.time()
    r = subprocess.run(
        ["timeout", "110", "/Users/s22625/repos/doeff/.venv/bin/python", "-m", "pytest",
         "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy", "-k", "first_turn_carries",
         "--no-header", "-q"],
        cwd=W, capture_output=True, text=True,
        env={**__import__("os").environ, "PYTHONPATH": PP, "PYTHONUNBUFFERED": "1"})
    dt = round(time.time() - t0, 1)
    verdict = "green" if r.returncode == 0 else ("timeout" if r.returncode == 124 else "red")
    msg = ""
    for line in (r.stdout + r.stderr).splitlines():
        if "AssertionError" in line or "Error:" in line:
            msg = line.strip()[:300]
            break
    ok = "OK" if verdict == expect else "MISMATCH"
    row = {"case": name, "what": what, "expect": expect, "verdict": verdict, "ok": ok,
           "seconds": dt, "message": msg}
    out.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
restore()
pathlib.Path("/tmp/sb-mutations.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
bad = [r for r in out if r["ok"] != "OK"]
print(f"=== 反例 {len(out)} 形 / 食い違い {len(bad)}")
print("=== git status(後始末の確認):")
print(subprocess.run(["git", "status", "--porcelain"], cwd=W, capture_output=True, text=True).stdout)
