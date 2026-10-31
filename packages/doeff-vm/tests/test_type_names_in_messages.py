"""A message names a doeff_vm class by its bare name, as it names a class written in Python.

Python's messages read the C-level name of a type (tp_name), which pyo3 builds from the pyclass
``module`` — so ``x + 1`` with ``x = child()`` ended with ``unsupported operand type(s) for +:
'doeff_vm.doeff_vm.Call' and 'int'``. ``__module__`` and ``__qualname__``
stay: pickle and the stored type names (cache keys, memo) read them.
"""

import doeff_vm.doeff_vm as extension
import pytest

EXTENSION_TYPES = sorted(
    (value for value in vars(extension).values() if isinstance(value, type)),
    key=lambda cls: cls.__name__,
)


@pytest.mark.parametrize("cls", EXTENSION_TYPES, ids=lambda cls: cls.__name__)
def test_a_type_error_names_the_class_without_its_module(cls: type) -> None:
    # object.__new__ refuses a class that has its own constructor with a message that names the
    # class by its tp_name — the name every other message about it uses.
    with pytest.raises(TypeError) as info:
        object.__new__(cls)
    message = str(info.value)
    assert f"{cls.__module__}.{cls.__name__}" not in message
    assert cls.__name__ in message


def test_the_classes_users_hold_name_themselves_in_an_operator_error() -> None:
    from doeff_vm import Err, Ok, Pure

    for value, name in ((Ok(1), "Ok"), (Err(ValueError("x")), "Err"), (Pure(1), "Pure")):
        with pytest.raises(TypeError) as info:
            value + 1
        assert str(info.value) == f"unsupported operand type(s) for +: '{name}' and 'int'"


def test_the_module_path_pickle_reads_stays() -> None:
    from doeff_vm import Call, Ok

    assert (Ok.__module__, Ok.__qualname__) == ("doeff_vm.doeff_vm", "Ok")
    assert (Call.__module__, Call.__qualname__) == ("doeff_vm.doeff_vm", "Call")
