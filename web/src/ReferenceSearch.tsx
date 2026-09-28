import { useRef, useState } from 'react';
import { firstPage, pageBody, useResource } from './api';
import type { Page, Part, Section, Span, Work, Paging } from './api';
import { Empty, Pager, Status } from './components';

export type SearchJump = { workId: string; range: Span; annotationId?: string };
type Selection = {
  work: Work;
  partId: string;
  sectionId: string;
  partName: string;
  sectionName: string;
};
type Scope = { work_id: string; part_ids?: string[]; section_ids?: string[] };
type Query = { query: string; scope: Scope[]; limit: number; format: 'compact' };
type Hit = {
  work_name: string;
  part_name: string;
  section_title: string;
  source_range: Span & { work_id: string };
  excerpt: string;
  excerpt_truncated: boolean;
  annotation_ids: string[];
};
type Results = Page<Hit> & {
  search_id: string;
  expires_at: string;
  candidate_window_limited: boolean;
  warnings?: { code: string; work_id: string; message: string }[];
};

/** 只分页读取目录；父组件在所属作品或分部切换时重建，防止旧选项污染新范围。 */
function ScopeSelect({
  kind,
  selection,
  onChange,
}: {
  kind: 'parts' | 'sections';
  selection: Selection;
  onChange: (id: string, name: string) => void;
}) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [previous, setPrevious] = useState<(Part | Section)[]>([]);
  const result = useResource<{ parts?: Page<Part>; sections?: Page<Section> }>('/library/browse', {
    view: kind,
    work_id: selection.work.id,
    format: 'full',
    limit: 40,
    ...(kind === 'sections' && selection.partId ? { part_id: selection.partId } : {}),
    ...(cursor ? { cursor } : {}),
  });
  const page = result.data?.[kind];
  const options = [...previous, ...(page?.items || [])];
  const label = `${selection.work.name} · ${kind === 'parts' ? '分部' : '章节'}`;
  return (
    <div className="search-scope-select">
      <label>
        {kind === 'parts' ? '分部' : '章节'}
        <select
          aria-label={label}
          value={kind === 'parts' ? selection.partId : selection.sectionId}
          onChange={(e) => {
            const item = options.find((item) => item.id === e.target.value);
            onChange(
              e.target.value,
              item ? ('title' in item ? `${item.part_name} · ${item.title}` : item.name) : '',
            );
          }}
        >
          <option value="">{kind === 'parts' ? '全部分部' : '全部章节'}</option>
          {options.map((item) => (
            <option key={item.id} value={item.id}>
              {'title' in item ? `${item.part_name} · ${item.title}` : item.name}
            </option>
          ))}
        </select>
      </label>
      <Status resource={result} />
      {page?.next_cursor && (
        <button
          type="button"
          disabled={result.loading}
          onClick={() => {
            setPrevious(options);
            setCursor(page.next_cursor);
          }}
        >
          加载更多{kind === 'parts' ? '分部' : '章节'}
        </button>
      )}
    </div>
  );
}

const hitKey = (hit: Hit) => JSON.stringify(hit.source_range);

