import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, List, Optional, Tuple, TypeVar
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from lboxdlib.auth import get_csrf
from lboxdlib.selectors.filter_sort import transform_letterboxd_url


BASE_URL = "https://letterboxd.com"

T = TypeVar("T")

if TYPE_CHECKING:
    from lboxdlib.selectors.filter_sort import BrowseTransform


def apply_browse_transform(
    url: str,
    browse_transform: Optional["BrowseTransform"] = None,
) -> str:
    """
    Apply filter_sort.BrowseTransform to a URL when requested.

    The import is intentionally lazy so page_handling can still be used in
    contexts where filter_sort is not available, unless a transform is passed.
    """
    if browse_transform is None:
        return url

    return transform_letterboxd_url(url, browse_transform)





# ============================================================
# HTTP debugging helpers
# ============================================================

def _response_url(response) -> str:
    """Best-effort response URL for curl_cffi/requests-like responses."""
    return str(getattr(response, "url", "") or "")


def _html_title_preview(html: str) -> str:
    """Return a compact title/body preview for failed HTML responses."""
    soup = BeautifulSoup(html or "", "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    text = soup.get_text(" ", strip=True) if soup else (html or "")
    preview = text[:500]

    if title and preview:
        return f"title={title!r} preview={preview!r}"
    if title:
        return f"title={title!r}"
    return f"preview={(html or '')[:500]!r}"


def raise_for_status_with_preview(response, *, context: str = "HTTP request", verbose: bool = False) -> None:
    """
    Raise for HTTP errors, but print a useful preview for 403/429/503 first.

    This is for diagnosing whether failures are Letterboxd application errors,
    Cloudflare/challenge pages, stale clearance cookies, or malformed requests.
    It intentionally does not retry or change request behavior.
    """
    status = getattr(response, "status_code", None)

    if status in {403, 429, 503}:
        print(f"{context} returned HTTP {status}")
        url = _response_url(response)
        if url:
            print(f"  response url: {url}")

        headers = getattr(response, "headers", {}) or {}
        server = headers.get("server") or headers.get("Server")
        cf_ray = headers.get("cf-ray") or headers.get("CF-Ray")
        content_type = headers.get("content-type") or headers.get("Content-Type")

        if server:
            print(f"  server: {server}")
        if cf_ray:
            print(f"  cf-ray: {cf_ray}")
        if content_type:
            print(f"  content-type: {content_type}")

        body = getattr(response, "text", "") or ""
        if body:
            print("  body preview:")
            print(_html_title_preview(body))

    elif verbose and status and status >= 400:
        print(f"{context} returned HTTP {status}")

    response.raise_for_status()


# ============================================================
# Classic pagination
# ============================================================

@dataclass(frozen=True)
class PaginationInfo:
    """
    Parsed info from Letterboxd's standard numbered pagination.

    Presence of an enabled <a class="next" href="..."> means there is
    another page. Absence means the current page is the last page, or
    no standard pagination exists.
    """
    has_pagination: bool
    is_first_page: bool
    is_last_page: bool
    current_page: Optional[int]
    prev_href: Optional[str]
    next_href: Optional[str]


def parse_pagination(html: str) -> PaginationInfo:
    soup = BeautifulSoup(html, "lxml")
    container = soup.select_one("div.pagination")

    if not container:
        return PaginationInfo(
            has_pagination=False,
            is_first_page=True,
            is_last_page=True,
            current_page=None,
            prev_href=None,
            next_href=None,
        )

    prev_a = container.select_one(".paginate-nextprev a.previous[href]")
    next_a = container.select_one(".paginate-nextprev a.next[href]")

    prev_href = prev_a.get("href") if prev_a else None
    next_href = next_a.get("href") if next_a else None

    current_page = None
    cur_span = container.select_one(
        ".paginate-pages li.paginate-page.paginate-current > span"
    )
    if cur_span:
        try:
            current_page = int(cur_span.get_text(strip=True))
        except ValueError:
            current_page = None

    return PaginationInfo(
        has_pagination=True,
        is_first_page=prev_href is None,
        is_last_page=next_href is None,
        current_page=current_page,
        prev_href=prev_href,
        next_href=next_href,
    )


def ensure_page(url: str, *, page: int = 1) -> str:
    """
    Return a Letterboxd standard pagination URL with /page/{page}/ applied.

    Query strings and fragments are preserved, so filtered URLs paginate as:

      /films/by/rating/?filters=show-shorts+hide-watched
      -> /films/by/rating/page/1/?filters=show-shorts+hide-watched

    Examples:
      /foo/bar/                 -> /foo/bar/page/1/
      /foo/bar/page/3/          -> /foo/bar/page/1/
      /foo/bar/?filters=a+b     -> /foo/bar/page/1/?filters=a+b
      https://.../foo/?filters=a+b -> https://.../foo/page/1/?filters=a+b
    """
    if page < 1:
        raise ValueError("page must be >= 1")

    parts = urlsplit(url)
    raw_path = parts.path or "/"
    has_leading_slash = raw_path.startswith("/")

    segments = [segment for segment in raw_path.split("/") if segment]

    # Remove any existing standard pagination segment before inserting the
    # requested page. This keeps path-level filters before /page/{n}/.
    cleaned_segments: list[str] = []
    i = 0
    while i < len(segments):
        if (
            segments[i] == "page"
            and i + 1 < len(segments)
            and segments[i + 1].isdigit()
        ):
            i += 2
            continue

        cleaned_segments.append(segments[i])
        i += 1

    base_path = "/".join(cleaned_segments)

    if has_leading_slash:
        base_path = "/" + base_path if base_path else ""

    paged_path = f"{base_path.rstrip('/')}/page/{page}/" if base_path else f"/page/{page}/"

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            paged_path,
            parts.query,
            parts.fragment,
        )
    )


