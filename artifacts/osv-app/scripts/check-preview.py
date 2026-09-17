#!/usr/bin/env python3
"""Smoke-check the public ОСВ preview routes without authenticating."""

from __future__ import annotations

import argparse
import sys
import urllib.error
import urllib.parse
import urllib.request


EXPECTED = {
    "/healthz": ("application/json", '"status":"ok"'),
    "/": ("text/html", "Войти в систему"),
    "/login": ("text/html", "Вход"),
}


def check(base_url: str, timeout: float) -> None:
    opener = urllib.request.build_opener(
        urllib.request.HTTPRedirectHandler(),
    )
    for path, (content_type, marker) in EXPECTED.items():
        url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        try:
            with opener.open(url, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                actual_type = response.headers.get_content_type()
                if response.status != 200:
                    raise RuntimeError(f"HTTP {response.status}")
                if actual_type != content_type:
                    raise RuntimeError(
                        f"ожидался Content-Type {content_type}, получен {actual_type}"
                    )
                if marker not in body:
                    raise RuntimeError(f"в ответе нет контрольного текста {marker!r}")
        except (
            OSError,
            RuntimeError,
            UnicodeError,
            urllib.error.HTTPError,
        ) as exc:
            raise RuntimeError(f"{url}: {exc}") from exc
        print(f"OK {url}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "base_url",
        nargs="?",
        default="http://127.0.0.1:21844/",
        help="корневой URL локального сервиса или встроенного preview",
    )
    parser.add_argument("--timeout", type=float, default=10)
    args = parser.parse_args()
    try:
        check(args.base_url, args.timeout)
    except RuntimeError as exc:
        print(f"ОШИБКА preview ОСВ: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())