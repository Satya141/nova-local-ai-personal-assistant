"""NOVA's browser: the user's installed Edge, driven through Playwright, in its own profile.

The profile is separate from the user's everyday browser: it is signed out of
everything, so NOVA cannot act on the user's accounts by accident. The window
is visible, so the user can watch what NOVA does and take over.

Page text is untrusted. It goes back to the model marked as such, and every
action it might suggest still passes through the permission gate.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote_plus, urlparse

log = logging.getLogger(__name__)

TEXT_LIMIT = 3000
ELEMENT_LIMIT = 80
SEARCH_URL = "https://html.duckduckgo.com/html/?q={query}"

# Numbers each visible interactive element so the model can refer to it, and describes it.
_ELEMENTS_JS = """
(limit) => {
  const items = [];
  const nodes = document.querySelectorAll(
    'a[href], button, input:not([type=hidden]), textarea, select, [role=button], [role=link], [contenteditable=true]');
  let id = 0;
  for (const el of nodes) {
    const box = el.getBoundingClientRect();
    const style = getComputedStyle(el);
    if (box.width === 0 || box.height === 0 || style.visibility === 'hidden' || style.display === 'none') continue;
    id += 1;
    el.setAttribute('data-nova-id', String(id));
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    const label = (el.getAttribute('aria-label') || el.innerText || el.value || el.placeholder
                   || el.title || el.name || '').trim().replace(/\\s+/g, ' ').slice(0, 80);
    items.push({id, tag, type, role: el.getAttribute('role') || '', label, href: tag === 'a' ? el.href : null,
                target: el.getAttribute('target') || ''});
    if (id >= limit) break;
  }
  return items;
}
"""

_TEXT_JS = """
() => {
  const root = document.querySelector('main, article, [role=main]') || document.body;
  return root ? root.innerText : '';
}
"""

# DuckDuckGo's HTML results, or Bing's; whichever page is open.
_RESULTS_JS = """
() => {
  const pick = (rows, title, snippet) => [...document.querySelectorAll(rows)].map(r => ({
    title: (r.querySelector(title) || {}).innerText || '',
    url: (r.querySelector(title) || {}).href || '',
    snippet: (r.querySelector(snippet) || {}).innerText || '',
  })).filter(r => r.title && r.url);
  const ddg = pick('.result', '.result__a', '.result__snippet');
  return ddg.length ? ddg : pick('li.b_algo', 'h2 a', '.b_caption p, .b_lineclamp2, .b_lineclamp3');
}
"""
BING_URL = "https://www.bing.com/search?q={query}"
# Search engines sometimes ask an automated browser to prove it is a person. NOVA never tries to
# get past that; it tells the user, who can complete it in NOVA's window if they want to.
_HUMAN_CHECK = re.compile(r"bots use duckduckgo|complete the following challenge|unusual traffic|verify you are (a )?human|captcha", re.I)


class BrowserError(RuntimeError):
    """The browser could not do what was asked. The message is shown to the model."""


# Schemes written without "//" that must never be mistaken for a host name.
_NON_WEB = re.compile(r"^(javascript|data|file|vbscript|about|blob|view-source|edge|chrome|ms-[a-z-]+|mailto|tel):", re.I)
_HOST = re.compile(r"^(localhost|[a-z0-9-]+(\.[a-z0-9-]+)+|\d{1,3}(\.\d{1,3}){3})(:\d{1,5})?$", re.I)


def safe_url(raw: str) -> str:
    """Only web pages: no file://, javascript:, data:, browser-internal or other schemes."""
    url = raw.strip()
    refusal = BrowserError(f"NOVA only opens web pages (http or https), not '{raw}'.")
    if _NON_WEB.match(url):
        raise refusal
    if "://" in url:
        if not url.lower().startswith(("http://", "https://")):
            raise refusal
    else:
        url = "https://" + url
    if not _HOST.match(urlparse(url).netloc):
        raise refusal
    return url


def unwrap_result_url(url: str) -> str:
    """Search engines send result links through a redirect; return the real destination."""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    if parsed.netloc.endswith("duckduckgo.com") and parsed.path.startswith("/l/") and query.get("uddg"):
        return query["uddg"][0]
    if parsed.netloc.endswith("bing.com") and parsed.path.startswith("/ck/") and query.get("u"):
        encoded = query["u"][0]
        if encoded.startswith("a1"):
            payload = encoded[2:]
            try:
                return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                pass
    return url


