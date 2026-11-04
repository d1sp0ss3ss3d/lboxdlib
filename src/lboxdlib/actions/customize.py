# lb_media_customization.py
from __future__ import annotations

from dataclasses import dataclass
from html import unescape
from typing import Literal, Optional, Sequence
from urllib.parse import quote, urljoin

import json
import re

from bs4 import BeautifulSoup


BASE_URL = "https://letterboxd.com"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"

MediaType = Literal["poster", "backdrop"]


class LetterboxdMediaError(RuntimeError):
    pass


class LetterboxdCSRFError(LetterboxdMediaError):
    pass

@dataclass(slots=True)
class PosteritatiResolvedImage:
    poster_id: str
    page_url: str
    canonical_url: str
    image_url: Optional[str]
    title: Optional[str] = None


def posteritati_page_url(posteritati_id: int | str) -> str:
    """
    Letterboxd source='posteritati' posterId appears to map to:
        https://posteritati.com/poster/{posterId}
    """
    pid = str(posteritati_id).strip()
    if not pid.isdigit():
        raise ValueError(f"Posteritati ID should be numeric, got {posteritati_id!r}")
    return f"https://posteritati.com/poster/{pid}"


def resolve_posteritati_image(session, posteritati_id: int | str) -> PosteritatiResolvedImage:
    """
    Resolve a Letterboxd Posteritati posterId to the actual Posteritati CDN image.

    Important:
        The CDN image path is not safely derivable from the public posterId.
        Fetch the Posteritati page and parse the <img> instead.

    Looks for:
        <img alt="Full screen image of product" src="https://cdn.posteritati.com/posters/...">
    with fallbacks for other cdn.posteritati.com poster images.
    """
    page_url = posteritati_page_url(posteritati_id)

    headers = {
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0",
    }

    response = session.get(page_url, headers=headers, allow_redirects=True)
    response.raise_for_status()

    canonical_url = response.url
    soup = BeautifulSoup(response.text, "html.parser")

    image_url = None

    # Most specific match from your observed DOM.
    img = soup.select_one('img[alt="Full screen image of product"][src*="cdn.posteritati.com/posters/"]')
    if img and img.get("src"):
        image_url = urljoin(canonical_url, img["src"])

    # Fallback: any Posteritati CDN poster image.
    if not image_url:
        img = soup.select_one('img[src*="cdn.posteritati.com/posters/"]')
        if img and img.get("src"):
            image_url = urljoin(canonical_url, img["src"])

    # Optional title extraction.
    title = None
    h1 = soup.select_one("h1")
    if h1:
        title = h1.get_text(" ", strip=True)
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()

    return PosteritatiResolvedImage(
        poster_id=str(posteritati_id),
        page_url=page_url,
        canonical_url=canonical_url,
        image_url=image_url,
        title=title,
    )

def tmdb_image_url(path: str, *, size: str = "w500") -> str:
    """
    Build a direct TMDB image URL from a TMDB file_path.

    Examples:
        /abc.jpg -> https://image.tmdb.org/t/p/w500/abc.jpg
        abc.jpg  -> https://image.tmdb.org/t/p/w500/abc.jpg
    """
    path = path.strip()
    if not path:
        raise ValueError("Empty TMDB image path")

    if not path.startswith("/"):
        path = "/" + path

    return f"{TMDB_IMAGE_BASE}/{size}{path}"


def split_letterboxd_numeric_id(numeric_id: int | str) -> str:
    """
    Letterboxd resized image URLs split the internal numeric film ID into digits.

    Examples:
        28195 -> '2/8/1/9/5'
        51719 -> '5/1/7/1/9'
    """
    s = str(numeric_id).strip()
    if not s.isdigit():
        raise ValueError(f"Expected numeric Letterboxd film ID, got {numeric_id!r}")
    return "/".join(s)


def strip_image_extension(path_or_name: str) -> str:
    """
    /abc123.jpg -> abc123
    abc123.jpg  -> abc123
    47092       -> 47092
    """
    name = path_or_name.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[0]


def infer_letterboxd_numeric_id_from_preview_url(url: str) -> Optional[str]:
    """
    Extract the Letterboxd numeric film ID from observed resized URLs.

    Examples:
        .../alternative-backdrop/2/8/1/9/5/tmdb/... -> 28195
        .../alternative-poster/5/1/7/1/9/p/...      -> 51719

    Returns None if the pattern cannot be recognized.
    """
    patterns = [
        r"/alternative-poster/((?:\d/)+\d)/",
        r"/alternative-backdrop/((?:\d/)+\d)/",
        r"/film-poster/((?:\d/)+\d)/",
    ]

    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1).replace("/", "")

    return None


