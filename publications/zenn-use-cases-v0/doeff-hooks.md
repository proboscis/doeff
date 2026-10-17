---
title: "AIに要る時だけ規則を見せるhook — 決まり851本をCLAUDE.mdに書かない"
emoji: "📏"
type: "tech"
topics: ["claudecode", "python", "doeff", "ai", "llm"]
published: false
---

AIエージェントに守ってほしい決まりは、失敗のたびに増えます。私たちは、それをCLAUDE.mdに書き足していました。CLAUDE.mdは、Claude Codeが毎回の会話の頭に読み込む指示のファイルです。

- 2026年7月21日に7,808字だったCLAUDE.mdは、9月22日に39,821字になりました。
- 長くなるほど、その場に関係の無い決まりも毎回読ませることになります。
- 書いてあるのに守られないことも、繰り返し起きました。

全部を読ませると長すぎます。削ると、要る時に手元にありません。この記事は、この悩みをClaude Codeのhookで解いた開発の記録です。決まりを「いつ見せるか」の条件つきで持ち、当たった時だけAIに見せます。

この仕組みは、自作のAI開発の基盤agoraで動いています。agoraは、AIの会話を何十も並べて開発を進める仕組みです。全体の話は、連載の5本目「[AIの会話を最大50並べて、1人で開発する](doeff-fleet.md)」にあります。

## まず短いコード

Claude Codeのhookは、決まった出来事(イベント)の時に外の命令を呼ぶ仕組みです。道具(ツール)を使う直前には、出来事のJSONが標準入力に届きます。命令がJSONで`additionalContext`を返すと、その文がAIの文脈に足されます。次のhookは、規則を2本だけ持ちます。

```python
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


if __name__ == "__main__":  # Claude Codeから呼ばれた時だけ、標準入出力を使う。
    answer = hook(json.load(sys.stdin))  # 標準入力の出来事から、助言を決める。
    if answer:  # 当たる規則があった時だけ書く。
        print(json.dumps(answer, ensure_ascii=False))  # 標準出力のJSONを、Claude Codeが読む。
```

- 規則は、道具の名と正規表現と、見せる文の組です。
- 設定ファイルを丸ごと出す命令の時だけ、1行の助言を返します。
- 当たらなければ何も出さないので、AIの文脈は変わりません。

hookは、Claude Codeの設定ファイルで出来事に結びつけます。上のコードを`.claude/hooks/rule_hook.py`に置いた時の例です。

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{"type": "command", "command": "python3 .claude/hooks/rule_hook.py"}]
      }
    ]
  }
}
```

これで、AIが`cat ~/.claude/settings.json`を走らせる直前に、助言が1行だけ文脈に入ります。私たちの環境には、この形の規則が851本あります。

![決まりを、常に読む文と要る時だけ見せる文に分ける](/images/zenn-use-cases-v0/generated/hooks-concept.png)
*CLAUDE.mdは毎回読みます。851本の規則は、出来事ごとに絞り、当たった物だけを助言として足します。*

## 常に読む文と、要る時だけ見せる文を分ける

CLAUDE.mdに残すのは、どの場面でも要ることだけです。答え方、作業の場所、使える道具の一覧などです。残りの決まりは、1本ずつ「いつ見せるか」の条件つきの規則にしました。

規則は、Claude Codeの5つの出来事のどれかに結びつきます。2026年10月17日の規則の目録で数えた本数です。

| 出来事 | いつ起きるか | 規則の数 |
| --- | --- | --- |
| UserPromptSubmit | 利用者の入力が届いた時 | 47本 |
| PreToolUse | 道具を使う直前 | 597本 |
| PostToolUse | 道具が終わった後 | 66本 |
| PostToolUseFailure | 道具が失敗した後 | 46本 |
| Stop | 返事を送る直前 | 95本 |

道具の直前の規則が、全体の7割です。失敗の多くは、道具の使い方に出るからです。

例えば「設定ファイルを丸ごと出さない」は、事故の後に足した規則です。設定ファイルを2つ比べた時に、鍵の値が1つ会話の記録に残りました。助言の文には、事故の日と、代わりに打つ命令を書きます。値を伏せて欄の名だけを出す`jq 'keys'`などです。

同じ目録で数えると、助言の文は1本あたり平均300字、長くても400字です。851本を全部つなぐと255,942字になります。CLAUDE.mdに入れれば、今の17倍の長さです。

## 規則に、当たる例と当たらない例を持たせる

綴り(正規表現)の規則は、書いた日に正しくても、直すうちに壊れます。そこで規則ごとに、当たるべき例と当たってはいけない例を持たせます。

```python
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
```

- 当たるべき例に当たらない規則は、置いてあっても働きません。
- 当たってはいけない例に当たる規則は、関係の無い場面で邪魔をします。
- 規則を直すたびに、この試験が両方を確かめます。

私たちの規則の宣言も、同じ2種類の例を持ちます。それでも、綴りだけの規則には弱点が残ります。文字列の中身にも当たることです。

この記事を書く間にも、それが起きました。試験のために`echo`で渡した文字列に、本物のhookが当たりました。文字列に`cat ~/.claude/settings.json`が入っていたからです。規則のファイルを表示しただけで、出力の中の例の文に当たったこともあります。今は命令の文と出力の文を分けて当てています。ファイルを表示するだけの命令も、出力の規則からは外します。

## 綴りで決まらない規則は、判定器に問う

「利用者が議論を求めている時は、実装に進まない」という規則を考えます。これは綴りでは決まりません。同じ「比べて」でも、議論の時と作業の依頼の時があるからです。

851本のうち77本、約9%がこの形です。これらは判定器に問います。判定器は、文と問いを受け取り、当たる確率を返す小さなモデルです。

ここでdoeffを使います。doeffは、処理の中の「問う」「待つ」といった操作を、外側の部品で差し替えるPythonのライブラリです。判定器への問いを、外側への依頼にします。

```python
from dataclasses import dataclass  # 依頼を、変わらない値にする。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼・再開・委譲・実行を使う。


