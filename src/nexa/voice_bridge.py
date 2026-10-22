"""Thread-safe bridge between NEXA's voice listener and avatar GUI."""

import threading


class VoiceBridge:
    """Deliver voice events safely to the Tkinter GUI."""

    def __init__(self, root, on_event):
        self.root = root
        self.on_event = on_event
        self._lock = threading.Lock()
        self._started = False
        self._stop_event = threading.Event()
        self._thread = None

    def emit(self, event, text=""):
        if self._stop_event.is_set():
            return
        try:
            self.root.after(0, lambda e=event, t=text: self._deliver(e, t))
        except Exception:
            pass

    def _deliver(self, event, text):
        if self._stop_event.is_set():
            return
        try:
            self.on_event(event, text)
        except Exception as exc:
            print(f"NEXA GUI event error: {exc}")

    def start(self):
        """Start the listener, or restart it after a completed Stop action."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            # A stopped Event cannot be cleared safely while an old listener is
            # still running. We only replace it after the old thread has ended.
            self._stop_event = threading.Event()
            self._started = True
            self._thread = threading.Thread(
                target=self._run, name="NEXA-Voice-Bridge", daemon=True
            )
            self._thread.start()
            return True

    def _run(self):
        self.emit("status", "Starting microphone")
        try:
            from nexa import voice
            voice.listen_loop(
                stop_event=self._stop_event,
                event_callback=self.emit,
            )
        except Exception as exc:
            self.emit("error", f"Voice listener failed: {type(exc).__name__}: {exc}")
        finally:
            self.emit("status", "Voice listener stopped")

    def stop(self):
        self._stop_event.set()

    @property
    def is_running(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )
