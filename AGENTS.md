# Repository Guidelines

## Scope

This repository contains the independent macOS application in `dipbot/`. The Windows release and customer license in the parent workspace are reference/deployment materials outside this repository. Do not copy them into Git or modify them while working on this application.

## Structure

- `dipbot/`: responsibility-based packages; see `docs/ARCHITECTURE_RU.md`. `domain` is pure Python; `application` coordinates `market`, `execution`, `persistence`; `ui` is presentation. `bootstrap.py` owns startup.
- `tests/`: offline pytest tests with mocked HTTP and transaction submission.
- `tools/`: reusable diagnostics and verification; see `tools/README.md`. Some tools submit LIVE transactions; do not batch-run them.
- `docs/`: compact current documentation; begin with `docs/README.md` and `docs/CURRENT_STATUS_RU.md`.
- `README_RU.md`: detailed user guide; `README.md`: repository landing page.

## Commands

```bash
uv sync --frozen --group dev
uv run --frozen pytest -q
QT_QPA_PLATFORM=offscreen uv run --frozen python launcher.py --smoke-test
./BUILD_MAC.command
```

Run on macOS. The smoke test creates temporary state, signs only with an ephemeral unfunded key and does not access user Keychain entries or broadcast transactions. Packaging changes also require a smoke test of the built executable and `codesign --verify --deep --strict`.

## Implementation and review

Use four-space Python indentation. Preserve exact UI labels and concise Russian documentation. Keep RPC and receipt waits off the UI thread. Use Decimal/integer arithmetic for financial amounts and preserve the write-before-broadcast journal, canonical pool validation, minOut bounds and ambiguous-transaction latch.

Do not describe reconstructed rules as recovered source code or imply LIVE validation based on mock tests. Transaction checks require explicit authorization and a dedicated test wallet. Report checks not performed.

Never commit licenses, keys, seed phrases, credential-bearing RPC URLs, wallet state, generated bundles or local environments. Review the staged file list and diff. Use concise imperative commit subjects and describe behavior plus validation in PRs.


## Documentation hygiene

Keep current behavior in the user guide and current status. Historical evidence and retired reverse-analysis scripts are in Git at `89e3a4f`; they are not an active task list. Write generated reports/screenshots to ignored `.local-artifacts/` or `/tmp`. Preserve behavioral regression tests even when their names refer to earlier audits.

## Dependency boundaries

Production modules must not import repository `tools` or `tests`. Keep `domain` free of Qt, web3, network and storage imports. Backend packages must not import `ui`, `application` or `checks`; the architecture tests enforce this. Use explicit package imports. Preserve the single sequential owner of trade execution when extracting services.
