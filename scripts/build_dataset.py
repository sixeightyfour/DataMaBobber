import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql"

START = 2
END = 9999

BATCH_SIZE = 15
REQUEST_DELAY_SECONDS = .5

OUTPUT_PATH = Path("docs/data/scp_articles_detailed.json")

def scp_url(n: int) -> str:
    if n < 1000:
        slug = f"scp-{n:03d}"
    else:
        slug = f"scp-{n:04d}"

    return f"http://scp-wiki.wikidot.com/{slug}"


def build_batch_query(numbers: list[int]) -> str:
    parts = []

    for n in numbers:
        alias = f"p{n:04d}"
        url = scp_url(n)
        parts.append(
            f'''
            {alias}: page(url: "{url}") {{
              url
              attributions {{
                user {{
                  name
                }}
              }}
              wikidotInfo {{
                rating
                createdAt
                tags
                coarseVoteRecords {{
                  timestamp
                  direction
                }}
              }}
            }}
            '''
        )
    return "query SCPFullDatasetBatch {\n" + "\n".join(parts) + "\n}"


def run_query(query: str) -> dict:
    response = requests.post(
        CROM_ENDPOINT,
        json={"query": query},
        timeout=120,
    )
    response.raise_for_status()

    payload = response.json()

    if "errors" in payload:
        raise RuntimeError(payload["errors"])

    return payload["data"]


def normalize_attributions(raw_attributions) -> list[str]:
    if not raw_attributions:
        return ["Unknown user"]

    output = []

    for item in raw_attributions:
        if item is None:
            continue

        if isinstance(item, dict):
            user = item.get("user")

            if isinstance(user, dict):
                value = user.get("name")
            else:
                value = user
            if value:
                output.append(str(value).strip())

            continue

        if isinstance(item, str):
            value = item.strip()

            if value:
                output.append(value)

            continue

        output.append(str(item).strip())

    output = [name for name in output if name]

    if not output:
        return ["Unknown user"]

    seen = set()
    deduped = []

    for name in output:
        key = name.casefold()
        if key in seen:
            continue

        seen.add(key)
        deduped.append(name)

    return deduped


def poster_from_attributions(attributions: list[str]) -> dict:
    first = attributions[0] if attributions else "Unknown user"

    return {
        "name": first,
        "unixName": first,
    }

def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None

    value = value.replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def vote_direction_value(direction) -> int:
    if direction in (1, "+1"):
        return 1
    if direction in (-1, "-1"):
        return -1

    if isinstance(direction, str):
        normalized = direction.strip().lower()

        if normalized in {"up", "upvote", "positive", "for"}:
            return 1

        if normalized in {"down", "downvote", "negative", "against"}:
            return -1

    return 0

def compact_votes(vote_records: list[dict], created_at: datetime) -> list[dict]:
    compact = []
    for vote in vote_records or []:
        timestamp = parse_datetime(vote.get("timestamp"))
        if timestamp is None:
            continue

        direction = vote_direction_value(vote.get("direction"))

        if direction == 0:
            continue

        days_after_creation = (
            timestamp - created_at
        ).total_seconds() / 86400

        compact.append({
            "days_after_creation": round(days_after_creation, 4),
            "direction": direction,
        })
    compact.sort(key=lambda v: v["days_after_creation"])
    return compact


def process_page(n: int, page: dict | None) -> dict | None:
    url = scp_url(n)

    if not page:
        return None

    info = page.get("wikidotInfo")
    if not info:
        return None

    created_at = parse_datetime(info.get("createdAt"))
    if created_at is None:
        return None

    attributions = normalize_attributions(page.get("attributions"))
    return {
        "scp_number": n,
        "url": page.get("url") or url,
        "createdAt": created_at.isoformat(),
        "createdDate": created_at.date().isoformat(),
        "current_rating": info.get("rating"),

        # Multi-author field. Use this for user/coauthor analysis.
        "attributions": attributions,

        # Backward-compatible single-user field.
        # This is the first attribution only.
        "poster": poster_from_attributions(attributions),
        "tags": info.get("tags") or [],
        "votes": compact_votes(info.get("coarseVoteRecords") or [], created_at),
    }

def fetch_dataset() -> list[dict]:
    articles = []
    numbers = list(range(START, END + 1))

    for i in range(0, len(numbers), BATCH_SIZE):
        batch = numbers[i:i + BATCH_SIZE]
        query = build_batch_query(batch)

        print(f"Fetching SCP-{batch[0]:03d} through SCP-{batch[-1]:04d}")

        data = run_query(query)

        for n in batch:
            alias = f"p{n:04d}"
            article = process_page(n, data.get(alias))
            if article:
                articles.append(article)

        time.sleep(REQUEST_DELAY_SECONDS)

    articles.sort(key=lambda row: row["scp_number"])
    return articles


def save_dataset(articles: list[dict]) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "start": START,
        "end": END,
        "articleCount": len(articles),
        "articles": articles,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Saved dataset to {OUTPUT_PATH}")
    print(f"Articles saved: {len(articles)}")

def main():
    articles = fetch_dataset()
    save_dataset(articles)


if __name__ == "__main__":
    main()
