import { useState } from "react";
import { api, formatMsk, Me, ROLE_TITLES } from "../api";
import { ErrorBox, Field, useAction } from "../components/ui";
import { getTheme, setTheme, ThemeChoice } from "../theme";
import { confirmAction } from "../telegram";
import { Referral } from "./CandidatesAdmin";

export function Profile({ me, onReload }: { me: Me; onReload: () => void }) {
  const [editing, setEditing] = useState(false);
  const [theme, setThemeState] = useState<ThemeChoice>(getTheme());
  const nc = me.name_change;
  const { busy, error, run } = useAction();

  return (
    <div>
      <h1>{me.user.full_name}</h1>
      {!me.has_product_access && me.product_access_message && (
        <div className="notice">{me.product_access_message}</div>
      )}
      <div className="card">
        <div className="kv">
          <span>Роль</span>
          <span>{ROLE_TITLES[me.user.role]}</span>
          <span>Telegram ID</span>
          <span>{me.user.telegram_id}</span>
          {me.user.username && (
            <>
              <span>Username</span>
              <span>@{me.user.username}</span>
            </>
          )}
        </div>
      </div>

      <h2>ФИО</h2>
      <ErrorBox error={error} />
      {nc?.status === "pending" ? (
        <div className="card stack">
          <div>
            Заявка на смену ФИО: <b>{nc.new_last_name} {nc.new_first_name}</b>
          </div>
          <div className="muted small">Ожидает подтверждения с {formatMsk(nc.created_at)}</div>
          <div className="row">
            <button onClick={() => setEditing(true)} disabled={busy}>Изменить</button>
            <button
              className="danger"
              disabled={busy}
              onClick={async () => {
                if (!(await confirmAction("Отменить заявку на смену ФИО?"))) return;
                const r = await run(() => api.del("/me/name-change"));
                if (r.ok) onReload();
              }}
            >
              Отменить заявку
            </button>
          </div>
        </div>
      ) : (
        <>
          {nc && nc.status === "rejected" && (
            <div className="card small">
              Последняя заявка на смену ФИО отклонена{nc.reason ? `: ${nc.reason}` : ""}
            </div>
          )}
          {!editing && (
            <button onClick={() => setEditing(true)}>
              {me.is_owner ? "Изменить ФИО" : "Запросить смену ФИО"}
            </button>
          )}
        </>
      )}
      {editing && <NameChangeForm me={me} onDone={() => { setEditing(false); onReload(); }} onCancel={() => setEditing(false)} />}

      {(me.is_owner || me.permissions.includes("referrals.use")) && <Referral />}

      <h2>Тема</h2>
      <div className="row">
        {(["system", "light", "dark"] as ThemeChoice[]).map((t) => (
          <button
            key={t}
            className={theme === t ? "primary" : ""}
            onClick={() => {
              setTheme(t);
              setThemeState(t);
            }}
          >
            {t === "system" ? "Как в Telegram" : t === "light" ? "Светлая" : "Тёмная"}
          </button>
        ))}
      </div>
    </div>
  );
}

function NameChangeForm({ me, onDone, onCancel }: { me: Me; onDone: () => void; onCancel: () => void }) {
  const pending = me.name_change?.status === "pending" ? me.name_change : null;
  const [last, setLast] = useState(pending?.new_last_name ?? me.user.last_name ?? "");
  const [first, setFirst] = useState(pending?.new_first_name ?? me.user.first_name ?? "");
  const { busy, error, run } = useAction();
  return (
    <form
      className="card"
      onSubmit={async (e) => {
        e.preventDefault();
        const r = await run(() => api.post("/me/name-change", { first_name: first, last_name: last }));
        if (r.ok) onDone();
      }}
    >
      {!me.is_owner && <p className="muted small" style={{ marginTop: 0 }}>Новое ФИО вступит в силу после подтверждения администрацией.</p>}
      <ErrorBox error={error} />
      <Field label="Фамилия">
        <input value={last} onChange={(e) => setLast(e.target.value)} maxLength={64} required />
      </Field>
      <Field label="Имя">
        <input value={first} onChange={(e) => setFirst(e.target.value)} maxLength={64} required />
      </Field>
      <div className="row">
        <button className="primary" type="submit" disabled={busy}>
          {busy ? "Отправка…" : me.is_owner ? "Сохранить" : "Отправить"}
        </button>
        <button type="button" onClick={onCancel} disabled={busy}>
          Отмена
        </button>
      </div>
    </form>
  );
}
