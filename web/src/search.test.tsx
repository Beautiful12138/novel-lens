// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { ReferenceSearch } from './ReferenceSearch';
import { Reader } from './Reader';
import App from './App';
const wid = '11111111-1111-4111-8111-111111111111';
const sid = '22222222-2222-4222-8222-222222222222';
const book = { id: wid, name: '样本作品', part_count: 1, section_count: 1, character_count: 100 };
const span = { work_id: wid, section_id: sid, start_paragraph_id: 'p50', end_paragraph_id: 'p52' };
const hit = {
  work_name: book.name,
  part_name: '正文',
  section_title: '重逢',
  source_range: span,
  excerpt: '她在门前停下。',
  excerpt_truncated: true,
  annotation_ids: [],
};
const reply = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
function server(
  query: (body: any) => Promise<Response> = async (body) =>
    reply({
      search_id: 'snap',
      items: [hit],
      next_cursor: body.cursor ? null : 'page2',
      candidate_window_limited: true,
    }),
) {
  const calls: { path: string; body: any }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || '{}'));
      calls.push({ path, body });
      if (path === '/reference/query') return query(body);
      if (path === '/library/browse') {
        const items =
          body.view === 'works'
            ? [book]
            : body.view === 'parts'
              ? [{ id: 'part', name: '正文' }]
              : body.view === 'sections'
                ? [{ id: sid, part_id: 'part', part_name: '正文', title: '重逢' }]
                : [];
        return reply({ [body.view]: { items, next_cursor: null } });
      }
      if (path === `/works/${wid}`) return reply(book);
      if (path.includes('/sections/'))
        return reply({
          id: sid,
          part_id: 'part',
          part_name: '正文',
          title: '重逢',
          paragraph_count: 150,
        });
      if (path === '/assets/annotations/coverage') return reply({ items: [] });
      if (path === '/source/read') {
        const edge = Number(String(body.start_paragraph_id || 'p50').slice(1));
        const start = body.before ? Math.max(1, edge - body.before) : body.after ? edge : 50;
        const end = body.before ? edge : body.after ? Math.min(150, edge + body.after) : 52;
        return reply({
          work_id: wid,
          section_id: sid,
          actual_range: { start_paragraph_id: `p${start}`, end_paragraph_id: `p${end}` },
          items: [
            { id: `p${start}`, ordinal: start, text: `正文起点${start}` },
            { id: `p${end}`, ordinal: end, text: `正文末点${end}` },
          ],
          next_cursor: null,
        });
      }
      throw Error(path);
    }),
  );
  return calls;
}
async function submit() {
  await screen.findByRole('checkbox', { name: '样本作品' });
  fireEvent.click(screen.getByRole('checkbox', { name: '样本作品' }));
  fireEvent.change(screen.getByRole('textbox', { name: '想找什么原文？' }), {
    target: { value: '重逢' },
  });
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
}
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  history.replaceState(null, '', '/');
});
it('范围选择与快照续页正确，回到首结果页不重建搜索', async () => {
  const calls = server();
  render(<ReferenceSearch onOpen={() => {}} />);
  await submit();
  await screen.findByText('她在门前停下。');
  const first = calls.find((c) => c.path === '/reference/query')!.body;
  expect(first.scope).toEqual([{ work_id: wid }]);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText('第 2 页');
  await waitFor(() =>
    expect(screen.getByRole('button', { name: '上一页' }).hasAttribute('disabled')).toBe(false),
  );
  fireEvent.click(screen.getByRole('button', { name: '上一页' }));
  await screen.findByText('第 1 页');
  expect(calls.filter((c) => c.path === '/reference/query').map((c) => c.body)).toEqual([
    first,
    { search_id: 'snap', format: 'compact', cursor: 'page2' },
    { search_id: 'snap', format: 'compact' },
  ]);
});
it('限定章节优先于分部，改分部清除旧章节；空范围不会请求', async () => {
  const calls = server();
  render(<ReferenceSearch onOpen={() => {}} />);
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
  await screen.findByRole('alert');
  expect(calls.some((c) => c.path === '/reference/query')).toBe(false);
  await screen.findByRole('checkbox', { name: '样本作品' });
  fireEvent.click(screen.getByRole('checkbox', { name: '样本作品' }));
  fireEvent.click(screen.getByText('全部原文 · 限定范围'));
  await screen.findByRole('option', { name: '正文 · 重逢' });
  fireEvent.change(screen.getByRole('combobox', { name: '样本作品 · 章节' }), {
    target: { value: sid },
  });
  fireEvent.change(screen.getByRole('textbox', { name: '想找什么原文？' }), {
    target: { value: '重逢' },
  });
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
  await screen.findByText('她在门前停下。');
  expect(calls.filter((c) => c.path === '/reference/query').at(-1)!.body.scope).toEqual([
    { work_id: wid, section_ids: [sid] },
  ]);
  fireEvent.change(screen.getByRole('combobox', { name: '样本作品 · 分部' }), {
    target: { value: 'part' },
  });
  expect(
    (screen.getByRole('combobox', { name: '样本作品 · 章节' }) as HTMLSelectElement).value,
  ).toBe('');
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
  await screen.findByText('她在门前停下。');
  expect(calls.filter((c) => c.path === '/reference/query').at(-1)!.body.scope).toEqual([
    { work_id: wid, part_ids: ['part'] },
  ]);
});
it('快照过期按原提交条件重查，不使用未提交草稿', async () => {
  let expired = true;
  const calls = server(async (body) =>
    body.search_id && expired
      ? reply({ code: 'REFERENCE_SEARCH_UNAVAILABLE', message: '过期' }, 410)
      : reply({ search_id: 'snap', items: [hit], next_cursor: 'page2' }),
  );
  render(<ReferenceSearch onOpen={() => {}} />);
  await submit();
  await screen.findByText('她在门前停下。');
  fireEvent.change(screen.getByRole('textbox', { name: '想找什么原文？' }), {
    target: { value: '未提交的草稿' },
  });
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText(/搜索已过期/);
  expired = false;
  fireEvent.click(screen.getByRole('button', { name: '按原条件重新检索' }));
  await screen.findByText('她在门前停下。');
  expect(calls.filter((c) => c.path === '/reference/query').at(-1)!.body.query).toBe('重逢');
  expect(
    (screen.getByRole('textbox', { name: '想找什么原文？' }) as HTMLTextAreaElement).value,
  ).toBe('未提交的草稿');
});
it('新提交不受旧查询迟到响应影响，未就绪与模型失败可重试', async () => {
  let resolve!: (value: Response) => void;
  let count = 0;
  server(async () => {
    count++;
    if (count === 1)
      return new Promise((r) => {
        resolve = r;
      });
    if (count === 2) return reply({ code: 'REFERENCE_NOT_READY', message: '未准备' }, 409);
    if (count === 3) return reply({ code: 'EMBEDDING_UNAVAILABLE', message: '模型不可用' }, 503);
    return reply({ search_id: 'new', items: [], next_cursor: null });
  });
  render(<ReferenceSearch onOpen={() => {}} />);
  await submit();
  await waitFor(() => expect(count).toBe(1));
  fireEvent.change(screen.getByRole('textbox', { name: '想找什么原文？' }), {
    target: { value: '雨夜' },
  });
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
  await screen.findByText(/原文索引尚未就绪/);
  resolve(reply({ search_id: 'old', items: [hit], next_cursor: null }));
  fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
  await screen.findByText('模型不可用');
  fireEvent.click(screen.getByRole('button', { name: '重试检索' }));
  await screen.findByText(/当前条件下没有匹配原文/);
  expect(screen.queryByText('她在门前停下。')).toBeNull();
});
it('检索进入阅读并补读，返回保留查询、页码和所选候选', async () => {
  const calls = server();
  history.replaceState(null, '', '/#view=search');
  render(<App />);
  await submit();
  await screen.findByText('她在门前停下。');
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByText('第 2 页');
  await screen.findByRole('button', { name: '阅读候选 1 的原文与上下文' });
  fireEvent.click(screen.getByRole('button', { name: '阅读候选 1 的原文与上下文' }));
  await screen.findByText('正文起点50');
  fireEvent.click(screen.getByRole('button', { name: '向后补读' }));
  await screen.findByText('正文起点52');
  expect(calls.filter((c) => c.path === '/source/read').at(-1)!.body).toMatchObject({
    start_paragraph_id: 'p52',
    end_paragraph_id: 'p52',
    after: 39,
  });
  fireEvent.click(screen.getByRole('button', { name: '返回检索结果' }));
  await screen.findByRole('textbox', { name: '想找什么原文？' });
  const results = screen.getByRole('region', { name: '原文检索结果' });
  expect(within(results).getByText('第 2 页')).toBeTruthy();
  expect(
    screen.getByRole('button', { name: '阅读候选 1 的原文与上下文' }).getAttribute('aria-pressed'),
  ).toBe('true');
  expect(calls.filter((c) => c.path === '/reference/query')).toHaveLength(2);
});
it('向前补读沿实际页界继续，章首禁止再向前', async () => {
  const calls = server();
  render(
    <Reader workId={wid} initialSection={sid} initialRange={span} active onReturn={() => {}} />,
  );
  await screen.findByText('正文起点50');
  fireEvent.click(screen.getByRole('button', { name: '向前补读' }));
  await screen.findByText('正文起点11');
  expect(calls.filter((c) => c.path === '/source/read').at(-1)!.body).toMatchObject({
    start_paragraph_id: 'p50',
    end_paragraph_id: 'p50',
    before: 39,
  });
  fireEvent.click(screen.getByRole('button', { name: '向前补读' }));
  await screen.findByText('正文起点1');
  expect(screen.getByRole('button', { name: '向前补读' }).hasAttribute('disabled')).toBe(true);
});