def letterboxd_alternative_preview_url(
    *,
    media_type: MediaType,
    film_numeric_id: int | str,
    source: str,
    media_id: str,
    film_slug: Optional[str] = None,
) -> str:
    """
    Generate a Letterboxd resized alternative preview URL.

    Prefer the picker's parsed <img src> when available. This function exists
    for later reconstruction when you only have source + media_id + film id.

    Observed patterns:

    TMDB poster:
        /alternative-poster/<digits>/p/<tmdb_key>-0-300-0-450-crop.jpg

    Posteritati poster:
        /alternative-poster/<digits>/ptt/<posteritati_id>/<film_slug>-md-web-0-300-0-450-crop.jpg

    TMDB backdrop:
        /alternative-backdrop/<digits>/tmdb/<tmdb_key>-480-480-270-270-crop-000000.jpg

    Posteritati backdrops are not confirmed in the captured DOM. If Letterboxd
    later exposes data-source='posteritati' for backdrops, capture the picker
    img src and store it rather than guessing.
    """
    if media_type not in {"poster", "backdrop"}:
        raise ValueError("media_type must be 'poster' or 'backdrop'")

    digits = split_letterboxd_numeric_id(film_numeric_id)

    if source == "default":
        raise ValueError("Default media preview should be taken from picker img src, not generated")

    if media_type == "poster":
        if source == "tmdb":
            image_key = strip_image_extension(media_id)
            return (
                f"https://a.ltrbxd.com/resized/alternative-poster/"
                f"{digits}/p/{quote(image_key)}-0-300-0-450-crop.jpg"
            )

        if source == "posteritati":
            if not film_slug:
                raise ValueError("film_slug is required for Posteritati poster preview URLs")

            return (
                f"https://a.ltrbxd.com/resized/alternative-poster/"
                f"{digits}/ptt/{quote(str(media_id))}/{quote(film_slug)}-md-web-0-300-0-450-crop.jpg"
            )

    if media_type == "backdrop":
        if source == "tmdb":
            image_key = strip_image_extension(media_id)
            return (
                f"https://a.ltrbxd.com/resized/alternative-backdrop/"
                f"{digits}/tmdb/{quote(image_key)}-480-480-270-270-crop-000000.jpg"
            )

        if source == "posteritati":
            raise ValueError(
                "Posteritati backdrop URL pattern is not confirmed. "
                "Use the picker img src as preview_url."
            )

    raise ValueError(
        f"Cannot generate Letterboxd preview URL for "
        f"media_type={media_type!r}, source={source!r}, media_id={media_id!r}"
    )


@dataclass(slots=True)
class MediaChoice:
    media_id: str
    source: str
    media_type: MediaType

    lang: Optional[str] = None
    aspect_ratio: Optional[float] = None
    popularity: Optional[int] = None
    preview_url: Optional[str] = None
    film_numeric_id: Optional[str] = None
    film_slug: Optional[str] = None

    is_default: bool = False
    is_selected: bool = False
    is_global_selected: bool = False

    def tmdb_url(
        self,
        *,
        poster_size: str = "w500",
        backdrop_size: str = "w780",
    ) -> Optional[str]:
        if self.source != "tmdb" or self.media_id == "default":
            return None

        size = poster_size if self.media_type == "poster" else backdrop_size
        return tmdb_image_url(self.media_id, size=size)

    def posteritati_page_url(self) -> Optional[str]:
        if self.source != "posteritati":
            return None
        if self.media_type != "poster":
            return None
        if not str(self.media_id).isdigit():
            return None
        return posteritati_page_url(self.media_id)

    def resolve_posteritati_image_url(self, session) -> Optional[str]:
        if self.source != "posteritati" or self.media_type != "poster":
            return None
        resolved = resolve_posteritati_image(session, self.media_id)
        return resolved.image_url

    def source_image_url(
        self,
        *,
        session=None,
    ) -> Optional[str]:
        """
        Direct-ish source image URL.

        TMDB:
            Generated directly.

        Posteritati:
            Requires fetching the Posteritati page, because posterId maps to
            the Posteritati page, not directly to the CDN image path.
        """
        if self.source == "tmdb":
            return self.tmdb_url()

        if self.source == "posteritati" and session is not None:
            return self.resolve_posteritati_image_url(session)

        return None

    def generated_letterboxd_preview_url(self) -> Optional[str]:
        if self.media_id == "default":
            return None

        if not self.film_numeric_id:
            return None

        try:
            return letterboxd_alternative_preview_url(
                media_type=self.media_type,
                film_numeric_id=self.film_numeric_id,
                source=self.source,
                media_id=self.media_id,
                film_slug=self.film_slug,
            )
        except ValueError:
            return None

    def best_image_url(self, *, session=None) -> Optional[str]:
        """
        Best usable image URL.

        Preference order:
            1. Letterboxd picker preview URL
            2. Reconstructed Letterboxd resized URL
            3. TMDB direct URL / Posteritati CDN URL if session is provided
        """
        return (
            self.preview_url
            or self.generated_letterboxd_preview_url()
            or self.source_image_url(session=session)
        )


