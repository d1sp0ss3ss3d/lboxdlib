"""
Core Letterboxd session management using curl_cffi.

This module intentionally preserves the working surface of the original
lb_session.py while adding server-safe cookie helpers and a public LBoxdSession
wrapper object.

Responsibilities:
- create curl_cffi sessions with the selected browser impersonation
- perform Letterboxd login
- serialize/deserialize Letterboxd cookies
- load/save cookie JSON for CLI use
- extract signed-in username and CSRF token
- expose LBoxdSession as a drop-in session-like object with convenience
  properties for signed-in user and CSRF
- keep get_signed_in_user()/get_csrf() safe for both raw curl sessions and
  LBoxdSession wrappers

It does not know about FastAPI, local server users, encrypted storage, or any
higher-level Letterboxd feature modules.

NOTE: Let curl-cffi set browser-like headers. Attempting to override too much
header state is very likely to make Letterboxd/Cloudflare requests fail.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

from curl_cffi import requests


COOKIE_PATH = Path("~/.lb_session.json").expanduser()
BASE_URL = "https://letterboxd.com"
SIGNIN_URL = f"{BASE_URL}/sign-in/"
LOGIN_POST_URL = f"{BASE_URL}/user/login.do"
ANONYMOUS_PRIME_URL = SIGNIN_URL
IMPERSONATE = "chrome"
DEFAULT_COOKIE_DOMAIN = ".letterboxd.com"
DEFAULT_COOKIE_PATH = "/"

CookieRecord = dict[str, Any]
CookieInput = Iterable[CookieRecord] | Mapping[str, str]


class LBoxdSession:
    """
    Session-like Letterboxd client backed by curl_cffi.requests.Session.

    LBoxdSession is a thin wrapper/proxy. Existing library code can continue to
    call session.get(...), session.post(...), session.patch(...), session.cookies,
    etc. Unknown attributes are delegated to the wrapped curl_cffi Session.

    An authenticated LBoxdSession is only valid if it has both:
    - a signed-in Letterboxd username cookie
    - a CSRF cookie

    Pass anonymous=True to create a permanently unauthenticated client. This
    skips cookie-file lookup and credential prompting entirely.

    Call set_anonymous() on an authenticated instance to temporarily route
    requests through a separate anonymous curl_cffi Session. That isolated
    session may retain anonymous Letterboxd/Cloudflare cookies acquired while
    priming, but it never inherits the authenticated session's cookies, default
    headers, or HTTP authentication. The authenticated session remains intact
    and can be restored with set_anonymous(False).

    Args:
        json_path:
            Plaintext cookie JSON path for local CLI/REPL use.
        cookies:
            Optional cookies used to construct the session immediately. This may
            be either the JSON-safe list produced by session_to_cookie_list() or
            a simple name/value mapping such as session.cookies.get_dict(). If
            supplied, the cookies are written to json_path after validation.
        auto:
            If True and json_path does not contain a valid session, prompt for
            username/password and perform login. If False, raise instead.
        username:
            Optional Letterboxd username. If supplied with password and no valid
            local cookie session is available, login via lb_login() instead of
            prompting.
        password:
            Optional Letterboxd password. Must be supplied together with username.
        anonymous:
            If True, construct a permanently unauthenticated client without
            reading json_path or prompting for credentials. cookies, username,
            and password cannot be supplied in this mode. The anonymous HTTP
            session is primed lazily before its first request.
    """

    def __init__(
        self,
        json_path: Path | str = COOKIE_PATH,
        cookies: CookieInput | None = None,
        *,
        auto: bool = True,
        username: str | None = None,
        password: str | None = None,
        anonymous: bool = False,
    ) -> None:
        self.json_path = Path(json_path).expanduser()
        self._session: requests.Session
        self._anonymous_session: requests.Session | None = None
        self._anonymous_primed = False
        self._has_authenticated_session = False
        self._signed_in_user: str | None = None
        self._csrf: str | None = None
        self._anonymous = bool(anonymous)

        if anonymous:
            if cookies is not None or username is not None or password is not None:
                raise ValueError(
                    "cookies, username, and password cannot be supplied when "
                    "anonymous=True"
                )

            # Deliberately do not inspect json_path. This empty session becomes
            # the isolated anonymous cookie jar used for priming and requests.
            self._session = new_session()
            self._anonymous_session = self._session
            return

        if cookies is not None:
            self._session = load_lb_session_from_cookie_input(cookies)
            self._refresh_identity()
            self.save()
            return

        last_error: Exception | None = None

        if self.json_path.exists():
            self._session = load_lb_session_from_cookies(self.json_path)
            try:
                self._refresh_identity()
                return
            except RuntimeError as exc:
                last_error = exc
                if not auto and username is None and password is None:
                    raise

        if username is not None or password is not None:
            if not username or not password:
                raise ValueError(
                    "Both username and password must be supplied together."
                )

            self._session = lb_login(username, password, cookie_path=self.json_path)
            self._refresh_identity()
            return

        if not auto:
            if last_error is not None:
                raise RuntimeError(
                    f"No valid Letterboxd session found at {self.json_path}"
                ) from last_error

            raise FileNotFoundError(
                f"No valid Letterboxd session found at {self.json_path}"
            )

        self._session = prompt_login(self.json_path)
        self._refresh_identity()

    def __getattr__(self, name: str) -> Any:
        """Delegate unknown attributes/methods to the active curl_cffi session."""
        return getattr(self._active_session(), name)

    def __enter__(self) -> LBoxdSession:
        if hasattr(self._session, "__enter__"):
            self._session.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> Any:
        self.close()
        return None

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(username={self.username!r})"

    @property
    def anonymous(self) -> bool:
        """Return True when requests are being sent without session identity."""
        return self._anonymous

    @property
    def is_anonymous(self) -> bool:
        """Convenience alias for anonymous."""
        return self._anonymous

    @property
    def authenticated(self) -> bool:
        """Return True when authenticated routing is currently active."""
        return self._has_authenticated_session and not self._anonymous

    @property
    def has_authenticated_session(self) -> bool:
        """Return True if an authenticated session is available to restore."""
        return self._has_authenticated_session

    def _ensure_anonymous_session(self) -> requests.Session:
        """Create the isolated anonymous session on first use."""
        if self._anonymous_session is None:
            self._anonymous_session = new_session()
        return self._anonymous_session

    def _active_session(self) -> requests.Session:
        """Return the session currently used by delegated operations."""
        if self._anonymous:
            return self._ensure_anonymous_session()
        return self._session

    def set_anonymous(self, enabled: bool = True) -> LBoxdSession:
        """
        Enable or disable anonymous request routing.

        While enabled, request(), get(), post(), head(), and the other standard
        HTTP verb helpers use an isolated anonymous curl_cffi Session. It may
        keep cookies acquired anonymously during priming, but it never receives
        cookies, default headers, or HTTP auth from the authenticated session.
        Cookie and Authorization values supplied directly to a call are also
        removed so anonymous mode fails closed.

        The wrapped authenticated session is not altered. Call
        set_anonymous(False) to resume authenticated requests.
        """
        enabled = bool(enabled)
        if not enabled and not self._has_authenticated_session:
            raise RuntimeError(
                "Cannot disable anonymous mode: no authenticated session is loaded"
            )

        if enabled:
            self._ensure_anonymous_session()

        self._anonymous = enabled
        return self

    def prime_anonymous(self, *, force: bool = False) -> LBoxdSession:
        """
        Prime the isolated anonymous session with Letterboxd.

        Priming visits the sign-in page without account cookies. This allows
        Letterboxd/Cloudflare to establish any anonymous cookies required by
        later public requests. It happens automatically before the first
        anonymous request; callers may use force=True to refresh the anonymous
        cookie jar after a later 403 response.
        """
        if self._anonymous_primed and not force:
            return self

        if force:
            self._anonymous_primed = False

        session = self._ensure_anonymous_session()
        response = session.get(ANONYMOUS_PRIME_URL, allow_redirects=True)
        response.raise_for_status()
        self._anonymous_primed = True
        return self

    def _anonymous_request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Perform a request through the isolated, primed anonymous session."""
        # Do not allow call-specific credentials to defeat anonymous mode.
        kwargs.pop("auth", None)
        kwargs.pop("cookies", None)

        headers = kwargs.get("headers")
        if headers is not None:
            anonymous_headers = requests.Headers(headers)
            anonymous_headers.pop("Cookie", None)
            anonymous_headers.pop("Authorization", None)
            kwargs["headers"] = anonymous_headers

        # Match new_session()'s browser fingerprint. Unlike the authenticated
        # jar, anonymous cookies are intentionally retained so Cloudflare and
        # Letterboxd priming can carry forward to the requested page.
        kwargs.setdefault("impersonate", IMPERSONATE)
        self.prime_anonymous()
        return self._ensure_anonymous_session().request(method, url, **kwargs)

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        """Send a request through either anonymous or authenticated routing."""
        if self._anonymous:
            return self._anonymous_request(method, url, **kwargs)
        return self._session.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("GET", url, **kwargs)
        return self._session.get(url, **kwargs)

    def options(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("OPTIONS", url, **kwargs)
        return self._session.options(url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("HEAD", url, **kwargs)
        return self._session.head(url, **kwargs)

    def post(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("POST", url, **kwargs)
        return self._session.post(url, **kwargs)

    def put(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("PUT", url, **kwargs)
        return self._session.put(url, **kwargs)

    def patch(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("PATCH", url, **kwargs)
        return self._session.patch(url, **kwargs)

    def delete(self, url: str, **kwargs: Any) -> Any:
        if self._anonymous:
            return self._anonymous_request("DELETE", url, **kwargs)
        return self._session.delete(url, **kwargs)

    def close(self) -> None:
        """Close both authenticated and isolated anonymous sessions."""
        if (
            self._anonymous_session is not None
            and self._anonymous_session is not self._session
        ):
            self._anonymous_session.close()
        self._session.close()

    @property
    def raw(self) -> requests.Session:
        """Return the currently active underlying curl_cffi Session."""
        return self._active_session()

    @property
    def signed_in_user(self) -> str | None:
        """Return the active username, or None while routing anonymously."""
        if self._anonymous:
            return None
        return self._signed_in_user

    @property
    def username(self) -> str | None:
        """Alias for signed_in_user."""
        return self.signed_in_user

    @property
    def csrf(self) -> str | None:
        """Return the CSRF token belonging to the currently active session."""
        if self._anonymous:
            return self._ensure_anonymous_session().cookies.get(
                "com.xk72.webparts.csrf"
            )
        return self._csrf

    def _refresh_identity(self) -> None:
        """Refresh cached convenience properties, raising if the session is invalid."""
        self._signed_in_user = get_signed_in_user(self._session)
        self._csrf = get_csrf(self._session)
        self._has_authenticated_session = True

    def refresh(self) -> LBoxdSession:
        """Refresh username/csrf convenience properties from current cookies."""
        if not self._has_authenticated_session:
            raise RuntimeError("No authenticated Letterboxd session to refresh")
        self._refresh_identity()
        return self

    def save(self, path: Path | str | None = None) -> None:
        """Serialize and save this session's cookies."""
        if not self._has_authenticated_session:
            raise RuntimeError("No authenticated Letterboxd session to save")
        save_lb_session_cookies(self._session, self.json_path if path is None else path)

    def to_cookie_list(self) -> list[CookieRecord]:
        """Return JSON-safe cookie records for this session."""
        if not self._has_authenticated_session:
            raise RuntimeError("No authenticated Letterboxd session to serialize")
        return session_to_cookie_list(self._session)

    @classmethod
    def from_session(
        cls,
        session: requests.Session,
        json_path: Path | str = COOKIE_PATH,
        *,
        save: bool = False,
    ) -> LBoxdSession:
        """
        Wrap an existing curl_cffi.requests.Session.

        The supplied session must already be authenticated. Construction raises
        RuntimeError if the session does not contain the signed-in username cookie
        and CSRF cookie required by LBoxdSession.

        Args:
            session:
                Existing authenticated curl_cffi.requests.Session.
            json_path:
                Cookie JSON path associated with this wrapper.
            save:
                If True, serialize the supplied session's cookies to json_path.
        """
        obj = cls.__new__(cls)
        obj.json_path = Path(json_path).expanduser()
        obj._session = session
        obj._anonymous_session = None
        obj._anonymous_primed = False
        obj._has_authenticated_session = False
        obj._signed_in_user = None
        obj._csrf = None
        obj._anonymous = False
        obj._refresh_identity()
        if save:
            obj.save()
        return obj

    @classmethod
    def from_raw_session(
        cls,
        session: requests.Session,
        json_path: Path | str = COOKIE_PATH,
        *,
        save: bool = False,
    ) -> LBoxdSession:
        """
        Backwards-compatible alias for from_session().
        """
        return cls.from_session(session, json_path=json_path, save=save)


# Backwards-compatible spelling for user code that imports `LboxdSession`.
LboxdSession = LBoxdSession


def new_session() -> requests.Session:
    """Create a new curl_cffi session using the project's impersonation mode."""
    return requests.Session(impersonate=IMPERSONATE)


# Backwards/convenience alias. Some callers reasonably look for this name when
# importing this module as `session`.
def lb_session() -> requests.Session:
    """Alias for new_session()."""
    return new_session()


def is_logged_in_by_cookie(session: requests.Session) -> bool:
    """Check if the session contains Letterboxd authentication cookies."""
    cookie_names = {c.name for c in session.cookies.jar}
    return any(
        name.startswith("letterboxd.user") for name in cookie_names
    ) or "letterboxd.signed.in.as" in cookie_names


def session_to_cookie_list(session: requests.Session) -> list[CookieRecord]:
    """Serialize a curl_cffi session's cookies to JSON-safe records."""
    cookies: list[CookieRecord] = []

    for c in session.cookies.jar:
        cookies.append({
            "name": c.name,
            "value": c.value,
            "domain": c.domain,
            "path": c.path,
            "secure": bool(c.secure),
        })

    return cookies


def load_lb_session_from_cookie_list(cookies: Iterable[CookieRecord]) -> requests.Session:
    """Build a curl_cffi session from JSON-safe cookie records."""
    session = new_session()

    for c in cookies:
        # Keep this compatible with cookie records produced by the original
        # lb_session.py. Avoid inventing extra fields or headers here.
        session.cookies.set(**c)

    return session


def load_lb_session_from_cookie_mapping(cookies: Mapping[str, str]) -> requests.Session:
    """
    Build a curl_cffi session from a simple name/value cookie mapping.

    Prefer load_lb_session_from_cookie_list() when possible because it preserves
    domain/path/secure metadata. This helper exists for callers that only have a
    simple curl_cffi/requests-style cookie dict.
    """
    session = new_session()

    for name, value in cookies.items():
        session.cookies.set(
            name,
            value,
            domain=DEFAULT_COOKIE_DOMAIN,
            path=DEFAULT_COOKIE_PATH,
        )

    return session


def load_lb_session_from_cookie_input(cookies: CookieInput) -> requests.Session:
    """Build a curl_cffi session from either cookie records or a cookie mapping."""
    if isinstance(cookies, Mapping):
        return load_lb_session_from_cookie_mapping(cookies)
    return load_lb_session_from_cookie_list(cookies)


def save_cookie_list(cookies: list[CookieRecord], path: Path | str = COOKIE_PATH) -> None:
    """Write cookie records to a plaintext JSON file for CLI/local use."""
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cookies, f, indent=2)


def load_cookie_list(path: Path | str = COOKIE_PATH) -> list[CookieRecord]:
    """Read cookie records from a plaintext JSON file."""
    path = Path(path).expanduser()
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def save_lb_session_cookies(session: requests.Session, path: Path | str = COOKIE_PATH) -> None:
    """Serialize and save a session's cookies to a plaintext JSON file."""
    save_cookie_list(session_to_cookie_list(session), path)


def load_lb_session_from_cookies(path: Path | str = COOKIE_PATH) -> requests.Session:
    """Load a session from a previously saved cookie JSON file."""
    return load_lb_session_from_cookie_list(load_cookie_list(path))


def lb_login(
    username: str,
    password: str,
    cookie_path: Path | str | None = COOKIE_PATH,
    verbose=False
) -> requests.Session:
    """
    Perform a fresh Letterboxd login and return an authenticated raw session.

    This preserves the original lb_session.py behavior by default: cookies are
    written to COOKIE_PATH unless a different cookie_path is supplied. Server
    code should pass cookie_path=None and store session_to_cookie_list(session)
    encrypted instead.
    """
    session = new_session()

    # Get sign-in page; this sets the CSRF cookie.
    session.get(SIGNIN_URL, allow_redirects=True).raise_for_status()

    csrf = session.cookies.get("com.xk72.webparts.csrf")
    if not csrf:
        raise RuntimeError("Missing CSRF cookie after GET /sign-in/")

    form = {
        "__csrf": csrf,
        "username": username,
        "password": password,
        "remember": "true",
    }

    login_headers = {
        "Origin": BASE_URL,
        "Referer": SIGNIN_URL,
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }

    login_resp = session.post(
        LOGIN_POST_URL,
        data=form,
        #headers=login_headers,
        allow_redirects=True,
    )

    login_resp.raise_for_status()

    try:
        login_result = login_resp.json()
    except ValueError:
        login_result = None

    if isinstance(login_result, dict) and login_result.get("result") == "error":
        messages = login_result.get("messages") or []
        if isinstance(messages, str):
            detail = messages
        elif isinstance(messages, (list, tuple)):
            detail = "; ".join(str(message) for message in messages)
        else:
            detail = str(messages)
        raise RuntimeError(f"Letterboxd login failed: {detail or 'Unknown error'}")

    # Validate login by accessing a protected page.
    # session.get(f"{BASE_URL}/activity/", allow_redirects=True).raise_for_status()

    if not is_logged_in_by_cookie(session):
        raise RuntimeError("Login failed – authentication cookies not set")

    # LBoxdSession requires this stricter signed-in-user check, so make login
    # enforce it too. This catches sessions that have auth-looking cookies but
    # are missing the convenience identity cookie required by the public object.
    get_signed_in_user(session)
    get_csrf(session)

    if cookie_path is not None:
        save_lb_session_cookies(session, cookie_path)
        
    if verbose:
        print("login status:", login_resp.status_code)
        print("login URL:", login_resp.url)
        print("cookie names:", sorted(c.name for c in session.cookies.jar))
        print("response preview:", login_resp.text[:300])

    return session


def lb_login_to_cookie_list(username: str, password: str) -> tuple[requests.Session, list[CookieRecord]]:
    """Login without writing plaintext cookies and return session + cookie records."""
    session = lb_login(username, password, cookie_path=None)
    return session, session_to_cookie_list(session)


def prompt_login(cookie_path: Path | str = COOKIE_PATH) -> requests.Session:
    """Prompt for Letterboxd credentials, login, save cookies, and return raw session."""
    username = input("Letterboxd username: ").strip()
    password = getpass.getpass("Letterboxd password: ")
    return lb_login(username, password, cookie_path)


def ensure_session(
    cookie_path: Path | str = COOKIE_PATH,
    auto: bool = True,
    *,
    username: str | None = None,
    password: str | None = None,
    anonymous: bool = False,
) -> LBoxdSession:
    """
    Return an authenticated or permanently anonymous LBoxdSession.

    Behavior:
    - If anonymous=True, do not inspect cookie_path or prompt for credentials.
    - If a valid cookie file exists, load it.
    - Otherwise, if username and password are supplied, login via lb_login().
    - Otherwise, when auto=True, prompt for username/password.
    - Otherwise, raise.

    This preserves the CLI/REPL convenience path while allowing non-interactive
    callers to pass credentials directly.
    """
    cookie_path = Path(cookie_path).expanduser()

    if anonymous:
        if username is not None or password is not None:
            raise ValueError(
                "username and password cannot be supplied when anonymous=True"
            )
        return LBoxdSession(json_path=cookie_path, anonymous=True)

    last_error: Exception | None = None

    if cookie_path.exists():
        try:
            return LBoxdSession(json_path=cookie_path, auto=False)
        except RuntimeError as exc:
            last_error = exc

    if username is not None or password is not None:
        if not username or not password:
            raise ValueError("Both username and password must be supplied together.")

        raw = lb_login(username, password, cookie_path=cookie_path)
        return LBoxdSession.from_session(raw, json_path=cookie_path)

    if not auto:
        if last_error is not None:
            raise RuntimeError(
                f"No valid Letterboxd session found at {cookie_path}"
            ) from last_error

        raise FileNotFoundError(
            f"No valid Letterboxd session found at {cookie_path}"
        )

    raw = prompt_login(cookie_path)
    return LBoxdSession.from_session(raw, json_path=cookie_path)


def ensure_raw_session(
    cookie_path: Path | str = COOKIE_PATH,
    auto: bool = True,
    *,
    username: str | None = None,
    password: str | None = None,
    anonymous: bool = False,
) -> requests.Session:
    """
    Return the active authenticated or primed anonymous raw curl_cffi session.

    This helper is provided for code that truly requires the underlying type.
    Most callers should use ensure_session() and rely on LBoxdSession delegation.
    """
    wrapper = ensure_session(
        cookie_path=cookie_path,
        auto=auto,
        username=username,
        password=password,
        anonymous=anonymous,
    )
    if anonymous:
        wrapper.prime_anonymous()
    return wrapper.raw


def get_signed_in_user(session: requests.Session | LBoxdSession) -> str:
    """
    Return the logged-in Letterboxd username.

    For LBoxdSession, prefer the validated/cached convenience property. For raw
    curl_cffi sessions, read the cookie directly. This keeps the helper safe for
    both public wrapper usage and lower-level login/bootstrap code.
    """
    if isinstance(session, LBoxdSession):
        username = session.signed_in_user
        if username:
            return username
        raise RuntimeError("Not signed in")

    username = session.cookies.get("letterboxd.signed.in.as")
    if username:
        return username

    raise RuntimeError("Not signed in")


def get_csrf(session: requests.Session | LBoxdSession) -> str:
    """
    Return the Letterboxd CSRF token.

    For LBoxdSession, prefer the validated/cached convenience property. For raw
    curl_cffi sessions, read the cookie directly. This avoids duplicating wrapper
    state while preserving compatibility with raw-session internals.
    """
    if isinstance(session, LBoxdSession):
        csrf = session.csrf
        if csrf:
            return csrf
        raise RuntimeError("CSRF cookie not found in session")

    csrf = session.cookies.get("com.xk72.webparts.csrf")
    if csrf:
        return csrf

    raise RuntimeError("CSRF cookie not found in session")
