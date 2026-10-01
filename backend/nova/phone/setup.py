"""The setup page a phone sees first, over plain HTTP, before it trusts NOVA's certificate.

It serves three things and nothing else: the page, its script, and NOVA's certificate
authority to install. The phone app itself, pairing and the API live only on the HTTPS
listener. The page checks whether the phone already trusts NOVA and, if so, goes straight on.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response
from starlette.routing import Route

CERTIFICATE_NAME = "NOVA.crt"

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="nova-apps" content="{apps}">
<meta name="color-scheme" content="light dark">
<title>Set up NOVA</title>
<style>
  :root {{ --bg: #f7f5f2; --text: #26231f; --muted: #6f6a63; --card: #ffffff; --line: #e4dfd8; --accent: #7a5cff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #171615; --text: #f1eee9; --muted: #a8a29a; --card: #211f1d; --line: #34312d; --accent: #a28bff; }}
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: var(--bg); color: var(--text); font: 16px/1.5 system-ui, sans-serif; }}
  main {{ max-width: 30rem; margin: 0 auto; padding: 2rem 1rem 3rem; }}
  h1 {{ font-size: 1.4rem; margin: 0 0 .5rem; }}
  p {{ margin: .4rem 0; }}
  .muted {{ color: var(--muted); font-size: .9rem; }}
  ol {{ padding: 0; list-style: none; counter-reset: step; margin: 1.2rem 0; }}
  li {{ counter-increment: step; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
       padding: .9rem 1rem .9rem 3rem; margin-bottom: .7rem; position: relative; }}
  li::before {{ content: counter(step); position: absolute; left: 1rem; top: .9rem; width: 1.4rem; height: 1.4rem;
       border-radius: 50%; background: var(--accent); color: #fff; font-size: .8rem; font-weight: 600;
       display: grid; place-items: center; }}
  .button {{ display: inline-block; margin-top: .5rem; padding: .55rem 1rem; border-radius: 999px; border: 0;
       background: var(--accent); color: #fff; font: inherit; font-weight: 600; text-decoration: none; }}
  code {{ font-size: .8rem; word-break: break-all; }}
  [hidden] {{ display: none; }}
</style>
</head>
<body>
<main>
  <h1>Set up NOVA on this phone</h1>
  <p id="status" class="muted">Checking…</p>
  <div id="steps" hidden>
    <p>Your phone needs NOVA's certificate, once. With it, your phone can check that it is really talking to your PC, and everything between them is encrypted.</p>
    <ol>
      <li>Download the certificate.<br><a class="button" href="/{certificate}" download="{certificate}">Download {certificate}</a></li>
      <li>Open <b>Settings</b>, search for <b>CA certificate</b> and open it. Tap <b>Install anyway</b>, then choose <b>{certificate}</b> from Downloads. Android may ask for your screen lock.</li>
      <li>Come back here.<br><button id="continue" class="button" type="button">Continue</button></li>
    </ol>
    <p class="muted">The certificate is called <b>{authority}</b>. It can only vouch for addresses on home networks, never for a website. Its SHA-256 fingerprint, under Settings → Trusted credentials → User, starts with <code>{fingerprint}</code>, the same as on the PC.</p>
  </div>
</main>
<script src="/setup.js"></script>
</body>
</html>
"""

_SCRIPT = """\
// NOVA's .local name first (the same on every Wi-Fi, so pairing carries over), then the
// address on this network.
const apps = document.querySelector('meta[name="nova-apps"]').content.split(" ").filter(Boolean);
const code = location.hash.slice(1).replace(/\\D/g, "").slice(0, 6);
const status = document.getElementById("status");
const steps = document.getElementById("steps");

// Fails (a network error) until the phone trusts NOVA's certificate, or cannot find the name.
async function reachable(app) {
  const stop = new AbortController();
  const timer = setTimeout(() => stop.abort(), 4000);
  try {
    await fetch(app + "/phone-icon.png", { mode: "no-cors", cache: "no-store", signal: stop.signal });
    return true;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

async function check(again) {
  status.textContent = "Checking…";
  for (const app of apps) {
    if (await reachable(app)) {
      status.textContent = "Opening NOVA…";
      location.replace(app + "/phone" + (code ? "#code=" + code : ""));
      return;
    }
  }
  steps.hidden = false;
  status.textContent = again
    ? "Your phone does not trust NOVA's certificate yet. Check step 2, then tap Continue again."
    : "";
}

document.getElementById("continue").addEventListener("click", () => check(true));
check(false);
"""


def setup_app(
    app_urls: Callable[[], list[str]],
    authority: Callable[[], tuple[str, str, bytes]],
    allowed: Callable[[Any, Any], bool],
) -> Starlette:
    """`app_urls` are the phone app's HTTPS origins, best first; `authority` gives (name,
    fingerprint, DER); `allowed(server, client)` says whether a sender may use this listener."""

    def headers(request: Request) -> dict[str, str]:
        return {
            "Content-Security-Policy": (
                "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; "
                f"connect-src {' '.join(app_urls()) or "'none'"}; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            ),
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
        }

    async def page(request: Request) -> Response:
        if not allowed(request.scope.get("server"), request.scope.get("client")):
            return PlainTextResponse("Phone access is for your own network and devices only", 403)
        urls = app_urls()
        if not urls:
            return PlainTextResponse("Phone access is off", 503)
        name, fingerprint, _ = authority()
        body = _PAGE.format(
            apps=html.escape(" ".join(urls)),
            certificate=CERTIFICATE_NAME,
            authority=html.escape(name),
            fingerprint=html.escape(fingerprint[:11]),
        )
        return Response(body, media_type="text/html; charset=utf-8", headers=headers(request))

    async def script(request: Request) -> Response:
        if not allowed(request.scope.get("server"), request.scope.get("client")):
            return PlainTextResponse("Phone access is for your own network and devices only", 403)
        return Response(_SCRIPT, media_type="text/javascript; charset=utf-8", headers=headers(request))

    async def certificate(request: Request) -> Response:
        if not allowed(request.scope.get("server"), request.scope.get("client")):
            return PlainTextResponse("Phone access is for your own network and devices only", 403)
        # As a plain download: Chrome hands the CA certificate type to Android's installer, which
        # since Android 11 refuses it outright instead of saving the file for Settings.
        return Response(
            authority()[2],
            media_type="application/octet-stream",
            headers={**headers(request), "Content-Disposition": f'attachment; filename="{CERTIFICATE_NAME}"'},
        )

    async def elsewhere(request: Request) -> Response:
        return RedirectResponse("/")

    return Starlette(
        routes=[
            Route("/", page),
            Route("/setup.js", script),
            Route(f"/{CERTIFICATE_NAME}", certificate),
            Route("/{rest:path}", elsewhere),
        ]
    )
