"""doeff_hy.wire が pydantic を読まずに解き・書き・JSON Schema を出す事の検(card ki-b7701d5fc0dd)。

wire.hy の 5 つの API(parse・parse-json・dump・dump-json・json-schema)は自前の解き手(doeff_hy/wire_codec.py)を呼び、pydantic を
import しない。defwire の展開(record.hy)が置く __pydantic_config__ も pydantic を読まずに作る設定の辞書。照らしの相手の pydantic は
doeff_hy.wire_parity だけが読む(この検は読まない)。job の土台の閉包は doeff-cluster の test_job_base_imports_no_pydantic.py が確かめる。
"""

import subprocess
import sys
import textwrap


def test_the_wire_api_imports_no_pydantic_and_works_without_it() -> None:
    script = textwrap.dedent(
        """
        import sys
        import hy
        import doeff_hy
        from doeff import run
        from doeff_hy import wire

        module = hy.I.types.ModuleType("probe_rows")
        sys.modules["probe_rows"] = module
        hy.eval(
            hy.read_many(
                "(require doeff-hy.record [defwire])\\n"
                "(import dataclasses [dataclass])\\n"
                "(defwire Row {:names :camel :unknown :reject} (#^ str lane-id) (setv #^ int n 0))"
            ),
            module=module,
        )
        Row = module.Row
        row = run(wire.parse_json(Row, '{"laneId":"L","n":2}'))
        assert row == Row(lane_id="L", n=2), row
        assert run(wire.parse(Row, {"laneId": "L"})) == Row(lane_id="L"), "parse"
        refused = run(wire.parse_json(Row, '{"laneId":1,"zz":0}'))
        assert isinstance(refused, wire.Malformed), refused
        assert run(wire.dump(row)) == {"laneId": "L", "n": 2}
        assert run(wire.dump_json(row)) == '{"laneId":"L","n":2}'
        assert list(run(wire.json_schema(Row))["properties"]) == ["laneId", "n"]
        assert Row.__pydantic_config__["extra"] == "forbid"
        assert run(wire.parse_as(dict[str, int], {"n": 1})) == {"n": 1}
        assert run(wire.parse_json_as(tuple[Row, ...], '[{"laneId":"L"}]')) == (Row(lane_id="L"),)
        loaded = sorted(name for name in sys.modules if name.split(".")[0] in ("pydantic", "pydantic_core"))
        assert not loaded, loaded
        print("ok")
        """
    )
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120, check=False)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"
