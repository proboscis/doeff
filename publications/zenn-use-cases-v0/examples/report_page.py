"""報告のページの記事の完全な例: 中身のファイルからページを組み、描いた図だけを後から差し込む。

使い方(引数なしで走らせると、テストを走らせる):
  python report_page.py new <dir>             中身のファイルの例を作り、頼まれた時刻を記録する。
  python report_page.py build <content.md>    ページ(page.html)を組み、「図 n/m」を表示する。
  python report_page.py request <content.md>  図を描く作業役への依頼文を表示する。
  python report_page.py timing <content.md>   頼まれてから、文章が開くまでと図が揃うまでの秒を表示する。
"""

import contextlib  # テストで、記録の表示を受け取る。
import html  # 本文の字を、HTMLの字として安全にする。
import io  # 表示を文字列として集める。
import json  # 所要の記録を、1行1件のJSONで書く。
import re  # 中身のファイルから、図の指定の囲みを探す。
import sys  # 命令の引数を読む。
import tempfile  # テストの作業の場所を、一時のdirに作る。
from dataclasses import dataclass  # 依頼を、書き換えないデータ型で表す。
from datetime import datetime  # 記録の行に、今の時刻を書く。
from pathlib import Path  # 中身のファイル・図・ページの置き場を扱う。

from doeff import Effect, Pass, Resume, do, handler, run, with_handlers  # 依頼と実行。

FIGURE = re.compile(r"^~~~図 ([a-z0-9-]+)\n.*?^~~~\n", re.M | re.S)  # 「~~~図 <id>」から閉じの「~~~」までの囲み。
UNFILLED = "(ここに書く"  # 雛形のまま残った所の印。
SAMPLE = """\
# 検索の応答が遅くなった件

10月12日の夜の、検索の応答の報告です。決めてほしい事はありません。

## 結論

- 遅くなったのは、索引を作り直した後の30分だけでした。
- 作り直しの手順に、温める段を1つ足しました。

## 応答の秒

| 時刻 | 中央値 | 遅い1% |
| --- | --- | --- |
| 21時 | 0.2秒 | 0.9秒 |
| 22時 | 3.1秒 | 8.4秒 |
| 23時 | 0.2秒 | 1.0秒 |

~~~図 fig-1
題: 作り直しの前後の応答の秒
見せること: 作り直した直後の30分だけ、応答が遅いこと
使う数字: 21時 0.2秒・22時 3.1秒・23時 0.2秒
形: 横に時刻、縦に秒の折れ線
~~~

## 直した手順

~~~図 fig-2
題: 索引の作り直しの手順
見せること: 切り替えの前に、温める段を足したこと
使う数字: 作る → 温める → 切り替える
形: 左から右へ3つの箱と矢印
~~~
"""
SHELL = """\
<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>{title}</title>
<style>
body {{ max-width: 960px; margin: 2rem auto; padding: 0 1rem; font: 16px/1.7 sans-serif; color: #222; }}
table {{ border-collapse: collapse; }} th, td {{ border: 1px solid #ccc; padding: 4px 10px; }}
.pending {{ border: 1px dashed #999; padding: 2rem; color: #666; }} svg {{ max-width: 100%; height: auto; }}
</style></head><body>
{body}
</body></html>
"""
REQUEST = """\
中身のファイル: {content}
このファイルの図の指定(~~~図 <id> の囲み)ごとに、SVGを1つずつ描いてください。
- 材料は、この中身のファイルだけです。コード・issue・会話の記録を調べ直しません。
- 数字と文を足しません。図に載せるのは、図の指定と中身のファイルにある物だけです。
- 図は {figures}/<id>.svg に、viewBoxを付けた<svg>要素1つで保存します。
- 外のファイル・フォント・画像・スクリプトを読みません。
- 全部を保存したら、次の命令を1回実行します: {build}
- 最後の行が「図 n/n」なら終わりです。ページ全体のスクリーンショットは撮りません。
- 返事は、描いた図のidと、何を見せたかを1行ずつ書きます。
"""


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class DrawnFigure(Effect):  # 「この図の、描き終えたSVGが欲しい」という依頼。
    figure_id: str  # 図の指定のid。


