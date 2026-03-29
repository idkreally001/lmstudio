import os
import queue
import logging
import threading
import subprocess
from functools import wraps

from flask import Flask, render_template, request, jsonify, send_file
from flask_socketio import SocketIO

from bridge import bridge
from main import initialize_agent, CONTAINER_NAME
from tools.cleanup_worker import start_cleanup_worker

# ===========================================================================
# Edge-TTS Pipeline (0% RAM Usage, Fast Cloud Voices)
# ===========================================================================
import edge_tts
import asyncio
import re
import time
import emoji
from flask import send_from_directory

TTS_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), 'tts'))
os.makedirs(TTS_DIR, exist_ok=True)

def init_tts():
    print(f"[*] Voice engine initialized. Saving audio strictly to {TTS_DIR}")

def cleanup_tts(max_files=5):
    """Deletes oldest TTS files if the count exceeds max_files."""
    try:
        files = [os.path.join(TTS_DIR, f) for f in os.listdir(TTS_DIR) if f.startswith('tts_') and f.endswith('.mp3')]
        files.sort(key=os.path.getmtime)  # oldest first
        
        while len(files) > max_files:
            os.remove(files.pop(0))
    except Exception as e:
        print(f"[!] TTS Cleanup Error: {e}")

# ---------------------------------------------------------------------------
# Logging — suppress noisy Werkzeug request lines
# ---------------------------------------------------------------------------
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ---------------------------------------------------------------------------
# App & Socket setup
# ---------------------------------------------------------------------------
app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# ---------------------------------------------------------------------------
# Security — token auth
# Set the DASHBOARD_TOKEN environment variable to enable protection.
# Leave unset (or empty) to disable (useful for local-only dev).
# ---------------------------------------------------------------------------
_API_TOKEN = os.environ.get('DASHBOARD_TOKEN', '').strip()


