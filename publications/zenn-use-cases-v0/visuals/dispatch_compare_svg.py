"""配る判断の記事の図「配った枚数と、急ぎのcardの最長の待ち」を、比べた表(reviews/dispatch-compare.csv)からSVGに描く。

使い方: `uv run --no-sync python publications/zenn-use-cases-v0/visuals/dispatch_compare_svg.py`。
visuals/dispatch-compare.svg を書き、PNGへはrender_svg.mjsで撮る(幅1200・高さ1000)。
2つの指標は単位が違うので、1つの軸に混ぜず、上下2段に分ける。仮定2つの値は、同じ行の2つの印を線で結ぶ。
"""

import csv  # 比べた表を読む。
from pathlib import Path  # 表とSVGの置き場を指す。

SOURCE = Path(__file__).resolve().parents[1]  # 記事の正本のディレクトリ。
TABLE = SOURCE / "reviews/dispatch-compare.csv"  # 2026-10-11の報告から写した表。
OUTPUT = SOURCE / "visuals/dispatch-compare.svg"  # 書き出すSVG。
FONT = "IPAPGothic, IPAGothic, sans-serif"  # 図の他の記事と同じ字形。
BLUE = "#2a78d6"  # datavizの既定の1番。模擬の値。
ORANGE = "#eb6834"  # datavizの既定の2番。AIの配り役の実績。
INK, MUTED, GRID = "#111827", "#4b5563", "#e5e7eb"  # 字・薄い字・格子の色。
LEFT, RIGHT = 340, 1060  # 値の軸の左と右の端。
ROW_GAP = 44  # 1行の高さ。
PANELS = (  # 上下2段(見出し・表の列・軸の上端・目盛りの間隔・単位・見出しの基線)。
    ("配った枚数(枚)", "count", 500, 100, "枚", 250),  # 上の段。
    ("急ぎ(p0)のcardの最長の待ち(分)", "p0", 600, 100, "分", 625),  # 下の段。
)


def read_rows() -> list[dict[str, str]]:  # 表を、行ごとの辞書にする。
    lines = [line for line in TABLE.read_text().splitlines() if not line.startswith("#")]  # 注の行を除く。
    return list(csv.DictReader(lines))  # 見出しの行を鍵にして読む。


def rounded(value: float) -> int:  # 分と枚数を、四捨五入した整数にする(108.5は109)。
    return int(value + 0.5)  # 値は正なので、0.5を足して切り捨てる。


def span(low: int, high: int, unit: str) -> str:  # 2つの仮定の値を「低〜高」の字にする。
    return f"{low}{unit}" if low == high else f"{low}〜{high}{unit}"  # 同じなら1つだけ書く。


