import { useEffect, useState } from 'react';

export type Page<T> = { items: T[]; next_cursor: string | null };
export type Work = {
  id: string;
  name: string;
  part_count: number;
  section_count: number;
  paragraph_count: number;
  character_count: number;
};
export type Part = { id: string; name: string; ordinal: number; section_count: number };
export type Section = {
  id: string;
  part_id: string;
  part_name: string;
  title: string;
  paragraph_count: number;
};
export type Span = { section_id: string; start_paragraph_id: string; end_paragraph_id: string };
export type Tag = { id: string; full_name: string; description?: string };
export type Mark = {
  id: string;
  note_preview: string | null;
  note_truncated: boolean;
  source_range_count: number;
  first_source_range: Span;
  tags: Tag[];
};
export type Annotation = {
  status: 'active' | 'withdrawn';
  id: string;
  work_id: string;
  note: string | null;
  source_ranges: Span[];
};
export type Detail = { annotation: Annotation; tags: Tag[] };
export type ReferenceLocation = Span & {
  part_name: string;
  section_title: string;
  start_ordinal: number;
  end_ordinal: number;
};
export type LocatedDetail = Detail & { locations: ReferenceLocation[] };
export type ParagraphPick = { span: Span; ordinal: number; count: number };
export type Coverage = {
  work_id: string;
  section_id: string;
  items: { paragraph_id: string; ordinal: number; annotation_count: number }[];
};
export type Source = Page<{ id: string; ordinal: number; text: string }> & {
  work_id: string;
  section_id: string;
  actual_range: Omit<Span, 'section_id'> | null;
};
export type Resource<T> = { data?: T; loading: boolean; error?: string; retry: () => void };

/** 只返回可展示的业务错误，不把请求、响应原文或连接信息写入控制台。 */
export async function request<T>(
  path: string,
  body: object | undefined,
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: body ? 'POST' : 'GET',
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
    signal,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.message || `服务暂时无法读取数据（${response.status}）`);
  }
  return response.json();
}

/** 每个资源独立取消；请求身份变化立即隐藏旧数据，迟到响应不得覆盖新范围。 */
export function useResource<T>(path: string | null, body?: object): Resource<T> {
  const key = JSON.stringify([path, body]);
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; data?: T; error?: string; loading: boolean }>(
    { key: '', loading: true },
  );
  useEffect(() => {
    if (!path) return;
    const abort = new AbortController();
    let current = true;
    let timedOut = false;
    const timeout = setTimeout(() => {
      timedOut = true;
      abort.abort();
    }, 20000);
    setResult({ key, loading: true });
    request<T>(path, body, abort.signal)
      .then((data) => {
        if (current) setResult({ key, data, loading: false });
      })
      .catch((error) => {
        if (current)
          setResult({
            key,
            loading: false,
            error: timedOut
              ? '读取超时，请重试。'
              : error instanceof Error
                ? error.message
                : '无法连接服务，请重试。',
          });
      })
      .finally(() => clearTimeout(timeout));
    return () => {
      current = false;
      clearTimeout(timeout);
      abort.abort();
    };
    // 身份由完整 URL 与序列化参数决定，避免渲染时创建对象触发重复请求。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);
  const value = result.key === key ? result : { loading: Boolean(path) };
  return { ...value, retry: () => setAttempt((value) => value + 1) };
}

export type Paging = { cursors: (string | null)[]; page: number };
export const firstPage = (): Paging => ({ cursors: [null], page: 0 });
export function pageBody(paging: Paging) {
  const cursor = paging.cursors[paging.page];
  return cursor ? { cursor } : {};
}
/** 游标链只允许跟随服务返回的下一页，筛选变化由父组件重新创建分页状态。 */
export function advance(paging: Paging, cursor: string): Paging {
  return { cursors: [...paging.cursors.slice(0, paging.page + 1), cursor], page: paging.page + 1 };
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function parseLocation(hash: string): { work: string | null; section: string | null } {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const work = params.get('work');
  const section = params.get('section');
  return {
    work: work && uuid.test(work) ? work : null,
    section: work && uuid.test(work) && section && uuid.test(section) ? section : null,
  };
}
