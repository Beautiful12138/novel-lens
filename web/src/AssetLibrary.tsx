import { useState } from 'react';
import { firstPage, pageBody, useResource } from './api';
import type { Mark, Page, Section, Span, Tag, Work } from './api';
import { Empty, Pager, Status } from './components';
import { MarkDetails } from './Annotations';

export type SharedTag = Tag & {
  name: string;
  namespace: string;
  description: string;
  aliases: string[];
};
export type AssetJump = { workId: string; annotationId: string; range: Span };
type AssetMark = Mark & {
  work_id: string;
  work_name: string;
  part_name: string;
  section_title: string;
  status: 'active' | 'withdrawn';
};
type Filters = {
  work_id: string;
  section_id: string;
  tag_id: string;
  query: string;
  status: string;
};

/** 下拉目录按需追加元数据；过滤条件变化清空旧选项，原文不在这里下载。 */
function CatalogSelect({
  kind,
  workId,
  value,
  onChange,
}: {
  kind: 'works' | 'sections';
  workId?: string;
  value: string;
  onChange: (id: string) => void;
}) {
  const [paging, setPaging] = useState(firstPage);
  const [previous, setPrevious] = useState<(Work | Section)[]>([]);
  const result = useResource<{ works?: Page<Work>; sections?: Page<Section> }>(
    kind === 'sections' && !workId ? null : '/library/browse',
    {
      view: kind,
      ...(workId ? { work_id: workId } : {}),
      format: 'full',
      limit: 40,
      ...pageBody(paging),
    },
  );
  const page = kind === 'works' ? result.data?.works : result.data?.sections;
  const options = [...previous, ...(page?.items || [])];
  const label = kind === 'works' ? '作品范围' : '章节范围';
  return (
    <label className="filter-field">
      {label}
      <select
        aria-label={label}
        value={value}
        disabled={kind === 'sections' && !workId}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">{kind === 'works' ? '全部可见作品' : '全部章节'}</option>
        {options.map((item) => (
          <option key={item.id} value={item.id}>
            {'title' in item ? `${item.part_name} · ${item.title}` : item.name}
          </option>
        ))}
      </select>
      {page?.next_cursor && (
        <button
          type="button"
          onClick={() => {
            setPrevious(options);
            setPaging({ cursors: [page.next_cursor], page: 0 });
          }}
        >
          加载更多{kind === 'works' ? '作品' : '章节'}
        </button>
      )}
      {result.error && <Status resource={result} />}
    </label>
  );
}

