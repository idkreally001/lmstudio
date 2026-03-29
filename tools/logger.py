import os
import json
import logging
from logging.handlers import RotatingFileHandler
from bridge import bridge
from datetime import datetime

# ---------------------------------------------------------------------------
# Rotating log setup — writes to workspace/logs/agent.log
# ---------------------------------------------------------------------------
LOG_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "workspace", "logs"))
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "agent.log")

_logger = logging.getLogger("agent_logger")
_logger.setLevel(logging.DEBUG)

_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_logger.addHandler(_handler)


def get_logger():
    """Return the shared rotating logger instance."""
    return _logger


@bridge.tool({
    "type": "function",
    "function": {
        "name": "log_experiment_note",
        "description": "Saves a formal note or observation about the current experiment to a local log file.",
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The observation or data point to record."},
                "filename": {"type": "string", "description": "Optional: Specific log file name (default: experiment_log.md)"}
            },
            "required": ["note"]
        }
    }
})
def log_experiment_note(note, filename="experiment_log.md"):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    entry = f"### [{timestamp}]\n{note}\n\n"

    try:
        clean_filename = filename.lstrip(" /\\")
        workspace_dir = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "workspace"))
        os.makedirs(workspace_dir, exist_ok=True)
        full_path = os.path.join(workspace_dir, clean_filename)

        with open(full_path, "a", encoding="utf-8") as f:
            f.write(entry)

        # Also log to the rotating logger
        _logger.info(f"Experiment note logged to {clean_filename}: {note[:100]}...")

        return json.dumps({"status": "ok", "output": f"Successfully logged to /workspace/{clean_filename}"})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Logging Error: {str(e)}"})