@dataclass(slots=True)
class MediaPicker:
    """
    Parsed poster/backdrop picker.
    """
    film_slug: str
    media_type: MediaType
    action: str
    action_url: str

    default_media_item_id: Optional[str]
    selected_media_item_id: Optional[str]
    global_selected_media_item_id: Optional[str]
    default_object_only_selection: bool

    object_id: str = ""
    film_numeric_id: Optional[str] = None
    choices: list[MediaChoice] | None = None

    def __post_init__(self) -> None:
        if self.choices is None:
            self.choices = []

    def get_choice(self, media_id: str, *, source: Optional[str] = None) -> MediaChoice:
        for choice in self.choices or []:
            if choice.media_id == media_id and (source is None or choice.source == source):
                return choice

        if source:
            raise KeyError(f"No {self.media_type} choice found for media_id={media_id!r}, source={source!r}")

        raise KeyError(f"No {self.media_type} choice found for media_id={media_id!r}")

    @property
    def last_choice(self) -> MediaChoice:
        real_choices = [c for c in (self.choices or []) if c.media_id != "default"]
        if not real_choices:
            raise KeyError(f"No non-default {self.media_type} choices found")
        return real_choices[-1]

    @property
    def selected_choice(self) -> Optional[MediaChoice]:
        if not self.selected_media_item_id:
            return None
        try:
            return self.get_choice(self.selected_media_item_id)
        except KeyError:
            return None


def _csrf_from_cookiejar(session) -> Optional[str]:
    return session.cookies.get("com.xk72.webparts.csrf")


