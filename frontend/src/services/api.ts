import axios from "axios";

const rawBase = import.meta.env.VITE_API_URL || "http://127.0.0.1:5000";
const baseURL = rawBase.replace(/\/+$/, ""); // saca slash final

export const api = axios.create({
  baseURL,
});

//api.interceptors.request.use((config) => {
//  const token = localStorage.getItem("token");
//  if (token) config.headers.Authorization = `Bearer ${token}`;
//  return config;
//});