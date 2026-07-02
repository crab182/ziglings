import React, { useState, useEffect } from 'react'
import { getDocumentContent } from '../services/api'

/**
 * Full-document preview modal. Fetches the reconstructed document by source
 * and renders it with optional query-term highlighting.
 *
 *   <DocPreview source={s} collection={c} highlight={query} onClose={fn} />
 */
export default function DocPreview({ source, collection = 'default', highlight = '', onClose }) {
  const [doc, setDoc] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    let alive = true
    getDocumentContent(source, collection)
      .then((d) => { if (alive) setDoc(d) })
      .catch((e) => { if (alive) setError(e.message) })
    return () => { alive = false }
  }, [source, collection])

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose?.() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  // Regex-free highlighting: scan with case-insensitive indexOf so user input
  // never reaches a RegExp constructor (avoids regex-injection/ReDoS entirely).
  const renderContent = (text) => {
    const terms = highlight.trim().split(/\s+/).filter((t) => t.length > 2)
    if (!terms.length) return text
    const lowerTerms = terms.map((t) => t.toLowerCase())
    const lowerText = text.toLowerCase()
    const segments = []
    let pos = 0
    let key = 0
    while (pos < text.length) {
      let best = -1
      let bestLen = 0
      for (const t of lowerTerms) {
        const idx = lowerText.indexOf(t, pos)
        if (idx !== -1 && (best === -1 || idx < best)) {
          best = idx
          bestLen = t.length
        }
      }
      if (best === -1) {
        segments.push(<span key={key++}>{text.slice(pos)}</span>)
        break
      }
      if (best > pos) segments.push(<span key={key++}>{text.slice(pos, best)}</span>)
      segments.push(<mark key={key++}>{text.slice(best, best + bestLen)}</mark>)
      pos = best + bestLen
    }
    return segments
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h3>{source}</h3>
          <button className="modal-close" onClick={onClose}>×</button>
        </div>
        <div className="modal-body">
          {error && <div className="alert alert-error">{error}</div>}
          {!doc && !error && <div><span className="spinner"></span> Loading document…</div>}
          {doc && (
            <>
              <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem' }}>
                {doc.chunk_count} chunks · collection: {doc.collection}
              </p>
              <pre className="doc-content">{renderContent(doc.content || '')}</pre>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
