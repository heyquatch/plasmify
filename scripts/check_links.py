"""Check that every download URL referenced by the built-in presets still resolves."""

from __future__ import annotations

import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from plasmify import presets


def urls_of(preset: presets.Preset) -> list[str]:
    found = []
    for section in ("style", "icons", "cursor", "layout", "shell"):
        data = preset.section(section)
        for value in [data, *data.values()]:
            if isinstance(value, dict):
                found += [value[k] for k in ("url", "repo") if isinstance(value.get(k), str)]
            elif isinstance(value, str) and value.startswith("https://"):
                found.append(value)
    found += [image["url"] for image in preset.section("wallpaper").get("images", [])]
    return sorted(set(found))


def check(url: str) -> tuple[str, str | None]:
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310
    try:
        # URLs come from the reviewed built-in presets and are https (validated on load).
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return url, None if response.status < 400 else str(response.status)
    except urllib.error.HTTPError as exc:
        return url, str(exc.code)
    except OSError as exc:
        return url, str(exc)


def main() -> int:
    urls = sorted({u for p in presets.load_all(include_user=False) for u in urls_of(p)})
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(check, urls))
    broken = [(url, err) for url, err in results if err]
    for url, err in broken:
        print(f"BROKEN {err}: {url}")
    print(f"{len(urls) - len(broken)}/{len(urls)} links OK")
    return 1 if broken else 0


if __name__ == "__main__":
    sys.exit(main())
