import { useState } from "react";
import { api, Me, Network, ProductListItem, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";
import { load, save } from "../storage";
import { confirmAction } from "../telegram";
import { DAY_STATUS, fmtDay, WorkPoint } from "./Schedule";

function isoToday(offset = 0) {
  const d = new Date(Date.now() + offset * 86400000);
  return d.toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
}

interface PeopleFilters {
  network: string;
  coach: string;
  instructor: string;
  user: string;
  product: string;
}

const EMPTY: PeopleFilters = { network: "", coach: "", instructor: "", user: "", product: "" };

function useFilterData(me: Me) {
  const canUsers = me.is_owner || me.permissions.includes("users.view");
  const networks = useLoad(() => api.get<Network[]>("/networks").catch(() => []), []);
  const products = useLoad(() => api.get<ProductListItem[]>("/products").catch(() => []), []);
  const people = useLoad(
    () =>
      canUsers
        ? api.get<{ items: UserShort[] }>("/users?limit=200&status=active").then((r) => r.items)
        : Promise.resolve([] as UserShort[]),
    [canUsers],
  );
  return { networks: networks.data ?? [], products: products.data ?? [], people: people.data ?? [] };
}

function Filters({ me, value, onChange, withUser }: { me: Me; value: PeopleFilters; onChange: (v: PeopleFilters) => void; withUser: boolean }) {
  const d = useFilterData(me);
  const sel = (key: keyof PeopleFilters, label: string, options: { id: number; name: string }[]) =>
    options.length > 0 && (
      <Field label={label}>
        <select value={value[key]} onChange={(e) => onChange({ ...value, [key]: e.target.value })}>
          <option value="">Все</option>
          {options.map((o) => (
            <option key={o.id} value={o.id}>
              {o.name}
            </option>
          ))}
        </select>
      </Field>
    );
  const byRole = (r: string) => d.people.filter((p) => p.role === r).map((p) => ({ id: p.id, name: p.full_name }));
  return (
    <details className="card" open>
      <summary>Фильтры</summary>
      {sel("network", "Сеть", d.networks.map((n) => ({ id: n.id, name: n.name })))}
      {sel("coach", "Тренер", byRole("coach"))}
      {sel("instructor", "Инструктор", byRole("instructor"))}
      {withUser && sel("user", "Сотрудник", d.people.map((p) => ({ id: p.id, name: p.full_name })))}
      {sel("product", "Продукт", d.products.map((p) => ({ id: p.id, name: p.name })))}
    </details>
  );
}

function qs(f: PeopleFilters, extra: Record<string, string>) {
  const p = new URLSearchParams(extra);
  if (f.network) p.set("network_id", f.network);
  if (f.coach) p.set("coach_id", f.coach);
  if (f.instructor) p.set("instructor_id", f.instructor);
  if (f.user) p.set("user_id", f.user);
  if (f.product) p.set("product_id", f.product);
  return p.toString();
}

interface SummaryRow {
  day: string;
  user_id: number;
  full_name: string;
  status: string;
  start: string | null;
  end: string | null;
  point: string | null;
  planned_units: number;
  actual_units: number;
  plan_percent: number | null;
}

interface SummaryData {
  rows: SummaryRow[];
  days: { day: string; planned: number; actual: number; people: number; pending: number; day_off: number; none: number }[];
  total: { planned_units: number; actual_units: number; plan_percent: number | null };
}

export function DaySummary({ me }: { me: Me }) {
  const [f, setF] = useState<PeopleFilters>(() => load("summary.filters", EMPTY));
  const [range, setRange] = useState(() => load("summary.range", { start: isoToday(), end: isoToday(6) }));
  const set = (v: PeopleFilters) => {
    setF(v);
    save("summary.filters", v);
  };
  const setR = (v: { start: string; end: string }) => {
    setRange(v);
    save("summary.range", v);
  };
  const data = useLoad(
    () => api.get<SummaryData>(`/schedule/summary?${qs(f, range)}`),
    [JSON.stringify(f), range.start, range.end],
  );
  return (
    <div>
      <h1>Сводка по дням</h1>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <Field label="С">
          <input type="date" value={range.start} onChange={(e) => setR({ ...range, start: e.target.value })} />
        </Field>
        <Field label="По">
          <input type="date" value={range.end} onChange={(e) => setR({ ...range, end: e.target.value })} />
        </Field>
      </div>
      <Filters me={me} value={f} onChange={set} withUser />
      <ErrorBox error={data.error} onRetry={data.reload} />
      {data.loading && !data.data && <Loader />}
      {data.data && (
        <>
          <div className="card kv">
            <span>План, ед.</span>
            <span>{data.data.total.planned_units}</span>
            <span>Факт, ед.</span>
            <span>{data.data.total.actual_units}</span>
            <span>Выполнение</span>
            <span>{data.data.total.plan_percent !== null ? `${data.data.total.plan_percent}%` : "—"}</span>
          </div>
          {data.data.days.map((d) => (
            <details className="card" key={d.day}>
              <summary>
                <b>{fmtDay(d.day)}</b> · работают {d.people} · ожидают {d.pending} · выходной {d.day_off} · без статуса {d.none} ·
                план {d.planned} / факт {d.actual}
              </summary>
              {data.data!.rows
                .filter((r) => r.day === d.day)
                .map((r) => (
                  <div className="small" key={r.user_id} style={{ padding: "4px 0", borderTop: "1px solid var(--border)" }}>
                    <div className="row spread">
                      <span>{r.full_name}</span>
                      <span className={`chip ${DAY_STATUS[r.status]?.cls ?? ""}`}>{DAY_STATUS[r.status]?.title ?? r.status}</span>
                    </div>
                    {r.start && (
                      <div className="muted">
                        {r.start}–{r.end} · {r.point}
                      </div>
                    )}
                    {(r.planned_units > 0 || r.actual_units > 0) && (
                      <div className="muted">
                        План {r.planned_units} · факт {r.actual_units}
                        {r.plan_percent !== null ? ` · ${r.plan_percent}%` : ""}
                      </div>
                    )}
                  </div>
                ))}
            </details>
          ))}
        </>
      )}
    </div>
  );
}

interface DistributionData {
  day: string;
  people: number;
  points: {
    point: string;
    address: string | null;
    people: { user_id: number; full_name: string; start: string; end: string; plans: { product: string; units: number }[]; facts: { product: string; units: number }[] }[];
  }[];
  plan: { product: string; units: number }[];
  fact: { product: string; units: number }[];
  text: string;
}

export function Distribution({ me }: { me: Me }) {
  const [f, setF] = useState<PeopleFilters>(() => load("distr.filters", EMPTY));
  const [day, setDay] = useState(isoToday(1));
  const data = useLoad(() => api.get<DistributionData>(`/schedule/distribution?${qs(f, { day })}`), [JSON.stringify(f), day]);
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <h1>Распред</h1>
      <Field label="День">
        <input type="date" value={day} onChange={(e) => setDay(e.target.value)} />
      </Field>
      <Filters
        me={me}
        value={f}
        onChange={(v) => {
          setF(v);
          save("distr.filters", v);
        }}
        withUser={false}
      />
      <ErrorBox error={data.error} onRetry={data.reload} />
      {data.loading && !data.data && <Loader />}
      {data.data && (
        <>
          <button
            className="primary block"
            style={{ marginBottom: 10 }}
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(data.data!.text);
                setCopied(true);
              } catch {
                setCopied(false);
                window.prompt("Скопируйте текст", data.data!.text);
              }
            }}
          >
            {copied ? "Скопировано" : "Скопировать распред"}
          </button>
          {data.data.points.length === 0 && <Empty>Подтверждённых смен нет</Empty>}
          {data.data.points.map((p) => (
            <div className="card" key={p.point}>
              <b>{p.point}</b>
              {p.address && <div className="muted small">{p.address}</div>}
              {p.people.map((x) => (
                <div className="small" key={x.user_id} style={{ marginTop: 6 }}>
                  {x.full_name} · {x.start}–{x.end}
                  <div className="muted">
                    План: {x.plans.map((pl) => `${pl.product} ${pl.units}`).join(", ") || "—"}
                    {x.facts.length > 0 && ` · факт: ${x.facts.map((pl) => `${pl.product} ${pl.units}`).join(", ")}`}
                  </div>
                </div>
              ))}
            </div>
          ))}
          <div className="card small">
            Итого: людей {data.data.people}; план: {data.data.plan.map((p) => `${p.product} ${p.units}`).join(", ") || "—"}
            {data.data.fact.length > 0 && <div>Факт: {data.data.fact.map((p) => `${p.product} ${p.units}`).join(", ")}</div>}
          </div>
        </>
      )}
    </div>
  );
}

