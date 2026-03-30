// =============================================================================
// CONFIG
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
let statusInterval    = null;
let currentFilePath   = null;
let _auditCache       = { mtime: null, log: '' };
let currentAudio      = null;
let currentAction     = null;
let streamState       = null;

// =============================================================================
// HELPERS
// =============================================================================
function apiFetch(url, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers };
    if (API_TOKEN) headers['X-Token'] = API_TOKEN;
    return fetch(url, { ...options, headers });
}

function escapeHtml(str) {
    return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

// =============================================================================
// STREAM PARSER — handles <think> tag detection across chunk boundaries
// =============================================================================
class StreamParser {
    constructor() {
        this.buffer = '';
        this.inThink = false;
    }

    feed(text) {
        this.buffer += text;
        const events = [];
        let safety = 0;

        while (this.buffer.length > 0 && safety++ < 500) {
            if (!this.inThink) {
                const idx = this.buffer.indexOf('<think>');
                if (idx === 0) {
                    this.buffer = this.buffer.slice(7);
                    this.inThink = true;
                    events.push({ type: 'thinking_start' });
                } else if (idx > 0) {
                    events.push({ type: 'content', text: this.buffer.slice(0, idx) });
                    this.buffer = this.buffer.slice(idx);
                } else {
                    // Check for partial <think> at end
                    const partial = this._partialMatch('<think>');
                    if (partial > 0 && this.buffer.length - partial < this.buffer.length) {
                        const safe = this.buffer.slice(0, this.buffer.length - partial);
                        if (safe) events.push({ type: 'content', text: safe });
                        this.buffer = this.buffer.slice(this.buffer.length - partial);
                        break;
                    }
                    events.push({ type: 'content', text: this.buffer });
                    this.buffer = '';
                }
            } else {
                const idx = this.buffer.indexOf('</think>');
                if (idx === 0) {
                    this.buffer = this.buffer.slice(8);
                    this.inThink = false;
                    events.push({ type: 'thinking_end' });
                } else if (idx > 0) {
                    events.push({ type: 'thinking', text: this.buffer.slice(0, idx) });
                    this.buffer = this.buffer.slice(idx);
                } else {
                    const partial = this._partialMatch('</think>');
                    if (partial > 0) {
                        const safe = this.buffer.slice(0, this.buffer.length - partial);
                        if (safe) events.push({ type: 'thinking', text: safe });
                        this.buffer = this.buffer.slice(this.buffer.length - partial);
                        break;
                    }
                    events.push({ type: 'thinking', text: this.buffer });
                    this.buffer = '';
                }
            }
        }
        return events;
    }

    _partialMatch(tag) {
        for (let i = Math.min(tag.length - 1, this.buffer.length); i >= 1; i--) {
            if (this.buffer.endsWith(tag.slice(0, i))) return i;
        }
        return 0;
    }

    flush() {
        const events = [];
        if (this.buffer) {
            events.push({ type: this.inThink ? 'thinking' : 'content', text: this.buffer });
            this.buffer = '';
        }
        return events;
    }
}

// =============================================================================
// 1. SOCKET.IO
// =============================================================================
const socket = io();

// --- Streaming event handlers ---
socket.on('chat_token', ({ content }) => {
    if (!streamState) return;
    const events = streamState.parser.feed(content);
    processStreamEvents(events);
});

socket.on('chat_tool_call', ({ name, args }) => {
    if (!streamState) return;
    // Flush any buffered content before tool block
    processStreamEvents(streamState.parser.flush());
    addToolCallBlock(name, args);
});

socket.on('chat_tool_result', ({ name, result }) => {
    if (!streamState) return;
    updateToolResultBlock(name, result);
});

socket.on('chat_done', (data) => {
    if (!streamState) return;
    finalizeStream();
});

socket.on('chat_audio', ({ audio_url }) => {
    // Add TTS button to the last AI message
    if (audio_url) {
        const lastMsg = chatContainer.querySelector('.ai-message:last-child');
        if (lastMsg) addTtsButton(lastMsg, audio_url);
    }
});

socket.on('audit_update', () => {
    fetchAudit();
});

socket.on('workspace_update', () => {
    refreshWorkspace();
});

socket.on('history_update', () => {
    fetchHistory();
});

socket.on('conversations_update', () => {
    fetchConversations();
});

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
const IMAGE_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'];

async function refreshWorkspace() {
    try {
        const r = await apiFetch('/workspace');
        if (!r.ok) return;
        const { tree } = await r.json();
        const treeEl = document.getElementById('workspace-tree');
        if (!tree) return;

        treeEl.innerHTML = '';
        tree.split('\n').forEach(line => {
            const path = line.trim().replace('./', '');
            if (!path || path === '.' || !path.match(/^[\w\-\.\/]+$/)) return;

            const div = document.createElement('div');
            const isDir = path.endsWith('/');
            const ext = path.split('.').pop().toLowerCase();
            const isImage = IMAGE_EXTS.includes(ext);

            div.className = 'hover:bg-gray-800 cursor-pointer px-2 py-1 rounded transition flex items-center gap-2 group text-gray-300';
            div.setAttribute('role', 'treeitem');

            let icon = isDir ? 'fa-folder text-yellow-500' : 'fa-file-code text-blue-400';
            if (isImage) icon = 'fa-image text-emerald-400';

            div.innerHTML = `
                <i class="fas ${icon} text-[10px]" aria-hidden="true"></i>
                <span class="truncate">${escapeHtml(path)}</span>`;

            if (!isDir) {
                div.onclick = () => isImage ? openImagePreview(path) : openFileInEditor(path);
                div.setAttribute('tabindex', '0');
                div.addEventListener('keydown', e => { if (e.key === 'Enter') div.click(); });
            }
            treeEl.appendChild(div);
        });
    } catch (e) {
        console.error('Workspace refresh failed:', e);
    }
}

async function openFileInEditor(path) {
    fileNameLabel.textContent = `${path} (loading…)`;
    document.getElementById('image-preview').classList.add('hidden');
    document.getElementById('editor').style.display = '';
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

function openImagePreview(path) {
    fileNameLabel.textContent = path;
    document.getElementById('editor').style.display = 'none';
    const preview = document.getElementById('image-preview');
    preview.classList.remove('hidden');
    document.getElementById('preview-img').src = `/workspace/file/${encodeURIComponent(path)}`;
    editorPane.classList.add('active');
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

confirmModal.addEventListener('click', e => {
    if (e.target === confirmModal) closeModal();
});

document.addEventListener('keydown', e => {
    if (e.key === 'Escape') {
        if (!confirmModal.classList.contains('hidden')) closeModal();
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
// 6. STREAMING CHAT
// =============================================================================
function sendMessage() {
    const query = userInput.value.trim();
    if (!query || isGenerating) return;

    renderMessage('user', query);
    userInput.value = '';
    userInput.style.height = 'auto';
    popup.classList.add('hidden');
    setLoading(true);

    // Create streaming message container
    streamState = createStreamContainer();

    // Send via Socket.io for streaming
    socket.emit('chat_message', { query });
}

function createStreamContainer() {
    const wrapper = document.createElement('div');
    wrapper.className = 'flex justify-start relative group';

    const bubble = document.createElement('div');
    bubble.className = 'ai-message bg-[#161b22] border border-gray-800 max-w-[85%] rounded-2xl px-5 py-4 shadow-xl mb-4 relative overflow-hidden group';

    const content = document.createElement('div');
    content.className = 'stream-content markdown-body';

    bubble.appendChild(content);
    wrapper.appendChild(bubble);
    chatContainer.appendChild(wrapper);
    chatContainer.scrollTop = chatContainer.scrollHeight;

    return {
        wrapper,
        bubble,
        contentEl: content,
        activeTextArea: null,
        parser: new StreamParser(),
        thinkingEl: null,
        toolBlocks: [],
    };
}

function ensureActiveTextArea() {
    if (!streamState) return null;
    if (!streamState.activeTextArea) {
        const span = document.createElement('span');
        span.className = 'stream-text stream-cursor';
        streamState.contentEl.appendChild(span);
        streamState.activeTextArea = span;
    }
    return streamState.activeTextArea;
}

function deactivateTextArea() {
    if (streamState && streamState.activeTextArea) {
        streamState.activeTextArea.classList.remove('stream-cursor');
        streamState.activeTextArea = null;
    }
}

function processStreamEvents(events) {
    if (!streamState) return;

    for (const event of events) {
        switch (event.type) {
            case 'thinking_start': {
                deactivateTextArea();
                
                const details = document.createElement('details');
                details.className = 'thinking-block';
                details.innerHTML = `
                    <summary>
                        <span class="thinking-indicator"></span>
                        Thinking...
                    </summary>
                    <div class="thinking-content"></div>`;
                streamState.contentEl.appendChild(details);
                streamState.thinkingEl = details.querySelector('.thinking-content');
                break;
            }

            case 'thinking':
                if (streamState.thinkingEl) {
                    streamState.thinkingEl.textContent += event.text;
                }
                break;

            case 'thinking_end': {
                if (streamState.thinkingEl) {
                    // Mark as complete
                    const indicator = streamState.thinkingEl.closest('.thinking-block').querySelector('.thinking-indicator');
                    if (indicator) indicator.classList.add('done');
                }
                streamState.thinkingEl = null;
                break;
            }

            case 'content':
                ensureActiveTextArea().textContent += event.text;
                break;
        }
    }

    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addToolCallBlock(name, args) {
    if (!streamState) return;

    deactivateTextArea();

    const block = document.createElement('details');
    block.className = 'tool-call-block';
    block.innerHTML = `
        <summary>
            <span class="tool-indicator running"></span>
            Calling <code>${escapeHtml(name)}</code>...
        </summary>
        <div class="tool-call-content">
            <div class="tool-section-label">Arguments</div>
            <pre><code>${escapeHtml(JSON.stringify(args, null, 2))}</code></pre>
            <div class="tool-result-area" style="display:none">
                <div class="tool-section-label">Result</div>
                <pre><code class="tool-result-code"></code></pre>
            </div>
        </div>`;

    streamState.contentEl.appendChild(block);
    streamState.toolBlocks.push({ name, element: block, hasResult: false });
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function updateToolResultBlock(name, result) {
    if (!streamState) return;

    const match = [...streamState.toolBlocks].reverse().find(t => t.name === name && !t.hasResult);
    if (match) {
        match.hasResult = true;
        const block = match.element;

        // Update indicator
        const indicator = block.querySelector('.tool-indicator');
        const isError = result.includes('"status": "error"') || result.includes('Error');
        indicator.classList.remove('running');
        indicator.classList.add(isError ? 'error' : 'done');

        // Update summary text
        const summary = block.querySelector('summary');
        summary.innerHTML = `
            <span class="tool-indicator ${isError ? 'error' : 'done'}"></span>
            <code>${escapeHtml(name)}</code> — ${isError ? 'failed' : 'completed'}`;

        // Show result
        const resultArea = block.querySelector('.tool-result-area');
        resultArea.style.display = '';
        block.querySelector('.tool-result-code').textContent = result;

        // Detect and render images in result
        detectAndRenderImages(result, resultArea);
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function detectAndRenderImages(text, container) {
    // Match common image paths in tool output
    const imgRegex = /\/workspace\/([\w\-\.\/]+\.(png|jpg|jpeg|gif|svg|webp))/gi;
    let match;
    while ((match = imgRegex.exec(text)) !== null) {
        const imgPath = match[1];
        const img = document.createElement('img');
        img.src = `/workspace/file/${encodeURIComponent(imgPath)}`;
        img.className = 'chat-image';
        img.alt = imgPath;
        img.onclick = () => openLightbox(img.src);
        container.appendChild(img);
    }
}

function openLightbox(src) {
    const lb = document.createElement('div');
    lb.className = 'image-lightbox';
    lb.innerHTML = `<img src="${src}" alt="Full size preview">`;
    lb.onclick = () => lb.remove();
    document.body.appendChild(lb);
}

function finalizeStream() {
    if (!streamState) return;

    // Flush remaining parser buffer
    processStreamEvents(streamState.parser.flush());
    deactivateTextArea();

    // Process all stream-text spans
    const textBlocks = streamState.contentEl.querySelectorAll('.stream-text');
    textBlocks.forEach(block => {
        const rawText = block.textContent.trim();
        if (rawText) {
            // Replace raw text with rendered markdown
            const rendered = DOMPurify.sanitize(marked.parse(rawText));
            const mdDiv = document.createElement('div');
            mdDiv.className = 'rendered-content';
            mdDiv.innerHTML = rendered;
            block.replaceWith(mdDiv);

            // Syntax highlighting
            mdDiv.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));

            // Add "Run Script" buttons
            mdDiv.querySelectorAll('pre').forEach(pre => {
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

            // Detect images in rendered markdown
            mdDiv.querySelectorAll('img').forEach(img => {
                img.classList.add('chat-image');
                img.onclick = () => openLightbox(img.src);
            });
        } else {
            block.remove();
        }
    });

    streamState = null;
    setLoading(false);

    // Refresh workspace (agent may have created/changed files)
    refreshWorkspace();
    fetchConversations();
}

function setLoading(loading) {
    isGenerating          = loading;
    userInput.disabled    = loading;
    sendBtn.disabled      = loading;

    if (loading) {
        stopBtnContainer.classList.remove('hidden');
        if (!statusInterval) {
            statusInterval = setInterval(fetchStatus, 1000);
        }
    } else {
        stopBtnContainer.classList.add('hidden');
        clearInterval(statusInterval);
        statusInterval = null;
        userInput.focus();
    }
    chatContainer.scrollTop = chatContainer.scrollHeight;
}

async function fetchStatus() {
    try {
        const r = await apiFetch('/status');
        if (!r.ok) return;
    } catch (e) { /* non-fatal */ }
}

async function stopGeneration() {
    if (!isGenerating) return;
    if (currentAudio) currentAudio.pause();
    try {
        await apiFetch('/stop', { method: 'POST' });
    } catch (e) {
        console.error('Stop signal failed:', e);
    }
    if (streamState) finalizeStream();
    setLoading(false);
}

// =============================================================================
// 7. RENDER (for non-streamed messages: user bubbles, legacy)
// =============================================================================
function renderMessage(role, text, audioUrl = null, toolCalls = null, toolName = null) {
    const div = document.createElement('div');
    const isUser = role === 'user';
    div.className = isUser ? 'flex justify-end' : 'flex justify-start relative group';

    let html = '';
    if (role === 'tool') {
        const isError = text.includes('"status": "error"') || text.includes('Error');
        html = `
            <div class="ai-message bg-[#161b22] border border-gray-800 max-w-[85%] rounded-2xl px-5 py-4 shadow-xl mb-4 relative overflow-hidden group">
                <details class="tool-call-block" open>
                    <summary>
                        <span class="tool-indicator ${isError ? 'error' : 'done'}"></span>
                        <code>${escapeHtml(toolName || 'tool')}</code> — result
                    </summary>
                    <div class="tool-call-content">
                        <div class="tool-result-area">
                            <div class="tool-section-label">Result</div>
                            <pre><code class="tool-result-code">${escapeHtml(text)}</code></pre>
                        </div>
                    </div>
                </details>
            </div>`;
    } else {
        let content = text || '';
        if (role === 'assistant' || role === 'ai') {
            content = content.replace(/### \[TOOL_OUTPUT\] ###[\s\S]*?### \[END_OUTPUT\] ###/g, '');
            content = content.replace(
                /<think>([\s\S]*?)<\/think>/g,
                `<details class="thinking-block">
                    <summary><span class="thinking-indicator done"></span>Internal Reasoning</summary>
                    <div class="thinking-content">$1</div>
                 </details>`
            );
            content = DOMPurify.sanitize(marked.parse(content));

            if (toolCalls && toolCalls.length) {
                toolCalls.forEach(tc => {
                    const name = tc.function.name;
                    const args = tc.function.arguments;
                    content += `
                        <details class="tool-call-block" open>
                            <summary>
                                <span class="tool-indicator done"></span>
                                <code>${escapeHtml(name)}</code> — called
                            </summary>
                            <div class="tool-call-content">
                                <div class="tool-section-label">Arguments</div>
                                <pre><code>${escapeHtml(typeof args === 'string' ? args : JSON.stringify(args, null, 2))}</code></pre>
                            </div>
                        </details>`;
                });
            }
        }

        html = `
            <div class="${isUser ? 'bg-blue-600' : 'ai-message bg-[#161b22] border border-gray-800'}
                         max-w-[85%] rounded-2xl px-5 py-4 shadow-xl mb-4 relative overflow-hidden group"
                 role="${isUser ? 'none' : 'article'}">
                <div class="markdown-body">${content}</div>
            </div>`;
    }

    div.innerHTML = html;
    chatContainer.appendChild(div);
    div.querySelectorAll('pre code').forEach(el => hljs.highlightElement(el));

    if (!isUser && audioUrl) {
        addTtsButton(div.querySelector('.ai-message'), audioUrl);
    }

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

    chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addTtsButton(msgEl, audioUrl) {
    if (!msgEl) return;
    const btn = document.createElement('button');
    btn.className = 'tts-button absolute top-2 right-2 p-1.5 rounded-lg bg-gray-800/80 border border-gray-700 text-blue-400 hover:text-white hover:bg-blue-600 transition-all shadow-md opacity-0 group-hover:opacity-100 focus:opacity-100 z-10';
    btn.title = 'Play/Pause Audio';
    btn.innerHTML = '<i class="fas fa-play text-[10px]"></i>';
    btn.onclick = () => toggleAudio(audioUrl, btn);
    msgEl.appendChild(btn);
}

function toggleAudio(url, btn) {
    const icon = btn.querySelector('i');
    if (!currentAudio || currentAudio.src.indexOf(url) === -1) {
        if (currentAudio) {
            currentAudio.pause();
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
// 8. CURRENT MODEL DISPLAY
// =============================================================================
async function fetchModels() {
    try {
        const r = await apiFetch('/models');
        if (!r.ok) return;
        const { current } = await r.json();
        const display = document.getElementById('current-model-display');
        if (display) {
            display.textContent = current || 'No model loaded';
        }
    } catch (e) { console.error('Model fetch failed:', e); }
}

// =============================================================================
// 9. CONVERSATION MANAGEMENT
// =============================================================================
async function fetchConversations() {
    try {
        const r = await apiFetch('/conversations');
        if (!r.ok) return;
        const { conversations, current } = await r.json();
        const list = document.getElementById('conversation-list');

        if (!conversations.length) {
            list.innerHTML = '<div class="text-[10px] text-gray-600 italic text-center pt-4">No conversations yet.</div>';
            return;
        }

        list.innerHTML = '';
        conversations.forEach(conv => {
            const div = document.createElement('div');
            div.className = `conv-item ${conv.id === current ? 'active' : ''}`;
            div.setAttribute('role', 'listitem');

            const timeAgo = formatTimeAgo(conv.updated_at);
            div.innerHTML = `
                <div class="conv-title">${escapeHtml(conv.title)}</div>
                <div class="conv-meta">${conv.message_count} messages · ${timeAgo}</div>
                <button class="conv-delete" onclick="event.stopPropagation(); deleteConversation('${conv.id}', ${conv.id === current})" title="Delete">
                    <i class="fas fa-trash"></i>
                </button>`;

            div.onclick = () => loadConversation(conv.id);
            list.appendChild(div);
        });
    } catch (e) { console.error('Conversation fetch failed:', e); }
}

async function createNewConversation() {
    try {
        await apiFetch('/conversations', { method: 'POST' });
        chatContainer.innerHTML = `
            <div class="flex justify-center my-4 opacity-50 text-sm text-gray-500 italic">
                Agent initialized. Awaiting tasks...
            </div>`;
        fetchConversations();
        fetchHistory();
    } catch (e) { console.error('New conversation failed:', e); }
}

async function loadConversation(convId) {
    try {
        const r = await apiFetch(`/conversations/${convId}`, { method: 'PUT' });
        if (!r.ok) return;
        const { history } = await r.json();

        chatContainer.innerHTML = '';
        history.forEach(msg => {
            renderMessage(msg.role, msg.content, null, msg.tool_calls, msg.name);
        });

        fetchConversations();
        fetchHistory();
    } catch (e) { console.error('Load conversation failed:', e); }
}

async function deleteConversation(convId, isActive) {
    if (!confirm('Delete this conversation?')) return;
    try {
        await apiFetch(`/conversations/${convId}`, { method: 'DELETE' });
        if (isActive) {
            chatContainer.innerHTML = `
                <div class="flex justify-center my-4 opacity-50 text-sm text-gray-500 italic">
                    Agent initialized. Awaiting tasks...
                </div>`;
            fetchHistory();
        }
        fetchConversations();
    } catch (e) { console.error('Delete conversation failed:', e); }
}

function formatTimeAgo(isoDate) {
    if (!isoDate) return '';
    const diff = Date.now() - new Date(isoDate).getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'just now';
    if (mins < 60) return `${mins}m ago`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `${hrs}h ago`;
    const days = Math.floor(hrs / 24);
    return `${days}d ago`;
}

// =============================================================================
// 10. LEFT SIDEBAR TABS
// =============================================================================
function switchLeftTab(tab) {
    const isConvos = tab === 'convos';
    document.getElementById('panel-convos').classList.toggle('hidden', !isConvos);
    document.getElementById('panel-files').classList.toggle('hidden', isConvos);

    const tabConvos = document.getElementById('tab-convos');
    const tabFiles = document.getElementById('tab-files');
    tabConvos.classList.toggle('text-blue-400', isConvos);
    tabConvos.classList.toggle('border-blue-500', isConvos);
    tabConvos.classList.toggle('text-gray-500', !isConvos);
    tabConvos.classList.toggle('border-transparent', !isConvos);

    tabFiles.classList.toggle('text-blue-400', !isConvos);
    tabFiles.classList.toggle('border-blue-500', !isConvos);
    tabFiles.classList.toggle('text-gray-500', isConvos);
    tabFiles.classList.toggle('border-transparent', isConvos);
}

// =============================================================================
// 11. AUDIT LOG
// =============================================================================
async function fetchAudit() {
    try {
        const r = await apiFetch('/audit');
        if (!r.ok) return;
        const { log, mtime } = await r.json();
        if (mtime && mtime === _auditCache.mtime) return;
        _auditCache = { mtime, log };
        const atBottom = systemTerminal.scrollHeight - systemTerminal.scrollTop
                         <= systemTerminal.clientHeight + 10;
        systemTerminal.textContent = log;
        if (atBottom) systemTerminal.scrollTop = systemTerminal.scrollHeight;
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// 12. BOOTSTRAP INFO
// =============================================================================
async function fetchInfo() {
    try {
        const r = await apiFetch('/info');
        if (!r.ok) return;
        const { container } = await r.json();
        document.getElementById('container-name').textContent = container;
        document.getElementById('container-badge').classList.remove('hidden');
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// 13. RIGHT SIDEBAR TABS
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
            div.onclick = () => { userInput.value = msg.content; userInput.focus(); };
            list.appendChild(div);
        });
        list.scrollTop = list.scrollHeight;
    } catch (e) { /* non-fatal */ }
}

// =============================================================================
// 14. EVENT LISTENERS
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
// 15. DARK MODE
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

if (localStorage.getItem('theme') === 'light') {
    document.body.classList.add('light-mode');
    const icon = document.querySelector('#dark-mode-toggle i');
    if (icon) icon.className = 'fas fa-sun text-yellow-400';
}

// =============================================================================
// 16. EXPORT & RUN SCRIPT
// =============================================================================
function exportHistory() {
    window.location.href = '/export';
}

async function runCodeBlock(code, language, preElement) {
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

        const existing = preElement.parentElement.querySelector('.script-output');
        if (existing) existing.remove();

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

// =============================================================================
// 17. BOOTSTRAP
// =============================================================================
window.addEventListener('load', () => {
    userInput.focus();
    setTimeout(() => { fitAddon.fit(); editor.resize(); }, 500);
});

fetchInfo();
fetchModels();
fetchAudit();
fetchHistory();
fetchConversations();
refreshWorkspace();

setInterval(fetchModels,          30_000);