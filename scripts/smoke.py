#!/usr/bin/env python3
"""Wait for API liveness, readiness, and the frontend entrypoint."""

import argparse
import sys
import time
import urllib.error
import urllib.request

ENDPOINTS = ("/healthz", "/readyz", "/")


def check_endpoint(url: str, timeout: float, opener=None) -> str | None:
    if opener is None:
        opener = urllib.request.urlopen
    try:
        with opener(url, timeout=timeout) as response:
            status = response.getcode()
            response.read(2048)
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return str(exc)
    if status != 200:
        return f"HTTP {status}"
    return None


def run_smoke(base_url: str, timeout: float, opener=None) -> dict[str, str]:
    deadline = time.monotonic() + timeout
    pending = list(ENDPOINTS)
    errors = {}
    while pending:
        for path in list(pending):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            url = f"{base_url.rstrip('/')}{path}"
            error = check_endpoint(url, min(5.0, remaining), opener)
            if error is None:
                print(f"ok: {url}")
                pending.remove(path)
                errors.pop(path, None)
            else:
                errors[path] = error
        if not pending:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(1.0, remaining))
    for path in pending:
        errors[path] = errors.get(path, "smoke deadline exceeded")
    return errors


def positive_float(value: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=positive_float, default=120.0, help="overall smoke timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    errors = run_smoke(args.base_url, args.timeout)
    if errors:
        for path, error in errors.items():
            print(f"failed: {path}: {error}", file=sys.stderr)
        return 1
    print("smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
