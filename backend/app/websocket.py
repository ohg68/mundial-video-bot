import asyncio
import json
import logging
from typing import Optional
from fastapi import WebSocket, WebSocketDisconnect

log = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}

    async def connect(self, project_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections.setdefault(project_id, []).append(websocket)

    def disconnect(self, project_id: str, websocket: WebSocket):
        conns = self._connections.get(project_id, [])
        if websocket in conns:
            conns.remove(websocket)
        if not conns and project_id in self._connections:
            del self._connections[project_id]

    async def send_progress(self, project_id: str, data: dict):
        conns = self._connections.get(project_id, [])
        dead = []
        for ws in conns:
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(project_id, ws)

    async def broadcast(self, data: dict):
        for project_id in list(self._connections.keys()):
            await self.send_progress(project_id, data)


manager = ConnectionManager()


async def websocket_endpoint(websocket: WebSocket, project_id: str):
    await manager.connect(project_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        manager.disconnect(project_id, websocket)


def parse_ffmpeg_progress(line: str, duration: Optional[float] = None) -> Optional[float]:
    if "time=" not in line:
        return None
    try:
        time_str = line.split("time=")[1].split(" ")[0].strip()
        parts = time_str.split(":")
        seconds = float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        if duration and duration > 0:
            return min(round(seconds / duration * 100, 1), 100.0)
        return seconds
    except (IndexError, ValueError):
        return None
