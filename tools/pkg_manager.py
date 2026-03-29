import subprocess
import json
from bridge import bridge
from tools.retry import retry

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "manage_packages",
        "description": "Install or list Python packages inside the Docker sandbox using pip.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["install", "list"]},
                "package_name": {"type": "string", "description": "The name of the package to install (required for 'install')."}
            },
            "required": ["action"]
        }
    }
})
def manage_packages(action, package_name=None):
    try:
        if action == "install" and package_name:
            cmd = f"pip install {package_name}"
        elif action == "list":
            cmd = "pip list"
        else:
            return json.dumps({"status": "error", "message": "package_name is required for 'install' action."})

        return _exec_pip(cmd)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Package Manager Error: {str(e)}"})

@retry(max_attempts=3, backoff_factor=1.0)
def _exec_pip(cmd):
    process = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
        capture_output=True, text=True, encoding="utf-8", timeout=120
    )
    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    if process.returncode == 0:
        return json.dumps({"status": "ok", "output": stdout or "Done."})
    else:
        return json.dumps({"status": "error", "output": stdout, "message": stderr})