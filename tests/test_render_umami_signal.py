from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "render_umami_signal.py"
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures"
GENERATED_AT = "2026-07-29T06:17:00+08:00"

SPEC = importlib.util.spec_from_file_location("render_umami_signal", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
render = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = render
SPEC.loader.exec_module(render)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _svg_path(root: Path, name: str) -> Path:
    return root / "profile" / "assets" / name


def _signal_element(path: Path) -> ET.Element:
    root = ET.parse(path).getroot()
    return next(
        element
        for element in root.iter()
        if element.get("data-umami-signal") is not None
    )


class UmamiSignalTests(unittest.TestCase):
    maxDiff = None

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.output_root = Path(self.temporary_directory.name)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _write_fixture(self, name: str, payload: object) -> Path:
        path = self.output_root / name
        path.write_text(
            json.dumps(payload, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def _run(
        self,
        pageviews: Path | None = None,
        stats: Path | None = None,
        *,
        generated_at: str = GENERATED_AT,
    ) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        arguments = [
            "--pageviews-fixture",
            str(pageviews or FIXTURE_DIR / "umami-pageviews.json"),
            "--stats-fixture",
            str(stats or FIXTURE_DIR / "umami-stats.json"),
            "--output-root",
            str(self.output_root),
            "--generated-at",
            generated_at,
        ]
        with redirect_stdout(stdout), redirect_stderr(stderr):
            result = render.main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_normal_traffic_fixture(self) -> None:
        result, stdout, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        self.assertIn("120 views, 56 visitors", stdout)

        snapshot = json.loads(
            (
                self.output_root
                / "profile/assets/data/umami-snapshot.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["pageviews"], 120)
        self.assertEqual(snapshot["visitors"], 56)
        self.assertEqual(len(snapshot["hourlyPageviews"]), 24)
        self.assertEqual(snapshot["hourlyPageviews"][7], 12)
        self.assertEqual(snapshot["hourlyPageviews"][15], 14)

    def test_all_zero_data_uses_flat_baseline(self) -> None:
        rows = [
            {
                "x": (
                    datetime(2026, 7, 28, hour, tzinfo=ZoneInfo("UTC"))
                ).isoformat(),
                "y": 0,
            }
            for hour in range(24)
        ]
        pageviews = self._write_fixture("zero-pageviews.json", {"pageviews": rows})
        stats = self._write_fixture(
            "zero-stats.json", {"pageviews": 0, "visitors": 0}
        )
        result, _, stderr = self._run(pageviews, stats)
        self.assertEqual(result, 0, stderr)

        desktop = _signal_element(
            _svg_path(self.output_root, "header-light.svg")
        ).get("d", "")
        mobile = _signal_element(
            _svg_path(self.output_root, "header-mobile-light.svg")
        ).get("d", "")
        self.assertEqual(set(re.findall(r"[ML][\d.]+ ([\d.]+)", desktop)), {"220"})
        self.assertEqual(set(re.findall(r"[ML][\d.]+ ([\d.]+)", mobile)), {"450"})

    def test_missing_hourly_buckets_are_filled_with_zero(self) -> None:
        pageviews = self._write_fixture(
            "missing-pageviews.json",
            {
                "pageviews": [
                    {"x": "2026-07-27T23:00:00Z", "y": 3},
                    {"x": "2026-07-28T22:00:00Z", "y": 7},
                ]
            },
        )
        stats = self._write_fixture(
            "missing-stats.json", {"pageviews": 10, "visitors": 2}
        )
        result, _, stderr = self._run(pageviews, stats)
        self.assertEqual(result, 0, stderr)
        snapshot = json.loads(
            (self.output_root / render.SNAPSHOT_PATH).read_text(encoding="utf-8")
        )
        self.assertEqual(snapshot["hourlyPageviews"][0], 3)
        self.assertEqual(snapshot["hourlyPageviews"][-1], 7)
        self.assertEqual(snapshot["hourlyPageviews"][1:-1], [0] * 22)

    def test_single_spike_respects_plot_padding(self) -> None:
        values = [0] * 24
        values[12] = 9999
        path = render.signal_path(tuple(values), render.DESKTOP_LAYOUT)
        y_values = [
            float(value)
            for value in re.findall(r"[ML][\d.]+ ([\d.]+)", path)
        ]
        self.assertEqual(min(y_values), render.DESKTOP_LAYOUT.y_top)
        self.assertEqual(max(y_values), render.DESKTOP_LAYOUT.y_baseline)

    def test_invalid_api_fixture_does_not_write_outputs(self) -> None:
        pageviews = self._write_fixture(
            "invalid-pageviews.json", {"pageviews": "not-an-array"}
        )
        result, _, stderr = self._run(pageviews)
        self.assertEqual(result, 1)
        self.assertIn("invalid schema", stderr)
        self.assertFalse((self.output_root / "profile").exists())

    def test_api_timeout_is_sanitized(self) -> None:
        secret = "umami-secret-value-that-must-not-leak"
        with patch.object(render, "urlopen", side_effect=TimeoutError):
            with self.assertRaises(render.SignalSyncError) as context:
                render._request_json(
                    "https://api.umami.is/v1/us/websites/private/pageviews",
                    secret,
                    "pageviews",
                    0.01,
                )
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn("private", str(context.exception))
        self.assertIn("failed before a response", str(context.exception))

    def test_live_requests_use_documented_aggregate_endpoints(self) -> None:
        calls: list[tuple[str, str, str, float]] = []

        def fake_request(
            url: str,
            api_key: str,
            endpoint_label: str,
            timeout: float,
        ) -> object:
            calls.append((url, api_key, endpoint_label, timeout))
            if endpoint_label == "pageviews":
                return json.loads(
                    (FIXTURE_DIR / "umami-pageviews.json").read_text(
                        encoding="utf-8"
                    )
                )
            return json.loads(
                (FIXTURE_DIR / "umami-stats.json").read_text(encoding="utf-8")
            )

        generated_at = datetime.fromisoformat(GENERATED_AT)
        with patch.object(render, "_request_json", side_effect=fake_request):
            render.fetch_umami_payloads(
                api_key="not-a-real-key",
                website_id="website-id",
                region="us",
                timezone_name="Asia/Taipei",
                generated_at=generated_at,
                timeout=12,
            )

        self.assertEqual([call[2] for call in calls], ["pageviews", "stats"])
        pageviews_url = urlsplit(calls[0][0])
        stats_url = urlsplit(calls[1][0])
        self.assertEqual(
            pageviews_url.path,
            "/v1/us/websites/website-id/pageviews",
        )
        self.assertEqual(
            stats_url.path,
            "/v1/us/websites/website-id/stats",
        )
        pageviews_query = parse_qs(pageviews_url.query)
        self.assertEqual(pageviews_query["unit"], ["hour"])
        self.assertEqual(pageviews_query["timezone"], ["Asia/Taipei"])
        self.assertEqual(
            int(pageviews_query["endAt"][0])
            - int(pageviews_query["startAt"][0]),
            24 * 60 * 60 * 1000,
        )
        self.assertEqual(
            set(parse_qs(stats_url.query)),
            {"startAt", "endAt"},
        )

    def test_stats_failure_never_writes_partial_snapshot(self) -> None:
        pageviews_payload = json.loads(
            (FIXTURE_DIR / "umami-pageviews.json").read_text(encoding="utf-8")
        )
        request_count = 0

        def fail_second_request(
            url: str,
            api_key: str,
            endpoint_label: str,
            timeout: float,
        ) -> object:
            nonlocal request_count
            request_count += 1
            if request_count == 1:
                return pageviews_payload
            raise render.SignalSyncError("Umami stats request failed.")

        with (
            patch.object(render, "_request_json", side_effect=fail_second_request),
            patch.dict(
                render.os.environ,
                {
                    "UMAMI_API_KEY": "not-a-real-key",
                    "UMAMI_WEBSITE_ID": "website-id",
                    "UMAMI_REGION": "us",
                    "UMAMI_TIMEZONE": "Asia/Taipei",
                },
                clear=False,
            ),
        ):
            stdout = io.StringIO()
            stderr = io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                result = render.main(
                    [
                        "--output-root",
                        str(self.output_root),
                        "--generated-at",
                        GENERATED_AT,
                    ]
                )

        self.assertEqual(result, 1)
        self.assertIn("stats request failed", stderr.getvalue())
        self.assertFalse((self.output_root / "profile").exists())

    def test_repeated_generation_is_deterministic(self) -> None:
        first_result, _, first_error = self._run()
        self.assertEqual(first_result, 0, first_error)
        first_hashes = {
            path.relative_to(self.output_root): _sha256(path)
            for path in self.output_root.rglob("*")
            if path.is_file()
        }

        second_result, second_stdout, second_error = self._run()
        self.assertEqual(second_result, 0, second_error)
        second_hashes = {
            path.relative_to(self.output_root): _sha256(path)
            for path in self.output_root.rglob("*")
            if path.is_file()
        }
        self.assertEqual(first_hashes, second_hashes)
        self.assertIn("changed: none", second_stdout)

    def test_generated_svg_is_valid_xml(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        for relative in render.HEADER_PATHS:
            ET.parse(self.output_root / relative)

    def test_desktop_dark_light_geometry_matches(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        dark = _signal_element(
            _svg_path(self.output_root, "header-dark.svg")
        ).get("d")
        light = _signal_element(
            _svg_path(self.output_root, "header-light.svg")
        ).get("d")
        self.assertEqual(dark, light)

    def test_mobile_dark_light_geometry_matches(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        dark = _signal_element(
            _svg_path(self.output_root, "header-mobile-dark.svg")
        ).get("d")
        light = _signal_element(
            _svg_path(self.output_root, "header-mobile-light.svg")
        ).get("d")
        self.assertEqual(dark, light)

    def test_generated_telemetry_text_stays_inside_viewbox(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        for relative in render.HEADER_PATHS:
            root = ET.parse(self.output_root / relative).getroot()
            width = float(root.get("viewBox", "").split()[2])
            group = next(
                element
                for element in root.iter()
                if element.get("aria-label") == "Umami traffic snapshot"
            )
            for text in (
                element
                for element in group.iter()
                if element.tag.endswith("text")
            ):
                x = float(text.get("x", "0"))
                content = "".join(text.itertext())
                font_size = 15 if "mobile" in relative.name else 13
                estimated_width = len(content) * font_size * 0.62
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + estimated_width, width)

    def test_reduced_motion_keeps_static_signal_visible(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        for relative in render.HEADER_PATHS:
            raw = (self.output_root / relative).read_text(encoding="utf-8")
            self.assertIn("@media (prefers-reduced-motion: reduce)", raw)
            self.assertRegex(
                raw,
                r"\.signal-motion\s*\{\s*display:\s*none\s*!important;",
            )
            root = ET.fromstring(raw)
            signal = _signal_element(self.output_root / relative)
            self.assertNotIn(
                "signal-motion", signal.get("class", "").split()
            )
            animations = [
                element
                for element in root.iter()
                if element.tag.endswith("animateMotion")
            ]
            self.assertEqual(len(animations), 1)

    def test_generated_files_do_not_contain_secrets(self) -> None:
        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        forbidden = (
            "UMAMI_API_KEY",
            "x-umami-api-key",
            "umami-secret",
            "I1JIGVvybEvbiIrR",
        )
        for path in self.output_root.rglob("*"):
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
            for token in forbidden:
                self.assertNotIn(token, raw)

    def test_generator_does_not_modify_unrelated_assets(self) -> None:
        unrelated = [
            Path("profile/README.md"),
            Path("profile/assets/contact-light.svg"),
            Path("profile/assets/contact-dark.svg"),
            Path("profile/assets/projects/pastexam-light.svg"),
            Path("profile/assets/projects/pastexam-dark.svg"),
        ]
        for relative in unrelated:
            target = self.output_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(REPO_ROOT / relative, target)
        before = {
            relative: _sha256(self.output_root / relative)
            for relative in unrelated
        }

        result, _, stderr = self._run()
        self.assertEqual(result, 0, stderr)
        after = {
            relative: _sha256(self.output_root / relative)
            for relative in unrelated
        }
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
