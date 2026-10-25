# The composition scene: @do programs compose by yielding programs inside @do (docs/11-kleisli-arrows.md,
# "Composition via Nested @do"). The page's fetch_user_posts example, with fetch_user asking for the name prefix:
# the Ask yielded two @do calls deep travels out to the reader installed around the whole run. Prints, in order,
# where each body starts and returns, and the effects that got past reader (none: reader answered the Ask).
from doeff_core_effects.handlers import reader

from doeff import Ask, Pass, WithObserve, do, handler, run


@do
def fetch_user(user_id: int):
    print(f"  fetch_user({user_id}) starts")
    prefix = yield Ask("prefix")
    name = f"{prefix}-{user_id}"
    user = {"id": user_id, "name": name}
    print(f"  fetch_user returns {user}")
    return user


@do
def fetch_posts(user: dict):
    print("  fetch_posts(user) starts")
    posts = [f"post-for-{user['name']}"]
    print(f"  fetch_posts returns {posts}")
    return posts


@do
def fetch_user_posts(user_id: int):
    print(f"fetch_user_posts({user_id}) starts")
    user = yield fetch_user(user_id)
    posts = yield fetch_posts(user)
    print(f"fetch_user_posts returns {posts}")
    return posts


passed_reader = []


@handler
def outside_reader(effect, k):
    """Notes each effect that got past reader, and passes it on."""
    passed_reader.append(effect)
    yield Pass(effect, k)


def seen(effect):
    print(f"  the program yields {type(effect).__name__}({effect.key!r})")


print("fetch_user_posts(7) has .map / .flat_map:", hasattr(fetch_user_posts(7), "map"), hasattr(fetch_user_posts(7), "flat_map"))
answer = run(outside_reader(reader(env={"prefix": "user"})(WithObserve(seen, fetch_user_posts(7)))))
print("answer:", answer)
print("effects that got past reader:", passed_reader)
