import os
import requests
import json
import importlib
import sys
from datetime import datetime

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False


def _load_prompts_yaml():
    """Load system_prompt from prompts.yaml if available."""
    if not _HAS_YAML:
        return None
    path = os.path.join(os.path.dirname(__file__), "prompts.yaml")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("system_prompt")
    except Exception:
        return None


def smart_summarize(text, max_lines=50, max_chars=2000):
    """Condenses long output to keep the AI from getting overwhelmed."""
    lines = text.splitlines()
    if len(lines) <= max_lines and len(text) <= max_chars:
        return text
    summary  = f"[Output Truncated - {len(lines)} lines total]\n"
    summary += "\n".join(lines[:max_lines // 2])
    summary += "\n\n... [SNIP] ...\n\n"
    summary += "\n".join(lines[-(max_lines // 2):])
    summary += f"\n\n[Full output was {len(text)} characters. Use grep or tail to see more.]"
    return summary


def load_config() -> dict:
    """Reads config.json fresh every call — no restart needed."""
    defaults = {
        "max_iterations": 10,
        "max_turns":      25,
        "temperature":    0.2,
        "hard_limit":     True,
        "timeout":        300,
        "system_prompt":  None,
    }
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            overrides = json.load(f)
        defaults.update(overrides)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[!] Config read error: {e}")
    return defaults


def _sanitize_args(args: dict) -> dict:
    """
    Recursively convert any Windows-style backslash paths in tool arguments
    to forward slashes so they work correctly inside the Linux container.
    Also strips the Windows drive prefix (e.g. C:\\Users\\...) down to
    just the relative portion so paths don't break Docker commands.
    """
    import re
    def fix(value):
        if isinstance(value, str):
            # Normalise backslashes to forward slashes
            value = value.replace('\\', '/')
            # Strip Windows drive letter prefix e.g. "C:/" down to "/"
            # This regex finds drive letters in the string (even inside commands)
            value = re.sub(r'(?i)\b[A-Z]:(?=/)', '', value)
            return value
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(i) for i in value]
        return value

    return {k: fix(v) for k, v in args.items()}


class AIBridge:
    def __init__(self, url="http://localhost:1234/v1", max_iterations=10, max_turns=25, hard_limit=True):
        self.url            = url
        self.max_iterations = max_iterations
        self.max_turns      = max_turns
        self.hard_limit     = hard_limit
        self.registry       = {}
        self.schemas        = []
        self.history        = []
        self.model          = self._get_active_model()
        self.audit_log      = "agent_audit.md"
        self.current_tool   = None
        self.cancel_flag    = False

        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(f"\n\n{'='*50}\n")
            f.write(f"NEW SESSION STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"{'='*50}\n\n")

    # ------------------------------------------------------------------
    # Session persistence
    # ------------------------------------------------------------------
    def save_session(self, filename="session_backup.json"):
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=4)
        return f"Session saved to {filename}."

    def load_session(self, filename="session_backup.json"):
        if os.path.exists(filename):
            with open(filename, "r", encoding="utf-8") as f:
                self.history = json.load(f)
            return f"Session loaded from {filename}."
        return f"No session found at {filename}."

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _get_active_model(self):
        try:
            response = requests.get(f"{self.url}/models", timeout=5)
            models   = response.json().get("data", [])
            return models[0]["id"] if models else "default-model"
        except Exception:
            return "default-model"

    def tool(self, schema):
        def decorator(func):
            name = schema["function"]["name"]
            # Prevent duplicate registration on module reload
            if name not in self.registry:
                self.registry[name] = func
                self.schemas.append(schema)
            return func
        return decorator

    def load_tools(self, folder="tools"):
        if not os.path.exists(folder):
            os.makedirs(folder)
        if os.getcwd() not in sys.path:
            sys.path.append(os.getcwd())
        for file in os.listdir(folder):
            if file.endswith(".py") and file != "__init__.py":
                module_name = f"{folder}.{file[:-3]}".replace("/", ".").replace("\\", ".")
                try:
                    module = importlib.import_module(module_name)
                    importlib.reload(module)
                except Exception as e:
                    print(f"[!] Error loading {module_name}: {e}")
        print(f"[*] Bridge: {len(self.schemas)} tools online.")

    def log_action(self, role, content):
        timestamp = datetime.now().strftime("%H:%M:%S")
        with open(self.audit_log, "a", encoding="utf-8") as f:
            f.write(f"### [{timestamp}] {role.upper()}\n{content}\n\n---\n")

    # ------------------------------------------------------------------
    # Main chat loop
    # ------------------------------------------------------------------
    def chat(self, prompt):
        self.cancel_flag = False
        cfg = load_config()  # fresh read every request

        if prompt.strip() == "/clear":
            self.history = []
            self.log_action("System", "Chat history cleared (/clear)")
            return "Chat context has been reset. Starting a fresh conversation."
        if prompt.strip() == "/reset":
            # Direct logic from reset_workspace tool to avoid circular dependency
            import subprocess
            try:
                subprocess.run(["docker", "exec", "ai_sandbox", "bash", "-c", "rm -rf /workspace/*"])
                status = "Workspace environment successfully cleared."
            except Exception as e:
                status = f"Reset Error: {e}"
            self.log_action("System", f"Environment Reset (/reset): {status}")
            return status
        if prompt.strip() == "/save":
            return self.save_session()
        if prompt.strip() == "/load":
            return self.load_session()

        if not self.history:
            # Prefer prompts.yaml > config.json > hardcoded fallback
            yaml_prompt = _load_prompts_yaml()
            raw_prompt = yaml_prompt or cfg.get("system_prompt")
            if isinstance(raw_prompt, list):
                raw_prompt = "\n".join(raw_prompt)
            system_prompt = raw_prompt or (
                    "You are an Autonomous AI Research Agent operating securely within a Linux Docker sandbox (ai_sandbox). "
                    "Your goal is to solve complex technical tasks, write and debug code, and perform research independently.\n\n"
                    "CRITICAL OPERATIONAL RULES:\n"
                    "1. ENVIRONMENT: You are restricted to the '/workspace' directory.\n"
                    "2. SAFE CODING: Wrap code in triple-quotes to prevent newline errors.\n"
                    "3. SELF-CORRECTION: If a script fails, read the error and fix it yourself.\n"
                    "4. DATA HANDLING: Summarize tool outputs; don't dump raw data.\n"
                    "5. EFFICIENCY: Monitor your action limits. Don't repeat failures.\n"
                    "6. REASONING: Use <think> blocks for planning. Keep final replies focused.\n"
                    "7. PATHS: Always use Linux-style forward-slash paths (e.g. /workspace/file.py)."
            )
            self.history.append({"role": "system", "content": system_prompt})
            # --- NEW: Log the initial system message ---
            self.log_action("System", system_prompt)

        self.history.append({"role": "user", "content": prompt})
        self.log_action("User", prompt)

        tools_used  = 0
        total_turns = 0
        last_failed_call = None
        self.current_tool = "Thinking..."

        while True:
            if self.cancel_flag:
                return "TERMINATED: Backend process was stopped."

            total_turns += 1

            payload = {
                "model":       self.model,
                "messages":    self.history,
                "tools":       self.schemas if self.schemas else None,
                "tool_choice": "auto",
                "temperature": cfg["temperature"],
                "max_tokens":  cfg.get("max_tokens", 2048),
            }

            try:
                response = requests.post(f"{self.url}/chat/completions", json=payload, timeout=cfg["timeout"])
                response_data = response.json()

                if 'choices' not in response_data:
                    return f"API Error: {response_data.get('error', 'Unknown error')}"

                msg = response_data['choices'][0]['message']

                raw_content = msg.get("content")
                if isinstance(raw_content, list):
                    msg["content"] = " ".join(
                        block.get("text", "") for block in raw_content
                        if isinstance(block, dict)
                    )
                elif raw_content is None:
                    msg["content"] = ""

                self.history.append(msg)

                if msg.get("content"):
                    self.log_action("AI", msg["content"])

                tool_calls = msg.get("tool_calls")
                if tool_calls:
                    if self.cancel_flag:
                        return "TERMINATED: Stopped before tool execution."

                    for call in tool_calls:
                        func_name = call["function"]["name"]
                        call_id   = call.get("id")

                        if func_name not in self.registry:
                            import difflib
                            # Find the closest matching tool name
                            suggestions = difflib.get_close_matches(func_name, self.registry.keys(), n=1)
                            hint = f" Did you mean '{suggestions[0]}?'" if suggestions else ""
                            
                            error_msg = f"Error: '{func_name}' is not a valid tool.{hint} Use 'list_available_tools' to see the registry."
                            
                            self.history.append({
                                "role": "tool",
                                "tool_call_id": call_id,
                                "name": func_name,
                                "content": error_msg,
                            })
                            continue
                        
                        raw_args  = call["function"]["arguments"]
                        args      = json.loads(raw_args) if isinstance(raw_args, str) else raw_args

                        # Sanitize paths before they reach the tool
                        args = _sanitize_args(args)

                        # --- NEW: Log the tool call (arguments) ---
                        self.log_action("Tool Call", f"Function: {func_name}\nArguments: {json.dumps(args, indent=2)}")

                        self.current_tool = f"Running: {func_name}"
                        tools_used += 1

                        try:
                            raw_result = str(self.registry[func_name](**args))
                        except Exception as tool_e:
                            raw_result = f"Error executing tool {func_name}: {tool_e}"

                        # Dynamic circuit-breaker to stop infinite failure loops
                        is_failure = "Error" in raw_result or ("STDERR:\n" in raw_result and len(raw_result.split("STDERR:\n")[-1].strip()) > 0)
                        call_signature = f"{func_name}::{json.dumps(args, sort_keys=True)}"
                        
                        if is_failure:
                            if last_failed_call == call_signature:
                                raw_result += "\n\n[CRITICAL SYSTEM INTERVENTION]: You just ran the exact same failed command twice in a row. STOP executing this immediately. Analyze the error above and provide a different solution."
                            last_failed_call = call_signature
                        else:
                            last_failed_call = None

                        processed_result = smart_summarize(raw_result)

                        # --- NEW: Log the tool output ---
                        self.log_action("Tool Output", processed_result)

                        self.history.append({
                            "role":         "tool",
                            "tool_call_id": call_id,
                            "name":         func_name,
                            "content":      processed_result,
                        })

                        if "TERMINATE_SIGNAL:" in raw_result:
                            return raw_result.replace("TERMINATE_SIGNAL:", "Research Concluded:").strip()

                    if cfg["hard_limit"] and tools_used >= cfg["max_iterations"]:
                        return "Stopped: Max tool actions reached."
                    continue

                content = msg.get("content", "")
                self.current_tool = None

                if content:
                    return content.split("</think>")[-1].strip() if "</think>" in content else content
                return "[NO OUTPUT]"

            except Exception as e:
                return f"Bridge Error: {e}"

bridge = AIBridge()