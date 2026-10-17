# Stand-ins for real infrastructure. They shout when called so a test can see a leak.
from dataclasses import dataclass

CALLS: list[str] = []


@dataclass(frozen=True)
class User:
    name: str
    email: str


def load_user(user_id: int) -> User:
    CALLS.append(f"REAL load_user({user_id})")
    return User("Real Person", "real@example.com")


def send_mail(to: str, body: str) -> None:
    CALLS.append(f"REAL send_mail({to!r})")
