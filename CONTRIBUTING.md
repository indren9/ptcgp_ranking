# Contributing

Thanks for your interest in the project. PTCGP Ranking - MARS is published by
Andrea Visentin under the MIT License.

## Workflow

1. Fork the repository.
2. Clone your fork locally.
3. Create a feature branch with a short descriptive name.
4. Make focused changes and keep generated outputs out of the commit.
5. Open a pull request against `main` and explain what changed and why.

## Python Runtime Contract

The project supports only Python minors that have been explicitly validated:

- **Python 3.12** — supported.
- **Python 3.14** — supported and recommended for local development.
- **Python 3.13** — not validated and not supported.

Do not interpret the support contract as a continuous minimum-version range;
support is limited to the explicitly validated minors above.

GitHub production continues to run on **Python 3.12**. The Windows scheduler
continues to use its **current deployed runtime and virtual environment,
unchanged**. Neither runtime is migrated by this documentation update.
## Development Setup

```bash
python -m venv .venv

# Windows
. .venv/Scripts/activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
pytest -q
```

Use the notebooks for inspection and preview, but avoid committing heavy
notebook outputs. Large generated files should live under ignored folders such
as `outputs/`, `cache/`, or `logs/`.

## Code Expectations

- Keep pull requests cohesive and easy to review.
- Prefer existing project patterns over new abstractions.
- Add or update tests when changing behavior.
- Keep public documentation and user-facing messages in English.
- Preserve compatibility wrappers unless the removal is intentional and tested.
- Do not commit credentials, tokens, personal cache data, or generated analysis
  outputs.

## Legal Notes

Pokemon and related names are trademarks of their respective owners. This
project is unaffiliated with them; see `NOTICE`.

By opening a pull request, you agree that your contribution is released under
the repository MIT License.

## Issues

Open a GitHub issue with a short description, reproduction steps, expected
behavior, and relevant logs or output paths.
