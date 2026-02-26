import redis
from rq import Queue

from validation_tool.backend.settings import redis_url


def get_queue() -> Queue:
    conn = redis.Redis.from_url(redis_url())
    return Queue("validations", connection=conn)
