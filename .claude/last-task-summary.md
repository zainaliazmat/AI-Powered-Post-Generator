# Last Task Summary — Task 6: POST /database/{table}

## Status: DONE

## Files Modified
- `tests/test_main.py` — appended 4 new tests (lines 453–498)
- `src/main.py` — added `db_create_row` route (POST `/database/{table}`)

## What Was Done

### TDD Steps
1. Appended 4 failing tests to `tests/test_main.py`:
   - `test_db_create_row_returns_tr_fragment` — response is 200, contains `id="db-row-`, has HTMX headers
   - `test_db_create_row_persists_to_db` — row is actually written to SQLite
   - `test_db_create_row_duplicate_returns_409` — IntegrityError returns 409
   - `test_db_create_row_writes_audit_log` — INSERT is written to `logs/db_audit.log`

2. Confirmed 2 tests failed with 405 (route not defined).

3. Added `db_create_row` route to `src/main.py` after `db_update_row`, before `db_row_fragment`:
   - Filters empty string values and `id` from form payload
   - Guards against empty payload (422)
   - Validates table (404) and columns (422)
   - Catches `IntegrityError` and `OperationalError` → 409 with edit panel
   - Re-fetches row via `lastrowid` / `WHERE rowid = ?`
   - Calls `_audit("INSERT", ...)` inside `with` block
   - Sets `HX-Retarget: #db-tbody` and `HX-Reswap: afterbegin`

## Commands Run
```
./venv/bin/python -m pytest tests/test_main.py::test_db_create_row_returns_tr_fragment tests/test_main.py::test_db_create_row_persists_to_db -v
# → 2 FAILED (expected)

./venv/bin/python -m pytest tests/test_main.py::test_db_create_row_returns_tr_fragment tests/test_main.py::test_db_create_row_persists_to_db tests/test_main.py::test_db_create_row_duplicate_returns_409 tests/test_main.py::test_db_create_row_writes_audit_log -v
# → 4 PASSED

./venv/bin/python -m pytest tests/test_main.py -v
# → 54 PASSED
```

## Test Results
54/54 passed in 4.09s — no regressions.

## Suggested Commit
```
git add src/main.py tests/test_main.py .claude/last-task-summary.md
git commit -m "feat(main): add POST /database/{table} create-row route with audit log (Task 6)"
```
