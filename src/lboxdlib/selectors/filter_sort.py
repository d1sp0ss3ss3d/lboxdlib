# filter_sort.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable
from urllib.parse import quote, quote_plus, unquote, unquote_plus, urlencode, urlsplit, urlunsplit


LETTERBOXD_HOSTS = {"letterboxd.com", "www.letterboxd.com"}


# ------------------------------------------------------------
# Classification
# ------------------------------------------------------------

class LetterboxdUrlKind(StrEnum):
    SEARCH = "search"
    LIST = "list"
    FILMS = "films"
    GLOBAL_TAG = "global_tag"
    USER_TAG = "user_tag"
    OTHER = "other"


class TagTrust(StrEnum):
    GLOBAL = "global"
    USER_SCOPED = "user_scoped"
    TRUSTED_USER_SCOPED = "trusted_user_scoped"


@dataclass(frozen=True)
class ParsedLetterboxdUrl:
    original_url: str
    scheme: str
    netloc: str
    segments: list[str]
    query: dict[str, str | None]

    @property
    def kind(self) -> LetterboxdUrlKind:
        if not self.segments:
            return LetterboxdUrlKind.OTHER

        if self.segments[0] == "search":
            return LetterboxdUrlKind.SEARCH

        if self.segments[0] == "films":
            return LetterboxdUrlKind.FILMS

        # /tag/{tag-slug}/
        if len(self.segments) >= 2 and self.segments[0] == "tag":
            return LetterboxdUrlKind.GLOBAL_TAG

        # /{username}/tag/{tag-slug}/
        if len(self.segments) >= 3 and self.segments[1] == "tag":
            return LetterboxdUrlKind.USER_TAG

        # /{username}/list/{list-slug}/...
        if len(self.segments) >= 3 and self.segments[1] == "list":
            return LetterboxdUrlKind.LIST

        return LetterboxdUrlKind.OTHER

    @property
    def is_list(self) -> bool:
        return self.kind == LetterboxdUrlKind.LIST

    @property
    def is_films(self) -> bool:
        return self.kind == LetterboxdUrlKind.FILMS

    @property
    def is_search(self) -> bool:
        return self.kind == LetterboxdUrlKind.SEARCH

    @property
    def is_tag(self) -> bool:
        return self.kind in {
            LetterboxdUrlKind.GLOBAL_TAG,
            LetterboxdUrlKind.USER_TAG,
        }

    @property
    def tag_slug(self) -> str | None:
        if self.kind == LetterboxdUrlKind.GLOBAL_TAG and len(self.segments) >= 2:
            return self.segments[1]

        if self.kind == LetterboxdUrlKind.USER_TAG and len(self.segments) >= 3:
            return self.segments[2]

        return None

    @property
    def tag_username(self) -> str | None:
        if self.kind == LetterboxdUrlKind.USER_TAG and len(self.segments) >= 3:
            return self.segments[0]

        return None

    def tag_trust(self, trusted_usernames: set[str] | None = None) -> TagTrust | None:
        trusted_usernames = trusted_usernames or set()

        if self.kind == LetterboxdUrlKind.GLOBAL_TAG:
            return TagTrust.GLOBAL

        if self.kind == LetterboxdUrlKind.USER_TAG:
            if self.tag_username in trusted_usernames:
                return TagTrust.TRUSTED_USER_SCOPED
            return TagTrust.USER_SCOPED

        return None


def _parse_query_preserving_letterboxd_flags(query_string: str) -> dict[str, str | None]:
    """
    Parse a query string while preserving Letterboxd's unusual forms.

    Letterboxd uses literal plus signs inside the filters value:
      ?filters=show-shorts+hide-watched

    urllib.parse.parse_qsl() treats + as a space, which corrupts that value
    if an already-filtered URL is transformed again. Letterboxd also uses
    bare flags such as ?adult, so store those as None instead of rewriting
    them as adult=.
    """
    query: dict[str, str | None] = {}

    for part in query_string.split("&"):
        if not part:
            continue

        if "=" in part:
            raw_key, raw_value = part.split("=", 1)
            key = unquote_plus(raw_key)

            if key == "filters":
                # Preserve literal + between filter names. Decode percent escapes,
                # but do not use unquote_plus(), which would turn + into a space.
                query[key] = unquote(raw_value)
            else:
                query[key] = unquote_plus(raw_value)
        else:
            query[unquote_plus(part)] = None

    return query


