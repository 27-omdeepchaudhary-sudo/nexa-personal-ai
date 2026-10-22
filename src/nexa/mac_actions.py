"""macOS actions for NEXA. Brave only; never launches Chrome."""
import re, shutil, subprocess, urllib.parse, urllib.request
from html.parser import HTMLParser
from difflib import SequenceMatcher

BRAVE = "Brave Browser"

def _osascript(script):
    p = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
    if p.returncode != 0:
        err = (p.stderr or "").strip()
        if "not allowed" in err.lower() or "assistive" in err.lower() or "-1743" in err:
            return False, "macOS needs Automation/Accessibility permission for this control."
        return False, "macOS could not complete that action. Check Terminal permissions."
    return True, ""

def open_url(url):
    """Open a URL in Brave and report launch failures."""
    try:
        result = subprocess.run(
            ["open", "-a", BRAVE, url],
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )
        if result.returncode != 0:
            return "I couldn't open Brave or the requested page. Check that Brave Browser is installed."
        return ""
    except subprocess.TimeoutExpired:
        return "Opening Brave timed out. Please check whether the browser is responding."
    except OSError:
        return "I couldn't launch Brave. Check that Brave Browser is installed."


_LAST_GOOGLE_QUERY = ""
_LAST_GOOGLE_START = 0


class _ReadablePageParser(HTMLParser):
    """Extract readable text from ordinary HTML pages."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip_depth = 0
        self.skip_tags = {"script", "style", "noscript", "svg", "nav", "footer"}

    def handle_starttag(self, tag, attrs):
        if tag in self.skip_tags:
            self.skip_depth += 1
        elif not self.skip_depth and tag in {
            "p", "h1", "h2", "h3", "h4", "li", "article", "section", "br"
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.skip_tags and self.skip_depth:
            self.skip_depth -= 1
        elif not self.skip_depth and tag in {
            "p", "h1", "h2", "h3", "h4", "li", "article", "section", "br"
        }:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self.skip_depth:
            value = " ".join(data.split())
            if value:
                self.parts.append(value)

def get_current_page_content():
    """Read the public HTML of the active Brave tab; never claim private-page access."""
    try:
        result = subprocess.run(
            [
                "osascript", "-e",
                'tell application "Brave Browser" to get URL of active tab of front window'
            ],
            capture_output=True, text=True, timeout=5, check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None, "I couldn't identify the active Brave tab. Please open a webpage first."

        url = result.stdout.strip()
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None, "This page isn't a public web address that I can read."

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/131 Safari/537.36"},
        )
        with urllib.request.urlopen(request, timeout=12) as response:
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None, "This page doesn't appear to be an HTML webpage."
            html = response.read(1_000_000).decode("utf-8", errors="replace")

        parser = _ReadablePageParser()
        parser.feed(html)
        content = " ".join(" ".join(parser.parts).split())
        if len(content) < 250:
            return None, "I couldn't extract enough readable text. The page may need a login or JavaScript."
        return (url, content[:12000]), None

    except Exception as exc:
        print(f"[NEXA] Page reading failed: {type(exc).__name__}")
        return None, "I couldn't read this webpage. It may require login, JavaScript, or browser-only access."

def _navigate_brave_same_tab(url):
    """Navigate the active Brave tab using macOS keyboard automation."""
    safe_url = url.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        f'tell application "{BRAVE}" to activate\n'
        'delay 0.3\n'
        'tell application "System Events"\n'
        'keystroke "l" using {command down}\n'
        'delay 0.1\n'
        f'keystroke "{safe_url}"\n'
        'key code 36\n'
        'end tell'
    )
    ok, error = _osascript(script)
    return "" if ok else error

def open_google():
    error = _navigate_brave_same_tab("https://www.google.com/")
    return error or "Google is open in the current Brave tab."

def search_google(query):
    global _LAST_GOOGLE_QUERY, _LAST_GOOGLE_START
    query = query.strip(" \t\n\r\"'“”‘’")
    if not query:
        return "Tell me what you want to search on Google."
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query)
    error = _navigate_brave_same_tab(url)
    if error:
        return error
    _LAST_GOOGLE_QUERY = query
    _LAST_GOOGLE_START = 0
    return f"Searching Google for {query}."

def next_google_results():
    global _LAST_GOOGLE_START
    if not _LAST_GOOGLE_QUERY:
        return "Please search for a topic on Google first."
    next_start = _LAST_GOOGLE_START + 10
    url = (
        "https://www.google.com/search?q="
        + urllib.parse.quote_plus(_LAST_GOOGLE_QUERY)
        + f"&start={next_start}"
    )
    error = _navigate_brave_same_tab(url)
    if error:
        return error
    _LAST_GOOGLE_START = next_start
    return "Showing the next Google results page."


class _GoogleResultParser(HTMLParser):
    """Collect external links whose anchors contain Google result headings."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchor = None
        self.heading_depth = 0
        self.heading_text = []
        self.results = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self.anchor = {"href": attrs.get("href", ""), "has_heading": False}
            self.heading_text = []
        elif tag == "h3" and self.anchor is not None:
            self.heading_depth += 1
            self.anchor["has_heading"] = True

    def handle_data(self, data):
        if self.heading_depth:
            self.heading_text.append(data)

    def handle_endtag(self, tag):
        if tag == "h3" and self.heading_depth:
            self.heading_depth -= 1
        elif tag == "a" and self.anchor is not None:
            href = self.anchor["href"]
            title = " ".join("".join(self.heading_text).split())
            if self.anchor["has_heading"] and href and title:
                if href.startswith("/url?"):
                    params = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                    href = (params.get("q") or params.get("url") or [""])[0]
                parsed = urllib.parse.urlparse(href)
                host = (parsed.hostname or "").lower()
                if parsed.scheme in ("http", "https") and host and not (
                    host == "google.com" or host.endswith(".google.com")
                ):
                    if not any(item[1] == href for item in self.results):
                        self.results.append((title, href))
            self.anchor = None
            self.heading_depth = 0
            self.heading_text = []

