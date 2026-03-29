// =============================================================================
// CONFIG
// Inject your token here or via a <meta> tag / env-baked variable.
// Must match DASHBOARD_TOKEN in the server environment.
// =============================================================================
const API_TOKEN = document.querySelector('meta[name="dashboard-token"]')?.content || '';

// =============================================================================
// ELEMENTS
// =============================================================================
const chatContainer    = document.getElementById('chat-container');
const systemTerminal   = document.getElementById('system-terminal');
const userInput        = document.getElementById('user-input');
const sendBtn          = document.getElementById('send-btn');
const popup            = document.getElementById('command-popup');
const confirmModal     = document.getElementById('confirm-modal');
const stopBtnContainer = document.getElementById('stop-btn-container');
const editorPane       = document.getElementById('editor-pane');
const fileNameLabel    = document.getElementById('current-file-name');

// =============================================================================
// GLOBAL STATE
// =============================================================================
let currentController = null;
let isGenerating      = false;
let statusInterval    = null;   // guarded — never double-created
let currentFilePath   = null;
let _auditCache       = { mtime: null, log: '' };
let currentAudio      = null;
let currentAction     = null;

// =============================================================================
// HELPERS — authenticated fetch
// =============================================================================
function apiFetch(url, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (API_TOKEN) headers['X-Token'] = API_TOKEN;
    return fetch(url, { ...options, headers });
}

// =============================================================================
// 1. SOCKET.IO
// =============================================================================
const socket = io();

// =============================================================================
// 2. ACE EDITOR
// =============================================================================
ace.require('ace/ext/language_tools');
const editor = ace.edit('editor');
editor.setTheme('ace/theme/github_dark');
editor.session.setMode('ace/mode/python');
editor.setOptions({
    fontSize: '13px',
    showPrintMargin: false,
    useSoftTabs: true,
    tabSize: 4,
    enableBasicAutocompletion: true,
    enableLiveAutocompletion: true,
});

// =============================================================================
// 3. XTERM + FIT ADDON
// =============================================================================
const term = new Terminal({
    cursorBlink: true,
    theme: { background: '#000000', foreground: '#ffffff', cursor: '#3b82f6' },
    fontSize: 12,
    fontFamily: '"Fira Code", monospace',
});
const fitAddon = new FitAddon.FitAddon();
term.loadAddon(fitAddon);
term.open(document.getElementById('terminal'));
fitAddon.fit();

socket.on('terminal_output', ({ output }) => term.write(output));
term.onData(data => socket.emit('terminal_input', { input: data }));

// =============================================================================
// 4. WORKSPACE & FILE MANAGEMENT
// =============================================================================
async function refreshWorkspace() {
    try {
        const r = await apiFetch('/workspace');
        if (!r.ok) return;
        const { tree } = await r.json();
        const treeEl = document.getElementById('workspace-tree');
        if (!tree) return;

        treeEl.innerHTML = '';
        // Strip any header/error lines — only process lines that look like paths
        tree.split('\n').forEach(line => {
            const path = line.trim().replace('./', '');
            if (!path || path === '.' || !path.match(/^[\w\-\.\/]+$/)) return;

            const div = document.createElement('div');
            const isDir = path.endsWith('/');
            div.className = 'hover:bg-gray-800 cursor-pointer px-2 py-1 rounded transition flex items-center gap-2 group text-gray-300';
            div.setAttribute('role', isDir ? 'treeitem' : 'treeitem');
            div.innerHTML = `
                <i class="fas ${isDir ? 'fa-folder text-yellow-500' : 'fa-file-code text-blue-400'} text-[10px]" aria-hidden="true"></i>
                <span class="truncate">${escapeHtml(path)}</span>`;

            if (!isDir) {
                div.onclick = () => openFileInEditor(path);
                div.setAttribute('tabindex', '0');
                div.addEventListener('keydown', e => { if (e.key === 'Enter') openFileInEditor(path); });
            }
            treeEl.appendChild(div);
        });
    } catch (e) {
        console.error('Workspace refresh failed:', e);
    }
}

