# The layered-architecture scene: the page's three layers (docs/12-patterns.md, "Layered Architecture"). The domain
# layer is a plain computation, the service layer asks for its database and cache, the API layer logs the request
# and keeps a failure of the service as a value with Try. Prints every effect the layers yield, in order, and
# the log and the answer for an order that exists (42) and one that does not (7).
from dataclasses import dataclass

from doeff_core_effects import ReadWriterLog
from doeff_core_effects.handlers import reader, state, try_handler, writer

from doeff import Ask, Tell, Try, WithObserve, do, run


@dataclass(frozen=True)
class Item:
    price: int
    quantity: int


@dataclass
class Order:
    id: int
    items: tuple
    total: float | None = None


class MemoryDatabase:
    def __init__(self, orders):
        self.orders = orders

    @do
    def get_order(self, order_id):
        if order_id not in self.orders:
            raise LookupError(f"no order {order_id}")
        return self.orders[order_id]

    @do
    def save_order(self, order):
        print(f"  database: saved order {order.id}")


class MemoryCache:
    @do
    def set(self, key, value, ttl):
        print(f"  cache: set {key} ttl={ttl}")


# Domain layer - pure business logic
@do
def calculate_order_total(items):
    subtotal = sum(item.price * item.quantity for item in items)
    tax = subtotal * 0.1
    return subtotal + tax


# Service layer - orchestration
@do
def order_service(order_id):
    db = yield Ask("database")
    cache = yield Ask("cache")
    order = yield db.get_order(order_id)
    total = yield calculate_order_total(order.items)
    order.total = total
    yield db.save_order(order)
    yield cache.set(f"order_{order_id}", order, ttl=300)
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
    return value, (yield ReadWriterLog())


def observe(effect):
    # The observer stands around the whole handler stack, so it also sees the effects the Try sub-program yields
    # inside try_handler; the Get and Put that writer itself uses are left out.
    name = type(effect).__name__
    if name == "Ask":
        print(f"  yielded: Ask({effect.key!r})")
    elif name in {"Tell", "WriterTellEffect", "Try"}:
        print(f"  yielded: {'Tell' if name == 'WriterTellEffect' else name}")


for order_id in [42, 7]:
    db = MemoryDatabase({42: Order(42, (Item(price=20, quantity=2), Item(price=5, quantity=2)))})
    print(f"get_order_handler({order_id}):")
    stack = state()(writer(try_handler(with_log(get_order_handler(order_id)))))
    value, log = run(reader(env={"database": db, "cache": MemoryCache()})(WithObserve(observe, stack)))
    print("  log:", log)
    print("  returns:", value)
