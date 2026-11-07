"""Parts that the expansions of defrecord and defeffect read at load time.

defrecord writes the functions (__init__, __repr__, __eq__, __hash__, and the frozen __setattr__ / __delattr__) at expansion time and lets dataclass build only
the table of fields (see the header note of doeff_hy/record.hy). The class body the expansion wrote and the statements right after the class is built read this module.

This module is a leaf module that imports only the standard library. Modules inside doeff_hy (outcome_forms and others) also use defrecord and
are loaded while doeff_hy.record is being loaded, so putting what the expansion reads when it builds a class into doeff_hy.record would make a circular import.
"""

import dataclasses
import reprlib
import types

#: The errors raised by the frozen __setattr__ / __delattr__ the expansion writes (same type as dataclass, so callers' except FrozenInstanceError keeps working as before).
FrozenInstanceError = dataclasses.FrozenInstanceError


class _FactoryDefault:
    """Marker for an omitted argument of a field with a default factory (the default of the __init__ the expansion writes; the signature text is ``<factory>``, same as dataclass)."""

    def __repr__(self) -> str:
        return "<factory>"


#: Default value of the argument for a default-factory field in the __init__ the expansion writes (the factory is called only when it is omitted).
FACTORY_DEFAULT = _FactoryDefault()


@dataclasses.dataclass(frozen=True, kw_only=True)
class _ReferenceRecord:
    """A sample with the same __dataclass_params__ as the previous expansion (dataclass frozen, kw_only); built only to copy its value."""


#: __dataclass_params__ of a record whose functions the expansion wrote: restores the value of the call that keeps dataclass from building functions to the same value as the previous expansion
#: (frozen, eq, init, repr true; kw_only).
RECORD_PARAMS: object = getattr(_ReferenceRecord, "__dataclass_params__")

@dataclasses.dataclass(frozen=True)
class _ReferenceEffect:
    """A sample with the same __dataclass_params__ as the previous expansion of defeffect (dataclass frozen, positional arguments)."""


#: __dataclass_params__ of an effect type whose functions the expansion wrote: restores the value to the same as the previous expansion (frozen, eq, init, repr true; kw_only false).
EFFECT_PARAMS: object = getattr(_ReferenceEffect, "__dataclass_params__")

#: Recursion guard for the __repr__ the expansion writes (same as dataclass's __repr__: a value that contains itself prints as ``...``).
recursive_repr = reprlib.recursive_repr


# --- Methods shared between types ----------------------------------------------------------------------
# __repr__ and the frozen __setattr__ / __delattr__ are not written per type: all defrecord types point at this one set from the class body.
# Only the sequence of field names differs per type, and the expansion puts it in the class body as a literal tuple: __doeff_fields__ (all fields — the freeze guard) and
# __doeff_repr_fields__. When each type carried its own functions (code objects, the __annotate__ of annotations, and so on), a job with about 1,760 types exceeded 10 MB.
# __eq__ and __hash__ are called every time a value goes into a dict key or a set, so they stay per type (see the note in record.hy).
# Behavior is the same as standard dataclass (frozen, kw_only). The type name is read from the type of self, not from a name (so a type written in a class body works the same).


class _RecordDoc:
    """Builds the __doc__ of a defrecord type as ``Name(*, a, b)`` (field names) when read — describes types that do not put a docstring in the runtime expansion.

    A descriptor placed in the type dict's __doc__ has __get__ called when the type's __doc__ is read. Since the type holds no string, a per-type string
    does not stay in memory. For a type without a docstring, dataclass computes the signature text at load time (inspect.signature), but this descriptor
    returns the real text, so it does not compute it. The docstring that was written remains in the source and the type-check projection."""

    def __get__(self, instance: object, owner: type | None = None) -> str:
        cls = owner if owner is not None else type(instance)
        names = _names(cls, "__doeff_fields__")
        return f"{cls.__qualname__}(*, {', '.join(names)})" if names else f"{cls.__qualname__}()"


#: Shared descriptor that the defrecord expansion puts in the class body's __doc__.
RECORD_DOC = _RecordDoc()


