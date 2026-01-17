import axios from "axios";

const rawBase =
  import.meta.env.VITE_API_URL ||
  "https://proptech-contracts-platform.onrender.com";

const baseURL = rawBase.replace(/\/+$/, "");

export const api = axios.create({
  baseURL,
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});