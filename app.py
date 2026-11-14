
import os
import re
import requests

from flask import Flask, Response, request, abort
from urllib.parse import urlparse, urljoin, quote, unquote

app = Flask(__name__)

UPSTREAM = (
    "https://cache0.wonvt.st/live/"
    "1ewxgzu023dos60vlh62k/master.m3u8"
)

REFERER = "https://tvnow.st/"

ALLOWED_HOSTS = {
    "cache0.wonvt.st"
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

        # Rewrite URI attributes (keys, maps, etc.)
        def replace_uri(match):
            original = match.group(2)

            if original.startswith(("data:", "skd:")):
                return match.group(0)

            absolute = urljoin(base_url, original)

            return 'URI="' + local_proxy_url(absolute) + '"'

        line = re.sub(
            r'URI="([^"]+)"',
            lambda m: replace_uri(m),
            line
        )

        # Rewrite playlist and segment URLs
        if not line.startswith("#"):
            absolute = urljoin(base_url, line)
            line = local_proxy_url(absolute)

        output.append(line)

    return "\n".join(output) + "\n"


@app.route("/")
def home():
    return "RoqueDJ HLS Proxy Online"


@app.route("/live.m3u8")
def live():

    return proxy_response(UPSTREAM)


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
            return Response(
                "Upstream error",
                status=502
            )

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
  
