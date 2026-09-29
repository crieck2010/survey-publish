"""Tests for the publish queue: parse_schedule_time, QueueStore, tick, CLI."""
from datetime import datetime, timedelta

import pytest

from publish import (
    DEFAULT_SLOTS,
    QueuedItem,
    QueueStore,
    parse_schedule_time,
    tick,
)
from publish import cli
from .fakes import FakePublisher, make_fake_registry


@pytest.fixture()
def store(tmp_path):
    return QueueStore(path=tmp_path / "queue.json")


@pytest.fixture()
def now():
    return datetime.now().astimezone()


def _item(**overrides):
    base = dict(
        video_path="/tmp/reel.mp4",
        title="Gulf Stream this week",
        caption="Weekly update.",
        hashtags=["oceans", "surveying"],
        platforms=["youtube", "tiktok"],
        scheduled_at=(datetime.now().astimezone() - timedelta(hours=1)).isoformat(),
        platform_options={"privacy": "public"},
    )
    base.update(overrides)
    return QueuedItem(**base)


# -- DEFAULT_SLOTS ----------------------------------------------------------

def test_default_slots_documented():
    assert DEFAULT_SLOTS == ["08:30", "12:30", "18:30"]


# -- parse_schedule_time ----------------------------------------------------

def test_parse_hhmm_future_today(now):
    base = now.replace(hour=6, minute=0, second=0, microsecond=0)
    dt = parse_schedule_time("12:30", now=base)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (
        base.year, base.month, base.day, 12, 30)
    assert dt.tzinfo is not None


def test_parse_hhmm_past_rolls_to_tomorrow(now):
    base = now.replace(hour=23, minute=0, second=0, microsecond=0)
    dt = parse_schedule_time("08:30", now=base)
    nxt = (base + timedelta(days=1))
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (
        nxt.year, nxt.month, nxt.day, 8, 30)


def test_parse_hhmm_right_now_rolls_to_tomorrow(now):
    base = now.replace(hour=12, minute=30, second=0, microsecond=0)
    dt = parse_schedule_time("12:30", now=base)
    assert dt.date() == (base + timedelta(days=1)).date()


def test_parse_calendar_datetime(now):
    dt = parse_schedule_time("2026-10-05 18:30")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 10, 5, 18, 30)
    assert dt.tzinfo is not None
    # naive input is assumed local
    assert dt.utcoffset() == now.utcoffset()


def test_parse_iso_with_offset_preserved():
    dt = parse_schedule_time("2026-09-29T12:30:00-04:00")
    assert dt.utcoffset() == timedelta(hours=-4)
    assert (dt.hour, dt.minute) == (12, 30)


def test_parse_naive_iso_assumed_local(now):
    dt = parse_schedule_time("2026-09-29T12:30:00")
    assert dt.utcoffset() == now.utcoffset()


@pytest.mark.parametrize("bad", ["", "   ", "not a time", "25:00", "12:60", "12:30:45"])
def test_parse_bad_input_raises_valueerror(bad, now):
    with pytest.raises(ValueError, match="HH:MM|parse|Bad time"):
        parse_schedule_time(bad, now=now)


# -- QueueStore round-trip --------------------------------------------------

def test_enqueue_get_round_trip(store):
    item_id = store.enqueue(_item())
    assert len(item_id) == 32  # uuid4 hex
    got = store.get(item_id)
    assert got is not None
    assert got.title == "Gulf Stream this week"
    assert got.caption == "Weekly update."
    assert got.hashtags == ["oceans", "surveying"]
    assert got.platforms == ["youtube", "tiktok"]
    assert got.status == "queued"
    assert got.attempts == 0
    assert got.platform_options == {"privacy": "public"}
    # scheduled_at normalized to ISO with offset
    assert datetime.fromisoformat(got.scheduled_at).tzinfo is not None


def test_enqueue_normalizes_platforms(store):
    item_id = store.enqueue(_item(platforms=["YouTube", " tiktok ", "youtube"]))
    assert store.get(item_id).platforms == ["youtube", "tiktok"]


def test_enqueue_does_not_mutate_caller(store):
    item = _item(platforms=["YouTube"])
    store.enqueue(item)
    assert item.platforms == ["YouTube"]
    assert item.status == "queued"


def test_enqueue_forces_queued_status(store):
    item_id = store.enqueue(_item(status="published"))
    assert store.get(item_id).status == "queued"


@pytest.mark.parametrize("kwargs", [
    {"video_path": ""},
    {"title": ""},
    {"platforms": []},
    {"scheduled_at": "not-a-time"},
    {"scheduled_at": ""},
])
def test_enqueue_validation(store, kwargs):
    with pytest.raises(ValueError):
        store.enqueue(_item(**kwargs))


def test_get_unknown_returns_none(store):
    assert store.get("nope") is None


