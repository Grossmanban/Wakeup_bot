import { useState } from "react";
import { api, parseRub, ProductListItem, Role, ROLE_TITLES, rub, UserShort } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";

interface BonusRule {
  id: number;
  name: string;
  amount_kop: number;
  conditions: { product_id: number; min_units: number }[];
  min_quality: number | null;
  period_from: string;
  period_to: string;
  is_active: boolean;
}

interface LazyRule {
  id: number;
  product_id: number;
  min_daily_units: number;
  reduced_rate_kop: number;
  roles: string[];
  user_ids: number[];
  period_from: string;
  period_to: string | null;
  is_active: boolean;
  exempt_user_ids: number[];
}

function today() {
  return new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" });
}

export function BonusRules() {
  const rules = useLoad(() => api.get<BonusRule[]>("/bonus-rules"), []);
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const [edit, setEdit] = useState<Partial<BonusRule> | null>(null);
  const action = useAction();
  const name = (id: number) => products.data?.find((p) => p.id === id)?.name ?? `#${id}`;

  const save = async () => {
    if (!edit) return;
    const kop = typeof edit.amount_kop === "number" ? edit.amount_kop : NaN;
    const body = { ...edit, amount_kop: kop };
    delete (body as { id?: number }).id;
    const r = await action.run(() =>
      edit.id ? api.put(`/bonus-rules/${edit.id}`, body) : api.post("/bonus-rules", body),
    );
    if (r.ok) {
      setEdit(null);
      rules.reload();
    }
  };

  return (
    <div>
      <h1>Автоматические бонусы</h1>
      <p className="muted small">
        Бонус начисляется один раз, когда за период выполнены минимумы по всем продуктам правила (по единицам,
        допущенным к оплате) и качество не ниже порога. Отключение правила не отменяет уже начисленные бонусы.
      </p>
      <ErrorBox error={rules.error ?? action.error} />
      {!edit && (
        <button
          className="primary"
          onClick={() =>
            setEdit({ name: "", amount_kop: 0, conditions: [], min_quality: null, period_from: today(), period_to: today(), is_active: true })
          }
        >
          + Правило
        </button>
      )}
      {edit && (
        <div className="card">
          <Field label="Название">
            <input value={edit.name ?? ""} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </Field>
          <Field label="Сумма, ₽">
            <input
              inputMode="decimal"
              defaultValue={edit.amount_kop ? String(edit.amount_kop / 100) : ""}
              onChange={(e) => setEdit({ ...edit, amount_kop: parseRub(e.target.value) ?? 0 })}
            />
          </Field>
          {(edit.conditions ?? []).map((c, i) => (
            <div className="row" key={i} style={{ flexWrap: "nowrap", marginBottom: 6 }}>
              <select
                value={c.product_id}
                onChange={(e) =>
                  setEdit({
                    ...edit,
                    conditions: edit.conditions!.map((x, j) => (j === i ? { ...x, product_id: Number(e.target.value) } : x)),
                  })
                }
              >
                {products.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min={1}
                placeholder="минимум"
                value={c.min_units}
                onChange={(e) =>
                  setEdit({
                    ...edit,
                    conditions: edit.conditions!.map((x, j) => (j === i ? { ...x, min_units: Number(e.target.value) } : x)),
                  })
                }
              />
              <button onClick={() => setEdit({ ...edit, conditions: edit.conditions!.filter((_, j) => j !== i) })}>✕</button>
            </div>
          ))}
          <button
            disabled={!products.data?.length}
            onClick={() =>
              setEdit({ ...edit, conditions: [...(edit.conditions ?? []), { product_id: products.data![0].id, min_units: 10 }] })
            }
          >
            + Условие по продукту
          </button>
          <Field label="Минимальное качество, % (пусто — без условия)">
            <input
              type="number"
              min={0}
              max={100}
              value={edit.min_quality ?? ""}
              onChange={(e) => setEdit({ ...edit, min_quality: e.target.value === "" ? null : Number(e.target.value) })}
            />
          </Field>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <Field label="С">
              <input type="date" value={edit.period_from} onChange={(e) => setEdit({ ...edit, period_from: e.target.value })} />
            </Field>
            <Field label="По">
              <input type="date" value={edit.period_to} onChange={(e) => setEdit({ ...edit, period_to: e.target.value })} />
            </Field>
          </div>
          <label className="check">
            <input type="checkbox" checked={edit.is_active} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} />
            Правило действует
          </label>
          <div className="row">
            <button className="primary" disabled={action.busy} onClick={save}>
              Сохранить
            </button>
            <button onClick={() => setEdit(null)}>Отмена</button>
          </div>
        </div>
      )}
      {rules.loading && !rules.data && <Loader />}
      {rules.data && rules.data.length === 0 && <Empty>Правил нет</Empty>}
      {rules.data?.map((r) => (
        <div className="card small" key={r.id}>
          <div className="row spread">
            <b>
              {r.name} · {rub(r.amount_kop)}
            </b>
            <span className={`chip ${r.is_active ? "ok" : ""}`}>{r.is_active ? "Действует" : "Отключено"}</span>
          </div>
          <div className="muted">
            {r.period_from} — {r.period_to} · {r.conditions.map((c) => `${name(c.product_id)} от ${c.min_units}`).join(", ")}
            {r.min_quality !== null ? ` · качество от ${r.min_quality}%` : ""}
          </div>
          <button style={{ marginTop: 6 }} onClick={() => setEdit(r)}>
            Изменить
          </button>
        </div>
      ))}
    </div>
  );
}

