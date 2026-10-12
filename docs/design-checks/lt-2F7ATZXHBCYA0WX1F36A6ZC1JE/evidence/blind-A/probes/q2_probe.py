"""Q2 の振る舞いの確かめ: child-environment が宣言(declared)・worker の文脈(extra)から来たこの名も外すか。"""
import hy  # noqa: F401
from doeff import run
from doeff_cluster.worker.core.launch import child_environment

name = "CLAUDE_" + "PROJECT_DIR"
print("declared から:", run(child_environment({"PATH": "/bin"}, {}, {name: "/work"}, {})))
print("extra から:", run(child_environment({"PATH": "/bin"}, {name: "/work"}, {}, {})))
