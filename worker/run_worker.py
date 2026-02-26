"""RQ worker entrypoint.

Run:
  python -m validation_tool.worker.run_worker
"""

import redis
from rq import Worker

from validation_tool.backend.settings import redis_url


def main() -> None:
    conn = redis.Redis.from_url(redis_url())
    worker = Worker(["validations"], connection=conn)
    worker.work()


if __name__ == "__main__":
    main()
