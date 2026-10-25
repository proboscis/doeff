# Ok and Err are plain values: they print, test as true or false, and match by pattern.
# Backs the Ok/Err scene of the error-handling video.
from doeff import Err, Ok

for result in [Ok(5), Err(ValueError("boom"))]:
    match result:
        case Ok(value=v):
            matched = f"Ok(value={v!r})"
        case Err(error=e):
            matched = f"Err(error={e!r})"
    print(repr(result), "bool:", bool(result), "match:", matched)
