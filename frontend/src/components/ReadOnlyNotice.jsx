import { useAuth } from '../auth'
import { Alert } from './ui'

export default function ReadOnlyNotice() {
  const { target } = useAuth()
  if (!target || target.writable) return null
  return <Alert type="info">You are viewing {target.full_name}’s data. It is read-only, so nothing can be added or changed.</Alert>
}
