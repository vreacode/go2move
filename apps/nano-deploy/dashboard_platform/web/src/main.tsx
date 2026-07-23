import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import MobileCaptureConsole from "./MobileCaptureConsole";
import "./styles.css";

function Root() {
  const [isMobile, setIsMobile] = React.useState(() => window.matchMedia("(max-width: 760px)").matches);

  React.useEffect(() => {
    const query = window.matchMedia("(max-width: 760px)");
    const update = () => setIsMobile(query.matches);
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);

  return isMobile ? <MobileCaptureConsole /> : <App />;
}

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <Root />
  </React.StrictMode>,
);
