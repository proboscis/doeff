import json, os, pathlib, subprocess, sys, time
W = pathlib.Path(os.environ.get("SB_WT", str(pathlib.Path.home() / ".worktrees/doeff-wt-adr012-sendback")))
A = W / "packages/doeff-agents/src/doeff_agents/sessionhost/acp/agentd.hy"
J = W / "packages/doeff-agents/src/doeff_agents/sessionhost/acp/judgment.hy"
BOOK = W / "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy"
PRISTINE = {p: p.read_text(encoding="utf-8") for p in (A, J, BOOK)}

CALL = '            (<- text str (mail-turn-text-of message-id message.spec body message.status))\n'
INTER = ('                (SessionInterject :session-id job.session-id :text text :ref message-id\n'
         '                                  :attachments carried))\n')
ROSTER_J = '      {"judgment.hy" {"bodies.append" "1 手番目の畳みと温かい send が読む同じ bodies へ、順のまま積む"'
ROSTER_A = '       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"}})'
J_APPEND = '          (.append bodies text)\n'
J_INIT = '  (setv bodies [])\n  (setv attachments [])\n'
J_RET = '  #((tuple bodies) (tuple attachments) (tuple missing)))'
J_END = '        (.append missing input-id)))\n' + J_RET
NUMBERED = r'''        (.append missing input-id)))
  ;; 段 10 lane 10r 追補の続き（相乗り）: 1 手番に 2 通以上を畳む拍は、agent が「何通目/全何通」を
  ;; 読めるように見出しに通番の欄を出す。全 M 通は畳み終わるまで分からないので、
  ;; 並べ終えた bodies へ 1 度だけ書き足す（郵便が 1 通の拍は今日と 1 文字も変わらない）。
  (setv total (len bodies))
  (when (> total 1)
    (setv numbered [])
    (for [[index folded] (enumerate bodies)]
      (setv parts (.split folded "\n" 1))
      (setv head (get parts 0))
      (setv rest (if (> (len parts) 1) (get parts 1) ""))
      (.append numbered (+ (cut head 0 -1) "・通=" (str (+ index 1)) "/" (str total) "]\n" rest)))
    (setv bodies numbered))
''' + J_RET
BADPATH_OLD = '#("agentd.hy" (/ ACP-DIR "agentd.hy"))]]'
SIG_OLD = '(defk mail-turn-text-of [message-id spec body [status None]]\n  {:pre [(: message-id str) (: spec dict) (: body str) (: status (| dict None))]'
SIG_NEW = '(defk mail-turn-text-of [message-id spec body [status None] [attachment-count 0]]\n  {:pre [(: message-id str) (: spec dict) (: body str) (: status (| dict None)) (: attachment-count int)]'

def after_call(extra):
    return [(A, CALL, CALL + extra)]

