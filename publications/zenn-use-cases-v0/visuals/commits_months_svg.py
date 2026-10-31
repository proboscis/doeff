"""開発の記録の図「月ごとのcommitの数と、Claudeとの共著の行があるcommit」を、数えた表(checks/commits-months.csv)からSVGに描く。

使い方: `uv run --no-sync python publications/zenn-use-cases-v0/visuals/commits_months_svg.py`。
visuals/commits-months.svg を書き、PNGへはrender_svg.mjsで撮る(幅1200・高さ820)。
"""

import csv  # 月ごとの数の表を読む。
from pathlib import Path  # 表とSVGの置き場を指す。

SOURCE = Path(__file__).resolve().parents[1]  # 記事の正本のディレクトリ。
TABLE = SOURCE / "checks/commits-months.csv"  # 2026-10-18に数えた表。
OUTPUT = SOURCE / "visuals/commits-months.svg"  # 書き出すSVG。
CLAUDE_COLOR = "#2a78d6"  # 共著の行があるcommit(datavizの既定の1番の青)。
OTHER_COLOR = "#eb6834"  # 共著の行が無いcommit(2番の橙)。
LEFT, RIGHT, TOP, BOTTOM = 130, 1160, 250, 690  # 描く範囲の左・右・上・下の端。
TOP_VALUE = 2200  # 縦軸の上端の値。
FONT = "IPAPGothic, IPAGothic, sans-serif"  # 図の他の記事と同じ字形。
NOTED = {"2025-11", "2026-02", "2026-10"}  # 割合を棒の上に書く月(本文で触れる月だけ)。
Row = tuple[str, int, int]  # 1か月分の行(月・全体・共著)。


def read_rows() -> list[Row]:  # 表を、月と数の組にする。
    lines = [line for line in TABLE.read_text().splitlines() if not line.startswith("#")]  # 注の行を除く。
    return [(row["month"], int(row["total"]), int(row["claude"])) for row in csv.DictReader(lines)]  # 月の順の行。


def y_of(value: float) -> float:  # 値を、縦の座標へ直す。
    return BOTTOM - (BOTTOM - TOP) * value / TOP_VALUE  # 0が下の端、TOP_VALUEが上の端。


def x_of(index: int, count: int) -> float:  # 何か月目かを、その月の棒の中央の横の座標へ直す。
    return LEFT + (index + 0.5) * (RIGHT - LEFT) / count  # 範囲を月の数で等分する。


def bars(rows: list[Row]) -> list[str]:  # 月ごとに、共著を下・印なしを上に積んだ棒を描く。
    width = (RIGHT - LEFT) / len(rows) - 22  # 棒の幅(左右に11pxずつ空ける)。
    return [  # 系列の間に2pxの白い隙間を残して積む。数が0の系列は描かない。
        f'<rect x="{x_of(index, len(rows)) - width / 2:.1f}" y="{y_of(high):.1f}" width="{width:.1f}" '  # 区切りの位置。
        f'height="{max(1.0, y_of(low) - y_of(high) - (2 if low else 0)):.1f}" fill="{color}"/>'  # 高さと色。
        for index, (_, total, claude) in enumerate(rows)  # 月ごとに。
        for color, low, high in ((CLAUDE_COLOR, 0, claude), (OTHER_COLOR, claude, total))  # 下の系列から順に。
        if high > low  # 数が0の系列は除く。
    ]


def axes(rows: list[Row]) -> list[str]:  # 目盛りの線と字を描く。
    lines = [  # 500個ごとの薄い横線と値。
        f'<line x1="{LEFT}" y1="{y_of(value):.1f}" x2="{RIGHT}" y2="{y_of(value):.1f}" stroke="#e5e7eb" stroke-width="2"/>'  # 線。
        f'<text x="{LEFT - 14}" y="{y_of(value) + 9:.1f}" text-anchor="end" font-size="24" fill="#4b5563">{value:,}</text>'  # 値。
        for value in range(0, TOP_VALUE + 1, 500)  # 0・500・1,000・1,500・2,000。
    ]
    months = [  # 月の目盛り。年の変わり目と最初の月だけ年を書く。
        f'<text x="{x_of(index, len(rows)):.1f}" y="{BOTTOM + 34}" text-anchor="middle" font-size="22" fill="#4b5563">'  # 位置。
        f"{int(month[5:])}月</text>"  # 月。
        + (  # 年の印。
            f'<text x="{x_of(index, len(rows)):.1f}" y="{BOTTOM + 64}" text-anchor="middle" font-size="22" fill="#4b5563">{month[:4]}年</text>'
            if index == 0 or month.endswith("-01")  # 最初の月と1月だけ。
            else ""  # ほかの月は書かない。
        )
        for index, (month, _, _) in enumerate(rows)  # 月ごとに。
    ]
    return lines + months  # 描いた要素を返す。


