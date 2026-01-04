import { Link, useNavigate } from "react-router-dom";

export default function Navbar() {
  const navigate = useNavigate();

  function logout() {
    localStorage.removeItem("token");
    localStorage.removeItem("user");
    navigate("/login");
  }

  return (
    <header className="border-b bg-white">
      <div className="mx-auto flex max-w-6xl items-center justify-between p-4">
        <Link to="/" className="font-semibold text-gray-900">
          PropTech Contracts
        </Link>

        <nav className="flex items-center gap-3 text-sm">
          <Link to="/properties" className="text-gray-700 hover:underline">
            Propiedades
          </Link>
          <Link to="/contracts" className="text-gray-700 hover:underline">
            Contratos
          </Link>
          <button
            onClick={logout}
            className="rounded-md bg-gray-900 px-3 py-1.5 text-white hover:bg-gray-800"
          >
            Salir
          </button>
        </nav>
      </div>
    </header>
  );
}