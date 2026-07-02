import React, { useState, useEffect } from 'react'
import { getStatus, getMetrics, getMcpInfo } from '../services/api'
import HelpBubble from '../components/HelpBubble'

export default function Dashboard() {
  const [status, setStatus] = useState(null)
  const [metrics, setMetrics] = useState(null)
  const [mcp, setMcp] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    getStatus().then(setStatus).catch(e => setError(e.message))
    getMetrics().then(setMetrics).catch(() => {})
    getMcpInfo().then(setMcp).catch(() => {})
  }, [])

  if (error) return <div className="alert alert-error">{error}</div>
  if (!status) return <div><span className="spinner"></span> Loading...</div>

  return (
    <div>
      <div className="page-header">
        <h2>Dashboard</h2>
        <p>System overview for {status.hostname} ({status.ip})</p>
      </div>

      <div className="stats-grid">
        <div className="stat-card">
          <div className="label">Total Documents</div>
          <div className="value">{status.total_documents}</div>
        </div>
        <div className="stat-card">
          <div className="label">Collections</div>
          <div className="value">{status.collections.length}</div>
        </div>
        <div className="stat-card">
          <div className="label">Active API Keys</div>
          <div className="value">{status.active_credentials}</div>
        </div>
        <div className="stat-card">
          <div className="label">MCP Server</div>
          <div className="value" style={{ color: status.mcp_enabled ? 'var(--success)' : 'var(--danger)' }}>
            {status.mcp_enabled ? 'Active' : 'Disabled'}
          </div>
        </div>
      </div>

      {/* Performance metrics */}
      {metrics && (metrics.query_count > 0 || metrics.ingest_count > 0) && (
        <div className="card">
          <div className="card-header">
            <h3>
              Performance
              <HelpBubble text="Average query latencies since last restart. Retrieve = vector+BM25 search. Rerank = cross-encoder re-scoring." />
            </h3>
          </div>
          <div className="metrics-grid">
            <div className="metric-item">
              <div className="label">Queries</div>
              <div className="value">{metrics.query_count}</div>
            </div>
            <div className="metric-item">
              <div className="label">Ingestions</div>
              <div className="value">{metrics.ingest_count}</div>
            </div>
            <div className="metric-item">
              <div className="label">Avg Retrieve</div>
              <div className="value">{metrics.avg_retrieve_ms}ms</div>
            </div>
            <div className="metric-item">
              <div className="label">Avg Rerank</div>
              <div className="value">{metrics.avg_rerank_ms}ms</div>
            </div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <h3>Collections</h3>
        </div>
        {status.collections.length > 0 ? (
          <table className="table">
            <thead><tr><th>Name</th></tr></thead>
            <tbody>
              {status.collections.map(c => (
                <tr key={c}><td>{c}</td></tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p style={{ color: 'var(--text-muted)' }}>No collections yet. Upload documents to get started.</p>
        )}
      </div>

      <div className="card">
        <div className="card-header">
          <h3>
            MCP Connection Info
            <HelpBubble text="Point your LLM client (Claude, Cursor, etc.) at these endpoints with a Bearer API key." />
          </h3>
        </div>
        <div className="form-group">
          <label>SSE Endpoint</label>
          <div className="code-block">https://{status.ip}:8943/sse</div>
        </div>
        <div className="form-group">
          <label>Streamable HTTP Endpoint</label>
          <div className="code-block">https://{status.ip}:8943/mcp</div>
        </div>
        <div className="form-group">
          <label>Server Info</label>
          <div className="code-block">https://{status.ip}:8943/mcp/info</div>
        </div>
        {mcp && (
          <div className="form-group">
            <label>Capabilities</label>
            <div className="citation-list">
              {(mcp.capabilities || []).map(c => <span key={c} className="citation-badge">{c}</span>)}
              {mcp.tools && <span className="citation-badge">{mcp.tools.length} tools</span>}
              {mcp.prompts && <span className="citation-badge">{mcp.prompts.length} prompts</span>}
            </div>
          </div>
        )}
        <p style={{ color: 'var(--text-muted)', fontSize: '0.8rem', marginTop: '0.5rem' }}>
          Use your API key as a Bearer token in the Authorization header.
        </p>
      </div>
    </div>
  )
}
