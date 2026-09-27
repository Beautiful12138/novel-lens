// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { advance, firstPage, pageBody, parseLocation, useResource } from './api';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('切换作品后丢弃晚到响应，并在加载新范围时隐藏旧内容', async () => {
  const replies: ((response: Response) => void)[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(() => new Promise<Response>((resolve) => replies.push(resolve))),
  );
  const { result, rerender } = renderHook(
    ({ work }) => useResource<{ name: string }>('/library/browse', { work }),
    { initialProps: { work: '甲' } },
  );
  rerender({ work: '乙' });
  expect(result.current.data).toBeUndefined();
  await act(async () => replies[1](new Response(JSON.stringify({ name: '乙' }))));
  await waitFor(() => expect(result.current.data?.name).toBe('乙'));
  await act(async () => replies[0](new Response(JSON.stringify({ name: '甲' }))));
  expect(result.current.data?.name).toBe('乙');
});

it('失败时呈现真实错误，重试同一范围可恢复', async () => {
  const fetcher = vi
    .fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ message: '作品已屏蔽' }), { status: 409 }))
    .mockResolvedValueOnce(new Response(JSON.stringify({ items: [] })));
  vi.stubGlobal('fetch', fetcher);
  const { result } = renderHook(() =>
    useResource<{ items: unknown[] }>('/source/read', { work_id: '甲' }),
  );
  await waitFor(() => expect(result.current.error).toBe('作品已屏蔽'));
  act(() => result.current.retry());
  await waitFor(() => expect(result.current.data?.items).toEqual([]));
  expect(fetcher.mock.calls[0][1].body).toBe(fetcher.mock.calls[1][1].body);
});

it('返回前页再向后时采用服务的新游标，不复用旧分支', () => {
  const second = advance(firstPage(), 'second');
  const third = advance(second, 'stale-third');
  const replaced = advance({ ...third, page: 0 }, 'new-second');
  expect(replaced.cursors).toEqual([null, 'new-second']);
  expect(pageBody(replaced)).toEqual({ cursor: 'new-second' });
});

it('无效书签不能成为请求路径或跨作品章节入口', () => {
  expect(parseLocation('#work=../../.env&section=invalid')).toEqual({ work: null, section: null });
  const id = 'be644f80-c14b-46c3-af99-e5ef75661da7';
  expect(parseLocation(`#work=${id}&section=${id}`)).toEqual({ work: id, section: id });
  expect(parseLocation(`#section=${id}`)).toEqual({ work: null, section: null });
});
