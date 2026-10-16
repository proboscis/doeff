"""開発の記録の図「35日の、日ごとのcommitの数」を、数えた表(reviews/fleet-commits.csv)からSVGに描く。

使い方: `uv run --no-sync python publications/zenn-use-cases-v0/visuals/fleet_commits_svg.py`。
visuals/fleet-commits.svg を書き、PNGへはrender_svg.mjsで撮る(幅1200・高さ800)。
"""

import csv  # 日ごとの数の表を読む。
from datetime import date  # 目盛りの日付を作る。
from itertools import accumulate  # 積み上げの高さを、下から順に足して作る。
from pathlib import Path  # 表とSVGの置き場を指す。

SOURCE = Path(__file__).resolve().parents[1]  # 記事の正本のディレクトリ。
TABLE = SOURCE / "reviews/fleet-commits.csv"  # 2026-10-17に数えた表。
OUTPUT = SOURCE / "visuals/fleet-commits.svg"  # 書き出すSVG。
SERIES = (  # 下から積む順の系列(表の列名・凡例の名・色)。色はdatavizの既定の1〜3番。
    ("controllers", "裏の仕組み(配り役・作業ボードなど)", "#2a78d6"),  # 1番の青。
    ("screen", "画面", "#eb6834"),  # 2番の橙。
    ("doeff", "doeff", "#1baf7a"),  # 3番の青緑。
)
LEGEND_AT = ((120, 150), (120, 192), (420, 192))  # 凡例の各行の左端と字の基線。
LEFT, RIGHT, TOP, BOTTOM = 120, 1160, 250, 680  # 描く範囲の左・右・上・下の端。
TOP_VALUE = 1400  # 縦軸の上端の値。
FONT = "IPAPGothic, IPAGothic, sans-serif"  # 図の他の記事と同じ字形。
Row = tuple[date, dict[str, int]]  # 1日分の行(日付と、系列ごとの数)。


def read_rows() -> list[Row]:  # 表を、日付と系列ごとの数の組にする。
    lines = [line for line in TABLE.read_text().splitlines() if not line.startswith("#")]  # 注の行を除く。
    return [  # 日付順の行を返す。
        (date.fromisoformat(row["day"]), {key: int(row[key]) for key, _, _ in SERIES})  # 1日分。
        for row in csv.DictReader(lines)  # 見出しの行を鍵にして読む。
    ]


def y_of(value: float) -> float:  # 値を、縦の座標へ直す。
    return BOTTOM - (BOTTOM - TOP) * value / TOP_VALUE  # 0が下の端、TOP_VALUEが上の端。


def x_of(index: int, count: int) -> float:  # 何日目かを、その日の棒の中央の横の座標へ直す。
    return LEFT + (index + 0.5) * (RIGHT - LEFT) / count  # 範囲を日数で等分する。


def segments(counts: dict[str, int]) -> list[tuple[str, int, int]]:  # 1日分を、色と下端・上端の組にする。
    tops = list(accumulate(counts[key] for key, _, _ in SERIES))  # 下から積んだ上端。
    return [(color, low, high) for (_, _, color), low, high in zip(SERIES, [0, *tops[:-1]], tops, strict=True)]  # 組。


def bars(rows: list[Row]) -> list[str]:  # 日ごとの積み上げの棒を描く。
    width = (RIGHT - LEFT) / len(rows) - 8  # 棒の幅(左右に4pxずつ空ける)。
    return [  # 系列の間に2pxの白い隙間を残して積む。
        f'<rect x="{x_of(index, len(rows)) - width / 2:.1f}" y="{y_of(high):.1f}" width="{width:.1f}" '  # 区切りの位置。
        f'height="{max(0.0, y_of(low) - y_of(high) - 2):.1f}" fill="{color}"/>'  # 高さと色。
        for index, (_, counts) in enumerate(rows)  # 日ごとに。
        for color, low, high in segments(counts)  # 下の系列から順に。
    ]


