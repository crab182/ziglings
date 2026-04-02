import React, { useState } from 'react'
import { browseSMB, ingestFromSMB, listCollections } from '../services/api'

export default function SMBBrowser() {
  const [server, setServer] = useState('192.168.1.52')
  const [share, setShare] = useState('')
  const [username, setUsername] = useState('guest')
  const [password, setPassword] = useState('')
  const [domain, setDomain] = useState('WORKGROUP')
  const [currentPath, setCurrentPath] = useState('/')
  const [entries, setEntries] = useState([])
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [ingestCollection, setIngestCollection] = useState('default')
  const [pathHistory, setPathHistory] = useState(['/'])

  const browse = async (path = '/') => {
    if (!server || !share) {
      setMessage({ type: 'error', text: 'Enter server and share name' })
      return
    }
    setLoading(true)
    setMessage(null)
    try {
      const result = await browseSMB(server, share, path, username, password, domain)
      setEntries(result)
      setCurrentPath(path)
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  const navigateTo = (entry) => {
    if (!entry.is_directory) return
    const newPath = currentPath === '/' ? `/${entry.name}` : `${currentPath}/${entry.name}`
    setPathHistory(prev => [...prev, newPath])
    browse(newPath)
  }

  const navigateUp = () => {
    if (pathHistory.length <= 1) return
    const newHistory = pathHistory.slice(0, -1)
    setPathHistory(newHistory)
    browse(newHistory[newHistory.length - 1])
  }

  const navigateToIndex = (index) => {
    const newHistory = pathHistory.slice(0, index + 1)
    setPathHistory(newHistory)
    browse(newHistory[newHistory.length - 1])
  }

  const handleIngest = async () => {
    if (!server || !share) return
    setLoading(true)
    setMessage(null)
    try {
      const result = await ingestFromSMB({
        server, share, path: currentPath,
        username, password, domain,
        collection: ingestCollection, recursive: true,
      })
      let text = `Ingested ${result.files_processed} files (${result.total_chunks} chunks)`
      if (result.errors?.length) text += `. Errors: ${result.errors.length}`
      setMessage({ type: 'success', text })
    } catch (e) {
      setMessage({ type: 'error', text: e.message })
    }
    setLoading(false)
  }

  const formatSize = (bytes) => {
    if (bytes === 0) return '-'
    const units = ['B', 'KB', 'MB', 'GB']
    const i = Math.floor(Math.log(bytes) / Math.log(1024))
    return `${(bytes / Math.pow(1024, i)).toFixed(1)} ${units[i]}`
  }

  const pathParts = currentPath.split('/').filter(Boolean)

  return (
    <div className={loading ? 'loading' : ''}>
      <div className="page-header">
        <h2>SMB File Browser</h2>
        <p>Browse and ingest documents from SMB shares on your LAN</p>
      </div>

      {message && (
        <div className={`alert alert-${message.type}`}>
          {message.text}
          <button onClick={() => setMessage(null)} style={{ float: 'right', background: 'none', border: 'none', color: 'inherit', cursor: 'pointer' }}>x</button>
        </div>
      )}

      <div className="card">
        <div className="card-header"><h3>Connection</h3></div>
        <div className="form-row">
          <div className="form-group">
            <label>Server IP / Hostname</label>
            <input className="input" value={server} onChange={e => setServer(e.target.value)} placeholder="192.168.1.x" />
          </div>
          <div className="form-group">
            <label>Share Name</label>
            <input className="input" value={share} onChange={e => setShare(e.target.value)} placeholder="Documents" />
          </div>
          <div className="form-group">
            <label>Username</label>
            <input className="input" value={username} onChange={e => setUsername(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Password</label>
            <input className="input" type="password" value={password} onChange={e => setPassword(e.target.value)} />
          </div>
          <div className="form-group">
            <label>Domain</label>
            <input className="input" value={domain} onChange={e => setDomain(e.target.value)} />
          </div>
        </div>
        <div style={{ display: 'flex', gap: '0.5rem', marginTop: '0.5rem' }}>
          <button className="btn btn-primary" onClick={() => browse('/')}>Connect & Browse</button>
        </div>
      </div>

      {entries.length > 0 && (
        <>
          <div className="card">
            <div className="breadcrumb">
              <button onClick={() => navigateToIndex(0)}>root</button>
              {pathParts.map((part, i) => (
                <React.Fragment key={i}>
                  <span>/</span>
                  <button onClick={() => navigateToIndex(i + 1)}>{part}</button>
                </React.Fragment>
              ))}
            </div>

            <div style={{ display: 'flex', gap: '0.5rem', marginBottom: '1rem' }}>
              <button className="btn btn-outline btn-sm" onClick={navigateUp} disabled={pathHistory.length <= 1}>.. Up</button>
              <button className="btn btn-outline btn-sm" onClick={() => browse(currentPath)}>Refresh</button>
              <div style={{ flex: 1 }} />
              <input
                className="input"
                style={{ width: '150px' }}
                value={ingestCollection}
                onChange={e => setIngestCollection(e.target.value)}
                placeholder="Collection"
              />
              <button className="btn btn-primary btn-sm" onClick={handleIngest}>
                Ingest This Folder
              </button>
            </div>

            <div className="file-browser">
              {entries.map((entry, i) => (
                <div key={i} className="file-item" onClick={() => navigateTo(entry)}>
                  <span style={{ fontFamily: 'monospace', color: entry.is_directory ? 'var(--accent)' : 'var(--text-muted)' }}>
                    {entry.is_directory ? '[D]' : ' F '}
                  </span>
                  <span className="name">{entry.name}</span>
                  <span className="size">{formatSize(entry.size)}</span>
                  <span className="date">{entry.last_modified ? new Date(entry.last_modified).toLocaleDateString() : ''}</span>
                </div>
              ))}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
