import { useEffect, useRef, useState } from "react";
import { ApiError } from "../api";
import { initData } from "../telegram";

/** Фото с сервера: загружается с заголовком авторизации и показывается через blob-URL. */
export function AuthImage({ id, alt = "", style }: { id: number; alt?: string; style?: React.CSSProperties }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let revoked = false;
    let objectUrl: string | null = null;
    fetch(`/api/photos/${id}`, { headers: { Authorization: `tma ${initData()}` } })
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((b) => {
        if (revoked) return;
        objectUrl = URL.createObjectURL(b);
        setUrl(objectUrl);
      })
      .catch(() => setFailed(true));
    return () => {
      revoked = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [id]);
  if (failed) return <div className="muted small">Фото недоступно</div>;
  if (!url) return <div className="muted small">Загрузка фото…</div>;
  return (
    <a href={url} target="_blank" rel="noreferrer">
      <img src={url} alt={alt} style={{ maxWidth: "100%", borderRadius: 8, display: "block", ...style }} />
    </a>
  );
}

const MAX_SIDE = 1600;

async function downscale(source: CanvasImageSource, w: number, h: number): Promise<Blob> {
  const k = Math.min(1, MAX_SIDE / Math.max(w, h));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(w * k);
  canvas.height = Math.round(h * k);
  canvas.getContext("2d")!.drawImage(source, 0, 0, canvas.width, canvas.height);
  return new Promise((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Не удалось подготовить фото"))), "image/jpeg", 0.88),
  );
}

async function fileToJpeg(file: File): Promise<Blob> {
  const bmp = await createImageBitmap(file).catch(() => null);
  if (!bmp) return file;
  return downscale(bmp, bmp.width, bmp.height);
}

/**
 * Камера: предпросмотр → снимок → «Отправить» / «Переснять» / «Отмена».
 * Фото считается загруженным только после ответа сервера (onUpload вернул успех).
 * Если камера недоступна в этом клиенте, используется системная съёмка через input capture.
 */
export function Camera({
  title,
  onUpload,
  onClose,
}: {
  title: string;
  onUpload: (blob: Blob) => Promise<boolean>;
  onClose: () => void;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const [mode, setMode] = useState<"starting" | "live" | "fallback">("starting");
  const [shot, setShot] = useState<{ blob: Blob; url: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [videoReady, setVideoReady] = useState(false);

  const stop = () => {
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
  };

  const start = async () => {
    setError(null);
    setVideoReady(false);
    if (!navigator.mediaDevices?.getUserMedia) {
      setMode("fallback");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 } },
        audio: false,
      });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play().catch(() => undefined);
      }
      setMode("live");
    } catch {
      setMode("fallback");
    }
  };

  useEffect(() => {
    start();
    return stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => () => {
    if (shot) URL.revokeObjectURL(shot.url);
  }, [shot]);

  const capture = async () => {
    const v = videoRef.current;
    if (!v || !v.videoWidth) return;
    const blob = await downscale(v, v.videoWidth, v.videoHeight);
    setShot({ blob, url: URL.createObjectURL(blob) });
    stop();
  };

  const retake = () => {
    setShot(null);
    if (mode === "live") start();
  };

  const send = async () => {
    if (!shot) return;
    setBusy(true);
    setError(null);
    const ok = await onUpload(shot.blob).catch((e) => {
      setError(e instanceof ApiError || e instanceof Error ? e.message : String(e));
      return false;
    });
    setBusy(false);
    if (ok) onClose();
  };

  return (
    <div className="modal-back">
      <div className="modal stack">
        <h2 style={{ marginTop: 0 }}>{title}</h2>
        {error && <div className="error">{error}</div>}
        {shot ? (
          <img src={shot.url} alt="Предпросмотр" style={{ width: "100%", borderRadius: 8 }} />
        ) : mode === "fallback" ? (
          <label className="list-item" style={{ textAlign: "center" }}>
            Открыть камеру
            <input
              type="file"
              accept="image/*"
              capture="environment"
              style={{ display: "none" }}
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                const blob = await fileToJpeg(f);
                setShot({ blob, url: URL.createObjectURL(blob) });
              }}
            />
          </label>
        ) : (
          <video
            ref={videoRef}
            playsInline
            muted
            onLoadedData={() => setVideoReady(true)}
            style={{ width: "100%", borderRadius: 8, background: "#000" }}
          />
        )}
        <div className="row">
          {shot ? (
            <>
              <button className="primary" disabled={busy} onClick={send}>
                {busy ? "Отправка…" : "Отправить"}
              </button>
              <button disabled={busy} onClick={retake}>
                Переснять
              </button>
            </>
          ) : (
            mode === "live" && (
              <button className="primary" onClick={capture} disabled={!videoReady}>
                {videoReady ? "Снять" : "Камера запускается…"}
              </button>
            )
          )}
          <button
            disabled={busy}
            onClick={() => {
              stop();
              onClose();
            }}
          >
            Отмена
          </button>
        </div>
      </div>
    </div>
  );
}