def panel(title: str, column: str, top_value: int, step: int, unit: str, baseline: int, rows: list[dict[str, str]]) -> list[str]:  # 1段を描く。
    def x_of(value: float) -> float:  # 値を、横の座標へ直す。
        return LEFT + (RIGHT - LEFT) * value / top_value  # 0が左の端、top_valueが右の端。

    first = baseline + 40  # 1行目の中央の縦の座標。
    last = first + ROW_GAP * (len(rows) - 1)  # 最後の行の中央。
    parts = [f'<text x="40" y="{baseline}" font-size="28" font-weight="bold" fill="{INK}">{title}</text>']  # 段の見出し。
    for tick in range(0, top_value + 1, step):  # 目盛りごとに、
        x = x_of(tick)  # 目盛りの横の座標。
        parts.append(f'<line x1="{x:.1f}" y1="{first - 24}" x2="{x:.1f}" y2="{last + 24}" stroke="{GRID}" stroke-width="2"/>')  # 縦の薄い線。
        parts.append(f'<text x="{x:.1f}" y="{last + 52}" text-anchor="middle" font-size="20" fill="{MUTED}">{tick:,}</text>')  # 目盛りの値。
    for index, row in enumerate(rows):  # 行ごとに、
        y = first + ROW_GAP * index  # 行の中央。
        tight, loose = float(row[f"tight_{column}"]), float(row[f"loose_{column}"])  # 2つの仮定の値。
        low, high = sorted((rounded(tight), rounded(loose)))  # 字に書く低い値と高い値。
        parts.append(f'<text x="40" y="{y + 8}" font-size="24" fill="{INK}">{row["label"]}</text>')  # 行の名。
        if row["row"] == "actual":  # 実績は仮定に依らない1つの値なので、橙の菱形にする。
            x = x_of(tight)  # 菱形の中央。
            parts.append(f'<path d="M{x:.1f},{y - 12} L{x + 12:.1f},{y} L{x:.1f},{y + 12} L{x - 12:.1f},{y} z" fill="{ORANGE}" stroke="#ffffff" stroke-width="2"/>')  # 菱形。
        else:  # 模擬は、逼迫を塗りの丸、余裕を白抜きの輪にし、線で結ぶ。
            parts.append(f'<line x1="{x_of(tight):.1f}" y1="{y}" x2="{x_of(loose):.1f}" y2="{y}" stroke="{BLUE}" stroke-width="3"/>')  # 2つの仮定を結ぶ線。
            parts.append(f'<circle cx="{x_of(loose):.1f}" cy="{y}" r="12" fill="#ffffff" stroke="{BLUE}" stroke-width="3"/>')  # 余裕の仮定の輪。
            parts.append(f'<circle cx="{x_of(tight):.1f}" cy="{y}" r="8" fill="{BLUE}" stroke="#ffffff" stroke-width="2"/>')  # 逼迫の仮定の丸。
        label_x = x_of(max(tight, loose)) + 22  # 値の字は、右の印の右に置く。
        parts.append(f'<text x="{label_x:.1f}" y="{y + 8}" font-size="22" fill="{INK}">{span(low, high, unit)}</text>')  # 値の字。
    return parts  # 描いた要素を返す。


def main() -> None:  # 表を読み、2段の図をSVGに書く。
    rows = read_rows()  # 比べた表の6行。
    parts = [  # 図の頭(背景・題・説明・凡例)。
        f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1000" viewBox="0 0 1200 1000" font-family="{FONT}">',  # SVGの根。
        '<rect width="1200" height="1000" fill="#ffffff"/>',  # 白背景。
        f'<text x="600" y="62" text-anchor="middle" font-size="40" font-weight="bold" fill="{INK}">配った枚数と、急ぎのcardの最長の待ち</text>',  # 題。
        f'<text x="600" y="108" text-anchor="middle" font-size="22" fill="{MUTED}">2026年10月10日(日本時間)の負荷。A〜Eは模擬、実績はAIの配り役が実際に配った値</text>',  # 説明。
        f'<circle cx="70" cy="172" r="8" fill="{BLUE}"/>',  # 凡例: 逼迫の丸。
        f'<text x="90" y="180" font-size="22" fill="{INK}">逼迫の仮定(利用枠 = 負荷の60%)</text>',  # 凡例の字。
        f'<circle cx="480" cy="172" r="12" fill="#ffffff" stroke="{BLUE}" stroke-width="3"/>',  # 凡例: 余裕の輪。
        f'<text x="504" y="180" font-size="22" fill="{INK}">余裕の仮定(利用枠 = 負荷の10倍)</text>',  # 凡例の字。
        f'<path d="M900,160 L912,172 L900,184 L888,172 z" fill="{ORANGE}"/>',  # 凡例: 実績の菱形。
        f'<text x="922" y="180" font-size="22" fill="{INK}">実績</text>',  # 凡例の字。
    ]
    for title, column, top_value, step, unit, baseline in PANELS:  # 上下の段を描く。
        parts.extend(panel(title, column, top_value, step, unit, baseline, rows))  # 段の要素を足す。
    parts.append(f'<text x="40" y="975" font-size="20" fill="{MUTED}">模擬の上限は50。配り役は10月10日の夕方から、同時に持たせる会話を20までに抑えていた。</text>')  # 注。
    parts.append("</svg>")  # 根を閉じる。
    OUTPUT.write_text("\n".join(parts) + "\n")  # SVGを書く。
    print(f"wrote {OUTPUT}")  # 書いた先を示す。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ描く。
    main()  # 表からSVGを書く。
