print("Dumbgeons & Dragons - Site Generator")
print("="*50)

import os
import re
import json
import csv
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET

# =============================================
# CONFIGURATION
# =============================================

# Ad-free feed — used for transcript generation only (not exposed in site)
RSS_URL = "https://rss.art19.com/alternate_feeds/user/dumbgeons-and-dragons/rAHpDXXd23RFF9j4G_DZoiPlDRPl1rWM?access_token=vi4Xv2OizVDreEuwLo-ZbOIwlIDxr89k"

# Ad-supported feed — used for audio player on site
AD_RSS_URL = "https://rss.art19.com/dumbgeons-and-dragons"

# Ad placement CSV from Art19 — update filename when you export a new one
AD_CSV_PATH = r"C:\Users\russd\ad_placements.csv"

# =============================================
# MANUAL EPISODE OVERRIDES
# For episodes where the RSS title doesn't follow
# the standard s##e## format and can't be auto-matched.
# Format: (season, episode): "Exact RSS title"
# =============================================

MANUAL_OVERRIDES = {
    (2, 157): "Halloween at Krorn Manor Part 01",
    (2, 158): "Halloween at Krorn Manor Part 02",
    (2, 159): "Subterfuge in Flaternouge (A Patreon Improv Live Show)",
}

GITHUB_ISSUES_URL = "https://github.com/russdmore-lgtm/dumbdragons-scripts/issues/new"

TRANSCRIPT_FOLDERS = {
    "Season 1": r"C:\Transcripts\Season 1",
    "Season 2": r"C:\Transcripts\Season 2",
    "Season 3": r"C:\Transcripts\Season 3",
    "Season 4": r"C:\Transcripts\Season 4",
}

SITE_OUTPUT = r"C:\Sites\dumbdragons-scripts"
DOCS_OUTPUT = os.path.join(SITE_OUTPUT, "docs")

# =============================================
# FETCH RSS FEED
# =============================================

