import { useRef, useState } from "react";
import { api, ImportBatch, ProductListItem, upload } from "../api";
import { ErrorBox, Field, useAction, useLoad } from "../components/ui";

const STATUS_TEXT = { ok: "Будет загружено", error: "Ошибка", duplicate: "Дубликат", skipped: "Пустая строка" };

export function Import() {
  const products = useLoad(() => api.get<ProductListItem[]>("/products"), []);
  const [productId, setProductId] = useState<string>("");
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [onlyProblems, setOnlyProblems] = useState(true);
  const fileRef = useRef<HTMLInputElement>(null);
  const action = useAction();

  const choose = async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    if (productId) form.append("product_id", productId);
    const r = await action.run(() => upload<ImportBatch>("/imports", form));
    if (r.ok) setBatch(r.value);
    if (fileRef.current) fileRef.current.value = "";
  };

  return (
    <div>
      <h1>Импорт заданий</h1>
      <p className="muted small">
        Файл .xlsx, первая строка — заголовки. Обязательна колонка «ID задания». Распознаются колонки: Продукт, Тип,
        Ссылка, Ожидаемый текст, Рейтинг, Инструкция, Комментарий, Материалы, Пол, Единиц. Остальные колонки
        сохраняются как дополнительные поля.
      </p>
      {!batch && (
        <>
          <Field label="Продукт (если в файле нет колонки «Продукт»)">
            <select value={productId} onChange={(e) => setProductId(e.target.value)}>
              <option value="">— из файла —</option>
              {products.data
                ?.filter((p) => p.status !== "archived")
                .map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
            </select>
          </Field>
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            disabled={action.busy}
            onChange={(e) => e.target.files?.[0] && choose(e.target.files[0])}
          />
          {action.busy && <p className="muted">Проверка файла…</p>}
        </>
      )}
      <ErrorBox error={action.error} />

      {batch && (
        <>
          <div className="card kv">
            <span>Файл</span>
            <span>{batch.file_name}</span>
            <span>Строк</span>
            <span>{batch.summary.total}</span>
            <span>Будет добавлено</span>
            <span>
              <b>{batch.summary.ok}</b>
            </span>
            <span>С ошибками</span>
            <span>{batch.summary.errors}</span>
            <span>Дубликаты</span>
            <span>{batch.summary.duplicates}</span>
            <span>Пустые строки</span>
            <span>{batch.summary.skipped}</span>
            {batch.summary.extra_columns.length > 0 && (
              <>
                <span>Доп. колонки</span>
                <span>{batch.summary.extra_columns.join(", ")}</span>
              </>
            )}
          </div>

          {batch.result ? (
            <div className="notice">
              Загружено заданий: <b>{batch.result.added}</b>. Пропущено строк: {batch.result.skipped}, дубликатов:{" "}
              {batch.result.duplicates}, ошибок: {batch.result.errors}.
              {Object.entries(batch.result.available).map(([pid, n]) => (
                <div key={pid}>
                  Доступно в пуле «{products.data?.find((p) => String(p.id) === pid)?.name ?? pid}»: {n}
                </div>
              ))}
            </div>
          ) : (
            <div className="row">
              <button
                className="primary"
                disabled={action.busy || batch.summary.ok === 0}
                onClick={async () => {
                  const r = await action.run(() => api.post<ImportBatch["result"]>(`/imports/${batch.id}/commit`));
                  if (r.ok) setBatch({ ...batch, status: "committed", result: r.value });
                }}
              >
                Загрузить {batch.summary.ok}
              </button>
              <button
                disabled={action.busy}
                onClick={async () => {
                  await action.run(() => api.post(`/imports/${batch.id}/discard`));
                  setBatch(null);
                }}
              >
                Отмена
              </button>
            </div>
          )}
          {batch.result && (
            <button style={{ marginTop: 8 }} onClick={() => setBatch(null)}>
              Загрузить ещё файл
            </button>
          )}

          <h2>Строки</h2>
          <label className="check">
            <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
            Только проблемные
          </label>
          {batch.rows
            .filter((r) => !onlyProblems || (r.status !== "ok" && r.status !== "skipped"))
            .slice(0, 300)
            .map((r) => (
              <div className="card small" key={r.row}>
                <div className="row spread">
                  <span>
                    Строка {r.row}
                    {r.external_id ? ` · ID ${r.external_id}` : ""}
                    {r.type ? ` · ${r.type}` : ""}
                  </span>
                  <span className={`chip ${r.status === "ok" ? "ok" : r.status === "error" ? "bad" : "warn"}`}>
                    {STATUS_TEXT[r.status]}
                  </span>
                </div>
                {r.errors.map((e, i) => (
                  <div key={i} className="muted">
                    {e}
                  </div>
                ))}
              </div>
            ))}
        </>
      )}
    </div>
  );
}
