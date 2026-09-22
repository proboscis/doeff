import hashlib, json, pathlib, sys
D = pathlib.Path.home() / ".worktrees/doeff-wt-adr012-design/docs/design/adr012-pin-reaim/sendback-1"
def h(name):
    return hashlib.sha256((D / name).read_bytes()).hexdigest()
def ref(name):
    return {"path": name, "sha256": h(name)}

BASE = "09309e77ecd24d3f555b012000e910782b734fa0"
MODULES = [
  {"id": "law",
   "responsibility": "agentd / sessionhost について何が真であるべきかを宣言する",
   "owns": "ADR の rule / law の :statement と :counterexamples",
   "hides": "真の実現方法とソースの綴り",
   "interface": "law の名と statement(針が :enforcement で名指す)",
   "effects": "なし — 宣言のみ(副作用は持たない)",
   "lifetime": "冊の版。改訂は法・反例・実装を 1 つの commit で出す",
   "invariants": "statement は実装の綴りに言及しない。この便では 1 行も変えていない"},
  {"id": "needle",
   "responsibility": "法が現在のコードで守られているかを機械で撃つ",
   "owns": "冊の deftest とその中の走査の口・名簿(MAIL-TURN-TEXT-CONSUMERS 等)",
   "hides": "実装の内部の綴り(局所変数の名・行の折れ方・引数の位置)",
   "interface": "pytest から呼ばれる deftest と、冊の中の部品(form-use-head / carriers-of / bound-calls-of 等)",
   "effects": "file の読み取りのみ(hy.read-many と Path.read-text)。書き込み・通信・並行実行は持たない。file が無い/解析できない時は例外 = 赤",
   "lifetime": "冊の版。針の走査の口は冊の private で、他の 26 冊は 1 か所も参照していない",
   "invariants": "禁止の綴りを列挙しない(開いた集合)。許されている読み手を 1 か所の名簿で宣言し(閉じた集合)、運ぶ名の閉包の実測と突き合わせる"},
  {"id": "impl",
   "responsibility": "手番へ渡す郵便の文を組み(judgment)、運ぶ(judgment の畳み・agentd の注入)",
   "owns": "mail-turn-text-of(組む座 1 点)・message-bodies-of と deliver-interrupts-of(運ぶ)",
   "hides": "見出しの綴り・欄の順・欄の在否の判断(運ぶ側は 1 つも知らない)",
   "interface": "mail-turn-text-of [message-id spec body [status None]] -> str と SessionInterject / launch の prompt",
   "effects": "judgment は純粋(defk の退化形・bind ゼロ)。agentd は型つきの要求(SessionInterject 等)を返すだけで実 I/O は handlers.py",
   "lifetime": "常駐の拍ごと。session の生死は handlers と器の側",
   "invariants": "文を組む座は 1 点。運ぶ側は組み替えない(積んだ先を歩いて書き足すのも組み替え)。この便では 1 byte も変えていない"},
]

def sc(sid, axis, change, claim, expected, actual, changed, unchanged, reason, revision):
    return {"id": sid, "axis": axis, "applicability": "applicable", "change": change,
            "claim": claim, "expected_scope": expected, "actual_scope": actual,
            "changed_modules": changed, "unchanged_modules": unchanged,
            "reason": reason, "revision": revision}

