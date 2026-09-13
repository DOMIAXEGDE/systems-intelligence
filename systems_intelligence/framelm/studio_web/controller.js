'use strict';
(() => {
  const el=id=>document.getElementById(id), endpoint='/api/controller/';
  const state={ids:new Set(),filter:{},head:1,at:1,entities:[],events:[],cursor:0,entityOffset:null,eventCursor:null,registry:null,inspection:null,timer:null,grant:null,inspectionRequest:0};
  const json=x=>JSON.stringify(x,null,2);
  const selection=()=>state.ids.size?{ids:[...state.ids]}:state.filter;
  async function read(name,params={}){return api(endpoint+name+'?'+new URLSearchParams(Object.fromEntries(Object.entries(params).filter(([,v])=>v!==null&&v!==undefined))));}
  const post=(name,data)=>api(endpoint+name,data);
  function action(id,fn){el(id).addEventListener('click',async()=>{el(id).disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{el(id).disabled=false;}});}
  function button(text,fn){const b=node('button',text);b.addEventListener('click',async()=>{b.disabled=true;try{await fn();}catch(e){notice(e.message,true);}finally{b.disabled=false;}});return b;}
  function draw(plot){
    const canvas=el('ct-canvas'),ctx=canvas.getContext('2d'),w=canvas.width,h=canvas.height;
    ctx.clearRect(0,0,w,h);ctx.fillStyle='#f8fafc';ctx.fillRect(0,0,w,h);
    const points=plot.series.flatMap(s=>s.points);let xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
    for(const [x,y] of points){xmin=Math.min(xmin,x);xmax=Math.max(xmax,x);ymin=Math.min(ymin,y);ymax=Math.max(ymax,y);}
    if(!points.length){xmin=ymin=0;xmax=ymax=1;}if(xmin===xmax)xmax++;if(ymin===ymax)ymax++;
    const left=60,right=w-25,top=35,bottom=h-45;
    const xy=([x,y])=>[left+(x-xmin)/(xmax-xmin)*(right-left),bottom-(y-ymin)/(ymax-ymin)*(bottom-top)];
    ctx.font='13px system-ui';
    for(let i=0;i<=5;i++){let x=left+(right-left)*i/5,y=top+(bottom-top)*i/5;ctx.strokeStyle='#cbd5e1';ctx.beginPath();ctx.moveTo(x,top);ctx.lineTo(x,bottom);ctx.moveTo(left,y);ctx.lineTo(right,y);ctx.stroke();ctx.fillStyle='#475569';ctx.fillText((xmin+(xmax-xmin)*i/5).toPrecision(3),x-10,h-18);ctx.fillText((ymax-(ymax-ymin)*i/5).toPrecision(3),2,y+4);}
    const colors=['#2563eb','#e11d48','#059669','#9333ea','#d97706'],labels=new Map();el('ct-legend').replaceChildren();
    plot.series.forEach(s=>{const first=!labels.has(s.label);if(first)labels.set(s.label,labels.size);const index=labels.get(s.label);ctx.strokeStyle=colors[index%colors.length];ctx.lineWidth=1.6;ctx.beginPath();let prev=null;for(const p of s.points){const q=xy(p);if(!prev)ctx.moveTo(...q);else{if(s.kind==='step')ctx.lineTo(q[0],prev[1]);ctx.lineTo(...q);}prev=q;}ctx.stroke();if(s.points.length===1){ctx.fillStyle=ctx.strokeStyle;ctx.beginPath();ctx.arc(...xy(s.points[0]),4,0,2*Math.PI);ctx.fill();}if(first){const label=node('span',s.label+(s.display_stride>1?` (display stride ${s.display_stride})`:''));label.style.color=colors[index%colors.length];el('ct-legend').append(label);}});
    el('ct-plot-note').textContent=plot.notice||plot.diagnostics?.join(' ')||'Numerical plot.';
  }
  async function inspectAt(at){
    const request=++state.inspectionRequest;
    state.at=Number(at);el('ct-sequence').value=state.at;el('ct-scrub').value=state.at;
    const params={selection:json(selection()),at:state.at,models:el('ct-models').checked?'1':'0',limit:1000};
    const inspected=await read('inspect',params);
    while(inspected.next_offset!==null){const page=await read('inspect',{...params,offset:inspected.next_offset});inspected.entities.push(...page.entities);inspected.next_offset=page.next_offset;}
    const pp={selection:params.selection,at:params.at,view:el('ct-view').value||'bytes',limit:1000};let plot=await read('plot',pp);
    while(plot.next_offset!==null){const page=await read('plot',{...pp,offset:plot.next_offset});plot.series.push(...page.series);plot.next_offset=page.next_offset;}
    if(request!==state.inspectionRequest)return;
    inspected.selected_event=state.events.find(e=>e.seq===params.at)||null;state.inspection=inspected;el('ct-inspector').textContent=json(inspected);draw(plot);
  }
  async function loadEntities(append=false){
    const page=await read('inspect',{selection:json(state.filter),at:state.head,offset:append?state.entityOffset:0});
    state.entities=append?state.entities.concat(page.entities):page.entities;state.entityOffset=page.next_offset;
    el('ct-entities').replaceChildren();el('ct-more-entities').hidden=state.entityOffset===null;
    for(const e of state.entities){const key=e.kind+':'+e.id,label=node('label','','ct-entity'),check=document.createElement('input');check.type='checkbox';check.checked=state.ids.has(key);check.addEventListener('change',async()=>{check.checked?state.ids.add(key):state.ids.delete(key);await loadEvents();await inspectAt(state.at);});label.append(check,node('span',`${e.kind} · ${e.state.title||e.id}\nrevision ${e.state.revision??'—'}`));el('ct-entities').append(label);}
  }
  async function loadEvents(append=false){
    const page=await read('events',{selection:json(selection()),after:append?state.eventCursor:0,at:state.head,limit:100});
    state.events=append?state.events.concat(page.events):page.events;state.eventCursor=page.next_sequence;
    el('ct-events').replaceChildren();el('ct-more-events').hidden=state.eventCursor===null;
    for(const e of state.events){const b=button(`${e.seq} · ${e.type} · ${e.phase}`,()=>inspectAt(e.seq));b.className='ct-event';el('ct-events').append(b);}
    el('ct-event-status').textContent=`${state.events.length} events loaded · journal position ${state.head}`;
  }
  async function skills(){
    const data=await read('skills');el('ct-pause').textContent=data.paused?'Resume granted autonomy':'Pause autonomy';el('ct-pause').dataset.paused=String(data.paused);
    el('ct-skills').replaceChildren();el('ct-proposals').replaceChildren();
    for(const s of data.skills){const card=node('div','','ct-skill');card.append(node('h3',s.id),node('p',`${s.enabled?'Enabled':'Draft / disabled'} · ${s.grant?'autonomy granted':'supervised'} · ${s.digest}`,'hint'));const source=node('details');source.append(node('summary','Review source, schemas and declarations'),node('pre',json(s),'ct-code'));card.append(source);const input=node('textarea');input.value='{}';input.rows=2;input.setAttribute('aria-label',s.id+' input JSON');card.append(input);
      card.append(button('Approve this version',async()=>{await post('skills/activate',{id:s.id,digest:s.digest});await skills();await contracts();}),button('Run',async()=>{el('ct-action-result').textContent=json(await post('skills/run',{id:s.id,input:JSON.parse(input.value)}));await skills();}),button('Cancel run',()=>post('skills/cancel',{id:s.id})),button('Disable',async()=>{await post('skills/disable',{id:s.id});await skills();}),button('Configure grant',()=>{state.grant=s;el('ct-grant-id').textContent=s.id+' · '+s.digest;el('ct-grant-data').value=json({capabilities:s.capabilities,scope:s.scope,budgets:{timeout:30,max_output:262144,max_actions:10,max_depth:8}});el('ct-grant-panel').open=true;}),button('Revoke autonomy',async()=>{await post('skills/revoke',{id:s.id});await skills();}));el('ct-skills').append(card);}
    for(const p of data.proposals){const card=node('div','','ct-proposal');card.append(node('h3',`${p.status} · ${p.command}`));const details=node('details');details.open=p.status==='pending';details.append(node('summary','Review targets, revisions, requested changes and prior state'),node('pre',json(p),'ct-code'));card.append(details);if(p.status==='pending')card.append(button('Approve this action',async()=>{await post('proposals/approve',{id:p.id,digest:p.digest});await skills();await refresh();}),button('Reject',async()=>{await post('proposals/reject',{id:p.id});await skills();}));el('ct-proposals').append(card);}
  }
  async function refresh(){
    await contracts();
    const snapshot=await read('inspect',{limit:1});state.head=snapshot.at_sequence;state.at=state.head;el('ct-end').value=state.head;el('ct-scrub').max=state.head;el('ct-sequence').max=state.head;
    await loadEntities();await loadEvents();await inspectAt(state.at);await skills();
    const filters=await read('filters');el('ct-saved').replaceChildren(node('option','Choose…'));for(const name of Object.keys(filters.filters)){const opt=node('option',name);opt.value=name;el('ct-saved').append(opt);}
  }
  window.controllerRefresh=refresh;
  async function contracts(){const oldView=el('ct-view').value,oldCommand=el('ct-command').value;state.registry=await read('registry');el('ct-view').replaceChildren();el('ct-command').replaceChildren();for(const view of state.registry.views){const option=node('option',view.id+' · '+view.units);option.value=view.id;el('ct-view').append(option);}for(const command of state.registry.commands){const option=node('option',command.name);option.value=command.name;el('ct-command').append(option);}if(state.registry.views.some(v=>v.id===oldView))el('ct-view').value=oldView;if(state.registry.commands.some(c=>c.name===oldCommand))el('ct-command').value=oldCommand;contract();}
  window.inspectInController=async ids=>{state.ids=new Set(ids);activate('controller');};
  function contract(){const value=state.registry?.commands.find(c=>c.name===el('ct-command').value);el('ct-contract').textContent=json(value||{});}
  el('ct-command').addEventListener('change',contract);
  el('ct-view').addEventListener('change',()=>inspectAt(state.at).catch(e=>notice(e.message,true)));
  el('ct-models').addEventListener('change',()=>inspectAt(state.at).catch(e=>notice(e.message,true)));
  el('ct-scrub').addEventListener('change',()=>inspectAt(el('ct-scrub').value).catch(e=>notice(e.message,true)));
  el('ct-saved').addEventListener('change',async()=>{if(el('ct-saved').value==='Choose…')return;state.ids.clear();state.filter={saved_filter:el('ct-saved').value};await refresh();});
  action('ct-refresh',refresh);action('ct-seek',()=>inspectAt(el('ct-sequence').value));
  action('ct-filter',async()=>{state.ids.clear();state.filter={};if(el('ct-kind').value)state.filter.kinds=[el('ct-kind').value];if(el('ct-query').value)state.filter.query=el('ct-query').value;if(el('ct-correlation').value)state.filter.correlation=el('ct-correlation').value;await refresh();});
  action('ct-clear',async()=>{state.ids.clear();await loadEntities();await loadEvents();await inspectAt(state.at);});
  action('ct-save-filter',async()=>{const name=prompt('Name this selection');if(name){await post('filter',{name,selection:selection()});await refresh();}});
  action('ct-more-entities',()=>loadEntities(true));action('ct-more-events',()=>loadEvents(true));
  action('ct-prev',()=>inspectAt([...state.events].reverse().find(e=>e.seq<state.at)?.seq||1));action('ct-next',()=>inspectAt(state.events.find(e=>e.seq>state.at)?.seq||state.head));
  action('ct-play',async()=>{if(state.timer){clearInterval(state.timer);state.timer=null;el('ct-play').textContent='Play replay';return;}while(state.eventCursor!==null)await loadEvents(true);const start=Number(el('ct-start').value),end=Number(el('ct-end').value),frames=[start,...state.events.filter(e=>e.seq>start&&e.seq<=end&&(el('ct-lifecycle').checked||e.changes.length)).map(e=>e.seq)];let i=0,busy=false;el('ct-play').textContent='Stop replay';state.timer=setInterval(async()=>{if(busy)return;if(i>=frames.length){clearInterval(state.timer);state.timer=null;el('ct-play').textContent='Play replay';return;}busy=true;try{await inspectAt(frames[i++]);}catch(e){notice(e.message,true);clearInterval(state.timer);state.timer=null;}finally{busy=false;}},200);});
  async function exportReplay(format){const data=await post('export',{selection:selection(),start_sequence:Number(el('ct-start').value),end_sequence:Number(el('ct-end').value),format,view:el('ct-view').value,stride:Number(el('ct-stride').value),include_lifecycle:el('ct-lifecycle').checked});el('ct-downloads').replaceChildren();for(const [key,label] of [['gif_name','Download GIF'],['bundle_name','Download replay data']])if(data[key]){const a=node('a',label);a.href=endpoint+'download/'+data[key];a.download=data[key];el('ct-downloads').append(a);}notice(`${data.frames} replay frames exported.`);}
  action('ct-gif',()=>exportReplay('gif'));action('ct-bundle',()=>exportReplay('bundle'));
  action('ct-branch',async()=>{el('ct-action-result').textContent=json(await post('branch',{at_sequence:state.at,destination:el('ct-destination').value}));});
  action('ct-json',()=>{const a=node('a');a.href=endpoint+'selection?'+new URLSearchParams({selection:json(selection()),at:state.at,models:el('ct-models').checked?'1':'0'});a.download='controller-selection.json';a.click();});
  const examples={'context.append':{title:'Example context',body:'A context supervised by the controller.',enabled:true,tags:'example'},'context.edit':{id:'CONTEXT_ID',revision:1,title:'Revised context',body:'Updated text',enabled:true,tags:''},'context.toggle':{id:'CONTEXT_ID',revision:1,enabled:false},'pandr.commit':{revision:0,commands:[{op:'set_function',function:'union(sin(x), abs(x), implicit(x**2+y**2-4))'}]},'pandr.plot':{functions:'sin(x)',x_range:[-7,7],y_range:[-2,2],resolution:400},'pandr.codec':{action:'encode',value:'Hello'},'pandr.resolve':{sequence:'v17',options:{}},'generator.preview':{recipe:{mode:'numeric',width:3,start:0,limit:5}}};
  action('ct-command-template',()=>{el('ct-payload').value=json(examples[el('ct-command').value]||{});});
  action('ct-dispatch',async()=>{const result=await post('dispatch',{command:el('ct-command').value,payload:JSON.parse(el('ct-payload').value),request_id:crypto.randomUUID()});el('ct-action-result').textContent=json(result);if(result.segments){const series=[];result.segments.forEach(([i,segment])=>{series.push({label:result.labels[i],points:segment});});draw({series,notice:result.diagnostics.join(' ')});}await skills();});
  action('ct-draft',async()=>{await post('skills/draft',{manifest:JSON.parse(el('ct-manifest').value),author:'human'});await skills();});
  action('ct-grant',async()=>{if(!state.grant)throw Error('Select a skill to configure.');await post('skills/grant',{id:state.grant.id,digest:state.grant.digest,...JSON.parse(el('ct-grant-data').value)});await skills();});
  action('ct-pause',async()=>{await post('skills/pause',{paused:el('ct-pause').dataset.paused!=='true'});await skills();});
  el('ct-manifest').value=json({id:'example',version:'1.0.0',api_version:1,dependencies:[],skills:[{id:'append_context',instructions:'Propose adding an example context.',capabilities:['context.append'],scope:['context:*'],triggers:[],input_schema:{type:'object'},source:"result = controller.dispatch('context.append', {'title':'Skill example','body':'Created through a reviewed controller action.','enabled':True,'tags':'skill'})"}]});
  // Contextual links use the application's active selections; no source text becomes code.
  for(const [target,label,ids] of [['chat-title','Inspect chat in Controller',()=>typeof activeChat!=='undefined'&&activeChat?['chat:'+activeChat.id]:[]],['generator','Inspect generator in Controller',()=>[]],['contexts','Inspect contexts in Controller',()=>[]]]){const parent=el(target);if(parent){const b=button(label,async()=>{if(target==='generator')state.filter={kinds:['generator']};else if(target==='contexts')state.filter={kinds:['context']};else state.filter={};state.ids=new Set(ids());activate('controller');});(target==='chat-title'?parent.parentElement:parent).append(b);}}
})();
