print("value:", repr(value))
print("program:", type(program).__name__)
print("interpreter:", interpreter.__module__ + "." + interpreter.__name__)
print("injected:", sorted(k for k in globals() if not k.startswith("__")))