def _parse_boolish(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def _parse_optional_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_optional_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _extract_film_slug_from_action(action: str) -> str:
    match = re.search(r"/film/([^/]+)/customize-(?:poster|backdrop)/", action)
    if not match:
        raise ValueError(f"Could not infer film slug from action URL: {action!r}")
    return match.group(1)


def fetch_media_picker(
    session,
    film_slug: str,
    media_type: MediaType,
    *,
    object_id: str = "",
) -> str:
    """
    Fetch Letterboxd poster/backdrop picker HTML.

    media_type:
        "poster"   -> /film/{slug}/poster-picker/?objectId=...
        "backdrop" -> /film/{slug}/backdrop-picker/?objectId=...

    object_id:
        "" for film-level picker.
        e.g. "filmListEntry:4756063327" for list-entry-specific context.
    """
    if media_type not in {"poster", "backdrop"}:
        raise ValueError("media_type must be 'poster' or 'backdrop'")

    url = f"{BASE_URL}/film/{film_slug}/{media_type}-picker/"
    referer = f"{BASE_URL}/film/{film_slug}/{media_type}/"

    headers = {
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": referer,
    }

    response = session.get(
        url,
        params={"objectId": object_id},
        headers=headers,
    )
    response.raise_for_status()
    return response.text


def parse_media_picker(
    html_text: str,
    *,
    film_slug: Optional[str] = None,
    object_id: str = "",
    film_numeric_id: Optional[str] = None,
) -> MediaPicker:
    """
    Parse Letterboxd poster/backdrop picker HTML.

    Supports:
        .js-poster-picker    with data-poster-picker-options
        .js-backdrop-picker  with data-backdrop-picker-options
    """
    soup = BeautifulSoup(html_text, "html.parser")

    picker_el = soup.select_one(".js-poster-picker, .js-backdrop-picker")
    if not picker_el:
        raise ValueError("Could not find .js-poster-picker or .js-backdrop-picker")

    if "js-poster-picker" in picker_el.get("class", []):
        media_type: MediaType = "poster"
        options_attr = "data-poster-picker-options"
    elif "js-backdrop-picker" in picker_el.get("class", []):
        media_type = "backdrop"
        options_attr = "data-backdrop-picker-options"
    else:
        raise ValueError("Picker element found, but type could not be determined")

    options_raw = picker_el.get(options_attr)
    if not options_raw:
        raise ValueError(f"Picker missing {options_attr}")

    options = json.loads(unescape(options_raw))

    action = options["action"]
    action_url = urljoin(BASE_URL, action)

    if film_slug is None:
        film_slug = _extract_film_slug_from_action(action)

    default_media_item_id = options.get("defaultMediaItemId")
    selected_media_item_id = options.get("selectedMediaItemId")
    global_selected_media_item_id = options.get("globalSelectedMediaItemId")
    default_object_only_selection = _parse_boolish(
        options.get("defaultObjectOnlySelection"),
        default=False,
    )

    choices: list[MediaChoice] = []

    selector = f'[data-js-content-media-picker-mediaitem="{media_type}"]'
    for item in soup.select(selector):
        media_id = item.get("data-id")
        source = item.get("data-source")

        if not media_id or not source:
            continue

        img = item.select_one("img")
        preview_url = img.get("src") if img else None

        inferred_numeric_id = film_numeric_id
        if not inferred_numeric_id and preview_url:
            inferred_numeric_id = infer_letterboxd_numeric_id_from_preview_url(preview_url)

        choice = MediaChoice(
            media_id=media_id,
            source=source,
            media_type=media_type,
            lang=item.get("data-lang"),
            aspect_ratio=_parse_optional_float(item.get("data-aspect-ratio")),
            popularity=_parse_optional_int(item.get("data-popularity")),
            preview_url=preview_url,
            film_numeric_id=inferred_numeric_id,
            film_slug=film_slug,
            is_default=(media_id == default_media_item_id),
            is_selected=(media_id == selected_media_item_id),
            is_global_selected=(media_id == global_selected_media_item_id),
        )
        choices.append(choice)

    if not film_numeric_id:
        for choice in choices:
            if choice.film_numeric_id:
                film_numeric_id = choice.film_numeric_id
                break

    return MediaPicker(
        film_slug=film_slug,
        media_type=media_type,
        action=action,
        action_url=action_url,
        default_media_item_id=default_media_item_id,
        selected_media_item_id=selected_media_item_id,
        global_selected_media_item_id=global_selected_media_item_id,
        default_object_only_selection=default_object_only_selection,
        object_id=object_id,
        film_numeric_id=film_numeric_id,
        choices=choices,
    )


def get_media_picker(
    session,
    film_slug: str,
    media_type: MediaType,
    *,
    object_id: str = "",
    film_numeric_id: Optional[str] = None,
) -> MediaPicker:
    html_text = fetch_media_picker(
        session,
        film_slug,
        media_type,
        object_id=object_id,
    )
    return parse_media_picker(
        html_text,
        film_slug=film_slug,
        object_id=object_id,
        film_numeric_id=film_numeric_id,
    )


def _field_name_for_media_type(media_type: MediaType) -> str:
    if media_type == "poster":
        return "posterId"
    if media_type == "backdrop":
        return "backdropId"
    raise ValueError("media_type must be 'poster' or 'backdrop'")


def customize_media(
    session,
    picker: MediaPicker,
    choice: MediaChoice | str,
    *,
    source: Optional[str] = None,
    csrf: Optional[str] = None,
    object_id: Optional[str] = None,
    object_only: Optional[bool] = None,
    retry_on_csrf: bool = True,
) -> dict:
    """
    Submit a poster/backdrop choice to Letterboxd.

    choice:
        Either a MediaChoice object, or a media_id string.
        If using a string and duplicate media_id values ever exist, pass source too.

    object_id:
        None -> use picker.object_id.
        ""   -> film-level.
        "filmListEntry:4756063327" -> list-entry-specific.

    object_only:
        None -> use picker.default_object_only_selection.
        False -> film-level/global preference.
        True  -> object-specific preference.

    retry_on_csrf:
        If Letterboxd returns {"errorCodes":["csrf"], "csrf":"..."},
        retry once with the returned token.
    """
    if isinstance(choice, MediaChoice):
        selected = choice
    else:
        selected = picker.get_choice(choice, source=source)

    if selected.media_type != picker.media_type:
        raise ValueError(
            f"Choice is for {selected.media_type!r}, picker is for {picker.media_type!r}"
        )

    if csrf is None:
        csrf = _csrf_from_cookiejar(session)

    if not csrf:
        raise LetterboxdCSRFError(
            "Missing CSRF token. Pass csrf=... or ensure session cookies include "
            "'com.xk72.webparts.csrf'."
        )

    if object_id is None:
        object_id = picker.object_id

    if object_only is None:
        object_only = picker.default_object_only_selection

    field_name = _field_name_for_media_type(picker.media_type)

    def _post(token: str) -> dict:
        data = {
            "__csrf": token,
            field_name: selected.media_id,
            "source": selected.source,
            "objectOnly": "true" if object_only else "false",
            "objectId": object_id or "",
        }

        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": BASE_URL,
            "Referer": f"{BASE_URL}/film/{picker.film_slug}/{picker.media_type}/",
        }

        response = session.post(
            picker.action_url,
            data=data,
            headers=headers,
        )
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise LetterboxdMediaError(
                f"Letterboxd did not return JSON. Status={response.status_code}, "
                f"body={response.text[:500]!r}"
            ) from exc

        return payload

    result = _post(csrf)

    if (
        retry_on_csrf
        and result.get("result") is False
        and "csrf" in result
        and "csrf" in result.get("errorCodes", [])
    ):
        result = _post(result["csrf"])

    if result.get("result") is False:
        messages = result.get("messages") or []
        codes = result.get("errorCodes") or []
        raise LetterboxdMediaError(
            f"Letterboxd customization failed. codes={codes!r}, messages={messages!r}, response={result!r}"
        )

    return result


