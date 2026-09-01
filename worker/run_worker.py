"""RQ worker entrypoint.

Run:
  python -m validation_tool.worker.run_worker
"""

import os
import multiprocessing as mp

import redis
from rq import Worker

from backend.settings import redis_url


def _run_one_worker() -> None:
  conn = redis.Redis.from_url(redis_url())
  worker = Worker(["validations"], connection=conn)
  worker.work()


def main() -> None:
  processes = int(os.getenv("WORKER_PROCESSES", "1"))
  if processes <= 1:
    _run_one_worker()
    return

  children: list[mp.Process] = []
  for i in range(processes):
    p = mp.Process(target=_run_one_worker, name=f"rq-worker-{i + 1}")
    p.start()
    children.append(p)

  for p in children:
    p.join()


if __name__ == "__main__":
    main()
