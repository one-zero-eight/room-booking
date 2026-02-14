import asyncio
import datetime

from src.modules.bookings.caching import CacheEntry, CacheForBookings
from src.modules.bookings.schemas import Attendee, Booking


def _booking(room_id: str = "r1", start_offset: int = 0, end_offset: int = 3600) -> Booking:
    base = datetime.datetime(2025, 2, 14, 10, 0, 0, tzinfo=datetime.UTC)
    return Booking(
        room_id=room_id,
        title="Meeting",
        start=base + datetime.timedelta(seconds=start_offset),
        end=base + datetime.timedelta(seconds=end_offset),
        outlook_booking_id="oid-1",
        attendees=[Attendee(email="a@b.com", status="Accept", assosiated_room_id=None)],
        related_to_me=None,
    )


def test_cache_entry_stores_bookings_start_end_timestamp():
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    ts = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    entry = CacheEntry(bookings=[b], start=start, end=end, timestamp=ts)
    assert entry.bookings == [b]
    assert entry.start == start
    assert entry.end == end
    assert entry.timestamp == ts


def test_cache_accepts_ttl_as_int_seconds():
    cache = CacheForBookings(ttl=60)
    assert cache.ttl == datetime.timedelta(seconds=60)


def test_cache_accepts_ttl_as_timedelta():
    cache = CacheForBookings(ttl=datetime.timedelta(minutes=5))
    assert cache.ttl == datetime.timedelta(minutes=5)


def test_update_cache_stores_copy():
    cache = CacheForBookings(ttl=60)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        b.title = "Modified"
        return await cache.get_cached_entry("r1", start, end, now=now)

    entry = asyncio.run(go())
    assert entry is not None
    assert entry.bookings[0].title == "Meeting"


def test_get_cached_entry_hit():
    cache = CacheForBookings(ttl=3600)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_entry("r1", start, end, now=now)

    entry = asyncio.run(go())
    assert entry is not None
    assert len(entry.bookings) == 1
    assert entry.start == start and entry.end == end


