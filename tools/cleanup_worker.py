"""Background cleanup worker that prunes old TTS audio and temp files."""
import os
import time
import json
import threading

def _load_cache_config():
    """Read audio_cache settings from config.json."""
    defaults = {"max_files": 5, "max_size_mb": 100}
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
        defaults.update(cfg.get("audio_cache", {}))
    except Exception:
        pass
    return defaults

def _prune_directory(directory, max_files, max_size_bytes):
    """Remove oldest files until count and size are within limits."""
    if not os.path.isdir(directory):
        return
    files = []
    for fname in os.listdir(directory):
        fpath = os.path.join(directory, fname)
        if os.path.isfile(fpath):
            files.append(fpath)
    files.sort(key=os.path.getmtime)  # oldest first

    # Prune by count
    while len(files) > max_files:
        try:
            os.remove(files.pop(0))
        except OSError:
            pass

    # Prune by total size
    total = sum(os.path.getsize(f) for f in files if os.path.exists(f))
    while total > max_size_bytes and files:
        try:
            total -= os.path.getsize(files[0])
            os.remove(files.pop(0))
        except OSError:
            break

def cleanup_loop(tts_dir, interval=300):
    """Runs forever, pruning old files every `interval` seconds."""
    while True:
        try:
            cfg = _load_cache_config()
            max_files = cfg.get("max_files", 5)
            max_size = cfg.get("max_size_mb", 100) * 1024 * 1024
            _prune_directory(tts_dir, max_files, max_size)
        except Exception as e:
            print(f"[!] Cleanup worker error: {e}")
        time.sleep(interval)

def start_cleanup_worker(tts_dir, interval=300):
    """Start the cleanup worker in a background daemon thread."""
    t = threading.Thread(target=cleanup_loop, args=(tts_dir, interval), daemon=True)
    t.start()
    print(f"[*] Cleanup worker started (interval={interval}s, dir={tts_dir})")
    return t
