import { useState } from "react";
import { api, formatMsk, ProductListItem } from "../api";
import { Empty, ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";
import { TgGroup } from "./Groups";

interface Route {
  id: number;
  kind: "product" | "general" | "summary" | "extra";
  product_id: number | null;
  group_id: number;
  is_active: boolean;
}

interface Overview {
  routes: Route[];
  stats: Record<string, number | Record<string, { photos: number; units: number }>>;
  deliveries: { id: number; photo_id: number; group: string; status: string; error: string | null; sent_at: string | null }[];
  posts: { id: number; group: string; kind: string; version: number; status: string; reason: string | null }[];
}

const KIND: Record<string, string> = {
  product: "Группа продукта",
  general: "Общая группа (все фотоотчёты)",
  summary: "Группа ежедневной сводки",
  extra: "Дополнительный маршрут",
};

export function PhotoReports() {
  const [day, setDay] = useState(() => new Date().toLocaleDateString("sv-SE", { timeZone: "Europe/Moscow" }));
  const ov = useLoad(() => api.get<Overview>(`/photoreports?day=${day}`), [day]);
  const groups = useLoad(() => api.get<TgGroup[]>("/groups"), []);
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const [form, setForm] = useState({ kind: "product", product: "", group: "" });
  const action = useAction();
  const gname = (id: number) => groups.data?.find((g) => g.id === id)?.title ?? `#${id}`;
  const pname = (id: number | null) => (id ? products.data?.find((p) => p.id === id)?.name ?? `#${id}` : "все продукты");
  if (ov.loading && !ov.data) return <Loader />;
  if (!ov.data) return <ErrorBox error={ov.error} onRetry={ov.reload} />;
  const missing = (products.data ?? []).filter(
    (p) => p.status === "active" && !ov.data!.routes.some((r) => r.kind === "product" && r.product_id === p.id && r.is_active),
  );
  const stats = ov.data.stats;
  return (
    <div>
      <h1>Telegram-фотоотчёты</h1>
      <p className="muted small">
        Фото всегда сохраняются в системе. В группы уходят копии с подписью; «Отправлено» — значит, Telegram подтвердил
        отправку. Проверить права бота и отправить тестовое сообщение можно в разделе «Telegram-группы».
      </p>
      <ErrorBox error={action.error} />
      {missing.length > 0 && (
        <div className="error small">Нет группы для продуктов: {missing.map((p) => p.name).join(", ")}</div>
      )}
      <h2>Маршруты</h2>
      {ov.data.routes.length === 0 && <Empty>Маршрутов нет</Empty>}
      {ov.data.routes.map((r) => (
        <div className="card small row spread" key={r.id}>
          <span>
            <b>{KIND[r.kind]}</b>
            {(r.kind === "product" || r.kind === "extra") && ` · ${pname(r.product_id)}`} → {gname(r.group_id)}
          </span>
          <span className="row">
            <button
              onClick={async () => {
                const res = await action.run(() =>
                  api.post("/photoreports/routes", { kind: r.kind, product_id: r.product_id, group_id: r.group_id, is_active: !r.is_active }),
                );
                if (res.ok) ov.reload();
              }}
            >
              {r.is_active ? "Выключить" : "Включить"}
            </button>
            <button
              className="danger"
              onClick={async () => {
                const res = await action.run(() => api.del(`/photoreports/routes/${r.id}`));
                if (res.ok) ov.reload();
              }}
            >
              ✕
            </button>
          </span>
        </div>
      ))}
      <div className="card">
        <Field label="Вид">
          <select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
            {Object.entries(KIND).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </Field>
        {(form.kind === "product" || form.kind === "extra") && (
          <Field label="Продукт">
            <select value={form.product} onChange={(e) => setForm({ ...form, product: e.target.value })}>
              {form.kind === "extra" && <option value="">Все продукты</option>}
              {form.kind === "product" && <option value="">—</option>}
              {products.data?.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="Группа">
          <select value={form.group} onChange={(e) => setForm({ ...form, group: e.target.value })}>
            <option value="">—</option>
            {groups.data?.map((g) => (
              <option key={g.id} value={g.id}>
                {g.title}
                {g.status !== "connected" ? " (бот не в группе)" : ""}
              </option>
            ))}
          </select>
        </Field>
        <button
          className="primary"
          disabled={!form.group || (form.kind === "product" && !form.product) || action.busy}
          onClick={async () => {
            const res = await action.run(() =>
              api.post("/photoreports/routes", {
                kind: form.kind,
                product_id: form.product ? Number(form.product) : null,
                group_id: Number(form.group),
              }),
            );
            if (res.ok) {
              setForm({ ...form, product: "", group: "" });
              ov.reload();
            }
          }}
        >
          Сохранить маршрут
        </button>
      </div>

      <h2>День</h2>
      <input type="date" value={day} onChange={(e) => setDay(e.target.value)} style={{ marginBottom: 8 }} />
      <div className="card kv small">
        {(
          [
            ["photos", "Фотоотчётов"],
            ["employees", "Сотрудников"],
            ["products", "Продуктов"],
            ["unique_tasks", "Уникальных заданий"],
            ["units", "Выполненных единиц"],
            ["pending", "Ожидают модерации"],
            ["approved", "Допущено к оплате"],
            ["rejected", "Отклонено"],
            ["delivery_errors", "Ошибок отправки"],
          ] as const
        ).map(([k, t]) => (
          <span key={k} style={{ display: "contents" }}>
            <span>{t}</span>
            <span>{stats[k] as number}</span>
          </span>
        ))}
      </div>
      {ov.data.posts.map((p) => (
        <div className="small muted" key={p.id}>
          {p.kind === "header" ? "Заголовок" : `Сводка v${p.version}`} · {p.group} · {p.status === "sent" ? "отправлено" : p.status === "failed" ? "ошибка" : "в очереди"}
          {p.reason ? ` · ${p.reason}` : ""}
        </div>
      ))}
      <h2>Отправки</h2>
      {ov.data.deliveries.length === 0 && <Empty>За этот день отправок нет</Empty>}
      {ov.data.deliveries.map((d) => (
        <div className="card small" key={d.id}>
          <div className="row spread">
            <span>
              Фото #{d.photo_id} → {d.group}
            </span>
            <span className={`chip ${d.status === "sent" ? "ok" : d.status === "failed" ? "bad" : "warn"}`}>
              {d.status === "sent" ? "Отправлено" : d.status === "failed" ? "Ошибка" : "В очереди"}
            </span>
          </div>
          {d.sent_at && <div className="muted">{formatMsk(d.sent_at)}</div>}
          {d.error && <div className="error">{d.error}</div>}
          {d.status === "failed" && (
            <button
              onClick={async () => {
                const res = await action.run(() => api.post(`/photoreports/deliveries/${d.id}/retry`));
                if (res.ok) ov.reload();
              }}
            >
              Отправить повторно
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
