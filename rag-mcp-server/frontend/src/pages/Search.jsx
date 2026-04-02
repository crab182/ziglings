import React, { useState, useEffect } from 'react'
import { queryDocuments, listCollections } from '../services/api'

export default function Search() {
  const [query, setQuery] = useState('')
  const [collection, setCollection] = useState('default')
  const [nResults, setNResults] = useState(5)
  const [results, setResults] = useState(null)
  const [collections, setCollections] = useState([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    listCollections().then(r => setCollections(r.collections)).catch(() => {})
  }, [])

  const handleSearch = async () => {
    if (!query.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await queryDocuments(query, collection, nResults)
      setResults(res.results)
    } catch (e) {
      setError(e.message)
    }
    setLoading(false)
  }

  return (
    <div>
      <div className="page-header">
        <h2>Search Documents</h2>
        <p>Semantic search across your indexed documents</p>
      </div>

      <div className="card">
        <div className="form-row">
          <div className="form-group" style={{ flex: 2 }}>
            <label>Search Query</label>
            <input
              className="input"
              placeholder="Enter your search query..."
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSearch()}
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
            <label>Results</label>
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
          {loading ? <><span className="spinner"></span> Searching...</> : 'Search'}
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {results && (
        <div className="card">
          <div className="card-header">
            <h3>Results ({results.length})</h3>
          </div>
          <div className="search-results">
            {results.length === 0 ? (
              <p style={{ color: 'var(--text-muted)' }}>No results found. Try a different query or check if documents are indexed.</p>
            ) : (
              results.map((r, i) => (
                <div key={i} className="result-item">
                  <span className="score">Score: {r.score}</span>
                  <div className="source">{r.source}</div>
                  <div className="content">{r.content}</div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  )
}
