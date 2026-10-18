"""マージキューの記事の2枚(mergequeue-flow.svg・mergequeue-verify.svg)を、本文の数と記録の表から描く。

使い方: リポジトリの根で `uv run --no-sync python publications/zenn-use-cases-v0/visuals/mergequeue_svg.py`。
- flow: 本文の2つ目の枠と同じ8件の山(75秒おき)を、517秒の門と5秒の門で並べた時刻表。
- verify: ../reviews/mergequeue-verify-runs.csv の42回の結末を、日(行)× 刻(列)のマス目にする。
色は dataviz の既定の 1 番(門の間)と状態の色(赤・判じられない・緑)。状態の色には必ず記号と文字を添える。
"""

import csv  # 検証の結末の表を読む。
from pathlib import Path  # 表と出力の場所を決める。

HERE = Path(__file__).resolve().parent  # visuals/。
TABLE = HERE.parent / "reviews" / "mergequeue-verify-runs.csv"  # 4時間ごとの検証の結末。
FONT = 'font-family="IPAPGothic, IPAGothic, sans-serif"'  # 他の図と同じ日本語の字形。
INK, MUTED, FAINT = "#111827", "#4b5563", "#d1d5db"  # 文字・弱い文字・待ちの帯と罫線。
GATE = "#2a78d6"  # 門で確かめている間(dataviz の既定の 1 番)。
RED, WARN, GOOD = "#d03b3b", "#fab219", "#0ca30c"  # 状態の色(赤・判じられない・緑)。


def burst(seconds: int) -> list[tuple[int, int, int]]:  # 本文の2つ目の枠と同じ山の、(到着・門の始め・門の終わり)。
    rows, free = [], 0  # 各件の刻と、門が空く刻。
    for at in (i * 75 for i in range(8)):  # 75秒おきに8件。
        start = max(free, at)  # 前の件が済むまで待つ。
        free = start + seconds  # 門の時間だけ進む。
        rows = [*rows, (at, start, free)]  # 刻の組を足した列を作る。
    return rows  # 8件の刻。


