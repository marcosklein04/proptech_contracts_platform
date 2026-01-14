import { createBrowserRouter, Navigate } from "react-router-dom";
import type { ReactNode } from "react";

import AppLayout from "../components/layout/AppLayout";
import Login from "../pages/Login";
import Dashboard from "../pages/Dashboard";
import Contracts from "../pages/Contracts";
import Properties from "../pages/Properties";

function RequireAuth({ children }: { children: ReactNode }) {
  const token = localStorage.getItem("token");
  return token ? <>{children}</> : <Navigate to="/login" replace />;
}

export const router = createBrowserRouter([
  { path: "/login", element: <Login /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppLayout />
      </RequireAuth>
    ),
    children: [
      { index: true, element: <Dashboard /> },
      { path: "contracts", element: <Contracts /> },
      { path: "properties", element: <Properties /> },
    ],
  },
]);

export default router;