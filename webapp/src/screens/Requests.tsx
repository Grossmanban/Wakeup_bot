import { useState } from "react";
import { api, formatMsk, Me, NameChange, Registration, UserShort } from "../api";
import { Empty, ErrorBox, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";

type RegItem = Registration & { user: UserShort };
type NameItem = NameChange & { user: UserShort };

export function Requests({ me }: { me: Me }) {
  const canReg = me.is_owner || me.permissions.includes("registrations.review");
  const canNames = me.is_owner || me.permissions.includes("name_changes.review");
  return (
    <div>
      {canReg && <Registrations />}
      {canNames && <NameChanges />}
    </div>
  );
}

function Registrations() {
  const { data, error, loading, reload } = useLoad(() => api.get<RegItem[]>("/registrations"), []);
  const [rejecting, setRejecting] = useState<RegItem | null>(null);
  const action = useAction();

  return (
    <section>
      <h1>Заявки на регистрацию</h1>
      <ErrorBox error={error} onRetry={reload} />
      <ErrorBox error={action.error} />
      {loading && !data && <Loader />}
      {data && data.length === 0 && <Empty>Новых заявок нет</Empty>}
      {data?.map((r) => (
        <div className="card stack" key={r.id}>
          <div>
            <b>
              {r.last_name} {r.first_name}
            </b>
            {r.user.username && <span className="muted"> @{r.user.username}</span>}
          </div>
          <div className="muted small">
            Telegram ID {r.user.telegram_id} · {formatMsk(r.created_at)}
          </div>
          <div className="row">
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                const res = await action.run(() => api.post(`/registrations/${r.id}/approve`));
                if (res.ok) reload();
              }}
            >
              Принять
            </button>
            <button className="danger" disabled={action.busy} onClick={() => setRejecting(r)}>
              Отклонить
            </button>
          </div>
        </div>
      ))}
      {rejecting && (
        <ReasonDialog
          title={`Отклонить заявку: ${rejecting.last_name} ${rejecting.first_name}`}
          confirmText="Отклонить"
          danger
          onClose={() => setRejecting(null)}
          onConfirm={async (reason) => {
            await api.post(`/registrations/${rejecting.id}/reject`, { reason: reason || null });
            reload();
          }}
        />
      )}
    </section>
  );
}

function NameChanges() {
  const { data, error, loading, reload } = useLoad(() => api.get<NameItem[]>("/name-changes"), []);
  const [rejecting, setRejecting] = useState<NameItem | null>(null);
  const action = useAction();

  return (
    <section>
      <h1>Смена ФИО</h1>
      <ErrorBox error={error} onRetry={reload} />
      <ErrorBox error={action.error} />
      {loading && !data && <Loader />}
      {data && data.length === 0 && <Empty>Заявок на смену ФИО нет</Empty>}
      {data?.map((r) => (
        <div className="card stack" key={r.id}>
          <div>
            {r.old_last_name} {r.old_first_name} → <b>{r.new_last_name} {r.new_first_name}</b>
          </div>
          <div className="muted small">{formatMsk(r.created_at)}</div>
          <div className="row">
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                const res = await action.run(() => api.post(`/name-changes/${r.id}/approve`));
                if (res.ok) reload();
              }}
            >
              Подтвердить
            </button>
            <button className="danger" disabled={action.busy} onClick={() => setRejecting(r)}>
              Отклонить
            </button>
          </div>
        </div>
      ))}
      {rejecting && (
        <ReasonDialog
          title="Отклонить смену ФИО"
          confirmText="Отклонить"
          danger
          onClose={() => setRejecting(null)}
          onConfirm={async (reason) => {
            await api.post(`/name-changes/${rejecting.id}/reject`, { reason: reason || null });
            reload();
          }}
        />
      )}
    </section>
  );
}
