import subprocess
import base64
import json
from bridge import bridge
from tools.retry import retry

CONTAINER_NAME = "ai_sandbox"

# Whitelist: only these base commands are allowed
ALLOWED_COMMANDS = {"cat", "ls", "rm", "mkdir", "mv", "cp", "chmod", "echo"}

def _validate_path(path):
    """Ensure path stays under /workspace to prevent directory traversal."""
    import posixpath
    resolved = posixpath.normpath(path)
    if not resolved.startswith("/workspace"):
        raise ValueError(f"Path '{path}' is outside the allowed /workspace directory.")
    return resolved

@bridge.tool({
    "type": "function",
    "function": {
        "name": "manage_files",
        "description": "Read, write, list, delete, move, copy, or chmod files inside the Docker sandbox.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["read", "write", "mkdir", "list", "delete", "move", "copy", "chmod"]},
                "path": {"type": "string", "description": "The path inside the container."},
                "content": {"type": "string", "description": "Required only for 'write' action. For 'move'/'copy' pass JSON {src,dst}. For 'chmod' pass JSON {mode}."}
            },
            "required": ["action", "path"]
        }
    }
})
def manage_files(action, path, content=None):
    try:
        # HARD FIX: Detect the 'space instead of slash' hallucination
        if path.startswith("/workspace "):
            actual_path = path.replace("/workspace ", "/workspace/", 1)
            # Don't just fix it silently—tell the agent it messed up
            return json.dumps({"status": "error", "message": f"CRITICAL: Path contains a space. Did you mean '{actual_path}'? Paths must be absolute and use slashes without spaces."})

        path = _validate_path(path)

        if action == "read":
            cmd = f'cat "{path}"'
        elif action == "write":
            encoded = base64.b64encode(content.encode()).decode()
            cmd = f'echo "{encoded}" | base64 -d > "{path}"'
        elif action == "mkdir":
            cmd = f'mkdir -p "{path}"'
        elif action == "list":
            cmd = f'ls -F "{path}"'
        elif action == "delete":
            cmd = f'rm -rf "{path}"'
        elif action == "move":
            args = json.loads(content) if content else {}
            src = _validate_path(args.get('src', ''))
            dst = _validate_path(args.get('dst', ''))
            cmd = f'mv "{src}" "{dst}"'
        elif action == "copy":
            args = json.loads(content) if content else {}
            src = _validate_path(args.get('src', ''))
            dst = _validate_path(args.get('dst', ''))
            cmd = f'cp -r "{src}" "{dst}"'
        elif action == "chmod":
            args = json.loads(content) if content else {}
            mode = args.get('mode', '644')
            cmd = f'chmod {mode} "{path}"'
        else:
            return json.dumps({"status": "error", "message": f"Unknown action: {action}"})

        result = _run_docker(cmd)
        return result
    except ValueError as ve:
        return json.dumps({"status": "error", "message": str(ve)})
    except Exception as e:
        return json.dumps({"status": "error", "message": f"File Error: {str(e)}"})

@retry(max_attempts=3, backoff_factor=0.5)
def _run_docker(cmd):
    process = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
        capture_output=True, text=True, encoding="utf-8", timeout=30
    )
    if process.returncode == 0:
        return json.dumps({"status": "ok", "output": process.stdout.strip() or "Action completed successfully."})
    else:
        return json.dumps({"status": "error", "message": process.stderr.strip()})