const LAZY_ROLES: Role[] = ["employee", "instructor", "coach", "admin"];

export function LazyRules() {
  const rules = useLoad(() => api.get<LazyRule[]>("/lazy-rules"), []);
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const people = useLoad(() => api.get<{ items: UserShort[] }>("/users?limit=200&status=active").then((r) => r.items).catch(() => []), []);
  const [edit, setEdit] = useState<Partial<LazyRule> & { rate?: string } | null>(null);
  const action = useAction();
  const pname = (id: number) => products.data?.find((p) => p.id === id)?.name ?? `#${id}`;
  const uname = (id: number) => people.data?.find((p) => p.id === id)?.full_name ?? `#${id}`;

  const save = async () => {
    if (!edit) return;
    const kop = parseRub(edit.rate ?? "");
    if (kop === null || Number.isNaN(kop)) {
      action.setError(new Error("Укажите сниженную ставку в рублях"));
      return;
    }
    const body = {
      product_id: edit.product_id,
      min_daily_units: edit.min_daily_units,
      reduced_rate_kop: kop,
      roles: edit.roles ?? [],
      user_ids: edit.user_ids ?? [],
      period_from: edit.period_from,
      period_to: edit.period_to || null,
      is_active: edit.is_active ?? true,
    };
    const r = await action.run(() => (edit.id ? api.put(`/lazy-rules/${edit.id}`, body) : api.post("/lazy-rules", body)));
    if (r.ok) {
      setEdit(null);
      rules.reload();
    }
  };

  return (
    <div>
      <h1>Налог на лень</h1>
      <p className="muted small">
        Если в рабочий день по продукту выполнено меньше минимума, недобор копится как единицы по сниженной ставке. При
        выплате они учитываются первыми, остаток переносится. Сотрудник «работал по продукту», если была подтверждённая
        смена с планом по нему или выполнена хотя бы одна единица.
      </p>
      <ErrorBox error={rules.error ?? action.error} />
      {!edit && (
        <button
          className="primary"
          disabled={!products.data?.length}
          onClick={() =>
            setEdit({ product_id: products.data![0].id, min_daily_units: 10, rate: "", roles: [], user_ids: [], period_from: today(), period_to: null, is_active: true })
          }
        >
          + Правило
        </button>
      )}
      {edit && (
        <div className="card">
          <Field label="Продукт">
            <select value={edit.product_id} onChange={(e) => setEdit({ ...edit, product_id: Number(e.target.value) })}>
              {products.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Минимальный дневной объём, единиц">
            <input type="number" min={1} value={edit.min_daily_units} onChange={(e) => setEdit({ ...edit, min_daily_units: Number(e.target.value) })} />
          </Field>
          <Field label="Сниженная ставка, ₽ за единицу">
            <input inputMode="decimal" value={edit.rate ?? ""} onChange={(e) => setEdit({ ...edit, rate: e.target.value })} />
          </Field>
          <div className="muted small">Роли (пусто — все)</div>
          <div className="row">
            {LAZY_ROLES.map((r) => (
              <label className="check" key={r}>
                <input
                  type="checkbox"
                  checked={(edit.roles ?? []).includes(r)}
                  onChange={(e) =>
                    setEdit({ ...edit, roles: e.target.checked ? [...(edit.roles ?? []), r] : (edit.roles ?? []).filter((x) => x !== r) })
                  }
                />
                {ROLE_TITLES[r]}
              </label>
            ))}
          </div>
          {people.data && people.data.length > 0 && (
            <Field label="Конкретные сотрудники (дополнительно к ролям)">
              <select
                multiple
                value={(edit.user_ids ?? []).map(String)}
                onChange={(e) => setEdit({ ...edit, user_ids: [...e.target.selectedOptions].map((o) => Number(o.value)) })}
                style={{ minHeight: 100 }}
              >
                {people.data.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.full_name}
                  </option>
                ))}
              </select>
            </Field>
          )}
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <Field label="С">
              <input type="date" value={edit.period_from} onChange={(e) => setEdit({ ...edit, period_from: e.target.value })} />
            </Field>
            <Field label="По (пусто — бессрочно)">
              <input type="date" value={edit.period_to ?? ""} onChange={(e) => setEdit({ ...edit, period_to: e.target.value || null })} />
            </Field>
          </div>
          <label className="check">
            <input type="checkbox" checked={edit.is_active ?? true} onChange={(e) => setEdit({ ...edit, is_active: e.target.checked })} />
            Правило включено
          </label>
          <div className="row">
            <button className="primary" disabled={action.busy} onClick={save}>
              Сохранить
            </button>
            <button onClick={() => setEdit(null)}>Отмена</button>
          </div>
        </div>
      )}
      {rules.data && rules.data.length === 0 && <Empty>Правил нет</Empty>}
      {rules.data?.map((r) => (
        <div className="card small" key={r.id}>
          <div className="row spread">
            <b>
              {pname(r.product_id)}: минимум {r.min_daily_units} в день, ставка {rub(r.reduced_rate_kop)}
            </b>
            <span className={`chip ${r.is_active ? "ok" : ""}`}>{r.is_active ? "Включено" : "Выключено"}</span>
          </div>
          <div className="muted">
            {r.period_from} — {r.period_to ?? "бессрочно"} ·{" "}
            {r.roles.length || r.user_ids.length
              ? [...r.roles.map((x) => ROLE_TITLES[x as Role]), ...r.user_ids.map(uname)].join(", ")
              : "все"}
          </div>
          {r.exempt_user_ids.length > 0 && <div className="muted">Исключения: {r.exempt_user_ids.map(uname).join(", ")}</div>}
          <ExemptionEditor rule={r} people={people.data ?? []} onChanged={rules.reload} />
          <button style={{ marginTop: 6 }} onClick={() => setEdit({ ...r, rate: String(r.reduced_rate_kop / 100) })}>
            Изменить
          </button>
        </div>
      ))}
    </div>
  );
}

function ExemptionEditor({ rule, people, onChanged }: { rule: LazyRule; people: UserShort[]; onChanged: () => void }) {
  const [user, setUser] = useState("");
  const action = useAction();
  if (!people.length) return null;
  const exempt = new Set(rule.exempt_user_ids);
  return (
    <div className="row" style={{ flexWrap: "nowrap", marginTop: 6 }}>
      <select value={user} onChange={(e) => setUser(e.target.value)}>
        <option value="">Персональное исключение…</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>
            {exempt.has(p.id) ? "✓ " : ""}
            {p.full_name}
          </option>
        ))}
      </select>
      <button
        disabled={!user || action.busy}
        onClick={async () => {
          const uid = Number(user);
          const r = await action.run(() => api.post(`/lazy-rules/${rule.id}/exemptions`, { user_id: uid, on: !exempt.has(uid) }));
          if (r.ok) onChanged();
        }}
      >
        {user && exempt.has(Number(user)) ? "Убрать" : "Исключить"}
      </button>
    </div>
  );
}
