import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { firstPage, pageBody, useResource } from './api';
import type {
  Coverage,
  LocatedDetail,
  ParagraphPick,
  Page,
  Paging,
  Section,
  Source,
  Span,
  Work,
} from './api';
import { Directory } from './Directory';
import { Annotations } from './Annotations';
import { Empty, Pager, Status } from './components';

type ReadingView = Paging & { sectionId: string; range?: Span; scroll: number };
type Anchor = { id: string; offset: number };

/** 正文定位使用稳定段落 ID，不以正文字符串、标签名或章节标题作为身份。 */
export function captureAnchor(element: HTMLElement | null): Anchor | null {
  if (!element) return null;
  const top = element.getBoundingClientRect().top;
  const row = [...element.querySelectorAll<HTMLElement>('[data-paragraph]')].find(
    (row) => row.getBoundingClientRect().bottom > top,
  );
  return row ? { id: row.dataset.paragraph!, offset: row.getBoundingClientRect().top - top } : null;
}

type ReaderOptions = {
  initialSection: string | null;
  initialRange?: Span;
  initialAnnotationId?: string;
  active: boolean;
  returnLabel?: string;
  onReturn: () => void;
};
function ReaderBody({
  work,
  initialSection,
  initialRange,
  initialAnnotationId,
  active,
  returnLabel,
  onReturn,
}: { work: Work } & ReaderOptions) {
  const [view, setView] = useState<ReadingView | null>(
    initialSection
      ? { ...firstPage(), sectionId: initialSection, range: initialRange, scroll: 0 }
      : null,
  );
  const [returnTo, setReturnTo] = useState<ReadingView | null>(null);
  const [contents, setContents] = useState(true);
  const [chapterSelection, setChapterSelection] = useState(0);
  const [notes, setNotes] = useState(Boolean(initialAnnotationId));
  const [pick, setPick] = useState<ParagraphPick | null>(null);
  const [focus, setFocus] = useState<LocatedDetail | null>(null);
  const onDetail = useCallback((detail: LocatedDetail) => {
    anchor.current = captureAnchor(prose.current);
    setFocus(detail);
  }, []);
  const clearFocus = useCallback(() => {
    anchor.current = captureAnchor(prose.current);
    setFocus(null);
  }, []);
  const prose = useRef<HTMLDivElement>(null);
  const anchor = useRef<Anchor | null>(null);
  const first = useResource<{ sections: Page<Section> }>(view ? null : '/library/browse', {
    view: 'sections',
    work_id: work.id,
    limit: 1,
    format: 'full',
  });
  useEffect(() => {
    if (!view && first.data?.sections.items[0])
      setView({ ...firstPage(), sectionId: first.data.sections.items[0].id, scroll: 0 });
  }, [first.data, view]);
  const section = useResource<Section>(
    view ? `/works/${work.id}/sections/${view.sectionId}` : null,
  );
  const source = useResource<Source>(
    view ? '/source/read' : null,
    view
      ? {
          work_id: work.id,
          section_id: view.sectionId,
          ...view.range,
          limit: 40,
          ...pageBody(view),
        }
      : undefined,
  );
  useEffect(() => {
    if (view && active)
      history.replaceState(null, '', `#work=${work.id}&section=${view.sectionId}`);
  }, [work.id, view?.sectionId, active]);
  useLayoutEffect(() => {
    if (source.data && prose.current) prose.current.scrollTop = view?.scroll || 0;
  }, [source.data]);
  useLayoutEffect(() => {
    if (!anchor.current || !prose.current) return;
    const row = [...prose.current.querySelectorAll<HTMLElement>('[data-paragraph]')].find(
      (row) => row.dataset.paragraph === anchor.current!.id,
    );
    if (row)
      prose.current.scrollTop +=
        row.getBoundingClientRect().top -
        prose.current.getBoundingClientRect().top -
        anchor.current.offset;
    anchor.current = null;
  }, [contents, notes, focus]);
  function toggle(kind: 'contents' | 'notes') {
    anchor.current = captureAnchor(prose.current);
    if (kind === 'contents') setContents((value) => !value);
    else {
      if (notes) {
        setFocus(null);
        setPick(null);
        if (returnTo) {
          setView(returnTo);
          setReturnTo(null);
          anchor.current = null;
        }
      }
      setNotes((value) => !value);
    }
  }
  function selectSection(id: string) {
    setReturnTo(null);
    setFocus(null);
    setPick(null);
    setNotes(false);
    setChapterSelection((value) => value + 1);
    setView({ ...firstPage(), sectionId: id, scroll: 0 });
  }
  function openReference(range: Span) {
    if (!returnTo && view) setReturnTo({ ...view, scroll: prose.current?.scrollTop || 0 });
    setView({ ...firstPage(), sectionId: range.section_id, range, scroll: 0 });
  }
  function restore() {
    if (returnTo) {
      setView(returnTo);
      setReturnTo(null);
    }
  }
  const data = source.data;
  const scope = data?.actual_range ? { section_id: data.section_id, ...data.actual_range } : null;
  const coverage = useResource<Coverage>(
    scope ? '/assets/annotations/coverage' : null,
    scope ? { work_id: work.id, ...scope } : undefined,
  );
  const counts = new Map(
    coverage.data?.items.map((item) => [item.paragraph_id, item.annotation_count]) || [],
  );
  const visibleRanges = notes
    ? focus?.locations.filter(
        (range) =>
          range.section_id === data?.section_id &&
          data.items.length &&
          range.start_ordinal <= data.items.at(-1)!.ordinal &&
          range.end_ordinal >= data.items[0].ordinal,
      ) || []
    : [];
  function openMarker(id: string, ordinal: number, count: number) {
    if (!data) return;
    anchor.current = captureAnchor(prose.current);
    setFocus(null);
    setPick({
      ordinal,
      count,
      span: { section_id: data.section_id, start_paragraph_id: id, end_paragraph_id: id },
    });
    setNotes(true);
    setChapterSelection((value) => value + 1);
  }
  const pageLabel = data?.items.length
    ? `第 ${data.items[0].ordinal}–${data.items.at(-1)!.ordinal} 段`
    : '';
  return (
    <div className="reading-page">
      <div className="workbar">
        <div>
          <h1>{work.name}</h1>
          <p className="hint">
            {section.data
              ? `${section.data.part_name} · ${section.data.title}`
              : '选择章节开始阅读'}{' '}
            · 原文只读
          </p>
        </div>
        <div className="tools">
          {returnLabel && <button onClick={onReturn}>{returnLabel}</button>}
          <button aria-expanded={contents} onClick={() => toggle('contents')}>
            {contents ? '收起目录' : '显示目录'}
          </button>
          <button aria-expanded={notes} onClick={() => toggle('notes')}>
            {notes ? '收起标注' : '显示标注'}
          </button>
        </div>
      </div>
      <div
        className={`workspace ${contents ? '' : 'without-directory'} ${notes ? '' : 'without-notes'}`}
      >
        <div className="directory-slot" hidden={!contents}>
          <Directory workId={work.id} selected={section.data} onSelect={selectSection} />
        </div>
        <main className="reader" aria-label="小说原文">
          <div className="reader-head">
            <h2>{section.data?.title || '原文'}</h2>
            <div className="reader-location">
              <span className="hint">
                {view?.range ? '引用阅读 · 本页' : '章节正文'}
                {pageLabel ? ` · ${pageLabel}` : ''}
              </span>
              {returnTo && <button onClick={restore}>返回阅读位置</button>}
              {view?.range && (
                <button onClick={() => selectSection(view.sectionId)}>从本章开头阅读</button>
              )}
            </div>
            <div className="marker-status">
              {data &&
                (coverage.loading ? (
                  <span role="status">正在读取页边标注…</span>
                ) : coverage.error ? (
                  <span role="alert">
                    页边标注读取失败，正文仍可阅读。
                    <button onClick={coverage.retry}>重试标记</button>
                  </span>
                ) : (
                  <span>页边数字表示关联分析条数，点击查看。</span>
                ))}
            </div>
            {notes && focus && data && (
              <p className="focus-caption" role="status">
                当前分析：{focus.tags.map((tag) => tag.full_name).join(' · ') || '无标签标注'}
                <br />
                {visibleRanges.length
                  ? visibleRanges
                      .map(
                        (range) =>
                          `依据第 ${range.start_ordinal}–${range.end_ordinal} 段${data!.items[0].ordinal > range.start_ordinal ? '（前页续入）' : ''}${data!.items.at(-1)!.ordinal < range.end_ordinal ? '（后页继续）' : ''}`,
                      )
                      .join('；')
                  : '引用位于其他位置，可从右侧打开。'}
              </p>
            )}
          </div>
          <div className="prose" ref={prose} tabIndex={0} aria-label="可独立滚动的原文">
            <Status resource={source} />
            <Status resource={section} />
            {!view && <Status resource={first} />}{' '}
            {!view && first.data?.sections.items.length === 0 && (
              <Empty>作品暂无章节，等待外部 AI 导入原文。</Empty>
            )}
            {data?.items.length === 0 && <Empty>此范围没有正文。</Empty>}
            {data?.items.map((item) => {
              const focused = visibleRanges.some(
                (range) => item.ordinal >= range.start_ordinal && item.ordinal <= range.end_ordinal,
              );
              const count = counts.get(item.id) || 0;
              return (
                <div
                  className={`paragraph ${focused ? 'focus-range' : ''}`}
                  key={item.id}
                  data-paragraph={item.id}
                >
                  <span className="paragraph-number">{item.ordinal}</span>
                  <p>{item.text}</p>
                  <span className="paragraph-marker">
                    {count > 0 && (
                      <button
                        aria-label={`第 ${item.ordinal} 段：${count} 条分析`}
                        aria-pressed={pick?.span.start_paragraph_id === item.id && notes}
                        onClick={() => openMarker(item.id, item.ordinal, count)}
                        title={`查看第 ${item.ordinal} 段的 ${count} 条分析`}
                      >
                        <svg viewBox="0 0 20 20" aria-hidden="true">
                          <path d="M3 3h14v10H8l-5 4V3Z" />
                        </svg>
                        <span>{count}</span>
                      </button>
                    )}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="reader-footer">
            {view && (
              <Pager
                paging={view}
                next={data?.next_cursor}
                disabled={source.loading || !data}
                onChange={(paging) => {
                  if (!view.range) {
                    setPick(null);
                    setFocus(null);
                    setNotes(false);
                    setChapterSelection((value) => value + 1);
                  }
                  setView({ ...view, ...paging, scroll: 0 });
                }}
              />
            )}
            <span className="hint">
              {data
                ? data.next_cursor
                  ? '后面还有正文'
                  : view?.range
                    ? '已到引用末尾'
                    : '已到本章末尾'
                : '每页最多 40 个完整段落'}
            </span>
          </div>
        </main>
        <div className="annotation-slot" hidden={!notes}>
          {notes && (
            <Annotations
              key={chapterSelection}
              initialSelectedId={chapterSelection === 0 ? initialAnnotationId : undefined}
              workId={work.id}
              paragraphPick={pick}
              onDetail={onDetail}
              onClear={clearFocus}
              pageScope={scope}
              active={view?.range}
              onOpen={openReference}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export function Reader({ workId, ...options }: { workId: string } & ReaderOptions) {
  const work = useResource<Work>(`/works/${workId}`);
  return work.data ? (
    <ReaderBody work={work.data} {...options} />
  ) : (
    <main className="page">
      <Status resource={work} />
      <a href="#">返回作品库</a>
    </main>
  );
}