@dataclass(frozen=True)  # 発行した依頼を書き換えない型にする。
class Record(Effect):  # 「所要の記録に1行を足したい」という依頼。
    event: str  # 出来事(requestedかopened)。
    drawn: int  # 入った図の数。
    total: int  # 図の指定の数。


def problems(content):  # 組む前に、中身のファイルの決まりを確かめる。
    checks = (  # 確かめる事と、当たった時の文。
        (UNFILLED in content, f"雛形の「{UNFILLED}」が残っています"),  # 書きかけを止める。
        (FIGURE.search(content) is None, "図の指定が1つもありません"),  # 流れや比べを、文だけで書いている。
    )
    opening = content.split("\n## ", 1)[0]  # 最初の見出しまでの冒頭。
    long = [sentence for sentence in re.findall(r"[^。\n]+。", opening) if len(sentence) > 60]  # 60字を越える文。
    return [text for hit, text in checks if hit] + [f"冒頭の文が{len(s)}字です: {s[:12]}…" for s in long]  # 空なら組んでよい。


@do  # 呼ぶと、ページを組む手順(Program)ができる。
def build(content):  # 中身のファイルから、ページの本文と図の数を作る。
    page, drawn, specs = content, 0, FIGURE.findall(content)  # 組む本文・入った図の数・図の指定。
    for match in FIGURE.finditer(content):  # 図の指定ごとに、
        svg = yield DrawnFigure(match.group(1))  # 描き終えた図を依頼する。まだならNoneが返る。
        drawn += svg is not None  # 入った図を数える。
        box = svg or f"(図 {match.group(1)} を作成中。文章と表は先に読めます)\n"  # 無い図は、作成中の枠にする。
        page = page.replace(match.group(0), box)  # 図の指定を、図か枠に置き換える。
    return page, drawn, len(specs)  # 本文・入った図の数・図の指定の数。


@do  # 呼ぶと、確かめてから組む手順(Program)ができる。
def checked_build(content):  # 決まりの外なら組まず、組んだら記録する。
    found = problems(content)  # 先に、中身の決まりを確かめる。
    if found:  # 決まりの外なら、
        return found  # 組まずに、直す所を返す。
    page, drawn, total = yield build(content)  # buildで組む。
    yield Record("opened", drawn, total)  # 開いた事と図の数を、記録に足す。
    return page, drawn, total  # 組んだ本文と図の数を返す。


@do  # 呼ぶと、頼まれた事を記録する手順(Program)ができる。
def requested():  # 頼まれた時刻を、所要の記録の最初の行にする。
    yield Record("requested", 0, 0)  # 図はまだ数えない。


@handler  # Programに取り付けられるハンドラにする。
def not_yet(effect, k):  # 描いた図がまだ無い時の答え。
    if isinstance(effect, DrawnFigure):  # 図の依頼には、
        return (yield Resume(k, None))  # 「まだ無い」を返す。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def drawn_in(figures):  # 描き終えた図の辞書から、テスト用のハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    def answer(effect, k):  # 描き終えた図だけに答える。
        if isinstance(effect, DrawnFigure) and effect.figure_id in figures:  # 描き終えた図なら、
            return (yield Resume(k, figures[effect.figure_id]))  # そのSVGを返す。
        return (yield Pass(effect, k))  # まだの図は、外側のハンドラへ渡す。

    return answer  # 作ったハンドラを返す。


def from_dir(folder):  # 作業役が図を置くdir(figures/<id>.svg)から、本番のハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    def answer(effect, k):  # 置かれた図だけに答える。
        path = folder / f"{effect.figure_id}.svg" if isinstance(effect, DrawnFigure) else None  # 図の置き場。
        if path is not None and path.is_file():  # 作業役が描き終えていれば、
            lines = [line for line in path.read_text().splitlines() if line.strip()]  # 空行を除く(段落を切らない)。
            return (yield Resume(k, "\n".join(lines) + "\n"))  # そのSVGを返す。
        return (yield Pass(effect, k))  # まだの図は、外側のハンドラへ渡す。

    return answer  # 作ったハンドラを返す。


