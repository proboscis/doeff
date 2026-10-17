import sys


def pytest_sessionfinish(session, exitstatus):
    for name in ("doeff_cluster.worker.protocol.env_translation", "doeff_cluster.worker.core.env_prepare",
                 "doeff_cluster.worker.intent.env_prepare_model", "doeff_cluster.worker.protocol.env_store"):
        mod = sys.modules.get(name)
        print(f"\n[origin] {name}: {getattr(mod, '__file__', None) if mod else 'not imported'}"
              f" ShareBytecode={'ShareBytecode' in dir(mod) if mod else '-'}")
