from __future__ import annotations

import sys
import types
import unittest
from dataclasses import dataclass

try:
    import curl_cffi  # noqa: F401
except ModuleNotFoundError:  # Parser/fake-session tests do not require curl_cffi.
    curl_cffi = types.ModuleType("curl_cffi")
    curl_cffi.requests = types.SimpleNamespace(Session=type("Session", (), {}))
    sys.modules["curl_cffi"] = curl_cffi

from lboxdlib.fetch import (
    fetch_activities,
    fetch_activity,
    fetch_review_liker_names,
    fetch_review_likers,
)
from lboxdlib.modules.activity_from_page import (
    FollowActivity,
    ReviewActivity,
    ReviewReferenceActivity,
    extract_activity,
)
from lboxdlib.modules.activity_page_handling import activity_ajax_spec


LIKE_ROW_1 = """
<section class="activity-row -basic" data-activity-id="200">
  <div class="table-activity-user">
    <a class="avatar -a24" href="https://letterboxd.com/alice/"><img alt="Alice"/></a>
  </div>
  <div class="table-activity-description">
    <p class="activity-summary">
      <a class="name" href="https://letterboxd.com/alice/"><strong>Alice</strong></a>
      liked <strong class="name">your</strong>
      <a class="target" href="https://letterboxd.com/viewer/film/alpha/">
        <span class="context">review of</span> Alpha
      </a>
    </p>
  </div>
  <time datetime="2026-07-12T10:00:00Z"></time>
</section>
"""

LIKE_ROW_2 = """
<section class="activity-row -basic" data-activity-id="100">
  <div class="table-activity-user">
    <a class="avatar -a24" href="https://letterboxd.com/alice/"><img alt="Alice"/></a>
  </div>
  <div class="table-activity-description">
    <p class="activity-summary">
      <a class="name" href="https://letterboxd.com/alice/"><strong>Alice</strong></a>
      liked <strong class="name">your</strong>
      <a class="target" href="https://letterboxd.com/viewer/film/beta/">
        <span class="context"><span class="rating rated-8">★★★★</span> review of</span> Beta
      </a>
    </p>
  </div>
  <time datetime="2026-07-12T09:00:00Z"></time>
</section>
"""

FOLLOW_ROW = """
<section class="activity-row -basic" data-activity-id="150">
  <div class="table-activity-user">
    <a class="avatar -a24" href="https://letterboxd.com/bob/"><img alt="Bob"/></a>
  </div>
  <div class="table-activity-description">
    <p class="activity-summary"><a class="name" href="https://letterboxd.com/bob/"><strong>Bob</strong></a> followed <strong class="name">you</strong></p>
  </div>
  <time datetime="2026-07-12T09:30:00Z"></time>
</section>
"""

VISIBLE_PAGE = f"""
<html><body>
  <div id="activity-table">{LIKE_ROW_1}{FOLLOW_ROW}</div>
  <a class="load-more-activity ajax-action"
     data-url="/ajax/activity-pagination/viewer/incoming/">Load older activity</a>
</body></html>
"""

FIRST_FRAGMENT = LIKE_ROW_1 + FOLLOW_ROW
SECOND_FRAGMENT = LIKE_ROW_2

FULL_REVIEW_ROW = r"""
<section class="activity-row -review" data-activity-id="300">
  <div class="table-activity-user"><a class="avatar" href="/critic/"><img alt="Critic"/></a></div>
  <article class="production-viewing" data-object-name="Review" data-object-id="viewing:123" data-owner="critic">
    <div data-component-class="LazyPoster"
         data-postered-identifier='{"uid":"film:9","lid":"abc"}'
         data-item-slug="gamma" data-item-name="Gamma" data-item-link="/film/gamma/"></div>
    <h2 class="primaryname"><a href="/critic/film/gamma/">Gamma</a></h2>
    <div class="attribution-detail"><a class="owner" href="/critic/">Critic</a> reviewed</div>
    <div class="review-actions">
      <div data-component-class="LikeComponent"
           data-likeable-identifier='{"uid":"viewing:123","lid":"rvw","type":"Viewing","typeName":"Review"}'
           data-count="4" data-likes-page="/critic/film/gamma/likes/"></div>
    </div>
    <div class="js-review-body"><p>A review.</p></div>
  </article>
  <time datetime="2026-07-12T08:00:00Z"></time>
</section>
"""


