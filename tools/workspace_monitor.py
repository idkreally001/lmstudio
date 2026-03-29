import subprocess
import json
from bridge import bridge

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "monitor_workspace",
        "description": "Provides a recursive tree-view of all files and folders in the /workspace directory.",
        "parameters": {
            "type": "object",
            "properties": {}
        }
    }
})
def monitor_workspace():
    try:
        process = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c",
             "find . -maxdepth 3 -not -path '*/.*'"],
            capture_output=True, text=True, timeout=10, encoding="utf-8"
        )
        if process.returncode == 0:
            tree = process.stdout.strip()
            return tree if tree else "."
        else:
            return "."
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Monitor Error: {e}"})