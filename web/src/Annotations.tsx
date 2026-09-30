import { useEffect, useState } from 'react';
import { firstPage, pageBody, useResource } from './api';
import type {
  LocatedDetail,
  ReferenceLocation,
  ParagraphPick,
  Mark,
  Page,
  Span,
  Source,
} from './api';
import { Empty, Pager, Status } from './components';

/** 引用坐标与说明同一快照，重复章节名仍区分分部，不批量下载引用正文。 */
function Reference({
  workId,
  span,
  index,
  location,
  active,
  onOpen,
  onEvidence,
  roleNote,
}: {
  workId: string;
  span: Span;
  index: number;
  location: ReferenceLocation;
  active: boolean;
  onOpen: () => void;
  onEvidence: () => void;
  roleNote: string;
}) {
  const [preview, setPreview] = useState(false);

  return (
    <li>
      <button className="reference" aria-current={active ? 'location' : undefined} onClick={onOpen}>
        <span>引用 {index + 1}</span>
        <strong>
          {location.part_name} · {location.section_title}
        </strong>
        <span>
          第 {location.reading_start_ordinal}–{location.reading_end_ordinal} 段 · 连续阅读
        </span>
      </button>
      <p>{roleNote}</p>
      <button className="preview-toggle" onClick={onEvidence}>
        定位证据 · 第 {location.start_ordinal}–{location.end_ordinal} 段
      </button>
      <button
        className="preview-toggle"
        onClick={() => setPreview((value) => !value)}
        aria-expanded={preview}
      >
        {preview ? '收起原文预览' : '预览原文'}
      </button>
      {preview && <QuotePreview workId={workId} span={span} />}
    </li>
  );
}

/** 预览按完整自然段分页，避免为每张引用卡一次载入长篇证据。 */
function QuotePreview({ workId, span }: { workId: string; span: Span }) {
  const [paging, setPaging] = useState(firstPage);
  const source = useResource<Source>('/source/read', {
    work_id: workId,
    ...span,
    limit: 3,
    ...pageBody(paging),
  });
  return (
    <div className="quote-preview">
      <Status resource={source} />
      {source.data?.items.map((item) => (
        <p key={item.id}>
          <span className="hint">第 {item.ordinal} 段</span>
          <br />
          {item.text}
        </p>
      ))}
      {source.data && (
        <>
          <Pager
            paging={paging}
            next={source.data.next_cursor}
            onChange={setPaging}
            disabled={source.loading}
          />
          <p className="hint">{source.data.next_cursor ? '后面还有引用原文' : '已到引用末尾'}</p>
        </>
      )}
    </div>
  );
}

export function MarkDetails({
  id,
  workId,
  active,
  onBack,
  onOpen,
  onDetail,
}: {
  onDetail?: (detail: LocatedDetail) => void;
  id: string;
  workId: string;
  active?: Span;
  onBack: () => void;
  onOpen: (span: Span) => void;
}) {
  const detail = useResource<LocatedDetail>('/assets/annotations/detail', {
    work_id: workId,
    annotation_id: id,
  });
  useEffect(() => {
    if (detail.data) onDetail?.(detail.data);
  }, [detail.data, onDetail]);
  return (
    <>
      <div className="panel-head">
        <button onClick={onBack}>返回标注列表</button>
        <h2>标注详情</h2>
      </div>
      <div className="panel-scroll">
        <Status resource={detail} />
        {detail.data && (
          <>
            {detail.data.annotation.status === 'withdrawn' && (
              <p className="withdrawn">此标注已撤回，不参与默认有效标注召回。</p>
            )}
            <p className="hint">
              {detail.data.annotation.kind === 'comparison' ? '作品认识' : '具体观察'}
            </p>
            <h3>{detail.data.annotation.title}</h3>
            <p className="hint">适用范围：{detail.data.annotation.scope_note}</p>
            <p className="note">{detail.data.annotation.note}</p>
            <div className="tags">
              {detail.data.tags.map((tag) => (
                <span key={tag.id} title={tag.description}>
                  {tag.full_name}
                </span>
              ))}
            </div>
            <h3>原文依据 · {detail.data.annotation.references.length} 处</h3>
            <p className="hint">分别打开每处引用，结合上下文阅读。</p>
            <ol className="references">
              {detail.data.annotation.references.map((reference, index) => (
                <Reference
                  key={`${reference.evidence_range.section_id}:${index}`}
                  workId={workId}
                  span={reference.reading_range}
                  index={index}
                  location={detail.data!.locations[index]}
                  active={JSON.stringify(active) === JSON.stringify(reference.reading_range)}
                  onOpen={() => onOpen(reference.reading_range)}
                  onEvidence={() => onOpen(reference.evidence_range)}
                  roleNote={reference.role_note}
                />
              ))}
            </ol>
          </>
        )}
      </div>
    </>
  );
}

