
import os
import re
import requests

from flask import Flask, Response, request, abort
from urllib.parse import urlparse, urljoin, quote, unquote

app = Flask(__name__)

REFERER = "https://tvnow.st/"

CHANNELS = {
    "nickelodeon": "https://cache0.wonvt.st/live/1ewxgzu023dos60vlh62k/master.m3u8",
    "disney-channel": "https://cache0.wonvt.st/live/0i3hubv0wk47dd1l1o550/master.m3u8",
    "disney-junior": "https://cache0.wonvt.st/live/12axgb204j4al111stg8p/master.m3u8",
    "disney-xd": "https://cache0.wonvt.st/live/0sw6uxy1c6ro7z0wmk5ex/master.m3u8",
    "cartoon-network": "https://cache0.wonvt.st/live/162nn8p18ljgc71v5dou4/master.m3u8",
    "nicktoons": "https://cache0.wonvt.st/live/06dd65k13kqj0919p35h0/master.m3u8",
    "nick-jr": "https://cache0.wonvt.st/live/1bex0aj18f1l6i1q5egjg/master.m3u8",
}

ALLOWED_HOSTS = {
    "cache0.wonvt.st",
    "silver-harbor-27.lifeisaware.workers.dev"
}

session = requests.Session()

session.headers.update({
    "Referer": REFERER,
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*"
})


def validate_url(url):
    parsed = urlparse(url)

    if parsed.scheme != "https":
        abort(403)

    if parsed.hostname not in ALLOWED_HOSTS:
        abort(403)

    return url


def local_proxy_url(url):
    return "/proxy?url=" + quote(url, safe="")


def rewrite_playlist(content, base_url):
    lines = content.splitlines()
    output = []

    for line in lines:
        line = line.strip()

        if not line:
            output.append(line)
            continue

        def replace_uri(match):
            original = match.group(1)

            if original.startswith(("data:", "skd:")):
                return match.group(0)

            absolute = urljoin(base_url, original)

            return 'URI="' + local_proxy_url(absolute) + '"'

        line = re.sub(
            r'URI="([^"]+)"',
            replace_uri,
            line
        )

        if not line.startswith("#"):
            absolute = urljoin(base_url, line)
            line = local_proxy_url(absolute)

        output.append(line)

    return "\n".join(output) + "\n"


@app.route("/")
def home():
    return "RoqueDJ HLS Proxy Online"


@app.route("/channels")
def channels():
    return {
        "ok": True,
        "count": len(CHANNELS),
        "channels": [
            {
                "name": name,
                "url": f"/{name}.m3u8"
            }
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

    return proxy_response(unquote(url))


def proxy_response(url):
    validate_url(url)

    headers = {}

    if request.headers.get("Range"):
        headers["Range"] = request.headers["Range"]

    try:
        upstream = session.get(
            url,
            headers=headers,
            timeout=20,
            stream=False,
            allow_redirects=True
        )

        validate_url(upstream.url)

        if upstream.status_code >= 400:
            return Response("Upstream error", status=502)

        content_type = upstream.headers.get(
            "Content-Type", ""
        )

        if (
            "mpegurl" in content_type.lower()
            or upstream.text.lstrip().startswith("#EXTM3U")
        ):
            result = rewrite_playlist(
                upstream.text,
                upstream.url
            )

            response = Response(
                result,
                content_type="application/vnd.apple.mpegurl"
            )
        else:
            response = Response(
                upstream.content,
                status=upstream.status_code,
                content_type=content_type or
                "application/octet-stream"
            )

        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "no-store"

        return response

    except requests.RequestException:
        return Response(
            "Upstream connection failed",
            status=502
        )


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
    )
    
