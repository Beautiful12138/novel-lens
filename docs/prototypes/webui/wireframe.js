/* 可点击线框：全部数据为自编演示内容，仅在页面内存中保存，不调用业务服务。 */
'use strict';
const $ = (id) => document.getElementById(id);
const escapeText = (value) => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const sample = {
  works: [
    {id: 'rain', name: '雨停之前', part: '正文', ready: true, chapters: [
      {name: '第一章 · 留下的伞', paragraphs: [
        '雨从午后下到天黑。许苓把最后一摞碗放回架上，才发现门口那把黑伞还在。',
        '伞尖抵着门框，下面积了一小圈水。父亲出门时特意绕开了它，仿佛那是客人暂时放下、很快就会取走的东西。',
        '“明天还开门吗？”她问。',
        '父亲没有回头。他用指甲抠着价目表边上卷起的胶带，抠了两下，又把它按平。',
        '“米泡上了。”他说。',
        '许苓看着墙上那张停水通知。明早六点到下午四点，红章盖在最后一行，日期是今天。她下午就告诉过他。',
        '门外有人敲了两下玻璃。隔壁的陈姨抱着一只蓝色水桶，桶沿搭了一条还没拆标的毛巾。',
        '“我家还有一个。”陈姨说，“你们先接满，早上卖完那一锅再说。”',
        '父亲伸手去接，许苓却先握住了桶柄。塑料很新，细细的边缘硌着她的手心。',
        '“只卖一锅。”她对父亲说。',
        '父亲终于转过身，看了看她，又看那张通知。“那你晚一点来。”',
        '陈姨推门出去，顺手拿起了黑伞。门上的铃响了，伞底那圈水还留在原地。'
      ]},
      {name: '第二章 · 天亮以前', paragraphs: [
        '五点半，街上的路灯还没有熄。许苓从后门进去，看见父亲蹲在炉子旁边，正把纸箱撕成细条。',
        '那只蓝桶放在水池里，毛巾已经取下，叠得整整齐齐。她伸手试了试桶里的水，凉意一直窜到手腕。',
        '“不是叫你晚点来？”父亲说。',
        '“醒了。”她把袖子卷上去，没有再解释。',
        '第一位客人坐下时，米粥刚好翻起一个泡。许苓在本子上写下一横，父亲从她身后拿走了两个碗。',
        '谁也没再提第二锅。'
      ]}
    ]},
    {id: 'night', name: '夜班归途', part: '正文', ready: false, chapters: [
      {name: '第一章 · 末班车', paragraphs: [
        '最后一班车停在路口，司机没有熄火。周启跑到车门前，先把手里的饭盒递了上去。',
        '“今天又晚了。”司机说。',
        '周启点点头，坐到那个窗户关不严的位置。他把饭盒放在膝上，手掌贴着盒盖，还能感觉到一点热。',
        '车过桥时，他才想起忘了给家里打电话。屏幕亮了一下，映出玻璃上疲倦的脸。'
      ]}
    ]}
  ],
  tags: [
    {id:'t1', namespace:'写法', name:'答非所问', description:'回应偏离问题字面内容，通过回避或转移呈现人物态度。', aliases:['回避式回应'], version:1},
    {id:'t2', namespace:'叙述', name:'动作承接', description:'借具体动作连接对话、停顿和人物处境，不以说明替代现场。', aliases:[], version:1},
    {id:'t3', namespace:'结构', name:'物件回收', description:'使前文出现的物件在后文得到具体交代。', aliases:[], version:1},
    {id:'t4', namespace:'叙述', name:'环境留白', description:'通过环境中未解释的细节留下供读者判断的空间。', aliases:[], version:1}
  ],
  marks: [
    {id:'a1', work:'rain', refs:[{chapter:0,start:3,end:5}], tags:['t1','t2'], note:'父亲用“米泡上了”回应是否开门，避开直接决定；抠胶带的动作让这段迟疑留在现场。', status:'active', version:1},
    {id:'a2', work:'rain', refs:[{chapter:0,start:8,end:11}], tags:['t2'], note:'“先接满”把分歧转成可以一起动手的事。女儿以“只卖一锅”提出界限，父亲用上班时间作回应。', status:'active', version:1},
    {id:'a3', work:'rain', refs:[{chapter:0,start:12,end:12}], tags:['t3'], note:'黑伞的归属通过离场动作交代，雨水痕迹保留了开头的空间细节。', status:'active', version:1},
    {id:'a4', work:'night', refs:[{chapter:0,start:1,end:3}], tags:['t2'], note:'先递上饭盒再上车，把赶车的急促落在具体动作上；坐下后摸盒盖，才露出这一趟夜归的温度。', status:'active', version:1},
    {id:'a5', work:'rain', refs:[{chapter:0,start:7,end:8},{chapter:1,start:2,end:2}], tags:['t3'], note:'蓝桶和毛巾从借水现场进入次日厨房；同一物件跨章出现，将邻人的帮助落实为开门前的准备。', status:'active', version:1},
    {id:'a6', work:'night', refs:[{chapter:0,start:4,end:4}], tags:['t1'], note:'这段只有未拨出的电话，尚不足以当作回避式回应；保留为已撤回的分类示例。', status:'withdrawn', version:2}
  ]
};
let data = structuredClone(sample);
let state = {view:'read', work:'rain', chapter:0, mark:null, refIndex:0, draft:null, showWithdrawn:false, directoryHidden:false, highlight:null, search:null, returnSearch:false};
let afterLeave = null, tagEditing = null, deleteTarget = null, managedWork = null, noticeTimer;
let draftBaseline = '';
const readerPositions = new Map();
const work = () => data.works.find(w => w.id === state.work);
const chapter = () => work()?.chapters[state.chapter];
const mark = () => data.marks.find(m => m.id === state.mark);
function notify(message) { clearTimeout(noticeTimer); $('notice').textContent=message; noticeTimer=setTimeout(()=>{$('notice').textContent='';},5000); }
// 只比较用户可编辑内容；版本变更及共享标签定义更新不等于本条草稿有修改。
function draftSignature(d) { return JSON.stringify({refs:d.refs,note:d.note||'',tags:[...d.tags].sort()}); }
function isDirty() { return !!state.draft && draftSignature(state.draft)!==draftBaseline; }
function guard(action) {
  if(isDirty()) { afterLeave=action; $('leave-dialog').showModal(); }
  else { const wasEditing=!!state.draft;state.draft=null;if(wasEditing&&state.view==='read')renderPanel();action(); }
}
// 按可见段落和相对偏移保存阅读锚点，列宽变化后不沿用失效的像素位置。
function readingAnchor() {
  const scroller=$('prose');if(!scroller)return null;
  const top=scroller.getBoundingClientRect().top;
  const el=[...scroller.querySelectorAll('[data-paragraph]')].find(p=>p.getBoundingClientRect().bottom>top+1);
  return el?{id:el.id,offset:el.getBoundingClientRect().top-top}:null;
}
function restoreReading(anchor) {
  const scroller=$('prose'),el=anchor&&$(anchor.id);
  if(scroller&&el)scroller.scrollTop+=el.getBoundingClientRect().top-scroller.getBoundingClientRect().top-anchor.offset;
}
function rememberReading() { const el=$('prose');if(el)readerPositions.set(el.dataset.readingKey,readingAnchor()); }
function syncEditingLayout() {
  const anchor=readingAnchor();
  const editing=!!state.draft;
  document.querySelector('.workspace')?.classList.toggle('editing',editing);
  const toggle=$('toggle-contents');if(toggle){toggle.disabled=editing;toggle.textContent=editing?'编辑时收起目录':state.directoryHidden?'展开目录':'收起目录';}
  restoreReading(anchor);
}
function go(view) { guard(()=>{assetState.returnView=null;state.returnSearch=false;state.view=view;state.draft=null;render();}); }
function rangeText(m) { return `第 ${m.start}${m.start===m.end?'':`–${m.end}`} 段`; }
function quote(m) { const w=data.works.find(x=>x.id===m.work);return w.chapters[m.chapter].paragraphs.slice(m.start-1,m.end).join('\n'); }
// 标注只保存有序引用；阅读与编辑明确选择一处，同时保留其他引用。
function markReference(m,index=state.refIndex||0) { return m?{work:m.work,...(m.refs[index]||m.refs[0])}:null; }
function chapterReferences(m) { return m.refs.filter(r=>r.chapter===state.chapter); }
function referenceTitle(m,index) { const r=markReference(m,index);return `${data.works.find(w=>w.id===m.work).chapters[r.chapter].name} · ${rangeText(r)}`; }
function markReferencesHtml(m,locatable=true) {
  return m.refs.map((r,i)=>`<details class="disclosure" ${i===state.refIndex?'open':''}><summary>引用 ${i+1} · ${escapeText(referenceTitle(m,i))}</summary><div class="quote">${escapeText(quote(markReference(m,i)))}</div>${locatable?`<button type="button" data-reader-reference="${m.id}" data-reference-index="${i}">阅读此处原文</button>`:''}</details>`).join('');
}
function badges(ids) { return ids.map(id=>data.tags.find(t=>t.id===id)).filter(Boolean).map(t=>`<span class="badge">${escapeText(t.namespace)}/${escapeText(t.name)}</span>`).join(''); }
function openWork(id, c=0) { guard(()=>{assetState.returnView=null;state={...state,view:'read',work:id,chapter:c,mark:null,refIndex:0,draft:null,highlight:null,returnSearch:false};render();}); }
// 库入口记录来源；在阅读区切换同一标注的其他引用时继续保留返回入口。
function openMarkReference(id,index=0) {
  const m=data.marks.find(x=>x.id===id);if(!m||!m.refs[index])return;
  guard(()=>{rememberAssetView();if(state.view==='tags'||state.view==='marks')assetState.returnView=state.view;
    const r=markReference(m,index),returnSearch=state.view==='read'&&state.returnSearch;state={...state,view:'read',work:m.work,chapter:r.chapter,mark:id,refIndex:index,draft:null,highlight:null,returnSearch};selectionRange=null;render();focusRange(r);
  });
}
function render() {
  rememberAssetView();rememberReading();
  $('main').classList.toggle('reading-view',state.view==='read'&&!!work());
  $('main').classList.toggle('asset-view',state.view==='tags'||state.view==='marks');
  document.querySelectorAll('[data-nav]').forEach(b=>{if(b.dataset.nav===state.view)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');});
  if(!work() && state.view==='read')state.view='library';
  if(state.view==='library')renderLibrary(); else if(state.view==='search')renderSearch(); else if(state.view==='tags')renderTagLibrary(); else if(state.view==='marks')renderMarkLibrary(); else renderReader();
}
function renderLibrary() {
  $('main').innerHTML=`<section class="page"><div class="page-intro"><h1>作品库</h1><p class="muted">进入作品，阅读原文和标注；按需要补充自己的理解。</p></div>${data.works.length?data.works.map(w=>`<article class="work-row"><div><h2>${escapeText(w.name)}</h2><p>${w.chapters.length} 章 · ${escapeText(w.part)}</p><p>${w.ready?'阅读处理已完成 · 原文可检索':'阅读处理尚未完成 · 原文索引准备中'}</p></div><div class="actions"><button class="primary" data-open-work="${w.id}">阅读与标注</button><button data-status="${w.id}">准备情况</button><button data-manage="${w.id}">作品管理</button></div></article>`).join(''):`<div class="empty"><h2>还没有作品</h2><p>在已连接 NovelLens 的外部 AI 中提供小说 TXT，完成导入后在这里查看。</p><button id="empty-reset">恢复演示样例</button></div>`}<p class="hint" style="margin-top:28px">本页显示虚构样例，准备状态用于说明信息结构。</p></section>`;
  $('empty-reset')?.addEventListener('click',reset);
}
function renderReader() {
  rememberReading();
  $('main').classList.add('reading-view');
  const w=work(), c=chapter(),key=`${w.id}:${state.chapter}`;
  $('main').innerHTML=`<section class="workbar"><div><h1>${escapeText(w.name)}</h1><p class="hint">${escapeText(c.name)} · 原文只读</p></div><div class="tools">${assetReturnLabel()?`<button id="back-assets">${escapeText(assetReturnLabel())}</button>`:''}${state.returnSearch?'<button id="back-results">返回检索结果</button>':''}<button id="toggle-contents">${state.directoryHidden?'展开目录':'收起目录'}</button><button id="new-mark" class="primary">新建标注</button><button data-manage="${w.id}">作品管理</button></div></section>
  <div class="workspace ${state.directoryHidden?'hide-contents':''}"><aside class="contents" aria-label="作品目录" tabindex="0"><h2>目录</h2><div class="part">${escapeText(w.part)}</div>${w.chapters.map((c,i)=>`<button data-chapter="${i}" ${i===state.chapter?'aria-current="true"':''}>${escapeText(c.name)}</button>`).join('')}</aside>
  <article class="reader" aria-label="小说原文"><div class="reader-head"><h2>${escapeText(c.name)}</h2><p class="hint">${c.paragraphs.length} 段 · 拖选正文可新建标注，引用保留完整自然段。</p></div><div class="prose" id="prose" data-reading-key="${key}" tabindex="0" aria-label="可独立滚动的原文">${c.paragraphs.map((p,i)=>{const n=data.marks.filter(m=>m.work===w.id&&m.status==='active'&&chapterReferences(m).some(r=>r.start<=i+1&&r.end>=i+1)).length;return `<div class="paragraph" id="p-${i+1}" data-paragraph="${i+1}"><span class="range-number" aria-label="第 ${i+1} 段">${i+1}</span><p>${escapeText(p)}</p><div>${n?`<button data-paragraph-marks="${i+1}" aria-label="查看第 ${i+1} 段的 ${n} 条标注">${n}注</button>`:''}</div></div>`;}).join('')}</div></article>
  <aside class="panel" id="panel" aria-label="原文标注"></aside></div>`;
  $('new-mark').onclick=()=>guard(()=>startDraft(null));
  $('back-results')?.addEventListener('click',()=>go('search'));
  $('back-assets')?.addEventListener('click',returnToAssets);
  $('toggle-contents').onclick=()=>{const anchor=readingAnchor();state.directoryHidden=!state.directoryHidden;document.querySelector('.workspace').classList.toggle('hide-contents',state.directoryHidden);syncEditingLayout();restoreReading(anchor);};
  $('prose').addEventListener('mouseup',captureSelection);
  renderPanel();highlight();restoreReading(readerPositions.get(key));
}
let selectionRange=null;
function captureSelection() {
  const s=window.getSelection();if(!s||s.isCollapsed)return;
  const a=s.anchorNode?.parentElement?.closest('[data-paragraph]'),b=s.focusNode?.parentElement?.closest('[data-paragraph]');
  if(a&&b&&$('prose').contains(a)&&$('prose').contains(b))selectionRange={work:state.work,chapter:state.chapter,start:Math.min(+a.dataset.paragraph,+b.dataset.paragraph),end:Math.max(+a.dataset.paragraph,+b.dataset.paragraph)};
}
function highlight() {
  const h=state.draft?markReference(state.draft):mark()?markReference(mark()):state.highlight;
  document.querySelectorAll('[data-paragraph]').forEach(p=>p.classList.toggle('in-range',!!h&&h.work===state.work&&h.chapter===state.chapter&&+p.dataset.paragraph>=h.start&&+p.dataset.paragraph<=h.end));
}
// 只调整原文滚动容器，保持作品栏及侧栏位置稳定。
function focusRange(m) { highlight();const el=$(`p-${m.start}`),scroller=$('prose');if(el&&scroller)scroller.scrollTo({top:el.getBoundingClientRect().top-scroller.getBoundingClientRect().top+scroller.scrollTop-scroller.clientHeight*.2,behavior:'instant'}); }
function selectMark(id) { const m=data.marks.find(x=>x.id===id);if(!m)return;const index=m.refs.findIndex(r=>r.chapter===state.chapter);openMarkReference(id,index<0?0:index); }
function renderPanel() {
  syncEditingLayout();
  if(state.draft){renderEditor();return;}
  const m=mark();
  if(m) {
    const peers=data.marks.filter(x=>x.work===state.work&&chapterReferences(x).length&&(state.showWithdrawn||x.status==='active'||x.id===m.id));
    const index=peers.findIndex(x=>x.id===m.id);
    $('panel').innerHTML=`<div class="panel-heading"><div><h2>${m.status==='withdrawn'?'已撤回标注':'标注详情'}</h2><span class="hint">${rangeText(markReference(m))} · 共 ${m.refs.length} 处引用 · ${index+1} / ${peers.length}</span></div><button id="all-marks">本章列表</button></div><div class="panel-body" tabindex="0" aria-label="标注内容"><div class="actions"><button id="locate">定位原文 · ${rangeText(markReference(m))}</button></div><h3 class="section-label">分析说明</h3><p class="detail-note">${escapeText(m.note||'没有填写说明。')}</p><div class="section-label">关联标签</div><div>${badges(m.tags)||'<p class="hint">没有关联标签。</p>'}</div>${markReferencesHtml(m)}<p class="hint">当前版本 ${m.version} · 修改不改变原文或阅读进度</p><div class="actions"><button data-mark="${peers[index-1]?.id||''}" ${index===0?'disabled':''}>上一条</button><button data-mark="${peers[index+1]?.id||''}" ${index===peers.length-1?'disabled':''}>下一条</button></div></div><div class="panel-footer actions"><button class="primary" id="edit-mark">修订标注</button><button id="withdraw-mark">${m.status==='active'?'撤回标注':'恢复标注'}</button></div>`;
    $('all-marks').onclick=()=>{state.mark=null;renderPanel();highlight();};
    $('edit-mark').onclick=()=>startDraft(m);
    $('locate').onclick=()=>focusRange(markReference(m));
    $('withdraw-mark').onclick=()=>{m.status=m.status==='active'?'withdrawn':'active';m.version++;notify(m.status==='withdrawn'?'标注已撤回；原文仍可检索。':'标注已恢复；演示线索等待同步。');renderReader();};
  } else {
    const marks=data.marks.filter(m=>m.work===state.work&&chapterReferences(m).length&&(state.showWithdrawn||m.status==='active'));
    $('panel').innerHTML=`<div class="panel-heading"><div><h2>本章分析标注</h2><span class="hint">${marks.length} 条 · 选择一条，结合原文核验</span></div></div><div class="panel-body" tabindex="0" aria-label="标注列表"><label class="tools"><input id="withdrawn-filter" type="checkbox" ${state.showWithdrawn?'checked':''}>包括已撤回标注</label>${marks.length?marks.map(m=>`<article class="mark-row"><button data-mark="${m.id}"><strong>${chapterReferences(m).map(rangeText).join('、')}${m.refs.length>1?` · 共 ${m.refs.length} 处引用`:''}${m.status==='withdrawn'?' · 已撤回':''}</strong><p>${escapeText(m.note||'未填写说明')}</p><div>${badges(m.tags)}</div></button></article>`).join(''):'<div class="empty"><h3>本章尚无标注</h3><p class="hint">可以直接阅读；也可以选择原文，留下自己的理解。</p></div>'}</div><div class="panel-footer hint">分析帮助定位与理解，核验仍以原文为准。</div>`;
    $('withdrawn-filter').onchange=(e)=>{state.showWithdrawn=e.target.checked;renderPanel();};
  }
}
function startDraft(m) {
  const selected=selectionRange?.work===state.work&&selectionRange.chapter===state.chapter?selectionRange:{start:1,end:1};
  if(!m)state.refIndex=0;
  state.draft=m?structuredClone(m):{id:null,work:state.work,refs:[{chapter:state.chapter,start:selected.start,end:selected.end}],tags:[],note:'',status:'active',version:0};
  state.mark=m?.id||null;draftBaseline=draftSignature(state.draft);selectionRange=null;renderPanel();highlight();$('draft-note').focus({preventScroll:true});
}
function rangeOptions(selected) { return chapter().paragraphs.map((_,i)=>`<option value="${i+1}" ${i+1===selected?'selected':''}>第 ${i+1} 段</option>`).join(''); }
function renderEditor() {
  const d=state.draft,r=markReference(d);
  $('panel').innerHTML=`<div class="panel-heading"><div><h2>${d.id?'修订标注':'新建标注'}</h2><span class="hint" id="draft-status">${d.id?'尚未修改':'填写后保存'}</span></div><button type="button" id="editor-locate">定位原文</button></div><form id="mark-form" class="panel-body" tabindex="0" aria-label="标注编辑"><label for="draft-note">分析说明（可选）</label><textarea id="draft-note" rows="5" placeholder="记录帮助理解或找回这段原文的观察。">${escapeText(d.note)}</textarea><div class="section-label">原文范围 · 完整自然段</div><p class="hint">正在编辑引用 ${state.refIndex+1} / ${d.refs.length} · ${escapeText(chapter().name)}。其他引用完整保留；可保存后切换引用继续修订。</p><div class="ranges"><div><label for="range-start">起始段落</label><select id="range-start">${rangeOptions(r.start)}</select></div><div><label for="range-end">结束段落</label><select id="range-end">${rangeOptions(r.end)}</select></div></div><details class="disclosure"><summary id="range-summary">查看引用 · ${rangeText(r)}</summary><div id="draft-quote" class="quote">${escapeText(quote(r))}</div></details>${d.refs.length>1?`<details class="disclosure"><summary>查看全部 ${d.refs.length} 处引用</summary><div id="draft-all-refs">${markReferencesHtml(d,false)}</div></details>`:''}<div class="section-label">已选标签</div><div id="selected-tags"></div><details class="disclosure" id="tag-picker"><summary>添加或更换标签</summary><label for="tag-search">查找已有标签</label><input id="tag-search" type="search" placeholder="名称、定义或别名"><fieldset class="tag-options"><legend class="sr-only">关联标签</legend><div id="tag-choices"></div></fieldset><button type="button" id="create-tag">创建新标签</button></details><details class="disclosure"><summary>维护共享标签</summary><p class="hint">修改定义会影响所有关联作品；仅改变本条分类，请在上方添加或移除关联。</p><div id="shared-tags"></div></details><div id="save-problem" role="alert"></div></form><div class="panel-footer actions"><button type="submit" form="mark-form" class="primary" id="save-mark">保存标注</button><button type="button" id="cancel-edit">取消</button><span class="hint">原文保持不变</span></div>`;
  renderSelectedTags();renderTagChoices('');renderSharedTags();
  $('draft-note').oninput=e=>{d.note=e.target.value;updateDirtyStatus();};
  $('tag-search').oninput=e=>renderTagChoices(e.target.value);
  ['range-start','range-end'].forEach(id=>$(id).onchange=()=>{const current=d.refs[state.refIndex];current.start=+$('range-start').value;current.end=+$('range-end').value;const ref=markReference(d);$('draft-quote').textContent=current.start<=current.end?quote(ref):'起始段落不能晚于结束段落。';$('range-summary').textContent=`查看引用 · ${rangeText(ref)}`;if($('draft-all-refs'))$('draft-all-refs').innerHTML=markReferencesHtml(d,false);highlight();updateDirtyStatus();});
  $('editor-locate').onclick=()=>focusRange(markReference(d));
  $('create-tag').onclick=()=>openTag(null);
  $('cancel-edit').onclick=()=>guard(()=>{state.draft=null;renderPanel();highlight();$('edit-mark')?.focus({preventScroll:true});});
  $('mark-form').onsubmit=e=>{saveMark(e);const problem=$('save-problem');if(problem?.textContent)problem.scrollIntoView({block:'nearest'});};
}
function updateDirtyStatus() { if($('draft-status'))$('draft-status').textContent=isDirty()?'有未保存的修改':state.draft?.id?'尚未修改':'填写后保存'; }
function renderSelectedTags() {
  const tags=data.tags.filter(t=>state.draft.tags.includes(t.id));
  $('selected-tags').innerHTML=tags.length?tags.map(t=>`<button type="button" class="selected-tag" data-remove-tag="${t.id}" aria-label="移除关联 ${escapeText(t.name)}">${escapeText(t.namespace)}/${escapeText(t.name)} <span aria-hidden="true">移除</span></button>`).join(''):'<p class="hint">尚未关联标签，也可以不添加。</p>';
}
function renderSharedTags() {
  $('shared-tags').innerHTML=data.tags.map(t=>`<button type="button" data-edit-tag="${t.id}">编辑共享标签：${escapeText(t.name)}</button>`).join('');
}
function renderTagChoices(query) {
  const found=data.tags.filter(t=>`${t.namespace}/${t.name} ${t.description} ${t.aliases.join(' ')}`.includes(query));
  $('tag-choices').innerHTML=found.length?found.map(t=>`<label class="tag-choice"><input type="checkbox" data-tag="${t.id}" ${state.draft.tags.includes(t.id)?'checked':''}><span>${escapeText(t.namespace)}/${escapeText(t.name)}<br><span class="hint">${escapeText(t.description)}</span></span></label>`).join(''):'<p class="hint">没有匹配标签，可以创建一个。</p>';
}
function saveMark(event) {
  event.preventDefault();const d=state.draft, current=data.marks.find(m=>m.id===d.id);
  if(!d.refs.length||d.refs.some(r=>!Number.isInteger(r.chapter)||!Number.isInteger(r.start)||!Number.isInteger(r.end)||r.start<1||r.start>r.end||!work().chapters[r.chapter]||r.end>work().chapters[r.chapter].paragraphs.length)){$('save-problem').innerHTML='<p class="error">引用必须位于现有章节内，起始段落不能晚于结束段落。</p>';return;}
  if(new Set(d.refs.map(r=>`${r.chapter}:${r.start}:${r.end}`)).size!==d.refs.length){$('save-problem').innerHTML='<p class="error">引用范围不能完全重复，请调整当前引用。</p>';return;}
  if($('scenario').value==='failure'){$('save-problem').innerHTML='<p class="error">演示：保存失败，草稿已保留。将顶部情境切回“正常”后重试。</p>';return;}
  if($('scenario').value==='conflict'&&current&&current.version===d.version){current.version++;current.note=(current.note||'')+'（演示：另一位调用方补充了说明。）';}
  if(current&&current.version!==d.version){
    $('save-problem').innerHTML=`<div class="conflict"><strong>标注已有新版本，尚未保存</strong><p>最新说明：${escapeText(current.note)}</p><p>你的草稿仍保留在上方。请核对引用和标签后重新提交。</p><button type="button" id="compare-latest">查看最新完整标注</button><button type="button" id="resolve-conflict">已核对，以当前草稿提交</button><div id="latest-detail"></div></div>`;
    $('compare-latest').onclick=()=>{$('latest-detail').innerHTML=`${markReferencesHtml(current,false)}<p>${badges(current.tags)}</p>`;};
    $('resolve-conflict').onclick=()=>{d.version=current.version;$('scenario').value='normal';$('save-problem').innerHTML='<p class="hint">已采用最新版本条件；请再次点击“保存标注”。</p>';};return;
  }
  const saved={...structuredClone(d),note:d.note?.trim()?d.note:null,id:d.id||crypto.randomUUID(),version:current?current.version+1:1};
  if(current)Object.assign(current,saved);else data.marks.push(saved);
  state.mark=saved.id;state.draft=null;renderReader();notify('标注已保存到演示数据；检索线索同步中（示例）。');
}
function openTag(id) {
  tagEditing=id;const t=data.tags.find(t=>t.id===id);
  $('tag-title').textContent=t?'修订共享标签':'创建标签';
  $('tag-impact').textContent=t?'修改名称、定义或别名将影响所有使用此标签的作品。当前标注的分类调整可以只增删关联。':'标签为全库共享资产。创建成功后独立保存；取消标注不会删除这个标签。';
  $('tag-namespace').value=t?.namespace||'写法';$('tag-namespace').disabled=!!t;
  $('tag-name').value=t?.name||'';$('tag-description').value=t?.description||'';$('tag-aliases').value=t?.aliases.join('，')||'';$('tag-error').textContent='';$('tag-dialog').showModal();
}
$('tag-form').onsubmit=e=>{
  e.preventDefault();const namespace=$('tag-namespace').value,name=$('tag-name').value,description=$('tag-description').value;
  if(!namespace.trim()||namespace!==namespace.trim()||!name.trim()||name!==name.trim()||!description.trim()){$('tag-error').textContent='名称和标签分组不能为空或含首尾空格；定义不能为空。';return;}
  const same=data.tags.find(t=>t.namespace===namespace&&t.name===name&&t.id!==tagEditing);
  if(same){$('tag-error').textContent='同名标签已存在，请取消后在标签列表中查询并复用。';return;}
  const aliases=$('tag-aliases').value.split(/[，,]/).map(x=>x.trim()).filter(Boolean);let t=data.tags.find(t=>t.id===tagEditing);
  if(t)Object.assign(t,{name,description,aliases,version:t.version+1});else {t={id:crypto.randomUUID(),namespace,name,description,aliases,version:1};data.tags.push(t);state.draft?.tags.push(t.id);}
  if(!tagEditing&&state.view==='tags')assetState.tags={...assetState.tags,query:'',namespace:'',selected:t.id};
  $('tag-dialog').close();if(state.draft){renderSelectedTags();renderTagChoices($('tag-search').value);renderSharedTags();updateDirtyStatus();}else render();notify('共享标签已保存到演示数据。');
};
function showManagement(id) {
  managedWork=id;$('manage-name').textContent=data.works.find(w=>w.id===id).name;$('manage-dialog').showModal();
}
$('manage-status').onclick=()=>{$('manage-dialog').close();showStatus(managedWork);};
$('manage-delete').onclick=()=>{$('manage-dialog').close();openDelete(managedWork);};
function openDelete(id) { guard(()=>{deleteTarget=id;$('delete-work-name').textContent=data.works.find(w=>w.id===id).name;$('delete-confirm').value='';$('delete-error').textContent='';$('delete-submit').disabled=true;$('delete-dialog').showModal();}); }
$('delete-confirm').oninput=()=>{$('delete-submit').disabled=$('delete-confirm').value!==data.works.find(w=>w.id===deleteTarget)?.name;};
$('delete-form').onsubmit=e=>{
  e.preventDefault();if($('delete-confirm').value!==data.works.find(w=>w.id===deleteTarget)?.name)return;
  if($('scenario').value==='busy'){$('delete-error').textContent='演示：作品正在进行数据操作，本次未删除。切回“正常”后可重试。';return;}
  data.works=data.works.filter(w=>w.id!==deleteTarget);data.marks=data.marks.filter(m=>m.work!==deleteTarget);state.draft=null;state.mark=null;state.highlight=null;state.search=null;state.returnSearch=false;assetState.returnView=null;state.refIndex=0;for(const key of readerPositions.keys())if(key.startsWith(`${deleteTarget}:`))readerPositions.delete(key);state.view='library';if(state.work===deleteTarget){state.work=data.works[0]?.id;state.chapter=0;}
  $('delete-dialog').close();render();notify('演示作品已删除；其他作品与共享标签保留。');
};
function showStatus(id) { const w=data.works.find(w=>w.id===id);$('status-title').textContent=`${w.name} · 准备情况`;$('status-content').innerHTML=`<p class="hint">以下为示例状态，只表达已保存的数据，不代表 AI 正在运行。</p><div class="status-row"><strong>阅读处理</strong>${w.ready?'全部章节已处理':'第一章仍有待处理段落'}</div><div class="status-row"><strong>原文索引</strong>${w.ready?'已就绪，可以检索原文':'准备中，尚不可统一检索'}</div><div class="status-row"><strong>标记线索</strong>${w.ready?'示例初始状态已同步；原型修改不执行索引':'尚待同步'}</div><p class="hint">如需继续分析，在原先连接的外部 AI 中继续作品准备。</p>`;$('status-dialog').showModal(); }
function renderSearch() {
  $('main').innerHTML=`<section class="page"><div class="page-intro"><h1>原文检索</h1><p class="muted">选择参考作品，找到原文，再回到上下文中核验。</p></div><div class="search-layout"><form class="search-form" id="search-form"><h2>参考范围</h2>${data.works.map(w=>`<label class="check"><input type="checkbox" name="scope" value="${w.id}" ${state.search?.scope?.includes(w.id)||(!state.search&&w.ready)?'checked':''}>${escapeText(w.name)}</label>`).join('')}<label for="query">想查找什么原文？</label><textarea id="query" rows="4" required placeholder="例如：人物没有直接说出不满的对话">${escapeText(state.search?.query||'人物没有直接说出不满的对话')}</textarea><button class="primary" type="submit">查找原文</button><p class="hint" style="margin-top:14px">演示固定候选，不运行真实搜索或相关性排序。正式版另支持分部/章节范围与续查。</p><p id="search-error" class="error" role="alert"></p></form><div id="results" aria-live="polite"></div></div></section>`;
  $('search-form').onsubmit=e=>{e.preventDefault();const scope=[...document.querySelectorAll('[name=scope]:checked')].map(x=>x.value);if(!scope.length){$('search-error').textContent='请至少选择一部作品。';return;}if(scope.some(id=>!data.works.find(w=>w.id===id).ready)){$('search-error').textContent='所选作品的原文索引尚未就绪。可调整范围，或稍后重试。';return;}$('search-error').textContent='';state.search={scope,query:$('query').value};renderResults();};
  renderResults();
}
function renderResults() {
  if(!state.search){$('results').innerHTML='<div class="empty"><h2>先选择范围并查询</h2><p class="muted">候选将保留作品和原文位置，点击即可继续阅读。</p></div>';return;}
  const w=data.works.find(w=>state.search.scope.includes(w.id));
  if(!w){$('results').innerHTML='<p>参考作品已删除，请重新选择范围。</p>';return;}
  $('results').innerHTML=`<h2>候选原文</h2><p class="hint">固定演示候选 · 查询：${escapeText(state.search.query)}</p>${[{start:3,end:5},{start:8,end:11}].map((r,i)=>`<article class="result"><h3>${escapeText(w.name)} · 第一章</h3><p class="hint">${rangeText(r)}</p><p>${escapeText(w.chapters[0].paragraphs.slice(r.start-1,Math.min(r.start+1,r.end)).join(''))}${r.end-r.start>1?'…':''}</p><button data-result="${i}" data-result-work="${w.id}" data-start="${r.start}" data-end="${r.end}">阅读原文与上下文</button></article>`).join('')}<p class="hint">演示候选结束，不表示已穷尽全库相关原文。</p>`;
}
function reset() { guard(()=>{data=structuredClone(sample);resetAssetState();readerPositions.clear();$('main').replaceChildren();state={view:'read',work:'rain',chapter:0,mark:null,refIndex:0,draft:null,showWithdrawn:false,directoryHidden:false,highlight:null,search:null,returnSearch:false};selectionRange=null;$('scenario').value='normal';render();notify('演示数据已重置。');}); }
$('reset').onclick=reset;$('brand').onclick=e=>{e.preventDefault();go('library');};
$('leave-confirm').onclick=()=>{state.draft=null;$('leave-dialog').close();const action=afterLeave;afterLeave=null;render();action?.();};
document.addEventListener('change',e=>{if(e.target.matches('[data-tag]')&&state.draft){const id=e.target.dataset.tag;state.draft.tags=e.target.checked?[...new Set([...state.draft.tags,id])]:state.draft.tags.filter(x=>x!==id);renderSelectedTags();updateDirtyStatus();}});
document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const d=b.dataset;
  if(d.close){$(d.close).close();return;}
  if(d.nav){go(d.nav);return;}
  if(d.openWork){openWork(d.openWork);return;}
  if(d.manage){showManagement(d.manage);return;}
  if(d.removeTag&&state.draft){state.draft.tags=state.draft.tags.filter(x=>x!==d.removeTag);renderSelectedTags();renderTagChoices($('tag-search').value);updateDirtyStatus();return;}
  if(d.status){showStatus(d.status);return;}
  if(d.delete){openDelete(d.delete);return;}
  if(d.chapter!==undefined){guard(()=>{state.chapter=+d.chapter;state.mark=null;state.refIndex=0;state.draft=null;state.highlight=null;selectionRange=null;render();});return;}
  if(d.mark){selectMark(d.mark);return;}
  if(d.readerReference){openMarkReference(d.readerReference,+d.referenceIndex);return;}
  if(d.editTag){openTag(d.editTag);return;}
  if(d.paragraphMarks){const matches=data.marks.filter(m=>m.work===state.work&&chapterReferences(m).length&&m.status==='active'&&chapterReferences(m).some(r=>r.start<=+d.paragraphMarks&&r.end>=+d.paragraphMarks));if(matches.length===1){const m=matches[0];openMarkReference(m.id,m.refs.findIndex(r=>r.chapter===state.chapter&&r.start<=+d.paragraphMarks&&r.end>=+d.paragraphMarks));}else guard(()=>{state.mark=null;renderPanel();notify('这段有多条标注，请在本章列表中选择对应范围。');});return;}
  if(d.result!==undefined){assetState.returnView=null;state={...state,view:'read',work:d.resultWork,chapter:0,mark:null,draft:null,highlight:{work:d.resultWork,chapter:0,start:+d.start,end:+d.end},returnSearch:true};render();focusRange(state.highlight);}
});
window.addEventListener('beforeunload',e=>{if(isDirty()){e.preventDefault();e.returnValue='';}});
render();