interface PendingShift {
  id: number;
  day: string;
  start: string;
  end: string;
  point: string;
  user_id: number;
  full_name: string;
}

export function ShiftRequests() {
  const list = useLoad(() => api.get<PendingShift[]>("/shifts/pending"), []);
  const action = useAction();
  const [rejecting, setRejecting] = useState<PendingShift | null>(null);
  return (
    <div>
      <h1>Смены на подтверждение</h1>
      <ErrorBox error={list.error ?? action.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Заявок нет</Empty>}
      {list.data?.map((s) => (
        <div className="card stack" key={s.id}>
          <b>{s.full_name}</b>
          <div className="small">
            {fmtDay(s.day)} · {s.start}–{s.end} · {s.point}
          </div>
          <div className="row">
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post(`/shifts/${s.id}/confirm`));
                if (r.ok) list.reload();
              }}
            >
              Подтвердить
            </button>
            <button className="danger" onClick={() => setRejecting(s)}>
              Отклонить
            </button>
          </div>
        </div>
      ))}
      {rejecting && (
        <ReasonDialog
          title="Отклонить смену"
          confirmText="Отклонить"
          danger
          reasonRequired
          reasonLabel="Причина"
          onClose={() => setRejecting(null)}
          onConfirm={async (reason) => {
            await api.post(`/shifts/${rejecting.id}/reject`, { reason });
            list.reload();
          }}
        />
      )}
    </div>
  );
}

