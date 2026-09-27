import type { Paging, Resource } from './api';
import { advance } from './api';

export function Status<T>({
  resource,
  label = '正在读取…',
}: {
  resource: Resource<T>;
  label?: string;
}) {
  if (resource.loading)
    return (
      <p className="status" role="status">
        {label}
      </p>
    );
  if (resource.error)
    return (
      <div className="error" role="alert">
        <p>{resource.error}</p>
        <button onClick={resource.retry}>重新读取</button>
      </div>
    );
  return null;
}
export function Pager({
  paging,
  next,
  onChange,
  disabled = false,
  noun = '页',
}: {
  paging: Paging;
  next?: string | null;
  onChange: (value: Paging) => void;
  disabled?: boolean;
  noun?: string;
}) {
  return (
    <div className="pager">
      <button
        disabled={disabled || paging.page === 0}
        onClick={() => onChange({ ...paging, page: paging.page - 1 })}
      >
        上一页
      </button>
      <span>
        第 {paging.page + 1} {noun}
      </span>
      <button disabled={disabled || !next} onClick={() => next && onChange(advance(paging, next))}>
        下一页
      </button>
    </div>
  );
}
export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="empty">{children}</p>;
}
