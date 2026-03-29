import subprocess
import json
from bridge import bridge

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "reset_workspace",
        "description": "Deletes everything in the /workspace directory to start fresh.",
        "parameters": {"type": "object", "properties": {}}
    }
})
def reset_workspace():
    try:
        subprocess.run(["docker", "exec", CONTAINER_NAME, "bash", "-c", "rm -rf /workspace/*"], timeout=15)
        return json.dumps({"status": "ok", "output": "Workspace cleared. Ready for new experiments."})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Reset Error: {str(e)}"})