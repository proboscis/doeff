"""Q1 の振る舞いの確かめ: 宣言の検め(EnvVar・coordinator の environ-refusal)がこの名を断るか。"""
import hy  # noqa: F401
from doeff_cluster.shared.intent.runtime_env_model import EnvVar, RuntimeEnvInvalid
from doeff_cluster.coordinator.core.cluster_policy import environ_refusal

try:
    EnvVar(name="CLAUDE_PROJECT_DIR", value="/work")
    print("EnvVar: 受けた")
except RuntimeEnvInvalid as error:
    print("EnvVar: 断った", error.kind, error)
print("environ-refusal:", environ_refusal({"CLAUDE_PROJECT_DIR": "/work"}, []))
print("environ-refusal (ほかの名):", environ_refusal({"MODE": "x"}, []))
