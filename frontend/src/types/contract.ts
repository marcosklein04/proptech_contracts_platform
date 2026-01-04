export type Currency = "ARS" | "USD";

export type Contract = {
  id: string;
  propertyLabel: string;
  ownerName: string;
  tenantName: string;
  startDate: string; // ISO date
  endDate: string;   // ISO date
  amount: number;
  currency: Currency;
  adjustment?: {
    type: "IPC_QUARTERLY" | "NONE";
    frequencyMonths?: number;
  };
};