import { api } from "./api";
import type { Contract } from "../types/contract";

export async function getContracts(): Promise<Contract[]> {
  const res = await api.get<Contract[]>("/contracts");
  return res.data;
}

export type CreateContractPayload = {
  propertyLabel: string;
  ownerName: string;
  tenantName: string;
  startDate: string; // YYYY-MM-DD
  endDate: string;   // YYYY-MM-DD
  amount: number;
  currency: "ARS" | "USD";
};

export async function createContract(payload: CreateContractPayload): Promise<{ id: string }> {
  const res = await api.post("/contracts", payload);
  return res.data;
}