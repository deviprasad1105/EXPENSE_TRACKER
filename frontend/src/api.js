import axios from 'axios'

const api = axios.create({ baseURL: '/api' })

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

api.interceptors.response.use(
  (r) => r,
  (err) => {
    // Only a rejected token ends the session; network or server errors never log the user out
    if (err.response?.status === 401 && localStorage.getItem('token') && !err.config.url.startsWith('/auth/')) {
      localStorage.removeItem('token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  },
)

// Plain-language wording for the server's field checks (FastAPI / pydantic validation errors)
const FIELD_RULES = {
  username: 'can only use letters, numbers, dot (.), dash (-) and underscore (_), with no spaces',
  new_username: 'can only use letters, numbers, dot (.), dash (-) and underscore (_), with no spaces',
}

function fieldLabel(name) {
  const text = String(name ?? 'value').replace(/_/g, ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

export function validationText(d) {
  const field = d.loc?.slice(-1)[0]
  const label = fieldLabel(field)
  const ctx = d.ctx || {}
  switch (d.type) {
    case 'missing':
      return `${label} is required`
    case 'string_too_short':
      return `${label} needs at least ${ctx.min_length} character${ctx.min_length === 1 ? '' : 's'}`
    case 'string_too_long':
      return `${label} can have at most ${ctx.max_length} characters`
    case 'string_pattern_mismatch':
      return `${label} ${FIELD_RULES[field] || 'has characters that aren’t allowed'}`
    case 'value_error':
      return `${label}: ${String(d.msg).replace(/^value error,\s*/i, '').replace(/^value is not a valid email address:\s*/i, '')}`
    case 'greater_than':
      return `${label} must be more than ${ctx.gt}`
    case 'greater_than_equal':
      return `${label} must be at least ${ctx.ge}`
    case 'less_than_equal':
      return `${label} can be at most ${ctx.le}`
    default:
      return `${label}: ${d.msg}`
  }
}

export function errorMessage(err) {
  const detail = err?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map(validationText).join('. ')
  return detail || err?.message || 'Something went wrong'
}

export async function download(url, params, fallbackName) {
  let res
  try {
    res = await api.get(url, { params, responseType: 'blob' })
  } catch (err) {
    // The error body arrives as a Blob too: read it so the server's message ("range too long"…) is shown
    const data = err.response?.data
    if (data instanceof Blob) {
      try {
        err.response.data = JSON.parse(await data.text())
      } catch {
        /* not JSON: keep the generic message */
      }
    }
    throw err
  }
  const disposition = res.headers['content-disposition'] || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(res.data)
  link.download = match ? match[1] : fallbackName
  link.click()
  // Revoking at once can cancel the download in some browsers
  setTimeout(() => URL.revokeObjectURL(link.href), 10_000)
}

export const money = (v) =>
  Number(v || 0).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

export const todayISO = () => {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export default api
