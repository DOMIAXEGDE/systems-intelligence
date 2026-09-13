(() => {
    'use strict';

    const root = document.getElementById('workbench');
    if (!root) return;

    const byId = (id) => document.getElementById(id);
    const ui = {
        editor: byId('sourceEditor'), frame: byId('editorFrame'), title: byId('editor-title'),
        lines: byId('lineNumbers'), saveState: byId('saveState'), cursor: byId('cursorState'),
        panelToggle: byId('panelToggle'), panelCount: byId('panelCount'), panel: byId('controlPanel'),
        panelClose: byId('panelClose'), scrim: byId('panelScrim'), fileList: byId('fileList'),
        fileLimit: byId('fileLimit'), newFileName: byId('newFileName'), createFile: byId('createFile'),
        renameFile: byId('renameFile'), duplicateFile: byId('duplicateFile'), deleteFile: byId('deleteFile'),
        compileFile: byId('compileFile'), saveFile: byId('saveFile'), buildLight: byId('buildLight'),
        diagnostic: byId('diagnostic'), exampleSelect: byId('exampleSelect'), loadExample: byId('loadExample'),
        analytics: byId('analyticsGrid'), map: byId('mapPreview'), eventList: byId('eventList'),
        eventCount: byId('eventCount'), exportWorkspace: byId('exportWorkspace'),
        importWorkspace: byId('importWorkspace'), downloadBuild: byId('downloadBuild'),
        downloadSource: byId('downloadSource'), toast: byId('toast'),
    };
    const state = { config: null, workspace: null, dirty: false, saveTimer: 0, toastTimer: 0, queue: Promise.resolve() };
    const endpoint = root.dataset.endpoint || './index.php?api=1';

    class WorkbenchError extends Error {
        constructor(message, payload = {}) { super(message); this.payload = payload; }
    }

    const activeFile = () => state.workspace?.files?.find(
        (file) => file.id === state.workspace['active-file-id'],
    ) || state.workspace?.files?.[0] || null;

    async function fetchJson(action = null, payload = {}) {
        const options = action === null
            ? { credentials: 'same-origin', headers: { Accept: 'application/json' } }
            : {
                method: 'POST', credentials: 'same-origin',
                headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
                body: JSON.stringify({ action, ...payload }),
            };
        const response = await fetch(endpoint, options);
        let data;
        try { data = await response.json(); }
        catch { throw new WorkbenchError('The workbench returned an unreadable response.'); }
        if (!response.ok || data.ok !== true) {
            throw new WorkbenchError(data.error || 'The workbench request failed.', data);
        }
        return data;
    }

    function request(action, payload = {}) {
        const run = () => fetchJson(action, payload);
        state.queue = state.queue.then(run, run);
        return state.queue;
    }

    async function boot() {
        try {
            const data = await fetchJson();
            state.config = data.config;
            const editor = state.config?.editor || {};
            ui.editor.wrap = editor.wrap === false ? 'off' : 'soft';
            ui.editor.style.tabSize = String(Number.isInteger(editor.tab_size) ? editor.tab_size : 2);
            if (Number.isInteger(state.config?.workspace?.max_filename_symbols)) {
                ui.newFileName.maxLength = state.config.workspace.max_filename_symbols;
            }
            renderExamples();
            applyWorkspace(data.workspace, true);
            ui.editor.disabled = false;
            ui.frame.dataset.state = 'ready';
            ui.editor.focus();
        } catch (error) { showFatal(error); }
    }

    function applyWorkspace(workspace, replaceEditor = false) {
        state.workspace = workspace;
        const file = activeFile();
        if (!file) throw new WorkbenchError('The JSON workspace contains no active text file.');
        if (replaceEditor) {
            ui.editor.value = file.source;
            state.dirty = false;
            updateLines(); updateCursor();
        }
        renderFiles(); renderBuild(file.build);
        ui.title.textContent = file.name;
        ui.fileLimit.textContent = `${workspace.files.length} / ${state.config?.workspace?.max_files ?? '—'}`;
        ui.saveState.textContent = state.dirty ? 'Unsaved source changes' : `JSON saved · revision ${file.revision}`;
    }

    function renderFiles() {
        ui.fileList.replaceChildren();
        const current = activeFile();
        for (const file of state.workspace.files) {
            const button = document.createElement('button');
            button.type = 'button'; button.className = 'file-row'; button.dataset.fileId = file.id;
            button.setAttribute('role', 'listitem');
            button.setAttribute('aria-current', file.id === current.id ? 'true' : 'false');
            const name = document.createElement('strong'); name.textContent = file.name;
            const revision = document.createElement('span');
            revision.textContent = file.build ? `r${file.revision} · built` : `r${file.revision}`;
            button.append(name, revision);
            button.addEventListener('click', () => switchFile(file.id));
            ui.fileList.append(button);
        }
    }

    function renderExamples() {
        ui.exampleSelect.replaceChildren();
        for (const example of state.config?.examples || []) {
            const option = document.createElement('option');
            option.value = example.id; option.textContent = example.name;
            ui.exampleSelect.append(option);
        }
    }

    function renderBuild(build) {
        const database = build?.database || null;
        ui.downloadBuild.disabled = database === null;
        ui.eventList.replaceChildren(); ui.map.replaceChildren();
        if (!database) {
            ui.buildLight.dataset.state = 'idle';
            ui.buildLight.textContent = state.dirty ? 'Changed' : 'Not built';
            ui.eventCount.textContent = '0 events';
            ui.panelCount.textContent = String(state.workspace?.files?.length ?? '—');
            renderAnalytics(null);
            const empty = document.createElement('p'); empty.className = 'empty-state';
            empty.textContent = 'Compile the current file to render its proven records.';
            ui.eventList.append(empty); return;
        }
        ui.buildLight.dataset.state = 'passed'; ui.buildLight.textContent = 'Proven';
        ui.eventCount.textContent = `${database.events.length} event${database.events.length === 1 ? '' : 's'}`;
        ui.panelCount.textContent = String(database.events.length);
        renderAnalytics(database); renderMap(database.map);
        database.events.forEach((event, index) => ui.eventList.append(eventCard(event, index)));
    }

    function renderAnalytics(database) {
        const values = database ? [
            database.analytics?.['event-records'] ?? database.events.length,
            database.analytics?.['proofs-passed'] ?? '—',
            database.analytics?.['multiline-records'] ?? '—',
            `${database.map?.['cells-occupied'] ?? 0}/${database.map?.['cells-total'] ?? 0}`,
        ] : ['—', '—', '—', '—'];
        ui.analytics.querySelectorAll('article strong').forEach((card, index) => {
            card.textContent = String(values[index]);
        });
    }

    function renderMap(map) {
        if (!map?.cells) return;
        for (const rowData of map.cells) {
            const row = document.createElement('div'); row.className = 'map-row';
            for (const value of rowData) {
                const cell = document.createElement('span'); cell.className = 'map-cell';
                cell.dataset.occupied = value === null ? 'false' : 'true';
                if (value !== null) cell.title = value;
                row.append(cell);
            }
            ui.map.append(row);
        }
    }

    function eventCard(event, index) {
        const card = document.createElement('details'); card.className = 'event-card';
        const summary = document.createElement('summary');
        const ordinal = document.createElement('span'); ordinal.className = 'event-ordinal';
        ordinal.textContent = String(index + 1).padStart(2, '0');
        const title = document.createElement('span'); title.className = 'event-title';
        const strong = document.createElement('strong'); strong.textContent = event.label;
        const preview = document.createElement('span'); preview.textContent = String(event.value).replaceAll('\n', ' ↵ ');
        title.append(strong, preview);
        const proof = document.createElement('span'); proof.className = 'proof-mark';
        proof.textContent = event['backward-computation']?.['matches-value'] ? 'proved' : 'failed';
        summary.append(ordinal, title, proof);
        const body = document.createElement('div'); body.className = 'event-body';
        body.append(eventBlock('Source record', event.instruction?.source || ''), eventBlock('Rendered value', String(event.value)));
        const meta = document.createElement('div'); meta.className = 'event-meta';
        const metadata = [
            `lines ${event.instruction?.['source-lines']?.start ?? '?'}–${event.instruction?.['source-lines']?.end ?? '?'}`,
            `op ${event.instruction?.operation ?? '?'}`,
            `symbols ${event.metrics?.['value-symbols'] ?? '?'}`,
            `ID digits ${event.metrics?.['sequential-ID-digits'] ?? '?'}`,
        ];
        for (const value of metadata) {
            const item = document.createElement('span'); item.textContent = value; meta.append(item);
        }
        body.append(meta, eventBlock('Sequential-string ID', String(event['sequential-string ID'])));
        card.append(summary, body); return card;
    }

    function eventBlock(labelText, value) {
        const fragment = document.createDocumentFragment();
        const label = document.createElement('p'); label.className = 'event-label'; label.textContent = labelText;
        const block = document.createElement('pre'); block.textContent = value;
        fragment.append(label, block); return fragment;
    }

    function updateLines() {
        const count = Math.max(1, ui.editor.value.split('\n').length);
        ui.lines.textContent = Array.from({ length: count }, (_, index) => String(index + 1)).join('\n');
    }

    function updateCursor() {
        const lines = ui.editor.value.slice(0, ui.editor.selectionStart).split('\n');
        ui.cursor.textContent = `Ln ${lines.length}, Col ${lines.at(-1).length + 1}`;
    }

    function scheduleSave() {
        clearTimeout(state.saveTimer);
        state.saveTimer = setTimeout(() => saveCurrent(true), state.config?.editor?.autosave_ms ?? 650);
    }

    async function saveCurrent(quiet = false) {
        const file = activeFile();
        if (!file || !state.dirty) { if (!quiet) toast('Source is already saved.'); return state.workspace; }
        clearTimeout(state.saveTimer);
        const source = ui.editor.value;
        ui.frame.dataset.state = 'saving'; ui.saveState.textContent = 'Saving source to JSON…';
        try {
            const data = await request('save-file', { fileId: file.id, source });
            const unchanged = ui.editor.value === source && activeFile()?.id === file.id;
            state.workspace = data.workspace;
            if (unchanged) state.dirty = false;
            applyWorkspace(data.workspace, false); ui.frame.dataset.state = 'ready';
            if (!quiet) toast('Source saved.'); return data.workspace;
        } catch (error) {
            ui.frame.dataset.state = 'error'; ui.saveState.textContent = error.message;
            if (!quiet) toast(error.message); throw error;
        }
    }

    async function switchFile(fileId) {
        if (activeFile()?.id === fileId) return;
        try {
            await saveCurrent(true);
            const data = await request('set-active-file', { fileId });
            applyWorkspace(data.workspace, true); closePanel(); ui.editor.focus();
        } catch (error) { toast(error.message); }
    }

    async function compileCurrent() {
        const file = activeFile(); if (!file) return;
        clearTimeout(state.saveTimer); clearDiagnostic();
        ui.frame.dataset.state = 'compiling'; ui.buildLight.dataset.state = 'working';
        ui.buildLight.textContent = 'Compiling'; ui.saveState.textContent = 'Parsing, evaluating, ranking, and proving…';
        const source = ui.editor.value;
        try {
            const data = await request('compile-file', { fileId: file.id, source });
            state.dirty = false; applyWorkspace(data.workspace, true); ui.frame.dataset.state = 'ready';
            toast(`Compiled ${activeFile()?.build?.database?.events?.length ?? 0} proven events.`);
        } catch (error) {
            ui.frame.dataset.state = 'error'; ui.buildLight.dataset.state = 'failed';
            ui.buildLight.textContent = 'Failed'; ui.saveState.textContent = 'Compile failed · source remains editable';
            showDiagnostic(error);
            if (error.payload?.diagnostic?.line) selectLine(error.payload.diagnostic.line);
        }
    }

    function showDiagnostic(error) {
        const diagnostic = error.payload?.diagnostic;
        ui.diagnostic.hidden = false;
        ui.diagnostic.textContent = diagnostic
            ? `Line ${diagnostic.line}, column ${diagnostic.column}\n${diagnostic.message}` : error.message;
    }
    function clearDiagnostic() { ui.diagnostic.hidden = true; ui.diagnostic.textContent = ''; }

    function selectLine(lineNumber) {
        const lines = ui.editor.value.split('\n');
        const line = Math.max(1, Math.min(Number(lineNumber), lines.length));
        let start = 0;
        for (let index = 1; index < line; index++) start += lines[index - 1].length + 1;
        closePanel(); ui.editor.focus(); ui.editor.setSelectionRange(start, start + lines[line - 1].length);
        const height = parseFloat(getComputedStyle(ui.editor).lineHeight) || 24;
        ui.editor.scrollTop = Math.max(0, (line - 3) * height); ui.lines.scrollTop = ui.editor.scrollTop; updateCursor();
    }

    async function createFile() {
        const name = ui.newFileName.value.trim();
        if (!name) { ui.newFileName.focus(); return; }
        try {
            await saveCurrent(true); const data = await request('create-file', { name });
            ui.newFileName.value = ''; applyWorkspace(data.workspace, true); closePanel(); ui.editor.focus();
        } catch (error) { toast(error.message); }
    }

    async function renameFile() {
        const file = activeFile(); if (!file) return;
        const name = window.prompt('Rename the current text file:', file.name);
        if (name === null || name.trim() === '' || name.trim() === file.name) return;
        try {
            await saveCurrent(true);
            const data = await request('rename-file', { fileId: file.id, name: name.trim() });
            applyWorkspace(data.workspace, true); toast('File renamed.');
        } catch (error) { toast(error.message); }
    }

    async function duplicateFile() {
        const file = activeFile(); if (!file) return;
        try {
            await saveCurrent(true); const data = await request('duplicate-file', { fileId: file.id });
            applyWorkspace(data.workspace, true); toast('File duplicated.');
        } catch (error) { toast(error.message); }
    }

    async function deleteFile() {
        const file = activeFile();
        if (!file || !confirm(`Delete “${file.name}” from this browser workspace?`)) return;
        try {
            const data = await request('delete-file', { fileId: file.id });
            state.dirty = false; applyWorkspace(data.workspace, true); toast('File deleted.');
        } catch (error) { toast(error.message); }
    }

    function loadExample() {
        const example = (state.config?.examples || []).find((item) => item.id === ui.exampleSelect.value);
        if (!example) return;
        if (ui.editor.value.trim() !== '' && !confirm('Replace the current editor source with this configured example?')) return;
        ui.editor.value = example.source; state.dirty = true; clearDiagnostic(); renderBuild(null);
        updateLines(); updateCursor(); scheduleSave(); closePanel(); ui.editor.focus();
    }

    function downloadBlob(contents, filename, type) {
        const url = URL.createObjectURL(new Blob([contents], { type }));
        const link = document.createElement('a'); link.href = url; link.download = filename;
        document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
    const downloadJson = (data, filename) => downloadBlob(
        `${JSON.stringify(data, null, 2)}\n`, filename, 'application/json;charset=utf-8',
    );

    async function importWorkspace(file) {
        if (!file) return;
        try {
            const parsed = JSON.parse(await file.text());
            const workspace = parsed.workspace && typeof parsed.workspace === 'object' ? parsed.workspace : parsed;
            if (!confirm('Replace this browser workspace with the imported JSON files?')) return;
            const data = await request('import-workspace', { workspace });
            state.dirty = false; applyWorkspace(data.workspace, true); toast('Workspace imported.');
        } catch (error) {
            toast(error instanceof SyntaxError ? 'The selected file is not valid JSON.' : error.message);
        } finally { ui.importWorkspace.value = ''; }
    }

    function openPanel() {
        ui.panelToggle.setAttribute('aria-expanded', 'true'); ui.panel.setAttribute('aria-hidden', 'false');
        ui.scrim.hidden = false; requestAnimationFrame(() => { ui.scrim.dataset.open = 'true'; });
        setTimeout(() => ui.panelClose.focus(), 30);
    }
    function closePanel() {
        ui.panelToggle.setAttribute('aria-expanded', 'false'); ui.panel.setAttribute('aria-hidden', 'true');
        ui.scrim.dataset.open = 'false'; setTimeout(() => { ui.scrim.hidden = true; }, 260);
    }
    function toast(message) {
        clearTimeout(state.toastTimer); ui.toast.textContent = message; ui.toast.hidden = false;
        state.toastTimer = setTimeout(() => { ui.toast.hidden = true; }, 2400);
    }
    function showFatal(error) {
        ui.frame.dataset.state = 'error'; ui.title.textContent = 'Workspace unavailable';
        ui.saveState.textContent = error.message;
        ui.editor.value = '# The PHP JSON workspace could not be loaded.\n# Refresh the page to try again.';
        updateLines(); toast(error.message);
    }
    function insertTab(event) {
        const width = state.config?.editor?.tab_size ?? 2;
        ui.editor.setRangeText(' '.repeat(Math.max(1, Math.min(8, width))), ui.editor.selectionStart, ui.editor.selectionEnd, 'end');
        state.dirty = true; updateLines(); updateCursor(); scheduleSave(); event.preventDefault();
    }

    ui.editor.addEventListener('input', () => {
        state.dirty = true; ui.frame.dataset.state = 'ready'; ui.saveState.textContent = 'Unsaved source changes';
        ui.buildLight.dataset.state = 'idle'; ui.buildLight.textContent = 'Changed'; clearDiagnostic();
        updateLines(); updateCursor(); scheduleSave();
    });
    ui.editor.addEventListener('scroll', () => { ui.lines.scrollTop = ui.editor.scrollTop; });
    ui.editor.addEventListener('click', updateCursor); ui.editor.addEventListener('keyup', updateCursor);
    ui.editor.addEventListener('select', updateCursor);
    ui.editor.addEventListener('keydown', (event) => {
        if (event.key === 'Tab') insertTab(event);
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
            event.preventDefault(); saveCurrent(false).catch(() => {});
        }
        if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
            event.preventDefault(); openPanel(); compileCurrent();
        }
    });
    ui.panelToggle.addEventListener('click', openPanel); ui.panelClose.addEventListener('click', closePanel);
    ui.scrim.addEventListener('click', closePanel); ui.createFile.addEventListener('click', createFile);
    ui.newFileName.addEventListener('keydown', (event) => { if (event.key === 'Enter') createFile(); });
    ui.renameFile.addEventListener('click', renameFile); ui.duplicateFile.addEventListener('click', duplicateFile);
    ui.deleteFile.addEventListener('click', deleteFile); ui.compileFile.addEventListener('click', compileCurrent);
    ui.saveFile.addEventListener('click', () => saveCurrent(false).catch(() => {}));
    ui.loadExample.addEventListener('click', loadExample);
    ui.exportWorkspace.addEventListener('click', () => downloadJson(state.workspace, 'gpil-workspace.json'));
    ui.importWorkspace.addEventListener('change', () => importWorkspace(ui.importWorkspace.files?.[0]));
    ui.downloadBuild.addEventListener('click', () => {
        const file = activeFile();
        if (file?.build?.database) downloadJson(file.build.database, file.name.replace(/\.[^.]+$/, '') + '.json');
    });
    ui.downloadSource.addEventListener('click', () => {
        const file = activeFile();
        if (file) downloadBlob(ui.editor.value, file.name, 'text/plain;charset=utf-8');
    });
    document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape' && ui.panel.getAttribute('aria-hidden') === 'false') closePanel();
    });
    window.addEventListener('pagehide', () => {
        const file = activeFile(); if (!state.dirty || !file) return;
        fetch(endpoint, {
            method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action: 'save-file', fileId: file.id, source: ui.editor.value }), keepalive: true,
        }).catch(() => {});
    });

    boot();
})();
