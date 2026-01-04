import { useNavigate } from "react-router-dom";

export default function Login() {
  const navigate = useNavigate();

  function mockLogin() {
    localStorage.setItem("token", "mock-token");
    localStorage.setItem("user", JSON.stringify({ name: "Inmobiliaria Demo" }));
    navigate("/");
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-xl bg-white p-6 shadow">
        <h1 className="text-xl font-semibold text-gray-900">Ingresar</h1>
        <p className="mt-1 text-sm text-gray-600">
          Panel para inmobiliarias: contratos, vencimientos y ajustes.
        </p>

        <button
          onClick={mockLogin}
          className="mt-6 w-full rounded-md bg-gray-900 px-4 py-2 text-white hover:bg-gray-800"
        >
          Entrar (demo)
        </button>
      </div>
    </div>
  );
}