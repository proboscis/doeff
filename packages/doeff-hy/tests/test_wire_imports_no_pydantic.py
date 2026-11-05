"""Check that doeff_hy._wire decodes, encodes, and emits JSON Schema without importing pydantic.

The five APIs of wire.hy (parse, parse-json, dump, dump-json, json-schema) call the in-house decoder (doeff_hy/_wire_codec.py) and do not
import pydantic. The __pydantic_config__ that the defwire expansion (record.hy) emits is also a config dict built without importing pydantic. The comparison counterpart, pydantic, is
imported only by doeff_hy._wire_parity (this check does not import it). The closure of the job base is checked by test_job_base_imports_no_pydantic.py in doeff-cluster.
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
        from doeff_hy import _wire as wire

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
