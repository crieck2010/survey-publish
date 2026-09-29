"""Command-line interface: ``survey-publish``.

Commands:
    survey-publish connect <platform>
    survey-publish status
    survey-publish publish --platform <name> --video <mp4> --title "..."
        [--caption "..."] [--hashtags a,b,c] [--opt key=value ...]
    survey-publish publish-all --video <mp4> --title "..."
        [--caption "..."] [--hashtags a,b,c] [--opt key=value ...]
        [--platforms youtube,tiktok]
    survey-publish schedule --video <mp4> --at "12:30" --platforms youtube,tiktok
        --title "..." [--caption "..."] [--hashtags a,b,c] [--date YYYY-MM-DD]
    survey-publish queue [--status queued]   # list scheduled items
    survey-publish queue-cancel <id>         # cancel a scheduled item
    survey-publish queue-reschedule <id> --at "18:30"
    survey-publish tick                      # publish everything due now

``--opt key=value`` feeds adapter-specific ``platform_options`` (repeatable).
Exit code is 0 on success, 1 on any failure; errors are one-line actionable
messages on stderr, never tracebacks. ``tick`` exits 0 unless it fails
catastrophically; per-item publish failures are recorded in the queue.
"""
from __future__ import annotations

import argparse
import os
import sys

from . import __version__
from .base import PublishError
from .models import PublishRequest, PublishResult
from .queue import QueuedItem, QueueStore, parse_schedule_time, tick
from .registry import UnknownPlatformError, get_adapter, list_platforms


def _add_publish_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--video", required=True, help="Path to the finished MP4 file.")
    parser.add_argument("--title", required=True, help="Video/post title.")
    parser.add_argument("--caption", default="", help="Body text (hashtags appended automatically).")
    parser.add_argument(
        "--hashtags",
        default="",
        help="Comma-separated hashtags without '#', e.g. 'surveying,maps,reels'.",
    )
    parser.add_argument(
        "--opt",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Adapter-specific option (repeatable), e.g. --opt privacy=unlisted.",
    )


def _parse_opts(opt_list: list) -> dict:
    options = {}
    for item in opt_list:
        if "=" not in item:
            raise PublishError(
                f"Bad --opt {item!r}: expected KEY=VALUE (e.g. --opt privacy=unlisted)."
            )
        key, value = item.split("=", 1)
        key = key.strip()
        if not key:
            raise PublishError(f"Bad --opt {item!r}: empty key.")
        options[key] = value
    return options


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="survey-publish",
        description="Publish finished MP4 reels to YouTube, Instagram, Facebook, and TikTok.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    c = sub.add_parser("connect", help="Connect a platform via OAuth (stores tokens locally).")
    c.add_argument("platform", help=f"Platform name: {', '.join(list_platforms())}.")

    sub.add_parser("status", help="Show connection status for every platform.")

    p = sub.add_parser("publish", help="Publish a video to one platform.")
    p.add_argument(
        "--platform", required=True, help=f"Platform name: {', '.join(list_platforms())}."
    )
    _add_publish_args(p)

    pa = sub.add_parser(
        "publish-all", help="Publish a video to every connected platform."
    )
    pa.add_argument(
        "--platforms",
        default="",
        help="Comma-separated subset of platforms (default: all connected).",
    )
    _add_publish_args(pa)

    d = sub.add_parser("disconnect", help="Delete stored tokens for a platform.")
    d.add_argument("platform", help=f"Platform name: {', '.join(list_platforms())}.")

    s = sub.add_parser("schedule", help="Schedule a video for later publishing.")
    _add_publish_args(s)
    s.add_argument(
        "--platforms",
        required=True,
        help="Comma-separated platforms, e.g. 'youtube,instagram,tiktok'.",
    )
    s.add_argument(
        "--at",
        required=True,
        help="When to publish: 'HH:MM' (next occurrence, rolls to tomorrow if "
        "passed), 'YYYY-MM-DD HH:MM', or full ISO-8601. Combine with --date "
        "for a calendar day.",
    )
    s.add_argument(
        "--date",
        default="",
        help="Optional calendar day YYYY-MM-DD, combined with --at HH:MM.",
    )

    q = sub.add_parser(
        "queue",
        help="List scheduled items ('queue cancel <id>' cancels one).",
    )
    q.add_argument(
        "--status",
        default="queued",
        help="Filter by status: queued, published, failed, canceled, all "
        "(default: queued).",
    )
    q.add_argument(
        "action",
        nargs="?",
        default=None,
        help="Optional action: 'cancel'.",
    )
    q.add_argument("id", nargs="?", default=None, help="Item id for 'cancel'.")

    qc = sub.add_parser(
        "queue-cancel", help="Cancel a scheduled item (same as 'queue cancel')."
    )
    qc.add_argument("id", help="Item id (first 8 characters are enough).")

    qr = sub.add_parser("queue-reschedule", help="Move a queued item to a new time.")
    qr.add_argument("id", help="Item id (first 8 characters are enough).")
    qr.add_argument(
        "--at",
        required=True,
        help="New time: 'HH:MM', 'YYYY-MM-DD HH:MM', or full ISO-8601.",
    )
    qr.add_argument(
        "--date",
        default="",
        help="Optional calendar day YYYY-MM-DD, combined with --at HH:MM.",
    )

    sub.add_parser(
        "tick",
        help="Publish everything due now. Quiet when nothing is due; "
        "designed for Windows Task Scheduler every 15 minutes.",
    )

    return parser


