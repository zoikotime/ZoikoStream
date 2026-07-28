import axios from "axios";

const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8001",
});

// Attach the stored token to every request.
api.interceptors.request.use((config) => {
  const token = localStorage.getItem("token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

export default api;

// Turn an axios error into a readable string for toasts. Must always return a
// string: FastAPI 422s send `detail` as an array of {loc,msg,...} objects, and
// passing a non-string to toast.error() crashes React (blank screen).
export const errMsg = (e, fallback = "Something went wrong") => {
  const d = e?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => x?.msg).filter(Boolean).join(", ") || fallback;
  return e?.message || fallback;
};