def parse_letterboxd_url(url: str) -> ParsedLetterboxdUrl:
    split = urlsplit(url)

    netloc = split.netloc or "letterboxd.com"
    scheme = split.scheme or "https"

    segments = [s for s in split.path.split("/") if s]
    query = _parse_query_preserving_letterboxd_flags(split.query)

    return ParsedLetterboxdUrl(
        original_url=url,
        scheme=scheme,
        netloc=netloc,
        segments=segments,
        query=query,
    )


def build_url(parsed: ParsedLetterboxdUrl, segments: list[str], query: dict[str, str | None]) -> str:
    """
    Build a Letterboxd URL.

    Special cases:
      filters=show-shorts+hide-watched
      adult

    We preserve literal plus signs for Letterboxd's filters query rather
    than allowing urlencode() to turn them into %2B, and we preserve bare
    query flags instead of rewriting them as flag=.
    """
    path = "/" + "/".join(s.strip("/") for s in segments if s) + "/"

    query_parts: list[str] = []

    for key, value in query.items():
        if value is None:
            query_parts.append(quote_plus(key))
        elif key == "filters":
            query_parts.append(f"filters={quote(str(value), safe='+')}")
        else:
            query_parts.append(urlencode({key: value}))

    qs = "&".join(query_parts)

    return urlunsplit((parsed.scheme, parsed.netloc, path, qs, ""))


# ------------------------------------------------------------
# Sort adapters
# ------------------------------------------------------------

class FilmSort(StrEnum):
    NAME = "name"

    RELEASE_NEWEST = "release"
    RELEASE_EARLIEST = "release-earliest"

    RATING_HIGHEST = "rating"
    RATING_LOWEST = "rating-lowest"

    YOUR_RATING_HIGHEST = "your-rating"
    YOUR_RATING_LOWEST = "your-rating-lowest"

    YOUR_INTEREST_LIKED = "your-interest-liked"
    YOUR_INTEREST_RELATED = "your-interest-related"

    SHORTEST = "shortest"
    LONGEST = "longest"

    POPULAR_ALL_TIME = "popular"
    POPULAR_THIS_WEEK = "popular-this-week"
    POPULAR_THIS_MONTH = "popular-this-month"
    POPULAR_THIS_YEAR = "popular-this-year"


FILM_SORT_SEGMENTS: dict[FilmSort, list[str]] = {
    FilmSort.NAME: ["by", "name"],

    FilmSort.RELEASE_NEWEST: ["by", "release"],
    FilmSort.RELEASE_EARLIEST: ["by", "release-earliest"],

    FilmSort.RATING_HIGHEST: ["by", "rating"],
    FilmSort.RATING_LOWEST: ["by", "rating-lowest"],

    FilmSort.YOUR_RATING_HIGHEST: ["by", "your-rating"],
    FilmSort.YOUR_RATING_LOWEST: ["by", "your-rating-lowest"],

    FilmSort.YOUR_INTEREST_LIKED: ["by", "your-interest-liked"],
    FilmSort.YOUR_INTEREST_RELATED: ["by", "your-interest-related"],

    FilmSort.SHORTEST: ["by", "shortest"],
    FilmSort.LONGEST: ["by", "longest"],

    FilmSort.POPULAR_ALL_TIME: ["popular"],
    FilmSort.POPULAR_THIS_WEEK: ["popular", "this", "week"],
    FilmSort.POPULAR_THIS_MONTH: ["popular", "this", "month"],
    FilmSort.POPULAR_THIS_YEAR: ["popular", "this", "year"],
}


# Longest first.
KNOWN_SORT_PREFIXES = [
    ["popular", "this", "week"],
    ["popular", "this", "month"],
    ["popular", "this", "year"],
    ["popular"],
    ["by", "your-interest-related"],
    ["by", "your-interest-liked"],
    ["by", "your-rating-lowest"],
    ["by", "your-rating"],
    ["by", "release-earliest"],
    ["by", "release"],
    ["by", "rating-lowest"],
    ["by", "rating"],
    ["by", "shortest"],
    ["by", "longest"],
    ["by", "name"],
]