function MarkList({
  workId,
  scope,
  onSelect,
  autoSelect = false,
}: {
  autoSelect?: boolean;
  workId: string;
  scope: Span | null | undefined;
  onSelect: (id: string) => void;
}) {
  const [paging, setPaging] = useState(firstPage);
  const marks = useResource<{ annotations: Page<Mark> }>(
    scope === null ? null : '/library/browse',
    {
      view: 'annotations',
      work_id: workId,
      status: 'active',
      limit: 12,
      ...(scope ? { source_range: { ...scope, work_id: workId } } : {}),
      ...pageBody(paging),
    },
  );
  // 只在本次查询返回时自动选择唯一候选，避免父组件重渲染重复打开详情。
  useEffect(() => {
    if (
      autoSelect &&
      marks.data?.annotations.items.length === 1 &&
      !marks.data.annotations.next_cursor
    )
      onSelect(marks.data.annotations.items[0].id);
  }, [marks.data, autoSelect]);
  return (
    <>
      <div className="panel-scroll" key={paging.page}>
        <Status resource={marks} />
        {scope === null && <Empty>打开原文后可查看本页标注。</Empty>}
        {marks.data?.annotations.items.length === 0 && (
          <Empty>
            {scope
              ? '本页原文没有有效标注，可继续阅读或切换为全书标注。'
              : '这部作品还没有有效标注。'}
          </Empty>
        )}
        {marks.data?.annotations.items.map((mark) => (
          <button className="mark" key={mark.id} onClick={() => onSelect(mark.id)}>
            <strong>{mark.title}</strong>
            <span className="hint">{mark.source_range_count} 处原文依据</span>
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
      {marks.data && (
        <Pager
          paging={paging}
          next={marks.data.annotations.next_cursor}
          onChange={setPaging}
          disabled={marks.loading}
        />
      )}
    </>
  );
}

export function Annotations({
  workId,
  pageScope,
  active,
  onOpen,
  initialSelectedId,
  paragraphPick,
  onDetail,
  onClear,
}: {
  initialSelectedId?: string;
  paragraphPick?: ParagraphPick | null;
  onDetail?: (detail: LocatedDetail) => void;
  onClear?: () => void;
  workId: string;
  pageScope: Span | null;
  active?: Span;
  onOpen: (span: Span) => void;
}) {
  const [all, setAll] = useState(false);
  const [selected, setSelected] = useState<{ id: string; scope: Span | null | undefined } | null>(
    initialSelectedId ? { id: initialSelectedId, scope: undefined } : null,
  );
  // 详情打开时保留原列表实例与页码；回到原文位置后可继续原来的列表。
  const scope = selected ? selected.scope : all ? undefined : paragraphPick?.span || pageScope;
  return (
    <aside className="annotations" aria-label="原文标注">
      <div className="panel-view" hidden={Boolean(selected)}>
        <div className="panel-head">
          <h2>
            {all
              ? '全书分析标注'
              : paragraphPick
                ? `第 ${paragraphPick.ordinal} 段的分析`
                : '本页分析标注'}
          </h2>
          <div className="segmented">
            <button
              aria-pressed={!all}
              onClick={() => {
                setAll(false);
                onClear?.();
              }}
            >
              {paragraphPick ? '本段标注' : '当前原文页'}
            </button>
            <button
              aria-pressed={all}
              onClick={() => {
                setAll(true);
                onClear?.();
              }}
            >
              全书标注
            </button>
          </div>
          <p className="hint">选择标注查看完整说明，再打开原文依据。</p>
        </div>
        <MarkList
          key={JSON.stringify(scope)}
          workId={workId}
          scope={scope}
          autoSelect={Boolean(paragraphPick && paragraphPick.count === 1 && !all)}
          onSelect={(id) => {
            onClear?.();
            setSelected({ id, scope });
          }}
        />
      </div>
      {selected && (
        <div className="panel-view">
          <MarkDetails
            id={selected.id}
            workId={workId}
            active={active}
            onBack={() => {
              setSelected(null);
              onClear?.();
            }}
            onOpen={onOpen}
            onDetail={onDetail}
          />
        </div>
      )}
    </aside>
  );
}