def _names(cls: type, attr: str) -> tuple[str, ...]:
    """The tuple of field names the expansion put in the class body (attr = __doeff_fields__ and others)."""
    names: tuple[str, ...] = getattr(cls, attr)
    return names


def _frozen_owner(cls: type) -> type:
    """The type with the freeze guard (the first type in the MRO that has __doeff_fields__ in its own dict) — the starting point of super when writing a value of a child type that has no guard."""
    for klass in cls.__mro__:
        if "__doeff_fields__" in vars(klass):
            return klass
    raise TypeError(f"{cls.__qualname__} is not a defrecord / defeffect type (it has no __doeff_fields__)")


@reprlib.recursive_repr()
def record_repr(self: object) -> str:
    """``Name(a=1, b='x')`` — the same text as dataclass's __repr__ (the type's __qualname__, and the fields shown in repr in the order written)."""
    cls = self.__class__
    return f"{cls.__qualname__}(" + ", ".join([f"{name}={getattr(self, name)!r}" for name in _names(cls, "__doeff_repr_fields__")]) + ")"


def record_setattr(self: object, name: str, value: object) -> None:
    """Frozen: a value of the type itself refuses every name; a value of a child type without a guard refuses only field names (same as dataclass frozen)."""
    cls = type(self)
    if "__doeff_fields__" in vars(cls) or name in _names(cls, "__doeff_fields__"):
        raise FrozenInstanceError(f"cannot assign to field {name!r}")
    super(_frozen_owner(cls), self).__setattr__(name, value)


def record_delattr(self: object, name: str) -> None:
    """Frozen __delattr__ (same guard as record_setattr)."""
    cls = type(self)
    if "__doeff_fields__" in vars(cls) or name in _names(cls, "__doeff_fields__"):
        raise FrozenInstanceError(f"cannot delete field {name!r}")
    super(_frozen_owner(cls), self).__delattr__(name)


def record_setstate(self: object, state: object) -> None:
    """For pickle and copy to restore a value (sets without going through the frozen __setattr__ — the same role as __setstate__ of dataclass frozen with slots).
    state has the shape of object.__getstate__: a slots-only type is ``(None, {field: value})``; a child type with __dict__ is ``(dict, {field: value})``.
    It also accepts the state of a value pickled before slots were used (a ``{field: value}`` dict).
    A field the state lacks (the value was pickled before the field was added to the type) gets the field's default or default factory, the same value
    a value without slots read from the class attribute before; a field without a default stays unset. Without this, a stored value of the older shape
    raises AttributeError on the first read of the new field (agora-controllers' landed-declare job read a stored report without ready_ms and pin_asks)."""
    parts = state if isinstance(state, tuple) else (state, None)
    for part in parts:
        if part:
            for name, value in part.items():
                object.__setattr__(self, name, value)
    fields: dict[str, dataclasses.Field[object]] = getattr(type(self), "__dataclass_fields__", {})
    for name, f in fields.items():
        if not hasattr(self, name):
            if f.default is not dataclasses.MISSING:
                object.__setattr__(self, name, f.default)
            elif f.default_factory is not dataclasses.MISSING:
                object.__setattr__(self, name, f.default_factory())


def _put_default(fields: dict[str, dataclasses.Field[object]], name: str, value: object) -> None:
    """Put the default the class body wrote for one field into the field table, the way dataclass reads a class attribute default.

    The defrecord expansion declares the slots in the class body (so dataclass does not rebuild the class), and a slot cannot share its name with a class
    attribute, so the body binds the default to a name of its own and dataclass sees a field without a default. A field marker (``(field …)``) replaces the
    table's field and takes its name, type, and kind (keyword-only unless the marker says otherwise); a plain value becomes the field's default. A mutable default
    (a value whose type is not hashable) is refused with the same error as dataclass."""
    made = fields[name]
    if isinstance(value, dataclasses.Field):
        value.name = made.name
        value.type = made.type
        setattr(value, "_field_type", getattr(made, "_field_type"))
        if value.kw_only is dataclasses.MISSING:
            value.kw_only = made.kw_only
        default = value.default
        fields[name] = value
    else:
        made.default = value
        default = value
    if default is not dataclasses.MISSING and type(default).__hash__ is None:
        raise ValueError(f"mutable default {type(default)} for field {name} is not allowed: use default_factory")


