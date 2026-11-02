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

export function errorMessage(err) {
  const detail = err?.response?.data?.detail
  if (Array.isArray(detail)) return detail.map((d) => `${d.loc?.slice(-1)[0]}: ${d.msg}`).join(', ')
  return detail || err?.message || 'Something went wrong'
}

export async function download(url, params, fallbackName) {
  const res = await api.get(url, { params, responseType: 'blob' })
  const disposition = res.headers['content-disposition'] || ''
  const match = disposition.match(/filename="?([^"]+)"?/)
  const link = document.createElement('a')
  link.href = URL.createObjectURL(res.data)
  link.download = match ? match[1] : fallbackName
  link.click()
  URL.revokeObjectURL(link.href)
}

export const money = (v) =>
  Number(v || 0).toLocaleString('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 })

export const todayISO = () => {
  const d = new Date()
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10)
}

export default api
