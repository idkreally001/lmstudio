import time
import functools

def retry(max_attempts=3, backoff_factor=0.5, allowed_exceptions=(Exception,)):
    """Retry decorator with exponential back‑off.
    Args:
        max_attempts: maximum number of attempts (including the first).
        backoff_factor: base delay in seconds; actual delay = backoff_factor * (2 ** (attempt-1)).
        allowed_exceptions: tuple of exception classes that trigger a retry.
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 0
            while True:
                try:
                    return func(*args, **kwargs)
                except allowed_exceptions as e:
                    attempt += 1
                    if attempt >= max_attempts:
                        raise
                    delay = backoff_factor * (2 ** (attempt - 1))
                    time.sleep(delay)
        return wrapper
    return decorator
