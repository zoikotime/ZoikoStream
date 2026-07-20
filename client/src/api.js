import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000",
});

// Attach the stored token to every request.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export default api;

// Turn an axios error into a readable message for toasts.
export const errMsg = (e, fallback = "Something went wrong") =>
  e?.response?.data?.detail || e?.message || fallback;
