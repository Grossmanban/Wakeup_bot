import { useState } from "react";
import { api, Me, Network, ProductListItem, rub, TASK_STATUS_TITLES, TaskStatus, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { load, save } from "../storage";
import { downloadFile } from "../telegram";

export interface Metrics {
  tasks: number;
  units: number;
  working_days: number;
  hours: number;
  units_per_hour: number | null;
  units_per_day: number | null;
  avg_task_minutes: number | null;
  pending_units: number;
  approved_units: number;
  paid_units: number;
  rejected_units: number;
  quality: number | null;
  sample_units: number;
  small_sample: boolean;
  paid_units_per_hour: number | null;
  forecast_paid_units_per_hour: number | null;
  earnings_kop: number;
  earnings_per_hour_kop: number | null;
  planned_units: number;
  plan_percent: number | null;
  insufficient_time: boolean;
}

interface UserStats extends Metrics {
  user_id: number;
  full_name: string;
  shift_hours: number;
  products: (Metrics & { product_id: number; product: string })[];
}

interface StatsData {
  total: Metrics;
  users: UserStats[];
  products: (Metrics & { product_id: number; product: string })[];
  hourly: { hour: number; units: number }[];
  formulas: Record<string, string>;
}

const PRESETS: [string, string][] = [
  ["today", "Сегодня"],
  ["yesterday", "Вчера"],
  ["week", "Текущая неделя"],
  ["last_week", "Прошлая неделя"],
  ["month", "Текущий месяц"],
  ["last_month", "Прошлый месяц"],
  ["custom", "Период"],
];

const n = (v: number | null, suffix = "") => (v === null ? "Недостаточно данных" : `${v.toLocaleString("ru-RU")}${suffix}`);

export function MetricsView({ m, compact }: { m: Metrics; compact?: boolean }) {
  return (
    <div className="kv small">
      <span>Выполнено единиц</span>
      <span>
        <b>{m.units}</b> ({m.tasks} заданий, {m.working_days} раб. дн.)
      </span>
      <span>Валовые ед./ч</span>
      <span>{n(m.units_per_hour)}</span>
      <span>Качество</span>
      <span>
        {m.quality === null ? "—" : `${m.quality}%`}, {m.sample_units} проверенных ед.
        {m.small_sample && <span className="chip warn"> малая выборка</span>}
      </span>
      <span>Прогноз оплачиваемых ед./ч</span>
      <span>{n(m.forecast_paid_units_per_hour)}</span>
      <span>Факт оплачиваемых ед./ч</span>
      <span>{n(m.paid_units_per_hour)}</span>
      {!compact && (
        <>
          <span>Время выполнения</span>
          <span>{m.hours} ч</span>
          <span>Ед. в день</span>
          <span>{m.units_per_day ?? "—"}</span>
          <span>Среднее задание</span>
          <span>{m.avg_task_minutes !== null ? `${m.avg_task_minutes} мин` : "—"}</span>
        </>
      )}
      <span>На модерации / допущено / оплачено / отклонено</span>
      <span>
        {m.pending_units} / {m.approved_units} / {m.paid_units} / {m.rejected_units}
      </span>
      <span>Заработок</span>
      <span>
        {rub(m.earnings_kop)}
        {m.earnings_per_hour_kop !== null ? ` (${rub(m.earnings_per_hour_kop)}/ч)` : ""}
      </span>
      {m.planned_units > 0 && (
        <>
          <span>Выполнение плана</span>
          <span>
            {m.units} из {m.planned_units} ({m.plan_percent}%)
          </span>
        </>
      )}
    </div>
  );
}

export function MyStats() {
  const [preset, setPreset] = useState("month");
  const d = useLoad(() => api.get<StatsData>(`/my/stats?preset=${preset}`), [preset]);
  const me = d.data?.users[0];
  return (
    <>
      <h2>Моя статистика</h2>
      <select value={preset} onChange={(e) => setPreset(e.target.value)} style={{ marginBottom: 8 }}>
        {PRESETS.filter(([k]) => k !== "custom").map(([k, t]) => (
          <option key={k} value={k}>
            {t}
          </option>
        ))}
      </select>
      <ErrorBox error={d.error} />
      {me && (
        <div className="card">
          <MetricsView m={me} />
        </div>
      )}
    </>
  );
}

interface F {
  preset: string;
  start: string;
  end: string;
  network: string;
  coach: string;
  instructor: string;
  users: number[];
  products: number[];
  statuses: TaskStatus[];
  sort: string;
}

const DEFAULT: F = { preset: "week", start: "", end: "", network: "", coach: "", instructor: "", users: [], products: [], statuses: [], sort: "units" };

function query(f: F) {
  const p = new URLSearchParams();
  if (f.preset === "custom") {
    p.set("start", f.start);
    p.set("end", f.end);
  } else p.set("preset", f.preset);
  if (f.network) p.set("network_id", f.network);
  if (f.coach) p.set("coach_id", f.coach);
  if (f.instructor) p.set("instructor_id", f.instructor);
  f.users.forEach((u) => p.append("user_ids", String(u)));
  f.products.forEach((u) => p.append("product_ids", String(u)));
  f.statuses.forEach((u) => p.append("statuses", u));
  p.set("sort", f.sort);
  return p.toString();
}

export function StatsAdmin({ me }: { me: Me }) {
  const nav = useNav();
  const [f, setF] = useState<F>(() => load("stats.filters", DEFAULT));
  const set = (v: F) => {
    setF(v);
    save("stats.filters", v);
  };
  const ready = f.preset !== "custom" || (f.start && f.end);
  const d = useLoad(() => (ready ? api.get<StatsData>(`/stats?${query(f)}`) : Promise.resolve(null)), [query(f)]);
  const networks = useLoad(() => api.get<Network[]>("/networks").catch(() => []), []);
  const products = useLoad(() => api.get<ProductListItem[]>("/products").catch(() => []), []);
  const people = useLoad(() => api.get<{ items: UserShort[] }>("/users?limit=200").then((r) => r.items).catch(() => []), []);
  const action = useAction();
  const canExport = me.is_owner || me.permissions.includes("export");

  const exportFile = async (kind: "users" | "tasks", format: "xlsx" | "csv") => {
    const body: Record<string, unknown> = { kind, format, sort: f.sort };
    if (f.preset === "custom") Object.assign(body, { start: f.start, end: f.end });
    else body.preset = f.preset;
    if (f.network) body.network_id = Number(f.network);
    if (f.coach) body.coach_id = Number(f.coach);
    if (f.instructor) body.instructor_id = Number(f.instructor);
    if (f.users.length) body.user_ids = f.users;
    if (f.products.length) body.product_ids = f.products;
    if (f.statuses.length) body.statuses = f.statuses;
    const r = await action.run(() => api.post<{ url: string; file_name: string }>("/stats/export-link", body));
    if (r.ok) downloadFile(r.value.url, r.value.file_name);
  };

  const multi = (label: string, value: (number | string)[], opts: { id: number | string; name: string }[], onChange: (v: string[]) => void) =>
    opts.length > 0 && (
      <Field label={label}>
        <select multiple value={value.map(String)} style={{ minHeight: 90 }} onChange={(e) => onChange([...e.target.selectedOptions].map((o) => o.value))}>
          {opts.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </Field>
    );
  const byRole = (r: string) => (people.data ?? []).filter((p) => p.role === r);

  return (
    <div>
      <h1>Статистика</h1>
      <details className="card" open>
        <summary>Фильтры</summary>
        <Field label="Период">
          <select value={f.preset} onChange={(e) => set({ ...f, preset: e.target.value })}>
            {PRESETS.map(([k, t]) => (
              <option key={k} value={k}>
                {t}
              </option>
            ))}
          </select>
        </Field>
        {f.preset === "custom" && (
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <input type="date" value={f.start} onChange={(e) => set({ ...f, start: e.target.value })} />
            <input type="date" value={f.end} onChange={(e) => set({ ...f, end: e.target.value })} />
          </div>
        )}
        {(networks.data?.length ?? 0) > 0 && (
          <Field label="Сеть">
            <select value={f.network} onChange={(e) => set({ ...f, network: e.target.value })}>
              <option value="">Все</option>
              {networks.data!.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {byRole("coach").length > 0 && (
          <Field label="Команда тренера">
            <select value={f.coach} onChange={(e) => set({ ...f, coach: e.target.value })}>
              <option value="">Все</option>
              {byRole("coach").map((x) => (
                <option key={x.id} value={x.id}>
                  {x.full_name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {byRole("instructor").length > 0 && (
          <Field label="Команда инструктора">
            <select value={f.instructor} onChange={(e) => set({ ...f, instructor: e.target.value })}>
              <option value="">Все</option>
              {byRole("instructor").map((x) => (
                <option key={x.id} value={x.id}>
                  {x.full_name}
                </option>
              ))}
            </select>
          </Field>
        )}
        {multi("Сотрудники", f.users, (people.data ?? []).map((p) => ({ id: p.id, name: p.full_name })), (v) => set({ ...f, users: v.map(Number) }))}
        {multi("Продукты", f.products, (products.data ?? []).map((p) => ({ id: p.id, name: p.name })), (v) => set({ ...f, products: v.map(Number) }))}
        {multi(
          "Статусы заданий",
          f.statuses,
          (["moderation", "approved", "paid", "rejected"] as TaskStatus[]).map((s) => ({ id: s, name: TASK_STATUS_TITLES[s] })),
          (v) => set({ ...f, statuses: v as TaskStatus[] }),
        )}
        <button onClick={() => set({ ...DEFAULT, preset: f.preset })}>Сбросить фильтры</button>
      </details>
      <ErrorBox error={d.error ?? action.error} onRetry={d.reload} />
      {d.loading && !d.data && <Loader />}
      {d.data && (
        <>
          <h2>Итого</h2>
          <div className="card">
            <MetricsView m={d.data.total} />
          </div>
          <div className="row" style={{ marginBottom: 8 }}>
            <button
              onClick={() =>
                nav.push({ type: "custom", key: "drill", render: () => <Drilldown q={query(f)} /> })
              }
            >
              Задания ({d.data.total.tasks})
            </button>
            {canExport && (
              <>
                <button disabled={action.busy} onClick={() => exportFile("users", "xlsx")}>
                  Сотрудники XLSX
                </button>
                <button disabled={action.busy} onClick={() => exportFile("tasks", "xlsx")}>
                  Задания XLSX
                </button>
                <button disabled={action.busy} onClick={() => exportFile("tasks", "csv")}>
                  CSV
                </button>
              </>
            )}
          </div>

          {d.data.products.length > 0 && <h2>По продуктам</h2>}
          {d.data.products.map((p) => (
            <details className="card" key={p.product_id}>
              <summary>
                <b>{p.product}</b> · {p.units} ед. · качество {p.quality === null ? "—" : `${p.quality}%`}
              </summary>
              <MetricsView m={p} compact />
            </details>
          ))}

          <h2>Сравнение сотрудников</h2>
          <Field label="Сортировка">
            <select value={f.sort} onChange={(e) => set({ ...f, sort: e.target.value })}>
              <option value="units">Выполненные единицы</option>
              <option value="quality">Качество</option>
              <option value="units_per_hour">Валовая производительность</option>
              <option value="paid_units_per_hour">Оплачиваемые ед./ч</option>
              <option value="earnings">Заработок</option>
              <option value="plan">Выполнение плана</option>
            </select>
          </Field>
          {d.data.users.length === 0 && <Empty>Нет данных</Empty>}
          {d.data.users.map((u, i) => (
            <details className="card" key={u.user_id}>
              <summary>
                {i + 1}. <b>{u.full_name}</b> · {u.units} ед. · {u.units_per_hour ?? "—"} ед./ч · качество{" "}
                {u.quality === null ? "—" : `${u.quality}%`}
              </summary>
              <MetricsView m={u} />
              <div className="small muted">Часы смен по графику: {u.shift_hours}</div>
              {u.products.map((p) => (
                <div key={p.product_id} className="small" style={{ marginTop: 6 }}>
                  <b>{p.product}</b>: {p.units} ед., {p.units_per_hour ?? "—"} ед./ч, качество{" "}
                  {p.quality === null ? "—" : `${p.quality}%`}, прогноз {p.forecast_paid_units_per_hour ?? "—"} опл. ед./ч
                </div>
              ))}
            </details>
          ))}

          <h2>По часам</h2>
          <Hourly data={d.data.hourly} />
          <details className="small">
            <summary>Как считаются показатели</summary>
            {Object.values(d.data.formulas).map((t, i) => (
              <p key={i} className="muted">
                {t}
              </p>
            ))}
          </details>
        </>
      )}
    </div>
  );
}

function Hourly({ data }: { data: { hour: number; units: number }[] }) {
  const max = Math.max(1, ...data.map((d) => d.units));
  const shown = data.filter((d, i) => d.units > 0 || (i >= 8 && i <= 22));
  return (
    <div className="card">
      {shown.map((d) => (
        <div key={d.hour} className="row small" style={{ flexWrap: "nowrap", gap: 6 }}>
          <span style={{ width: 40 }}>{String(d.hour).padStart(2, "0")}:00</span>
          <div style={{ flex: 1, background: "var(--chip)", borderRadius: 4, height: 10 }}>
            <div style={{ width: `${(d.units * 100) / max}%`, background: "var(--accent)", height: 10, borderRadius: 4 }} />
          </div>
          <span style={{ width: 30, textAlign: "right" }}>{d.units}</span>
        </div>
      ))}
      <div className="muted small">Единица относится к часу завершения задания (МСК).</div>
    </div>
  );
}

interface DrillRow {
  task_id: number;
  external_id: string;
  product: string;
  employee: string;
  completed_at: string;
  units: number;
  minutes: number | null;
  status: TaskStatus;
  earnings_kop: number;
}

function Drilldown({ q }: { q: string }) {
  const nav = useNav();
  const d = useLoad(() => api.get<DrillRow[]>(`/stats/tasks?${q}`), [q]);
  if (d.loading && !d.data) return <Loader />;
  if (!d.data) return <ErrorBox error={d.error} />;
  return (
    <div>
      <h1>Задания</h1>
      {d.data.length === 0 && <Empty>Нет заданий</Empty>}
      {d.data.map((r) => (
        <button className="list-item small" key={r.task_id} onClick={() => nav.push({ type: "task", id: r.task_id })}>
          <div className="row spread">
            <b>
              {r.product} · {r.external_id}
            </b>
            <span>{TASK_STATUS_TITLES[r.status]}</span>
          </div>
          <div className="muted">
            {r.employee} · {new Date(r.completed_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} · {r.units} ед. ·{" "}
            {r.minutes ?? "—"} мин · {rub(r.earnings_kop)}
          </div>
        </button>
      ))}
    </div>
  );
}