CASES = [
    ("base", "green", [], "素の本線(試作を当てただけ)"),
    # --- 作り替えの盲検 A・B(2026-09-22・claude-opus-5)
    ("numbered", "red", [(J, J_END, NUMBERED)],
     "盲検 B: 積んだ先の bodies を歩いて見出しに通番を書き足す(前の試作は緑で素通り)"),
    ("lencheck", "red", after_call('            (when (> (len text) 60000) (setv stopped True))\n'),
     "盲検 A: 注入の前に文の長さを測る(組み替えない読み — 過剰に赤くする範囲の実例)"),
    ("lencheckdeclared", "green", after_call('            (when (> (len text) 60000) (setv stopped True))\n') +
       [(BOOK, ROSTER_A,
         '       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"\n'
         '                    "len" "注入の前に文の長さを測る(組み替えない読み・盲検 A 2026-09-22)"}})')],
     "盲検 A + 名簿へ 1 行宣言(過剰な赤の直し方 = 名簿を人が直す)"),
    # --- 依頼者 §3 の 3 行(空の母集団が緑に倒れないこと)
    ("movecall", "red", [(A, CALL, "")],
     "呼び mail-turn-text-of を scan の外へ移す(呼びの行を消す)— §3-1"),
    ("dropusej", "red", [(J, J_APPEND, '          (.append bodies body)\n')],
     "名簿の送り先の片方(judgment の .append)が文を読まなくなる — §3-2"),
    ("badpath", "red", [(BOOK, BADPATH_OLD, '#("agentd.hy" (/ ACP-DIR "agentc.hy"))]]')],
     "検査器が読む path を存在しない名へ 1 字差し替える(例外でよい)— §3-3"),
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
    # --- 当席が足した 3 形
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
    ("overwrite", "red", after_call('            (setv text "fixed")\n'),
     "束ねた名を読まずに別の値で置き換える(文を捨てる)"),
    ("dropuse", "red", [(A, ':text text :ref message-id', ':text body :ref message-id')],
     "名簿の読み手が読まなくなる(束ねた文が捨てられる・agentd 側)"),
    ("rosterempty", "red", [(BOOK, ROSTER_A, '       "agentd.hy" {}})')],
     "名簿から読み手の項を落とす(検査が噛む証拠の対・赤の側)"),
    ("newreader", "red", after_call('            (<- (LogLine :text text))\n'),
     "読み手を足して名簿へ宣言しない"),
    ("renamecarrier", "red", [(J, J_INIT, '  (setv texts [])\n  (setv attachments [])\n'),
       (J, J_APPEND, '          (.append texts text)\n'),
       (J, J_RET, '  #((tuple texts) (tuple attachments) (tuple missing)))')],
     "積む先の名 bodies を改名する(過剰に赤くする範囲 — 名簿の鍵と食い違う)"),
    # --- 正当な整理(緑であるべき)
    ("renamecarrierdeclared", "green", [(J, J_INIT, '  (setv texts [])\n  (setv attachments [])\n'),
       (J, J_APPEND, '          (.append texts text)\n'),
       (J, J_RET, '  #((tuple texts) (tuple attachments) (tuple missing)))'),
       (BOOK, ROSTER_J, '      {"judgment.hy" {"texts.append" "1 手番目の畳みと温かい send が読む同じ bodies へ、順のまま積む"')],
     "積む先の改名 + 名簿の鍵を同じ便で直す(過剰な赤の直し方)"),
    ("renamebound", "green", [(A, '(<- text str (mail-turn-text-of message-id message.spec body message.status))',
       '(<- turn-text str (mail-turn-text-of message-id message.spec body message.status))'),
       (A, ':text text :ref message-id', ':text turn-text :ref message-id')],
     "束ねた名を改名する"),
    ("foldonly", "green", [(A, CALL,
       '            (<- text str\n                (mail-turn-text-of message-id message.spec body\n'
       '                                   message.status))\n')],
     "呼びを 3 行に折るだけ(何も足さない)"),
    ("foldinter", "green", [(A, INTER,
       '                (SessionInterject\n                  :session-id job.session-id\n'
       '                  :ref message-id\n                  :text text\n'
       '                  :attachments carried))\n')],
     "送り先を折り直して欄の順を替える"),
    ("newreaderdeclared", "green", after_call('            (<- (LogLine :text text))\n') +
       [(BOOK, ROSTER_A,
         '       "agentd.hy" {"SessionInterject" "走っている手番への注入の要求へ、組み替えずそのまま渡す"\n'
         '                    "LogLine" "配達した文をそのまま log に残す(2026-09-22 の反例の対)"}})')],
     "読み手を足して名簿へ 1 行宣言する(検査が噛む証拠の対・緑の側)"),
    # --- form にすると同時に死ぬ族(依頼者 §4)
    ("commentmention", "green", [(A, CALL,
       '            (<- text str (mail-turn-text-of message-id message.spec body message.status)) ;; ここで (setv text …) と書かない\n')],
     "註が禁止の綴りを説明として書く(reader は ;; を捨てる — 字面の針なら誤検出)"),
    ("stringmention", "green", after_call('            (<- (LogLine :text "agentd: no (setv text ...) after the compose call"))\n'),
     "文字列 literal の中に禁止の綴り(String と Symbol は別の節点 — 字面の針なら誤検出)"),
    ("addarg", "green", [(J, SIG_OLD, SIG_NEW),
       (A, '(mail-turn-text-of message-id message.spec body message.status))',
           '(mail-turn-text-of message-id message.spec body message.status (len carried)))')],
     "呼びに欄を足す(judgment の宣言と型の検査に既定つきの欄 + agentd が渡す)"),
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
ONLY = [s for s in os.environ.get("SB_ONLY", "").split(",") if s]
if ONLY:
    CASES = [c for c in CASES if c[0] in ONLY]
    assert len(CASES) == len(ONLY), f"未知の形: {set(ONLY) - {c[0] for c in CASES}}"
for name, expect, diffs, what in CASES:
    restore()
    try:
        apply(diffs)
    except AssertionError as e:
        row = {"case": name, "what": what, "expect": expect, "verdict": "anchor-fail", "ok": "ANCHOR-FAIL",
               "seconds": 0, "message": str(e)}
        out.append(row); print(json.dumps(row, ensure_ascii=False), flush=True); continue
    t0 = time.time()
    r = subprocess.run(
        ["timeout", "110", "/Users/s22625/repos/doeff/.venv/bin/python", "-m", "pytest",
         "docs/adr/defadr_doeff_agents_012_agentd_acp_arms.hy", "-k", "first_turn_carries",
         "--no-header", "-q"],
        cwd=W, capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": PP, "PYTHONUNBUFFERED": "1"})
    dt = round(time.time() - t0, 1)
    verdict = "green" if r.returncode == 0 else ("timeout" if r.returncode == 124 else "red")
    msg = ""
    for line in (r.stdout + r.stderr).splitlines():
        if "AssertionError" in line or "Error:" in line or "FileNotFoundError" in line:
            msg = line.strip()[:400]
            break
    ok = "OK" if verdict == expect else "MISMATCH"
    row = {"case": name, "what": what, "expect": expect, "verdict": verdict, "ok": ok,
           "seconds": dt, "message": msg}
    out.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)
restore()
pathlib.Path(os.environ.get("SB_OUT", "/tmp/sb2-mutations.json")).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
bad = [r for r in out if r["ok"] != "OK"]
print(f"=== 反例 {len(out)} 形 / 食い違い {len(bad)}")
print("=== git status(後始末の確認):")
print(subprocess.run(["git", "status", "--porcelain"], cwd=W, capture_output=True, text=True).stdout)
