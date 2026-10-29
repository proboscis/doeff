from typing import TYPE_CHECKING

import pydantic  # fires
import pydantic.fields  # fires
from pydantic import TypeAdapter  # fires
from pydantic import BaseModel, ValidationError  # fires
from pydantic_core import core_schema  # fires
from pydantic_core.core_schema import SerializationInfo  # fires
import pydantic_settings
import json

if TYPE_CHECKING:
    from pydantic import GetCoreSchemaHandler


def read(text):
    import pydantic_core  # fires
    return json.loads(text)


class Opaque:
    @classmethod
    def __get_pydantic_core_schema__(cls, source, handler):
        from pydantic_core import core_schema as schema

        return schema.any_schema()