class Cookies(dict):
    pass


@dataclass
class FakeResponse:
    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self) -> None:
        self.cookies = Cookies(
            {
                "letterboxd.signed.in.as": "viewer",
                "com.xk72.webparts.csrf": "current-token",
            }
        )
        self.calls: list[tuple[str, list[tuple[str, str]], dict[str, str]]] = []

    def get(self, url: str, params=None, headers=None):
        params_list = list(params or [])
        header_dict = dict(headers or {})
        self.calls.append((url, params_list, header_dict))

        if url == "https://letterboxd.com/activity/incoming/":
            return FakeResponse(VISIBLE_PAGE)
        if url == "https://letterboxd.com/ajax/activity-pagination/viewer/incoming/":
            after = dict(params_list).get("after")
            return FakeResponse(SECOND_FRAGMENT if after else FIRST_FRAGMENT)
        raise AssertionError(f"Unexpected URL: {url}")




class StreamDiscoverySession(FakeSession):
    def get(self, url: str, params=None, headers=None):
        params_list = list(params or [])
        header_dict = dict(headers or {})
        self.calls.append((url, params_list, header_dict))

        if url == "https://letterboxd.com/activity/you/":
            return FakeResponse(
                '<a class="load-more-activity" '
                'data-url="/ajax/activity-pagination/viewer/observed-you-route/">More</a>'
            )
        if url == "https://letterboxd.com/ajax/activity-pagination/viewer/observed-you-route/":
            return FakeResponse(FULL_REVIEW_ROW)
        raise AssertionError(f"Unexpected URL: {url}")




class MainActivitySession(FakeSession):
    def get(self, url: str, params=None, headers=None):
        params_list = list(params or [])
        header_dict = dict(headers or {})
        self.calls.append((url, params_list, header_dict))

        if url == "https://letterboxd.com/activity/":
            return FakeResponse(
                '<a class="load-more-activity" '
                'data-url="/ajax/activity-pagination/viewer/following/">More</a>'
            )
        if url == "https://letterboxd.com/ajax/activity-pagination/viewer/following/":
            return FakeResponse(FULL_REVIEW_ROW)
        raise AssertionError(f"Unexpected URL: {url}")


class MemberActivitySession(FakeSession):
    def get(self, url: str, params=None, headers=None):
        params_list = list(params or [])
        header_dict = dict(headers or {})
        self.calls.append((url, params_list, header_dict))

        if url == "https://letterboxd.com/critic/activity/":
            return FakeResponse(
                FULL_REVIEW_ROW
                + '<a class="load-more-activity" '
                'data-url="/ajax/activity-pagination/critic/">More</a>'
            )
        raise AssertionError(f"Unexpected URL: {url}")