function TagFilter({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const [query, setQuery] = useState('');
  const [searched, setSearched] = useState('');
  const [paging, setPaging] = useState(firstPage);
  const tags = useResource<{ tag_page: Page<SharedTag> }>('/library/browse', {
    view: 'tags',
    limit: 20,
    ...(searched ? { query: searched } : {}),
    ...pageBody(paging),
  });
  const [picked, setPicked] = useState<SharedTag | null>(null);
  const items = tags.data?.tag_page.items || [];
  return (
    <div className="tag-filter">
      <label>
        标签
        <select
          aria-label="标签筛选"
          value={value}
          onChange={(event) => {
            const id = event.target.value;
            setPicked(items.find((tag) => tag.id === id) || null);
            onChange(id);
          }}
        >
          <option value="">全部标签</option>
          {picked && !items.some((tag) => tag.id === picked.id) && (
            <option value={picked.id}>{picked.full_name}</option>
          )}
          {items.map((tag) => (
            <option key={tag.id} value={tag.id}>
              {tag.full_name}
            </option>
          ))}
        </select>
      </label>
      <details>
        <summary>查找更多标签</summary>
        <label>
          名称、定义或别名
          <input value={query} onChange={(event) => setQuery(event.target.value)} maxLength={300} />
        </label>
        <button
          type="button"
          onClick={() => {
            setSearched(query.trim());
            setPaging(firstPage());
          }}
        >
          查找标签
        </button>
        <Status resource={tags} />
        {tags.data && (
          <Pager
            paging={paging}
            next={tags.data.tag_page.next_cursor}
            onChange={setPaging}
            disabled={tags.loading}
          />
        )}
      </details>
    </div>
  );
}

function AnnotationResults({
  filters,
  onOpen,
}: {
  filters: Filters;
  onOpen: (jump: AssetJump) => void;
}) {
  const [paging, setPaging] = useState(firstPage);
  const [selected, setSelected] = useState<AssetMark | null>(null);
  const result = useResource<Page<AssetMark>>('/assets/annotations/browse', {
    ...(filters.work_id ? { work_id: filters.work_id } : {}),
    ...(filters.section_id ? { section_id: filters.section_id } : {}),
    ...(filters.tag_id ? { tag_ids: [filters.tag_id] } : {}),
    ...(filters.query ? { query: filters.query } : {}),
    status: filters.status === 'all' ? null : filters.status,
    limit: 12,
    ...pageBody(paging),
  });
  return (
    <div className="asset-results">
      <section className="asset-list" aria-label="标注结果">
        <div className="asset-list-scroll" key={paging.page}>
          <Status resource={result} />
          {result.data?.items.length === 0 && (
            <Empty>当前条件下没有标注。可以调整筛选；选择“全部状态”也能查看已撤回项。</Empty>
          )}
          {result.data?.items.map((mark) => (
            <button
              className="asset-row"
              key={mark.id}
              aria-pressed={selected?.id === mark.id}
              onClick={() => setSelected(mark)}
            >
              <strong>{mark.work_name}</strong>
              <span className="hint">
                首处引用：{mark.part_name} · {mark.section_title}
                <br />
                {mark.source_range_count} 处依据 · {mark.status === 'active' ? '有效' : '已撤回'}
              </span>
              <p>
                {mark.note_preview || '未填写说明'}
                {mark.note_truncated ? '…' : ''}
              </p>
              <span className="tags">
                {mark.tags.map((tag) => (
                  <span key={tag.id}>{tag.full_name}</span>
                ))}
              </span>
            </button>
          ))}
        </div>
        <Pager
          paging={paging}
          next={result.data?.next_cursor}
          disabled={result.loading}
          onChange={(value) => {
            setPaging(value);
            setSelected(null);
          }}
        />
      </section>
      <section className="asset-detail" aria-label="标注完整详情">
        {selected ? (
          <MarkDetails
            key={selected.id}
            id={selected.id}
            workId={selected.work_id}
            onBack={() => setSelected(null)}
            onOpen={(range) =>
              onOpen({ workId: selected.work_id, annotationId: selected.id, range })
            }
          />
        ) : (
          <Empty>选择一条标注，查看完整说明、标签和全部原文依据。</Empty>
        )}
      </section>
    </div>
  );
}

/** 两个资产入口共用服务端过滤；筛选提交后重置游标及过时详情。 */
function AnnotationBrowser({
  tag,
  onOpen,
}: {
  tag?: SharedTag;
  onOpen: (jump: AssetJump) => void;
}) {
  const initial: Filters = {
    work_id: '',
    section_id: '',
    tag_id: tag?.id || '',
    query: '',
    status: 'active',
  };
  const [draft, setDraft] = useState(initial);
  const [filters, setFilters] = useState(initial);
  return (
    <div className="annotation-browser">
      <form
        className="asset-filters"
        onSubmit={(event) => {
          event.preventDefault();
          setFilters({ ...draft, query: draft.query.trim() });
        }}
      >
        <CatalogSelect
          kind="works"
          value={draft.work_id}
          onChange={(work_id) => setDraft({ ...draft, work_id, section_id: '' })}
        />
        {!tag && (
          <CatalogSelect
            key={draft.work_id}
            kind="sections"
            workId={draft.work_id}
            value={draft.section_id}
            onChange={(section_id) => setDraft({ ...draft, section_id })}
          />
        )}
        <label className="filter-field">
          状态
          <select
            aria-label="标注状态"
            value={draft.status}
            onChange={(event) => setDraft({ ...draft, status: event.target.value })}
          >
            <option value="active">有效标注</option>
            <option value="withdrawn">已撤回</option>
            <option value="all">全部状态</option>
          </select>
        </label>
        {!tag && (
          <TagFilter value={draft.tag_id} onChange={(tag_id) => setDraft({ ...draft, tag_id })} />
        )}
        <label className="filter-field search-field">
          说明文字
          <input
            aria-label="搜索标注说明"
            placeholder="按字面搜索说明"
            value={draft.query}
            maxLength={300}
            onChange={(event) => setDraft({ ...draft, query: event.target.value })}
          />
        </label>
        <button className="primary" type="submit">
          应用筛选
        </button>
        <button
          type="button"
          onClick={() => {
            setDraft(initial);
            setFilters(initial);
          }}
        >
          清除筛选
        </button>
      </form>
      <AnnotationResults key={JSON.stringify(filters)} filters={filters} onOpen={onOpen} />
    </div>
  );
}

export function AnnotationLibrary({ onOpen }: { onOpen: (jump: AssetJump) => void }) {
  return (
    <main className="asset-page">
      <div className="asset-heading">
        <h1>标注库</h1>
        <p className="hint">跨作品查看分析说明，通过每处引用回到原文。</p>
      </div>
      <AnnotationBrowser onOpen={onOpen} />
    </main>
  );
}

export function TagLibrary({ onOpen }: { onOpen: (jump: AssetJump) => void }) {
  const [draft, setDraft] = useState({ query: '', namespace: '' });
  const [filter, setFilter] = useState(draft);
  const [paging, setPaging] = useState(firstPage);
  const [selected, setSelected] = useState<SharedTag | null>(null);
  const result = useResource<{ tag_page: Page<SharedTag> }>('/library/browse', {
    view: 'tags',
    ...(filter.query ? { query: filter.query } : {}),
    ...(filter.namespace ? { namespace: filter.namespace } : {}),
    limit: 20,
    ...pageBody(paging),
  });
  return (
    <main className="asset-page">
      <div className="asset-heading">
        <h1>标签库</h1>
        <p className="hint">共享标签用于组织原文依据；使用位置来自关联标注。</p>
      </div>
      <div className="tag-workspace">
        <section className="tag-directory" aria-label="标签目录">
          <form
            onSubmit={(event) => {
              event.preventDefault();
              setFilter({ query: draft.query.trim(), namespace: draft.namespace.trim() });
              setPaging(firstPage());
              setSelected(null);
            }}
          >
            <label>
              名称、定义或别名
              <input
                aria-label="搜索标签"
                value={draft.query}
                maxLength={300}
                onChange={(event) => setDraft({ ...draft, query: event.target.value })}
              />
            </label>
            <label>
              标签分组
              <input
                aria-label="标签分组"
                placeholder="精确分组，例如：人物"
                value={draft.namespace}
                maxLength={64}
                onChange={(event) => setDraft({ ...draft, namespace: event.target.value })}
              />
            </label>
            <div className="tools">
              <button className="primary">搜索标签</button>
              <button
                type="button"
                onClick={() => {
                  setDraft({ query: '', namespace: '' });
                  setFilter({ query: '', namespace: '' });
                  setPaging(firstPage());
                  setSelected(null);
                }}
              >
                清除
              </button>
            </div>
          </form>
          <div className="tag-list" key={`${JSON.stringify(filter)}:${paging.page}`}>
            <Status resource={result} />
            {result.data?.tag_page.items.length === 0 && <Empty>没有匹配的标签。</Empty>}
            {result.data?.tag_page.items.map((tag) => (
              <button
                key={tag.id}
                className="tag-row"
                aria-pressed={selected?.id === tag.id}
                onClick={() => setSelected(tag)}
              >
                <strong>{tag.full_name}</strong>
                <span>{tag.description}</span>
              </button>
            ))}
          </div>
          <Pager
            paging={paging}
            next={result.data?.tag_page.next_cursor}
            disabled={result.loading}
            onChange={(value) => {
              setPaging(value);
              setSelected(null);
            }}
          />
        </section>
        <section className="tag-content" aria-label="标签定义与使用位置">
          {selected ? (
            <>
              <div className="tag-definition">
                <h2>{selected.full_name}</h2>
                <p>{selected.description}</p>
                <p className="hint">
                  别名：{selected.aliases.length ? selected.aliases.join('、') : '暂无'} · 全库共享
                </p>
              </div>
              <AnnotationBrowser key={selected.id} tag={selected} onOpen={onOpen} />
            </>
          ) : (
            <Empty>选择标签，查看定义、别名与各部作品中的原文位置。</Empty>
          )}
        </section>
      </div>
    </main>
  );
}
