/**
 * turboCache.js — IndexedDB-based cache for Turbo scan results.
 * Replaces localStorage when cache backend is set to 'idb'.
 * IndexedDB has no 5 MB limit, so all tickers can be stored without truncation.
 */

const DB_NAME    = 'sachoki_turbo_v1'
const STORE_NAME = 'scan_cache'

let _dbPromise = null

function openDB() {
  if (_dbPromise) return _dbPromise
  _dbPromise = new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, 1)
    req.onupgradeneeded = e => {
      const db = e.target.result
      if (!db.objectStoreNames.contains(STORE_NAME)) {
        db.createObjectStore(STORE_NAME, { keyPath: 'key' })
      }
    }
    req.onsuccess = e => resolve(e.target.result)
    req.onerror   = e => { _dbPromise = null; reject(e.target.error) }
  })
  return _dbPromise
}

/** Read a cached payload { results, lastScan } for (tf, uni). Returns null on miss/error. */
export async function idbGet(tf, uni) {
  try {
    const db = await openDB()
    return await new Promise(resolve => {
      const tx  = db.transaction(STORE_NAME, 'readonly')
      const req = tx.objectStore(STORE_NAME).get(`${tf}_${uni}`)
      req.onsuccess = () => resolve(req.result?.data ?? null)
      req.onerror   = () => resolve(null)
    })
  } catch {
    return null
  }
}

/** Write { results, lastScan } for (tf, uni). Fire-and-forget. */
export async function idbSet(tf, uni, results, lastScan) {
  try {
    const db = await openDB()
    await new Promise(resolve => {
      const tx = db.transaction(STORE_NAME, 'readwrite')
      tx.objectStore(STORE_NAME).put({
        key: `${tf}_${uni}`,
        data: { results, lastScan },
        savedAt: Date.now(),
      })
      tx.oncomplete = resolve
      tx.onerror    = resolve
    })
  } catch {}
}

/** Return current cache backend: 'idb' | 'ls' */
export function getCacheBackend() {
  try { return localStorage.getItem('sachoki_cache_backend') || 'ls' } catch { return 'ls' }
}

/** Persist cache backend choice. */
export function setCacheBackend(val) {
  try { localStorage.setItem('sachoki_cache_backend', val) } catch {}
}
