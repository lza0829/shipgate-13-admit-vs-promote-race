function uuidKey() {
  if (window.crypto && typeof window.crypto.randomUUID === 'function') {
    return window.crypto.randomUUID()
  }
  return 'k-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10)
}

export async function api(path, options = {}) {
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) }
  // 写口默认带幂等令牌：同一按钮的双击/重试自动收口到单一结局
  const method = (options.method || 'GET').toUpperCase()
  if (method !== 'GET' && !headers['Idempotency-Key']) {
    headers['Idempotency-Key'] = uuidKey()
  }
  const res = await fetch(`/api${path}`, { ...options, headers })
  const text = await res.text()
  let data
  try { data = text ? JSON.parse(text) : null } catch { data = { 明细: text } }
  if (!res.ok) {
    const detail = data && data.detail
    // 争用收口（409）：detail 是结构化载荷，带争用戳
    if (res.status === 409 && detail && typeof detail === 'object') {
      const err = new Error(detail.说明 || '争用收口：整笔失败，可重试')
      err.contention = detail
      throw err
    }
    const msg = detail || (data && data.明细) || res.statusText
    throw new Error(typeof msg === 'string' ? msg : JSON.stringify(msg))
  }
  return data
}