def flow_svg() -> str:  # 時刻表の図を描く。
    left, right, minutes = 230, 1140, 70  # 横軸の左端・右端・分の幅。
    x = lambda second: left + (right - left) * second / (minutes * 60)  # noqa: E731 — 秒を横の位置へ。
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1060" viewBox="0 0 1200 1060" {FONT}>',
             '<rect width="1200" height="1060" fill="#ffffff"/>',
             f'<text x="600" y="62" text-anchor="middle" font-size="38" font-weight="bold" fill="{INK}">'
             '同じ8件を、517秒の門と5秒の門で流す</text>',
             f'<text x="600" y="104" text-anchor="middle" font-size="24" fill="{MUTED}">'
             '10分の間に、8件が75秒おきに届いた時の待ち(仮想の時計で流した)</text>']
    for panel, (seconds, title, top) in enumerate([(517, "517秒の門(テストを走らせる)", 150),
                                                     (5, "5秒の門(衝突の印だけを見る)", 590)]):
        rows = burst(seconds)  # この門の刻。
        parts.append(f'<text x="40" y="{top}" font-size="28" font-weight="bold" fill="{INK}">{title}</text>')
        for minute in range(0, minutes + 1, 10):  # 10分ごとの目盛りと縦の罫線。
            parts.append(f'<line x1="{x(minute * 60):.1f}" y1="{top + 20}" x2="{x(minute * 60):.1f}" y2="{top + 340}" '
                         f'stroke="{FAINT}" stroke-width="1"/>')
            parts.append(f'<text x="{x(minute * 60):.1f}" y="{top + 368}" text-anchor="middle" font-size="20" '
                         f'fill="{MUTED}">{minute}分</text>')
        for index, (at, start, end) in enumerate(rows):  # 1件ずつ、待ちの帯と門の帯を描く。
            y = top + 34 + index * 38  # この件の行の上端。
            parts.append(f'<text x="{left - 16}" y="{y + 22}" text-anchor="end" font-size="20" fill="{MUTED}">'
                         f'{index + 1}件目</text>')
            if start > at:  # 待っている間の帯。
                parts.append(f'<rect x="{x(at):.1f}" y="{y + 6}" width="{x(start) - x(at):.1f}" height="16" rx="4" '
                             f'fill="{FAINT}"/>')
            width = max(4.0, x(end) - x(start))  # 5秒の帯も見えるよう、最小4px。
            parts.append(f'<rect x="{x(start):.1f}" y="{y}" width="{width:.1f}" height="28" rx="4" fill="{GATE}"/>')
            parts.append(f'<path d="M{x(at) - 7:.1f},{y - 4} L{x(at) + 7:.1f},{y - 4} L{x(at):.1f},{y + 6} z" fill="{INK}"/>')
        last = rows[-1]  # 最後の件の待ちに、値を添える。
        label = (f"最後の1件は {last[2] - last[0]:,}秒(約{round((last[2] - last[0]) / 60)}分)待つ" if panel == 0
                 else "どの件も、届いてから5秒で入る")
        # 517秒の門は、帯の無い右上の空きに札を置く。5秒の門は、最後の件の右に置く。
        anchor_x, label_y, anchor = ((right, top + 56, "end") if panel == 0
                                     else (x(rows[-1][0]) + 30, top + 34 + 7 * 38 + 22, "start"))
        parts.append(f'<text x="{anchor_x:.1f}" y="{label_y}" text-anchor="{anchor}" font-size="24" '
                     f'font-weight="bold" fill="{INK}">{label}</text>')
    legend_y = 990  # 凡例の高さ。
    parts += [f'<path d="M60,{legend_y - 18} L76,{legend_y - 18} L68,{legend_y - 6} z" fill="{INK}"/>',
              f'<text x="86" y="{legend_y - 4}" font-size="22" fill="{INK}">届いた刻</text>',
              f'<rect x="230" y="{legend_y - 22}" width="40" height="16" rx="4" fill="{FAINT}"/>',
              f'<text x="280" y="{legend_y - 4}" font-size="22" fill="{INK}">待っている間</text>',
              f'<rect x="460" y="{legend_y - 26}" width="40" height="24" rx="4" fill="{GATE}"/>',
              f'<text x="510" y="{legend_y - 4}" font-size="22" fill="{INK}">門で確かめている間</text>',
              f'<text x="600" y="{legend_y + 46}" text-anchor="middle" font-size="22" fill="{MUTED}">'
              '10月16日に実際に届いた161件では、517秒の門で待ちの中央値2.3時間・最大7.2時間</text>',
              '</svg>']
    return "\n".join(parts) + "\n"  # 1枚のSVG。


CELLS = {  # 結末ごとの、塗り・文字の色・記号・名前。
    "red": (RED, "#ffffff", "×", "赤"),
    "indeterminate": (WARN, INK, "△", "判じられない"),
    "not-started": ("#f3f4f6", MUTED, "—", "始まらず"),
    "no-log": ("#f3f4f6", MUTED, "—", "記録なし"),
}


