/* 视觉样稿仅使用既有虚构数据。关联线严格表示标注引用，不生成额外关系。 */
'use strict';
const byId = id => document.getElementById(id);
const novels = {
  rain: {
    title: ['雨停', '之前'], name: '雨停之前', chapters: 2, chapter: '留下的伞', quote: '“米泡上了。”',
    state: '原文可读 · 索引已就绪',
    paragraphs: [
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
    ],
    annotations: [
      {id: 'a1', title: '答非所问', start: 3, end: 5, tags: ['写法 / 答非所问', '叙述 / 动作承接'], note: '父亲用“米泡上了”回应是否开门，避开直接决定；抠胶带的动作让这段迟疑留在现场。'},
      {id: 'a3', title: '物件回收', start: 12, end: 12, tags: ['结构 / 物件回收'], note: '黑伞的归属通过离场动作交代，雨水痕迹保留了开头的空间细节。'}
    ]
  },
  night: {
    title: ['夜班', '归途'], name: '夜班归途', chapters: 1, chapter: '末班车', quote: '“今天又晚了。”',
    state: '原文可读 · 索引准备中',
    paragraphs: [
      '最后一班车停在路口，司机没有熄火。周启跑到车门前，先把手里的饭盒递了上去。',
      '“今天又晚了。”司机说。',
      '周启点点头，坐到那个窗户关不严的位置。他把饭盒放在膝上，手掌贴着盒盖，还能感觉到一点热。',
      '车过桥时，他才想起忘了给家里打电话。屏幕亮了一下，映出玻璃上疲倦的脸。'
    ],
    annotations: [{id: 'a4', title: '动作承接', start: 1, end: 3, tags: ['叙述 / 动作承接'], note: '先递上饭盒再上车，把赶车的急促落在具体动作上；坐下后摸盒盖，才露出这一趟夜归的温度。'}]
  }
};
let currentWork = 'rain';
let annotationIndex = 0;
let reading = false;
let annotationsVisible = true;
let exploreWindowY = 0;
let positionFrame = 0;
const readingPositions = new Map();
let switchTimer;
let lineFrame = 0;
let animateUntil = 0;
let pointerFrame = 0;
let pointerX = 0;
let pointerY = 0;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
const stage = document.querySelector('.stage');
const experience = byId('experience');
const sourceScroll = byId('source-scroll');
const activeNovel = () => novels[currentWork];
const activeAnnotation = () => activeNovel().annotations[annotationIndex];

/** 只在视图过渡期间重算曲线，静止页面不持续跑动画循环。 */
function scheduleLine(duration = 850) {
  animateUntil = Math.max(animateUntil, performance.now() + (reducedMotion.matches ? 0 : duration));
  if (!lineFrame) lineFrame = requestAnimationFrame(updateLine);
}
function updateLine() {
  lineFrame = 0;
  if (reading) return;
  const scope = stage.getBoundingClientRect();
  const note = byId('analysis').getBoundingClientRect();
  const visibleSource = sourceScroll.getBoundingClientRect();
  const selected = [...document.querySelectorAll('.source-paragraph.highlight')].find(row => {
    const bounds = row.getBoundingClientRect();
    return bounds.bottom > visibleSource.top && bounds.top < visibleSource.bottom;
  });
  byId('reference-line').style.visibility = selected ? 'visible' : 'hidden';
  if (selected) {
    const text = selected.getBoundingClientRect();
    const x1 = text.right - scope.left - 5;
    const y1 = Math.max(visibleSource.top + 8, Math.min(text.top + text.height / 2, visibleSource.bottom - 8)) - scope.top;
    const x2 = note.left - scope.left;
    const y2 = note.top + 41 - scope.top;
    const middle = (x1 + x2) / 2;
    byId('connection').setAttribute('d', `M${x1},${y1} C${middle},${y1} ${middle},${y2} ${x2},${y2}`);
    byId('connection-dot').setAttribute('cx', String(x1));
    byId('connection-dot').setAttribute('cy', String(y1));
  }
  if (performance.now() < animateUntil) lineFrame = requestAnimationFrame(updateLine);
}

