/**
 * REST API client. Session-cookie auth + CSRF header.
 * All URLs are same-origin (Django serves web app and API together).
 */

function getCookie(name) {
  const match = document.cookie.match(new RegExp(`(?:^|; )${name}=([^;]*)`));
  return match ? decodeURIComponent(match[1]) : null;
}

export class ApiError extends Error {
  constructor(message, { code = "error", status = 0, details = [] } = {}) {
    super(message);
    this.code = code;
    this.status = status;
    this.details = details;
  }
}

async function parseError(response) {
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    /* non-JSON error body */
  }
  const err = payload && payload.error ? payload.error : {};
  return new ApiError(err.message || `خطای سرور (${response.status})`, {
    code: err.code || "http_error",
    status: response.status,
    details: err.details || [],
  });
}

export async function apiFetch(path, { method = "GET", body, formData, headers = {} } = {}) {
  const opts = {
    method,
    credentials: "same-origin",
    headers: { ...headers },
  };

  const unsafe = !["GET", "HEAD", "OPTIONS"].includes(method.toUpperCase());
  if (unsafe) {
    const csrf = getCookie("csrftoken");
    if (csrf) opts.headers["X-CSRFToken"] = csrf;
  }

  if (formData) {
    opts.body = formData;
  } else if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }

  const response = await fetch(path, opts);
  if (!response.ok) {
    throw await parseError(response);
  }
  if (response.status === 204) return null;
  return response.json();
}

export const api = {
  // Auth
  authConfig: () => apiFetch("/api/auth/config/"),
  me: () => apiFetch("/api/me/"),
  telegramLogin: (initData) =>
    apiFetch("/api/auth/telegram/", { method: "POST", body: { init_data: initData } }),
  devLogin: (username) =>
    apiFetch("/api/auth/dev/", { method: "POST", body: { username } }),
  logout: () => apiFetch("/api/auth/logout/", { method: "POST" }),

  // Projects
  listProjects: () => apiFetch("/api/projects/"),
  createProject: (title) => apiFetch("/api/projects/", { method: "POST", body: { title } }),
  getProject: (id) => apiFetch(`/api/projects/${id}/`),
  patchProject: (id, data) => apiFetch(`/api/projects/${id}/`, { method: "PATCH", body: data }),
  deleteProject: (id) => apiFetch(`/api/projects/${id}/`, { method: "DELETE" }),

  // Assets
  uploadAsset: (projectId, file, onProgress) =>
    uploadWithProgress(`/api/projects/${projectId}/assets/`, file, onProgress),
  deleteAsset: (assetId) => apiFetch(`/api/assets/${assetId}/`, { method: "DELETE" }),
  assetAudioUrl: (assetId) => `/api/assets/${assetId}/audio/`,

  // Exports
  createExport: (projectId, settings) =>
    apiFetch(`/api/projects/${projectId}/exports/`, { method: "POST", body: settings }),
  getExport: (exportId) => apiFetch(`/api/exports/${exportId}/`),
  exportDownloadUrl: (exportId) => `/api/exports/${exportId}/download/`,
};

/**
 * XHR-based upload so we can report real progress (fetch has no upload
 * progress events). Includes CSRF header; returns parsed JSON.
 */
function uploadWithProgress(url, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url, true);
    xhr.withCredentials = true;
    const csrf = getCookie("csrftoken");
    if (csrf) xhr.setRequestHeader("X-CSRFToken", csrf);
    xhr.responseType = "json";

    if (onProgress && xhr.upload) {
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable) {
          onProgress(event.loaded / event.total);
        }
      };
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response);
      } else {
        const err = (xhr.response && xhr.response.error) || {};
        reject(
          new ApiError(err.message || `خطای سرور (${xhr.status})`, {
            code: err.code || "upload_failed",
            status: xhr.status,
            details: err.details || [],
          }),
        );
      }
    };
    xhr.onerror = () =>
      reject(new ApiError("اتصال به سرور برقرار نشد.", { code: "network_error" }));
    xhr.ontimeout = () =>
      reject(new ApiError("آپلود بیش از حد طول کشید.", { code: "timeout" }));

    const form = new FormData();
    form.append("file", file);
    xhr.send(form);
  });
}