@dataclass(frozen=True)  # 一度出した問いを、途中で書き換えない。
class AskJudge(Effect):  # 「この文は、各条件に当たるか」を外側へ問う依頼。
    text: str  # 問いの対象の文(利用者の依頼など)。
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
def advise_prompt(prompt):  # 利用者の依頼1つから、見せる助言を決める。
    questions = tuple(rule["question"] for rule in JUDGED)  # 規則ごとの問いを並べる。
    answers = yield AskJudge(prompt, questions)  # 問いを1つの依頼に束ね、確率の組を受け取る。
    return [f"{rule['advice']}(規則 {rule['id']})"  # 当たった規則の助言を1行ずつ。
            for rule, probability in zip(JUDGED, answers, strict=True)  # 規則と答えを順に組む。
            if probability is not None and probability >= 0.5]  # 答えがあり、当たる側だけを採る。
```

- `@do`を付けた関数は、呼んでもすぐには動きません。実行する手順(Program)を返します。
- `yield AskJudge(...)`は、問いを外側へ渡し、答えが来るまで止まります。
- このような依頼をエフェクト(effect)と呼びます。
- `advise_prompt`は、判定器をどう呼ぶかを知りません。

依頼を受けて答えを決める部品を、ハンドラ(handler)と呼びます。試験では、表で答えるハンドラを付けます。

```python
def fixed_judge(answers):  # 試験用の判定器。問いの文ごとに、決めた確率で答える。
    @handler  # この関数を、Programに取り付けられるハンドラにする。
    @do  # ハンドラ自身もyieldで、再開や委譲を指示する。
    def judge(effect, k):  # effectは届いた依頼、kは止まった処理の続き。
        if isinstance(effect, AskJudge):  # 判定器への問いだけを受け持つ。
            probabilities = tuple(answers.get(q) for q in effect.questions)  # 表に無い問いは答え無し。
            return (yield Resume(k, probabilities))  # 確率の組で、止まった処理を再開する。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。
    return judge  # 表を閉じ込めたハンドラを返す。


