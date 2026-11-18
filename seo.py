import gzip
import json
import time
import xml.etree.ElementTree as ET
from collections import deque
from html.parser import HTMLParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser


MAX_PAGES = 350
MAX_SITEMAPS = 15
USER_AGENT = "LinkScopeTree/2.0"


def clean_url(url):
    if not url:
        return None
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((
        parts.scheme.lower(),
        parts.netloc.lower(),
        path,
        "",
        "",
    ))


def download(url):
    req = Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    with urlopen(req, timeout=9) as res:
        data = res.read(2_500_001)
        if len(data) > 2_500_000:
            raise ValueError("فایل بیش از حد مجاز بزرگ است.")
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        return data, res.headers.get("Content-Type", ""), res.geturl()


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)
                    break


def sitemap_pages(sitemap_url):
    pending = deque([sitemap_url])
    visited_maps = set()
    seen_pages = set()
    pages = []
    host = None

    while pending and len(visited_maps) < MAX_SITEMAPS and len(pages) < MAX_PAGES:
        cur = pending.popleft()
        if cur in visited_maps:
            continue
        visited_maps.add(cur)

        try:
            data, _, final_url = download(cur)
            root = ET.fromstring(data)
        except Exception:
            if cur == sitemap_url:
                raise
            continue

        if host is None:
            host = urlsplit(final_url).netloc.lower()

        tag = root.tag.rsplit("}", 1)[-1]
        if tag == "sitemapindex":
            for item in root.findall("{*}sitemap"):
                loc = item.findtext("{*}loc")
                if loc:
                    child = urljoin(final_url, loc.strip())
                    if urlsplit(child).netloc.lower() == host:
                        pending.append(child)
        elif tag == "urlset":
            for item in root.findall("{*}url"):
                loc = item.findtext("{*}loc")
                if not loc:
                    continue
                p = clean_url(urljoin(final_url, loc.strip()))
                if p and urlsplit(p).netloc.lower() == host and p not in seen_pages:
                    seen_pages.add(p)
                    pages.append(p)
                if len(pages) >= MAX_PAGES:
                    break

    if not pages:
        raise ValueError("هیچ صفحه‌ای در این سایت‌مپ پیدا نشد.")
    return pages


def get_robots(sample_url):
    parts = urlsplit(sample_url)
    robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        data, _, _ = download(robots_url)
        parser = RobotFileParser()
        parser.parse(data.decode("utf-8", errors="replace").splitlines())
        return parser
    except Exception:
        return None


def calculate_pagerank(pages, outgoing):
    n = len(pages)
    ranks = {p: 1.0 / n for p in pages}
    d = 0.85
    for _ in range(350):
        dangling = sum(ranks[p] for p in pages if not outgoing[p])
        base = (1.0 - d) / n + (d * dangling) / n
        new_ranks = {p: base for p in pages}
        for src in pages:
            targets = outgoing[src]
            if targets:
                share = (d * ranks[src]) / len(targets)
                for t in targets:
                    new_ranks[t] += share
        diff = sum(abs(new_ranks[p] - ranks[p]) for p in pages)
        ranks = new_ranks
        if diff < 1e-9:
            break
    return ranks


