from importlib import import_module
import hy  # noqa: F401


# Names of the submodules behind `_` (ADR-DOE-API-001 R5), read on first use: the root does not import
# them while it loads. `X as X` shows each name to type checkers. A root that already has `__all__`
# lists the names there too; the tool does not add `__all__` to a root without one (DOEFF021).
_LAZY_EXPORTS: dict[str, str] = {
    "Copy": "doeff_docker._effects",
    "DockerBuild": "doeff_docker._effects",
    "DockerRun": "doeff_docker._effects",
    "Expose": "doeff_docker._effects",
    "From": "doeff_docker._effects",
    "ImagePush": "doeff_docker._effects",
    "Run": "doeff_docker._effects",
    "SetEnv": "doeff_docker._effects",
    "ShellRun": "doeff_docker._effects",
    "ShellRunResult": "doeff_docker._effects",
    "Workdir": "doeff_docker._effects",
    "docker_build_handler": "doeff_docker._handlers.docker",
    "dockerfile_collector_handler": "doeff_docker._handlers.dockerfile",
    "image_push_handler": "doeff_docker._handlers.docker",
    "shell_run_handler": "doeff_docker._handlers.shell",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_EXPORTS.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_LAZY_EXPORTS})
