import { useEffect, useState } from 'react';
import { firstPage, pageBody, useResource } from './api';
import type { Page, Part, Section } from './api';
import { Empty, Pager, Status } from './components';

function PartChapters({
  part,
  workId,
  selected,
  onSelect,
}: {
  part: Part;
  workId: string;
  selected?: Section;
  onSelect: (section: string) => void;
}) {
  const [open, setOpen] = useState(selected?.part_id === part.id);
  const [paging, setPaging] = useState(firstPage);
  const chapters = useResource<{ sections: Page<Section> }>(open ? '/library/browse' : null, {
    view: 'sections',
    work_id: workId,
    part_id: part.id,
    limit: 30,
    format: 'full',
    ...pageBody(paging),
  });
  useEffect(() => {
    if (selected?.part_id === part.id) setOpen(true);
  }, [selected?.part_id, part.id]);
  return (
    <section className="part">
      <button
        className="part-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        {part.name}
        <span>{open ? '收起' : '展开'}</span>
      </button>
      {open && (
        <div className="chapters">
          <Status resource={chapters} />
          {chapters.data?.sections.items.map((section) => (
            <button
              key={section.id}
              aria-current={selected?.id === section.id ? 'location' : undefined}
              onClick={() => onSelect(section.id)}
            >
              {section.title}
            </button>
          ))}
          {chapters.data?.sections.items.length === 0 && <Empty>此分部暂无章节。</Empty>}
          {(paging.page > 0 || chapters.data?.sections.next_cursor) && (
            <Pager
              paging={paging}
              next={chapters.data?.sections.next_cursor}
              onChange={setPaging}
              disabled={chapters.loading}
            />
          )}
        </div>
      )}
    </section>
  );
}

export function Directory({
  workId,
  selected,
  onSelect,
}: {
  workId: string;
  selected?: Section;
  onSelect: (section: string) => void;
}) {
  const [paging, setPaging] = useState(firstPage);
  const parts = useResource<{ parts: Page<Part> }>('/library/browse', {
    view: 'parts',
    work_id: workId,
    limit: 20,
    format: 'full',
    ...pageBody(paging),
  });
  return (
    <aside className="directory" aria-label="作品目录">
      <h2>目录</h2>
      {selected && (
        <p className="current-chapter">
          正在阅读
          <br />
          {selected.part_name} · {selected.title}
        </p>
      )}
      <Status resource={parts} />
      {parts.data?.parts.items.map((part) => (
        <PartChapters
          key={part.id}
          part={part}
          workId={workId}
          selected={selected}
          onSelect={onSelect}
        />
      ))}
      {parts.data?.parts.items.length === 0 && <Empty>作品暂无分部。</Empty>}
      {(paging.page > 0 || parts.data?.parts.next_cursor) && (
        <Pager
          paging={paging}
          next={parts.data?.parts.next_cursor}
          onChange={setPaging}
          disabled={parts.loading}
        />
      )}
    </aside>
  );
}
