import fs from "node:fs/promises";
import path from "node:path";

const CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql";

const OUTPUT_PATH = "docs/data/j_ex_urls.json";

const SITE_URL_PREFIX = "http://scp-wiki.wikidot.com";

const REQUEST_DELAY_MS = 250;

const QUERIES = [
  {
    type: "-J",
    tag: "joke",
    pageSize: 500,
  },
  {
    type: "-EX",
    tag: "explained",
    pageSize: 100,
  },
];

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function normalizeUrl(url) {
  if (!url) return "";

  return String(url)
    .trim()
    .replace(/^https:\/\/scp-wiki\.wikidot\.com/i, "http://scp-wiki.wikidot.com")
    .replace(/\/+$/, "");
}

function buildTaggedScpQuery({ tag, pageSize, after }) {
  const afterPart = after ? `after: ${JSON.stringify(after)}` : "";

  return `
    query TaggedScpPages {
      pages(
        first: ${pageSize}
        ${afterPart}
        sort: { key: CREATED_AT, order: DESC }
        filter: {
          url: { startsWith: "${SITE_URL_PREFIX}" }
          wikidotInfo: {
            _and: [
              { tags: { eq: ${JSON.stringify(tag)} } }
              { tags: { eq: "scp" } }
            ]
          }
        }
      ) {
        edges {
          cursor
          node {
            url
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
  `;
}

async function runQuery(query) {
  const response = await fetch(CROM_ENDPOINT, {
    method: "POST",
    headers: {
      "content-type": "application/json",
    },
    body: JSON.stringify({ query }),
  });

  if (!response.ok) {
    throw new Error(`GraphQL request failed: ${response.status} ${response.statusText}`);
  }

  const payload = await response.json();

  if (payload.errors) {
    throw new Error(JSON.stringify(payload.errors, null, 2));
  }

  return payload.data;
}

async function fetchUrlsForQuery(queryConfig) {
  const rows = [];
  let after = null;
  let pageNumber = 1;

  while (true) {
    const query = buildTaggedScpQuery({
      tag: queryConfig.tag,
      pageSize: queryConfig.pageSize,
      after,
    });

    console.log(`Fetching ${queryConfig.type} page ${pageNumber}`);

    const data = await runQuery(query);
    const pages = data.pages;
    const edges = pages.edges || [];

    for (const edge of edges) {
      const url = normalizeUrl(edge?.node?.url);

      if (!url) {
        continue;
      }

      rows.push({
        url,
        type: queryConfig.type,
        sourceTag: queryConfig.tag,
      });
    }

    if (!pages.pageInfo?.hasNextPage) {
      break;
    }

    after = pages.pageInfo.endCursor;
    pageNumber += 1;

    await sleep(REQUEST_DELAY_MS);
  }

  return rows;
}

async function saveOutput(rows) {
  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });

  const byUrlAndType = new Map();

  for (const row of rows) {
    const key = `${row.type}:${row.url}`;

    if (!byUrlAndType.has(key)) {
      byUrlAndType.set(key, row);
    }
  }

  const deduped = [...byUrlAndType.values()].sort((a, b) => {
    if (a.type !== b.type) {
      return a.type.localeCompare(b.type);
    }

    return a.url.localeCompare(b.url);
  });

  const payload = {
    generatedAt: new Date().toISOString(),
    source: CROM_ENDPOINT,
    siteUrlPrefix: SITE_URL_PREFIX,
    queryLogic: {
      "-J": {
        tags: ["joke", "scp"],
      },
      "-EX": {
        tags: ["explained", "scp"],
      },
    },
    requestedTypes: ["-J", "-EX"],
    urlCount: deduped.length,
    countsByType: {
      "-J": deduped.filter(row => row.type === "-J").length,
      "-EX": deduped.filter(row => row.type === "-EX").length,
    },
    urls: deduped,
  };

  await fs.writeFile(
    OUTPUT_PATH,
    JSON.stringify(payload, null, 2),
    "utf8",
  );

  console.log(`Saved ${deduped.length} URLs to ${OUTPUT_PATH}`);
  console.log(payload.countsByType);
}

async function main() {
  const allRows = [];

  for (const queryConfig of QUERIES) {
    const rows = await fetchUrlsForQuery(queryConfig);
    console.log(`${queryConfig.type}: fetched ${rows.length} URLs`);
    allRows.push(...rows);
  }

  await saveOutput(allRows);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});