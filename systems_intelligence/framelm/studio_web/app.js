'use strict';
const $ = (id) => document.getElementById(id);
const token = document.querySelector('meta[name="studio-token"]').content;
let page = 0, selected = null, lastPreview = null, busy = false, listRequest = 0;

async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json', 'X-Studio-Token': token}, body: JSON.stringify(data)
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || 'Request failed.');
  return result;
}
function notice(text, error = false) {
  $('notice').textContent = text; $('notice').className = error ? 'error' : ''; $('notice').hidden = false;
}
function node(tag, text, className) {
  const element = document.createElement(tag); element.textContent = text;
  if (className) element.className = className;
  return element;
}
function activate(tab) {
  if(tab === 'controller' && window.controllerRefresh) window.controllerRefresh().catch(e => notice(e.message,true));
  document.querySelectorAll('.panel').forEach(p => p.hidden = p.id !== tab);
  document.querySelectorAll('.nav').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
  $('crumb').textContent = {chat:'Chat', contexts: 'Contexts', generator: 'Generator', test: 'Retrieval check', controller:'Controller'}[tab];
}
document.querySelectorAll('.nav').forEach(button => button.addEventListener('click', () => activate(button.dataset.tab)));

async function loadContexts() {
  const request = ++listRequest;
  const params = new URLSearchParams({q: $('search').value, status: $('filter').value, page});
  const data = await api('/api/contexts?' + params);
  if (request !== listRequest) return;
  if (!data.items.length && page > 0) { page--; return loadContexts(); }
  for (const key of ['total','enabled','disabled']) $('stat-' + key).textContent = data.stats[key].toLocaleString();
  $('context-rows').replaceChildren();
  data.items.forEach(item => {
    const row = document.createElement('tr'), nameCell = document.createElement('td');
    const title = node('button', item.title, 'context-title'); title.addEventListener('click', () => openEditor(item.id));
    nameCell.append(title, node('div', item.preview.replace(/\s+/g, ' '), 'context-preview'));
    const tags = document.createElement('td'); tags.append(node('span', item.tags || '—', 'tag'));
    const status = document.createElement('td'); status.append(node('span', item.enabled ? '● Enabled' : '○ Disabled', 'status ' + (item.enabled ? 'on' : 'off')));
    const actions = document.createElement('td'), group = node('div', '', 'row-actions');
    const edit = node('button', 'Edit'); edit.addEventListener('click', () => openEditor(item.id));
    const toggle = node('button', item.enabled ? 'Disable' : 'Enable');
    toggle.addEventListener('click', async () => {
      toggle.disabled = true;
      try { await api('/api/toggle', {id: item.id, revision: item.revision, enabled: !item.enabled}); await loadContexts(); notice(item.enabled ? 'Context disabled. It is no longer used in retrieval.' : 'Context enabled and available for retrieval.'); }
      catch (e) { notice(e.message, true); toggle.disabled = false; }
    });
    const inspect = node('button','Inspect','quiet'); inspect.addEventListener('click',()=>window.inspectInController(['context:'+item.id]));
    group.append(edit, toggle, inspect); actions.append(group); row.append(nameCell,tags,status,actions); $('context-rows').append(row);
  });
  $('empty').hidden = !!data.items.length;
  $('page-info').textContent = data.total ? `${page * data.page_size + 1}–${page * data.page_size + data.items.length} of ${data.total.toLocaleString()} contexts` : '0 contexts';
  $('previous').disabled = page === 0; $('next').disabled = (page + 1) * data.page_size >= data.total;
}
let searchTimer;
$('search').addEventListener('input', () => { clearTimeout(searchTimer); searchTimer = setTimeout(() => {page = 0; loadContexts().catch(e => notice(e.message,true));}, 250); });
$('filter').addEventListener('change', () => {page = 0; loadContexts().catch(e => notice(e.message,true));});
$('previous').addEventListener('click', () => {page--; loadContexts().catch(e => notice(e.message,true));});
$('next').addEventListener('click', () => {page++; loadContexts().catch(e => notice(e.message,true));});

