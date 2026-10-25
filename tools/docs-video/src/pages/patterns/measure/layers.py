# The layered-architecture scene: the page's three layers (docs/12-patterns.md, "Layered Architecture"). The domain
# layer is a plain computation, the service layer yields GetOrder, SaveOrder and CacheSet, the API layer logs the
# request and keeps a failure of the service as a value with Try. The in-memory handler gives the three effects
# their meaning and prints what it does. Prints every effect the layers yield, in order, and the log and the answer
# for an order that exists (42) and one that does not (7).
from dataclasses import dataclass

from doeff_core_effects.handlers import state, try_handler, writer, writer_log

from doeff import EffectBase, Pass, Resume, ResumeThrow, Tell, Try, WithObserve, do, handler, run


@dataclass(frozen=True)
class Item:
    price: int
    quantity: int


@dataclass
class Order:
    id: int
    items: tuple
    total: float | None = None


@dataclass(frozen=True)
class GetOrder(EffectBase):
    order_id: int


@dataclass(frozen=True)
class SaveOrder(EffectBase):
    order: Order


@dataclass(frozen=True)
class CacheSet(EffectBase):
    key: str
    value: object
    ttl: int


def in_memory_store(orders):
    # The test handler of the three effects: a dict for the database and one for the cache.
    cache = {}

    @handler
    def on_store(effect, k):
        if isinstance(effect, GetOrder):
            if effect.order_id not in orders:
                print(f"  in_memory_store: no order {effect.order_id}")
                return (yield ResumeThrow(k, LookupError(f"no order {effect.order_id}")))
            return (yield Resume(k, orders[effect.order_id]))
        if isinstance(effect, SaveOrder):
            orders[effect.order.id] = effect.order
            print(f"  in_memory_store: saved order {effect.order.id}")
            return (yield Resume(k, None))
        if isinstance(effect, CacheSet):
            cache[effect.key] = effect.value
            print(f"  in_memory_store: cache set {effect.key} ttl={effect.ttl}")
            return (yield Resume(k, None))
        yield Pass(effect, k)

    return on_store


# Domain layer - pure business logic
@do
def calculate_order_total(items):
    subtotal = sum(item.price * item.quantity for item in items)
    tax = subtotal * 0.1
    return subtotal + tax


# Service layer - orchestration
@do
def order_service(order_id):
    order = yield GetOrder(order_id)
    order.total = yield calculate_order_total(order.items)
    yield SaveOrder(order)
    yield CacheSet(f"order_{order_id}", order, ttl=300)
    return order


# API layer - external interface
@do
def get_order_handler(order_id):
    yield Tell(f"Request: GET /orders/{order_id}")
    result = yield Try(order_service(order_id))
    order = result.value if result.is_ok() else {"error": str(result.error)}
    yield Tell(f"Response: {order}")
    return order


@do
def with_log(program):
    value = yield program
    return value, (yield writer_log())


def observe(effect):
    # The observer stands around the whole handler stack, so it also sees the effects the Try sub-program yields
    # inside try_handler; the Get and Put that writer itself uses are left out.
    name = type(effect).__name__
    if name == "GetOrder":
        print(f"  yielded: GetOrder({effect.order_id})")
    elif name in {"SaveOrder", "CacheSet", "Tell", "WriterTellEffect", "Try"}:
        print(f"  yielded: {'Tell' if name == 'WriterTellEffect' else name}")


for order_id in [42, 7]:
    orders = {42: Order(42, (Item(price=20, quantity=2), Item(price=5, quantity=2)))}
    print(f"get_order_handler({order_id}):")
    stack = state()(writer(try_handler(with_log(get_order_handler(order_id)))))
    value, log = run(in_memory_store(orders)(WithObserve(observe, stack)))
    print("  log:", log)
    print("  returns:", value)
