import subprocess
import json
from bridge import bridge
from tools.retry import retry

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "manage_git",
        "description": "Clone repositories or manage git state inside the Docker sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["clone", "pull", "status"]},
                "repo_url": {"type": "string", "description": "The GitHub URL (required for clone)."},
                "destination": {"type": "string", "description": "Folder name for the repo."}
            },
            "required": ["action"]
        }
    }
})
def manage_git(action, repo_url=None, destination=None):
    try:
        if action == "clone" and repo_url:
            dest = f" {destination}" if destination else ""
            cmd = f"git clone {repo_url}{dest}"
        elif action == "pull":
            cmd = "git pull"
        else:
            cmd = "git status"

        return _exec_git(cmd)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Git Error: {str(e)}"})

@retry(max_attempts=2, backoff_factor=1.0)
def _exec_git(cmd):
    process = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", f"cd /workspace && {cmd}"],
        capture_output=True, text=True, timeout=120, encoding="utf-8"
    )
    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    if process.returncode == 0:
        return json.dumps({"status": "ok", "output": stdout or "Done."})
    else:
        return json.dumps({"status": "error", "message": stderr})