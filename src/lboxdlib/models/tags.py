"""Tag records shared by reviews and search results."""

from dataclasses import asdict, dataclass


@dataclass(frozen=True, slots=True)
class TagSummary:
    """A Letterboxd tag link and its display label."""

    name: str
    slug: str
    url: str
    owner: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TagSummary":
        return cls(**data)
