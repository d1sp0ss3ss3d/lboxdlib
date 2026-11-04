import time

from curl_cffi import requests

from lboxdlib.auth import get_csrf


# batch rating
def rate_by_lid(session, lids: list[str], rating: float, delay: float = 0.75, dry_run: bool = False) -> list[dict]:
    """
    PATCH Letterboxd ratings for a list of Letterboxd film lids.

    Args:
        lids: list[str], e.g. ["1NTU", "2auI", "1w9q"]
        rating: float, e.g. 0.5, 1.0, 4.5, 5.0
        delay: seconds to wait between requests
        dry_run: if True, print what would be sent without sending

    Returns:
        list[dict] result records
    """
    if not isinstance(lids, list):
        raise TypeError("lids must be a list of strings")

    if not lids:
        raise ValueError("lids cannot be empty")

    if isinstance(rating, int) and (rating < 0.5 or rating > 5 or rating * 2 != int(rating * 2)):
        raise ValueError("rating must be a half-star value from 0.5 to 5.0")

    session.headers.update({
        "x-csrf-token": get_csrf(session),
        "X-Requested-With": "XMLHttpRequest",
    })

    results = []

    for index, lid in enumerate(lids, start=1):
        lid = str(lid).strip()

        if not lid:
            results.append({
                "index": index,
                "lid": lid,
                "ok": False,
                "skipped": True,
                "error": "empty lid",
            })
            continue

        url = f"https://letterboxd.com/api/v0/me/rate/{lid}"
        payload = {"rating": rating}

        print(f"{'[DRY RUN]' if dry_run else '[PATCH]'} {index}/{len(lids)} {lid} -> {rating}")

        if dry_run:
            results.append({
                "index": index,
                "lid": lid,
                "ok": True,
                "dry_run": True,
            })
            continue

        try:
            response = session.patch(url, json=payload, timeout=30)
        except requests.RequestException as exc:
            results.append({
                "index": index,
                "lid": lid,
                "ok": False,
                "error": str(exc),
            })
            continue

        if response.status_code == 204:
            results.append({
                "index": index,
                "lid": lid,
                "ok": True,
                "status": response.status_code,
            })
        else:
            results.append({
                "index": index,
                "lid": lid,
                "ok": False,
                "status": response.status_code,
                "response": response.text[:500],
            })
        time.sleep(delay)

    return results
