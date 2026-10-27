import { useState } from "react";
import { api, formatMsk, Me, Network, ProductListItem, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";
import { confirmAction } from "../telegram";

interface ResultRow {
  user_id: number;
  full_name: string;
  units: number;
  reached_at: string | null;
  done: boolean;
  place: number;
  prize: string | null;
}

interface Goal {
  id: number;
  title: string;
  kind: "goal" | "competition";
  scope: "individual" | "team";
  participants_spec: { type: string; ids: number[] };
  product_ids: number[];
  target_units: number | null;
  period_from: string;
  period_to: string;
  deadline_type: "period" | "deadline";
  visibility: "participants" | "all" | "managers";
  counting: "approved" | "completed";
  freeze_participants: boolean;
  mode: "race" | "max" | null;
  ranking: "individual" | "total" | "average" | "min_each";
  prizes: { place: number; prize: string }[];
  exclusions: number[];
  tie_break: "earliest" | "shared";
  status: string;
  is_participant?: boolean;
  my?: ResultRow | null;
  aggregate: { participants: number; total_units: number; average_units: number; min_units: number; team_progress?: number; team_done?: boolean };
  rows?: ResultRow[];
}

function Progress({ value, target }: { value: number; target: number | null }) {
  if (!target) return <span>{value} ед.</span>;
  const pct = Math.min(100, Math.round((value * 100) / target));
  return (
    <div>
      <div className="small">
        {value} из {target} ({pct}%)
      </div>
      <div style={{ background: "var(--chip)", borderRadius: 6, height: 8 }}>
        <div style={{ width: `${pct}%`, background: "var(--accent)", height: 8, borderRadius: 6 }} />
      </div>
    </div>
  );
}

export function GoalsList({ me }: { me: Me }) {
  const nav = useNav();
  const list = useLoad(() => api.get<Goal[]>("/goals"), []);
  if (list.loading && !list.data) return <Loader />;
  if (!list.data) return <ErrorBox error={list.error} onRetry={list.reload} />;
  const manager = me.is_owner || me.permissions.includes("goals.manage");
  const items = manager ? list.data.filter((g) => g.is_participant || g.visibility === "all") : list.data;
  if (!items.length) return null;
  return (
    <>
      <h1>Цели и соревнования</h1>
      {items.map((g) => (
        <button
          className="list-item"
          key={g.id}
          onClick={() => nav.push({ type: "custom", key: `goal-${g.id}`, render: () => <GoalView id={g.id} /> })}
        >
          <div className="row spread">
            <b>{g.title}</b>
            <span className="chip">{g.kind === "competition" ? "Соревнование" : "Цель"}</span>
          </div>
          <div className="muted small">
            до {g.period_to}
            {g.my ? ` · место ${g.my.place}` : ""}
          </div>
          {g.scope === "team" ? (
            <Progress value={g.aggregate.team_progress ?? g.aggregate.total_units} target={g.target_units} />
          ) : (
            g.my && <Progress value={g.my.units} target={g.target_units} />
          )}
        </button>
      ))}
    </>
  );
}

export function GoalView({ id }: { id: number }) {
  const g = useLoad(() => api.get<Goal>(`/goals/${id}`), [id]);
  if (g.loading && !g.data) return <Loader />;
  if (!g.data) return <ErrorBox error={g.error} onRetry={g.reload} />;
  const d = g.data;
  return (
    <div>
      <h1>{d.title}</h1>
      <div className="card kv">
        <span>Период</span>
        <span>
          {d.period_from} — {d.period_to}
        </span>
        <span>Учёт</span>
        <span>{d.counting === "approved" ? "Допущенные к оплате единицы" : "Выполненные единицы"}</span>
        {d.target_units && (
          <>
            <span>{d.kind === "competition" && d.mode === "race" ? "Порог" : "Цель"}</span>
            <span>{d.target_units} ед.</span>
          </>
        )}
        <span>Участников</span>
        <span>{d.aggregate.participants}</span>
        <span>Всего</span>
        <span>{d.aggregate.total_units} ед.</span>
        <span>В среднем</span>
        <span>{d.aggregate.average_units} ед.</span>
        <span>Минимум</span>
        <span>{d.aggregate.min_units} ед.</span>
      </div>
      {d.scope === "team" && d.target_units && (
        <Progress value={d.aggregate.team_progress ?? 0} target={d.target_units} />
      )}
      {d.prizes.length > 0 && (
        <div className="card small">
          {d.prizes.map((p) => (
            <div key={p.place}>
              {p.place} место — {p.prize}
            </div>
          ))}
        </div>
      )}
      <h2>Рейтинг</h2>
      {d.rows?.map((r) => (
        <div className="card small" key={r.user_id}>
          <div className="row spread">
            <b>
              {r.place}. {r.full_name}
            </b>
            <span>{r.units} ед.</span>
          </div>
          {r.reached_at && <div className="muted">порог достигнут {formatMsk(r.reached_at)}</div>}
          {r.prize && <div className="chip ok">{r.prize}</div>}
        </div>
      ))}
    </div>
  );
}

const SPEC_TITLES: Record<string, string> = {
  users: "Сотрудники",
  network: "Сеть",
  coach_team: "Команда тренера",
  instructor_team: "Команда инструктора",
};

export function GoalsAdmin() {
  const nav = useNav();
  const list = useLoad(() => api.get<Goal[]>("/goals"), []);
  const [edit, setEdit] = useState<Partial<Goal> | null>(null);
  const action = useAction();
  return (
    <div>
      <h1>Цели и соревнования</h1>
      <ErrorBox error={list.error ?? action.error} />
      {!edit && (
        <button className="primary" onClick={() => setEdit(blankGoal())}>
          + Создать
        </button>
      )}
      {edit && (
        <GoalEditor
          goal={edit}
          onCancel={() => setEdit(null)}
          onSaved={() => {
            setEdit(null);
            list.reload();
          }}
        />
      )}
      {list.data && list.data.length === 0 && <Empty>Целей пока нет</Empty>}
      {list.data?.map((g) => (
        <div className="card small" key={g.id}>
          <div className="row spread">
            <b>{g.title}</b>
            <span className="chip">{g.status}</span>
          </div>
          <div className="muted">
            {g.period_from} — {g.period_to} · участников {g.aggregate.participants} · всего {g.aggregate.total_units} ед.
          </div>
          <div className="row" style={{ marginTop: 6 }}>
            <button onClick={() => nav.push({ type: "custom", key: `goal-${g.id}`, render: () => <GoalView id={g.id} /> })}>
              Результаты
            </button>
            <button onClick={() => setEdit(g)}>Изменить</button>
            {(["active", "hidden", "archived", "deleted"] as const)
              .filter((s) => s !== g.status)
              .map((s) => (
                <button
                  key={s}
                  className={s === "deleted" ? "danger" : ""}
                  onClick={async () => {
                    if (s === "deleted" && !(await confirmAction("Удалить цель? История сохранится."))) return;
                    const r = await action.run(() => api.put(`/goals/${g.id}/status`, { status: s }));
                    if (r.ok) list.reload();
                  }}
                >
                  {{ active: "Показать", hidden: "Скрыть", archived: "В архив", deleted: "Удалить" }[s]}
                </button>
              ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function blankGoal(): Partial<Goal> {
  const today = new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
  return {
    title: "",
    kind: "goal",
    scope: "individual",
    participants_spec: { type: "users", ids: [] },
    product_ids: [],
    target_units: 10,
    period_from: today,
    period_to: today,
    deadline_type: "period",
    visibility: "participants",
    counting: "approved",
    freeze_participants: true,
    mode: null,
    ranking: "individual",
    prizes: [],
    exclusions: [],
    tie_break: "earliest",
  };
}

function GoalEditor({ goal, onCancel, onSaved }: { goal: Partial<Goal>; onCancel: () => void; onSaved: () => void }) {
  const [g, setG] = useState<Partial<Goal>>(goal);
  const products = useLoad(() => api.get<ProductListItem[]>("/products").catch(() => []), []);
  const networks = useLoad(() => api.get<Network[]>("/networks").catch(() => []), []);
  const people = useLoad(() => api.get<{ items: UserShort[] }>("/users?limit=200&status=active").then((r) => r.items).catch(() => []), []);
  const action = useAction();
  const spec = g.participants_spec!;
  const options =
    spec.type === "network"
      ? (networks.data ?? []).map((n) => ({ id: n.id, name: n.name }))
      : (people.data ?? [])
          .filter((p) => spec.type === "users" || p.role === (spec.type === "coach_team" ? "coach" : "instructor"))
          .map((p) => ({ id: p.id, name: p.full_name }));
  const multi = (label: string, value: number[], opts: { id: number; name: string }[], onChange: (v: number[]) => void) => (
    <Field label={label}>
      <select multiple value={value.map(String)} style={{ minHeight: 110 }} onChange={(e) => onChange([...e.target.selectedOptions].map((o) => Number(o.value)))}>
        {opts.map((o) => (
          <option key={o.id} value={o.id}>
            {o.name}
          </option>
        ))}
      </select>
    </Field>
  );
  return (
    <div className="card">
      <ErrorBox error={action.error} />
      <Field label="Название">
        <input value={g.title} onChange={(e) => setG({ ...g, title: e.target.value })} />
      </Field>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <Field label="Тип">
          <select value={g.kind} onChange={(e) => setG({ ...g, kind: e.target.value as Goal["kind"], mode: e.target.value === "competition" ? "race" : null })}>
            <option value="goal">Цель</option>
            <option value="competition">Соревнование</option>
          </select>
        </Field>
        <Field label="Формат">
          <select value={g.scope} onChange={(e) => setG({ ...g, scope: e.target.value as Goal["scope"] })}>
            <option value="individual">Индивидуальная</option>
            <option value="team">Командная</option>
          </select>
        </Field>
      </div>
      {g.kind === "competition" && (
        <Field label="Режим">
          <select value={g.mode ?? "race"} onChange={(e) => setG({ ...g, mode: e.target.value as Goal["mode"] })}>
            <option value="race">Гонка до порога</option>
            <option value="max">Максимум за период</option>
          </select>
        </Field>
      )}
      {(g.kind === "goal" || g.mode === "race" || g.scope === "team") && (
        <Field label={g.mode === "race" ? "Порог, единиц" : "Объём, единиц"}>
          <input type="number" min={1} value={g.target_units ?? ""} onChange={(e) => setG({ ...g, target_units: e.target.value ? Number(e.target.value) : null })} />
        </Field>
      )}
      {g.scope === "team" && (
        <Field label="Показатель команды">
          <select value={g.ranking} onChange={(e) => setG({ ...g, ranking: e.target.value as Goal["ranking"] })}>
            <option value="total">Общий объём</option>
            <option value="average">Средний результат на человека</option>
            <option value="min_each">Минимум каждого участника</option>
          </select>
        </Field>
      )}
      <Field label="Участники">
        <select value={spec.type} onChange={(e) => setG({ ...g, participants_spec: { type: e.target.value, ids: [] } })}>
          {Object.entries(SPEC_TITLES).map(([k, v]) => (
            <option key={k} value={k}>
              {v}
            </option>
          ))}
        </select>
      </Field>
      {multi("Выбор (несколько — с Ctrl/долгим нажатием)", spec.ids, options, (ids) => setG({ ...g, participants_spec: { ...spec, ids } }))}
      {multi("Продукты", g.product_ids ?? [], (products.data ?? []).map((p) => ({ id: p.id, name: p.name })), (ids) => setG({ ...g, product_ids: ids }))}
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <Field label="С">
          <input type="date" value={g.period_from} onChange={(e) => setG({ ...g, period_from: e.target.value })} />
        </Field>
        <Field label="По">
          <input type="date" value={g.period_to} onChange={(e) => setG({ ...g, period_to: e.target.value })} />
        </Field>
      </div>
      <div className="row" style={{ flexWrap: "nowrap" }}>
        <Field label="Учитывать">
          <select value={g.counting} onChange={(e) => setG({ ...g, counting: e.target.value as Goal["counting"] })}>
            <option value="approved">Допущенные к оплате</option>
            <option value="completed">Все выполненные</option>
          </select>
        </Field>
        <Field label="Видимость">
          <select value={g.visibility} onChange={(e) => setG({ ...g, visibility: e.target.value as Goal["visibility"] })}>
            <option value="participants">Участникам</option>
            <option value="all">Всем</option>
            <option value="managers">Только руководителям</option>
          </select>
        </Field>
      </div>
      {g.kind === "competition" && (
        <>
          <Field label="Призы (по строке: «место — приз»)">
            <textarea
              defaultValue={(g.prizes ?? []).map((p) => `${p.place} — ${p.prize}`).join("\n")}
              onChange={(e) =>
                setG({
                  ...g,
                  prizes: e.target.value
                    .split("\n")
                    .map((l) => l.match(/^\s*(\d+)\s*[—-]\s*(.+)$/))
                    .filter(Boolean)
                    .map((m) => ({ place: Number(m![1]), prize: m![2].trim() })),
                })
              }
            />
          </Field>
          <Field label="Ничья">
            <select value={g.tie_break} onChange={(e) => setG({ ...g, tie_break: e.target.value as Goal["tie_break"] })}>
              <option value="earliest">Выше тот, кто достиг результата раньше</option>
              <option value="shared">Общее место</option>
            </select>
          </Field>
          {multi("Исключения", g.exclusions ?? [], (people.data ?? []).map((p) => ({ id: p.id, name: p.full_name })), (ids) => setG({ ...g, exclusions: ids }))}
        </>
      )}
      <label className="check">
        <input type="checkbox" checked={g.freeze_participants} onChange={(e) => setG({ ...g, freeze_participants: e.target.checked })} />
        Зафиксировать состав участников на старте
      </label>
      <div className="row">
        <button
          className="primary"
          disabled={action.busy}
          onClick={async () => {
            const body = { ...g };
            for (const k of ["id", "status", "created_at", "is_participant", "my", "aggregate", "rows"]) delete (body as Record<string, unknown>)[k];
            const r = await action.run(() => (g.id ? api.put(`/goals/${g.id}`, body) : api.post("/goals", body)));
            if (r.ok) onSaved();
          }}
        >
          Сохранить
        </button>
        <button onClick={onCancel}>Отмена</button>
      </div>
    </div>
  );
}
