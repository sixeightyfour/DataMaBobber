import fs from "node:fs/promises";
import path from "node:path";

const CROM_ENDPOINT = "https://apiv1.crom.avn.sh/graphql";

const INPUT_MARKDOWN_PATH = "scripts/j_arc_pages.md";
const OUTPUT_PATH = "docs/data/j_arc_articles.json";

const BATCH_SIZE = 15;
const REQUEST_DELAY_MS = 250;

const BLACKLISTED_CHILD_URLS = new Set([
  "http://scp-wiki.wikidot.com/fragment:scp-8980-1",
]);

const NON_CONTENT_BLOCKS = new Set([
  "code",
  "html",
  "embed",
  "math",
]);

const NON_CONTENT_STANDALONE_BLOCKS = new Set([
  "toc",
  "f toc",
  "footnoteblock",
  "image",
  "=image",
  "file",
  "iframe",
  "module",
  "module654",
  "include",
  "include-messy",
  "include-elements",
]);

const CONTENT_WRAPPER_BLOCKS = new Set([
  "div",
  "div_",
  "span",
  "span_",
  "blockquote",
  "quote",
  "collapsible",
  "tabview",
  "tabs",
  "tab",
  "table",
  "row",
  "cell",
  "hcell",
  "paragraph",
  "p",
  "bold",
  "b",
  "strong",
  "italics",
  "i",
  "em",
  "emphasis",
  "underline",
  "u",
  "strikethrough",
  "s",
  "del",
  "deletion",
  "ins",
  "insertion",
  "monospace",
  "tt",
  "mono",
  "mark",
  "highlight",
  "size",
  "hidden",
  "invisible",
  "anchor",
  "anchor_",
  "a",
  "a_",
  "*anchor",
  "*anchor_",
  "*a",
  "*a_",
  "ruby",
  "rt",
  "rubytext",
  "subscript",
  "sub",
  "superscript",
  "sup",
  "super",
]);

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function normalizeUrl(url) {
  if (!url) return "";
  return String(url).trim().replace(/\/+$/, "");
}

function isBlacklistedChildUrl(url) {
  return BLACKLISTED_CHILD_URLS.has(normalizeUrl(url));
}

function wikidotUrlFromSlug(slug) {
  return `http://scp-wiki.wikidot.com/${normalizeSlug(slug)}`;
}

function normalizeSlug(rawSlug) {
  return rawSlug
    .trim()
    .replace(/\\/g, "")
    .replace(/&#x20;/gi, " ")
    .replace(/\s+/g, "-")
    .toLowerCase();
}

function extractSlugFromTripleBracket(inner) {
  const beforePipe = inner.split("|")[0].trim();
  return normalizeSlug(beforePipe);
}

function extractTitleFromLine(line, slug) {
  const afterBracket = line.replace(/^.*?\]\]\]/, "").trim();

  const cleaned = afterBracket
    .replace(/^[-–—]\s*/, "")
    .replace(/\\$/, "")
    .replace(/<[^>]+>/g, "")
    .replace(/&#x20;/g, " ")
    .trim();

  return cleaned || slug;
}

function parseJArcMarkdown(markdown) {
  const rows = [];
  let currentKind = null;

  const lines = markdown.split(/\r?\n/);

  for (const line of lines) {
    const upper = line.toUpperCase();

    if (upper.includes("-J ARTICLES")) {
      currentKind = "J";
      continue;
    }

    if (upper.includes("ARC ARTICLES")) {
      currentKind = "ARC";
      continue;
    }

    if (!currentKind) {
      continue;
    }

    const matches = [...line.matchAll(/\[\[\[([\s\S]*?)\]\]\]/g)];

    for (const match of matches) {
      const inner = match[1];

      if (!inner || inner.includes("a href=")) {
        continue;
      }

      const slug = extractSlugFromTripleBracket(inner);

      if (!slug) {
        continue;
      }

      rows.push({
        kind: currentKind,
        slug,
        url: wikidotUrlFromSlug(slug),
        listed_title: extractTitleFromLine(line, slug),
      });
    }

    // Special case for the line with raw [[a href="taboo-j"]] syntax.
    const hrefMatch = line.match(/\[\[a\s+href=["']([^"']+)["']\]\]/i);
    if (hrefMatch && currentKind) {
      const slug = normalizeSlug(hrefMatch[1]);

      rows.push({
        kind: currentKind,
        slug,
        url: wikidotUrlFromSlug(slug),
        listed_title: "Taboo-J",
      });
    }
  }

  const seen = new Set();
  const deduped = [];

  for (const row of rows) {
    const key = `${row.kind}:${row.slug.toLowerCase()}`;

    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    deduped.push(row);
  }

  return deduped;
}

