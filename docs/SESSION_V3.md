# Session v3 / `LBoxdSession`

`LBoxdSession` is now the public session object for `lboxdlib`. It is exposed from:

```python
from lboxdlib import LBoxdSession
from lboxdlib.auth import LBoxdSession
```

It remains compatible with existing helpers:

```python
from lboxdlib.auth import ensure_session, get_csrf, get_signed_in_user

session = ensure_session()
csrf = get_csrf(session)
username = get_signed_in_user(session)
```

`ensure_session()` now returns an authenticated `LBoxdSession`. The wrapper delegates HTTP methods and attributes to its underlying `curl_cffi.requests.Session`, so existing code that calls `session.get(...)`, `session.post(...)`, `session.patch(...)`, or uses `session.cookies` should continue to work.

For finer control, construct it directly:

```python
from pathlib import Path
from lboxdlib import LBoxdSession

session = LBoxdSession(
    json_path=Path("~/.lb_session.json").expanduser(),
    cookies=cookies,
)

print(session.username)
print(session.csrf)
```

`cookies` may be either the JSON-safe cookie-record list produced by `session_to_cookie_list()` or a simple name/value mapping such as `session.cookies.get_dict()`.

For code that explicitly requires the underlying curl-cffi object, use:

```python
raw = session.raw
# or
from lboxdlib.auth import ensure_raw_session
raw = ensure_raw_session()
```
