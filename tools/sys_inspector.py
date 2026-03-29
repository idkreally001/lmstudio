import subprocess
import json
from bridge import bridge

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "inspect_sandbox",
        "description": "Check the container's environment (packages, RAM, or processes).",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "enum": ["installed_apps", "cpu_ram", "processes", "network"]}
            },
            "required": ["query"]
        }
    }
})
def inspect_sandbox(query):
    queries = {
        "installed_apps": "dpkg --get-selections",
        "cpu_ram": "free -h",
        "processes": "ps aux",
        "network": "ip addr"
    }

    cmd = queries.get(query)
    if not cmd:
        return json.dumps({"status": "error", "message": f"Invalid query '{query}'. Valid: {list(queries.keys())}"})

    try:
        process = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=15, encoding="utf-8"
        )
        if process.returncode == 0:
            return json.dumps({"status": "ok", "output": process.stdout.strip() or "[NO OUTPUT]"})
        else:
            return json.dumps({"status": "error", "message": process.stderr.strip()})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"Inspection Error: {str(e)}"})