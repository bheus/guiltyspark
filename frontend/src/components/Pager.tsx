import { PAGE_SIZE } from "../lib/constants";

interface PagerProps {
  page: number;
  total: number;
  onPage: (page: number) => void;
}

// Newest-first paging shared by the two archive panels. Renders nothing when a
// single page holds everything, so short histories stay uncluttered.
export function Pager({ page, total, onPage }: PagerProps) {
  if (total <= PAGE_SIZE) return null;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const start = page * PAGE_SIZE + 1;
  const end = Math.min(total, (page + 1) * PAGE_SIZE);
  return (
    <div className="pager">
      <button
        className="btn"
        disabled={page <= 0}
        onClick={() => onPage(page - 1)}
      >
        ‹ Newer
      </button>
      <span className="pager-label">
        {start}–{end} of {total} · page {page + 1} / {pages}
      </span>
      <button
        className="btn"
        disabled={page + 1 >= pages}
        onClick={() => onPage(page + 1)}
      >
        Earlier ›
      </button>
    </div>
  );
}
