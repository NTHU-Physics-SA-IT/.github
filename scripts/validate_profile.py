#!/usr/bin/env python3
"""Validate the Organization Profile without third-party dependencies."""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = REPO_ROOT / "profile"
README = PROFILE_DIR / "README.md"
ASSET_DIR = PROFILE_DIR / "assets"

EXPECTED_ASSETS = {
    "assets/header-dark.svg",
    "assets/header-light.svg",
    "assets/header-mobile-dark.svg",
    "assets/header-mobile-light.svg",
    "assets/contact-dark.svg",
    "assets/contact-light.svg",
    "assets/contact-mobile-dark.svg",
    "assets/contact-mobile-light.svg",
    "assets/projects/pastexam-dark.svg",
    "assets/projects/pastexam-light.svg",
    "assets/projects/pastexam-mobile-dark.svg",
    "assets/projects/pastexam-mobile-light.svg",
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
    r"font-weight\s*(?::|=)\s*[\"']?([1-6]00)\b", re.IGNORECASE
)
URL_FUNCTION_PATTERN = re.compile(r"url\(([^)]+)\)", re.IGNORECASE)


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


def validate_readme(errors: list[str]) -> set[Path]:
    if not README.is_file():
        errors.append("Missing profile/README.md")
        return set()

    parser = ReadmeHTMLParser()
    parser.feed(README.read_text(encoding="utf-8"))
    referenced: set[Path] = set()

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

        # Header and project card have four responsive sources. Contact has two.
        if len(sources) == 4:
            actual_media = tuple(source.get("media", "") for source in sources)
            if actual_media != required_media:
                errors.append(
                    f"README picture {index} source order/media is incorrect"
                )
        elif len(sources) == 2:
            actual_media = tuple(source.get("media", "") for source in sources)
            if actual_media != required_media[2:]:
                errors.append(
                    f"README picture {index} dark/light source order is incorrect"
                )
        else:
            errors.append(
                f"README picture {index} has unexpected source count: {len(sources)}"
            )

    for link in parser.links:
        parsed = urlsplit(link)
        if parsed.scheme not in {"https"} or not parsed.netloc:
            errors.append(f"README link must be an absolute HTTPS URL: {link}")

    return referenced


def validate_svg(path: Path, errors: list[str]) -> None:
    relative = path.relative_to(REPO_ROOT).as_posix()
    raw = path.read_text(encoding="utf-8")

    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        errors.append(f"{relative}: invalid XML ({exc})")
        return

    if _local_name(root.tag) != "svg":
        errors.append(f"{relative}: root element is not svg")
    if not root.get("viewBox"):
        errors.append(f"{relative}: missing viewBox")
    if root.get("role") != "img":
        errors.append(f"{relative}: role must be img")

    direct_children = {_local_name(child.tag) for child in root}
    if "title" not in direct_children:
        errors.append(f"{relative}: missing title")
    if "desc" not in direct_children:
        errors.append(f"{relative}: missing desc")

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

    name = path.name
    if name.startswith("header"):
        if "prefers-reduced-motion: reduce" not in raw:
            errors.append(f"{relative}: missing reduced-motion media query")
        if 'dur="16s"' not in raw:
            errors.append(f"{relative}: missing 16-second typing cycle")
        if "900" not in raw:
            errors.append(f"{relative}: missing 900-weight title")
    elif name.startswith("pastexam"):
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