def _fetch_google_results(query):
    url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(query) + "&num=10"
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/131 Safari/537.36"},
    )
    with urllib.request.urlopen(request, timeout=12) as response:
        html = response.read().decode("utf-8", errors="replace")
    parser = _GoogleResultParser()
    parser.feed(html)
    return parser.results

def open_google_result(number):
    if not _LAST_GOOGLE_QUERY:
        return "Please search for a topic on Google first."
    if number not in (1, 2, 3, 4, 5):
        return "I can open one of the first five results."
    try:
        results = _fetch_google_results(_LAST_GOOGLE_QUERY)
    except Exception as exc:
        print(f"[NEXA] Google result lookup failed: {type(exc).__name__}")
        return "I couldn't read the Google results. Please check your internet connection or try another search."
    if len(results) < number:
        return "I couldn't identify that result on the current Google results page."
    title, url = results[number - 1]
    error = _navigate_brave_same_tab(url)
    return error or f"Opening result {number}: {title}."

def search_google_followup():
    if not _LAST_GOOGLE_QUERY:
        return "Please search for a topic on Google first."
    return search_google(_LAST_GOOGLE_QUERY + " detailed explanation")

def open_youtube():
    error = open_url("https://www.youtube.com/")
    return error or "Boss, YouTube is open in Brave."

def search_youtube(query):
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    error = open_url(url)
    return error or f"Searching YouTube for {query}."

def search_news(query="latest news"):
    url = "https://news.google.com/search?q=" + urllib.parse.quote_plus(query)
    error = open_url(url)
    return error or f"Opening news results for {query}."

