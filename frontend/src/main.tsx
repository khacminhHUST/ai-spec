import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { installDeployAdapter } from "./deploy";
import "./styles.css";

installDeployAdapter(); // chạy nhúng trong web Tri thức: tiền tố đường dẫn + token (local: không làm gì)

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