# ------------------------------------------------------------
# Route filters / adapters
# ------------------------------------------------------------

class PosterGridSize(StrEnum):
    DEFAULT = "default"
    LARGE = "large"


class ListViewMode(StrEnum):
    GRID = "grid"
    DETAIL = "detail"


class FilmFilter(StrEnum):
    SHOW_WATCHED = "show-watched"
    HIDE_WATCHED = "hide-watched"

    SHOW_LIKED = "show-liked"
    HIDE_LIKED = "hide-liked"

    SHOW_RATED = "show-rated"
    HIDE_RATED = "hide-rated"

    SHOW_LOGGED = "show-logged"
    HIDE_LOGGED = "hide-logged"

    SHOW_REVIEWED = "show-reviewed"
    HIDE_REVIEWED = "hide-reviewed"

    SHOW_WATCHLIST = "show-watchlist"
    HIDE_WATCHLIST = "hide-watchlist"

    SHOW_OWNED = "show-owned"
    HIDE_OWNED = "hide-owned"

    SHOW_SHORTS = "show-shorts"
    HIDE_SHORTS = "hide-shorts"

    SHOW_TV = "show-tv"
    HIDE_TV = "hide-tv"

    HIDE_DOCUMENTARIES = "hide-documentaries"
    HIDE_UNRELEASED = "hide-unreleased"


FILTER_CONFLICT_GROUPS = [
    {FilmFilter.SHOW_WATCHED, FilmFilter.HIDE_WATCHED},
    {FilmFilter.SHOW_LIKED, FilmFilter.HIDE_LIKED},
    {FilmFilter.SHOW_RATED, FilmFilter.HIDE_RATED},
    {FilmFilter.SHOW_LOGGED, FilmFilter.HIDE_LOGGED},
    {FilmFilter.SHOW_REVIEWED, FilmFilter.HIDE_REVIEWED},
    {FilmFilter.SHOW_WATCHLIST, FilmFilter.HIDE_WATCHLIST},
    {FilmFilter.SHOW_OWNED, FilmFilter.HIDE_OWNED},
    {FilmFilter.SHOW_SHORTS, FilmFilter.HIDE_SHORTS},
    {FilmFilter.SHOW_TV, FilmFilter.HIDE_TV},
]


@dataclass
class BrowseTransform:
    """
    Path-level and query-level transforms to apply to a Letterboxd URL.

    Search URLs are intentionally ignored.

    Important model:
      - with/{role}/{slug}/ is a single entity slot.
        actor/director/studio/etc. should not be treated as repeatable predicates.
      - genre is repeatable/additive through its slug value:
        genre/animation+horror/
        genre/animation-horror/
        genre/-horror/not/
      - service negation uses:
        not/on/{service}/
        but not/on/no-services/ is refused.
      - theme is a films-only base route:
        /films/theme/{theme}/by/name/
      - tags are their own bases:
        /tag/{tag}/                  global/noisy
        /{username}/tag/{tag}/        user-scoped/trustable
      - detail/ is list-only.
      - size/large/ is presentation-only.
    """

    sort: FilmSort | None = None

    # Only one "with" slot should be used.
    with_role: str | None = None
    with_slug: str | None = None

    # Theme / nano-genre route.
    # Example:
    #   /films/theme/song-and-dance/by/name/
    theme: str | None = None

    # Tag route.
    # If tag_slug is set, this builds either:
    #   /tag/{tag_slug}/
    # or:
    #   /{tag_username}/tag/{tag_slug}/
    tag_slug: str | None = None
    tag_username: str | None = None

    # Existing global tag URLs are noisy and are not transformed unless this is True.
    allow_global_tag_transform: bool = False

    # Existing user-scoped tag URLs are transformed only when the username is trusted.
    trusted_tag_usernames: set[str] = field(default_factory=set)

    # Genre is special/repeatable.
    genres_include: list[str] = field(default_factory=list)
    genres_exclude: list[str] = field(default_factory=list)

    # Ordinary single-slot filters.
    decade: str | None = None
    country: str | None = None
    language: str | None = None

    # Streaming availability.
    service: str | None = None
    service_exclude: bool = False

    # Presentation adapters.
    size: PosterGridSize = PosterGridSize.DEFAULT
    list_view: ListViewMode = ListViewMode.GRID

    # Header/query filter state.
    film_filters: list[FilmFilter] = field(default_factory=list)

    # Use ?filters=... rather than mutating cookies.
    write_filters_query: bool = True