def ratio_labels(rows: list[Row]) -> list[str]:  # 本文で触れる月に、共著の割合を書く。
    return [  # 棒の上に書く。
        f'<text x="{x_of(index, len(rows)):.1f}" y="{y_of(total) - 12:.1f}" text-anchor="middle" font-size="22" '  # 位置。
        f'font-weight="bold" fill="#111827">{claude / total:.0%}</text>'  # 割合。
        for index, (month, total, claude) in enumerate(rows)  # 月ごとに。
        if month in NOTED  # 触れる月だけ。
    ]


def legend(rows: list[Row]) -> list[str]:  # 凡例を、系列ごとの合計つきで描く。
    claude = sum(row[2] for row in rows)  # 共著の合計。
    other = sum(row[1] - row[2] for row in rows)  # 印なしの合計。
    entries = (  # 色・名・合計・位置。
        (CLAUDE_COLOR, "Claudeとの共著の行あり", claude, 150),  # 1行目。
        (OTHER_COLOR, "共著の行なし(人の手かCodexで、分けられない)", other, 192),  # 2行目。
    )
    return [  # 色の見本と、名と合計。
        f'<rect x="{LEFT}" y="{y - 22}" width="26" height="26" rx="4" fill="{color}"/>'  # 色の見本。
        f'<text x="{LEFT + 38}" y="{y}" font-size="26" fill="#111827">{name} {count:,}個</text>'  # 名と合計。
        for color, name, count, y in entries  # 系列ごとに1つ。
    ]


def main() -> None:  # 表を読んでSVGを書く。
    rows = read_rows()  # 14か月分の行。
    assert len(rows) == 14, rows  # 2025-09〜2026-10の14か月。
    assert rows[0][0] == "2025-09", rows  # 最初の月は、最初のcommitの2025年9月。
    total = sum(row[1] for row in rows)  # 全体の数。
    body = [  # SVGの中身。
        '<rect width="1200" height="820" fill="#ffffff"/>',  # 白い背景。
        '<text x="600" y="60" text-anchor="middle" font-size="40" font-weight="bold" fill="#111827">'  # 題の位置。
        "月ごとのcommitの数と、Claudeとの共著の行があるcommit</text>",  # 題。
        '<text x="600" y="102" text-anchor="middle" font-size="24" fill="#4b5563">'  # 範囲の位置。
        f"doeffのmain・2025年9月16日〜2026年10月18日・計{total:,}個(mergeを含む)</text>",  # 範囲。
        *legend(rows),  # 凡例。
        *axes(rows),  # 目盛り。
        *bars(rows),  # 棒。
        *ratio_labels(rows),  # 本文で触れる月の割合。
        f'<line x1="{LEFT}" y1="{BOTTOM}" x2="{RIGHT}" y2="{BOTTOM}" stroke="#374151" stroke-width="2"/>',  # 0の線。
        '<text x="600" y="800" text-anchor="middle" font-size="22" fill="#4b5563">'  # 測った日の位置。
        "2026年10月18日に、書いた月(日本時間)で数えた。棒の上の数は共著の割合。10月は18日まで</text>",  # 測った日。
    ]
    head = f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="820" viewBox="0 0 1200 820" font-family="{FONT}">'  # 頭。
    OUTPUT.write_text("\n  ".join([head, *body]) + "\n</svg>\n")  # SVGを書く。
    print(f"wrote {OUTPUT.name}: {len(rows)}か月・計{total:,}個")  # 書いた事を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ描く。
    main()  # 表からSVGを書く。