class IncomingActivityTests(unittest.TestCase):
    def test_incoming_rows_are_typed(self) -> None:
        items = extract_activity(FIRST_FRAGMENT, hydrate_likes=False)
        self.assertIsInstance(items[0], ReviewReferenceActivity)
        self.assertEqual(items[0].action, "liked_review_reference")
        self.assertEqual(items[0].actor.displayName, "Alice")
        self.assertEqual(items[0].reviewOwnerSlug, "viewer")

        self.assertIsInstance(items[1], FollowActivity)
        self.assertEqual(items[1].action, "followed_you")
        self.assertEqual(items[1].targetSlug, "bob")

    def test_full_embedded_review_remains_review_activity(self) -> None:
        items = extract_activity(FULL_REVIEW_ROW, hydrate_likes=False)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ReviewActivity)
        self.assertEqual(items[0].viewingUid, "viewing:123")
        self.assertEqual(items[0].film.name, "Gamma")

    def test_singular_fetch_activity_remains_compatible(self) -> None:
        plural_items = fetch_activities(
            FakeSession(),
            "https://letterboxd.com/activity/incoming/",
            pages=1,
            hydrate_likes=False,
        )
        singular_items = fetch_activity(
            FakeSession(),
            "https://letterboxd.com/activity/incoming/",
            pages=1,
            hydrate_likes=False,
        )
        self.assertEqual(plural_items, singular_items)

    def test_incoming_ajax_request_contract_and_cursor(self) -> None:
        session = FakeSession()
        items = fetch_activities(
            session,
            "https://letterboxd.com/activity/incoming/",
            pages=2,
            hydrate_likes=False,
        )
        self.assertEqual(len(items), 3)

        first_ajax = session.calls[1]
        self.assertEqual(
            first_ajax[0],
            "https://letterboxd.com/ajax/activity-pagination/viewer/incoming/",
        )
        self.assertEqual(first_ajax[1], [("__csrf", "current-token")])
        self.assertEqual(first_ajax[2]["X-Requested-With"], "XMLHttpRequest")
        self.assertEqual(
            first_ajax[2]["Content-Type"],
            "application/x-www-form-urlencoded; charset=UTF-8",
        )
        self.assertEqual(
            first_ajax[2]["Referer"],
            "https://letterboxd.com/activity/incoming/",
        )

        second_ajax = session.calls[2]
        self.assertEqual(
            second_ajax[1],
            [("after", "150"), ("__csrf", "current-token")],
        )

    def test_review_liker_helpers_return_unique_names_and_counts(self) -> None:
        names = fetch_review_liker_names(FakeSession(), pages=2)
        self.assertEqual(names, ["Alice"])

        likers = fetch_review_likers(FakeSession(), pages=2)
        self.assertEqual(len(likers), 1)
        self.assertEqual(likers[0].slug, "alice")
        self.assertEqual(likers[0].likeCount, 2)
        self.assertEqual(likers[0].filmNames, ["Alpha", "Beta"])



    def test_main_activity_shell_falls_back_to_following_ajax(self) -> None:
        session = MainActivitySession()
        items = fetch_activities(
            session,
            "https://letterboxd.com/activity/",
            pages=1,
            hydrate_likes=False,
        )
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ReviewActivity)
        params = session.calls[1][1]
        self.assertIn(("reviews", "true"), params)
        self.assertIn(("diaryEntries", "true"), params)
        self.assertEqual(params[-1], ("__csrf", "current-token"))
        self.assertEqual(
            session.calls[1][0],
            "https://letterboxd.com/ajax/activity-pagination/viewer/following/",
        )

    def test_member_activity_uses_rendered_page_one(self) -> None:
        session = MemberActivitySession()
        items = fetch_activities(
            session,
            "https://letterboxd.com/critic/activity/",
            pages=1,
            hydrate_likes=False,
        )
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ReviewActivity)
        self.assertEqual(len(session.calls), 1)

    def test_you_stream_prefers_observed_load_more_endpoint(self) -> None:
        session = StreamDiscoverySession()
        items = fetch_activities(
            session,
            "https://letterboxd.com/activity/you/",
            pages=1,
            hydrate_likes=False,
        )
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], ReviewActivity)
        self.assertEqual(
            session.calls[1][0],
            "https://letterboxd.com/ajax/activity-pagination/viewer/observed-you-route/",
        )
        self.assertEqual(session.calls[1][1], [("__csrf", "current-token")])
        self.assertEqual(
            session.calls[1][2]["Referer"],
            "https://letterboxd.com/activity/you/",
        )

    def test_explicit_incoming_ajax_url_keeps_correct_referrer(self) -> None:
        spec = activity_ajax_spec(
            FakeSession(),
            "https://letterboxd.com/ajax/activity-pagination/viewer/incoming/"
            "?after=123&__csrf=stale",
        )
        self.assertEqual(spec.referer, "https://letterboxd.com/activity/incoming/")
        self.assertEqual(spec.initial_after, "123")
        self.assertFalse(any(key == "after" for key, _ in spec.base_params))


if __name__ == "__main__":
    unittest.main()