def axes(rows: list[Row]) -> list[str]:  # 目盛りの線と字を描く。
    lines = [  # 400個ごとの薄い横線と値。
        f'<line x1="{LEFT}" y1="{y_of(value):.1f}" x2="{RIGHT}" y2="{y_of(value):.1f}" stroke="#e5e7eb" stroke-width="2"/>'  # 線。
        f'<text x="{LEFT - 14}" y="{y_of(value) + 9:.1f}" text-anchor="end" font-size="26" fill="#4b5563">{value:,}</text>'  # 値。
        for value in range(0, TOP_VALUE + 1, 400)  # 0・400・800・1,200。
    ]
    days = [  # 9/12から7日ごとと、最後の10/16に日付を書く。
        f'<text x="{x_of(index, len(rows)):.1f}" y="{BOTTOM + 40}" text-anchor="middle" font-size="26" '  # 位置。
        f'fill="#4b5563">{day.month}/{day.day}</text>'  # 日付。
        for index, (day, _) in enumerate(rows)  # 日ごとに。
        if index % 7 == 0 or index == len(rows) - 1  # 週の頭と最後の日だけ。
    ]
    return lines + days  # 描いた要素を返す。


def legend(rows: list[Row]) -> list[str]:  # 凡例を、系列ごとの35日の合計つきで描く。
    return [  # 色の見本と、名と合計。
        f'<rect x="{x}" y="{y - 22}" width="26" height="26" rx="4" fill="{color}"/>'  # 色の見本。
        f'<text x="{x + 38}" y="{y}" font-size="28" fill="#111827">{name} {sum(c[key] for _, c in rows):,}個</text>'  # 名と合計。
        for (key, name, color), (x, y) in zip(SERIES, LEGEND_AT, strict=True)  # 系列ごとに1つ。
    ]


def peak_label(rows: list[Row]) -> str:  # 最も多かった日に、数を添える。
    index = max(range(len(rows)), key=lambda i: sum(rows[i][1].values()))  # 合計が最大の日。
    day, counts = rows[index]  # その日の日付と数。
    total = sum(counts.values())  # その日の合計。
    return (  # 棒の上に書く。
        f'<text x="{x_of(index, len(rows)):.1f}" y="{y_of(total) - 14:.1f}" text-anchor="middle" font-size="26" '  # 位置。
        f'font-weight="bold" fill="#111827">{day.month}/{day.day} {total:,}個</text>'  # 日付と数。
    )


def main() -> None:  # 表を読んでSVGを書く。
    rows = read_rows()  # 35日分の行。
    assert len(rows) == 35, len(rows)  # 9/12〜10/16の35日。
    total = sum(sum(counts.values()) for _, counts in rows)  # 3つのリポジトリの合計。
    body = [  # SVGの中身。
        '<rect width="1200" height="800" fill="#ffffff"/>',  # 白い背景。
        '<text x="600" y="64" text-anchor="middle" font-size="44" font-weight="bold" fill="#111827">'  # 題の位置。
        "35日の、日ごとのcommitの数</text>",  # 題。
        '<text x="600" y="108" text-anchor="middle" font-size="26" fill="#4b5563">'  # 範囲の位置。
        f"3つのリポジトリのmain・2026年9月12日〜10月16日(日本時間)・計{total:,}個</text>",  # 範囲。
        *legend(rows),  # 凡例。
        *axes(rows),  # 目盛り。
        *bars(rows),  # 棒。
        peak_label(rows),  # 最も多かった日。
        f'<line x1="{LEFT}" y1="{BOTTOM}" x2="{RIGHT}" y2="{BOTTOM}" stroke="#374151" stroke-width="2"/>',  # 0の線。
        '<text x="600" y="772" text-anchor="middle" font-size="24" fill="#4b5563">'  # 測った日の位置。
        "2026年10月17日に、各リポジトリのcommitの時刻で数えた</text>",  # 測った日。
    ]
    head = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="800" viewBox="0 0 1200 800" font-family="{FONT}">'  # 頭。
    OUTPUT.write_text("\n  ".join([head, *body]) + "\n</svg>\n")  # SVGを書く。
    print(f"wrote {OUTPUT.name}: {len(rows)}日・計{total:,}個")  # 書いた事を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ描く。
    main()  # 表からSVGを書く。