def customize_poster(
    session,
    film_slug: str,
    poster_id: str,
    *,
    source: Optional[str] = None,
    object_id: str = "",
    object_only: Optional[bool] = None,
    csrf: Optional[str] = None,
) -> dict:
    """
    Convenience wrapper:
        fetch poster picker -> find poster -> POST customize-poster.

    For film-level:
        object_id=""
        object_only=False

    For list-entry-specific:
        object_id="filmListEntry:<id>"
        object_only=True
    """
    picker = get_media_picker(
        session,
        film_slug,
        "poster",
        object_id=object_id,
    )

    if object_only is None:
        object_only = picker.default_object_only_selection

    return customize_media(
        session,
        picker,
        poster_id,
        source=source,
        csrf=csrf,
        object_id=object_id,
        object_only=object_only,
    )


def customize_backdrop(
    session,
    film_slug: str,
    backdrop_id: str,
    *,
    source: Optional[str] = None,
    object_id: str = "",
    object_only: Optional[bool] = None,
    csrf: Optional[str] = None,
) -> dict:
    """
    Convenience wrapper:
        fetch backdrop picker -> find backdrop -> POST customize-backdrop.

    For film-level:
        object_id=""
        object_only=False

    For list-entry-specific:
        object_id="filmListEntry:<id>"
        object_only=True
    """
    picker = get_media_picker(
        session,
        film_slug,
        "backdrop",
        object_id=object_id,
    )

    if object_only is None:
        object_only = picker.default_object_only_selection

    return customize_media(
        session,
        picker,
        backdrop_id,
        source=source,
        csrf=csrf,
        object_id=object_id,
        object_only=object_only,
    )


def choose_last_media(
    session,
    film_slug: str,
    media_type: MediaType,
    *,
    object_id: str = "",
    object_only: Optional[bool] = None,
    csrf: Optional[str] = None,
) -> dict:
    """
    Convenience method for tests:
        fetch picker -> select the last non-default item -> POST customization.
    """
    picker = get_media_picker(
        session,
        film_slug,
        media_type,
        object_id=object_id,
    )

    choice = picker.last_choice

    if object_only is None:
        object_only = picker.default_object_only_selection

    return customize_media(
        session,
        picker,
        choice,
        csrf=csrf,
        object_id=object_id,
        object_only=object_only,
    )


def summarize_choices(choices: Sequence[MediaChoice]) -> list[dict]:
    """
    Display-independent summary for logging/testing.
    """
    return [
        {
            "media_id": c.media_id,
            "source": c.source,
            "media_type": c.media_type,
            "lang": c.lang,
            "aspect_ratio": c.aspect_ratio,
            "popularity": c.popularity,
            "film_numeric_id": c.film_numeric_id,
            "film_slug": c.film_slug,
            "is_default": c.is_default,
            "is_selected": c.is_selected,
            "is_global_selected": c.is_global_selected,
            "preview_url": c.preview_url,
            "generated_letterboxd_preview_url": c.generated_letterboxd_preview_url(),
            "source_image_url": c.source_image_url(),
            "best_image_url": c.best_image_url(),
        }
        for c in choices
    ]