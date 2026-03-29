# AI Research Agent

A fully autonomous AI research agent with a secure Docker sandbox, voice output (Edge-TTS), and a modern dark-mode IDE interface.

## Features

- **Secure Docker Sandbox** — All code runs inside an isolated `ai_sandbox` container with a non-root user (`sandbox:1000`).
- **Tool Registry** — Auto-discovers and registers tools from the `tools/` directory.
- **Retry Mechanism** — Docker and pip calls use exponential back-off retries.
- **Standardized JSON Responses** — All tools return `{"status": "ok", "output": "..."}` or `{"status": "error", "message": "..."}`.
- **Centralized Logging** — Rotating log files (`workspace/logs/agent.log`, 5 MB × 5 backups) with a `/logs` endpoint.
- **Voice Output** — Edge-TTS (cloud) generates audio with zero local RAM usage. Audio files are auto-pruned (max 5 files, 100 MB).
- **Dark Mode** — Toggle between dark and light themes using the moon/sun icon in the header.
- **Run Script** — Hover over any Python/Bash code block to reveal a "Run" button that executes the snippet in the sandbox.
- **History Export** — Download the full `agent_audit.md` as a Markdown file via the "Export" button.
- **GPU Support** — Set `"gpu": true` in `config.json` to enable CUDA acceleration (requires an NVIDIA GPU like RTX 4060).
- **Path Sanitization** — All file operations enforce `/workspace` boundaries to prevent directory traversal.
- **Circuit Breaker** — Stops infinite loops when the same tool call fails twice in a row.
- **Prompts Management** — System prompts are externalized to `prompts.yaml` for easy editing.

## Quick Start

### Prerequisites
- Python 3.10+
- Docker Desktop
- LM Studio running on port 1234

### Setup
```bash
# Option 1: Automated setup
bash setup.sh

# Option 2: Manual setup
python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt
docker build -t ai_sandbox_image .
docker run -d --name ai_sandbox -v "$(pwd)/workspace:/workspace" ai_sandbox_image
```

### Run
```bash
python web_app.py
```
Open `http://127.0.0.1:5000` in your browser.

## Configuration

### `config.json`
| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `max_tokens` | int | 4096 | Max tokens per response |
| `max_iterations` | int | 15 | Max tool calls per request |
| `temperature` | float | 0.1 | LLM sampling temperature |
| `hard_limit` | bool | false | Stop after max_iterations |
| `timeout` | int | 300 | Request timeout (seconds) |
| `audio_cache.max_files` | int | 5 | Max TTS audio files to keep |
| `audio_cache.max_size_mb` | int | 100 | Max total audio cache size |
| `gpu` | bool | true | Enable CUDA GPU acceleration |

### `prompts.yaml`
Edit `prompts.yaml` to modify the system prompt without touching `config.json`. The bridge loads prompts from YAML first, falling back to `config.json`.

### Validate Configuration
```bash
python manage_config.py --print
python manage_config.py --set temperature 0.3
```

## Model Selection

Model selection is handled entirely by **LM Studio**, not `config.json`. See [MODEL_SELECTION.md](MODEL_SELECTION.md) for details.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Main IDE interface |
| `/chat` | POST | Send a message to the agent |
| `/workspace` | GET | Get workspace file tree |
| `/info` | GET | Model and container info |
| `/audit` | GET | Last 50 lines of audit log |
| `/history` | GET | Conversation history |
| `/status` | GET | Current tool status |
| `/stop` | POST | Stop current generation |
| `/logs` | GET | Last 200 lines of rotating agent log |
| `/export` | GET | Download agent_audit.md |
| `/run_script` | POST | Execute a code snippet in sandbox |
| `/api/files` | GET/POST | Read/write files in workspace |
| `/tts/<file>` | GET | Serve TTS audio files |

## Security

- **Non-root container**: The Docker image runs as user `sandbox` (UID 1000).
- **Path sanitization**: All file operations validate paths stay within `/workspace`.
- **Token auth**: Set `DASHBOARD_TOKEN` env var to protect API endpoints.
- **Command whitelisting**: File manager only allows safe commands (`cat`, `ls`, `rm`, `mv`, `cp`, `chmod`, `mkdir`).

## Testing

```bash
# Unit tests
pytest tests/test_tools.py -v

# Integration test (requires running Docker container)
python tests/test_integration.py
```

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `Agent returned an empty response` | Now returns `[NO OUTPUT]` placeholder — this is expected for void operations |
| `Docker Error: Is the container running?` | Run `docker start ai_sandbox` |
| `Model not detected` | Ensure LM Studio is running on port 1234 |
| `TTS generation failure` | Check internet connection (Edge-TTS uses Azure cloud) |
| Tool call loops | Circuit breaker stops after 2 identical failures — check the audit log |
| GPU not detected | Verify `nvidia-smi` works and `"gpu": true` is set in `config.json` |

## Project Structure

```
LmStudio/
├── bridge.py            # Core AI bridge (tool execution, prompts, circuit breaker)
├── web_app.py           # Flask server (routes, TTS, terminal, Socket.io)
├── main.py              # CLI entry point & agent initialization
├── config.json          # Runtime configuration
├── prompts.yaml         # Externalized system prompts
├── manage_config.py     # CLI config validator
├── Dockerfile           # Sandbox image (non-root)
├── requirements.txt     # Python dependencies
├── setup.sh             # One-command environment setup
├── MODEL_SELECTION.md   # How to switch models in LM Studio
├── tools/
│   ├── __init__.py      # Auto-discovery registry
│   ├── retry.py         # @retry decorator
│   ├── cleanup_worker.py# Background TTS file pruner
│   ├── file_manager.py  # File CRUD with path validation
│   ├── docker_executor.py # Shell command execution
│   ├── python_sandbox.py# Python script runner
│   ├── pkg_manager.py   # pip install/list
│   ├── git_manager.py   # git clone/pull/status
│   ├── logger.py        # Rotating log + experiment notes
│   ├── code_linter.py   # AST syntax checker
│   ├── web_scraper.py   # URL content extraction
│   ├── web_search.py    # DuckDuckGo search
│   ├── memory.py        # Postgres-backed memory
│   ├── sys_inspector.py # Container environment inspection
│   ├── workspace_monitor.py # File tree view
│   ├── sandbox_cleaner.py   # Workspace reset
│   ├── system_tools.py  # Tool registry listing
│   └── exit_handler.py  # Graceful session termination
├── static/
│   ├── script.js        # Frontend logic (dark mode, run script, export)
│   ├── style.css        # Dark/light theme, TTS tooltips, animations
│   └── favicon.png
├── templates/
│   └── index.html       # Main IDE template
├── tests/
│   ├── test_tools.py    # Unit tests
│   └── test_integration.py # CSV→Script→Delete workflow test
└── .github/workflows/
    └── ci.yml           # Lint, test, Docker build
```

## License

MIT