def fetch_standard_pages(
    session,
    url: str,
    *,
    start_page: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
    browse_transform: Optional["BrowseTransform"] = None,
) -> List[str]:
    url = apply_browse_transform(url, browse_transform)

    html_docs: List[str] = []

    current_page = start_page
    fetched = 0
    seen: set[str] = set()

    while True:
        page_url = ensure_page(url, page=current_page)

        if page_url in seen:
            if verbose:
                print(f"Stopping: already fetched {page_url}")
            break

        seen.add(page_url)

        if verbose:
            print(f"Fetching standard page {current_page}: {page_url}")

        r = session.get(page_url)
        raise_for_status_with_preview(r, context=f"standard page {current_page}", verbose=verbose)

        html = r.text
        html_docs.append(html)
        fetched += 1

        if verbose:
            lazy_count = html.count('data-component-class="LazyPoster"')
            print(f"  received {len(html):,} chars, {lazy_count} LazyPoster blocks")

        if pages is not None and fetched >= pages:
            if verbose:
                print(f"Stopping: reached requested page limit ({pages})")
            break

        info = parse_pagination(html)
        if info.is_last_page or not info.next_href:
            if verbose:
                print("Stopping: no next standard page")
            break

        current_page += 1

    return html_docs


# ============================================================
# Cursor-based pagination / AJAX search fragments
# ============================================================

def _path_from_url_or_path(url_or_path: str) -> str:
    parsed = urlparse(url_or_path)

    if parsed.scheme or parsed.netloc:
        return parsed.path

    if url_or_path.startswith("/"):
        return url_or_path

    return "/" + url_or_path


def _query_from_url(url_or_path: str) -> str:
    parsed = urlparse(url_or_path)
    return parsed.query or ""


def _absolute_url(url_or_path: str) -> str:
    parsed = urlparse(url_or_path)

    if parsed.scheme and parsed.netloc:
        return url_or_path

    path = url_or_path if url_or_path.startswith("/") else "/" + url_or_path
    return BASE_URL + path


def _cursor_ajax_path(url_or_path: str) -> str:
    """
    Convert visible Letterboxd search URLs/paths to their AJAX equivalent.

    Observed rule:
      /search/...   -> /s/search/...
      /s/search/... -> /s/search/...

    Important:
      Do not strip search category segments.

    Examples:
      /search/test/             -> /s/search/test/
      /search/films/test/       -> /s/search/films/test/
      /s/search/films/test/     -> /s/search/films/test/
    """
    path = _path_from_url_or_path(url_or_path)

    if path.startswith("/s/search/"):
        return path

    if path.startswith("/search/"):
        return "/s" + path

    return path


