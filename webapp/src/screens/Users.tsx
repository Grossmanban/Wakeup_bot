import { useEffect, useState } from "react";
import { AccountStatus, api, Role, ROLE_TITLES, STATUS_TITLES, UserShort } from "../api";
import { Empty, ErrorBox, Loader, StatusChip } from "../components/ui";
import { load, save } from "../storage";

interface Filters {
  q: string;
  role: Role | "";
  status: AccountStatus | "";
}

const PAGE = 50;

export function Users({ onOpen }: { onOpen: (id: number) => void }) {
  const [filters, setFilters] = useState<Filters>(() => load("users.filters", { q: "", role: "", status: "" }));
  const [query, setQuery] = useState(filters.q);
  const [items, setItems] = useState<UserShort[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  // Поиск с задержкой, чтобы не слать запрос на каждую букву.
  useEffect(() => {
    const t = setTimeout(() => setFilters((f) => (f.q === query ? f : { ...f, q: query })), 300);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => save("users.filters", filters), [filters]);

  const fetchPage = async (offset: number) => {
    const p = new URLSearchParams({ limit: String(PAGE), offset: String(offset) });
    if (filters.q.trim()) p.set("q", filters.q.trim());
    if (filters.role) p.set("role", filters.role);
    if (filters.status) p.set("status", filters.status);
    return api.get<{ items: UserShort[]; total: number }>(`/users?${p}`);
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchPage(0)
      .then((r) => {
        if (cancelled) return;
        setItems(r.items);
        setTotal(r.total);
      })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filters.q, filters.role, filters.status]);

  const more = async () => {
    setLoading(true);
    try {
      const r = await fetchPage(items.length);
      setItems((prev) => [...prev, ...r.items]);
      setTotal(r.total);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1>Сотрудники</h1>
      <input
        placeholder="Поиск: ФИО, @username или Telegram ID"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        style={{ marginBottom: 8 }}
      />
      <div className="row" style={{ marginBottom: 12, flexWrap: "nowrap" }}>
        <select value={filters.role} onChange={(e) => setFilters({ ...filters, role: e.target.value as Role | "" })}>
          <option value="">Все роли</option>
          {(Object.keys(ROLE_TITLES) as Role[]).map((r) => (
            <option key={r} value={r}>
              {ROLE_TITLES[r]}
            </option>
          ))}
        </select>
        <select
          value={filters.status}
          onChange={(e) => setFilters({ ...filters, status: e.target.value as AccountStatus | "" })}
        >
          <option value="">Все статусы</option>
          {(["active", "pending", "rejected", "disabled", "blocked"] as AccountStatus[]).map((s) => (
            <option key={s} value={s}>
              {STATUS_TITLES[s]}
            </option>
          ))}
        </select>
      </div>
      <ErrorBox error={error} />
      {!loading && items.length === 0 && !error && <Empty>Никого не найдено</Empty>}
      {items.map((u) => (
        <button className="list-item" key={u.id} onClick={() => onOpen(u.id)}>
          <div className="row spread">
            <b>{u.full_name}</b>
            <StatusChip status={u.status} />
          </div>
          <div className="muted small">
            {ROLE_TITLES[u.role]}
            {u.username ? ` · @${u.username}` : ""}
          </div>
        </button>
      ))}
      {loading && <Loader />}
      {!loading && items.length < total && (
        <button className="block" onClick={more}>
          Показать ещё ({total - items.length})
        </button>
      )}
    </div>
  );
}
