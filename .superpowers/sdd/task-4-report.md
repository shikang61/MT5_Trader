# Task 4 Report: Dockerfile.northflank — Wine + MT5 + mt5linux bridge image

**Status:** Complete (files created, syntax verified, committed)

## Files Created

- `Dockerfile.northflank` — Ubuntu 24.04 → winehq-stable → Xvfb/x11vnc/noVNC → uv project install + mt5linux Linux client → bake MT5 install → supervisord CMD
- `deploy/northflank/install_mt5.sh` — build-time script: wineboot init, install broker-B MT5 terminal via `${MT5_INSTALLER_URL}`, install Windows Python 3.11.9 under Wine, then `pip install MetaTrader5 mt5linux` into that Wine Python (bridge SERVER side)

## Shell Syntax Check

```
bash -n deploy/northflank/install_mt5.sh
→ SYNTAX OK (exit 0)
```

No syntax errors.

## Build-Robustness Fixes Applied (2026-06-19)

Two surgical changes to `deploy/northflank/install_mt5.sh`:

**Fix 1 — wrap wineboot in a virtual display**
- Old: `wineboot --init && sleep 5`
- New: `xvfb-run -a wineboot --init && sleep 5`
- Reason: wineboot can error without a display; xvfb-run gives it a headless one.

**Fix 2 — precise Wine-Python path resolution + loud failure**
- Old: `WINPY="$(find "$WINEPREFIX" -name python.exe -path '*Python3*' | head -1)"`
- New:
  ```bash
  WINPY="$(find "$WINEPREFIX" -name python.exe -path '*Python311*' | head -1)"
  [ -n "$WINPY" ] || { echo "install_mt5: wine python.exe not found under $WINEPREFIX" >&2; exit 1; }
  ```
- Reason: `*Python3*` could match any Python 3.x version; `*Python311*` pins to the installed 3.11.9. The guard clause fails loudly if the path resolves to empty (previously silent: `wine ""` would give a confusing error later).

`bash -n` result: exit 0 (no syntax errors).

## Deferred Steps (cannot run locally)

**Step 3 (`docker build`) — DEFERRED:** Docker daemon is not running in this environment. The image build will be verified by Northflank's Kaniko builder on first deploy. Build ARG `MT5_INSTALLER_URL` must be supplied at build time in the Northflank build config.

**Step 4 (Wine-side bridge deps check) — DEFERRED:** Requires a running container from the built image. Also blocked on not having broker-B's installer URL. The verify command from the brief:
```bash
docker run --rm mt5-northflank:dev bash -lc \
  'source /opt/wine-mt5/winpython.env && xvfb-run -a wine "$WINE_PYTHON" -c "import MetaTrader5, mt5linux; print(\"wine deps ok\")"'
```
This must be run manually after the first successful Northflank build, or locally once Docker is running and a broker-B URL is available.

## Key Design Decisions

1. `mt5linux` installed via `uv pip install mt5linux` in the Linux layer — NOT added to `pyproject.toml` (as required).
2. `MT5_INSTALLER_URL` is a Docker build ARG, not hardcoded — broker-B URL supplied at build time on Northflank.
3. Xvfb is present throughout: both the `uv sync` layer (via system packages) and `install_mt5.sh` uses `xvfb-run -a` for all Wine GUI operations.
4. `COPY . /app` comes AFTER the MT5 Wine bake layer to avoid cache busting Wine setup on every source change.
5. Files are verbatim from the brief — no additions beyond what was specified.

## Concerns

- `supervisord.conf` and `crontab` are referenced in the Dockerfile (`COPY deploy/northflank/supervisord.conf` and `COPY deploy/northflank/crontab`) but are not yet created (tasks 5/6/7). The Kaniko build will fail until those files exist — this is expected for a multi-task branch.
- The `chmod +x deploy/northflank/*.sh` RUN step will also need `start_*.sh` scripts from later tasks to be meaningful.
- `winehq-stable` on Ubuntu 24.04 ("noble") was the correct repo codename used in the apt sources line.