def _build_cursor_query(
    original_query: str,
    csrf: str,
    *,
    cursor: Optional[str] = None,
) -> str:
    """
    Preserve bare query flags like ?adult instead of rewriting them as adult=.

    Removes stale __csrf and cursor values, then appends the current cursor
    and csrf values.
    """
    parts: List[str] = []

    for part in original_query.split("&"):
        if not part:
            continue

        key = part.split("=", 1)[0]
        if key in {"__csrf", "cursor"}:
            continue

        parts.append(part)

    if cursor:
        parts.append(f"cursor={cursor}")

    parts.append(f"__csrf={csrf}")

    return "&".join(parts)


def _next_cursor_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "lxml")
    ul = soup.select_one("ul.results[data-cursor], ul[data-cursor]")
    return ul.get("data-cursor") if ul else None


def fetch_next_cursored_page(
    session,
    url_or_path: str,
    *,
    cursor: Optional[str] = None,
    verbose: bool = False,
) -> Tuple[str, Optional[str]]:
    """
    Fetch one cursor/AJAX page.

    For search URLs, this mirrors the browser request shape:

      visible:
        https://letterboxd.com/search/films/test/?adult

      AJAX:
        https://letterboxd.com/s/search/films/test/?adult&__csrf=...

      next page:
        https://letterboxd.com/s/search/films/test/?adult&cursor=...&__csrf=...
    """
    csrf = get_csrf(session)
    if not csrf:
        raise ValueError("CSRF token required for cursor fetch")

    ajax_path = _cursor_ajax_path(url_or_path)
    original_query = _query_from_url(url_or_path)
    query = _build_cursor_query(original_query, csrf, cursor=cursor)

    ajax_url = f"{BASE_URL}{ajax_path}?{query}"

    headers = {
        "Accept": "text/html, */*; q=0.01",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": _absolute_url(url_or_path),
    }

    if verbose:
        if cursor:
            print(f"Fetching cursor page: {ajax_url}")
        else:
            print(f"Fetching first cursor page: {ajax_url}")

    r = session.get(ajax_url, headers=headers)
    raise_for_status_with_preview(r, context="cursor page", verbose=verbose)

    html = r.text
    next_cursor = _next_cursor_from_html(html)

    if verbose:
        print(f"  received {len(html):,} chars")
        print(f"  next cursor: {next_cursor or 'none'}")

    return html, next_cursor


def fetch_cursored_pages(
    session,
    url_or_path: str,
    *,
    pages: Optional[int] = None,
    verbose: bool = False,
) -> List[str]:
    html_docs: List[str] = []

    cursor: Optional[str] = None
    fetched = 0

    while True:
        html, cursor = fetch_next_cursored_page(
            session,
            url_or_path,
            cursor=cursor,
            verbose=verbose,
        )

        html_docs.append(html)
        fetched += 1

        if pages is not None and fetched >= pages:
            if verbose:
                print(f"Stopping: reached requested page limit ({pages})")
            break

        if not cursor:
            if verbose:
                print("Stopping: no next cursor")
            break

    return html_docs


def unify_cursored_results(pages: List[str]) -> List[Tag]:
    """
    Flatten cursor/AJAX result fragments into a single list of top-level <li> tags.

    Each page is expected to have one result <ul> with data-cursor.
    """
    all_results: List[Tag] = []

    for html in pages:
        soup = BeautifulSoup(html, "lxml")
        ul = soup.select_one("ul[data-cursor]")

        if not ul:
            continue

        ul.attrs.pop("data-cursor", None)
        all_results.extend(ul.find_all("li", recursive=False))

    return all_results


# ============================================================
# Films-browser CSI fragments
# ============================================================

def has_lazy_posters(html: str) -> bool:
    return 'data-component-class="LazyPoster"' in html


def _raw_query_key(part: str) -> str:
    """
    Return the raw key portion of a query part without decoding `+` as space.

    This is important for Letterboxd's filters query, whose value is shaped as
    `hide-shorts+hide-documentaries+hide-unreleased` and should remain literal.
    """
    return part.split("=", 1)[0]


