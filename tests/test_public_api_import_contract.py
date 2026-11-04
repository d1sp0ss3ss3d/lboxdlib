"""Smoke tests for the stable public import contract.

These tests intentionally exercise the package-root and facade imports that
library users are expected to rely on. A tiny curl_cffi stub keeps this test
suite network-free and avoids requiring the optional runtime HTTP dependency in
minimal packaging-verification environments.
"""

from __future__ import annotations

import sys
import types


def install_curl_cffi_stub(monkeypatch):
    curl_cffi = types.ModuleType("curl_cffi")
    requests = types.ModuleType("curl_cffi.requests")

    class FakeSession:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.cookies = types.SimpleNamespace(jar=[], get=lambda name, default=None: default)

    requests.Session = FakeSession
    curl_cffi.requests = requests
    monkeypatch.setitem(sys.modules, "curl_cffi", curl_cffi)
    monkeypatch.setitem(sys.modules, "curl_cffi.requests", requests)


def test_package_root_exports_official_public_objects(monkeypatch):
    install_curl_cffi_stub(monkeypatch)

    from lboxdlib import (  # noqa: PLC0415
        LBoxdSession,
        FilmHydrator,
        ListManager,
        batch_unfollow_users,
        fetch_reviews,
        fetch_users,
        follow_user,
        hydrate_lids,
        like_review,
        resolve_user_slug,
        select_rated_viewings,
        unfollow_user,
        user_follows,
    )

    assert LBoxdSession is not None
    assert FilmHydrator is not None
    assert ListManager is not None
    assert callable(hydrate_lids)
    assert callable(fetch_reviews)
    assert callable(fetch_users)
    assert callable(select_rated_viewings)
    assert callable(like_review)
    assert callable(follow_user)
    assert callable(unfollow_user)
    assert callable(batch_unfollow_users)
    assert callable(user_follows)
    assert callable(resolve_user_slug)


def test_facade_imports_match_public_api_contract(monkeypatch):
    install_curl_cffi_stub(monkeypatch)

    from lboxdlib.actions import batch_unfollow_users, follow_user, like_review, unfollow_user  # noqa: PLC0415
    from lboxdlib.auth import LBoxdSession, ensure_raw_session, ensure_session  # noqa: PLC0415
    from lboxdlib.fetch import fetch_activity, fetch_reviews, fetch_users  # noqa: PLC0415
    from lboxdlib.lists import (  # noqa: PLC0415
        DEFAULT_GENERATOR_LIST_SLUG,
        DEFAULT_MAX_BATCH_SIZE,
        FilmHydrator,
        ListManager,
        hydrate_lids,
    )
    from lboxdlib.relationships import resolve_user_slug, user_follows  # noqa: PLC0415
    from lboxdlib.select import select_rated_viewings  # noqa: PLC0415

    assert LBoxdSession is not None
    assert callable(ensure_session)
    assert callable(ensure_raw_session)
    assert DEFAULT_GENERATOR_LIST_SLUG
    assert DEFAULT_MAX_BATCH_SIZE > 0
    assert LBoxdSession is not None
    assert FilmHydrator is not None
    assert ListManager is not None
    assert callable(hydrate_lids)
    assert callable(fetch_activity)
    assert callable(fetch_reviews)
    assert callable(fetch_users)
    assert callable(select_rated_viewings)
    assert callable(like_review)
    assert callable(follow_user)
    assert callable(unfollow_user)
    assert callable(batch_unfollow_users)
    assert callable(user_follows)
    assert callable(resolve_user_slug)