async function openEditor(id = null) {
  try {
    selected = id ? await api('/api/context?id=' + encodeURIComponent(id)) : null;
    $('editor-heading').textContent = selected ? 'Edit context' : 'Append context';
    $('edit-title').value = selected?.title || ''; $('edit-body').value = selected?.body || '';
    $('edit-tags').value = selected?.tags || ''; $('edit-enabled').checked = selected?.enabled ?? true;
    $('provenance-details').hidden = !selected;
    $('provenance').textContent = selected ? JSON.stringify({id:selected.id, revision:selected.revision, ...selected.provenance},null,2) : '';
    $('editor-error').textContent = ''; $('editor').showModal(); $('edit-title').focus();
  } catch (e) {notice(e.message, true);}
}
$('new-context').addEventListener('click', () => openEditor());
for (const id of ['close-editor','cancel-editor']) $(id).addEventListener('click', () => $('editor').close());
$('editor-form').addEventListener('submit', async (event) => {
  event.preventDefault(); $('save-context').disabled = true;
  const item = {title: $('edit-title').value, body: $('edit-body').value, tags: $('edit-tags').value, enabled: $('edit-enabled').checked};
  try {
    await api(selected ? '/api/edit' : '/api/append', selected ? {...item,id:selected.id,revision:selected.revision} : item);
    $('editor').close(); await loadContexts(); notice('Context saved. Retrieval is up to date.');
  } catch(e) { $('editor-error').textContent = e.message; }
  finally { $('save-context').disabled = false; }
});
$('import-files').addEventListener('click', async () => {
  try { const r = await api('/api/adopt',{}); await loadContexts(); notice(`Imported ${r.added} new files from the configured contexts folder. Existing managed records were preserved.`); }
  catch(e) { notice(e.message,true); }
});