def test_list_queue_sorted_and_filtered(store, now):
    t0 = now - timedelta(hours=2)
    t1 = now - timedelta(hours=1)
    t2 = now + timedelta(hours=1)
    id_b = store.enqueue(_item(title="b", scheduled_at=t2.isoformat()))
    id_a = store.enqueue(_item(title="a", scheduled_at=t0.isoformat()))
    id_c = store.enqueue(_item(title="c", scheduled_at=t1.isoformat()))
    titles = [i.title for i in store.list_queue()]
    assert titles == ["a", "c", "b"]
    assert [i.title for i in store.list_queue(status_filter="queued")] == ["a", "c", "b"]
    assert store.list_queue(status_filter="published") == []
    store.mark_published(id_a, {"youtube": "u"})
    assert [i.title for i in store.list_queue(status_filter="published")] == ["a"]
    assert [i.title for i in store.list_queue(status_filter="all")] == ["a", "c", "b"]


def test_due_items(store, now):
    past = store.enqueue(_item(title="past",
                                scheduled_at=(now - timedelta(minutes=5)).isoformat()))
    future = store.enqueue(_item(title="future",
                                 scheduled_at=(now + timedelta(hours=2)).isoformat()))
    due = store.due_items(now)
    assert [i.id for i in due] == [past]
    store.cancel(past)
    assert store.due_items(now) == []


def test_cancel(store):
    item_id = store.enqueue(_item())
    store.cancel(item_id)
    assert store.get(item_id).status == "canceled"
    store.cancel(item_id)  # idempotent no-op
    assert store.get(item_id).status == "canceled"
    with pytest.raises(ValueError, match="No queued item"):
        store.cancel("missing")
    store.mark_published(item_id2 := store.enqueue(_item()), {})
    with pytest.raises(ValueError, match="Cannot cancel"):
        store.cancel(item_id2)


def test_cancel_failed_item_allowed(store):
    item_id = store.enqueue(_item())
    store.mark_failed(item_id, "boom")
    store.cancel(item_id)
    assert store.get(item_id).status == "canceled"


def test_reschedule(store, now):
    item_id = store.enqueue(_item())
    new = (now + timedelta(days=1)).replace(microsecond=0)
    store.reschedule(item_id, new.isoformat())
    assert store.get(item_id).scheduled_at == new.isoformat()
    assert store.get(item_id).status == "queued"
    with pytest.raises(ValueError, match="Bad scheduled_at|ISO"):
        store.reschedule(item_id, "junk")
    with pytest.raises(ValueError, match="No queued item"):
        store.reschedule("missing", new.isoformat())
    store.mark_failed(item_id, "boom")
    with pytest.raises(ValueError, match="only queued"):
        store.reschedule(item_id, new.isoformat())


def test_mark_published_and_failed(store):
    item_id = store.enqueue(_item())
    store.mark_published(item_id, {"youtube": "https://youtu.be/x"})
    got = store.get(item_id)
    assert got.status == "published"
    assert got.published_urls == {"youtube": "https://youtu.be/x"}
    assert got.last_error == ""

    item2 = store.enqueue(_item())
    store.mark_failed(item2, "youtube: boom")
    store.mark_failed(item2, "youtube: boom again")
    got2 = store.get(item2)
    assert got2.status == "failed"
    assert got2.attempts == 2
    assert got2.last_error == "youtube: boom again"


def test_queue_file_permissions(store, tmp_path):
    store.enqueue(_item())
    import os
    import stat

    mode = stat.S_IMODE(os.stat(tmp_path / "queue.json").st_mode)
    assert mode == 0o600


def test_corrupt_queue_file_quarantined(store, tmp_path):
    path = tmp_path / "queue.json"
    store.enqueue(_item())
    path.write_text("{not valid json", encoding="utf-8")
    assert store.list_queue() == []  # starts empty, no crash
    backups = list(tmp_path.glob("queue.json.corrupt-*"))
    assert len(backups) == 1
    assert "{not valid json" in backups[0].read_text(encoding="utf-8")


# -- tick -------------------------------------------------------------------

def _registry(ok=("youtube",), fail=()):
    adapters = {name: FakePublisher(name=name) for name in ok}
    adapters.update({name: FakePublisher(name=name, fail=True) for name in fail})
    return make_fake_registry(adapters), adapters


def test_tick_all_success(store, now):
    registry, adapters = _registry(ok=("youtube", "tiktok"))
    past = (now - timedelta(minutes=1)).isoformat()
    item_id = store.enqueue(_item(platforms=["youtube", "tiktok"], scheduled_at=past))
    summary = tick(store, registry, now=now)
    assert summary == {"processed": 1, "published": 1, "failed": 0}
    got = store.get(item_id)
    assert got.status == "published"
    assert got.published_urls == {
        "youtube": "https://example.com/youtube/vid1",
        "tiktok": "https://example.com/tiktok/vid1",
    }
    assert got.attempts == 0
    # request built from the item
    req = adapters["youtube"].published[0]
    assert req.title == "Gulf Stream this week"
    assert req.hashtags == ["oceans", "surveying"]
    assert req.platform_options == {"privacy": "public"}
    assert req.video_path == "/tmp/reel.mp4"


