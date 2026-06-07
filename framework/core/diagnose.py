"""DiagnoseLogger — request pipeline observability with SSE streaming"""
import queue
import threading
import time
import json


class DiagnoseLogger:
    """Per-client singleton logger. Hooks into _post/_get pipeline steps."""

    def __init__(self, app_name: str, enabled: bool = True):
        self.app_name = app_name
        self.enabled = enabled
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def subscribe(self) -> queue.Queue:
        """Return a Queue that receives diagnose events (for SSE streaming)."""
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass

    def log(self, method: str, path: str, step: str, detail: str, ms: float = 0):
        if not self.enabled:
            return
        ts = time.strftime("%H:%M:%S")
        line = f"[diagnose {ts}] {method} {path} | {step}: {detail}"
        if ms > 0:
            line += f" | {ms:.1f}ms"
        print(line)

        payload = {
            "app": self.app_name, "method": method, "path": path,
            "step": step, "detail": detail, "ms": round(ms, 1), "time": ts,
        }
        with self._lock:
            dead = []
            for q in self._subscribers:
                try:
                    q.put_nowait(json.dumps(payload))
                except queue.Full:
                    pass
                except Exception:
                    dead.append(q)
            for q in dead:
                try:
                    self._subscribers.remove(q)
                except ValueError:
                    pass
