"""Preserve serialized state writes while observation requests run concurrently."""
import threading

WRITE_LOCK = threading.Lock()