/** 概览展示完整引用；阅读模式显示当前章全部段落与上下文。 */
function renderSource(position = null) {
  const novel = activeNovel();
  const annotation = activeAnnotation();
  const first = reading ? 1 : annotation.start;
  const last = reading ? novel.paragraphs.length : annotation.end;
  sourceScroll.replaceChildren();
  for (let index = first; index <= last; index++) {
    const row = document.createElement('div');
    row.className = `source-paragraph${index >= annotation.start && index <= annotation.end ? ' highlight' : ''}`;
    row.dataset.paragraph = String(index);
    const number = document.createElement('span');
    number.textContent = String(index).padStart(2, '0');
    const paragraph = document.createElement('p');
    paragraph.textContent = novel.paragraphs[index - 1];
    row.append(number, paragraph);
    sourceScroll.append(row);
  }
  const range = annotation.start === annotation.end ? `${annotation.start}` : `${annotation.start}–${annotation.end}`;
  byId('source-caption').textContent = `引用范围 · 第 ${range} 段`;
  sourceScroll.scrollTop = 0;
  if (reading) restoreReadingPosition(position || {paragraph: String(annotation.start), offset: 16});
  scheduleLine();
}

/** 使用段落及相对位置记录阅读锚点，侧栏改变行宽时仍保持原处。 */
function captureReadingPosition() {
  const viewport = sourceScroll.getBoundingClientRect();
  const row = [...sourceScroll.querySelectorAll('.source-paragraph')].find(item => item.getBoundingClientRect().bottom > viewport.top + 2);
  return row ? {paragraph: row.dataset.paragraph, offset: row.getBoundingClientRect().top - viewport.top} : null;
}
/** 仅在布局过渡的有限时间内校正锚点；新的定位动作取消旧校正。 */
function restoreReadingPosition(position) {
  cancelAnimationFrame(positionFrame);
  if (!position) return;
  const stopAt = performance.now() + (reducedMotion.matches ? 0 : 800);
  const restore = () => {
    positionFrame = 0;
    if (!reading) return;
    const row = sourceScroll.querySelector(`[data-paragraph="${position.paragraph}"]`);
    if (row) {
      const delta = row.getBoundingClientRect().top - sourceScroll.getBoundingClientRect().top - position.offset;
      sourceScroll.scrollTo({top: sourceScroll.scrollTop + delta, behavior: 'instant'});
    }
    if (performance.now() < stopAt) positionFrame = requestAnimationFrame(restore);
  };
  restore();
}
function locateInSource() {
  cancelAnimationFrame(positionFrame);
  const target = document.querySelector('.source-paragraph.highlight');
  if (target) sourceScroll.scrollTo({top: Math.max(0, target.offsetTop - sourceScroll.offsetTop - 16), behavior: reducedMotion.matches ? 'instant' : 'smooth'});
  scheduleLine();
}

