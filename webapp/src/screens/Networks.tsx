import { useState } from "react";
import { api, Network } from "../api";
import { Empty, ErrorBox, Loader, useAction, useLoad } from "../components/ui";
import { confirmAction } from "../telegram";

export function Networks() {
  const [archived, setArchived] = useState(false);
  const { data, error, loading, reload } = useLoad(
    () => api.get<Network[]>(`/networks?include_archived=${archived}`),
    [archived],
  );
  const [name, setName] = useState("");
  const [editing, setEditing] = useState<{ id: number; name: string } | null>(null);
  const action = useAction();

  return (
    <div>
      <h1>Сети</h1>
      <form
        className="row"
        style={{ flexWrap: "nowrap", marginBottom: 12 }}
        onSubmit={async (e) => {
          e.preventDefault();
          const r = await action.run(() => api.post("/networks", { name }));
          if (r.ok) {
            setName("");
            reload();
          }
        }}
      >
        <input placeholder="Название новой сети" value={name} onChange={(e) => setName(e.target.value)} maxLength={128} />
        <button className="primary" type="submit" disabled={action.busy || !name.trim()}>
          Создать
        </button>
      </form>
      <label className="check">
        <input type="checkbox" checked={archived} onChange={(e) => setArchived(e.target.checked)} />
        Показывать архивные
      </label>
      <ErrorBox error={error} onRetry={reload} />
      <ErrorBox error={action.error} />
      {loading && !data && <Loader />}
      {data && data.length === 0 && <Empty>Сетей пока нет</Empty>}
      {data?.map((n) => (
        <div className="card" key={n.id}>
          {editing?.id === n.id ? (
            <form
              className="row"
              style={{ flexWrap: "nowrap" }}
              onSubmit={async (e) => {
                e.preventDefault();
                const r = await action.run(() => api.patch(`/networks/${n.id}`, { name: editing.name }));
                if (r.ok) {
                  setEditing(null);
                  reload();
                }
              }}
            >
              <input value={editing.name} onChange={(e) => setEditing({ id: n.id, name: e.target.value })} maxLength={128} />
              <button className="primary" type="submit" disabled={action.busy}>
                OK
              </button>
              <button type="button" onClick={() => setEditing(null)}>
                Отмена
              </button>
            </form>
          ) : (
            <div className="row spread">
              <span>
                {n.name} {n.is_archived && <span className="chip">архив</span>}
              </span>
              <span className="row">
                <button onClick={() => setEditing({ id: n.id, name: n.name })}>Переименовать</button>
                <button
                  className={n.is_archived ? "" : "danger"}
                  disabled={action.busy}
                  onClick={async () => {
                    const q = n.is_archived ? `Восстановить сеть «${n.name}»?` : `Отправить сеть «${n.name}» в архив?`;
                    if (!(await confirmAction(q))) return;
                    const r = await action.run(() => api.patch(`/networks/${n.id}`, { is_archived: !n.is_archived }));
                    if (r.ok) reload();
                  }}
                >
                  {n.is_archived ? "Восстановить" : "В архив"}
                </button>
              </span>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
