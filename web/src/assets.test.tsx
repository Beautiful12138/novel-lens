// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AnnotationLibrary, TagLibrary } from './AssetLibrary';
import App from './App';
import { Reader } from './Reader';

const work = '11111111-1111-4111-8111-111111111111';
const section = '22222222-2222-4222-8222-222222222222';
const span = { section_id: section, start_paragraph_id: 'p9', end_paragraph_id: 'p12' };
const book = { id: work, name: '测试作品', part_count: 1, section_count: 1, character_count: 20 };
const mark = {
  id: 'mark1',
  title: '日常中的变化',
  scope_note: '只在所读章节成立',
  kind: 'comparison',
  work_id: work,
  work_name: book.name,
  part_name: '第一卷',
  section_title: '同名章节',
  note_preview: '可查看的分析',
  note_truncated: false,
  tags: [],
  source_range_count: 1,
  status: 'active',
};
const detail = {
  annotation: {
    ...mark,
    note: '未截断的完整说明',
    references: [{ evidence_range: span, reading_range: span, role_note: '对照日常叙述' }],
  },
  tags: [],
  locations: [
    {
      ...span,
      part_name: '第一卷',
      section_title: '同名章节',
      start_ordinal: 9,
      end_ordinal: 12,
      reading_start_ordinal: 9,
      reading_end_ordinal: 12,
    },
  ],
};

function installServer(singleMarker = false, wideReading = false) {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      calls.push({ path, body });
      let data: unknown;
      if (path === '/assets/annotations/browse')
        data = { items: body.query === '不存在' ? [] : [mark], next_cursor: null };
      else if (path === '/assets/annotations/detail')
        data = wideReading
          ? {
              ...detail,
              annotation: {
                ...detail.annotation,
                references: [
                  {
                    evidence_range: span,
                    reading_range: { ...span, start_paragraph_id: 'p5', end_paragraph_id: 'p15' },
                    role_note: '完整互动的铺垫和收束',
                  },
                ],
              },
              locations: [
                { ...detail.locations[0], reading_start_ordinal: 5, reading_end_ordinal: 15 },
              ],
            }
          : detail;
      else if (path === '/assets/annotations/coverage')
        data = {
          work_id: work,
          section_id: section,
          items: [{ paragraph_id: 'p9', ordinal: 9, annotation_count: singleMarker ? 1 : 2 }],
        };
      else if (path === '/library/browse' && body.view === 'works')
        data = { works: { items: [book], next_cursor: null } };
      else if (path === '/library/browse' && body.view === 'tags')
        data = { tag_page: { items: [], next_cursor: null } };
      else if (path === '/library/browse' && body.view === 'parts')
        data = { parts: { items: [], next_cursor: null } };
      else if (path === '/library/browse' && body.view === 'annotations')
        data = { annotations: { items: [mark], next_cursor: null } };
      else if (path === `/works/${work}`) data = book;
      else if (path === `/works/${work}/sections/${section}`)
        data = { id: section, part_id: 'part1', part_name: '第一卷', title: '同名章节' };
      else if (path === '/source/read')
        data = {
          work_id: work,
          section_id: section,
          actual_range: span,
          items: [{ id: 'p9', ordinal: 9, text: '对应原文。' }],
          next_cursor: null,
        };
      else throw new Error(`测试中未声明请求 ${path}`);
      return new Response(JSON.stringify(data));
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
});

