import { useEffect, useState } from "react";
import {
  api,
  FlowStage,
  LinkItem,
  Me,
  Network,
  parseRub,
  Product,
  PRODUCT_STATUS_TITLES,
  ProductStatus,
  Rates,
  Role,
  ROLE_TITLES,
  rub,
  TaskType,
} from "../api";
import { ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { confirmAction } from "../telegram";

const REQUIRED_FIELDS: Record<string, string> = {
  link: "Ссылка",
  expected_text: "Ожидаемый текст",
  rating: "Рейтинг",
  instructions: "Инструкция",
  comment: "Комментарий",
  materials: "Материалы",
  gender: "Пол",
};

export function ProductEditor({ me, id }: { me: Me; id: number }) {
  const product = useLoad(() => api.get<Product>(`/products/${id}`), [id]);
  if (product.loading && !product.data) return <Loader />;
  if (product.error || !product.data) return <ErrorBox error={product.error} onRetry={product.reload} />;
  const p = product.data;
  const owner = me.is_owner;
  return (
    <div>
      <h1>{p.name}</h1>
      {owner ? (
        <>
          <StatusBlock product={p} onChanged={product.reload} />
          <SettingsForm product={p} onSaved={product.reload} />
          <TaskTypesEditor productId={p.id} />
          <RatesEditor productId={p.id} />
          <NetworksEditor productId={p.id} />
        </>
      ) : (
        <ReadOnly product={p} />
      )}
    </div>
  );
}

function ReadOnly({ product }: { product: Product }) {
  return (
    <div className="card kv">
      <span>Статус</span>
      <span>{PRODUCT_STATUS_TITLES[product.status]}</span>
      <span>Лимит активных</span>
      <span>{product.active_task_limit}</span>
      <span>Этапов</span>
      <span>{product.flow.length}</span>
      <span>OCR</span>
      <span>{product.ocr_enabled ? "Включена" : "Выключена"}</span>
    </div>
  );
}

function StatusBlock({ product, onChanged }: { product: Product; onChanged: () => void }) {
  const action = useAction();
  const set = async (status: ProductStatus, question: string) => {
    if (!(await confirmAction(question))) return;
    const r = await action.run(() => api.put(`/products/${product.id}/status`, { status }));
    if (r.ok) onChanged();
  };
  return (
    <div className="card stack">
      <div>
        Статус: <b>{PRODUCT_STATUS_TITLES[product.status]}</b>
      </div>
      <ErrorBox error={action.error} />
      <div className="row">
        {product.status !== "active" && (
          <button className="primary" onClick={() => set("active", "Сделать продукт активным?")}>
            {product.status === "deleted" ? "Восстановить" : "Активировать"}
          </button>
        )}
        {product.status === "active" && (
          <button onClick={() => set("hidden", "Скрыть продукт? Новые задания выдаваться не будут.")}>Скрыть</button>
        )}
        {product.status !== "archived" && product.status !== "deleted" && (
          <button onClick={() => set("archived", "Отправить продукт в архив?")}>В архив</button>
        )}
        {product.status !== "deleted" && (
          <button className="danger" onClick={() => set("deleted", "Удалить продукт? История и задания сохранятся.")}>
            Удалить
          </button>
        )}
      </div>
    </div>
  );
}

type Draft = Omit<Product, "id" | "status" | "created_at">;

function SettingsForm({ product, onSaved }: { product: Product; onSaved: () => void }) {
  const [d, setD] = useState<Draft>(() => strip(product));
  useEffect(() => setD(strip(product)), [product]);
  const action = useAction();
  const [saved, setSaved] = useState(false);
  const upd = (patch: Partial<Draft>) => {
    setSaved(false);
    setD((prev) => ({ ...prev, ...patch }));
  };

  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        const r = await action.run(() => api.patch(`/products/${product.id}`, d));
        if (r.ok) {
          setSaved(true);
          onSaved();
        }
      }}
    >
      <h2>Основное</h2>
      <Field label="Название">
        <input value={d.name} onChange={(e) => upd({ name: e.target.value })} maxLength={128} />
      </Field>
      <Field label="Описание">
        <textarea value={d.description ?? ""} onChange={(e) => upd({ description: e.target.value || null })} />
      </Field>
      <Field label="Лимит активных заданий на сотрудника">
        <input
          type="number"
          min={1}
          max={100}
          value={d.active_task_limit}
          onChange={(e) => upd({ active_task_limit: Number(e.target.value) })}
        />
      </Field>
      <Field label="Ограничение по полу">
        <select value={d.gender ?? ""} onChange={(e) => upd({ gender: (e.target.value || null) as Draft["gender"] })}>
          <option value="">Нет</option>
          <option value="m">Только мужчины</option>
          <option value="f">Только женщины</option>
        </select>
      </Field>
      <Field label="Инструкция">
        <textarea value={d.instructions ?? ""} onChange={(e) => upd({ instructions: e.target.value || null })} />
      </Field>
      <LinksEditor title="Ссылки" items={d.links} onChange={(links) => upd({ links })} />
      <LinksEditor title="Материалы" items={d.materials} onChange={(materials) => upd({ materials })} />

      <h2>Импорт из Excel</h2>
      <div className="muted small">Обязательные поля строки</div>
      <div className="row">
        {Object.entries(REQUIRED_FIELDS).map(([code, title]) => (
          <label className="check" key={code}>
            <input
              type="checkbox"
              checked={d.required_fields.includes(code)}
              onChange={(e) =>
                upd({
                  required_fields: e.target.checked
                    ? [...d.required_fields, code]
                    : d.required_fields.filter((x) => x !== code),
                })
              }
            />
            {title}
          </label>
        ))}
      </div>
      <Field label="Ожидаемый текст по умолчанию (если в файле пусто)">
        <input
          value={d.expected_text_default ?? ""}
          onChange={(e) => upd({ expected_text_default: e.target.value || null })}
        />
      </Field>

      <h2>Выполнение</h2>
      <FlowEditor flow={d.flow} onChange={(flow) => upd({ flow })} />
      <label className="check">
        <input
          type="checkbox"
          checked={d.final_photo_required}
          onChange={(e) => upd({ final_photo_required: e.target.checked })}
        />
        Обязательное финальное фото
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={d.timer_reset_reason_required}
          onChange={(e) => upd({ timer_reset_reason_required: e.target.checked })}
        />
        При сбросе таймера нужна причина
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={d.allow_return_for_fix}
          onChange={(e) => upd({ allow_return_for_fix: e.target.checked })}
        />
        Модератор может вернуть задание на исправление
      </label>
      <Field label="Причины ошибок (по одной в строке)">
        <textarea
          value={d.error_reasons.join("\n")}
          onChange={(e) => upd({ error_reasons: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })}
        />
      </Field>

      <h2>Проверка фото (OCR)</h2>
      <label className="check">
        <input type="checkbox" checked={d.ocr_enabled} onChange={(e) => upd({ ocr_enabled: e.target.checked })} />
        Проверять текст на финальном фото
      </label>
      {d.ocr_enabled && (
        <>
          <Field label="Правило">
            <select value={d.ocr_rule} onChange={(e) => upd({ ocr_rule: e.target.value as Draft["ocr_rule"] })}>
              <option value="contains">Ожидаемый текст содержится на фото</option>
              <option value="exact">Текст совпадает целиком</option>
              <option value="fuzzy">Похожесть не ниже порога</option>
            </select>
          </Field>
          {d.ocr_rule === "fuzzy" && (
            <Field label="Порог похожести, %">
              <input
                type="number"
                min={50}
                max={100}
                value={d.ocr_threshold}
                onChange={(e) => upd({ ocr_threshold: Number(e.target.value) })}
              />
            </Field>
          )}
          <Field label="Количество попыток">
            <input
              type="number"
              min={1}
              max={10}
              value={d.ocr_attempts}
              onChange={(e) => upd({ ocr_attempts: Number(e.target.value) })}
            />
          </Field>
        </>
      )}

      <h2>Стажировка и статистика</h2>
      <Field label="Объём стажировки, единиц (0 — без стажировки)">
        <input
          type="number"
          min={0}
          value={d.training_volume}
          onChange={(e) => upd({ training_volume: Number(e.target.value) })}
        />
      </Field>
      <Field label="Минимальная выборка для оценки качества, единиц">
        <input
          type="number"
          min={1}
          value={d.min_sample_units}
          onChange={(e) => upd({ min_sample_units: Number(e.target.value) })}
        />
      </Field>

      <ErrorBox error={action.error} />
      {saved && <div className="notice">Сохранено</div>}
      <button className="primary block" type="submit" disabled={action.busy}>
        {action.busy ? "Сохранение…" : "Сохранить настройки"}
      </button>
    </form>
  );
}

