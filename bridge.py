import os
import uuid
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
    return {k: fix(v) for k, v in args.items()}


def is_looping(history):
    """Detect if the last 3 tool turns were errors (potential loop)."""
    tools_turns = [m for m in history if m.get("role") == "tool" and m.get("content")]
    if len(tools_turns) >= 3:
        last_3 = tools_turns[-3:]
        return all("error" in m["content"].lower() or "critical" in m["content"].lower() for m in last_3)
    return False


class AIBridge:
    CONV_DIR = os.path.join(os.path.dirname(__file__), "conversations")

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
        self.current_conversation_id = str(uuid.uuid4())

        self.on_audit_callback = None
        self.on_workspace_callback = None
        self.on_history_callback = None
        self.on_conversations_callback = None

        os.makedirs(self.CONV_DIR, exist_ok=True)

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

    def get_available_models(self):
        try:
            response = requests.get(f"{self.url}/models", timeout=5)
            return response.json().get("data", [])
        except Exception:
            return []

    def set_model(self, model_id):
        self.model = model_id
        return self.model

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
            
        if self.on_audit_callback:
            self.on_audit_callback()

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
            system_prompt = raw_prompt
            self.history.append({"role": "system", "content": system_prompt})
            # --- NEW: Log the initial system message ---
            self.log_action("System", system_prompt)

        if is_looping(self.history):
            prompt += "\n\n(INTERNAL MONITOR: You are in an error loop. Verify file paths and names immediately using 'ls'.)"

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
                        
                        if self.on_workspace_callback:
                            self.on_workspace_callback()

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

    # ------------------------------------------------------------------
    # Conversation persistence
    # ------------------------------------------------------------------
    def save_conversation(self, title=None):
        if not self.history:
            return None
        if not title:
            for msg in self.history:
                if msg["role"] == "user":
                    title = msg["content"][:80]
                    break
            title = title or "Untitled"

        path = os.path.join(self.CONV_DIR, f"{self.current_conversation_id}.json")
        created = datetime.now().isoformat()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    created = json.load(f).get("created_at", created)
            except Exception:
                pass

        data = {
            "id": self.current_conversation_id,
            "title": title,
            "created_at": created,
            "updated_at": datetime.now().isoformat(),
            "message_count": len(self.history),
            "history": self.history,
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        if self.on_conversations_callback:
            self.on_conversations_callback()
            
        return data["id"]

    def list_conversations(self):
        convos = []
        for fname in os.listdir(self.CONV_DIR):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.CONV_DIR, fname), "r", encoding="utf-8") as f:
                    d = json.load(f)
                convos.append({
                    "id": d["id"], "title": d.get("title", "Untitled"),
                    "created_at": d.get("created_at"), "updated_at": d.get("updated_at"),
                    "message_count": d.get("message_count", 0),
                })
            except Exception:
                pass
        convos.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return convos

    def load_conversation(self, conv_id):
        path = os.path.join(self.CONV_DIR, f"{conv_id}.json")
        if not os.path.exists(path):
            return False
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.history = data.get("history", [])
        self.current_conversation_id = conv_id
        
        if self.on_conversations_callback:
            self.on_conversations_callback()
        if self.on_history_callback:
            self.on_history_callback()
            
        return True

    def delete_conversation(self, conv_id):
        path = os.path.join(self.CONV_DIR, f"{conv_id}.json")
        if os.path.exists(path):
            os.remove(path)
            if self.current_conversation_id == conv_id:
                self.new_conversation()
            if self.on_conversations_callback:
                self.on_conversations_callback()
            return True
        return False

    def new_conversation(self):
        if self.history:
            self.save_conversation()
        self.history = []
        self.current_conversation_id = str(uuid.uuid4())
        
        if self.on_conversations_callback:
            self.on_conversations_callback()
        if self.on_history_callback:
            self.on_history_callback()
            
        return self.current_conversation_id

    # ------------------------------------------------------------------
    # Streaming chat
    # ------------------------------------------------------------------
    def chat_stream(self, prompt, emit_fn):
        """Streaming chat — emits events via emit_fn(event_name, payload_dict)."""
        self.cancel_flag = False
        cfg = load_config()

        # --- Slash commands (synchronous) ---
        slash = prompt.strip()
        if slash == "/clear":
            self.history = []
            self.log_action("System", "Chat history cleared")
            emit_fn("chat_token", {"content": "Chat context has been reset."})
            emit_fn("chat_done", {})
            return
        if slash == "/reset":
            import subprocess
            try:
                subprocess.run(["docker", "exec", "ai_sandbox", "bash", "-c", "rm -rf /workspace/*"])
                msg = "Workspace cleared."
            except Exception as e:
                msg = f"Reset Error: {e}"
            emit_fn("chat_token", {"content": msg})
            emit_fn("chat_done", {})
            return
        if slash in ("/save", "/load"):
            result = self.save_session() if slash == "/save" else self.load_session()
            emit_fn("chat_token", {"content": result})
            emit_fn("chat_done", {})
            return

        # --- Init system prompt ---
        if not self.history:
            yaml_prompt = _load_prompts_yaml()
            raw_prompt = yaml_prompt or cfg.get("system_prompt")
            if isinstance(raw_prompt, list):
                raw_prompt = "\n".join(raw_prompt)
            system_prompt = raw_prompt or (
                "You are an Autonomous AI Research Agent operating securely within a Linux Docker sandbox."
            )
            self.history.append({"role": "system", "content": system_prompt})
            self.log_action("System", system_prompt)

        if is_looping(self.history):
            prompt += "\n\n(INTERNAL MONITOR: You are in an error loop. Verify file paths and names immediately using 'ls'.)"

        self.history.append({"role": "user", "content": prompt})
        self.log_action("User", prompt)
        
        if self.on_history_callback:
            self.on_history_callback()

        tools_used = 0
        last_failed_call = None
        self.current_tool = "Thinking..."

        while True:
            if self.cancel_flag:
                emit_fn("chat_done", {})
                return

            payload = {
                "model":       self.model,
                "messages":    self.history,
                "tools":       self.schemas if self.schemas else None,
                "tool_choice": "auto",
                "temperature": cfg["temperature"],
                "max_tokens":  cfg.get("max_tokens", 2048),
                "stream":      True,
            }

            try:
                resp = requests.post(
                    f"{self.url}/chat/completions",
                    json=payload, timeout=cfg["timeout"], stream=True,
                )

                content_buf = ""
                tc_buf = []  # tool call deltas
                finish = None

                for raw_line in resp.iter_lines():
                    if self.cancel_flag:
                        emit_fn("chat_done", {})
                        return
                    if not raw_line:
                        continue
                    line_s = raw_line.decode("utf-8", errors="replace").strip()
                    if line_s == "data: [DONE]":
                        break
                    if not line_s.startswith("data: "):
                        continue
                    try:
                        chunk = json.loads(line_s[6:])
                    except json.JSONDecodeError:
                        continue
                    if "choices" not in chunk or not chunk["choices"]:
                        continue

                    choice = chunk["choices"][0]
                    delta = choice.get("delta", {})
                    fr = choice.get("finish_reason")
                    if fr:
                        finish = fr

                    # Content tokens
                    if delta.get("content"):
                        t = delta["content"]
                        content_buf += t
                        emit_fn("chat_token", {"content": t})

                    # Tool call deltas
                    if delta.get("tool_calls"):
                        for tcd in delta["tool_calls"]:
                            idx = tcd.get("index", 0)
                            while len(tc_buf) <= idx:
                                tc_buf.append({"id": "", "function": {"name": "", "arguments": ""}})
                            if tcd.get("id"):
                                tc_buf[idx]["id"] = tcd["id"]
                            fn = tcd.get("function", {})
                            if fn.get("name"):
                                tc_buf[idx]["function"]["name"] = fn["name"]
                            if fn.get("arguments"):
                                tc_buf[idx]["function"]["arguments"] += fn["arguments"]

                # --- Handle response ---
                if finish == "tool_calls" or (tc_buf and finish != "stop"):
                    msg = {"role": "assistant", "content": content_buf or ""}
                    msg["tool_calls"] = [
                        {"id": tc["id"], "type": "function", "function": tc["function"]}
                        for tc in tc_buf
                    ]
                    self.history.append(msg)
                    if content_buf:
                        self.log_action("AI", content_buf)

                    for tc in tc_buf:
                        func_name = tc["function"]["name"]
                        call_id = tc["id"]
                        try:
                            args = json.loads(tc["function"]["arguments"])
                        except json.JSONDecodeError:
                            args = {}
                        args = _sanitize_args(args)

                        emit_fn("chat_tool_call", {"name": func_name, "args": args})
                        self.log_action("Tool Call", f"Function: {func_name}\nArguments: {json.dumps(args, indent=2)}")
                        self.current_tool = f"Running: {func_name}"
                        tools_used += 1

                        if func_name not in self.registry:
                            import difflib
                            sug = difflib.get_close_matches(func_name, self.registry.keys(), n=1)
                            hint = f" Did you mean '{sug[0]}'?" if sug else ""
                            raw_result = f"Error: '{func_name}' is not a valid tool.{hint}"
                        else:
                            try:
                                raw_result = str(self.registry[func_name](**args))
                            except Exception as te:
                                raw_result = f"Error executing {func_name}: {te}"

                        is_fail = "Error" in raw_result
                        sig = f"{func_name}::{json.dumps(args, sort_keys=True)}"
                        if is_fail:
                            if last_failed_call == sig:
                                raw_result += "\n\n[CRITICAL]: Same command failed twice. Stopping."
                            last_failed_call = sig
                        else:
                            last_failed_call = None

                        processed = smart_summarize(raw_result)
                        self.log_action("Tool Output", processed)
                        emit_fn("chat_tool_result", {"name": func_name, "result": processed})

                        self.history.append({
                            "role": "tool", "tool_call_id": call_id,
                            "name": func_name, "content": processed,
                        })
                        
                        if self.on_workspace_callback:
                            self.on_workspace_callback()

                        if "TERMINATE_SIGNAL:" in raw_result:
                            emit_fn("chat_done", {})
                            self.current_tool = None
                            return

                    if cfg["hard_limit"] and tools_used >= cfg["max_iterations"]:
                        emit_fn("chat_token", {"content": "\nStopped: Max tool actions reached."})
                        emit_fn("chat_done", {})
                        self.current_tool = None
                        return
                    continue  # next LLM turn

                # --- Normal text response ---
                msg = {"role": "assistant", "content": content_buf or ""}
                self.history.append(msg)
                
                if self.on_history_callback:
                    self.on_history_callback()
                
                if finish == "length":
                    # Auto-heal: model was cut off
                    self.log_action("System", "[Auto-Continue triggered due to exact token limit hit]")
                    self.history.append({"role": "user", "content": "[System: Your previous response was cut off due to token limits. Please continue exactly where you left off.]"})
                    continue
                
                if content_buf:
                    self.log_action("AI", content_buf)
                self.current_tool = None
                self.save_conversation()
                emit_fn("chat_done", {"content": content_buf})
                return

            except Exception as e:
                emit_fn("chat_token", {"content": f"Bridge Error: {e}"})
                emit_fn("chat_done", {})
                self.current_tool = None
                return


bridge = AIBridge()