# Contributing to 404-researcher

Thanks for your interest in improving the project! Here's how contributions work.

## Reporting a bug or suggesting an idea

Open an [Issue](../../issues) — describe what happened (or what you'd like to see), and include steps to reproduce if it's a bug. No need to write code first.

## Proposing a code change

This project uses the standard GitHub **fork → branch → pull request** workflow:

1. **Fork** this repository (button top-right on GitHub) — this creates your own copy under your account.
2. **Clone your fork** and create a branch for your change:
   ```bash
   git checkout -b my-improvement
   ```
3. **Make your changes** and test that the app still runs (`streamlit run app.py`).
4. **Commit and push** to your fork:
   ```bash
   git commit -m "Describe your change"
   git push origin my-improvement
   ```
5. **Open a Pull Request** from your fork/branch back to `404-researcher/research-assistant`'s `main` branch. Describe what you changed and why.

## What happens next

A pull request does **not** get merged automatically. The maintainer reviews it, may ask questions or request adjustments, and only merges it once it looks good. Your changes stay on your own fork until then — nothing is published to the main project without that review.

## Ideas that are especially welcome

- A new academic database source in `search.py`
- Bug fixes (open an issue first if the fix isn't obvious)
- UI/UX improvements in `app.py`
- Documentation improvements

## Code style

- No strict linting is enforced, but try to match the existing style in the file you're editing (naming, docstrings, structure).
- Keep changes focused — a bug fix shouldn't also refactor unrelated code.

## Questions?

Open an issue — happy to help.