it('分类和多作品限定标签，多选默认任一命中，可切换全部并打开真实引用', async () => {
  const calls: Record<string, unknown>[] = [];
  const onOpen = vi.fn();
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      calls.push({ path, ...body });
      let data: unknown;
      if (path === '/library/browse' && body.view === 'works')
        data = {
          works: { items: [book, { ...book, id: 'work2', name: '另一部作品' }], next_cursor: null },
        };
      else if (body.view === 'categories')
        data = {
          categories: [
            { id: 'interaction', name: '人物与互动', tag_count: 2, annotation_count: 1 },
          ],
        };
      else if (body.view === 'tags')
        data = {
          tag_page: {
            items: ['对白', '停顿'].map((name, i) => ({
              id: `tag${i}`,
              namespace: '写法',
              name,
              full_name: `写法/${name}`,
              description: `${name}的定义`,
              aliases: [],
              categories: ['interaction'],
              annotation_count: 1,
            })),
            next_cursor: null,
          },
        };
      else if (path === '/assets/annotations/browse') data = { items: [mark], next_cursor: null };
      else if (path === '/assets/annotations/detail') data = detail;
      else throw new Error(`未声明的请求 ${path}`);
      return new Response(JSON.stringify(data));
    }),
  );
  render(<TagLibrary onOpen={onOpen} />);
  fireEvent.click(screen.getByText('作品范围：全部可见作品'));
  fireEvent.click(await screen.findByRole('checkbox', { name: '测试作品' }));
  fireEvent.click(screen.getByRole('checkbox', { name: '另一部作品' }));
  fireEvent.click(screen.getByRole('button', { name: /人物与互动/ }));
  await waitFor(() =>
    expect(calls.filter((c) => c.view === 'tags').at(-1)).toMatchObject({
      category: 'interaction',
      work_ids: [work, 'work2'],
    }),
  );
  fireEvent.click(await screen.findByRole('button', { name: /写法\/对白/ }));
  fireEvent.click(screen.getByRole('button', { name: /写法\/停顿/ }));
  await screen.findByText('可查看的分析');
  await waitFor(() =>
    expect(calls.filter((c) => c.path === '/assets/annotations/browse').at(-1)).toMatchObject({
      work_ids: [work, 'work2'],
      tag_ids: ['tag0', 'tag1'],
      tag_match: 'any',
      status: 'active',
    }),
  );
  fireEvent.change(screen.getByRole('combobox', { name: '标签匹配方式' }), {
    target: { value: 'all' },
  });
  await waitFor(() =>
    expect(calls.filter((c) => c.path === '/assets/annotations/browse').at(-1)?.tag_match).toBe(
      'all',
    ),
  );
  fireEvent.click((await screen.findByText('可查看的分析')).closest('button')!);
  fireEvent.click(await screen.findByRole('button', { name: /引用 1.*第 9–12 段/ }));
  expect(onOpen).toHaveBeenCalledWith({ workId: work, range: span, annotationId: 'mark1' });
  fireEvent.click(screen.getByRole('button', { name: '恢复全库范围' }));
  await screen.findByText(/选择一个或多个标签/);
  await waitFor(() =>
    expect(calls.filter((c) => c.view === 'tags').at(-1)?.work_ids).toBeUndefined(),
  );
});

it('说明与状态筛选提交到服务端，空结果明确显示', async () => {
  const calls = installServer();
  render(<AnnotationLibrary onOpen={() => {}} />);
  await screen.findByText('可查看的分析');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索标注说明' }), {
    target: { value: '不存在' },
  });
  fireEvent.change(screen.getByRole('combobox', { name: '标注状态' }), {
    target: { value: 'withdrawn' },
  });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  await screen.findByText(/当前条件下没有标注/);
  const last = calls.filter((call) => call.path === '/assets/annotations/browse').at(-1)!;
  expect(last.body).toMatchObject({ query: '不存在', status: 'withdrawn' });
  expect(last.body.cursor).toBeUndefined();
});

it('资产引用进入阅读后仍显示关联标注，返回保留查询和选择', async () => {
  installServer();
  history.replaceState(null, '', '/#view=annotations');
  render(<App />);
  await screen.findByText('可查看的分析');
  fireEvent.change(screen.getByRole('textbox', { name: '搜索标注说明' }), {
    target: { value: '保留条件' },
  });
  fireEvent.click(screen.getByRole('button', { name: '应用筛选' }));
  const item = await screen.findByText('可查看的分析');
  fireEvent.click(item.closest('button')!);
  const reference = await screen.findByRole('button', { name: /引用 1.*第 9–12 段/ });
  fireEvent.click(reference);
  await screen.findByRole('button', { name: '返回标注库' });
  await screen.findByText('对应原文。');
  expect(
    within(screen.getByRole('complementary', { name: '原文标注' })).getByText('未截断的完整说明'),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '返回标注库' }));
  await waitFor(() =>
    expect(screen.getByRole('textbox', { name: '搜索标注说明' }).getAttribute('value')).toBe(
      '保留条件',
    ),
  );
  expect(
    screen.getByRole('button', { name: /测试作品.*可查看的分析/ }).getAttribute('aria-pressed'),
  ).toBe('true');
});

