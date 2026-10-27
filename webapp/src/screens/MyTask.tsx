import { useEffect, useState } from "react";
import { api, ApiError, FlowStage, formatMsk, LinkItem, Me, Task, TASK_STATUS_TITLES } from "../api";
import { Camera, AuthImage } from "../components/photo";
import { ErrorBox, Field, Loader, ReasonDialog, useAction, useLoad } from "../components/ui";
import { initData, openExternal } from "../telegram";

export interface MyTaskProduct {
  id: number;
  name: string;
  instructions: string | null;
  links: LinkItem[];
  materials: LinkItem[];
  flow: FlowStage[];
  final_photo_required: boolean;
  timer_reset_reason_required: boolean;
  error_reasons: string[];
  ocr_enabled: boolean;
  ocr_attempts: number;
}

export interface TaskPhoto {
  id: number;
  kind: "stage" | "final";
  stage: number | null;
  uploaded_at: string;
  ocr_status: "passed" | "failed" | "error" | null;
  ocr_text: string | null;
  ocr_detail: string | null;
  ocr_attempt: number | null;
}

export interface MyTaskData {
  task: Task;
  product: MyTaskProduct;
  stage_state: { stage: number; total: number; seconds_left: number };
  photos: TaskPhoto[];
  ocr_attempts_left: number;
  uploaded?: TaskPhoto;
}

