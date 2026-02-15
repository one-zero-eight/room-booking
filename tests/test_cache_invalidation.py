import datetime

import pytest

from src.modules.bookings.caching import CacheForBookings
from src.modules.bookings.schemas import Attendee, Booking


def _booking(
    room_id: str = "r1",
    outlook_booking_id: str = "oid-1",
    start_offset: int = 0,
    end_offset: int = 3600,
) -> Booking:
    base = datetime.datetime(2025, 2, 14, 10, 0, 0, tzinfo=datetime.UTC)
    return Booking(
        room_id=room_id,
        title="Meeting",
        start=base + datetime.timedelta(seconds=start_offset),
        end=base + datetime.timedelta(seconds=end_offset),
        outlook_booking_id=outlook_booking_id,
        attendees=[Attendee(email="a@b.com", status="Accept", assosiated_room_id=None)],
        related_to_me=None,
    )


@pytest.mark.anyio
async def test_add_booking_to_cache_adds_to_overlapping_slots():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1", start_offset=0, end_offset=3600)
    new_booking = _booking(room_id="r1", outlook_booking_id="oid-2", start_offset=1800, end_offset=5400)

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = 1000.0

    # Create cache with one booking
    await cache.update_cache("r1", [b1], start, end, now=now)

    # Add new booking to cache
    await cache.add_booking_to_cache(new_booking, now=now)

    # Verify both bookings are in cache
    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    assert len(entry.bookings) == 2
    assert entry.bookings[0].outlook_booking_id == "oid-1"
    assert entry.bookings[1].outlook_booking_id == "oid-2"


@pytest.mark.anyio
async def test_add_booking_to_cache_sorts_by_start_time():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1", start_offset=3600, end_offset=7200)
    new_booking = _booking(room_id="r1", outlook_booking_id="oid-2", start_offset=0, end_offset=3600)

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 14, 0, tzinfo=datetime.UTC)
    now = 1000.0

    await cache.update_cache("r1", [b1], start, end, now=now)
    await cache.add_booking_to_cache(new_booking, now=now)

    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    assert len(entry.bookings) == 2
    # Should be sorted by start time
    assert entry.bookings[0].outlook_booking_id == "oid-2"
    assert entry.bookings[1].outlook_booking_id == "oid-1"


@pytest.mark.anyio
async def test_add_booking_to_cache_no_duplicate():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1")

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = 1000.0

    await cache.update_cache("r1", [b1], start, end, now=now)
    # Try to add same booking again
    await cache.add_booking_to_cache(b1, now=now)

    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    assert len(entry.bookings) == 1


@pytest.mark.anyio
async def test_add_booking_to_cache_no_overlap():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1", start_offset=0, end_offset=3600)
    # This booking is completely outside the cached range
    new_booking = _booking(room_id="r1", outlook_booking_id="oid-2", start_offset=14400, end_offset=18000)

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = 1000.0

    await cache.update_cache("r1", [b1], start, end, now=now)
    await cache.add_booking_to_cache(new_booking, now=now)

    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    # Should still only have the original booking since new one doesn't overlap
    assert len(entry.bookings) == 1
    assert entry.bookings[0].outlook_booking_id == "oid-1"


@pytest.mark.anyio
async def test_add_booking_to_cache_no_cache_for_room():
    cache = CacheForBookings(ttl=3600)
    new_booking = _booking(room_id="r1", outlook_booking_id="oid-1")
    now = 1000.0

    # Try to add booking when no cache exists for room
    await cache.add_booking_to_cache(new_booking, now=now)

    # Should not create cache entry
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is None


@pytest.mark.anyio
async def test_remove_booking_from_cache():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1")
    b2 = _booking(room_id="r1", outlook_booking_id="oid-2", start_offset=3600, end_offset=7200)

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 14, 0, tzinfo=datetime.UTC)
    now = 1000.0

    await cache.update_cache("r1", [b1, b2], start, end, now=now)
    await cache.remove_booking_from_cache("oid-1")

    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    assert len(entry.bookings) == 1
    assert entry.bookings[0].outlook_booking_id == "oid-2"


@pytest.mark.anyio
async def test_remove_booking_from_cache_removes_from_all_slots():
    cache = CacheForBookings(ttl=3600, max_slots_per_room=5)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1")
    now = 1000.0

    # Create multiple slots with the same booking
    slot1_start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    slot1_end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    slot2_start = datetime.datetime(2025, 2, 14, 14, 0, tzinfo=datetime.UTC)
    slot2_end = datetime.datetime(2025, 2, 14, 18, 0, tzinfo=datetime.UTC)

    await cache.update_cache("r1", [b1], slot1_start, slot1_end, now=now)
    await cache.update_cache("r1", [b1], slot2_start, slot2_end, now=now)

    # Remove booking from cache
    await cache.remove_booking_from_cache("oid-1")

    # Verify booking is removed from both slots
    entry1 = await cache.get_cached_entry("r1", slot1_start, slot1_end, now=now)
    entry2 = await cache.get_cached_entry("r1", slot2_start, slot2_end, now=now)
    assert entry1 is not None
    assert len(entry1.bookings) == 0
    assert entry2 is not None
    assert len(entry2.bookings) == 0


@pytest.mark.anyio
async def test_remove_booking_from_cache_no_cache_for_room():
    cache = CacheForBookings(ttl=3600)
    now = 1000.0

    # Try to remove booking when no cache exists for room
    await cache.remove_booking_from_cache("oid-1")

    # Should not raise error
    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is None


@pytest.mark.anyio
async def test_remove_nonexistent_booking():
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1")

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = 1000.0

    await cache.update_cache("r1", [b1], start, end, now=now)
    # Try to remove non-existent booking
    await cache.remove_booking_from_cache("oid-nonexistent")

    entry = await cache.get_cached_entry("r1", start, end, now=now)
    assert entry is not None
    assert len(entry.bookings) == 1
    assert entry.bookings[0].outlook_booking_id == "oid-1"


@pytest.mark.anyio
async def test_remove_booking_from_cache_searches_all_rooms():
    """Test that remove_booking_from_cache searches across all rooms without needing room_id"""
    cache = CacheForBookings(ttl=3600)
    b1 = _booking(room_id="r1", outlook_booking_id="oid-1")
    b2 = _booking(room_id="r2", outlook_booking_id="oid-2")
    b3 = _booking(room_id="r3", outlook_booking_id="oid-3")

    start = datetime.datetime(2025, 2, 14, 9, 0, tzinfo=datetime.UTC)
    end = datetime.datetime(2025, 2, 14, 12, 0, tzinfo=datetime.UTC)
    now = 1000.0

    # Create cache entries for different rooms
    await cache.update_cache("r1", [b1], start, end, now=now)
    await cache.update_cache("r2", [b2], start, end, now=now)
    await cache.update_cache("r3", [b3], start, end, now=now)

    # Remove booking from r2 without specifying room_id
    await cache.remove_booking_from_cache("oid-2")

    # Verify r1 and r3 still have their bookings
    entry1 = await cache.get_cached_entry("r1", start, end, now=now)
    entry3 = await cache.get_cached_entry("r3", start, end, now=now)
    assert entry1 is not None
    assert len(entry1.bookings) == 1
    assert entry1.bookings[0].outlook_booking_id == "oid-1"
    assert entry3 is not None
    assert len(entry3.bookings) == 1
    assert entry3.bookings[0].outlook_booking_id == "oid-3"

    # Verify r2 no longer has the booking
    entry2 = await cache.get_cached_entry("r2", start, end, now=now)
    assert entry2 is not None
    assert len(entry2.bookings) == 0
