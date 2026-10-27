import { useState } from "react";
import { api, Me } from "../api";
import { ErrorBox, Field, useAction } from "../components/ui";

export function Register({ me, onDone, onCancel }: { me: Me; onDone: () => void; onCancel?: () => void }) {
  const reg = me.registration?.status === "pending" ? me.registration : null;
  const [last, setLast] = useState(reg?.last_name ?? me.user.last_name ?? me.user.tg_last_name ?? "");
  const [first, setFirst] = useState(reg?.first_name ?? me.user.first_name ?? me.user.tg_first_name ?? "");
  const { busy, error, run } = useAction();
  const isOwner = me.user.role === "owner";

  return (
    <div>
      <h1>{isOwner ? "Ваше имя" : reg ? "Изменить заявку" : "Регистрация"}</h1>
      {!isOwner && (
        <p className="muted">
          Укажите настоящие фамилию и имя. Заявку рассмотрит администрация, после одобрения откроется
          доступ к приложению.
        </p>
      )}
      <ErrorBox error={error} />
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => api.post("/registration", { first_name: first, last_name: last }));
          if (r.ok) onDone();
        }}
      >
        <Field label="Фамилия">
          <input value={last} onChange={(e) => setLast(e.target.value)} autoComplete="family-name" maxLength={64} required />
        </Field>
        <Field label="Имя">
          <input value={first} onChange={(e) => setFirst(e.target.value)} autoComplete="given-name" maxLength={64} required />
        </Field>
        <div className="row">
          <button className="primary" type="submit" disabled={busy || !first.trim() || !last.trim()}>
            {busy ? "Отправка…" : isOwner ? "Сохранить" : reg ? "Сохранить изменения" : "Отправить заявку"}
          </button>
          {onCancel && (
            <button type="button" onClick={onCancel} disabled={busy}>
              Отмена
            </button>
          )}
        </div>
      </form>
    </div>
  );
}
