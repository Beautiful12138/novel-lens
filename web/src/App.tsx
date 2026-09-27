import { useEffect, useRef, useState } from 'react';
import { firstPage, pageBody, parseLocation, useResource } from './api';
import type { Page, Work } from './api';
import { Empty, Pager, Status } from './components';
import { Reader } from './Reader';
import { AnnotationLibrary, TagLibrary } from './AssetLibrary';
import type { AssetJump } from './AssetLibrary';

function Works({ onNavigate }: { onNavigate: () => void }) {
  const [paging, setPaging] = useState(firstPage);
  const works = useResource<{ works: Page<Work> }>('/library/browse', {
    view: 'works',
    format: 'full',
    limit: 20,
    ...pageBody(paging),
  });
  return (
    <main className="page library">
      <div className="page-heading">
        <div>
          <h1>作品库</h1>
          <p className="hint">打开作品，阅读原文与已保存的分析标注。</p>
        </div>
        <button disabled={works.loading} onClick={works.retry}>
          刷新作品
        </button>
      </div>
      <Status resource={works} />
      {works.data?.works.items.length === 0 && (
        <Empty>还没有可见作品。由外部 AI 导入后，刷新即可查看。</Empty>
      )}
      {works.data && (
        <>
          <ul className="work-list">
            {works.data.works.items.map((work) => (
              <li key={work.id}>
                <div>
                  <h2>
                    <a href={`#work=${work.id}`} onClick={onNavigate}>
                      {work.name}
                    </a>
                  </h2>
                  <p className="hint">
                    {work.part_count} 个分部 · {work.section_count} 章 ·{' '}
                    {work.character_count.toLocaleString()} 字
                  </p>
                </div>
                <a className="button primary" href={`#work=${work.id}`} onClick={onNavigate}>
                  打开阅读
                </a>
              </li>
            ))}
          </ul>
          <Pager
            paging={paging}
            next={works.data.works.next_cursor}
            onChange={setPaging}
            disabled={works.loading}
          />
        </>
      )}
    </main>
  );
}

type ViewName = 'works' | 'reader' | 'tags' | 'annotations';
function routeFromHash() {
  const locationInfo = parseLocation(location.hash);
  const page = new URLSearchParams(location.hash.slice(1)).get('view');
  const view: ViewName = locationInfo.work
    ? 'reader'
    : page === 'tags' || page === 'annotations'
      ? page
      : 'works';
  return { ...locationInfo, view };
}

/** 资产页保持挂载，使引用跳转不丢失查询、页码、选择与两栏位置。 */
export default function App() {
  const [route, setRoute] = useState(routeFromHash);
  const lastWork = useRef(route);
  const [jump, setJump] = useState<(AssetJump & { token: number }) | null>(null);
  const jumpSequence = useRef(0);
  const [returnSource, setReturnSource] = useState<'tags' | 'annotations' | null>(null);
  const [visited, setVisited] = useState({
    tags: route.view === 'tags',
    annotations: route.view === 'annotations',
  });
  if (route.work) lastWork.current = route;
  useEffect(() => {
    const changed = () => setRoute(routeFromHash());
    window.addEventListener('hashchange', changed);
    return () => window.removeEventListener('hashchange', changed);
  }, []);
  useEffect(() => {
    if (route.view === 'tags' || route.view === 'annotations')
      setVisited((previous) => ({ ...previous, [route.view]: true }));
    const title = { works: '作品库', reader: '阅读与标注', tags: '标签库', annotations: '标注库' }[
      route.view
    ];
    document.title = `NovelLens · ${title}`;
  }, [route.view]);
  function openAsset(source: 'tags' | 'annotations', target: AssetJump) {
    setReturnSource(source);
    setJump({ ...target, token: ++jumpSequence.current });
    const hash = `#work=${target.workId}&section=${target.range.section_id}`;
    if (location.hash === hash)
      setRoute({ work: target.workId, section: target.range.section_id, view: 'reader' });
    else location.hash = hash;
  }
  const target = jump?.workId === lastWork.current.work ? jump : null;
  return (
    <>
      <a
        className="skip"
        href="#main-content"
        onClick={(event) => {
          event.preventDefault();
          document.getElementById('main-content')?.focus();
        }}
      >
        跳到内容
      </a>
      <header className="app-header">
        <a className="brand" href="#" onClick={() => setReturnSource(null)}>
          NovelLens
        </a>
        <nav aria-label="主导航" onClick={() => setReturnSource(null)}>
          <a href="#" aria-current={route.view === 'works' ? 'page' : undefined}>
            作品库
          </a>
          {lastWork.current.work && (
            <a
              href={`#work=${lastWork.current.work}`}
              aria-current={route.view === 'reader' ? 'page' : undefined}
            >
              阅读与标注
            </a>
          )}
          <a href="#view=tags" aria-current={route.view === 'tags' ? 'page' : undefined}>
            标签库
          </a>
          <a
            href="#view=annotations"
            aria-current={route.view === 'annotations' ? 'page' : undefined}
          >
            标注库
          </a>
        </nav>
        <span className="header-note">原文与分析</span>
      </header>
      <div id="main-content" className="app-content" tabIndex={-1}>
        <div hidden={route.view !== 'works'} className="view">
          <Works onNavigate={() => setReturnSource(null)} />
        </div>
        {lastWork.current.work && (
          <div hidden={route.view !== 'reader'} className="view">
            <Reader
              key={`${lastWork.current.work}:${target?.token || 0}`}
              workId={lastWork.current.work}
              initialSection={lastWork.current.section}
              initialRange={target?.range}
              initialAnnotationId={target?.annotationId}
              active={route.view === 'reader'}
              returnLabel={
                returnSource ? `返回${returnSource === 'tags' ? '标签库' : '标注库'}` : undefined
              }
              onReturn={() => {
                const source = returnSource;
                setReturnSource(null);
                location.hash = `#view=${source}`;
              }}
            />
          </div>
        )}
        {visited.tags && (
          <div hidden={route.view !== 'tags'} className="view">
            <TagLibrary onOpen={(target) => openAsset('tags', target)} />
          </div>
        )}
        {visited.annotations && (
          <div hidden={route.view !== 'annotations'} className="view">
            <AnnotationLibrary onOpen={(target) => openAsset('annotations', target)} />
          </div>
        )}
      </div>
    </>
  );
}
