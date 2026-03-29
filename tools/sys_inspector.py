import subprocess
import json
from bridge import bridge

CONTAINER_NAME = "ai_sandbox"

@bridge.tool({
    "type": "function",
    "function": {
        "name": "inspect_sandbox",
        "description": "Surgically check the container's environment. Use 'filter' to avoid long outputs.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string", 
                    "enum": ["installed_apps", "cpu_ram", "processes", "network"]
                },
                "filter": {
                    "type": "string", 
                    "description": "Optional: Search for a specific package or process name (e.g., 'pillow' or 'python')."
                }
            },
            "required": ["query"]
        }
    }
})
def inspect_sandbox(query, filter=None):
    queries = {
        "installed_apps": "dpkg --get-selections",
        "cpu_ram": "free -h",
        "processes": "ps aux",
        "network": "ip addr"
    }

    base_cmd = queries.get(query)
    if not base_cmd:
        return json.dumps({"status": "error", "message": f"Invalid query."})

    # If a filter is provided, pipe the output to grep
    if filter:
        # Using -i for case-insensitive and searching for the specific term
        cmd = f"{base_cmd} | grep -i '{filter}'"
    else:
        cmd = base_cmd

    try:
        process = subprocess.run(
            ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
            capture_output=True, text=True, timeout=15, encoding="utf-8"
        )
        
        # If grep finds nothing, it returns code 1, but that's a valid 'not found' result
        if process.returncode == 0:
            return json.dumps({"status": "ok", "output": process.stdout.strip()})
        elif process.returncode == 1 and filter:
            return json.dumps({"status": "ok", "output": f"'{filter}' not found in {query}."})
        else:
            return json.dumps({"status": "error", "message": process.stderr.strip() or "Command failed"})
            
    except Exception as e:
        return json.dumps({"status": "error", "message": str(e)})