async function openFileInEditor(path) {
    fileNameLabel.textContent = `${path} (loading…)`;
    try {
        const r = await apiFetch(`/api/files?path=${encodeURIComponent(path)}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const { content } = await r.json();

        currentFilePath = path;
        fileNameLabel.textContent = path;
        editor.setValue(content, -1);

        const ext = path.split('.').pop();
        const modeMap = { py: 'python', md: 'markdown', html: 'html', js: 'javascript', json: 'json', css: 'css', sh: 'sh', txt: 'text' };
        editor.session.setMode(`ace/mode/${modeMap[ext] || 'text'}`);

        editorPane.classList.add('active');
        setTimeout(() => editor.resize(), 310);
    } catch (err) {
        fileNameLabel.textContent = `Error loading ${path}`;
        console.error('Failed to load file:', err);
    }
}

async function saveCurrentFile() {
    if (!currentFilePath) return;
    const saveBtn = document.getElementById('editor-save-btn');
    saveBtn.textContent = 'Saving…';
    saveBtn.disabled = true;

    try {
        const r = await apiFetch('/api/files', {
            method: 'POST',
            body: JSON.stringify({ path: currentFilePath, content: editor.getValue() }),
        });
        const result = await r.json();
        saveBtn.textContent = result.status === 'success' ? 'Saved!' : 'Error';
    } catch (err) {
        saveBtn.textContent = 'Failed';
        console.error('Save failed:', err);
    } finally {
        setTimeout(() => {
            saveBtn.textContent = 'Save';
            saveBtn.disabled = false;
        }, 2000);
    }
}

// =============================================================================
// 5. UI TOGGLES & COMMANDS
// =============================================================================
function toggleEditor() {
    editorPane.classList.remove('active');
}

function toggleTerminal() {
    const termPane   = document.getElementById('terminal-pane');
    const overlay    = document.getElementById('input-overlay');
    const toggleBtn  = document.getElementById('terminal-toggle-btn');
    const isOpen     = termPane.classList.contains('h-48');

    if (isOpen) {
        termPane.classList.remove('h-48');
        termPane.style.height   = '32px';
        overlay.style.bottom    = '32px';
        toggleBtn.innerHTML     = '<i class="fas fa-chevron-up" aria-hidden="true"></i>';
        toggleBtn.setAttribute('aria-expanded', 'false');
    } else {
        termPane.classList.add('h-48');
        termPane.style.height   = '12rem';
        overlay.style.bottom    = '12rem';
        toggleBtn.innerHTML     = '<i class="fas fa-chevron-down" aria-hidden="true"></i>';
        toggleBtn.setAttribute('aria-expanded', 'true');
    }

    setTimeout(() => { editor.resize(); fitAddon.fit(); }, 310);
}

function applyCommand(cmd) {
    popup.classList.add('hidden');

    if (cmd === '/clear') {
        currentAction = '/clear';
        document.getElementById('modal-header-text').textContent = 'Clear Conversation';
        document.getElementById('modal-body-text').textContent   = 'Are you sure you want to reset the agent\'s chat context? Workspace files will be kept.';
        confirmModal.classList.remove('hidden');
        confirmModal.classList.add('flex');
        requestAnimationFrame(() => document.getElementById('modal-cancel-btn').focus());
        return;
    }
    if (cmd === '/reset') {
        currentAction = '/reset';
        document.getElementById('modal-header-text').textContent = 'Reset Environment';
        document.getElementById('modal-body-text').textContent   = 'Are you sure you want to wipe ALL files in the /workspace directory? This cannot be undone.';
        confirmModal.classList.remove('hidden');
        confirmModal.classList.add('flex');
        requestAnimationFrame(() => document.getElementById('modal-cancel-btn').focus());
        return;
    }
    
    userInput.value = cmd;
    sendMessage();
}

function closeModal() {
    confirmModal.classList.add('hidden');
    confirmModal.classList.remove('flex');
    userInput.focus();
}

// Close modal on backdrop click
confirmModal.addEventListener('click', e => {
    if (e.target === confirmModal) closeModal();
});

// Close modal on Escape
document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        if (!confirmModal.classList.contains('hidden')) { closeModal(); }
    }
});

async function confirmClear() {
    closeModal();
    const action = currentAction || '/reset';
    renderMessage('ai', `*Executing ${action === '/clear' ? 'chat' : 'environment'} reset…*`);
    try {
        await apiFetch('/chat', {
            method: 'POST',
            body: JSON.stringify({ query: action }),
        });
        location.reload();
    } catch (e) {
        renderMessage('ai', `**Error:** Failed to execute ${action}. Please try again.`);
        console.error('confirmClear failed:', e);
    }
}

// =============================================================================
// 6. COMMUNICATION & TELEMETRY
// =============================================================================
async function fetchStatus() {
    try {
        const r = await apiFetch('/status');
        if (!r.ok) return;
        const { tool } = await r.json();
        const loader = document.getElementById('ai-loading');
    } catch (e) { /* non-fatal */ }
}

async function sendMessage() {
    const query = userInput.value.trim();
    if (!query || sendBtn.disabled) return;

    renderMessage('user', query);
    userInput.value = '';
    userInput.style.height = 'auto';
    popup.classList.add('hidden');
    setLoading(true);

    currentController = new AbortController();

    try {
        const r = await apiFetch('/chat', {
            method: 'POST',
            body: JSON.stringify({ query }),
            signal: currentController.signal,
        });
        if (!r.ok) throw new Error(`Server error: ${r.status}`);
        const { answer, audio_url } = await r.json();
        renderMessage('ai', answer, audio_url);
        
        refreshWorkspace();
    } catch (err) {
        if (err.name === 'AbortError') {
            renderMessage('ai', '<em class="text-gray-500 text-sm"><i class="fas fa-ban mr-2 text-red-400" aria-hidden="true"></i>Generation halted.</em>');
        } else {
            renderMessage('ai', `**Bridge Error:** ${escapeHtml(err.message)}`);
        }
    } finally {
        setLoading(false);
        currentController = null;
    }
}

function setLoading(loading) {
    isGenerating          = loading;
    userInput.disabled    = loading;
    sendBtn.disabled      = loading;

    if (loading) {
        stopBtnContainer.classList.remove('hidden');

        // Guard: never create more than one interval
        if (!statusInterval) {
            statusInterval = setInterval(fetchStatus, 1000);
        }

        const loader = document.createElement('div');
        loader.id        = 'ai-loading';
        loader.className = 'flex justify-start text-xs text-gray-500 italic loading-dots my-2';
        loader.setAttribute('aria-live', 'polite');
        loader.textContent = 'Agent is processing';
        chatContainer.appendChild(loader);
    } else {
        stopBtnContainer.classList.add('hidden');
        clearInterval(statusInterval);
        statusInterval = null;

        document.getElementById('ai-loading')?.remove();
        userInput.focus();
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function stopGeneration() {
    if (!isGenerating) return;
    currentController?.abort();
    if (currentAudio) currentAudio.pause();
    try {
        await apiFetch('/stop', { method: 'POST' });
    } catch (e) {
        console.error('Stop signal failed:', e);
    }
}

// =============================================================================
// 7. RENDERING
// =============================================================================
function escapeHtml(str) {
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

function renderMessage(role, text, audioUrl = null) {
    const div = document.createElement('div');
    div.className = role === 'user' ? 'flex justify-end' : 'flex justify-start relative group';

    let content = text;
    if (role === 'ai') {
        // Strip raw tool output blocks
        content = content.replace(/### \[TOOL_OUTPUT\] ###[\s\S]*?### \[END_OUTPUT\] ###/g, '');
        // Render <think> tags as collapsible reasoning blocks
        content = content.replace(
            /<think>([\s\S]*?)<\/think>/g,
            `<details class="mb-4 bg-gray-900/50 p-3 rounded-lg border border-gray-800">
                <summary class="text-xs text-blue-400 font-bold uppercase tracking-widest cursor-pointer">Internal Reasoning</summary>
                <div class="text-[11px] text-gray-400 mt-2 font-mono whitespace-pre-wrap leading-relaxed border-t border-gray-800 pt-2">$1</div>
             </details>`
        );
        // Parse markdown then sanitize — order matters
        content = DOMPurify.sanitize(marked.parse(content));
    }

    let ttsHtml = '';
    if (role === 'ai' && audioUrl) {
        ttsHtml = `
            <button onclick="toggleAudio('${audioUrl}', this)" 
                class="tts-button absolute top-2 right-2 p-1.5 rounded-lg bg-gray-800/80 border border-gray-700 text-blue-400 hover:text-white hover:bg-blue-600 transition-all shadow-md opacity-0 group-hover:opacity-100 focus:opacity-100 z-10"
                title="Play/Pause Audio">
                <i class="fas fa-play text-[10px]"></i>
            </button>`;
    }

    div.innerHTML = `
        <div class="${role === 'user' ? 'bg-blue-600' : 'bg-[#161b22] border border-gray-800'}
                     max-w-[85%] rounded-2xl px-5 py-4 shadow-xl mb-4 relative overflow-hidden group"
             role="${role === 'ai' ? 'article' : 'none'}">
            ${ttsHtml}
            <div class="markdown-body">${content}</div>
        </div>`;

    chatContainer.appendChild(div);
    div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));

    // Add "Run Script" button to code blocks
    div.querySelectorAll('pre').forEach(pre => {
        const codeEl = pre.querySelector('code');
        if (!codeEl) return;
        const lang = (codeEl.className.match(/language-(\w+)/) || [])[1] || 'python';
        if (!['python', 'py', 'bash', 'sh', 'javascript', 'js'].includes(lang)) return;
        const btn = document.createElement('button');
        btn.className = 'run-script-btn';
        btn.innerHTML = '<i class="fas fa-play" style="margin-right:3px"></i>Run';
        btn.title = `Run this ${lang} snippet in the sandbox`;
        btn.onclick = () => runCodeBlock(codeEl.textContent, lang, pre);
        pre.style.position = 'relative';
        pre.appendChild(btn);
    });
    
    // Maintain only the last 5 TTS buttons as active links to audio
    const allTtsButtons = document.querySelectorAll('.tts-button');
    if (allTtsButtons.length > 5) {
        for (let i = 0; i < allTtsButtons.length - 5; i++) {
            allTtsButtons[i].classList.add('hidden');
        }
    }

    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function toggleAudio(url, btn) {
    const icon = btn.querySelector('i');
    
    // If clicking a new audio source
    if (!currentAudio || currentAudio.src.indexOf(url) === -1) {
        if (currentAudio) {
            currentAudio.pause();
            // Reset previous button if it exists
            const prevBtn = document.querySelector('.tts-button i.fa-pause');
            if (prevBtn) {
                prevBtn.classList.replace('fa-pause', 'fa-play');
                prevBtn.parentElement.classList.remove('bg-blue-600/20', 'border-blue-500');
            }
        }
        
        currentAudio = new Audio(url);
        currentAudio.onended = () => {
            icon.classList.replace('fa-pause', 'fa-play');
            btn.classList.remove('bg-blue-600/20', 'border-blue-500');
        };
        currentAudio.play();
        icon.classList.replace('fa-play', 'fa-pause');
        btn.classList.add('bg-blue-600/20', 'border-blue-500');
    } else {
        // Toggling same audio
        if (currentAudio.paused) {
            currentAudio.play();
            icon.classList.replace('fa-play', 'fa-pause');
            btn.classList.add('bg-blue-600/20', 'border-blue-500');
        } else {
            currentAudio.pause();
            icon.classList.replace('fa-pause', 'fa-play');
            btn.classList.remove('bg-blue-600/20', 'border-blue-500');
        }
    }
}

// =============================================================================
// 8. AUDIT LOG — scroll-preserving, change-detecting poll
// =============================================================================
async function fetchAudit() {
    try {
        const r = await apiFetch('/audit');
        if (!r.ok) return;
        const { log, mtime } = await r.json();

        // Skip DOM update if nothing changed
        if (mtime && mtime === _auditCache.mtime) return;
        _auditCache = { mtime, log };

        const atBottom = systemTerminal.scrollHeight - systemTerminal.scrollTop
                         <= systemTerminal.clientHeight + 10;
        systemTerminal.textContent = log;
        if (atBottom) systemTerminal.scrollTop = systemTerminal.scrollHeight;
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// 9. BOOTSTRAP INFO
// =============================================================================
async function fetchInfo() {
    try {
        const r = await apiFetch('/info');
        if (!r.ok) return;
        const { model, container } = await r.json();
        document.getElementById('model-name').textContent     = model;
        document.getElementById('container-name').textContent = container;
        document.getElementById('model-badge').classList.remove('hidden');
        document.getElementById('container-badge').classList.remove('hidden');
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// 10. EVENT LISTENERS
// =============================================================================
userInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

userInput.addEventListener('input', () => {
    requestAnimationFrame(() => {
        userInput.style.height = 'auto';
        userInput.style.height = Math.min(userInput.scrollHeight, 200) + 'px';
    });
    popup.classList.toggle('hidden', !userInput.value.startsWith('/'));
});

window.addEventListener('resize', () => {
    fitAddon.fit();
    editor.resize();
});

// =============================================================================
// 12. HISTORY PANEL
// =============================================================================
function switchTab(tab) {
    const isHistory = tab === 'history';

    document.getElementById('panel-history').classList.toggle('hidden', !isHistory);
    document.getElementById('panel-audit').classList.toggle('hidden', isHistory);

    document.getElementById('tab-history').classList.toggle('text-blue-400', isHistory);
    document.getElementById('tab-history').classList.toggle('border-blue-500', isHistory);
    document.getElementById('tab-history').classList.toggle('text-gray-500', !isHistory);
    document.getElementById('tab-history').classList.toggle('border-transparent', !isHistory);

    document.getElementById('tab-audit').classList.toggle('text-blue-400', !isHistory);
    document.getElementById('tab-audit').classList.toggle('border-blue-500', !isHistory);
    document.getElementById('tab-audit').classList.toggle('text-gray-500', isHistory);
    document.getElementById('tab-audit').classList.toggle('border-transparent', isHistory);
}

async function fetchHistory() {
    try {
        const r = await apiFetch('/history');
        if (!r.ok) return;
        const { history } = await r.json();
        const list = document.getElementById('history-list');
        if (!history.length) return;

        list.innerHTML = '';
        history.forEach(msg => {
            const div = document.createElement('div');
            const isUser = msg.role === 'user';
            div.className = `p-2 rounded-lg text-[10px] leading-relaxed cursor-pointer hover:opacity-80 transition ${
                isUser ? 'bg-blue-900/30 text-blue-200 border border-blue-900/50' : 'bg-gray-800/50 text-gray-300 border border-gray-700/50'
            }`;
            div.innerHTML = `
                <div class="font-bold uppercase tracking-widest mb-1 ${isUser ? 'text-blue-400' : 'text-gray-500'}">
                    ${isUser ? '<i class="fas fa-user mr-1"></i>You' : '<i class="fas fa-robot mr-1"></i>Agent'}
                </div>
                <div class="truncate opacity-80">${escapeHtml(msg.content.slice(0, 120))}${msg.content.length > 120 ? '…' : ''}</div>`;
            // Click to copy message into input
            div.onclick = () => { userInput.value = msg.content; userInput.focus(); };
            list.appendChild(div);
        });
        list.scrollTop = list.scrollHeight;
    } catch (e) { /* non-fatal */ }
}

async function saveHistory() {
    try {
        const r = await apiFetch('/chat', {
            method: 'POST',
            body: JSON.stringify({ query: '/save' }),
        });
        const { answer } = await r.json();
        alert(answer);
    } catch (e) { console.error('Save failed:', e); }
}

async function loadHistory() {
    if (!confirm('Load previous session? This will replace current history.')) return;
    try {
        const r = await apiFetch('/chat', {
            method: 'POST',
            body: JSON.stringify({ query: '/load' }),
        });
        const { answer } = await r.json();
        renderMessage('ai', answer);
        fetchHistory();
    } catch (e) { console.error('Load failed:', e); }
}
window.addEventListener('load', () => {
    userInput.focus();
    setTimeout(() => { fitAddon.fit(); editor.resize(); }, 500);
});

fetchInfo();
fetchAudit();
fetchHistory();
refreshWorkspace();

setInterval(fetchInfo,        15_000);
setInterval(fetchAudit,        3_000);
setInterval(fetchHistory,      5_000);
setInterval(refreshWorkspace, 30_000);

// =============================================================================
// 13. DARK MODE TOGGLE
// =============================================================================
function toggleDarkMode() {
    document.body.classList.toggle('light-mode');
    const icon = document.querySelector('#dark-mode-toggle i');
    if (document.body.classList.contains('light-mode')) {
        icon.className = 'fas fa-sun text-yellow-400';
        localStorage.setItem('theme', 'light');
    } else {
        icon.className = 'fas fa-moon text-purple-400';
        localStorage.setItem('theme', 'dark');
    }
}

// Restore theme on load
if (localStorage.getItem('theme') === 'light') {
    document.body.classList.add('light-mode');
    const icon = document.querySelector('#dark-mode-toggle i');
    if (icon) icon.className = 'fas fa-sun text-yellow-400';
}

// =============================================================================
// 14. HISTORY EXPORT
// =============================================================================
function exportHistory() {
    window.location.href = '/export';
}

// =============================================================================
// 15. RUN CODE BLOCK
// =============================================================================
async function runCodeBlock(code, language, preElement) {
    // Show a loading state on the button
    const btn = preElement.querySelector('.run-script-btn');
    if (btn) {
        btn.innerHTML = '<i class="fas fa-spinner fa-spin" style="margin-right:3px"></i>Running...';
        btn.disabled = true;
    }

    try {
        const r = await apiFetch('/run_script', {
            method: 'POST',
            body: JSON.stringify({ code, language }),
        });
        const { output } = await r.json();

        // Remove any previous output block
        const existing = preElement.parentElement.querySelector('.script-output');
        if (existing) existing.remove();

        // Create output block
        const outDiv = document.createElement('div');
        outDiv.className = 'script-output';
        outDiv.textContent = output || '[NO OUTPUT]';
        preElement.parentElement.appendChild(outDiv);
    } catch (err) {
        console.error('Run script failed:', err);
    } finally {
        if (btn) {
            btn.innerHTML = '<i class="fas fa-play" style="margin-right:3px"></i>Run';
            btn.disabled = false;
        }
    }
}