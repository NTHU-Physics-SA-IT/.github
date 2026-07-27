#!/usr/bin/env python3
"""Validate the Organization Profile without third-party dependencies."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from collections import Counter, defaultdict
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs, unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO_ROOT / "profile"
README = PROFILE_DIR / "README.md"
ASSET_DIR = PROFILE_DIR / "assets"

EXPECTED_VIEWBOXES = {
    "assets/header-dark.svg": "0 0 1200 360",
    "assets/header-light.svg": "0 0 1200 360",
    "assets/header-mobile-dark.svg": "0 0 720 620",
    "assets/header-mobile-light.svg": "0 0 720 620",
    "assets/contact-dark.svg": "0 0 1200 190",
    "assets/contact-light.svg": "0 0 1200 190",
    "assets/contact-mobile-dark.svg": "0 0 720 360",
    "assets/contact-mobile-light.svg": "0 0 720 360",
    "assets/projects/pastexam-dark.svg": "0 0 1200 320",
    "assets/projects/pastexam-light.svg": "0 0 1200 320",
    "assets/projects/pastexam-mobile-dark.svg": "0 0 720 580",
    "assets/projects/pastexam-mobile-light.svg": "0 0 720 580",
}
EXPECTED_ASSETS = set(EXPECTED_VIEWBOXES)

EXPECTED_CACHE_VERSIONS = {
    "header": "2",
    "contact": "2",
    "pastexam": "1",
}

THEME_PAIRS = (
    ("assets/header-dark.svg", "assets/header-light.svg"),
    ("assets/header-mobile-dark.svg", "assets/header-mobile-light.svg"),
    ("assets/contact-dark.svg", "assets/contact-light.svg"),
    ("assets/contact-mobile-dark.svg", "assets/contact-mobile-light.svg"),
    ("assets/projects/pastexam-dark.svg", "assets/projects/pastexam-light.svg"),
    (
        "assets/projects/pastexam-mobile-dark.svg",
        "assets/projects/pastexam-mobile-light.svg",
    ),
)

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
    r"font-weight\s*(?::|=)\s*[\"']?([1-6]00)\b", re.IGNORECASE
)
URL_FUNCTION_PATTERN = re.compile(r"url\(([^)]+)\)", re.IGNORECASE)
HEX_COLOR_PATTERN = re.compile(r"#[0-9A-Fa-f]{6}\b")
THEME_DESC_PATTERN = re.compile(r"\b(?:dark|light)\b", re.IGNORECASE)
REDUCED_MOTION_PATTERN = re.compile(
    r"@media\s*\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)"
    r"\s*\{(?P<body>.*?)\}\s*\}",
    re.IGNORECASE | re.DOTALL,
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


def _without_query_or_fragment(reference: str) -> str:
    parsed = urlsplit(reference)
    return unquote(parsed.path)


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


def _normalized_theme_svg(path: Path) -> str | None:
    """Normalize theme presentation while preserving geometry and text."""

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None

    for element in root.iter():
        if _local_name(element.tag) == "desc" and element.text:
            element.text = THEME_DESC_PATTERN.sub("THEME", element.text)
        for attribute in tuple(element.attrib):
            if _local_name(attribute) in {
                "opacity",
                "fill-opacity",
                "stroke-opacity",
            }:
                element.set(attribute, "THEME_OPACITY")

    serialized = ET.tostring(root, encoding="unicode")
    serialized = HEX_COLOR_PATTERN.sub("#THEME_COLOR", serialized)
    return re.sub(
        r"((?:fill-|stroke-)?opacity\s*:\s*)"
        r"(?:\d+(?:\.\d*)?|\.\d+)",
        r"\1THEME_OPACITY",
        serialized,
        flags=re.IGNORECASE,
    )


def validate_readme(errors: list[str]) -> set[Path]:
    if not README.is_file():
        errors.append("Missing profile/README.md")
        return set()

    parser = ReadmeHTMLParser()
    parser.feed(README.read_text(encoding="utf-8"))
    referenced: set[Path] = set()
    family_versions: dict[str, set[str]] = defaultdict(set)

    for reference in parser.asset_refs:
        parsed = urlsplit(reference)
        if parsed.scheme or parsed.netloc:
            errors.append(f"README asset must be relative: {reference}")
            continue

        local_ref = _without_query_or_fragment(reference)
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
            query = parse_qs(parsed.query, keep_blank_values=True)
            versions = query.get("v", [])
            expected_version = EXPECTED_CACHE_VERSIONS[family]
            if set(query) != {"v"} or versions != [expected_version]:
                errors.append(
                    "README cache version must be "
                    f"v={expected_version} for {family}: {reference}"
                )
            family_versions[family].update(versions)

    for family, expected_version in EXPECTED_CACHE_VERSIONS.items():
        actual_versions = family_versions.get(family, set())
        if actual_versions != {expected_version}:
            errors.append(
                f"README {family} cache versions are not coherent: "
                f"expected {expected_version}, found "
                f"{sorted(actual_versions) or 'none'}"
            )

    required_media = (
        "(prefers-color-scheme: dark) and (max-width: 768px)",
        "(prefers-color-scheme: light) and (max-width: 768px)",
        "(prefers-color-scheme: dark)",
        "(prefers-color-scheme: light)",
    )

    for index, picture in enumerate(parser.pictures, start=1):
        sources = [attrs for tag, attrs in picture if tag == "source"]
        images = [attrs for tag, attrs in picture if tag == "img"]
        if not images:
            errors.append(f"README picture {index} has no img fallback")
            continue

        fallback = images[-1]
        if fallback.get("width") != "100%":
            errors.append(f"README picture {index} fallback width is not 100%")
        if not fallback.get("alt", "").strip():
            errors.append(f"README picture {index} fallback alt is empty")

        # Every current asset family has mobile and desktop dark/light sources.
        if len(sources) != 4:
            errors.append(
                f"README picture {index} has unexpected source count: {len(sources)}"
            )
            continue

        actual_media = tuple(source.get("media", "") for source in sources)
        if actual_media != required_media:
            errors.append(
                f"README picture {index} source order/media is incorrect"
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
    for index, animation in enumerate(width_animations, start=1):
        label = f"{relative}: width animation {index}"
        if animation.get("calcMode") != "discrete":
            errors.append(f"{label} must use calcMode=discrete")
        if _duration_seconds(animation.get("dur", "")) != 16.0:
            errors.append(f"{label} must use dur=16s")
        if animation.get("repeatCount") != "indefinite":
            errors.append(f"{label} must repeat indefinitely")

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
            or abs(values[-1]) > tolerance
            or any(value < 0 for value in values)
        ):
            errors.append(
                f"{label} widths must start/end at zero and stay non-negative"
            )

        maximum = max(values)
        if maximum <= 0:
            errors.append(f"{label} never reveals any text")
            continue
        if any(
            values[position] > values[position + 1]
            for position in range(len(values) - 2)
        ):
            errors.append(
                f"{label} widths must increase monotonically before reset"
            )

        completion_index = values.index(maximum)
        if key_times[completion_index] > 0.351 + tolerance:
            errors.append(
                f"{label} completes after the 0.351 cycle boundary"
            )
        reset_indices = [
            position
            for position in range(completion_index + 1, len(values))
            if values[position] < maximum
        ]
        if not reset_indices:
            errors.append(f"{label} has no reset step")
        elif key_times[reset_indices[0]] < 0.968 - tolerance:
            errors.append(
                f"{label} resets before the 0.968 cycle boundary"
            )

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
                break
            ancestor = parent_map.get(ancestor)
        if not inside_clip_path:
            errors.append(f"{label} must be contained by a clipPath")

    cursor_animations = [
        animation
        for animation in animations
        if _local_name(animation.tag) == "animate"
        and animation.get("attributeName") == "opacity"
        and _duration_seconds(animation.get("dur", "")) == 0.8
    ]
    if len(cursor_animations) != 1:
        errors.append(
            f"{relative}: expected one 0.8s cursor animation, "
            f"found {len(cursor_animations)}"
        )
    else:
        cursor_animation = cursor_animations[0]
        if cursor_animation.get("repeatCount") != "indefinite":
            errors.append(f"{relative}: cursor must repeat indefinitely")
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

    reduced_motion = REDUCED_MOTION_PATTERN.search(raw)
    if reduced_motion is None:
        errors.append(f"{relative}: missing reduced-motion media query")
    else:
        reduced_body = reduced_motion.group("body")
        if not re.search(
            r"\.motion-layer\s*\{[^}]*display\s*:\s*none\s*!important",
            reduced_body,
            re.IGNORECASE | re.DOTALL,
        ):
            errors.append(
                f"{relative}: reduced motion must hide only motion layers"
            )
        if ".static-fallback" in reduced_body:
            errors.append(
                f"{relative}: reduced motion must not hide static fallback"
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
                    _local_name(child.tag) == "animate"
                    and child.get("attributeName") == "opacity"
                    and _duration_seconds(child.get("dur", "")) == 16.0
                    for child in element
                )
            ):
                cover_candidates.append(element)
    if not cover_candidates:
        errors.append(
            f"{relative}: motion cover must default to opacity=0"
        )


def validate_theme_parity(errors: list[str]) -> None:
    for dark_name, light_name in THEME_PAIRS:
        dark_path = PROFILE_DIR / dark_name
        light_path = PROFILE_DIR / light_name
        if not dark_path.is_file() or not light_path.is_file():
            continue

        dark_normalized = _normalized_theme_svg(dark_path)
        light_normalized = _normalized_theme_svg(light_path)
        if (
            dark_normalized is not None
            and light_normalized is not None
            and dark_normalized != light_normalized
        ):
            errors.append(
                "Dark/light geometry or text differs after palette and "
                f"description normalization: {dark_name} / {light_name}"
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
            f"{relative}: font-weight below 700 ({match.group(1)})"
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

    if path.name.startswith("header"):
        validate_header_animation(root, raw, relative, errors)
        if "900" not in raw:
            errors.append(f"{relative}: missing 900-weight title")
    elif path.name.startswith("contact"):
        animation_elements = [
            element
            for element in root.iter()
            if _local_name(element.tag) in ANIMATION_ELEMENTS
        ]
        if animation_elements or CSS_ANIMATION_PATTERN.search(raw):
            errors.append(f"{relative}: contact assets must remain static")
    elif path.name.startswith("pastexam"):
        if "900" not in raw:
            errors.append(f"{relative}: missing 900-weight project title")


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

    validate_theme_parity(errors)

    unreferenced = {
        path.resolve() for path in svg_paths
    } - {path.resolve() for path in referenced}
    if unreferenced:
        errors.extend(
            "SVG is not referenced by README: "
            + path.relative_to(REPO_ROOT).as_posix()
            for path in sorted(unreferenced)
        )

    validate_repository_files(errors)

    if errors:
        print("Profile validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print(
        "Profile validation passed: "
        f"{len(svg_paths)} SVG files, "
        f"{len(referenced)} README asset references."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
