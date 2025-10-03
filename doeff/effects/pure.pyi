__all__: Any

def Pure(value: Any) -> Effect: ...

class PureEffect:
    value: Any
    def intercept(self, transform: Callable[Any, Effect | Program]) -> PureEffect: ...

