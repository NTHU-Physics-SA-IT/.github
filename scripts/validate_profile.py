#!/usr/bin/env python3
"""Validate the Organization Profile without third-party dependencies."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from collections import Counter, defaultdict
import re
import struct
import sys
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO_ROOT / "profile"
README = PROFILE_DIR / "README.md"
ASSET_DIR = PROFILE_DIR / "assets"

EXPECTED_VIEWBOXES = {
    "assets/header-light.svg": "0 0 1200 360",
    "assets/header-mobile-light.svg": "0 0 720 520",
    "assets/contact-light.svg": "0 0 1200 190",
    "assets/contact-mobile-light.svg": "0 0 720 250",
    "assets/projects/pastexam-light.svg": "0 0 1200 276",
    "assets/projects/pastexam-mobile-light.svg": "0 0 720 516",
}
EXPECTED_ASSETS = set(EXPECTED_VIEWBOXES)

EXPECTED_PNG_DIMENSIONS = {
    "assets/fallback/header-light.png": (2400, 720),
    "assets/fallback/header-mobile-light.png": (1440, 1040),
    "assets/fallback/contact-light.png": (2400, 380),
    "assets/fallback/contact-mobile-light.png": (1440, 500),
    "assets/fallback/pastexam-light.png": (2400, 552),
    "assets/fallback/pastexam-mobile-light.png": (1440, 1032),
}

EXPECTED_SVG_CACHE_VERSIONS = {
    "header": "10",
    "contact": "5",
    "pastexam": "9",
}
EXPECTED_PNG_CACHE_VERSION = "1"

RAW_PROFILE_HOST = "raw.githubusercontent.com"
RAW_PROFILE_PATH_PREFIX = "/NTHU-Physics-SA-IT/.github/main/profile/"

EXPECTED_PICTURE_ASSETS = {
    "assets/fallback/header-mobile-light.png": (
        "assets/header-mobile-light.svg",
        "assets/header-light.svg",
    ),
    "assets/fallback/pastexam-mobile-light.png": (
        "assets/projects/pastexam-mobile-light.svg",
        "assets/projects/pastexam-light.svg",
    ),
    "assets/fallback/contact-mobile-light.png": (
        "assets/contact-mobile-light.svg",
        "assets/contact-light.svg",
    ),
}

FONT_PARTS = (
    '"Courier New"',
    "Courier",
    '"Liberation Mono"',
    '"DejaVu Sans Mono"',
    "monospace",
)

FORBIDDEN_ELEMENTS = {"script", "foreignObject", "image"}
LOCAL_PATH_PATTERN = re.compile(
    r"(?:file://|(?<![A-Za-z])[A-Za-z]:[\\/]|/(?:home|Users)/)",
    re.IGNORECASE,
)
ACTIVE_URI_PATTERN = re.compile(
    r"(?:href|src)\s*=\s*[\"']\s*(?:data|javascript):",
    re.IGNORECASE,
)
LOW_WEIGHT_PATTERN = re.compile(
    r"font-weight\s*(?::|=)\s*[\"']?([1-3]00)\b", re.IGNORECASE
)
URL_FUNCTION_PATTERN = re.compile(r"url\(([^)]+)\)", re.IGNORECASE)
REDUCED_MOTION_PATTERN = re.compile(
    r"prefers-reduced-motion",
    re.IGNORECASE,
)
CSS_ANIMATION_PATTERN = re.compile(
    r"(?:@keyframes|\banimation(?:-name)?\s*:)",
    re.IGNORECASE,
)
ANIMATION_ELEMENTS = {"animate", "animateMotion", "animateTransform", "set"}


class ReadmeHTMLParser(HTMLParser):
    """Collect HTML asset references, links, and picture source ordering."""

    def __init__(self) -> None:
        super().__init__()
        self.asset_refs: list[str] = []
        self.links: list[str] = []
        self.pictures: list[list[tuple[str, dict[str, str]]]] = []
        self._picture: list[tuple[str, dict[str, str]]] | None = None

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attr_map = {key: value or "" for key, value in attrs}

        if tag == "picture":
            self._picture = []
        elif tag in {"source", "img"}:
            value = attr_map.get("srcset") or attr_map.get("src")
            if value:
                self.asset_refs.extend(_srcset_urls(value))
            if self._picture is not None:
                self._picture.append((tag, attr_map))
        elif tag == "a" and attr_map.get("href"):
            self.links.append(attr_map["href"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "picture" and self._picture is not None:
            self.pictures.append(self._picture)
            self._picture = None


def _srcset_urls(value: str) -> list[str]:
    return [candidate.strip().split()[0] for candidate in value.split(",")]


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _raw_profile_asset_path(reference: str) -> str | None:
    parsed = urlsplit(reference)
    if (
        parsed.scheme != "https"
        or parsed.netloc != RAW_PROFILE_HOST
        or not parsed.path.startswith(RAW_PROFILE_PATH_PREFIX)
        or parsed.fragment
    ):
        return None
    return unquote(parsed.path[len(RAW_PROFILE_PATH_PREFIX) :])


def _png_dimensions(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as stream:
        header = stream.read(24)
    if (
        len(header) != 24
        or header[:8] != b"\x89PNG\r\n\x1a\n"
        or header[12:16] != b"IHDR"
    ):
        return None
    return struct.unpack(">II", header[16:24])


def _case_exact(path: Path, base: Path) -> bool:
    """Check path spelling on case-insensitive filesystems."""

    try:
        relative = path.relative_to(base)
    except ValueError:
        return False

    cursor = base
    for part in relative.parts:
        if not cursor.is_dir():
            return False
        names = {entry.name for entry in cursor.iterdir()}
        if part not in names:
            return False
        cursor /= part
    return True


def _asset_family(reference_path: str) -> str | None:
    name = Path(reference_path).name
    if name.startswith("header"):
        return "header"
    if name.startswith("contact"):
        return "contact"
    if name.startswith("pastexam"):
        return "pastexam"
    return None


def _has_class(element: ET.Element, class_name: str) -> bool:
    return class_name in element.get("class", "").split()


def _parse_number_list(value: str) -> list[float] | None:
    try:
        return [float(part.strip()) for part in value.split(";")]
    except ValueError:
        return None


def _duration_seconds(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+(?:\.\d*)?|\.\d+)s\s*", value)
    return float(match.group(1)) if match else None


def _svg_length(
    value: str | None,
    axis_size: float,
    *,
    default: float | None = None,
    percentage_origin: float = 0,
) -> float | None:
    """Parse the numeric and percentage lengths used by profile rects."""

    if value is None or not value.strip():
        return default

    normalized = value.strip()
    percentage = normalized.endswith("%")
    if percentage:
        normalized = normalized[:-1].strip()
    elif normalized.lower().endswith("px"):
        normalized = normalized[:-2].strip()

    try:
        number = float(normalized)
    except ValueError:
        return None

    if percentage:
        return percentage_origin + (axis_size * number / 100)
    return number


def validate_transparent_outer_canvas(
    root: ET.Element,
    relative: str,
    errors: list[str],
) -> None:
    """Reject render-tree rects whose geometry covers the complete viewBox."""

    viewbox = root.get("viewBox", "").replace(",", " ").split()
    try:
        min_x, min_y, width, height = (float(part) for part in viewbox)
    except (TypeError, ValueError):
        return
    if width <= 0 or height <= 0:
        return

    parent_map = {
        child: parent for parent in root.iter() for child in parent
    }
    non_rendering = {
        "clipPath",
        "defs",
        "marker",
        "mask",
        "pattern",
        "symbol",
    }
    tolerance = 1e-6
    for rect in (
        element
        for element in root.iter()
        if _local_name(element.tag) == "rect"
    ):
        ancestor = parent_map.get(rect)
        inside_non_rendering = False
        while ancestor is not None:
            if _local_name(ancestor.tag) in non_rendering:
                inside_non_rendering = True
                break
            ancestor = parent_map.get(ancestor)
        if inside_non_rendering:
            continue

        x = _svg_length(
            rect.get("x"),
            width,
            default=0,
            percentage_origin=min_x,
        )
        y = _svg_length(
            rect.get("y"),
            height,
            default=0,
            percentage_origin=min_y,
        )
        rect_width = _svg_length(rect.get("width"), width)
        rect_height = _svg_length(rect.get("height"), height)
        if None in {x, y, rect_width, rect_height}:
            continue

        covers_viewbox = (
            x <= min_x + tolerance
            and y <= min_y + tolerance
            and x + rect_width >= min_x + width - tolerance
            and y + rect_height >= min_y + height - tolerance
        )
        if covers_viewbox:
            errors.append(
                f"{relative}: render-tree rect covers the complete viewBox; "
                "remove it so the Header/Contact canvas remains transparent"
            )


def validate_readme(errors: list[str]) -> set[Path]:
    if not README.is_file():
        errors.append("Missing profile/README.md")
        return set()

    parser = ReadmeHTMLParser()
    parser.feed(README.read_text(encoding="utf-8"))
    referenced: set[Path] = set()
    family_versions: dict[tuple[str, str], set[str]] = defaultdict(set)

    for reference in parser.asset_refs:
        parsed = urlsplit(reference)
        local_ref = _raw_profile_asset_path(reference)
        if local_ref is None:
            errors.append(
                "README asset must use the canonical raw profile URL: "
                f"{reference}"
            )
            continue

        candidate = (README.parent / local_ref).resolve()

        try:
            candidate.relative_to(README.parent.resolve())
        except ValueError:
            errors.append(f"README asset escapes profile directory: {reference}")
            continue

        if ".." in Path(local_ref).parts:
            errors.append(f"README asset contains parent traversal: {reference}")
        if not candidate.is_file():
            errors.append(f"README asset does not exist: {reference}")
        elif not _case_exact(candidate, README.parent.resolve()):
            errors.append(f"README asset casing is not exact: {reference}")
        referenced.add(candidate)

        family = _asset_family(local_ref)
        if family is not None:
            suffix = Path(local_ref).suffix.lower()
            if suffix == ".svg":
                expected_version = EXPECTED_SVG_CACHE_VERSIONS[family]
            elif suffix == ".png":
                expected_version = EXPECTED_PNG_CACHE_VERSION
            else:
                errors.append(
                    f"README asset has an unsupported format: {reference}"
                )
                continue

            query = parse_qs(parsed.query, keep_blank_values=True)
            versions = query.get("v", [])
            if set(query) != {"v"} or versions != [expected_version]:
                errors.append(
                    "README cache version must be "
                    f"v={expected_version} for {family} {suffix}: {reference}"
                )
            family_versions[(family, suffix)].update(versions)

    for family, expected_version in EXPECTED_SVG_CACHE_VERSIONS.items():
        actual_versions = family_versions.get((family, ".svg"), set())
        if actual_versions != {expected_version}:
            errors.append(
                f"README {family} SVG cache versions are not coherent: "
                f"expected {expected_version}, found "
                f"{sorted(actual_versions) or 'none'}"
            )
        actual_png_versions = family_versions.get((family, ".png"), set())
        if actual_png_versions != {EXPECTED_PNG_CACHE_VERSION}:
            errors.append(
                f"README {family} PNG cache versions are not coherent: "
                f"expected {EXPECTED_PNG_CACHE_VERSION}, found "
                f"{sorted(actual_png_versions) or 'none'}"
            )

    if len(parser.pictures) != len(EXPECTED_PICTURE_ASSETS):
        errors.append(
            "README must contain exactly "
            f"{len(EXPECTED_PICTURE_ASSETS)} picture elements"
        )

    required_media = ("(max-width: 768px)", "")

    for index, picture in enumerate(parser.pictures, start=1):
        tags = [tag for tag, _attrs in picture]
        sources = [attrs for tag, attrs in picture if tag == "source"]
        images = [attrs for tag, attrs in picture if tag == "img"]
        if tags != ["source", "source", "img"]:
            errors.append(
                f"README picture {index} must contain source, source, img "
                f"in that order; found {tags}"
            )
        if len(images) != 1:
            errors.append(
                f"README picture {index} must have exactly one img fallback"
            )
        if not images:
            continue

        fallback = images[-1]
        if fallback.get("width") != "100%":
            errors.append(f"README picture {index} fallback width is not 100%")
        if not fallback.get("alt", "").strip():
            errors.append(f"README picture {index} fallback alt is empty")

        fallback_path = _raw_profile_asset_path(fallback.get("src", ""))
        if fallback_path not in EXPECTED_PICTURE_ASSETS:
            errors.append(
                f"README picture {index} fallback must use an expected "
                "mobile light PNG"
            )
            fallback_path = None
        if fallback.get("srcset") or fallback.get("sizes"):
            errors.append(
                f"README picture {index} fallback must not depend on "
                "sanitized img srcset/sizes attributes"
            )

        # Each family has mobile/desktop SVG sources and a basic mobile PNG.
        if len(sources) != 2:
            errors.append(
                f"README picture {index} has unexpected source count: {len(sources)}"
            )
            continue

        actual_media = tuple(source.get("media", "") for source in sources)
        if actual_media != required_media:
            errors.append(
                f"README picture {index} source order/media is incorrect"
            )

        for source in sources:
            if source.get("type") != "image/svg+xml":
                errors.append(
                    f"README picture {index} source must declare "
                    'type="image/svg+xml"'
                )

        if fallback_path is not None:
            expected_sources = EXPECTED_PICTURE_ASSETS[fallback_path]
            actual_sources = tuple(
                _raw_profile_asset_path(source.get("srcset", ""))
                for source in sources
            )
            if actual_sources != expected_sources:
                errors.append(
                    f"README picture {index} responsive light SVG mapping is "
                    f"incorrect: expected {expected_sources}, "
                    f"found {actual_sources}"
                )

    for link in parser.links:
        parsed = urlsplit(link)
        if parsed.scheme not in {"https"} or not parsed.netloc:
            errors.append(f"README link must be an absolute HTTPS URL: {link}")

    return referenced


def validate_header_animation(
    root: ET.Element, raw: str, relative: str, errors: list[str]
) -> None:
    parent_map = {
        child: parent for parent in root.iter() for child in parent
    }
    animations = [
        element
        for element in root.iter()
        if _local_name(element.tag) in ANIMATION_ELEMENTS
    ]
    width_animations = [
        element
        for element in animations
        if _local_name(element.tag) == "animate"
        and element.get("attributeName") == "width"
    ]

    if len(width_animations) != 6:
        errors.append(
            f"{relative}: expected six width animations, "
            f"found {len(width_animations)}"
        )

    tolerance = 1e-9
    typing_clip_paths: set[str] = set()
    for index, animation in enumerate(width_animations, start=1):
        label = f"{relative}: width animation {index}"
        if animation.get("calcMode") != "discrete":
            errors.append(f"{label} must use calcMode=discrete")
        duration = _duration_seconds(animation.get("dur", ""))
        if duration is None or not 0.7 <= duration <= 1.5:
            errors.append(f"{label} duration must be between 0.7s and 1.5s")
        if animation.get("repeatCount") != "1":
            errors.append(f"{label} must play exactly once")
        if animation.get("fill") != "freeze":
            errors.append(f"{label} must freeze at full width")

        values = _parse_number_list(animation.get("values", ""))
        key_times = _parse_number_list(animation.get("keyTimes", ""))
        if values is None or key_times is None:
            errors.append(f"{label} has non-numeric values/keyTimes")
            continue
        if len(values) != len(key_times) or len(values) < 3:
            errors.append(
                f"{label} values/keyTimes counts must match and contain steps"
            )
            continue
        if (
            abs(key_times[0]) > tolerance
            or abs(key_times[-1] - 1.0) > tolerance
            or any(
                key_times[position] > key_times[position + 1]
                for position in range(len(key_times) - 1)
            )
        ):
            errors.append(
                f"{label} keyTimes must be monotonic from 0 through 1"
            )
        if (
            abs(values[0]) > tolerance
            or any(value < 0 for value in values)
        ):
            errors.append(
                f"{label} widths must start at zero and stay non-negative"
            )

        maximum = max(values)
        if maximum <= 0:
            errors.append(f"{label} never reveals any text")
            continue
        if abs(values[-1] - maximum) > tolerance:
            errors.append(f"{label} must finish at full width")
        if any(
            values[position] > values[position + 1]
            for position in range(len(values) - 1)
        ):
            errors.append(
                f"{label} widths must increase monotonically"
            )

        begin = _duration_seconds(animation.get("begin", ""))
        if begin is None or duration is None:
            errors.append(f"{label} must use a numeric begin time")
        elif begin + duration > 6.6 + tolerance:
            errors.append(f"{label} must complete by 6.6s")

        animated_rect = parent_map.get(animation)
        if (
            animated_rect is None
            or _local_name(animated_rect.tag) != "rect"
            or _parse_number_list(animated_rect.get("width", ""))
            != [0.0]
        ):
            errors.append(
                f"{label} must animate a clip rect with static width=0"
            )
        ancestor = animated_rect
        inside_clip_path = False
        while ancestor is not None:
            if _local_name(ancestor.tag) == "clipPath":
                inside_clip_path = True
                typing_clip_paths.add(ancestor.get("id", ""))
                break
            ancestor = parent_map.get(ancestor)
        if not inside_clip_path:
            errors.append(f"{label} must be contained by a clipPath")

    if len(typing_clip_paths) != 1:
        errors.append(
            f"{relative}: typing must use one shared clipPath, "
            f"found {len(typing_clip_paths)}"
        )

    cursor_animations = [
        animation
        for animation in animations
        if _local_name(animation.tag) == "animate"
        and animation.get("attributeName") == "opacity"
        and _duration_seconds(animation.get("dur", "")) == 1.2
    ]
    if len(cursor_animations) != 1:
        errors.append(
            f"{relative}: expected one 1.2s cursor animation, "
            f"found {len(cursor_animations)}"
        )
    else:
        cursor_animation = cursor_animations[0]
        if cursor_animation.get("repeatCount") != "indefinite":
            errors.append(f"{relative}: cursor must repeat indefinitely")
        cursor_begin = _duration_seconds(cursor_animation.get("begin", ""))
        if cursor_begin is None or cursor_begin < 6.5:
            errors.append(
                f"{relative}: cursor must begin after typing completes"
            )
        cursor_values = _parse_number_list(
            cursor_animation.get("values", "")
        )
        if (
            cursor_values is None
            or not cursor_values
            or cursor_values[0] != 1
            or cursor_values[-1] != 1
            or 0 not in cursor_values
        ):
            errors.append(
                f"{relative}: cursor opacity must blink from 1 to 0 to 1"
            )

        ancestor = parent_map.get(cursor_animation)
        inside_motion_layer = False
        hidden_without_smil = False
        while ancestor is not None:
            inside_motion_layer |= _has_class(ancestor, "motion-layer")
            try:
                hidden_without_smil |= float(
                    ancestor.get("opacity", "1")
                ) == 0
            except ValueError:
                pass
            ancestor = parent_map.get(ancestor)
        if not inside_motion_layer:
            errors.append(
                f"{relative}: cursor must be inside the motion layer"
            )
        if not hidden_without_smil:
            errors.append(
                f"{relative}: cursor wrapper must default to opacity=0"
            )

    static_fallbacks = [
        element
        for element in root.iter()
        if _has_class(element, "static-fallback")
    ]
    if len(static_fallbacks) != 1:
        errors.append(
            f"{relative}: expected one static fallback, "
            f"found {len(static_fallbacks)}"
        )
    else:
        fallback = static_fallbacks[0]
        if any(
            _has_class(ancestor, "motion-layer")
            and fallback in set(ancestor.iter())
            for ancestor in root.iter()
        ):
            errors.append(
                f"{relative}: static fallback must be outside motion layers"
            )
        fallback_text = [
            element
            for element in fallback.iter()
            if _local_name(element.tag) == "text"
        ]
        if len(fallback_text) < 6:
            errors.append(
                f"{relative}: static fallback must contain all six lines"
            )
        if (
            fallback.get("display") == "none"
            or fallback.get("visibility") == "hidden"
            or fallback.get("opacity") == "0"
        ):
            errors.append(
                f"{relative}: static fallback must be visible by default"
            )

    if re.search(
        r"\.static-fallback\s*\{[^}]*(?:display\s*:\s*none|"
        r"visibility\s*:\s*hidden|opacity\s*:\s*0(?:\D|$))",
        raw,
        re.IGNORECASE | re.DOTALL,
    ):
        errors.append(
            f"{relative}: CSS must not hide the static fallback"
        )

    if REDUCED_MOTION_PATTERN.search(raw):
        errors.append(
            f"{relative}: must not change animation visibility based on "
            "prefers-reduced-motion"
        )
    for class_name in ("motion-layer", "signal-motion"):
        if re.search(
            rf"\.{class_name}\s*\{{[^}}]*(?:display\s*:\s*none|"
            rf"visibility\s*:\s*hidden|opacity\s*:\s*0(?:\D|$))",
            raw,
            re.IGNORECASE | re.DOTALL,
        ):
            errors.append(
                f"{relative}: CSS must not hide {class_name}"
            )

    motion_layers = [
        element
        for element in root.iter()
        if _has_class(element, "motion-layer")
    ]
    cover_candidates: list[ET.Element] = []
    for motion_layer in motion_layers:
        for element in motion_layer.iter():
            if (
                _local_name(element.tag) == "rect"
                and element.get("opacity") == "0"
                and any(
                    _local_name(child.tag) == "set"
                    and child.get("attributeName") == "opacity"
                    and child.get("to") == "1"
                    and child.get("fill") == "freeze"
                    for child in element
                )
            ):
                cover_candidates.append(element)
    if not cover_candidates:
        errors.append(
            f"{relative}: motion cover must default to opacity=0"
        )

    signal_animations = [
        animation
        for animation in animations
        if _local_name(animation.tag) == "animateMotion"
    ]
    if len(signal_animations) != 1:
        errors.append(
            f"{relative}: expected one signal animateMotion, "
            f"found {len(signal_animations)}"
        )
    else:
        signal_animation = signal_animations[0]
        signal_duration = _duration_seconds(signal_animation.get("dur", ""))
        if signal_duration is None or not 9 <= signal_duration <= 12:
            errors.append(
                f"{relative}: signal animation must last 9-12 seconds"
            )
        if signal_animation.get("repeatCount") != "indefinite":
            errors.append(
                f"{relative}: signal animation must repeat indefinitely"
            )
        ancestor = parent_map.get(signal_animation)
        inside_signal_motion = False
        while ancestor is not None:
            inside_signal_motion |= _has_class(ancestor, "signal-motion")
            ancestor = parent_map.get(ancestor)
        if not inside_signal_motion:
            errors.append(
                f"{relative}: signal animation must be in signal-motion"
            )

    if raw.count("<!-- UMAMI GENERATED SIGNAL START -->") != 1:
        errors.append(
            f"{relative}: missing unique Umami signal start marker"
        )
    if raw.count("<!-- UMAMI GENERATED SIGNAL END -->") != 1:
        errors.append(
            f"{relative}: missing unique Umami signal end marker"
        )

    signal_paths = [
        element
        for element in root.iter()
        if element.get("data-umami-signal") is not None
    ]
    if len(signal_paths) != 1:
        errors.append(
            f"{relative}: expected one generated Umami signal path, "
            f"found {len(signal_paths)}"
        )
    else:
        signal_path = signal_paths[0]
        expected_layout = (
            "mobile" if "header-mobile-" in relative else "desktop"
        )
        if signal_path.get("data-umami-signal") != expected_layout:
            errors.append(
                f"{relative}: generated signal layout marker is incorrect"
            )
        path_data = signal_path.get("d", "")
        point_matches = re.findall(
            r"[ML](-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)",
            path_data,
        )
        if len(point_matches) != 24:
            errors.append(
                f"{relative}: generated signal must contain 24 points"
            )
        else:
            coordinates = [
                (float(x), float(y)) for x, y in point_matches
            ]
            if any(
                coordinates[index][0] >= coordinates[index + 1][0]
                for index in range(len(coordinates) - 1)
            ):
                errors.append(
                    f"{relative}: generated signal x coordinates must increase"
                )
            y_min, y_max = (
                (420, 450) if expected_layout == "mobile" else (150, 220)
            )
            if any(not y_min <= y <= y_max for _, y in coordinates):
                errors.append(
                    f"{relative}: generated signal exceeds plot padding"
                )

        if len(signal_animations) == 1 and (
            signal_animations[0].get("path") != path_data
        ):
            errors.append(
                f"{relative}: scanner dot must follow the data path exactly"
            )

        group = parent_map.get(signal_path)
        if (
            group is None
            or group.get("aria-label") != "Umami traffic snapshot"
        ):
            errors.append(
                f"{relative}: generated signal needs an aggregate snapshot label"
            )
        elif group.get("data-generated-at") != "pending":
            hourly = group.get("data-hourly", "").split(",")
            if len(hourly) != 24 or any(
                not value.isdigit() for value in hourly
            ):
                errors.append(
                    f"{relative}: generated snapshot metadata is invalid"
                )
            for attribute in ("data-pageviews", "data-visitors"):
                if not group.get(attribute, "").isdigit():
                    errors.append(
                        f"{relative}: {attribute} must be an integer"
                    )

        telemetry = " ".join(
            "".join(element.itertext())
            for element in root.iter()
            if _local_name(element.tag) == "text"
        )
        for label in ("SYSTEM SIGNAL", "VIEWS", "VISITORS", "UPDATED"):
            if label not in telemetry:
                errors.append(
                    f"{relative}: generated telemetry is missing {label}"
                )
        if expected_layout == "desktop" and (
            "24H TRAFFIC / HOURLY PAGEVIEWS" not in telemetry
        ):
            errors.append(
                f"{relative}: desktop signal is missing its data definition"
            )
        if re.search(r"\b(?:LIVE|REALTIME)\b", telemetry):
            errors.append(
                f"{relative}: snapshot telemetry must not claim realtime data"
            )


def validate_svg(path: Path, errors: list[str]) -> None:
    relative = path.relative_to(REPO_ROOT).as_posix()
    asset_name = path.relative_to(PROFILE_DIR).as_posix()
    raw = path.read_text(encoding="utf-8")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append(f"{relative}: invalid XML ({exc})")
        return

    if _local_name(root.tag) != "svg":
        errors.append(f"{relative}: root element is not svg")
    expected_viewbox = EXPECTED_VIEWBOXES.get(asset_name)
    if expected_viewbox is None:
        errors.append(f"{relative}: unexpected SVG asset")
    elif root.get("viewBox") != expected_viewbox:
        errors.append(
            f"{relative}: viewBox must be {expected_viewbox}, "
            f"found {root.get('viewBox') or 'missing'}"
        )
    if root.get("role") != "img":
        errors.append(f"{relative}: role must be img")

    direct_children = {
        _local_name(child.tag): child
        for child in root
        if _local_name(child.tag) in {"title", "desc"}
    }
    if "title" not in direct_children:
        errors.append(f"{relative}: missing title")
    if "desc" not in direct_children:
        errors.append(f"{relative}: missing desc")

    id_counts = Counter(
        element.get("id")
        for element in root.iter()
        if element.get("id") is not None
    )
    labelledby_tokens = root.get("aria-labelledby", "").split()
    if not labelledby_tokens:
        errors.append(f"{relative}: missing aria-labelledby")
    elif len(labelledby_tokens) != len(set(labelledby_tokens)):
        errors.append(f"{relative}: aria-labelledby contains duplicate tokens")
    for token in labelledby_tokens:
        if id_counts[token] != 1:
            errors.append(
                f"{relative}: aria-labelledby token {token!r} must resolve "
                f"exactly once, found {id_counts[token]}"
            )
    for element_name in ("title", "desc"):
        element = direct_children.get(element_name)
        if (
            element is not None
            and element.get("id") not in labelledby_tokens
        ):
            errors.append(
                f"{relative}: direct {element_name} id must be referenced "
                "by aria-labelledby"
            )

    for font_part in FONT_PARTS:
        if font_part not in raw:
            errors.append(f"{relative}: font stack is missing {font_part}")

    for match in LOW_WEIGHT_PATTERN.finditer(raw):
        errors.append(
            f"{relative}: font-weight below 400 ({match.group(1)})"
        )

    if LOCAL_PATH_PATTERN.search(raw):
        errors.append(f"{relative}: contains a local filesystem path")
    if ACTIVE_URI_PATTERN.search(raw):
        errors.append(f"{relative}: contains a data/javascript URI")
    if re.search(r"\bbase64\b", raw, re.IGNORECASE):
        errors.append(f"{relative}: contains base64 content")

    for match in URL_FUNCTION_PATTERN.finditer(raw):
        target = match.group(1).strip().strip("\"'")
        if not target.startswith("#"):
            errors.append(f"{relative}: external CSS url() target: {target}")

    for element in root.iter():
        element_name = _local_name(element.tag)
        if element_name in FORBIDDEN_ELEMENTS:
            errors.append(f"{relative}: forbidden element <{element_name}>")

        for attribute, value in element.attrib.items():
            attribute_name = _local_name(attribute)
            if attribute_name.lower().startswith("on"):
                errors.append(
                    f"{relative}: forbidden event attribute {attribute_name}"
                )
            if attribute_name == "href" and not value.startswith("#"):
                errors.append(f"{relative}: external href target {value}")

    if path.name.startswith(("header", "contact")):
        validate_transparent_outer_canvas(root, relative, errors)

    if path.name.startswith("header"):
        validate_header_animation(root, raw, relative, errors)
    elif path.name.startswith("contact"):
        animation_elements = [
            element
            for element in root.iter()
            if _local_name(element.tag) in ANIMATION_ELEMENTS
        ]
        if animation_elements or CSS_ANIMATION_PATTERN.search(raw):
            errors.append(f"{relative}: contact assets must remain static")


def validate_png_fallbacks(errors: list[str]) -> None:
    fallback_dir = ASSET_DIR / "fallback"
    actual = {
        path.relative_to(PROFILE_DIR).as_posix()
        for path in fallback_dir.glob("*.png")
        if path.is_file()
    }
    expected = set(EXPECTED_PNG_DIMENSIONS)

    for relative in sorted(expected - actual):
        errors.append(f"Missing required PNG fallback: {relative}")
    for relative in sorted(actual - expected):
        errors.append(f"Unexpected PNG fallback: {relative}")

    for relative, expected_dimensions in EXPECTED_PNG_DIMENSIONS.items():
        path = PROFILE_DIR / relative
        if not path.is_file():
            continue

        dimensions = _png_dimensions(path)
        if dimensions is None:
            errors.append(f"{relative}: invalid PNG signature or IHDR")
        elif dimensions != expected_dimensions:
            errors.append(
                f"{relative}: expected {expected_dimensions[0]}x"
                f"{expected_dimensions[1]}, found "
                f"{dimensions[0]}x{dimensions[1]}"
            )

        if path.stat().st_size >= 1_000_000:
            errors.append(f"{relative}: PNG fallback must remain below 1 MB")


def validate_repository_files(errors: list[str]) -> None:
    forbidden_suffixes = {".woff", ".woff2", ".ttf", ".otf", ".eot"}

    for path in REPO_ROOT.rglob("*"):
        if ".git" in path.parts or not path.is_file():
            continue
        if path.name == ".env" or path.name.startswith(".env."):
            errors.append(
                f"Forbidden environment file: {path.relative_to(REPO_ROOT)}"
            )
        if path.suffix.lower() in forbidden_suffixes:
            errors.append(f"Forbidden font file: {path.relative_to(REPO_ROOT)}")


def main() -> int:
    errors: list[str] = []

    referenced = validate_readme(errors)
    svg_paths = sorted(ASSET_DIR.rglob("*.svg"))
    actual_assets = {
        path.relative_to(PROFILE_DIR).as_posix() for path in svg_paths
    }

    missing_assets = EXPECTED_ASSETS - actual_assets
    if missing_assets:
        errors.extend(
            f"Missing required SVG: {asset}" for asset in sorted(missing_assets)
        )

    for svg_path in svg_paths:
        validate_svg(svg_path, errors)

    unreferenced = {
        path.resolve() for path in svg_paths
    } - {path.resolve() for path in referenced}
    if unreferenced:
        errors.extend(
            "SVG is not referenced by README: "
            + path.relative_to(REPO_ROOT).as_posix()
            for path in sorted(unreferenced)
        )

    validate_png_fallbacks(errors)
    validate_repository_files(errors)

    if errors:
        print("Profile validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        "Profile validation passed: "
        f"{len(svg_paths)} SVG files, "
        f"{len(EXPECTED_PNG_DIMENSIONS)} PNG fallbacks, "
        f"{len(referenced)} README asset references."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
