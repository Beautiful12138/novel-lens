/* 单页视觉样稿：筛选与原文预览仅使用既有虚构样例，不保存数据。 */
'use strict';
const search = document.querySelector('#search');
const books = [...document.querySelectorAll('[data-book]')];
const preview = document.querySelector('#work-preview');
let activeFilter = 'all';
let previewTrigger = null;
const samples = {
  rain: {title:'雨停之前', chapter:'第一章 · 留下的伞', status:'阅读处理已完成，原文可检索。', paragraphs:[
    '雨从午后下到天黑。许苓把最后一摞碗放回架上，才发现门口那把黑伞还在。',
    '伞尖抵着门框，下面积了一小圈水。父亲出门时特意绕开了它，仿佛那是客人暂时放下、很快就会取走的东西。',
    '“明天还开门吗？”她问。',
    '父亲没有回头。他用指甲抠着价目表边上卷起的胶带，抠了两下，又把它按平。',
    '“米泡上了。”他说。']},
  night: {title:'夜班归途', chapter:'第一章 · 末班车', status:'阅读处理尚未完成，原文索引准备中。现有原文仍可阅读。', paragraphs:[
    '最后一班车停在路口，司机没有熄火。周启跑到车门前，先把手里的饭盒递了上去。',
    '“今天又晚了。”司机说。',
    '周启点点头，坐到那个窗户关不严的位置。他把饭盒放在膝上，手掌贴着盒盖，还能感觉到一点热。',
    '车过桥时，他才想起忘了给家里打电话。屏幕亮了一下，映出玻璃上疲倦的脸。']}
};
/** 查询仅匹配作品名；状态筛选与查询共同生效，隐藏过期预览。 */
function filterBooks(){
  let count=0;
  const query=search.value.trim().toLocaleLowerCase();
  for(const book of books){
    const visible=(activeFilter==='all'||book.dataset.status===activeFilter)&&book.dataset.title.toLocaleLowerCase().includes(query);
    book.hidden=!visible;
    if(visible)count++;
  }
  document.querySelector('#empty').hidden=count!==0;
  document.querySelector('#visible-count').textContent=String(count);
  document.querySelector('#result-count').textContent=`显示 ${count} 部作品`;
  preview.hidden=true;
}
document.querySelectorAll('[data-filter]').forEach(button=>button.addEventListener('click',()=>{
  activeFilter=button.dataset.filter;
  document.querySelectorAll('[data-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  filterBooks();
}));
search.addEventListener('input',filterBooks);
document.querySelector('#clear-search').addEventListener('click',()=>{
  search.value='';activeFilter='all';
  document.querySelectorAll('[data-filter]').forEach(item=>item.setAttribute('aria-pressed',String(item.dataset.filter==='all')));
  filterBooks();search.focus();
});
/** 原文和准备说明都在当前页展开，避免暗示生产阅读页已接通。 */
function showPreview(id,statusOnly,trigger){
  const sample=samples[id];
  previewTrigger=trigger;
  document.querySelector('#preview-kind').textContent=statusOnly?'准备情况 · 演示状态':'原文预览';
  document.querySelector('#preview-title').textContent=sample.title;
  document.querySelector('#preview-meta').textContent=statusOnly?'阅读处理与索引就绪分别记录':sample.chapter;
  const content=document.querySelector('#preview-content');
  content.replaceChildren();
  (statusOnly?[sample.status]:sample.paragraphs).forEach(text=>{const p=document.createElement('p');p.textContent=text;content.append(p);});
  preview.hidden=false;
  preview.focus({preventScroll:true});
  preview.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'start'});
}
document.querySelectorAll('[data-open]').forEach(button=>button.addEventListener('click',()=>showPreview(button.dataset.open,false,button)));
document.querySelectorAll('[data-status-info]').forEach(button=>button.addEventListener('click',()=>showPreview(button.dataset.statusInfo,true,button)));
document.querySelector('#close-preview').addEventListener('click',()=>{preview.hidden=true;previewTrigger?.focus({preventScroll:true});previewTrigger?.scrollIntoView({block:'nearest'});});
