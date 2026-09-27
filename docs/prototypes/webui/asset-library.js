/* 标签库与标注库的内存演示。两者通过同一条标注的全部引用进入阅读。 */
'use strict';

function initialAssetState() {
  return {
    tags: {query:'', namespace:'', selected:null, work:'', status:'active'},
    marks: {query:'', work:'', chapter:'', tag:'', status:'active', selected:null},
    returnView:null,
    scroll:new Map(),
    expanded:new Set()
  };
}
let assetState=initialAssetState();
function resetAssetState() { assetState=initialAssetState(); }

// 使用 DOM 记录所属视图和筛选键，避免导航先更新 state 后保存到错误页面。
function rememberAssetView() {
  document.querySelectorAll('[data-asset-scroll]').forEach(el=>assetState.scroll.set(el.dataset.assetScroll,el.scrollTop));
  document.querySelectorAll('[data-asset-reference]').forEach(el=>{
    if(el.open)assetState.expanded.add(el.dataset.assetReference);else assetState.expanded.delete(el.dataset.assetReference);
  });
}
function restoreAssetView() {
  document.querySelectorAll('[data-asset-scroll]').forEach(el=>{el.scrollTop=assetState.scroll.get(el.dataset.assetScroll)||0;});
}
function assetReturnLabel() { return assetState.returnView==='tags'?'返回标签库':assetState.returnView==='marks'?'返回标注库':''; }
function returnToAssets() {
  const destination=assetState.returnView;
  if(!destination)return;
  guard(()=>{state.view=destination;state.draft=null;state.returnSearch=false;assetState.returnView=null;render();});
}
function assetKey(view,detail=false) {
  const f={...assetState[view]};if(!detail)delete f.selected;
  return escapeText(JSON.stringify([view,detail,f]));
}
function normalizeAssetFilters(f) {
  if(f.work&&!data.works.some(w=>w.id===f.work)){f.work='';if('chapter' in f)f.chapter='';}
  if(f.tag&&!data.tags.some(t=>t.id===f.tag))f.tag='';
  const w=data.works.find(w=>w.id===f.work);
  if('chapter' in f&&(!w||(f.chapter!==''&&!w.chapters[+f.chapter])))f.chapter='';
}
function workOptions(value) {
  return `<option value="">全部作品</option>${data.works.map(w=>`<option value="${w.id}" ${w.id===value?'selected':''}>${escapeText(w.name)}</option>`).join('')}`;
}
function statusOptions(value) {
  return [['active','有效标注'],['withdrawn','已撤回'],['all','全部状态']].map(([v,n])=>`<option value="${v}" ${v===value?'selected':''}>${n}</option>`).join('');
}
function statusMatches(m,status) { return status==='all'||m.status===status; }
function refLocation(m,ref) {
  const w=data.works.find(w=>w.id===m.work);
  return `${w?.name||'作品已删除'} · ${w?.chapters[ref.chapter]?.name||'章节不存在'} · ${rangeText(ref)}`;
}
function excerpt(value,limit=96) { const chars=Array.from(value||'');return escapeText(chars.slice(0,limit).join(''))+(chars.length>limit?'…':''); }
function assetEmpty(title,message,resetView) {
  return `<div class="asset-empty"><h2>${title}</h2><p class="muted">${message}</p>${resetView?`<button data-clear-assets="${resetView}">清除筛选</button>`:''}</div>`;
}
// 一个按钮对应一处引用。预览允许省略，位置与完整引用始终可访问。
function assetReferences(m,full=true) {
  return m.refs.map((ref,index)=>`<div class="asset-reference">
    <div class="reference-heading"><span class="hint">引用 ${index+1} · ${escapeText(refLocation(m,ref))}</span><button data-asset-ref="${m.id}" data-ref-index="${index}">阅读原文${m.refs.length>1?` · 引用 ${index+1}`:''}</button></div>
    <p class="source-excerpt">${excerpt(quote({work:m.work,...ref}))}</p>
    ${full?`<details data-asset-reference="${m.id}:${index}" ${assetState.expanded.has(`${m.id}:${index}`)?'open':''}><summary>展开完整引用</summary><div class="quote">${escapeText(quote({work:m.work,...ref}))}</div></details>`:''}
  </div>`).join('');
}
function renderTagLibrary() {
  const f=assetState.tags;normalizeAssetFilters(f);
  if(f.namespace&&!data.tags.some(t=>t.namespace===f.namespace))f.namespace='';
  const q=f.query.trim().toLocaleLowerCase();
  const tags=data.tags.filter(t=>(!f.namespace||t.namespace===f.namespace)&&`${t.namespace}/${t.name} ${t.description} ${t.aliases.join(' ')}`.toLocaleLowerCase().includes(q));
  if(!tags.some(t=>t.id===f.selected))f.selected=tags[0]?.id||null;
  const t=tags.find(t=>t.id===f.selected);
  const groups=[...new Set(data.tags.map(t=>t.namespace))];
  $('main').innerHTML=`<section class="asset-shell"><div class="asset-title"><div><h1>标签库</h1><p class="muted">理解标签的含义，再沿使用位置回到原文。</p></div><button class="primary" data-create-library-tag>创建标签</button></div>
    <form id="tag-library-filter" class="asset-filters"><label for="tag-library-query">查找标签<input id="tag-library-query" type="search" placeholder="名称、定义或别名" value="${escapeText(f.query)}"></label><label for="tag-library-group">标签分组<select id="tag-library-group"><option value="">全部分组</option>${groups.map(g=>`<option ${g===f.namespace?'selected':''}>${escapeText(g)}</option>`).join('')}</select></label><button type="submit">查找</button><button type="button" data-clear-assets="tags">清除筛选</button></form>
    <div class="asset-columns tag-columns"><aside class="asset-list" aria-label="标签列表" data-asset-scroll="${assetKey('tags')}"><p class="hint list-caption">${tags.length} 个标签 · 演示数据</p>${tags.map(tag=>`<button class="asset-row" data-library-tag="${tag.id}" aria-pressed="${tag.id===f.selected}"><span class="asset-row-title">${escapeText(tag.name)}</span><span class="hint">${escapeText(tag.namespace)}</span><span class="asset-row-preview">${excerpt(tag.description,58)}</span></button>`).join('')||assetEmpty(data.tags.length?'没有匹配标签':'还没有标签',data.tags.length?'试试其他名称、别名或分组。':'可以创建共享标签，之后再关联标注。',data.tags.length?'tags':null)}</aside>
    <section class="asset-detail" aria-label="标签详情" data-asset-scroll="${assetKey('tags',true)}">${t?tagDetail(t,f):assetEmpty('选择一个标签','这里会显示定义、别名与关联原文。')}</section></div></section>`;
  $('tag-library-filter').onsubmit=e=>{e.preventDefault();f.query=$('tag-library-query').value;f.namespace=$('tag-library-group').value;render();};
  ['tag-usage-work','tag-usage-status'].forEach(id=>$(id)?.addEventListener('change',()=>{f.work=$('tag-usage-work').value;f.status=$('tag-usage-status').value;render();}));
  restoreAssetView();
}
function tagDetail(t,f) {
  const linked=data.marks.filter(m=>m.tags.includes(t.id));
  const shown=linked.filter(m=>(!f.work||m.work===f.work)&&statusMatches(m,f.status));
  return `<div class="asset-detail-heading"><div><h2>${escapeText(t.name)}</h2><p class="hint">${escapeText(t.namespace)} · 全库共享标签</p></div><button data-edit-tag="${t.id}">修订共享标签</button></div><p class="asset-definition">${escapeText(t.description)}</p><p class="hint">别名：${t.aliases.length?t.aliases.map(escapeText).join('、'):'未设置'}</p>
    <section class="tag-usages"><div class="usage-heading"><h3>使用位置</h3><div class="usage-filters"><label for="tag-usage-work">作品<select id="tag-usage-work">${workOptions(f.work)}</select></label><label for="tag-usage-status">标注状态<select id="tag-usage-status">${statusOptions(f.status)}</select></label></div></div>
    <p class="hint">当前条件下 ${shown.length} 条关联标注 · 演示数据</p>${shown.map(m=>`<article class="tag-usage"><div class="asset-detail-heading"><h3>${escapeText(data.works.find(w=>w.id===m.work).name)}</h3><span class="hint">${m.status==='withdrawn'?'已撤回 · ':''}${m.refs.length} 处引用</span></div><p class="asset-note">${escapeText(m.note||'没有填写说明。')}</p>${assetReferences(m)}</article>`).join('')||assetEmpty(linked.length?'当前条件下没有关联标注':'这个标签还没有关联标注',linked.length?'可切换作品或标注状态继续查看。':'标签定义已独立保存；可以在阅读时把它关联到标注。')}</section>`;
}
function renderMarkLibrary() {
  const f=assetState.marks;normalizeAssetFilters(f);
  const q=f.query.trim().toLocaleLowerCase();
  const marks=data.marks.filter(m=>(!f.work||m.work===f.work)&&(!f.tag||m.tags.includes(f.tag))&&statusMatches(m,f.status)&&(f.chapter===''||m.refs.some(r=>r.chapter===+f.chapter))&&(m.note||'').toLocaleLowerCase().includes(q));
  if(!marks.some(m=>m.id===f.selected))f.selected=marks[0]?.id||null;
  const selected=marks.find(m=>m.id===f.selected), w=data.works.find(w=>w.id===f.work);
  $('main').innerHTML=`<section class="asset-shell"><div class="asset-title"><div><h1>标注库</h1><p class="muted">集中查看已保存的观察，逐处核验它所依据的原文。</p></div></div>
    <form id="mark-library-filter" class="asset-filters"><label for="mark-library-query">查找说明<input id="mark-library-query" type="search" placeholder="说明中的文字" value="${escapeText(f.query)}"></label><label for="mark-library-work">作品<select id="mark-library-work">${workOptions(f.work)}</select></label><label for="mark-library-chapter">章节<select id="mark-library-chapter" ${!w?'disabled':''}><option value="">${w?'全部章节':'先选择作品'}</option>${w?w.chapters.map((c,i)=>`<option value="${i}" ${f.chapter===String(i)?'selected':''}>${escapeText(c.name)}</option>`).join(''):''}</select></label><label for="mark-library-tag">标签<select id="mark-library-tag"><option value="">全部标签</option>${data.tags.map(t=>`<option value="${t.id}" ${t.id===f.tag?'selected':''}>${escapeText(t.namespace)}/${escapeText(t.name)}</option>`).join('')}</select></label><label for="mark-library-status">状态<select id="mark-library-status">${statusOptions(f.status)}</select></label><button type="submit">查找</button><button type="button" data-clear-assets="marks">清除筛选</button></form>
    <div class="asset-columns mark-columns"><aside class="asset-list" aria-label="标注列表" data-asset-scroll="${assetKey('marks')}"><p class="hint list-caption">${marks.length} 条标注 · 演示数据</p>${marks.map(m=>`<button class="asset-row" data-library-mark="${m.id}" aria-pressed="${m.id===f.selected}"><span class="asset-row-title">${escapeText(data.works.find(w=>w.id===m.work).name)}${m.status==='withdrawn'?'<span class="withdrawn-label">已撤回</span>':''}</span><span class="hint">${escapeText(data.works.find(w=>w.id===m.work).chapters[listReference(m).chapter].name)} · ${rangeText(listReference(m))}${m.refs.length>1?` 等 ${m.refs.length} 处`:''}</span><span class="asset-row-preview">${excerpt(m.note||'没有填写说明。',86)}</span><span>${badges(m.tags)}</span></button>`).join('')||assetEmpty(data.marks.length?'当前条件下没有标注':'还没有标注',data.marks.length?'调整作品、标签、状态或说明关键词。':'作品准备后可在这里浏览；也可以在阅读时补充标注。',data.marks.length?'marks':null)}</aside>
    <section class="asset-detail" aria-label="标注完整详情" data-asset-scroll="${assetKey('marks',true)}">${selected?markLibraryDetail(selected):assetEmpty('选择一条标注','这里会完整显示说明、标签和每处引用。')}</section></div></section>`;
  const readFields=()=>{f.query=$('mark-library-query').value;f.work=$('mark-library-work').value;f.chapter=$('mark-library-chapter').value;f.tag=$('mark-library-tag').value;f.status=$('mark-library-status').value;};
  $('mark-library-filter').onsubmit=e=>{e.preventDefault();readFields();render();};
  ['mark-library-work','mark-library-chapter','mark-library-tag','mark-library-status'].forEach(id=>$(id).onchange=()=>{readFields();if(id==='mark-library-work')f.chapter='';render();});
  restoreAssetView();
}
// 章节筛选命中后，摘要与主要阅读入口优先指向该章；完整引用顺序不变。
function listReference(m) { return m.refs.find(r=>assetState.marks.chapter!==''&&r.chapter===+assetState.marks.chapter)||m.refs[0]; }
function markLibraryDetail(m) {
  const w=data.works.find(w=>w.id===m.work);
  return `<div class="asset-detail-heading"><div><h2>标注详情</h2><p class="hint">${escapeText(w.name)} · ${m.status==='withdrawn'?'已撤回':'有效标注'} · 当前版本 ${m.version}</p></div><button class="primary" data-asset-ref="${m.id}" data-ref-index="${m.refs.indexOf(listReference(m))}">进入原文核验与修订</button></div>
    <h3 class="section-label">分析说明</h3><p class="asset-note">${escapeText(m.note||'没有填写说明。')}</p><h3 class="section-label">关联标签</h3><div class="asset-tag-links">${m.tags.map(id=>data.tags.find(t=>t.id===id)).filter(Boolean).map(t=>`<button data-show-library-tag="${t.id}">${escapeText(t.namespace)}/${escapeText(t.name)}</button>`).join('')||'<p class="hint">没有关联标签。</p>'}</div>
    <h3 class="section-label">原文依据 · ${m.refs.length} 处引用</h3><p class="hint">逐处打开原文，阅读工作区会同时选中这条标注。</p>${assetReferences(m,true)}`;
}

document.addEventListener('click',e=>{
  const b=e.target.closest('button');if(!b)return;const d=b.dataset;
  if(d.libraryTag){assetState.tags.selected=d.libraryTag;render();}
  if(d.libraryMark){assetState.marks.selected=d.libraryMark;render();}
  if(d.assetRef){openMarkReference(d.assetRef,+d.refIndex);}
  if(d.createLibraryTag!==undefined)openTag(null);
  if(d.showLibraryTag){guard(()=>{assetState.tags={query:'',namespace:'',selected:d.showLibraryTag,work:'',status:'active'};assetState.returnView=null;state.view='tags';render();});}
  if(d.clearAssets){const defaults=initialAssetState();assetState[d.clearAssets]=defaults[d.clearAssets];render();}
});
