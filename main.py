from bridge import bridge
import subprocess
import sys
import os

CONTAINER_NAME = "ai_sandbox"

def ensure_sandbox():
    """Checks if the Docker container is running; attempts to start it if not."""
    try:
        # Added timeout and check=False to prevent hangs
        status = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER_NAME],
            capture_output=True, text=True, timeout=5
        )
        
        if "true" not in status.stdout:
            print(f"[*] Container '{CONTAINER_NAME}' is not running. Attempting start...")
            subprocess.run(["docker", "start", CONTAINER_NAME], check=True)
            print(f"[*] Container '{CONTAINER_NAME}' started successfully.")
    except subprocess.TimeoutExpired:
        print(f"[!] Error: Docker command timed out. Is Docker Desktop running?")
        sys.exit(1)
    except subprocess.CalledProcessError:
        print(f"[!] Critical: Container '{CONTAINER_NAME}' not found. Please create it.")
        sys.exit(1)
    except Exception as e:
        print(f"[!] Docker Check Failed: {e}")
        sys.exit(1)

def initialize_agent():
    """Initializes tools and checks model connection."""
    print("\n[*] Initializing Research Agent...")
    
    # 1. Ensure the sandbox is ready first
    ensure_sandbox()
    
    # 2. Force tool registration into the bridge
    # We check if registry is empty to avoid double-loading
    if not bridge.registry:
        bridge.load_tools()
    
    # 3. Verify Model Connection
    if bridge.model == "default-model":
        print("[!] Warning: AI Model not detected. Ensure LM Studio is running on port 1234.")
    else:
        print(f"[*] Agent ready using model: {bridge.model}")
    
    print(f"[*] Registry status: {len(bridge.registry)} tools indexed.\n")

def start_terminal():
    """The interactive CLI loop."""
    print("\n--- AI Agent Online: Terminal Mode ---")
    while True:
        try:
            query = input("\n[User] > ")
            if query.lower() in ["exit", "quit"]: break
            if not query.strip(): continue

            print("\n[AI is thinking...]")
            answer = bridge.chat(query)
            print(f"\nAI: {answer}\n" + "-" * 50)
        except KeyboardInterrupt:
            print("\n[*] Shutting down...")
            break

if __name__ == "__main__":
    # If running main.py directly (CLI Mode)
    initialize_agent()
    start_terminal()