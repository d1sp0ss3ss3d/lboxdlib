from __future__ import annotations

from dataclasses import dataclass

import pytest

from lboxdlib.relationships import resolve_user_slug


@dataclass
class UserLike:
    slug: str | None = None
    url: str | None = None


def test_resolve_user_slug_accepts_slug_url_relative_url_and_object():
    assert resolve_user_slug("gary") == "gary"
    assert resolve_user_slug("/gary/") == "gary"
    assert resolve_user_slug("https://letterboxd.com/gary/") == "gary"
    assert resolve_user_slug(UserLike(slug="gary")) == "gary"
    assert resolve_user_slug(UserLike(url="https://letterboxd.com/gary/")) == "gary"


def test_resolve_user_slug_rejects_non_member_urls():
    with pytest.raises(ValueError):
        resolve_user_slug("https://letterboxd.com/activity/")
