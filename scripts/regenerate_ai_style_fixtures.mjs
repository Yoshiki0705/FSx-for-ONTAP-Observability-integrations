#!/usr/bin/env node
// Regenerates the expected values in scripts/tests/fixtures/ai_style_strong.json.
//
// Requires Node and markdown-it (bundled with markdownlint-cli2). CI does not run this script:
// the test reads the generated JSON only, so the Python checks stay stdlib-only and copyable.
//
// The metric is the one tools/ai_style_rules.py computes: render to HTML, drop <pre>...</pre> and
// <code>...</code>, drop HTML comments and tags, decode entities, then count non-overlapping "**".
// Only the expected values are rewritten. The markdown of each case is never changed.
//
// Usage:
//   node scripts/regenerate_ai_style_fixtures.mjs                 # rewrite the fixture file
//   node scripts/regenerate_ai_style_fixtures.mjs --corpus a.md   # print "count<TAB>path" per file

import { execSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);

function resolveMarkdownIt() {
  try {
    return dirname(require.resolve("markdown-it/package.json"));
  } catch {
    const root = execSync("npm root -g", { encoding: "utf8" }).trim();
    return join(root, "markdownlint-cli2", "node_modules", "markdown-it");
  }
}

const markdownItDir = resolveMarkdownIt();
const markdownit = require(markdownItDir);
const markdownItVersion = JSON.parse(readFileSync(join(markdownItDir, "package.json"), "utf8")).version;
const md = markdownit({ html: true });

const ENTITIES = { "&amp;": "&", "&lt;": "<", "&gt;": ">", "&quot;": '"', "&#39;": "'" };

function decode(text) {
  return text
    .replace(/&#x([0-9a-f]+);/gi, (_, hex) => String.fromCodePoint(parseInt(hex, 16)))
    .replace(/&#([0-9]+);/g, (_, dec) => String.fromCodePoint(parseInt(dec, 10)))
    .replace(/&(?:amp|lt|gt|quot|#39);/g, (entity) => ENTITIES[entity]);
}

export function literalDoubleStars(markdown) {
  const visible = decode(
    md
      .render(markdown)
      .replace(/<pre[\s>][\s\S]*?<\/pre>/g, "")
      .replace(/<code[\s>][\s\S]*?<\/code>/g, "")
      .replace(/<!--[\s\S]*?-->/g, "")
      .replace(/<[^>]*>/g, ""),
  );
  return visible.split("**").length - 1;
}

// Frontmatter is not Markdown, and markdown-it would render it as a rule and a heading.
// Blank lines keep the line count the same as the Python side, which removes it the same way.
function stripFrontmatter(text) {
  const lines = text.split("\n");
  if (lines[0].trim() !== "---") return text;
  const end = lines.findIndex((line, index) => index > 0 && line.trim() === "---");
  if (end < 0) return text;
  return [...lines.slice(0, end + 1).map(() => ""), ...lines.slice(end + 1)].join("\n");
}

const args = process.argv.slice(2);
if (args[0] === "--corpus") {
  for (const path of args.slice(1)) {
    const count = literalDoubleStars(stripFrontmatter(readFileSync(path, "utf8")));
    process.stdout.write(`${count}\t${path}\n`);
  }
} else {
  const here = dirname(fileURLToPath(import.meta.url));
  const fixture = join(here, "tests", "fixtures", "ai_style_strong.json");
  const data = JSON.parse(readFileSync(fixture, "utf8"));
  data.renderer = `markdown-it ${markdownItVersion} (html: true)`;
  data.cases = data.cases.map((item) => ({
    id: item.id,
    markdown: item.markdown,
    literal_double_stars: literalDoubleStars(item.markdown),
  }));
  writeFileSync(fixture, `${JSON.stringify(data, null, 2)}\n`);
}