function strip(p: Product): Draft {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { id, status, created_at, ...rest } = p;
  return rest;
}

function LinksEditor({ title, items, onChange }: { title: string; items: LinkItem[]; onChange: (v: LinkItem[]) => void }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div className="muted small">{title}</div>
      {items.map((l, i) => (
        <div className="row" key={i} style={{ flexWrap: "nowrap", marginBottom: 6 }}>
          <input
            placeholder="Название"
            value={l.title}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)))}
          />
          <input
            placeholder="https://"
            value={l.url}
            onChange={(e) => onChange(items.map((x, j) => (j === i ? { ...x, url: e.target.value } : x)))}
          />
          <button type="button" onClick={() => onChange(items.filter((_, j) => j !== i))}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" onClick={() => onChange([...items, { title: "", url: "" }])}>
        + Добавить
      </button>
    </div>
  );
}

function FlowEditor({ flow, onChange }: { flow: FlowStage[]; onChange: (v: FlowStage[]) => void }) {
  const set = (i: number, patch: Partial<FlowStage>) => onChange(flow.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const move = (i: number, dir: -1 | 1) => {
    const j = i + dir;
    if (j < 0 || j >= flow.length) return;
    const next = [...flow];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div>
      <p className="muted small">
        После «Найдено» сотрудник проходит этапы по порядку. «Далее» доступно, когда отмечен чек-лист, прошло
        минимальное время и загружено обязательное фото.
      </p>
      {flow.map((s, i) => (
        <div className="card" key={i}>
          <div className="row spread">
            <b>Этап {i + 1}</b>
            <span className="row">
              <button type="button" onClick={() => move(i, -1)} disabled={i === 0}>
                ↑
              </button>
              <button type="button" onClick={() => move(i, 1)} disabled={i === flow.length - 1}>
                ↓
              </button>
              <button type="button" className="danger" onClick={() => onChange(flow.filter((_, j) => j !== i))}>
                Удалить
              </button>
            </span>
          </div>
          <Field label="Название этапа">
            <input value={s.title} onChange={(e) => set(i, { title: e.target.value })} maxLength={100} />
          </Field>
          <Field label="Подсказка">
            <textarea value={s.instructions ?? ""} onChange={(e) => set(i, { instructions: e.target.value || null })} />
          </Field>
          <Field label="Чек-лист (по пункту в строке)">
            <textarea
              value={s.checklist.join("\n")}
              onChange={(e) => set(i, { checklist: e.target.value.split("\n").map((x) => x.trim()).filter(Boolean) })}
            />
          </Field>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <Field label="Минимум секунд на этапе">
              <input
                type="number"
                min={0}
                value={s.min_seconds}
                onChange={(e) => set(i, { min_seconds: Number(e.target.value) })}
              />
            </Field>
            <Field label="Фото на этапе">
              <select value={s.photo} onChange={(e) => set(i, { photo: e.target.value as FlowStage["photo"] })}>
                <option value="none">Не нужно</option>
                <option value="optional">По желанию</option>
                <option value="required">Обязательно</option>
              </select>
            </Field>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() => onChange([...flow, { title: `Этап ${flow.length + 1}`, checklist: [], min_seconds: 0, photo: "none" }])}
      >
        + Добавить этап
      </button>
    </div>
  );
}

function TaskTypesEditor({ productId }: { productId: number }) {
  const types = useLoad(() => api.get<TaskType[]>(`/products/${productId}/task-types`), [productId]);
  const [draft, setDraft] = useState<TaskType[] | null>(null);
  useEffect(() => setDraft(types.data), [types.data]);
  const action = useAction();
  if (!draft) return <Loader />;
  const set = (i: number, patch: Partial<TaskType>) => setDraft(draft.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  return (
    <>
      <h2>Типы заданий</h2>
      <p className="muted small">
        «Названия в Excel» — как тип записан в колонке «Тип» файла (через запятую). Если тип один, колонку можно не
        заполнять.
      </p>
      <ErrorBox error={types.error ?? action.error} />
      {draft.map((t, i) => (
        <div className="card" key={t.id ?? `new-${i}`}>
          <div className="row" style={{ flexWrap: "nowrap" }}>
            <Field label="Название">
              <input value={t.name} onChange={(e) => set(i, { name: e.target.value })} />
            </Field>
            <Field label="Единиц за задание">
              <input type="number" min={1} value={t.units} onChange={(e) => set(i, { units: Number(e.target.value) })} />
            </Field>
          </div>
          <Field label="Названия в Excel">
            <input
              value={t.excel_names.join(", ")}
              onChange={(e) => set(i, { excel_names: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) })}
            />
          </Field>
          <label className="check">
            <input type="checkbox" checked={t.is_active} onChange={(e) => set(i, { is_active: e.target.checked })} />
            Используется
          </label>
        </div>
      ))}
      <div className="row">
        <button type="button" onClick={() => setDraft([...draft, { name: "", excel_names: [], units: 1, is_active: true }])}>
          + Тип
        </button>
        <button
          className="primary"
          disabled={action.busy}
          onClick={async () => {
            const r = await action.run(() =>
              api.put<TaskType[]>(`/products/${productId}/task-types`, draft.filter((t) => t.name.trim())),
            );
            if (r.ok) setDraft(r.value);
          }}
        >
          Сохранить типы
        </button>
      </div>
    </>
  );
}

const RATE_ROLES: Role[] = ["employee", "instructor", "coach", "admin"];

function RatesEditor({ productId }: { productId: number }) {
  const rates = useLoad(() => api.get<Rates>(`/products/${productId}/rates`), [productId]);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const action = useAction();
  if (rates.loading && !rates.data) return <Loader />;
  if (!rates.data) return <ErrorBox error={rates.error} />;
  const r = rates.data;
  const save = async (role: Role) => {
    const raw = inputs[role] ?? "";
    const kop = parseRub(raw);
    if (Number.isNaN(kop)) {
      action.setError(new Error("Ставка: число рублей, например 50 или 12,5"));
      return;
    }
    if (kop === null && !(await confirmAction(`Снять ставку для роли «${ROLE_TITLES[role]}»?`))) return;
    const res = await action.run(() => api.put<Rates>(`/products/${productId}/rates/roles/${role}`, { amount_kop: kop }));
    if (res.ok) {
      rates.setData(res.value);
      setInputs((x) => ({ ...x, [role]: "" }));
    }
  };
  return (
    <>
      <h2>Ставки за единицу</h2>
      <p className="muted small">
        Сначала действует индивидуальная ставка сотрудника (задаётся в его карточке), затем ставка роли. Без ставки
        задания не выдаются.
      </p>
      <ErrorBox error={action.error} />
      {RATE_ROLES.map((role) => (
        <div className="row" key={role} style={{ flexWrap: "nowrap", marginBottom: 6 }}>
          <span style={{ minWidth: 120 }}>{ROLE_TITLES[role]}</span>
          <span style={{ minWidth: 80 }}>{r.roles[role] !== undefined ? rub(r.roles[role]) : "не задана"}</span>
          <input
            placeholder="₽"
            inputMode="decimal"
            value={inputs[role] ?? ""}
            onChange={(e) => setInputs((x) => ({ ...x, [role]: e.target.value }))}
          />
          <button disabled={action.busy} onClick={() => save(role)}>
            OK
          </button>
        </div>
      ))}
      {r.users.length > 0 && (
        <div className="card small">
          <b>Индивидуальные ставки</b>
          {r.users.map((u) => (
            <div key={u.user_id}>
              {u.full_name}: {rub(u.amount_kop)}
            </div>
          ))}
        </div>
      )}
      {r.history.length > 0 && (
        <details className="small">
          <summary>История ставок</summary>
          {r.history.map((h) => (
            <div key={h.id} className="muted">
              {new Date(h.created_at).toLocaleString("ru-RU", { timeZone: "Europe/Moscow" })} —{" "}
              {h.role ? ROLE_TITLES[h.role] : h.user_name}: {h.amount_kop === null ? "снята" : rub(h.amount_kop)}
            </div>
          ))}
        </details>
      )}
    </>
  );
}

function NetworksEditor({ productId }: { productId: number }) {
  const networks = useLoad(() => api.get<Network[]>("/networks"), []);
  const current = useLoad(() => api.get<number[]>(`/products/${productId}/networks`), [productId]);
  const [sel, setSel] = useState<Set<number> | null>(null);
  useEffect(() => {
    if (current.data) setSel(new Set(current.data));
  }, [current.data]);
  const action = useAction();
  if (!networks.data || !sel) return null;
  if (networks.data.length === 0) return null;
  return (
    <>
      <h2>Сети</h2>
      <p className="muted small">Если ничего не отмечено — задания доступны сотрудникам всех сетей.</p>
      <ErrorBox error={action.error} />
      <div className="row">
        {networks.data.map((n) => (
          <label className="check" key={n.id}>
            <input
              type="checkbox"
              checked={sel.has(n.id)}
              onChange={() => {
                const s = new Set(sel);
                if (s.has(n.id)) s.delete(n.id);
                else s.add(n.id);
                setSel(s);
              }}
            />
            {n.name}
          </label>
        ))}
      </div>
      <button
        disabled={action.busy}
        onClick={async () => {
          const r = await action.run(() => api.put<number[]>(`/products/${productId}/networks`, { ids: [...sel] }));
          if (r.ok) current.setData(r.value);
        }}
      >
        Сохранить сети
      </button>
    </>
  );
}
