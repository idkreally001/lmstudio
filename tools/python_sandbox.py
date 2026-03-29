import subprocess
import json
from bridge import bridge
from tools.retry import retry

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "run_python_script",
        "description": "Execute a Python script inside the Docker sandbox and return the output.",
        "parameters": {
            "type": "object",
            "properties": {
                "script_path": {"type": "string", "description": "The path to the .py file inside the container."}
            },
            "required": ["script_path"]
        }
    }
})
def run_python_script(script_path):
    cmd = f'python3 "{script_path}"'
    try:
        return _exec_python(cmd)
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Sandbox Execution Error: {str(e)}"})

@retry(max_attempts=2, backoff_factor=0.5)
def _exec_python(cmd):
    process = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
        capture_output=True, text=True, timeout=60, encoding="utf-8"
    )
    stdout = process.stdout.strip()
    stderr = process.stderr.strip()
    if process.returncode == 0:
        return json.dumps({"status": "ok", "output": stdout or "[NO OUTPUT]"})
    else:
        return json.dumps({"status": "error", "output": stdout, "message": stderr})