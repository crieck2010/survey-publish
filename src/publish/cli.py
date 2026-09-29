"""Command-line interface: ``survey-publish``.

Commands:
    survey-publish connect <platform>
    survey-publish status
    survey-publish publish --platform <name> --video <mp4> --title "..."
        [--caption "..."] [--hashtags a,b,c] [--opt key=value ...]
    survey-publish publish-all --video <mp4> --title "..."
        [--caption "..."] [--hashtags a,b,c] [--opt key=value ...]
        [--platforms youtube,tiktok]

``--opt key=value`` feeds adapter-specific ``platform_options`` (repeatable).
Exit code is 0 on success, 1 on any failure; errors are one-line actionable
messages on stderr, never tracebacks.
"""
from __future__ import annotations

import argparse
import sys

from . import __version__
from .base import PublishError
from .models import PublishRequest, PublishResult
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