it('作品翻页不会提交表单，跨页选择多作品保持范围', async () => {
  const calls = server();
  const original = fetch;
  const second = { ...book, id: '33333333-3333-4333-8333-333333333333', name: '另一本' };
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, init?: RequestInit) => {
      const body = JSON.parse(String(init?.body || '{}'));
      if (path === '/library/browse' && body.view === 'works')
        return reply({
          works: {
            items: body.cursor ? [second] : [book],
            next_cursor: body.cursor ? null : 'works2',
          },
        });
      return original(path, init);
    }),
  );
  render(<ReferenceSearch onOpen={() => {}} />);
  await screen.findByRole('checkbox', { name: '样本作品' });
  fireEvent.click(screen.getByRole('checkbox', { name: '样本作品' }));
  fireEvent.change(screen.getByRole('textbox', { name: '想找什么原文？' }), {
    target: { value: '雨夜' },
  });
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  await screen.findByRole('checkbox', { name: '另一本' });
  expect(calls.some((c) => c.path === '/reference/query')).toBe(false);
  fireEvent.click(screen.getByRole('checkbox', { name: '另一本' }));
  fireEvent.click(screen.getByRole('button', { name: '检索原文' }));
  await screen.findByText('她在门前停下。');
  expect(calls.filter((c) => c.path === '/reference/query').at(-1)!.body.scope).toEqual([
    { work_id: wid },
    { work_id: second.id },
  ]);
});
