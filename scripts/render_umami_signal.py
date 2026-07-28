#!/usr/bin/env python3
"""Render aggregate Umami traffic into the Organization Profile header SVGs."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import escape
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


REPO_ROOT = Path(__file__).resolve().parents[1]
START_MARKER = "  <!-- UMAMI GENERATED SIGNAL START -->"
END_MARKER = "  <!-- UMAMI GENERATED SIGNAL END -->"
HEADER_PATHS = (
    Path("profile/assets/header-light.svg"),
    Path("profile/assets/header-dark.svg"),
    Path("profile/assets/header-mobile-light.svg"),
    Path("profile/assets/header-mobile-dark.svg"),
)
SNAPSHOT_PATH = Path("profile/assets/data/umami-snapshot.json")
DEFAULT_REGION = "us"
DEFAULT_TIMEZONE = "Asia/Taipei"
WINDOW_HOURS = 24
REQUEST_TIMEOUT_SECONDS = 20.0
REGION_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,15}$")


class SignalSyncError(RuntimeError):
    """A sanitized, user-facing signal synchronization error."""


@dataclass(frozen=True)
class Snapshot:
    """Validated aggregate data used to render every SVG variant."""

    generated_at: datetime
    timezone_name: str
    pageviews: int
    visitors: int
    hourly_pageviews: tuple[int, ...]

    def __post_init__(self) -> None:
        if self.generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        if len(self.hourly_pageviews) != WINDOW_HOURS:
            raise ValueError("hourly_pageviews must contain 24 values")
        if self.pageviews < 0 or self.visitors < 0:
            raise ValueError("summary values must be non-negative")
        if any(value < 0 for value in self.hourly_pageviews):
            raise ValueError("hourly values must be non-negative")


@dataclass(frozen=True)
class SignalLayout:
    """Geometry and typography coordinates for one responsive SVG layout."""

    name: str
    x_start: float
    x_end: float
    y_top: float
    y_baseline: float


DESKTOP_LAYOUT = SignalLayout(
    name="desktop",
    x_start=790,
    x_end=1160,
    y_top=150,
    y_baseline=220,
)
MOBILE_LAYOUT = SignalLayout(
    name="mobile",
    x_start=42,
    x_end=678,
    y_top=420,
    y_baseline=450,
)


def _non_negative_integer(value: Any, field: str) -> int:
    """Return an integer aggregate while rejecting booleans and fractions."""

    if isinstance(value, dict) and set(value) >= {"value"}:
        value = value["value"]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SignalSyncError(f"Umami response field {field!r} is not numeric.")
    if not math.isfinite(float(value)) or int(value) != value or value < 0:
        raise SignalSyncError(
            f"Umami response field {field!r} is not a non-negative integer."
        )
    return int(value)


def _parse_timestamp(value: Any, timezone_info: ZoneInfo) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SignalSyncError("Umami pageviews response contains an invalid timestamp.")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SignalSyncError(
            "Umami pageviews response contains an invalid timestamp."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_info)
    return parsed.astimezone(timezone_info)


def validate_pageviews_response(payload: Any) -> list[tuple[Any, int]]:
    """Validate the documented pageviews response without retaining sessions."""

    if not isinstance(payload, dict) or not isinstance(
        payload.get("pageviews"), list
    ):
        raise SignalSyncError("Umami pageviews response has an invalid schema.")

    # Timestamp parsing needs the requested timezone and happens during bucketing.
    validated: list[tuple[Any, int]] = []
    for index, row in enumerate(payload["pageviews"]):
        if not isinstance(row, dict) or "x" not in row or "y" not in row:
            raise SignalSyncError(
                f"Umami pageviews bucket {index} has an invalid schema."
            )
        value = _non_negative_integer(row["y"], f"pageviews[{index}].y")
        validated.append((row["x"], value))
    return validated


def validate_stats_response(payload: Any) -> tuple[int, int]:
    """Extract only the aggregate pageviews and visitors fields."""

    if not isinstance(payload, dict):
        raise SignalSyncError("Umami stats response has an invalid schema.")
    if "pageviews" not in payload or "visitors" not in payload:
        raise SignalSyncError("Umami stats response is missing aggregate fields.")
    return (
        _non_negative_integer(payload["pageviews"], "pageviews"),
        _non_negative_integer(payload["visitors"], "visitors"),
    )


def build_hourly_buckets(
    pageview_rows: list[tuple[Any, int]],
    generated_at: datetime,
    timezone_info: ZoneInfo,
) -> tuple[int, ...]:
    """Build 24 local-hour buckets, filling absent API buckets with zero."""

    local_now = generated_at.astimezone(timezone_info)
    last_hour = local_now.replace(minute=0, second=0, microsecond=0)
    first_hour = last_hour - timedelta(hours=WINDOW_HOURS - 1)
    buckets = {
        first_hour + timedelta(hours=index): 0
        for index in range(WINDOW_HOURS)
    }

    for raw_timestamp, value in pageview_rows:
        timestamp = _parse_timestamp(raw_timestamp, timezone_info)
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        if hour in buckets:
            buckets[hour] += value

    return tuple(buckets[hour] for hour in sorted(buckets))


def build_snapshot(
    pageviews_payload: Any,
    stats_payload: Any,
    generated_at: datetime,
    timezone_name: str,
) -> Snapshot:
    try:
        timezone_info = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SignalSyncError(
            f"UMAMI_TIMEZONE is not a known IANA timezone: {timezone_name!r}."
        ) from exc

    rows = validate_pageviews_response(pageviews_payload)
    pageviews, visitors = validate_stats_response(stats_payload)
    hourly = build_hourly_buckets(rows, generated_at, timezone_info)
    return Snapshot(
        generated_at=generated_at.astimezone(timezone_info).replace(
            second=0, microsecond=0
        ),
        timezone_name=timezone_name,
        pageviews=pageviews,
        visitors=visitors,
        hourly_pageviews=hourly,
    )


def signal_path(values: tuple[int, ...], layout: SignalLayout) -> str:
    """Map traffic to deterministic SVG coordinates using square-root scaling."""

    if len(values) != WINDOW_HOURS:
        raise ValueError("signal path requires exactly 24 values")
    maximum = max(values, default=0)
    coordinates: list[tuple[float, float]] = []
    for index, value in enumerate(values):
        fraction = index / (WINDOW_HOURS - 1)
        x = layout.x_start + (layout.x_end - layout.x_start) * fraction
        if maximum == 0:
            y = layout.y_baseline
        else:
            normalized = math.sqrt(value / maximum)
            y = layout.y_baseline - (
                layout.y_baseline - layout.y_top
            ) * normalized
        coordinates.append((round(x, 1), round(y, 1)))

    def number(value: float) -> str:
        numeric = float(value)
        return str(int(numeric)) if numeric.is_integer() else f"{numeric:.1f}"

    return "".join(
        f"{'M' if index == 0 else 'L'}{number(x)} {number(y)}"
        for index, (x, y) in enumerate(coordinates)
    )


def _timezone_label(snapshot: Snapshot) -> str:
    if snapshot.timezone_name == "Asia/Taipei":
        return "TST"
    return snapshot.generated_at.tzname() or "LOCAL"


def _render_generated_section(
    snapshot: Snapshot,
    layout: SignalLayout,
    *,
    dark: bool,
) -> str:
    path = signal_path(snapshot.hourly_pageviews, layout)
    stroke = "#55D8F5" if dark else "#006D85"
    generated_iso = snapshot.generated_at.isoformat(timespec="minutes")
    hourly_csv = ",".join(str(value) for value in snapshot.hourly_pageviews)
    views = f"{snapshot.pageviews:,}"
    visitors = f"{snapshot.visitors:,}"
    timezone_label = _timezone_label(snapshot)

    group_attributes = (
        'aria-label="Umami traffic snapshot" '
        f'data-generated-at="{escape(generated_iso, quote=True)}" '
        f'data-pageviews="{snapshot.pageviews}" '
        f'data-visitors="{snapshot.visitors}" '
        f'data-hourly="{hourly_csv}"'
    )

    if layout.name == "desktop":
        updated = (
            f"{snapshot.generated_at:%Y-%m-%d %H:%M} {timezone_label}"
        )
        return "\n".join(
            (
                f"  <g {group_attributes}>",
                '    <text x="790" y="98" class="signal-heading cyan">SYSTEM SIGNAL</text>',
                '    <text x="790" y="119" class="signal-copy muted">24H TRAFFIC / HOURLY PAGEVIEWS</text>',
                '    <path d="M790 220H1160" class="border" stroke-width="1" opacity=".55"/>',
                f'    <path data-umami-signal="desktop" d="{path}"',
                f'          fill="none" stroke="{stroke}" stroke-width="1.7"/>',
                '    <g class="signal-motion" aria-hidden="true">',
                '      <circle r="3.5" class="cyan">',
                f'        <animateMotion dur="10s" repeatCount="indefinite" path="{path}"/>',
                "      </circle>",
                "    </g>",
                f'    <text x="790" y="250" class="signal-telemetry primary">VIEWS      {views}</text>',
                f'    <text x="790" y="271" class="signal-telemetry primary">VISITORS   {visitors}</text>',
                f'    <text x="790" y="292" class="signal-telemetry muted">UPDATED    {updated}</text>',
                "  </g>",
            )
        )

    updated = f"{snapshot.generated_at:%m-%d %H:%M} {timezone_label}"
    return "\n".join(
        (
            f"  <g {group_attributes}>",
            '    <text x="42" y="414" class="signal-heading cyan">SYSTEM SIGNAL / 24H HOURLY PAGEVIEWS</text>',
            '    <path d="M42 450H678" class="border" stroke-width="1" opacity=".55"/>',
            f'    <path data-umami-signal="mobile" d="{path}"',
            f'          fill="none" stroke="{stroke}" stroke-width="1.8"/>',
            '    <g class="signal-motion" aria-hidden="true">',
            '      <circle r="3.5" class="cyan">',
            f'        <animateMotion dur="10s" repeatCount="indefinite" path="{path}"/>',
            "      </circle>",
            "    </g>",
            f'    <text x="42" y="477" class="signal-telemetry primary">VIEWS {views}</text>',
            f'    <text x="288" y="477" class="signal-telemetry primary">VISITORS {visitors}</text>',
            f'    <text x="42" y="501" class="signal-telemetry muted">UPDATED {updated}</text>',
            "  </g>",
        )
    )


def replace_generated_section(svg: str, generated_section: str) -> str:
    """Replace only the explicitly delimited generated block."""

    if svg.count(START_MARKER) != 1 or svg.count(END_MARKER) != 1:
        raise SignalSyncError("Header SVG is missing unique Umami signal markers.")
    start = svg.index(START_MARKER) + len(START_MARKER)
    end = svg.index(END_MARKER, start)
    if end <= start:
        raise SignalSyncError("Header SVG has malformed Umami signal markers.")
    return f"{svg[:start]}\n{generated_section}\n{svg[end:]}"


def _load_json_file(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SignalSyncError(f"{label} fixture is not valid JSON.") from exc


def _request_json(
    url: str,
    api_key: str,
    endpoint_label: str,
    timeout: float,
) -> Any:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "x-umami-api-key": api_key,
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read()
    except HTTPError as exc:
        raise SignalSyncError(
            f"Umami {endpoint_label} request failed with HTTP {exc.code}."
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise SignalSyncError(
            f"Umami {endpoint_label} request failed before a response was received."
        ) from exc

    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SignalSyncError(
            f"Umami {endpoint_label} response is not valid JSON."
        ) from exc


def fetch_umami_payloads(
    *,
    api_key: str,
    website_id: str,
    region: str,
    timezone_name: str,
    generated_at: datetime,
    timeout: float,
) -> tuple[Any, Any]:
    """Fetch both complete aggregate responses before rendering anything."""

    if not REGION_PATTERN.fullmatch(region):
        raise SignalSyncError("UMAMI_REGION has an invalid format.")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SignalSyncError(
            f"UMAMI_TIMEZONE is not a known IANA timezone: {timezone_name!r}."
        ) from exc

    end_at = int(generated_at.timestamp() * 1000)
    start_at = int((generated_at - timedelta(hours=WINDOW_HOURS)).timestamp() * 1000)
    encoded_website_id = quote(website_id, safe="")
    base = f"https://api.umami.is/v1/{region}/websites/{encoded_website_id}"
    pageviews_query = urlencode(
        {
            "startAt": start_at,
            "endAt": end_at,
            "unit": "hour",
            "timezone": timezone_name,
        }
    )
    stats_query = urlencode({"startAt": start_at, "endAt": end_at})
    pageviews = _request_json(
        f"{base}/pageviews?{pageviews_query}",
        api_key,
        "pageviews",
        timeout,
    )
    stats = _request_json(
        f"{base}/stats?{stats_query}",
        api_key,
        "stats",
        timeout,
    )
    return pageviews, stats


def _fixture_generated_at(pageviews_payload: Any, timezone_name: str) -> datetime:
    """Derive a stable fixture clock from its latest hourly bucket."""

    try:
        timezone_info = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SignalSyncError(
            f"UMAMI_TIMEZONE is not a known IANA timezone: {timezone_name!r}."
        ) from exc
    rows = validate_pageviews_response(pageviews_payload)
    if not rows:
        raise SignalSyncError(
            "Fixture mode needs --generated-at when pageviews is empty."
        )
    latest = max(
        _parse_timestamp(timestamp, timezone_info) for timestamp, _ in rows
    )
    return latest.replace(minute=17, second=0, microsecond=0)


def _parse_generated_at(value: str, timezone_name: str) -> datetime:
    try:
        timezone_info = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SignalSyncError(
            f"UMAMI_TIMEZONE is not a known IANA timezone: {timezone_name!r}."
        ) from exc
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SignalSyncError("--generated-at must be an ISO 8601 timestamp.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone_info)
    return parsed.astimezone(timezone_info).replace(second=0, microsecond=0)


def _current_generated_at(timezone_name: str) -> datetime:
    try:
        timezone_info = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise SignalSyncError(
            f"UMAMI_TIMEZONE is not a known IANA timezone: {timezone_name!r}."
        ) from exc
    return datetime.now(timezone.utc).astimezone(timezone_info).replace(
        second=0, microsecond=0
    )


def _snapshot_json(snapshot: Snapshot) -> str:
    payload = {
        "generatedAt": snapshot.generated_at.isoformat(timespec="minutes"),
        "timezone": snapshot.timezone_name,
        "windowHours": WINDOW_HOURS,
        "pageviews": snapshot.pageviews,
        "visitors": snapshot.visitors,
        "hourlyPageviews": list(snapshot.hourly_pageviews),
    }
    return f"{json.dumps(payload, indent=2, ensure_ascii=False)}\n"


def _validate_rendered_headers(rendered: dict[Path, str]) -> None:
    for relative, raw in rendered.items():
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as exc:
            raise SignalSyncError(
                f"Generated {relative.as_posix()} is not valid XML."
            ) from exc
        paths = [
            element
            for element in root.iter()
            if element.get("data-umami-signal") is not None
        ]
        if len(paths) != 1:
            raise SignalSyncError(
                f"Generated {relative.as_posix()} has an invalid signal path."
            )

    for dark_name, light_name in (
        (
            Path("profile/assets/header-dark.svg"),
            Path("profile/assets/header-light.svg"),
        ),
        (
            Path("profile/assets/header-mobile-dark.svg"),
            Path("profile/assets/header-mobile-light.svg"),
        ),
    ):
        dark_root = ET.fromstring(rendered[dark_name])
        light_root = ET.fromstring(rendered[light_name])
        dark_path = next(
            element.get("d")
            for element in dark_root.iter()
            if element.get("data-umami-signal") is not None
        )
        light_path = next(
            element.get("d")
            for element in light_root.iter()
            if element.get("data-umami-signal") is not None
        )
        if dark_path != light_path:
            raise SignalSyncError(
                "Generated dark/light signal geometry does not match."
            )


def _write_if_changed(path: Path, content: str) -> bool:
    if path.is_file() and path.read_text(encoding="utf-8") == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
    ) as handle:
        handle.write(content)
        temporary_path = Path(handle.name)
    os.replace(temporary_path, path)
    return True


def render_snapshot(snapshot: Snapshot, output_root: Path) -> list[Path]:
    """Render and validate all outputs before atomically replacing any file."""

    rendered: dict[Path, str] = {}
    for relative in HEADER_PATHS:
        target = output_root / relative
        source = target if target.is_file() else REPO_ROOT / relative
        try:
            original = source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SignalSyncError(
                f"Unable to read header template {relative.as_posix()}."
            ) from exc
        layout = MOBILE_LAYOUT if "mobile" in relative.name else DESKTOP_LAYOUT
        section = _render_generated_section(
            snapshot,
            layout,
            dark="-dark" in relative.name,
        )
        rendered[relative] = replace_generated_section(original, section)

    _validate_rendered_headers(rendered)
    outputs = dict(rendered)
    outputs[SNAPSHOT_PATH] = _snapshot_json(snapshot)
    changed = [
        relative
        for relative, content in outputs.items()
        if _write_if_changed(output_root / relative, content)
    ]
    return changed


def _arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pageviews-fixture", type=Path)
    parser.add_argument("--stats-fixture", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT,
        help="Repository-shaped output directory (defaults to this checkout).",
    )
    parser.add_argument(
        "--generated-at",
        help="ISO 8601 clock override for deterministic fixture tests.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=REQUEST_TIMEOUT_SECONDS,
        help="Umami API timeout in seconds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _arguments(argv)
    fixture_mode = bool(args.pageviews_fixture or args.stats_fixture)
    if fixture_mode and not (
        args.pageviews_fixture and args.stats_fixture
    ):
        print(
            "Umami signal sync failed: fixture mode requires both fixture files.",
            file=sys.stderr,
        )
        return 1
    if args.timeout <= 0:
        print(
            "Umami signal sync failed: --timeout must be positive.",
            file=sys.stderr,
        )
        return 1

    timezone_name = os.environ.get("UMAMI_TIMEZONE") or DEFAULT_TIMEZONE
    try:
        if fixture_mode:
            pageviews_payload = _load_json_file(
                args.pageviews_fixture, "Pageviews"
            )
            stats_payload = _load_json_file(args.stats_fixture, "Stats")
            generated_at = (
                _parse_generated_at(args.generated_at, timezone_name)
                if args.generated_at
                else _fixture_generated_at(pageviews_payload, timezone_name)
            )
        else:
            api_key = os.environ.get("UMAMI_API_KEY")
            website_id = os.environ.get("UMAMI_WEBSITE_ID")
            missing = [
                name
                for name, value in (
                    ("UMAMI_API_KEY", api_key),
                    ("UMAMI_WEBSITE_ID", website_id),
                )
                if not value
            ]
            if missing:
                raise SignalSyncError(
                    "Missing required environment variable(s): "
                    + ", ".join(missing)
                    + "."
                )
            region = os.environ.get("UMAMI_REGION") or DEFAULT_REGION
            generated_at = (
                _parse_generated_at(args.generated_at, timezone_name)
                if args.generated_at
                else _current_generated_at(timezone_name)
            )
            pageviews_payload, stats_payload = fetch_umami_payloads(
                api_key=api_key,
                website_id=website_id,
                region=region,
                timezone_name=timezone_name,
                generated_at=generated_at,
                timeout=args.timeout,
            )

        snapshot = build_snapshot(
            pageviews_payload,
            stats_payload,
            generated_at,
            timezone_name,
        )
        output_root = args.output_root.resolve()
        changed = render_snapshot(snapshot, output_root)
    except SignalSyncError as exc:
        print(f"Umami signal sync failed: {exc}", file=sys.stderr)
        return 1

    changed_summary = ", ".join(path.as_posix() for path in changed) or "none"
    print(
        "Umami signal snapshot rendered: "
        f"{snapshot.pageviews} views, {snapshot.visitors} visitors, "
        f"{len(snapshot.hourly_pageviews)} hourly buckets; "
        f"changed: {changed_summary}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
