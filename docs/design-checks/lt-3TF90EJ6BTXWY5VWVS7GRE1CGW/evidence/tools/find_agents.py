# /// script
# requires-python = ">=3.11"
# ///
"""会話の記録(jsonl)から Agent tool の呼びと結果の要点(識別子・所要・tool の呼び数・要求した model/effort)を取り出す。"""
import json
import sys

path = sys.argv[1]
calls = {}
for line in open(path):
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        continue
    message = row.get("message") or {}
    content = message.get("content")
    if not isinstance(content, list):
        continue
    for part in content:
        if part.get("type") == "tool_use" and part.get("name") == "Agent":
            calls[part["id"]] = {"at": row.get("timestamp"), "input": {k: (v if k != "prompt" else v[:300] + "…") for k, v in part["input"].items()}}
        if part.get("type") == "tool_result" and part.get("tool_use_id") in calls:
            result = part.get("content")
            text = result if isinstance(result, str) else "\n".join(x.get("text", "") for x in result if isinstance(x, dict))
            calls[part["tool_use_id"]]["result_tail"] = text[-700:]
            calls[part["tool_use_id"]]["result_at"] = row.get("timestamp")
            calls[part["tool_use_id"]]["tool_use_result"] = row.get("toolUseResult") if isinstance(row.get("toolUseResult"), dict) else None
for call_id, info in calls.items():
    print("=" * 20, call_id)
    print(json.dumps({k: v for k, v in info.items() if k != "tool_use_result"}, ensure_ascii=False, indent=1))
    tur = info.get("tool_use_result")
    if tur:
        print("toolUseResult:", json.dumps({k: v for k, v in tur.items() if k != "content"}, ensure_ascii=False))
