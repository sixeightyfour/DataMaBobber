import json
import time
from datetime import datetime, timezone
from pathlib import Path

import requests


CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql"

TARGET_TAGS = {"explained", "joke"}

PAGE_SIZE = 250
REQUEST_DELAY_SECONDS = 0.25

OUTPUT_PATH = Path("docs/data/tagged_pages.json")

QUERY = """
query AllPagesForTagFiltering($after: ID, $first: Int!) {
  pages(first: $first, after: $after) {
    edges {
      cursor
      node {
        url
        wikidotInfo {
          rating
          createdAt
          tags
        }
      }
    }
    pageInfo {
      hasNextPage
      endCursor
    }
  }
}
"""


def run_query(after=None) -> dict:
    response = requests.post(
        CROM_ENDPOINT,
        json={
            "query": QUERY,
            "variables": {
                "after": after,
                "first": PAGE_SIZE,
            },
        },
        timeout=60,
    )
    response.raise_for_status()

    payload = response.json()

    if "errors" in payload:
        raise RuntimeError(payload["errors"])

    return payload["data"]["pages"]


def exact_matching_tags(tags: list[str]) -> list[str]:
    return sorted(tag for tag in TARGET_TAGS if tag in tags)


def fetch_tagged_pages() -> tuple[list[dict], int]:
    after = None
    kept = []
    total_seen = 0

    while True:
        page_data = run_query(after=after)
        edges = page_data["edges"]

        for edge in edges:
            node = edge["node"]
            info = node.get("wikidotInfo")

            if not info:
                continue

            tags = info.get("tags") or []
            matched_tags = exact_matching_tags(tags)

            if matched_tags:
                kept.append({
                    "url": node.get("url"),
                    "rating": info.get("rating"),
                    "createdAt": info.get("createdAt"),
                    "tags": tags,
                    "matchedTags": matched_tags,
                })

        total_seen += len(edges)
        print(f"Scanned {total_seen:,} pages; kept {len(kept):,}")

        page_info = page_data["pageInfo"]

        if not page_info["hasNextPage"]:
            break

        after = page_info["endCursor"]
        time.sleep(REQUEST_DELAY_SECONDS)

    kept.sort(key=lambda row: row["url"] or "")

    return kept, total_seen


def save_dataset(rows: list[dict], total_seen: int) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": CROM_ENDPOINT,
        "targetTags": sorted(TARGET_TAGS),
        "pageSize": PAGE_SIZE,
        "totalPagesScanned": total_seen,
        "articleCount": len(rows),
        "countsByTag": {
            tag: sum(1 for row in rows if tag in row["matchedTags"])
            for tag in sorted(TARGET_TAGS)
        },
        "articles": rows,
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))

    print(f"Saved dataset to: {OUTPUT_PATH}")
    print(f"Pages scanned: {total_seen:,}")
    print(f"Tagged pages saved: {len(rows):,}")
    print(f"Counts by tag: {payload['countsByTag']}")


def main():
    rows, total_seen = fetch_tagged_pages()
    save_dataset(rows, total_seen)


if __name__ == "__main__":
    main()
