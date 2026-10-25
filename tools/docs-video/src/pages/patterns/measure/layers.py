# The layered-architecture scene: the page's layered example (docs/12-patterns.md, "Layered Architecture") under its
# test stack. The domain layer is a plain function, the service layer yields GetOrder, SaveOrder and CachePut, the API
# layer logs the request and keeps a failure of the service as a value with Try; in_memory_orders and
# in_memory_cache_handler give the effects their meaning. Prints every effect the layers yield, in order, and the log
# and the answer for an order that exists (7) and one that does not (8).
from dataclasses import dataclass, replace

from doeff_core_effects import ReadWriterLog, Tell
from doeff_core_effects.cache_effects import CachePut
from doeff_core_effects.cache_handlers import in_memory_cache_handler
from doeff_core_effects.handlers import await_handler, state, try_handler, writer
from doeff_core_effects.scheduler import scheduled

from doeff import EffectBase, Resume, Try, WithObserve, do, handler, run, with_handlers


@dataclass(frozen=True)
class Item:
    price: float
    quantity: int


@dataclass(frozen=True)
class Order:
    id: int
    items: tuple[Item, ...]
    total: float = 0.0


# Effects - what the service layer asks the infrastructure for
@dataclass(frozen=True)
class GetOrder(EffectBase):
    order_id: int


@dataclass(frozen=True)
class SaveOrder(EffectBase):
    order: Order


# Domain layer - pure business logic
def calculate_order_total(items):
    subtotal = sum(item.price * item.quantity for item in items)
    return round(subtotal * 1.1, 2)


# Service layer - orchestration, by effects only
@do
def order_service(order_id):
    order = yield GetOrder(order_id)
    order = replace(order, total=calculate_order_total(order.items))
    yield SaveOrder(order)
    yield CachePut(f"order_{order_id}", order, ttl=300)
    return order


# API layer - external interface
@do
def get_order_handler(order_id):
    yield Tell(f"Request: GET /orders/{order_id}")
    result = yield Try(order_service(order_id))
    response = result.value.total if result.is_ok() else {"error": repr(result.error)}
    yield Tell(f"Response: {response}")
    return response


# Infrastructure layer - the test handler of the order effects
def in_memory_orders(orders):
    @handler
    def on_order(effect: GetOrder | SaveOrder, k):
        if isinstance(effect, GetOrder):
            return (yield Resume(k, orders[effect.order_id]))
        orders[effect.order.id] = effect.order
        print(f"  in_memory_orders: saved order {effect.order.id}, total {effect.order.total}")
        return (yield Resume(k, None))

    return on_order


@do
def with_log(program):
    response = yield program
    return response, (yield ReadWriterLog())


def observe(effect):
    # The observer stands around the whole handler stack, so it also sees the effects the Try sub-program yields
    # inside try_handler; the effects the handlers themselves use (Get, Put, awaits) are left out.
    name = type(effect).__name__
    if name == "GetOrder":
        print(f"  yielded: GetOrder({effect.order_id})")
    elif name == "SaveOrder":
        print("  yielded: SaveOrder")
    elif name in {"CachePut", "CachePutEffect"}:
        print(f"  yielded: CachePut({effect.key!r})")
    elif name in {"Tell", "WriterTellEffect", "Try", "TryEffect"}:
        print(f"  yielded: {'Tell' if 'Tell' in name else 'Try'}")


orders = {7: Order(7, (Item(100.0, 1),))}
for order_id in [7, 8]:
    print(f"get_order_handler({order_id}):")
    stack = [state(), writer, try_handler, await_handler(), in_memory_cache_handler(), in_memory_orders(orders)]
    response, log = run(scheduled(WithObserve(observe, with_handlers(stack, with_log(get_order_handler(order_id))))))
    print("  log:", log)
    print("  returns:", response)