def require_token(f):
    """Decorator: rejects requests whose X-Token header doesn't match."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if _API_TOKEN and request.headers.get('X-Token') != _API_TOKEN:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Path safety — prevent directory traversal in file API
# ---------------------------------------------------------------------------
WORKSPACE_ROOT = os.path.realpath(
    os.environ.get('WORKSPACE_ROOT', os.path.join(os.path.dirname(__file__), 'workspace'))
)


def safe_path(user_path: str) -> str | None:
    """
    Resolves user_path relative to WORKSPACE_ROOT.
    Returns the mapped container path (/workspace/...) if it stays within WORKSPACE_ROOT,
    otherwise returns None.
    """
    resolved = os.path.realpath(os.path.join(WORKSPACE_ROOT, user_path))
    
    # Allow accessing the root workspace folder directly
    if resolved == WORKSPACE_ROOT:
        return "/workspace"
        
    if not resolved.startswith(WORKSPACE_ROOT + os.sep):
        return None
        
    # Map back to Linux container /workspace path for Docker commands
    rel_path = os.path.relpath(resolved, WORKSPACE_ROOT).replace('\\', '/')
    return f"/workspace/{rel_path}"


# ---------------------------------------------------------------------------
# Agent initialisation — deferred so Flask starts even on failure
# ---------------------------------------------------------------------------
def _startup():
    try:
        initialize_agent()
        print('[*] Agent initialized successfully.')
    except Exception as exc:
        print(f'[!] Agent initialization failed: {exc}')
        
    # Start the clean, folderized TTS system
    init_tts()


with app.app_context():
    _startup()
    # Start background cleanup worker for TTS audio files
    start_cleanup_worker(TTS_DIR, interval=300)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/chat', methods=['POST'])
@require_token
def chat():
    try:
        user_input = request.json.get('query', '').strip()
        if not user_input:
            return jsonify({'answer': 'Empty query received.'}), 400
            
        response = bridge.chat(user_input)
        
        # Audio rendering block processing
        audio_url = None
        
        # Strip tags and markdown for clean speech
        clean_text = re.sub(r'<think>[\s\S]*?</think>', '', response)
        clean_text = re.sub(r'### \[TOOL_OUTPUT\] ###[\s\S]*?### \[END_OUTPUT\] ###', '', clean_text)
        clean_text = re.sub(r'[*#~`_\[\]]', '', clean_text).strip()
        
        # Remove emojis completely so the voice doesn't stumble
        clean_text = emoji.replace_emoji(clean_text, replace='')
        
        if clean_text:
            filename = f"tts_{int(time.time())}.mp3"
            out_path = os.path.join(TTS_DIR, filename)
            
            # Use asyncio to execute edge-tts (takes zero local ram)
            async def render():
                # Adjusted to a Male voice (Christopher) and increased speed by 15%
                comm = edge_tts.Communicate(clean_text, "en-US-ChristopherNeural", rate="+15%")
                await comm.save(out_path)
            
            try:
                asyncio.run(render())
                audio_url = f"/tts/{filename}"
                # Clean up old files after successfully generating a new one
                cleanup_tts(5)
            except Exception as render_err:
                print(f"[!] TTS generation failure: {render_err}")
                
        return jsonify({'answer': response, 'audio_url': audio_url})
    except Exception as exc:
        return jsonify({'answer': f'Web App Error: {exc}'}), 500

@app.route('/tts/<path:filename>')
def serve_tts(filename):
    """Serves the generated audio directly out of the dedicated tts folder."""
    return send_from_directory(TTS_DIR, filename)


@app.route('/workspace')
@require_token
def get_workspace():
    try:
        if 'monitor_workspace' in bridge.registry:
            tree_output = bridge.registry['monitor_workspace']()
            return jsonify({'tree': tree_output})
        return jsonify({'tree': 'Workspace monitor tool not available.'})
    except Exception as exc:
        return jsonify({'tree': f'Error: {exc}'})


@app.route('/info')
@require_token
def get_info():
    return jsonify({
        'model': bridge.model,
        'container': CONTAINER_NAME,
        'tools_loaded': list(bridge.registry.keys()),
    })


@app.route('/audit')
@require_token
def get_audit():
    """
    Returns the last 50 lines of the audit log along with the file's
    modification time so the client can skip re-rendering unchanged content.
    """
    try:
        path = bridge.audit_log
        if not os.path.exists(path):
            return jsonify({'log': 'Audit log not found.', 'mtime': None})
        mtime = os.path.getmtime(path)
        with open(path, 'r', encoding='utf-8') as fh:
            log = ''.join(fh.readlines()[-50:])
        return jsonify({'log': log, 'mtime': mtime})
    except Exception as exc:
        return jsonify({'log': f'Error reading log: {exc}', 'mtime': None})


@app.route('/history')
@require_token
def get_history():
    """Returns the current conversation history for the sidebar."""
    messages = [
        {"role": m["role"], "content": m.get("content", "")}
        for m in bridge.history
        if m["role"] in ("user", "assistant") and m.get("content", "").strip()
    ]
    return jsonify({"history": messages})


@app.route('/status')
@require_token
def get_status():
    return jsonify({'tool': bridge.current_tool or 'Idle'})


@app.route('/stop', methods=['POST'])
@require_token
def stop_generation():
    was_running = not bridge.cancel_flag
    bridge.cancel_flag = True
    return jsonify({
        'status': 'stop_requested' if was_running else 'already_idle'
    })


@app.route('/logs')
@require_token
def get_logs():
    """Returns the last 200 lines from the rotating agent log."""
    log_path = os.path.join(os.path.dirname(__file__), 'workspace', 'logs', 'agent.log')
    try:
        if not os.path.exists(log_path):
            return jsonify({'log': 'No log file found.', 'lines': 0})
        with open(log_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()[-200:]
        return jsonify({'log': ''.join(lines), 'lines': len(lines)})
    except Exception as exc:
        return jsonify({'log': f'Error reading log: {exc}', 'lines': 0})


@app.route('/export')
@require_token
def export_audit():
    """Download the agent_audit.md as a Markdown file."""
    path = bridge.audit_log
    if not os.path.exists(path):
        return jsonify({'error': 'Audit log not found.'}), 404
    return send_file(path, as_attachment=True, download_name='agent_audit.md', mimetype='text/markdown')


@app.route('/run_script', methods=['POST'])
@require_token
def run_user_script():
    """Execute a code snippet inside the Docker sandbox and return the output."""
    data = request.json or {}
    code = data.get('code', '')
    lang = data.get('language', 'python')
    if not code.strip():
        return jsonify({'output': 'No code provided.'}), 400
    try:
        import subprocess as sp
        import base64
        encoded = base64.b64encode(code.encode()).decode()
        if lang in ('python', 'py'):
            cmd = f"echo '{encoded}' | base64 -d | python3"
        elif lang in ('bash', 'sh'):
            cmd = f"echo '{encoded}' | base64 -d | bash"
        else:
            cmd = f"echo '{encoded}' | base64 -d | python3"
        proc = sp.run(
            ['docker', 'exec', CONTAINER_NAME, 'bash', '-c', cmd],
            capture_output=True, text=True, timeout=60, encoding='utf-8'
        )
        output = proc.stdout + ('\n' + proc.stderr if proc.stderr else '')
        return jsonify({'output': output.strip() or '[NO OUTPUT]'})
    except Exception as exc:
        return jsonify({'output': f'Execution Error: {exc}'}), 500


@app.route('/api/files', methods=['GET', 'POST'])
@require_token
def handle_files():
    try:
        if 'manage_files' not in bridge.registry:
            return jsonify({'error': 'File manager tool not loaded.'}), 503

        if request.method == 'GET':
            raw = request.args.get('path', '')
            path = safe_path(raw)
            if not path:
                return jsonify({'error': 'Invalid or unsafe path.'}), 400
            result = bridge.registry['manage_files'](action='read', path=path)
            return jsonify({'content': result})

        # POST — write
        data    = request.json or {}
        raw     = data.get('path', '')
        content = data.get('content', '')
        path    = safe_path(raw)
        if not path:
            return jsonify({'status': 'Invalid or unsafe path.'}), 400
        result = bridge.registry['manage_files'](action='write', path=path, content=content)
        ok = not str(result).startswith('File Error')
        return jsonify({'status': 'success' if ok else result})

    except Exception as exc:
        return jsonify({'status': f'Error: {exc}'}), 500


# ---------------------------------------------------------------------------
# Terminal — Windows-compatible Docker bridge via Socket.io
# ---------------------------------------------------------------------------
_terminal_processes: dict[str, subprocess.Popen] = {}


def _build_popen_kwargs() -> dict:
    kwargs = dict(
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=0,
    )
    if os.name == 'nt':
        kwargs['creationflags'] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _normalize(text: str) -> str:
    """Convert bare LF to CRLF for xterm.js, avoid double-converting CRLF."""
    return text.replace('\r\n', '\n').replace('\n', '\r\n')


def _read_from_process(sid: str, process: subprocess.Popen) -> None:
    """
    Reads process stdout one byte at a time (required on Windows pipes)
    but batches output using a queue before emitting to reduce Socket.io
    event overhead on high-throughput output.
    """
    out_queue: queue.Queue[str | None] = queue.Queue()

    def _reader():
        try:
            for char in iter(lambda: process.stdout.read(1), ''):
                out_queue.put(char)
        finally:
            out_queue.put(None)  # sentinel

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    buf: list[str] = []
    while True:
        try:
            char = out_queue.get(timeout=0.05)
            if char is None:
                # Process ended — flush remaining buffer
                if buf:
                    socketio.emit('terminal_output', {'output': ''.join(buf)}, room=sid)
                break
            buf.append(char)
        except queue.Empty:
            if buf:
                output = ''.join(buf).replace('\n', '\r\n')
                socketio.emit('terminal_output', {'output': output}, room=sid)
                buf = []


@socketio.on('connect')
def on_connect():
    sid = request.sid
    try:
        process = subprocess.Popen(
            ['docker', 'exec', '-i', '-e', 'TERM=xterm-256color', CONTAINER_NAME, 'bash', '-i'],
            **_build_popen_kwargs()
        )
        _terminal_processes[sid] = process
        thread = threading.Thread(target=_read_from_process, args=(sid, process), daemon=True)
        thread.start()

    except FileNotFoundError:
        socketio.emit('terminal_output', {
            'output': '\r\n\033[31m[!] Docker not found — is it installed and on PATH?\033[0m\r\n'
        }, room=sid)
    except Exception as exc:
        socketio.emit('terminal_output', {
            'output': f'\r\n\033[31m[!] Terminal error: {exc}\033[0m\r\n'
        }, room=sid)


# Xterm.js sends escape sequences for special keys that Windows pipes
# cannot handle. This strips them down to only safe printable ASCII
# plus the most essential control characters (Enter, Backspace, Tab, Ctrl+C).
_SAFE_CONTROL = {'\r', '\n', '\x7f', '\t', '\x03'}

def _sanitize_terminal_input(raw: str) -> str:
    """Drop ANSI escape sequences and non-printable chars unsafe on Windows pipes."""
    out = []
    i = 0
    while i < len(raw):
        ch = raw[i]
        # Strip ESC sequences (ESC [ ... final_byte)
        if ch == '\x1b':
            i += 1
            if i < len(raw) and raw[i] == '[':
                i += 1
                while i < len(raw) and raw[i] not in 'ABCDEFGHJKSTfmnsu~':
                    i += 1
                i += 1  # skip final byte
            continue
        if ch.isprintable() or ch in _SAFE_CONTROL:
            out.append(ch)
        i += 1
    return ''.join(out)


@socketio.on('terminal_input')
def on_terminal_input(data):
    sid     = request.sid
    process = _terminal_processes.get(sid)
    if process and process.stdin:
        try:
            safe = _sanitize_terminal_input(data.get('input', ''))
            if safe:
                process.stdin.write(safe)
                process.stdin.flush()
        except Exception as exc:
            print(f'[!] Terminal write error ({sid}): {exc}')


@socketio.on('disconnect')
def on_disconnect():
    sid     = request.sid
    process = _terminal_processes.pop(sid, None)
    if process:
        try:
            process.terminate()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print(f'\n[*] Dashboard online → http://127.0.0.1:{port}')
    socketio.run(app, port=port, debug=False, log_output=False)