def test_tick_partial_failure_keeps_successful_urls(store, now):
    registry, adapters = _registry(ok=("youtube",), fail=("tiktok",))
    past = (now - timedelta(minutes=1)).isoformat()
    item_id = store.enqueue(_item(platforms=["youtube", "tiktok"], scheduled_at=past))
    summary = tick(store, registry, now=now)
    assert summary == {"processed": 1, "published": 0, "failed": 1}
    got = store.get(item_id)
    assert got.status == "failed"
    assert got.attempts == 1
    assert "tiktok" in got.last_error and "boom" in got.last_error
    # the platform that succeeded is remembered: manual requeue can skip it
    assert got.published_urls == {"youtube": "https://example.com/youtube/vid1"}
    assert adapters["youtube"].publish_calls == 1
    assert adapters["tiktok"].publish_calls == 1


def test_tick_skips_non_queued(store, now):
    registry, _ = _registry(ok=("youtube",))
    past = (now - timedelta(minutes=1)).isoformat()
    published = store.enqueue(_item(scheduled_at=past))
    store.mark_published(published, {"youtube": "u"})
    canceled = store.enqueue(_item(scheduled_at=past))
    store.cancel(canceled)
    in_flight = store.enqueue(_item(scheduled_at=past))
    store._set_status(in_flight, "publishing")  # simulated concurrent tick
    summary = tick(store, registry, now=now)
    assert summary == {"processed": 0, "published": 0, "failed": 0}
    assert store.get(published).status == "published"
    assert store.get(canceled).status == "canceled"


def test_tick_unknown_platform_recorded_not_raised(store, now):
    registry, _ = _registry(ok=("youtube",))
    past = (now - timedelta(minutes=1)).isoformat()
    item_id = store.enqueue(_item(platforms=["myspace"], scheduled_at=past))
    summary = tick(store, registry, now=now)  # must not raise
    assert summary["failed"] == 1
    got = store.get(item_id)
    assert got.status == "failed"
    assert "myspace" in got.last_error


def test_tick_nothing_due(store, now):
    registry, adapters = _registry()
    store.enqueue(_item(scheduled_at=(now + timedelta(hours=1)).isoformat()))
    summary = tick(store, registry, now=now)
    assert summary == {"processed": 0, "published": 0, "failed": 0}
    assert adapters["youtube"].publish_calls == 0


def test_tick_multiple_items_oldest_first(store, now):
    registry, adapters = _registry()
    older = store.enqueue(_item(title="older", platforms=["youtube"],
                                scheduled_at=(now - timedelta(hours=2)).isoformat()))
    newer = store.enqueue(_item(title="newer", platforms=["youtube"],
                                scheduled_at=(now - timedelta(hours=1)).isoformat()))
    tick(store, registry, now=now)
    assert store.get(older).status == "published"
    assert store.get(newer).status == "published"
    assert adapters["youtube"].publish_calls == 2


# -- CLI --------------------------------------------------------------------

def _patch_queue_store(monkeypatch, tmp_path):
    path = tmp_path / "queue.json"

    def factory(*args, **kwargs):
        return QueueStore(path=path)

    monkeypatch.setattr(cli, "QueueStore", factory)
    return path


def _parse(argv):
    return cli.build_parser().parse_args(argv)


def test_schedule_args_parsing(tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 8)
    args = _parse(["schedule", "--video", str(video), "--title", "T",
                   "--at", "12:30", "--platforms", "youtube,tiktok",
                   "--caption", "cap", "--hashtags", "a,b",
                   "--opt", "privacy=unlisted"])
    assert args.command == "schedule"
    assert args.at == "12:30"
    assert args.platforms == "youtube,tiktok"
    assert args.opt == ["privacy=unlisted"]


def test_schedule_end_to_end(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 8)
    code = cli.main(["schedule", "--video", str(video), "--title", "My reel",
                     "--at", "23:59", "--platforms", "youtube,tiktok"])
    assert code == 0
    out = capsys.readouterr().out
    assert "Queued" in out and "My reel" in out and "23:59" in out
    assert "Full id:" in out


def test_schedule_with_date_and_opt(capsys, tmp_path, monkeypatch):
    path = _patch_queue_store(monkeypatch, tmp_path)
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"\x00" * 8)
    code = cli.main(["schedule", "--video", str(video), "--title", "T",
                     "--at", "08:30", "--date", "2026-10-01",
                     "--platforms", "youtube",
                     "--opt", "privacy=unlisted"])
    assert code == 0
    store = QueueStore(path=path)
    item = store.list_queue()[0]
    assert item.scheduled_at.startswith("2026-10-01T08:30")
    assert item.platform_options == {"privacy": "unlisted"}


