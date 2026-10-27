import { useState } from "react";
import { api, formatMsk } from "../api";
import { ErrorBox, Field, Loader, useAction, useLoad } from "../components/ui";

export interface CandidateData {
  id: number;
  status: "draft" | "submitted" | "reviewing" | "needs_info" | "approved" | "rejected" | "training";
  first_name: string | null;
  last_name: string | null;
  location: string | null;
  days_per_week: number | null;
  can_train: boolean | null;
  skills: Record<string, number>;
  truthful: boolean;
  pd_consent_version: string | null;
  pd_consent_at: string | null;
  submitted_at: string | null;
  reason: string | null;
  info_request: string | null;
  contact_status: string | null;
}

export interface Training {
  id: number;
  starts_at: string;
  place: string;
  comment: string | null;
  status: "scheduled" | "confirmed" | "cancelled";
  cancel_reason: string | null;
}

interface CandidateView {
  candidate: CandidateData | null;
  trainings: Training[];
  skills: string[];
  pd_consent_version: string;
  pd_consent_url: string;
  referred: boolean;
}

export const CANDIDATE_STATUS: Record<string, string> = {
  draft: "Черновик",
  submitted: "Отправлена",
  reviewing: "Рассматривается",
  needs_info: "Требуется уточнение",
  approved: "Одобрена",
  rejected: "Отклонена",
  training: "Обучение",
};

/** Анкета кандидата: открывается только по действительной реферальной ссылке. */
export function Welcome({ onDone }: { onDone: () => void }) {
  const v = useLoad(() => api.get<CandidateView>("/my/candidate"), []);
  if (v.loading && !v.data) return <Loader />;
  if (!v.data) return <ErrorBox error={v.error} onRetry={v.reload} />;
  return <CandidateForm view={v.data} onSaved={v.setData} onSubmitted={onDone} />;
}

function CandidateForm({
  view,
  onSaved,
  onSubmitted,
}: {
  view: CandidateView;
  onSaved: (v: CandidateView) => void;
  onSubmitted: () => void;
}) {
  const c = view.candidate;
  const [f, setF] = useState({
    last_name: c?.last_name ?? "",
    first_name: c?.first_name ?? "",
    location: c?.location ?? "",
    days_per_week: c?.days_per_week ?? 3,
    can_train: c?.can_train ?? true,
    skills: Object.fromEntries(view.skills.map((s) => [s, c?.skills[s] ?? 5])) as Record<string, number>,
    truthful: c?.truthful ?? false,
    pd_consent: !!c?.pd_consent_at,
  });
  const action = useAction();
  const save = () => api.put<CandidateView>("/my/candidate", f);

  return (
    <div>
      <h1>Анкета кандидата</h1>
      {c?.status === "needs_info" && <div className="notice">Нужно уточнить: {c.info_request}</div>}
      <ErrorBox error={action.error} />
      <Field label="Фамилия">
        <input value={f.last_name} onChange={(e) => setF({ ...f, last_name: e.target.value })} maxLength={64} />
      </Field>
      <Field label="Имя">
        <input value={f.first_name} onChange={(e) => setF({ ...f, first_name: e.target.value })} maxLength={64} />
      </Field>
      <Field label="Метро или район">
        <input value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} maxLength={200} />
      </Field>
      <Field label="Сколько дней в неделю готовы работать">
        <select value={f.days_per_week} onChange={(e) => setF({ ...f, days_per_week: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5, 6, 7].map((n) => (
            <option key={n}>{n}</option>
          ))}
        </select>
      </Field>
      <label className="check">
        <input type="checkbox" checked={f.can_train} onChange={(e) => setF({ ...f, can_train: e.target.checked })} />
        Могу пройти обучение
      </label>
      <h2>Оцените себя от 1 до 10</h2>
      {view.skills.map((s) => (
        <Field key={s} label={`${s}: ${f.skills[s]}`}>
          <input
            type="range"
            min={1}
            max={10}
            value={f.skills[s]}
            onChange={(e) => setF({ ...f, skills: { ...f.skills, [s]: Number(e.target.value) } })}
          />
        </Field>
      ))}
      <label className="check">
        <input type="checkbox" checked={f.truthful} onChange={(e) => setF({ ...f, truthful: e.target.checked })} />
        Подтверждаю, что данные достоверны
      </label>
      <label className="check">
        <input type="checkbox" checked={f.pd_consent} onChange={(e) => setF({ ...f, pd_consent: e.target.checked })} />
        <span>
          Согласен(на) на обработку персональных данных (версия {view.pd_consent_version})
          {view.pd_consent_url && (
            <>
              {" — "}
              <a href={view.pd_consent_url} target="_blank" rel="noreferrer">
                текст согласия
              </a>
            </>
          )}
        </span>
      </label>
      <div className="row">
        <button
          disabled={action.busy}
          onClick={async () => {
            const r = await action.run(save);
            if (r.ok) onSaved(r.value);
          }}
        >
          Сохранить черновик
        </button>
        <button
          className="primary"
          disabled={action.busy || !f.truthful || !f.pd_consent}
          onClick={async () => {
            const r = await action.run(async () => {
              await save();
              return api.post<CandidateView>("/my/candidate/submit");
            });
            if (r.ok) onSubmitted();
          }}
        >
          Отправить анкету
        </button>
      </div>
    </div>
  );
}

