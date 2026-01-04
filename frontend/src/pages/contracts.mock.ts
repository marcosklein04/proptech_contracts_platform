import type { Contract } from "../types/contract";

export const mockContracts: Contract[] = [
  {
    id: "C-1001",
    propertyLabel: "Laprida 1368 2° CF",
    ownerName: "María Gómez",
    tenantName: "Juan Pérez",
    startDate: "2025-03-01",
    endDate: "2027-03-01",
    amount: 550000,
    currency: "ARS",
    adjustment: { type: "IPC_QUARTERLY", frequencyMonths: 3 },
  },
  {
    id: "C-1002",
    propertyLabel: "Rodríguez Peña 1875 PB",
    ownerName: "Ana López",
    tenantName: "Consultorio Recoleta SRL",
    startDate: "2024-02-01",
    endDate: "2026-02-01",
    amount: 900,
    currency: "USD",
    adjustment: { type: "NONE" },
  },
  {
    id: "C-1003",
    propertyLabel: "Luis M. Campos 369 7°",
    ownerName: "Carlos Ruiz",
    tenantName: "Sofía Martínez",
    startDate: "2023-01-10",
    endDate: "2025-01-10",
    amount: 420000,
    currency: "ARS",
    adjustment: { type: "IPC_QUARTERLY", frequencyMonths: 3 },
  },
];