def _find_video(query):
    try:
        import yt_dlp
        opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True, "noplaylist": True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info("ytsearch5:" + query, download=False)
        entries = data.get("entries") or []
        if not entries: return None
        q = re.sub(r"[^\w\s]", " ", query.lower()).strip()
        def score(entry):
            title = re.sub(r"[^\w\s]", " ", (entry.get("title") or "").lower()).strip()
            return SequenceMatcher(None, q, title).ratio() + (0.15 if q and q in title else 0)
        best = max(entries, key=score)
        vid = best.get("id")
        return ("https://www.youtube.com/watch?v=" + urllib.parse.quote(vid), best.get("title", query)) if vid else None
    except Exception as exc:
        print(f"[NEXA] YouTube lookup failed: {type(exc).__name__}")
        return None

def play_song(query):
    query = query.strip(" \t\n\r\"'“”‘’")
    if not query: return "Tell me the song title or artist, and I'll look it up."
    found = _find_video(query)
    if found:
        url, title = found
        error = open_url(url)
        if error:
            return error
        return f"Opening {title} on YouTube in Brave. If autoplay is blocked, click Play once."
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    error = open_url(url)
    if error:
        return error
    return f"I couldn't resolve a direct video for {query}, so I've opened the search results."

def player_action(action):
    # YouTube keyboard shortcuts need the browser/player to be frontmost. K is a toggle.
    keys = {"pause": "k", "resume": "k", "toggle": "k", "next": "n", "previous": "p", "forward": "l", "back": "j"}
    if action not in keys: return "I don't recognize that playback control."
    key = keys[action]
    modifier = "shift down" if action in ("next", "previous") else ""
    using = f" using {{{modifier}}}" if modifier else ""
    script = f'tell application "{BRAVE}" to activate\ndelay 0.4\ntell application "System Events" to keystroke "{key}"{using}'
    ok, err = _osascript(script)
    if not ok: return err
    if action in ("pause", "resume", "toggle"):
        return "Sent YouTube's play-pause shortcut. YouTube uses one toggle for both pause and resume, so the video player must be available in Brave."
    return {"next":"Sent YouTube's next-video shortcut.", "previous":"Sent YouTube's previous-video shortcut.", "forward":"Skipped forward about 10 seconds.", "back":"Skipped back about 10 seconds."}[action]

