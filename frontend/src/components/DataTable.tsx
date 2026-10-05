import { useMemo, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react';
import { ChevronDown, ChevronUp, Filter, X } from 'lucide-react';

export type SortDir = 'asc' | 'desc';
type Primitive = string | number | boolean | null | undefined;

/** Column definition; `key` must be a field of the row type. */
export interface Column<T extends object> {
  key: Extract<keyof T, string>;
  label: ReactNode;
  /** Custom cell renderer (defaults to the raw field value). */
  render?: (row: T) => ReactNode;
  /** Value used for filtering/sorting (defaults to the raw field value). */
  value?: (row: T) => Primitive;
  /** `true` = case-insensitive substring, `'exact'` = case-insensitive equality. */
  filterable?: boolean | 'exact';
  sortable?: boolean;
  align?: 'left' | 'right' | 'center';
  className?: string;
  style?: CSSProperties;
}

interface DataTableProps<T extends object> {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string;
  onRowClick?: (row: T) => void;
  emptyText?: ReactNode;
  initialSort?: { key: Extract<keyof T, string>; dir: SortDir };
  initialFilters?: Partial<Record<Extract<keyof T, string>, string>>;
  /** Filters applied outside the table (e.g. URL ticker) – shows the reset button. */
  externalFilterActive?: boolean;
  /** Called by "Filter zurücksetzen" in addition to clearing column filters. */
  onResetFilters?: () => void;
}

const filterInputStyle: CSSProperties = {
  width: '100%',
  padding: '4px 6px',
  fontSize: '0.65rem',
  background: 'var(--surface-high)',
  border: '1px solid rgba(255,255,255,0.08)',
  borderRadius: '4px',
  color: 'var(--on-surface)',
  outline: 'none',
  marginTop: '4px',
  fontFamily: 'var(--font-mono)',
  display: 'block',
};

function cellValue<T extends object>(col: Column<T>, row: T): Primitive {
  if (col.value) return col.value(row);
  const raw: unknown = row[col.key];
  if (raw == null || typeof raw === 'string' || typeof raw === 'number' || typeof raw === 'boolean') {
    return raw;
  }
  return Array.isArray(raw) ? raw.join(', ') : String(raw);
}

function compare(a: Primitive, b: Primitive): number {
  if (a == null && b == null) return 0;
  if (a == null) return 1; // nulls last
  if (b == null) return -1;
  if (typeof a === 'number' && typeof b === 'number') return a - b;
  return String(a).localeCompare(String(b), 'de', { numeric: true });
}

export function DataTable<T extends object>({
  rows,
  columns,
  rowKey,
  onRowClick,
  emptyText = 'Keine Daten',
  initialSort,
  initialFilters,
  externalFilterActive = false,
  onResetFilters,
}: DataTableProps<T>) {
  const [filters, setFilters] = useState<Record<string, string>>(
    () => ({ ...(initialFilters as Record<string, string> | undefined) }),
  );
  const [sort, setSort] = useState<{ key: string; dir: SortDir } | null>(initialSort ?? null);

  const visible = useMemo(() => {
    const active = columns.filter((c) => c.filterable && filters[c.key]?.trim());
    let result = active.length
      ? rows.filter((row) =>
          active.every((col) => {
            const needle = filters[col.key].trim().toLowerCase();
            const hay = String(cellValue(col, row) ?? '').toLowerCase();
            return col.filterable === 'exact' ? hay === needle : hay.includes(needle);
          }),
        )
      : rows;
    if (sort) {
      const col = columns.find((c) => c.key === sort.key);
      if (col) {
        const factor = sort.dir === 'asc' ? 1 : -1;
        result = [...result].sort((a, b) => {
          const va = cellValue(col, a);
          const vb = cellValue(col, b);
          // keep nulls last regardless of direction
          if (va == null || vb == null) return compare(va, vb);
          return compare(va, vb) * factor;
        });
      }
    }
    return result;
  }, [rows, columns, filters, sort]);

  const hasColumnFilters = Object.values(filters).some((v) => v.trim() !== '');
  const showReset = hasColumnFilters || externalFilterActive;

  const toggleSort = (key: string) => {
    setSort((prev) => {
      if (prev?.key !== key) return { key, dir: 'desc' };
      return prev.dir === 'desc' ? { key, dir: 'asc' } : null;
    });
  };

  const resetFilters = () => {
    setFilters({});
    onResetFilters?.();
  };

  const onRowKeyDown = (e: KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (onRowClick && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onRowClick(row);
    }
  };

  return (
    <div>
      {showReset && (
        <div className="flex items-center gap-sm mb-md">
          <Filter size={14} style={{ color: 'var(--primary)' }} />
          <span className="text-xs text-dim">
            {visible.length} von {rows.length} Einträgen
          </span>
          <button className="btn btn-sm btn-ghost" onClick={resetFilters} style={{ fontSize: '0.7rem', gap: '4px' }}>
            <X size={12} /> Filter zurücksetzen
          </button>
        </div>
      )}
      <div className="card" style={{ padding: 0, overflow: 'auto' }}>
        <table className="data-table">
          <thead>
            <tr>
              {columns.map((col) => {
                const sorted = sort?.key === col.key ? sort.dir : null;
                return (
                  <th
                    key={col.key}
                    className={col.align ? `text-${col.align}` : undefined}
                    aria-sort={sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : undefined}
                  >
                    {col.sortable ? (
                      <button
                        type="button"
                        className="th-sort"
                        onClick={() => toggleSort(col.key)}
                        title="Sortieren"
                      >
                        {col.label}
                        {sorted === 'asc' && <ChevronUp size={11} />}
                        {sorted === 'desc' && <ChevronDown size={11} />}
                      </button>
                    ) : (
                      col.label
                    )}
                    {col.filterable && (
                      <input
                        type="text"
                        value={filters[col.key] ?? ''}
                        onChange={(e) => setFilters((prev) => ({ ...prev, [col.key]: e.target.value }))}
                        placeholder={col.filterable === 'exact' ? 'exakt…' : 'Filter…'}
                        aria-label={`Filter ${typeof col.label === 'string' ? col.label : col.key}`}
                        style={filterInputStyle}
                      />
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, i) => (
              <tr
                key={rowKey(row, i)}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                onKeyDown={onRowClick ? (e) => onRowKeyDown(e, row) : undefined}
                tabIndex={onRowClick ? 0 : undefined}
                style={onRowClick ? { cursor: 'pointer' } : undefined}
              >
                {columns.map((col) => (
                  <td
                    key={col.key}
                    className={[col.align ? `text-${col.align}` : '', col.className ?? ''].join(' ').trim() || undefined}
                    style={col.style}
                  >
                    {col.render ? col.render(row) : String(cellValue(col, row) ?? '—')}
                  </td>
                ))}
              </tr>
            ))}
            {visible.length === 0 && (
              <tr>
                <td colSpan={columns.length} className="text-dim text-center">
                  {emptyText}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