# ------------------------------------------------------------
# Segment helpers
# ------------------------------------------------------------

def _starts_with(seq: list[str], prefix: list[str]) -> bool:
    return seq[: len(prefix)] == prefix


def _is_theme_url(segments: list[str]) -> bool:
    return (
        len(segments) >= 3
        and segments[0] == "films"
        and segments[1] == "theme"
    )


def _is_global_tag_url(segments: list[str]) -> bool:
    return len(segments) >= 2 and segments[0] == "tag"


def _is_user_tag_url(segments: list[str]) -> bool:
    return len(segments) >= 3 and segments[1] == "tag"


def _is_tag_url(segments: list[str]) -> bool:
    return _is_global_tag_url(segments) or _is_user_tag_url(segments)


def _strip_known_sort_from_tail(tail: list[str]) -> list[str]:
    for prefix in KNOWN_SORT_PREFIXES:
        if _starts_with(tail, prefix):
            return tail[len(prefix):]
    return tail


def _strip_known_sort_after_films_base(segments: list[str]) -> list[str]:
    """
    Remove one known sort adapter from either:

      /films/{sort}/...
      /films/theme/{theme}/{sort}/...

    Theme matters because Letterboxd uses:

      /films/theme/song-and-dance/by/name/

    not:

      /films/by/name/theme/song-and-dance/
    """
    if not segments or segments[0] != "films":
        return segments

    if _is_theme_url(segments):
        base = segments[:3]
        tail = segments[3:]
        return base + _strip_known_sort_from_tail(tail)

    base = segments[:1]
    tail = segments[1:]
    return base + _strip_known_sort_from_tail(tail)


def _strip_known_sort_after_tag_base(segments: list[str]) -> list[str]:
    """
    Remove one known sort adapter from either:

      /tag/{tag}/{sort}/...
      /{username}/tag/{tag}/{sort}/...
    """
    if _is_global_tag_url(segments):
        base = segments[:2]
        tail = segments[2:]
        return base + _strip_known_sort_from_tail(tail)

    if _is_user_tag_url(segments):
        base = segments[:3]
        tail = segments[3:]
        return base + _strip_known_sort_from_tail(tail)

    return segments


def _apply_sort_to_films_url(segments: list[str], sort: FilmSort | None) -> list[str]:
    if sort is None:
        return segments

    if not segments or segments[0] != "films":
        return segments

    stripped = _strip_known_sort_after_films_base(segments)

    if _is_theme_url(stripped):
        return stripped[:3] + FILM_SORT_SEGMENTS[sort] + stripped[3:]

    return ["films"] + FILM_SORT_SEGMENTS[sort] + stripped[1:]


def _apply_sort_to_tag_url(segments: list[str], sort: FilmSort | None) -> list[str]:
    if sort is None:
        return segments

    stripped = _strip_known_sort_after_tag_base(segments)

    if _is_global_tag_url(stripped):
        return stripped[:2] + FILM_SORT_SEGMENTS[sort] + stripped[2:]

    if _is_user_tag_url(stripped):
        return stripped[:3] + FILM_SORT_SEGMENTS[sort] + stripped[3:]

    return segments


def _apply_theme_base(segments: list[str], theme: str | None) -> list[str]:
    """
    Normalize films URLs to:

      /films/theme/{theme}/...

    while preserving existing sort/filter tail.

    This is films-only. A list URL or tag URL should not receive a theme base.
    """
    if not theme:
        return segments

    if not segments or segments[0] != "films":
        raise ValueError("theme/{slug}/ is only supported on /films/... URLs.")

    theme = theme.strip("/")

    if _is_theme_url(segments):
        tail = segments[3:]
    else:
        tail = segments[1:]

    return ["films", "theme", theme] + tail


def _tag_base_segments(tag_slug: str, username: str | None = None) -> list[str]:
    tag_slug = tag_slug.strip("/")

    if username:
        return [username.strip("/"), "tag", tag_slug]

    return ["tag", tag_slug]


