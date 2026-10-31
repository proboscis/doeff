# ruff: noqa: E402, I001  # 本文の枠と同じ順に、importを枠ごとに置く。
"""記事「AIに要る時だけ規則を見せるhook」の完全な例。本文のPythonの枠4つを、この順のまま1つのファイルにした。

checks/hooks-check.pyが、本文の枠を上から順に走らせてtest関数を呼ぶ。このファイルは同じ枠を単独で走らせる。
本文の最初の枠との違いは、標準入力を読む条件に引数`--hook`を足した1行だけ。引数なしなら試験を順に呼ぶ。

    python rule_hook.py                       # 3つの試験を走らせる。
    echo '<出来事のJSON>' | python rule_hook.py --hook   # Claude Codeのhookと同じ入出力で1回答える。
"""

import json  # hookに届く出来事のJSONを読み、返す答えをJSONで書く。
import re  # 命令の文に、規則の綴りが当たるかを見る。
import sys  # 標準入力から出来事を読む。

RULES = [  # 規則は「いつ当てるか」と「何を見せるか」の組にする。
    {"id": "config-values-are-hidden", "tool": "Bash",  # 設定ファイルの値を出さない規則。
     "when": r"\b(?:cat|head|tail|diff)\b[^\n]*settings\.json",  # 設定ファイルを丸ごと出す命令。
     "advice": "設定ファイルは丸ごと出さず、欄の名だけを出してください。"},  # 当たった時に見せる文。
    {"id": "python-runs-through-uv", "tool": "Bash",  # OSに入っているpythonを使わない規則。
     "when": r"(?:^|[;&|]\s*)python3?\b",  # 命令の頭で、pythonを直に呼ぶ形。
     "advice": "OSに入っているpythonではなく、uv run pythonを使ってください。"},  # 当たった時に見せる文。
]


def hook(event):  # 道具を使う直前の出来事1つから、見せる助言を選ぶ。
    command = event["tool_input"].get("command", "")  # Bashなら、これから走る命令の文。
    hits = [rule for rule in RULES  # 道具の名と綴りの両方が合う規則だけを残す。
            if rule["tool"] == event["tool_name"] and re.search(rule["when"], command)]  # 道具の名と綴りを見る。
    if not hits:  # 当たる規則が無ければ、何も足さない。
        return None  # 何も出力しないhookは、AIの文脈を変えない。
    text = "\n".join(f"{rule['advice']}(規則 {rule['id']})" for rule in hits)  # 1規則1行。
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",  # 道具の直前の出来事への答え。
                                   "additionalContext": text}}  # AIの文脈に足す文。


def test_only_the_matching_rule_is_shown():  # 当たった規則の文だけが出る事を確かめる。
    event = {"tool_name": "Bash", "tool_input": {"command": "cat ~/.claude/settings.json"}}  # 設定を丸ごと出す命令。
    advice = hook(event)["hookSpecificOutput"]["additionalContext"]  # hookが足す文。
    assert advice == "設定ファイルは丸ごと出さず、欄の名だけを出してください。(規則 config-values-are-hidden)"  # 1行だけ。
    assert hook({"tool_name": "Bash", "tool_input": {"command": "ls"}}) is None  # 関係の無い命令には何も出さない。


if __name__ == "__main__" and "--hook" in sys.argv:  # hookとして呼ばれた時だけ、標準入出力を使う。
    answer = hook(json.load(sys.stdin))  # 標準入力の出来事から、助言を決める。
    if answer:  # 当たる規則があった時だけ書く。
        print(json.dumps(answer, ensure_ascii=False))  # 標準出力のJSONを、Claude Codeが読む。
    raise SystemExit(0)  # hookの時は、下の試験を走らせずに終わる。


EXAMPLES = {  # 規則ごとに、当たるべき命令と、当たってはいけない命令を持つ。
    "config-values-are-hidden": (  # 設定ファイルの規則の例。
        ["cat ~/.claude/settings.json", "diff a/settings.json b/settings.json"],  # 当たるべき命令。
        ["jq 'keys' ~/.claude/settings.json", "cat README.md"]),  # 当たってはいけない命令。
    "python-runs-through-uv": (  # pythonの規則の例。
        ["python3 -c 'print(1)'", "cd app && python main.py"],  # 当たるべき命令。
        ["uv run python main.py", "ls python_docs"]),  # 当たってはいけない命令。
}


def shown_ids(command):  # 命令1つに当たった規則のidを、助言の文から読む。
    answer = hook({"tool_name": "Bash", "tool_input": {"command": command}})  # hookに出来事を渡す。
    if answer is None:  # 何も当たらなければ、空の一覧。
        return []  # 当たった規則は無い。
    return re.findall(r"規則 ([\w-]+)", answer["hookSpecificOutput"]["additionalContext"])  # 行末のidを集める。