def change_system_volume(direction, level=None):
    """Set exact macOS volume or adjust it by 10 points."""
    try:
        current = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=3
        )
        if current.returncode != 0:
            return "I couldn't read the Mac volume. Check Terminal permissions."

        old_level = int(current.stdout.strip())

        if level is not None:
            new_level = max(0, min(100, int(level)))
        elif direction == "up":
            new_level = min(100, old_level + 10)
        else:
            new_level = max(0, old_level - 10)

        result = subprocess.run(
            ["osascript", "-e", f"set volume output volume {new_level}"],
            capture_output=True, text=True, timeout=3
        )
        if result.returncode != 0:
            return "macOS refused the volume change. Check Terminal permissions."

        verify = subprocess.run(
            ["osascript", "-e", "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=3
        )
        if verify.returncode == 0:
            actual = int(verify.stdout.strip())
            return f"Mac volume is now {actual} percent."

        return f"Requested Mac volume: {new_level} percent."

    except (subprocess.TimeoutExpired, ValueError, OSError):
        return "I couldn't change the Mac volume. Please try again."

def change_brightness(direction, level=None):
    """Adjust brightness relatively, or set an exact 0-100% level when supported.

    Exact percentage control on macOS needs the free `brightness` command-line
    utility. Without it, relative up/down still works using the built-in keys.
    """
    if level is not None:
        try:
            level = max(0, min(100, int(level)))
        except (TypeError, ValueError):
            return "Please give a brightness percentage from 0 to 100."

        utility = shutil.which("brightness")
        if not utility:
            return (
                "Exact brightness percentages need the free 'brightness' command. "
                "Install it with Homebrew using: brew install brightness. "
                "Relative brightness up and down still work."
            )
        try:
            result = subprocess.run(
                [utility, str(level / 100)],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return f"Screen brightness set to {level} percent."
            return "The brightness utility could not set that level. Check macOS permissions."
        except (subprocess.TimeoutExpired, OSError):
            return "I couldn't set the requested brightness percentage."

    # On MacBook built-in keyboards F1 dims and F2 brightens.
    keycode = 122 if direction == "down" else 120
    script = f'tell application "System Events" to key code {keycode}'
    ok, err = _osascript(script)
    return f"Screen brightness {direction}." if ok else err

def control_brave_window(action):
    if action == "minimize":
        script = f'tell application "System Events" to tell process "{BRAVE}" to set value of attribute "AXMinimized" of front window to true'
    elif action == "maximize":
        # Clicking the zoom button toggles the front window between normal and zoomed size.
        script = f'tell application "{BRAVE}" to activate\ndelay 0.2\ntell application "System Events" to tell process "{BRAVE}" to click (first button of front window whose subrole is "AXZoomButton")'
    else:
        return "I don't recognize that window control."
    ok, err = _osascript(script)
    if ok: return f"Brave window {action}d." if action == "minimize" else "Brave window maximized or restored."
    return err


def _research_result_fields(item):
    """Normalize common Google result formats without assuming one shape."""
    title = ""
    url = ""
    snippet = ""

    if isinstance(item, dict):
        title = str(item.get("title") or item.get("name") or "")
        url = str(item.get("url") or item.get("href") or item.get("link") or "")
        snippet = str(item.get("snippet") or item.get("description") or item.get("text") or "")
    elif isinstance(item, (tuple, list)):
        values = [str(value) for value in item]
        for value in values:
            if value.startswith(("https://", "http://")) and not url:
                url = value
        remaining = [value for value in values if value != url]
        if remaining:
            title = remaining[0]
        if len(remaining) > 1:
            snippet = " ".join(remaining[1:])
    elif isinstance(item, str):
        if item.startswith(("https://", "http://")):
            url = item
        else:
            match = re.search(r"https?://\S+", item)
            if match:
                url = match.group(0).rstrip("),.;]")
                title = item[:match.start()].strip()
                snippet = item[match.end():].strip()

    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    if parsed.netloc.lower().endswith("google.com"):
        return None
    return {"title": title[:250], "url": url, "snippet": snippet[:1000]}


def _fetch_research_page(url):
    """Fetch readable text from one public HTML page, with strict size/time limits."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    host = parsed.hostname.lower()
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return ""

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X) AppleWebKit/537.36 Chrome/131 Safari/537.36"
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            final_url = urllib.parse.urlparse(response.geturl())
            if final_url.scheme not in ("http", "https"):
                return ""
            if "html" not in response.headers.get("Content-Type", "").lower():
                return ""
            html = response.read(500_000).decode("utf-8", errors="replace")
        parser = _ReadablePageParser()
        parser.feed(html)
        text = " ".join(" ".join(parser.parts).split())
        return text[:5000] if len(text) >= 250 else ""
    except Exception as exc:
        print(f"[NEXA] Skipped research page ({host}): {type(exc).__name__}", flush=True)
        return ""


def research_google(query, max_sources=3):
    """Collect a small number of public pages from existing Google search results."""
    query = str(query).strip()
    if not query:
        return {"query": query, "sources": []}

    try:
        raw_results = _fetch_google_results(query)
    except Exception as exc:
        print(f"[NEXA] Google research search failed: {type(exc).__name__}", flush=True)
        return {"query": query, "sources": []}

    if isinstance(raw_results, dict):
        raw_results = raw_results.get("results", [])
    if not isinstance(raw_results, (list, tuple)):
        raw_results = []

    sources = []
    seen = set()
    for item in raw_results:
        result = _research_result_fields(item)
        if not result or result["url"] in seen:
            continue
        seen.add(result["url"])
        page_text = _fetch_research_page(result["url"])
        if not page_text:
            continue
        result["content"] = page_text
        sources.append(result)
        if len(sources) >= max(1, min(int(max_sources), 3)):
            break

    return {"query": query, "sources": sources}
