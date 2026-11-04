from pathlib import Path

from lboxdlib.parsers.activity import DiaryActivity, extract_activity


def test_basic_diary_activity_fixture_parses_rating_and_film():
    html = Path(__file__).parent.joinpath("fixtures/activity_diary_entry.html").read_text(
        encoding="utf-8"
    )

    items = extract_activity(html)
    diaries = [item for item in items if isinstance(item, DiaryActivity)]

    assert len(diaries) == 1
    diary = diaries[0]
    assert diary.ownerSlug == "celfromhell"
    assert diary.ownerDisplayName == "Cel 👽"
    assert diary.film is not None
    assert diary.film.slug == "the-ugly-stepsister"
    assert diary.film.name == "The Ugly Stepsister"
    assert diary.rating == 4.5
    assert diary.ratingText == "★★★★½"
    assert diary.likedFilm is True