function graphqlString(value) {
  return JSON.stringify(value);
}

function buildBatchQuery(pages) {
  const parts = pages.map((page, index) => {
    const alias = `p${String(index).padStart(3, "0")}`;

    return `
      ${alias}: page(url: ${graphqlString(page.url)}) {
        url
        attributions {
          user {
            name
          }
        }
        wikidotInfo {
          rating
          createdAt
          tags
          source
          coarseVoteRecords {
            timestamp
            direction
          }
          children {
            url
            wikidotInfo {
              source
            }
          }
        }
      }
    `;
  });

  return `query JArcDatasetBatch {\n${parts.join("\n")}\n}`;
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

function parseDate(value) {
  if (!value) return null;

  const date = new Date(value);

  if (Number.isNaN(date.getTime())) {
    return null;
  }

  return date;
}

function voteDirectionValue(direction) {
  if (direction === 1 || direction === "+1") {
    return 1;
  }

  if (direction === -1 || direction === "-1") {
    return -1;
  }

  if (typeof direction === "string") {
    const normalized = direction.trim().toLowerCase();

    if (["up", "upvote", "positive", "for"].includes(normalized)) {
      return 1;
    }

    if (["down", "downvote", "negative", "against"].includes(normalized)) {
      return -1;
    }
  }

  return 0;
}

function normalizeAttributions(rawAttributions) {
  if (!rawAttributions || rawAttributions.length === 0) {
    return ["Unknown user"];
  }

  const output = [];

  for (const item of rawAttributions) {
    if (!item) continue;

    const user = item.user;
    const value =
      user && typeof user === "object"
        ? user.name
        : user;

    if (value) {
      output.push(String(value).trim());
    }
  }

  const cleaned = output.filter(Boolean);

  if (cleaned.length === 0) {
    return ["Unknown user"];
  }

  const seen = new Set();
  const deduped = [];

  for (const name of cleaned) {
    const key = name.toLowerCase();

    if (seen.has(key)) {
      continue;
    }

    seen.add(key);
    deduped.push(name);
  }

  return deduped;
}

function posterFromAttributions(attributions) {
  const first = attributions[0] || "Unknown user";

  return {
    name: first,
    unixName: first,
  };
}

function escapeRegex(value) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function removeLicenseboxBlocks(text) {
  return text.replace(
    /\[\[include\s+:scp-wiki:component:license-box\b[\s\S]*?\[\[include\s+:scp-wiki:component:license-box-end\]\]/gi,
    " ",
  );
}

function removeNonContentPairedBlocks(text) {
  for (const blockName of NON_CONTENT_BLOCKS) {
    const pattern = new RegExp(
      String.raw`\[\[\s*${escapeRegex(blockName)}\b[^\]]*\]\][\s\S]*?\[\[\s*/\s*${escapeRegex(blockName)}\s*\]\]`,
      "gi",
    );

    text = text.replace(pattern, " ");
  }

  text = text.replace(
    /\[\[\s*module(?:654)?\s+css\b[^\]]*\]\][\s\S]*?\[\[\s*\/\s*module\s*\]\]/gi,
    " ",
  );

  return text;
}

function removeNonContentStandaloneBlocks(text) {
  const blockNames = [...NON_CONTENT_STANDALONE_BLOCKS]
    .sort((a, b) => b.length - a.length)
    .map(escapeRegex)
    .join("|");

  const pattern = new RegExp(
    String.raw`\[\[\s*(?:${blockNames})\b[^\]]*\]\]`,
    "gi",
  );

  return text.replace(pattern, " ");
}

function stripContentWrapperTags(text) {
  const blockNames = [...CONTENT_WRAPPER_BLOCKS]
    .sort((a, b) => b.length - a.length)
    .map(escapeRegex)
    .join("|");

  const openingPattern = new RegExp(
    String.raw`\[\[\s*(?:${blockNames})\b[^\]]*\]\]`,
    "gi",
  );

  const closingPattern = new RegExp(
    String.raw`\[\[\s*\/\s*(?:${blockNames})\s*\]\]`,
    "gi",
  );

  text = text.replace(openingPattern, " ");
  text = text.replace(closingPattern, " ");

  return text;
}

function convertLinksToVisibleText(text) {
  text = text.replace(/\[\[\[\s*[^\]|]+\s*\|\s*([^\]]+?)\s*\]\]\]/g, "$1");
  text = text.replace(/\[\[\[\s*([^\]]+?)\s*\]\]\]/g, "$1");

  text = text.replace(/\[\s*https?:\/\/[^\s\]]+\s+([^\]]+?)\s*\]/gi, "$1");
  text = text.replace(/\[\s*https?:\/\/[^\]]+\]/gi, " ");
  text = text.replace(/https?:\/\/\S+/gi, " ");

  return text;
}

