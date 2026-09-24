export class RequestError extends Error {
  status: number

  constructor(status: number, message: string) {
    super(message)
    this.name = 'RequestError'
    this.status = status
  }
}

export async function request<T>(path: string, chatId?: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers)
  if (chatId) headers.set('X-Chat-ID', chatId)
  const response = await fetch(path, { ...init, headers, credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => ({ detail: response.statusText }))
    throw new RequestError(statusOrZero(response.status), String(body.detail || body.error || response.statusText))
  }
  return response.json() as Promise<T>
}

function statusOrZero(status: number): number {
  return Number.isInteger(status) ? status : 0
}

export function jsonBody(value: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  }
}

export function putBody(value: unknown): RequestInit {
  return {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(value),
  }
}