export function Points() {
  const list = useLoad(() => api.get<WorkPoint[]>("/work-points?include_archived=true"), []);
  const [form, setForm] = useState({ name: "", address: "", capacity: "" });
  const [edit, setEdit] = useState<WorkPoint | null>(null);
  const action = useAction();
  const body = (v: { name: string; address: string; capacity: string }) => ({
    name: v.name,
    address: v.address || null,
    capacity: v.capacity ? Number(v.capacity) : null,
  });
  return (
    <div>
      <h1>Рабочие точки</h1>
      <ErrorBox error={list.error ?? action.error} />
      <form
        className="card"
        onSubmit={async (e) => {
          e.preventDefault();
          const r = await action.run(() => api.post("/work-points", body(form)));
          if (r.ok) {
            setForm({ name: "", address: "", capacity: "" });
            list.reload();
          }
        }}
      >
        <Field label="Название">
          <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
        </Field>
        <Field label="Адрес">
          <input value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} />
        </Field>
        <Field label="Вместимость (пусто — без ограничения)">
          <input type="number" min={1} value={form.capacity} onChange={(e) => setForm({ ...form, capacity: e.target.value })} />
        </Field>
        <button className="primary" disabled={action.busy || !form.name.trim()}>
          Добавить точку
        </button>
      </form>
      {list.data?.map((p) =>
        edit?.id === p.id ? (
          <form
            className="card"
            key={p.id}
            onSubmit={async (e) => {
              e.preventDefault();
              const r = await action.run(() =>
                api.patch(`/work-points/${p.id}`, body({ name: edit.name, address: edit.address ?? "", capacity: edit.capacity ? String(edit.capacity) : "" })),
              );
              if (r.ok) {
                setEdit(null);
                list.reload();
              }
            }}
          >
            <Field label="Название">
              <input value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
            </Field>
            <Field label="Адрес">
              <input value={edit.address ?? ""} onChange={(e) => setEdit({ ...edit, address: e.target.value })} />
            </Field>
            <Field label="Вместимость">
              <input
                type="number"
                min={1}
                value={edit.capacity ?? ""}
                onChange={(e) => setEdit({ ...edit, capacity: e.target.value ? Number(e.target.value) : null })}
              />
            </Field>
            <div className="row">
              <button className="primary">Сохранить</button>
              <button type="button" onClick={() => setEdit(null)}>
                Отмена
              </button>
            </div>
          </form>
        ) : (
          <div className="card" key={p.id}>
            <div className="row spread">
              <b>
                {p.name} {p.is_archived && <span className="chip">архив</span>}
              </b>
              <span className="muted small">{p.capacity ? `мест: ${p.capacity}` : "без ограничения"}</span>
            </div>
            {p.address && <div className="small muted">{p.address}</div>}
            <div className="row" style={{ marginTop: 6 }}>
              <button onClick={() => setEdit(p)}>Изменить</button>
              <button
                onClick={async () => {
                  if (!(await confirmAction(p.is_archived ? "Восстановить точку?" : "Отправить точку в архив?"))) return;
                  const r = await action.run(() => api.patch(`/work-points/${p.id}`, { is_archived: !p.is_archived }));
                  if (r.ok) list.reload();
                }}
              >
                {p.is_archived ? "Восстановить" : "В архив"}
              </button>
            </div>
          </div>
        ),
      )}
    </div>
  );
}

