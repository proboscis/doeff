"""待ちの1行の記事の図「待ちの申告2,644個を、最後に申告した日ごとに分ける」を、日ごとの表(reviews/waitline-days.csv)からSVGに描く。

使い方: `uv run --no-sync python publications/zenn-use-cases-v0/visuals/waitline_days_svg.py`。
visuals/waitline-days.svg を書き、PNGへはrender_svg.mjsで撮る(幅1200・高さ960)。
workと筆者を待つ申告は数の桁が違うので、1つの軸に混ぜず、左右2つの段に分ける。左はworkの数、右は筆者を待つ数を種類ごとに積む。
"""

import csv  # 日ごとの表を読む。
from pathlib import Path  # 表とSVGの置き場を指す。

SOURCE = Path(__file__).resolve().parents[1]  # 記事の正本のディレクトリ。
TABLE = (
    SOURCE / "reviews/waitline-days.csv"
)  # 2026-10-18 20:14(日本時間)の申告を、最後に申告した日で数えた表。
OUTPUT = SOURCE / "visuals/waitline-days.svg"  # 書き出すSVG。
FONT = "IPAPGothic, IPAGothic, sans-serif"  # 図の他の記事と同じ字形。
GRAY = "#9ca3af"  # workの棒。1つの系列なので、色で区別しない。
KINDS = (  # 筆者を待つ申告の種類(表の列・凡例の字・datavizの既定の1〜3番を固定の順で)。
    ("user_decide", "決めてほしい(decide)", "#2a78d6"),  # 1番の青。
    ("user_review", "見てほしい(review)", "#eb6834"),  # 2番の橙。
    (
        "user_discuss",
        "議論したい(discuss)",
        "#1baf7a",
    ),  # 3番の水色。字の濃さが足りないので、値の字を必ず添える。
)
INK, MUTED, GRID, SURFACE = "#111827", "#4b5563", "#e5e7eb", "#ffffff"  # 字・薄い字・格子・地の色。
TOP, ROW = 250, 56  # 最初の行の上端と、1行の高さ。
BAR = 30  # 棒の太さ。
WORK = (190, 560, 600, 100)  # 左の段: 軸の左端・右端・上限・目盛りの間隔。
USER = (720, 1090, 80, 20)  # 右の段: 軸の左端・右端・上限・目盛りの間隔。


def read_rows() -> list[dict[str, str]]:  # 表を、行ごとの辞書にするため。
    return list(csv.DictReader(TABLE.read_text().splitlines()))  # 見出しの行を鍵にして読む。


def scale(
    axis: tuple[int, int, int, int], value: float
) -> float:  # 値を、その段の横の座標へ直すため。
    left, right, top, _ = axis  # 軸の左端・右端・上限。
    return left + (right - left) * value / top  # 0を左端、上限を右端に置く。


def axis_marks(
    axis: tuple[int, int, int, int], bottom: int
) -> list[str]:  # 段の目盛りの線と字を描くため。
    _, _, top, step = axis  # 上限と目盛りの間隔。
    return [
        part
        for value in range(0, top + 1, step)
        for part in (  # 0から上限まで、間隔ごとに。
            f'<line x1="{scale(axis, value):.1f}" y1="{TOP - 14}" x2="{scale(axis, value):.1f}" y2="{bottom}" stroke="{GRID}" stroke-width="2"/>',  # 薄い縦線。
            f'<text x="{scale(axis, value):.1f}" y="{bottom + 30}" text-anchor="middle" font-size="20" fill="{MUTED}">{value}</text>',  # 目盛りの字。
        )
    ]