def _apply_tag_base(
    segments: list[str],
    *,
    tag_slug: str | None,
    tag_username: str | None,
) -> list[str]:
    """
    Normalize to either:

      /tag/{tag}/
      /{username}/tag/{tag}/

    Preserve any tail after the tag slug, such as:
      /by/name/
      /genre/horror/
      /on/plex-us/
      /size/large/
    """
    if not tag_slug:
        return segments

    if _is_global_tag_url(segments):
        tail = segments[2:]
    elif _is_user_tag_url(segments):
        tail = segments[3:]
    else:
        tail = []

    return _tag_base_segments(tag_slug, tag_username) + tail


def _tag_url_is_transformable(
    parsed: ParsedLetterboxdUrl,
    transform: BrowseTransform,
) -> bool:
    """
    Global tag pages are noisy by default.

    User tag pages are transformable only if the username is explicitly trusted.
    """
    if parsed.kind == LetterboxdUrlKind.GLOBAL_TAG:
        return transform.allow_global_tag_transform

    if parsed.kind == LetterboxdUrlKind.USER_TAG:
        username = parsed.tag_username
        return bool(username and username in transform.trusted_tag_usernames)

    return False


def _remove_existing_detail(segments: list[str]) -> list[str]:
    return [s for s in segments if s != "detail"]


def _remove_existing_size(segments: list[str]) -> list[str]:
    out: list[str] = []
    i = 0

    while i < len(segments):
        if segments[i] == "size" and i + 1 < len(segments):
            i += 2
            continue

        out.append(segments[i])
        i += 1

    return out


def _remove_existing_filter_family(segments: list[str], families: set[str]) -> list[str]:
    """
    Remove known path filters controlled by this module:

      genre/{value}
      country/{value}
      language/{value}
      decade/{value}
      on/{value}
      not/on/{value}
      with/{role}/{slug}

    Theme and tag bases are handled separately.
    """
    out: list[str] = []
    i = 0

    while i < len(segments):
        cur = segments[i]

        if cur in {"genre", "country", "language", "decade", "on"} and cur in families:
            i += 2
            continue

        if cur == "not" and "on" in families and i + 2 < len(segments) and segments[i + 1] == "on":
            i += 3
            continue

        if cur == "with" and "with" in families and i + 2 < len(segments):
            i += 3
            continue

        out.append(cur)
        i += 1

    return out


def _clean_include_genre_slug(value: str) -> str:
    value = value.strip().strip("/")
    while value.startswith("+"):
        value = value[1:].strip()
    return value


def _clean_exclude_genre_slug(value: str) -> str:
    value = value.strip().strip("/")
    while value.startswith("+"):
        value = value[1:].strip()
    while value.startswith("-"):
        value = value[1:].strip()
    return value


def _genre_segment(include: Iterable[str], exclude: Iterable[str]) -> list[str]:
    include = [g for raw in include if (g := _clean_include_genre_slug(raw))]
    exclude = [g for raw in exclude if (g := _clean_exclude_genre_slug(raw))]

    if not include and not exclude:
        return []

    if include and exclude:
        # Letterboxd's genre expression is plus-joined terms.
        # Excluded terms are terms too; they just have a leading '-'.
        # Example: /films/popular/genre/thriller+-horror/
        value = "+".join([*include, *(f"-{g}" for g in exclude)])
        return ["genre", value]

    if include:
        return ["genre", "+".join(include)]

    # Canonical pure exclusion form observed:
    #   /genre/-horror/not/
    if len(exclude) == 1:
        return ["genre", f"-{exclude[0]}", "not"]

    value = "+".join(f"-{g}" for g in exclude)
    return ["genre", value, "not"]


def _service_segment(service: str | None, exclude: bool) -> list[str]:
    if not service:
        return []

    service = service.strip("/")

    if exclude:
        if service == "no-services":
            raise ValueError("Letterboxd appears to ignore not/on/no-services; refusing to generate it.")
        return ["not", "on", service]

    return ["on", service]


def _with_segment(role: str | None, slug: str | None) -> list[str]:
    if not role and not slug:
        return []

    if not role or not slug:
        raise ValueError("Both with_role and with_slug are required for a with/... filter.")

    return ["with", role.strip("/"), slug.strip("/")]


