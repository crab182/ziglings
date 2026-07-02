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

  const renderContent = (text) => {
    const terms = highlight.trim().split(/\s+/).filter((t) => t.length > 2)
    if (!terms.length) return text
    const re = new RegExp(`(${terms.map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('|')})`, 'gi')
    return text.split(re).map((seg, i) =>
      re.test(seg) ? <mark key={i}>{seg}</mark> : <span key={i}>{seg}</span>
    )
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
