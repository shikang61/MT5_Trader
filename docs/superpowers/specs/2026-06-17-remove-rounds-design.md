# Design: Remove competition rounds → standing daily-reset risk posture

Date: 2026-06-17
Status: approved (design); implementation pending
Scope: `config/risk.yaml`, `RiskManager` (`risk/manager.py`), the kill path in
`live/runner.py` (shared by `LiveRunner` / `PortfolioRunner` / `PairRunner`), and
their tests. No strategy, sizing-math, blackout, vol_guard, or passive-book
changes.

## Problem

The risk module is competition-shaped: `config/risk.yaml` carries a `rounds:`
list (round1/2/3) with time windows and escalating budgets (`max_leverage`
3→6, `target_vol` →0.70, `give_back`), and `RiskManager.round_at(now)` selects
the active round by wall-clock. The competition is no longer the goal — the bot
should run indefinitely on a demo account and the competition account as a step
toward real-cash deployment.

Two consequences of the current design make it unfit for continuous running:

1. **Round vocabulary is dead weight** once there are no rounds — `round_at`,
   the `rounds` list, and round-name-keyed state add confusion with no purpose.
2. **The kill switch never resets and terminates the process.**
   `mark_round_start` anchors start-equity once via `setdefault` keyed by round
   name; with rounds gone everything keys to one bucket anchored to the bot's
   *first-ever* equity. The kill then trips permanently once equity falls
   `loss_limit` below inception. Worse, on a trip `runner._kill_check` sets
   `self.halted = True`, which makes `resilient_loop` exit — the process dies and
   stays dead (no auto-resume; `watchdog.py` only notifies, does not restart).

## Decisions (agreed during brainstorming)

- **Kill anchor = daily reset.** Anchor to the first equity seen each UTC day.
  Trip if intraday equity falls `loss_limit` below day-start; stand down flat for
  the rest of the day; **auto-resume next UTC day.** Mirrors `passive_limits.daily_halt`.
- **Standing posture** (single block, replaces `default` + rounds):
  `max_leverage 2`, `target_vol 0.30` (~0.75x lev on gold), `loss_limit 0.04`
  (daily), `give_back 0.0` (trailing kill off).
- **Clean removal:** rename `RoundCfg` → `Posture`, delete `round_at` and the
  `rounds` list. Strip all "round" vocabulary from the risk module.
- **Loop runs indefinitely:** a kill is a daily stand-down, not a process exit.

## Components

### 1. `config/risk.yaml`

- Delete the `rounds:` list.
- Rename `default:` → `posture:`:
```yaml
posture:
  max_leverage: 2
  target_vol:   0.30   # annualized vol budget; ~0.75x lev on gold
  loss_limit:   0.04   # daily kill: drawdown fraction from day-start equity;
                       # stand down flat, auto-resume next UTC day
  give_back:    0.0    # trailing daily kill: drawdown fraction from day-PEAK (0 = off)
```
- `blackout:` and `vol_guard:` blocks unchanged.
- Header comment rewritten: daily-reset semantics, no competition/round language.

### 2. `RiskManager` (`risk/manager.py`)

- `RoundCfg` → `Posture` dataclass: `max_leverage, loss_limit, target_vol,
  give_back` (drop `name/start/end`).
- Remove the `rounds` list and `round_at()`. Constructor takes one `Posture`.
- `from_yaml`: read the single `posture:` block (+ `blackout`/`vol_guard` as today).
- Day-keyed state replaces round-keyed: `_day, _day_start_equity,
  _day_peak_equity, _halted_day` (all `None`/0 to start).
- `roll_day(now, equity)`: on a new UTC date set `_day`, `_day_start_equity`,
  `_day_peak_equity = equity`; otherwise `_day_peak_equity = max(peak, equity)`.
- `size(signal, vol, now)`: unchanged formula, reads `self.posture` directly
  (still returns 0.0 inside a blackout window).
- `kill(equity, now) -> bool` (mirrors `daily_halt` semantics):
```python
def kill(self, equity, now):
    today = now.date()
    if self._halted_day == today:        # sticky: stood down already today
        return True
    if self._day_start_equity is None:
        return False
    hit_floor = equity / self._day_start_equity - 1.0 <= -self.posture.loss_limit
    gave_back = (self.posture.give_back > 0
                 and equity / self._day_peak_equity - 1.0 <= -self.posture.give_back)
    if hit_floor or gave_back:
        self._halted_day = today
        return True
    return False
```

### 3. Kill path (`live/runner.py`)

- `_kill_check(now)`: `equity = gw.equity()`; `risk.roll_day(now, equity)`; if
  `risk.kill(equity, now)` → `flatten_all()`, return `(equity, True)`. Log
  `KILL_SWITCH` **once on the trip transition** (track `self._stood_down` bool);
  log `RESUME` when a new day clears the stand-down.
- Remove `self.halted` as the loop terminator. `resilient_loop`'s `should_halt`
  becomes `lambda: False` — the loop runs until the process is stopped
  externally (start_all.bat / OS), matching the passive book.
- `step()` returns early when killed (stays flat); flatten is idempotent
  (reconcile-to-0), so re-entry next day is automatic once `kill` returns False.
- Shared by `LiveRunner`, `PortfolioRunner`, `PairRunner` via `_kill_check`.

### 4. Tests

- Mechanical: `RoundCfg`→`Posture`, `RiskManager([], default)`→`RiskManager(posture)`
  in `test_risk.py`, `test_kill_flatten.py`, `test_vol_guard.py`, `test_calendar.py`.
- Drop `test_round_selection` (no rounds).
- Add to `test_risk.py`:
  - **Daily reset / auto-resume:** trip on day D (`kill` True), then `roll_day`
    into day D+1 with recovered equity → `kill` False (resumes).
  - **Sticky within day:** after a trip, `kill` stays True for the rest of D even
    if equity recovers above the floor intraday.
  - **give_back from day-peak:** with `give_back > 0`, trip on drawdown from the
    day's peak while still above the day-start floor.
  - **Floor from day-start:** baseline `loss_limit` trip.

## Out of scope

- Passive book (`run_passive_paper.py`, magic 2003): `lots` and `daily_loss_cap`
  stay fixed per user decision. No change.
- Position-sizing math, strategies, blackout, vol_guard, dashboard.
- Small-account ($1000) min-lot behaviour (separate concern; not addressed here).
- `docs/COMPETITION_PLAN.md` left as historical record.

## Risks

- **Behaviour change is real:** a kill no longer ends the process. Verify the bot
  keeps heartbeating after a simulated trip and resumes at the next UTC day. Net
  positive for `watchdog.py` (no more false "log stale" after a kill-exit), and
  `KILL_SWITCH` is still logged so the notification still fires.
- **Day boundary vs server time:** anchor uses true UTC `now.date()` (process
  clock), consistent across runners; not tied to broker server time, so no
  Lesson-#12 offset issue for the anchor itself.
- **Inception edge:** before the first `roll_day` of a session, `kill` returns
  False (no anchor yet) — first cycle sets it. Matches the existing
  "kill without round-start is safe" guarantee.

## Workflow

Branch `feat/standing-risk-posture` → TDD the new `kill`/`roll_day` →
rename + remove round plumbing → update runner kill path → migrate tests → full
suite → PR. Deploy: VPS `git pull`, then **relaunch the portfolio bot**
(RDP-launched). Passive bot untouched.