SCENARIOS = [
  sc("sc-effects", "effects",
     "注入の路だけ「割り込みです」の 1 行を足したくなる / 相乗りの畳みに通番の見出しを足したくなる(運ぶ側で文を組み替える誘惑)",
     "組み替えは綴りに依らず・束ねた名を経由しなくても赤になり、正当な拡張は judgment の 1 点(引数を足す)か冊の名簿 1 行で済む",
     "実装 1 file(judgment の組む座)+ 冊の名簿 0〜1 行", "同じ(addarg = 緑・組み替え 13 形 = 赤・盲検 B の numbered は 1 版で素通り → 2 版で赤)",
     ["impl", "needle"], ["law"],
     "意図した公開契約の拡張(judgment の欄を足す)と、知識の漏洩(運ぶ側が見出しの綴りを持つ)を針が区別した。1 版は積んだ先の組み替えを区別できず、2 版で運ぶ名の閉包を足した",
     "あり — 盲検 B で主張 1 が反証され、carriers-of を足した(claims-outcome.md)"),
  sc("sc-storage", "storage",
     "本文を記録の service に置いた郵便が増え、fetched から引く欄が育つ",
     "文を組む座は変わらず、運ぶ側の読みの集合も変わらない",
     "実装 1 file(message-bodies-of の本文の引き)", "同じ(針は本文の出所を 1 つも読まない)",
     ["impl"], ["law", "needle"],
     "針が読むのは呼びを包む form と運ぶ名の読みだけで、本文の出所は読まない",
     "なし"),
  sc("sc-concurrency", "concurrency",
     "1 拍で複数の job の注入を並行に配る(腕が job ごとに分かれる)",
     "針は form の構造を読むので、腕が増えても『呼びは 1 つ・読み手は名簿』の形で撃てる",
     "実装 1 file + 名簿 0 行", "同じ(針は並行実行の性質を持たない — file の読み取りのみ)",
     ["impl"], ["law", "needle"],
     "針の走査は純粋な読みで、拍の並行性に依存しない",
     "なし"),
  sc("sc-distribution", "distribution",
     "機体が増え、agentd が別の器(codex app-server)で文の形を変える / 器の注入の口の上限を運ぶ側で見たくなる(盲検 A)",
     "器ごとの分岐は judgment の 1 点に入り、運ぶ側の組み替えは赤。組み替えない読み(長さを測る)も赤になるが名簿 1 行で緑",
     "実装 1 file(judgment)+ 冊 0〜1 行", "同じ(wrap / consume は赤・盲検 A の lencheck は赤 → lencheckdeclared は緑)",
     ["impl", "needle"], ["law"],
     "運ぶ側で器ごとに組み替える形が赤になるため、分岐は組む座へ寄せるしかない。過剰に赤くする範囲は註に名指しで書き、緩めない",
     "註に『過剰に赤くする範囲』を足した"),
  sc("sc-hardware", "hardware",
     "会話の器が音声で、文を平易化・短縮したくなる",
     "平易化は組む座の中(または宣言した読み手)に入り、運ぶ側の組み替えは赤",
     "実装 1 file + 冊の名簿 0〜1 行", "同じ(newreader = 赤 / newreaderdeclared = 緑 の対で確認)",
     ["impl", "needle"], ["law"],
     "読み手を足す正当な拡張は名簿 1 行の宣言で通り、宣言しない形は赤(何を宣言せよかが文言で読める)",
     "なし"),
  sc("sc-simulation", "simulation",
     "決定的な再生(同じ郵便で同じ文)を要求する",
     "文が 1 点で組まれる限り再生は決定的。第 2 の合成点が入ると再生が崩れる(盲検 B の検体: 郵便 2 通で同じ郵便に 2 つの綴り)",
     "実装 0 file(既に満たされている)", "同じ(第 2 の合成点を作る 13 形が赤)",
     [], ["law", "needle", "impl"],
     "針が第 2 の合成点を綴りに依らず・経路に依らず赤にするので、再生の決定性が構造で守られる",
     "なし"),
]

MUT = {r["case"]: r for r in json.loads((D / "mutations.json").read_text())}
ALL_SC = [s["id"] for s in SCENARIOS]

def chk(cid, scs, control, expectation, observation, ev="evidence.md", status="passed"):
    return {"id": cid, "scenarios": scs, "control": control, "expectation": expectation,
            "observation": observation, "evidence": ref(ev), "status": status}

CHECKS = [
  chk("pos-focus", ALL_SC, "positive",
      f"試作(2 版)を基準 {BASE[:8]} に当てて、焦点の 1 本(-k first_turn_carries・下線)が緑",
      f"1 passed, 58 deselected in {MUT['base']['seconds']}s(冊 1 file の全 59 本も緑 — evidence.md R6)"),
  chk("pos-scope", ALL_SC, "positive",
      "変更 file は冊 1 本・sessionhost の差分 0・law の :statement の差分 0・冊の中に (law spelling-pins… の定義 0 件",
      "git status = 冊 1 本 / sessionhost の差分 空 / :statement の差分 0 行 / (law spelling-pins の定義 0 件(evidence.md R6)"),
]
RED_MAP = {
  "numbered": ("sc-effects", "sc-simulation"),
  "setv1": ("sc-effects",), "rebind": ("sc-effects",), "setv2": ("sc-effects",),
  "foldcall": ("sc-effects",), "consume": ("sc-effects", "sc-distribution"),
  "let": ("sc-effects",), "wrap": ("sc-effects", "sc-distribution"),
  "alias": ("sc-effects",), "overwrite": ("sc-simulation",), "dropuse": ("sc-simulation",),
  "rosterempty": ("sc-hardware",), "newreader": ("sc-hardware",),
  "lencheck": ("sc-distribution",), "renamecarrier": ("sc-effects",),
  "movecall": ("sc-simulation",), "dropusej": ("sc-simulation",), "badpath": ("sc-simulation",),
}
for case, scs in RED_MAP.items():
    r = MUT[case]
    CHECKS.append(chk(f"neg-{case}", list(scs), "negative",
                      f"{r['what']} を当てると赤(狙った構造の assert か、読めない時の例外が撃つ)",
                      f"赤({r['seconds']}s)・文言 = {r['message'][:220]}"))
