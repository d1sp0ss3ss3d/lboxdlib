from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Literal, Optional


SharePolicy = Literal["You", "Anyone", "Friends"]
ListAction = Literal["ADD", "DELETE", "UPDATE"]

_FILM_POSTER_ID_RE = re.compile(r"/film-poster/(?:\d+/)+(?P<filmId>\d+)-")


def _drop_none(data: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def uid_from_poster(data: Optional[Dict[str, Any]]) -> Optional[str]:
    """
    Extract a Letterboxd film uid from a hydrated FilmSummary poster payload.

    Hydrated list preview FilmSummary objects do not include uid directly, but
    their poster URLs contain the numeric filmId segment.
    """
    if not data:
        return None

    for size in data.get("sizes", []):
        url = size.get("url") if isinstance(size, dict) else None
        if not url:
            continue

        match = _FILM_POSTER_ID_RE.search(url)
        if match:
            return f"film:{match.group('filmId')}"

    return None


def _enum_from_value(
    enum_cls: type[Enum],
    value: Any,
    default: Enum,
) -> Enum:
    if isinstance(value, enum_cls):
        return value

    if value is None:
        return default

    try:
        return enum_cls(value)
    except ValueError:
        return default


class MemberPronoun(Enum):
    THEY_THEIR = "they/their"
    HE_HIS = "he/his"
    HE_THEIR = "he/their"
    SHE_HER = "she/her"
    SHE_THEIR = "she/their"
    XE_XYR = "xe/xyr"
    ZE_HIR = "ze/hir"
    ZE_ZIR = "ze/zir"
    IT_ITS = "it/its"


class MemberPosters(Enum):
    ANY = "Any"
    THEIR = "Only their"
    YOUR = "Only your"
    NO = "No"


@dataclass
class MemberSummary:
    username: str
    givenName: str | None
    familyName: str | None
    slug: str
    location: str
    website: str | None
    bio: str | None
    pronoun: MemberPronoun = MemberPronoun.THEY_THEIR
    posters: MemberPosters = MemberPosters.NO
    includeInMembers: bool = True
    adultContent: bool = False
    avatarUrl: str | None = None

    @property
    def url(self, base: str = "https://letterboxd.com") -> str:
        return f"{base.rstrip('/')}/{self.slug}/"

    @property
    def name(self) -> str:
        parts = [self.givenName, self.familyName]
        return " ".join(part for part in parts if part).strip()

    @property
    def displayName(self) -> str:
        return self.name or self.username

    def to_dict(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "givenName": self.givenName,
            "familyName": self.familyName,
            "slug": self.slug,
            "location": self.location,
            "website": self.website,
            "bio": self.bio,
            "pronoun": self.pronoun.value,
            "posters": self.posters.value,
            "includeInMembers": self.includeInMembers,
            "adultContent": self.adultContent,
            "avatarUrl": self.avatarUrl,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemberSummary":
        return cls(
            username=data["username"],
            givenName=data.get("givenName"),
            familyName=data.get("familyName"),
            slug=data["slug"],
            location=data.get("location") or "",
            website=data.get("website"),
            bio=data.get("bio"),
            pronoun=_enum_from_value(
                MemberPronoun,
                data.get("pronoun"),
                default=MemberPronoun.THEY_THEIR,
            ),
            posters=_enum_from_value(
                MemberPosters,
                data.get("posters"),
                default=MemberPosters.NO,
            ),
            includeInMembers=bool(data.get("includeInMembers", True)),
            adultContent=bool(data.get("adultContent", False)),
            avatarUrl=data.get("avatarUrl"),
        )


@dataclass
class ImageSize:
    width: int
    height: int
    url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ImageSize":
        return cls(
            width=int(data["width"]),
            height=int(data["height"]),
            url=data["url"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "url": self.url,
        }


@dataclass
class ImageSet:
    sizes: List[ImageSize] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ImageSet":
        if not data:
            return cls()

        return cls(
            sizes=[
                ImageSize.from_dict(size)
                for size in data.get("sizes", [])
            ]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sizes": [size.to_dict() for size in self.sizes],
        }


@dataclass
class Link:
    type: str
    id: str
    url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Link":
        return cls(
            type=data["type"],
            id=data["id"],
            url=data["url"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "url": self.url,
        }


@dataclass
class Genre:
    lid: str
    name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Genre":
        return cls(
            lid=data.get("lid") if data.get("lid") is not None else data["id"],
            name=data["name"],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "lid": self.lid,
            "name": self.name,
        }


@dataclass
class PersonSummary:
    lid: str
    name: str
    tmdbid: Optional[str] = None
    poster: Optional[ImageSet] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PersonSummary":
        return cls(
            lid=data.get("lid") if data.get("lid") is not None else data["id"],
            name=data["name"],
            tmdbid=data.get("tmdbid"),
            poster=ImageSet.from_dict(data.get("poster"))
            if data.get("poster") is not None
            else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "lid": self.lid,
            "name": self.name,
            "tmdbid": self.tmdbid,
            "poster": self.poster.to_dict() if self.poster else None,
        })


@dataclass
class FilmSummary:
    lid: str
    name: str

    uid: Optional[str] = None
    link: Optional[str] = None
    releaseYear: Optional[int] = None
    runTime: Optional[int] = None
    rating: Optional[float] = None
    poster: Optional[ImageSet] = None

    directors: List[PersonSummary] = field(default_factory=list)
    genres: List[Genre] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)

    omdb_plot: Optional[str] = None

    # Broad enrichment payloads. These allow modules such as
    # omdb_enrichment and tmdb_enrichment to attach structured metadata to
    # FilmSummary objects and have that metadata survive to_dict().
    omdb: Optional[Dict[str, Any]] = None
    tmdb: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FilmSummary":
        # Hydrated list-preview FilmSummary payloads use "id" for the film lid.
        # Locally serialized FilmSummary records use "lid".
        lid = data.get("lid") if data.get("lid") is not None else data["id"]
        uid = data.get("uid") or uid_from_poster(data.get("poster"))

        return cls(
            lid=str(lid),
            uid=uid,
            name=data.get("name") or str(lid),
            link=data.get("link"),
            releaseYear=data.get("releaseYear"),
            runTime=data.get("runTime"),
            rating=data.get("rating"),
            poster=ImageSet.from_dict(data.get("poster"))
            if data.get("poster") is not None
            else None,
            directors=[
                PersonSummary.from_dict(person)
                for person in data.get("directors", [])
            ],
            genres=[
                Genre.from_dict(genre)
                for genre in data.get("genres", [])
            ],
            links=[
                Link.from_dict(link)
                for link in data.get("links", [])
            ],
            omdb_plot=(
                data.get("omdb_plot")
                or data.get("omdbPlot")
                or (data.get("omdb", {}).get("plot") if isinstance(data.get("omdb"), dict) else None)
                or (data.get("omdb", {}).get("plot_full") if isinstance(data.get("omdb"), dict) else None)
            ),
            omdb=dict(data["omdb"]) if isinstance(data.get("omdb"), dict) else None,
            tmdb=dict(data["tmdb"]) if isinstance(data.get("tmdb"), dict) else None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "lid": self.lid,
            "uid": self.uid,
            "name": self.name,
            "link": self.link,
            "releaseYear": self.releaseYear,
            "runTime": self.runTime,
            "rating": self.rating,
            "poster": self.poster.to_dict() if self.poster else None,
            "directors": [director.to_dict() for director in self.directors],
            "genres": [genre.to_dict() for genre in self.genres],
            "links": [link.to_dict() for link in self.links],
            "omdb_plot": self.omdb_plot,
            "omdb": self.omdb,
            "tmdb": self.tmdb,
        })

    @property
    def filmId(self) -> Optional[str]:
        if not self.uid or not self.uid.startswith("film:"):
            return None
        return self.uid[5:]

    @property
    def film_id(self) -> Optional[str]:
        return self.filmId

    @property
    def title(self) -> str:
        return self.name

    @property
    def display_title(self) -> str:
        if self.releaseYear is None:
            return self.name
        return f"{self.name} ({self.releaseYear})"

    @property
    def year(self) -> Optional[int]:
        return self.releaseYear

    @property
    def runtime(self) -> Optional[int]:
        return self.runTime

    @property
    def url(self) -> Optional[str]:
        for link in self.links:
            if link.type == "letterboxd":
                return link.url
        return self.link

    @property
    def slug(self) -> Optional[str]:
        url = self.url or self.link
        if not url:
            return None
        return url.rstrip("/").split("/")[-1]

    @property
    def imdb_id(self) -> Optional[str]:
        for link in self.links:
            if link.type == "imdb":
                return link.id
        return None

    @property
    def tmdb_id(self) -> Optional[str]:
        for link in self.links:
            if link.type == "tmdb":
                return link.id
        return None

    @property
    def film_uid_url(self) -> Optional[str]:
        if not self.uid:
            return None
        return f"https://letterboxd.com/film/{self.uid}/"

    def _has_named(self, items, value):
        wanted = str(getattr(value, "name", value)).casefold()
        return any(item.name.casefold() == wanted for item in items)

    def is_genre(self, gen):
        return self._has_named(self.genres, gen)

    def is_director(self, director):
        return self._has_named(self.directors, director)

    @classmethod
    def from_soup(cls, tag):
        from lboxdlib.parsers.films import film_summary_from_lazy_poster

        film = film_summary_from_lazy_poster(tag)
        if film is None:
            raise ValueError("Could not build FilmSummary from supplied poster element")
        return film


@dataclass
class ListPreviewEntry:
    film: FilmSummary

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ListPreviewEntry":
        return cls(
            film=FilmSummary.from_dict(data["film"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "film": self.film.to_dict(),
        }


@dataclass
class ListRecord:
    lid: str
    name: str
    filmCount: int
    published: bool
    ranked: bool

    descriptionLbml: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    whenCreated: Optional[str] = None
    whenUpdated: Optional[str] = None
    sharePolicy: Optional[SharePolicy] = None

    previewEntries: List[ListPreviewEntry] = field(default_factory=list)
    links: List[Link] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ListRecord":
        return cls(
            lid=data.get("lid") if data.get("lid") is not None else data["id"],
            name=data["name"],
            filmCount=int(data["filmCount"]),
            published=bool(data["published"]),
            ranked=bool(data["ranked"]),
            descriptionLbml=data.get("descriptionLbml"),
            description=data.get("description"),
            tags=list(data.get("tags", [])),
            whenCreated=data.get("whenCreated"),
            whenUpdated=data.get("whenUpdated"),
            sharePolicy=data.get("sharePolicy"),
            previewEntries=[
                ListPreviewEntry.from_dict(entry)
                for entry in data.get("previewEntries", [])
            ],
            links=[
                Link.from_dict(link)
                for link in data.get("links", [])
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "lid": self.lid,
            "name": self.name,
            "filmCount": self.filmCount,
            "published": self.published,
            "ranked": self.ranked,
            "descriptionLbml": self.descriptionLbml,
            "description": self.description,
            "tags": self.tags,
            "whenCreated": self.whenCreated,
            "whenUpdated": self.whenUpdated,
            "sharePolicy": self.sharePolicy,
            "previewEntries": [
                entry.to_dict()
                for entry in self.previewEntries
            ],
            "links": [link.to_dict() for link in self.links],
        })

    @property
    def url(self) -> Optional[str]:
        for link in self.links:
            if link.type == "letterboxd":
                return link.url
        return None


@dataclass
class ListResponse:
    data: ListRecord
    messages: List[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ListResponse":
        return cls(
            data=ListRecord.from_dict(payload["data"]),
            messages=list(payload.get("messages", [])),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "data": self.data.to_dict(),
            "messages": self.messages,
        }


@dataclass
class ListCommand:
    action: ListAction
    film: Optional[str] = None
    position: Optional[int] = None
    newPosition: Optional[int] = None

    def __post_init__(self) -> None:
        if self.action == "ADD":
            if not self.film:
                raise ValueError("ADD commands require film")

        elif self.action == "DELETE":
            if self.position is None:
                raise ValueError("DELETE commands require position")

        elif self.action == "UPDATE":
            if self.position is None:
                raise ValueError("UPDATE commands require position")
            if self.newPosition is None:
                raise ValueError("UPDATE commands require newPosition")

        else:
            raise ValueError(f"Unsupported list action: {self.action}")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ListCommand":
        return cls(
            action=data["action"],
            film=data.get("film"),
            position=data.get("position"),
            newPosition=data.get("newPosition"),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _drop_none({
            "film": self.film,
            "action": self.action,
            "position": self.position,
            "newPosition": self.newPosition,
        })


@dataclass
class CreateListRequest:
    name: str
    entries: List[ListCommand]

    published: bool = False
    sharePolicy: SharePolicy = "You"
    ranked: bool = False
    description: str = ""
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        valid_share_policies = {"You", "Anyone", "Friends"}

        if not self.name or not self.name.strip():
            raise ValueError("name must not be empty")

        if self.sharePolicy not in valid_share_policies:
            raise ValueError(
                f"sharePolicy must be one of {sorted(valid_share_policies)}"
            )

        if not self.entries:
            raise ValueError("entries must contain at least one command")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CreateListRequest":
        return cls(
            published=bool(data.get("published", False)),
            name=data["name"],
            sharePolicy=data.get("sharePolicy", "You"),
            ranked=bool(data.get("ranked", False)),
            description=data.get("description", ""),
            tags=list(data.get("tags", [])),
            entries=[
                ListCommand.from_dict(entry)
                for entry in data.get("entries", [])
            ],
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "published": self.published,
            "name": self.name,
            "sharePolicy": self.sharePolicy,
            "ranked": self.ranked,
            "description": self.description,
            "tags": self.tags,
            "entries": [
                entry.to_dict()
                for entry in self.entries
            ],
        }


@dataclass
class BatchListOperationRequest:
    entries: List[ListCommand]

    def __post_init__(self) -> None:
        if not self.entries:
            raise ValueError("entries must contain at least one command")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BatchListOperationRequest":
        return cls(
            entries=[
                ListCommand.from_dict(entry)
                for entry in data.get("entries", [])
            ]
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entries": [
                entry.to_dict()
                for entry in self.entries
            ],
        }

@dataclass
class ReviewSummary:
    owner: str | None
    ownerDisplayName: str | None

    filmSlug: str | None
    filmName: str | None
    filmYear: int | None = None
    filmLid: str | None = None
    filmUid: str | None = None

    viewingUid: str | None = None          # e.g. "viewing:1357474780"
    viewingId: int | None = None           # e.g. 1357474780
    reviewLid: str | None = None           # e.g. "eOGg1b"

    reviewUrl: str | None = None           # specific review when known
    reviewsUrl: str | None = None          # /user/film/slug/reviews/
    reviewCountForFilm: int | None = None  # known only when page exposes it
    isNumberedReview: bool = False         # /user/film/slug/1/

    date: str | None = None                # YYYY-MM-DD when available
    viewingAction: str | None = None       # "Watched" / "Rewatched"

    rating: float | None = None
    ratingText: str | None = None          # "★★★★½", "★★★★★", etc.
    likedFilm: bool = False

    reviewText: str | None = None
    reviewHtml: str | None = None
    fullTextUrl: str | None = None

    likesCount: int | None = None
    likesUrl: str | None = None

    commentsCount: int | None = None
    commentsUrl: str | None = None

    tags: list[str] = field(default_factory=list)


    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewSummary":
        return cls(
            owner=data.get("owner"),
            ownerDisplayName=data.get("ownerDisplayName"),
            filmSlug=data.get("filmSlug"),
            filmName=data.get("filmName"),
            filmYear=data.get("filmYear"),
            filmLid=data.get("filmLid"),
            filmUid=data.get("filmUid"),
            viewingUid=data.get("viewingUid"),
            viewingId=data.get("viewingId"),
            reviewLid=data.get("reviewLid"),
            reviewUrl=data.get("reviewUrl"),
            reviewsUrl=data.get("reviewsUrl"),
            reviewCountForFilm=data.get("reviewCountForFilm"),
            isNumberedReview=bool(data.get("isNumberedReview", False)),
            date=data.get("date"),
            viewingAction=data.get("viewingAction"),
            rating=data.get("rating"),
            ratingText=data.get("ratingText"),
            likedFilm=bool(data.get("likedFilm", False)),
            reviewText=data.get("reviewText"),
            reviewHtml=data.get("reviewHtml"),
            fullTextUrl=data.get("fullTextUrl"),
            likesCount=data.get("likesCount"),
            likesUrl=data.get("likesUrl"),
            commentsCount=data.get("commentsCount"),
            commentsUrl=data.get("commentsUrl"),
            tags=list(data.get("tags") or []),
        )


@dataclass(slots=True)
class DiaryEntry:
    # ---- identity ----
    viewing_id: int                 # e.g. 1192118314
    owner: str                      # e.g. "grryboy"

    # ---- film identity ----
    film_slug: str                  # "the-rip-2026"
    film_lid: Optional[str] = None  # e.g. "NP5y"
    film_uid: str = None                    # TMDb film ID (Letterboxd internal)

    # ---- diary content ----
    rating: Optional[int] = None    # 1–10 scale
    review: Optional[str] = None

    # ---- viewing metadata ----
    viewing_date: Optional[str] = None  # YYYY-MM-DD
    tags: Optional[str] = None           # raw tag string (comma-separated)

    # ---- state flags (DOM-derived) ----
    watched: Optional[bool] = None
    in_watchlist: Optional[bool] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "DiaryEntry":
        return cls(**data)