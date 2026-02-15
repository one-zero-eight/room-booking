import asyncio
import datetime
import time
from collections.abc import Iterable
from dataclasses import dataclass

from src.modules.bookings.schemas import Booking


@dataclass
class CacheSlot:
    bookings: list[Booking]
    start: datetime.datetime
    end: datetime.datetime
    timestamp: float  # time.monotonic()


# Alias for backward compatibility; get_cached_entry returns a slot-shaped value.
CacheEntry = CacheSlot


def _ttl_to_seconds(ttl: datetime.timedelta | int) -> float:
    if isinstance(ttl, int):
        return float(ttl)
    return ttl.total_seconds()


class CacheForBookings:
    ttl_sec: float
    max_slots_per_room: int
    cache: dict[str, list[CacheSlot]]
    _lock: asyncio.Lock

    def __init__(self, ttl: "datetime.timedelta | int", max_slots_per_room: int = 10):
        self.ttl_sec = _ttl_to_seconds(ttl)
        self.max_slots_per_room = max_slots_per_room
        self.cache = {}
        self._lock = asyncio.Lock()

    def _prune_expired(self, room_id: str, now: float) -> None:
        slots = self.cache.get(room_id)
        if not slots:
            return
        valid = [s for s in slots if s.timestamp + self.ttl_sec > now]
        if not valid:
            self.cache.pop(room_id, None)
        else:
            self.cache[room_id] = valid

    def _evict_oldest(self, room_id: str) -> None:
        slots = self.cache.get(room_id, [])
        if len(slots) <= self.max_slots_per_room:
            return
        slots.sort(key=lambda s: s.timestamp)
        self.cache[room_id] = slots[-self.max_slots_per_room :]

    def _update_cache_impl(
        self,
        room_id: str,
        bookings: list[Booking],
        start: datetime.datetime,
        end: datetime.datetime,
        now: float,
    ) -> None:
        slot = CacheSlot(
            bookings=[b.model_copy() for b in bookings],
            start=start,
            end=end,
            timestamp=now,
        )
        if room_id not in self.cache:
            self.cache[room_id] = []
        self.cache[room_id].append(slot)
        self._prune_expired(room_id, now)
        self._evict_oldest(room_id)

    def _get_cached_entry_impl(
        self,
        room_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
        now: float,
    ) -> CacheSlot | None:
        slots = self.cache.get(room_id)
        if not slots:
            return None
        not_outdated = [s for s in slots if s.timestamp + self.ttl_sec > now]
        if not not_outdated:
            self.cache.pop(room_id, None)
            return None
        for slot in not_outdated:
            if slot.start <= start and slot.end >= end:
                return CacheSlot(
                    bookings=[b.model_copy() for b in slot.bookings],
                    start=slot.start,
                    end=slot.end,
                    timestamp=slot.timestamp,
                )
        return None

    async def update_cache(
        self,
        room_id: str,
        bookings: list[Booking],
        start: datetime.datetime,
        end: datetime.datetime,
        now: float | None = None,
    ) -> None:
        now = now if now is not None else time.monotonic()
        async with self._lock:
            self._update_cache_impl(room_id, bookings, start, end, now)

    async def update_cache_from_mapping(
        self,
        room_id_x_bookings: dict[str, list[Booking]],
        start: datetime.datetime,
        end: datetime.datetime,
        now: float | None = None,
    ) -> None:
        now = now if now is not None else time.monotonic()
        async with self._lock:
            for room_id, bookings in room_id_x_bookings.items():
                self._update_cache_impl(room_id, bookings, start, end, now)

    async def get_cached_entry(
        self,
        room_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
        now: float | None = None,
    ) -> CacheSlot | None:
        now = now if now is not None else time.monotonic()
        async with self._lock:
            return self._get_cached_entry_impl(room_id, start, end, now)

    async def get_cached_bookings(
        self,
        room_ids: Iterable[str],
        start: datetime.datetime,
        end: datetime.datetime,
        now: float | None = None,
    ) -> tuple[dict[str, list[Booking]], set[str]]:
        now = now if now is not None else time.monotonic()
        room_x_cache: dict[str, list[Booking]] = {}
        cache_misses: set[str] = set()
        async with self._lock:
            for room_id in room_ids:
                entry = self._get_cached_entry_impl(room_id, start, end, now)
                if entry is None:
                    cache_misses.add(room_id)
                else:
                    room_x_cache[room_id] = entry.bookings
        return room_x_cache, cache_misses

    async def add_booking_to_cache(self, booking: Booking, now: float | None = None) -> None:
        """
        Add a booking to all cache slots that overlap with the booking's time range.
        This allows immediate cache updates after booking creation.
        
        Args:
            booking: The booking to add to cache
            now: Optional timestamp for consistency with other cache methods (not currently used for TTL checks)
        """
        now = now if now is not None else time.monotonic()
        async with self._lock:
            slots = self.cache.get(booking.room_id)
            if not slots:
                return

            for slot in slots:
                # Check if this slot overlaps with the booking time range
                # Two time ranges overlap if: slot.start < booking.end AND booking.start < slot.end
                if slot.start < booking.end and booking.start < slot.end:
                    # Check if booking is not already in the slot
                    # For bookings with outlook_booking_id, match by ID
                    # For bookings without outlook_booking_id (free busy info), match by (room_id, start, end)
                    if booking.outlook_booking_id is not None:
                        is_duplicate = any(b.outlook_booking_id == booking.outlook_booking_id for b in slot.bookings)
                    else:
                        is_duplicate = any(
                            b.room_id == booking.room_id and b.start == booking.start and b.end == booking.end
                            for b in slot.bookings
                        )

                    if not is_duplicate:
                        slot.bookings.append(booking.model_copy())
                        # Keep bookings sorted by start time
                        slot.bookings.sort(key=lambda b: b.start)

    async def remove_booking_from_cache(self, booking: Booking) -> None:
        """
        Remove a booking from all cache slots across all rooms.
        This allows immediate cache updates after booking cancellation.
        
        Bookings can be identified in two ways:
        - By outlook_booking_id (for bookings from account calendar view)
        - By (room_id, start, end) tuple (for bookings from free busy info that don't have outlook_booking_id)
        
        Args:
            booking: The booking object to remove. Will use outlook_booking_id if available,
                    otherwise match by (room_id, start, end)
        """
        async with self._lock:
            if booking.outlook_booking_id is not None:
                # Match by outlook_booking_id (for account calendar bookings)
                for slots in self.cache.values():
                    for slot in slots:
                        slot.bookings = [b for b in slot.bookings if b.outlook_booking_id != booking.outlook_booking_id]
            else:
                # Match by (room_id, start, end) for free busy info bookings
                slots = self.cache.get(booking.room_id)
                if slots:
                    for slot in slots:
                        slot.bookings = [
                            b
                            for b in slot.bookings
                            if not (b.room_id == booking.room_id and b.start == booking.start and b.end == booking.end)
                        ]