def _merge_raw_query_strings(primary_query: str, fallback_query: str) -> str:
    """
    Merge query strings without decoding/re-encoding them.

    Values from `primary_query` win. Values from `fallback_query` are appended
    only when the same raw key is not already present.

    This preserves bare flags such as `?adult` and literal plus signs in
    Letterboxd's `filters=` value.
    """
    merged_parts: list[str] = []
    seen_keys: set[str] = set()

    for query in (primary_query, fallback_query):
        for part in query.split("&"):
            if not part:
                continue

            key = _raw_query_key(part)
            if key in seen_keys:
                continue

            merged_parts.append(part)
            seen_keys.add(key)

    return "&".join(merged_parts)


def _merge_url_query_preserving_raw_values(url: str, fallback_url: str) -> str:
    """
    Return `url` with query params from `fallback_url` appended when absent.

    Used for films-browser CSI URLs. Letterboxd's visible films URL can carry
    user filter state in the query string, while the CSI `data-src` often only
    includes `esiAllowFilters=true`. If we do not carry `filters=...` forward,
    the paged CSI request may fail or fetch the wrong unfiltered data.
    """
    parts = urlsplit(url)
    fallback_parts = urlsplit(fallback_url)

    if not fallback_parts.query:
        return url

    merged_query = _merge_raw_query_strings(parts.query, fallback_parts.query)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            merged_query,
            parts.fragment,
        )
    )


def _path_segment_after(path: str, marker: str) -> Optional[str]:
    """Return the raw path segment immediately after `marker`, if present."""
    idx = path.find(marker)
    if idx < 0:
        return None

    start = idx + len(marker)
    if start >= len(path):
        return None

    end = path.find("/", start)
    if end < 0:
        end = len(path)

    segment = path[start:end]
    return segment or None


def _replace_path_segment_after(path: str, marker: str, replacement: str) -> str:
    """Replace the raw path segment immediately after `marker`."""
    idx = path.find(marker)
    if idx < 0:
        return path

    start = idx + len(marker)
    end = path.find("/", start)
    if end < 0:
        end = len(path)

    return path[:start] + replacement + path[end:]


def _repair_csi_genre_segment_from_visible_url(csi_url: str, visible_url: str) -> str:
    """
    Preserve Letterboxd's raw genre include/exclude slug in films-browser CSI URLs.

    Letterboxd's visible genre route can use `+` as a join marker and `-` as an
    exclusion marker, e.g. `/films/genre/thriller+-horror/`. Some CSI shell
    pages expose a decoded/re-encoded data-src path such as
    `/genre/thriller%20-horror/`, which is not equivalent and can 403.

    When the visible URL contains a /genre/{slug}/ segment, treat that raw slug
    as authoritative and transplant it into the CSI URL while preserving the CSI
    sort prefix, query string, and fragment.
    """
    visible_parts = urlsplit(_absolute_url(visible_url))
    csi_parts = urlsplit(csi_url)

    visible_genre_segment = _path_segment_after(visible_parts.path, "/genre/")
    if not visible_genre_segment:
        return csi_url

    if "/genre/" not in csi_parts.path:
        return csi_url

    repaired_path = _replace_path_segment_after(
        csi_parts.path,
        "/genre/",
        visible_genre_segment,
    )

    if repaired_path == csi_parts.path:
        return csi_url

    return urlunsplit(
        (
            csi_parts.scheme,
            csi_parts.netloc,
            repaired_path,
            csi_parts.query,
            csi_parts.fragment,
        )
    )