def analyze(sitemap_url):
    pages = sitemap_pages(sitemap_url)
    page_set = set(pages)
    host = urlsplit(pages[0]).netloc.lower()
    robots = get_robots(pages[0])

    outgoing = {p: set() for p in pages}
    crawled = set()

    for p in pages:
        if robots and not robots.can_fetch(USER_AGENT, p):
            continue
        try:
            data, ctype, final_url = download(p)
            if "html" not in ctype.lower() and ctype:
                continue
            if urlsplit(final_url).netloc.lower() != host:
                continue

            html = data.decode("utf-8", errors="replace")
            parser = LinkParser()
            parser.feed(html)
            crawled.add(p)

            for href in parser.links:
                tgt = clean_url(urljoin(final_url, href))
                if tgt in page_set and tgt != p:
                    outgoing[p].add(tgt)
        except Exception:
            pass
        time.sleep(0.06)

    incoming = {p: set() for p in pages}
    edges = []
    tree_out = {}

    for src, tgts in outgoing.items():
        tree_out[src] = sorted(list(tgts))
        for tgt in tgts:
            incoming[tgt].add(src)
            edges.append({"source": src, "target": tgt})

    tree_in = {p: sorted(list(incoming[p])) for p in pages}
    ranks = calculate_pagerank(pages, outgoing)

    nodes = [
        {
            "id": p,
            "rank": ranks[p],
            "in_count": len(incoming[p]),
            "out_count": len(outgoing[p]),
            "crawled": p in crawled,
            "orphan": p in crawled and len(incoming[p]) == 0,
        }
        for p in pages
    ]

    return {
        "nodes": nodes,
        "edges": edges,
        "tree_out": tree_out,
        "tree_in": tree_in,
        "crawled_count": len(crawled),
    }