async function uploadPhoto(taskId: number, blob: Blob, kind: "stage" | "final", stage?: number) {
  const form = new FormData();
  form.append("file", blob, "photo.jpg");
  form.append("kind", kind);
  if (stage !== undefined) form.append("stage", String(stage));
  let res: Response;
  try {
    res = await fetch(`/api/my/tasks/${taskId}/photos`, {
      method: "POST",
      headers: { Authorization: `tma ${initData()}` },
      body: form,
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером — фото не загружено. Повторите");
  }
  const data = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, data?.code ?? "error", data?.message ?? "Фото не загружено");
  return data as MyTaskData;
}

export function MyTask({ id }: { me: Me; id: number }) {
  const d = useLoad(() => api.get<MyTaskData>(`/my/tasks/${id}`), [id]);
  const action = useAction();
  const [camera, setCamera] = useState<{ kind: "stage" | "final"; stage?: number } | null>(null);
  const [dialog, setDialog] = useState<"reset" | "error" | null>(null);
  const [lastOcr, setLastOcr] = useState<TaskPhoto | null>(null);

  if (d.loading && !d.data) return <Loader />;
  if (!d.data) return <ErrorBox error={d.error} onRetry={d.reload} />;
  const { task: t, product: p, stage_state: st } = d.data;
  const act = async (path: string, body?: unknown) => {
    const r = await action.run(() => api.post<MyTaskData>(`/my/tasks/${t.id}/${path}`, body));
    if (r.ok) d.setData(r.value);
    return r;
  };
  const active = t.status === "active";
  const allStagesDone = t.found_at !== null && t.current_stage >= p.flow.length;
  const finals = d.data.photos.filter((x) => x.kind === "final");
  const needFinal = p.final_photo_required || p.ocr_enabled;
  const finalOk = !needFinal || (finals.length > 0 && (!p.ocr_enabled || finals[finals.length - 1].ocr_status === "passed"));

  return (
    <div>
      <h1>
        {p.name} · {t.external_id}
      </h1>
      {t.status === "manual_review" && (
        <div className="notice">
          Задание ожидает решения администрации{t.manual_review_reason ? `: ${t.manual_review_reason}` : ""}. Мы
          сообщим в боте.
        </div>
      )}
      {t.moderation_reason && active && <div className="notice">Нужно исправить: {t.moderation_reason}</div>}
      {!active && t.status !== "manual_review" && (
        <div className="notice">
          {TASK_STATUS_TITLES[t.status]}
          {t.completed_at ? ` · завершено ${formatMsk(t.completed_at)}` : ""}
          {t.status === "rejected" && t.moderation_reason ? `. Причина: ${t.moderation_reason}` : ""}
        </div>
      )}
      <TaskInfo task={t} product={p} />
      <ErrorBox error={action.error} />

      {active && (
        <>
          {t.link && (
            <button
              className={t.opened_at ? "block" : "primary block"}
              style={{ marginBottom: 8 }}
              disabled={action.busy}
              onClick={async () => {
                openExternal(t.link!);
                if (!t.opened_at) await act("open");
              }}
            >
              {t.opened_at ? "Открыть ссылку ещё раз" : "1. Открыть ссылку"}
            </button>
          )}
          {!t.found_at && (
            <button
              className="primary block"
              disabled={action.busy || (!!t.link && !t.opened_at)}
              onClick={() => act("found")}
            >
              {t.link ? "2. Найдено" : "Найдено"}
            </button>
          )}

          {t.found_at && !allStagesDone && (
            <StageCard
              key={t.current_stage}
              index={t.current_stage}
              stage={p.flow[t.current_stage]}
              total={p.flow.length}
              secondsLeft={st.seconds_left}
              photos={d.data.photos.filter((x) => x.kind === "stage" && x.stage === t.current_stage)}
              busy={action.busy}
              onPhoto={() => setCamera({ kind: "stage", stage: t.current_stage })}
              onNext={(checklist) => act(`stages/${t.current_stage}/complete`, { checklist })}
              onTimerSync={d.reload}
            />
          )}

          {allStagesDone && (
            <div className="card stack">
              <b>Завершение</b>
              {needFinal && (
                <>
                  {p.ocr_enabled && (
                    <div className="muted small">
                      Фото проверяется автоматически. Осталось попыток: {d.data.ocr_attempts_left} из {p.ocr_attempts}
                    </div>
                  )}
                  {finals.length > 0 && <PhotoResult photo={lastOcr ?? finals[finals.length - 1]} ocr={p.ocr_enabled} />}
                  {!finalOk && (
                    <button className="primary" disabled={action.busy} onClick={() => setCamera({ kind: "final" })}>
                      {finals.length ? "Переснять финальное фото" : "Сделать финальное фото"}
                    </button>
                  )}
                </>
              )}
              <button
                className="primary"
                disabled={action.busy || !finalOk}
                onClick={async () => {
                  await act("complete");
                }}
              >
                Завершить задание
              </button>
            </div>
          )}

          <div className="row" style={{ marginTop: 12 }}>
            {t.found_at && !allStagesDone && (
              <button disabled={action.busy} onClick={() => setDialog("reset")}>
                Сбросить таймер
              </button>
            )}
            <button className="danger" disabled={action.busy} onClick={() => setDialog("error")}>
              Сообщить об ошибке
            </button>
          </div>
        </>
      )}

      {d.data.photos.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary>Фото задания ({d.data.photos.length})</summary>
          {d.data.photos.map((ph) => (
            <div className="card small" key={ph.id}>
              <div className="muted">
                {ph.kind === "final" ? "Финальное" : `Этап ${(ph.stage ?? 0) + 1}`} · {formatMsk(ph.uploaded_at)}
              </div>
              <AuthImage id={ph.id} />
              {ph.ocr_status && <PhotoResult photo={ph} ocr />}
            </div>
          ))}
        </details>
      )}

      {camera && (
        <Camera
          title={camera.kind === "final" ? "Финальное фото" : `Фото этапа ${(camera.stage ?? 0) + 1}`}
          onClose={() => setCamera(null)}
          onUpload={async (blob) => {
            const r = await uploadPhoto(t.id, blob, camera.kind, camera.stage);
            d.setData(r);
            if (r.uploaded) setLastOcr(r.uploaded);
            return true;
          }}
        />
      )}
      {dialog === "reset" && (
        <ReasonDialog
          title="Сбросить таймер этапа"
          confirmText="Сбросить"
          reasonRequired={p.timer_reset_reason_required}
          reasonLabel={p.timer_reset_reason_required ? "Причина" : "Причина (необязательно)"}
          onClose={() => setDialog(null)}
          onConfirm={async (reason) => {
            const r = await api.post<MyTaskData>(`/my/tasks/${t.id}/timer-reset`, { reason: reason || null });
            d.setData(r);
          }}
        />
      )}
      {dialog === "error" && (
        <ErrorDialog
          reasons={p.error_reasons}
          onClose={() => setDialog(null)}
          onSubmit={async (reason, comment) => {
            const r = await api.post<MyTaskData>(`/my/tasks/${t.id}/report-error`, { reason, comment });
            d.setData(r);
          }}
        />
      )}
    </div>
  );
}

function StageCard({
  index,
  stage,
  total,
  secondsLeft,
  photos,
  busy,
  onPhoto,
  onNext,
  onTimerSync,
}: {
  index: number;
  stage: FlowStage;
  total: number;
  secondsLeft: number;
  photos: TaskPhoto[];
  busy: boolean;
  onPhoto: () => void;
  onNext: (checklist: boolean[]) => void;
  onTimerSync: () => void;
}) {
  const [checks, setChecks] = useState<boolean[]>(() => stage.checklist.map(() => false));
  // Обратный отсчёт только для показа; проверку времени делает сервер.
  const [left, setLeft] = useState(secondsLeft);
  useEffect(() => setLeft(secondsLeft), [secondsLeft]);
  useEffect(() => {
    if (left <= 0) return;
    const t = setTimeout(() => setLeft((x) => x - 1), 1000);
    return () => clearTimeout(t);
  }, [left]);
  const photoMissing = stage.photo === "required" && photos.length === 0;
  const ready = checks.every(Boolean) && left <= 0 && !photoMissing;

  return (
    <div className="card stack">
      <div className="row spread">
        <b>
          Этап {index + 1} из {total}: {stage.title}
        </b>
        {left > 0 && <span className="chip warn">{formatSeconds(left)}</span>}
      </div>
      {stage.instructions && <div className="small" style={{ whiteSpace: "pre-wrap" }}>{stage.instructions}</div>}
      {stage.checklist.map((item, i) => (
        <label className="check" key={i}>
          <input
            type="checkbox"
            checked={checks[i]}
            onChange={(e) => setChecks(checks.map((c, j) => (j === i ? e.target.checked : c)))}
          />
          {item}
        </label>
      ))}
      {stage.photo !== "none" && (
        <div className="row">
          <button onClick={onPhoto} disabled={busy}>
            {photos.length ? "Переснять фото" : stage.photo === "required" ? "Сделать фото (обязательно)" : "Сделать фото"}
          </button>
          {photos.length > 0 && <span className="chip ok">Фото загружено</span>}
        </div>
      )}
      <button
        className="primary"
        disabled={busy || !ready}
        onClick={() => {
          if (left <= 0) onNext(checks);
          else onTimerSync();
        }}
      >
        {left > 0 ? `Далее через ${formatSeconds(left)}` : "Далее"}
      </button>
    </div>
  );
}

