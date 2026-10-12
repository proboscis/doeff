# /// script
# requires-python = ">=3.11"
# ///
"""会話の記録から盲検 A・B の依頼文(prompt)を、加工せず file に書き出す。使い方: save_prompts.py <記録の jsonl> <出力 dir>"""
import json
import sys
from pathlib import Path

out = Path(sys.argv[2])
for line in open(sys.argv[1]):
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    content = (row.get("message") or {}).get("content")
    if not isinstance(content, list):
        continue
    for part in content:
        if part.get("type") == "tool_use" and part.get("name") == "Agent":
            description = part["input"].get("description", "")
            name = "blind-A-prompt.md" if description.startswith("盲検A") else "blind-B-prompt.md" if description.startswith("盲検B") else None
            if name:
                (out / name).write_text(part["input"]["prompt"] + "\n")
                print(name, len(part["input"]["prompt"]))