def extract_films_browser_csi_src(html: str, base_url: str) -> Optional[str]:
    """
    Return the films-browser CSI source only when the initial raw HTML is a shell.

    This intentionally ignores generic CSI blocks, such as list comments.

    It also refuses CSI when the HTML already contains LazyPoster DOM, because
    that page can be handled by normal extraction / standard pagination.
    """
    if has_lazy_posters(html):
        return None

    soup = BeautifulSoup(html, "lxml")
    csi = soup.select_one(".productions-browser-list .csi[data-src]")
    if not csi:
        return None

    src = csi.get("data-src")
    if not src:
        return None

    if "/csi/films/films-browser-list/" not in src:
        return None

    csi_url = urljoin(base_url, src)
    csi_url = _repair_csi_genre_segment_from_visible_url(csi_url, base_url)

    # The visible films URL may contain query-level header filters such as:
    #   ?filters=hide-shorts+hide-documentaries+hide-unreleased
    # The CSI data-src commonly contains only:
    #   ?esiAllowFilters=true
    # Carry the visible query state forward without decoding/re-encoding it.
    return _merge_url_query_preserving_raw_values(csi_url, base_url)


def csi_url_for_page(csi_url: str, page: int) -> str:
    """
    Return a paged CSI URL with /page/{n}/ applied before the query.

    Page 1 is explicit. This matches the observed Letterboxd films-browser
    behavior where the bare CSI endpoint may not return the same parseable
    listing fragment as /page/1/.
    """
    parts = urlsplit(csi_url)
    path = parts.path.rstrip("/")

    bits = path.split("/")
    if len(bits) >= 2 and bits[-2] == "page" and bits[-1].isdigit():
        path = "/".join(bits[:-2])

    path = f"{path}/page/{page}/"

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            path,
            parts.query,
            parts.fragment,
        )
    )


def films_browser_csi_src_from_url(url: str) -> Optional[str]:
    """
    Best-effort CSI endpoint synthesis for Letterboxd films-browser URLs.

    This fallback is used when Cloudflare blocks the visible /films/... page
    before we can read the page shell and extract the .csi[data-src] value.

    Examples:
      /films/genre/horror/
        -> /csi/films/films-browser-list/genre/horror/?esiAllowFilters=true

      /films/genre/thriller+-horror/?filters=hide-shorts+hide-documentaries
        -> /csi/films/films-browser-list/genre/thriller+-horror/
           ?esiAllowFilters=true&filters=hide-shorts+hide-documentaries

    Returns None for non-film-browser routes.
    """
    absolute = _absolute_url(url)
    parts = urlsplit(absolute)

    path = parts.path or "/"
    if path == "/films":
        path = "/films/"

    if not path.startswith("/films/"):
        return None

    suffix = path[len("/films"):] or "/"
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    if not suffix.endswith("/"):
        suffix += "/"

    csi_path = "/csi/films/films-browser-list" + suffix
    query = _merge_raw_query_strings("esiAllowFilters=true", parts.query)

    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            csi_path,
            query,
            "",
        )
    )


def fetch_standard_probe_has_films(
    session,
    url: str,
    *,
    verbose: bool = False,
) -> bool:
    """
    Probe the explicit /page/1/ URL before accepting CSI.

    This probe is deliberately non-fatal. Letterboxd/Cloudflare may block
    the visible /page/1/ document even when the films-browser CSI endpoint
    works. A 403/429/503 here only means "do not use standard pagination";
    it must not abort collection.
    """
    page1_url = ensure_page(url, page=1)

    if verbose:
        print(f"Probing standard page 1 before CSI: {page1_url}")

    try:
        r = session.get(page1_url)

        if getattr(r, "status_code", None) and r.status_code >= 400:
            try:
                raise_for_status_with_preview(
                    r,
                    context="standard page 1 probe",
                    verbose=verbose,
                )
            except Exception as exc:
                if verbose:
                    print(
                        "standard page 1 probe failed non-fatally; "
                        f"continuing without standard pagination: {type(exc).__name__}: {exc}"
                    )
                return False

        html = r.text
        lazy_count = html.count('data-component-class="LazyPoster"')

        if verbose:
            print(f"  probe received {len(html):,} chars, {lazy_count} LazyPoster blocks")

        return lazy_count > 0

    except Exception as exc:
        if verbose:
            print(
                "standard page 1 probe failed non-fatally; "
                f"continuing without standard pagination: {type(exc).__name__}: {exc}"
            )
        return False


def _csi_headers(referer_url: str) -> dict[str, str]:
    """Headers for Letterboxd films-browser CSI/XHR requests."""
    return {
        "Accept": "text/html, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": _absolute_url(referer_url),
    }