function formatSeconds(s: number) {
  const m = Math.floor(s / 60);
  return m ? `${m}:${String(s % 60).padStart(2, "0")}` : `${s} с`;
}

function PhotoResult({ photo, ocr }: { photo: TaskPhoto; ocr: boolean }) {
  if (!ocr || !photo.ocr_status) return <div className="chip ok">Фото загружено</div>;
  const title =
    photo.ocr_status === "passed" ? "Проверка пройдена" : photo.ocr_status === "failed" ? "Текст не совпал" : "Проверка не выполнена";
  return (
    <div className="small">
      <span className={`chip ${photo.ocr_status === "passed" ? "ok" : "bad"}`}>{title}</span>
      {photo.ocr_detail && <span className="muted"> {photo.ocr_detail}</span>}
      {photo.ocr_text && (
        <div className="muted" style={{ whiteSpace: "pre-wrap" }}>
          Распознано: {photo.ocr_text}
        </div>
      )}
    </div>
  );
}

function ErrorDialog({
  reasons,
  onSubmit,
  onClose,
}: {
  reasons: string[];
  onSubmit: (reason: string, comment: string | null) => Promise<void>;
  onClose: () => void;
}) {
  const all = [...reasons, "Другое"];
  const [reason, setReason] = useState(all[0]);
  const [comment, setComment] = useState("");
  const { busy, error, run } = useAction();
  return (
    <div className="modal-back" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>Сообщить об ошибке</h2>
        <p className="muted small">Задание будет приостановлено до решения администрации.</p>
        <ErrorBox error={error} />
        <Field label="Причина">
          <select value={reason} onChange={(e) => setReason(e.target.value)}>
            {all.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </Field>
        <Field label={reason === "Другое" ? "Опишите проблему" : "Комментарий (необязательно)"}>
          <textarea value={comment} onChange={(e) => setComment(e.target.value)} maxLength={1000} />
        </Field>
        <div className="row">
          <button
            className="danger"
            disabled={busy || (reason === "Другое" && !comment.trim())}
            onClick={async () => {
              const r = await run(() => onSubmit(reason, comment.trim() || null));
              if (r.ok) onClose();
            }}
          >
            Отправить
          </button>
          <button onClick={onClose} disabled={busy}>
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}

export function TaskInfo({ task: t, product: p }: { task: Task; product: MyTaskProduct }) {
  return (
    <>
      <div className="card kv">
        <span>Статус</span>
        <span>{TASK_STATUS_TITLES[t.status]}</span>
        <span>Единиц</span>
        <span>{t.units}</span>
        {t.rating && (
          <>
            <span>Рейтинг</span>
            <span>{t.rating}</span>
          </>
        )}
        {t.expected_text && (
          <>
            <span>Ожидаемый текст</span>
            <span>{t.expected_text}</span>
          </>
        )}
        {t.comment && (
          <>
            <span>Комментарий</span>
            <span>{t.comment}</span>
          </>
        )}
        {Object.entries(t.extra).map(([k, v]) => (
          <span key={k} style={{ display: "contents" }}>
            <span>{k}</span>
            <span>{v}</span>
          </span>
        ))}
      </div>
      {(t.instructions || p.instructions) && (
        <details className="card small" open={!!t.instructions}>
          <summary>Инструкция</summary>
          <div style={{ whiteSpace: "pre-wrap" }}>{t.instructions || p.instructions}</div>
        </details>
      )}
      {(t.materials.length > 0 || p.materials.length > 0 || p.links.length > 0) && (
        <div className="card small">
          {[...p.links, ...p.materials].map((l, i) => (
            <div key={i}>
              <a href={l.url} target="_blank" rel="noreferrer">
                {l.title}
              </a>
            </div>
          ))}
          {t.materials.map((m, i) => (
            <div key={`m${i}`}>
              <a href={m} target="_blank" rel="noreferrer">
                {m}
              </a>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