def row_parts(
    index: int, row: dict[str, str]
) -> list[str]:  # 1日の行(日付・workの棒・筆者を待つ積み棒)を描くため。
    y = TOP + index * ROW  # この行の上端。
    mid = y + BAR / 2 + 7  # 字の基線。
    work = int(row["work"])  # その日のworkの数。
    day = row["day"][5:].replace("-", "月", 1) + "日"  # 「10月09日」の形。
    parts = [  # 日付とworkの棒と値。
        f'<text x="{WORK[0] - 16}" y="{mid:.1f}" text-anchor="end" font-size="22" fill="{INK}">{day}</text>',  # 日付。
        f'<rect x="{WORK[0]}" y="{y}" width="{scale(WORK, work) - WORK[0]:.1f}" height="{BAR}" rx="4" fill="{GRAY}"/>',  # workの棒。
        f'<text x="{scale(WORK, work) + 8:.1f}" y="{mid:.1f}" font-size="20" fill="{INK}">{work}</text>',  # workの値。
    ]
    counts = [(int(row[column]), label, color) for column, label, color in KINDS]  # 種類ごとの数。
    starts = [
        sum(count for count, _, _ in counts[:i]) for i in range(len(counts))
    ]  # 積む時の始まりの値。
    parts += [  # 0でない種類だけを、2pxの隙間を空けて積む。
        f'<rect x="{scale(USER, start) + (2 if start else 0):.1f}" y="{y}" width="{scale(USER, start + count) - scale(USER, start) - (2 if start else 0):.1f}" '
        f'height="{BAR}" rx="4" fill="{color}"/>'
        for (count, _, color), start in zip(counts, starts, strict=True)
        if count
    ]
    total = sum(count for count, _, _ in counts)  # 筆者を待つ数の合計。
    label = (
        "・".join(f"{text.split('(')[0]} {count}" for count, text, _ in counts if count) or "0"
    )  # 値の字(種類と数)。
    parts.append(
        f'<text x="{scale(USER, total) + 8:.1f}" y="{mid:.1f}" font-size="20" fill="{INK}">{label}</text>'
    )  # 右に添える。
    return parts


def legend() -> list[str]:  # 右の段の凡例を描くため(色だけで種類を見分けさせない)。
    return [
        part
        for i, (_, label, color) in enumerate(KINDS)
        for part in (
            f'<rect x="{USER[0] + i * 160}" y="174" width="22" height="22" rx="4" fill="{color}"/>',  # 色の印。
            f'<text x="{USER[0] + i * 160 + 30}" y="192" font-size="18" fill="{INK}">{label.split("(")[0]}</text>',  # 種類の字。
        )
    ]


def main() -> None:  # 表を読み、SVGを書くため。
    rows = read_rows()  # 日ごとの行。
    bottom = TOP + len(rows) * ROW - 10  # 最後の行の下端。
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="960" viewBox="0 0 1200 960" font-family="{FONT}">',
        f'<rect width="1200" height="960" fill="{SURFACE}"/>',
        f'<text x="600" y="62" text-anchor="middle" font-size="36" font-weight="bold" fill="{INK}">待ちの申告2,644個を、最後に申告した日ごとに分ける</text>',
        f'<text x="600" y="102" text-anchor="middle" font-size="22" fill="{MUTED}">2026年10月18日20時14分(日本時間)の時点。会話ごとに、最後のターンの1行だけが残る</text>',
        f'<text x="{WORK[0]}" y="160" font-size="26" font-weight="bold" fill="{INK}">work(外を待つ・続ける)  計2,508</text>',
        f'<text x="{USER[0]}" y="160" font-size="26" font-weight="bold" fill="{INK}">筆者を待つ  計136</text>',
        *axis_marks(WORK, bottom),
        *axis_marks(USER, bottom),
        *legend(),
        *[part for index, row in enumerate(rows) for part in row_parts(index, row)],
        f'<text x="{(WORK[0] + WORK[1]) / 2}" y="{bottom + 64}" text-anchor="middle" font-size="20" fill="{MUTED}">会話の数(個)</text>',
        f'<text x="{(USER[0] + USER[1]) / 2}" y="{bottom + 64}" text-anchor="middle" font-size="20" fill="{MUTED}">会話の数(個)・左と目盛りが違う</text>',
        f'<text x="600" y="{bottom + 110}" text-anchor="middle" font-size="22" fill="{INK}">10月11日までの「見てほしい」119個は、1週間後も残っていた。筆者は11日にこの申告をやめた</text>',
        "</svg>",
    ]
    OUTPUT.write_text("\n".join(parts) + "\n")  # SVGを書く。
    print("wrote", OUTPUT)  # 書いた先を表示する。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ描く。
    main()  # 表からSVGを書く。