/** 选择始终按当前作品查找；悬停、聚焦和点击共享同一个引用状态。 */
function selectAnnotation(index) {
  if (index < 0 || index >= activeNovel().annotations.length) return;
  if (index !== annotationIndex && !reading) readingPositions.delete(currentWork);
  annotationIndex = index;
  const annotation = activeAnnotation();
  byId('annotation-title').textContent = annotation.title;
  byId('annotation-note').textContent = annotation.note;
  byId('annotation-position').textContent = `${String(index + 1).padStart(2, '0')} / ${String(activeNovel().annotations.length).padStart(2, '0')}`;
  byId('annotation-tags').replaceChildren(...annotation.tags.map(tag => {const span = document.createElement('span');span.textContent = tag;return span;}));
  document.querySelectorAll('[data-annotation]').forEach(button => button.setAttribute('aria-pressed', String(Number(button.dataset.annotation) === index)));
  renderSource();
}
function renderWork() {
  const novel = activeNovel();
  experience.classList.toggle('night', currentWork === 'night');
  const title = byId('work-title');
  title.replaceChildren(...novel.title.map(text => {const span = document.createElement('span');span.textContent = text;return span;}));
  const stop = document.createElement('span');stop.className = 'title-stop';stop.textContent = '。';title.lastElementChild.append(stop);
  byId('work-meta').textContent = `正文 · ${novel.chapters} 章`;
  byId('work-quote').textContent = novel.quote;
  byId('work-state').textContent = novel.state;
  byId('work-position').firstChild.textContent = currentWork === 'rain' ? '01 ' : '02 ';
  byId('chapter-title').textContent = novel.chapter;
  byId('chapter-count').textContent = `第一章 / 共 ${novel.chapters} 章`;
  document.querySelectorAll('[data-work]').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.work === currentWork)));
  const picker = byId('annotation-picker');
  picker.replaceChildren();
  novel.annotations.forEach((annotation, index) => {
    const button = document.createElement('button');
    button.dataset.annotation = String(index);
    button.setAttribute('aria-pressed', String(index === annotationIndex));
    const number = document.createElement('span');number.textContent = String(index + 1).padStart(2, '0');
    button.append(number, document.createTextNode(annotation.title));
    ['pointerenter', 'focus', 'click'].forEach(event => button.addEventListener(event, () => {
      if (event !== 'click' && reading) return;
      if (annotationIndex !== index) selectAnnotation(index);
    }));
    picker.append(button);
  });
  selectAnnotation(annotationIndex);
}
function setReading(value, locate = false) {
  if (value === reading) return;
  if (reading) readingPositions.set(currentWork, captureReadingPosition());
  else exploreWindowY = window.scrollY;
  cancelAnimationFrame(positionFrame);
  reading = value;
  experience.classList.toggle('reading', value);
  document.body.classList.toggle('reading-active', value);
  experience.classList.toggle('annotations-hidden', !annotationsVisible);
  byId('read-toggle').setAttribute('aria-pressed', String(value));
  byId('read-toggle').querySelector('span').textContent = value ? '返回探索' : '展开阅读';
  byId('read-toggle').querySelector('use').setAttribute('href', value ? '#back' : '#arrow');
  byId('source-expand').setAttribute('aria-label', value ? '返回探索视图' : '展开原文阅读');
  byId('mode-label').textContent = value ? '阅读视图' : '探索视图';
  byId('stage-instruction').lastChild.textContent = value ? '当前展示第一章；选择标注可定位对应原文。' : '将鼠标移到标注上，探索对应原文。';
  stage.style.setProperty('--px', '0px');stage.style.setProperty('--py', '0px');
  renderSource(value && !locate ? readingPositions.get(currentWork) : null);
  window.scrollTo({top: value ? 0 : exploreWindowY, behavior: 'instant'});
  byId('announcement').textContent = `${activeNovel().name}，${value ? '已展开阅读' : '已返回探索'}`;
}
byId('read-toggle').addEventListener('click', () => setReading(!reading));
byId('source-expand').addEventListener('click', () => setReading(!reading));
byId('locate').addEventListener('click', () => {if (!reading) setReading(true, true);else locateInSource();});
byId('annotation-toggle').addEventListener('click', () => {
  const position = captureReadingPosition();
  annotationsVisible = !annotationsVisible;
  experience.classList.toggle('annotations-hidden', !annotationsVisible);
  byId('annotation-toggle').setAttribute('aria-expanded', String(annotationsVisible));
  byId('annotation-toggle').querySelector('span').textContent = annotationsVisible ? '收起标注' : '显示标注';
  restoreReadingPosition(position);
});
// 主动滚动优先于过渡期间的锚点保持，避免用户与动画争夺位置。
sourceScroll.addEventListener('wheel', () => cancelAnimationFrame(positionFrame), {passive: true});
sourceScroll.addEventListener('pointerdown', () => cancelAnimationFrame(positionFrame));
document.querySelectorAll('[data-work]').forEach(button => button.addEventListener('click', () => {
  clearTimeout(switchTimer);
  const nextWork = button.dataset.work;
  if (nextWork === currentWork) {experience.classList.remove('switching');return;}
  const finishSwitch = () => {
    currentWork = nextWork;annotationIndex = 0;renderWork();
    experience.classList.remove('switching');
    byId('announcement').textContent = `已切换到${activeNovel().name}`;
    scheduleLine();
  };
  if (reducedMotion.matches) finishSwitch();
  else {experience.classList.add('switching');switchTimer = setTimeout(finishSwitch, 190);}
}));
stage.addEventListener('pointermove', event => {
  if (reading || reducedMotion.matches || event.pointerType !== 'mouse') return;
  const bounds = stage.getBoundingClientRect();
  pointerX = ((event.clientX - bounds.left) / bounds.width - .5) * 16;
  pointerY = ((event.clientY - bounds.top) / bounds.height - .5) * 12;
  if (!pointerFrame) pointerFrame = requestAnimationFrame(() => {
    pointerFrame = 0;stage.style.setProperty('--px', `${pointerX}px`);stage.style.setProperty('--py', `${pointerY}px`);scheduleLine();
  });
});
stage.addEventListener('pointerleave', () => {stage.style.setProperty('--px','0px');stage.style.setProperty('--py','0px');scheduleLine();});
sourceScroll.addEventListener('scroll', () => scheduleLine(0), {passive: true});
window.addEventListener('resize', () => scheduleLine(0));
reducedMotion.addEventListener('change', () => {stage.style.setProperty('--px','0px');stage.style.setProperty('--py','0px');scheduleLine(0);});
renderWork();
document.fonts.ready.then(() => scheduleLine(0));
