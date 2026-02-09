from itertools import product

from src.modules.rooms.repository import room_repository
from src.modules.rules.service import _check_rules

for room, booking_longer_than_3_hours, highest_role, in_access_list, non_restricted_time in product(
    room_repository.get_all(), [0, 1], ["none", "student", "staff"], [0, 1], [0, 1]
):
    print(
        f"{room.id:6}, {booking_longer_than_3_hours}, {highest_role:10} {in_access_list} {non_restricted_time} "
        f"| {_check_rules(room=room, booking_longer_than_3_hours=booking_longer_than_3_hours, highest_role=highest_role, in_access_list=in_access_list, non_restricted_time=non_restricted_time)}"
    )
