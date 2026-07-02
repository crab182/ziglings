import React, { useState, useEffect, useRef } from 'react'
import { queryDocuments, askDocumentsStream, askDocuments, listCollections } from '../services/api'
import HelpBubble from '../components/HelpBubble'
import AnswerText from '../components/AnswerText'
import DocPreview from '../components/DocPreview'

export default function Search() {
  const [query, setQuery] = useState('')
  const [collection, setCollection] = useState('default')
  const [nResults, setNResults] = useState(5)
  const [results, setResults] = useState(null)
  const [collections, setCollections] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('search') // 'search' | 'ask'
  const [expandedSource, setExpandedSource] = useState(null)
  const [history, setHistory] = useState([])
  const [preview, setPreview] = useState(null) // {source, collection}
  const [highlightCite, setHighlightCite] = useState(null) // "entryIdx:srcIdx"
  const sourceRefs = useRef({})
  const lastQueryRef = useRef('')

  useEffect(() => {
    listCollections().then(r => setCollections(r.collections || [])).catch(() => {})
  }, [])

  const runSearch = async () => {
    const res = await queryDocuments(query, collection, nResults)
    setResults(res.results)
  }

  const runAsk = async () => {
    const q = query
    const idx = history.length
    setHistory(h => [...h, { query: q, answer: '', sources: [], model: '', streaming: true }])
    const patch = (fn) => setHistory(h => h.map((e, i) => (i === idx ? fn(e) : e)))
    try {
      await askDocumentsStream(q, collection, nResults, {
        onSources: (s) => patch(e => ({ ...e, sources: s })),
        onDelta: (t) => patch(e => ({ ...e, answer: e.answer + t })),
        onDone: (model) => patch(e => ({ ...e, model, streaming: false })),
        onError: (msg) => patch(e => ({ ...e, streaming: false, error: msg })),
      })
    } catch (e) {
      // Fall back to non-streaming ask if the stream endpoint fails outright
      try {
        const res = await askDocuments(q, collection, nResults)
        patch(en => ({ ...en, answer: res.answer, sources: res.sources, model: res.model, streaming: false }))
      } catch (e2) {
        patch(en => ({ ...en, streaming: false, error: e2.message }))
      }
    }
  }

  const handleSubmit = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    lastQueryRef.current = query
    try {
      if (mode === 'ask') { await runAsk(); setQuery('') }
      else { await runSearch() }
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  const onCite = (entryIdx, srcIdx) => {
    const key = `${entryIdx}:${srcIdx}`
    setHighlightCite(key)
    sourceRefs.current[key]?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    setTimeout(() => setHighlightCite(h => (h === key ? null : h)), 2000)
  }

  return (
    <div>
      <div className="page-header">
        <h2>Search Documents</h2>
        <p>Semantic search across your indexed documents</p>
      </div>

      <div className="tabs" style={{ marginBottom: '1rem' }}>
        <button className={`tab ${mode === 'search' ? 'active' : ''}`} onClick={() => setMode('search')}>
          Search
          <HelpBubble text="Returns raw document chunks ranked by relevance. Best for exploring what's in the collection." />
        </button>
        <button className={`tab ${mode === 'ask' ? 'active' : ''}`} onClick={() => setMode('ask')}>
          Ask
          <HelpBubble text="Generates an answer using a local LLM with clickable [n] source citations, streamed live. Requires Ollama on the server." />
        </button>
      </div>

      {mode === 'ask' && history.length > 0 && (
        <div className="card" style={{ maxHeight: '55vh', overflowY: 'auto' }}>
          {history.map((h, i) => (
            <div key={i} style={{ marginBottom: '1.5rem' }}>
              <div style={{ color: 'var(--accent)', fontWeight: 600, marginBottom: '0.5rem' }}>Q: {h.query}</div>
              {h.error ? (
                <div className="alert alert-error">{h.error}</div>
              ) : (h.answer || h.streaming) ? (
                <div className="answer-block">
                  <div>
                    <AnswerText text={h.answer} sources={h.sources} onCite={(s) => onCite(i, s)} />
                    {h.streaming && <span className="stream-cursor">▋</span>}
                  </div>
                  {h.model && <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>via {h.model}</div>}
                  {h.sources && h.sources.length > 0 && (
                    <div className="citation-list">
                      {h.sources.map((s, j) => (
                        <span
                          key={j}
                          ref={(el) => { sourceRefs.current[`${i}:${j}`] = el }}
                          className={`citation-badge clickable ${highlightCite === `${i}:${j}` ? 'cite-active' : ''}`}
                          title={s.excerpt}
                          onClick={() => setPreview({ source: s.source, collection })}
                        >
                          [{j + 1}] {s.source}{s.page ? ` p.${s.page}` : ''}{s.section ? ` · ${s.section}` : ''}
                        </span>
                      ))}
                    </div>
                  )}
                  {!h.streaming && !h.answer && (!h.sources || h.sources.length === 0) && (
                    <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>No results found.</span>
                  )}
                  {!h.streaming && !h.answer && h.sources && h.sources.length > 0 && (
                    <span style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>No LLM available — sources shown above. Use Search mode for raw chunks.</span>
                  )}
                </div>
              ) : null}
              {i < history.length - 1 && <hr style={{ borderColor: 'var(--border)', margin: '1rem 0' }} />}
            </div>
          ))}
        </div>
      )}

      <div className="card">
        <div className="form-row">
          <div className="form-group" style={{ flex: 2 }}>
            <label>
              {mode === 'ask' ? 'Ask a question' : 'Search Query'}
              <HelpBubble title="Semantic search" text="Searches by meaning, not just keywords." />
            </label>
            <input
              className="input"
              placeholder={mode === 'ask' ? 'Ask a question about your documents...' : 'Enter your search query...'}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSubmit()}
              autoFocus
            />
          </div>
          <div className="form-group">
            <label>Collection</label>
            <select className="select" value={collection} onChange={e => setCollection(e.target.value)}>
              {collections.map(c => (
                <option key={c.name} value={c.name}>
                  {c.name} ({c.document_count ?? 0} docs)
                </option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Sources</label>
            <input className="input" type="number" min="1" max="20" value={nResults}
              onChange={e => setNResults(parseInt(e.target.value) || 5)} />
          </div>
        </div>
        <button className="btn btn-primary" onClick={handleSubmit} disabled={loading}>
          {loading ? <><span className="spinner"></span> {mode === 'ask' ? 'Thinking...' : 'Searching...'}</> : mode === 'ask' ? 'Ask' : 'Search'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {results && mode === 'search' && (
        <div className="card">
          <div className="card-header"><h3>Results ({results.length})</h3></div>
          <div className="search-results">
            {results.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No results found.</p>
            ) : (
              results.map((r, i) => (
                <div key={i} className="result-item">
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div className="source">
                      <button className="link-btn" onClick={() => setPreview({ source: r.source, collection })}>
                        {r.source}
                      </button>
                      {r.metadata?.page_number && <span className="citation-badge">p.{r.metadata.page_number}</span>}
                      {r.metadata?.section_header && <span className="citation-badge">{r.metadata.section_header}</span>}
                    </div>
                    <span className="score">{r.rerank_score != null ? `rerank: ${r.rerank_score}` : `score: ${r.score}`}</span>
                  </div>
                  <div className="content" onClick={() => setExpandedSource(expandedSource === i ? null : i)}
                    style={{ maxHeight: expandedSource === i ? 'none' : '4.5em', overflow: 'hidden', cursor: 'pointer', transition: 'max-height 0.2s' }}>
                    {r.content}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}

      {preview && (
        <DocPreview
          source={preview.source}
          collection={preview.collection}
          highlight={lastQueryRef.current}
          onClose={() => setPreview(null)}
        />
      )}
    </div>
  )
}
