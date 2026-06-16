import React, { useState, useEffect } from 'react'
import { queryDocuments, askDocuments, listCollections } from '../services/api'
import HelpBubble from '../components/HelpBubble'

export default function Search() {
  const [query, setQuery] = useState('')
  const [collection, setCollection] = useState('default')
  const [nResults, setNResults] = useState(5)
  const [results, setResults] = useState(null)
  const [answer, setAnswer] = useState(null)
  const [collections, setCollections] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [mode, setMode] = useState('search') // 'search' | 'ask'
  const [expandedSource, setExpandedSource] = useState(null)
  const [history, setHistory] = useState([]) // chat history for ask mode

  useEffect(() => {
    listCollections().then(r => setCollections(r.collections || [])).catch(() => {})
  }, [])

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    setAnswer(null)
    try {
      if (mode === 'ask') {
        const res = await askDocuments(query, collection, nResults)
        setAnswer(res)
        setResults(null)
        setHistory(h => [...h, { query, answer: res.answer, sources: res.sources, model: res.model }])
      } else {
        const res = await queryDocuments(query, collection, nResults)
        setResults(res.results)
        setAnswer(null)
      }
      setQuery('')
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  const toggleSource = (idx) => {
    setExpandedSource(expandedSource === idx ? null : idx)
  }

  return (
    <div>
      <div className="page-header">
        <h2>Search Documents</h2>
        <p>Semantic search across your indexed documents</p>
      </div>

      {/* Mode toggle */}
      <div className="tabs" style={{ marginBottom: '1rem' }}>
        <button className={`tab ${mode === 'search' ? 'active' : ''}`} onClick={() => setMode('search')}>
          Search
          <HelpBubble text="Returns raw document chunks ranked by relevance. Best for exploring what's in the collection." />
        </button>
        <button className={`tab ${mode === 'ask' ? 'active' : ''}`} onClick={() => setMode('ask')}>
          Ask
          <HelpBubble text="Generates an answer using a local LLM with source citations. Requires Ollama running on the server." />
        </button>
      </div>

      {/* Ask mode: chat history */}
      {mode === 'ask' && history.length > 0 && (
        <div className="card" style={{ maxHeight: '50vh', overflowY: 'auto' }}>
          {history.map((h, i) => (
            <div key={i} style={{ marginBottom: '1.5rem' }}>
              <div style={{ color: 'var(--accent)', fontWeight: 600, marginBottom: '0.5rem' }}>
                Q: {h.query}
              </div>
              {h.answer ? (
                <div className="answer-block">
                  <div style={{ whiteSpace: 'pre-wrap', marginBottom: '0.75rem' }}>{h.answer}</div>
                  {h.model && <div style={{ fontSize: '0.75rem', color: 'var(--text-muted)' }}>via {h.model}</div>}
                  {h.sources && h.sources.length > 0 && (
                    <div className="citation-list">
                      {h.sources.map((s, j) => (
                        <span key={j} className="citation-badge" title={s.excerpt}>
                          {s.source}{s.page ? ` p.${s.page}` : ''}{s.section ? ` - ${s.section}` : ''}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ color: 'var(--text-muted)', fontStyle: 'italic' }}>
                  No LLM available — use Search mode for raw results.
                </div>
              )}
              {i < history.length - 1 && <hr style={{ borderColor: 'var(--border)', margin: '1rem 0' }} />}
            </div>
          ))}
        </div>
      )}

      {/* Query input */}
      <div className="card">
        <div className="form-row">
          <div className="form-group" style={{ flex: 2 }}>
            <label>
              {mode === 'ask' ? 'Ask a question' : 'Search Query'}
              <HelpBubble title="Semantic search" text="Searches by meaning, not just keywords. Ask a natural-language question." />
            </label>
            <input
              className="input"
              placeholder={mode === 'ask' ? 'Ask a question about your documents...' : 'Enter your search query...'}
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
              autoFocus
            />
          </div>
          <div className="form-group">
            <label>Collection</label>
            <select className="select" value={collection} onChange={e => setCollection(e.target.value)}>
              {collections.map(c => (
                <option key={c.name} value={c.name}>{c.name} ({c.document_count})</option>
              ))}
            </select>
          </div>
          <div className="form-group">
            <label>Sources</label>
            <input
              className="input"
              type="number"
              min="1"
              max="20"
              value={nResults}
              onChange={e => setNResults(parseInt(e.target.value) || 5)}
            />
          </div>
        </div>
        <button className="btn btn-primary" onClick={handleSearch} disabled={loading}>
          {loading ? <><span className="spinner"></span> {mode === 'ask' ? 'Thinking...' : 'Searching...'}</> : mode === 'ask' ? 'Ask' : 'Search'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Search mode results with expandable sources */}
      {results && mode === 'search' && (
        <div className="card">
          <div className="card-header">
            <h3>Results ({results.length})</h3>
          </div>
          <div className="search-results">
            {results.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No results found.</p>
            ) : (
              results.map((r, i) => (
                <div key={i} className="result-item" onClick={() => toggleSource(i)} style={{ cursor: 'pointer' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <div className="source">
                      {r.source}
                      {r.metadata?.page_number && <span className="citation-badge">p.{r.metadata.page_number}</span>}
                      {r.metadata?.section_header && <span className="citation-badge">{r.metadata.section_header}</span>}
                    </div>
                    <span className="score">
                      {r.rerank_score != null ? `rerank: ${r.rerank_score}` : `score: ${r.score}`}
                    </span>
                  </div>
                  <div className="content" style={{
                    maxHeight: expandedSource === i ? 'none' : '4.5em',
                    overflow: 'hidden',
                    transition: 'max-height 0.2s',
                  }}>
                    {r.content}
                  </div>
                  {expandedSource !== i && r.content.length > 200 && (
                    <span style={{ color: 'var(--accent)', fontSize: '0.8rem' }}>click to expand</span>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
