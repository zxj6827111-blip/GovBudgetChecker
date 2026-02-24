# Backend `src/` Layout Migration Notes

This document records the migration state after Phase 6 cleanup.

## Current layout policy

- Backend runtime code is under `src/`.
- Code imports must use `src.*` paths.
- Top-level `rules/` and `config/` remain asset directories (YAML/config files).

## Compatibility layer status

- Removed in Phase 6:
  - `services/`
  - `engine/`
  - `providers/`
  - `schemas/`
- Any import like `from services...` or `from engine...` is now unsupported.

## Path-sensitive modules

- Organization data path defaults to project-root `data/` in `src/services/org_storage.py`.
- Rule loader remains at `rules/loader_ext.py` and is imported as `rules.loader_ext`.
- Runtime code must not infer asset paths from relocated module paths.

## Import guidelines

- Use `from src.services.org_storage import OrganizationStorage`
- Do not introduce legacy root imports.

## Regression checks

```bash
python -m pytest tests -q
npm --prefix app run build
```
