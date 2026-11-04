# src/lboxdlib/actions/settings.py

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from lboxdlib.auth import (
    BASE_URL,
    DEFAULT_COOKIE_DOMAIN,
    DEFAULT_COOKIE_PATH,
    LBoxdSession,
)


CHANGE_PASSWORD_URL = f"{BASE_URL}/user/change-password.do"
AUTH_SETTINGS_URL = f"{BASE_URL}/settings/auth/"

EXPORT_DATA_URL = f"{BASE_URL}/data/export/"
EXPORT_SETTINGS_URL = f"{BASE_URL}/user/exportdata/"

UPDATE_USER_URL = f"{BASE_URL}/user/update.do"
PROFILE_SETTINGS_URL = f"{BASE_URL}/settings/"


@dataclass(frozen=True)
class ExportUserDataResult:
    path: Path
    filename: str
    size_bytes: int
    content_type: str


def _filename_from_content_disposition(value: str | None) -> str | None:
    if not value:
        return None

    message = Message()
    message["Content-Disposition"] = value
    return message.get_filename()


def export_user_data(
    session: LBoxdSession,
    destination: str | Path = ".",
    *,
    overwrite: bool = False,
) -> ExportUserDataResult:
    """
    Download the authenticated user's Letterboxd data export ZIP.

    Args:
        session:
            Authenticated LBoxdSession.
        destination:
            Directory in which to save the server-provided filename, or an
            explicit .zip path.
        overwrite:
            Whether an existing destination file may be replaced.

    Returns:
        Information about the downloaded export.
    """
    if not isinstance(session, LBoxdSession):
        raise TypeError("export_user_data() requires an authenticated LBoxdSession")

    response = session.get(
        EXPORT_DATA_URL,
        headers={
            "Referer": EXPORT_SETTINGS_URL,
        },
        allow_redirects=True,
    )
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "")
    content_disposition = response.headers.get("Content-Disposition")
    filename = _filename_from_content_disposition(content_disposition)

    # A redirected login or error page may still return HTTP 200, so do not
    # rely only on status code.
    if "application/zip" not in content_type.lower():
        preview = response.text[:300].replace("\n", " ")
        raise RuntimeError(
            "Letterboxd did not return a ZIP export. "
            f"Content-Type was {content_type!r}; response began {preview!r}"
        )

    if not filename:
        filename = f"letterboxd-{session.username}-export.zip"

    destination = Path(destination).expanduser()

    if destination.suffix.lower() == ".zip":
        output_path = destination
    else:
        output_path = destination / filename

    output_path.parent.mkdir(parents=True, exist_ok=True)

    if output_path.exists() and not overwrite:
        raise FileExistsError(
            f"Export already exists: {output_path}. "
            "Pass overwrite=True to replace it."
        )

    content = response.content

    # Basic signature validation catches HTML/error responses incorrectly
    # labelled as application/zip.
    if not content.startswith(b"PK"):
        raise RuntimeError(
            "Letterboxd response claimed to be a ZIP but did not have a ZIP signature"
        )

    output_path.write_bytes(content)

    return ExportUserDataResult(
        path=output_path,
        filename=output_path.name,
        size_bytes=len(content),
        content_type=content_type,
    )


@dataclass(frozen=True)
class ChangePasswordResult:
    result: bool
    csrf: str | None
    messages: tuple[str, ...]
    error_codes: tuple[str, ...]
    error_fields: tuple[str, ...]
    raw: dict[str, Any]

    @property
    def ok(self) -> bool:
        return self.result

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ChangePasswordResult":
        return cls(
            result=bool(payload.get("result")),
            csrf=payload.get("csrf"),
            messages=tuple(payload.get("messages") or ()),
            error_codes=tuple(payload.get("errorCodes") or ()),
            error_fields=tuple(payload.get("errorFields") or ()),
            raw=payload,
        )


