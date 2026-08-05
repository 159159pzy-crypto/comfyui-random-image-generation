export class ApiError extends Error {
  constructor(message, { status = 0, payload = null, path = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
    this.path = path;
  }
}

export async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: options.body instanceof FormData
      ? options.headers
      : { "Content-Type": "application/json", ...(options.headers || {}) },
  });
  const text = await response.text();
  let payload = null;
  try {
    payload = text ? JSON.parse(text) : null;
  } catch {
    payload = null;
  }
  if (!response.ok) {
    throw new ApiError(payload?.error || `请求失败 (${response.status})`, {
      status: response.status,
      payload,
      path,
    });
  }
  return payload;
}
