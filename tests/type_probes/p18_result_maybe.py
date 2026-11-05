# Types of Ok / Err / Some / Nothing / Maybe (gap 5 — fixed)
from typing import assert_type

from doeff import Err, Maybe, Nothing, Ok, Some

o = Ok(1)
assert_type(o, Ok[int])
assert_type(o.value, int)
e = Err(ValueError("x"))
assert_type(e.error, ValueError)
s = Some(1)
assert_type(s, Some[int])
assert_type(s.value, int)
m: Maybe[int] = Nothing