def test_schedule_warns_on_missing_video(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    code = cli.main(["schedule", "--video", str(tmp_path / "nope.mp4"),
                     "--title", "T", "--at", "12:30",
                     "--platforms", "youtube"])
    assert code == 0  # warning, not an error: file may be rendered later
    assert "Warning" in capsys.readouterr().err


def test_schedule_bad_platform_errors(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    code = cli.main(["schedule", "--video", "x.mp4", "--title", "T",
                     "--at", "12:30", "--platforms", "myspace"])
    assert code == 1
    assert "Valid platforms" in capsys.readouterr().err


def test_schedule_bad_time_errors(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    code = cli.main(["schedule", "--video", "x.mp4", "--title", "T",
                     "--at", "someday", "--platforms", "youtube"])
    assert code == 1
    assert "HH:MM" in capsys.readouterr().err


def test_schedule_bad_date_errors(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    code = cli.main(["schedule", "--video", "x.mp4", "--title", "T",
                     "--at", "12:30", "--date", "10/01/2026",
                     "--platforms", "youtube"])
    assert code == 1
    assert "YYYY-MM-DD" in capsys.readouterr().err


def _enqueue_cli(monkeypatch, tmp_path, **kw):
    path = _patch_queue_store(monkeypatch, tmp_path)
    store = QueueStore(path=path)
    base = dict(video_path="r.mp4", title="Reel", platforms=["youtube"],
                scheduled_at=(datetime.now().astimezone()
                              - timedelta(minutes=1)).isoformat())
    base.update(kw)
    return store, store.enqueue(QueuedItem(**base))


def test_queue_lists_table(capsys, tmp_path, monkeypatch):
    store, item_id = _enqueue_cli(monkeypatch, tmp_path, title="My reel")
    code = cli.main(["queue"])
    assert code == 0
    out = capsys.readouterr().out
    assert item_id[:8] in out and "My reel" in out and "queued" in out


def test_queue_status_filter(capsys, tmp_path, monkeypatch):
    store, item_id = _enqueue_cli(monkeypatch, tmp_path)
    code = cli.main(["queue", "--status", "published"])
    assert code == 0
    assert "No items" in capsys.readouterr().out
    code = cli.main(["queue", "--status", "all"])
    assert code == 0
    assert item_id[:8] in capsys.readouterr().out


def test_queue_cancel_both_forms(capsys, tmp_path, monkeypatch):
    store, item_id = _enqueue_cli(monkeypatch, tmp_path)
    code = cli.main(["queue-cancel", item_id[:8]])
    assert code == 0
    assert store.get(item_id).status == "canceled"

    store2, item_id2 = _enqueue_cli(monkeypatch, tmp_path)
    code = cli.main(["queue", "cancel", item_id2])
    assert code == 0
    assert store2.get(item_id2).status == "canceled"


def test_queue_cancel_unknown_id_errors(capsys, tmp_path, monkeypatch):
    _enqueue_cli(monkeypatch, tmp_path)
    code = cli.main(["queue-cancel", "deadbeef"])
    assert code == 1
    assert "No queued item" in capsys.readouterr().err


def test_queue_reschedule(capsys, tmp_path, monkeypatch):
    store, item_id = _enqueue_cli(monkeypatch, tmp_path)
    code = cli.main(["queue-reschedule", item_id[:8], "--at", "18:30",
                     "--date", "2026-10-02"])
    assert code == 0
    assert store.get(item_id).scheduled_at.startswith("2026-10-02T18:30")
    assert "Rescheduled" in capsys.readouterr().out


def test_tick_cli_quiet_on_empty(capsys, tmp_path, monkeypatch):
    _patch_queue_store(monkeypatch, tmp_path)
    code = cli.main(["tick"])
    assert code == 0
    assert capsys.readouterr().out == ""


def test_tick_cli_records_failure_exit_zero(capsys, tmp_path, monkeypatch):
    # Real registry, no credentials connected: every platform fails cleanly
    # (video missing -> PublishError before any network), tick still exits 0.
    _patch_queue_store(monkeypatch, tmp_path)
    store = cli.QueueStore()
    past = (datetime.now().astimezone() - timedelta(minutes=1)).isoformat()
    item_id = store.enqueue(QueuedItem(video_path="missing.mp4", title="T",
                                       platforms=["youtube"], scheduled_at=past))
    code = cli.main(["tick"])
    assert code == 0
    out = capsys.readouterr().out
    assert "1 due item(s)" in out and "1 failed" in out
    got = store.get(item_id)
    assert got.status == "failed" and got.attempts == 1