def verify_svg() -> str:  # 検証の結末のマス目の図を描く。
    with TABLE.open(newline="") as handle:  # 42回の結末を読む。
        runs = list(csv.DictReader(handle))
    days = sorted({run["started_utc"][:8] for run in runs})  # 行 = 日(UTC)。
    hours = sorted({run["started_utc"][9:] for run in runs})  # 列 = 始まった刻(UTC)。
    left, top, width, height = 200, 210, 156, 74  # マス目の左上と、マス1つの大きさ。
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="1060" viewBox="0 0 1200 1060" {FONT}>',
             '<rect width="1200" height="1060" fill="#ffffff"/>',
             f'<text x="600" y="62" text-anchor="middle" font-size="38" font-weight="bold" fill="{INK}">'
             '4時間ごとの全体の検証の結末(7日・42回)</text>',
             f'<text x="600" y="104" text-anchor="middle" font-size="24" fill="{MUTED}">'
             '2026年10月11〜17日(UTC)。結末が出た39回は、赤20回・判じられない19回で、緑は0回</text>',
             f'<text x="600" y="146" text-anchor="middle" font-size="22" fill="{MUTED}">'
             'マスの数は、その回で赤になった段の数(全部で25の段)</text>']
    for column, hour in enumerate(hours):  # 列の見出し。
        parts.append(f'<text x="{left + column * width + width / 2:.0f}" y="{top - 16}" text-anchor="middle" '
                     f'font-size="22" fill="{MUTED}">{int(hour)}時(UTC)</text>')
    for row, day in enumerate(days):  # 行の見出し。
        parts.append(f'<text x="{left - 16}" y="{top + row * height + height / 2 + 8:.0f}" text-anchor="end" '
                     f'font-size="22" fill="{MUTED}">{int(day[4:6])}月{int(day[6:])}日</text>')
    for run in runs:  # 1回ずつマスを塗る。
        row, column = days.index(run["started_utc"][:8]), hours.index(run["started_utc"][9:])
        fill, ink, mark, name = CELLS[run["outcome"]]  # この回の結末の見た目。
        cx, cy = left + column * width, top + row * height  # マスの左上。
        stroke = ' stroke="#9ca3af" stroke-width="2"' if fill == "#f3f4f6" else ""  # 灰のマスは縁を付ける。
        parts.append(f'<rect x="{cx + 3}" y="{cy + 3}" width="{width - 6}" height="{height - 6}" rx="8" fill="{fill}"{stroke}/>')
        text = f"{mark} {run['red_stages']}" if run["outcome"] in ("red", "indeterminate") else f"{mark} {name}"
        parts.append(f'<text x="{cx + width / 2:.0f}" y="{cy + height / 2 + 9:.0f}" text-anchor="middle" font-size="26" '
                     f'font-weight="bold" fill="{ink}">{text}</text>')
    counts = {key: sum(run["outcome"] == key for run in runs) for key in CELLS}  # 結末ごとの回数。
    legend = [(GOOD, "#ffffff", "○", f"緑(全部の段が通った) {0}回", ""),
              (RED, "#ffffff", "×", f"赤 {counts['red']}回", ""),
              (WARN, INK, "△", f"判じられない(走らなかった段がある) {counts['indeterminate']}回", ""),
              ("#f3f4f6", MUTED, "—", f"始まらず・記録なし {counts['not-started'] + counts['no-log']}回",
               ' stroke="#9ca3af" stroke-width="2"')]
    for index, (fill, ink, mark, label, stroke) in enumerate(legend):  # 凡例は記号と文字で、色だけに頼らない。
        y = top + len(days) * height + 50 + index * 44  # 凡例の行の高さ。
        parts.append(f'<rect x="200" y="{y - 26}" width="44" height="34" rx="6" fill="{fill}"{stroke}/>')
        parts.append(f'<text x="222" y="{y - 1}" text-anchor="middle" font-size="22" font-weight="bold" fill="{ink}">{mark}</text>')
        parts.append(f'<text x="260" y="{y}" font-size="22" fill="{INK}">{label}</text>')
    parts += [f'<text x="600" y="{top + len(days) * height + 270}" text-anchor="middle" font-size="24" font-weight="bold" '
              f'fill="{INK}">安定版のブランチ stable は、10月6日の版から進んでいない</text>',
              f'<text x="600" y="{top + len(days) * height + 306}" text-anchor="middle" font-size="22" fill="{MUTED}">'
              '10月18日に数えると、mainは stable より4,407 commit先にある</text>',
              '</svg>']
    return "\n".join(parts) + "\n"  # 1枚のSVG。


if __name__ == "__main__":  # ファイルを直接実行した場合だけ、2枚を書き出す。
    (HERE / "mergequeue-flow.svg").write_text(flow_svg())  # 時刻表の図。
    (HERE / "mergequeue-verify.svg").write_text(verify_svg())  # 検証の結末の図。
    print("wrote mergequeue-flow.svg mergequeue-verify.svg")  # 書いたことを示す。