def _build_request(args: argparse.Namespace) -> PublishRequest:
    hashtags = [h.strip() for h in (args.hashtags or "").split(",") if h.strip()]
    return PublishRequest(
        video_path=args.video,
        title=args.title,
        caption=args.caption or "",
        hashtags=hashtags,
        platform_options=_parse_opts(args.opt or []),
    )


def cmd_connect(args: argparse.Namespace) -> int:
    adapter = get_adapter(args.platform)
    adapter.connect()
    return 0


def cmd_disconnect(args: argparse.Namespace) -> int:
    adapter = get_adapter(args.platform)
    adapter.disconnect()
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    rows = []
    for name in list_platforms():
        adapter = get_adapter(name)
        try:
            connected = adapter.is_connected()
        except Exception:
            connected = False
        label = adapter.display_name or name
        account = adapter.account_label() if connected else ""
        rows.append((label, "yes" if connected else "no", account))
    width = max(len(r[0]) for r in rows)
    print(f"{'Platform'.ljust(width)}  Connected  Account")
    for label, connected, account in rows:
        print(f"{label.ljust(width)}  {connected.ljust(9)}  {account}")
    return 0


def _report_result(result: PublishResult) -> None:
    if result.ok:
        print(f"{result.platform}: OK -> {result.url_or_id}")
    else:
        print(f"{result.platform}: FAILED: {result.error}")


def cmd_publish(args: argparse.Namespace) -> int:
    request = _build_request(args)
    adapter = get_adapter(args.platform)
    result = adapter.publish(request)  # raises PublishError on failure
    _report_result(result)
    return 0


def cmd_publish_all(args: argparse.Namespace) -> int:
    request = _build_request(args)
    wanted = [p.strip().lower() for p in (args.platforms or "").split(",") if p.strip()]
    if wanted:
        for name in wanted:
            get_adapter(name)  # validate early; raises UnknownPlatformError
        names = wanted
    else:
        names = list_platforms()

    results: "list[PublishResult]" = []
    for name in names:
        adapter = get_adapter(name)
        try:
            connected = adapter.is_connected()
        except Exception:
            connected = False
        if not connected:
            results.append(
                PublishResult.failure(name, adapter._not_connected_message())
            )
            continue
        try:
            results.append(adapter.publish(request))
        except Exception as exc:  # per-platform failure must not abort the run
            results.append(PublishResult.failure(name, str(exc)))

    failed = 0
    for result in results:
        _report_result(result)
        if not result.ok:
            failed += 1
    if failed:
        print(f"\n{failed} of {len(results)} platform(s) failed.", file=sys.stderr)
        return 1
    print(f"\nPublished to all {len(results)} platform(s).")
    return 0


def _resolve_scheduled_at(at_text: str, date_text: str):
    """Combine --at/--date into a tz-aware local datetime."""
    at_text = (at_text or "").strip()
    date_text = (date_text or "").strip()
    if date_text:
        from datetime import datetime as _dt

        try:
            _dt.strptime(date_text, "%Y-%m-%d")
        except ValueError:
            raise PublishError(
                f"Bad --date {date_text!r}: expected YYYY-MM-DD (e.g. 2026-09-29)."
            )
        import re as _re

        if not _re.fullmatch(r"\d{1,2}:\d{2}", at_text):
            raise PublishError(
                f"--date {date_text!r} needs --at as HH:MM (got {at_text!r})."
            )
        return parse_schedule_time(f"{date_text} {at_text}")
    return parse_schedule_time(at_text)  # raises ValueError with guidance


def _split_platforms(platforms_text: str) -> "list[str]":
    names = [p.strip().lower() for p in (platforms_text or "").split(",") if p.strip()]
    if not names:
        raise PublishError(
            "Provide at least one platform, e.g. --platforms youtube,instagram,tiktok."
        )
    for name in names:
        get_adapter(name)  # validate early; raises UnknownPlatformError
    return names


