# 核の 4 個だけの環境: Hy で書いた公開の module も .pyi で名が分かる(抜け 8 — 直った)。実行時は extra hy が要る(MissingExtraError)
from doeff_core_effects.file_effects import PathStat
from doeff_core_effects._offloaded_call import offloaded
from doeff_core_effects.pooled_postgres_sql import pooled_postgres_sql_handler  # gap-6 want-clean[pyright] (strict で型が一部分からない)
from doeff_core_effects._python_bytecode import compiled_pyc
from doeff_core_effects.sql_transaction import transaction_scope  # gap-6 want-clean[pyright] (strict で型が一部分からない)
from doeff_core_effects.sql_transaction import Transaction  # expect-error (無い名)

__all__ = ["PathStat", "Transaction", "compiled_pyc", "offloaded", "pooled_postgres_sql_handler",  # noqa: DOEFF021 — 型の見本の file が __all__ を意図して持つ
           "transaction_scope"]
