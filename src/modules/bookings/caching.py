import datetime
from collections.abc import Iterable
from dataclasses import dataclass

from src.modules.bookings.schemas import Booking


@dataclass
class CacheSlot:
    bookings: list[Booking]
    start: datetime.datetime
    end: datetime.datetime
    timestamp: datetime.datetime


# Alias for backward compatibility; get_cached_entry returns a slot-shaped value.
CacheEntry = CacheSlot


class CacheForBookings:
    ttl: datetime.timedelta
    max_slots_per_room: int
    cache: dict[str, list[CacheSlot]]

    def __init__(self, ttl: datetime.timedelta | int, max_slots_per_room: int = 10):
        self.ttl = datetime.timedelta(seconds=ttl) if isinstance(ttl, int) else ttl
        self.max_slots_per_room = max_slots_per_room
        self.cache = {}

    def _prune_expired(self, room_id: str, now: datetime.datetime) -> None:
        slots = self.cache.get(room_id)
        if not slots:
            return
        valid = [s for s in slots if s.timestamp + self.ttl > now]
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

    def update_cache(
        self,
        room_id: str,
        bookings: list[Booking],
        start: datetime.datetime,
        end: datetime.datetime,
        now: datetime.datetime | None = None,
    ):
        now = now or datetime.datetime.now()
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

    def update_cache_from_mapping(
        self,
        room_id_x_bookings: dict[str, list[Booking]],
        start: datetime.datetime,
        end: datetime.datetime,
        now: datetime.datetime | None = None,
    ):
        now = now or datetime.datetime.now()
        for room_id, bookings in room_id_x_bookings.items():
            self.update_cache(room_id=room_id, bookings=bookings, start=start, end=end, now=now)

    def get_cached_entry(
        self,
        room_id: str,
        start: datetime.datetime,
        end: datetime.datetime,
        now: datetime.datetime | None = None,
    ) -> CacheSlot | None:
        now = now or datetime.datetime.now()
        slots = self.cache.get(room_id)
        if not slots:
            return None
        not_outdated = [s for s in slots if s.timestamp + self.ttl > now]
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

    def get_cached_bookings(
        self,
        room_ids: Iterable[str],
        start: datetime.datetime,
        end: datetime.datetime,
        now: datetime.datetime | None = None,
    ) -> tuple[dict[str, list[Booking]], set[str]]:
        room_x_cache: dict[str, list[Booking]] = {}
        cache_misses: set[str] = set()
        for room_id in room_ids:
            entry = self.get_cached_entry(room_id, start, end, now=now)
            if entry is None:
                cache_misses.add(room_id)
            else:
                room_x_cache[room_id] = entry.bookings
        return room_x_cache, cache_misses
