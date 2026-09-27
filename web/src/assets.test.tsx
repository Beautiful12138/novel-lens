// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AnnotationLibrary } from './AssetLibrary';
import App from './App';
import { Reader } from './Reader';

const work = '11111111-1111-4111-8111-111111111111';
const section = '22222222-2222-4222-8222-222222222222';
const span = { section_id: section, start_paragraph_id: 'p9', end_paragraph_id: 'p12' };
const book = { id: work, name: '测试作品', part_count: 1, section_count: 1, character_count: 20 };
const mark = {
  id: 'mark1',
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
  annotation: { ...mark, note: '未截断的完整说明', source_ranges: [span] },
  tags: [],
  locations: [
    { ...span, part_name: '第一卷', section_title: '同名章节', start_ordinal: 9, end_ordinal: 12 },
  ],
};

function installServer(singleMarker = false) {
  const calls: { path: string; body: Record<string, unknown> }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options?: RequestInit) => {
      const body = options?.body ? JSON.parse(String(options.body)) : {};
      calls.push({ path, body });
      let data: unknown;
      if (path === '/assets/annotations/browse')
        data = { items: body.query === '不存在' ? [] : [mark], next_cursor: null };
      else if (path === '/assets/annotations/detail') data = detail;
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