def _size_segment(size: PosterGridSize) -> list[str]:
    if size == PosterGridSize.DEFAULT:
        return []
    if size == PosterGridSize.LARGE:
        return ["size", "large"]

    raise ValueError(f"Unsupported size adapter: {size}")


def _list_view_segment(view: ListViewMode) -> list[str]:
    if view == ListViewMode.GRID:
        return []
    if view == ListViewMode.DETAIL:
        return ["detail"]

    raise ValueError(f"Unsupported list view mode: {view}")


def _validate_film_filters(filters: list[FilmFilter]) -> None:
    selected = set(filters)

    for group in FILTER_CONFLICT_GROUPS:
        if len(selected & group) > 1:
            names = ", ".join(sorted(f.value for f in selected & group))
            raise ValueError(f"Conflicting film filters: {names}")


def film_filter_query_value(filters: list[FilmFilter]) -> str:
    """
    Query form:

      ?filters=show-shorts+hide-watched
    """
    _validate_film_filters(filters)
    return "+".join(f.value for f in filters)


def film_filter_cookie_value(filters: list[FilmFilter]) -> str:
    """
    Cookie form:

      filmFilter=show-shorts%20hide-watched
    """
    _validate_film_filters(filters)
    return "%20".join(f.value for f in filters)


# ------------------------------------------------------------
# Main transformer
# ------------------------------------------------------------

def transform_letterboxd_url(url: str, transform: BrowseTransform) -> str:
    parsed = parse_letterboxd_url(url)

    # Refuse to transform arbitrary non-Letterboxd hosts that happen to use
    # Letterboxd-like path segments. Relative URLs are normalized above to
    # letterboxd.com and remain supported.
    if parsed.netloc not in LETTERBOXD_HOSTS:
        return url

    # Hard requirement: ignore search URLs completely.
    if parsed.kind == LetterboxdUrlKind.SEARCH:
        return url

    segments = list(parsed.segments)
    query = dict(parsed.query)

    is_list = parsed.kind == LetterboxdUrlKind.LIST
    is_films = parsed.kind == LetterboxdUrlKind.FILMS
    is_tag = parsed.kind in {
        LetterboxdUrlKind.GLOBAL_TAG,
        LetterboxdUrlKind.USER_TAG,
    }

    # Preserve unrelated Letterboxd URLs unless we are explicitly creating a tag URL.
    if not is_list and not is_films and not is_tag and not transform.tag_slug:
        return url

    # Existing global/user tag URLs are not all equally trustworthy.
    # /tag/{tag}/ is global and noisy unless explicitly allowed.
    # /{username}/tag/{tag}/ is allowed only for trusted usernames.
    if is_tag and not transform.tag_slug:
        if not _tag_url_is_transformable(parsed, transform):
            return url

    # If transform.tag_slug is provided, construct a tag base.
    # This allows:
    #   BrowseTransform(tag_slug="zombies")
    #   -> /tag/zombies/
    #
    #   BrowseTransform(tag_username="d1sp0ss3ss3d", tag_slug="zombies")
    #   -> /d1sp0ss3ss3d/tag/zombies/
    if transform.tag_slug:
        if transform.theme:
            raise ValueError("theme and tag_slug cannot be combined in one route.")

        segments = _apply_tag_base(
            segments,
            tag_slug=transform.tag_slug,
            tag_username=transform.tag_username,
        )

        is_tag = True
        is_films = False
        is_list = False

    # --------------------------------------------------------
    # Theme base and sorting.
    # --------------------------------------------------------
    # Theme is films-only and affects where sort belongs:
    #   /films/theme/song-and-dance/by/name/
    if transform.theme:
        if not is_films:
            raise ValueError("theme/{slug}/ is only valid for /films/... URLs.")
        segments = _apply_theme_base(segments, transform.theme)

    # Sorting is currently handled only for /films/... and tag URLs.
    # List sort routes are not modeled here.
    if is_films:
        segments = _apply_sort_to_films_url(segments, transform.sort)
    elif is_tag:
        segments = _apply_sort_to_tag_url(segments, transform.sort)

    # --------------------------------------------------------
    # Remove replaceable adapters controlled by this transform.
    # --------------------------------------------------------
    families_to_replace: set[str] = set()

    if transform.with_role or transform.with_slug:
        families_to_replace.add("with")
    if transform.genres_include or transform.genres_exclude:
        families_to_replace.add("genre")
    if transform.decade:
        families_to_replace.add("decade")
    if transform.country:
        families_to_replace.add("country")
    if transform.language:
        families_to_replace.add("language")
    if transform.service:
        families_to_replace.add("on")

    if families_to_replace:
        segments = _remove_existing_filter_family(segments, families_to_replace)

    # Presentation adapters are replaceable.
    segments = _remove_existing_size(segments)

    if is_list:
        segments = _remove_existing_detail(segments)

    # --------------------------------------------------------
    # Add semantic route filters.
    # --------------------------------------------------------
    additions: list[str] = []

    # One entity slot only.
    additions.extend(_with_segment(transform.with_role, transform.with_slug))

    # Repeatable/additive genre slot.
    additions.extend(_genre_segment(transform.genres_include, transform.genres_exclude))

    if transform.decade:
        additions.extend(["decade", transform.decade.strip("/")])

    if transform.country:
        additions.extend(["country", transform.country.strip("/")])

    if transform.language:
        additions.extend(["language", transform.language.strip("/")])

    additions.extend(_service_segment(transform.service, transform.service_exclude))

    segments.extend(additions)

    # --------------------------------------------------------
    # Add presentation adapters.
    # --------------------------------------------------------
    if is_list:
        segments.extend(_list_view_segment(transform.list_view))
    elif transform.list_view == ListViewMode.DETAIL:
        raise ValueError("detail/ is only valid for Letterboxd list URLs.")

    # size/large/ is valid for poster-grid display.
    # It does not change result membership.
    segments.extend(_size_segment(transform.size))

    # --------------------------------------------------------
    # Apply header/query filter state.
    # --------------------------------------------------------
    if transform.film_filters and transform.write_filters_query:
        query["filters"] = film_filter_query_value(transform.film_filters)

    return build_url(parsed, segments, query)


