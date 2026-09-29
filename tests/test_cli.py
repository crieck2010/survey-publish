"""Tests for the CLI: arg parsing, dispatch, publish-all aggregation."""
import pytest

from publish import cli
from publish.base import PublishError
from publish.models import PublishResult
from publish.registry import UnknownPlatformError
from publish.store import TokenStore
from .fakes import FakeSession


@pytest.fixture()
def video(tmp_path):
    path = tmp_path / "reel.mp4"
    path.write_bytes(b"\x00" * 8)
    return str(path)


def _parse(argv):
    return cli.build_parser().parse_args(argv)


def test_publish_args_parsing(video):
    args = _parse([
        "publish", "--platform", "youtube", "--video", video,
        "--title", "My reel", "--caption", "cap here",
        "--hashtags", "a,b,c", "--opt", "privacy=unlisted",
        "--opt", "category_id=28",
    ])
    assert args.command == "publish"
    assert args.platform == "youtube"
    assert args.video == video
    request = cli._build_request(args)
    assert request.hashtags == ["a", "b", "c"]
    assert request.platform_options == {"privacy": "unlisted", "category_id": "28"}


def test_publish_requires_platform():
    with pytest.raises(SystemExit):
        _parse(["publish", "--video", "x.mp4", "--title", "t"])


def test_publish_all_defaults_to_connected_subset():
    args = _parse(["publish-all", "--video", "x.mp4", "--title", "t"])
    assert args.platforms == ""


def test_bad_opt_format_errors():
    args = _parse(["publish", "--platform", "youtube", "--video", "x.mp4",
                   "--title", "t", "--opt", "noequals"])
    with pytest.raises(PublishError, match="KEY=VALUE"):
        cli._build_request(args)


def test_unknown_platform_cli_errors(capsys):
    code = cli.main(["publish", "--platform", "myspace", "--video", "x.mp4", "--title", "t"])
    assert code == 1
    assert "Valid platforms" in capsys.readouterr().err


def test_status_table_lists_all_platforms(capsys, tmp_path, monkeypatch):
    store = TokenStore(path=tmp_path / "tokens.json")
    monkeypatch.setattr("publish.cli.get_adapter",
                        lambda name, **kw: __import__("publish.registry", fromlist=["get_adapter"]).get_adapter(name, store=store, session=FakeSession()))
    code = cli.main(["status"])
    assert code == 0
    out = capsys.readouterr().out
    for name in ("YouTube", "Instagram", "Facebook", "TikTok"):
        assert name in out


def test_publish_all_aggregates_per_platform_results(capsys, tmp_path, monkeypatch, video):
    """publish-all must continue past one platform's failure and report both."""
    from publish import registry as reg

    class FailAdapter:
        name = "youtube"
        display_name = "YouTube"

        def __init__(self, *args, **kwargs):
            pass

        def is_connected(self):
            return True

        def publish(self, request):
            raise PublishError("boom: quota exceeded")

        def _not_connected_message(self):
            return "not connected"

    class OkAdapter:
        name = "tiktok"
        display_name = "TikTok"

        def __init__(self, *args, **kwargs):
            pass

        def is_connected(self):
            return True

        def publish(self, request):
            return PublishResult.success("tiktok", url_or_id="pid1")

        def _not_connected_message(self):
            return "not connected"

    monkeypatch.setattr(reg, "PLATFORMS", {"youtube": FailAdapter, "tiktok": OkAdapter})
    monkeypatch.setattr(cli, "list_platforms", lambda: ["youtube", "tiktok"])

    code = cli.main(["publish-all", "--video", video, "--title", "t"])
    assert code == 1
    out = capsys.readouterr().out
    assert "youtube: FAILED: boom: quota exceeded" in out
    assert "tiktok: OK -> pid1" in out


def test_publish_all_skips_unconnected_with_actionable_message(capsys, tmp_path, monkeypatch, video):
    from publish import registry as reg

    class NeverConnected:
        name = "youtube"
        display_name = "YouTube"

        def __init__(self, *args, **kwargs):
            pass

        def is_connected(self):
            return False

        def _not_connected_message(self):
            return "YouTube is not connected. Run `survey-publish connect youtube`."

    monkeypatch.setattr(reg, "PLATFORMS", {"youtube": NeverConnected})
    monkeypatch.setattr(cli, "list_platforms", lambda: ["youtube"])

    code = cli.main(["publish-all", "--video", video, "--title", "t"])
    assert code == 1
    out = capsys.readouterr().out
    assert "survey-publish connect youtube" in out


def test_connect_unknown_platform_errors(capsys):
    code = cli.main(["connect", "nope"])
    assert code == 1
    assert "Valid platforms" in capsys.readouterr().err