/** 一次提交拥有一条搜索快照；翻页绝不重新提交自然语言或范围。 */
function SearchResults({
  query,
  scopeLabel,
  onOpen,
  onRestart,
}: {
  query: Query;
  scopeLabel: string;
  onOpen: (target: SearchJump) => void;
  onRestart: () => void;
}) {
  const [paging, setPaging] = useState(firstPage);
  const [body, setBody] = useState<object>(query);
  const result = useResource<Results>('/reference/query', body, 90000);
  const [selected, setSelected] = useState<string | null>(null);
  const scroll = useRef<HTMLDivElement>(null);
  const stale = ['REFERENCE_SEARCH_UNAVAILABLE', 'REFERENCE_SEARCH_STALE'].includes(
    result.errorCode || '',
  );
  const notReady = result.errorCode === 'REFERENCE_NOT_READY';
  function turn(next: Paging) {
    if (!result.data) return;
    setBody({ search_id: result.data.search_id, format: 'compact', ...pageBody(next) });
    setPaging(next);
    setSelected(null);
    if (scroll.current) scroll.current.scrollTop = 0;
  }
  return (
    <section className="search-results" aria-label="原文检索结果">
      <header className="search-result-heading">
        <h2>“{query.query}”</h2>
        <p className="hint">{scopeLabel}</p>
        <button onClick={onRestart}>重新检索</button>
      </header>
      <div className="search-result-scroll" ref={scroll}>
        {result.loading && <p role="status">正在检索原文，请稍候…</p>}
        {result.error && (
          <div className="error" role="alert">
            <p>
              {stale
                ? '搜索已过期或参考内容发生变化，请按原条件重新检索。'
                : notReady
                  ? '所选范围的原文索引尚未就绪，请等待准备完成，或缩小检索范围。'
                  : result.error}
            </p>
            <button onClick={stale ? onRestart : result.retry}>
              {stale ? '按原条件重新检索' : '重试检索'}
            </button>
          </div>
        )}
        {result.data?.warnings?.map((warning) => (
          <p role="status" className="status" key={`${warning.work_id}:${warning.code}`}>
            {warning.message}
          </p>
        ))}
        {result.data?.items.length === 0 && (
          <Empty>当前条件下没有匹配原文。可调整描述或作品范围后再检索。</Empty>
        )}
        <ol className="reference-list">
          {result.data?.items.map((hit, index) => (
            <li key={hitKey(hit)} className={selected === hitKey(hit) ? 'selected' : ''}>
              <h3>
                {hit.work_name} · {hit.part_name}
              </h3>
              <p className="hint">{hit.section_title}</p>
              <blockquote>{hit.excerpt}</blockquote>
              <div className="reference-actions">
                <span className="hint">
                  {hit.excerpt_truncated
                    ? '摘录未完整显示，请打开原文。'
                    : '候选原文 · 可继续补读上下文'}
                </span>
                <button
                  className="primary"
                  aria-label={`阅读候选 ${index + 1} 的原文与上下文`}
                  aria-pressed={selected === hitKey(hit)}
                  onClick={() => {
                    setSelected(hitKey(hit));
                    onOpen({ workId: hit.source_range.work_id, range: hit.source_range });
                  }}
                >
                  阅读原文与上下文
                </button>
              </div>
            </li>
          ))}
        </ol>
        {result.data?.candidate_window_limited && (
          <p className="status">
            候选窗口已达上限，当前结果不代表全部匹配；可缩小范围或调整描述继续查找。
          </p>
        )}
      </div>
      {result.data && (
        <footer className="search-result-footer">
          <Pager
            paging={paging}
            next={result.data.next_cursor}
            onChange={turn}
            disabled={result.loading}
          />
          {!result.data.next_cursor && <span className="hint">已到本次候选末尾</span>}
        </footer>
      )}
    </section>
  );
}

