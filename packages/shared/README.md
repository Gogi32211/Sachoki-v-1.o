# packages/shared

Cross-service utilities, types, and frontend components that are used by more than one service.

## packages/shared/python

Python utilities safe to import from any service without pulling in domain logic.

Candidates (do not move yet — audit imports first):
- Database connection helpers (`db.py` subset)
- OHLCV data types / Pydantic schemas
- Common response models

## packages/shared/frontend

React primitives and design system components shared between dashboard and any future frontend.

Candidates (do not move yet — verify no dashboard-specific dependencies):
- `design-system/` components (Button, Card, Badge, etc.)
- `utils/exportTickers.js`
- `utils/signalBadges.js`

## Rules for adding to shared

1. Must be used by at least two services.
2. Must have zero domain logic (no signal computation, no scoring).
3. Must have no circular dependencies back into any service.
4. Add a test for any utility moved here.

Do not move files here without explicit approval during Phase 2+.
