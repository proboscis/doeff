# welcome.py
from dataclasses import dataclass

from doeff import EffectBase, do


@dataclass(frozen=True)
class LoadUser(EffectBase):
    user_id: int


@dataclass(frozen=True)
class SendMail(EffectBase):
    to: str
    body: str


@do
def welcome(user_id: int):
    user = yield LoadUser(user_id)
    body = f"Welcome, {user.name}!"
    yield SendMail(user.email, body)
    return body