def test_get_cached_entry_request_within_range():
    cache = CacheForBookings(ttl=3600)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    req_start = datetime.datetime(2025, 2, 14, 10, 0, tzinfo=datetime.UTC)
    req_end = datetime.datetime(2025, 2, 14, 11, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_entry("r1", req_start, req_end, now=now)

    entry = asyncio.run(go())
    assert entry is not None
    assert entry.bookings[0].room_id == "r1"


def test_get_cached_entry_miss_room_unknown():
    cache = CacheForBookings(ttl=3600)
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    entry = asyncio.run(cache.get_cached_entry("r1", start, end, now=now))
    assert entry is None


def test_get_cached_entry_miss_request_outside_range():
    cache = CacheForBookings(ttl=3600)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    req_start = datetime.datetime(2025, 2, 14, 13, 0, tzinfo=datetime.UTC)
    req_end = datetime.datetime(2025, 2, 14, 14, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_entry("r1", req_start, req_end, now=now)

    entry = asyncio.run(go())
    assert entry is None


def test_get_cached_entry_miss_expired_removes_entry():
    cache = CacheForBookings(ttl=60)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    later = now + datetime.timedelta(seconds=61)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_entry("r1", start, end, now=later)

    entry = asyncio.run(go())
    assert entry is None
    assert "r1" not in cache.cache


def test_get_cached_entry_hit_at_ttl_boundary():
    cache = CacheForBookings(ttl=60)
    b = _booking()
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    at_boundary = now + datetime.timedelta(seconds=59)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_entry("r1", start, end, now=at_boundary)

    entry = asyncio.run(go())
    assert entry is not None


def test_update_cache_from_mapping():
    cache = CacheForBookings(ttl=60)
    b1 = _booking(room_id="r1")
    b2 = _booking(room_id="r2")
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache_from_mapping({"r1": [b1], "r2": [b2]}, start, end, now=now)
        e1 = await cache.get_cached_entry("r1", start, end, now=now)
        e2 = await cache.get_cached_entry("r2", start, end, now=now)
        return e1, e2

    e1, e2 = asyncio.run(go())
    assert e1 is not None
    assert e2 is not None


def test_get_cached_bookings_returns_hits_and_misses():
    cache = CacheForBookings(ttl=3600)
    b = _booking(room_id="r1")
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [b], start, end, now=now)
        return await cache.get_cached_bookings(["r1", "r2"], start, end, now=now)

    room_x_cache, cache_misses = asyncio.run(go())
    assert room_x_cache.keys() == {"r1"}
    assert len(room_x_cache["r1"]) == 1
    assert room_x_cache["r1"][0].room_id == "r1"
    assert cache_misses == {"r2"}


def test_get_cached_bookings_all_miss():
    cache = CacheForBookings(ttl=3600)
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    room_x_cache, cache_misses = asyncio.run(cache.get_cached_bookings(["r1", "r2"], start, end, now=now))
    assert room_x_cache == {}
    assert cache_misses == {"r1", "r2"}


def test_get_cached_bookings_all_hit():
    cache = CacheForBookings(ttl=3600)
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache("r1", [_booking(room_id="r1")], start, end, now=now)
        await cache.update_cache("r2", [_booking(room_id="r2")], start, end, now=now)
        return await cache.get_cached_bookings(["r1", "r2"], start, end, now=now)

    room_x_cache, cache_misses = asyncio.run(go())
    assert set(room_x_cache.keys()) == {"r1", "r2"}
    assert len(cache_misses) == 0


def test_multiple_slots_query_finds_containing_slot():
    cache = CacheForBookings(ttl=3600, max_slots_per_room=5)
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    req_start = datetime.datetime(2025, 2, 14, 15, 0, tzinfo=datetime.UTC)
    req_end = datetime.datetime(2025, 2, 14, 16, 0, tzinfo=datetime.UTC)

    async def go():
        await cache.update_cache(
            "r1",
            [_booking(room_id="r1")],
            datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC),
            datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC),
            now=now,
        )
        await cache.update_cache(
            "r1",
            [_booking(room_id="r1", start_offset=3600, end_offset=7200)],
            datetime.datetime(2025, 2, 14, 14, 0, tzinfo=datetime.UTC),
            datetime.datetime(2025, 2, 14, 18, 0, tzinfo=datetime.UTC),
            now=now,
        )
        return await cache.get_cached_entry("r1", req_start, req_end, now=now)

    entry = asyncio.run(go())
    assert entry is not None
    assert entry.start.hour == 14 and entry.end.hour == 18
    assert len(entry.bookings) == 1


def test_evict_oldest_when_over_max_slots():
    cache = CacheForBookings(ttl=86400, max_slots_per_room=2)  # long ttl so none expire
    now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    base_start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    base_end = datetime.datetime(2025, 2, 14, 10, 0, tzinfo=datetime.UTC)

    async def go():
        for i in range(3):
            await cache.update_cache("r1", [_booking()], base_start, base_end, now=now + datetime.timedelta(seconds=i))
        return await cache.get_cached_entry("r1", base_start, base_end, now=now)

    entry = asyncio.run(go())
    assert len(cache.cache["r1"]) == 2
    assert entry is not None


def test_expired_slot_skipped_fresh_slot_used():
    cache = CacheForBookings(ttl=60, max_slots_per_room=5)
    old_now = datetime.datetime(2025, 2, 14, 8, 0, tzinfo=datetime.UTC)
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    new_now = old_now + datetime.timedelta(seconds=70)

    async def go():
        await cache.update_cache("r1", [_booking(room_id="r1")], start, end, now=old_now)
        await cache.update_cache("r1", [_booking(room_id="r1", start_offset=3600)], start, end, now=new_now)
        return await cache.get_cached_entry("r1", start, end, now=new_now)

    entry = asyncio.run(go())
    assert entry is not None
    assert len(cache.cache["r1"]) == 1