def _fetch_csi_response_with_retry(
    session,
    page_url: str,
    *,
    referer_url: str,
    verbose: bool = False,
    retry_statuses: tuple[int, ...] = (403, 429, 503),
    max_retries: int = 2,
    retry_delay_seconds: float = 1.25,
):
    """
    Fetch a CSI page, retrying transient anti-bot/rate-limit responses.

    Letterboxd's films-browser CSI endpoint is more fragile than visible page
    URLs. In practice, repeated CSI/XHR requests can get a 403/429/503 even
    though earlier pages in the same route succeeded. On retry, first fetch the
    matching visible paged URL as a browser would have done, then re-request the
    CSI URL using that visible URL as Referer.
    """
    headers = _csi_headers(referer_url)
    r = session.get(page_url, headers=headers)

    attempts = 0
    while r.status_code in retry_statuses and attempts < max_retries:
        attempts += 1

        if verbose:
            print(
                f"  CSI request returned HTTP {r.status_code}; "
                f"warming visible referer and retrying ({attempts}/{max_retries})"
            )

        if retry_delay_seconds > 0:
            time.sleep(retry_delay_seconds * attempts)

        # Warm the visible paged URL. This may refresh cookies/cache state and
        # makes the subsequent XHR look more like browser navigation.
        warm = session.get(
            referer_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": _absolute_url(referer_url),
            },
        )

        if verbose:
            print(f"  visible referer warm-up status: {warm.status_code}")

        r = session.get(page_url, headers=headers)

    return r


def fetch_films_browser_csi_pages(
    session,
    url: str,
    first_html: str,
    *,
    start_page: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
    max_csi_pages: int = 25,
    csi_max_retries: int = 2,
    csi_retry_delay_seconds: float = 1.25,
) -> List[str]:
    """
    Fetch CSI pages for true films-browser shell pages.

    CSI has no reliable next-page marker, so this always uses a safety cap.
    The cap is a count of CSI pages fetched from start_page, not an absolute
    Letterboxd page number. This matters when resuming from a deep page such as
    start_page=24.

    A page-specific visible URL is used as Referer for each CSI request because
    deep CSI pagination is more likely to 403 when every XHR claims to come
    from the route root.
    """
    csi_src = extract_films_browser_csi_src(first_html, url) or films_browser_csi_src_from_url(url)
    if not csi_src:
        return [first_html] if first_html else []

    html_docs: List[str] = []
    fetched = 0
    page = start_page

    while True:
        if pages is not None and fetched >= pages:
            if verbose:
                print(f"Stopping: reached requested page limit ({pages})")
            break

        if fetched >= max_csi_pages:
            if verbose:
                print(f"Stopping: reached CSI safety cap ({max_csi_pages} pages fetched from start_page={start_page})")
            break

        page_url = csi_url_for_page(csi_src, page)
        referer_url = ensure_page(url, page=page)

        if verbose:
            print(f"Fetching CSI page {page}: {page_url}")
            print(f"  CSI referer: {referer_url}")

        r = _fetch_csi_response_with_retry(
            session,
            page_url,
            referer_url=referer_url,
            verbose=verbose,
            max_retries=csi_max_retries,
            retry_delay_seconds=csi_retry_delay_seconds,
        )
        raise_for_status_with_preview(r, context=f"CSI page {page}", verbose=verbose)

        html = r.text
        lazy_count = html.count('data-component-class="LazyPoster"')

        if verbose:
            print(f"  received {len(html):,} chars, {lazy_count} LazyPoster blocks")

        if lazy_count == 0:
            if verbose:
                print("Stopping: CSI page had no LazyPoster blocks")
            break

        html_docs.append(html)
        fetched += 1

        if lazy_count < 72:
            if verbose:
                print("Stopping: CSI page appears to be terminal page")
            break

        page += 1

    return html_docs



# ============================================================
# Dispatch / extraction
# ============================================================

