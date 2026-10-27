import { useState } from "react";
import { api, Me } from "../api";
import { ErrorBox, Field, useAction } from "../components/ui";

/** ФИО владельца при первом входе. */
export function Register({ me, onDone }: { me: Me; onDone: () => void }) {
  return <NameForm me={me} onDone={onDone} endpoint="/registration" title="Ваше имя" submit="Сохранить" />;
}

/** Действующий сотрудник по приглашению: только фамилия и имя, затем сразу рабочий интерфейс. */
export function StaffRegister({ me, onDone }: { me: Me; onDone: () => void }) {
  return (
    <NameForm
      me={me}
      onDone={onDone}
      endpoint="/staff-registration"
      title="Регистрация сотрудника"
      hint="Вы приглашены как действующий сотрудник. Укажите настоящие фамилию и имя — после этого откроется рабочий интерфейс."
      submit="Зарегистрироваться"
    />
  );
}

function NameForm({
  me,
  onDone,
  endpoint,
  title,
  hint,
  submit,
}: {
  me: Me;
  onDone: () => void;
  endpoint: string;
  title: string;
  hint?: string;
  submit: string;
}) {
  const [last, setLast] = useState(me.user.last_name ?? me.user.tg_last_name ?? "");
  const [first, setFirst] = useState(me.user.first_name ?? me.user.tg_first_name ?? "");
  const { busy, error, run } = useAction();

  return (
    <div>
      <h1>{title}</h1>
      {hint && <p className="muted">{hint}</p>}
      <ErrorBox error={error} />
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          const r = await run(() => api.post(endpoint, { first_name: first, last_name: last }));
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
            {busy ? "Отправка…" : submit}
          </button>
        </div>
      </form>
    </div>
  );
}
