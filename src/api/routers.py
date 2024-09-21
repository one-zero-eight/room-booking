from src.modules.rooms.routes import router as router_rooms
from src.modules.bookings.routes import router as router_bookings

routers = [router_rooms, router_bookings]

__all__ = ["routers"]
