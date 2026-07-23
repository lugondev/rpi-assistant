from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable


def _post_json(base_url: str, path: str, body: dict, opener) -> dict:
    req = urllib.request.Request(
        urllib.parse.urljoin(base_url, path),
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with opener(req, timeout=30) as resp:  # nosec B310
        return json.loads(resp.read())


def pair_init(base_url: str, serial: str, *, opener=urllib.request.urlopen) -> tuple[str, str]:
    data = _post_json(base_url, "/v1/devices/pair/init", {"serial": serial}, opener)["data"]
    return data["code"], data["poll_token"]


def pair_status(base_url: str, poll_token: str, *, opener=urllib.request.urlopen) -> dict | None:
    url = urllib.parse.urljoin(
        base_url, "/v1/devices/pair/status?" + urllib.parse.urlencode({"poll_token": poll_token})
    )
    req = urllib.request.Request(url, method="GET")
    try:
        with opener(req, timeout=30) as resp:  # nosec B310
            return json.loads(resp.read())["data"]
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def run_pairing(
    base_url: str,
    serial: str,
    *,
    show_code: Callable[[str], None],
    sleep: Callable[[float], None],
    opener=urllib.request.urlopen,
    poll_interval: float = 3.0,
) -> str:
    while True:
        # pair_init can fail outright (DNS failure, connection refused, gateway
        # down at boot, or a transient 5xx) before we ever have a poll_token.
        # Retry quietly with backoff instead of crashing the whole service --
        # this commonly happens booting before the network/gateway is up.
        try:
            code, poll_token = pair_init(base_url, serial, opener=opener)
        except (urllib.error.URLError, urllib.error.HTTPError):
            sleep(poll_interval)
            continue
        show_code(code)
        while True:
            try:
                status = pair_status(base_url, poll_token, opener=opener)
            except (urllib.error.URLError, urllib.error.HTTPError):
                # Transient network/server error while polling -- keep polling
                # with the same token rather than propagating and crashing.
                sleep(poll_interval)
                continue
            if status is None:
                break  # expired -> re-init, show a fresh code
            if status.get("claimed"):
                return status["token"]
            sleep(poll_interval)