def detect_pagination_type(
    session,
    url: str,
    *,
    verbose: bool = False,
    browse_transform: Optional["BrowseTransform"] = None,
) -> Tuple[str, str]:
    """
    Fetch the first visible page and decide how it should be handled.

    Returns:
      ("standard", html)
      ("cursor", html)
      ("csi", html)
      ("single", html)

    Search pages are treated as cursor pages because their useful result DOM may
    live behind /s/search/... rather than in the initially fetched document.
    """
    url = apply_browse_transform(url, browse_transform)

    path = _path_from_url_or_path(url)

    if path.startswith("/search/") or path.startswith("/s/search/"):
        if verbose:
            print("Detected search URL; using cursor pagination")

        html, _ = fetch_next_cursored_page(
            session,
            url,
            verbose=verbose,
        )
        return "cursor", html

    if verbose:
        print(f"Fetching initial visible page: {url}")

    r = session.get(url)

    if getattr(r, "status_code", None) and r.status_code >= 400:
        fallback_csi_src = films_browser_csi_src_from_url(url)

        try:
            raise_for_status_with_preview(r, context="initial visible page", verbose=verbose)
        except Exception:
            if fallback_csi_src:
                if verbose:
                    print(
                        "Initial visible films-browser page failed; "
                        f"falling back to synthesized CSI endpoint: {fallback_csi_src}"
                    )
                return "csi", ""
            raise

    html = r.text

    if verbose:
        lazy_count = html.count('data-component-class="LazyPoster"')
        print(f"  received {len(html):,} chars, {lazy_count} LazyPoster blocks")

    # Prefer standard pagination whenever the initial document exposes it.
    info = parse_pagination(html)
    if info.has_pagination and info.next_href:
        if verbose:
            print("Detected standard pagination")
        return "standard", html

    # Only use films-browser CSI when the initial document is a true shell.
    csi_src = extract_films_browser_csi_src(html, url)
    if csi_src:
        if fetch_standard_probe_has_films(session, url, verbose=verbose):
            if verbose:
                print("Detected standard pagination via explicit /page/1/ probe")
            return "standard", html

        if verbose:
            print(f"Detected films-browser CSI shell: {csi_src}")
        return "csi", html

    if "data-cursor" in html:
        if verbose:
            print("Detected cursor pagination")
        return "cursor", html

    if verbose:
        print("Detected single page")

    return "single", html


def fetch_from_pages(
    session,
    url: str,
    extract_fn: Optional[Callable[[str], List[T]]] = None,
    *,
    start_page: int = 1,
    pages: Optional[int] = None,
    verbose: bool = False,
    extract_fn_args: Optional[Any] = None,
    browse_transform: Optional["BrowseTransform"] = None,
) -> List[str] | List[T]:
    """
    Fetch all relevant pages/fragments for a URL.

    If extract_fn is None:
        returns List[str] of HTML documents/fragments.

    If extract_fn is provided:
        applies extract_fn(html) to each page and returns a flattened list.

    If browse_transform is provided:
        first applies filter_sort.transform_letterboxd_url(url, browse_transform),
        then paginates the transformed URL.
    """
    url = apply_browse_transform(url, browse_transform)

    page_type, first_html = detect_pagination_type(
        session,
        url,
        verbose=verbose,
    )

    if page_type == "standard":
        html_pages = fetch_standard_pages(
            session,
            url,
            start_page=start_page,
            pages=pages,
            verbose=verbose,
        )

    elif page_type == "cursor":
        html_pages = fetch_cursored_pages(
            session,
            url,
            pages=pages,
            verbose=verbose,
        )

    elif page_type == "csi":
        html_pages = fetch_films_browser_csi_pages(
            session,
            url,
            first_html,
            start_page=start_page,
            pages=pages,
            verbose=verbose,
        )

    elif page_type == "single":
        html_pages = [first_html]

    else:
        raise ValueError(f"Unknown pagination type: {page_type}")

    if extract_fn is None:
        return html_pages

    out: List[T] = []

    for i, html in enumerate(html_pages, start=1):
        if extract_fn_args:
            extracted = extract_fn(html, **extract_fn_args)
        else:
            extracted = extract_fn(html)
        out.extend(extracted)

        if verbose:
            print(f"Extracted {len(extracted)} records from page {i}")

    if verbose:
        print(f"Total extracted records: {len(out)}")

    return out