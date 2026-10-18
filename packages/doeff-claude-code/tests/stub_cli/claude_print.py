"""CLI の 1 回きりの print(`--output-format json --json-schema …`)の替え玉 — 本番の print の handler を API を撃たずに回すための CLI。

素の Python で書く(Hy の起動の 0.5〜1 秒を待ちの上限の検に入れない — 混んだ機体で上限より先に起動が終わらないと、検が見る物を
書く前に止められる)。実物(2.1.292・2026-10-19 の測り)と同じ形の 1 つの JSON の object(type result)を stdout に出して降りる:

- 成功 = rc 0・is_error 偽・structured_output(--json-schema の欄ごとの答え)と、その JSON の文の result・usage・modelUsage・
  total_cost_usd・duration_ms・duration_api_ms。
- API の誤り = rc 1・is_error 真・api_error_status(例 401)・result に誤りの文。
- 設定 dir(CLAUDE_CONFIG_DIR)に .credentials.json が無い = rc 1・is_error 真・api_error_status なし(実物の「Not logged in」の形)。

答えは --json-schema の欄の型で決める: number = NUMBER_ANSWER・object = 最初の欄に FIRST_OPTION_ANSWER、残りの欄に 1 から引いた残りを等分。
標準入力の本文の印で筋を替える(印は本文のどこに在ってもよい): STUB:api-401 = API の誤り 401 / STUB:crash = stdout に何も出さず
stderr に 1 行・rc 2 / STUB:no-structured = 成功だが structured_output が無い / STUB:out-of-range = number の欄を 1.5 で答える /
STUB:hang = 降ろされるまで待つ(信号を待つ — 時間で起きない)。

env STUB_PRINT_RECORD(file の path)が在れば、見た物(argv・cwd・設定 dir・その .credentials.json の access token と権限・
MAX_THINKING_TOKENS・本文の長さ・pid)を JSON で書く — 検が handler の渡し方を確かめるため。
"""

import json
import os
import signal
import stat
import sys

NUMBER_ANSWER = 0.8125
FIRST_OPTION_ANSWER = 0.625
FLAGS_WITH_VALUE = {"--output-format", "--json-schema", "--model", "--system-prompt", "--tools", "--setting-sources", "--effort"}


def read_flags(argv: list[str]) -> dict[str, object]:
    """argv の旗 → 値の表(値を取らない旗は True)— 答えの Schema と model を読むため。"""
    flags: dict[str, object] = {}
    at = 0
    while at < len(argv):
        name = argv[at]
        if name in FLAGS_WITH_VALUE:
            flags[name] = argv[at + 1]
            at += 2
        else:
            flags[name] = True
            at += 1
    return flags


def answer_for(schema: dict, text: str) -> dict[str, object]:
    """答えの Schema の欄ごとの答えを、頭の註の規則で作るため。"""
    number = 1.5 if "STUB:out-of-range" in text else NUMBER_ANSWER

    def field_answer(field: dict) -> object:
        if field.get("type") != "object":
            return number
        options = list(field.get("properties", {}))
        rest = (1 - FIRST_OPTION_ANSWER) / max(len(options) - 1, 1)
        return {option: FIRST_OPTION_ANSWER if at == 0 else rest for at, option in enumerate(options)}

    return {name: field_answer(field) for name, field in schema.get("properties", {}).items()}


def credential_seen(config_dir: str) -> dict[str, object] | None:
    """設定 dir の .credentials.json の access token と権限を読むため(無ければ None — 実物の「Not logged in」の筋)。"""
    path = os.path.join(config_dir, ".credentials.json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as stream:
        oauth = json.load(stream)["claudeAiOauth"]
    return {"access_token": oauth["accessToken"], "refresh_token": oauth.get("refreshToken"),
            "expires_at": oauth.get("expiresAt"), "mode": stat.S_IMODE(os.stat(path).st_mode)}


def result_line(**fields: object) -> dict[str, object]:
    """実物の result の行の形を作るため(欄は実物の名のまま)。"""
    return {"type": "result", "subtype": "success", "is_error": False, "num_turns": 1, "duration_ms": 12,
            "duration_api_ms": 7, "total_cost_usd": 0.0, "usage": {"input_tokens": 0, "output_tokens": 0},
            "modelUsage": {}, "result": "", **fields}


def main() -> int:
    """替え玉の CLI の入口(頭の註の筋で答えて終了 code を返す)。"""
    flags = read_flags(sys.argv[1:])
    text = sys.stdin.read()
    model = str(flags.get("--model", ""))
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR", "")
    credential = credential_seen(config_dir) if config_dir else None
    record = os.environ.get("STUB_PRINT_RECORD")
    if record:
        with open(record, "w", encoding="utf-8") as stream:
            json.dump({"argv": sys.argv[1:], "cwd": os.getcwd(), "config_dir": config_dir, "credential": credential,
                       "max_thinking_tokens": os.environ.get("MAX_THINKING_TOKENS"), "input_chars": len(text),
                       "pid": os.getpid()}, stream)
    if "STUB:hang" in text:
        while True:
            signal.pause()
    if "STUB:crash" in text:
        print("stub crash", file=sys.stderr)
        return 2
    if credential is None:
        print(json.dumps(result_line(is_error=True, result="Not logged in · Please run /login")))
        return 1
    if "STUB:api-401" in text:
        print(json.dumps(result_line(is_error=True, api_error_status=401,
                                     result="Failed to authenticate. API Error: 401 OAuth access token is invalid.")))
        return 1
    answer = answer_for(json.loads(str(flags["--json-schema"])), text)
    line = result_line(result=json.dumps(answer), total_cost_usd=0.0125,
                       usage={"input_tokens": 1000, "cache_creation_input_tokens": 200, "cache_read_input_tokens": 300,
                              "output_tokens": 50},
                       modelUsage={model: {"inputTokens": 1000, "outputTokens": 50, "contextWindow": 200000,
                                           "maxOutputTokens": 32000}})
    if "STUB:no-structured" not in text:
        line["structured_output"] = answer
    print(json.dumps(line))
    return 0


if __name__ == "__main__":
    sys.exit(main())
