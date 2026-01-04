import { api } from "./api";

export type ExtractedContract = {
  ownerName: string | null;
  tenantName: string | null;
  startDate: string | null;
  endDate: string | null;
  amount: number | null;
  currency: "ARS" | "USD";
  adjustment?: { type: "IPC_QUARTERLY" | "NONE"; frequencyMonths?: number };
  // opcional si más adelante lo agregás
  propertyLabel?: string | null;
};

export async function uploadAndExtract(file: File): Promise<{
  extracted: ExtractedContract;
  textPreview?: string;
}> {
  const fd = new FormData();
  fd.append("file", file);

  const res = await api.post("/contracts/upload", fd, {
    headers: { "Content-Type": "multipart/form-data" },
  });

  return res.data;
}