# Contributing

## Workflow

- Create a feature or fix branch from `main`.
- Keep commits focused on one logical change.
- Do not mix production code, generated artifacts, and experimental test files in the same commit.

## Before Opening a PR

- Run the smallest relevant validation for the files you changed.
- Confirm that large binaries, model weights, logs, and local databases are not staged.
- Review the diff for accidental runtime or environment-specific changes.

## Repository Hygiene

- Keep secrets in `.env` and local machine configuration only.
- Do not commit assets that are better downloaded or rebuilt locally.
- Avoid adding files larger than 5 MB unless there is a strong reason and the repository policy has been discussed first.

## Code Style

- Prefer clear, direct naming over clever abstractions.
- Keep comments short and only where they add real value.
- Preserve existing runtime behavior unless the change is intentional and documented.

## Testing

- Add or update tests when they materially improve confidence in the change.
- If a change is operational or infrastructure-heavy, document the verification method in the PR description.
