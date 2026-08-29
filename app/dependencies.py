from app.db.session import get_db, get_redis

# Expose dependencies for ease of imports in other app routers
__all__ = ["get_db", "get_redis"]