class ChangePasswordError(RuntimeError):
    def __init__(self, result: ChangePasswordResult) -> None:
        self.result = result
        message = "; ".join(result.messages) or "Letterboxd password change failed"
        codes = ", ".join(result.error_codes)
        if codes:
            message = f"{message} [{codes}]"
        super().__init__(message)


def _sync_csrf_cookie(session: LBoxdSession, csrf: str | None) -> None:
    """
    Letterboxd's JSON response includes a csrf field. If it differs from the
    cookie value, keep the wrapped session aligned for the next AJAX request.
    """
    if not csrf:
        return

    raw = session.raw
    current = raw.cookies.get("com.xk72.webparts.csrf")
    if current == csrf:
        return

    raw.cookies.set(
        "com.xk72.webparts.csrf",
        csrf,
        domain=DEFAULT_COOKIE_DOMAIN,
        path=DEFAULT_COOKIE_PATH,
    )


def change_password(
    session: LBoxdSession,
    current_password: str,
    new_password: str,
    *,
    confirm_password: str | None = None,
    save: bool = True,
    validate: bool = True,
    raise_on_error: bool = True,
) -> ChangePasswordResult:
    """
    Change the password for the authenticated Letterboxd account.

    Args:
        session:
            Authenticated LBoxdSession.
        current_password:
            Current Letterboxd password.
        new_password:
            Desired new password.
        confirm_password:
            Confirmation value sent to Letterboxd. Defaults to new_password.
            Exposed mainly for tests or for deliberately surfacing mismatch errors.
        save:
            If True, persist updated cookies to session.json_path after success.
        validate:
            If True, perform a protected GET after success to ensure the updated
            cookie jar still represents a signed-in session.
        raise_on_error:
            If True, raise ChangePasswordError when Letterboxd returns result=false.

    Returns:
        ChangePasswordResult containing Letterboxd's messages/error codes.
    """
    if not isinstance(session, LBoxdSession):
        raise TypeError("change_password() requires an authenticated LBoxdSession")

    if not current_password:
        raise ValueError("current_password is required")

    if not new_password:
        raise ValueError("new_password is required")

    confirm = new_password if confirm_password is None else confirm_password
    csrf = session.csrf

    # Use a list of tuples because Letterboxd sends duplicate __csrf fields.
    # A dict would collapse them.
    form = [
        ("__csrf", csrf),
        ("currentPassword", current_password),
        ("newPassword", new_password),
        ("confirmPassword", confirm),
        ("__csrf", csrf),
    ]

    # Keep headers minimal. auth.py explicitly warns not to over-specify
    # browser headers with curl-cffi/Cloudflare.
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Origin": BASE_URL,
        "Referer": AUTH_SETTINGS_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    response = session.post(
        CHANGE_PASSWORD_URL,
        data=form,
        headers=headers,
        allow_redirects=True,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        body = getattr(response, "text", "")
        preview = body[:300].replace("\n", " ")
        raise RuntimeError(
            f"Letterboxd did not return JSON for password change: {preview!r}"
        ) from exc

    result = ChangePasswordResult.from_payload(payload)

    # Keep the in-memory session usable even after validation failures such as
    # password.confirm.mismatch or invalid.minChars, because Letterboxd still
    # returns a csrf value in those responses.
    _sync_csrf_cookie(session, result.csrf)
    session.refresh()

    if not result.ok:
        if raise_on_error:
            raise ChangePasswordError(result)
        return result

    if validate:
        validation = session.get(AUTH_SETTINGS_URL, allow_redirects=True)
        validation.raise_for_status()
        session.refresh()

    if save:
        session.save()

    return result


@dataclass(frozen=True)
class UpdateProfileResult:
    result: bool
    csrf: str | None
    messages: tuple[str, ...]
    error_codes: tuple[str, ...]
    error_fields: tuple[str, ...]
    raw: dict[str, Any]

    show_adult_content: bool | None
    facebook_page: str | None
    instagram_page: str | None
    imdb_page: str | None
    youtube_page: str | None
    tiktok_page: str | None
    bluesky_page: str | None
    threads_page: str | None

    @property
    def ok(self) -> bool:
        return self.result

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UpdateProfileResult":
        return cls(
            result=bool(payload.get("result")),
            csrf=payload.get("csrf"),
            messages=tuple(payload.get("messages") or ()),
            error_codes=tuple(payload.get("errorCodes") or ()),
            error_fields=tuple(payload.get("errorFields") or ()),
            raw=payload,
            show_adult_content=payload.get("showAdultContent"),
            facebook_page=payload.get("facebookPage"),
            instagram_page=payload.get("instagramPage"),
            imdb_page=payload.get("imdbPage"),
            youtube_page=payload.get("youtubePage"),
            tiktok_page=payload.get("tiktokPage"),
            bluesky_page=payload.get("blueskyPage"),
            threads_page=payload.get("threadsPage"),
        )


class UpdateProfileError(RuntimeError):
    def __init__(self, result: UpdateProfileResult) -> None:
        self.result = result
        message = "; ".join(result.messages) or "Letterboxd profile update failed"
        codes = ", ".join(result.error_codes)
        if codes:
            message = f"{message} [{codes}]"
        super().__init__(message)


class _ProfileSettingsFormParser(HTMLParser):
    """
    Extract browser-submitted fields from the Letterboxd profile settings form.

    This intentionally preserves duplicate fields such as favouriteFilmIds.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)

        self.in_target_form = False
        self.form_depth = 0
        self.fields: list[tuple[str, str]] = []

        self._textarea_name: str | None = None
        self._textarea_parts: list[str] = []

        self._select_name: str | None = None
        self._select_first_value: str | None = None
        self._select_selected_value: str | None = None

        self._in_option = False
        self._option_value: str | None = None
        self._option_parts: list[str] = []
        self._option_selected = False

    @staticmethod
    def _attrs_dict(attrs: list[tuple[str, str | None]]) -> dict[str, str]:
        return {k: "" if v is None else v for k, v in attrs}

    def handle_startendtag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        self.handle_starttag(tag, attrs)

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attrs_dict = self._attrs_dict(attrs)

        if tag == "form":
            action = attrs_dict.get("action", "")
            if not self.in_target_form and "/user/update.do" in action:
                self.in_target_form = True
                self.form_depth = 1
            elif self.in_target_form:
                self.form_depth += 1
            return

        if not self.in_target_form:
            return

        if tag == "input":
            self._handle_input(attrs_dict)
            return

        if tag == "textarea":
            name = attrs_dict.get("name")
            if name:
                self._textarea_name = name
                self._textarea_parts = []
            return

        if tag == "select":
            name = attrs_dict.get("name")
            if name:
                self._select_name = name
                self._select_first_value = None
                self._select_selected_value = None
            return

        if tag == "option" and self._select_name is not None:
            self._in_option = True

            # Browser behavior:
            # - If value="" is explicitly present, submit "".
            # - If value is absent, submit the option text.
            self._option_value = (
                attrs_dict["value"] if "value" in attrs_dict else None
            )
            self._option_parts = []
            self._option_selected = "selected" in attrs_dict
            return

    def _handle_input(self, attrs: dict[str, str]) -> None:
        name = attrs.get("name")
        if not name:
            return

        input_type = attrs.get("type", "text").lower()

        if input_type in {"submit", "button", "image", "file", "reset"}:
            return

        if input_type in {"checkbox", "radio"} and "checked" not in attrs:
            return

        self.fields.append((name, attrs.get("value", "")))

    def handle_data(self, data: str) -> None:
        if self.in_target_form and self._textarea_name is not None:
            self._textarea_parts.append(data)

        if self.in_target_form and self._in_option:
            self._option_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.in_target_form:
            return

        if tag == "textarea" and self._textarea_name is not None:
            self.fields.append(
                (self._textarea_name, "".join(self._textarea_parts))
            )
            self._textarea_name = None
            self._textarea_parts = []
            return

        if tag == "option" and self._select_name is not None and self._in_option:
            value = self._option_value
            if value is None:
                value = "".join(self._option_parts).strip()

            if self._select_first_value is None:
                self._select_first_value = value

            if self._option_selected:
                self._select_selected_value = value

            self._in_option = False
            self._option_value = None
            self._option_parts = []
            self._option_selected = False
            return

        if tag == "select" and self._select_name is not None:
            value = (
                self._select_selected_value
                if self._select_selected_value is not None
                else self._select_first_value
            )
            self.fields.append((self._select_name, value or ""))
            self._select_name = None
            self._select_first_value = None
            self._select_selected_value = None
            return

        if tag == "form":
            self.form_depth -= 1
            if self.form_depth <= 0:
                self.in_target_form = False


def _fetch_profile_settings_form(session: LBoxdSession) -> list[tuple[str, str]]:
    response = session.get(PROFILE_SETTINGS_URL, allow_redirects=True)
    response.raise_for_status()

    parser = _ProfileSettingsFormParser()
    parser.feed(response.text)

    if not parser.fields:
        raise RuntimeError("Could not find Letterboxd profile settings form")

    return parser.fields


def _replace_form_field(
    fields: Sequence[tuple[str, str]],
    name: str,
    value: str,
) -> list[tuple[str, str]]:
    """
    Replace the first occurrence of a single-value form field.

    Duplicate fields with other names, such as favouriteFilmIds, are preserved.
    """
    updated: list[tuple[str, str]] = []
    replaced = False

    for field_name, field_value in fields:
        if field_name == name:
            if not replaced:
                updated.append((field_name, value))
                replaced = True
            else:
                # For single-value fields, discard accidental duplicates.
                continue
        else:
            updated.append((field_name, field_value))

    if not replaced:
        updated.append((name, value))

    return updated


def _replace_csrf_fields(
    fields: Sequence[tuple[str, str]],
    csrf: str,
) -> list[tuple[str, str]]:
    updated: list[tuple[str, str]] = []
    found = False

    for name, value in fields:
        if name == "__csrf":
            updated.append((name, csrf))
            found = True
        else:
            updated.append((name, value))

    if not found:
        updated.insert(0, ("__csrf", csrf))

    return updated


def update_bio(
    session: LBoxdSession,
    bio: str,
    *,
    save: bool = True,
    raise_on_error: bool = True,
) -> UpdateProfileResult:
    """
    Update the authenticated user's Letterboxd bio.

    This fetches the current profile settings form, changes only the bio field,
    and submits the full form back to /user/update.do.
    """
    if not isinstance(session, LBoxdSession):
        raise TypeError("update_bio() requires an authenticated LBoxdSession")

    fields = _fetch_profile_settings_form(session)
    fields = _replace_csrf_fields(fields, session.csrf)
    fields = _replace_form_field(fields, "bio", bio)

    headers = {
        "Accept": "*/*",
        "Origin": BASE_URL,
        "Referer": PROFILE_SETTINGS_URL,
        "X-Requested-With": "XMLHttpRequest",
    }

    response = session.post(
        UPDATE_USER_URL,
        data=fields,
        headers=headers,
        allow_redirects=True,
    )
    response.raise_for_status()

    try:
        payload = response.json()
    except ValueError as exc:
        body = getattr(response, "text", "")
        preview = body[:300].replace("\n", " ")
        raise RuntimeError(
            f"Letterboxd did not return JSON for profile update: {preview!r}"
        ) from exc

    result = UpdateProfileResult.from_payload(payload)

    _sync_csrf_cookie(session, result.csrf)
    session.refresh()

    if not result.ok:
        if raise_on_error:
            raise UpdateProfileError(result)
        return result

    if save:
        session.save()

    return result