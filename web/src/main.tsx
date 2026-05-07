import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { AuthProvider } from "./auth/AuthContext";
import { Toaster } from "./components/Toaster";
import "./styles/globals.css";

const root = document.getElementById("root");
if (!root) throw new Error("#root not found");

// 注意 BrowserRouter basename：与 vite base 对齐，使路由表里的 /ui/login 在
// 浏览器地址栏体现为 /ui/login（FastAPI 静态托管在 /ui 前缀下）。
ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
        <Toaster />
      </AuthProvider>
    </BrowserRouter>
  </React.StrictMode>,
);