def _resolve_id(store: QueueStore, prefix: str) -> str:
    """Resolve a full id or an unambiguous id prefix to the full id."""
    prefix = (prefix or "").strip()
    if not prefix:
        raise PublishError("An item id is required.")
    direct = store.get(prefix)
    if direct is not None:
        return direct.id
    matches = [i.id for i in store.list_queue() if i.id.startswith(prefix)]
    if not matches:
        raise PublishError(
            f"No queued item matches id {prefix!r}. "
            "Run `survey-publish queue` to see scheduled items."
        )
    if len(matches) > 1:
        raise PublishError(
            f"Id prefix {prefix!r} is ambiguous ({len(matches)} matches). "
            "Use more characters."
        )
    return matches[0]


def cmd_schedule(args: argparse.Namespace) -> int:
    when = _resolve_scheduled_at(args.at, args.date)
    platforms = _split_platforms(args.platforms)
    hashtags = [h.strip() for h in (args.hashtags or "").split(",") if h.strip()]
    if args.video and not os.path.isfile(args.video):
        print(
            f"Warning: video file {args.video!r} does not exist yet. It must "
            "exist when the item is due, or the publish will be recorded as "
            "failed.",
            file=sys.stderr,
        )
    item = QueuedItem(
        video_path=args.video,
        title=args.title,
        caption=args.caption or "",
        hashtags=hashtags,
        platforms=platforms,
        scheduled_at=when.isoformat(),
        platform_options=_parse_opts(args.opt or []),
    )
    item_id = QueueStore().enqueue(item)
    print(
        f"Queued {item_id[:8]}: {args.title!r} -> "
        f"{when.strftime('%Y-%m-%d %H:%M %Z')} ({', '.join(platforms)})"
    )
    print(f"Full id: {item_id}")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    store = QueueStore()
    action = (args.action or "").strip().lower()
    if action:
        if action != "cancel":
            raise PublishError(
                f"Unknown queue action {args.action!r}. "
                "Try `survey-publish queue cancel <id>`."
            )
        if not args.id:
            raise PublishError("`queue cancel` needs an item id.")
        return _cmd_cancel(store, _resolve_id(store, args.id))
    items = store.list_queue(status_filter=(args.status or "all"))
    if not items:
        label = args.status or "all"
        print(f"No items with status {label!r}." if label != "all" else "Queue is empty.")
        return 0
    print(f"{'ID'.ljust(8)}  {'Scheduled (local)'.ljust(17)}  {'Platforms'.ljust(24)}  {'Status'.ljust(9)}  Title")
    for item in items:
        dt = item.scheduled_dt
        when = dt.strftime("%Y-%m-%d %H:%M") if dt else "?"
        platforms = ",".join(item.platforms)
        title = item.title if len(item.title) <= 40 else item.title[:37] + "..."
        print(
            f"{item.id[:8].ljust(8)}  {when.ljust(17)}  "
            f"{platforms[:24].ljust(24)}  {item.status.ljust(9)}  {title}"
        )
    return 0


def _cmd_cancel(store: QueueStore, item_id: str) -> int:
    store.cancel(item_id)  # raises ValueError with guidance
    print(f"Canceled {item_id[:8]}.")
    return 0


def cmd_queue_cancel(args: argparse.Namespace) -> int:
    store = QueueStore()
    return _cmd_cancel(store, _resolve_id(store, args.id))


def cmd_queue_reschedule(args: argparse.Namespace) -> int:
    store = QueueStore()
    item_id = _resolve_id(store, args.id)
    when = _resolve_scheduled_at(args.at, args.date)
    store.reschedule(item_id, when.isoformat())  # raises ValueError with guidance
    print(f"Rescheduled {item_id[:8]} -> {when.strftime('%Y-%m-%d %H:%M %Z')}.")
    return 0


def cmd_tick(args: argparse.Namespace) -> int:
    store = QueueStore()
    try:
        summary = tick(store)
    except Exception as exc:  # catastrophic: store unreadable, etc.
        print(f"Error: tick failed: {exc}", file=sys.stderr)
        return 1
    if summary["processed"]:
        print(
            f"tick: {summary['processed']} due item(s): "
            f"{summary['published']} published, {summary['failed']} failed."
        )
    return 0


def main(argv: "list[str] | None" = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "connect":
            return cmd_connect(args)
        if args.command == "disconnect":
            return cmd_disconnect(args)
        if args.command == "status":
            return cmd_status(args)
        if args.command == "publish":
            return cmd_publish(args)
        if args.command == "publish-all":
            return cmd_publish_all(args)
        if args.command == "schedule":
            return cmd_schedule(args)
        if args.command == "queue":
            return cmd_queue(args)
        if args.command == "queue-cancel":
            return cmd_queue_cancel(args)
        if args.command == "queue-reschedule":
            return cmd_queue_reschedule(args)
        if args.command == "tick":
            return cmd_tick(args)
        parser.error(f"unknown command {args.command!r}")
        return 2
    except (PublishError, UnknownPlatformError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