HTML = r'''<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LinkScope | ساختار دقیق لینک‌های داخلی</title>
  <style>
    :root {
      --bg: #f5f8fc;
      --card: #ffffff;
      --ink: #102138;
      --muted: #5e718d;
      --border: #e3ebf4;
      --teal: #088385;
      --teal-light: #e6f6f6;
      --red: #e7586a;
      --blue: #2563eb;
    }

    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background: var(--bg);
      font-family: system-ui, -apple-system, Segoe UI, Roboto, Tahoma, Arial, sans-serif;
      line-height: 1.6;
    }

    .wrap {
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px 16px 80px;
    }

    .hero {
      background: linear-gradient(135deg, #0d213a 0%, #0d5f66 100%);
      color: #fff;
      padding: 30px;
      border-radius: 20px;
      box-shadow: 0 12px 30px rgba(13,33,58,0.15);
    }
    .hero h1 { margin: 0 0 6px; font-size: 26px; }
    .hero p { margin: 0; opacity: 0.9; font-size: 14px; }

    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 22px;
      margin-top: 20px;
      box-shadow: 0 4px 18px rgba(0,0,0,0.03);
    }

    .form {
      display: flex;
      gap: 12px;
      margin-top: 14px;
    }
    input[type="url"], input[type="text"] {
      padding: 12px 16px;
      border: 1px solid #c9d7e6;
      border-radius: 10px;
      font-size: 14px;
      outline: none;
      direction: ltr;
    }
    input[type="url"]:focus, input[type="text"]:focus {
      border-color: var(--teal);
      box-shadow: 0 0 0 3px rgba(8,131,133,0.15);
    }
    .flex-1 { flex: 1; min-width: 0; }

    button {
      padding: 12px 24px;
      background: var(--teal);
      color: #fff;
      border: none;
      border-radius: 10px;
      font-weight: 700;
      cursor: pointer;
      font-size: 14px;
      white-space: nowrap;
    }
    button:hover { background: #066b6c; }
    button:disabled { opacity: 0.6; cursor: wait; }

    .status { margin: 10px 0 0; font-size: 13px; color: var(--muted); min-height: 20px; }
    .status.error { color: var(--red); font-weight: bold; }

    .metrics {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-top: 20px;
    }
    .metric {
      background: #fff;
      border: 1px solid var(--border);
      padding: 18px;
      border-radius: 14px;
    }
    .metric b { font-size: 26px; color: var(--teal); display: block; line-height: 1.3; }
    .metric span { font-size: 12px; color: var(--muted); }

    .hidden { display: none !important; }

    /* Canvas / Graph Section */
    .graph-wrapper {
      position: relative;
      height: 520px;
      border-radius: 14px;
      background: #fafcfe;
      border: 1px solid var(--border);
      overflow: hidden;
      margin-top: 14px;
    }
    canvas {
      width: 100%;
      height: 100%;
      display: block;
      cursor: grab;
      touch-action: none;
    }
    canvas:active { cursor: grabbing; }

    .graph-ctrls {
      position: absolute;
      top: 12px;
      right: 12px;
      display: flex;
      gap: 8px;
      z-index: 5;
    }
    .mini-btn {
      background: rgba(255,255,255,0.92);
      border: 1px solid var(--border);
      color: var(--ink);
      padding: 6px 12px;
      font-size: 12px;
      border-radius: 8px;
      cursor: pointer;
      box-shadow: 0 2px 6px rgba(0,0,0,0.06);
    }
    .mini-btn:hover { background: #fff; }

    .active-badge {
      position: absolute;
      bottom: 12px;
      right: 12px;
      background: rgba(16, 33, 56, 0.9);
      color: #fff;
      padding: 6px 14px;
      border-radius: 8px;
      font-size: 12px;
      direction: ltr;
      max-width: 80%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      pointer-events: none;
    }

    /* Tree Explorer */
    .tree-search-bar {
      display: flex;
      gap: 10px;
      margin-bottom: 16px;
    }
    .tree-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      max-height: 600px;
      overflow-y: auto;
      padding-left: 6px;
    }
    details.tree-item {
      border: 1px solid var(--border);
      border-radius: 10px;
      background: #fff;
      transition: all 0.2s ease;
    }
    details.tree-item[open] {
      border-color: #bcd4e8;
      box-shadow: 0 3px 10px rgba(0,0,0,0.03);
    }
    summary.tree-head {
      padding: 12px 14px;
      font-size: 13px;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      user-select: none;
      direction: ltr;
      text-align: left;
    }
    summary.tree-head:hover { background: #f8fbff; }
    .page-name {
      font-weight: 600;
      color: var(--ink);
      overflow-wrap: anywhere;
      margin-right: 10px;
    }
    .badge {
      display: inline-block;
      padding: 2px 9px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 600;
      white-space: nowrap;
    }
    .badge-teal { background: var(--teal-light); color: var(--teal); }
    .badge-red { background: #fee2e2; color: var(--red); }

    .sub-tree {
      padding: 10px 18px 14px 28px;
      border-top: 1px solid #f0f4f9;
      background: #fafcfe;
      direction: ltr;
      text-align: left;
      font-size: 12px;
    }
    .sub-tree-title {
      font-weight: 700;
      color: var(--muted);
      margin-bottom: 6px;
      font-size: 11px;
      direction: rtl;
      text-align: right;
    }
    .link-row {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 4px 0;
      color: #334155;
    }
    .link-row::before {
      content: "↳";
      color: var(--teal);
      font-size: 14px;
      font-weight: bold;
    }
    .link-row a {
      color: #0d6466;
      text-decoration: none;
      word-break: break-all;
    }
    .link-row a:hover { text-decoration: underline; }
    .empty-note {
      color: var(--muted);
      font-style: italic;
      font-size: 12px;
      padding: 4px 0;
    }

    @media (max-width: 768px) {
      .form { flex-direction: column; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>🕸️ نقشه و ساختار درختی لینک‌های داخلی</h1>
      <p>مشاهده دقیق مبدأ ⬅ مقصد: هر صفحه به چه صفحاتی لینک داده و از چه صفحاتی لینک گرفته است.</p>
    </div>

    <div class="card">
      <h3 style="margin-top:0;">ورود آدرس سایت‌مپ</h3>
      <div style="font-size:13px; color:var(--muted); margin-bottom:8px;">
        آدرس فایل سایت‌مپ سایت مورد نظر را وارد کرده و دکمه بررسی را بزنید (تا 350 صفحه خزش می‌شود):
      </div>
      <form id="crawlForm" class="form">
        <input
          id="sitemapUrl"
          type="url"
          class="flex-1"
          required
          placeholder="https://example.com/sitemap.xml"
        >
        <button id="runBtn" type="submit">بررسی و رسم نمودار</button>
      </form>
      <div id="status" class="status"></div>
    </div>

    <div id="resultBlock" class="hidden">
      <!-- Cards -->
      <div class="metrics">
        <div class="metric"><b id="mPages">0</b><span>صفحات شناسایی‌شده</span></div>
        <div class="metric"><b id="mLinks">0</b><span>تعداد کل پیوندها (Edges)</span></div>
        <div class="metric"><b id="mOrphans" style="color:var(--red);">0</b><span>بدون لینک ورودی (یتیم)</span></div>
        <div class="metric"><b id="mAvg">0</b><span>میانگین خروجی به ازای صفحه</span></div>
      </div>

      <!-- Graph -->
      <div class="card">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px;">
          <h3 style="margin:0;">📍 گراف اتصالات مستقیم (روی هر دایره کلیک کنید تا ارتباطاتش روشن شود)</h3>
          <span style="font-size:12px; color:var(--muted);">حرکت با کشیدن موس • زوم با غلتک موس</span>
        </div>

        <div class="graph-wrapper" id="canvasBox">
          <canvas id="graphCanvas"></canvas>
          <div class="graph-ctrls">
            <button class="mini-btn" id="resetCamBtn">نمای کلی</button>
            <button class="mini-btn" id="clearSelectBtn">نمایش همه یال‌ها</button>
          </div>
          <div id="activeBadge" class="active-badge hidden"></div>
        </div>
      </div>

      <!-- Collapsible Detailed Tree -->
      <div class="card">
        <h3 style="margin-top:0;">🌳 کاوشگر درختی مبدأ و مقصد (Tree Explorer)</h3>
        <p style="font-size:13px; color:var(--muted); margin-top:-4px;">
          برای هر صفحه، دقیقاً ببینید به کدام صفحات دیگر سایت لینک خروجی فرستاده یا از کجا لینک دریافت کرده است:
        </p>

        <div class="tree-search-bar">
          <input
            id="treeSearch"
            type="text"
            class="flex-1"
            placeholder="جستجو در بین آدرس صفحات..."
          >
          <button class="mini-btn" id="expandAllBtn">باز کردن همه</button>
          <button class="mini-btn" id="collapseAllBtn">بستن همه</button>
        </div>

        <div class="tree-list" id="treeList"></div>
      </div>
    </div>
  </div>

  <script>
    const form = document.getElementById("crawlForm");
    const sitemapInput = document.getElementById("sitemapUrl");
    const runBtn = document.getElementById("runBtn");
    const statusBox = document.getElementById("status");
    const resultBlock = document.getElementById("resultBlock");
    const canvas = document.getElementById("graphCanvas");
    const canvasBox = document.getElementById("canvasBox");
    const activeBadge = document.getElementById("activeBadge");

    let networkData = null;
    let nodesPos = [];
    let scale = 1;
    let panX = 0;
    let panY = 0;
    let isDragging = false;
    let startX = 0;
    let startY = 0;
    let selectedNodeId = null;

    form.addEventListener("submit", async e => {
      e.preventDefault();
      runBtn.disabled = true;
      runBtn.textContent = "در حال خزش و ساخت درخت...";
      statusBox.className = "status";
      statusBox.textContent = "در حال دریافت سایت‌مپ، صفحات و تحلیل پیوندها...";
      resultBlock.classList.add("hidden");

      try {
        const res = await fetch("/api/analyze", {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({sitemap: sitemapInput.value.trim()})
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || "خطایی رخ داد.");

        networkData = data;
        renderAll(data);
        statusBox.textContent = "تحلیل با موفقیت انجام شد.";
      } catch (err) {
        statusBox.className = "status error";
        statusBox.textContent = "خطا: " + err.message;
      } finally {
        runBtn.disabled = false;
        runBtn.textContent = "بررسی و رسم نمودار";
      }
    });

    function renderAll(data) {
      // Metrics
      const totalEdges = data.edges.length;
      const orphans = data.nodes.filter(n => n.orphan).length;
      document.getElementById("mPages").textContent = data.nodes.length;
      document.getElementById("mLinks").textContent = totalEdges;
      document.getElementById("mOrphans").textContent = orphans;
      document.getElementById("mAvg").textContent = (totalEdges / (data.nodes.length || 1)).toFixed(1);

      // Render Tree
      buildTreeList(data);

      // Setup 2D Directed Force Graph
      setupGraphPhysics(data);
      resultBlock.classList.remove("hidden");
      resetCamera();
    }

    /* ---------------- TREE LIST BUILDER ---------------- */
    function buildTreeList(data) {
      const container = document.getElementById("treeList");
      container.innerHTML = "";

      const sortedNodes = [...data.nodes].sort((a, b) => b.out_count - a.out_count);

      sortedNodes.forEach(node => {
        const item = document.createElement("details");
        item.className = "tree-item";
        item.dataset.url = node.id.toLowerCase();

        const targets = data.tree_out[node.id] || [];
        const inbounds = data.tree_in[node.id] || [];

        const badgeClass = node.orphan ? "badge-red" : "badge-teal";
        const badgeLabel = node.orphan ? "بدون ورودی" : `${node.out_count} خروجی | ${node.in_count} ورودی`;

        item.innerHTML = `
          <summary class="tree-head">
            <span class="page-name">${escapeHtml(node.id)}</span>
            <span class="badge ${badgeClass}">${badgeLabel}</span>
          </summary>
          <div class="sub-tree">
            <div class="sub-tree-title">🔗 صفحات مقصدی که این صفحه به آن‌ها لینک داده است (${targets.length}):</div>
            ${
              targets.length > 0
                ? targets.map(t => `<div class="link-row"><a href="${t}" target="_blank" rel="noopener">${escapeHtml(t)}</a></div>`).join("")
                : `<div class="empty-note">به هیچ صفحه‌ای از این دامنه لینک خروجی نداده است.</div>`
            }
            <div class="sub-tree-title" style="margin-top:10px;">📥 صفحاتی که به این صفحه لینک داده‌اند (${inbounds.length}):</div>
            ${
              inbounds.length > 0
                ? inbounds.map(src => `<div class="link-row"><a href="${src}" target="_blank" rel="noopener">${escapeHtml(src)}</a></div>`).join("")
                : `<div class="empty-note">هیچ صفحه‌ای به این صفحه لینک نداده است (صفحه یتیم).</div>`
            }
          </div>
        `;
        container.appendChild(item);
      });
    }

    // Tree Search Filter
    document.getElementById("treeSearch").addEventListener("input", e => {
      const q = e.target.value.toLowerCase().trim();
      document.querySelectorAll(".tree-item").forEach(el => {
        el.style.display = el.dataset.url.includes(q) ? "" : "none";
      });
    });

    document.getElementById("expandAllBtn").addEventListener("click", () => {
      document.querySelectorAll(".tree-item").forEach(el => el.open = true);
    });
    document.getElementById("collapseAllBtn").addEventListener("click", () => {
      document.querySelectorAll(".tree-item").forEach(el => el.open = false);
    });

    /* ---------------- GRAPH SIMULATION & DRAWING ---------------- */
    function setupGraphPhysics(data) {
      const N = data.nodes.length;
      const R = 220;

      // Circle layout initially
      nodesPos = data.nodes.map((node, i) => {
        const angle = (i / N) * 2 * Math.PI;
        return {
          id: node.id,
          x: Math.cos(angle) * R,
          y: Math.sin(angle) * R,
          vx: 0,
          vy: 0,
          rank: node.rank,
          in: node.in_count,
          out: node.out_count,
          orphan: node.orphan
        };
      });

      const idMap = new Map(nodesPos.map((n, i) => [n.id, i]));
      const links = data.edges.map(e => ({
        source: idMap.get(e.source),
        target: idMap.get(e.target)
      })).filter(l => l.source !== undefined && l.target !== undefined);

      // Simple 2D force iterations
      for (let step = 0; step < 90; step++) {
        for (let i = 0; i < N; i++) {
          for (let j = i + 1; j < N; j++) {
            let dx = nodesPos[j].x - nodesPos[i].x;
            let dy = nodesPos[j].y - nodesPos[i].y;
            let dist = Math.hypot(dx, dy) || 1;
            if (dist < 320) {
              let force = (320 - dist) / dist * 0.05;
              nodesPos[i].x -= dx * force;
              nodesPos[i].y -= dy * force;
              nodesPos[j].x += dx * force;
              nodesPos[j].y += dy * force;
            }
          }
        }

        for (const l of links) {
          let s = nodesPos[l.source];
          let t = nodesPos[l.target];
          let dx = t.x - s.x;
          let dy = t.y - s.y;
          let dist = Math.hypot(dx, dy) || 1;
          let force = (dist - 90) * 0.035;
          s.x += (dx / dist) * force;
          s.y += (dy / dist) * force;
          t.x -= (dx / dist) * force;
          t.y -= (dy / dist) * force;
        }
      }
    }

    function drawGraph() {
      const w = canvasBox.clientWidth;
      const h = canvasBox.clientHeight;
      const dpr = window.devicePixelRatio || 1;

      canvas.width = w * dpr;
      canvas.height = h * dpr;
      const ctx = canvas.getContext("2d");
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      ctx.save();
      ctx.translate(w / 2 + panX, h / 2 + panY);
      ctx.scale(scale, scale);

      const idMap = new Map(nodesPos.map((n, i) => [n.id, i]));
      const targetsOfSelected = new Set(
        selectedNodeId && networkData.tree_out[selectedNodeId] ? networkData.tree_out[selectedNodeId] : []
      );
      const sourcesOfSelected = new Set(
        selectedNodeId && networkData.tree_in[selectedNodeId] ? networkData.tree_in[selectedNodeId] : []
      );

      // Draw Edges (with arrows)
      networkData.edges.forEach(edge => {
        const s = nodesPos[idMap.get(edge.source)];
        const t = nodesPos[idMap.get(edge.target)];
        if (!s || !t) return;

        let isSelectedEdge = false;
        let strokeColor = "rgba(148, 163, 184, 0.25)";
        let lineWidth = 1;

        if (selectedNodeId) {
          if (edge.source === selectedNodeId) {
            strokeColor = "#088385"; // Outgoing: Teal
            lineWidth = 2.5;
            isSelectedEdge = true;
          } else if (edge.target === selectedNodeId) {
            strokeColor = "#2563eb"; // Incoming: Blue
            lineWidth = 2;
            isSelectedEdge = true;
          } else {
            strokeColor = "rgba(226, 232, 240, 0.15)";
          }
        }

        drawArrow(ctx, s.x, s.y, t.x, t.y, strokeColor, lineWidth, isSelectedEdge);
      });

      // Draw Nodes
      nodesPos.forEach(n => {
        let radius = 6 + Math.min(14, n.in * 1.2);
        let fill = n.orphan ? "#e7586a" : "#0ea5a2";

        if (selectedNodeId) {
          if (n.id === selectedNodeId) {
            fill = "#0d213a";
            radius += 4;
          } else if (targetsOfSelected.has(n.id)) {
            fill = "#088385"; // Target
          } else if (sourcesOfSelected.has(n.id)) {
            fill = "#2563eb"; // Source
          } else {
            fill = "#cbd5e1"; // Dimmed
          }
        }

        ctx.beginPath();
        ctx.arc(n.x, n.y, radius, 0, Math.PI * 2);
        ctx.fillStyle = fill;
        ctx.fill();
        ctx.lineWidth = 2;
        ctx.strokeStyle = "#fff";
        ctx.stroke();

        // Label if selected or target
        if (selectedNodeId === n.id || targetsOfSelected.has(n.id)) {
          ctx.font = "10px Tahoma, sans-serif";
          ctx.fillStyle = "#1e293b";
          const shortText = n.id.replace(/^https?:\/\/[^\/]+/, "") || "/";
          ctx.fillText(shortText, n.x + radius + 4, n.y + 3);
        }
      });

      ctx.restore();
    }

    function drawArrow(ctx, x1, y1, x2, y2, color, width, isSelected) {
      const headlen = isSelected ? 8 : 5;
      const angle = Math.atan2(y2 - y1, x2 - x1);
      const targetRadius = 10;
      const endX = x2 - targetRadius * Math.cos(angle);
      const endY = y2 - targetRadius * Math.sin(angle);

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = width;

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(endX, endY);
      ctx.stroke();

      ctx.beginPath();
      ctx.moveTo(endX, endY);
      ctx.lineTo(endX - headlen * Math.cos(angle - Math.PI / 6), endY - headlen * Math.sin(angle - Math.PI / 6));
      ctx.lineTo(endX - headlen * Math.cos(angle + Math.PI / 6), endY - headlen * Math.sin(angle + Math.PI / 6));
      ctx.closePath();
      ctx.fill();
    }

    /* ---------------- INTERACTIVITY ---------------- */
    canvas.addEventListener("pointerdown", e => {
      isDragging = true;
      startX = e.clientX - panX;
      startY = e.clientY - panY;
      canvas.setPointerCapture(e.pointerId);
    });

    canvas.addEventListener("pointermove", e => {
      if (isDragging) {
        panX = e.clientX - startX;
        panY = e.clientY - startY;
        drawGraph();
      }
    });

    canvas.addEventListener("pointerup", e => {
      isDragging = false;
      const rect = canvas.getBoundingClientRect();
      const mouseX = e.clientX - rect.left;
      const mouseY = e.clientY - rect.top;

      // Check click on node
      const w = canvasBox.clientWidth;
      const h = canvasBox.clientHeight;
      const transformedX = (mouseX - (w / 2 + panX)) / scale;
      const transformedY = (mouseY - (h / 2 + panY)) / scale;

      const clicked = nodesPos.find(n => Math.hypot(n.x - transformedX, n.y - transformedY) < 18);

      if (clicked) {
        selectedNodeId = clicked.id;
        const outList = networkData.tree_out[clicked.id] || [];
        activeBadge.textContent = `صفحه مبدأ: ${clicked.id} ⬅ به ${outList.length} صفحه لینک داده`;
        activeBadge.classList.remove("hidden");

        // Highlight in Tree List and open it
        const targetDetail = Array.from(document.querySelectorAll(".tree-item"))
          .find(el => el.dataset.url === clicked.id.toLowerCase());
        if (targetDetail) {
          targetDetail.open = true;
          targetDetail.scrollIntoView({behavior: "smooth", block: "center"});
        }
      }
      drawGraph();
    });

    canvas.addEventListener("wheel", e => {
      e.preventDefault();
      const zoomFactor = e.deltaY < 0 ? 1.15 : 0.87;
      scale = Math.min(3.5, Math.max(0.35, scale * zoomFactor));
      drawGraph();
    }, {passive: false});

    function resetCamera() {
      scale = 0.95;
      panX = 0;
      panY = 0;
      selectedNodeId = null;
      activeBadge.classList.add("hidden");
      drawGraph();
    }

    document.getElementById("resetCamBtn").addEventListener("click", resetCamera);
    document.getElementById("clearSelectBtn").addEventListener("click", () => {
      selectedNodeId = null;
      activeBadge.classList.add("hidden");
      drawGraph();
    });

    window.addEventListener("resize", drawGraph);

    function escapeHtml(str) {
      return (str || "").replace(/[&<>"']/g, m => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      })[m]);
    }
  </script>
</body>
</html>
'''


class Handler(BaseHTTPRequestHandler):
    def send_body(self, content, content_type, status=200):
        body = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self.send_body(HTML, "text/html; charset=utf-8")
        else:
            self.send_body("Not found", "text/plain; charset=utf-8", 404)

    def do_POST(self):
        if self.path != "/api/analyze":
            self.send_body("Not found", "text/plain; charset=utf-8", 404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            result = analyze(payload["sitemap"])
            self.send_body(json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
        except Exception as err:
            self.send_body(json.dumps({"error": str(err)}, ensure_ascii=False), "application/json; charset=utf-8", 400)


if __name__ == "__main__":
    addr = "http://127.0.0.1:8000"
    print(f"\n[✓] LinkScope Tree is running on: {addr}")
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
        server.server_close()
