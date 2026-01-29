# Test file for doeff-linter

import os
from dataclasses import dataclass


# DOEFF001: Builtin shadowing
def dict():  # Should trigger DOEFF001
    return {}


# DOEFF002: Mutable attribute naming
class Counter:
    def __init__(self):
        self.count = 0

    def increment(self):
        self.count += 1  # Should trigger DOEFF002 - not using mut_ prefix


# DOEFF004: No os.environ access
def get_api_key():
    return os.environ["API_KEY"]  # Should trigger DOEFF004


# DOEFF005: No setter methods
class Config:
    def __init__(self):
        self._value = None

    def set_value(self, value):  # Should trigger DOEFF005
        self._value = value


# DOEFF006: No tuple returns
def get_user_info() -> tuple[str, int]:  # Should trigger DOEFF006
    return ("Alice", 25)


# DOEFF007: No mutable argument mutations
def process_items(items):
    items.append("new")  # Should trigger DOEFF007
    return items


# DOEFF008: No dataclass attribute mutation
@dataclass
class Person:
    name: str
    age: int


def update_person(person: Person):
    person.name = "Bob"  # Should trigger DOEFF008


# DOEFF009: Missing return type annotation
def add(a: int, b: int):  # Should trigger DOEFF009
    return a + b


# Good code - should NOT trigger any rules
class GoodCounter:
    def __init__(self):
        self.mut_count = 0  # Properly named mutable attribute

    def increment(self):
        self.mut_count += 1  # OK - properly named


def good_function(x: int) -> int:  # Has return type
    return x * 2


def process_items_good(items: list) -> list:  # Returns new list
    return items + ["new"]



