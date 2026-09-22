# Contributing to SpotiPi

Thanks for contributing.

This guide focuses on practical contribution steps for this repository.

## Prerequisites

- Python 3.10+
- Spotify Premium account (for real playback testing)
- Git

## Local Setup

```bash
git clone https://github.com/phobits/SpotiPi.git
cd SpotiPi
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python generate_token.py
./scripts/local_server.sh start
```

Open: [http://127.0.0.1:5001](http://127.0.0.1:5001)

## Development Workflow

1. Create a branch from `main`.
2. Implement your change in small, reviewable commits.
3. Add or update tests for behavior changes.
4. Run validation locally.
5. Open a pull request with a clear description.

## Validation Before PR

Run at minimum:

```bash
pytest
```

For UI changes, also verify manually:

- Mobile and desktop layouts
- Alarm flow and sleep timer flow
- Playback controls and error states
- Browser console has no new errors

## Code Guidelines

- Keep Flask routes thin; move business logic to `src/services/` or `src/core/`.
- Reuse existing helpers for config, validation, and Spotify API access.
- Keep Python syntax compatible with the current codebase style (3.10+).
- Preserve thread-safe patterns around shared state/config access.
- Prefer small focused functions over broad side-effect-heavy changes.

AI assistant contributors must follow [`AGENTS.md`](AGENTS.md).

## Commit and PR Guidelines

- Use clear commit messages (Conventional Commit style is preferred, e.g. `feat: ...`, `fix: ...`, `docs: ...`).
- Keep PRs scoped to one concern.
- In the PR description include:
  - What changed
  - Why it changed
  - How you tested it
  - Screenshots for UI changes

## Security

- Never commit secrets, tokens, or `.env` values.
- Avoid logging sensitive Spotify/auth data.
- Validate and sanitize user-controlled input.

## Reporting Bugs

When opening an issue, include:

- Environment (OS, Python version, browser/device)
- Reproduction steps
- Expected behavior vs actual behavior
- Relevant logs and screenshots

## Helpful References

- [`Readme.MD`](Readme.MD)
- [`AGENTS.md`](AGENTS.md)
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- [`docs/ENVIRONMENT_VARIABLES.md`](docs/ENVIRONMENT_VARIABLES.md)
