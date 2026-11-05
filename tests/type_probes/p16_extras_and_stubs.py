# Environment with only the 4 core extras: public modules written in Hy also expose their names via .pyi (gap 8 -- fixed). At runtime extra hy is required (MissingExtraError)
from doeff_core_effects import PathStat
from doeff_core_effects._offloaded_call import offloaded
from doeff_core_effects import pooled_postgres_sql_handler  # gap-6 want-clean[pyright] (some types unknown under strict)
from doeff_core_effects._python_bytecode import compiled_pyc
from doeff_core_effects._sql_transaction import transaction_scope  # gap-6 want-clean[pyright] (some types unknown under strict)
from doeff_core_effects._sql_transaction import Transaction  # expect-error (no such name)

__all__ = ["PathStat", "Transaction", "compiled_pyc", "offloaded", "pooled_postgres_sql_handler",  # noqa: DOEFF021 -- the type-sample file intentionally has __all__
           "transaction_scope"]
