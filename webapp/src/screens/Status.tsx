import { useState } from "react";
import { formatMsk, Me } from "../api";
import { Register } from "./Register";

/** Экраны до допуска к работе: заявка на рассмотрении, отказ, отключение, блокировка. */
export function StatusScreen({ me, onReload }: { me: Me; onReload: () => void }) {
  const [editing, setEditing] = useState(false);
  const reg = me.registration;

  if (editing) {
    return (
      <Register
        me={me}
        onDone={() => {
          setEditing(false);
          onReload();
        }}
        onCancel={() => setEditing(false)}
      />
    );
  }

  if (me.screen === "pending" && reg) {
    return (
      <div>
        <h1>Заявка отправлена</h1>
        <div className="card">
          <div className="kv">
            <span>ФИО</span>
            <span>
              {reg.last_name} {reg.first_name}
            </span>
            <span>Отправлена</span>
            <span>{formatMsk(reg.created_at)}</span>
            <span>Статус</span>
            <span>Ожидает рассмотрения</span>
          </div>
        </div>
        <p className="muted">Мы сообщим о решении в чате с ботом.</p>
        <div className="row">
          <button onClick={() => setEditing(true)}>Исправить данные</button>
          <button onClick={onReload}>Обновить</button>
        </div>
      </div>
    );
  }

  if (me.screen === "rejected") {
    return (
      <div>
        <h1>Заявка отклонена</h1>
        {reg?.reason && <div className="card">Причина: {reg.reason}</div>}
        <button className="primary" onClick={() => setEditing(true)}>
          Подать заявку снова
        </button>
      </div>
    );
  }

  // disabled / blocked / no_app_access
  return (
    <div className="center">
      <h1>{me.message}</h1>
      {me.user.status_reason && <p className="muted">Причина: {me.user.status_reason}</p>}
    </div>
  );
}
