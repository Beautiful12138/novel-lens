// @vitest-environment jsdom
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { AuthGate } from './Auth';
import { useResource } from './api';

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

function ProtectedData() {
  const resource = useResource<{ name: string }>('/library/browse', { view: 'works' });
  return <p>{resource.data?.name || '正在读取作品'}</p>;
}

it('登录前不读取业务；错误可重试，成功后读取作品，退出隐藏数据', async () => {
  let logged = false;
  const calls: string[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (path: string, options?: RequestInit) => {
      calls.push(path);
      if (path === '/auth/login') {
        logged = JSON.parse(String(options?.body)).access_key === 'test-only-key';
        return new Response(
          JSON.stringify(
            logged
              ? { enabled: true, authenticated: true }
              : {
                  code: 'UNAUTHORIZED',
                  message: '访问密钥不正确，请重试',
                },
          ),
          { status: logged ? 200 : 401 },
        );
      }
      if (path === '/auth/logout') logged = false;
      return new Response(
        JSON.stringify(
          path === '/library/browse'
            ? { name: '真实作品列表' }
            : {
                enabled: true,
                authenticated: logged,
              },
        ),
      );
    }),
  );
  render(
    <AuthGate>
      <ProtectedData />
    </AuthGate>,
  );
  const field = await screen.findByLabelText('访问密钥');
  expect(calls).toEqual(['/auth/status']);
  fireEvent.change(field, { target: { value: 'wrong' } });
  fireEvent.click(screen.getByRole('button', { name: '进入作品库' }));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent', '访问密钥不正确，请重试');
  fireEvent.change(field, { target: { value: 'test-only-key' } });
  fireEvent.click(screen.getByRole('button', { name: '进入作品库' }));
  await screen.findByText('真实作品列表');
  fireEvent.click(screen.getByRole('button', { name: '退出登录' }));
  await screen.findByLabelText('访问密钥');
  expect(screen.queryByText('真实作品列表')).toBeNull();
});

it('会话失效隐藏已读数据并允许重新登录；本机匿名模式不出现退出入口', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      async (path: string) =>
        new Response(
          JSON.stringify(
            path === '/auth/status'
              ? { enabled: false, authenticated: true }
              : { name: '已读作品' },
          ),
        ),
    ),
  );
  render(
    <AuthGate>
      <ProtectedData />
    </AuthGate>,
  );
  await screen.findByText('已读作品');
  expect(screen.queryByRole('button', { name: '退出登录' })).toBeNull();
  act(() => window.dispatchEvent(new Event('novel-lens:unauthorized')));
  await screen.findByLabelText('访问密钥');
  expect(screen.queryByText('已读作品')).toBeNull();
  expect(screen.getByRole('alert').textContent).toContain('登录已失效');
});

it('连接失败可重试且不会进入业务页面', async () => {
  const fetcher = vi
    .fn()
    .mockRejectedValueOnce(new TypeError('network failed'))
    .mockResolvedValue(new Response(JSON.stringify({ enabled: true, authenticated: false })));
  vi.stubGlobal('fetch', fetcher);
  render(
    <AuthGate>
      <p>业务内容</p>
    </AuthGate>,
  );
  fireEvent.click(await screen.findByRole('button', { name: '重新连接' }));
  await screen.findByLabelText('访问密钥');
  await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
  expect(screen.queryByText('业务内容')).toBeNull();
});
