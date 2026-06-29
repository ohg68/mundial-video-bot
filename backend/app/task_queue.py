import asyncio
import uuid
import logging
from datetime import datetime
from typing import Callable, Any
from app.database import SessionLocal, TaskRecord
from app.websocket import manager

log = logging.getLogger(__name__)


class TaskQueue:
    def __init__(self, max_workers: int = 3):
        self._queue: asyncio.Queue = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._max_workers = max_workers

    async def start(self):
        for i in range(self._max_workers):
            task = asyncio.create_task(self._worker(i))
            self._workers.append(task)

    async def stop(self):
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()

    async def submit(self, project_id: str, task_type: str, fn: Callable, *args, **kwargs) -> str:
        task_id = str(uuid.uuid4())
        db = SessionLocal()
        try:
            record = TaskRecord(
                id=task_id,
                project_id=project_id,
                task_type=task_type,
                status="pending",
            )
            db.add(record)
            db.commit()
        finally:
            db.close()

        await manager.send_progress(project_id, {
            "type": "task_queued",
            "task_id": task_id,
            "task_type": task_type,
        })

        await self._queue.put((task_id, project_id, task_type, fn, args, kwargs))
        return task_id

    async def _worker(self, worker_id: int):
        while True:
            item = await self._queue.get()
            if item is None:
                break

            task_id, project_id, task_type, fn, args, kwargs = item

            db = SessionLocal()
            try:
                record = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
                if record:
                    record.status = "running"
                    record.progress = 0.0
                    db.commit()

                await manager.send_progress(project_id, {
                    "type": "task_started",
                    "task_id": task_id,
                    "task_type": task_type,
                })

                result = await fn(*args, **kwargs)

                if record:
                    record.status = "completed"
                    record.progress = 100.0
                    record.result = str(result) if result else None
                    db.commit()

                await manager.send_progress(project_id, {
                    "type": "task_completed",
                    "task_id": task_id,
                    "task_type": task_type,
                    "progress": 100,
                })

            except Exception as e:
                log.exception(f"Task {task_id} failed")
                if record:
                    record.status = "failed"
                    record.error = str(e)
                    db.commit()

                await manager.send_progress(project_id, {
                    "type": "task_failed",
                    "task_id": task_id,
                    "task_type": task_type,
                    "error": str(e),
                })
            finally:
                db.close()
                self._queue.task_done()


task_queue = TaskQueue()


async def send_progress(project_id: str, task_type: str, progress: float, detail: str = ""):
    await manager.send_progress(project_id, {
        "type": "task_progress",
        "task_type": task_type,
        "progress": round(progress, 1),
        "detail": detail,
    })