interface SettingsData {
  min_shifts_per_week: number;
  schedule_visibility: "own" | "busy" | "full";
  daily_summary_time: string;
  invite_cooldown_hours: number;
  referral_bonus_kop: number;
  min_sample_units: number;
}

export function SettingsScreen() {
  const s = useLoad(() => api.get<SettingsData>("/settings"), []);
  const action = useAction();
  const [saved, setSaved] = useState(false);
  if (s.loading && !s.data) return <Loader />;
  if (!s.data) return <ErrorBox error={s.error} />;
  const v = s.data;
  const patch = async (changes: Partial<SettingsData>) => {
    setSaved(false);
    const r = await action.run(() => api.patch<SettingsData>("/settings", changes));
    if (r.ok) {
      s.setData(r.value);
      setSaved(true);
    }
  };
  return (
    <div>
      <h1>Настройки</h1>
      <ErrorBox error={action.error} />
      {saved && <div className="notice">Сохранено</div>}
      <Field label="Минимум смен в неделю">
        <select value={v.min_shifts_per_week} onChange={(e) => patch({ min_shifts_per_week: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </Field>
      <Field label="Видимость графика коллег для сотрудников">
        <select
          value={v.schedule_visibility}
          onChange={(e) => patch({ schedule_visibility: e.target.value as SettingsData["schedule_visibility"] })}
        >
          <option value="own">Только собственный график</option>
          <option value="busy">Занятость коллег</option>
          <option value="full">Подробный график коллег</option>
        </select>
      </Field>
      <Field label="Время дневной сводки фотоотчётов (МСК)">
        <input type="time" defaultValue={v.daily_summary_time} onBlur={(e) => e.target.value !== v.daily_summary_time && patch({ daily_summary_time: e.target.value })} />
      </Field>
      <Field label="Повторное приглашение в группу не раньше, чем через (часов)">
        <input
          type="number"
          min={0}
          defaultValue={v.invite_cooldown_hours}
          onBlur={(e) => Number(e.target.value) !== v.invite_cooldown_hours && patch({ invite_cooldown_hours: Number(e.target.value) })}
        />
      </Field>
      <Field label="Реферальный бонус после стажировки, ₽ (0 — не начислять)">
        <input
          type="number"
          min={0}
          defaultValue={v.referral_bonus_kop / 100}
          onBlur={(e) => {
            const kop = Math.round(Number(e.target.value) * 100);
            if (kop !== v.referral_bonus_kop) patch({ referral_bonus_kop: kop });
          }}
        />
      </Field>
      <Field label="Малая выборка для качества, единиц">
        <input
          type="number"
          min={1}
          defaultValue={v.min_sample_units}
          onBlur={(e) => Number(e.target.value) !== v.min_sample_units && patch({ min_sample_units: Number(e.target.value) })}
        />
      </Field>
    </div>
  );
}