function recipeData() {
  const form = $('generator-form'), data = Object.fromEntries(new FormData(form));
  for(const key of ['width','start','limit','handle_width']) {
    // Preserve uint64 ordinals as decimal strings over JSON, avoiding JS's
    // 53-bit number limit. The server converts only these validated fields.
    if (!/^\d+$/.test(data[key])) throw new Error(`${key} must contain decimal digits.`);
  }
  for(const key of ['enabled','all','allow_large']) data[key] = form.elements[key].checked;
  data.separator = data.separator.replace(/\\n/g,'\n').replace(/\\t/g,'\t').replace(/\\r/g,'\r');
  return data;
}
function invalidatePreview() {lastPreview = null; $('generate').disabled = true;}
$('generator-form').addEventListener('input', invalidatePreview);
$('generator-form').addEventListener('change', invalidatePreview);
$('mode').addEventListener('change', () => {
  const f = $('generator-form').elements, mode = f.mode.value;
  const hints = {literal:'Literal produces one record. Width is unused for literal and reverse flows.',reverse:'Reverse produces one record, reversing Unicode characters while retaining valid text.',repeat:'Repeat emits width separate records with the same payload, each with its own ordinal and handle.',cartesian:'Custom Cartesian treats each input character as an alphabet symbol and enumerates width-length tuples.',symbols:'Symbol combinations use the selected preset alphabet. Width is the number of symbols in each record.',numeric:'Numeric handles enumerate zero-padded decimal strings. Width controls the number of digits.'};
  $('flow-hint').textContent = hints[mode];
  f.width.value = mode === 'numeric' ? '7' : ['repeat','cartesian'].includes(mode) ? '3' : '4';
  f.body_template.value = mode === 'numeric' ? 'Object handle {value} maps to ordinal {ordinal}.' : ['symbols','cartesian'].includes(mode) ? 'Symbol candidate {value} has ordinal {ordinal}.' : '{value}';
});
$('generator-form').addEventListener('submit', async event => {
  event.preventDefault(); $('preview-button').disabled = true;
  try {
    const recipe = recipeData(), result = await api('/api/preview', recipe);
    lastPreview = JSON.stringify(recipe); $('preview-records').replaceChildren();
    $('generation-plan').textContent = `Possible records: ${result.plan.total}\nSelected: ${result.plan.count.toLocaleString()} · Start: ${result.plan.start}\nNext start: ${result.plan.next_start}${result.plan.truncated ? ' · More available' : ''}`;
    result.records.forEach(item => { const card = node('article','','preview-record'); card.append(node('strong',item.title),node('pre',item.body),node('small',`Ordinal ${item.provenance.ordinal} · FNV ${item.provenance.fnv1a64}`)); $('preview-records').append(card); });
    $('generate').disabled = busy || result.plan.count === 0;
  } catch(e) { invalidatePreview(); notice(e.message,true); }
  finally { $('preview-button').disabled = false; }
});
async function pollJob() {
  try {
    const job = await api('/api/job');
    if (job.state === 'running') {
      busy = true; $('generate').disabled = true; $('cancel-job').hidden = false;
      $('job-status').textContent = `Preparing ${job.processed.toLocaleString()} / ${job.count.toLocaleString()} records. The batch becomes available when complete.`;
      setTimeout(pollJob, 400); return;
    }
    busy = false; $('cancel-job').hidden = true;
    if (job.state === 'complete') {
      $('job-status').textContent = `Appended ${job.added.toLocaleString()} contexts; skipped ${job.skipped.toLocaleString()} existing ordinals.`;
      await loadContexts(); notice($('job-status').textContent);
    } else if (job.state !== 'idle') { $('job-status').textContent = job.message; notice(job.message,job.state === 'failed'); }
    $('generate').disabled = !lastPreview;
  } catch(e) { notice(e.message,true); busy = false; }
}
$('generate').addEventListener('click', async () => {
  try {
    const recipe = recipeData(); if (JSON.stringify(recipe) !== lastPreview) throw new Error('Preview the changed recipe before appending.');
    busy = true; $('generate').disabled = true;
    await api('/api/generate',recipe); await pollJob();
  } catch(e) {busy = false; notice(e.message,true); $('generate').disabled = !lastPreview;}
});
$('cancel-job').addEventListener('click', () => api('/api/cancel',{}).catch(e => notice(e.message,true)));
$('save-recipe').addEventListener('click', () => {
  try {
    const blob = new Blob([JSON.stringify(recipeData(),null,2)], {type:'application/json'}), url = URL.createObjectURL(blob);
    const a = document.createElement('a'); a.href = url; a.download = 'framelm-recipe.json'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch(e) {notice(e.message,true);}
});
$('load-recipe').addEventListener('change', async event => {
  try {
    const file = event.target.files[0]; if (!file) return;
    if(file.size > 100000) throw new Error('Recipe file exceeds 100 KB.');
    const data = JSON.parse(await file.text()); await api('/api/preview',data);
    const f = $('generator-form').elements;
    for(const [key,value] of Object.entries(data)) if(f[key]) {
      if(f[key].type === 'checkbox') f[key].checked = value;
      else f[key].value = key === 'separator' ? value.replace(/\n/g,'\\n').replace(/\t/g,'\\t').replace(/\r/g,'\\r') : value;
    }
    invalidatePreview(); notice('Recipe loaded. Preview it before appending.');
  } catch(e) {notice(e.message,true);}
  event.target.value = '';
});
$('ask-form').addEventListener('submit', async event => {
  event.preventDefault(); $('answer').textContent = 'Searching enabled contexts…';
  try { const r = await api('/api/ask',{prompt:$('prompt').value}); $('answer').textContent = r.response + '\n\n' + r.contexts.map(c => `[${c.citation}] ${c.source}`).join('\n') + `\n\nStatus: ${r.status}`; }
  catch(e) {$('answer').textContent = e.message;}
});
$('reindex').addEventListener('click', async () => {
  try { const r = await api('/api/reindex',{}); notice(`Rebuilt ${r.chunks} chunks. Disabled contexts remain excluded.`); }
  catch(e) {notice(e.message,true);}
});
loadContexts().catch(e => notice(e.message,true));
pollJob();

let activeChat = null, sendingChat = false, pendingChatRequest = null;
async function refreshChats() {
  const data = await api('/api/chats');
  $('chat-backend').textContent = data.backend === 'extractive'
    ? 'Local extractive mode · Responses quote relevant context excerpts.'
    : `Model backend: ${data.backend}${data.model ? ' · ' + data.model : ''}. Prompts and retrieved contexts go to the configured model backend.`;
  $('chat-list').replaceChildren();
  for (const session of data.sessions) {
    const button = node('button',session.title,'chat-session' + (activeChat?.id === session.id ? ' selected' : ''));
    button.addEventListener('click',async () => {
      if(sendingChat) return notice('Wait for the current reply before switching conversations.');
      try {activeChat = await api('/api/chat?id=' + encodeURIComponent(session.id)); pendingChatRequest = null; renderChat(); await refreshChats();}
      catch(e) {notice(e.message,true);}
    });
    $('chat-list').append(button);
  }
  if(!data.sessions.length) $('chat-list').append(node('p','Your conversations will appear here.','hint'));
}
function renderChat() {
  $('chat-title').textContent = activeChat?.title || 'New conversation';
  const log = $('chat-messages'); log.replaceChildren();
  if(!activeChat?.turns.length) log.append(node('p','Ask a question about an enabled context to begin.','chat-welcome'));
  if(activeChat?.revision > 100) log.append(node('p','Showing the most recent 100 turns.','hint'));
  for(const turn of activeChat?.turns || []) {
    const user = node('article','','chat-message user'); user.append(node('strong','You'),node('div',turn.prompt,'chat-text')); log.append(user);
    const r = turn.result, reply = node('article','','chat-message assistant');
    reply.append(node('strong','FrameLM'),node('div',r.response,'chat-text'));
    reply.append(node('p',`${r.selected_backend || 'No resolution'} · ${r.status.replaceAll('_',' ')}`,'hint'));
    if(r.warnings?.length) reply.append(node('p',r.warnings.join('\n'),'chat-warning'));
    if(r.contexts?.length) {
      const details = document.createElement('details'); details.append(node('summary',`References for this reply (${r.contexts.length})`));
      details.append(node('p','Saved evidence from when this reply was created. New messages use the current enabled contexts.','hint'));
      for(const context of r.contexts) {
        const source = node('article','','chat-source'); source.append(node('strong',`[${context.citation}] ${context.source}`),node('p',context.text));
        const match = /^context:([a-f0-9-]+) \/ /.exec(context.source);
        if(match) {const edit = node('button','Open context','quiet'); edit.addEventListener('click',() => openEditor(match[1])); source.append(edit);}
        details.append(source);
      }
      reply.append(details);
    }
    if(r.conversation?.used_previous_topic) {
      const detail = document.createElement('details'); detail.append(node('summary','Topic used for this follow-up'),node('pre',r.conversation.retrieval_prompt,'chat-text')); reply.append(detail);
    }
    log.append(reply);
  }
  log.scrollTop = log.scrollHeight;
  $('export-chat').disabled = !activeChat?.turns.length;
}
$('new-chat').addEventListener('click', () => {
  if(sendingChat) return notice('Wait for the current reply before starting a new conversation.');
  activeChat = null; pendingChatRequest = null; $('chat-input').value = ''; renderChat(); refreshChats().catch(e=>notice(e.message,true));
});
$('chat-form').addEventListener('submit',async event => {
  event.preventDefault(); if(sendingChat) return;
  const prompt = $('chat-input').value.trim(); if(!prompt) return;
  sendingChat = true; $('send-chat').disabled = true; $('chat-input').disabled = true;
  $('chat-status').textContent = 'Searching enabled contexts and preparing a reply…';
  const pending = node('article','','chat-message user pending'); pending.append(node('strong','You'),node('div',prompt,'chat-text')); $('chat-messages').append(pending); pending.scrollIntoView({block:'nearest'});
  try {
    if(!activeChat) activeChat = await api('/api/chat/new',{});
    if(!pendingChatRequest || pendingChatRequest.prompt !== prompt || pendingChatRequest.id !== activeChat.id)
      pendingChatRequest = {id:activeChat.id, revision:activeChat.revision, prompt, request_id:crypto.randomUUID()};
    activeChat = await api('/api/chat/send',pendingChatRequest);
    pendingChatRequest = null; $('chat-input').value = ''; renderChat(); await refreshChats();
    $('chat-status').textContent = 'Saved locally. Each new turn searches the current enabled contexts.';
  } catch(e) {pending.remove(); notice(e.message,true); $('chat-status').textContent = 'Message not completed. Your text is retained; retry or reopen the conversation.';}
  finally {sendingChat = false; $('send-chat').disabled = false; $('chat-input').disabled = false; $('chat-input').focus();}
});
$('chat-input').addEventListener('keydown',event => {
  if(event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {event.preventDefault(); $('chat-form').requestSubmit();}
});
$('export-chat').addEventListener('click',() => {
  if(!activeChat) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(activeChat,null,2)],{type:'application/json'}));
  const a = document.createElement('a'); a.href = url; a.download = 'framelm-conversation.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
});
refreshChats().catch(e=>notice(e.message,true)); renderChat(); activate('chat');