@handler  # Programに取り付けられるハンドラにする。
def printed(effect, k):  # 記録の依頼を、表示で答えるテスト用のハンドラ(本物はファイルに時刻つきで足す)。
    if isinstance(effect, Record):  # 記録の依頼には、
        print(effect)  # 何を記録するかを表示し、
        return (yield Resume(k, None))  # 処理の続きを動かす。
    return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。


def local_now():  # 今の時刻を、時差つきで読む(本番の時計)。
    return datetime.now().astimezone()  # 機械の時計を読む。


def log_to(path, clock=local_now):  # 記録の依頼を、時刻つきの1行としてファイルに足す本番のハンドラを作る。
    @handler  # Programに取り付けられるハンドラにする。
    def answer(effect, k):  # 記録の依頼だけを受け持つ。
        if isinstance(effect, Record):  # 記録の依頼には、
            row = {"at": clock().isoformat(timespec="seconds"), "event": effect.event}  # 時計から時刻を読む。
            row["figures"] = [effect.drawn, effect.total]  # 入った図と図の指定の数。
            with path.open("a", encoding="utf-8") as log:  # 記録のファイルを足す形で開き、
                log.write(json.dumps(row, ensure_ascii=False) + "\n")  # 1行を足す。
            return (yield Resume(k, None))  # 処理の続きを動かす。
        return (yield Pass(effect, k))  # 担当外の依頼は、外側のハンドラへ渡す。

    return answer  # 作ったハンドラを返す。


def line_html(line):  # 表でない塊の1行を、見出し・箇条・作成中の枠・段落のどれかにする。
    heading = re.match(r"(#{1,3}) (.*)", line)  # 見出しの行か。
    if heading:  # 見出しなら、
        level = len(heading.group(1))  # 見出しの深さ。
        return f"<h{level}>{html.escape(heading.group(2))}</h{level}>"  # 見出しにする。
    if line.startswith("- "):  # 箇条の行なら、
        return f"<ul><li>{html.escape(line[2:])}</li></ul>"  # 箇条にする。
    if line.startswith("(図 "):  # 作成中の枠なら、
        return f'<p class="pending">{html.escape(line)}</p>'  # 点線の枠にする。
    return f"<p>{html.escape(line)}</p>"  # それ以外は段落にする。


def block_html(block):  # 空行で区切った塊1つを、図・表・行の並びのどれかにする。
    if block.lstrip().startswith("<svg"):  # 描いた図は、
        return f"<figure>{block}</figure>"  # そのまま入れる。
    rows = [line for line in block.splitlines() if line.startswith("|") and not set(line) <= set("|-: ")]  # 表の行。
    if not rows:  # 表でない塊は、
        return "\n".join(line_html(line) for line in block.splitlines())  # 1行ずつ変える。
    cells = [[html.escape(cell.strip()) for cell in row.strip("|").split("|")] for row in rows]  # 表のセル。
    head = "".join(f"<th>{cell}</th>" for cell in cells[0])  # 1行目を見出しの行にする。
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in cells[1:])  # 残りの行。
    return f"<table><tr>{head}</tr>{body}</table>"  # 1つの表にする。


def to_html(page):  # 組んだ本文を、決まった雛形(殻)に入れたHTMLにする。
    body = "\n".join(block_html(block) for block in re.split(r"\n\s*\n", page.strip()))  # 塊ごとに変える。
    title = re.search(r"^# (.*)$", page, re.M)  # ページの題。
    return SHELL.format(title=html.escape(title.group(1) if title else "報告"), body=body)  # 雛形に入れる。


def places(content_path):  # 中身のファイルから、図・ページ・記録の置き場を決める。
    folder = content_path.parent  # 中身のファイルのdir。
    return folder / "figures", folder / "page.html", folder / f"{content_path.stem}.timing.log"  # 3つの置き場。


