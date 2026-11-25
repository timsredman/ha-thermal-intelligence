# Contributing

1. Use Python 3.14.2 and Home Assistant Core 2026.11.2 APIs.
2. Keep source processing local and event driven. Do not add a poll loop for state already available in HA.
3. Keep v0.1 observational. Any heating-control feature needs an explicit later version and safety design.
4. Add deterministic tests for thermal calculations, invalid/unavailable states, stale values and restart persistence.
5. Run `pytest`, `ruff check .`, `python -m script.hassfest` in a Home Assistant development environment, and HACS validation before release.
6. Do not claim causation from observed correlation or a single thermal snapshot.