class BrowserSession:
    def __init__(
        self,
        profile_dir: Path,
        *,
        headless: bool = False,
        channel: str = "msedge",
        search_urls: tuple[str, ...] = (SEARCH_URL, BING_URL),
    ) -> None:
        self._profile = profile_dir
        self._headless = headless
        self._channel = channel
        # Tried in order until one gives results.
        self._search_urls = search_urls
        self._lock = asyncio.Lock()
        self._playwright = None
        self._context = None
        self._page = None
        # The numbered elements from the last read, so a confirmation can name what will be clicked.
        self.elements: dict[int, dict[str, Any]] = {}

    async def _ensure_page(self):
        if self._page is not None and not self._page.is_closed():
            return self._page
        if self._context is not None:
            # The user closed NOVA's window; start again.
            await self._shutdown()
        from playwright.async_api import async_playwright

        self._profile.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(
                str(self._profile), channel=self._channel, headless=self._headless, no_viewport=not self._headless,
                args=["--no-first-run", "--no-default-browser-check"],
                # A page must not be able to put files on the user's computer through NOVA.
                accept_downloads=False,
            )
        except Exception as exc:
            await self._shutdown()
            raise BrowserError(f"Could not start the browser (Microsoft Edge is needed): {exc}") from exc
        self._page = self._context.pages[0] if self._context.pages else await self._context.new_page()
        return self._page

    async def _snapshot(self, page, text_limit: int = TEXT_LIMIT) -> dict[str, Any]:
        await page.wait_for_load_state("domcontentloaded")
        text = re.sub(r"\n{3,}", "\n\n", (await page.evaluate(_TEXT_JS)) or "").strip()
        elements = await page.evaluate(_ELEMENTS_JS, ELEMENT_LIMIT)
        self.elements = {item["id"]: item for item in elements}
        return {
            "title": await page.title(),
            "url": page.url,
            "text": text[:text_limit] + (" ..." if len(text) > text_limit else ""),
            "elements": elements,
        }

    async def open(self, url: str) -> dict[str, Any]:
        url = safe_url(url)
        async with self._lock:
            page = await self._ensure_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
            except Exception as exc:
                raise BrowserError(f"Could not open {url}: {exc}") from exc
            return await self._snapshot(page)

    async def read(self) -> dict[str, Any]:
        async with self._lock:
            if self._page is None or self._page.is_closed():
                raise BrowserError("No web page is open. Use open_web_page or web_search first.")
            return await self._snapshot(self._page)

    async def search(self, query: str, limit: int = 6) -> list[dict[str, str]]:
        async with self._lock:
            page = await self._ensure_page()
            asked_for_human = False
            for template in self._search_urls:
                try:
                    await page.goto(template.format(query=quote_plus(query)), wait_until="domcontentloaded", timeout=25000)
                    results = await page.evaluate(_RESULTS_JS)
                    if not results:
                        asked_for_human |= bool(_HUMAN_CHECK.search(await page.evaluate("() => document.body.innerText")))
                except Exception as exc:
                    log.warning("Search via %s failed: %s", template, exc)
                    continue
                if results:
                    return [
                        {"title": r["title"].strip(), "url": unwrap_result_url(r["url"]), "snippet": r["snippet"].strip()[:300]}
                        for r in results[:limit]
                    ]
            if asked_for_human:
                raise BrowserError(
                    "The search engine asked to confirm a person is searching. NOVA does not answer those checks; "
                    "the user can complete it in NOVA's browser window and ask again."
                )
            return []

    def _locator(self, page, element_id: int):
        if element_id not in self.elements:
            raise BrowserError(f"There is no element {element_id} on the page. Call read_web_page to get fresh numbers.")
        return page.locator(f'[data-nova-id="{element_id}"]').first

    async def click(self, element_id: int) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            locator = self._locator(page, element_id)
            # A link that opens a new tab must be followed, or NOVA would read the old page as if
            # nothing happened. A link that says so is waited for; any other pop-up is caught on the way.
            target = (self.elements[element_id].get("target") or "").lower()
            popups: list = []
            try:
                if target and target not in ("_self", "_parent", "_top"):
                    async with page.expect_popup(timeout=15000) as popup:
                        await locator.click(timeout=8000)
                    popups.append(await popup.value)
                else:
                    def caught(popup) -> None:
                        popups.append(popup)

                    page.on("popup", caught)
                    try:
                        await locator.click(timeout=8000)
                        await page.wait_for_load_state("domcontentloaded", timeout=15000)
                    finally:
                        page.remove_listener("popup", caught)
                if popups:
                    page = self._page = popups[-1]
                    await page.bring_to_front()
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except BrowserError:
                raise
            except Exception as exc:
                raise BrowserError(f"Could not click element {element_id}: {exc}") from exc
            return await self._snapshot(page, text_limit=2500)

    async def type(self, element_id: int, text: str, submit: bool) -> dict[str, Any]:
        async with self._lock:
            page = await self._ensure_page()
            try:
                field = self._locator(page, element_id)
                await field.fill(text, timeout=8000)
                if submit:
                    await field.press("Enter")
                    await page.wait_for_load_state("domcontentloaded", timeout=15000)
            except BrowserError:
                raise
            except Exception as exc:
                raise BrowserError(f"Could not type into element {element_id}: {exc}") from exc
            return await self._snapshot(page, text_limit=2500)

    async def _shutdown(self) -> None:
        for closer in (self._context, self._playwright):
            if closer is None:
                continue
            try:
                await (closer.close() if closer is self._context else closer.stop())
            except Exception:
                pass
        self._context = self._playwright = self._page = None
        self.elements = {}

    async def close(self) -> None:
        async with self._lock:
            await self._shutdown()
