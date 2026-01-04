import dayjs from "dayjs";
import type { Contract } from "../../types/contract";

type Props = {
  contracts: Contract[];
  onUploadClick: () => void;
};

function formatMoney(amount: number, currency: Contract["currency"]) {
  if (currency === "USD") return `USD ${amount.toLocaleString("en-US")}`;
  return `$ ${amount.toLocaleString("es-AR")}`;
}

function contractStatus(endDate: string) {
  const end = dayjs(endDate);
  const now = dayjs();
  const diffDays = end.diff(now, "day");

  if (diffDays < 0) return { label: "Vencido", className: "bg-red-100 text-red-700" };
  if (diffDays <= 60) return { label: "Por vencer", className: "bg-amber-100 text-amber-800" };
  return { label: "Activo", className: "bg-green-100 text-green-700" };
}

export default function ContractsTable({ contracts, onUploadClick }: Props) {
  return (
    <div className="rounded-xl bg-white shadow">
      <div className="flex items-center justify-between border-b p-4">
        <div>
          <h2 className="text-lg font-semibold text-gray-900">Contratos</h2>
          <p className="text-sm text-gray-600">Gestión de contratos, vencimientos y ajustes.</p>
        </div>

        <button
          onClick={onUploadClick}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
        >
          Subir contrato
        </button>
      </div>

      <div className="overflow-x-auto">
        <table className="min-w-full text-left text-sm">
          <thead className="bg-gray-50 text-gray-600">
            <tr>
              <th className="px-4 py-3 font-medium">Propiedad</th>
              <th className="px-4 py-3 font-medium">Propietario</th>
              <th className="px-4 py-3 font-medium">Inquilino</th>
              <th className="px-4 py-3 font-medium">Vigencia</th>
              <th className="px-4 py-3 font-medium">Importe</th>
              <th className="px-4 py-3 font-medium">Ajuste</th>
              <th className="px-4 py-3 font-medium">Estado</th>
            </tr>
          </thead>

          <tbody className="divide-y">
            {contracts.map((c) => {
              const status = contractStatus(c.endDate);
              return (
                <tr key={c.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{c.propertyLabel}</div>
                    <div className="text-xs text-gray-500">{c.id}</div>
                  </td>
                  <td className="px-4 py-3 text-gray-800">{c.ownerName}</td>
                  <td className="px-4 py-3 text-gray-800">{c.tenantName}</td>
                  <td className="px-4 py-3 text-gray-700">
                    {dayjs(c.startDate).format("DD/MM/YYYY")} – {dayjs(c.endDate).format("DD/MM/YYYY")}
                  </td>
                  <td className="px-4 py-3 text-gray-900">
                    {formatMoney(c.amount, c.currency)}
                  </td>
                  <td className="px-4 py-3 text-gray-700">
                    {c.currency === "ARS" ? "IPC trimestral" : "Sin ajuste"}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex rounded-full px-2 py-1 text-xs font-medium ${status.className}`}>
                      {status.label}
                    </span>
                  </td>
                </tr>
              );
            })}

            {contracts.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-gray-600">
                  No hay contratos para mostrar.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}