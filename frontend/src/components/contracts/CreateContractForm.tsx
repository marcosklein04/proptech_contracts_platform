import { useState } from "react";
import { api } from "../../services/api";

type Props = {
  onCreated: () => void;
  onCancel: () => void;
  initial?: Partial<{
    propertyLabel: string;
    ownerName: string;
    tenantName: string;
    startDate: string;
    endDate: string;
    amount: number;
    currency: "ARS" | "USD";
  }>;
};

export default function CreateContractForm({ onCreated, onCancel, initial }: Props) {
  const [propertyLabel, setPropertyLabel] = useState(initial?.propertyLabel ?? "");
  const [ownerName, setOwnerName] = useState(initial?.ownerName ?? "");
  const [tenantName, setTenantName] = useState(initial?.tenantName ?? "");
  const [startDate, setStartDate] = useState(initial?.startDate ?? "");
  const [endDate, setEndDate] = useState(initial?.endDate ?? "");
  const [amount, setAmount] = useState<number>(initial?.amount ?? 0);
  const [currency, setCurrency] = useState<"ARS" | "USD">(initial?.currency ?? "ARS");

  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setError(null);

    if (!propertyLabel || !ownerName || !tenantName || !startDate || !endDate || !amount || !currency) {
      setError("Completá todos los campos obligatorios.");
      return;
    }

    try {
      setLoading(true);
      await api.post("/contracts", {
        propertyLabel,
        ownerName,
        tenantName,
        startDate,
        endDate,
        amount,
        currency,
      });
      onCreated();
    } catch {
      setError("No se pudo crear el contrato. Revisá el backend y la conexión.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="text-sm font-medium text-gray-800">Propiedad *</label>
          <input
            value={propertyLabel}
            onChange={(e) => setPropertyLabel(e.target.value)}
            className="mt-1 w-full rounded-md border p-2 text-sm"
            placeholder='Ej: "Laprida 1368 2° CF"'
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Propietario *</label>
          <input
            value={ownerName}
            onChange={(e) => setOwnerName(e.target.value)}
            className="mt-1 w-full rounded-md border p-2 text-sm"
            placeholder="Nombre y apellido"
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Inquilino *</label>
          <input
            value={tenantName}
            onChange={(e) => setTenantName(e.target.value)}
            className="mt-1 w-full rounded-md border p-2 text-sm"
            placeholder="Nombre y apellido / Razón social"
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Inicio *</label>
          <input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="mt-1 w-full rounded-md border p-2 text-sm"
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Fin *</label>
          <input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="mt-1 w-full rounded-md border p-2 text-sm"
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Importe *</label>
          <input
            type="number"
            value={amount}
            onChange={(e) => setAmount(Number(e.target.value))}
            className="mt-1 w-full rounded-md border p-2 text-sm"
            min={0}
          />
        </div>

        <div>
          <label className="text-sm font-medium text-gray-800">Moneda *</label>
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value as "ARS" | "USD")}
            className="mt-1 w-full rounded-md border p-2 text-sm"
          >
            <option value="ARS">ARS</option>
            <option value="USD">USD</option>
          </select>
        </div>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <button
          onClick={onCancel}
          className="rounded-md border px-4 py-2 text-sm hover:bg-gray-50"
          disabled={loading}
        >
          Cancelar
        </button>

        <button
          onClick={submit}
          className="rounded-md bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-60"
          disabled={loading}
        >
          {loading ? "Creando..." : "Crear contrato"}
        </button>
      </div>
    </div>
  );
}