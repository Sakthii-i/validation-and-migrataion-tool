import redis
from rq import Queue

from backend.settings import redis_url


def get_queue() -> Queue:
    conn = redis.Redis.from_url(redis_url())
    return Queue("validations", connection=conn)