def fetch_feed(url):
    print("Fetching RSS feed...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        total = 0
        chunks = []
        while True:
            chunk = response.read(1024 * 64)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            print(f"  {total // 1024} KB...", end="\r")
        data = b"".join(chunks)
    print(f"\n  Feed downloaded ({total // 1024} KB)")
    return ET.fromstring(data)

def get_episode_number_from_title(title):
    m = re.search(r'[Ss]0?(\d+)[Ee]0*(\d+)', title)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None

def get_episode_number_from_filename(filename):
    m = re.match(r'S0?(\d+)E0*(\d+)', filename, re.IGNORECASE)
    if m:
        return int(m.group(1)), int(m.group(2))
    return None, None

def build_embed_html(audio_url):
    return f'<source src="{audio_url}" type="audio/mpeg">'

def load_ad_placements(csv_path):
    """Load ad placement timestamps keyed by episode_guid."""
    placements = {}
    if not os.path.exists(csv_path):
        print(f"  WARNING: Ad CSV not found at {csv_path}")
        return placements
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            guid = row["episode_guid"].strip()
            pos_type = row["position_type"].strip()
            start = row["start_position"].strip()
            if pos_type == "postroll" or start == "end":
                continue  # Skip postroll
            try:
                ts = float(start)
            except ValueError:
                continue
            # Force preroll to start of transcript
            if pos_type == "preroll":
                ts = 0.0
            if guid not in placements:
                placements[guid] = []
            placements[guid].append({
                "type": pos_type,
                "timestamp": ts,
            })
    print(f"  Loaded ad placements for {len(placements)} episodes")
    return placements

def fetch_ad_feed(url):
    """Fetch the ad-supported feed to get public audio URLs."""
    print("Fetching ad-supported feed for audio URLs...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as response:
        total = 0
        chunks = []
        while True:
            chunk = response.read(1024 * 64)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            print(f"  {total // 1024} KB...", end="\r")
        data = b"".join(chunks)
    print(f"\n  Ad feed downloaded ({total // 1024} KB)")
    return ET.fromstring(data)

def build_issue_url(title):
    params = urllib.parse.urlencode({
        "title": f"Transcript Error: {title}",
        "body": "**Episode:** " + title + "\n\n**Timestamp / location in transcript:**\n\n**Incorrect text:**\n\n**Correct text:**\n"
    })
    return f"{GITHUB_ISSUES_URL}?{params}"

def strip_html_tags(text):
    """Clean RSS <description> text (Art19 descriptions often contain basic HTML)
    down to plain text suitable for a meta description or schema field."""
    if not text:
        return ""
    text = re.sub(r'<[^>]+>', ' ', text)
    text = (text.replace('&amp;', '&').replace('&quot;', '"')
                .replace('&#39;', "'").replace('&nbsp;', ' '))
    return ' '.join(text.split())

def truncate_for_meta(text, max_len=155):
    """Truncate to a search-snippet-friendly length without cutting mid-word."""
    text = text.strip()
    if len(text) <= max_len:
        return text
    truncated = text[:max_len].rsplit(' ', 1)[0]
    return truncated.rstrip('.,;:') + '...'

# Markers that consistently start the reusable show/campaign boilerplate and
# promo CTAs in the RSS description (Patreon plugs, cast & crew, merch links,
# advertiser info) — everything after the earliest of these is template text
# repeated across every episode, not unique to this one. Cutting here keeps
# meta descriptions and schema data genuinely episode-specific instead of
# mostly-duplicate content (and avoids baking in time-sensitive CTAs, like a
# survey deadline, that will read as stale forever once published).
# NOTE: inferred from one real episode description — worth a spot-check
# across a few more episodes in case a different show/season uses different
# markers or ordering.
BOILERPLATE_MARKERS = ["\U0001F3B2", "\u2728"]  # 🎲 (show intro), ✨ (campaign blurb)

def extract_synopsis(description):
    """Return only the episode-unique portion of an RSS description, cut
    before any standing boilerplate/CTA section begins."""
    if not description:
        return ""
    positions = [description.find(m) for m in BOILERPLATE_MARKERS if description.find(m) != -1]
    dash_divider = re.search(r'-\s*-\s*-\s*-\s*-', description)
    if dash_divider:
        positions.append(dash_divider.start())
    cut = min(positions) if positions else len(description)
    return description[:cut].strip()

# =============================================
# SHARED STYLES
# =============================================

SHARED_CSS = """
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700&family=Inter:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap');

    * { margin: 0; padding: 0; box-sizing: border-box; }

    /* Light mode variables (default) — coordinated with the brand palette:
       parchment background, ink text, gold accent (same family as dark mode,
       just inverted for a light reading surface). */
    :root {
        --bg: #F3EDE1;
        --bg-secondary: #ffffff;
        --text: #170D26;
        --text-muted: #6B5C82;
        --border: #DCD3C4;
        --border-light: #EAE3D6;
        --accent: #8C6D2F;
        --accent-hover: #6E5525;
        --yellow: #C9A24B;
        --input-bg: #ffffff;
        --mark-bg: #E4C273;
        --mark-text: #170D26;
        --font-display: 'Fraunces', Georgia, serif;
        --font-body: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        --font-mono: 'IBM Plex Mono', 'Courier New', monospace;
    }

    /* Dark mode variables — exact brand tokens from dumbdragons.com */
    [data-theme="dark"] {
        --bg: #170D26;
        --bg-secondary: #1F1330;
        --text: #F3EDE1;
        --text-muted: #B7A9CC;
        --border: #4E3178;
        --border-light: #3D2461;
        --accent: #C9A24B;
        --accent-hover: #E4C273;
        --yellow: #C9A24B;
        --input-bg: #1F1330;
        --mark-bg: #E4C273;
        --mark-text: #170D26;
    }

    /* Auto dark mode if user hasn't manually chosen */
    @media (prefers-color-scheme: dark) {
        :root:not([data-theme="light"]) {
            --bg: #170D26;
            --bg-secondary: #1F1330;
            --text: #F3EDE1;
            --text-muted: #B7A9CC;
            --border: #4E3178;
            --border-light: #3D2461;
            --accent: #C9A24B;
            --accent-hover: #E4C273;
            --yellow: #C9A24B;
            --input-bg: #1F1330;
            --mark-bg: #E4C273;
            --mark-text: #170D26;
        }
    }

    body { font-family: var(--font-body); background: var(--bg); color: var(--text); line-height: 1.7; transition: background 0.2s, color 0.2s; }
    .main-nav { background: rgba(23, 13, 38, 0.92); backdrop-filter: blur(6px); border-bottom: 1px solid rgba(201, 162, 75, 0.18); padding: 0.6rem 2rem; display: flex; justify-content: center; gap: 2rem; align-items: center; flex-wrap: wrap; }
    .main-nav a { color: var(--text-muted); text-decoration: none; font-size: 0.85rem; font-weight: 500; font-family: var(--font-body); text-transform: uppercase; letter-spacing: 1px; }
    .main-nav a:hover { color: #E4C273; }
    .main-nav a.active { color: #E4C273; }
    .main-nav .wordmark { font-family: var(--font-display); font-weight: 700; font-size: 0.95rem; color: #F3EDE1; text-decoration: none; margin-right: 1rem; text-transform: none; letter-spacing: 0.01em; }
    .main-nav .dropdown { position: relative; }
    .main-nav .dropdown > a::after { content: ' ▾'; font-size: 0.7rem; }
    .main-nav .dropdown-menu { display: none; position: absolute; top: 100%; left: 0; background: #1F1330; border: 1px solid rgba(201, 162, 75, 0.25); border-radius: 4px; min-width: 220px; box-shadow: 0 4px 12px rgba(0,0,0,0.35); z-index: 500; padding: 0.4rem 0; margin-top: 0; padding-top: 0.8rem; }
    .main-nav .dropdown-menu::before { content: ''; display: block; position: absolute; top: -8px; left: 0; right: 0; height: 8px; }
    .main-nav .dropdown-menu a { display: block; padding: 0.5rem 1rem; color: #F3EDE1; font-size: 0.8rem; letter-spacing: 0.5px; white-space: nowrap; }
    .main-nav .dropdown-menu a:hover { background: rgba(201, 162, 75, 0.12); color: #E4C273; }
    .main-nav .dropdown:hover .dropdown-menu { display: block; }
    @media (max-width: 768px) {
        .main-nav { gap: 0.75rem; padding: 0.5rem 1rem; font-size: 0.75rem; }
        .main-nav .wordmark { display: none; }
        .main-nav a { font-size: 0.75rem; letter-spacing: 0.5px; }
        .main-nav .dropdown-menu { left: auto; right: 0; }
    }
    header { background: #1F1330; padding: 1.2rem 2rem; border-bottom: 3px solid #C9A24B; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 0.5rem; }
    header .brand { font-family: var(--font-display); color: #E4C273; text-decoration: none; font-size: 1.1rem; letter-spacing: 0.02em; font-weight: 700; text-transform: none; }
    header .brand:hover { text-decoration: underline; }
    header nav { display: flex; gap: 1rem; align-items: center; }
    header nav a { color: #F3EDE1; text-decoration: none; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }
    header nav a:hover { color: #E4C273; }
    header nav a.report { border: 2px solid #B7A9CC; padding: 0.3rem 0.7rem; border-radius: 3px; }
    header nav a.report:hover { border-color: #E4C273; color: #E4C273; }
    .dark-toggle { background: none; border: 2px solid #B7A9CC; border-radius: 3px; padding: 0.3rem 0.6rem; cursor: pointer; font-size: 1rem; line-height: 1; color: #F3EDE1; }
    .dark-toggle:hover { border-color: #E4C273; }
    .search-bar { background: var(--bg-secondary); padding: 1rem 2rem; border-bottom: 1px solid var(--border); }
    .search-bar form { max-width: 800px; margin: 0 auto; display: flex; gap: 0.5rem; }
    .search-bar input { flex: 1; padding: 0.5rem 1rem; background: var(--input-bg); border: 2px solid var(--border); border-radius: 3px; color: var(--text); font-size: 0.95rem; }
    .search-bar input:focus { outline: none; border-color: var(--yellow); }
    .search-bar button { padding: 0.5rem 1.2rem; background: var(--accent); border: none; border-radius: 3px; color: #fff; cursor: pointer; font-size: 0.95rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
    .search-bar button:hover { background: var(--accent-hover); }
    footer { text-align: center; padding: 2rem; color: var(--text-muted); font-size: 0.85rem; border-top: 3px solid var(--yellow); margin-top: 3rem; background: var(--bg-secondary); }
    footer a { color: var(--text-muted); }
    footer a:hover { color: var(--accent); }
    .footer-links { margin-bottom: 0.75rem; }
    .disclaimer { font-size: 0.78rem; color: var(--text-muted); max-width: 700px; margin: 0.75rem auto 0; line-height: 1.6; opacity: 0.8; }
"""

DARK_MODE_SCRIPT = """
    <script>
        (function() {
            const saved = localStorage.getItem('theme');
            if (saved) document.documentElement.setAttribute('data-theme', saved);
        })();
    </script>
"""

DARK_MODE_TOGGLE_SCRIPT = """
    <script>
        const toggleBtn = document.getElementById('dark-toggle');
        function updateToggle() {
            const theme = document.documentElement.getAttribute('data-theme');
            const sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            const isDark = theme === 'dark' || (!theme && sysDark);
            toggleBtn.textContent = isDark ? '☀️' : '🌙';
            toggleBtn.title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
        }
        toggleBtn.addEventListener('click', function() {
            const current = document.documentElement.getAttribute('data-theme');
            const sysDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
            const isDark = current === 'dark' || (!current && sysDark);
            const next = isDark ? 'light' : 'dark';
            document.documentElement.setAttribute('data-theme', next);
            localStorage.setItem('theme', next);
            updateToggle();
        });
        updateToggle();
    </script>
"""

# =============================================
# ANALYTICS CONFIGURATION
# =============================================

# GoatCounter site code — set this to your GoatCounter subdomain
# e.g. if your URL is dumbdragons.goatcounter.com, set to "dumbdragons"
GOATCOUNTER_CODE = "dumbdragons"

# Google Apps Script webhook URL for search query logging
SEARCH_LOG_WEBHOOK = "https://script.google.com/macros/s/AKfycbynAVdrsRLFAyZaopyoJIm1t9ta2Y27aS3bJFIh3NJ2kNS4hMbOBw2ZJlP37bcfCOrF/exec"

def goatcounter_script():
    return f"""
    <script data-goatcounter="https://{GOATCOUNTER_CODE}.goatcounter.com/count"
            async src="//gc.zgo.at/count.js"></script>"""

def search_log_script():
    if not SEARCH_LOG_WEBHOOK:
        return ""
    return f"""
    <script>
        (function() {{
            var webhook = '{SEARCH_LOG_WEBHOOK}';
            function logSearch(query) {{
                if (!query || !webhook) return;
                var page = document.referrer || window.location.pathname;
                fetch(webhook, {{
                    method: 'POST',
                    mode: 'no-cors',
                    headers: {{ 'Content-Type': 'application/json' }},
                    body: JSON.stringify({{
                        query: query,
                        page: page,
                        timestamp: new Date().toISOString()
                    }})
                }}).catch(function() {{}});
            }}
            window._logSearch = logSearch;
        }})();
    </script>"""

MAIN_NAV = """    <nav class="main-nav">
        <a href="https://www.dumbdragons.com" class="wordmark">Dumb Dragons Productions</a>
        <div class="dropdown">
            <a href="https://www.dumbdragons.com/shows/">Shows</a>
            <div class="dropdown-menu">
                <a href="https://www.dumbdragons.com/shows/dumbgeons-campaign-two/">Dumbgeons &amp; Dragons Campaign 2</a>
                <a href="https://www.dumbdragons.com/shows/dumbgeons-campaign-one/">Dumbgeons &amp; Dragons Campaign 1</a>
                <a href="https://www.dumbdragons.com/shows/chromeheads-and-corpos/">Chromeheads &amp; Corpos</a>
            </div>
        </div>
        <a href="https://dumbdragons.dashery.com/" target="_blank">Shirts &amp; Stuff</a>
        <a href="https://www.dumbdragons.com/support/">Join Us</a>
        <a href="https://scripts.dumbdragons.com" class="active">Transcripts</a>
    </nav>"""

# =============================================

# =============================================
# PAGE-SPECIFIC CSS CONSTANTS
# (kept outside f-strings to avoid rem/decimal parse issues)
# =============================================

EPISODE_PAGE_CSS = """
        .episode-header { max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
        .episode-header h1 { font-family: var(--font-display); font-size: 1.8rem; color: var(--accent); margin-bottom: 0.3rem; font-weight: 700; text-transform: none; }
        .episode-header .meta { color: var(--text-muted); font-size: 0.9rem; margin-bottom: 1.5rem; font-family: var(--font-mono); }
        .player { position: sticky; top: 0; z-index: 100; background: var(--bg); padding: 0.75rem 0; border-bottom: 2px solid var(--yellow); margin-bottom: 0; width: 100%; }
        .player audio { border-radius: 4px; }
        .nav-links { display: flex; justify-content: space-between; margin-bottom: 2rem; }
        .nav-btn { color: var(--accent); text-decoration: none; font-size: 0.9rem; padding: 0.4rem 0.8rem; border: 2px solid var(--accent); border-radius: 3px; font-weight: 700; }
        .nav-btn:hover { background: var(--accent); color: #fff; }
        .nav-btn.disabled { color: var(--border); border-color: var(--border); cursor: default; }
        .transcript { max-width: 800px; margin: 0 auto 2rem; padding: 0 1rem; }
        .transcript h2 { font-family: var(--font-display); color: var(--accent); margin-bottom: 0.5rem; font-size: 1.15rem; letter-spacing: 0.02em; text-transform: none; font-weight: 700; border-bottom: 3px solid var(--yellow); padding-bottom: 0.5rem; }
        .transcript-hint { color: var(--text-muted); font-size: 0.8rem; margin-bottom: 1.5rem; font-style: italic; }
        .transcript p { margin-bottom: 0.75rem; color: var(--text); padding: 0.3rem 0.5rem; border-left: 3px solid transparent; border-radius: 2px; transition: border-color 0.15s, background 0.15s; }
        .transcript p.ts-line { cursor: pointer; }
        .transcript p.ts-line:hover { border-left-color: var(--border); background: var(--bg-secondary); }
        .transcript p.active { border-left-color: var(--accent); background: var(--bg-secondary); }
        .ad-break { margin: 1.5rem 0; padding: 0.6rem 1rem; background: var(--bg-secondary); border: 1px solid var(--border); border-left: 4px solid var(--yellow); border-radius: 3px; color: var(--text-muted); font-size: 0.85rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
        .report-footer { max-width: 800px; margin: 0 auto 2rem; padding: 0 1rem; }
        .report-footer a { color: var(--text-muted); font-size: 0.85rem; text-decoration: none; }
        .report-footer a:hover { color: var(--accent); }
        .downloads { display: flex; gap: 0.75rem; margin-bottom: 2rem; }
        .download-btn { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.4rem 0.9rem; border: 2px solid var(--accent); border-radius: 3px; color: var(--accent); text-decoration: none; font-size: 0.85rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
        .download-btn:hover { background: var(--accent); color: #fff; }
        .follow-btn { display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.4rem 0.9rem; background: var(--yellow); border: 2px solid var(--yellow); border-radius: 3px; color: #170D26; text-decoration: none; font-size: 0.85rem; font-weight: 700; text-transform: uppercase; letter-spacing: 0.5px; }
        .follow-btn:hover { background: #E4C273; border-color: #E4C273; }
        .follow-btn-nav { color: #170D26 !important; text-decoration: none; font-size: 0.85rem; font-weight: 700; background: var(--yellow); padding: 0.3rem 0.7rem; border-radius: 3px; }
        .follow-btn-nav:hover { background: #E4C273; }
        .back-to-top { position: fixed; bottom: 2rem; right: 1.5rem; background: var(--accent); color: #fff; border: none; border-radius: 50%; width: 44px; height: 44px; font-size: 1.2rem; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.3); display: none; align-items: center; justify-content: center; z-index: 150; transition: background 0.2s; }
        .back-to-top.visible { display: flex; }
        .back-to-top:hover { background: var(--accent-hover); }
        @media (max-width: 768px) {
            header { padding: 0.8rem 1rem; }
            header .brand { font-size: 0.9rem; }
            header nav { gap: 0.5rem; }
            header nav a { font-size: 0.75rem; padding: 0.2rem 0.4rem; letter-spacing: 0; }
            header nav a.report { border-width: 1px; font-size: 0.7rem; }
            .follow-btn-nav { font-size: 0.75rem; padding: 0.2rem 0.4rem; }
            .episode-header { margin: 1rem auto; }
            .episode-header h1 { font-size: 1.2rem; }
            .downloads { flex-wrap: wrap; gap: 0.5rem; }
            .nav-links { gap: 0.5rem; }
            .nav-btn { font-size: 0.8rem; padding: 0.3rem 0.6rem; }
            .share-toolbar { bottom: 1rem; padding: 0.5rem 0.75rem; gap: 0.4rem; }
            .share-btn { font-size: 0.75rem; padding: 0.3rem 0.5rem; }
            .back-to-top { bottom: 4rem; right: 1rem; width: 38px; height: 38px; font-size: 1rem; }
        }
    </style>
"""

SEARCH_PAGE_CSS = """
        main { max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
        .filters { display: flex; gap: 1.5rem; margin-bottom: 1.5rem; color: var(--text-muted); font-size: 0.9rem; }
        .filters label { display: flex; align-items: center; gap: 0.4rem; cursor: pointer; }
        .filters input { accent-color: var(--accent); }
        #results h3 { font-family: var(--font-display); color: var(--accent); margin-bottom: 1rem; font-weight: 700; text-transform: none; }
        #results ul { list-style: none; }
        #results li { padding: 0.8rem 0; border-bottom: 1px solid var(--border-light); }
        #results a { color: var(--text); text-decoration: none; font-size: 1rem; }
        #results a:hover { color: var(--accent); }
        #results .meta { color: var(--text-muted); font-size: 0.8rem; margin-bottom: 0.3rem; }
        #results .snippet { color: var(--text-muted); font-size: 0.85rem; margin-top: 0.3rem; line-height: 1.5; }
        mark { background: var(--mark-bg); color: var(--mark-text); padding: 0 2px; border-radius: 2px; }
        .no-results { color: var(--text-muted); }
        @media (max-width: 768px) {
            main { padding: 0 0.75rem; }
            .filters { flex-wrap: wrap; gap: 0.75rem; }
        }
    </style>
"""


# =============================================
# HTML TEMPLATES
# =============================================

def episode_page_html(season_num, ep_num, title, date, embed_html, transcript_text, prev_link, next_link, base_filename, ad_placements=None, description="", audio_url="", date_iso=""):
    nav_prev = f'<a href="{prev_link}" class="nav-btn">&#x2190; Previous</a>' if prev_link else '<span class="nav-btn disabled">&#x2190; Previous</span>'
    nav_next = f'<a href="{next_link}" class="nav-btn">Next &#x2192;</a>' if next_link else '<span class="nav-btn disabled">Next &#x2192;</span>'
    issue_url = build_issue_url(title)

    canonical_url = f"https://scripts.dumbdragons.com/docs/season-{season_num}/{urllib.parse.quote(base_filename)}.html"
    page_title = f"S{season_num:02d}{title}"
    synopsis = extract_synopsis(description)
    meta_description = truncate_for_meta(synopsis) if synopsis else (
        f"Full transcript for {title}, Season {season_num} of Dumbgeons & Dragons — searchable, with audio, downloadable TXT/SRT."
    )
    schema_description = synopsis if synopsis else meta_description

    schema = {
        "@context": "https://schema.org",
        "@type": "PodcastEpisode",
        "name": title,
        "url": canonical_url,
        "datePublished": date_iso if date_iso else date,
        "description": schema_description,
        "partOfSeries": {
            "@type": "PodcastSeries",
            "name": "Dumbgeons & Dragons",
            "url": "https://www.dumbdragons.com",
        },
    }
    if audio_url:
        schema["associatedMedia"] = {
            "@type": "MediaObject",
            "contentUrl": audio_url,
        }
    schema_json = json.dumps(schema, ensure_ascii=False)

    def _attr_escape(s):
        return s.replace('&', '&amp;').replace('"', '&quot;')

    seo_head = (
        '<meta name="description" content="' + _attr_escape(meta_description) + '">\n'
        '    <link rel="canonical" href="' + canonical_url + '">\n'
        '    <meta property="og:type" content="article">\n'
        '    <meta property="og:title" content="' + _attr_escape(page_title) + '">\n'
        '    <meta property="og:description" content="' + _attr_escape(meta_description) + '">\n'
        '    <meta property="og:url" content="' + canonical_url + '">\n'
        '    <script type="application/ld+json">\n' + schema_json + '\n    </script>'
    )

    sorted_ads = sorted(ad_placements or [], key=lambda x: x["timestamp"])
    ad_inserted = set()

    transcript_lines = []
    for line in transcript_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        ts_match = re.match(r'^\[(\d+\.?\d*)s\]\s*(.*)', line)
        if ts_match:
            ts = float(ts_match.group(1))
            text = ts_match.group(2).strip()
            transcript_lines.append({"ts": ts, "text": text})
        else:
            transcript_lines.append({"ts": None, "text": line})

    transcript_html = ""
    for line in transcript_lines:
        for ad in sorted_ads:
            ad_key = f"{ad['type']}_{ad['timestamp']}"
            if ad_key in ad_inserted:
                continue
            if line["ts"] is not None and line["ts"] >= ad["timestamp"]:
                label = "Pre-roll Ad Break" if ad["type"] == "preroll" else "Mid-roll Ad Break"
                transcript_html += f'<div class="ad-break">&#x1F4E2; {label}</div>\n'
                ad_inserted.add(ad_key)
        text = line["text"].replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if text:
            transcript_html += f'<p>{text}</p>\n'

    return ("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>""" + page_title + """ &mdash; Dumbgeons &amp; Dragons Transcripts</title>
    """ + seo_head + """
    """ + DARK_MODE_SCRIPT + """
    <style>
""" + SHARED_CSS + EPISODE_PAGE_CSS + """</head>
<body>
    """ + MAIN_NAV + """
    <header>
        <a href="../../index.html" class="brand">DUMBGEONS &amp; DRAGONS</a>
        <nav>
            <a href="../../search.html">Search</a>
            <a href="../../index.html">Episodes</a>
            <a href="https://pod.link/1191411985" class="follow-btn-nav" target="_blank">&#x1F3A7; Follow</a>
            <a href=\"""" + issue_url + """\" class="report" target="_blank">Report an error</a>
            <button class="dark-toggle" id="dark-toggle" title="Toggle dark mode">&#x1F319;</button>
        </nav>
    </header>
    <div class="search-bar">
        <form action="../../search.html" method="get">
            <input type="text" name="q" placeholder="Search transcripts...">
            <button type="submit">Search</button>
        </form>
    </div>
    <div class="episode-header">
        <p class="meta">Season """ + str(season_num) + """ &bull; """ + date + """</p>
        <h1>""" + title + """</h1>
        <div class="player">
            <audio id="episode-audio" controls style="width:100%; border-radius:4px;">
                """ + embed_html + """
                Your browser does not support the audio element.
            </audio>
        </div>
        <div class="downloads">
            <a href=\"""" + base_filename + """.txt" download class="download-btn">&#x2193; TXT</a>
            <a href=\"""" + base_filename + """.srt" download class="download-btn">&#x2193; SRT</a>
            <a href="https://pod.link/1191411985" class="follow-btn" target="_blank">&#x1F3A7; Follow the Show</a>
        </div>
        <div class="nav-links">
            """ + nav_prev + """
            """ + nav_next + """
        </div>
    </div>
    <div class="transcript">
        <h2>Transcript</h2>
        <p class="transcript-hint">Ad breaks are marked in the transcript below. The audio player uses the ad-supported feed.</p>
        """ + transcript_html + """
    </div>

    <button class="back-to-top" id="back-to-top" title="Back to top">&#x2191;</button>

    <div class="report-footer">
        <a href=\"""" + issue_url + """\" target="_blank">Found a transcription error? Report it on GitHub &rarr;</a>
    </div>
    <footer>
        <div class="footer-links">
            Dumbgeons &amp; Dragons &copy; Dumb Dragons Productions. All rights reserved.
            &bull; <a href=\"""" + GITHUB_ISSUES_URL + """\" target="_blank">Report a transcript error</a>
        </div>
        <p class="disclaimer">Transcripts are generated using AI speech-to-text technology (OpenAI Whisper) and may contain errors, particularly around proper nouns, character names, and crosstalk. They are provided for accessibility and search purposes and should not be considered verbatim records.</p>
    </footer>
    """ + DARK_MODE_TOGGLE_SCRIPT + """
    <script>
        window.addEventListener('scroll', function() {
            document.getElementById('back-to-top').classList.toggle('visible', window.scrollY > 400);
        }, { passive: true });
        document.getElementById('back-to-top').addEventListener('click', function() {
            window.scrollTo({ top: 0, behavior: 'smooth' });
        });
    </script>
    """ + goatcounter_script() + """
</body>
</html>""")


def index_html(season_episodes):
    import json as json_mod
    all_episodes = []
    for season_name, episodes in sorted(season_episodes.items()):
        season_num = int(re.search(r'[0-9]+', season_name).group())
        season_folder = season_name.lower().replace(" ", "-")
        for ep in episodes:
            all_episodes.append({
                "season": season_num,
                "season_name": season_name,
                "season_folder": season_folder,
                "ep_num": ep["ep_num"],
                "title": ep["title"],
                "date": ep["date"],
                "filename": ep["filename"],
            })
    episodes_json = json_mod.dumps(all_episodes)

    # Server-rendered episode listing (ascending order, matching the default
    # client-side sort). JS still re-renders this on load/sort-toggle exactly
    # as before — this just means the links exist in the raw HTML too, so
    # discovery doesn't depend on JS execution.
    static_listing = ""
    for season_name, episodes in sorted(season_episodes.items()):
        season_num = int(re.search(r'[0-9]+', season_name).group())
        season_folder = season_name.lower().replace(" ", "-")
        static_listing += f'<div class="season" id="{season_folder}"><h2>Season {season_num}</h2><ul class="episode-list">'
        for ep in episodes:
            static_listing += (
                f'<li><a href="docs/{season_folder}/{ep["filename"]}.html">{ep["title"]}</a>'
                f'<span>{ep["date"]}</span></li>'
            )
        static_listing += '</ul></div>'

    nav_links = ""
    for season_name in sorted(season_episodes.keys()):
        season_id = season_name.lower().replace(" ", "-")
        season_num = re.search(r'[0-9]+', season_name).group()
        nav_links += f'<a href="#{season_id}">Season {season_num}</a>\n'

    INDEX_PAGE_CSS = """
        header { text-align: center; flex-direction: column; padding: 2rem; }
        header .brand { font-size: 2.2rem; }
        header p { color: var(--text-muted); margin-top: 0.3rem; font-size: 0.95rem; font-family: var(--font-body); }
        .season-nav { background: var(--bg-secondary); padding: 0.8rem 2rem; text-align: center; border-bottom: 1px solid var(--border); }
        .season-nav a { color: var(--accent); text-decoration: none; margin: 0 1rem; font-size: 0.9rem; font-weight: 700; text-transform: uppercase; }
        .season-nav a:hover { text-decoration: underline; }
        main { max-width: 800px; margin: 2rem auto; padding: 0 1rem; }
        .season { margin-bottom: 3rem; }
        .season h2 { font-family: var(--font-display); font-weight: 700; color: var(--accent); border-bottom: 3px solid var(--yellow); padding-bottom: 0.5rem; margin-bottom: 1rem; font-size: 1.4rem; }
        .episode-list { list-style: none; }
        .episode-list li { padding: 0.6rem 0; border-bottom: 1px solid var(--border-light); display: flex; justify-content: space-between; align-items: baseline; }
        .episode-list a { color: var(--text); text-decoration: none; flex: 1; }
        .episode-list a:hover { color: var(--accent); }
        .episode-list span { color: var(--text-muted); font-size: 0.85rem; margin-left: 1rem; white-space: nowrap; }
        #search-results { margin-bottom: 2rem; }
        #search-results h3 { font-family: var(--font-display); color: var(--accent); margin-bottom: 1rem; font-weight: 700; text-transform: none; }
        #search-results ul { list-style: none; }
        #search-results li { padding: 0.6rem 0; border-bottom: 1px solid var(--border-light); }
        #search-results a { color: var(--text); text-decoration: none; }
        #search-results a:hover { color: var(--accent); }
        #search-results .snippet { color: var(--text-muted); font-size: 0.85rem; margin-top: 0.2rem; }
        mark { background: var(--mark-bg); color: var(--mark-text); padding: 0 2px; border-radius: 2px; }
        .sort-bar { max-width: 800px; margin: 1rem auto 0; padding: 0 1rem; display: flex; align-items: center; gap: 0.75rem; }
        .sort-bar span { color: var(--text-muted); font-size: 0.85rem; }
        .sort-btn { padding: 0.3rem 0.75rem; border: 2px solid var(--border); border-radius: 3px; background: none; color: var(--text-muted); font-size: 0.8rem; font-weight: 700; cursor: pointer; text-transform: uppercase; letter-spacing: 0.5px; }
        .sort-btn.active { border-color: var(--accent); color: var(--accent); background: none; }
        .sort-btn:hover { border-color: var(--accent); color: var(--accent); }
        @media (max-width: 768px) {
            header { padding: 1rem; }
            header .brand { font-size: 1.5rem; }
            .season-nav { padding: 0.6rem 1rem; gap: 0.5rem; }
            .season-nav a { margin: 0 0.5rem; font-size: 0.8rem; }
            main { padding: 0 0.75rem; }
            .episode-list li { flex-direction: column; align-items: flex-start; gap: 0.2rem; }
            .episode-list span { margin-left: 0; font-size: 0.8rem; }
        }
    """

    return ("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Dumbgeons &amp; Dragons Transcript Archive</title>
    <meta name="description" content="Searchable, downloadable transcripts for every episode of Dumbgeons &amp; Dragons — with audio, TXT and SRT downloads for each episode.">
    <link rel="canonical" href="https://scripts.dumbdragons.com/">
    """ + DARK_MODE_SCRIPT + """
    <style>
""" + SHARED_CSS + INDEX_PAGE_CSS + """    </style>
</head>
<body>
    """ + MAIN_NAV + """
    <header>
        <a href="index.html" class="brand">DUMBGEONS &amp; DRAGONS</a>
        <p>Transcript Archive &mdash; searchable scripts for every episode</p>
        <button class="dark-toggle" id="dark-toggle" title="Toggle dark mode">&#x1F319;</button>
    </header>
    <div class="search-bar">
        <form onsubmit="doSearch(event)">
            <input type="text" id="search-input" placeholder="Search transcripts...">
            <button type="submit">Search</button>
        </form>
    </div>
    <div class="season-nav">
        """ + nav_links + """
    </div>
    <div class="sort-bar">
        <span>Sort:</span>
        <button class="sort-btn active" id="sort-asc" onclick="setSort('asc')">Oldest First</button>
        <button class="sort-btn" id="sort-desc" onclick="setSort('desc')">Newest First</button>
    </div>
    <main>
        <div id="search-results" style="display:none;"></div>
        <div id="episode-listing">""" + static_listing + """</div>
    </main>
    <footer>
        <div class="footer-links">
            Dumbgeons &amp; Dragons &copy; Dumb Dragons Productions. All rights reserved.
            &bull; <a href=\"""" + GITHUB_ISSUES_URL + """\" target="_blank">Report a transcript error</a>
        </div>
        <p class="disclaimer">Transcripts are generated using AI speech-to-text technology (OpenAI Whisper) and may contain errors, particularly around proper nouns, character names, and crosstalk. They are provided for accessibility and search purposes and should not be considered verbatim records.</p>
    </footer>
    <script src="https://unpkg.com/lunr/lunr.js"></script>
    <script>
        var ALL_EPISODES = """ + episodes_json + """;
        var currentSort = localStorage.getItem('episode-sort') || 'asc';

        function setSort(order) {
            currentSort = order;
            localStorage.setItem('episode-sort', order);
            renderEpisodes();
        }

        function renderEpisodes() {
            var episodes = ALL_EPISODES.slice();
            if (currentSort === 'desc') episodes.reverse();
            var seasons = {};
            episodes.forEach(function(ep) {
                if (!seasons[ep.season_name]) seasons[ep.season_name] = { num: ep.season, folder: ep.season_folder, eps: [] };
                seasons[ep.season_name].eps.push(ep);
            });
            var sortedSeasons = Object.entries(seasons).sort(function(a, b) {
                return currentSort === 'asc' ? a[1].num - b[1].num : b[1].num - a[1].num;
            });
            var html = '';
            sortedSeasons.forEach(function(entry) {
                var name = entry[0], data = entry[1];
                var seasonId = name.toLowerCase().replace(/ /g, '-');
                html += '<div class="season" id="' + seasonId + '"><h2>Season ' + data.num + '</h2><ul class="episode-list">';
                data.eps.forEach(function(ep) {
                    html += '<li><a href="docs/' + ep.season_folder + '/' + ep.filename + '.html">' + ep.title + '</a><span>' + ep.date + '</span></li>';
                });
                html += '</ul></div>';
            });
            document.getElementById('episode-listing').innerHTML = html;
            document.getElementById('sort-asc').classList.toggle('active', currentSort === 'asc');
            document.getElementById('sort-desc').classList.toggle('active', currentSort === 'desc');
        }

        renderEpisodes();

        var searchIndex = null, episodeData = null, indexLoaded = false, indexLoading = false;

        function loadIndex(callback) {
            if (indexLoaded) { callback(); return; }
            if (indexLoading) { setTimeout(function() { loadIndex(callback); }, 100); return; }
            indexLoading = true;
            fetch('search-index.json').then(function(r) { return r.json(); }).then(function(data) {
                episodeData = data;
                searchIndex = lunr(function() {
                    this.ref('id');
                    this.field('title', { boost: 10 });
                    this.field('transcript');
                    data.forEach(function(ep) { this.add(ep); }, this);
                });
                indexLoaded = true; indexLoading = false;
                callback();
            });
        }

        document.getElementById('search-input').addEventListener('focus', function() { loadIndex(function() {}); });

        function doSearch(e) {
            e.preventDefault();
            var q = document.getElementById('search-input').value.trim();
            if (!q) return;
            if (window._logSearch) window._logSearch(q);
            loadIndex(function() { runSearch(q); });
        }

        function runSearch(q) {
            if (!searchIndex) return;
            var results = searchIndex.search(q);
            var container = document.getElementById('search-results');
            if (results.length === 0) {
                container.style.display = 'block';
                container.innerHTML = '<h3>No results found for "' + q + '"</h3>';
                return;
            }
            var html = '<h3>' + results.length + ' result' + (results.length > 1 ? 's' : '') + ' for "' + q + '"</h3><ul>';
            results.slice(0, 50).forEach(function(r) {
                var ep = episodeData.find(function(e) { return e.id === r.ref; });
                if (!ep) return;
                var snippet = ep.transcript.replace(new RegExp(q, 'gi'), '<mark>$&</mark>').substring(0, 200) + '...';
                html += '<li><a href="' + ep.url + '">' + ep.title + '</a><div class="snippet">' + snippet + '</div></li>';
            });
            html += '</ul>';
            container.innerHTML = html;
            container.style.display = 'block';
        }

        var urlParams = new URLSearchParams(window.location.search);
        var urlQ = urlParams.get('q');
        if (urlQ) {
            document.getElementById('search-input').value = urlQ;
            loadIndex(function() { runSearch(urlQ); });
        }
    </script>
    """ + DARK_MODE_TOGGLE_SCRIPT + """
    """ + search_log_script() + """
    """ + goatcounter_script() + """
</body>
</html>""")


def build_sitemap(season_episodes):
    """Build sitemap.xml listing the homepage, search page, and every episode page."""
    base = "https://scripts.dumbdragons.com"
    urls = [base + "/", base + "/search.html"]
    for season_name, episodes in sorted(season_episodes.items()):
        season_folder = season_name.lower().replace(" ", "-")
        for ep in episodes:
            urls.append(f"{base}/docs/{season_folder}/{urllib.parse.quote(ep['filename'])}.html")
    entries = "\n".join(f"  <url><loc>{u}</loc></url>" for u in urls)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{entries}\n"
        "</urlset>\n"
    )


def build_robots_txt():
    return (
        "User-agent: *\n"
        "Allow: /\n\n"
        "Sitemap: https://scripts.dumbdragons.com/sitemap.xml\n"
    )


def search_page_html(season_episodes):
    season_options = ""
    for season_name in sorted(season_episodes.keys()):
        season_num = re.search(r'[0-9]+', season_name).group()
        season_options += f'<label><input type="checkbox" class="season-filter" value="{season_num}" checked> Season {season_num}</label>\n'

    return ("""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Search &mdash; Dumbgeons &amp; Dragons Transcripts</title>
    <meta name="description" content="Search the full transcript archive for Dumbgeons &amp; Dragons, filterable by season.">
    <link rel="canonical" href="https://scripts.dumbdragons.com/search.html">
    """ + DARK_MODE_SCRIPT + """
    <style>
""" + SHARED_CSS + SEARCH_PAGE_CSS + """    </style>
</head>
<body>
    """ + MAIN_NAV + """
    <header>
        <a href="index.html" class="brand">DUMBGEONS &amp; DRAGONS</a>
        <nav>
            <a href="index.html">Episodes</a>
            <button class="dark-toggle" id="dark-toggle" title="Toggle dark mode">&#x1F319;</button>
        </nav>
    </header>
    <div class="search-bar">
        <form onsubmit="doSearch(event)">
            <input type="text" id="search-input" placeholder="Search transcripts...">
            <button type="submit">Search</button>
        </form>
    </div>
    <main>
        <div class="filters">
            <strong style="color:var(--text);">Filter:</strong>
            """ + season_options + """
        </div>
        <div id="results"></div>
    </main>
    <footer>
        <div class="footer-links">
            Dumbgeons &amp; Dragons &copy; Dumb Dragons Productions. All rights reserved.
            &bull; <a href=\"""" + GITHUB_ISSUES_URL + """\" target="_blank">Report a transcript error</a>
        </div>
        <p class="disclaimer">Transcripts are generated using AI speech-to-text technology (OpenAI Whisper) and may contain errors, particularly around proper nouns, character names, and crosstalk. They are provided for accessibility and search purposes and should not be considered verbatim records.</p>
    </footer>
    <script src="https://unpkg.com/lunr/lunr.js"></script>
    <script>
        var searchIndex = null, episodeData = null, indexLoaded = false, indexLoading = false;

        function loadIndex(callback) {
            if (indexLoaded) { callback(); return; }
            if (indexLoading) { setTimeout(function() { loadIndex(callback); }, 100); return; }
            indexLoading = true;
            document.getElementById('results').innerHTML = '<p style="color:var(--text-muted)">Loading search index...</p>';
            fetch('search-index.json').then(function(r) { return r.json(); }).then(function(data) {
                episodeData = data;
                searchIndex = lunr(function() {
                    this.ref('id');
                    this.field('title', { boost: 10 });
                    this.field('transcript');
                    data.forEach(function(ep) { this.add(ep); }, this);
                });
                indexLoaded = true; indexLoading = false;
                document.getElementById('results').innerHTML = '';
                callback();
            });
        }

        document.getElementById('search-input').addEventListener('focus', function() { loadIndex(function() {}); });

        function doSearch(e) {
            e.preventDefault();
            var q = document.getElementById('search-input').value.trim();
            if (q) {
                if (window._logSearch) window._logSearch(q);
                loadIndex(function() { runSearch(q); });
            }
        }

        function getSelectedSeasons() {
            return Array.from(document.querySelectorAll('.season-filter:checked')).map(function(el) { return el.value; });
        }

        function runSearch(q) {
            if (!searchIndex) return;
            var selectedSeasons = getSelectedSeasons();
            var results = searchIndex.search(q);
            var container = document.getElementById('results');
            var filtered = results.filter(function(r) {
                var ep = episodeData.find(function(e) { return e.id === r.ref; });
                return ep && selectedSeasons.includes(String(ep.season));
            });
            if (filtered.length === 0) {
                container.innerHTML = '<p class="no-results">No results found for "<strong>' + q + '</strong>".</p>';
                return;
            }
            var html = '<h3>' + filtered.length + ' result' + (filtered.length > 1 ? 's' : '') + ' for "' + q + '"</h3><ul>';
            filtered.slice(0, 100).forEach(function(r) {
                var ep = episodeData.find(function(e) { return e.id === r.ref; });
                if (!ep) return;
                var snippetText = ep.transcript;
                var idx = snippetText.toLowerCase().indexOf(q.toLowerCase());
                var start = Math.max(0, idx - 80);
                var end = Math.min(snippetText.length, idx + q.length + 120);
                var snippet = (start > 0 ? '...' : '') + snippetText.slice(start, end).replace(new RegExp(q, 'gi'), '<mark>$&</mark>') + (end < snippetText.length ? '...' : '');
                html += '<li><div class="meta">Season ' + ep.season + ' &bull; ' + ep.date + '</div><a href="' + ep.url + '">' + ep.title + '</a><div class="snippet">' + snippet + '</div></li>';
            });
            html += '</ul>';
            container.innerHTML = html;
        }

        document.querySelectorAll('.season-filter').forEach(function(cb) {
            cb.addEventListener('change', function() {
                var q = document.getElementById('search-input').value.trim();
                if (q) runSearch(q);
            });
        });

        var urlParams = new URLSearchParams(window.location.search);
        var urlQ = urlParams.get('q');
        if (urlQ) {
            document.getElementById('search-input').value = urlQ;
            if (window._logSearch) window._logSearch(urlQ);
            loadIndex(function() { runSearch(urlQ); });
        }
    </script>
    """ + DARK_MODE_TOGGLE_SCRIPT + """
    """ + search_log_script() + """
    """ + goatcounter_script() + """
</body>
</html>""")



def main():
    root = fetch_feed(RSS_URL)
    items = root.find("channel").findall("item")
    print(f"Feed has {len(items)} episodes.\n")

    # Load ad placements from CSV
    print("Loading ad placements...")
    ad_placements_by_guid = load_ad_placements(AD_CSV_PATH)

    # Fetch ad-supported feed for public audio URLs
    ad_root = fetch_ad_feed(AD_RSS_URL)
    ad_items = ad_root.find("channel").findall("item")
    ad_audio_by_guid = {}
    for item in ad_items:
        guid_el = item.find("guid")
        guid = guid_el.text if guid_el is not None else ""
        guid_match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', guid, re.IGNORECASE)
        if guid_match:
            guid = guid_match.group()
        enclosure = item.find("enclosure")
        if enclosure is not None:
            ad_audio_by_guid[guid] = enclosure.get("url", "")
    print(f"  Mapped audio URLs for {len(ad_audio_by_guid)} episodes\n")

    feed_lookup = {}
    for item in items:
        title_el = item.find("title")
        title = title_el.text if title_el is not None else ""

        desc_el = item.find("description")
        description = strip_html_tags(desc_el.text if desc_el is not None else "")

        pub_date_el = item.find("pubDate")
        pub_date = pub_date_el.text if pub_date_el is not None else ""
        pub_date_iso = ""
        try:
            from email.utils import parsedate
            from datetime import datetime
            parsed = parsedate(pub_date)
            if parsed:
                parsed_dt = datetime(*parsed[:6])
                pub_date = parsed_dt.strftime("%B %d, %Y")
                pub_date_iso = parsed_dt.strftime("%Y-%m-%d")
        except:
            pass

        guid_el = item.find("guid")
        guid = guid_el.text if guid_el is not None else ""
        guid_match = re.search(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', guid, re.IGNORECASE)
        if guid_match:
            guid = guid_match.group()

        season_num, ep_num = get_episode_number_from_title(title)
        if season_num and ep_num:
            # Use ad-supported audio URL if available, fall back to ad-free
            ad_audio = ad_audio_by_guid.get(guid, "")
            feed_lookup[(season_num, ep_num)] = {
                "title": title,
                "date": pub_date,
                "date_iso": pub_date_iso,
                "guid": guid,
                "audio_url": ad_audio,
                "description": description,
            }
        else:
            # Store by raw title for manual override matching
            feed_lookup[("by_title", title.strip())] = {
                "title": title,
                "date": pub_date,
                "date_iso": pub_date_iso,
                "guid": guid,
                "audio_url": ad_audio_by_guid.get(guid, ""),
                "description": description,
            }

    print(f"Mapped {len(feed_lookup)} episodes from feed.\n")

    season_episodes = {}
    search_index_data = []

    for season_name, transcript_folder in TRANSCRIPT_FOLDERS.items():
        if not os.path.exists(transcript_folder):
            print(f"WARNING: {transcript_folder} not found, skipping.")
            continue

        season_num = int(re.search(r'[0-9]+', season_name).group())
        season_folder_name = season_name.lower().replace(" ", "-")
        season_out_dir = os.path.join(DOCS_OUTPUT, season_folder_name)
        os.makedirs(season_out_dir, exist_ok=True)

        txt_files = sorted([f for f in os.listdir(transcript_folder) if f.endswith(".txt")])
        print(f"Processing {season_name}: {len(txt_files)} transcript files")

        season_eps = []
        for txt_file in txt_files:
            base = os.path.splitext(txt_file)[0]
            s_num, ep_num = get_episode_number_from_filename(base)

            if not ep_num:
                print(f"  Skipping (can't parse): {txt_file}")
                continue

            feed_data = feed_lookup.get((season_num, ep_num))

            # Try manual override if no direct match
            if not feed_data and (season_num, ep_num) in MANUAL_OVERRIDES:
                override_title = MANUAL_OVERRIDES[(season_num, ep_num)]
                feed_data = feed_lookup.get(("by_title", override_title))
                if feed_data:
                    print(f"  Manual override matched S{season_num:02d}E{ep_num:02d}: {override_title}")

            if feed_data:
                title = feed_data["title"]
                title = re.sub(r'\s*\(Campaign\s*\d+\s*[–-]\s*S\d+E\d+\)\s*$', '', title).strip()
                title = re.sub(r'^s\d+e[\d/]+\s*-\s*', '', title, flags=re.IGNORECASE).strip()
                date = feed_data["date"]
                date_iso = feed_data.get("date_iso", "")
                guid = feed_data["guid"]
                audio_url = feed_data["audio_url"]
                embed_html = build_embed_html(audio_url) if audio_url else ""
                ep_ad_placements = ad_placements_by_guid.get(guid, [])
                description = feed_data.get("description", "")
            else:
                print(f"  No RSS match for S{season_num:02d}E{ep_num:02d} — using placeholder")
                title = f"Season {season_num} Episode {ep_num}"
                date = ""
                date_iso = ""
                embed_html = ""
                audio_url = ""
                ep_ad_placements = []
                description = ""

            # Prepend episode number in E## format
            display_title = f"E{ep_num:02d} - {title}"

            txt_path = os.path.join(transcript_folder, txt_file)
            with open(txt_path, "r", encoding="utf-8") as f:
                transcript_text = f.read()

            # Clean transcript for search index
            clean_transcript = re.sub(r'^\[\d+\.?\d*s\]\s*', '', transcript_text, flags=re.MULTILINE)
            clean_transcript = ' '.join(clean_transcript.split())

            ep_url = f"docs/{season_folder_name}/{base}.html"

            season_eps.append({
                "ep_num": ep_num,
                "filename": base,
                "title": display_title,
                "date": date,
                "date_iso": date_iso,
                "transcript": transcript_text,
                "embed_html": embed_html,
                "ad_placements": ep_ad_placements,
                "description": description,
                "audio_url": audio_url,
            })

            search_index_data.append({
                "id": f"s{season_num}e{ep_num}",
                "season": season_num,
                "ep_num": ep_num,
                "title": display_title,
                "date": date,
                "url": ep_url,
                "transcript": clean_transcript[:8000],  # trimmed for index size
            })

        season_eps.sort(key=lambda x: x["ep_num"])

        for i, ep in enumerate(season_eps):
            prev_ep = season_eps[i - 1] if i > 0 else None
            next_ep = season_eps[i + 1] if i < len(season_eps) - 1 else None

            html = episode_page_html(
                season_num=season_num,
                ep_num=ep["ep_num"],
                title=ep["title"],
                date=ep["date"],
                embed_html=ep["embed_html"],
                transcript_text=ep["transcript"],
                prev_link=f"{prev_ep['filename']}.html" if prev_ep else None,
                next_link=f"{next_ep['filename']}.html" if next_ep else None,
                base_filename=ep["filename"],
                ad_placements=ep["ad_placements"],
                description=ep["description"],
                audio_url=ep["audio_url"],
                date_iso=ep["date_iso"],
            )

            out_path = os.path.join(season_out_dir, f"{ep['filename']}.html")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(html)

            # Copy txt and srt files into site output folder for download
            import shutil
            for ext in [".txt", ".srt"]:
                src = os.path.join(transcript_folder, ep["filename"] + ext)
                dst = os.path.join(season_out_dir, ep["filename"] + ext)
                if os.path.exists(src):
                    shutil.copy2(src, dst)

        print(f"  Generated {len(season_eps)} episode pages")
        season_episodes[season_name] = season_eps

    # Write search index
    print("\nWriting search index...")
    search_index_path = os.path.join(SITE_OUTPUT, "search-index.json")
    with open(search_index_path, "w", encoding="utf-8") as f:
        json.dump(search_index_data, f)
    print(f"  Search index: {len(search_index_data)} episodes, {os.path.getsize(search_index_path) // 1024} KB")

    # Write homepage
    print("Generating homepage...")
    with open(os.path.join(SITE_OUTPUT, "index.html"), "w", encoding="utf-8") as f:
        f.write(index_html(season_episodes))

    # Write search page
    print("Generating search page...")
    with open(os.path.join(SITE_OUTPUT, "search.html"), "w", encoding="utf-8") as f:
        f.write(search_page_html(season_episodes))

    # Write sitemap.xml and robots.txt
    print("Generating sitemap.xml and robots.txt...")
    with open(os.path.join(SITE_OUTPUT, "sitemap.xml"), "w", encoding="utf-8") as f:
        f.write(build_sitemap(season_episodes))
    with open(os.path.join(SITE_OUTPUT, "robots.txt"), "w", encoding="utf-8") as f:
        f.write(build_robots_txt())

    print(f"\n{'='*50}")
    print("SITE GENERATION COMPLETE")
    total = sum(len(eps) for eps in season_episodes.values())
    print(f"Episode pages: {total}")
    print(f"Search index: {len(search_index_data)} episodes")
    print(f"\nNext — push to GitHub:")
    print(f"  cd {SITE_OUTPUT}")
    print(f"  git add .")
    print(f'  git commit -m "Add search functionality"')
    print(f"  git push origin main")
    print(f"{'='*50}")

main()
