"""旧版の参考DOTからSVGとPNGを生成する。採用中のimagegen画像は変更しない。"""

from __future__ import annotations

import subprocess
from pathlib import Path


def main() -> None:
    source_dir = Path(__file__).resolve().parent / "visuals"
    image_dir = source_dir.parents[2] / "images" / "zenn-use-cases-v0"
    image_dir.mkdir(parents=True, exist_ok=True)
    sources = sorted(source_dir.glob("*.dot"))
    if not sources:
        raise ValueError("DOT原稿がありません")
    for source in sources:
        svg = source.with_suffix(".svg")
        png = image_dir / source.with_suffix(".png").name
        subprocess.run(["dot", "-Tsvg", str(source), "-o", str(svg)], check=True)
        subprocess.run(
            ["rsvg-convert", "--width", "1200", str(svg), "--output", str(png)], check=True
        )
        if png.stat().st_size > 3_000_000:
            raise ValueError(f"Zennの画像サイズ上限を超えています: {png}")
    print(f"{len(sources)}図のSVGとPNGを生成しました: {image_dir}")


if __name__ == "__main__":
    main()
