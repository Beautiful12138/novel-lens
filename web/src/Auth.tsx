import { useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { ApiError, request } from './api';

type AuthenticationStatus = { enabled: boolean; authenticated: boolean };

/** 确认登录前不挂载业务页面，密钥仅随登录请求发送，不写浏览器存储。 */
export function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthenticationStatus | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [key, setKey] = useState('');

  useEffect(() => {
    if (!status?.authenticated) document.title = 'NovelLens · 登录';
  }, [status]);

  useEffect(() => {
    const abort = new AbortController();
    let active = true;
    const timeout = window.setTimeout(() => abort.abort(), 15000);
    setLoading(true);
    request<AuthenticationStatus>('/auth/status', undefined, abort.signal)
      .then((value) => {
        if (!active) return;
        setStatus(value);
        setError('');
      })
      .catch(() => {
        if (active) setError('暂时无法连接服务，请检查连接后重试。');
      })
      .finally(() => {
        clearTimeout(timeout);
        if (active) setLoading(false);
      });
    function expired() {
      setStatus({ enabled: true, authenticated: false });
      setKey('');
      setError('登录已失效，请重新输入访问密钥。');
    }
    window.addEventListener('novel-lens:unauthorized', expired);
    return () => {
      active = false;
      clearTimeout(timeout);
      abort.abort();
      window.removeEventListener('novel-lens:unauthorized', expired);
    };
  }, [attempt]);

  async function login(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError('');
    const abort = new AbortController();
    const timeout = window.setTimeout(() => abort.abort(), 15000);
    try {
      setStatus(
        await request<AuthenticationStatus>('/auth/login', { access_key: key }, abort.signal),
      );
      setKey('');
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : '暂时无法登录，请检查连接后重试。');
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }

  async function logout() {
    setLoading(true);
    setError('');
    const abort = new AbortController();
    const timeout = window.setTimeout(() => abort.abort(), 15000);
    try {
      setStatus(await request<AuthenticationStatus>('/auth/logout', {}, abort.signal));
    } catch {
      setError('退出未完成，请检查连接后重试。');
    } finally {
      clearTimeout(timeout);
      setLoading(false);
    }
  }

  if (status?.authenticated) {
    return (
      <>
        {children}
        {status.enabled && (
          <div className="auth-session">
            {error && <span role="alert">{error}</span>}
            <button onClick={logout} disabled={loading}>
              {loading ? '正在退出…' : '退出登录'}
            </button>
          </div>
        )}
      </>
    );
  }
  return (
    <main className="login-page">
      <section className="login-form" aria-labelledby="login-title">
        <h1 id="login-title">NovelLens</h1>
        <p className="login-description">登录作品库，阅读原文与分析。</p>
        {status === null ? (
          <div aria-live="polite">
            <p>{loading ? '正在连接服务…' : error}</p>
            {!loading && <button onClick={() => setAttempt((value) => value + 1)}>重新连接</button>}
          </div>
        ) : (
          <form onSubmit={login}>
            <label htmlFor="access-key">访问密钥</label>
            <input
              id="access-key"
              name="access-key"
              type="password"
              autoComplete="current-password"
              maxLength={512}
              required
              value={key}
              onChange={(event) => setKey(event.target.value)}
              aria-invalid={Boolean(error)}
              aria-describedby={error ? 'login-error' : undefined}
            />
            {error && (
              <p id="login-error" className="login-error" role="alert">
                {error}
              </p>
            )}
            <button className="primary" type="submit" disabled={loading || !key.trim()}>
              {loading ? '正在登录…' : '进入作品库'}
            </button>
          </form>
        )}
      </section>
    </main>
  );
}