@handler  # 判定器が時間内に答えなかった時の代わりのハンドラ。
@do  # 再開を、ほかのハンドラと同じ書き方で指示する。
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
```

- `isinstance`で、届いた依頼が判定器への問いかを見分けます。
- `Resume(k, ...)`は、止まっていた`advise_prompt`へ答えを渡して再開します。
- 担当外の依頼は、`Pass`で外側のハンドラへ渡します。
- `silent_judge`は、判定器が答えなかった時の動きを試すためのハンドラです。

本番では、同じ`AskJudge`を、判定用のモデルを呼ぶハンドラが受けます。`advise_prompt`は1字も変えません。

規則を選ぶ私たちの本物のhookも、doeffで書いています。判定器への問いは、doeff-system-oneの`Judge`というエフェクトです。試験では、決めた答えを返すハンドラが答えます。このhookの56本の試験は、モデルを一度も呼ばずに走ります。

判定器の使い方では、2つのことを学びました。

- 問いは規則ごとに独立させ、1回の依頼に束ねます。最初は候補を1つの選択問題にまとめていました。似た規則が並ぶと、片方の例が隣の規則に吸われました。2026年9月22日に同じ例文12件で比べると、取り違えは1件から0件に減りました。問いを1本足す費用は、例文1件あたり約65トークンでした。
- 判定器は3.5秒で諦めます。答えが無い時は、判定器に問う規則だけを見送ります。綴りの規則は、そのまま出します。上の`silent_judge`の試験が、この動きです。

## 同じ助言を繰り返さない、返事の前で止める

同じ規則は、1回の依頼の間に1度だけ見せます。hookは会話ごとに小さな記憶のファイルを持ち、見せた規則のidを覚えます。同じ注意を何度も読ませると、それ自体が文脈を埋めるからです。

返事を送る直前(Stop)の規則は、返事を止めて書き直させることもできます。止めるのは、1回の依頼で2回までです。止め続けて返事が出ない事態を避けるためです。壊す命令と、認証情報の漏れを止める規則だけは、この上限の外に置きます。

一部の規則では、止める時に別のモデルが当たった所を引用し、書き換えの案を添えます。2026年10月15日に実際の返事3本で測りました。1回7.4〜9.3秒で、費用はAPIの値段で0.007〜0.016ドルでした。

## 何が変わったか

![CLAUDE.mdの字数と規則の本数](/images/zenn-use-cases-v0/generated/hooks-flow.png)
*9月22日〜23日に決まりを規則へ移し、CLAUDE.mdは3分の1になりました。その後、規則は851本まで増えています。*

| 測った物 | 値 | 測った日と範囲 |
| --- | --- | --- |
| CLAUDE.mdの字数 | 39,821字 → 12,944字 | 2026年9月22日 → 23日 |
| 規則の本数 | 46本 → 851本 | 2026年9月22日 → 10月17日(規則の日付で数えた) |
| 移した後のCLAUDE.md | 15,333字 | 2026年10月15日 |
| hookが見た出来事 | 113,845件 | 10月16日3時〜17日2時(日本時間)・1つの実行環境 |
| 助言を出した出来事 | 11.9% | 同上 |
| 1回の依頼で見えた規則 | 中央値20本・6,246字 | 同上・道具を20回以上使った依頼599件 |
| 1度でも出た規則 | 428本(851本の半分) | 同上 |
| hookの所要時間 | 中央値48ミリ秒・90%点286ミリ秒 | 同上 |
| 判定器を呼んだ出来事 | 11.2%・中央値549ミリ秒 | 同上 |

- 移した2日で、毎回読む文は3分の1になりました。
- その後、規則は18倍に増えましたが、毎回読む文は約1.5万字のままです。
- 1回の依頼で見えるのは、規則の文の合計の2%ほどです。
- 約23時間で、半分の規則が一度は出ました。

決まりを足すことに、ためらいが無くなりました。事故があった日に規則を1本足し、例を書いて試験に通せば済みます。CLAUDE.mdを読む全部の会話に、毎回の費用を払わせずに済みます。

分からないこともあります。助言の後にAIが従ったかは、12本の規則でしか数えていません。守られる割合が上がったかは、まだ言えません。

## まとめ

- CLAUDE.mdには、どの場面でも要ることだけを残します。
- 残りの決まりは「いつ見せるか」の条件つきの規則にし、hookで当たった時だけ見せます。
- 規則には当たる例と当たらない例を持たせ、試験で確かめます。
- 綴りで決まらない規則は判定器に問います。問いをdoeffのエフェクトにすると、試験では表で、本番ではモデルが答えます。
- 移して25日で規則は851本に増えましたが、毎回読む文は約1.5万字のままです。

次の9本目は「[async/awaitがあるのに、なぜyieldか](doeff-coroutines.md)」です。この記事の`AskJudge`のように、止まった所へ答える役を外側に置く理由を扱います。

[doeffとは？：メイン記事へ戻る](doeff-main.md)

## 参考

この記事のコードは、2026年10月17日に、PyPIのdoeff 0.6.0で確かめました。Pythonは3.12と3.14で試しました。GitHubにあるdoeffのmain(commit `9adc4f8`)でも、同じく動きました。Pythonの枠は、上から順に前の枠の続きとして動きます。`doeff`のパッケージだけが要ります。

試す時は、[完全な例](examples/rule_hook.py)を`rule_hook.py`として保存し、次のコマンドで実行します。

```bash
uv run --with "doeff==0.6.0" python rule_hook.py  # PyPIのdoeff 0.6.0を入れて、完全な例の3つの試験を走らせる。
```

- [Claude Codeのhooksの説明(公式)](https://code.claude.com/docs/en/hooks)
- [doeff本体(この記事で確かめた版)](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c)
- [判定器への問いのエフェクト(doeff-system-one)](https://github.com/proboscis/doeff/tree/9adc4f8e657a552f5253d8a7b422363789c2608c/packages/doeff-system-one)
- 前の回:[バックテストと本番で同じ処理を動かす](doeff-backtest.md)
- 次の回:[async/awaitがあるのに、なぜyieldか](doeff-coroutines.md)