function removeVariablesAndGeneratedTokens(text) {
  text = text.replace(/%%[^%]+%%/g, " ");
  text = text.replace(/\{\$[^}]+\}/g, " ");

  return text;
}

function stripFormattingSyntax(text) {
  text = text.replace(/##\s*[^|\n#]+?\|/g, " ");

  const replacements = [
    "**",
    "//",
    "__",
    "--",
    "##",
    "@@",
    "^^",
    ",,",
    "{{",
    "}}",
    "~~",
  ];

  for (const token of replacements) {
    text = text.split(token).join(" ");
  }

  text = text.replace(/^\s*\+{1,6}\*?\s+/gm, " ");
  text = text.replace(/^\s*[*#]+\s+/gm, " ");
  text = text.replace(/^\s*>+\s?/gm, " ");
  text = text.replace(/^\s*[-=]{4,}\s*$/gm, " ");

  return text;
}

function cleanSourceText(source) {
  if (!source) return "";

  let text = String(source);

  text = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");

  text = text.replace(/\[!--[\s\S]*?--\]/g, " ");
  text = text.replace(/\A---\n[\s\S]*?\n---\n/g, " ");

  text = removeLicenseboxBlocks(text);
  text = removeNonContentPairedBlocks(text);
  text = removeNonContentStandaloneBlocks(text);

  text = convertLinksToVisibleText(text);
  text = removeVariablesAndGeneratedTokens(text);
  text = stripContentWrapperTags(text);

  text = text.replace(/\[\[\s*\/?[^]]+?\]\]/g, " ");
  text = text.replace(/<[^>]+>/g, " ");

  text = stripFormattingSyntax(text);

  text = text.replace(/\|\|/g, " ");
  text = text.replace(/\|~/g, " ");
  text = text.replace(/~\|/g, " ");

  text = text.replace(/[\[\]{}<>|=]/g, " ");
  text = text.replace(/\s+/g, " ").trim();

  return text;
}

function countWords(text) {
  const matches = text.match(/\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)*\b/g);
  return matches ? matches.length : 0;
}

function countSourceWords(source) {
  return countWords(cleanSourceText(source));
}

function articleWordCount(info) {
  const parentSource = info.source || "";
  const parentWordCount = countSourceWords(parentSource);

  const children = info.children || [];

  let childrenWordCount = 0;
  let childPagesCounted = 0;
  let blacklistedChildrenExcluded = 0;

  for (const child of children) {
    const childUrl = child?.url;
    const childSource = child?.wikidotInfo?.source;

    if (!childSource) {
      continue;
    }

    if (isBlacklistedChildUrl(childUrl)) {
      blacklistedChildrenExcluded += 1;
      continue;
    }

    childrenWordCount += countSourceWords(childSource);
    childPagesCounted += 1;
  }

  return {
    word_count: parentWordCount + childrenWordCount,
    parent_word_count: parentWordCount,
    children_word_count: childrenWordCount,
    child_pages_counted: childPagesCounted,
    blacklisted_children_excluded: blacklistedChildrenExcluded,
  };
}

function compactVotes(voteRecords, createdAt) {
  const compact = [];

  for (const vote of voteRecords || []) {
    const timestamp = parseDate(vote.timestamp);

    if (!timestamp) {
      continue;
    }

    const direction = voteDirectionValue(vote.direction);

    if (direction === 0) {
      continue;
    }

    const daysAfterCreation =
      (timestamp.getTime() - createdAt.getTime()) / 86400000;

    compact.push({
      days_after_creation: Math.round(daysAfterCreation * 10000) / 10000,
      direction,
    });
  }

  compact.sort((a, b) => a.days_after_creation - b.days_after_creation);

  return compact;
}

function processPage(inputPage, page) {
  if (!page) {
    return null;
  }

  const info = page.wikidotInfo;

  if (!info) {
    return null;
  }

  const createdAt = parseDate(info.createdAt);

  if (!createdAt) {
    return null;
  }

  const attributions = normalizeAttributions(page.attributions);
  const wordDetails = articleWordCount(info);

  return {
    dataset_kind: inputPage.kind,
    slug: inputPage.slug,
    listed_title: inputPage.listed_title,

    url: page.url || inputPage.url,
    createdAt: createdAt.toISOString(),
    createdDate: createdAt.toISOString().slice(0, 10),
    current_rating: info.rating,

    attributions,
    poster: posterFromAttributions(attributions),

    tags: info.tags || [],
    word_count: wordDetails.word_count,
    parent_word_count: wordDetails.parent_word_count,
    children_word_count: wordDetails.children_word_count,
    child_pages_counted: wordDetails.child_pages_counted,
    blacklisted_children_excluded: wordDetails.blacklisted_children_excluded,
    votes: compactVotes(info.coarseVoteRecords || [], createdAt),
  };
}

async function fetchDataset(inputPages) {
  const articles = [];

  for (let i = 0; i < inputPages.length; i += BATCH_SIZE) {
    const batch = inputPages.slice(i, i + BATCH_SIZE);
    const query = buildBatchQuery(batch);

    console.log(
      `Fetching ${i + 1}-${Math.min(i + BATCH_SIZE, inputPages.length)} of ${inputPages.length}`,
    );

    const data = await runQuery(query);

    for (let j = 0; j < batch.length; j += 1) {
      const alias = `p${String(j).padStart(3, "0")}`;
      const article = processPage(batch[j], data[alias]);

      if (article) {
        articles.push(article);
      }
    }

    await sleep(REQUEST_DELAY_MS);
  }

  articles.sort((a, b) => {
    if (a.dataset_kind !== b.dataset_kind) {
      return a.dataset_kind.localeCompare(b.dataset_kind);
    }

    return a.slug.localeCompare(b.slug);
  });

  return articles;
}

async function saveDataset(inputPages, articles) {
  await fs.mkdir(path.dirname(OUTPUT_PATH), { recursive: true });

  const payload = {
    generatedAt: new Date().toISOString(),
    sourceMarkdown: INPUT_MARKDOWN_PATH,
    requestedCount: inputPages.length,
    articleCount: articles.length,
    jCount: articles.filter(article => article.dataset_kind === "J").length,
    arcCount: articles.filter(article => article.dataset_kind === "ARC").length,
    blacklistedChildUrls: [...BLACKLISTED_CHILD_URLS].sort(),
    articles,
  };

  await fs.writeFile(
    OUTPUT_PATH,
    JSON.stringify(payload),
    "utf8",
  );

  console.log(`Saved dataset to ${OUTPUT_PATH}`);
  console.log(`Requested pages: ${inputPages.length}`);
  console.log(`Articles saved: ${articles.length}`);
  console.log(`-J articles saved: ${payload.jCount}`);
  console.log(`-ARC articles saved: ${payload.arcCount}`);
}

async function main() {
  const markdown = await fs.readFile(INPUT_MARKDOWN_PATH, "utf8");
  const inputPages = parseJArcMarkdown(markdown);

  console.log(`Parsed ${inputPages.length} page slugs from ${INPUT_MARKDOWN_PATH}`);

  const articles = await fetchDataset(inputPages);

  await saveDataset(inputPages, articles);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
