"""検の振る舞いは変えず、走った session で読まれた doeff_traverse.handlers の file と sequential の引数を記す。"""
import inspect
import sys


def pytest_sessionfinish(session, exitstatus):
    mod = sys.modules.get("doeff_traverse.handlers")
    with open("/tmp/blind-b/probe-loaded.txt", "w", encoding="utf-8") as out:
        out.write(f"handlers: {getattr(mod, '__file__', None)}\n")
        if mod is not None:
            out.write(f"sequential signature: {inspect.signature(mod.sequential)}\n")
