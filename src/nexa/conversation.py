"""Thread-safe, session-only conversation history shared by NEXA."""
import threading

_LOCK = threading.RLock()
_HISTORY = []
_MAX_MESSAGES = 20


def get_history():
    """Return a snapshot so callers cannot mutate shared history directly."""
    with _LOCK:
        return [item.copy() for item in _HISTORY]


def add_exchange(user_message, assistant_message):
    """Append one completed user/assistant exchange safely."""
    user_message = str(user_message or "").strip()
    assistant_message = str(assistant_message or "").strip()
    if not user_message or not assistant_message:
        return
    with _LOCK:
        _HISTORY.extend([
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": assistant_message},
        ])
        del _HISTORY[:-_MAX_MESSAGES]


def clear_history():
    """Clear session history when explicitly requested by the application."""
    with _LOCK:
        _HISTORY.clear()
