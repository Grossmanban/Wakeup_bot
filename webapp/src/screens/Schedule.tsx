import { useState } from "react";
import { api, Me, MyProduct, newIdempotencyKey, rub } from "../api";
import { Empty, ErrorBox, Field, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";

export interface WorkPoint {
  id: number;
  name: string;
  address: string | null;
  capacity: number | null;
  is_archived: boolean;
}

interface DayShift {
  id: number;
  start: string;
  end: string;
  point_id: number;
  point: string;
  status: "pending" | "confirmed";
  plans: { product_id: number; product: string; units: number }[];
  forecast_kop: number | null;
  forecast_problems: string[];
  can_edit_time: boolean;
  can_edit_plans: boolean;
}

interface Day {
  day: string;
  weekday: string;
  status: string;
  actual_units: number;
  shift: DayShift | null;
}

interface MySchedule {
  days: Day[];
  weeks: { start: string; confirmed_shifts: number; min_shifts: number }[];
  book_from: string;
  book_to: string;
  today: string;
}

export const DAY_STATUS: Record<string, { title: string; cls: string }> = {
  confirmed: { title: "Смена подтверждена", cls: "ok" },
  working_today: { title: "Работает сегодня", cls: "ok" },
  worked: { title: "Работал", cls: "ok" },
  no_show: { title: "Смена без выполненных заданий", cls: "bad" },
  day_off: { title: "Выходной", cls: "" },
  pending: { title: "Ожидает подтверждения", cls: "warn" },
  none: { title: "Статус не указан", cls: "" },
};

export function fmtDay(iso: string) {
  const [, m, d] = iso.split("-");
  return `${d}.${m}`;
}

export function Schedule({ me }: { me: Me }) {
  const s = useLoad(() => api.get<MySchedule>("/my/schedule"), []);
  const points = useLoad(() => api.get<WorkPoint[]>("/work-points"), []);
  const products = useLoad(() => api.get<MyProduct[]>("/my/products"), []);
  const [editing, setEditing] = useState<{ day: string; shift: DayShift | null; mode: "new" | "time" | "plans" } | null>(null);
  const [cancelling, setCancelling] = useState<DayShift | null>(null);
  const action = useAction();

  if (s.loading && !s.data) return <Loader />;
  if (!s.data) return <ErrorBox error={s.error} onRetry={s.reload} />;
  const data = s.data;
  const bookable = (d: string) => d >= data.book_from && d <= data.book_to;

  return (
    <div>
      <h1>График</h1>
      <ErrorBox error={action.error} />
      {data.weeks.map((w, wi) => (
        <section key={w.start}>
          <h2>
            {wi === 0 ? "Текущая неделя" : "Следующая неделя"}{" "}
            <span className={`chip ${w.confirmed_shifts >= w.min_shifts ? "ok" : "warn"}`}>
              смен {w.confirmed_shifts} из минимума {w.min_shifts}
            </span>
          </h2>
          {data.days.slice(wi * 7, wi * 7 + 7).map((d) => {
            const st = DAY_STATUS[d.status] ?? { title: d.status, cls: "" };
            const isToday = d.day === data.today;
            return (
              <div className="card stack" key={d.day} style={isToday ? { borderColor: "var(--accent)" } : undefined}>
                <div className="row spread">
                  <b>
                    {d.weekday}, {fmtDay(d.day)}
                    {isToday ? " · сегодня" : ""}
                  </b>
                  <span className={`chip ${st.cls}`}>{st.title}</span>
                </div>
                {d.shift && (
                  <>
                    <div className="small">
                      {d.shift.start}–{d.shift.end} · {d.shift.point}
                    </div>
                    {d.shift.plans.length > 0 && (
                      <div className="small muted">
                        План: {d.shift.plans.map((p) => `${p.product} ${p.units}`).join(", ")}
                        {d.shift.forecast_kop !== null && ` · прогноз ${rub(d.shift.forecast_kop)}`}
                      </div>
                    )}
                    {d.shift.forecast_problems.map((p, i) => (
                      <div key={i} className="small error">
                        {p}
                      </div>
                    ))}
                    {d.actual_units > 0 && <div className="small">Выполнено: {d.actual_units} ед.</div>}
                    <div className="row">
                      {d.shift.can_edit_time && (
                        <button onClick={() => setEditing({ day: d.day, shift: d.shift, mode: "time" })}>
                          Время и точка
                        </button>
                      )}
                      {d.shift.can_edit_plans && (
                        <button onClick={() => setEditing({ day: d.day, shift: d.shift, mode: "plans" })}>План</button>
                      )}
                      {d.shift.can_edit_time && (
                        <button className="danger" onClick={() => setCancelling(d.shift)}>
                          Отменить
                        </button>
                      )}
                    </div>
                  </>
                )}
                {!d.shift && bookable(d.day) && (
                  <div className="row">
                    <button className="primary" onClick={() => setEditing({ day: d.day, shift: null, mode: "new" })}>
                      Встать в график
                    </button>
                    <button
                      disabled={action.busy}
                      onClick={async () => {
                        const r = await action.run(() =>
                          api.post("/day-off", { day: d.day, on: d.status !== "day_off" }),
                        );
                        if (r.ok) s.reload();
                      }}
                    >
                      {d.status === "day_off" ? "Убрать выходной" : "Выходной"}
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </section>
      ))}
      <Colleagues from={data.days[0].day} to={data.days[13].day} />
      {editing && points.data && (
        <ShiftDialog
          me={me}
          day={editing.day}
          isToday={editing.day === data.today}
          mode={editing.mode}
          shift={editing.shift}
          points={points.data}
          products={products.data ?? []}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            s.reload();
          }}
        />
      )}
      {cancelling && (
        <ReasonDialog
          title="Отменить смену"
          confirmText="Отменить смену"
          danger
          onClose={() => setCancelling(null)}
          onConfirm={async (reason) => {
            await api.post(`/shifts/${cancelling.id}/cancel`, { reason: reason || null });
            s.reload();
          }}
        />
      )}
    </div>
  );
}

function ShiftDialog({
  day,
  isToday,
  mode,
  shift,
  points,
  products,
  onClose,
  onSaved,
}: {
  me: Me;
  day: string;
  isToday: boolean;
  mode: "new" | "time" | "plans";
  shift: DayShift | null;
  points: WorkPoint[];
  products: MyProduct[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [start, setStart] = useState(shift?.start ?? "10:00");
  const [end, setEnd] = useState(shift?.end ?? "18:00");
  const [point, setPoint] = useState<number>(shift?.point_id ?? points[0]?.id ?? 0);
  const [plans, setPlans] = useState<Record<number, string>>(() =>
    Object.fromEntries((shift?.plans ?? []).map((p) => [p.product_id, String(p.units)])),
  );
  const [step, setStep] = useState<"form" | "confirm">("form");
  const { busy, error, run } = useAction();
  const planList = Object.entries(plans)
    .map(([pid, u]) => ({ product_id: Number(pid), units: Number(u) || 0 }))
    .filter((p) => p.units > 0);
  const forecast = planList.reduce((sum, p) => {
    const rate = products.find((x) => x.id === p.product_id)?.rate_kop;
    return rate === null || rate === undefined ? sum : sum + rate * p.units;
  }, 0);
  const pointName = points.find((p) => p.id === point)?.name ?? "";

  const save = async () => {
    const r = await run(async () => {
      if (mode === "new") {
        await api.post("/shifts", { day, start, end, point_id: point, plans: planList }, { "Idempotency-Key": newIdempotencyKey() });
      } else if (mode === "time") {
        await api.patch(`/shifts/${shift!.id}`, { start, end, point_id: point });
      } else {
        await api.put(`/shifts/${shift!.id}/plans`, { plans: planList });
      }
    });
    if (r.ok) onSaved();
  };

  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>
          {mode === "new" ? "Встать в график" : mode === "time" ? "Время и точка" : "План"} · {fmtDay(day)}
        </h2>
        <ErrorBox error={error} />
        {points.length === 0 && <Empty>Рабочие точки ещё не созданы</Empty>}
        {step === "form" ? (
          <>
            {mode !== "plans" && (
              <>
                <div className="row" style={{ flexWrap: "nowrap" }}>
                  <Field label="Начало">
                    <input type="time" value={start} onChange={(e) => setStart(e.target.value)} />
                  </Field>
                  <Field label="Окончание">
                    <input type="time" value={end} onChange={(e) => setEnd(e.target.value)} />
                  </Field>
                </div>
                <Field label="Рабочая точка">
                  <select value={point} onChange={(e) => setPoint(Number(e.target.value))}>
                    {points.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                        {p.capacity ? ` (мест: ${p.capacity})` : ""}
                      </option>
                    ))}
                  </select>
                </Field>
              </>
            )}
            {mode !== "time" &&
              products.map((p) => (
                <Field key={p.id} label={`План: ${p.name}, единиц`}>
                  <input
                    type="number"
                    min={0}
                    value={plans[p.id] ?? ""}
                    onChange={(e) => setPlans({ ...plans, [p.id]: e.target.value })}
                  />
                </Field>
              ))}
            <div className="row">
              <button className="primary" disabled={!point} onClick={() => (mode === "new" ? setStep("confirm") : save())}>
                {mode === "new" ? "Далее" : busy ? "Сохранение…" : "Сохранить"}
              </button>
              <button onClick={onClose}>Отмена</button>
            </div>
          </>
        ) : (
          <>
            <div className="card kv">
              <span>Дата</span>
              <span>{fmtDay(day)}</span>
              <span>Время</span>
              <span>
                {start}–{end}
              </span>
              <span>Точка</span>
              <span>{pointName}</span>
              <span>План</span>
              <span>
                {planList.length
                  ? planList.map((p) => `${products.find((x) => x.id === p.product_id)?.name} ${p.units}`).join(", ")
                  : "—"}
              </span>
              <span>Прогноз</span>
              <span>{planList.length ? rub(forecast) : "—"}</span>
            </div>
            {isToday && (
              <div className="notice small">Смена на сегодня станет действующей после подтверждения руководителем.</div>
            )}
            <div className="row">
              <button className="primary" disabled={busy} onClick={save}>
                {busy ? "Сохранение…" : "Подтвердить"}
              </button>
              <button onClick={() => setStep("form")} disabled={busy}>
                Назад
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Colleagues({ from, to }: { from: string; to: string }) {
  const c = useLoad(
    () => api.get<{ mode: string; items: { day: string; full_name: string; start?: string; end?: string; point?: string }[] }>(
      `/schedule/colleagues?start=${from}&end=${to}`,
    ),
    [from, to],
  );
  if (!c.data || c.data.mode === "own") return null;
  return (
    <>
      <h2>Коллеги</h2>
      {c.data.items.length === 0 && <Empty>Смен коллег нет</Empty>}
      {c.data.items.map((it, i) => (
        <div className="small" key={i}>
          {fmtDay(it.day)} · {it.full_name}
          {it.start ? ` · ${it.start}–${it.end} · ${it.point}` : ""}
        </div>
      ))}
    </>
  );
}