/** Статус отправленной анкеты: рассмотрение, контакт, обучение. */
export function CandidateStatusScreen({ onReload }: { onReload: () => void }) {
  const v = useLoad(() => api.get<CandidateView>("/my/candidate"), []);
  const action = useAction();
  if (v.loading && !v.data) return <Loader />;
  if (!v.data?.candidate) return <ErrorBox error={v.error} onRetry={v.reload} />;
  const c = v.data.candidate;
  if (c.status === "needs_info" || c.status === "draft")
    return <CandidateForm view={v.data} onSaved={v.setData} onSubmitted={() => { v.reload(); onReload(); }} />;
  return (
    <div>
      <h1>Анкета: {CANDIDATE_STATUS[c.status]}</h1>
      <div className="card kv">
        <span>ФИО</span>
        <span>
          {c.last_name} {c.first_name}
        </span>
        <span>Отправлена</span>
        <span>{c.submitted_at ? formatMsk(c.submitted_at) : "—"}</span>
      </div>
      {c.status === "rejected" && c.reason && <div className="notice">Причина: {c.reason}</div>}
      <ErrorBox error={action.error} />
      {c.status === "approved" && (
        <div className="card stack">
          <div>С вами свяжется ответственный или напишите ему сами по инструкции в боте.</div>
          {c.contact_status && (
            <div className="muted small">
              Вы отметили: {c.contact_status === "wrote" ? "«Я написал»" : "«Не могу связаться»"}
            </div>
          )}
          <div className="row">
            {(["wrote", "cannot_contact"] as const).map((st) => (
              <button
                key={st}
                disabled={action.busy}
                onClick={async () => {
                  const r = await action.run(() => api.post<CandidateView>("/my/candidate/contact", { status: st }));
                  if (r.ok) v.setData(r.value);
                }}
              >
                {st === "wrote" ? "Я написал" : "Не могу связаться"}
              </button>
            ))}
          </div>
        </div>
      )}
      {v.data.trainings.length > 0 && <h2>Обучение</h2>}
      {v.data.trainings.map((t) => (
        <div className="card stack" key={t.id}>
          <div>
            <b>{formatMsk(t.starts_at)}</b> · {t.place}
          </div>
          {t.comment && <div className="small">{t.comment}</div>}
          {t.status === "cancelled" ? (
            <div className="chip bad">Отменено{t.cancel_reason ? `: ${t.cancel_reason}` : ""}</div>
          ) : t.status === "confirmed" ? (
            <div className="chip ok">Вы подтвердили участие</div>
          ) : (
            <button
              className="primary"
              disabled={action.busy}
              onClick={async () => {
                const r = await action.run(() => api.post<CandidateView>(`/my/trainings/${t.id}/confirm`));
                if (r.ok) v.setData(r.value);
              }}
            >
              Подтвердить участие
            </button>
          )}
        </div>
      ))}
      <button onClick={() => { v.reload(); onReload(); }}>Обновить</button>
    </div>
  );
}
