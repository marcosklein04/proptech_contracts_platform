import { useEffect, useMemo, useState } from "react";
import ContractsTable from "../components/contracts/ContractsTable";
import CreateContractForm from "../components/contracts/CreateContractForm";
import { getContracts } from "../services/contracts";
import { uploadAndExtract } from "../services/contractsUpload";
import type { Contract } from "../types/contract";
import { mockContracts } from "./contracts.mock";

type Filter = "ALL" | "ACTIVE" | "EXPIRING" | "EXPIRED";

const IS_DEV = import.meta.env.DEV;

export default function Contracts() {
  const [filter, setFilter] = useState<Filter>("ALL");
  const [isUploadOpen, setIsUploadOpen] = useState(false);

  const [contracts, setContracts] = useState<Contract[]>([]);
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [extracting, setExtracting] = useState(false);
  const [extracted, setExtracted] = useState<any>(null);
  const [extractError, setExtractError] = useState<string | null>(null);

  async function loadContracts() {
  try {
    setLoading(true);
    const data = await getContracts();
    setContracts(data);
    setLoadError(null);
  } catch (e) {
    if (IS_DEV) {
      setContracts(mockContracts);
    } else {
      setContracts([]);
      setLoadError("No se pudo cargar contratos desde el backend. Revisá VITE_API_URL y CORS.");
    }
  } finally {
    setLoading(false);
  }
}

  useEffect(() => {
    loadContracts();
  }, []);

  const filtered = useMemo(() => {
    const now = new Date();
    const dayMs = 24 * 60 * 60 * 1000;

    return contracts.filter((c) => {
      const end = new Date(c.endDate).getTime();
      const diffDays = Math.floor((end - now.getTime()) / dayMs);

      if (filter === "EXPIRED") return diffDays < 0;
      if (filter === "EXPIRING") return diffDays >= 0 && diffDays <= 60;
      if (filter === "ACTIVE") return diffDays > 60;
      return true;
    });
  }, [contracts, filter]);

  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">Contratos</h1>
          <p className="text-sm text-gray-600">
            Visualizá estado, vencimientos y ajustes. Subí PDF/DOCX y prellená con IA.
          </p>
        </div>

        <div className="flex gap-2">
          {[
            ["ALL", "Todos"],
            ["ACTIVE", "Activos"],
            ["EXPIRING", "Por vencer (60d)"],
            ["EXPIRED", "Vencidos"],
          ].map(([key, label]) => (
            <button
              key={key}
              onClick={() => setFilter(key as Filter)}
              className={`rounded-md px-3 py-2 text-sm ${
                filter === key
                  ? "bg-gray-900 text-white"
                  : "bg-white text-gray-700 border hover:bg-gray-50"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <div className="rounded-md border bg-white p-3 text-sm text-gray-700">
          Cargando contratos...
        </div>
      )}

      {loadError && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {loadError}
        </div>
      )}

      <ContractsTable
        contracts={filtered}
        onUploadClick={() => {
          setIsUploadOpen(true);
          setSelectedFile(null);
          setExtracted(null);
          setExtractError(null);
        }}
      />

      {isUploadOpen && (
        <div className="fixed inset-0 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-lg rounded-xl bg-white p-6 shadow">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Subir contrato</h3>
                <p className="text-sm text-gray-600">
                  Seleccioná un PDF/DOCX, analizalo con IA y confirmá los datos antes de crear el contrato.
                </p>
              </div>

              <button
                onClick={() => setIsUploadOpen(false)}
                className="rounded-md px-2 py-1 text-gray-600 hover:bg-gray-100"
                disabled={extracting}
              >
                ✕
              </button>
            </div>

            <div className="mt-4 space-y-3">
              {extractError && (
                <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
                  {extractError}
                </div>
              )}

              <div>
                <label className="text-sm font-medium text-gray-800">
                  Archivo (PDF/DOCX)
                </label>
                <input
                  type="file"
                  className="mt-1 block w-full rounded-md border p-2 text-sm"
                  accept=".pdf,.docx"
                  onChange={(e) => {
                    setSelectedFile(e.target.files?.[0] ?? null);
                    setExtracted(null);
                    setExtractError(null);
                  }}
                />
              </div>

              <div className="flex gap-2">
                <button
                  className="rounded-md bg-gray-900 px-4 py-2 text-sm text-white hover:bg-gray-800 disabled:opacity-60"
                  disabled={!selectedFile || extracting}
                  onClick={async () => {
                    if (!selectedFile) return;

                    try {
                      setExtracting(true);
                      const res = await uploadAndExtract(selectedFile);
                      setExtracted(res.extracted);
                    } catch {
                      setExtractError(
                        "No se pudo analizar el archivo. Verificá que el backend esté activo."
                      );
                    } finally {
                      setExtracting(false);
                    }
                  }}
                >
                  {extracting ? "Analizando..." : "Analizar"}
                </button>

                <button
                  className="rounded-md border px-4 py-2 text-sm hover:bg-gray-50"
                  onClick={() => setIsUploadOpen(false)}
                  disabled={extracting}
                >
                  Cancelar
                </button>
              </div>

              {extracted && (
                <div className="mt-3 rounded-lg border bg-gray-50 p-4">
                  <CreateContractForm
                    onCancel={() => setIsUploadOpen(false)}
                    onCreated={async () => {
                      setIsUploadOpen(false);
                      setSelectedFile(null);
                      setExtracted(null);
                      await loadContracts();
                    }}
                    initial={{
                      propertyLabel: extracted.propertyLabel ?? "",
                      ownerName: extracted.ownerName ?? "",
                      tenantName: extracted.tenantName ?? "",
                      startDate: extracted.startDate ?? "",
                      endDate: extracted.endDate ?? "",
                      amount: extracted.amount ?? 0,
                      currency: extracted.currency ?? "ARS",
                    }}
                  />
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}