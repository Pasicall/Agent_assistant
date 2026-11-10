export function readLocal(key: string) {
  try { return localStorage.getItem(key) } catch { return null }
}

export function writeLocal(key: string, value: string) {
  try { localStorage.setItem(key, value); return true } catch { return false }
}

export function removeLocal(key: string) {
  try { localStorage.removeItem(key) } catch { /* Browser storage may be unavailable. */ }
}
