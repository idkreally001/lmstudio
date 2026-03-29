import subprocess
import json
from bridge import bridge
from tools.retry import retry

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "run_sandbox_command",
        "description": "Execute a bash command inside the isolated Docker sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The command to run (e.g. 'ls', 'python script.py', 'git clone')"}
            },
            "required": ["command"]
        }
    }
})
def run_sandbox_command(command):
    try:
        result = _exec_docker(command)
        return result
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Docker Error: {str(e)}. Is the container running?"})

MAX_OUTPUT_CHARS = 5000  # Protects the context window from "Data Nukes"

@retry(max_attempts=2, backoff_factor=0.5)
def _exec_docker(command):
    process = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", command],
        capture_output=True, text=True, timeout=60, encoding="utf-8"
    )
    
    stdout = process.stdout.strip()
    stderr = process.stderr.strip()

    # --- Truncation Logic ---
    def truncate(text, label):
        if len(text) > MAX_OUTPUT_CHARS:
            return (
                text[:MAX_OUTPUT_CHARS] + 
                f"\n\n[... {len(text) - MAX_OUTPUT_CHARS} CHARS TRUNCATED FROM {label} ...]\n"
                "WARNING: Output is too long. Use 'grep', 'head', or 'tail' to find specific data."
            )
        return text

    stdout = truncate(stdout, "STDOUT")
    stderr = truncate(stderr, "STDERR")
    # -------------------------

    if process.returncode == 0:
        return json.dumps({"status": "ok", "output": stdout or "[NO OUTPUT]"})
    else:
        # Include truncated stdout even on error, as it often contains clues
        return json.dumps({"status": "error", "output": stdout, "message": stderr})