def command_new(folder):  # 中身のファイルの例を作り、頼まれた時刻を記録する。
    folder.mkdir(parents=True, exist_ok=False)  # 既に在れば、上書きせずに止める。
    content_path = folder / "content.md"  # 中身のファイル。
    content_path.write_text(SAMPLE, encoding="utf-8")  # 例の中身を書く。
    run(with_handlers([log_to(places(content_path)[2])], requested()))  # 頼まれた時刻を記録する。
    return f"{content_path} を書きました"  # 次に書き換えるファイル。


def command_build(content_path):  # ページを組み直し、「図 n/m」を返す。
    figures, page_path, log_path = places(content_path)  # 置き場を決める。
    content = content_path.read_text(encoding="utf-8")  # 中身のファイルを読む。
    stack = [not_yet, from_dir(figures), log_to(log_path)]  # 外から順に: まだ無い図・置かれた図・記録。
    result = run(with_handlers(stack, checked_build(content)))  # 確かめてから組む。
    if isinstance(result, list):  # 決まりの外なら、
        raise SystemExit("組みませんでした:\n" + "\n".join(result))  # 直す所を出して止める。
    page, drawn, total = result  # 組んだ本文と図の数。
    page_path.write_text(to_html(page), encoding="utf-8")  # ページを書く。
    return f"{page_path}\n図 {drawn}/{total}"  # ページの置き場と「図 n/m」。


def command_request(content_path):  # 図を描く作業役への依頼文を作る。
    script = Path(__file__).resolve()  # どのdirから走らせても届くよう、この例の置き場を絶対のpathにする。
    build = f"uv run --no-project --with doeff==0.7.0 python {script} build {content_path.resolve()}"  # 作り直しの命令。
    return REQUEST.format(content=content_path, figures=places(content_path)[0], build=build).rstrip()  # 依頼文。


def command_timing(content_path):  # 頼まれてから、文章が開くまでと図が揃うまでの秒を返す。
    lines = places(content_path)[2].read_text(encoding="utf-8").splitlines()  # 記録の行。
    rows = [json.loads(line) for line in lines if line.strip()]  # 1行1件のJSON。
    start = next(datetime.fromisoformat(row["at"]) for row in rows if row["event"] == "requested")  # 頼まれた時刻。
    opened = [row for row in rows if row["event"] == "opened"]  # ページを組んだ行。
    full = [row for row in opened if 0 < row["figures"][1] == row["figures"][0]]  # 図が全部入った行。

    def since(row):  # 頼まれてからの秒を、表示の字にする。
        return f"{(datetime.fromisoformat(row['at']) - start).total_seconds():.0f}秒" if row else "まだ"  # 秒か「まだ」。

    first, last = (opened or [None])[0], (full or [None])[0]  # 最初に開いた行と、図が揃った最初の行。
    return f"文章が開くまで: {since(first)}\n図が揃うまで: {since(last)}"  # 2行の表示。


def test_build_pending_then_drawn() -> None:  # 図が無くても組め、描けた図から入る。
    _, drawn, total = run(with_handlers([not_yet], build(SAMPLE)))  # 図がまだ1つも無い。
    assert (drawn, total) == (0, 2)  # 「図 0/2」で組める。
    page, drawn, _ = run(with_handlers([not_yet, drawn_in({"fig-1": "<svg/>\n"})], build(SAMPLE)))  # 1つだけ描けた。
    assert (drawn, "<svg/>" in page, "図 fig-2 を作成中" in page) == (1, True, True)  # 描けた図と作成中の枠。
    page, drawn, _ = run(with_handlers([not_yet, drawn_in({"fig-1": "<svg/>\n", "fig-2": "<svg/>\n"})], build(SAMPLE)))  # 全部。
    assert (drawn, "~~~図" in page) == (2, False)  # 図の指定は、本文に残らない。


