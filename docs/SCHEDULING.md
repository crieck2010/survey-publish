# Scheduling & the publish queue — survey-publish 0.2.0

Publish later instead of now: `schedule` puts a reel in a local queue,
`tick` publishes everything whose time has come. The intended setup is
`tick` running every 15 minutes from Windows Task Scheduler, so scheduled
posts go out while you do anything else.

## The model

- **Queue file**: `~/.survey-publish/queue.json` (0600, atomic writes — same
  conventions as the token store). A corrupt file is quarantined to
  `queue.json.corrupt-<epoch>` and the queue starts empty rather than
  crashing.
- **One item** = one video + title/caption/hashtags + a platform list + a
  scheduled time. Times are always **the PC's local timezone** and are stored
  as ISO-8601 *with* the local UTC offset
  (`"2026-09-29T12:30:00-04:00"`).
- **Statuses**: `queued` → `published` | `failed` → (manual) `canceled`.
  `publishing` is a transient guard inside `tick()` so two overlapping runs
  can't double-publish the same item.
- **No silent retries.** If any platform fails, the item is marked `failed`
  with the per-platform errors recorded in `last_error` and `attempts`
  incremented. URLs of platforms that *did* succeed are kept in
  `published_urls` so a manual requeue can skip them.
- **Instagram caveat**: scheduled Instagram posts need the same
  `video_url` platform option as immediate ones — pass
  `--opt video_url=https://...` at schedule time.

## CLI

```bash
# Schedule for the next 12:30 (rolls to tomorrow if 12:30 already passed)
survey-publish schedule --video reel.mp4 --title "Gulf Stream this week" \
  --at "12:30" --platforms youtube,instagram,tiktok \
  --caption "Weekly ocean-current update." --hashtags "oceans,surveying"

# ... or a specific calendar slot
survey-publish schedule --video reel.mp4 --title "T" \
  --at "18:30" --date 2026-10-01 --platforms youtube,tiktok

# ... or a full timestamp
survey-publish schedule --video reel.mp4 --title "T" \
  --at "2026-10-01T18:30:00-04:00" --platforms youtube

# List what's scheduled (default filter: queued)
survey-publish queue
survey-publish queue --status failed     # what needs attention
survey-publish queue --status all

# Cancel (both forms work; first 8 id characters are enough)
survey-publish queue-cancel a1b2c3d4
survey-publish queue cancel a1b2c3d4

# Move a queued item to a new time
survey-publish queue-reschedule a1b2c3d4 --at "18:30"

# Publish everything due right now
survey-publish tick
```

`tick` prints one summary line when it processes anything and nothing at all
when the queue has nothing due. It exits `0` unless something catastrophic
happens (e.g. the queue file is unreadable); per-item publish failures are
recorded in the queue, not in the exit code.

## Time formats (`--at`)

| Format | Meaning |
|---|---|
| `"08:30"` | Next 08:30 local. If 08:30 today already passed (or is right now), tomorrow. |
| `"2026-10-01 18:30"` | That calendar day, 18:30 local. |
| `"2026-10-01T18:30:00-04:00"` | Exact instant. A naive value (no offset) is assumed local. |

The module constant `DEFAULT_SLOTS = ["08:30", "12:30", "18:30"]` holds
research-backed default slots (morning commute scroll, midday break, evening
wind-down) — embedders can offer these as preset buttons; any custom time
always works.

## Windows Task Scheduler setup

Run `survey-publish tick` every 15 minutes so due items publish within a
quarter hour of their slot:

```bat
:: Create the task (runs every 15 minutes, indefinitely)
schtasks /Create /TN "survey-publish tick" /TR "survey-publish tick" ^
  /SC MINUTE /MO 15 /F
```

Or via the GUI: Task Scheduler → Create Basic Task → Trigger: Daily, repeat
every 15 minutes → Action: Start a program → `survey-publish` with arguments
`tick`. Point "Start in" at anything; the queue lives at
`%USERPROFILE%\.survey-publish\queue.json` regardless of working directory.

Notes:

- The command must be on `PATH` (i.e. `pip install survey-publish` in the
  Python that owns the scheduled task's environment). If you use a venv,
  point `/TR` at the venv's `survey-publish.exe`.
- Check on it occasionally: `schtasks /Query /TN "survey-publish tick"`.
- To remove: `schtasks /Delete /TN "survey-publish tick" /F`.

## Manual requeue after a failure

```bash
survey-publish queue --status failed     # see what failed and why
# partial failure? note published_urls, then re-schedule ONLY the platforms
# that failed, so the ones that went out are not double-published:
survey-publish schedule --video reel.mp4 --title "T" \
  --at "12:30" --platforms tiktok
survey-publish queue-cancel <failed-id>  # retire the old item
```

`queue-reschedule` works on `queued` items; `queue-cancel` works on `queued`
and `failed` items.

## Honest limits

- **The PC must be ON and AWAKE at publish time.** Sleep/hibernate misses
  slots — a due item simply waits until the next `tick` run while the
  machine is awake. There is no cloud fallback in this engine.
- **Times are the PC's local timezone.** Move timezones and the stored
  offsets still mean the same absolute instant (they were recorded with the
  offset), but new `"HH:MM"` schedules follow wherever the PC is now.
- **Failed items need manual requeue** (cancel + schedule, or reschedule for
  queued items). Nothing retries on its own — by design, so a half-published
  item never silently double-posts.
- **One writer.** The queue file has no locking; don't run two `tick`
  loops against the same file. The transient `publishing` status guards
  against accidental overlap, but it is a guard, not a lock.
- **YouTube quota still applies** (~1,600 units/upload against 10,000/day) —
  scheduling doesn't create quota.
