import React, { useEffect, useState } from "react";
import ReactDOM from "react-dom/client";
import { App } from "./App";
import { SetupWizard } from "./components/SetupWizard";
import "./styles.css";

function Root() {
  const [state, setState] = useState<SetupState | null>(null);
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!window.desktop) {
      setDone(true);
      return;
    }
    void window.desktop
      .getSetupState()
      .then(setState)
      .catch(() => setDone(true));
  }, []);

  if (done) return <App />;
  if (!state) return <div className="setup-shell">加载中…</div>;
  if (state.needsSetup) {
    return <SetupWizard initialState={state} onDone={() => setDone(true)} />;
  }
  return <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
