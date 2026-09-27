/* 真实数据适配：所有业务调用均为读取，原文与说明始终通过 textContent 呈现。 */
'use strict';
const $ = id => document.getElementById(id);
const root = $('experience');
const stage = document.querySelector('.stage');
const reduced = matchMedia('(prefers-reduced-motion: reduce)');
const state = {works: [], worksCursor: null, work: null, sections: [], marks: [], marksCursor: null, markCursors: [null], markPage: 0, markScope: null, annotation: null, tags: [], rangeIndex: 0, source: null, sourceRequest: null, sourceCursors: [null], sourcePage: 0, reading: false, notesVisible: true};
let sequence = 0;
let controller;
let retryAction;
let lineFrame = 0;
let anchorFrame = 0;

/** 导航请求相互取消，序号校验防止迟到响应覆盖新作品或引用。 */
async function run(action) {
  controller?.abort();
  controller = new AbortController();
  const token = ++sequence;
  const signal = controller.signal;
  const ctx = {
    check() {if (token !== sequence) throw new DOMException('过期请求', 'AbortError');},
    async api(path, body) {
      const response = await fetch(`/api${path}`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal});
      const result = await response.json();
      this.check();
      if (!response.ok) throw new Error(result.message || `读取失败（${response.status}）`);
      return result;
    }
  };
  retryAction = action;
  document.body.classList.add('loading-data');
  $('network-status').hidden = false;
  $('network-message').textContent = '正在读取服务数据…';
  $('retry').hidden = true;
  try {
    await action(ctx);ctx.check();
    $('network-status').hidden = true;
  } catch (error) {
    if (error.name !== 'AbortError' && token === sequence) {
      $('network-message').textContent = error.message || '读取失败，请重试';
      $('retry').hidden = false;
    }
  } finally {
    if (token === sequence) document.body.classList.remove('loading-data');
  }
}
function option(value, text) {const node = document.createElement('option');node.value=value;node.textContent=text;return node;}
function pageArgs(cursor) {return cursor ? {cursor} : {};}
function workArgs() {return {work_id:state.work.id};}
function titleFor(mark) {return mark.tags?.map(tag=>tag.full_name).join(' · ') || '未分类标注';}
function range() {return state.annotation?.source_ranges[state.rangeIndex] || null;}
function section(id) {return state.sections.find(item=>item.id===id);}
function sectionLabel(id) {const item=section(id);return item ? `${item.part_name} · ${item.title}` : '章节';}
function clearSource(message) {
  state.source=null;
  $('source-scroll').replaceChildren();
  const text=document.createElement('p');text.className='source-message';text.textContent=message;$('source-scroll').append(text);
  $('chapter-title').textContent='原文';$('source-caption').textContent='';
  $('reference-line').style.visibility='hidden';$('chapter-start').disabled=true;
  updateSourcePager();
}
function clearAnnotation(message) {
  state.annotation=null;state.tags=[];
  $('annotation-title').textContent='标注';$('annotation-note').textContent=message;
  $('annotation-tags').replaceChildren();$('reference-select').replaceChildren();$('annotation-position').textContent='';
  $('locate').disabled=true;$('previous-mark').disabled=true;$('next-mark').disabled=true;
}
async function loadWorks(ctx, append=false) {
  const data=await ctx.api('/library/browse',{view:'works',format:'full',limit:50,...pageArgs(append?state.worksCursor:null)});
  state.works=append?[...state.works,...data.works.items]:data.works.items;
  state.worksCursor=data.works.next_cursor;
  $('work-select').replaceChildren(...state.works.map(work=>option(work.id,work.name)));
  if(state.work)$('work-select').value=state.work.id;
  $('more-works').hidden=!state.worksCursor;
  if(!state.work&&state.works.length)await loadWork(ctx,state.works[0].id);
  if(!state.works.length){$('work-title').textContent='作品库';$('work-meta').textContent='暂无可见作品';clearSource('服务中还没有可读取的作品。');clearAnnotation('暂无标注');}
}
async function loadWork(ctx,id) {
  state.work=state.works.find(work=>work.id===id);
  state.sections=[];state.marks=[];state.markScope=null;state.markCursors=[null];state.markPage=0;
  state.sourceRequest=null;state.sourceCursors=[null];state.sourcePage=0;
  clearSource('正在读取作品…');clearAnnotation('正在读取标注…');$('mark-list').replaceChildren();
  $('work-select').value=id;$('work-title').textContent=state.work.name;
  $('work-meta').textContent=`${state.work.part_count} 个分部 · ${state.work.section_count} 章`;
  $('work-quote').textContent='';
  $('catalog-summary').textContent=`${state.work.paragraph_count.toLocaleString()} 段 · ${state.work.character_count.toLocaleString()} 字`;
  let cursor=null,indexes=null;
  do {
    const data=await ctx.api('/library/browse',{view:'sections',...workArgs(),format:'full',limit:100,...pageArgs(cursor)});
    state.sections.push(...data.sections.items);cursor=data.sections.next_cursor;indexes=data.indexes;
  } while(cursor);
  $('section-select').replaceChildren(...state.sections.map(item=>option(item.id,`${item.part_name} · ${item.title}`)));
  $('work-state').textContent=`原文可读 · ${indexes?.source_ready&&indexes?.clues_ready?'索引已就绪':indexes?.state==='failed'?'索引失败':'索引未就绪'}`;
  await loadMarks(ctx,null,0);
  if(state.marks.length)await loadAnnotation(ctx,state.marks[0].id);
  else if(state.sections.length)await loadSection(ctx,state.sections[0].id);
  else clearSource('作品没有可读取的章节。');
}
async function loadMarks(ctx,cursor=null,page=0) {
  const data=await ctx.api('/library/browse',{view:'annotations',...workArgs(),status:'active',limit:12,...(state.markScope?{source_range:state.markScope}:{}),...pageArgs(cursor)});
  state.marks=data.annotations.items;state.marksCursor=data.annotations.next_cursor;state.markPage=page;
  renderMarks();
}
function renderMarks() {
  $('marks-scope-label').textContent=state.markScope?'当前原文页 · 引用相交的有效标注':'整部作品 · 有效标注';
  $('mark-page-label').textContent=`第 ${state.markPage+1} 页 · 本页 ${state.marks.length} 条`;
  $('previous-marks').disabled=state.markPage===0;$('next-marks').disabled=!state.marksCursor;
  $('mark-list').replaceChildren();
  for(const mark of state.marks){
    const button=document.createElement('button');button.className='mark-list-item';button.dataset.markId=mark.id;
    button.setAttribute('aria-pressed',String(mark.id===state.annotation?.id));
    const title=document.createElement('strong');title.textContent=titleFor(mark);
    const note=document.createElement('span');note.textContent=(mark.note_preview||'无说明文字')+(mark.note_truncated?'…':'');
    const meta=document.createElement('small');meta.textContent=`${sectionLabel(mark.first_source_range.section_id)} · ${mark.source_range_count} 处引用`;
    button.append(title,note,meta);button.addEventListener('click',()=>run(async ctx=>{closeDrawer();await loadAnnotation(ctx,mark.id);}));
    $('mark-list').append(button);
  }
  if(!state.marks.length){const p=document.createElement('p');p.textContent='这个范围内没有有效标注。';$('mark-list').append(p);}
  updateMarkPager();
}
function updateMarkPager(){
  const index=state.marks.findIndex(mark=>mark.id===state.annotation?.id);
  $('previous-mark').disabled=index<=0;$('next-mark').disabled=index<0||index>=state.marks.length-1;
  $('open-marks').textContent=`浏览标注 · 第 ${state.markPage+1} 页`;
}
async function loadAnnotation(ctx,id) {
  clearSource('正在读取标注引用…');clearAnnotation('正在读取完整说明…');
  const data=await ctx.api('/library/browse',{view:'annotations',...workArgs(),annotation_id:id});
  state.annotation=data.annotation;state.tags=data.tags||[];state.rangeIndex=0;
  $('annotation-title').textContent=state.tags.map(tag=>tag.name).join(' · ')||'未分类标注';
  $('annotation-note').textContent=state.annotation.note||'此标注没有说明文字。';
  $('annotation-tags').replaceChildren(...state.tags.map(tag=>{const span=document.createElement('span');span.textContent=tag.full_name;return span;}));
  $('annotation-position').textContent=`${state.annotation.source_ranges.length} 处引用`;
  $('reference-select').replaceChildren(...state.annotation.source_ranges.map((item,index)=>option(index,`${index+1}. ${sectionLabel(item.section_id)}`)));
  $('locate').disabled=!range();
  renderMarks();
  if(range())await loadReference(ctx,0);
  else clearSource('此标注没有原文引用。');
}
async function loadReference(ctx,index) {
  if(!state.annotation?.source_ranges[index])return;
  state.rangeIndex=index;$('reference-select').value=String(index);
  state.sourceRequest={...workArgs(),...range(),limit:40};state.sourceCursors=[null];state.sourcePage=0;
  await loadSource(ctx,null,0);
}
async function loadSection(ctx,id) {
  state.sourceRequest={...workArgs(),section_id:id,limit:40};state.sourceCursors=[null];state.sourcePage=0;
  await loadSource(ctx,null,0);
}
async function loadSource(ctx,cursor=null,page=0) {
  const request={...state.sourceRequest,...pageArgs(cursor)};
  clearSource('正在读取原文…');
  const data=await ctx.api('/source/read',request);
  state.source=data;state.sourcePage=page;
  $('section-select').value=data.section_id;
  const item=section(data.section_id);
  $('chapter-title').textContent=item?.title||'原文';
  $('stage-heading').textContent=item?.part_name||state.work.name;
  renderSource();
}
function renderSource(){
  $('source-scroll').replaceChildren();
  const data=state.source;if(!data)return;
  // 范围请求由服务裁定完整引用；整章页只在已知端点均出现时高亮。
  const active=range();
  const start=data.items.findIndex(item=>item.id===active?.start_paragraph_id);
  const end=data.items.findIndex(item=>item.id===active?.end_paragraph_id);
  for(const [index,item] of data.items.entries()){
    const row=document.createElement('div');row.className='source-paragraph';row.dataset.paragraph=item.id;
    if(state.sourceRequest.start_paragraph_id||(start>=0&&end>=start&&index>=start&&index<=end))row.classList.add('highlight');
    const number=document.createElement('span');number.textContent=String(item.ordinal);
    const text=document.createElement('p');text.textContent=item.text;row.append(number,text);$('source-scroll').append(row);
  }
  if(!data.items.length){const p=document.createElement('p');p.textContent='此范围没有正文。';$('source-scroll').append(p);}
  $('source-scroll').scrollTop=0;$('chapter-start').disabled=false;
  const first=data.items[0],last=data.items.at(-1);
  $('source-caption').textContent=first?`${state.sourceRequest.start_paragraph_id?'引用':'章节'} · 本页第 ${first.ordinal}–${last.ordinal} 段`:'';
  $('work-quote').textContent=first?`${first.text.slice(0,38)}${first.text.length>38?'…':''}`:'';
  updateSourcePager();scheduleLine();
}
function updateSourcePager(){
  $('previous-source').disabled=state.sourcePage===0||!state.source;
  $('next-source').disabled=!state.source?.next_cursor;
  $('source-page-label').textContent=state.source?`第 ${state.sourcePage+1} 页${state.source.next_cursor?' · 后面还有正文':' · 已到范围末尾'}`:'';
}
function setReading(value){
  preserveReadingAnchor(()=>{
  state.reading=value;root.classList.toggle('reading',value);document.body.classList.toggle('reading-active',value);
  $('read-toggle').setAttribute('aria-pressed',String(value));$('read-toggle').querySelector('span').textContent=value?'返回探索':'展开阅读';
  $('read-toggle').querySelector('use').setAttribute('href',value?'#back':'#arrow');
  $('source-expand').setAttribute('aria-label',value?'返回探索视图':'展开原文阅读');
  $('mode-label').textContent=value?'阅读视图':'探索视图';
  window.scrollTo({top:0,behavior:'instant'});scheduleLine();
  });
}
/** 布局切换保留可见段落；主动滚动或另一次布局变化终止旧恢复。 */
function preserveReadingAnchor(change){
  cancelAnimationFrame(anchorFrame);
  const scroll=$('source-scroll'),bounds=scroll.getBoundingClientRect();
  const row=[...scroll.children].find(item=>item.dataset.paragraph&&item.getBoundingClientRect().bottom>bounds.top);
  const id=row?.dataset.paragraph,offset=row?row.getBoundingClientRect().top-bounds.top:0;
  change();
  const stop=performance.now()+(reduced.matches?0:800);
  const keep=()=>{const target=id?scroll.querySelector(`[data-paragraph="${id}"]`):null;if(target)scroll.scrollTo({top:scroll.scrollTop+target.getBoundingClientRect().top-scroll.getBoundingClientRect().top-offset,behavior:'instant'});if(performance.now()<stop)anchorFrame=requestAnimationFrame(keep);};keep();
}
function toggleNotes(){
  preserveReadingAnchor(()=>{
    state.notesVisible=!state.notesVisible;root.classList.toggle('annotations-hidden',!state.notesVisible);
    $('annotation-toggle').setAttribute('aria-expanded',String(state.notesVisible));$('annotation-toggle').querySelector('span').textContent=state.notesVisible?'收起标注':'显示标注';
  });
}
function scheduleLine(){
  cancelAnimationFrame(lineFrame);const until=performance.now()+(reduced.matches?0:800);
  const draw=()=>{
    const scroll=$('source-scroll').getBoundingClientRect();
    const row=[...document.querySelectorAll('.source-paragraph.highlight')].find(item=>{const box=item.getBoundingClientRect();return box.bottom>scroll.top&&box.top<scroll.bottom;});
    $('reference-line').style.visibility=row&&!state.reading?'visible':'hidden';
    if(row){const base=stage.getBoundingClientRect(),a=row.getBoundingClientRect(),b=$('analysis').getBoundingClientRect();const x=a.right-base.left-5,y=Math.max(scroll.top+5,Math.min(a.top+a.height/2,scroll.bottom-5))-base.top,endX=b.left-base.left,endY=b.top+40-base.top,mid=(x+endX)/2;$('connection').setAttribute('d',`M${x},${y} C${mid},${y} ${mid},${endY} ${endX},${endY}`);$('connection-dot').setAttribute('cx',x);$('connection-dot').setAttribute('cy',y);}
    if(performance.now()<until)lineFrame=requestAnimationFrame(draw);
  };draw();
}
function openDrawer(){$('catalog-drawer').hidden=false;}
function closeDrawer(){$('catalog-drawer').hidden=true;}
$('retry').addEventListener('click',()=>retryAction&&run(retryAction));
$('read-toggle').addEventListener('click',()=>setReading(!state.reading));
$('source-expand').addEventListener('click',()=>setReading(!state.reading));
$('annotation-toggle').addEventListener('click',toggleNotes);
$('work-select').addEventListener('change',event=>run(ctx=>loadWork(ctx,event.target.value)));
$('more-works').addEventListener('click',()=>run(ctx=>loadWorks(ctx,true)));
$('section-select').addEventListener('change',event=>run(ctx=>loadSection(ctx,event.target.value)));
$('reference-select').addEventListener('change',event=>run(ctx=>loadReference(ctx,Number(event.target.value))));
$('locate').addEventListener('click',()=>run(async ctx=>{setReading(true);await loadReference(ctx,state.rangeIndex);}));
$('chapter-start').addEventListener('click',()=>run(async ctx=>{setReading(true);await loadSection(ctx,state.source?.section_id||state.sections[0].id);}));
$('next-source').addEventListener('click',()=>run(async ctx=>{const cursor=state.source.next_cursor;const page=state.sourcePage+1;state.sourceCursors[page]=cursor;await loadSource(ctx,cursor,page);}));
$('previous-source').addEventListener('click',()=>run(ctx=>loadSource(ctx,state.sourceCursors[state.sourcePage-1],state.sourcePage-1)));
['browse-marks','open-marks'].forEach(id=>$(id).addEventListener('click',openDrawer));
$('close-drawer').addEventListener('click',closeDrawer);
$('next-marks').addEventListener('click',()=>run(async ctx=>{const page=state.markPage+1;const cursor=state.marksCursor;state.markCursors[page]=cursor;await loadMarks(ctx,cursor,page);}));
$('previous-marks').addEventListener('click',()=>run(ctx=>loadMarks(ctx,state.markCursors[state.markPage-1],state.markPage-1)));
$('all-marks').addEventListener('click',()=>run(async ctx=>{state.markScope=null;state.markCursors=[null];await loadMarks(ctx);}));
$('marks-page-scope').addEventListener('click',()=>run(async ctx=>{if(!state.source?.actual_range)throw new Error('先打开原文页再查看对应标注');state.markScope={...workArgs(),section_id:state.source.section_id,...state.source.actual_range};state.markCursors=[null];await loadMarks(ctx);openDrawer();}));
for(const [id,step] of [['previous-mark',-1],['next-mark',1]])$(id).addEventListener('click',()=>run(ctx=>{const index=state.marks.findIndex(item=>item.id===state.annotation?.id);const next=state.marks[index+step];if(index<0||!next)return;return loadAnnotation(ctx,next.id);}));
$('source-scroll').addEventListener('scroll',scheduleLine,{passive:true});
for(const event of ['wheel','touchstart','pointerdown','keydown'])$('source-scroll').addEventListener(event,()=>cancelAnimationFrame(anchorFrame),{passive:true});
window.addEventListener('resize',scheduleLine);
stage.addEventListener('pointermove',event=>{if(state.reading||reduced.matches||event.pointerType!=='mouse')return;const box=stage.getBoundingClientRect();stage.style.setProperty('--px',`${((event.clientX-box.left)/box.width-.5)*12}px`);stage.style.setProperty('--py',`${((event.clientY-box.top)/box.height-.5)*8}px`);scheduleLine();});
stage.addEventListener('pointerleave',()=>{stage.style.setProperty('--px','0px');stage.style.setProperty('--py','0px');scheduleLine();});
run(ctx=>loadWorks(ctx));
