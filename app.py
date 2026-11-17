
import os
import re
import requests

from threading import local
from flask import Flask, Response, request, abort
from urllib.parse import urlparse, urljoin, quote
from waitress import serve

app = Flask(__name__)

REFERER = "https://tvnow.st/"
USER_AGENT = "Mozilla/5.0"

CHANNELS = {
    "nickelodeon": "https://cache0.wonvt.st/live/1ewxgzu023dos60vlh62k/master.m3u8",
    "disney-channel": "https://cache0.wonvt.st/live/0i3hubv0wk47dd1l1o550/master.m3u8",
    "disney-junior": "https://cache0.wonvt.st/live/12axgb204j4al111stg8p/master.m3u8",
    "disney-xd": "https://cache0.wonvt.st/live/0sw6uxy1c6ro7z0wmk5ex/master.m3u8",
    "cartoon-network": "https://cache0.wonvt.st/live/162nn8p18ljgc71v5dou4/master.m3u8",
    "nicktoons": "https://cache0.wonvt.st/live/06dd65k13kqj0919p35h0/master.m3u8",
    "nick-jr": "https://cache0.wonvt.st/live/1bex0aj18f1l6i1q5egjg/master.m3u8",
    "comedy-central": "https://cache0.wonvt.st/live/14rw4rg1p40gaj1jl7u0v/master.m3u8"
}

ALLOWED_HOSTS = {
    "cache0.wonvt.st",
    "silver-harbor-27.lifeisaware.workers.dev"
}

MAX_REDIRECTS = 5
MAX_PLAYLIST_SIZE = 2 * 1024 * 1024
CHUNK_SIZE = 32 * 1024
TIMEOUT = (8, 30)

thread_local = local()


def get_session():
    if not hasattr(thread_local, "session"):
        session = requests.Session()
        session.headers.update({
            "Referer": REFERER,
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity"
        })
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=4,
            max_retries=0
        )
        session.mount("https://", adapter)
        thread_local.session = session

    return thread_local.session


def validate_url(url):
    if not isinstance(url, str) or len(url) > 8192:
        abort(403)

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname not in ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        abort(403)

    return url


def local_proxy_url(url):
    validate_url(url)
    return "/proxy?url=" + quote(url, safe="")


def rewrite_playlist(content, base_url):
    output = []

    for line in content.splitlines():
        line = line.strip()

        if not line:
            output.append("")
            continue

        if line.startswith("#"):
            def replace_uri(match):
                original = match.group(2)

                if original.startswith(("data:", "skd:")):
                    return match.group(0)

                absolute = urljoin(base_url, original)

                return (
                    "URI=" + match.group(1)
                    + local_proxy_url(absolute)
                    + match.group(1)
                )

            line = re.sub(
                r"""URI=(["'])(.*?)\1""",
                replace_uri,
                line,
                flags=re.IGNORECASE
            )
            output.append(line)
        else:
            output.append(
                local_proxy_url(urljoin(base_url, line))
            )

    return "\n".join(output) + "\n"


def fetch_upstream(url, headers):
    session = get_session()

    for attempt in range(MAX_REDIRECTS + 1):
        validate_url(url)

        upstream = session.get(
            url,
            headers=headers,
            timeout=TIMEOUT,
            stream=True,
            allow_redirects=False
        )

        if upstream.status_code not in (301, 302, 303, 307, 308):
            return upstream

        location = upstream.headers.get("Location")
        upstream.close()

        if not location or attempt == MAX_REDIRECTS:
            return None

        url = urljoin(url, location)

    return None


def proxy_response(url):
    validate_url(url)

    headers = {}

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    upstream = None

    try:
        upstream = fetch_upstream(url, headers)

        if upstream is None:
            return Response("Upstream unavailable", status=502)

        if upstream.status_code not in (200, 206, 416):
            upstream.close()
            return Response("Upstream error", status=502)

        content_type = upstream.headers.get("Content-Type", "")

        iterator = upstream.iter_content(CHUNK_SIZE)
        first_chunk = next(iterator, b"")

        is_playlist = (
            "mpegurl" in content_type.lower()
            or upstream.url.lower().split("?")[0].endswith(".m3u8")
            or first_chunk.lstrip().startswith(b"#EXTM3U")
        )

        response_headers = {
            "Access-Control-Allow-Origin": "*",
            "Cache-Control": "no-store"
        }

        if is_playlist:
            try:
                chunks = [first_chunk]
                total_size = len(first_chunk)

                for chunk in iterator:
                    total_size += len(chunk)

                    if total_size > MAX_PLAYLIST_SIZE:
                        return Response("Playlist too large", status=502)

                    chunks.append(chunk)

                content = b"".join(chunks).decode(
                    "utf-8-sig",
                    errors="replace"
                )

                result = rewrite_playlist(content, upstream.url)

                return Response(
                    result,
                    status=200,
                    content_type="application/vnd.apple.mpegurl",
                    headers=response_headers
                )

            finally:
                upstream.close()

        for header in (
            "Content-Length",
            "Content-Range",
            "Accept-Ranges",
            "ETag",
            "Last-Modified"
        ):
            value = upstream.headers.get(header)
            if value:
                response_headers[header] = value

        def generate():
            try:
                if first_chunk:
                    yield first_chunk

                for chunk in iterator:
                    if chunk:
                        yield chunk
            finally:
                upstream.close()

        return Response(
            generate(),
            status=upstream.status_code,
            headers=response_headers,
            content_type=content_type or "application/octet-stream",
            direct_passthrough=True
        )

    except requests.RequestException:
        if upstream is not None:
            upstream.close()

        return Response("Upstream connection failed", status=502)


@app.route("/")
def home():
    return (
        "These are m3u8 links controlled by @RoqueDJ in GitHub. "
        "Do not upload to iptv-org, as they are being tested. Thank you."
    )


@app.route("/health")
def health():
    return {
        "ok": True,
        "service": "TVNow",
        "channels": len(CHANNELS)
    }


@app.route("/channels")
def channels():
    return {
        "ok": True,
        "count": len(CHANNELS),
        "channels": [
            {"name": name, "url": f"/{name}.m3u8"}
            for name in CHANNELS
        ]
    }


@app.route("/<channel>.m3u8")
def live(channel):
    if channel not in CHANNELS:
        abort(404)

    return proxy_response(CHANNELS[channel])


@app.route("/proxy")
def proxy():
    url = request.args.get("url")

    if not url:
        abort(400)

    return proxy_response(url)


if __name__ == "__main__":
    serve(
        app,
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        threads=4,
        connection_limit=20
    )
    