def test_every_rule_hits_its_examples():  # 規則の綴りを直すたびに、例で確かめる。
    for rule_id, (violating, complying) in EXAMPLES.items():  # 規則を1本ずつ見る。
        for command in violating:  # 当たるべき命令で、
            assert rule_id in shown_ids(command), command  # その規則が出る。
        for command in complying:  # 当たってはいけない命令で、
            assert rule_id not in shown_ids(command), command  # その規則は出ない。
    assert set(EXAMPLES) == {rule["id"] for rule in RULES}  # 例を持たない規則を残さない。


from dataclasses import dataclass  # 依頼を、変わらない値にする。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行を使う。


@dataclass(frozen=True)  # 一度出した問いを、途中で書き換えない。
class AskJudge(Effect):  # 「この文は、各条件に当たるか」を外側へ問う依頼。
    text: str  # 問いの対象の文(人が書いた依頼など)。
    questions: tuple  # 規則ごとに独立した、はい・いいえで答えられる1文の組。


JUDGED = [  # 綴りでは決まらず、判定器に問う規則。
    {"id": "discussion-is-not-a-go-signal",  # 議論の依頼で、実装に進まない規則。
     "question": "利用者は、実装ではなく設計の議論を求めている。",  # 判定器に問う1文。
     "advice": "議論の依頼です。例と比較で答え、実装には進まないでください。"},  # 当たった時に見せる文。
    {"id": "remember-goes-to-memory",  # 覚えてと頼まれた時の規則。
     "question": "利用者は、次の会話でも覚えておくよう頼んでいる。",  # 判定器に問う1文。
     "advice": "覚える依頼です。記憶のファイルに1件として書いてください。"},  # 当たった時に見せる文。
]


@do  # 判定器への問いをyieldする処理にする。
def advise_prompt(prompt):  # 依頼1つから、見せる助言を決める。
    questions = tuple(rule["question"] for rule in JUDGED)  # 規則ごとの問いを並べる。
    answers = yield AskJudge(prompt, questions)  # 問いを1つの依頼に束ね、確率の組を受け取る。
    return [f"{rule['advice']}(規則 {rule['id']})"  # 当たった規則の助言を1行ずつ。
            for rule, probability in zip(JUDGED, answers, strict=True)  # 規則と答えを順に組む。
            if probability is not None and probability >= 0.5]  # 答えがあり、当たる側だけを採る。


def fixed_judge(answers):  # 試験用の判定器。問いの文ごとに、決めた確率で答える。
    @handler  # この関数を、Programに取り付けられるハンドラにする。
    def judge(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, AskJudge):  # 判定器への問いだけを受け持つ。
            probabilities = tuple(answers.get(q) for q in effect.questions)  # 表に無い問いは答え無し。
            return (yield Resume(k, probabilities))  # 確率の組で、止まった処理を再開する。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。
    return judge  # 表を閉じ込めたハンドラを返す。


@handler  # 判定器が時間内に答えなかった時の代わりのハンドラ。
def silent_judge(effect, k):  # 答えを持たない判定器。
    if isinstance(effect, AskJudge):  # 判定器への問いを受ける。
        return (yield Resume(k, (None,) * len(effect.questions)))  # 全部を答え無しにして見送らせる。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側へ渡す。


def test_judged_rule_with_fixed_answers():  # 同じ処理を、判定器を替えて走らせる。
    prompt = "この設計を、A案とB案の例で比べてほしい"  # 議論を求める依頼。
    table = {JUDGED[0]["question"]: 0.92, JUDGED[1]["question"]: 0.03}  # 1つ目だけ当たる答え。
    judged = with_handlers([fixed_judge(table)], advise_prompt(prompt))  # 表で答える判定器を付ける。
    expected = ["議論の依頼です。例と比較で答え、実装には進まないでください。(規則 discussion-is-not-a-go-signal)"]  # 1行。
    assert run(judged) == expected  # 0.92の規則だけが出る。
    assert run(with_handlers([silent_judge], advise_prompt(prompt))) == []  # 答えが無ければ出さない。


if __name__ == "__main__":  # 引数なしで走らせた時は、試験を順に呼ぶ。
    test_only_the_matching_rule_is_shown()  # 最初の枠の試験。
    test_every_rule_hits_its_examples()  # 規則の例の試験。
    test_judged_rule_with_fixed_answers()  # 判定器を替える試験。
    print("OK: 3つの試験が通りました")  # 全部通った事を示す。