it('正常阅读默认收起分析，点击单条标记打开详情且不重新定位正文', async () => {
  const calls = installServer(true);
  render(<Reader workId={work} initialSection={section} active onReturn={() => {}} />);
  await screen.findByText('对应原文。');
  expect(screen.queryByRole('complementary', { name: '原文标注' })).toBeNull();
  const marker = await screen.findByRole('button', { name: '第 9 段：1 条分析' });
  const before = calls.filter((call) => call.path === '/source/read').length;
  fireEvent.click(marker);
  await screen.findByText('未截断的完整说明');
  expect(calls.filter((call) => call.path === '/source/read')).toHaveLength(before);
  fireEvent.click(screen.getByRole('button', { name: /引用 1.*第 9–12 段/ }));
  await waitFor(() =>
    expect(
      calls.filter((call) => call.path === '/source/read').at(-1)?.body.start_paragraph_id,
    ).toBe('p9'),
  );
  fireEvent.click(screen.getByRole('button', { name: '收起标注' }));
  expect(screen.queryByRole('complementary', { name: '原文标注' })).toBeNull();
  await waitFor(() =>
    expect(
      calls.filter((call) => call.path === '/source/read').at(-1)?.body.start_paragraph_id,
    ).toBeUndefined(),
  );
  await screen.findByText('对应原文。');
  fireEvent.click(screen.getByRole('button', { name: '收起目录' }));
  expect(screen.queryByRole('complementary', { name: '作品目录' })).toBeNull();
});

it('作品认识独立限定类型，默认连续阅读并可定位精确证据', async () => {
  const calls = installServer(false, true);
  const onOpen = vi.fn();
  render(<AnnotationLibrary understanding workId={work} onOpen={onOpen} />);
  await screen.findByText('可查看的分析');
  expect(calls.find((c) => c.path === '/assets/annotations/browse')?.body).toMatchObject({
    kind: 'comparison',
    work_id: work,
  });
  fireEvent.click(screen.getByText('可查看的分析').closest('button')!);
  fireEvent.click(await screen.findByRole('button', { name: /引用 1.*第 5–15 段.*连续阅读/ }));
  expect(onOpen).toHaveBeenLastCalledWith({
    workId: work,
    annotationId: 'mark1',
    range: { ...span, start_paragraph_id: 'p5', end_paragraph_id: 'p15' },
  });
  expect(screen.getByText('完整互动的铺垫和收束')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: /定位证据 · 第 9–12 段/ }));
  expect(onOpen).toHaveBeenLastCalledWith({ workId: work, annotationId: 'mark1', range: span });
});

it('作品认识深链打开对应作品，回读再返回保留范围', async () => {
  const calls = installServer();
  history.replaceState(null, '', `/#view=understanding&work=${work}`);
  render(<App />);
  await screen.findByRole('heading', { level: 1, name: '作品认识' });
  fireEvent.click((await screen.findByText('可查看的分析')).closest('button')!);
  fireEvent.click(await screen.findByRole('button', { name: /引用 1.*连续阅读/ }));
  fireEvent.click(await screen.findByRole('button', { name: '返回作品认识' }));
  await screen.findByRole('heading', { level: 1, name: '作品认识' });
  expect(location.hash).toBe(`#view=understanding&work=${work}`);
  expect(calls.filter((c) => c.path === '/assets/annotations/browse').at(-1)?.body).toMatchObject({
    kind: 'comparison',
    work_id: work,
  });
});