def test_problems_stop_the_build() -> None:  # 決まりの外の中身は、組まずに直す所を返す。
    assert problems(SAMPLE) == []  # 例の中身は決まりを満たす。
    assert problems(SAMPLE.replace("決めてほしい", UNFILLED)) == [f"雛形の「{UNFILLED}」が残っています"]  # 書きかけ。
    assert problems(FIGURE.sub("", SAMPLE)) == ["図の指定が1つもありません"]  # 図の指定が無い。
    long = SAMPLE.replace("決めてほしい事はありません。", "あ" * 61 + "。")  # 61字の文を冒頭に入れる。
    assert problems(long) == ["冒頭の文が62字です: " + "あ" * 12 + "…"]  # 句点を含めて62字。
    out = io.StringIO()  # 記録の表示を集める。
    with contextlib.redirect_stdout(out):  # printの表示を受け取る。
        result = run(with_handlers([not_yet, printed], checked_build(long)))  # 決まりの外の中身で組む。
    assert (result, out.getvalue()) == (problems(long), "")  # 組まず、記録もしない。
    with contextlib.redirect_stdout(out):  # printの表示を受け取る。
        run(with_handlers([not_yet, printed], checked_build(SAMPLE)))  # 決まりを満たす中身で組む。
    assert out.getvalue() == "Record(event='opened', drawn=0, total=2)\n"  # 開いた事を1行記録する。


def test_command_flow() -> None:  # 新しく作る・組む・図を置く・組み直す・所要を読む、の流れ。
    with tempfile.TemporaryDirectory() as tmp:  # 一時のdirで試す。
        folder = Path(tmp) / "search-slow"  # 1つの報告のdir。
        assert command_new(folder).endswith("content.md を書きました")  # 中身の例を書く。
        content_path = folder / "content.md"  # 中身のファイル。
        assert command_build(content_path).endswith("図 0/2")  # 文章と表が先に開く。
        first_page = (folder / "page.html").read_text()  # 最初に組んだページ。
        assert ("作成中" in first_page, "<table>" in first_page) == (True, True)  # 作成中の枠と表がある。
        (folder / "figures").mkdir()  # 作業役が図を置くdir。
        (folder / "figures/fig-1.svg").write_text('<svg viewBox="0 0 10 10">\n\n<rect width="10" height="10"/></svg>\n')  # 図1。
        assert command_build(content_path).endswith("図 1/2")  # 置いた図だけが入る。
        (folder / "figures/fig-2.svg").write_text('<svg viewBox="0 0 10 10"></svg>\n')  # 図2。
        assert command_build(content_path).endswith("図 2/2")  # 全部入る。
        assert (folder / "page.html").read_text().count("<figure><svg") == 2  # 2つの図が、ページに入った。
        timing = command_timing(content_path).splitlines()  # 所要の2行。
        assert [line.split(": ")[0] for line in timing] == ["文章が開くまで", "図が揃うまで"]  # 2つの行が出る。
        assert "まだ" not in "".join(timing)  # どちらも記録がある。
        request = command_request(content_path)  # 作業役への依頼文。
        assert (str(content_path.resolve()) in request, "figures/<id>.svg" in request) == (True, True)  # 置き場が埋まっている。


def verify() -> None:  # 記事のテストを順に実行する。
    test_build_pending_then_drawn()  # 図が無くても組め、描けた図から入る。
    test_problems_stop_the_build()  # 決まりの外は組まない。
    test_command_flow()  # 命令の流れが通る。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ動かす。
    if len(sys.argv) == 1:  # 引数が無ければ、
        verify()  # 外部通信なしで、組み方・決まり・命令の流れを確かめる。
        print("図の後入れ・組む前の決まり・命令の流れ: OK")  # 全確認後に表示する。
    else:  # 引数があれば、命令として動かす。
        name, target = sys.argv[1], Path(sys.argv[2])  # 命令の名と対象。
        commands = {"new": command_new, "build": command_build, "request": command_request, "timing": command_timing}  # 4つの命令。
        print(commands[name](target))  # 命令を実行し、結果を表示する。
