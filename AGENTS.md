# SpotiPi Agent Guidelines (v1.12.7)

This file is the canonical source of AI coding instructions for this repository.

## Scope and Precedence

- Use this file as the single instruction source for automated coding assistants.
- If another instruction file conflicts with this document, `AGENTS.md` wins.
- Keep guidance focused on Pi-safe runtime behavior, thread safety, and maintainable frontend UX.

## Architecture Snapshot

- Flask entrypoint is `run.py`, loading the app from `src/app.py`.
- Route blueprints live in `src/routes/`.
- Business logic belongs in `src/services/` and returns `ServiceResult`.
- Alarm/scheduler flows are in `src/core/`.
- Spotify integration lives in `src/api/spotify.py` (playback, search, queue, library) and shared HTTP helpers in `src/api/http.py`.
- Spotify credential management lives in `src/utils/spotify_secrets.py`.
- Input validation is centralized in `src/utils/validation.py`.
- Config is centralized via `src/config.py` plus thread-safe helpers in `src/utils/thread_safety.py`.
- Frontend shell lives in `frontend/src/` and is bundled into `static/dist/`.
- `templates/index.html` is the shell entrypoint, not the place for new feature markup.
- Initial frontend bootstrap data is assembled in `src/routes/main.py`.

## Core Engineering Rules

- Keep Python compatible with 3.10+ syntax.
- Preserve the snapshot contract: `/api/dashboard/status` and `/playback_status` may return `202` while data is `pending` or `auth_required`.
- Never mutate config or shared cache directly. Use:
  - `load_config()`
  - `save_config()`
  - `config_transaction()`
- Prefer consistent API envelopes with `api_response(...)` and `ServiceResult.to_dict()`.
- Use centralized validation in `src/utils/validation.py` and handle `ValidationError`.
- Reuse existing Spotify API helpers; do not create ad-hoc `requests.Session` instances.
- Use shared caching via `get_cache_migration_layer()` and honor configured TTLs.
- Keep read APIs additive; do not break the existing envelope or the `202` snapshot semantics.

## Logging and Observability

- Use `setup_logger(__name__)` from `src/utils/logger.py`.
- Prefer structured logging on error/critical paths:
  - `log_structured(logger, level, msg, **context)`
- Include useful context keys (for example `alarm_id`, `device_name`, `error_code`) where available.

## Raspberry Pi Guardrails

- Assume low CPU and memory on Pi Zero W; avoid expensive work on request threads.
- Respect low-power env toggles such as:
  - `SPOTIPI_LOW_POWER`
  - `SPOTIPI_MAX_CONCURRENCY`
  - `SPOTIPI_LIBRARY_TTL_MINUTES`
- Minimize disk writes and only persist when TTL/robustness justify it.
- Do not break production systemd behavior under `deploy/systemd/`.
- Treat the committed `static/dist/` bundle as production runtime input; Raspberry Pi deployments must not require an on-device frontend build.

## Security and Data Safety

- Keep Spotify token handling within `src/utils/token_encryption.py` patterns.
- Maintain restricted token file permissions (`0o600`).
- Treat `~/.spotipi/.env` as the canonical runtime secret path; repo-root `.env` is only a local dev override. `get_spotify_credentials()` falls back to `os.environ` so values loaded via `load_dotenv()` from a repo-level `.env` remain visible until migrated to the runtime file.
- Escape user-controlled strings in JS template literals.
- Preserve input sanitization rules, including device-name validation constraints.
- **LAN-trust is intentional, not a bug.** `SPOTIPI_TRUST_PRIVATE_NETWORK` defaults to `True`, so loopback/private/link-local clients reach state-changing and PII endpoints without admin auth; admin auth (`SPOTIPI_ADMIN_PASSWORD`) is opt-in and only enforced for non-local clients. This is a deliberate usability choice for a single-user home appliance — do NOT "harden" it by flipping the default. A review may note the posture, but treat it as by design.

## Frontend and UX Baseline

- The primary experience is the dashboard-first home surface with persistent playback.
- Keep the three primary actions obvious and fast: set alarm, start sleep, play now.
- Keep UI responsive and reliable across mobile and desktop breakpoints.
- Respect `prefers-reduced-motion`.
- Preserve accessibility primitives (`aria-*`, roles, labels) for interactive and dynamic UI.
- Use the `frontend/src/` Preact shell for new frontend behavior; do not add new feature logic to legacy `static/js/modules/`.
- All shipped styling lives in `frontend/src/styles.css` (bundled by esbuild into `static/dist/app.css`, the only stylesheet `templates/index.html` links). Make every CSS / responsive / a11y / contrast change there. The old `static/css/**` tree and the `alarm.html`/`sleep.html`/`settings.html` partials were removed in v1.12.4 as dead code — do not reintroduce per-page CSS or those templates.
- Keep raw API shapes behind frontend adapters/view models instead of spreading transport logic across components.
- Reuse established UI patterns before adding new variants (loading skeletons, empty states, transitions).
- Keep `/settings` as a surface of the same app shell unless a change explicitly requires otherwise.

## Testing and Quality Gates

- Add or update tests for any behavioral change in routes, services, or Spotify integration.
- Use deterministic tests with Flask test client (no live Spotify calls).
- Run `pytest` before finishing changes.
- If frontend code changes, also run:
  - `npm run typecheck`
  - `npm run build`
  - `npm run test:e2e`
- If deployment behavior is touched, verify `scripts/deploy_to_pi.sh.example` output format assumptions remain valid.

## Change Checklist

- Keep route handlers thin and move logic to service/core layers.
- Add new config fields to schema/defaults/documentation together.
- Document new environment variables in `config/default_config.json` and `Readme.MD`.
- Prefer extending existing modules over creating parallel abstractions.
- If frontend code changes, commit rebuilt `static/dist/` assets together with the source changes.