def seal_record(cls: type, plain: tuple[str, ...], calls_post_init: bool) -> None:
    """Finish a record whose functions the expansion wrote: restore __dataclass_params__ to the same value as the previous expansion, and check that the defaults of the plain fields the expansion read as literal values
    are not field markers (a Field with a factory, init, repr, compare, or hash) (the expansion cannot read a field passed through a name and the functions
    would take a different shape — write ``(field …)`` directly in the field). Refuse when the type has __post_init__ but the expansion's __init__ does not call it (calls_post_init is
    false) — if the expansion overlooks the body's own __post_init__, input checks silently stop working (regression of doeff 424a87f58)."""
    if "__post_init__" in vars(cls) and not calls_post_init:
        raise TypeError(f"The expansion's __init__ does not call __post_init__ of {cls.__name__} — the defrecord expansion overlooked the body's own function")
    fields: dict[str, dataclasses.Field[object]] = getattr(cls, "__dataclass_fields__")
    defaults: tuple[tuple[str, object], ...] = vars(cls).get("__doeff_defaults__", ())
    if defaults:
        delattr(cls, "__doeff_defaults__")
    for name, value in defaults:
        _put_default(fields, name, value)
    for name in plain:
        f = fields[name]
        if f.default_factory is not dataclasses.MISSING or not f.init or not f.repr or not f.compare or f.hash is not None:
            raise TypeError(f"The default of field {name} of {cls.__name__} is a field marker — write (field …) directly in a defrecord field")
    setattr(cls, "__dataclass_params__", RECORD_PARAMS)


def seal_effect(cls: type, names: tuple[str, ...], calls_post_init: bool) -> None:
    """Finish a defeffect effect type whose functions the expansion wrote: restore __dataclass_params__ to the same value as the previous expansion (frozen dataclass, positional
    arguments). The expansion reads field types and defaults literally, so refuse at load time when the dataclass field table differs from
    the shape the expansion read (the same hole as the 424a87f58 regression — the expansion's functions must not silently set different values):
    the type has __post_init__ but the expansion's __init__ does not call it; the sequence of field names (names) differs (a ClassVar, InitVar, or KW_ONLY type);
    a default is a field marker passed through a name (it has a factory, init, repr, compare, or hash, or the default of the expansion's __init__ differs from the field default)."""
    if "__post_init__" in vars(cls) and not calls_post_init:
        raise TypeError(f"The expansion's __init__ does not call __post_init__ of {cls.__name__} — the defeffect expansion overlooked :pre")
    fields = dataclasses.fields(cls)
    if tuple(f.name for f in fields) != names:
        raise TypeError(
            f"Fields {tuple(f.name for f in fields)} of {cls.__name__} differ from the fields the expansion read {names} — "
            "do not write ClassVar, InitVar, or KW_ONLY types in the :fields types"
        )
    init = vars(cls)["__init__"]
    if not isinstance(init, types.FunctionType):
        raise TypeError(f"__init__ of {cls.__name__} is not a function the expansion wrote: {init!r}")
    for f in fields:
        if f.default_factory is not dataclasses.MISSING or not f.init or not f.repr or not f.compare or f.hash is not None or f.kw_only:
            raise TypeError(f"The default of field {f.name} of {cls.__name__} is a field marker — write (field …) directly in a defeffect field")
    declared = tuple(f.default for f in fields if f.default is not dataclasses.MISSING)
    written = init.__defaults__ or ()
    if len(declared) != len(written) or any(d is not w for d, w in zip(declared, written)):
        raise TypeError(f"A field default of {cls.__name__} contains a field marker passed through a name — write (field …) directly in a defeffect field")
    setattr(cls, "__dataclass_params__", EFFECT_PARAMS)
