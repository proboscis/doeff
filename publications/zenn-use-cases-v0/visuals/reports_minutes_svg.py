"""報告のページの記事の図「図の数ごとの、図が全部入るまでの分」を、所要の表(reviews/reports-pages.csv)からSVGに描く。

使い方: `uv run --no-sync python publications/zenn-use-cases-v0/visuals/reports_minutes_svg.py`。
visuals/reports-minutes.svg を書き、PNGへはrender_svg.mjsで撮る(幅1200・高さ760)。
棒は毎日の中央値(1つの系列)、橙の菱形は2026年10月1日の試し(図4つ・5分36秒)。数はどの棒にも字で添える。
"""

import csv  # 所要の表を読む。
import statistics  # 中央値を数える。
from pathlib import Path  # 表とSVGの置き場を指す。

SOURCE = Path(__file__).resolve().parents[1]  # 記事の正本のディレクトリ。
TABLE = SOURCE / "reviews/reports-pages.csv"  # 2026-10-12〜18の所要の記録から写した表。
OUTPUT = SOURCE / "visuals/reports-minutes.svg"  # 書き出すSVG。
FONT = "IPAPGothic, IPAGothic, sans-serif"  # 図の他の記事と同じ字形。
BLUE = "#2a78d6"  # datavizの既定の1番。毎日の中央値。
ORANGE = "#eb6834"  # datavizの既定の2番。試しの1回。
INK, MUTED, GRID = "#111827", "#4b5563", "#e5e7eb"  # 字・薄い字・格子の色。
LEFT, RIGHT, TOP = 250, 1060, 230  # 値の軸の左と右の端と、最初の行の上端。
ROW, BAR = 84, 24  # 1行の高さと、棒の太さ。
AXIS_MAX, STEP = 25, 5  # 軸の上端(分)と目盛りの間隔。
TRIAL = 5 + 36 / 60  # 試しの5分36秒(分)。
GROUPS = (("図1つ", 1, 1), ("図2つ", 2, 2), ("図3つ", 3, 3), ("図4つ", 4, 4), ("図5〜8つ", 5, 8))  # 行の名と図の数の範囲。


def x_of(minutes: float) -> float:  # 分を、横の座標へ直す。
    return LEFT + (RIGHT - LEFT) * minutes / AXIS_MAX  # 0分が左の端。


def groups() -> list[tuple[str, float, int]]:  # 行ごとの名・中央値(分)・ページの数。
    with TABLE.open(newline="") as handle:  # 所要の表を開く。
        rows = [row for row in csv.DictReader(handle) if row["figures_in_s"]]  # 図が全部入ったページ。
    picked = [(name, [int(r["figures_in_s"]) for r in rows if low <= int(r["figures_total"]) <= high]) for name, low, high in GROUPS]  # 図の数で分ける。
    return [(name, statistics.median(seconds) / 60, len(seconds)) for name, seconds in picked]  # 中央値を分にする。


def bar(index: int, name: str, minutes: float, pages: int) -> str:  # 1行の名・棒・値の字を描く。
    y = TOP + index * ROW  # この行の上端。
    end = x_of(minutes)  # 棒の先。
    path = f"M{LEFT},{y} H{end - 4} Q{end},{y} {end},{y + 4} V{y + BAR - 4} Q{end},{y + BAR} {end - 4},{y + BAR} H{LEFT} Z"  # 先だけ丸い棒。
    return "\n".join([  # 行の3つの部品。
        f'  <text x="{LEFT - 20}" y="{y + 19}" text-anchor="end" font-size="24" fill="{INK}">{name}</text>',  # 行の名。
        f'  <path d="{path}" fill="{BLUE}"/>',  # 中央値の棒。
        f'  <text x="{end + 14}" y="{y + 20}" font-size="22" fill="{INK}">{minutes:.1f}分<tspan fill="{MUTED}">({pages}ページ)</tspan></text>',  # 値の字。
    ])


def axis() -> str:  # 目盛りの縦線と字を描く。
    bottom = TOP + len(GROUPS) * ROW - ROW + BAR + 20  # 最後の行の下。
    ticks = range(0, AXIS_MAX + 1, STEP)  # 0・5・10・15・20・25分。
    lines = [f'  <line x1="{x_of(t)}" y1="{TOP - 16}" x2="{x_of(t)}" y2="{bottom}" stroke="{GRID}" stroke-width="2"/>' for t in ticks]  # 縦線。
    labels = [f'  <text x="{x_of(t)}" y="{bottom + 34}" text-anchor="middle" font-size="22" fill="{MUTED}">{t}分</text>' for t in ticks]  # 字。
    return "\n".join([*lines, *labels])  # 線を先に、字を後に描く。


def trial() -> str:  # 図4つの行に、試しの菱形と字を描く。
    x, y = x_of(TRIAL), TOP + 3 * ROW + BAR / 2  # 図4つの行の真ん中。
    diamond = f"M{x},{y - 13} L{x + 13},{y} L{x},{y + 13} L{x - 13},{y} Z"  # 菱形。
    return "\n".join([  # 菱形と字。
        f'  <path d="{diamond}" fill="{ORANGE}" stroke="#ffffff" stroke-width="2"/>',  # 試しの印。
        f'  <text x="{x}" y="{y - 26}" text-anchor="middle" font-size="22" fill="{INK}">試し 5分36秒</text>',  # 試しの字。
    ])


def legend() -> str:  # 題の下の凡例を描く。
    return "\n".join([  # 2つの印と字。
        f'  <rect x="{LEFT}" y="128" width="28" height="20" rx="4" fill="{BLUE}"/>',  # 棒の印。
        f'  <text x="{LEFT + 40}" y="146" font-size="22" fill="{INK}">毎日の中央値(2026年10月12〜18日・180ページ)</text>',  # 棒の字。
        f'  <path d="M{LEFT + 14},{165} L{LEFT + 27},{178} L{LEFT + 14},{191} L{LEFT + 1},{178} Z" fill="{ORANGE}"/>',  # 菱形の印。
        f'  <text x="{LEFT + 40}" y="186" font-size="22" fill="{INK}">試しの1回(2026年10月1日・図4つ)</text>',  # 菱形の字。
    ])


def main() -> None:  # SVGを組み立てて書く。
    rows = groups()  # 行ごとの中央値。
    parts = [  # SVGの部品を、描く順に並べる。
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="760" viewBox="0 0 1200 760" font-family="{FONT}">',  # 外枠。
        '  <rect width="1200" height="760" fill="#ffffff"/>',  # 白の背景。
        f'  <text x="600" y="66" text-anchor="middle" font-size="38" font-weight="bold" fill="{INK}">図の数ごとの、図が全部入るまでの分</text>',  # 題。
        legend(),  # 凡例。
        axis(),  # 目盛り。
        *(bar(i, name, minutes, pages) for i, (name, minutes, pages) in enumerate(rows)),  # 5行の棒。
        trial(),  # 試しの菱形。
        f'  <text x="600" y="730" text-anchor="middle" font-size="20" fill="{MUTED}">頼まれてから、図の指定が全部ページに入るまで。図の数は、全部入った時の数。</text>',  # 注。
        "</svg>",  # 閉じ。
    ]
    OUTPUT.write_text("\n".join(parts) + "\n")  # SVGを書く。
    print(f"wrote {OUTPUT.name}: " + " / ".join(f"{name} {minutes:.1f}分 n={pages}" for name, minutes, pages in rows))  # 値を表示する。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ描く。
    main()  # 表からSVGを書く。