# ------------------------------------------------------------
# Examples
# ------------------------------------------------------------

if __name__ == "__main__":
    print(
        transform_letterboxd_url(
            "https://letterboxd.com/films/popular/",
            BrowseTransform(
                theme="song-and-dance",
                sort=FilmSort.NAME,
            ),
        )
    )
    # https://letterboxd.com/films/theme/song-and-dance/by/name/

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/films/popular/genre/horror/",
            BrowseTransform(
                sort=FilmSort.RATING_HIGHEST,
                genres_include=["animation"],
                genres_exclude=["horror"],
                service="favorite-services",
                film_filters=[FilmFilter.SHOW_SHORTS, FilmFilter.HIDE_WATCHED],
            ),
        )
    )
    # https://letterboxd.com/films/by/rating/genre/animation-horror/on/favorite-services/?filters=show-shorts+hide-watched

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/d1sp0ss3ss3d/list/check-your-head/on/plex-us/",
            BrowseTransform(
                service="plex-us",
                list_view=ListViewMode.DETAIL,
                film_filters=[FilmFilter.SHOW_SHORTS, FilmFilter.HIDE_WATCHED],
            ),
        )
    )
    # https://letterboxd.com/d1sp0ss3ss3d/list/check-your-head/on/plex-us/detail/?filters=show-shorts+hide-watched

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/d1sp0ss3ss3d/tag/zombies/",
            BrowseTransform(
                sort=FilmSort.NAME,
                trusted_tag_usernames={"d1sp0ss3ss3d"},
            ),
        )
    )
    # https://letterboxd.com/d1sp0ss3ss3d/tag/zombies/by/name/

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/tag/zombies/",
            BrowseTransform(
                sort=FilmSort.NAME,
            ),
        )
    )
    # https://letterboxd.com/tag/zombies/

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/tag/zombies/",
            BrowseTransform(
                sort=FilmSort.NAME,
                allow_global_tag_transform=True,
            ),
        )
    )
    # https://letterboxd.com/tag/zombies/by/name/

    print(
        transform_letterboxd_url(
            "https://letterboxd.com/search/actor:wes-anderson+director:garth-jennings/",
            BrowseTransform(genres_include=["horror"]),
        )
    )
    # https://letterboxd.com/search/actor:wes-anderson+director:garth-jennings/
