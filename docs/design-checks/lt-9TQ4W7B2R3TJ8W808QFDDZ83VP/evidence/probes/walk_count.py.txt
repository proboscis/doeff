"""負荷に左右されない数: test_emulated_screen の読み込みで、照らしの回数・歩みの回数・覚えの確かめの stat の回数・code の読みの回数・
読み込みの覚えを開いた回数を数える。"""
import hy  # noqa
import doeff_hy  # noqa
from doeff_hy_bytecode_guard import import_scope, macro_use

counts = {"checks": 0, "walks": 0, "file_state": 0, "facts": 0, "scopes": 0}


def counting(owner, name, key):
    original = getattr(owner, name)

    def counted(*args, **kwargs):
        counts[key] += 1
        return original(*args, **kwargs)

    setattr(owner, name, counted)


counting(macro_use, "record_is_current", "checks")
counting(macro_use, "_file_state", "file_state")
counting(macro_use, "_constant_text", "facts")
init = macro_use._Walker.__init__


def walker_init(self, *args):
    counts["walks"] += 1
    init(self, *args)


macro_use._Walker.__init__ = walker_init
scope_init = import_scope.ImportScope.__init__


def counted_scope(self):
    counts["scopes"] += 1
    scope_init(self)


import_scope.ImportScope.__init__ = counted_scope
import importlib  # noqa: E402

importlib.import_module("controllers.agora_sim.tests.test_emulated_screen")
print(counts)
