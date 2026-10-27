import { useState } from "react";
import { api, Me, PRODUCT_STATUS_TITLES, Product, ProductListItem } from "../api";
import { Empty, ErrorBox, Loader, useAction, useLoad } from "../components/ui";
import { useNav } from "../nav";

export function Products({ me }: { me: Me }) {
  const nav = useNav();
  const [showDeleted, setShowDeleted] = useState(false);
  const list = useLoad(
    () => api.get<ProductListItem[]>(`/products?include_deleted=${showDeleted}`),
    [showDeleted],
  );
  const [name, setName] = useState("");
  const action = useAction();

  return (
    <div>
      <h1>Продукты</h1>
      {me.is_owner && (
        <form
          className="row"
          style={{ flexWrap: "nowrap", marginBottom: 12 }}
          onSubmit={async (e) => {
            e.preventDefault();
            const r = await action.run(() => api.post<Product>("/products", { name }));
            if (r.ok) {
              setName("");
              nav.push({ type: "product", id: r.value.id });
            }
          }}
        >
          <input placeholder="Название нового продукта" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" type="submit" disabled={action.busy || !name.trim()}>
            Создать
          </button>
        </form>
      )}
      <ErrorBox error={action.error} />
      <label className="check">
        <input type="checkbox" checked={showDeleted} onChange={(e) => setShowDeleted(e.target.checked)} />
        Показывать удалённые
      </label>
      <ErrorBox error={list.error} onRetry={list.reload} />
      {list.loading && !list.data && <Loader />}
      {list.data && list.data.length === 0 && <Empty>Продуктов пока нет</Empty>}
      {list.data?.map((p) => (
        <button className="list-item" key={p.id} onClick={() => nav.push({ type: "product", id: p.id })}>
          <div className="row spread">
            <b>{p.name}</b>
            <span className={`chip ${p.status === "active" ? "ok" : "warn"}`}>{PRODUCT_STATUS_TITLES[p.status]}</span>
          </div>
          <div className="muted small">
            В пуле {p.pool} · активных {p.active} · на модерации {p.on_moderation}
          </div>
        </button>
      ))}
    </div>
  );
}
