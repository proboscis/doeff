"""目視確認を終えたslugだけ、採用画像と記事の台帳へ反映する。"""

import hashlib
import json
import struct
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
ROOT = SOURCE.parents[1]
captions = json.loads((SOURCE / "visuals/captions.json").read_text())
manifest = {e["id"]: e for e in json.loads((SOURCE / "imagegen/manifest.json").read_text())}
for slug in sys.argv[1:]:
    spec = json.loads((SOURCE / f"reviews/{slug}-visuals.json").read_text())
    captions[slug].update({
        f"{kind}_{field}": spec[kind][field]
        for kind in ("concept", "flow")
        for field in ("title", "caption")
    })
    for kind in ("concept", "flow"):
        name = f"{slug}-{kind}"
        path = ROOT / f"images/zenn-use-cases-v0/generated/{name}.png"
        data = path.read_bytes()
        width, height = struct.unpack(">II", data[16:24])
        stem = Path(manifest[name]["generation_prompt"]).stem
        edits = sorted((SOURCE / "imagegen").glob(stem + "-edit*.txt"), key=lambda p: p.stat().st_mtime)
        manifest[name] = {
            "id": name,
            "image": str(path.relative_to(ROOT)),
            "generation_prompt": stem + ".txt",
            "edit_prompts": [p.name for p in edits],
            "tool": "image_gen (built-in)",
            "sha256": hashlib.sha256(data).hexdigest(),
            "width": width,
            "height": height,
            "bytes": len(data),
            "review": "2026-09-16: 精査後のコード・コメント・値・矢印を目視確認。白背景の平面図として採用。",
        }
(SOURCE / "visuals/captions.json").write_text(json.dumps(captions, ensure_ascii=False, indent=2) + "\n")
(SOURCE / "imagegen/manifest.json").write_text(
    json.dumps(sorted(manifest.values(), key=lambda e: e["id"]), ensure_ascii=False, indent=2) + "\n"
)
review = SOURCE / "reviews/README.md"
rows = []
for line in review.read_text().splitlines():
    updated_line = line
    if line.startswith("| ") and "review_" in line:
        cols = [part.strip() for part in line.split("|")[1:-1]]
        slug = cols[0]
        if (SOURCE / f"reviews/{slug}.md").is_file():
            cols[2] = "修正・検証済み"
        if slug in sys.argv[1:]:
            cols[3] = "更新・目視確認済み"
        updated_line = "| " + " | ".join(cols) + " |"
    rows.append(updated_line)
review.write_text("\n".join(rows) + "\n")
print("採用画像の台帳を更新:", ", ".join(sys.argv[1:]))
