import { api, ApiError, Me } from "./api";
import { ErrorBox, Loader, useLoad } from "./components/ui";
import { Home } from "./screens/Home";
import { CandidateStatusScreen, Welcome } from "./screens/Candidate";
import { NoAccess, StatusScreen } from "./screens/Status";
import { Register, StaffRegister } from "./screens/Register";
import { initData } from "./telegram";

export function App() {
  if (!initData()) {
    return (
      <div className="center">
        <h1>Откройте приложение из Telegram</h1>
        <p className="muted">Вход возможен только через бота.</p>
      </div>
    );
  }
  return <Authed />;
}

function Authed() {
  const { data: me, error, loading, reload } = useLoad(() => api.get<Me>("/me"), []);

  if (loading && !me) return <Loader />;
  if (error) {
    const expired = error instanceof ApiError && error.status === 401;
    return (
      <ErrorBox
        error={expired ? new Error(`${error.message}. Закройте и снова откройте приложение`) : error}
        onRetry={expired ? undefined : reload}
      />
    );
  }
  if (!me) return null;

  switch (me.screen) {
    case "register":
      return <Register me={me} onDone={reload} />;
    case "candidate_form":
      return <Welcome onDone={reload} />;
    case "staff_register":
      return <StaffRegister me={me} onDone={reload} />;
    case "no_access":
      return <NoAccess />;
    case "candidate":
      return <CandidateStatusScreen onReload={reload} />;
    case "ready":
      return <Home me={me} onReload={reload} />;
    default:
      return <StatusScreen me={me} onReload={reload} />;
  }
}