for case, scs in {"renamebound": ("sc-effects",), "foldonly": ("sc-effects",),
                  "foldinter": ("sc-effects",), "newreaderdeclared": ("sc-hardware",),
                  "lencheckdeclared": ("sc-distribution",), "renamecarrierdeclared": ("sc-effects",),
                  "addarg": ("sc-effects", "sc-storage"),
                  "commentmention": ("sc-effects",), "stringmention": ("sc-effects",)}.items():
    r = MUT[case]
    CHECKS.append(chk(f"pos-{case}", list(scs), "positive",
                      f"{r['what']} は緑のまま(偽陽性を作らない / 過剰な赤の直し方が通る)",
                      f"緑({r['seconds']}s)"))
CHECKS.append(chk("neg-empty-population", ["sc-simulation"], "negative",
    "空の母集団(呼びが居ない・読みが居ない・file が読めない)が緑に倒れない(依頼者 §3 の 3 行)",
    "movecall / dropusej / badpath の 3 形とも赤(mutations.json)。0 件を緑と読む路は無い"))
CHECKS.append(chk("neg-wrongreason", ["sc-storage"], "negative",
    "狙った違反ではない赤(不正な変更)を、検出の成功に数えない",
    "1 版の addarg の最初の形(型の検査を足さない)は 5.7s で collection error(defk の既存の不変条件)。"
    "これを検出に数えず、型の検査も足した正しい形で撃ち直して緑"))

BLIND = json.loads(pathlib.Path("/tmp/sb-blind.json").read_text())
for b in BLIND:
    CHECKS.append(chk(b["id"], b["scenarios"], b["control"], b["expectation"], b["observation"], b["evidence"]))

report = {
  "version": 1,
  "task_id": "lt-KR8T10F3PWDB5M2DJWDJTWWDDW",
  "base_revision": BASE,
  "author": {"conversation_id": "c-XYP9ZM3W0GG8GPB4J60GK2AF49", "model": "claude-opus-5", "effort": "既定(起動口が受け付けないため未確認)",
             "note": "1 版・盲検の起動・反例 17 形 = claude-opus-5 の手番。2 版の設計と試作・反例の行列の起動 = claude-fable-5-1 の手番。行列の完走(28 形)・実行記録・この報告・実装の起票 = claude-opus-5 の手番。機体はどの手番も会社 Mac CA-20038667"},
  "requirements": (
    "差し戻し lt-YMAW77605Z9EXP3PYNBTK6JQFZ の 1 点。制約 = 『註と報告が主張する範囲 = 針が現に赤くする範囲』。"
    "受入 1〜6 は依頼者の機体で成立済み。受入 7(盲検 B の穴を塞いだ)だけが成り立たず、着地した針は "
    "2 つの正規表現で 1 行の字面を読むため 1 行の setv しか捕まえなかった(6 形が素通り)。"
    "直し方は 2 案(構造にする / 主張を縮める)のどちらでも受けると依頼者が明示し、推奨は構造にする案。"
    "追補 lt-XX7TCWJ2D55AAY6AGAQ5RRHJ0R: 向きは allowlist への反転で合意・hy_003 の先例は文字列を読む先例(file の軸は自前)・"
    "反例に §3 の 3 行(空の母集団)を足す・註に過剰に赤くする範囲も書き緩めない・範囲を広げない。"
    "この便の範囲 = 設計と最小実験まで(実装・着地は別便の実装段)。"),
  "design": ref("design.md"),
  "modules": MODULES,
  "scenarios": SCENARIOS,
  "checks": CHECKS,
  "unresolved": [],
}
(D / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
print("checks:", len(CHECKS), "unresolved:", report["unresolved"])
