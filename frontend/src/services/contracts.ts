import { api } from "./api";
import type { Contract } from "../types/contract";

export async function getContracts(): Promise<Contract[]> {
  const res = await api.get("/contracts");
  return res.data;
}