# Umami profile signal sync

## Architecture

The Header signal is a scheduled aggregate snapshot, not a browser-side or
real-time dashboard:

```text
Umami Cloud API
  -> scheduled GitHub Actions workflow
  -> scripts/render_umami_signal.py
  -> two generated light-theme Header SVG sections
  -> profile/assets/data/umami-snapshot.json
  -> automated commit to main
```

The SVG files never make network requests. The renderer replaces only the
content between `UMAMI GENERATED SIGNAL START` and
`UMAMI GENERATED SIGNAL END`.

## Repository configuration

Create this Actions secret:

- `UMAMI_API_KEY`: an Umami Cloud API key with access to the tracked website.

Create these Actions variables:

- `UMAMI_WEBSITE_ID`: the Umami website ID for PhysArchive.
- `UMAMI_REGION`: Umami Cloud region, normally `us` (default: `us`).
- `UMAMI_TIMEZONE`: IANA timezone used for hourly buckets (default:
  `Asia/Taipei`).

Do not place the values in source files, workflow logs, fixtures, Issues, or
Pull Requests. The API key is sent only in the `x-umami-api-key` request
header.

## API and data mapping

The renderer uses the documented Umami Cloud API base
`https://api.umami.is/v1/{region}` and calls:

- `GET /websites/:websiteId/pageviews` with `startAt`, `endAt`, `unit=hour`,
  and `timezone`.
- `GET /websites/:websiteId/stats` with the same 24-hour time window.

References:

- [Umami Cloud API keys](https://docs.umami.is/docs/cloud/api-key)
- [Umami website statistics API](https://docs.umami.is/docs/api/website-stats)

The pageviews response is mapped into 24 local-hour buckets. Missing hours are
filled with zero, values are sorted oldest to newest, and square-root scaling
maps them into the fixed signal area. Square-root scaling keeps smaller values
visible when one hour has a large spike. An all-zero snapshot produces a flat
baseline. The same snapshot is used for the desktop and mobile light-theme
variants.

Only these public aggregate fields are stored:

- snapshot generation time and timezone;
- 24 hourly pageview totals;
- 24-hour pageviews;
- 24-hour visitors.

No sessions, IP addresses, referrer details, paths, locations, fingerprints,
event payloads, headers, account identifiers, or credentials are stored.

## Schedule and manual execution

`.github/workflows/sync-umami-signal.yml` runs at minute 17 every six hours.
GitHub scheduled workflows use UTC, so the cron values correspond to 00:17,
06:17, 12:17, and 18:17 in Asia/Taipei. Scheduled workflows run from the
default branch only.

To run a refresh manually:

1. Open **Actions** in the repository.
2. Select **Sync Umami profile signal**.
3. Choose **Run workflow** from the default branch.
4. Inspect the validation and commit steps.

The workflow commits only when generated content changed, using:

```text
chore(analytics): refresh profile signal [skip ci]
```

## Local fixture validation

Fixtures contain artificial aggregate values and need no repository secret:

```bash
python scripts/render_umami_signal.py \
  --pageviews-fixture tests/fixtures/umami-pageviews.json \
  --stats-fixture tests/fixtures/umami-stats.json \
  --output-root temporary-output

python -m unittest discover -s tests -p "test_*.py"
python scripts/validate_profile.py
```

Do not render fixture values into production assets. Fixture output belongs in
a temporary directory.

## Failure and last-known-good behavior

The renderer fetches and validates both API responses before writing any file.
If either request times out, returns an error, or has an invalid schema, the
process exits non-zero. The workflow does not commit, and the repository keeps
the last-known-good Header SVG and snapshot.

Errors name the failed operation but omit request headers, API keys, website
IDs, response bodies, and URLs.

## API key rotation

1. Create a replacement key in Umami Cloud.
2. Replace the `UMAMI_API_KEY` Actions secret in repository settings.
3. Manually run the sync workflow and confirm a successful snapshot.
4. Revoke the old key in Umami Cloud.

Never keep both keys in the repository or use a personal GitHub token as an
Umami credential.

## Disabling the sync safely

Disable the scheduled workflow in the repository Actions settings, or remove
only its `schedule` trigger in a reviewed Pull Request. Do not delete the
generated SVG section or replace it with zero data. The last committed Header
remains a valid static snapshot when scheduling is disabled.

## Troubleshooting

- **Missing environment variable:** verify the secret and all repository
  variables exist and are available to Actions.
- **HTTP authentication failure:** rotate `UMAMI_API_KEY`; do not print it.
- **No generated diff:** the rounded snapshot and aggregates are unchanged.
- **Generated Header validation failure:** run
  `python scripts/validate_profile.py` and the unit tests; both retained
  light-theme Header assets must contain a valid generated signal section.
- **Old image on GitHub:** this is image caching. Do not increment the README
  cache query for scheduled snapshots.
- **Workflow does not run on a PR branch:** schedules execute only from the
  default branch after the workflow is merged.
