// Runs axe-core on every built page, in light and dark mode at three widths, and checks that no page scrolls
// sideways. A local and CI check only: nothing here is published, and the site itself never loads axe.
//
//   node axe/run.js [site folder]        (default: site/, built first with `python scraper/render.py`)
//
// The pages are served from this process on 127.0.0.1 (so the 404 page's root-relative links work); no other
// address is contacted. The browser is the Chrome the machine already has (`channel: "chrome"`), or the program
// named in AXE_BROWSER. Each page is checked with its script running, every disclosure open and every tiger fact
// shown, so nothing is skipped for being collapsed. The pages' Content-Security-Policy forbids injected scripts,
// so this test browser alone is told to bypass it; the published pages are not changed.
//
// Exits 1 when a rule is violated or a page overflows, 2 when the check could not run.
"use strict";

const fs = require("fs");
const http = require("http");
const path = require("path");

const ROOT = path.resolve(process.argv[2] || path.join(__dirname, "..", "site"));
const THEMES = ["light", "dark"];
const WIDTHS = [320, 375, 1280];
// WCAG 2.0, 2.1 and 2.2 at levels A and AA, plus axe's best-practice rules.
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa", "best-practice"];
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8",
  ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2", ".json": "application/json",
  ".webmanifest": "application/manifest+json", ".xml": "application/xml", ".txt": "text/plain; charset=utf-8",
  ".csv": "text/csv; charset=utf-8",
};

function htmlPages(dir, base = "") {
  const found = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const rel = base + entry.name;
    if (entry.isDirectory()) found.push(...htmlPages(path.join(dir, entry.name), rel + "/"));
    else if (entry.name.endsWith(".html")) found.push("/" + rel.replace(/index\.html$/, ""));
  }
  return found.sort();
}

function serve() {
  const server = http.createServer((req, res) => {
    let file = path.join(ROOT, decodeURIComponent(new URL(req.url, "http://localhost").pathname));
    if (!file.startsWith(ROOT)) { res.writeHead(403).end(); return; }
    if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
    const found = fs.existsSync(file);
    if (!found) file = path.join(ROOT, "404.html");
    res.writeHead(found ? 200 : 404, { "Content-Type": TYPES[path.extname(file)] || "application/octet-stream" });
    fs.createReadStream(file).pipe(res);
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

// Runs in the page: open everything, show every fact, run axe, measure sideways overflow.
async function check(tags) {
  document.querySelectorAll("details").forEach((d) => { d.open = true; });
  document.querySelectorAll(".tiger-fact").forEach((f) => { f.hidden = false; });
  const result = await window.axe.run(document, { runOnly: { type: "tag", values: tags } });
  const root = document.documentElement;
  return {
    violations: result.violations.map((v) => ({
      id: v.id, impact: v.impact, help: v.help, nodes: v.nodes.length, first: v.nodes[0].target.join(" "),
    })),
    incomplete: result.incomplete.reduce((n, v) => n + v.nodes.length, 0),
    overflow: root.scrollWidth > root.clientWidth ? root.scrollWidth - root.clientWidth : 0,
  };
}

async function main() {
  if (!fs.existsSync(path.join(ROOT, "index.html"))) {
    console.error(`No built site at ${ROOT}. Run "python scraper/render.py" first.`);
    return 2;
  }
  const { chromium } = require("playwright-core");
  const axeSource = fs.readFileSync(require.resolve("axe-core/axe.min.js"), "utf8");
  const axeVersion = require("axe-core/package.json").version;
  const pages = htmlPages(ROOT);
  const server = await serve();
  const base = `http://127.0.0.1:${server.address().port}`;
  // The sandbox is off because Ubuntu 24.04 runners block Chrome's; the only input is this repository's own pages.
  const launch = { args: ["--no-sandbox"] };
  if (process.env.AXE_BROWSER) launch.executablePath = process.env.AXE_BROWSER;
  else launch.channel = "chrome";
  const browser = await chromium.launch(launch);
  const problems = [];
  let runs = 0, nodes = 0, incomplete = 0;
  try {
    for (const theme of THEMES) {
      for (const width of WIDTHS) {
        const context = await browser.newContext({
          bypassCSP: true, colorScheme: theme, viewport: { width, height: 900 },
        });
        const page = await context.newPage();
        for (const url of pages) {
          await page.goto(base + url, { waitUntil: "load" });
          await page.addScriptTag({ content: axeSource });
          const result = await page.evaluate(check, TAGS);
          runs += 1;
          incomplete += result.incomplete;
          for (const v of result.violations) {
            nodes += v.nodes;
            problems.push(`${url} (${theme}, ${width}px): ${v.id} [${v.impact}] on ${v.nodes} element(s), ` +
                          `first ${v.first}: ${v.help}`);
          }
          if (result.overflow) problems.push(`${url} (${theme}, ${width}px): scrolls sideways by ${result.overflow}px`);
        }
        await context.close();
      }
    }
  } finally {
    await browser.close();
    server.close();
  }
  const summary = [
    `axe-core ${axeVersion} in ${browser.version()}: ${pages.length} pages, ${THEMES.length} themes, ` +
    `${WIDTHS.length} widths (${WIDTHS.join(", ")} px) = ${runs} runs.`,
    `Problems: ${problems.length} (${nodes} element(s) in rule violations). ` +
    `Needs a person to judge ("incomplete"): ${incomplete} element(s).`,
    ...problems.map((p) => "- " + p),
  ].join("\n");
  console.log(summary);
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, summary + "\n");
  return problems.length ? 1 : 0;
}

main().then((code) => { process.exitCode = code; }, (error) => {
  console.error("The accessibility check could not run:", error.message || error);
  process.exitCode = 2;
});
