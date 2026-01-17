import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import ProtectedRoute from "./components/ProtectedRoute";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* Login siempre público */}
        <Route path="/login" element={<Login />} />

        {/* Todo lo demás protegido */}
        <Route element={<ProtectedRoute />}>
          <Route path="/" element={<Dashboard />} />
          {/* Si tenés otras páginas, agregalas acá adentro */}
          {/* <Route path="/contratos" element={<Contratos />} /> */}
          {/* <Route path="/propiedades" element={<Propiedades />} /> */}
        </Route>

        {/* fallback */}
        <Route path="*" element={<div style={{ padding: 24 }}>404 Not Found</div>} />
      </Routes>
    </BrowserRouter>
  );
}