/** 草稿与结果条件分离，阅读往返保持挂载以保留结果及滚动位置。 */
export function ReferenceSearch({ onOpen }: { onOpen: (target: SearchJump) => void }) {
  const [text, setText] = useState('');
  const [selected, setSelected] = useState<Selection[]>([]);
  const [paging, setPaging] = useState(firstPage);
  const works = useResource<{ works: Page<Work> }>('/library/browse', {
    view: 'works',
    format: 'full',
    limit: 20,
    ...pageBody(paging),
  });
  const [submitted, setSubmitted] = useState<{
    query: Query;
    scopeLabel: string;
    token: number;
  } | null>(null);
  const [validation, setValidation] = useState('');
  function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!text.trim() || !selected.length) {
      setValidation('请输入检索描述，并至少选择一部作品。');
      return;
    }
    setValidation('');
    setSubmitted({
      query: {
        query: text.trim(),
        format: 'compact',
        limit: 8,
        scope: selected.map((s) => ({
          work_id: s.work.id,
          ...(s.sectionId
            ? { section_ids: [s.sectionId] }
            : s.partId
              ? { part_ids: [s.partId] }
              : {}),
        })),
      },
      scopeLabel: selected
        .map((s) => `${s.work.name} / ${s.sectionName || s.partName || '全部原文'}`)
        .join('；'),
      token: (submitted?.token || 0) + 1,
    });
  }
  function change(id: string, patch: Partial<Selection>) {
    setSelected((values) =>
      values.map((value) => (value.work.id === id ? { ...value, ...patch } : value)),
    );
  }
  return (
    <main className="reference-search">
      <div className="asset-heading">
        <h1>原文检索</h1>
        <p className="hint">描述想找的场景或写法，打开原文并结合上下文阅读。</p>
      </div>
      <div className="search-layout">
        <form className="search-form" onSubmit={submit}>
          <label className="search-query">
            想找什么原文？
            <textarea
              value={text}
              maxLength={8192}
              rows={3}
              placeholder="例如：人物重逢时，欲言又止的场景"
              onChange={(e) => setText(e.target.value)}
            />
          </label>
          <fieldset className="search-works">
            <legend>作品范围 · 已选 {selected.length}/20</legend>
            <Status resource={works} />
            {works.data?.works.items.length === 0 && (
              <Empty>没有可选作品，请先由外部 AI 导入。</Empty>
            )}
            {works.data?.works.items.map((work) => (
              <label key={work.id} className="work-choice">
                <input
                  type="checkbox"
                  checked={selected.some((s) => s.work.id === work.id)}
                  disabled={selected.length >= 20 && !selected.some((s) => s.work.id === work.id)}
                  onChange={(e) =>
                    setSelected((values) =>
                      e.target.checked
                        ? [
                            ...values,
                            { work, partId: '', sectionId: '', partName: '', sectionName: '' },
                          ]
                        : values.filter((s) => s.work.id !== work.id),
                    )
                  }
                />
                {work.name}
              </label>
            ))}
            {(paging.page > 0 || works.data?.works.next_cursor) && (
              <Pager
                paging={paging}
                next={works.data?.works.next_cursor}
                onChange={setPaging}
                disabled={works.loading || !works.data}
              />
            )}
          </fieldset>
          {selected.map((selection) => (
            <section className="selected-scope" key={selection.work.id}>
              <div className="scope-title">
                <h2>{selection.work.name}</h2>
                <button
                  type="button"
                  aria-label={`移除${selection.work.name}`}
                  onClick={() =>
                    setSelected((values) => values.filter((s) => s.work.id !== selection.work.id))
                  }
                >
                  移除
                </button>
              </div>
              <details>
                <summary>
                  {selection.sectionName || selection.partName || '全部原文'} · 限定范围
                </summary>
                <ScopeSelect
                  kind="parts"
                  selection={selection}
                  onChange={(partId, partName) =>
                    change(selection.work.id, { partId, partName, sectionId: '', sectionName: '' })
                  }
                />
                <ScopeSelect
                  key={selection.partId}
                  kind="sections"
                  selection={selection}
                  onChange={(sectionId, sectionName) =>
                    change(selection.work.id, { sectionId, sectionName })
                  }
                />
              </details>
            </section>
          ))}
          {validation && <p role="alert">{validation}</p>}
          <button className="primary search-submit" type="submit">
            检索原文
          </button>
          <p className="hint">调整条件后点击检索。标注线索辅助查找，结果以原文为中心。</p>
        </form>
        {submitted ? (
          <SearchResults
            key={submitted.token}
            {...submitted}
            onOpen={onOpen}
            onRestart={() =>
              setSubmitted((value) => (value ? { ...value, token: value.token + 1 } : value))
            }
          />
        ) : (
          <section className="search-results">
            <Empty>选择作品并提交描述，在这里查看适用原文。</Empty>
          </section>
        )}
      </div>
    </main>
  );
}
